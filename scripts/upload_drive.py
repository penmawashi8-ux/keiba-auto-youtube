#!/usr/bin/env python3
"""Google Drive APIを使ってoutput/video_*.mp4をアップロードする（テスト用）。
- OAuth2（refresh_token方式）で認証
- アップロード後、「リンクを知っている全員が閲覧可能」に設定
- 共有リンクをコンソールに出力する
"""

import io
import json
import os
import sys
from pathlib import Path

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

sys.path.insert(0, str(Path(__file__).parent))
from upload_youtube import generate_thumbnail

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
POSTED_IDS_FILE = "posted_ids.txt"

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def load_credentials() -> Credentials:
    """環境変数からOAuth2認証情報を構築する。"""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")

    missing = [
        name for name, val in [
            ("GOOGLE_CLIENT_ID", client_id),
            ("GOOGLE_CLIENT_SECRET", client_secret),
            ("GOOGLE_REFRESH_TOKEN", refresh_token),
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
        scopes=DRIVE_SCOPES,
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


def upload_to_drive(drive, title: str, video_path: str) -> str:
    """Google Drive に動画をアップロードして共有リンクを返す。"""
    file_metadata = {
        "name": f"【競馬速報】{title}.mp4",
        "mimeType": "video/mp4",
    }
    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024,
    )

    print(f"Google Drive にアップロード中: {file_metadata['name']}")
    try:
        request = drive.files().create(
            body=file_metadata,
            media_body=media,
            fields="id",
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                print(f"  アップロード進捗: {progress}%")

        file_id = response["id"]
        print(f"アップロード完了！ File ID: {file_id}")

        # 「リンクを知っている全員が閲覧可能」に設定
        drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        share_link = f"https://drive.google.com/file/d/{file_id}/view"
        return share_link

    except HttpError as e:
        try:
            error_content = json.loads(e.content.decode("utf-8"))
        except Exception:
            error_content = {}
        print(f"[エラー] Drive API HTTP {e.resp.status}: {error_content}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[エラー] アップロード失敗: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    print("=== Google Drive アップロード開始 ===")

    if not Path(NEWS_JSON).exists():
        print(f"[エラー] {NEWS_JSON} が見つかりません。", file=sys.stderr)
        sys.exit(1)

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のためアップロードをスキップします。")
        sys.exit(0)

    # video_[数字].mp4 のみ対象（一時ファイルを除外）
    video_files = sorted(
        f for f in Path(OUTPUT_DIR).glob("video_*.mp4")
        if f.stem.split("_")[1].isdigit()
    )
    if not video_files:
        print(f"[エラー] {OUTPUT_DIR}/video_*.mp4 が見つかりません。", file=sys.stderr)
        sys.exit(1)

    creds = load_credentials()
    drive = build("drive", "v3", credentials=creds)

    uploaded_links: list[str] = []
    for video_file in video_files:
        idx = int(video_file.stem.split("_")[1])

        if idx >= len(news_items):
            print(f"  [警告] インデックス {idx} の記事がありません。スキップします。")
            continue

        item = news_items[idx]
        title = item["title"]

        print(f"\n--- アップロード [{idx}]: {title[:50]} ---")
        share_link = upload_to_drive(drive, title, str(video_file))
        uploaded_links.append(share_link)
        print(f"動画リンク: {share_link}")

        # サムネイル生成・Drive アップロード
        print("  サムネイル生成中...")
        try:
            thumb_bytes = generate_thumbnail(title, idx)
            thumb_metadata = {
                "name": f"【競馬速報】{title[:40]}_サムネイル.jpg",
                "mimeType": "image/jpeg",
            }
            thumb_media = MediaIoBaseUpload(io.BytesIO(thumb_bytes), mimetype="image/jpeg")
            thumb_resp = drive.files().create(
                body=thumb_metadata, media_body=thumb_media, fields="id"
            ).execute()
            drive.permissions().create(
                fileId=thumb_resp["id"],
                body={"type": "anyone", "role": "reader"},
            ).execute()
            thumb_link = f"https://drive.google.com/file/d/{thumb_resp['id']}/view"
            print(f"  サムネイルリンク: {thumb_link}")
        except Exception as e:
            print(f"  [警告] サムネイルアップロード失敗: {e}", file=sys.stderr)

    update_posted_ids(news_items)

    print(f"\n=== アップロード完了: {len(uploaded_links)} 本 ===")
    for link in uploaded_links:
        print(f"  {link}")


if __name__ == "__main__":
    main()
