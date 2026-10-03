import json

import httpx

from scripts import fetch_articles as fa
from scripts.common import RobotsChecker

LONG = (
    "<html><head><title>記事タイトル</title></head><body><article><h1>記事タイトル</h1>"
    + "".join(
        f"<p>これは本文の段落その{i}です。テストのために十分な長さの文章を用意しています。</p>"
        for i in range(40)
    )
    + "</article></body></html>"
)
SHORT = "<html><body><article><p>有料記事です。続きは会員登録のうえ。</p></article></body></html>"


def client(routes):
    def handler(request):
        for key, resp in routes.items():
            if key in str(request.url):
                return resp() if callable(resp) else resp
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ok_partial_blocked_error_and_cache(digest_root):
    robots = "User-agent: *\nDisallow: /private/\n"
    calls = []

    def long_page():
        calls.append(1)
        return httpx.Response(200, html=LONG)

    routes = {
        "/robots.txt": httpx.Response(200, text=robots),
        "/ok": long_page,
        "/paid": httpx.Response(200, html=SHORT),
        "/broken": httpx.Response(500),
    }
    with client(routes) as http:
        rc = RobotsChecker(http)
        ok = fa.fetch_article(http, rc, "k1", "https://example.com/ok")
        assert ok["fetch_status"] == "ok" and ok["chars"] >= 500 and ok["title"] == "記事タイトル"
        assert (
            fa.fetch_article(http, rc, "k2", "https://example.com/paid")["fetch_status"]
            == "partial"
        )
        assert (
            fa.fetch_article(http, rc, "k3", "https://example.com/private/x")["fetch_status"]
            == "blocked"
        )
        assert (
            fa.fetch_article(http, rc, "k4", "https://example.com/broken")["fetch_status"]
            == "error"
        )
        # 取得済みなら再取得しない
        fa.fetch_article(http, rc, "k1", "https://example.com/ok")
    assert len(calls) == 1
    saved = json.loads((digest_root / "data" / "articles" / "k1.json").read_text())
    assert saved["key"] == "k1" and len(saved["text"]) <= 20_000


def test_too_large_is_error(digest_root):
    big = "<html><body>" + "a" * 2_100_000 + "</body></html>"
    with client(
        {"/robots.txt": httpx.Response(404), "/big": httpx.Response(200, html=big)}
    ) as http:
        rec = fa.fetch_article(http, RobotsChecker(http), "k5", "https://example.com/big")
    assert rec["fetch_status"] == "error"


def test_run_updates_page_titles(digest_root):
    sources = {
        "github": [],
        "qiita": [],
        "zenn": [],
        "blogs": [],
        "devio": [
            {
                "title": "話題の記事 崩れた見出し",
                "url": "https://dev.example.jp/ok",
                "article_key": "d1",
                "title_source": "page",
            }
        ],
    }
    (digest_root / "data" / "sources" / "2026-10-03.json").write_text(json.dumps(sources))
    with client(
        {"/robots.txt": httpx.Response(404), "/ok": httpx.Response(200, html=LONG)}
    ) as http:
        counts = fa.run(http)
    assert counts == {"ok": 1}
    out = json.loads((digest_root / "data" / "sources" / "2026-10-03.json").read_text())
    assert out["devio"][0]["title"] == "記事タイトル"


def test_strip_site_suffix():
    assert fa.strip_site_suffix("判断特化型AIを試した | DevelopersIO") == "判断特化型AIを試した"
    assert fa.strip_site_suffix("A | B | DevelopersIO") == "A | B"
    assert fa.strip_site_suffix("区切りなし") == "区切りなし"
