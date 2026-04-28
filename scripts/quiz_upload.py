#!/usr/bin/env python3
"""
quiz_video.mp4 を YouTube にアップロードする。
assets/quiz_thumbnail.jpg が存在すればサムネイルも設定する。
"""

import json
import os
import sys
from pathlib import Path

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

VIDEO_PATH = Path("quiz_video.mp4")
QUIZ_JSON = Path("quiz.json")
CATEGORY_ID = "17"   # スポーツ


def find_thumbnail() -> Path | None:
    assets = Path("assets")
    if not assets.exists():
        return None
    candidates = [
        assets / "quiz_thumbnail.jpg",
        assets / "quiz_thumbnail.jpeg",
        assets / "quiz_thumbnail.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    for p in sorted(assets.iterdir()):
        if "thumbnail" in p.name.lower() and p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            return p
    return None

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

CREDENTIAL_SETS = [
    ("GOOGLE_CLIENT_ID",   "GOOGLE_CLIENT_SECRET",   "GOOGLE_REFRESH_TOKEN"),
    ("GOOGLE_CLIENT_ID_2", "GOOGLE_CLIENT_SECRET_2", "GOOGLE_REFRESH_TOKEN_2"),
    ("GOOGLE_CLIENT_ID_3", "GOOGLE_CLIENT_SECRET_3", "GOOGLE_REFRESH_TOKEN_3"),
]

TITLE_OPTIONS = [
    "【名馬クイズ】この馬は誰だ！？90年代の伝説馬を全問正解できる？",
    "この馬は誰だ！？90年代の名馬クイズに全問正解できたら競馬通！",
    "【競馬クイズ】90年代の名馬を当てろ！全問正解できたらすごい！",
    "90年代競馬の伝説馬、全部わかる？【名馬当てクイズ・全15問】",
]

BASE_TAGS = [
    "競馬", "競馬クイズ", "名馬クイズ", "この馬は誰", "名馬",
    "競馬ファン", "keiba", "競馬速報", "名馬当てクイズ", "90年代競馬",
    "競馬動画", "競馬チャンネル", "クイズ",
]


def get_youtube_client():
    for id_key, secret_key, token_key in CREDENTIAL_SETS:
        client_id     = os.environ.get(id_key)
        client_secret = os.environ.get(secret_key)
        refresh_token = os.environ.get(token_key)
        if not all([client_id, client_secret, refresh_token]):
            continue
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=YOUTUBE_SCOPES,
        )
        creds.refresh(google.auth.transport.requests.Request())
        print(f"  認証OK ({id_key})")
        return build("youtube", "v3", credentials=creds)
    print("ERROR: YouTube認証情報が見つかりません。")
    print("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN を設定してください。")
    sys.exit(1)


def build_metadata(quiz_data: dict) -> tuple[str, str, list[str]]:
    import random
    title = random.choice(TITLE_OPTIONS)

    # 正解馬名をタグ・説明に使う
    horse_names: list[str] = []
    for part in quiz_data.get("parts", []):
        for q in part.get("questions", []):
            horse_names.append(q["choices"][q["correct_index"]])
    for q in quiz_data.get("questions", []):
        horse_names.append(q["choices"][q["correct_index"]])

    horse_tags = horse_names[:12]
    tags = list(dict.fromkeys(BASE_TAGS + horse_tags))[:30]

    horse_list = "　".join(horse_names) if horse_names else "名馬たち"
    description = (
        f"{quiz_data.get('title', '名馬当てクイズ')}\n\n"
        "ヒントだけで名馬を当てるクイズです。\n"
        "初級・中級・上級の全15問に挑戦してみてください！\n\n"
        f"登場する馬: {horse_list}\n\n"
        "#競馬 #競馬クイズ #名馬 #90年代競馬 #keiba"
    )

    return title, description, tags


def upload_video(youtube, title: str, description: str, tags: list[str]) -> str | None:
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": CATEGORY_ID,
            "defaultLanguage": "ja",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        str(VIDEO_PATH),
        mimetype="video/mp4",
        resumable=True,
        chunksize=5 * 1024 * 1024,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  進捗: {int(status.progress() * 100)}%")
    video_id = response.get("id", "")
    print(f"アップロード完了!")
    print(f"  動画URL : https://www.youtube.com/watch?v={video_id}")
    return video_id


def upload_thumbnail(youtube, video_id: str) -> None:
    thumb = find_thumbnail()
    if not thumb:
        print("  サムネイル画像なし → スキップ")
        return
    mimetype = "image/png" if thumb.suffix.lower() == ".png" else "image/jpeg"
    media = MediaFileUpload(str(thumb), mimetype=mimetype)
    try:
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        print(f"  サムネイル設定完了: {thumb}")
    except HttpError as e:
        print(f"  WARNING: サムネイル設定失敗: {e}")


def main() -> None:
    if not VIDEO_PATH.exists():
        print(f"ERROR: {VIDEO_PATH} が見つかりません。")
        sys.exit(1)

    size_mb = VIDEO_PATH.stat().st_size / 1024 / 1024
    print(f"=== クイズ動画 YouTube アップロード ===")
    print(f"動画: {VIDEO_PATH} ({size_mb:.1f} MB)")

    quiz_data = json.loads(QUIZ_JSON.read_text(encoding="utf-8")) if QUIZ_JSON.exists() else {}
    title, description, tags = build_metadata(quiz_data)

    print(f"タイトル: {title}")
    print(f"タグ ({len(tags)}件): {', '.join(tags[:6])} ...")

    youtube = get_youtube_client()
    video_id = upload_video(youtube, title, description, tags)
    if video_id:
        upload_thumbnail(youtube, video_id)
        Path("uploaded_quiz_video_id.txt").write_text(video_id)


if __name__ == "__main__":
    main()
