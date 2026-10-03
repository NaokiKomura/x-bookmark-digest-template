import signal
import time

import httpx
import pytest

from scripts import fetch_articles
from scripts.lib import web


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://localhost/",
        "http://sub.localhost/",
        "http://[64:ff9b::7f00:1]/",
        "file:///tmp/a",
    ],
)
def test_private_destination_never_requested(url):
    calls = []
    with (
        httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))) as http,
        pytest.raises(web.FetchError),
    ):
        web.fetch_bytes(http, url)
    assert calls == []


def test_redirect_to_private_destination_is_blocked():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http,
        pytest.raises(web.FetchError),
    ):
        web.fetch_bytes(http, "https://example.com/a")
    assert calls == ["https://example.com/a"]


def test_dns_is_pinned_with_original_host_and_tls_name(monkeypatch):
    resolutions, requests = [], []

    def resolve(host, port, **kwargs):
        resolutions.append(host)
        ip = "93.184.216.34" if len(resolutions) == 1 else "127.0.0.1"
        return [(2, 1, 6, "", (ip, port))]

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"ok"))

    monkeypatch.setattr(web.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handle))
    with web.make_client() as http:
        assert web.fetch_bytes(http, "https://example.com/a") == (b"ok", "https://example.com/a")
        with pytest.raises(web.FetchError):
            web.fetch_bytes(http, "https://example.com/b")
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "example.com"
    assert requests[0].extensions["sni_hostname"] == "example.com"


def test_dns_mixed_public_private_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        web.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with web.make_client() as http, pytest.raises(web.FetchError):
        web.fetch_bytes(http, "https://example.com/")


class TrickleStream(httpx.SyncByteStream):
    closed = False

    def __iter__(self):
        while True:
            time.sleep(0.005)
            yield b"x"

    def close(self):
        self.closed = True


@pytest.mark.parametrize("slow_robots", [False, True])
def test_total_deadline_interrupts_trickle_and_next_article_proceeds(
    digest_root, monkeypatch, slow_robots
):
    monkeypatch.setattr(web, "TOTAL_TIMEOUT_SECONDS", 0.05)
    stream = TrickleStream()

    def handler(request):
        if (request.url.path == "/robots.txt") == slow_robots and request.url.host == "example.com":
            return httpx.Response(200, stream=stream)
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, text="ok")
        )

    before = signal.getsignal(signal.SIGALRM)
    start = time.monotonic()
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        robots = web.RobotsChecker(http)
        record = fetch_articles.fetch_article(http, robots, "slow", "https://example.com/a")
        assert record["fetch_status"] == "error"
        assert "総時間" in record["error"]
        assert (
            fetch_articles.fetch_article(http, robots, "next", "https://other.example/a")[
                "fetch_status"
            ]
            == "partial"
        )
    assert time.monotonic() - start < 1
    assert stream.closed
    assert signal.getsignal(signal.SIGALRM) == before
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_public_redirect_keeps_final_url_and_strips_cross_origin_authorization():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://other.example/a"})
        return httpx.Response(200, text="final")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert web.fetch_bytes(
            http, "https://example.com/a", headers={"Authorization": "Bearer dummy"}
        ) == (b"final", "https://other.example/a")
    assert calls[0].headers["authorization"] == "Bearer dummy"
    assert "authorization" not in calls[1].headers


def test_robots_redirect_dns_private_address_is_rejected_before_connect(monkeypatch):
    calls = []
    monkeypatch.setattr(
        web.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (2, 1, 6, "", ("10.0.0.1" if host == "internal.example" else "93.184.216.34", port)),
        ],
    )

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://internal.example/robots.txt"})

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handler))
    with web.make_client() as http:
        # robots.txtの取得失敗時は従来どおり許可するが、内部宛先には接続しない。
        assert web.RobotsChecker(http).allowed("https://example.com/a")
    assert len(calls) == 1


def test_total_deadline_interrupts_wait_for_headers(monkeypatch):
    monkeypatch.setattr(web, "TOTAL_TIMEOUT_SECONDS", 0.03)

    def handler(request):
        time.sleep(1)
        return httpx.Response(200)

    started = time.monotonic()
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        pytest.raises(web.FetchTimeoutError),
    ):
        web.fetch_bytes(http, "https://example.com/a")
    assert time.monotonic() - started < 0.5
