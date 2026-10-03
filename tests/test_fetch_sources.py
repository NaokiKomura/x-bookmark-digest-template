import json
from datetime import date

import httpx

from scripts import fetch_sources as fs
from scripts.lib import parsers
from tests.conftest import fixture_bytes, fixture_text

RULES = {"max_age_days": 3, "max_new_per_blog": 10}
DAY = date(2026, 10, 3)


def test_select_new_blog_items_skips_old_and_seen():
    items = parsers.feed(fixture_bytes("sources/openai_news.rss"))
    assert [i["title"] for i in fs.select_new_blog_items(items, None, "feed", DAY, RULES)] == [
        "New & shiny"
    ]
    assert fs.select_new_blog_items(items, ["https://openai.com/index/a"], "feed", DAY, RULES) == []


def test_select_new_blog_items_first_page_run_only_seeds():
    page = [{"title": "p", "url": "https://www.anthropic.com/news/x", "published": None}]
    assert fs.select_new_blog_items(page, None, "page", DAY, RULES) == []
    assert len(fs.select_new_blog_items(page, [], "page", DAY, RULES)) == 1


def fake_sites(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/robots.txt"):
        return httpx.Response(404)
    if "github.com/trending" in url:
        return httpx.Response(200, text=fixture_text("sources/github_trending.html"))
    if "qiita.com" in url:
        return httpx.Response(200, content=fixture_bytes("sources/qiita_popular.atom"))
    if "zenn.dev" in url:
        return httpx.Response(200, content=fixture_bytes("sources/zenn_daily.json"))
    if "openai.com" in url:
        return httpx.Response(200, content=fixture_bytes("sources/openai_news.rss"))
    return httpx.Response(500)


def test_collect_records_failures_and_streaks(digest_root):
    prev = {"qiita": [{"url": "https://qiita.com/u/items/1", "streak_days": 2}]}
    (digest_root / "data" / "sources" / "2026-10-02.json").write_text(json.dumps(prev))

    with httpx.Client(transport=httpx.MockTransport(fake_sites)) as http:
        out = fs.collect(http, DAY)

    assert out["status"]["github"] == "ok" and out["github"][0]["rank"] == 1
    assert out["qiita"][0]["streak_days"] == 3
    assert out["zenn"][0]["likes"] == 5
    assert out["status"]["devio"] == "error"
    assert any(e["source"] == "devio" for e in out["errors"])
    assert [b["title"] for b in out["blogs"]] == ["New & shiny"]
    assert out["blogs"][0]["summary"] == "Startups can choose models & tune effort."
    statuses = {f"{b['company']}/{b['blog']}": b["status"] for b in out["blog_status"]}
    assert statuses["openai/News"] == "new" and statuses["aws/AWS News Blog"] == "error"
    seen = json.loads((digest_root / "state" / "seen_urls.json").read_text())["urls"]
    assert "https://openai.com/index/b" in seen["openai/News"]


def test_robots_disallow_marks_source_as_error(digest_root):
    def handler(request):
        if str(request.url).endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="should not be fetched")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        out = fs.collect(http, DAY)
    assert out["status"]["qiita"] == "error"
    assert "robots" in next(e["message"] for e in out["errors"] if e["source"] == "qiita")
