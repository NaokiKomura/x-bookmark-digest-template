"""GitHubトレンド・ランキング・公式ブログの取得（処理フロー 3）。

取得先と読み取り方は config/sources.json に置く。ページを読み取る情報源はサイト側の変更で
動かなくなる前提で扱い、失敗した情報源は `errors` に記録して、ほかの情報源の処理を続ける。

出力: data/sources/YYYY-MM-DD.json
- github / qiita / zenn / devio / blogs: 情報源ごとの項目（本文と Jev の結果は後続のスクリプトが足す）
- reserve: Qiita・Zenn の次点（テック判定で除外が出たときの補充用。classify_jev.py が消費して消す）
- status: 情報源ごとの取得結果（ok / none / error）
- errors: 失敗した情報源と理由
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin
from xml.etree.ElementTree import Element

import httpx
from bs4 import BeautifulSoup
from defusedxml import ElementTree

from scripts.common import (
    JST,
    FetchError,
    RobotsChecker,
    clean_url,
    fetch_bytes,
    gha_warning,
    load_config,
    make_client,
    plain_text,
    previous_day,
    read_json,
    root,
    sources_path,
    today_jst,
    url_key,
    write_json,
)

ATOM = "{http://www.w3.org/2005/Atom}"
GITHUB_API = "https://api.github.com"
REPO_PATH = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")
MAX_TITLE = 200


class ParseError(RuntimeError):
    pass


def to_int(text: str | None) -> int | None:
    digits = re.sub(r"[^0-9]", "", text or "")
    return int(digits) if digits else None


def to_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST).date()


# ---------- 読み取り（純粋関数。テストはここを中心に行う） ----------


def parse_github_trending(html: str, conf: dict[str, str]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    repos = []
    for row in soup.select(conf["row"]):
        link = row.select_one(conf["name_link"])
        match = REPO_PATH.match(link.get("href", "")) if link else None
        if not match:
            continue
        full_name = f"{match.group(1)}/{match.group(2)}"
        desc = row.select_one(conf["description"])
        lang = row.select_one(conf["language"])
        total = row.select_one(conf["stars_total"])
        today = row.select_one(conf["stars_today"])
        repos.append(
            {
                "title": full_name,
                "url": f"https://github.com/{full_name}",
                "description": desc.get_text(" ", strip=True) if desc else "",
                "language": lang.get_text(strip=True) if lang else None,
                "stars_today": to_int(today.get_text() if today else None),
                "stars_total": to_int(total.get_text() if total else None),
            }
        )
    if not repos:
        raise ParseError("GitHub Trending のページから項目を読み取れない")
    return repos


def parse_feed(body: bytes) -> list[dict[str, Any]]:
    """RSS 2.0 と Atom。並びはフィードのまま（人気の一覧なら順位の順）。"""
    root_el = ElementTree.fromstring(body)
    items = []

    def text(node: Element | None) -> str | None:
        return node.text if node is not None else None

    if root_el.tag == f"{ATOM}feed":
        for entry in root_el.iter(f"{ATOM}entry"):
            link = next(
                (
                    n.get("href")
                    for n in entry.findall(f"{ATOM}link")
                    if n.get("rel", "alternate") == "alternate"
                ),
                None,
            )
            items.append(
                {
                    "title": plain_text(text(entry.find(f"{ATOM}title")), MAX_TITLE),
                    "url": link or "",
                    "published": to_date(
                        text(entry.find(f"{ATOM}published")) or text(entry.find(f"{ATOM}updated"))
                    ),
                }
            )
    else:
        for item in root_el.iter("item"):
            items.append(
                {
                    "title": plain_text(text(item.find("title")), MAX_TITLE),
                    "url": (text(item.find("link")) or "").strip(),
                    "published": to_date(text(item.find("pubDate"))),
                }
            )
    return [i for i in items if i["url"].startswith("https://") and i["title"]]


def parse_zenn(body: bytes) -> list[dict[str, Any]]:
    data = json.loads(body)
    items = []
    for article in data.get("articles", []):
        path = article.get("path") or ""
        if not path.startswith("/") or path.startswith("//"):
            continue
        items.append(
            {
                "title": plain_text(article.get("title"), MAX_TITLE),
                "url": f"https://zenn.dev{path}",
                "published": to_date(article.get("published_at")),
                "likes": int(article.get("liked_count") or 0),
            }
        )
    return items


def parse_link_list(
    html: str, selector: str | None, pattern: str | None, base_url: str, exclude: str | None = None
) -> list[dict[str, Any]]:
    """一覧ページから記事のリンクを出現順に取る（DevelopersIO のランキング、RSS のないブログ）。"""
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select(selector) if selector else soup.find_all("a", href=True)
    regex = re.compile(pattern) if pattern else None
    items: dict[str, dict[str, Any]] = {}
    for a in anchors:
        href = a.get("href", "")
        if regex and not regex.match(href):
            continue
        url = clean_url(urljoin(base_url + "/", href))
        if not url.startswith("https://") or url in items:
            continue
        if exclude:
            for node in a.select(exclude):
                node.decompose()
        title = plain_text(a.get_text(" ", strip=True), MAX_TITLE)
        items[url] = {"title": title, "url": url, "published": None}
    if not items:
        raise ParseError("一覧ページから記事のリンクを読み取れない")
    return list(items.values())


# ---------- 情報源ごとの取得 ----------


_robots: RobotsChecker | None = None


def fetch_text(http: httpx.Client, url: str) -> bytes:
    """一覧ページやフィードを取る。robots.txt で禁止されていれば取らない。"""
    if _robots is not None and not _robots.allowed(url):
        raise FetchError("robots.txt で禁止されている")
    body, _ = fetch_bytes(http, url, max_bytes=5_000_000)
    return body


def github_search_fallback(
    http: httpx.Client, conf: dict[str, Any], day: date
) -> list[dict[str, Any]]:
    since = day - timedelta(days=conf.get("created_within_days", 7))
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = http.get(
        f"{GITHUB_API}/search/repositories",
        params={
            "q": f"created:>{since.isoformat()}",
            "sort": "stars",
            "order": "desc",
            "per_page": 10,
        },
        headers=headers,
    )
    if response.is_error:
        raise FetchError(f"GitHub Search API: HTTP {response.status_code}")
    return [
        {
            "title": repo["full_name"],
            "url": repo["html_url"],
            "description": repo.get("description") or "",
            "language": repo.get("language"),
            "stars_today": None,
            "stars_total": repo.get("stargazers_count"),
        }
        for repo in response.json().get("items", [])
    ]


def fetch_github(
    http: httpx.Client, conf: dict[str, Any], day: date
) -> tuple[list[dict[str, Any]], str | None]:
    """(リポジトリ, 注記) を返す。Trending が読めなければ Search API に切り替え、注記を付ける。"""
    try:
        repos = parse_github_trending(
            fetch_text(http, conf["trending"]["url"]).decode("utf-8", "replace"), conf["trending"]
        )
        note = None
    except (FetchError, ParseError) as error:
        gha_warning(f"GitHub Trending failed ({error}); falling back to Search API")
        repos = github_search_fallback(http, conf["fallback_search"], day)
        note = "Trendingを読み取れなかったため、直近に作成されたリポジトリのスター数順で代用"
    return repos[: conf["top"]], note


def fetch_ranking(http: httpx.Client, conf: dict[str, Any]) -> list[dict[str, Any]]:
    body = fetch_text(http, conf["url"])
    if conf["kind"] == "feed":
        items = parse_feed(body)
    elif conf["kind"] == "zenn_api":
        items = parse_zenn(body)
    else:
        items = parse_link_list(
            body.decode("utf-8", "replace"),
            conf.get("link"),
            None,
            conf["base_url"],
            conf.get("title_exclude"),
        )
        for item in items:
            item["title_source"] = "page"
    if not items:
        raise ParseError("ランキングが空")
    return items


def fetch_blog(http: httpx.Client, blog: dict[str, Any]) -> list[dict[str, Any]]:
    body = fetch_text(http, blog["url"])
    if blog["kind"] == "feed":
        return parse_feed(body)
    items = parse_link_list(
        body.decode("utf-8", "replace"), None, blog["link_pattern"], blog["base_url"]
    )
    for item in items:
        item["title_source"] = "page"
    return items


# ---------- 組み立て ----------


def blog_id(blog: dict[str, Any]) -> str:
    return f"{blog['company']}/{blog['blog']}"


def select_new_blog_items(
    items: list[dict[str, Any]],
    seen: list[str] | None,
    kind: str,
    day: date,
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    """既読URLにない記事を新着とする。

    - 初回（そのブログの既読が未記録）の一覧ページは、既読として記録するだけで新着にしない。
    - 公開日がわかる記事は max_age_days より古ければ新着にしない（フィードの古い記事の掘り起こし対策）。
    """
    if seen is None and kind == "page":
        return []
    known = set(seen or [])
    oldest = day - timedelta(days=rules.get("max_age_days", 7))
    fresh = [
        i
        for i in items
        if clean_url(i["url"]) not in known and (i["published"] is None or i["published"] >= oldest)
    ]
    return fresh[: rules.get("max_new_per_blog", 10)]


def ranking_entry(item: dict[str, Any], rank: int, prev: dict[str, int]) -> dict[str, Any]:
    url = clean_url(item["url"])
    entry = {
        "kind": "article",
        "rank": rank,
        "title": item["title"],
        "url": url,
        "published": item["published"].isoformat() if item.get("published") else None,
        "likes": item.get("likes"),
        "article_key": url_key(url),
        "streak_days": prev.get(url, 0) + 1,
        "jev": None,
    }
    if item.get("title_source"):
        entry["title_source"] = item["title_source"]
    return entry


def previous_streaks(day: date) -> dict[str, dict[str, int]]:
    """前日のファイルから、情報源ごとに URL → 連続日数 を作る。"""
    prev = read_json(sources_path(previous_day(day)), {})
    return {
        name: {i["url"]: i.get("streak_days", 1) for i in prev.get(name, [])}
        for name in ("github", "qiita", "zenn", "devio")
    }


def collect(http: httpx.Client, day: date) -> dict[str, Any]:
    global _robots
    _robots = RobotsChecker(http)
    conf = load_config("sources.json")
    seen_path = root() / "state" / "seen_urls.json"
    seen_state = read_json(seen_path, {"urls": {}})
    streaks = previous_streaks(day)
    out: dict[str, Any] = {
        "date": day.isoformat(),
        "github": [],
        "qiita": [],
        "zenn": [],
        "devio": [],
        "blogs": [],
        "reserve": {"qiita": [], "zenn": []},
        "status": {},
        "blog_status": [],
        "notes": {},
        "errors": [],
    }

    def fail(source: str, error: Exception) -> None:
        out["status"][source] = "error"
        out["errors"].append(
            {"source": source, "message": f"{type(error).__name__}: {error}"[:300]}
        )
        gha_warning(f"{source}: {error}")

    try:
        repos, note = fetch_github(http, conf["github"], day)
        for rank, repo in enumerate(repos, start=1):
            out["github"].append(
                {
                    "kind": "repo",
                    "rank": rank,
                    **repo,
                    "article_key": url_key(repo["url"]),
                    "streak_days": streaks["github"].get(repo["url"], 0) + 1,
                    "jev": None,
                }
            )
        out["status"]["github"] = "ok" if out["github"] else "none"
        if note:
            out["notes"]["github"] = note
    except Exception as error:  # noqa: BLE001 情報源ごとに失敗を閉じ込める
        fail("github", error)

    for name in ("qiita", "zenn", "devio"):
        source = conf[name]
        try:
            items = fetch_ranking(http, source)
            entries = [ranking_entry(i, r, streaks[name]) for r, i in enumerate(items, start=1)]
            out[name] = entries[: source["top"]]
            if name in out["reserve"]:
                out["reserve"][name] = entries[
                    source["top"] : source["top"] + source.get("reserve", 0)
                ]
            out["status"][name] = "ok" if out[name] else "none"
        except Exception as error:  # noqa: BLE001
            fail(name, error)

    for blog in conf["blogs"]:
        bid = blog_id(blog)
        status = {
            "company": blog["company"],
            "company_label": blog["company_label"],
            "blog": blog["blog"],
        }
        try:
            items = fetch_blog(http, blog)
        except Exception as error:  # noqa: BLE001
            out["blog_status"].append({**status, "status": "error", "count": 0})
            out["errors"].append(
                {"source": f"blog:{bid}", "message": f"{type(error).__name__}: {error}"[:300]}
            )
            gha_warning(f"blog {bid}: {error}")
            continue
        seen = seen_state["urls"].get(bid)
        new = select_new_blog_items(items, seen, blog["kind"], day, conf["blog_rules"])
        for item in new:
            url = clean_url(item["url"])
            entry = {
                "kind": "blog",
                "company": blog["company"],
                "company_label": blog["company_label"],
                "blog": blog["blog"],
                "title": item["title"],
                "url": url,
                "published": item["published"].isoformat() if item["published"] else None,
                "article_key": url_key(url),
                "jev": None,
            }
            if item.get("title_source"):
                entry["title_source"] = item["title_source"]
            out["blogs"].append(entry)
        # 一覧に出ている記事はすべて既読にする（新着の上限で落とした分も、翌日に掘り起こさない）
        merged = list(dict.fromkeys([clean_url(i["url"]) for i in items] + (seen or [])))[:500]
        seen_state["urls"][bid] = merged
        out["blog_status"].append({**status, "status": "new" if new else "none", "count": len(new)})

    blog_errors = [b for b in out["blog_status"] if b["status"] == "error"]
    out["status"]["blogs"] = (
        "error" if len(blog_errors) == len(conf["blogs"]) else ("ok" if out["blogs"] else "none")
    )
    write_json(seen_path, seen_state)
    return out


def main() -> int:
    day = today_jst()
    with make_client() as http:
        data = collect(http, day)
    write_json(sources_path(day), data)
    counts = {k: len(data[k]) for k in ("github", "qiita", "zenn", "devio", "blogs")}
    print(f"sources: {counts} errors={len(data['errors'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
