"""手順1〜2: トークン更新・ブックマーク取得・差分抽出。

1. Secrets のリフレッシュトークンで X API のアクセストークンを取る。
2. 新しいリフレッシュトークンを、ほかの処理より先に Secrets へ書き戻す。失敗したら止める
   （古いトークンのまま進むと、次回以降の実行がすべて失敗するため）。
3. ブックマークをページ送りで取り、state/seen_ids.json にない投稿だけを data/YYYY-MM-DD.json に足す。

入力: 環境変数 X_CLIENT_ID, X_CLIENT_SECRET, X_REFRESH_TOKEN, X_USER_ID, GH_PAT、state/seen_ids.json
出力: data/YYYY-MM-DD.json（BookmarksFile）、state/seen_ids.json

アクセストークンはメモリにだけ置き、ファイルやログに出さない。
X API の取得に失敗した場合は、エラーを data/YYYY-MM-DD.json に記録して終了コード 0 で終える
（追加の情報源の処理は続ける）。GitHub Actions では出力 bookmarks_error=1 を立て、
ワークフローの最後で失敗させて通知メールが届くようにする。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx

from scripts.lib import actions, x_oauth
from scripts.lib.models import Author, BookmarksFile, Link, Post, Quoted, SeenIds
from scripts.lib.store import (
    bookmarks_path,
    now_iso,
    read_json,
    root,
    seen_ids_path,
    today_jst,
    write_json,
)
from scripts.lib.urls import clean_url, domain_of, is_article_url, url_key

BOOKMARKS_URL = "https://api.x.com/2/users/{user_id}/bookmarks"
PAGE_SIZE = 20
"""1ページの件数。X API は返した件数ぶん課金される（取得済みの投稿も含む）ので大きくしない。"""
MAX_PAGES = 10
"""1回の実行で読むページの上限。取得済みの投稿に当たれば、そこで止める。"""
MAX_SEEN_IDS = 20_000
PARAMS = {
    "expansions": "author_id,referenced_tweets.id,referenced_tweets.id.author_id",
    "tweet.fields": "created_at,author_id,entities,referenced_tweets,note_tweet",
    "user.fields": "username,name",
}
LOCAL_TOKEN_FILE = ".secrets/x_refresh_token"

Runner = Callable[..., Any]
"""subprocess.run と同じ呼び方をする関数（テストで差し替える）。"""


class BookmarksFetchError(RuntimeError):
    """X API からブックマークを取れなかった（トークンの問題ではない）。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BookmarkBatch(list[Post]):
    """取得した投稿と、ページ上限で中断した場合の続き。"""

    next_token: str | None = None


# ---------- リフレッシュトークンの読み書き ----------


def load_refresh_token() -> str:
    """GitHub Actions では Secret（環境変数）、ローカルでは .secrets/ のファイルを優先する。"""
    local = root() / LOCAL_TOKEN_FILE
    if not actions.in_actions() and local.exists():
        token = local.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = os.environ.get("X_REFRESH_TOKEN", "").strip()
    if not token:
        raise x_oauth.TokenError("X_REFRESH_TOKEN is not set")
    return token


def save_refresh_token(token: str, runner: Runner = subprocess.run) -> None:
    """新しいリフレッシュトークンを書き戻す。GitHub Actions では gh secret set（GH_PAT で認証）。"""
    if actions.in_actions():
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        pat = os.environ.get("GH_PAT", "")
        if not repo or not pat:
            raise x_oauth.TokenError(
                "GITHUB_REPOSITORY or GH_PAT is not set; cannot write back the token"
            )
        result = runner(
            ["gh", "secret", "set", "X_REFRESH_TOKEN", "--repo", repo],
            input=token,
            text=True,
            capture_output=True,
            env={**os.environ, "GH_TOKEN": pat},
        )
        if result.returncode != 0:
            raise x_oauth.TokenError(f"gh secret set failed (exit {result.returncode})")
        return
    path = root() / LOCAL_TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)


# ---------- 取得 ----------


def fetch_new_bookmarks(
    http: httpx.Client,
    access_token: str,
    user_id: str,
    seen: set[str],
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    *,
    pagination_token: str | None = None,
) -> BookmarkBatch:
    """新着のブックマークを新しい順に返す。取得済みの投稿に当たったら、そこで止める。"""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = BOOKMARKS_URL.format(user_id=user_id)
    posts = BookmarkBatch()
    token = pagination_token
    for _ in range(max_pages):
        params: dict[str, Any] = {**PARAMS, "max_results": page_size}
        if token:
            params["pagination_token"] = token
        try:
            response = http.get(url, params=params, headers=headers)
        except httpx.HTTPError as error:
            raise BookmarksFetchError(type(error).__name__) from error
        if response.is_error:
            raise BookmarksFetchError(f"HTTP {response.status_code}", response.status_code)
        body = response.json()
        includes = body.get("includes") or {}
        users = {u["id"]: u for u in includes.get("users", [])}
        tweets = {t["id"]: t for t in includes.get("tweets", [])}
        for tweet in body.get("data") or []:
            if tweet["id"] in seen:
                return posts
            posts.append(to_post(tweet, users, tweets))
        token = (body.get("meta") or {}).get("next_token")
        if not token:
            break
    posts.next_token = token
    return posts


# ---------- X API の応答を Post に整える ----------


def url_entities(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    """長文投稿は note_tweet 側の URL を使う。"""
    note = tweet.get("note_tweet") or {}
    note_urls = (note.get("entities") or {}).get("urls")
    return note_urls or (tweet.get("entities") or {}).get("urls") or []


def tweet_text(tweet: dict[str, Any]) -> str:
    """長文投稿は note_tweet の本文を優先する。t.co の短縮URLは展開したURLに置き換える。"""
    note = tweet.get("note_tweet") or {}
    text: str = note.get("text") or tweet.get("text") or ""
    for item in url_entities(tweet):
        short = item.get("url")
        full = item.get("unwound_url") or item.get("expanded_url")
        if short and full:
            text = text.replace(short, full)
    return text.strip()


def extract_links(tweet: dict[str, Any], origin: str) -> list[Link]:
    links: list[Link] = []
    for item in url_entities(tweet):
        url = item.get("unwound_url") or item.get("expanded_url") or ""
        if not is_article_url(url):
            continue
        url = clean_url(url)
        links.append(
            {
                "url": url,
                "domain": domain_of(url),
                "card_title": item.get("title") or "",
                "card_description": item.get("description") or "",
                "article_key": url_key(url),
                "from": "quoted" if origin == "quoted" else "self",
            }
        )
    return links


def author_of(tweet: dict[str, Any], users: dict[str, Any]) -> Author:
    user = users.get(tweet.get("author_id", ""), {})
    return {"username": user.get("username", ""), "name": user.get("name", "")}


def status_url(username: str, tweet_id: str) -> str:
    return f"https://x.com/{username or 'i/web'}/status/{tweet_id}"


def to_post(tweet: dict[str, Any], users: dict[str, Any], tweets: dict[str, Any]) -> Post:
    author = author_of(tweet, users)
    quoted: Quoted | None = None
    links = extract_links(tweet, "self")
    for ref in tweet.get("referenced_tweets") or []:
        if ref.get("type") != "quoted":
            continue
        source = tweets.get(ref["id"])
        if source is None:  # 引用元が削除・非公開
            quoted = {
                "id": ref["id"],
                "text": "",
                "author": {"username": "", "name": ""},
                "url": status_url("", ref["id"]),
            }
            continue
        q_author = author_of(source, users)
        quoted = {
            "id": source["id"],
            "text": tweet_text(source),
            "author": q_author,
            "url": status_url(q_author["username"], source["id"]),
        }
        links += extract_links(source, "quoted")
    unique: dict[str, Link] = {}
    for link in links:
        unique.setdefault(link["article_key"], link)
    return {
        "id": tweet["id"],
        "text": tweet_text(tweet),
        "author": author,
        "created_at": tweet.get("created_at", ""),
        "url": status_url(author["username"], tweet["id"]),
        "fetched_at": now_iso(),
        "quoted": quoted,
        "links": list(unique.values()),
        "jev": None,
    }


# ---------- 保存 ----------


def merge_day_file(
    day: date, posts: list[Post], error: str | None, *, incomplete: bool = False
) -> int:
    """同じ日の再実行では既存の投稿に足す（ID で重複を除く）。追加した件数を返す。"""
    path = bookmarks_path(day)
    data: BookmarksFile = read_json(path, {"date": day.isoformat(), "posts": []})
    known = {p["id"] for p in data["posts"]}
    added = [p for p in posts if p["id"] not in known]
    data["posts"].extend(added)
    data["fetched_at"] = now_iso()
    data["incomplete"] = incomplete
    if error:
        data["error"] = error
    else:
        data.pop("error", None)
    write_json(path, data)
    return len(added)


def update_seen(seen: list[str], new_ids: list[str]) -> None:
    ids = list(dict.fromkeys([*new_ids, *seen]))[:MAX_SEEN_IDS]
    state: SeenIds = {"ids": ids}
    write_json(seen_ids_path(), state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="トークン更新・ブックマーク取得・差分抽出")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    args = parser.parse_args(argv)
    if args.max_pages < 1 or not 5 <= args.page_size <= 100:
        parser.error("max-pages は1以上、page-size は5〜100にしてください")

    day = today_jst()
    state: SeenIds = read_json(seen_ids_path(), {"ids": []})
    seen = state["ids"]
    pending = state.get("pending_ids", [])
    cursor = state.get("pagination_token")
    client_id = os.environ.get("X_CLIENT_ID", "")
    user_id = os.environ.get("X_USER_ID", "")
    if not client_id or not user_id:
        actions.error("X_CLIENT_ID and X_USER_ID are required")
        return 1

    with httpx.Client(timeout=30.0) as http:
        try:
            access, new_refresh = x_oauth.refresh(
                http, client_id, os.environ.get("X_CLIENT_SECRET") or None, load_refresh_token()
            )
            save_refresh_token(new_refresh)
        except x_oauth.TokenError as error:
            # ここで止める。後続の処理（情報源の取得など）も走らせない。
            actions.error(str(error))
            return 1
        print("refresh token rotated and written back")

        error_message = None
        next_token = None
        try:
            batch = fetch_new_bookmarks(
                http,
                access,
                user_id,
                set(seen),
                args.page_size,
                args.max_pages,
                pagination_token=cursor,
            )
            next_token = batch.next_token
            known_pending = set(pending)
            posts = [p for p in batch if p["id"] not in known_pending]
        except BookmarksFetchError as error:
            posts, error_message = [], f"ブックマークの取得に失敗: {error}"
            actions.warning(error_message)
            actions.set_output("bookmarks_error", "1")
            if cursor and error.status_code in (400, 404):
                # 期限切れのカーソルは次回先頭から読み直す。旧境界と取得途中のIDは保つ。
                state.pop("pagination_token", None)
                write_json(seen_ids_path(), state)

    added = merge_day_file(
        day,
        posts,
        error_message,
        incomplete=bool(next_token) if not error_message else bool(pending),
    )
    if not error_message:
        collected = list(dict.fromkeys([*pending, *(p["id"] for p in posts)]))
        if next_token:
            write_json(
                seen_ids_path(),
                {
                    "ids": seen,
                    "pending_ids": collected,
                    "pagination_token": next_token,
                },
            )
            actions.warning("ブックマークはページ上限に達しました。次回に続きから取得します")
        else:
            update_seen(seen, collected)
    print(f"bookmarks: {added} new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
