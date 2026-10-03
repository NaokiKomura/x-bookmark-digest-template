"""Jev（TypeSafe AI）によるテック判定とトピック分類（処理フロー 5）。

| 判定 | 対象 | 使い方 |
| --- | --- | --- |
| テック判定（Noul） | ブックマーク、Qiita・Zenn | 0.7以上は tech、0.4以上0.7未満は hold、0.4未満は excluded |
| トピック分類（Choice） | 上記を通ったものと、GitHub・DevelopersIO・公式ブログ | 最上位を採用。確率0.5未満は other |

テック判定とトピック分類は同じ state に対する独立した問いなので、1回の呼び出しで並列に尋ねる
（除外になった項目のトピックは使わない）。問いの文面は英語にする（Jev の精度が最も高い言語）。
質問 ID はモデルに送られないので、問いの文面と criteria だけで意味が通じるように書く。

Jev が応答しない場合（early access で SLA がない）は、その項目の jev を status: unavailable にして
ほかの値を空にする。ルーチンの Claude が代わりにテック判定とトピック分類を行う。
認証・権限・モデル名の誤りはすべての項目で失敗するので、以降の呼び出しをやめて全件 unavailable にする。

Jev には公開されている投稿・記事・README の本文だけを送る（トークンや個人の設定値は含めない）。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Protocol

from scripts.common import (
    bookmarks_path,
    excluded_path,
    gha_warning,
    load_config,
    make_client,
    read_json,
    sources_path,
    today_jst,
    write_json,
)

MODEL = "jev-latest"
TECH_ACCEPT = 0.7
TECH_HOLD = 0.4
TOPIC_MIN_PROB = 0.5
STATE_CHARS = 4_000
MAX_ARTICLES_PER_BOOKMARK = 2
RANKING_TOP = 10
TECH_SOURCES = ("qiita", "zenn")
TOPIC_ONLY_SOURCES = ("github", "devio", "blogs")

TECH_QUESTION_ID = "is_tech"
TOPIC_QUESTION_ID = "topic"


class Judge(Protocol):
    def __call__(self, state: dict[str, Any], with_tech: bool) -> dict[str, Any]: ...


# ---------- 問い ----------


def tech_question() -> Any:
    from typesafe_sdk import Noul

    return Noul(
        instructions=(
            "Is `item` mainly about software development or IT technology? "
            "`item.type` says what kind of content it is. The content may be written in Japanese."
        ),
        criteria={
            "true": {
                "definition": "The main subject is software development or IT technology",
                "includes": [
                    "programming, software design, testing, code review",
                    "AI, machine learning and LLMs, including how to use AI tools for work",
                    "cloud, infrastructure, DevOps, databases, networks",
                    "security and vulnerabilities",
                    "developer tools, editors, programming languages, libraries",
                    "IT products and services and their technical features",
                    "engineering careers and engineering organizations",
                ],
            },
            "false": {
                "definition": "The main subject is something other than software or IT technology",
                "examples": [
                    "daily life, food, travel, hobbies",
                    "entertainment, sports, celebrities",
                    "politics or society without a technology focus",
                    "general business or investment talk without a technology focus",
                ],
                "note": "Mentioning a smartphone or app only as a tool in a non-technical story does not count",
            },
        },
    )


def topic_question(topics: list[dict[str, str]]) -> Any:
    from typesafe_sdk import Choice

    return Choice(
        instructions=(
            "Which technology topic best matches the main subject of `item`? "
            "`item.type` says what kind of content it is. The content may be written in Japanese. "
            "If it spans several topics, choose the one the content most wants to convey."
        ),
        criteria={t["id"]: t["description"] for t in topics},
    )


def make_judge(api_key: str, topics: list[dict[str, str]]) -> Judge:
    from typesafe_sdk import TypeSafeClient

    client = TypeSafeClient(api_key=api_key, model=MODEL, timeout=30.0)
    tech_q, topic_q = tech_question(), topic_question(topics)

    def judge(state: dict[str, Any], with_tech: bool) -> dict[str, Any]:
        questions = {TOPIC_QUESTION_ID: topic_q}
        if with_tech:
            questions[TECH_QUESTION_ID] = tech_q
        response = client.system_one(state=state, questions=questions)
        topic = response.choices[TOPIC_QUESTION_ID]
        result: dict[str, Any] = {
            "topic": topic.choice,
            "topic_prob": float(topic.probabilities.get(topic.choice, 0.0)),
        }
        if with_tech:
            result["tech_prob"] = float(response.nouls[TECH_QUESTION_ID].noul)
        return result

    return judge


# ---------- state ----------


def clip(text: str | None) -> str:
    return (text or "")[:STATE_CHARS]


def bookmark_state(post: dict[str, Any], articles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "X (Twitter) post bookmarked by the reader",
        "post_text": post["text"],
    }
    if post.get("quoted"):
        item["quoted_post_text"] = post["quoted"]["text"]
    linked = []
    for link in post["links"][:MAX_ARTICLES_PER_BOOKMARK]:
        record = articles.get(link["article_key"]) or {}
        linked.append(
            {
                "title": record.get("title") or link.get("card_title", ""),
                "body": clip(record.get("text") or link.get("card_description", "")),
            }
        )
    if linked:
        item["linked_articles"] = linked
    return {"item": item}


def article_state(
    entry: dict[str, Any], record: dict[str, Any] | None, site: str
) -> dict[str, Any]:
    record = record or {}
    return {
        "item": {
            "type": f"article on {site}",
            "title": entry["title"],
            "body": clip(record.get("text")),
        }
    }


def repo_state(entry: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "item": {
            "type": "GitHub repository",
            "name": entry["title"],
            "description": entry.get("description", ""),
            "readme": clip((record or {}).get("text")),
        }
    }


# ---------- 結果の解釈 ----------


def tech_label(prob: float) -> str:
    if prob >= TECH_ACCEPT:
        return "tech"
    if prob >= TECH_HOLD:
        return "hold"
    return "excluded"


def interpret(raw: dict[str, Any] | None, with_tech: bool) -> dict[str, Any]:
    if raw is None:
        empty: dict[str, Any] = {"status": "unavailable", "topic": None, "topic_prob": None}
        if with_tech:
            empty.update({"tech_prob": None, "tech_label": None})
        return empty
    topic = raw["topic"] if raw["topic_prob"] >= TOPIC_MIN_PROB else "other"
    result: dict[str, Any] = {
        "status": "ok",
        "topic": topic,
        "topic_prob": round(raw["topic_prob"], 3),
    }
    if with_tech:
        result = {
            "status": "ok",
            "tech_prob": round(raw["tech_prob"], 3),
            "tech_label": tech_label(raw["tech_prob"]),
            "topic": topic,
            "topic_prob": round(raw["topic_prob"], 3),
        }
    return result


class Classifier:
    """Jev の呼び出しと、障害時の扱いをまとめる。"""

    def __init__(self, judge: Judge | None) -> None:
        self._judge = judge
        self.calls = 0
        self.failures = 0

    def classify(self, state: dict[str, Any], with_tech: bool) -> dict[str, Any]:
        if self._judge is None:
            return interpret(None, with_tech)
        self.calls += 1
        try:
            return interpret(self._judge(state, with_tech), with_tech)
        except Exception as error:  # noqa: BLE001 Jev の失敗は項目ごとに unavailable にする
            self.failures += 1
            if is_configuration_error(error):
                gha_warning(
                    f"Jev configuration error ({type(error).__name__}); skipping all remaining calls"
                )
                self._judge = None
            else:
                gha_warning(f"Jev failed: {type(error).__name__}")
            return interpret(None, with_tech)


def is_configuration_error(error: Exception) -> bool:
    try:
        from typesafe_sdk import (
            TypeSafeAuthenticationError,
            TypeSafeNotFoundError,
            TypeSafePermissionDeniedError,
        )
    except ImportError:
        return False
    return isinstance(
        error, TypeSafeAuthenticationError | TypeSafePermissionDeniedError | TypeSafeNotFoundError
    )


# ---------- 実行 ----------


def load_article(key: str) -> dict[str, Any] | None:
    from scripts.common import article_path

    return read_json(article_path(key), None)


def excluded_entry(
    source: str, item_id: str, title: str, url: str, prob: float | None
) -> dict[str, Any]:
    return {"source": source, "id": item_id, "title": title[:120], "url": url, "tech_prob": prob}


def classify_bookmarks(
    data: dict[str, Any], clf: Classifier, excluded: list[dict[str, Any]]
) -> None:
    for post in data["posts"]:
        if post.get("jev") and post["jev"]["status"] == "ok":
            continue
        articles = {
            lk["article_key"]: load_article(lk["article_key"]) or {} for lk in post["links"]
        }
        post["jev"] = clf.classify(bookmark_state(post, articles), with_tech=True)
        if post["jev"]["tech_label"] == "excluded":
            title = post["text"].replace("\n", " ")
            excluded.append(
                excluded_entry("bookmark", post["id"], title, post["url"], post["jev"]["tech_prob"])
            )


def classify_ranking(
    sources: dict[str, Any],
    name: str,
    site: str,
    clf: Classifier,
    excluded: list[dict[str, Any]],
    fetch_reserve_article: Any,
) -> None:
    """Qiita・Zenn: テック判定で除外したものを外し、10件を割ったら次点から補充する。"""
    kept: list[dict[str, Any]] = []
    queue = list(sources.get(name, []))
    reserve = list(sources.get("reserve", {}).get(name, []))
    while queue or (reserve and len(kept) < RANKING_TOP):
        if queue:
            entry = queue.pop(0)
        else:
            entry = reserve.pop(0)
            fetch_reserve_article(entry)
        if not (entry.get("jev") and entry["jev"]["status"] == "ok"):
            entry["jev"] = clf.classify(
                article_state(entry, load_article(entry["article_key"]), site), with_tech=True
            )
        if entry["jev"].get("tech_label") == "excluded":
            excluded.append(
                excluded_entry(
                    name,
                    entry["article_key"],
                    entry["title"],
                    entry["url"],
                    entry["jev"]["tech_prob"],
                )
            )
            continue
        kept.append(entry)
    sources[name] = kept[:RANKING_TOP]


def classify_topic_only(sources: dict[str, Any], clf: Classifier) -> None:
    for repo in sources.get("github", []):
        if not (repo.get("jev") and repo["jev"]["status"] == "ok"):
            repo["jev"] = clf.classify(
                repo_state(repo, load_article(repo["article_key"])), with_tech=False
            )
    for name, site in (("devio", "DevelopersIO"), ("blogs", None)):
        for entry in sources.get(name, []):
            if entry.get("jev") and entry["jev"]["status"] == "ok":
                continue
            label = site or f"{entry['company_label']} {entry['blog']}"
            entry["jev"] = clf.classify(
                article_state(entry, load_article(entry["article_key"]), label), with_tech=False
            )


def run(clf: Classifier, fetch_reserve_article: Any) -> dict[str, int]:
    day = today_jst()
    excluded: list[dict[str, Any]] = []

    bpath = bookmarks_path(day)
    bookmarks = read_json(bpath, None)
    if bookmarks is not None:
        classify_bookmarks(bookmarks, clf, excluded)
        write_json(bpath, bookmarks)

    spath = sources_path(day)
    sources = read_json(spath, None)
    if sources is not None:
        for name, site in (("qiita", "Qiita"), ("zenn", "Zenn")):
            classify_ranking(sources, name, site, clf, excluded, fetch_reserve_article)
        classify_topic_only(sources, clf)
        sources.pop("reserve", None)
        write_json(spath, sources)

    write_json(excluded_path(day), {"date": day.isoformat(), "items": excluded})
    return {"calls": clf.calls, "failures": clf.failures, "excluded": len(excluded)}


def main() -> int:
    from scripts.common import RobotsChecker
    from scripts.fetch_articles import fetch_article

    topics = load_config("topics.json")["topics"]
    api_key = os.environ.get("TYPESAFE_API_KEY", "")
    if not api_key:
        gha_warning("TYPESAFE_API_KEY is not set; every item is saved as jev unavailable")
    clf = Classifier(make_judge(api_key, topics) if api_key else None)
    with make_client() as http:
        robots = RobotsChecker(http)
        result = run(
            clf, lambda entry: fetch_article(http, robots, entry["article_key"], entry["url"])
        )
    print(f"jev: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
