"""初回のリフレッシュトークンをローカルで取る（OAuth 2.0 Authorization Code + PKCE）。

使い方:
    X_CLIENT_ID=... X_CLIENT_SECRET=... uv run python -m scripts.auth_local --repo OWNER/REPO

ブラウザで X の認可画面が開く。許可すると、受け取ったリフレッシュトークンを
`gh secret set X_REFRESH_TOKEN --repo OWNER/REPO` で直接 Secrets に登録する（画面やファイルには出さない）。
--repo を省くと .secrets/x_refresh_token（権限 600）に保存する（ローカルでの試験用）。

X アプリのコールバック URL に http://127.0.0.1:8765/callback を登録しておくこと。
スコープは tweet.read users.read bookmark.read offline.access の4つ。
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import http.server
import os
import secrets
import subprocess
import sys
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from scripts.lib.store import root
from scripts.lib.x_oauth import AUTHORIZE_URL, REDIRECT_URI, SCOPES, TokenError, post_token


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def wait_for_code(expected_state: str) -> str:
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlsplit(self.path).query)
            if urlsplit(self.path).path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            if query.get("state", [""])[0] != expected_state:
                result["error"] = "state mismatch"
            elif "code" in query:
                result["code"] = query["code"][0]
            else:
                result["error"] = query.get("error", ["unknown"])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("認証が終わりました。このタブを閉じてください。".encode())

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8765), Handler)
    while not result:
        server.handle_request()
    server.server_close()
    if "error" in result:
        raise SystemExit(f"authorization failed: {result['error']}")
    return result["code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初回のリフレッシュトークンを取得して登録する")
    parser.add_argument(
        "--repo", help="Secrets を登録するリポジトリ（OWNER/REPO）。省略時は .secrets/ に保存"
    )
    args = parser.parse_args(argv)

    client_id = os.environ.get("X_CLIENT_ID") or input("X_CLIENT_ID: ").strip()
    client_secret = os.environ.get("X_CLIENT_SECRET")
    if client_secret is None:
        client_secret = getpass.getpass(
            "X_CLIENT_SECRET（Public client なら空のまま Enter）: "
        ).strip()

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = (
        AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPES,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    print("ブラウザで X の認可画面を開きます。開かない場合は次の URL を開いてください:\n" + url)
    webbrowser.open(url)
    code = wait_for_code(state)

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }
    try:
        with httpx.Client(timeout=30.0) as http:
            refresh = str(post_token(http, client_id, client_secret or None, data)["refresh_token"])
    except TokenError as error:
        raise SystemExit(str(error)) from error

    if args.repo:
        done = subprocess.run(
            ["gh", "secret", "set", "X_REFRESH_TOKEN", "--repo", args.repo],
            input=refresh,
            text=True,
        )
        if done.returncode != 0:
            raise SystemExit("gh secret set failed")
        print(f"X_REFRESH_TOKEN を {args.repo} の Secrets に登録しました。")
    else:
        path = root() / ".secrets" / "x_refresh_token"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(refresh)
        print(f"リフレッシュトークンを {path} に保存しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
