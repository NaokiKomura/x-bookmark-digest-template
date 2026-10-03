"""手順4: リンク先記事・README・ブログ本文の取得と抽出。

入力: data/YYYY-MM-DD.json の links、data/sources/YYYY-MM-DD.json の各項目
出力: data/articles/<article_key>.json（ArticleRecord）。一覧ページ由来の見出しは記事のタイトルで置き換える

記事の取得ルール:
1. x.com、twitter.com、画像・動画の直リンクは記事として扱わない（fetch_bookmarks.py の時点で除く）。
2. article_key（URL のハッシュ）で data/articles/ を確認し、取得済みなら再取得しない。
3. robots.txt で禁止されているサイトは取得しない（fetch_status: blocked）。
4. 通信待ち10秒、robots.txtと本文を合わせた総時間30秒、上限2MBで取得し、trafilatura でタイトル、サイト名、公開日、本文を取り出す。
5. 本文は先頭から最大20,000文字で保存する。500文字未満なら partial。
6. ブックマークは1投稿あたり先頭の MAX_LINKS_PER_POST 本だけ取る（まとめ投稿で数十本のリンクがあるため）。
取得エラーは error として記録し、翌日以降も再取得しない。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, TypedDict

import httpx
import trafilatura

from scripts.lib.models import (
    ArticleRecord,
    BlogItem,
    BookmarksFile,
    FetchStatus,
    RankingItem,
    RepoItem,
    SourcesFile,
)
from scripts.lib.store import (
    article_path,
    bookmarks_path,
    now_iso,
    read_json,
    sources_path,
    today_jst,
    write_json,
)
from scripts.lib.urls import domain_of
from scripts.lib.web import FetchError, RobotsChecker, fetch_bytes, fetch_deadline, make_client

MAX_ARTICLE_CHARS = 20_000
PARTIAL_CHARS = 500
MAX_LINKS_PER_POST = 5
"""Jev の判定には先頭2本、レポートには主な1本を使う。残りは取らない。"""
GITHUB_API = "https://api.github.com"


class Extracted(TypedDict, total=False):
    title: str
    site_name: str
    published: str | None
    text: str
    error: str


def extract(html: bytes, url: str) -> Extracted:
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True, include_comments=False)
    if doc is None:
        return {}
    data: dict[str, Any] = doc if isinstance(doc, dict) else doc.as_dict()
    return {
        "title": data.get("title") or "",
        "site_name": data.get("sitename") or "",
        "published": data.get("date") or None,
        "text": (data.get("text") or "").strip(),
    }


def build_record(
    key: str, url: str, status: FetchStatus, info: Extracted | None = None
) -> ArticleRecord:
    info = info or {}
    full_text = info.get("text") or ""
    record: ArticleRecord = {
        "key": key,
        "url": url,
        "domain": domain_of(url),
        "title": info.get("title") or "",
        "site_name": info.get("site_name") or "",
        "published": info.get("published"),
        "fetched_at": now_iso(),
        "fetch_status": status,
        "chars": len(full_text),
        "text": full_text[:MAX_ARTICLE_CHARS],
    }
    if info.get("error"):
        record["error"] = info["error"]
    return record


def fetch_article(http: httpx.Client, robots: RobotsChecker, key: str, url: str) -> ArticleRecord:
    """取得済みならキャッシュを返す。なければ取得して data/articles/<key>.json に保存する。"""
    path = article_path(key)
    cached: ArticleRecord | None = read_json(path, None)
    if cached is not None:
        return cached
    record: ArticleRecord
    try:
        with fetch_deadline():
            if not robots.allowed(url):
                record = build_record(key, url, "blocked")
            else:
                body, final_url = fetch_bytes(http, url)
                info = extract(body, final_url)
                status: FetchStatus = (
                    "ok" if len(info.get("text") or "") >= PARTIAL_CHARS else "partial"
                )
                record = build_record(key, url, status, info)
    except FetchError as error:
        record = build_record(key, url, "error", {"error": str(error)})
    except Exception as error:  # noqa: BLE001 抽出の失敗も1件の失敗に閉じ込める
        record = build_record(key, url, "error", {"error": type(error).__name__})
    write_json(path, record)
    return record


def fetch_readme(http: httpx.Client, key: str, repo: RepoItem) -> ArticleRecord:
    """README は GitHub API（GITHUB_TOKEN）で取る。"""
    path = article_path(key)
    cached: ArticleRecord | None = read_json(path, None)
    if cached is not None:
        return cached
    headers = {"Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    info: Extracted = {"title": repo["title"], "site_name": "GitHub"}
    status: FetchStatus
    try:
        body, _ = fetch_bytes(http, f"{GITHUB_API}/repos/{repo['title']}/readme", headers=headers)
        info["text"] = body.decode("utf-8", "replace")
        status = "ok"
    except (FetchError, httpx.HTTPError) as error:
        info["error"] = str(error) or type(error).__name__
        status = "error"
    record = build_record(key, repo["url"], status, info)
    write_json(path, record)
    return record


def strip_site_suffix(title: str) -> str:
    """「記事名 | DevelopersIO」のような末尾のサイト名を外す。"""
    stripped = re.sub(r"\s*[|｜]\s*[^|｜]{1,40}$", "", title).strip()
    return stripped or title


def apply_page_title(item: RankingItem | BlogItem, record: ArticleRecord) -> None:
    """一覧ページから拾った見出しは崩れやすいので、記事から取れたタイトルがあれば置き換える。"""
    if item.get("title_source") == "page" and record["title"]:
        item["title"] = strip_site_suffix(record["title"])[:200]
        item["title_source"] = "article"


def run(http: httpx.Client) -> dict[str, int]:
    """当日のファイルにある記事をすべて取る。fetch_status ごとの件数を返す。"""
    day = today_jst()
    robots = RobotsChecker(http)
    counts: dict[str, int] = {}

    def tally(record: ArticleRecord) -> None:
        counts[record["fetch_status"]] = counts.get(record["fetch_status"], 0) + 1

    bookmarks: BookmarksFile | None = read_json(bookmarks_path(day), None)
    if bookmarks:
        for post in bookmarks["posts"]:
            for link in post["links"][:MAX_LINKS_PER_POST]:
                tally(fetch_article(http, robots, link["article_key"], link["url"]))

    spath = sources_path(day)
    sources: SourcesFile | None = read_json(spath, None)
    if sources:
        for repo in sources.get("github", []):
            tally(fetch_readme(http, repo["article_key"], repo))
        items: list[RankingItem | BlogItem] = [
            *sources.get("qiita", []),
            *sources.get("zenn", []),
            *sources.get("devio", []),
            *sources.get("blogs", []),
        ]
        for item in items:
            record = fetch_article(http, robots, item["article_key"], item["url"])
            apply_page_title(item, record)
            tally(record)
        write_json(spath, sources)
    return counts


def main() -> int:
    with make_client() as http:
        counts = run(http)
    print(f"articles: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
