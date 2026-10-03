import json

import pytest

from scripts import classify_jev as cj


def fake_judge(tech_by_title=None, topic="llm_agents", topic_prob=0.8, log=None):
    tech_by_title = tech_by_title or {}

    def judge(state, with_tech):
        item = state["item"]
        if log is not None:
            log.append((item.get("title") or item.get("post_text") or item.get("name"), with_tech))
        out = {"topic": topic, "topic_prob": topic_prob}
        if with_tech:
            out["tech_prob"] = tech_by_title.get(item.get("title") or item.get("post_text"), 0.9)
        return out

    return judge


@pytest.mark.parametrize(
    "prob,label", [(0.7, "tech"), (0.69, "hold"), (0.4, "hold"), (0.39, "excluded")]
)
def test_tech_thresholds(prob, label):
    assert cj.tech_label(prob) == label


def test_low_topic_probability_becomes_other():
    r = cj.interpret({"topic": "frontend", "topic_prob": 0.49}, with_tech=False)
    assert r == {"status": "ok", "topic": "other", "topic_prob": 0.49}


def test_failure_is_unavailable_and_config_error_stops_calls(monkeypatch):
    calls = []

    def judge(state, with_tech):
        calls.append(1)
        raise RuntimeError("boom")

    clf = cj.Classifier(judge)
    r = clf.classify({"item": {}}, with_tech=True)
    assert r == {
        "status": "unavailable",
        "topic": None,
        "topic_prob": None,
        "tech_prob": None,
        "tech_label": None,
    }
    monkeypatch.setattr(cj, "is_configuration_error", lambda e: True)
    clf.classify({"item": {}}, with_tech=True)
    clf.classify({"item": {}}, with_tech=True)
    assert len(calls) == 2  # 設定の誤りのあとは呼ばない


def entry(title, rank):
    return {
        "kind": "article",
        "rank": rank,
        "title": title,
        "url": f"https://q/{rank}",
        "article_key": f"k{rank}",
        "jev": None,
    }


def test_ranking_replenished_from_reserve(digest_root):
    sources = {
        "qiita": [entry(f"t{i}", i) for i in range(1, 11)],
        "reserve": {"qiita": [entry("r11", 11), entry("r12", 12)]},
    }
    excluded, fetched = [], []
    clf = cj.Classifier(fake_judge({"t3": 0.1, "t7": 0.2}))
    cj.classify_ranking(
        sources, "qiita", "Qiita", clf, excluded, lambda e: fetched.append(e["url"])
    )
    assert [e["title"] for e in sources["qiita"]][-2:] == ["r11", "r12"]
    assert len(sources["qiita"]) == 10
    assert [x["title"] for x in excluded] == ["t3", "t7"]
    assert fetched == ["https://q/11", "https://q/12"]


def test_run_end_to_end(digest_root):
    day = "2026-10-03"
    bookmarks = {
        "date": day,
        "posts": [
            {
                "id": "1",
                "text": "ラーメンおいしい",
                "url": "https://x.com/a/status/1",
                "quoted": None,
                "links": [],
                "jev": None,
            },
            {
                "id": "2",
                "text": "Claude Code のフック",
                "url": "https://x.com/a/status/2",
                "quoted": None,
                "links": [
                    {
                        "article_key": "a1",
                        "card_title": "C",
                        "card_description": "D",
                        "url": "https://e/a",
                    }
                ],
                "jev": None,
            },
        ],
    }
    (digest_root / "data" / f"{day}.json").write_text(json.dumps(bookmarks))
    (digest_root / "data" / "articles" / "a1.json").write_text(
        json.dumps({"title": "記事", "text": "本文" * 3000})
    )
    sources = {
        "github": [{"title": "o/r", "description": "d", "article_key": "g1", "jev": None}],
        "qiita": [],
        "zenn": [],
        "devio": [],
        "blogs": [],
        "reserve": {"qiita": [], "zenn": []},
    }
    (digest_root / "data" / "sources" / f"{day}.json").write_text(json.dumps(sources))
    log = []
    result = cj.run(cj.Classifier(fake_judge({"ラーメンおいしい": 0.05}, log=log)), lambda e: None)
    assert result == {"calls": 3, "failures": 0, "excluded": 1}
    out = json.loads((digest_root / "data" / f"{day}.json").read_text())
    assert out["posts"][0]["jev"]["tech_label"] == "excluded"  # 投稿は残し、印を付ける
    assert out["posts"][1]["jev"] == {
        "status": "ok",
        "tech_prob": 0.9,
        "tech_label": "tech",
        "topic": "llm_agents",
        "topic_prob": 0.8,
    }
    ex = json.loads((digest_root / "data" / "excluded" / f"{day}.json").read_text())
    assert ex["items"][0]["source"] == "bookmark"
    src = json.loads((digest_root / "data" / "sources" / f"{day}.json").read_text())
    assert "reserve" not in src and src["github"][0]["jev"]["topic"] == "llm_agents"
    assert ("o/r", False) in log  # GitHub はトピックだけ


def test_bookmark_state_clips_article_to_4000_chars():
    post = {
        "text": "t",
        "quoted": {"text": "q"},
        "links": [{"article_key": "a", "card_title": "c", "card_description": "d"}],
    }
    state = cj.bookmark_state(post, {"a": {"title": "T", "text": "x" * 9000}})
    art = state["item"]["linked_articles"][0]
    assert art["title"] == "T" and len(art["body"]) == 4000
    assert state["item"]["quoted_post_text"] == "q"


def test_questions_build_with_sdk():
    topics = [{"id": "a", "description": "A"}, {"id": "other", "description": "none"}]
    assert cj.topic_question(topics) is not None and cj.tech_question() is not None
