"""取得層の各スクリプトで共通に使うもの（パス、日付、JSONの読み書き、URL、HTTP）。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import robotparser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx

JST = ZoneInfo("Asia/Tokyo")
REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SECONDS = 10.0
MAX_BYTES = 2_000_000
MAX_ARTICLE_CHARS = 20_000
NON_ARTICLE_HOSTS = (
    "x.com",
    "twitter.com",
    "t.co",
    "pic.twitter.com",
    "pbs.twimg.com",
    "video.twimg.com",
)
MEDIA_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".heic",
    ".mp4",
    ".mov",
    ".webm",
    ".m4v",
    ".avi",
    ".mp3",
    ".m4a",
    ".wav",
)


def root() -> Path:
    """データを読み書きする場所。テストでは DIGEST_ROOT で一時ディレクトリに向ける。"""
    return Path(os.environ.get("DIGEST_ROOT", REPO_ROOT))


def today_jst(now: datetime | None = None) -> date:
    """レポートの日付。`DIGEST_DATE`（YYYY-MM-DD）があればそれを使う（手動の再実行用）。"""
    override = os.environ.get("DIGEST_DATE")
    if override:
        return date.fromisoformat(override)
    return (now or datetime.now(JST)).astimezone(JST).date()


def now_iso() -> str:
    return datetime.now(JST).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_config(name: str) -> Any:
    return read_json(REPO_ROOT / "config" / name, None)


def bookmarks_path(day: date) -> Path:
    return root() / "data" / f"{day.isoformat()}.json"


def sources_path(day: date) -> Path:
    return root() / "data" / "sources" / f"{day.isoformat()}.json"


def excluded_path(day: date) -> Path:
    return root() / "data" / "excluded" / f"{day.isoformat()}.json"


def article_path(key: str) -> Path:
    return root() / "data" / "articles" / f"{key}.json"


def previous_day(day: date) -> date:
    return day - timedelta(days=1)


# ---------- URL ----------


def clean_url(url: str) -> str:
    """計測用のクエリ（utm_* など）とフラグメントを外す。同じ記事を1件にまとめるため。"""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"ref", "ref_src", "fbclid", "gclid"}
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), "")
    )


def url_key(url: str) -> str:
    """data/articles/ のキー。URLのハッシュ（SHA-256の先頭12桁）。"""
    return hashlib.sha256(clean_url(url).encode("utf-8")).hexdigest()[:12]


def domain_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def is_article_url(url: str) -> bool:
    """x.com・twitter.com と、画像・動画の直リンクは記事として扱わない。"""
    if not is_http_url(url):
        return False
    host = (urlsplit(url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in NON_ARTICLE_HOSTS):
        return False
    path = urlsplit(url).path.lower()
    return not path.endswith(MEDIA_EXTENSIONS)


def plain_text(value: str | None, limit: int | None = None) -> str:
    """HTMLのタグと文字参照を外し、空白を詰める。"""
    import html

    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit else text


# ---------- HTTP ----------


class FetchError(RuntimeError):
    pass


def user_agent() -> str:
    config = load_config("sources.json") or {}
    return config.get("user_agent", "x-bookmark-digest/0.1")


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent()},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
    )


def fetch_bytes(http: httpx.Client, url: str, max_bytes: int = MAX_BYTES) -> tuple[bytes, str]:
    """本文を max_bytes まで取る。(本文, 最終URL) を返す。超えたら FetchError。"""
    try:
        with http.stream("GET", url) as response:
            if response.is_error:
                raise FetchError(f"HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise FetchError(f"larger than {max_bytes} bytes")
            return bytes(body), str(response.url)
    except httpx.HTTPError as error:
        raise FetchError(type(error).__name__) from error


class RobotsChecker:
    """robots.txt をホストごとに1回だけ読んで判定する。robots.txt が読めないときは許可として扱う。"""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http
        self._cache: dict[str, robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._cache:
            self._cache[origin] = self._load(origin)
        parser = self._cache[origin]
        return True if parser is None else parser.can_fetch(user_agent(), url)

    def _load(self, origin: str) -> robotparser.RobotFileParser | None:
        try:
            body, _ = fetch_bytes(self._http, origin + "/robots.txt", max_bytes=500_000)
        except FetchError:
            return None
        parser = robotparser.RobotFileParser()
        parser.parse(body.decode("utf-8", "replace").splitlines())
        return parser


def gha_warning(message: str) -> None:
    """GitHub Actions の注釈として出す（ローカルではただの出力）。"""
    print(f"::warning::{message}" if os.environ.get("GITHUB_ACTIONS") else f"warning: {message}")
