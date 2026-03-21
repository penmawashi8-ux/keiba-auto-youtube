#!/usr/bin/env python3
"""YouTube Data API v3 でOAuth2（refresh_token方式）を使って動画をアップロードする。"""

import json
import os
import sys
from pathlib import Path

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

NEWS_JSON = "news.json"
SCRIPT_TXT = "script.txt"
VIDEO_FILE = "output/video.mp4"
POSTED_IDS_FILE = "posted_ids.txt"

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CATEGORY_ID = "17"  # スポーツ
TAGS = ["競馬", "競馬ニュース", "keiba", "Shorts", "競馬速報"]


def load_credentials() -> Credentials:
    """環境変数からOAuth2認証情報を構築する。"""
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    missing = [
        name for name, val in [
            ("YOUTUBE_CLIENT_ID", client_id),
            ("YOUTUBE_CLIENT_SECRET", client_secret),
            ("YOUTUBE_REFRESH_TOKEN", refresh_token),
        ] if not val
    ]
    if missing:
        print(f"[エラー] 環境変数が未設定です: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=YOUTUBE_SCOPES,
    )

    # トークンをリフレッシュ
    try:
        request = google.auth.transport.requests.Request()
        creds.refresh(request)
        print("OAuth2トークンのリフレッシュ成功。")
    except Exception as e:
        print(f"[エラー] トークンリフレッシュ失敗: {e}", file=sys.stderr)
        sys.exit(1)

    return creds


def update_posted_ids(news_items: list[dict]) -> None:
    """投稿済みIDをposted_ids.txtに追記する。"""
    path = Path(POSTED_IDS_FILE)
    existing = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
    new_ids = {item["id"] for item in news_items}
    all_ids = existing | new_ids
    path.write_text("\n".join(sorted(all_ids)), encoding="utf-8")
    print(f"投稿済みID {len(new_ids)} 件を {POSTED_IDS_FILE} に追記しました。")


def upload_video(creds: Credentials, title: str, description: str, video_path: str) -> str:
    """YouTube に動画をアップロードしてvideoIdを返す。"""
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": f"【競馬速報】{title} #Shorts",
            "description": description,
            "tags": TAGS,
            "categoryId": CATEGORY_ID,
            "defaultLanguage": "ja",
            "defaultAudioLanguage": "ja",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024,  # 1MB チャンク
    )

    print(f"YouTube にアップロード中: {body['snippet']['title']}")
    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                print(f"  アップロード進捗: {progress}%")

        video_id = response["id"]
        print(f"アップロード完了！ Video ID: {video_id}")
        print(f"URL: https://www.youtube.com/watch?v={video_id}")
        return video_id

    except HttpError as e:
        error_content = json.loads(e.content.decode("utf-8"))
        print(f"[エラー] YouTube API エラー: {error_content}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[エラー] アップロード失敗: {e}", file=sys.stderr)
        sys.exit(1)


def build_description(script: str) -> str:
    hashtags = "\n\n#競馬 #競馬ニュース #keiba #Shorts #競馬速報"
    max_len = 5000 - len(hashtags)
    return script[:max_len] + hashtags


def main() -> None:
    print("=== YouTube アップロード開始 ===")

    # 入力ファイル確認
    for path in [VIDEO_FILE, NEWS_JSON, SCRIPT_TXT]:
        if not Path(path).exists():
            print(f"[エラー] {path} が見つかりません。", file=sys.stderr)
            sys.exit(1)

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のためアップロードをスキップします。")
        sys.exit(0)

    script = Path(SCRIPT_TXT).read_text(encoding="utf-8").strip()
    title = news_items[0]["title"]
    description = build_description(script)

    creds = load_credentials()
    upload_video(creds, title, description, VIDEO_FILE)
    update_posted_ids(news_items)

    print("=== アップロード処理完了 ===")


if __name__ == "__main__":
    main()
