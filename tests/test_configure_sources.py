import json

import httpx
import pytest

from scripts import configure_sources as cs
from scripts import fetch_sources as fs
from tests.test_fetch_sources import DAY, fake_sites


def test_choices_group_blogs_by_company():
    groups = {g["group"]: g for g in cs.choices()}
    assert [o["id"] for o in groups["trends"]["options"]] == ["github", "qiita", "zenn", "devio"]
    blogs = {o["id"]: o for o in groups["blogs"]["options"]}
    assert set(blogs) == {"anthropic", "openai", "google", "aws"}
    assert "claude.dev" in blogs["anthropic"]["note"]
    # Claude Code の選択式の設問（1問4択まで）に収まる
    assert all(len(g["options"]) <= 4 for g in groups.values())


def test_write_enabled_keeps_choice_order_and_rejects_unknown(digest_root):
    assert cs.main(["--enable", "aws, qiita"]) == 0
    saved = json.loads((digest_root / "enabled.json").read_text())
    assert saved["sources"] == ["qiita", "aws"]
    assert cs.main(["--enable", "qiita,hatena"]) == 2
    assert json.loads((digest_root / "enabled.json").read_text())["sources"] == ["qiita", "aws"]
    with pytest.raises(SystemExit):
        cs.main([])


def test_fetch_skips_sources_not_enabled(digest_root):
    cs.write_enabled(["zenn", "openai"])
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return fake_sites(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        out = fs.collect(http, DAY)
    assert set(out["status"]) == {"zenn", "blogs"}
    assert out["github"] == [] and out["qiita"] == []
    assert [b["company"] for b in out["blog_status"]] == ["openai"]
    assert set(calls) == {"zenn.dev", "openai.com"}


def test_fetch_with_no_extra_sources(digest_root):
    cs.write_enabled([])
    with httpx.Client(transport=httpx.MockTransport(fake_sites)) as http:
        out = fs.collect(http, DAY)
    assert out["status"] == {} and out["blog_status"] == []
