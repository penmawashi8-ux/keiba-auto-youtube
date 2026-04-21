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
import time
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
            "clientName": "WEB_CREATOR",
            "clientVersion": f"1.{today}.01.00",
            "hl": "ja",
            "gl": "JP",
        },
        "user": {
            "onBehalfOfUser": channel_id,
        },
    }


def _build_context_delegation(channel_id: str) -> dict:
    """delegationContext 形式（旧フォーマット、フォールバック用）。"""
    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    return {
        "client": {
            "clientName": "WEB_CREATOR",
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


def _post(url, params, headers, payload) -> tuple[int, str]:
    resp = requests.post(url, params=params, headers=headers, json=payload, timeout=30)
    return resp.status_code, resp.text[:800]


def _try_all_formats(headers, ctx, ctx_d, params, video_id, time_ms) -> tuple[bool, str, dict]:
    """5種のフォーマットを試す。成功フラグ・メッセージ・ボディ辞書を返す。"""
    url = f"{_STUDIO_BASE}/video_manager/metadata_update"
    bodies = {}

    trials = [
        ("1", {"context": ctx,   "encryptedVideoId": video_id, "videoMetadata": {"thumbnailDetails": {"stillImageTime": time_ms}}}),
        ("2", {"context": ctx,   "encryptedVideoId": video_id, "videoMetadata": {"thumbnail": {"stillImageTime": time_ms}}}),
        ("3", {"context": ctx,   "encryptedVideoId": video_id, "updatedMetadata": {"thumbnail": {"videoStill": {"operation": "SET_TIME", "timeMs": time_ms}}}}),
        ("4", {"context": ctx_d, "encryptedVideoId": video_id, "videoMetadata": {"thumbnailDetails": {"stillImageTime": time_ms}}}),
        ("5", {"context": ctx,   "videoId": video_id,          "videoMetadata": {"thumbnailDetails": {"stillImageTime": time_ms}}}),
    ]
    for label, payload in trials:
        sc, body = _post(url, params, headers, payload)
        bodies[label] = body
        if sc == 200:
            return True, f"HTTP {sc} (形式{label})", bodies
        print(f"  [試行{label}] HTTP {sc}: {body}", file=sys.stderr)

    return False, "", bodies


def set_thumbnail_by_timestamp(
    access_token: str,
    channel_id: str,
    video_id: str,
    time_ms: int = 500,
    max_retries: int = 3,
    retry_wait: int = 90,
) -> tuple[bool, str]:
    """動画の指定タイムスタンプのフレームをサムネイルに設定する。

    HTTP 500（動画処理中）の場合は retry_wait 秒待機してリトライする。
    """
    headers = _studio_headers(access_token)
    ctx = _build_context(channel_id)
    ctx_d = _build_context_delegation(channel_id)
    params = {"alt": "json", "key": _STUDIO_KEY}

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            print(f"  [{attempt}回目] {retry_wait}秒待機後にリトライ...")
            time.sleep(retry_wait)

        ok, msg, bodies = _try_all_formats(headers, ctx, ctx_d, params, video_id, time_ms)
        if ok:
            return True, msg

        # 全試行が 500 なら動画処理待ちの可能性 → リトライ
        all_500 = all("500" in b or '"INTERNAL"' in b for b in bodies.values())
        if not all_500 or attempt == max_retries:
            break
        print(f"  全試行 HTTP 500（動画処理中の可能性）。リトライします ({attempt}/{max_retries})")

    _save_debug_log(video_id, bodies)
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
