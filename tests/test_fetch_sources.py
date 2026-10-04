import json
from datetime import date

import httpx

from scripts import fetch_sources as fs
from scripts.lib import parsers
from tests.conftest import fixture_bytes, fixture_text

RULES = {"max_age_days": 3, "max_new_per_blog": 10}
DAY = date(2026, 10, 3)


def no_date(item):
    return None


def test_select_new_blog_items_skips_old_and_seen():
    items = parsers.feed(fixture_bytes("sources/openai_news.rss"))
    assert [i["title"] for i in fs.select_new_blog_items(items, None, DAY, RULES, no_date)] == [
        "New & shiny"
    ]
    assert (
        fs.select_new_blog_items(items, ["https://openai.com/index/a"], DAY, RULES, no_date) == []
    )


def test_select_new_blog_items_reads_dates_for_undated_items():
    page = [
        {"title": "new", "url": "https://www.anthropic.com/news/new", "published": None},
        {"title": "old", "url": "https://www.anthropic.com/news/old", "published": None},
        {"title": "unknown", "url": "https://www.anthropic.com/news/unknown", "published": None},
    ]
    dates = {"new": date(2026, 10, 2), "old": date(2026, 9, 1)}

    def date_of(item):
        return dates.get(item["title"])

    first = fs.select_new_blog_items(page, None, DAY, RULES, date_of)
    assert [(i["title"], i["published"]) for i in first] == [("new", date(2026, 10, 2))]
    # 2回目以降は、日付がわからなくても前回との差分なので新着にする
    later = fs.select_new_blog_items(page, [], DAY, RULES, date_of)
    assert [i["title"] for i in later] == ["new", "unknown"]


def test_select_new_blog_items_limits_date_lookups():
    page = [
        {"title": f"p{i}", "url": f"https://www.anthropic.com/news/p{i}", "published": None}
        for i in range(30)
    ]
    calls = []

    def date_of(item):
        calls.append(item["title"])
        return date(2026, 9, 1)

    assert fs.select_new_blog_items(page, None, DAY, RULES, date_of) == []
    assert len(calls) == RULES["max_new_per_blog"]


def test_article_date_reads_and_caches_the_article(digest_root):
    body = "<p>" + "本文です。" * 200 + "</p>"
    html = (
        '<html><head><meta property="article:published_time" content="2026-10-02T09:00:00Z">'
        f"<title>Post</title></head><body><article><h1>Post</h1>{body}</article></body></html>"
    )

    def handler(request):
        if str(request.url).endswith("/robots.txt"):
            return httpx.Response(404)
        return httpx.Response(200, html=html)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        got = fs.article_date(
            fs.Fetcher(http), {"title": "Post", "url": "https://www.anthropic.com/news/post"}
        )
    assert got == date(2026, 10, 2)
    key = fs.url_key("https://www.anthropic.com/news/post")
    assert (digest_root / "data" / "articles" / f"{key}.json").exists()


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
    if url == "https://www.anthropic.com/news":
        return httpx.Response(
            200, html='<a href="/news/fresh">Fresh</a><a href="/news/stale">Stale</a>'
        )
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
    assert statuses["anthropic/News"] == "none"  # 本文が取れず日付がわからない初回は新着にしない
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


def test_blog_rerun_preserves_items_judgment_and_adds_only_new(digest_root, monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(fake_sites)) as http:
        first = fs.collect(http, DAY)
        first["blogs"][0]["jev"] = {"status": "ok", "topic": "llm_agents", "topic_prob": 0.9}
        fs.write_json(fs.sources_path(DAY), first)
        again = fs.collect(http, DAY)
        assert again["blogs"] == first["blogs"]
        original = fs.fetch_blog

        def fetch_blog(fetcher, blog):
            entries = original(fetcher, blog)
            if blog["company"] == "openai":
                entries.append(
                    {"url": "https://openai.com/index/new", "title": "New", "published": DAY}
                )
            return entries

        monkeypatch.setattr(fs, "fetch_blog", fetch_blog)
        added = fs.collect(http, DAY)
        assert len(added["blogs"]) == 2
        assert added["blogs"][0] == first["blogs"][0]
        status = next(s for s in added["blog_status"] if s["company"] == "openai")
        assert status["status"] == "new" and status["count"] == 2
        monkeypatch.setattr(
            fs, "fetch_blog", lambda *args: (_ for _ in ()).throw(fs.FetchError("failed"))
        )
        failed = fs.collect(http, DAY)
        assert failed["blogs"] == added["blogs"]
        status = next(s for s in failed["blog_status"] if s["company"] == "openai")
        assert status["status"] == "error" and status["count"] == 2


def test_first_page_run_uses_article_dates(digest_root, monkeypatch):
    dates = {"https://www.anthropic.com/news/fresh": date(2026, 10, 2)}
    monkeypatch.setattr(
        fs, "article_date", lambda fetcher, item: dates.get(item["url"], date(2026, 8, 1))
    )
    with httpx.Client(transport=httpx.MockTransport(fake_sites)) as http:
        out = fs.collect(http, DAY)
    anthropic = [b for b in out["blogs"] if b["company"] == "anthropic"]
    assert [(b["title"], b["published"]) for b in anthropic] == [("Fresh", "2026-10-02")]
    seen = json.loads((digest_root / "state" / "seen_urls.json").read_text())["urls"]
    assert "https://www.anthropic.com/news/stale" in seen["anthropic/News"]


def test_blog_save_failure_does_not_advance_seen(digest_root, monkeypatch):
    def fail(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(fs, "write_json", fail)
    import pytest

    with httpx.Client(transport=httpx.MockTransport(fake_sites)) as http, pytest.raises(OSError):
        fs.collect(http, DAY)
    assert not fs.seen_urls_path().exists()


def test_rankings_rerun_preserves_successful_judgments(digest_root):
    with httpx.Client(transport=httpx.MockTransport(fake_sites)) as http:
        first = fs.collect(http, DAY)
        for source in ("github", "qiita", "zenn"):
            first[source][0]["jev"] = {"status": "ok", "topic": "devtools", "topic_prob": 0.9}
        fs.write_json(fs.sources_path(DAY), first)
        again = fs.collect(http, DAY)
    for source in ("github", "qiita", "zenn"):
        assert again[source][0]["jev"] == first[source][0]["jev"]


def test_rerun_restores_excluded_ranking_judgment(digest_root):
    with httpx.Client(transport=httpx.MockTransport(fake_sites)) as http:
        first = fs.collect(http, DAY)
        item = first["qiita"].pop(0)
        fs.write_json(fs.sources_path(DAY), first)
        fs.write_json(
            fs.excluded_path(DAY),
            {
                "date": DAY.isoformat(),
                "items": [
                    {
                        "source": "qiita",
                        "id": item["article_key"],
                        "title": item["title"],
                        "url": item["url"],
                        "tech_prob": 0.1,
                    }
                ],
            },
        )
        again = fs.collect(http, DAY)
    restored = next(i for i in again["qiita"] if i["article_key"] == item["article_key"])
    assert restored["jev"]["status"] == "ok"
    assert restored["jev"]["tech_label"] == "excluded"
