#!/usr/bin/env python3
"""YouTube Studio 内部 API でサムネイルを動画先頭フレームに設定する。

Playwright もクッキーも不要。OAuth2 リフレッシュトークンのみで動作する。
スチル選択（動画フレームから選ぶ）方式のため、チャンネル登録者数に関係なく
全チャンネルで利用可能（カスタム画像アップロードの 1,000 人要件は不要）。

必要な環境変数:
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN
"""

import datetime
import json
import os
import sys
from pathlib import Path

import google.auth.transport.requests
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

UPLOAD_RESULTS_JSON = "output/upload_results.json"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# YouTube Studio の JS に埋め込まれている公開 API キー
_STUDIO_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_STUDIO_BASE = "https://studio.youtube.com/youtubei/v1"

# upload_youtube.py と同じ複数プロジェクト構成（トークン期限切れ時のフォールバック用）
_CREDENTIAL_SETS = [
    ("GOOGLE_CLIENT_ID",   "GOOGLE_CLIENT_SECRET",   "GOOGLE_REFRESH_TOKEN"),
    ("GOOGLE_CLIENT_ID_2", "GOOGLE_CLIENT_SECRET_2", "GOOGLE_REFRESH_TOKEN_2"),
    ("GOOGLE_CLIENT_ID_3", "GOOGLE_CLIENT_SECRET_3", "GOOGLE_REFRESH_TOKEN_3"),
]


def _get_credentials() -> Credentials:
    """有効な OAuth2 認証情報を返す。プロジェクト1が失敗した場合は2→3と試みる。"""
    for id_key, secret_key, token_key in _CREDENTIAL_SETS:
        client_id = os.environ.get(id_key)
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
        try:
            creds.refresh(google.auth.transport.requests.Request())
            print(f"OAuth2 トークン取得成功 ({id_key})")
            return creds
        except Exception as e:
            print(f"[警告] トークンリフレッシュ失敗 ({id_key}): {e}", file=sys.stderr)
            continue

    print("[エラー] 有効な OAuth2 認証情報が見つかりませんでした", file=sys.stderr)
    sys.exit(1)


def _get_channel_id(creds: Credentials) -> str:
    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.channels().list(part="id", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("チャンネルが見つかりません")
    return items[0]["id"]


def _studio_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Goog-AuthUser": "0",
        "Origin": "https://studio.youtube.com",
        "Referer": "https://studio.youtube.com/",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }


def _build_context(channel_id: str) -> dict:
    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    return {
        "client": {
            "clientName": "YOUTUBE_STUDIO",
            "clientVersion": f"1.{today}.01.00",
            "hl": "ja",
            "gl": "JP",
        },
        "user": {
            "delegationContext": {
                "externalChannelId": channel_id,
                "roleType": {
                    "channelRoleType": "CREATOR_CHANNEL_ROLE_TYPE_OWNER"
                },
            }
        },
    }


def set_thumbnail_by_timestamp(
    access_token: str,
    channel_id: str,
    video_id: str,
    time_ms: int = 500,
) -> tuple[bool, str]:
    """動画の指定タイムスタンプのフレームをサムネイルに設定する。

    複数の API リクエスト形式を順番に試す（内部 API は公式ドキュメントがないため）。
    成功したかどうかと、レスポンスの概要を返す。
    """
    headers = _studio_headers(access_token)
    context = _build_context(channel_id)
    params = {"alt": "json", "key": _STUDIO_KEY}

    # --- 試行 1: thumbnailDetails.stillImageTime ---
    payload_v1 = {
        "context": context,
        "encryptedVideoId": video_id,
        "videoReadMask": {"videoId": True, "thumbnailDetails": True},
        "videoMetadata": {
            "thumbnailDetails": {
                "stillImageTime": time_ms,
            }
        },
    }
    resp = requests.post(
        f"{_STUDIO_BASE}/video_manager/metadata_update",
        params=params,
        headers=headers,
        json=payload_v1,
        timeout=30,
    )
    summary = f"HTTP {resp.status_code}"
    if resp.status_code == 200:
        return True, f"{summary} (形式1: thumbnailDetails.stillImageTime)"

    body_v1 = resp.text[:800]
    print(f"  [試行1] {summary}: {body_v1}", file=sys.stderr)

    # --- 試行 2: thumbnail.videoStill ---
    payload_v2 = {
        "context": context,
        "encryptedVideoId": video_id,
        "videoReadMask": {"videoId": True, "thumbnail": True},
        "updatedMetadata": {
            "thumbnail": {
                "videoStill": {
                    "operation": "SET_TIME",
                    "timeMs": time_ms,
                }
            }
        },
    }
    resp = requests.post(
        f"{_STUDIO_BASE}/video_manager/metadata_update",
        params=params,
        headers=headers,
        json=payload_v2,
        timeout=30,
    )
    summary = f"HTTP {resp.status_code}"
    if resp.status_code == 200:
        return True, f"{summary} (形式2: thumbnail.videoStill)"

    body_v2 = resp.text[:800]
    print(f"  [試行2] {summary}: {body_v2}", file=sys.stderr)

    # --- 試行 3: エンドポイントを変えて試す ---
    payload_v3 = {
        "context": context,
        "videoId": video_id,
        "thumbnailTimestamp": {"timeMs": time_ms},
    }
    resp = requests.post(
        f"{_STUDIO_BASE}/video_manager/update_video_thumbnail",
        params=params,
        headers=headers,
        json=payload_v3,
        timeout=30,
    )
    summary = f"HTTP {resp.status_code}"
    if resp.status_code == 200:
        return True, f"{summary} (形式3: update_video_thumbnail)"

    body_v3 = resp.text[:800]
    print(f"  [試行3] {summary}: {body_v3}", file=sys.stderr)

    # --- 試行 4: defaultThumbnail.timeMs 形式 ---
    payload_v4 = {
        "context": context,
        "encryptedVideoId": video_id,
        "videoReadMask": {"videoId": True, "thumbnailDetails": True},
        "videoMetadata": {
            "thumbnailDetails": {
                "defaultThumbnail": {"timeMs": str(time_ms)},
            }
        },
    }
    resp = requests.post(
        f"{_STUDIO_BASE}/video_manager/metadata_update",
        params=params,
        headers=headers,
        json=payload_v4,
        timeout=30,
    )
    summary = f"HTTP {resp.status_code}"
    if resp.status_code == 200:
        return True, f"{summary} (形式4: defaultThumbnail.timeMs)"

    body_v4 = resp.text[:800]
    print(f"  [試行4] {summary}: {body_v4}", file=sys.stderr)

    # すべて失敗 → ログを保存して原因調査に役立てる
    _save_debug_log(video_id, {
        "trial1": {"status": resp.status_code, "body": body_v1},
        "trial2": {"status": resp.status_code, "body": body_v2},
        "trial3": {"status": resp.status_code, "body": body_v3},
        "trial4": {"status": resp.status_code, "body": body_v4},
    })

    return False, f"全試行失敗（output/debug_studio_api_{video_id}.json を確認してください）"


def _save_debug_log(video_id: str, data: dict) -> None:
    Path("output").mkdir(exist_ok=True)
    path = Path(f"output/debug_studio_api_{video_id}.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [デバッグ] レスポンスを保存: {path}", file=sys.stderr)


def main() -> None:
    print("=== YouTube サムネイル設定 (Studio 内部 API) ===")

    if not Path(UPLOAD_RESULTS_JSON).exists():
        print(f"[警告] {UPLOAD_RESULTS_JSON} が見つかりません。スキップします。")
        sys.exit(0)

    results = json.loads(Path(UPLOAD_RESULTS_JSON).read_text(encoding="utf-8"))
    targets = [r for r in results if r.get("video_id")]
    if not targets:
        print("video_id が見つかりません。スキップします。")
        sys.exit(0)

    print(f"サムネイル設定対象: {len(targets)} 件")

    creds = _get_credentials()
    print("OAuth2 トークン取得成功")

    channel_id = _get_channel_id(creds)
    print(f"チャンネル ID: {channel_id}")

    success_count = 0
    for entry in targets:
        video_id = entry["video_id"]
        title = entry.get("title", "")[:50]

        print(f"\n--- {video_id} / {title} ---")
        ok, msg = set_thumbnail_by_timestamp(
            access_token=creds.token,
            channel_id=channel_id,
            video_id=video_id,
            time_ms=500,
        )

        if ok:
            success_count += 1
            print(f"  ✅ 完了: {msg}")
        else:
            print(f"  ❌ 失敗: {msg}", file=sys.stderr)

    print(f"\n=== 完了: {success_count}/{len(targets)} 件 ===")
    if success_count < len(targets):
        sys.exit(1)


if __name__ == "__main__":
    main()
