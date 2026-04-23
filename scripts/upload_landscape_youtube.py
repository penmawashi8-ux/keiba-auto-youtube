#!/usr/bin/env python3
"""YouTube Data API v3 でOAuth2（refresh_token方式）を使って横向き予想動画をアップロードする。"""

import io
import json
import os
import random
import re
import string
import subprocess
import sys
from pathlib import Path

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
# POSTED_IDS_FILE への追記は行わない（Shortsと投稿管理を分離するため）

# YouTubeタイトルテンプレート（横向き予想動画用・#Shortsなし）
# (prefix, suffix) 形式。実際のタイトルは prefix + article_title + suffix
# {date} は現在日付（JST）「4月30日」形式、{race_name}・{grade} は news.json から取得
_LANDSCAPE_TITLE_TEMPLATES = [
    ("【重賞予想】",           " {date} 徹底分析"),
    ("【競馬予想】",           " {date} 完全版"),
    ("{date}【重賞予想】",     " 徹底解説"),
    ("【{grade}予想】",        " {race_name} {date}"),
    ("{race_name} 予想",       " {date}【完全版】"),
    ("【競馬予想解説】{race_name} ", "{date}"),
    ("{date} {race_name}",     "【予想】完全版"),
]

# YouTube説明文テンプレート（横向き予想動画用）
_LANDSCAPE_DESC_TEMPLATES = [
    "{race_name}（{grade}）の予想解説動画です。",
    "{race_name} {date}の徹底分析です。",
    "今週の重賞{race_name}を徹底解説します。",
]

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # サムネイルアップロードに必要
]
CATEGORY_ID = "17"  # スポーツ
LANDSCAPE_TAGS = ["競馬", "競馬予想", "重賞予想", "keiba", "G1予想", "競馬解説", "馬券", "競馬情報"]

# YouTube API クォータ: 1日10,000ユニット / videos.insert = 1,600ユニット
# GCPプロジェクト切り替えで解決できるAPIクォータ超過
QUOTA_EXCEEDED_REASONS = {"quotaExceeded", "userRateLimitExceeded", "dailyLimitExceeded"}
# チャンネル自体の制限（プロジェクト切り替えでは解決不可）
CHANNEL_LIMIT_REASONS = {"uploadLimitExceeded"}

# 複数GCPプロジェクトの認証情報（クォータ超過時に順番に切り替え）
CREDENTIAL_SETS = [
    ("GOOGLE_CLIENT_ID",   "GOOGLE_CLIENT_SECRET",   "GOOGLE_REFRESH_TOKEN"),
    ("GOOGLE_CLIENT_ID_2", "GOOGLE_CLIENT_SECRET_2", "GOOGLE_REFRESH_TOKEN_2"),
    ("GOOGLE_CLIENT_ID_3", "GOOGLE_CLIENT_SECRET_3", "GOOGLE_REFRESH_TOKEN_3"),
]


def load_credentials_for(id_key: str, secret_key: str, token_key: str) -> Credentials | None:
    """指定した環境変数キーからOAuth2認証情報を構築する。未設定なら None を返す。"""
    client_id = os.environ.get(id_key)
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
        request = google.auth.transport.requests.Request()
        creds.refresh(request)
        print(f"OAuth2トークンのリフレッシュ成功 ({id_key})。")
        return creds
    except Exception as e:
        print(f"[警告] トークンリフレッシュ失敗 ({id_key}): {e}", file=sys.stderr)
        return None


def load_all_credentials() -> tuple[list[Credentials], list[str]]:
    """設定されている全GCPプロジェクトの認証情報をリストで返す。"""
    result = []
    load_log = []
    for i, (id_key, secret_key, token_key) in enumerate(CREDENTIAL_SETS):
        has_id = bool(os.environ.get(id_key))
        has_secret = bool(os.environ.get(secret_key))
        has_token = bool(os.environ.get(token_key))
        status = f"p{i+1}: {id_key}={'OK' if has_id else 'EMPTY'} {secret_key}={'OK' if has_secret else 'EMPTY'} {token_key}={'OK' if has_token else 'EMPTY'}"
        print(status)
        creds = load_credentials_for(id_key, secret_key, token_key)
        if creds:
            result.append(creds)
            load_log.append(f"{status} => LOADED")
        else:
            load_log.append(f"{status} => FAILED")
    if not result:
        print("[エラー] 有効な認証情報が1つもありません。", file=sys.stderr)
        sys.exit(1)
    print(f"認証情報: {len(result)} プロジェクト分ロード完了")
    return result, load_log


def generate_thumbnail(video_path: str, suffix: str = "landscape") -> str:
    """動画の先頭フレームをffmpegで抽出してサムネイルを保存する。"""
    thumb_path = f"{OUTPUT_DIR}/thumbnail_{suffix}.jpg"
    result = subprocess.run([
        "ffmpeg", "-y", "-ss", "0.5", "-i", video_path,
        "-vframes", "1", thumb_path,
    ], capture_output=True, text=True)
    if result.returncode == 0:
        size_kb = Path(thumb_path).stat().st_size // 1024
        print(f"  サムネイル保存: {thumb_path} ({size_kb} KB)")
    else:
        print(f"  [警告] サムネイル抽出失敗: {result.stderr[-200:]}", file=sys.stderr)
    return thumb_path


def upload_thumbnail(youtube, video_id: str, thumb_path: str) -> None:
    """動画にサムネイルをアップロードする（失敗は警告のみ）。"""
    if not Path(thumb_path).exists():
        print(f"  [警告] サムネイルファイルなし: {thumb_path}", file=sys.stderr)
        return
    with open(thumb_path, "rb") as f:
        data = f.read()
    media = MediaIoBaseUpload(
        io.BytesIO(data),
        mimetype="image/jpeg",
        resumable=False,
    )
    try:
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        print(f"  サムネイルアップロード完了: {video_id}")
    except HttpError as e:
        try:
            err_body = json.loads(e.content.decode("utf-8"))
            reason = err_body.get("error", {}).get("errors", [{}])[0].get("reason", "")
            err_msg = err_body.get("error", {}).get("message", "")
        except Exception:
            reason = ""
            err_msg = str(e)
        if e.resp.status == 403 and reason in ("forbidden", "channelNotEligible"):
            print(
                "[警告] サムネイルAPIには YouTube チャンネルの電話番号認証が必要です。\n"
                "       output/thumbnail_N.jpg を YouTube Studio から手動でアップロードしてください。\n"
                "       YouTube Studio > コンテンツ > 動画を選択 > 詳細 > サムネイル",
                file=sys.stderr,
            )
        elif e.resp.status == 403 and "insufficientPermissions" in reason:
            print(
                f"[警告] サムネイルAPI 権限不足 (reason={reason})。\n"
                "       youtube.force-ssl スコープでトークンを再発行してください:\n"
                "       python scripts/get_refresh_token.py",
                file=sys.stderr,
            )
        else:
            print(f"[警告] サムネイルアップロード失敗 HTTP {e.resp.status} reason={reason}: {err_msg}", file=sys.stderr)
    except Exception as e:
        print(f"[警告] サムネイルアップロード失敗: {type(e).__name__}: {e}", file=sys.stderr)


def _get_error_reasons(http_error: HttpError) -> set[str]:
    try:
        content = json.loads(http_error.content.decode("utf-8"))
        return {e.get("reason", "") for e in content.get("error", {}).get("errors", [])}
    except Exception:
        return set()


def is_quota_exceeded(http_error: HttpError) -> bool:
    """GCPプロジェクト切り替えで解決できるAPIクォータ超過かどうかを判定する。"""
    reasons = _get_error_reasons(http_error)
    if reasons & QUOTA_EXCEEDED_REASONS:
        return True
    try:
        content = json.loads(http_error.content.decode("utf-8"))
        message = content.get("error", {}).get("message", "").lower()
        if "quota" in message or "rate limit" in message:
            return True
    except Exception:
        pass
    return False


def is_channel_upload_limit(http_error: HttpError) -> bool:
    """チャンネルの1日アップロード上限か判定する（プロジェクト切り替えでは解決不可）。"""
    return bool(_get_error_reasons(http_error) & CHANNEL_LIMIT_REASONS)


def build_tags() -> list[str]:
    """予想動画用タグをYouTubeタグ上限(500文字合計)内で返す。"""
    tags = list(LANDSCAPE_TAGS)
    result: list[str] = []
    total = 0
    for tag in tags:
        if total + len(tag) + 1 <= 500:
            result.append(tag)
            total += len(tag) + 1
        else:
            break
    return result


def _jst_date_str() -> str:
    """現在日付（JST）を「4月30日」形式で返す。"""
    import datetime as _dt
    _jst = _dt.timezone(_dt.timedelta(hours=9))
    _today = _dt.datetime.now(_jst)
    return f"{_today.month}月{_today.day}日"


def _make_video_filename(race_name: str) -> str:
    """レース名から意味のあるファイル名を生成する（連番回避）。"""
    kata = re.findall(r'[ァ-ヶー]{3,}', race_name)
    kw = kata[0][:6] if kata else "yoso"
    import datetime as _dt2
    date = _dt2.datetime.now(_dt2.timezone(_dt2.timedelta(hours=9))).strftime("%Y%m%d")
    rand4 = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"{kw}_{date}_{rand4}.mp4"


def build_title(race_name: str, grade: str, date_str: str) -> str:
    """テンプレートからタイトルを生成する（100文字以内）。"""
    prefix_tpl, suffix_tpl = random.choice(_LANDSCAPE_TITLE_TEMPLATES)
    prefix = prefix_tpl.replace("{date}", date_str).replace("{race_name}", race_name).replace("{grade}", grade)
    suffix = suffix_tpl.replace("{date}", date_str).replace("{race_name}", race_name).replace("{grade}", grade)
    # タイトル本体なし（prefix + suffix で完結）
    title = (prefix + suffix)[:100]
    return title


def build_description(race_name: str, grade: str, date_str: str, script: str) -> str:
    """説明文テンプレートからdescriptionを生成する。"""
    intro_tpl = random.choice(_LANDSCAPE_DESC_TEMPLATES)
    intro = intro_tpl.replace("{race_name}", race_name).replace("{grade}", grade).replace("{date}", date_str)
    base_tags = "#競馬 #競馬予想 #重賞予想 #keiba #G1予想 #競馬解説"
    hashtags = f"\n\n{base_tags}"
    max_len = 5000 - len(intro) - 1 - len(hashtags)
    return intro + "\n" + script[:max_len] + hashtags


def upload_video(youtube, title: str, description: str, video_path: str) -> str | None:
    """YouTube に動画をアップロードして videoId を返す。
    クォータ超過の場合は None を返す（呼び出し元で判定）。
    """
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": build_tags(),
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
        try:
            error_content = json.loads(e.content.decode("utf-8"))
        except Exception:
            error_content = {}
        print(f"[エラー] YouTube API HTTP {e.resp.status}: {error_content}", file=sys.stderr)

        if is_channel_upload_limit(e):
            print(
                "[警告] チャンネルの1日アップロード上限に達しました。\n"
                "       GCPプロジェクト切り替えでは解決できません（チャンネル自体の制限）。\n"
                "       明日UTC 0:00（JST 9:00）にリセットされます。",
                file=sys.stderr,
            )
            return "CHANNEL_LIMIT"

        if is_quota_exceeded(e):
            print(
                "[警告] YouTube APIのクォータ（1日10,000ユニット）を超過しました。\n"
                "       次のGCPプロジェクトに切り替えます。",
                file=sys.stderr,
            )
            return None  # クォータ超過は呼び出し元でプロジェクト切り替え

        sys.exit(1)

    except Exception as e:
        print(f"[エラー] アップロード失敗: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    print("=== YouTube 横向き予想動画 アップロード開始 ===")

    if not Path(NEWS_JSON).exists():
        print(f"[エラー] {NEWS_JSON} が見つかりません。", file=sys.stderr)
        sys.exit(1)

    news_data = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    # news.json はリストまたは単一オブジェクトの場合どちらも対応
    if isinstance(news_data, list):
        race_info = news_data[0] if news_data else {}
    else:
        race_info = news_data

    race_name = race_info.get("race_name", "重賞レース")
    grade = race_info.get("grade", "G1")
    date_str = _jst_date_str()

    # 横向き動画ファイルを探す（landscape_video.py が output/landscape_video.mp4 を生成する想定）
    landscape_video = Path(OUTPUT_DIR) / "landscape_video.mp4"
    if not landscape_video.exists():
        # フォールバック: video_*.mp4 から最初の1本
        candidates = sorted(
            f for f in Path(OUTPUT_DIR).glob("video_*.mp4")
            if f.stem.split("_")[1].isdigit()
        )
        if not candidates:
            print(f"[エラー] アップロード対象の動画ファイルが見つかりません。", file=sys.stderr)
            sys.exit(1)
        landscape_video = candidates[0]

    # スクリプトを読み込む（あれば説明文に活用）
    script_path = Path(OUTPUT_DIR) / "prediction_script.txt"
    if not script_path.exists():
        # フォールバック
        candidates = sorted(Path(OUTPUT_DIR).glob("script_*.txt"))
        script_path = candidates[0] if candidates else None

    script = script_path.read_text(encoding="utf-8").strip() if script_path and script_path.exists() else ""

    title = build_title(race_name, grade, date_str)
    description = build_description(race_name, grade, date_str, script)

    print(f"レース名: {race_name} / グレード: {grade} / 日付: {date_str}")
    print(f"タイトル: {title}")

    # 連番ファイル名を意味のある名前にリネーム（パターン検出回避）
    new_filename = _make_video_filename(race_name)
    new_video_path = landscape_video.parent / new_filename
    landscape_video.rename(new_video_path)
    print(f"ファイルリネーム: {landscape_video.name} → {new_filename}")

    all_creds, load_log = load_all_credentials()
    cred_idx = 0
    youtube = build("youtube", "v3", credentials=all_creds[cred_idx])

    uploaded_count = 0
    quota_exceeded = False
    upload_log = []

    video_id = None
    while video_id is None:
        result = upload_video(youtube, title, description, str(new_video_path))
        if result == "CHANNEL_LIMIT":
            quota_exceeded = True
            upload_log.append(f"CHANNEL_LIMIT title={title[:50]}")
            break
        elif result is None:
            # APIクォータ超過: 次のGCPプロジェクトに切り替え
            cred_idx += 1
            if cred_idx < len(all_creds):
                print(f"  プロジェクト {cred_idx + 1} に切り替えてリトライ...")
                youtube = build("youtube", "v3", credentials=all_creds[cred_idx])
            else:
                print("[警告] 全プロジェクトのAPIクォータが超過しました。")
                quota_exceeded = True
                upload_log.append(f"QUOTA_EXCEEDED project={cred_idx} title={title[:50]}")
                break
        else:
            video_id = result

    if not quota_exceeded and video_id:
        # サムネイル生成・アップロード（ffmpegで動画先頭フレームを抽出）
        print("  サムネイル生成中...")
        thumb_path = ""
        try:
            thumb_path = generate_thumbnail(str(new_video_path))
            upload_thumbnail(youtube, video_id, thumb_path)
        except Exception as e:
            print(f"[警告] サムネイル処理失敗: {e}", file=sys.stderr)

        upload_log.append(f"OK project={cred_idx+1} video_id={video_id} title={title[:40]}")
        uploaded_count += 1

    # POSTED_IDS_FILE への追記は行わない（Shortsと投稿管理を分離するため）

    # 結果サマリーをファイルに書き出す（ワークフローでコミットして確認用）
    import datetime
    summary_lines = [
        f"date: {datetime.datetime.utcnow().isoformat()}Z",
        f"type: landscape_prediction",
        f"race_name: {race_name}",
        f"grade: {grade}",
        f"projects_loaded: {len(all_creds)}",
        f"uploaded: {uploaded_count}",
        f"quota_exceeded: {quota_exceeded}",
    ] + load_log + upload_log
    Path("last_upload_result.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print("\n".join(summary_lines))

    if quota_exceeded:
        print(
            f"\n[エラー] 全プロジェクトのクォータが超過しました。\n"
            "明日UTC 0:00にクォータがリセットされます。",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n=== アップロード処理完了: {uploaded_count} 本 ===")


if __name__ == "__main__":
    main()
