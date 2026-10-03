"""リンク先記事・README・ブログ本文の取得と抽出（処理フロー 4）。

記事の取得ルール:
1. x.com、twitter.com、画像・動画の直リンクは記事として扱わない（fetch_bookmarks.py の時点で除く）。
2. URL のハッシュをキーに data/articles/ を確認し、取得済みなら再取得しない。
3. robots.txt で禁止されているサイトは取得しない（fetch_status: blocked）。
4. タイムアウト10秒、上限2MBで取得し、trafilatura でタイトル、サイト名、公開日、本文を取り出す。
5. 本文は先頭から最大20,000文字で保存する。500文字未満なら partial。
取得エラーは error として記録し、翌日以降も再取得しない。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import httpx
import trafilatura

from scripts.common import (
    MAX_ARTICLE_CHARS,
    FetchError,
    RobotsChecker,
    article_path,
    bookmarks_path,
    domain_of,
    fetch_bytes,
    make_client,
    now_iso,
    read_json,
    sources_path,
    today_jst,
    write_json,
)

PARTIAL_CHARS = 500
GITHUB_API = "https://api.github.com"


def extract(html: bytes, url: str) -> dict[str, Any]:
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True, include_comments=False)
    if doc is None:
        return {}
    data = doc if isinstance(doc, dict) else doc.as_dict()
    return {
        "title": data.get("title") or "",
        "site_name": data.get("sitename") or "",
        "published": data.get("date") or None,
        "text": (data.get("text") or "").strip(),
    }


def build_record(
    key: str, url: str, status: str, info: dict[str, Any] | None = None
) -> dict[str, Any]:
    info = info or {}
    text = (info.get("text") or "")[:MAX_ARTICLE_CHARS]
    return {
        "key": key,
        "url": url,
        "domain": domain_of(url),
        "title": info.get("title") or "",
        "site_name": info.get("site_name") or "",
        "published": info.get("published"),
        "fetched_at": now_iso(),
        "fetch_status": status,
        "chars": len(info.get("text") or ""),
        "text": text,
        **({"error": info["error"]} if info.get("error") else {}),
    }


def fetch_article(http: httpx.Client, robots: RobotsChecker, key: str, url: str) -> dict[str, Any]:
    """取得済みならキャッシュを返す。なければ取得して data/articles/<key>.json に保存する。"""
    path = article_path(key)
    cached = read_json(path, None)
    if cached is not None:
        return cached
    if not robots.allowed(url):
        record = build_record(key, url, "blocked")
    else:
        try:
            body, final_url = fetch_bytes(http, url)
            info = extract(body, final_url)
            if not info.get("text"):
                record = build_record(key, url, "partial", info)
            else:
                status = "ok" if len(info["text"]) >= PARTIAL_CHARS else "partial"
                record = build_record(key, url, status, info)
        except FetchError as error:
            record = build_record(key, url, "error", {"error": str(error)})
        except Exception as error:  # noqa: BLE001 抽出ライブラリの想定外の失敗も1件の失敗に閉じ込める
            record = build_record(key, url, "error", {"error": type(error).__name__})
    write_json(path, record)
    return record


def fetch_readme(http: httpx.Client, key: str, repo: dict[str, Any]) -> dict[str, Any]:
    """README は GitHub API（GITHUB_TOKEN）で取る。"""
    path = article_path(key)
    cached = read_json(path, None)
    if cached is not None:
        return cached
    headers = {"Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    info: dict[str, Any] = {"title": repo["title"], "site_name": "GitHub"}
    try:
        response = http.get(f"{GITHUB_API}/repos/{repo['title']}/readme", headers=headers)
        if response.is_error:
            raise FetchError(f"HTTP {response.status_code}")
        info["text"] = response.text
        status = "ok"
    except (FetchError, httpx.HTTPError) as error:
        info["error"] = str(error) or type(error).__name__
        status = "error"
    record = build_record(key, repo["url"], status, info)
    write_json(path, record)
    return record


def apply_page_title(item: dict[str, Any], record: dict[str, Any]) -> None:
    """一覧ページから拾った見出しは崩れやすいので、記事から取れたタイトルがあれば置き換える。"""
    if item.get("title_source") == "page" and record.get("title"):
        item["title"] = strip_site_suffix(record["title"])[:200]
        item["title_source"] = "article"


def strip_site_suffix(title: str) -> str:
    """「記事名 | DevelopersIO」のような末尾のサイト名を外す。"""
    stripped = re.sub(r"\s*[|｜]\s*[^|｜]{1,40}$", "", title).strip()
    return stripped or title


def run(http: httpx.Client) -> dict[str, int]:
    day = today_jst()
    robots = RobotsChecker(http)
    counts: dict[str, int] = {}

    def tally(record: dict[str, Any]) -> None:
        counts[record["fetch_status"]] = counts.get(record["fetch_status"], 0) + 1

    bpath = bookmarks_path(day)
    bookmarks = read_json(bpath, None)
    if bookmarks:
        for post in bookmarks["posts"]:
            for link in post["links"]:
                tally(fetch_article(http, robots, link["article_key"], link["url"]))

    spath = sources_path(day)
    sources = read_json(spath, None)
    if sources:
        for repo in sources.get("github", []):
            tally(fetch_readme(http, repo["article_key"], repo))
        for name in ("qiita", "zenn", "devio", "blogs"):
            for item in sources.get(name, []):
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
