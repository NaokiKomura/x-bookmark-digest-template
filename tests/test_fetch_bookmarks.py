import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import fetch_bookmarks as fb

USERS = [
    {"id": "u1", "username": "kato_agents", "name": "加藤"},
    {"id": "u2", "username": "someone", "name": "引用元"},
]


def tweet(tid, author="u1", text="本文", urls=None, quoted=None, note=None):
    t = {"id": tid, "text": text, "author_id": author, "created_at": "2026-10-02T21:14:00Z"}
    if urls:
        t["entities"] = {"urls": urls}
    if quoted:
        t["referenced_tweets"] = [{"type": "quoted", "id": quoted}]
    if note:
        t["note_tweet"] = note
    return t


def test_to_post_quote_with_link_in_quoted():
    quoted = tweet(
        "q1",
        author="u2",
        text="引用元 https://t.co/a",
        urls=[
            {
                "url": "https://t.co/a",
                "expanded_url": "https://t.co/x",
                "unwound_url": "https://example.com/post?utm_source=x",
                "title": "カードの題",
                "description": "カードの概要",
            }
        ],
    )
    main = tweet(
        "t1",
        text="これは良い https://t.co/q",
        urls=[{"url": "https://t.co/q", "expanded_url": "https://x.com/someone/status/q1"}],
        quoted="q1",
    )
    post = fb.to_post(main, {u["id"]: u for u in USERS}, {"q1": quoted})
    assert post["url"] == "https://x.com/kato_agents/status/t1"
    assert post["quoted"]["author"]["username"] == "someone"
    assert post["quoted"]["text"] == "引用元 https://example.com/post?utm_source=x"
    # x.com のリンクは記事にしない。記事の URL は引用元から拾い、計測用クエリを外す
    assert [(lk["url"], lk["from"]) for lk in post["links"]] == [
        ("https://example.com/post", "quoted")
    ]
    assert post["links"][0]["card_title"] == "カードの題"
    assert post["jev"] is None


def test_note_tweet_text_is_preferred_and_media_links_skipped():
    t = tweet(
        "t2",
        text="短い",
        note={
            "text": "長文の本文 https://t.co/img",
            "entities": {
                "urls": [{"url": "https://t.co/img", "expanded_url": "https://example.com/a.png"}]
            },
        },
    )
    post = fb.to_post(t, {u["id"]: u for u in USERS}, {})
    assert post["text"] == "長文の本文 https://example.com/a.png"
    assert post["links"] == [] and post["quoted"] is None


def test_fetch_stops_at_seen_id():
    pages = [
        {
            "data": [tweet("3"), tweet("2")],
            "includes": {"users": USERS},
            "meta": {"next_token": "p2"},
        },
        {
            "data": [tweet("1"), tweet("0")],
            "includes": {"users": USERS},
            "meta": {"next_token": "p3"},
        },
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer AT"
        assert "referenced_tweets.id.author_id" in request.url.params["expansions"]
        return httpx.Response(200, json=pages[len(calls) - 1])

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        posts = fb.fetch_new_bookmarks(http, "AT", "me", seen={"1"})
    assert [p["id"] for p in posts] == ["3", "2"]
    assert len(calls) == 2


def test_refresh_error_raises_without_leaking_body():
    def handler(request):
        return httpx.Response(
            400, json={"error": "invalid_request", "error_description": "bad token"}
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        pytest.raises(fb.TokenError) as e,
    ):
        fb.refresh_access_token(http, "cid", None, "RT-old")
    assert "RT-old" not in str(e.value)


def test_write_back_failure_stops(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/repo")
    monkeypatch.setenv("GH_PAT", "pat")
    seen = {}

    def runner(args, **kw):
        seen["args"], seen["input"] = args, kw["input"]
        return SimpleNamespace(returncode=1)

    with pytest.raises(fb.TokenError):
        fb.save_refresh_token("RT-new", runner=runner)
    assert seen["args"][:3] == ["gh", "secret", "set"] and seen["input"] == "RT-new"


def test_main_stops_before_fetch_when_write_back_fails(digest_root, monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "cid")
    monkeypatch.setenv("X_USER_ID", "me")
    monkeypatch.setenv("X_REFRESH_TOKEN", "RT")
    monkeypatch.setattr(fb, "refresh_access_token", lambda *a: ("AT", "RT2"))

    def boom(token):
        raise fb.TokenError("write back failed")

    monkeypatch.setattr(fb, "save_refresh_token", boom)
    monkeypatch.setattr(fb, "fetch_new_bookmarks", lambda *a: pytest.fail("must not fetch"))
    assert fb.main([]) == 1
    assert not (digest_root / "data" / "2026-10-03.json").exists()


def test_main_writes_empty_file_and_updates_seen(digest_root, monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "cid")
    monkeypatch.setenv("X_USER_ID", "me")
    monkeypatch.setenv("X_REFRESH_TOKEN", "RT")
    (digest_root / "state" / "seen_ids.json").write_text('{"ids": ["old"]}')
    monkeypatch.setattr(fb, "refresh_access_token", lambda *a: ("AT", "RT2"))
    monkeypatch.setattr(fb, "fetch_new_bookmarks", lambda *a: [])
    assert fb.main([]) == 0
    data = json.loads((digest_root / "data" / "2026-10-03.json").read_text())
    assert data["posts"] == [] and data["date"] == "2026-10-03"
    # ローカルでは新しいトークンを .secrets/ に権限600で保存する
    token_file = digest_root / ".secrets" / "x_refresh_token"
    assert token_file.read_text() == "RT2" and oct(token_file.stat().st_mode)[-3:] == "600"


def test_api_error_is_recorded_and_seen_kept(digest_root, monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "cid")
    monkeypatch.setenv("X_USER_ID", "me")
    monkeypatch.setenv("X_REFRESH_TOKEN", "RT")
    out = digest_root / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    (digest_root / "state" / "seen_ids.json").write_text('{"ids": ["old"]}')
    monkeypatch.setattr(fb, "refresh_access_token", lambda *a: ("AT", "RT2"))

    def fail(*a):
        raise RuntimeError("bookmarks request failed: HTTP 503")

    monkeypatch.setattr(fb, "fetch_new_bookmarks", fail)
    assert fb.main([]) == 0
    data = json.loads((digest_root / "data" / "2026-10-03.json").read_text())
    assert "503" in data["error"]
    assert "bookmarks_error=1" in out.read_text()
    assert json.loads((digest_root / "state" / "seen_ids.json").read_text())["ids"] == ["old"]
