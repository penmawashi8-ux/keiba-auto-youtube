#!/usr/bin/env python3
"""任意の単体動画を YouTube にアップロードする汎用スクリプト。

タイトル・説明・タグ・サムネイル・公開設定をコマンドライン引数で指定する。
認証は既存パイプラインと同じ OAuth2 refresh_token 方式（複数GCPプロジェクト対応）。

使い方:
  python scripts/upload_custom_youtube.py \
      --file dist/nankan/nankan_class_short.mp4 \
      --title "..." --description "..." \
      --tags 競馬,地方競馬,南関東 \
      --thumbnail dist/nankan/short_thumb.jpg \
      --privacy public
"""

import argparse
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

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
CATEGORY_ID = "17"  # スポーツ

# クォータ超過時に順番に切り替える複数GCPプロジェクトの認証情報
CREDENTIAL_SETS = [
    ("GOOGLE_CLIENT_ID",   "GOOGLE_CLIENT_SECRET",   "GOOGLE_REFRESH_TOKEN"),
    ("GOOGLE_CLIENT_ID_2", "GOOGLE_CLIENT_SECRET_2", "GOOGLE_REFRESH_TOKEN_2"),
    ("GOOGLE_CLIENT_ID_3", "GOOGLE_CLIENT_SECRET_3", "GOOGLE_REFRESH_TOKEN_3"),
]
QUOTA_REASONS = {"quotaExceeded", "userRateLimitExceeded", "dailyLimitExceeded"}


def load_credentials_for(id_key, secret_key, token_key):
    cid = os.environ.get(id_key)
    secret = os.environ.get(secret_key)
    token = os.environ.get(token_key)
    if not all([cid, secret, token]):
        return None
    creds = Credentials(
        token=None, refresh_token=token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cid, client_secret=secret, scopes=YOUTUBE_SCOPES,
    )
    try:
        creds.refresh(google.auth.transport.requests.Request())
        print(f"OAuth2リフレッシュ成功 ({id_key})")
        return creds
    except Exception as e:
        print(f"[警告] リフレッシュ失敗 ({id_key}): {e}", file=sys.stderr)
        return None


def load_all_credentials():
    out = []
    for id_key, secret_key, token_key in CREDENTIAL_SETS:
        c = load_credentials_for(id_key, secret_key, token_key)
        if c:
            out.append(c)
    if not out:
        print("[エラー] 有効な認証情報がありません（GitHub Secrets未設定）。", file=sys.stderr)
        sys.exit(1)
    print(f"認証情報: {len(out)} プロジェクト分ロード")
    return out


def _reasons(e: HttpError) -> set:
    try:
        c = json.loads(e.content.decode("utf-8"))
        return {x.get("reason", "") for x in c.get("error", {}).get("errors", [])}
    except Exception:
        return set()


def build_tags(tags, limit=480):
    out, total = [], 0
    for t in tags:
        t = t.strip()
        if not t:
            continue
        if total + len(t) + 1 <= limit:
            out.append(t)
            total += len(t) + 1
    return out


def upload_video(youtube, args) -> str | None:
    body = {
        "snippet": {
            "title": args.title[:100],
            "description": args.description,
            "tags": build_tags(args.tags.split(",")),
            "categoryId": CATEGORY_ID,
            "defaultLanguage": "ja",
            "defaultAudioLanguage": "ja",
        },
        "status": {
            "privacyStatus": args.privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(args.file, mimetype="video/mp4",
                            resumable=True, chunksize=1024 * 1024)
    print(f"アップロード中: {body['snippet']['title']}  ({args.privacy})")
    try:
        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                print(f"  進捗: {int(status.progress()*100)}%")
        vid = resp["id"]
        print(f"完了! Video ID: {vid}")
        print(f"URL: https://www.youtube.com/watch?v={vid}")
        return vid
    except HttpError as e:
        print(f"[エラー] HTTP {e.resp.status}: {e.content[:300]}", file=sys.stderr)
        if _reasons(e) & QUOTA_REASONS:
            return None  # 次プロジェクトへ
        raise


def upload_thumbnail(youtube, video_id, thumb_path):
    if not thumb_path or not Path(thumb_path).exists():
        print(f"  [情報] サムネイルなし: {thumb_path}")
        return
    media = MediaIoBaseUpload(io.BytesIO(Path(thumb_path).read_bytes()),
                              mimetype="image/jpeg", resumable=False)
    try:
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        print(f"  サムネイル設定完了: {video_id}")
    except HttpError as e:
        print(f"  [警告] サムネイル設定失敗 HTTP {e.resp.status}: {e.content[:200]}",
              file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--tags", default="競馬,地方競馬,南関東")
    ap.add_argument("--thumbnail", default="")
    ap.add_argument("--privacy", default="public",
                    choices=["public", "unlisted", "private"])
    ap.add_argument("--url-out", default="",
                    help="アップロード成功時に動画URLを書き出すファイル")
    args = ap.parse_args()

    if not Path(args.file).exists():
        print(f"[エラー] 動画が見つかりません: {args.file}", file=sys.stderr)
        sys.exit(1)

    all_creds = load_all_credentials()
    last_err = None
    for i, creds in enumerate(all_creds):
        youtube = build("youtube", "v3", credentials=creds)
        try:
            vid = upload_video(youtube, args)
        except Exception as e:
            last_err = e
            print(f"[警告] プロジェクト{i+1}でアップロード失敗: {e}", file=sys.stderr)
            continue
        if vid is None:
            print(f"[情報] プロジェクト{i+1} クォータ超過 → 次へ", file=sys.stderr)
            continue
        upload_thumbnail(youtube, vid, args.thumbnail)
        url = f"https://youtu.be/{vid}"
        if args.url_out:
            Path(args.url_out).write_text(url, encoding="utf-8")
        print(f"::notice::Uploaded {args.file} -> {url}")
        return
    print(f"[エラー] 全プロジェクトでアップロード失敗。last_err={last_err}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
