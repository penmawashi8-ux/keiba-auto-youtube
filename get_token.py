#!/usr/bin/env python3
"""
YouTube OAuth2 refresh_token をローカルで取得するスクリプト。

使い方:
  1. YOUTUBE_CLIENT_ID と YOUTUBE_CLIENT_SECRET を以下に設定する
  2. python get_token.py を実行する
  3. ブラウザが開いたらGoogleアカウントでログインして許可する
  4. ターミナルに表示される refresh_token を GitHub Secrets に登録する

事前準備:
  pip install google-auth-oauthlib
"""

import json
import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("[エラー] google-auth-oauthlib がインストールされていません。")
    print("  pip install google-auth-oauthlib を実行してください。")
    sys.exit(1)

# ========================================================
# ここに Google Cloud Console で取得したクライアント情報を入力
# ========================================================
CLIENT_ID = "704753208448-rfq5pkn7vtvqmthksdp58u15f4dirfbd.apps.googleusercontent.com"
CLIENT_SECRET = "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"
# ========================================================

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "http://localhost:8080"


def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET:
        print("[エラー] get_token.py の CLIENT_ID と CLIENT_SECRET を設定してください。")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)

    print("ブラウザが開きます。Googleアカウントにログインして YouTube へのアクセスを許可してください。")
    print("（ブラウザが開かない場合は、表示されるURLを手動でブラウザに貼り付けてください）\n")

    creds = flow.run_local_server(
        port=8080,
        prompt="consent",
        access_type="offline",
    )

    print("\n" + "=" * 60)
    print("認証成功！以下の値を GitHub Secrets に登録してください。")
    print("=" * 60)
    print(f"\nYOUTUBE_CLIENT_ID:\n  {CLIENT_ID}")
    print(f"\nYOUTUBE_CLIENT_SECRET:\n  {CLIENT_SECRET}")
    print(f"\nYOUTUBE_REFRESH_TOKEN:\n  {creds.refresh_token}")
    print("\n" + "=" * 60)

    # オプション: トークン情報をファイルに保存
    token_info = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": creds.refresh_token,
    }
    output_path = Path("youtube_token.json")
    output_path.write_text(json.dumps(token_info, indent=2), encoding="utf-8")
    print(f"\n（参考用に {output_path} にも保存しました。このファイルは .gitignore に追加してください）")


if __name__ == "__main__":
    main()
