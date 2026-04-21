#!/usr/bin/env python3
"""YouTube サムネイルを自動設定する（2段階フォールバック）。

手法1: thumbnails().set() 公式API（画像アップロード）
  → チャンネル認証済みかつ要件を満たす場合に成功

手法2: YouTube Studio 内部API（スチル選択）
  → 公式APIが channelNotEligible で失敗した場合のフォールバック

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
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

UPLOAD_RESULTS_JSON = "output/upload_results.json"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

_STUDIO_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_STUDIO_BASE = "https://studio.youtube.com/youtubei/v1"

_CREDENTIAL_SETS = [
    ("GOOGLE_CLIENT_ID",   "GOOGLE_CLIENT_SECRET",   "GOOGLE_REFRESH_TOKEN"),
    ("GOOGLE_CLIENT_ID_2", "GOOGLE_CLIENT_SECRET_2", "GOOGLE_REFRESH_TOKEN_2"),
    ("GOOGLE_CLIENT_ID_3", "GOOGLE_CLIENT_SECRET_3", "GOOGLE_REFRESH_TOKEN_3"),
]


def _get_credentials() -> Credentials:
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

    print("[エラー] 有効な OAuth2 認証情報が見つかりませんでした", file=sys.stderr)
    sys.exit(1)


def _get_channel_id(creds: Credentials) -> str:
    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.channels().list(part="id", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("チャンネルが見つかりません")
    return items[0]["id"]


# ---------------------------------------------------------------------------
# 手法1: 公式 thumbnails().set() API
# ---------------------------------------------------------------------------

def try_official_thumbnail_set(
    creds: Credentials,
    video_id: str,
    thumb_path: str,
) -> tuple[bool, str]:
    """公式 API で画像をサムネイルとしてアップロードする。"""
    if not Path(thumb_path).exists():
        return False, f"thumbnail file not found: {thumb_path}"

    youtube = build("youtube", "v3", credentials=creds)
    media = MediaFileUpload(thumb_path, mimetype="image/jpeg", resumable=False)
    try:
        resp = youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        print(f"  [手法1] レスポンス: {json.dumps(resp, ensure_ascii=False)[:300]}")
        return True, "thumbnails().set() 成功"
    except HttpError as e:
        body = e.content.decode("utf-8", errors="replace")
        try:
            reason = json.loads(body)["error"]["errors"][0].get("reason", "")
        except Exception:
            reason = ""
        msg = f"HTTP {e.resp.status} reason={reason}: {body[:300]}"
        return False, msg
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# 手法2: YouTube Studio 内部 API（スチル選択）
# ---------------------------------------------------------------------------

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
        "client": {"clientName": "WEB_CREATOR", "clientVersion": f"1.{today}.01.00", "hl": "ja", "gl": "JP"},
        "user": {"onBehalfOfUser": channel_id},
    }


def _build_context_delegation(channel_id: str) -> dict:
    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    return {
        "client": {"clientName": "WEB_CREATOR", "clientVersion": f"1.{today}.01.00", "hl": "ja", "gl": "JP"},
        "user": {"delegationContext": {
            "externalChannelId": channel_id,
            "roleType": {"channelRoleType": "CREATOR_CHANNEL_ROLE_TYPE_OWNER"},
        }},
    }


def _post(url, params, headers, payload) -> tuple[int, str]:
    resp = requests.post(url, params=params, headers=headers, json=payload, timeout=30)
    return resp.status_code, resp.text[:800]


def _try_studio_formats(access_token, channel_id, video_id, time_ms) -> tuple[bool, str, dict]:
    headers = _studio_headers(access_token)
    ctx = _build_context(channel_id)
    ctx_d = _build_context_delegation(channel_id)
    params = {"alt": "json", "key": _STUDIO_KEY}
    url = f"{_STUDIO_BASE}/video_manager/metadata_update"
    bodies = {}

    trials = [
        ("1-obu-thumbnailDetails", ctx,   {"encryptedVideoId": video_id, "videoMetadata": {"thumbnailDetails": {"stillImageTime": time_ms}}}),
        ("2-obu-thumbnail",        ctx,   {"encryptedVideoId": video_id, "videoMetadata": {"thumbnail": {"stillImageTime": time_ms}}}),
        ("3-obu-videoStill",       ctx,   {"encryptedVideoId": video_id, "updatedMetadata": {"thumbnail": {"videoStill": {"operation": "SET_TIME", "timeMs": time_ms}}}}),
        ("4-obu-videoId",          ctx,   {"videoId": video_id,          "videoMetadata": {"thumbnailDetails": {"stillImageTime": time_ms}}}),
        ("5-del-thumbnailDetails", ctx_d, {"encryptedVideoId": video_id, "videoMetadata": {"thumbnailDetails": {"stillImageTime": time_ms}}}),
    ]
    for label, context, extra in trials:
        payload = {"context": context, **extra}
        sc, body = _post(url, params, headers, payload)
        bodies[label] = {"status": sc, "body": body}
        if sc == 200:
            return True, f"Studio API HTTP {sc} (形式{label})", bodies
        print(f"  [Studio {label}] HTTP {sc}: {body[:200]}", file=sys.stderr)

    return False, "", bodies


def try_studio_api(
    creds: Credentials,
    channel_id: str,
    video_id: str,
    time_ms: int = 500,
    max_retries: int = 3,
    retry_wait: int = 90,
) -> tuple[bool, str, dict]:
    """Studio 内部 API でスチル選択。HTTP 500 の場合はリトライ。"""
    all_bodies = {}
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            print(f"  [Studio リトライ {attempt}/{max_retries}] {retry_wait}秒待機...")
            time.sleep(retry_wait)

        ok, msg, bodies = _try_studio_formats(creds.token, channel_id, video_id, time_ms)
        all_bodies.update(bodies)
        if ok:
            return True, msg, all_bodies

        all_500 = all(b.get("status") == 500 for b in bodies.values())
        if not all_500 or attempt == max_retries:
            break
        print(f"  全試行 HTTP 500。リトライします ({attempt}/{max_retries})")

    return False, "Studio API 全試行失敗", all_bodies


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def set_thumbnail(
    creds: Credentials,
    channel_id: str,
    video_id: str,
    thumb_path: str,
) -> bool:
    """手法1→手法2の順で試す。"""
    # 手法1: 公式 API
    print("  [手法1] thumbnails().set() を試みます...")
    ok, msg = try_official_thumbnail_set(creds, video_id, thumb_path)
    if ok:
        print(f"  ✅ 手法1 成功: {msg}")
        return True
    print(f"  [手法1] 失敗: {msg}", file=sys.stderr)

    # channelNotEligible / forbidden 以外のエラーでも手法2を試す
    # 手法2: Studio 内部 API
    print("  [手法2] Studio 内部 API を試みます...")
    ok, msg, bodies = try_studio_api(creds, channel_id, video_id)
    if ok:
        print(f"  ✅ 手法2 成功: {msg}")
        return True

    print(f"  [手法2] 失敗: {msg}", file=sys.stderr)
    _save_debug_log(video_id, {
        "method1": {"result": "failed", "msg": msg},
        "method2": bodies,
    })
    return False


def _save_debug_log(video_id: str, data: dict) -> None:
    Path("output").mkdir(exist_ok=True)
    path = Path(f"output/debug_thumbnail_{video_id}.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [デバッグ] ログ保存: {path}", file=sys.stderr)


def main() -> None:
    print("=== YouTube サムネイル自動設定 ===")

    if not Path(UPLOAD_RESULTS_JSON).exists():
        print(f"[警告] {UPLOAD_RESULTS_JSON} が見つかりません。スキップします。")
        sys.exit(0)

    results = json.loads(Path(UPLOAD_RESULTS_JSON).read_text(encoding="utf-8"))
    targets = [r for r in results if r.get("video_id")]
    if not targets:
        print("video_id が見つかりません。スキップします。")
        sys.exit(0)

    print(f"対象: {len(targets)} 件")

    creds = _get_credentials()
    channel_id = _get_channel_id(creds)
    print(f"チャンネル ID: {channel_id}")

    success_count = 0
    for entry in targets:
        video_id = entry["video_id"]
        thumb_path = entry.get("thumbnail", "")
        title = entry.get("title", "")[:50]
        print(f"\n--- {video_id} / {title} ---")

        if set_thumbnail(creds, channel_id, video_id, thumb_path):
            success_count += 1
        else:
            print(f"  ❌ 失敗: {video_id}", file=sys.stderr)

    print(f"\n=== 完了: {success_count}/{len(targets)} 件 ===")
    if success_count < len(targets):
        sys.exit(1)


if __name__ == "__main__":
    main()
