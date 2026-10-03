import pytest

from scripts.lib import urls


def test_clean_url_drops_tracking_and_fragment():
    assert (
        urls.clean_url("HTTPS://Example.com/a?utm_source=x&id=1&ref=tw#top")
        == "https://example.com/a?id=1"
    )


def test_url_key_is_stable_across_tracking_params():
    assert urls.url_key("https://e.com/a?utm_medium=x") == urls.url_key("https://e.com/a")
    assert len(urls.url_key("https://e.com/a")) == 12


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/post", True),
        ("https://x.com/a/status/1", False),
        ("https://mobile.twitter.com/a", False),
        ("https://example.com/a.PNG", False),
        ("ftp://example.com/a", False),
    ],
)
def test_is_article_url(url, expected):
    assert urls.is_article_url(url) is expected


def test_domain_of_strips_www():
    assert urls.domain_of("https://www.anthropic.com/news") == "anthropic.com"
