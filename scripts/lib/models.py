"""data/ と state/ に書く JSON の形。取得層とルーチンの間の約束なので、変えるときは docs/data.md も直す。

キーの名前は JSON にそのまま出る。キーワードと同じ名前のキー（from）は関数形式の TypedDict で書く。
レポートに入る report-data の形はここではなく ROUTINE.md と scripts/report_tools.py の validate_data にある。
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

# ---------- 共通 ----------

SourceName = Literal["bookmarks", "github", "qiita", "zenn", "devio", "blogs"]
"""情報源の名前。data/sources の配列名、report-data の source_status、除外の source で同じ綴りを使う。"""

RankingSite = Literal["qiita", "zenn", "devio"]
FetchStatus = Literal["ok", "partial", "blocked", "error"]
TechLabel = Literal["tech", "hold", "excluded"]
JevStatus = Literal["ok", "unavailable"]
SourceStatus = Literal["ok", "none", "error"]
"""取得結果。ok=新着あり、none=取得できたが新着なし、error=取得失敗。"""


class TopicJev(TypedDict):
    """トピック分類だけの結果（GitHub、DevelopersIO、公式ブログ）。unavailable のときは値が None。"""

    status: JevStatus
    topic: str | None
    topic_prob: float | None


class TechJev(TopicJev):
    """テック判定つきの結果（ブックマーク、Qiita、Zenn）。"""

    tech_prob: float | None
    tech_label: TechLabel | None


# ---------- data/YYYY-MM-DD.json（ブックマーク） ----------


class Author(TypedDict):
    username: str
    name: str


class Quoted(TypedDict):
    id: str
    text: str
    author: Author
    url: str


Link = TypedDict(
    "Link",
    {
        "url": str,
        "domain": str,
        "card_title": str,
        "card_description": str,
        "article_key": str,
        "from": Literal["self", "quoted"],
    },
)


class Post(TypedDict):
    id: str
    text: str
    """長文投稿は note_tweet の本文。t.co は展開済み。"""
    author: Author
    created_at: str
    url: str
    fetched_at: str
    quoted: Quoted | None
    links: list[Link]
    jev: TechJev | None
    """None は未判定（classify_jev.py の前）。"""


class BookmarksFile(TypedDict):
    date: str
    fetched_at: NotRequired[str]
    posts: list[Post]
    error: NotRequired[str]
    """X API の取得に失敗したときだけ付く。"""
    incomplete: NotRequired[bool]
    """ページ上限で中断したとき。次回に続きから取得する。"""


# ---------- data/articles/<article_key>.json ----------


class ArticleRecord(TypedDict):
    key: str
    url: str
    domain: str
    title: str
    site_name: str
    published: str | None
    fetched_at: str
    fetch_status: FetchStatus
    chars: int
    """抽出した本文の元の文字数（切り詰める前）。読了目安に使う。"""
    text: str
    """本文。最大 20,000 文字。"""
    error: NotRequired[str]


# ---------- data/sources/YYYY-MM-DD.json ----------


class RepoItem(TypedDict):
    kind: Literal["repo"]
    rank: int
    title: str
    """owner/name"""
    url: str
    description: str
    language: str | None
    stars_today: int | None
    """Search API で代用した日は None。"""
    stars_total: int | None
    article_key: str
    streak_days: int
    jev: TopicJev | None


class RankingItem(TypedDict):
    kind: Literal["article"]
    rank: int
    title: str
    url: str
    published: str | None
    likes: int | None
    summary: str
    """フィードの概要（Qiita のみ。ほかは空）。本文が取れないときの代わり。"""
    article_key: str
    streak_days: int
    """前日にも載っていれば前日の値 + 1。"""
    jev: TechJev | TopicJev | None
    """Qiita・Zenn は TechJev、DevelopersIO は TopicJev。"""
    title_source: NotRequired[Literal["page", "article"]]
    """一覧ページから拾った見出しは page。記事から取り直したら article。"""


class BlogItem(TypedDict):
    kind: Literal["blog"]
    company: str
    company_label: str
    blog: str
    title: str
    url: str
    published: str | None
    summary: str
    """RSS の概要（最大500文字）。一覧ページのブログは空。本文が取れない記事（fetch_status が ok 以外）の要約に使う。"""
    article_key: str
    jev: TopicJev | None
    title_source: NotRequired[Literal["page", "article"]]


class BlogStatus(TypedDict):
    company: str
    company_label: str
    blog: str
    status: Literal["new", "none", "error"]
    count: int


class SourceError(TypedDict):
    source: str
    message: str


class Reserve(TypedDict):
    qiita: list[RankingItem]
    zenn: list[RankingItem]


class SourcesFile(TypedDict):
    date: str
    github: list[RepoItem]
    qiita: list[RankingItem]
    zenn: list[RankingItem]
    devio: list[RankingItem]
    blogs: list[BlogItem]
    reserve: NotRequired[Reserve]
    """テック判定で除外が出たときの補充用。classify_jev.py が消費して消す。"""
    status: dict[str, SourceStatus]
    blog_status: list[BlogStatus]
    notes: dict[str, str]
    errors: list[SourceError]


# ---------- data/excluded/YYYY-MM-DD.json ----------


class ExcludedItem(TypedDict):
    source: Literal["bookmarks", "qiita", "zenn"]
    id: str
    """ブックマークは投稿 ID、記事は article_key。"""
    title: str
    url: str
    tech_prob: float | None


class ExcludedFile(TypedDict):
    date: str
    items: list[ExcludedItem]


# ---------- state/ ----------


class SeenIds(TypedDict):
    ids: list[str]
    """新しい順。上限 20,000 件。"""
    pagination_token: NotRequired[str]
    pending_ids: NotRequired[list[str]]
    """続き取得が完了するまで ids に確定しない取得済みID。"""


class SeenUrls(TypedDict):
    urls: dict[str, list[str]]
    """"<company>/<blog>" → そのブログで見た記事の URL（新しい順、上限 500 件）。"""
