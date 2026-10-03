"""HTTP クライアント、上限付きの取得、robots.txt の判定。外部サイトへの取得は必ずここを通す。"""

from __future__ import annotations

from urllib import robotparser
from urllib.parse import urlsplit

import httpx

from scripts.lib.store import load_config

TIMEOUT_SECONDS = 10.0
MAX_BYTES = 2_000_000
DEFAULT_USER_AGENT = "x-bookmark-digest/0.1"


class FetchError(RuntimeError):
    """取得の失敗（HTTP エラー、上限超え、接続エラー、robots.txt での禁止）。"""


def user_agent() -> str:
    config = load_config("sources.json") or {}
    return str(config.get("user_agent", DEFAULT_USER_AGENT))


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent()},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
    )


def fetch_bytes(http: httpx.Client, url: str, max_bytes: int = MAX_BYTES) -> tuple[bytes, str]:
    """本文を max_bytes まで取る。(本文, リダイレクト後の URL) を返す。超えたら FetchError。"""
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


def fetch_allowed(
    http: httpx.Client, robots: RobotsChecker, url: str, max_bytes: int = MAX_BYTES
) -> bytes:
    """robots.txt で許可されていれば取る。禁止なら FetchError。"""
    if not robots.allowed(url):
        raise FetchError("disallowed by robots.txt")
    body, _ = fetch_bytes(http, url, max_bytes)
    return body
