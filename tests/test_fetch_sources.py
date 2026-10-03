import json
from datetime import date

import httpx

from scripts import fetch_sources as fs
from scripts.common import load_config

GH_HTML = """
<article class="Box-row">
  <h2><a href="/owner/repo-one">owner / repo-one</a></h2>
  <p>説明文 その1</p>
  <span itemprop="programmingLanguage">TypeScript</span>
  <a href="/owner/repo-one/stargazers">18,300</a>
  <span class="d-inline-block float-sm-right">1,240 stars today</span>
</article>
<article class="Box-row">
  <h2><a href="/owner/repo-two">owner / repo-two</a></h2>
  <a href="/owner/repo-two/stargazers">900</a>
  <span class="d-inline-block float-sm-right">12 stars today</span>
</article>
"""

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>New &amp; shiny</title><link>https://openai.com/index/a</link><pubDate>Fri, 02 Oct 2026 18:00:00 GMT</pubDate></item>
<item><title>Old</title><link>https://openai.com/index/b</link><pubDate>Mon, 01 Jun 2026 18:00:00 GMT</pubDate></item>
<item><title>Bad</title><link>javascript:alert(1)</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Qiita 1</title><link rel="alternate" href="https://qiita.com/u/items/1"/><published>2026-10-02T09:00:00+09:00</published></entry>
</feed>"""

DEVIO = """<main>
<a href="/articles/one/"><div class="relative"><img><div>話題の記事</div></div>記事その1</a>
<a href="/articles/one/">記事その1（重複）</a>
<a href="/articles/two/">記事その2</a>
<a href="/tag/aws/">タグ</a>
</main>"""


def test_parse_github_trending():
    conf = load_config("sources.json")["github"]["trending"]
    repos = fs.parse_github_trending(GH_HTML, conf)
    assert repos[0] == {
        "title": "owner/repo-one",
        "url": "https://github.com/owner/repo-one",
        "description": "説明文 その1",
        "language": "TypeScript",
        "stars_today": 1240,
        "stars_total": 18300,
    }
    assert repos[1]["language"] is None and repos[1]["description"] == ""


def test_parse_github_trending_raises_on_unknown_page():
    conf = load_config("sources.json")["github"]["trending"]
    try:
        fs.parse_github_trending("<html></html>", conf)
    except fs.ParseError:
        return
    raise AssertionError("expected ParseError")


def test_parse_feeds():
    rss = fs.parse_feed(RSS)
    assert [i["title"] for i in rss] == ["New & shiny", "Old"]  # https 以外は捨てる
    assert rss[0]["published"] == date(2026, 10, 3)  # 日本時間に直す
    atom = fs.parse_feed(ATOM)
    assert atom[0]["url"] == "https://qiita.com/u/items/1"


def test_parse_link_list_dedupes_and_strips_badge():
    conf = load_config("sources.json")["devio"]
    items = fs.parse_link_list(DEVIO, conf["link"], None, conf["base_url"], conf["title_exclude"])
    assert [(i["url"], i["title"]) for i in items] == [
        ("https://dev.classmethod.jp/articles/one/", "記事その1"),
        ("https://dev.classmethod.jp/articles/two/", "記事その2"),
    ]


def test_select_new_blog_items():
    rules = {"max_age_days": 7, "max_new_per_blog": 10}
    items = fs.parse_feed(RSS)
    day = date(2026, 10, 3)
    # 古い記事は新着にしない
    assert [i["title"] for i in fs.select_new_blog_items(items, None, "feed", day, rules)] == [
        "New & shiny"
    ]
    assert fs.select_new_blog_items(items, ["https://openai.com/index/a"], "feed", day, rules) == []
    page = [{"title": "p", "url": "https://www.anthropic.com/news/x", "published": None}]
    # 一覧ページの初回は既読にするだけ
    assert fs.select_new_blog_items(page, None, "page", day, rules) == []
    assert len(fs.select_new_blog_items(page, [], "page", day, rules)) == 1


def test_collect_records_failures_and_streaks(digest_root, monkeypatch):
    prev = {
        "qiita": [{"url": "https://qiita.com/u/items/1", "streak_days": 2}],
        "github": [],
        "zenn": [],
        "devio": [],
    }
    (digest_root / "data" / "sources" / "2026-10-02.json").write_text(json.dumps(prev))

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        if "github.com/trending" in url:
            return httpx.Response(200, text=GH_HTML)
        if "qiita.com" in url:
            return httpx.Response(200, content=ATOM)
        if "zenn.dev" in url:
            return httpx.Response(
                200, json={"articles": [{"title": "Z", "path": "/u/articles/z", "liked_count": 5}]}
            )
        if "openai.com" in url:
            return httpx.Response(200, content=RSS)
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        out = fs.collect(http, date(2026, 10, 3))
    assert out["status"]["github"] == "ok" and out["github"][0]["rank"] == 1
    assert out["qiita"][0]["streak_days"] == 3
    assert out["zenn"][0]["likes"] == 5
    assert out["status"]["devio"] == "error"
    assert any(e["source"] == "devio" for e in out["errors"])
    assert [b["title"] for b in out["blogs"]] == ["New & shiny"]
    statuses = {b["company"] + "/" + b["blog"]: b["status"] for b in out["blog_status"]}
    assert statuses["openai/News"] == "new" and statuses["aws/AWS News Blog"] == "error"
    seen = json.loads((digest_root / "state" / "seen_urls.json").read_text())["urls"]
    assert "https://openai.com/index/b" in seen["openai/News"]
