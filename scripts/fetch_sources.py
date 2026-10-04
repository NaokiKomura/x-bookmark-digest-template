"""手順3: GitHubトレンド・ランキング（Qiita、Zenn、DevelopersIO）・公式ブログの取得。

取得先と読み取り方は config/sources.json、読み取りの処理は scripts/lib/parsers.py にある。
ページを読み取る情報源はサイト側の変更で動かなくなる前提で扱い、失敗した情報源は errors に記録して
ほかの情報源の処理を続ける。

config/enabled.json（初回セットアップで選んだ情報源）にない情報源は取得しない。

公式ブログは、どのブログも公開日が max_age_days（3日）以内の記事だけを新着にする。
公開日が一覧やフィードにない記事は本文を取って日付を読む（その本文は fetch_articles が使い回す）。

入力: config/sources.json、前日の data/sources/YYYY-MM-DD.json（連続日数）、state/seen_urls.json
出力: data/sources/YYYY-MM-DD.json（SourcesFile。本文と Jev の結果は後続のスクリプトが足す）、state/seen_urls.json
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import httpx

from scripts.fetch_articles import fetch_article
from scripts.lib import actions, parsers
from scripts.lib.models import (
    BlogItem,
    BlogStatus,
    ExcludedFile,
    RankingItem,
    RankingSite,
    RepoItem,
    SeenUrls,
    SourcesFile,
    TechJev,
)
from scripts.lib.parsers import ParsedEntry, ParsedRepo, ParseError
from scripts.lib.store import (
    excluded_path,
    load_config,
    load_enabled,
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


def article_date(fetcher: Fetcher, item: ParsedEntry) -> date | None:
    """記事の本文を取って公開日を読む。取った本文は data/articles/ に残り、fetch_articles が使い回す。"""
    url = clean_url(item["url"])
    record = fetch_article(fetcher.http, fetcher.robots, url_key(url), url)
    return parsers.to_date(record.get("published"))


def blog_id(blog: dict[str, Any]) -> str:
    """state/seen_urls.json のキー。"""
    return f"{blog['company']}/{blog['blog']}"


def select_new_blog_items(
    items: list[ParsedEntry],
    seen: list[str] | None,
    day: date,
    rules: dict[str, Any],
    date_of: Callable[[ParsedEntry], date | None],
) -> list[ParsedEntry]:
    """既読 URL にない記事のうち、公開日が max_age_days 以内のものを新着とする（どのブログも同じ期間）。

    - 公開日が一覧やフィードにない記事は、date_of で記事の本文から読む（1ブログ max_new_per_blog 件まで）。
    - それでも日付がわからない記事は、そのブログの初回（既読が未記録）だけ新着にしない
      （過去の記事を一度に新着扱いしないため）。2回目以降は前回の一覧との差分なので新着にする。
    """
    first_run = seen is None
    known = set(seen or [])
    oldest = day - timedelta(days=rules.get("max_age_days", 3))
    limit = rules.get("max_new_per_blog", 10)
    fresh: list[ParsedEntry] = []
    lookups = 0
    for item in items:
        if len(fresh) >= limit:
            break
        if clean_url(item["url"]) in known:
            continue
        published = item.get("published")
        if published is None and lookups < limit:
            lookups += 1
            published = date_of(item)
            if published is not None:
                item = {**item, "published": published}
        if published is None:
            if first_run:
                continue
        elif published < oldest:
            continue
        fresh.append(item)
    return fresh


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
    saved: SourcesFile = read_json(sources_path(day), empty_sources(day))
    saved_excluded: ExcludedFile = read_json(
        excluded_path(day), {"date": day.isoformat(), "items": []}
    )
    out["blogs"] = list({clean_url(i["url"]): i for i in saved["blogs"]}.values())

    enabled = load_enabled()

    def wanted(source_id: str) -> bool:
        """初回セットアップで外した情報源は取りに行かない（config/enabled.json がなければすべて）。"""
        return enabled is None or source_id in enabled

    def record_error(source: str, error: Exception) -> None:
        out["errors"].append(
            {"source": source, "message": f"{type(error).__name__}: {error}"[:300]}
        )
        actions.warning(f"{source}: {error}")

    def restore_judgments(name: str, entries: list[RepoItem] | list[RankingItem]) -> None:
        saved_by_source: dict[str, list[RepoItem] | list[RankingItem]] = {
            "github": saved["github"],
            "qiita": saved["qiita"],
            "zenn": saved["zenn"],
            "devio": saved["devio"],
        }
        saved_items: list[RepoItem | RankingItem] = list(saved_by_source[name])
        reserve = saved.get("reserve")
        if reserve and name in ("qiita", "zenn"):
            saved_items.extend(reserve["qiita"] if name == "qiita" else reserve["zenn"])
        known = {i["article_key"]: i.get("jev") for i in saved_items}
        for excluded in saved_excluded["items"]:
            if excluded["source"] != name or excluded["tech_prob"] is None:
                continue
            if known.get(excluded["id"]) is None:
                # 除外したランキング項目は当日の配列から外れている。履歴から判定を復元する。
                # 除外項目のトピックは使わない。
                judgment: TechJev = {
                    "status": "ok",
                    "tech_label": "excluded",
                    "tech_prob": excluded["tech_prob"],
                    "topic": None,
                    "topic_prob": None,
                }
                known[excluded["id"]] = judgment
        for item in entries:
            jev = known.get(item["article_key"])
            if jev and jev["status"] == "ok":
                item["jev"] = jev

    # GitHub トレンド
    if wanted("github"):
        try:
            repos, note = fetch_github(fetcher, conf["github"], day)
            out["github"] = [
                to_repo_item(r, rank, streaks["github"]) for rank, r in enumerate(repos, 1)
            ]
            restore_judgments("github", out["github"])
            out["status"]["github"] = "ok" if out["github"] else "none"
            if note:
                out["notes"]["github"] = note
        except Exception as error:  # noqa: BLE001 情報源ごとに失敗を閉じ込める
            out["status"]["github"] = "error"
            record_error("github", error)

    # Qiita・Zenn・DevelopersIO のランキング（Qiita・Zenn は補充用の次点も取っておく）
    for name in RANKINGS:
        if not wanted(name):
            continue
        source = conf[name]
        try:
            items = fetch_ranking(fetcher, source)
            entries = [to_ranking_item(i, rank, streaks[name]) for rank, i in enumerate(items, 1)]
            restore_judgments(name, entries)
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
        if not wanted(blog["company"]):
            continue
        bid = blog_id(blog)
        status: BlogStatus = {
            "company": blog["company"],
            "company_label": blog["company_label"],
            "blog": blog["blog"],
            "status": "none",
            "count": sum(
                i["company"] == blog["company"] and i["blog"] == blog["blog"] for i in out["blogs"]
            ),
        }
        try:
            items = fetch_blog(fetcher, blog)
        except Exception as error:  # noqa: BLE001
            out["blog_status"].append({**status, "status": "error"})
            record_error(f"blog:{bid}", error)
            continue
        seen = seen_state["urls"].get(bid)
        new = select_new_blog_items(
            items, seen, day, conf["blog_rules"], lambda item: article_date(fetcher, item)
        )
        known = {clean_url(i["url"]) for i in out["blogs"]}
        added = [to_blog_item(i, blog) for i in new if clean_url(i["url"]) not in known]
        out["blogs"].extend({i["url"]: i for i in added}.values())
        # 一覧に出ている記事はすべて既読にする（新着の上限で落とした分も、翌日に掘り起こさない）
        listed = [clean_url(i["url"]) for i in items]
        seen_state["urls"][bid] = list(dict.fromkeys(listed + (seen or [])))[:MAX_SEEN_PER_BLOG]
        count = sum(
            i["company"] == blog["company"] and i["blog"] == blog["blog"] for i in out["blogs"]
        )
        out["blog_status"].append({**status, "status": "new" if count else "none", "count": count})

    if out["blog_status"]:
        all_failed = all(b["status"] == "error" for b in out["blog_status"])
        out["status"]["blogs"] = "error" if all_failed else ("ok" if out["blogs"] else "none")
    # 新着を日別ファイルへ確定してから既読を進める。保存失敗で新着を失わないためである。
    write_json(sources_path(day), out)
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
