#!/usr/bin/env python3
"""名馬シリーズ用 YouTube アップロードスクリプト

ニュース速報との違い:
  - タイトル形式: 【名馬列伝】{馬名}〜{キャッチフレーズ}〜 #Shorts
  - タグ: 名馬・競馬歴史系
  - サムネイル: ffmpegで生成したフレーム画像を使用（Pillow不使用）
  - posted_ids.txt は使用しない（名馬動画は1本ずつ手動投稿）
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

OUTPUT_DIR = "output"
DATA_DIR   = "data/famous_horses"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
CATEGORY_ID = "17"  # スポーツ
BASE_TAGS   = ["競馬", "名馬", "競馬歴史", "名馬列伝", "Shorts", "keiba", "競走馬"]

# 名馬列伝専用チャンネルを優先。未設定の場合はニュース系チャンネルにフォールバック。
CREDENTIAL_SETS = [
    ("FAMOUS_HORSE_CLIENT_ID",  "FAMOUS_HORSE_CLIENT_SECRET",  "FAMOUS_HORSE_REFRESH_TOKEN"),
    ("GOOGLE_CLIENT_ID",        "GOOGLE_CLIENT_SECRET",        "GOOGLE_REFRESH_TOKEN"),
    ("GOOGLE_CLIENT_ID_2",      "GOOGLE_CLIENT_SECRET_2",      "GOOGLE_REFRESH_TOKEN_2"),
    ("GOOGLE_CLIENT_ID_3",      "GOOGLE_CLIENT_SECRET_3",      "GOOGLE_REFRESH_TOKEN_3"),
]

QUOTA_EXCEEDED_REASONS  = {"quotaExceeded", "userRateLimitExceeded", "dailyLimitExceeded"}
CHANNEL_LIMIT_REASONS   = {"uploadLimitExceeded"}


# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------

def load_credentials(id_key: str, secret_key: str, token_key: str) -> Credentials | None:
    client_id     = os.environ.get(id_key)
    client_secret = os.environ.get(secret_key)
    refresh_token = os.environ.get(token_key)
    if not all([client_id, client_secret, refresh_token]):
        return None
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=YOUTUBE_SCOPES,
    )
    try:
        creds.refresh(google.auth.transport.requests.Request())
        print(f"  OAuth2 リフレッシュ成功 ({id_key})")
        return creds
    except Exception as e:
        print(f"  [警告] トークンリフレッシュ失敗 ({id_key}): {e}", file=sys.stderr)
        return None


def load_all_credentials() -> list[Credentials]:
    result = []
    for id_key, secret_key, token_key in CREDENTIAL_SETS:
        creds = load_credentials(id_key, secret_key, token_key)
        if creds:
            result.append(creds)
    if not result:
        print("[エラー] 有効な認証情報が1つもありません。", file=sys.stderr)
        sys.exit(1)
    print(f"  認証情報: {len(result)} プロジェクト分ロード完了")
    return result


# ---------------------------------------------------------------------------
# メタデータ読み込み
# ---------------------------------------------------------------------------

def load_horse_meta(horse_key: str) -> dict:
    """data/famous_horses/<horse_key>.json からメタデータを読む。"""
    meta_path = Path(f"{DATA_DIR}/{horse_key}.json")
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    # フォールバック: horse_keyをそのまま使用
    return {"name": horse_key, "catchphrase": "", "era": "", "tags_extra": []}


def build_title(horse_name: str, catchphrase: str) -> str:
    """YouTube タイトルを構築する（上限100文字）。"""
    if catchphrase:
        title = f"【名馬列伝】{horse_name}〜{catchphrase}〜 #Shorts"
    else:
        title = f"【名馬列伝】{horse_name} #Shorts"
    return title[:100]


def build_description(horse_name: str, catchphrase: str, era: str) -> str:
    lines = [
        f"【名馬列伝シリーズ】",
        f"今回の主役は {horse_name}。",
    ]
    if catchphrase:
        lines.append(f"「{catchphrase}」")
    if era:
        lines.append(f"活躍時期: {era}")
    lines += [
        "",
        "競馬の歴史を彩った名馬たちを紹介するシリーズです。",
        "チャンネル登録して次の名馬もお楽しみに！",
        "",
        "#名馬列伝 #競馬 #競走馬 #名馬 #Shorts",
    ]
    # Wikimedia Commons 引用元（存在する場合のみ追記）
    attr_path = Path("assets/attribution.json")
    if attr_path.exists():
        try:
            attr = json.loads(attr_path.read_text(encoding="utf-8"))
            lines += [
                "",
                "【画像クレジット】",
                f"出典: {attr.get('source', 'Wikimedia Commons')}",
                f"著作者: {attr.get('author', '')}",
                f"ライセンス: {attr.get('license', '')}",
                f"URL: {attr.get('url', '')}",
            ]
        except Exception:
            pass
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# アップロード
# ---------------------------------------------------------------------------

def _get_error_reasons(err: HttpError) -> set[str]:
    try:
        content = json.loads(err.content.decode("utf-8"))
        return {e.get("reason", "") for e in content.get("error", {}).get("errors", [])}
    except Exception:
        return set()


def upload_video(youtube, video_path: str, title: str, description: str, tags: list[str]) -> str | None:
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": CATEGORY_ID,
            "defaultLanguage": "ja",
            "defaultAudioLanguage": "ja",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=1024 * 1024)
    print(f"  アップロード中: {title}")
    try:
        request  = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"  進捗: {int(status.progress() * 100)}%")
        video_id = response["id"]
        print(f"  完了! Video ID: {video_id}")
        print(f"  URL: https://www.youtube.com/watch?v={video_id}")
        return video_id
    except HttpError as e:
        reasons = _get_error_reasons(e)
        if reasons & CHANNEL_LIMIT_REASONS:
            print("[エラー] チャンネルの1日アップロード上限。明日再試行してください。", file=sys.stderr)
            sys.exit(2)
        if reasons & QUOTA_EXCEEDED_REASONS:
            return None  # 次のGCPプロジェクトで再試行
        print(f"[エラー] YouTube API HTTP {e.resp.status}: {e.content.decode()[:500]}", file=sys.stderr)
        raise


def upload_thumbnail(youtube, video_id: str, thumb_path: str) -> None:
    """ffmpegで生成したサムネイル画像をアップロードする。"""
    if not Path(thumb_path).exists():
        print(f"  [警告] サムネイルファイルなし: {thumb_path}", file=sys.stderr)
        return
    size_kb = Path(thumb_path).stat().st_size // 1024
    print(f"  サムネイルアップロード中: {thumb_path} ({size_kb} KB)")
    try:
        with open(thumb_path, "rb") as f:
            data = f.read()
        media = MediaIoBaseUpload(__import__("io").BytesIO(data), mimetype="image/jpeg", resumable=False)
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        print("  サムネイルアップロード完了。")
    except HttpError as e:
        print(f"  [警告] サムネイルアップロード失敗 HTTP {e.resp.status}。"
              "YouTube Studio から手動でアップロードしてください。", file=sys.stderr)
    except Exception as e:
        print(f"  [警告] サムネイルアップロード失敗: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("使用法: python scripts/famous_horse_upload.py <horse_key>", file=sys.stderr)
        print("例:     python scripts/famous_horse_upload.py silport", file=sys.stderr)
        sys.exit(1)

    horse_key  = sys.argv[1]
    video_path = f"{OUTPUT_DIR}/video_0.mp4"
    thumb_path = f"{OUTPUT_DIR}/thumbnail_0.jpg"

    if not Path(video_path).exists():
        print(f"[エラー] 動画ファイルが見つかりません: {video_path}", file=sys.stderr)
        sys.exit(1)

    # 動画の先頭フレーム（名馬列伝デザインのサムネイルフレーム）を抽出
    print("  サムネイル抽出中...")
    result = subprocess.run([
        "ffmpeg", "-y", "-ss", "0.5", "-i", video_path,
        "-vframes", "1", "-s", "1280x720", thumb_path,
    ], capture_output=True, text=True)
    if result.returncode == 0:
        size_kb = Path(thumb_path).stat().st_size // 1024
        print(f"  サムネイル抽出完了: {thumb_path} ({size_kb} KB)")
    else:
        print(f"  [警告] サムネイル抽出失敗: {result.stderr[-200:]}", file=sys.stderr)

    meta        = load_horse_meta(horse_key)
    horse_name  = meta.get("name", horse_key)
    catchphrase = meta.get("catchphrase", "")
    era         = meta.get("era", "")
    extra_tags  = meta.get("tags_extra", [])

    title       = build_title(horse_name, catchphrase)
    description = build_description(horse_name, catchphrase, era)
    tags        = BASE_TAGS + [t for t in extra_tags if t not in BASE_TAGS]

    print("=== 名馬シリーズ YouTube アップロード開始 ===")
    print(f"  馬名: {horse_name} (key={horse_key})")
    print(f"  タイトル: {title}")

    all_creds = load_all_credentials()
    video_id  = None

    for creds in all_creds:
        youtube = build("youtube", "v3", credentials=creds)
        try:
            video_id = upload_video(youtube, video_path, title, description, tags)
            if video_id:
                break
        except HttpError:
            continue

    if not video_id:
        print("[エラー] 全GCPプロジェクトでアップロードが失敗しました。", file=sys.stderr)
        sys.exit(1)

    # サムネイルアップロード（最後に成功したyoutubeクライアントを使用）
    upload_thumbnail(youtube, video_id, thumb_path)

    # 結果をファイルに保存
    result_path = Path(f"{OUTPUT_DIR}/famous_horse_upload_result.txt")
    result_path.write_text(
        f"video_id={video_id}\n"
        f"horse_key={horse_key}\n"
        f"horse_name={horse_name}\n"
        f"title={title}\n"
        f"url=https://www.youtube.com/watch?v={video_id}\n",
        encoding="utf-8",
    )
    print(f"  結果を保存: {result_path}")
    print("=== アップロード完了 ===")


if __name__ == "__main__":
    main()
