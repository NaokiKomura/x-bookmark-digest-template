"""URL の正規化・キー・記事かどうかの判定と、HTML から文字だけを取り出す処理。"""

from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

NON_ARTICLE_HOSTS = (
    "x.com",
    "twitter.com",
    "t.co",
    "pic.twitter.com",
    "pbs.twimg.com",
    "video.twimg.com",
)
MEDIA_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".heic",
    ".mp4", ".mov", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav",
)  # fmt: skip
TRACKING_PARAMS = {"ref", "ref_src", "fbclid", "gclid"}


def clean_url(url: str) -> str:
    """計測用のクエリ（utm_* など）とフラグメントを外す。同じ記事を1件にまとめるため。"""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), "")
    )


def url_key(url: str) -> str:
    """data/articles/ のキー（article_key）。正規化した URL の SHA-256 の先頭12桁。"""
    return hashlib.sha256(clean_url(url).encode("utf-8")).hexdigest()[:12]


def domain_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


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
    return not urlsplit(url).path.lower().endswith(MEDIA_EXTENSIONS)


def plain_text(value: str | None, limit: int | None = None) -> str:
    """HTML のタグと文字参照を外し、空白を詰める。"""
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit else text
