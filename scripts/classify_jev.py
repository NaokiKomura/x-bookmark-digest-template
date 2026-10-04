"""手順5: Jev（TypeSafe AI）によるテック判定とトピック分類。

| 判定 | 対象 | 使い方（しきい値は config/jev.json） |
| --- | --- | --- |
| テック判定（Noul） | ブックマーク、Qiita・Zenn | 0.7以上は tech、0.4以上0.7未満は hold、0.4未満は excluded |
| トピック分類（Choice） | 上記を通ったものと、GitHub・DevelopersIO・公式ブログ | 最上位を採用。確率0.5未満は other |

テック判定とトピック分類は同じ state に対する独立した問いなので、1回の呼び出しで並列に尋ねる
（除外になった項目のトピックは使わない）。問いの文面は config/jev.json、選択肢は config/topics.json。

Jev が応答しない場合（early access で SLA がない）は、その項目の jev を status: unavailable にして
ほかの値を空にする。ルーチンの Claude が代わりにテック判定とトピック分類を行う。
認証・権限・モデル名の誤りはすべての項目で失敗するので、以降の呼び出しをやめて全件 unavailable にする。
Jev には公開されている投稿・記事・README の本文だけを送る（トークンや個人の設定値は含めない）。

入力: 当日の data/YYYY-MM-DD.json、data/sources/YYYY-MM-DD.json、data/articles/、環境変数 TYPESAFE_API_KEY
出力: 上の2ファイルの jev を埋める（Qiita・Zenn は除外を外して補充、reserve を消す）、data/excluded/YYYY-MM-DD.json
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any, Literal, Protocol, cast

from scripts.lib import actions
from scripts.lib.models import (
    ArticleRecord,
    BlogItem,
    BookmarksFile,
    ExcludedFile,
    ExcludedItem,
    Post,
    RankingItem,
    RepoItem,
    SourcesFile,
    TechJev,
    TechLabel,
    TopicJev,
)
from scripts.lib.store import (
    article_path,
    bookmarks_path,
    excluded_path,
    load_config,
    read_json,
    sources_path,
    today_jst,
    write_json,
)

TECH_QUESTION_ID = "is_tech"
TOPIC_QUESTION_ID = "topic"

State = dict[str, Any]
RawAnswer = dict[str, Any]
"""Judge の戻り値: {"topic": str, "topic_prob": float, "tech_prob": float（with_tech のときだけ）}"""


class Judge(Protocol):
    """Jev への1回の問い合わせ。テストでは偽物に差し替える。"""

    def __call__(self, state: State, with_tech: bool) -> RawAnswer: ...


# ---------- 設定と問い ----------


def jev_config() -> dict[str, Any]:
    conf: dict[str, Any] = load_config("jev.json")
    return conf


def make_judge(api_key: str, conf: dict[str, Any], topics: list[dict[str, str]]) -> Judge:
    from typesafe_sdk import Choice, Noul, TypeSafeClient

    client = TypeSafeClient(api_key=api_key, model=conf["model"], timeout=30.0)
    tech_q = Noul(**conf["tech_question"])
    topic_q = Choice(
        instructions=conf["topic_question"]["instructions"],
        criteria={t["id"]: t["description"] for t in topics},
    )

    def judge(state: State, with_tech: bool) -> RawAnswer:
        questions: dict[str, Any] = {TOPIC_QUESTION_ID: topic_q}
        if with_tech:
            questions[TECH_QUESTION_ID] = tech_q
        response = client.system_one(state=state, questions=questions)
        topic = response.choices[TOPIC_QUESTION_ID]
        answer: RawAnswer = {
            "topic": topic.choice,
            "topic_prob": float(topic.probabilities.get(topic.choice, 0.0)),
        }
        if with_tech:
            answer["tech_prob"] = float(response.nouls[TECH_QUESTION_ID].noul)
        return answer

    return judge


# ---------- state（Jev に渡す入力） ----------


def clip(text: str | None, conf: dict[str, Any]) -> str:
    return (text or "")[: conf["state_chars"]]


def bookmark_state(
    post: Post, articles: dict[str, ArticleRecord | None], conf: dict[str, Any]
) -> State:
    """投稿本文、引用元の本文、リンク先記事のタイトルと本文の先頭。記事が取れていなければ X のカード情報。"""
    fields: dict[str, Any] = {
        "type": "X (Twitter) post bookmarked by the reader",
        "post_text": post["text"],
    }
    if post["quoted"]:
        fields["quoted_post_text"] = post["quoted"]["text"]
    linked = []
    for link in post["links"][: conf["max_articles_per_bookmark"]]:
        record = articles.get(link["article_key"])
        linked.append(
            {
                "title": (record and record["title"]) or link["card_title"],
                "body": clip((record and record["text"]) or link["card_description"], conf),
            }
        )
    if linked:
        fields["linked_articles"] = linked
    return {"item": fields}


def article_state(
    item: RankingItem | BlogItem, record: ArticleRecord | None, site: str, conf: dict[str, Any]
) -> State:
    """記事・ブログ: タイトルと本文の先頭。本文が取れていなければフィードの概要（summary）を使う。"""
    body = (record and record["text"]) or item.get("summary", "")
    return {
        "item": {"type": f"article on {site}", "title": item["title"], "body": clip(body, conf)}
    }


def repo_state(repo: RepoItem, record: ArticleRecord | None, conf: dict[str, Any]) -> State:
    return {
        "item": {
            "type": "GitHub repository",
            "name": repo["title"],
            "description": repo["description"],
            "readme": clip(record and record["text"], conf),
        }
    }


# ---------- 結果の解釈 ----------


def tech_label(prob: float, thresholds: dict[str, float]) -> TechLabel:
    if prob >= thresholds["tech_accept"]:
        return "tech"
    if prob >= thresholds["tech_hold"]:
        return "hold"
    return "excluded"


def to_topic_jev(raw: RawAnswer | None, thresholds: dict[str, float]) -> TopicJev:
    if raw is None:
        return {"status": "unavailable", "topic": None, "topic_prob": None}
    topic = raw["topic"] if raw["topic_prob"] >= thresholds["topic_min_prob"] else "other"
    return {"status": "ok", "topic": topic, "topic_prob": round(raw["topic_prob"], 3)}


def to_tech_jev(raw: RawAnswer | None, thresholds: dict[str, float]) -> TechJev:
    base = to_topic_jev(raw, thresholds)
    if raw is None:
        return {**base, "tech_prob": None, "tech_label": None}
    return {
        **base,
        "tech_prob": round(raw["tech_prob"], 3),
        "tech_label": tech_label(raw["tech_prob"], thresholds),
    }


def is_configuration_error(error: Exception) -> bool:
    """認証・権限・モデル名の誤り（全項目で失敗するもの）。"""
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


class Classifier:
    """Jev の呼び出しと、障害時の扱いをまとめる。judge が None なら呼ばずに unavailable を返す。"""

    def __init__(self, judge: Judge | None, thresholds: dict[str, float]) -> None:
        self._judge = judge
        self._thresholds = thresholds
        self.calls = 0
        self.failures = 0

    def _ask(self, state: State, with_tech: bool) -> RawAnswer | None:
        if self._judge is None:
            return None
        self.calls += 1
        try:
            return self._judge(state, with_tech)
        except Exception as error:  # noqa: BLE001 Jev の失敗は項目ごとに unavailable にする
            self.failures += 1
            if is_configuration_error(error):
                actions.warning(
                    f"Jev configuration error ({type(error).__name__}); skipping all remaining calls"
                )
                self._judge = None
            else:
                actions.warning(f"Jev failed: {type(error).__name__}")
            return None

    def tech(self, state: State) -> TechJev:
        return to_tech_jev(self._ask(state, with_tech=True), self._thresholds)

    def topic(self, state: State) -> TopicJev:
        return to_topic_jev(self._ask(state, with_tech=False), self._thresholds)


# ---------- 実行 ----------


def load_article(key: str) -> ArticleRecord | None:
    record: ArticleRecord | None = read_json(article_path(key), None)
    return record


def judged(jev: TopicJev | None) -> bool:
    """同じ日の再実行では、判定済み（status: ok）の項目を呼び直さない。"""
    return jev is not None and jev["status"] == "ok"


def classify_bookmarks(
    data: BookmarksFile, clf: Classifier, conf: dict[str, Any], excluded: list[ExcludedItem]
) -> None:
    """除外になった投稿もファイルには残し、tech_label: excluded の印を付ける。"""
    for post in data["posts"]:
        if not judged(post["jev"]):
            articles = {lk["article_key"]: load_article(lk["article_key"]) for lk in post["links"]}
            post["jev"] = clf.tech(bookmark_state(post, articles, conf))
        jev = cast(TechJev, post["jev"])
        if jev["tech_label"] == "excluded":
            title = post["text"].replace("\n", " ")[:120]
            excluded.append(
                {
                    "source": "bookmarks",
                    "id": post["id"],
                    "title": title,
                    "url": post["url"],
                    "tech_prob": jev["tech_prob"],
                }
            )


def classify_ranking(
    sources: SourcesFile,
    name: Literal["qiita", "zenn"],
    site: str,
    clf: Classifier,
    conf: dict[str, Any],
    excluded: list[ExcludedItem],
    fetch_reserve_article: Callable[[RankingItem], object],
) -> None:
    """Qiita・Zenn: テック判定で除外したものを外し、10件を割ったら次点から補充する。"""
    top = int(load_config("sources.json")[name]["top"])
    kept: list[RankingItem] = []
    queue: list[RankingItem] = list(sources[name])
    reserve_lists = sources.get("reserve")
    reserve: list[RankingItem] = list(reserve_lists[name]) if reserve_lists else []
    while queue or (reserve and len(kept) < top):
        if queue:
            item = queue.pop(0)
        else:
            item = reserve.pop(0)
            fetch_reserve_article(item)
        if not judged(item["jev"]):
            item["jev"] = clf.tech(
                article_state(item, load_article(item["article_key"]), site, conf)
            )
        jev = cast(TechJev, item["jev"])  # Qiita・Zenn の jev は常にテック判定つき
        if jev["tech_label"] == "excluded":
            excluded.append(
                {
                    "source": name,
                    "id": item["article_key"],
                    "title": item["title"][:120],
                    "url": item["url"],
                    "tech_prob": jev["tech_prob"],
                }
            )
            continue
        kept.append(item)
    sources[name] = kept[:top]


def classify_topic_only(sources: SourcesFile, clf: Classifier, conf: dict[str, Any]) -> None:
    """GitHub・DevelopersIO・公式ブログはもともとテック系なので、トピック分類だけを行う。"""
    for repo in sources.get("github", []):
        if not judged(repo["jev"]):
            repo["jev"] = clf.topic(repo_state(repo, load_article(repo["article_key"]), conf))
    for item in sources.get("devio", []):
        if not judged(item["jev"]):
            item["jev"] = clf.topic(
                article_state(item, load_article(item["article_key"]), "DevelopersIO", conf)
            )
    for blog in sources.get("blogs", []):
        if not judged(blog["jev"]):
            site = f"{blog['company_label']} {blog['blog']}"
            blog["jev"] = clf.topic(
                article_state(blog, load_article(blog["article_key"]), site, conf)
            )


def run(
    clf: Classifier, conf: dict[str, Any], fetch_reserve_article: Callable[[RankingItem], object]
) -> dict[str, int]:
    day = today_jst()
    saved: ExcludedFile = read_json(excluded_path(day), {"date": day.isoformat(), "items": []})
    excluded: list[ExcludedItem] = list(saved["items"])

    bpath = bookmarks_path(day)
    bookmarks: BookmarksFile | None = read_json(bpath, None)
    if bookmarks is not None:
        classify_bookmarks(bookmarks, clf, conf, excluded)
        write_json(bpath, bookmarks)

    spath = sources_path(day)
    sources: SourcesFile | None = read_json(spath, None)
    if sources is not None:
        classify_ranking(sources, "qiita", "Qiita", clf, conf, excluded, fetch_reserve_article)
        classify_ranking(sources, "zenn", "Zenn", clf, conf, excluded, fetch_reserve_article)
        classify_topic_only(sources, clf, conf)
        sources.pop("reserve", None)
        write_json(spath, sources)

    # 判定が採用へ変わった項目だけ過去の除外から外す。unavailable は履歴を消さない。
    accepted: set[tuple[str, str]] = set()
    if bookmarks is not None:
        accepted.update(
            ("bookmarks", p["id"])
            for p in bookmarks["posts"]
            if p["jev"] and p["jev"]["status"] == "ok" and p["jev"]["tech_label"] != "excluded"
        )
    if sources is not None:
        accepted.update(
            (name, i["article_key"])
            for name in ("qiita", "zenn")
            for i in sources[name]
            if i["jev"]
            and i["jev"]["status"] == "ok"
            and i["jev"].get("tech_label") in ("tech", "hold")
        )
    merged = {(i["source"], i["id"]): i for i in excluded}
    excluded = [i for key, i in merged.items() if key not in accepted]
    out: ExcludedFile = {"date": day.isoformat(), "items": excluded}
    write_json(excluded_path(day), out)
    return {"calls": clf.calls, "failures": clf.failures, "excluded": len(excluded)}


def main() -> int:
    from scripts.fetch_articles import fetch_article
    from scripts.lib.web import RobotsChecker, make_client

    conf = jev_config()
    topics = load_config("topics.json")["topics"]
    api_key = os.environ.get("TYPESAFE_API_KEY", "")
    if not api_key:
        actions.warning("TYPESAFE_API_KEY is not set; every item is saved as jev unavailable")
    clf = Classifier(make_judge(api_key, conf, topics) if api_key else None, conf["thresholds"])
    with make_client() as http:
        robots = RobotsChecker(http)
        result = run(
            clf, conf, lambda item: fetch_article(http, robots, item["article_key"], item["url"])
        )
    print(f"jev: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
