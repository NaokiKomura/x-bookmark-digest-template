"""X API の OAuth 2.0（Authorization Code + PKCE）。fetch_bookmarks.py と auth_local.py で共通。

リフレッシュトークンは使うたびに新しいものに変わり、古いものは無効になる。
トークンはエラーメッセージやログに出さない。
"""

from __future__ import annotations

from typing import Any

import httpx

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
SCOPES = "tweet.read users.read bookmark.read offline.access"
REDIRECT_URI = "http://127.0.0.1:8765/callback"


class TokenError(RuntimeError):
    """トークンの取得・保存の失敗。これが出たら後続の処理を止める。"""


def post_token(
    http: httpx.Client, client_id: str, client_secret: str | None, data: dict[str, str]
) -> dict[str, Any]:
    """トークンのエンドポイントを呼ぶ。Confidential client は Basic 認証、Public client は client_id を本文に入れる。"""
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
        raise TokenError(f"token request failed: HTTP {response.status_code} {reason}")
    body = response.json()
    if not isinstance(body, dict) or not body.get("refresh_token"):
        raise TokenError("token response lacks refresh_token (is offline.access granted?)")
    return body


def refresh(
    http: httpx.Client, client_id: str, client_secret: str | None, refresh_token: str
) -> tuple[str, str]:
    """(access_token, 新しい refresh_token) を返す。"""
    body = post_token(
        http,
        client_id,
        client_secret,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    if not body.get("access_token"):
        raise TokenError("token response lacks access_token")
    return str(body["access_token"]), str(body["refresh_token"])
