"""手順3: GitHubトレンド・ランキング（Qiita、Zenn、DevelopersIO）・公式ブログの取得。

取得先と読み取り方は config/sources.json、読み取りの処理は scripts/lib/parsers.py にある。
ページを読み取る情報源はサイト側の変更で動かなくなる前提で扱い、失敗した情報源は errors に記録して
ほかの情報源の処理を続ける。

入力: config/sources.json、前日の data/sources/YYYY-MM-DD.json（連続日数）、state/seen_urls.json
出力: data/sources/YYYY-MM-DD.json（SourcesFile。本文と Jev の結果は後続のスクリプトが足す）、state/seen_urls.json
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from typing import Any

import httpx

from scripts.lib import actions, parsers
from scripts.lib.models import (
    BlogItem,
    BlogStatus,
    RankingItem,
    RankingSite,
    RepoItem,
    SeenUrls,
    SourcesFile,
)
from scripts.lib.parsers import ParsedEntry, ParsedRepo, ParseError
from scripts.lib.store import (
    load_config,
    previous_day,
    read_json,
    seen_urls_path,
    sources_path,
    today_jst,
    write_json,
)
from scripts.lib.urls import clean_url, url_key
from scripts.lib.web import FetchError, RobotsChecker, fetch_allowed, make_client

GITHUB_API = "https://api.github.com"
MAX_LIST_BYTES = 5_000_000
MAX_SEEN_PER_BLOG = 500
RANKINGS: tuple[RankingSite, ...] = ("qiita", "zenn", "devio")


class Fetcher:
    """一覧ページとフィードの取得（robots.txt を確かめてから取る）。"""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        self.robots = RobotsChecker(http)

    def get(self, url: str) -> bytes:
        return fetch_allowed(self.http, self.robots, url, MAX_LIST_BYTES)


# ---------- 情報源ごとの取得 ----------


def github_search_fallback(http: httpx.Client, conf: dict[str, Any], day: date) -> list[ParsedRepo]:
    """直近に作成されたリポジトリをスター数順に取る（GitHub Search API、GITHUB_TOKEN）。"""
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
    return parsers.github_search(response.json())


def fetch_github(
    fetcher: Fetcher, conf: dict[str, Any], day: date
) -> tuple[list[ParsedRepo], str | None]:
    """(リポジトリ, 注記) を返す。Trending が読めなければ Search API に切り替え、注記を付ける。"""
    try:
        html = fetcher.get(conf["trending"]["url"]).decode("utf-8", "replace")
        return parsers.github_trending(html, conf["trending"])[: conf["top"]], None
    except (FetchError, ParseError) as error:
        actions.warning(f"GitHub Trending failed ({error}); falling back to Search API")
        repos = github_search_fallback(fetcher.http, conf["fallback_search"], day)
        return repos[
            : conf["top"]
        ], "Trendingを読み取れなかったため、直近に作成されたリポジトリのスター数順で代用"


def fetch_ranking(fetcher: Fetcher, conf: dict[str, Any]) -> list[ParsedEntry]:
    body = fetcher.get(conf["url"])
    if conf["kind"] == "feed":
        items = parsers.feed(body)
    elif conf["kind"] == "zenn_api":
        items = parsers.zenn_api(body)
    else:
        items = parsers.link_list(
            body.decode("utf-8", "replace"),
            conf["base_url"],
            selector=conf.get("link"),
            exclude=conf.get("title_exclude"),
        )
    if not items:
        raise ParseError("ランキングが空")
    return items


def fetch_blog(fetcher: Fetcher, blog: dict[str, Any]) -> list[ParsedEntry]:
    body = fetcher.get(blog["url"])
    if blog["kind"] == "feed":
        return parsers.feed(body)
    return parsers.link_list(
        body.decode("utf-8", "replace"), blog["base_url"], pattern=blog["link_pattern"]
    )


# ---------- 組み立て ----------


def blog_id(blog: dict[str, Any]) -> str:
    """state/seen_urls.json のキー。"""
    return f"{blog['company']}/{blog['blog']}"


def select_new_blog_items(
    items: list[ParsedEntry],
    seen: list[str] | None,
    kind: str,
    day: date,
    rules: dict[str, Any],
) -> list[ParsedEntry]:
    """既読 URL にない記事を新着とする。

    - 初回（そのブログの既読が未記録）の一覧ページは、既読として記録するだけで新着にしない。
    - 公開日がわかる記事は max_age_days より古ければ新着にしない（フィードの古い記事の掘り起こし対策）。
    """
    if seen is None and kind == "page":
        return []
    known = set(seen or [])
    oldest = day - timedelta(days=rules.get("max_age_days", 3))

    def is_fresh(item: ParsedEntry) -> bool:
        published = item.get("published")
        return clean_url(item["url"]) not in known and (published is None or published >= oldest)

    fresh = [i for i in items if is_fresh(i)]
    return fresh[: rules.get("max_new_per_blog", 10)]


def iso_or_none(value: date | None) -> str | None:
    return value.isoformat() if value else None


def to_ranking_item(parsed: ParsedEntry, rank: int, prev: dict[str, int]) -> RankingItem:
    url = clean_url(parsed["url"])
    item: RankingItem = {
        "kind": "article",
        "rank": rank,
        "title": parsed["title"],
        "url": url,
        "published": iso_or_none(parsed.get("published")),
        "likes": parsed.get("likes"),
        "summary": parsed.get("summary", ""),
        "article_key": url_key(url),
        "streak_days": prev.get(url, 0) + 1,
        "jev": None,
    }
    if parsed.get("title_source") == "page":
        item["title_source"] = "page"
    return item


def to_repo_item(repo: ParsedRepo, rank: int, prev: dict[str, int]) -> RepoItem:
    return {
        "kind": "repo",
        "rank": rank,
        **repo,
        "article_key": url_key(repo["url"]),
        "streak_days": prev.get(repo["url"], 0) + 1,
        "jev": None,
    }


def to_blog_item(parsed: ParsedEntry, blog: dict[str, Any]) -> BlogItem:
    url = clean_url(parsed["url"])
    item: BlogItem = {
        "kind": "blog",
        "company": blog["company"],
        "company_label": blog["company_label"],
        "blog": blog["blog"],
        "title": parsed["title"],
        "url": url,
        "published": iso_or_none(parsed.get("published")),
        "summary": parsed.get("summary", ""),
        "article_key": url_key(url),
        "jev": None,
    }
    if parsed.get("title_source") == "page":
        item["title_source"] = "page"
    return item


def previous_streaks(day: date) -> dict[str, dict[str, int]]:
    """前日のファイルから、情報源ごとに URL → 連続日数 を作る。"""
    prev = read_json(sources_path(previous_day(day)), {})
    return {
        name: {i["url"]: i.get("streak_days", 1) for i in prev.get(name, [])}
        for name in ("github", *RANKINGS)
    }


def empty_sources(day: date) -> SourcesFile:
    return {
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


def collect(http: httpx.Client, day: date) -> SourcesFile:
    conf = load_config("sources.json")
    fetcher = Fetcher(http)
    seen_state: SeenUrls = read_json(seen_urls_path(), {"urls": {}})
    streaks = previous_streaks(day)
    out = empty_sources(day)

    def record_error(source: str, error: Exception) -> None:
        out["errors"].append(
            {"source": source, "message": f"{type(error).__name__}: {error}"[:300]}
        )
        actions.warning(f"{source}: {error}")

    # GitHub トレンド
    try:
        repos, note = fetch_github(fetcher, conf["github"], day)
        out["github"] = [
            to_repo_item(r, rank, streaks["github"]) for rank, r in enumerate(repos, 1)
        ]
        out["status"]["github"] = "ok" if out["github"] else "none"
        if note:
            out["notes"]["github"] = note
    except Exception as error:  # noqa: BLE001 情報源ごとに失敗を閉じ込める
        out["status"]["github"] = "error"
        record_error("github", error)

    # Qiita・Zenn・DevelopersIO のランキング（Qiita・Zenn は補充用の次点も取っておく）
    for name in RANKINGS:
        source = conf[name]
        try:
            items = fetch_ranking(fetcher, source)
            entries = [to_ranking_item(i, rank, streaks[name]) for rank, i in enumerate(items, 1)]
            top = source["top"]
            out[name] = entries[:top]
            if name != "devio":
                out["reserve"][name] = entries[top : top + source.get("reserve", 0)]
            out["status"][name] = "ok" if entries else "none"
        except Exception as error:  # noqa: BLE001
            out["status"][name] = "error"
            record_error(name, error)

    # 公式テックブログ
    for blog in conf["blogs"]:
        bid = blog_id(blog)
        status: BlogStatus = {
            "company": blog["company"],
            "company_label": blog["company_label"],
            "blog": blog["blog"],
            "status": "none",
            "count": 0,
        }
        try:
            items = fetch_blog(fetcher, blog)
        except Exception as error:  # noqa: BLE001
            out["blog_status"].append({**status, "status": "error"})
            record_error(f"blog:{bid}", error)
            continue
        seen = seen_state["urls"].get(bid)
        new = select_new_blog_items(items, seen, blog["kind"], day, conf["blog_rules"])
        out["blogs"].extend(to_blog_item(i, blog) for i in new)
        # 一覧に出ている記事はすべて既読にする（新着の上限で落とした分も、翌日に掘り起こさない）
        listed = [clean_url(i["url"]) for i in items]
        seen_state["urls"][bid] = list(dict.fromkeys(listed + (seen or [])))[:MAX_SEEN_PER_BLOG]
        out["blog_status"].append({**status, "status": "new" if new else "none", "count": len(new)})

    all_failed = all(b["status"] == "error" for b in out["blog_status"])
    out["status"]["blogs"] = "error" if all_failed else ("ok" if out["blogs"] else "none")
    write_json(seen_urls_path(), seen_state)
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
