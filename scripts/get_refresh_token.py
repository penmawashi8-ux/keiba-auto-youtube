#!/usr/bin/env python3
"""
YouTube サムネイル対応リフレッシュトークン取得スクリプト。

使い方:
  1. Google Cloud Console でリダイレクトURIに http://localhost:8080/ を追加
  2. GOOGLE_CLIENT_ID と GOOGLE_CLIENT_SECRET を環境変数にセット
  3. このスクリプトを実行してブラウザで認証
  4. 表示された refresh_token を GitHub Secrets の GOOGLE_REFRESH_TOKEN に上書き

必要スコープ:
  - youtube.upload     : 動画アップロード
  - youtube.force-ssl  : サムネイルアップロード
"""

import os
import sys
import urllib.parse
import urllib.request
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:8080/"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("[エラー] GOOGLE_CLIENT_ID と GOOGLE_CLIENT_SECRET を環境変数にセットしてください。")
        sys.exit(1)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # 必ず refresh_token を返させる
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    print("以下のURLをブラウザで開いて認証してください:\n")
    print(url)
    print()

    # ローカルサーバーで認証コードを受け取る
    auth_code = None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            auth_code = params.get("code", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h1>OK: ターミナルに戻ってください</h1>")

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", 8080), Handler)
    print("http://localhost:8080/ で待機中... (ブラウザで認証後に自動で進みます)\n")
    server.handle_request()

    if not auth_code:
        print("[エラー] 認証コードを取得できませんでした。")
        sys.exit(1)

    # 認証コードをトークンに交換
    data = urllib.parse.urlencode({
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        token_data = json.loads(resp.read())

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("[エラー] refresh_token が取得できませんでした。")
        print(json.dumps(token_data, indent=2))
        sys.exit(1)

    print("=" * 60)
    print("取得成功！以下の refresh_token を")
    print("GitHub Secrets > GOOGLE_REFRESH_TOKEN に上書きしてください:\n")
    print(refresh_token)
    print("=" * 60)


if __name__ == "__main__":
    main()
