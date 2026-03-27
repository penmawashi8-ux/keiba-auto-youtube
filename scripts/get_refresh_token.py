#!/usr/bin/env python3
"""
YouTube + Google Drive 対応リフレッシュトークン取得スクリプト。
ローカルサーバー不要・スマホ対応版。

使い方:
  1. GOOGLE_CLIENT_ID と GOOGLE_CLIENT_SECRET を環境変数にセット
  2. このスクリプトを実行
  3. 表示された URL をブラウザで開いてGoogleアカウントで認証
  4. 認証後にブラウザのアドレスバーに表示される URL から
     "code=" の後の文字列をコピーして貼り付け
  5. 表示された refresh_token を GitHub Secrets の GOOGLE_REFRESH_TOKEN に上書き

実行環境:
  - PC (ターミナル)
  - スマホ (Replit / Google Colab / a-Shell など)

必要スコープ:
  - youtube.upload     : 動画アップロード
  - youtube.force-ssl  : サムネイルアップロード
  - drive.file         : Google Drive アップロード（テスト用）
"""

import os
import sys
import urllib.parse
import urllib.request
import json

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/drive.file",
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
        "prompt": "consent",
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("=" * 60)
    print("【手順1】以下の URL をブラウザで開いてください")
    print("=" * 60)
    print(url)
    print()
    print("=" * 60)
    print("【手順2】Googleアカウントで認証後、ブラウザが")
    print("  「このサイトにアクセスできません」エラーを表示します。")
    print("  その時のアドレスバーのURLをコピーしてください。")
    print()
    print("  例: http://localhost/?code=4/0AXXX...&scope=...")
    print("      ↑ この URL 全体 または code= 以降の文字列をコピー")
    print("=" * 60)
    print()

    raw = input("コピーしたURL（またはコード）を貼り付け → ").strip()

    # URL全体が貼られた場合は code= 部分だけ抽出
    if "code=" in raw:
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query).get("code", [raw])[0]
    else:
        auth_code = raw

    if not auth_code:
        print("[エラー] 認証コードを取得できませんでした。")
        sys.exit(1)

    print("\nトークン取得中...")

    data = urllib.parse.urlencode({
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            token_data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[エラー] トークン取得失敗 HTTP {e.code}: {body}")
        sys.exit(1)

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("[エラー] refresh_token が取得できませんでした。")
        print(json.dumps(token_data, indent=2))
        sys.exit(1)

    print()
    print("=" * 60)
    print("✅ 取得成功！以下の refresh_token を")
    print("GitHub Secrets > GOOGLE_REFRESH_TOKEN に上書きしてください:")
    print()
    print(refresh_token)
    print("=" * 60)


if __name__ == "__main__":
    main()
