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
OUTPUT_DIR = "output"
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


def upload_video(youtube, title: str, description: str, video_path: str) -> str:
    """YouTube に動画をアップロードしてvideoIdを返す。"""
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
        chunksize=1024 * 1024,
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

    if not Path(NEWS_JSON).exists():
        print(f"[エラー] {NEWS_JSON} が見つかりません。", file=sys.stderr)
        sys.exit(1)

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のためアップロードをスキップします。")
        sys.exit(0)

    video_files = sorted(Path(OUTPUT_DIR).glob("video_*.mp4"))
    if not video_files:
        print(f"[エラー] {OUTPUT_DIR}/video_*.mp4 が見つかりません。", file=sys.stderr)
        sys.exit(1)

    creds = load_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    uploaded_count = 0
    for video_file in video_files:
        idx = int(video_file.stem.split("_")[1])

        if idx >= len(news_items):
            print(f"  [警告] インデックス {idx} の記事がありません。スキップします。")
            continue

        script_path = Path(f"{OUTPUT_DIR}/script_{idx}.txt")
        if not script_path.exists():
            print(f"  [警告] {script_path} が見つかりません。スキップします。")
            continue

        item = news_items[idx]
        title = item["title"]
        script = script_path.read_text(encoding="utf-8").strip()
        description = build_description(script)

        print(f"\n--- アップロード [{idx}]: {title[:50]} ---")
        upload_video(youtube, title, description, str(video_file))
        uploaded_count += 1

    update_posted_ids(news_items)
    print(f"\n=== アップロード処理完了: {uploaded_count} 本 ===")


if __name__ == "__main__":
    main()
