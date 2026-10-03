"""トークン更新・ブックマーク取得・差分抽出（処理フロー 1〜2）。

1. Secrets のリフレッシュトークンで X API のアクセストークンを取る。
2. 新しいリフレッシュトークンを、ほかの処理より先に Secrets へ書き戻す。失敗したら止める
   （古いトークンのまま進むと、次回以降の実行がすべて失敗するため）。
3. ブックマークをページ送りで取り、`state/seen_ids.json` にない投稿だけを data/YYYY-MM-DD.json に足す。

アクセストークンはメモリにだけ置き、ファイルやログに出さない。
X API の取得に失敗した場合は、エラーを data/YYYY-MM-DD.json に記録して終了コード 0 で終える
（追加の情報源の処理は続ける）。GitHub Actions では出力 `bookmarks_error=1` を立て、
ワークフローの最後で失敗させて通知メールが届くようにする。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from scripts.common import (
    bookmarks_path,
    clean_url,
    domain_of,
    gha_warning,
    is_article_url,
    now_iso,
    read_json,
    root,
    today_jst,
    url_key,
    write_json,
)

TOKEN_URL = "https://api.x.com/2/oauth2/token"
BOOKMARKS_URL = "https://api.x.com/2/users/{user_id}/bookmarks"
PAGE_SIZE = 50
MAX_PAGES = 5
"""1回の実行で読むページの上限。取得済みの投稿に当たれば、そこで止める。"""
MAX_SEEN_IDS = 20_000
PARAMS = {
    "expansions": "author_id,referenced_tweets.id,referenced_tweets.id.author_id",
    "tweet.fields": "created_at,author_id,entities,referenced_tweets,note_tweet",
    "user.fields": "username,name",
}
LOCAL_TOKEN_FILE = ".secrets/x_refresh_token"


class TokenError(RuntimeError):
    pass


# ---------- トークン ----------


def refresh_access_token(
    http: httpx.Client, client_id: str, client_secret: str | None, refresh_token: str
) -> tuple[str, str]:
    """(access_token, 新しい refresh_token) を返す。エラーの本文はトークンを含みうるので理由だけ出す。"""
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    if client_secret:
        response = http.post(TOKEN_URL, data=data, auth=(client_id, client_secret))
    else:
        response = http.post(TOKEN_URL, data={**data, "client_id": client_id})
    if response.is_error:
        try:
            body = response.json()
            reason = f"{body.get('error')}: {body.get('error_description')}"
        except ValueError:
            reason = "(no json body)"
        raise TokenError(f"token refresh failed: HTTP {response.status_code} {reason}")
    body = response.json()
    if not body.get("access_token") or not body.get("refresh_token"):
        raise TokenError("token response lacks access_token or refresh_token")
    return body["access_token"], body["refresh_token"]


def load_refresh_token() -> str:
    """GitHub Actions では Secret（環境変数）、ローカルでは .secrets/ のファイルを優先する。"""
    local = root() / LOCAL_TOKEN_FILE
    if not os.environ.get("GITHUB_ACTIONS") and local.exists():
        token = local.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = os.environ.get("X_REFRESH_TOKEN", "").strip()
    if not token:
        raise TokenError("X_REFRESH_TOKEN is not set")
    return token


def save_refresh_token(token: str, runner: Any = subprocess.run) -> None:
    """新しいリフレッシュトークンを書き戻す。GitHub Actions では `gh secret set`（GH_PAT で認証）。"""
    if os.environ.get("GITHUB_ACTIONS"):
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        pat = os.environ.get("GH_PAT", "")
        if not repo or not pat:
            raise TokenError("GITHUB_REPOSITORY or GH_PAT is not set; cannot write back the token")
        result = runner(
            ["gh", "secret", "set", "X_REFRESH_TOKEN", "--repo", repo],
            input=token,
            text=True,
            capture_output=True,
            env={**os.environ, "GH_TOKEN": pat},
        )
        if result.returncode != 0:
            raise TokenError(f"gh secret set failed (exit {result.returncode})")
        return
    path = root() / LOCAL_TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)


# ---------- 取得と整形 ----------


def fetch_new_bookmarks(
    http: httpx.Client,
    access_token: str,
    user_id: str,
    seen: set[str],
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> list[dict[str, Any]]:
    """新着のブックマークを新しい順に返す。取得済みの投稿に当たったら、そこで止める。"""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = BOOKMARKS_URL.format(user_id=user_id)
    posts: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {**PARAMS, "max_results": page_size}
        if token:
            params["pagination_token"] = token
        response = http.get(url, params=params, headers=headers)
        if response.is_error:
            raise RuntimeError(f"bookmarks request failed: HTTP {response.status_code}")
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
    return posts


def tweet_text(tweet: dict[str, Any]) -> str:
    """長文投稿は note_tweet の本文を優先する。t.co の短縮URLは展開したURLに置き換える。"""
    note = tweet.get("note_tweet") or {}
    text = note.get("text") or tweet.get("text") or ""
    for item in url_entities(tweet):
        short = item.get("url")
        full = item.get("unwound_url") or item.get("expanded_url")
        if short and full:
            text = text.replace(short, full)
    return text.strip()


def url_entities(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    note = tweet.get("note_tweet") or {}
    note_urls = (note.get("entities") or {}).get("urls")
    return note_urls or (tweet.get("entities") or {}).get("urls") or []


def extract_links(tweet: dict[str, Any], origin: str) -> list[dict[str, Any]]:
    links = []
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
                "from": origin,
            }
        )
    return links


def author_of(tweet: dict[str, Any], users: dict[str, Any]) -> dict[str, str]:
    user = users.get(tweet.get("author_id", ""), {})
    return {"username": user.get("username", ""), "name": user.get("name", "")}


def status_url(username: str, tweet_id: str) -> str:
    return f"https://x.com/{username or 'i/web'}/status/{tweet_id}"


def to_post(tweet: dict[str, Any], users: dict[str, Any], tweets: dict[str, Any]) -> dict[str, Any]:
    author = author_of(tweet, users)
    quoted = None
    links = extract_links(tweet, "self")
    for ref in tweet.get("referenced_tweets") or []:
        if ref.get("type") != "quoted":
            continue
        source = tweets.get(ref["id"])
        if source is None:
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
    unique: dict[str, dict[str, Any]] = {}
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


def merge_day_file(path: Path, day: str, posts: list[dict[str, Any]], error: str | None) -> int:
    """同じ日の再実行では既存の投稿に足す（IDで重複を除く）。追加した件数を返す。"""
    data = read_json(path, {"date": day, "posts": []})
    known = {p["id"] for p in data["posts"]}
    added = [p for p in posts if p["id"] not in known]
    data["posts"].extend(added)
    data["fetched_at"] = now_iso()
    if error:
        data["error"] = error
    else:
        data.pop("error", None)
    write_json(path, data)
    return len(added)


def update_seen(path: Path, seen: list[str], new_ids: list[str]) -> None:
    ids = list(dict.fromkeys([*new_ids, *seen]))[:MAX_SEEN_IDS]
    write_json(path, {"ids": ids})


def set_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    args = parser.parse_args(argv)

    day = today_jst()
    seen_path = root() / "state" / "seen_ids.json"
    seen_list: list[str] = read_json(seen_path, {"ids": []})["ids"]
    client_id = os.environ.get("X_CLIENT_ID", "")
    user_id = os.environ.get("X_USER_ID", "")
    if not client_id or not user_id:
        print("X_CLIENT_ID and X_USER_ID are required", file=sys.stderr)
        return 1

    with httpx.Client(timeout=30.0) as http:
        try:
            access, new_refresh = refresh_access_token(
                http, client_id, os.environ.get("X_CLIENT_SECRET") or None, load_refresh_token()
            )
            save_refresh_token(new_refresh)
        except TokenError as error:
            # ここで止める。後続の処理（情報源の取得など）も走らせない。
            print(f"::error::{error}", file=sys.stderr)
            return 1
        print("refresh token rotated and written back")

        error = None
        try:
            posts = fetch_new_bookmarks(
                http, access, user_id, set(seen_list), args.page_size, args.max_pages
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            posts, error = [], f"ブックマークの取得に失敗: {exc}"
            gha_warning(error)
            set_output("bookmarks_error", "1")

    added = merge_day_file(bookmarks_path(day), day.isoformat(), posts, error)
    if not error:
        update_seen(seen_path, seen_list, [p["id"] for p in posts])
    print(f"bookmarks: {added} new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
