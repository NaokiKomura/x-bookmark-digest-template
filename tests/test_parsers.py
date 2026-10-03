from datetime import date

import pytest

from scripts.lib import parsers
from scripts.lib.store import load_config
from tests.conftest import fixture_bytes, fixture_text


def test_github_trending():
    conf = load_config("sources.json")["github"]["trending"]
    repos = parsers.github_trending(fixture_text("sources/github_trending.html"), conf)
    assert repos[0] == {
        "title": "owner/repo-one",
        "url": "https://github.com/owner/repo-one",
        "description": "説明文 その1",
        "language": "TypeScript",
        "stars_today": 1240,
        "stars_total": 18300,
    }
    assert repos[1]["language"] is None and repos[1]["description"] == ""


def test_github_trending_raises_on_unknown_page():
    conf = load_config("sources.json")["github"]["trending"]
    with pytest.raises(parsers.ParseError):
        parsers.github_trending("<html></html>", conf)


def test_rss_drops_non_https_and_converts_to_jst():
    items = parsers.feed(fixture_bytes("sources/openai_news.rss"))
    assert [i["title"] for i in items] == ["New & shiny", "Old"]
    assert items[0]["published"] == date(2026, 10, 3)


def test_rss_keeps_description_as_summary():
    items = parsers.feed(fixture_bytes("sources/openai_news.rss"))
    assert items[0]["summary"] == "Startups can choose models & tune effort."


def test_atom():
    assert (
        parsers.feed(fixture_bytes("sources/qiita_popular.atom"))[0]["url"]
        == "https://qiita.com/u/items/1"
    )


def test_broken_feed_is_parse_error():
    with pytest.raises(parsers.ParseError):
        parsers.feed(b"<html>not a feed")


def test_zenn_api_skips_foreign_paths():
    items = parsers.zenn_api(fixture_bytes("sources/zenn_daily.json"))
    assert [(i["url"], i["likes"]) for i in items] == [("https://zenn.dev/u/articles/z", 5)]


def test_link_list_dedupes_and_strips_badge():
    conf = load_config("sources.json")["devio"]
    items = parsers.link_list(
        fixture_text("sources/devio_trending.html"),
        conf["base_url"],
        selector=conf["link"],
        exclude=conf["title_exclude"],
    )
    assert [(i["url"], i["title"]) for i in items] == [
        ("https://dev.classmethod.jp/articles/one/", "記事その1"),
        ("https://dev.classmethod.jp/articles/two/", "記事その2"),
    ]


def test_link_list_pattern_for_blogs():
    html = '<a href="/news">一覧</a><a href="/news/post-a">A</a><a href="/news/post-a/x">B</a>'
    blog = next(
        b
        for b in load_config("sources.json")["blogs"]
        if b["blog"] == "News" and b["company"] == "anthropic"
    )
    items = parsers.link_list(html, blog["base_url"], pattern=blog["link_pattern"])
    assert [i["url"] for i in items] == ["https://www.anthropic.com/news/post-a"]
