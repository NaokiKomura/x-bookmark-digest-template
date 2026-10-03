"""HTTP クライアント、上限付きの取得、robots.txt の判定。外部サイトへの取得は必ずここを通す。"""

from __future__ import annotations

import ipaddress
import signal
import socket
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from types import FrameType
from urllib import robotparser
from urllib.parse import urlsplit

import httpx

from scripts.lib.store import load_config

TIMEOUT_SECONDS = 10.0
TOTAL_TIMEOUT_SECONDS = 30.0
MAX_BYTES = 2_000_000
DEFAULT_USER_AGENT = "x-bookmark-digest/0.1"
_deadline_active = False


class FetchError(RuntimeError):
    """取得の失敗（HTTP エラー、上限超え、接続エラー、robots.txt での禁止）。"""


class FetchTimeoutError(FetchError):
    """総時間制限の超過。robots.txt の失敗許可で握りつぶさない。"""


def user_agent() -> str:
    config = load_config("sources.json") or {}
    return str(config.get("user_agent", DEFAULT_USER_AGENT))


def validate_destination(url: httpx.URL) -> None:
    """HTTP(S) と公開宛先だけを許す。DNS の検査は接続直前にも行う。"""
    host = url.host.lower().rstrip(".")
    if url.scheme not in ("http", "https") or not host or url.userinfo:
        raise FetchError("許可されていないURL")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise FetchError("内部宛ての接続は禁止")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not is_public_address(str(address)):
        raise FetchError("内部宛ての接続は禁止")


def is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return is_public_address(str(address.ipv4_mapped))
        # IPv4 への変換・トンネル経由で内部宛てになる接続も許可しない。
        if address.sixtofour or address.teredo or address in ipaddress.ip_network("64:ff9b::/96"):
            return False
    return address.is_global and not address.is_multicast


class ClosingStream(httpx.SyncByteStream):
    """レスポンスを閉じると、その要求専用の接続も解放する。"""

    def __init__(self, stream: httpx.SyncByteStream, transport: httpx.BaseTransport) -> None:
        self.stream = stream
        self.transport = transport

    def __iter__(self) -> Iterator[bytes]:
        yield from self.stream

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            self.transport.close()


class PublicTransport(httpx.BaseTransport):
    """DNS の全応答を検査し、検査済みIPへ接続する。リダイレクトも要求ごとに検査する。"""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        validate_destination(request.url)
        try:
            answers = socket.getaddrinfo(
                request.url.host,
                request.url.port or (443 if request.url.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as error:
            raise FetchError("DNS解決に失敗") from error
        addresses = list(dict.fromkeys(str(answer[4][0]) for answer in answers))
        if not addresses or not all(is_public_address(a) for a in addresses):
            raise FetchError("内部宛ての接続は禁止")
        # 接続時にホスト名を再解決させない。Host とTLSの検証名は元のホストを使う。
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=addresses[0]),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.host},
        )
        transport = httpx.HTTPTransport(trust_env=False)
        try:
            response = transport.handle_request(pinned)
        except BaseException:
            transport.close()
            raise
        assert isinstance(response.stream, httpx.SyncByteStream)
        response.stream = ClosingStream(response.stream, transport)
        return response


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent()},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        transport=PublicTransport(),
        trust_env=False,
    )


@contextmanager
def fetch_deadline() -> Iterator[None]:
    """POSIXのメインスレッドで通信全体を中断する。入れ子では既存の短い期限を守る。"""
    if threading.current_thread() is not threading.main_thread() or not hasattr(
        signal, "setitimer"
    ):
        raise FetchError("総時間制限にはPOSIXのメインスレッドが必要")
    global _deadline_active
    if _deadline_active:
        # この取得処理の外側ですでに期限が設定されている場合は延長しない。
        yield
        return

    if signal.getitimer(signal.ITIMER_REAL)[0] > 0:
        raise FetchError("既存のタイマーと総時間制限は併用できない")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(signum: int, frame: FrameType | None) -> None:
        raise FetchTimeoutError("取得の総時間制限を超過")

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, TOTAL_TIMEOUT_SECONDS)
    _deadline_active = True
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        _deadline_active = False


def fetch_bytes(
    http: httpx.Client,
    url: str,
    max_bytes: int = MAX_BYTES,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    """本文を上限まで取る。リダイレクトを含む取得全体に期限を設ける。"""
    try:
        with fetch_deadline():
            request = http.build_request("GET", url, headers=headers)
            for _ in range(21):
                validate_destination(request.url)
                with closing(http.send(request, stream=True, follow_redirects=False)) as response:
                    if response.is_redirect and response.next_request is not None:
                        request = response.next_request
                        continue
                    if response.is_error:
                        raise FetchError(f"HTTP {response.status_code}")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise FetchError(f"larger than {max_bytes} bytes")
                    return bytes(body), str(response.url)
            raise FetchError("リダイレクト回数の上限を超過")
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
        except FetchTimeoutError:
            raise
        except FetchError:
            return None
        parser = robotparser.RobotFileParser()
        parser.parse(body.decode("utf-8", "replace").splitlines())
        return parser


def fetch_allowed(
    http: httpx.Client, robots: RobotsChecker, url: str, max_bytes: int = MAX_BYTES
) -> bytes:
    """robots.txt で許可されていれば取る。禁止なら FetchError。"""
    with fetch_deadline():
        if not robots.allowed(url):
            raise FetchError("disallowed by robots.txt")
        body, _ = fetch_bytes(http, url, max_bytes)
        return body
