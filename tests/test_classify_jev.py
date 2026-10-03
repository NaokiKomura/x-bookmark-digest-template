import json

import pytest

from scripts import classify_jev as cj

CONF = cj.jev_config()
TH = CONF["thresholds"]


def fake_judge(tech_by_title=None, topic="llm_agents", topic_prob=0.8, log=None):
    """Jev の代わり。タイトル（または投稿本文）ごとにテック確率を決められる。"""
    tech_by_title = tech_by_title or {}

    def judge(state, with_tech):
        item = state["item"]
        key = item.get("title") or item.get("post_text") or item.get("name")
        if log is not None:
            log.append((key, with_tech))
        out = {"topic": topic, "topic_prob": topic_prob}
        if with_tech:
            out["tech_prob"] = tech_by_title.get(key, 0.9)
        return out

    return judge


@pytest.mark.parametrize(
    "prob,label", [(0.7, "tech"), (0.69, "hold"), (0.4, "hold"), (0.39, "excluded")]
)
def test_tech_thresholds(prob, label):
    assert cj.tech_label(prob, TH) == label


def test_low_topic_probability_becomes_other():
    assert cj.to_topic_jev({"topic": "frontend", "topic_prob": 0.49}, TH) == {
        "status": "ok",
        "topic": "other",
        "topic_prob": 0.49,
    }


def test_failure_is_unavailable_and_config_error_stops_calls(monkeypatch):
    calls = []

    def judge(state, with_tech):
        calls.append(1)
        raise RuntimeError("boom")

    clf = cj.Classifier(judge, TH)
    assert clf.tech({"item": {}}) == {
        "status": "unavailable",
        "topic": None,
        "topic_prob": None,
        "tech_prob": None,
        "tech_label": None,
    }
    monkeypatch.setattr(cj, "is_configuration_error", lambda e: True)
    clf.tech({"item": {}})
    clf.tech({"item": {}})
    assert len(calls) == 2  # 設定の誤りのあとは呼ばない


def test_no_api_key_means_no_calls():
    clf = cj.Classifier(None, TH)
    assert clf.topic({"item": {}})["status"] == "unavailable" and clf.calls == 0


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
    clf = cj.Classifier(fake_judge({"t3": 0.1, "t7": 0.2}), TH)
    cj.classify_ranking(
        sources, "qiita", "Qiita", clf, CONF, excluded, lambda e: fetched.append(e["url"])
    )
    assert [e["title"] for e in sources["qiita"]][-2:] == ["r11", "r12"]
    assert len(sources["qiita"]) == 10
    assert [(x["source"], x["title"]) for x in excluded] == [("qiita", "t3"), ("qiita", "t7")]
    assert fetched == ["https://q/11", "https://q/12"]


def post(pid, text, links=None):
    return {
        "id": pid,
        "text": text,
        "url": f"https://x.com/a/status/{pid}",
        "quoted": None,
        "links": links or [],
        "jev": None,
    }


def test_run_end_to_end(digest_root):
    day = "2026-10-03"
    link = {"article_key": "a1", "card_title": "C", "card_description": "D", "url": "https://e/a"}
    bookmarks = {
        "date": day,
        "posts": [post("1", "ラーメンおいしい"), post("2", "Claude Code のフック", [link])],
    }
    (digest_root / "data" / f"{day}.json").write_text(json.dumps(bookmarks))
    (digest_root / "data" / "articles" / "a1.json").write_text(
        json.dumps({"title": "記事", "text": "本文" * 3000})
    )
    sources = {
        "github": [{"title": "o/r", "description": "d", "article_key": "g1", "jev": None}],
        "qiita": [], "zenn": [], "devio": [], "blogs": [],
        "reserve": {"qiita": [], "zenn": []},
    }  # fmt: skip
    (digest_root / "data" / "sources" / f"{day}.json").write_text(json.dumps(sources))
    log = []

    result = cj.run(
        cj.Classifier(fake_judge({"ラーメンおいしい": 0.05}, log=log), TH), CONF, lambda e: None
    )

    assert result == {"calls": 3, "failures": 0, "excluded": 1}
    out = json.loads((digest_root / "data" / f"{day}.json").read_text())
    assert out["posts"][0]["jev"]["tech_label"] == "excluded"  # 投稿は残し、印を付ける
    assert out["posts"][1]["jev"] == {
        "status": "ok",
        "topic": "llm_agents",
        "topic_prob": 0.8,
        "tech_prob": 0.9,
        "tech_label": "tech",
    }
    ex = json.loads((digest_root / "data" / "excluded" / f"{day}.json").read_text())
    assert ex["items"][0]["source"] == "bookmarks"
    src = json.loads((digest_root / "data" / "sources" / f"{day}.json").read_text())
    assert "reserve" not in src and src["github"][0]["jev"]["topic"] == "llm_agents"
    assert ("o/r", False) in log  # GitHub はトピックだけ


def test_rerun_skips_judged_items(digest_root):
    day = "2026-10-03"
    done = post("1", "済み")
    done["jev"] = {
        "status": "ok",
        "topic": "devtools",
        "topic_prob": 0.9,
        "tech_prob": 0.9,
        "tech_label": "tech",
    }
    (digest_root / "data" / f"{day}.json").write_text(json.dumps({"date": day, "posts": [done]}))
    clf = cj.Classifier(fake_judge(), TH)
    cj.run(clf, CONF, lambda e: None)
    assert clf.calls == 0


def test_bookmark_state_clips_article():
    p = {
        "text": "t",
        "quoted": {"text": "q"},
        "links": [{"article_key": "a", "card_title": "c", "card_description": "d"}],
    }
    state = cj.bookmark_state(p, {"a": {"title": "T", "text": "x" * 9000}}, CONF)
    art = state["item"]["linked_articles"][0]
    assert art["title"] == "T" and len(art["body"]) == CONF["state_chars"]
    assert state["item"]["quoted_post_text"] == "q"


def test_bookmark_state_falls_back_to_card():
    p = {
        "text": "t",
        "quoted": None,
        "links": [{"article_key": "a", "card_title": "c", "card_description": "d"}],
    }
    art = cj.bookmark_state(p, {"a": None}, CONF)["item"]["linked_articles"][0]
    assert art == {"title": "c", "body": "d"}


def test_article_state_falls_back_to_feed_summary():
    item = {"title": "T", "summary": "フィードの概要"}
    assert cj.article_state(item, None, "OpenAI News", CONF)["item"]["body"] == "フィードの概要"
    record = {"text": "本文"}
    assert cj.article_state(item, record, "OpenAI News", CONF)["item"]["body"] == "本文"


def test_questions_build_with_sdk():
    from typesafe_sdk import Choice, Noul

    assert Noul(**CONF["tech_question"]) is not None
    assert (
        Choice(
            instructions=CONF["topic_question"]["instructions"],
            criteria={"a": "A", "other": "none"},
        )
        is not None
    )
