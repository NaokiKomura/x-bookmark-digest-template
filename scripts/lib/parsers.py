"""取得したページ・フィードから項目を読み取る（純粋関数。通信しない）。

サイト側の変更で壊れるのはここと config/sources.json のセレクタだけ。直すときは
tests/fixtures/sources/ のスナップショットを新しいページに差し替えてからテストを合わせる（docs/recipes.md）。
読み取れなかったときは ParseError を出す（呼び出し側が「取得失敗」として記録する）。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, TypedDict
from urllib.parse import urljoin
from xml.etree.ElementTree import Element

from bs4 import BeautifulSoup
from defusedxml import ElementTree

from scripts.lib.store import JST
from scripts.lib.urls import clean_url, plain_text

ATOM = "{http://www.w3.org/2005/Atom}"
REPO_PATH = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")
MAX_TITLE = 200
MAX_SUMMARY = 500


class ParseError(RuntimeError):
    """ページやフィードから項目を読み取れない（構造が変わった可能性が高い）。"""


class ParsedRepo(TypedDict):
    title: str
    url: str
    description: str
    language: str | None
    stars_today: int | None
    stars_total: int | None


class ParsedEntry(TypedDict, total=False):
    title: str
    url: str
    published: date | None
    likes: int
    title_source: str
    summary: str
    """フィードの概要（RSS の description、Atom の summary）。本文が取れないときの代わりに使う。"""


def to_int(text: str | None) -> int | None:
    digits = re.sub(r"[^0-9]", "", text or "")
    return int(digits) if digits else None


def to_date(value: str | None) -> date | None:
    """ISO 8601 と RFC 2822 の日時を日本時間の日付にする。"""
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


def github_trending(html: str, selectors: dict[str, str]) -> list[ParsedRepo]:
    """GitHub Trending のページ。selectors は config/sources.json の github.trending。"""
    soup = BeautifulSoup(html, "html.parser")
    repos: list[ParsedRepo] = []
    for row in soup.select(selectors["row"]):
        link = row.select_one(selectors["name_link"])
        match = REPO_PATH.match(str(link.get("href", ""))) if link else None
        if not match:
            continue
        full_name = f"{match.group(1)}/{match.group(2)}"
        desc = row.select_one(selectors["description"])
        lang = row.select_one(selectors["language"])
        total = row.select_one(selectors["stars_total"])
        today = row.select_one(selectors["stars_today"])
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


def feed(body: bytes) -> list[ParsedEntry]:
    """RSS 2.0 と Atom。並びはフィードのまま（人気の一覧なら順位の順）。https 以外の URL は捨てる。"""
    try:
        root_el = ElementTree.fromstring(body)
    except Exception as error:  # noqa: BLE001 壊れた XML は読み取り失敗として扱う
        raise ParseError(f"フィードを XML として読めない: {type(error).__name__}") from error

    def text(node: Element | None) -> str | None:
        return node.text if node is not None else None

    items: list[ParsedEntry] = []
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
                    "summary": plain_text(
                        text(entry.find(f"{ATOM}summary")) or text(entry.find(f"{ATOM}content")),
                        MAX_SUMMARY,
                    ),
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
                    "summary": plain_text(text(item.find("description")), MAX_SUMMARY),
                }
            )
    return [i for i in items if i["url"].startswith("https://") and i["title"]]


def zenn_api(body: bytes) -> list[ParsedEntry]:
    """Zenn の記事一覧の JSON（/api/articles）。"""
    try:
        data = json.loads(body)
    except ValueError as error:
        raise ParseError("Zenn の応答を JSON として読めない") from error
    items: list[ParsedEntry] = []
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


def link_list(
    html: str,
    base_url: str,
    selector: str | None = None,
    pattern: str | None = None,
    exclude: str | None = None,
) -> list[ParsedEntry]:
    """一覧ページから記事のリンクを出現順に取る（DevelopersIO のランキング、RSS のないブログ）。

    selector: リンクを選ぶ CSS セレクタ（なければすべての a）
    pattern: href が一致すべき正規表現
    exclude: 見出しから除く子要素の CSS セレクタ（バッジなど）
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select(selector) if selector else soup.find_all("a", href=True)
    regex = re.compile(pattern) if pattern else None
    items: dict[str, ParsedEntry] = {}
    for a in anchors:
        href = str(a.get("href", ""))
        if regex and not regex.match(href):
            continue
        url = clean_url(urljoin(base_url + "/", href))
        if not url.startswith("https://") or url in items:
            continue
        if exclude:
            for node in a.select(exclude):
                node.decompose()
        items[url] = {
            "title": plain_text(a.get_text(" ", strip=True), MAX_TITLE),
            "url": url,
            "published": None,
            "title_source": "page",
        }
    if not items:
        raise ParseError("一覧ページから記事のリンクを読み取れない")
    return list(items.values())


def github_search(data: dict[str, Any]) -> list[ParsedRepo]:
    """GitHub Search API（/search/repositories）の応答。Trending が読めない日の代わり。"""
    return [
        {
            "title": repo["full_name"],
            "url": repo["html_url"],
            "description": repo.get("description") or "",
            "language": repo.get("language"),
            "stars_today": None,
            "stars_total": repo.get("stargazers_count"),
        }
        for repo in data.get("items", [])
    ]
