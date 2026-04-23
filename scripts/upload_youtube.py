#!/usr/bin/env python3
"""YouTube Data API v3 でOAuth2（refresh_token方式）を使って動画をアップロードする。"""

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
POSTED_IDS_FILE = "posted_ids.txt"

# YouTubeタイトルテンプレート（動画ごとにランダム選択）
# (prefix, suffix) 形式。実際のタイトルは prefix + article_title + suffix
# {date} は _random_date_str() で生成された日付文字列に置換される
_TITLE_TEMPLATES = [
    # ── 【カテゴリ】date + title ─────────────────────────
    ("【競馬速報】{date} ",     " #Shorts"),   # 【競馬速報】4月23日 〇〇 #Shorts
    ("【競馬ニュース】{date} ", " #Shorts"),   # 【競馬ニュース】4/23 〇〇 #Shorts
    ("【競馬NEWS】{date} ",     " #Shorts"),   # 【競馬NEWS】2026/4/23 〇〇 #Shorts
    ("【競馬情報】{date} ",     " #Shorts"),   # 【競馬情報】4.23 〇〇 #Shorts
    ("【最新競馬情報】{date} ", " #Shorts"),   # 【最新競馬情報】2026年4月23日 〇〇 #Shorts
    ("【競馬最新情報】{date} ", " #Shorts"),
    ("【重賞速報】{date} ",     " #Shorts"),
    ("【競馬速報！】{date} ",   " #Shorts"),

    # ── date + 【カテゴリ】+ title ──────────────────────
    ("{date}【競馬速報】",     " #Shorts"),
    ("{date}【競馬ニュース】", " #Shorts"),
    ("{date}【競馬情報】",     " #Shorts"),
    ("{date}【重賞速報】",     " #Shorts"),

    # ── date + カテゴリ｜ + title ──────────────────────
    ("{date}競馬NEWS｜",     " #Shorts"),
    ("{date}競馬速報｜",     " #Shorts"),
    ("{date}競馬ニュース｜", " #Shorts"),
    ("{date}競馬情報｜",     " #Shorts"),
    ("{date}重賞速報｜",     " #Shorts"),

    # ── date｜カテゴリ｜ + title ───────────────────────
    ("{date}｜競馬速報｜",     " #Shorts"),
    ("{date}｜競馬ニュース｜", " #Shorts"),
    ("{date}｜競馬情報｜",     " #Shorts"),
    ("{date}｜競馬NEWS｜",     " #Shorts"),

    # ── カテゴリ｜{date} + title ───────────────────────
    ("競馬速報｜{date} ",     " #Shorts"),
    ("競馬ニュース｜{date} ", " #Shorts"),
    ("競馬情報｜{date} ",     " #Shorts"),
    ("競馬NEWS｜{date} ",     " #Shorts"),

    # ── カテゴリ｜ + title + date suffix ───────────────
    ("競馬速報｜",     " {date} #Shorts"),
    ("競馬ニュース｜", " {date} #Shorts"),
    ("競馬情報｜",     " {date} #Shorts"),
    ("競馬NEWS｜",     " {date} #Shorts"),
    ("重賞速報｜",     " {date} #Shorts"),

    # ── date + title + ｜カテゴリ suffix ──────────────
    ("{date} ", "｜競馬速報 #Shorts"),
    ("{date} ", "｜競馬ニュース #Shorts"),
    ("{date} ", "｜競馬情報 #Shorts"),
    ("{date} ", "｜競馬NEWS #Shorts"),
    ("{date} ", "【競馬速報】#Shorts"),
    ("{date} ", "【競馬ニュース】#Shorts"),

    # ── title + ｜カテゴリ date suffix ────────────────
    ("", "｜競馬速報 {date} #Shorts"),
    ("", "｜競馬最新情報 {date} #Shorts"),
    ("", "｜競馬ニュース {date} #Shorts"),
    ("", "｜競馬情報 {date} #Shorts"),
    ("", "｜競馬NEWS {date} #Shorts"),
    ("", "｜重賞速報 {date} #Shorts"),

    # ── title + 【カテゴリ】date suffix ───────────────
    ("", "【競馬速報】{date} #Shorts"),
    ("", "【競馬ニュース】{date} #Shorts"),
    ("", "【競馬情報】{date} #Shorts"),

    # ── title + date｜カテゴリ suffix ─────────────────
    ("", " {date}｜競馬速報 #Shorts"),
    ("", " {date}｜競馬ニュース #Shorts"),
    ("", " {date}｜競馬情報 #Shorts"),
    ("", " {date}｜競馬NEWS #Shorts"),

    # ── title + ｜カテゴリdate suffix ─────────────────
    ("", "｜競馬速報{date} #Shorts"),
    ("", "｜競馬ニュース{date} #Shorts"),
    ("", "｜競馬情報{date} #Shorts"),
    ("", "｜競馬NEWS{date} #Shorts"),
]

# YouTube説明文テンプレート（動画ごとにローテーション）
_DESC_INTRO_TEMPLATES = [
    "競馬の最新ニュースをお届けします。",
    "注目の競馬情報をまとめました。",
    "競馬ファン必見の速報情報です。",
]

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # サムネイルアップロードに必要
]
CATEGORY_ID = "17"  # スポーツ
TAGS = ["競馬", "競馬ニュース", "keiba", "Shorts", "競馬速報"]

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


def generate_thumbnail(video_path: str, idx: int) -> str:
    """動画の先頭フレームをffmpegで抽出してサムネイル(1280x720)を保存する。"""
    thumb_path = f"{OUTPUT_DIR}/thumbnail_{idx}.jpg"
    result = subprocess.run([
        "ffmpeg", "-y", "-ss", "0.5", "-i", video_path,
        "-vframes", "1", "-s", "1280x720", thumb_path,
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


def update_posted_ids(news_items: list[dict]) -> None:
    """投稿済みIDをposted_ids.txtに追記する。"""
    path = Path(POSTED_IDS_FILE)
    existing = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
    new_ids = {item["id"] for item in news_items}
    all_ids = existing | new_ids
    path.write_text("\n".join(sorted(all_ids)), encoding="utf-8")
    print(f"投稿済みID {len(new_ids)} 件を {POSTED_IDS_FILE} に追記しました。")


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


def build_tags(extra_keywords: list[str] | None = None) -> list[str]:
    """固定タグ + 動的キーワードをYouTubeタグ上限(500文字合計)内で返す。"""
    tags = list(TAGS)
    if extra_keywords:
        tags.extend(extra_keywords)
    # YouTube はタグの合計文字数が500文字以内
    result: list[str] = []
    total = 0
    for tag in tags:
        if total + len(tag) + 1 <= 500:
            result.append(tag)
            total += len(tag) + 1
        else:
            break
    return result


def _random_date_str(dt: "datetime.datetime") -> str:
    """日付を複数フォーマットからランダムに返す。"""
    import datetime as _dt3
    y, m, d = dt.year, dt.month, dt.day
    formats = [
        f"{y}/{m}/{d}",      # 2026/4/23
        f"{m}/{d}",          # 4/23
        f"{m}月{d}日",       # 4月23日
        f"{y}年{m}月{d}日",  # 2026年4月23日
        f"{m}.{d}",          # 4.23
    ]
    return random.choice(formats)


def _make_video_filename(title: str) -> str:
    """記事タイトルから意味のあるファイル名を生成する（連番回避）。"""
    kata = re.findall(r'[ァ-ヶー]{3,}', title)
    kw = kata[0][:6] if kata else "keiba"
    import datetime as _dt2
    date = _dt2.datetime.now(_dt2.timezone(_dt2.timedelta(hours=9))).strftime("%Y%m%d")
    rand4 = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"{kw}_{date}_{rand4}.mp4"


def upload_video(youtube, title: str, description: str, video_path: str, extra_keywords: list[str] | None = None) -> str | None:
    """YouTube に動画をアップロードして videoId を返す。
    クォータ超過の場合は None を返す（呼び出し元で判定）。
    """
    # YouTubeタイトルの上限は100文字（テンプレート・日付フォーマットをランダム選択）
    import datetime as _dt
    _jst = _dt.timezone(_dt.timedelta(hours=9))
    _today = _dt.datetime.now(_jst)
    date_str = _random_date_str(_today)
    prefix_tpl, suffix = random.choice(_TITLE_TEMPLATES)
    prefix = prefix_tpl.replace("{date}", date_str)
    suffix = suffix.replace("{date}", date_str)
    max_body = 100 - len(prefix) - len(suffix)
    short_title = title if len(title) <= max_body else title[:max_body - 1] + "…"
    body = {
        "snippet": {
            "title": f"{prefix}{short_title}{suffix}",
            "description": description,
            "tags": build_tags(extra_keywords),
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


def extract_seo_keywords(title: str, script: str) -> list[str]:
    """ニュースタイトル・スクリプトから馬名・レース名・騎手名候補を抽出する。"""
    text = title + "\n" + script
    found: set[str] = set()

    # カタカナ4文字以上（馬名・レース名候補）
    for m in re.finditer(r'[ァ-ヶーｦ-ﾟ]{4,}', text):
        found.add(m.group())

    # 漢字混じりのレース名（〇〇賞・杯・カップ・ステークス・記念など）
    for m in re.finditer(r'[\u4e00-\u9fff\u30a1-\u30f6ー]{2,}(?:賞|杯|カップ|ステークス|ハンデ|記念)', text):
        found.add(m.group())

    # G1/G2/G3 グレード
    for m in re.finditer(r'G[123]', text):
        found.add(m.group())

    # 漢字2〜4文字の騎手名（「騎手」「騎乗」前後に出現するもの）
    for m in re.finditer(r'([\u4e00-\u9fff]{2,4})(?=騎手|騎乗)', text):
        found.add(m.group(1))
    for m in re.finditer(r'(?<=騎手・)([\u4e00-\u9fff]{2,4})', text):
        found.add(m.group(1))

    # 30文字以下のもののみ、最大20個
    return sorted(k for k in found if 2 <= len(k) <= 30)[:20]


def build_description(script: str, seo_keywords: list[str] | None = None) -> str:
    intro = random.choice(_DESC_INTRO_TEMPLATES)
    base_tags = "#競馬 #競馬ニュース #keiba #Shorts #競馬速報"
    if seo_keywords:
        kw_hashtags = " ".join(f"#{k}" for k in seo_keywords[:15])
        hashtags = f"\n\n{base_tags} {kw_hashtags}"
    else:
        hashtags = f"\n\n{base_tags}"
    max_len = 5000 - len(intro) - 1 - len(hashtags)
    return intro + "\n" + script[:max_len] + hashtags


def main() -> None:
    print("=== YouTube アップロード開始 ===")

    if not Path(NEWS_JSON).exists():
        print(f"[エラー] {NEWS_JSON} が見つかりません。", file=sys.stderr)
        sys.exit(1)

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のためアップロードをスキップします。")
        sys.exit(0)

    # video_[数字].mp4 のみ対象（moviepy の一時ファイルを除外）
    video_files = sorted(
        f for f in Path(OUTPUT_DIR).glob("video_*.mp4")
        if f.stem.split("_")[1].isdigit()
    )
    if not video_files:
        print(f"[エラー] {OUTPUT_DIR}/video_*.mp4 が見つかりません。", file=sys.stderr)
        sys.exit(1)

    all_creds, load_log = load_all_credentials()
    cred_idx = 0
    youtube = build("youtube", "v3", credentials=all_creds[cred_idx])

    uploaded_count = 0
    quota_exceeded = False
    upload_log = []
    upload_results_data = []  # Playwright サムネイル設定用

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
        seo_keywords = extract_seo_keywords(title, script)
        description = build_description(script, seo_keywords)
        print(f"  SEOキーワード({len(seo_keywords)}): {', '.join(seo_keywords[:10])}")

        print(f"\n--- アップロード [{idx}]: {title[:50]} ---")

        # 連番ファイル名を意味のある名前にリネーム（パターン検出回避）
        new_filename = _make_video_filename(title)
        new_video_path = video_file.parent / new_filename
        video_file.rename(new_video_path)
        print(f"  ファイルリネーム: {video_file.name} → {new_filename}")

        video_id = None
        while video_id is None:
            result = upload_video(youtube, title, description, str(new_video_path), extra_keywords=seo_keywords)
            if result == "CHANNEL_LIMIT":
                # チャンネル制限: プロジェクト切り替えでは解決しない → 即停止
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
                    print("[警告] 全プロジェクトのAPIクォータが超過しました。残りはスキップします。")
                    quota_exceeded = True
                    upload_log.append(f"QUOTA_EXCEEDED project={cred_idx} title={title[:50]}")
                    break
            else:
                video_id = result

        if quota_exceeded:
            break

        # サムネイル生成・アップロード（ffmpegで動画先頭フレームを抽出）
        print("  サムネイル生成中...")
        thumb_path = ""
        try:
            thumb_path = generate_thumbnail(str(new_video_path), idx)
            upload_thumbnail(youtube, video_id, thumb_path)
        except Exception as e:
            print(f"[警告] サムネイル処理失敗: {e}", file=sys.stderr)

        # Playwright サムネイル設定用にアップロード結果を記録
        upload_results_data.append({
            "video_id": video_id,
            "thumbnail": thumb_path,
            "title": title,
        })

        upload_log.append(f"OK project={cred_idx+1} video_id={video_id} title={title[:40]}")
        uploaded_count += 1

    update_posted_ids(news_items)

    # Playwright サムネイル設定用: アップロード結果を JSON で保存
    results_json_path = Path(OUTPUT_DIR) / "upload_results.json"
    results_json_path.write_text(
        json.dumps(upload_results_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"アップロード結果を {results_json_path} に保存しました。")

    # 結果サマリーをファイルに書き出す（ワークフローでコミットして確認用）
    import datetime
    summary_lines = [
        f"date: {datetime.datetime.utcnow().isoformat()}Z",
        f"projects_loaded: {len(all_creds)}",
        f"uploaded: {uploaded_count}",
        f"quota_exceeded: {quota_exceeded}",
    ] + load_log + upload_log
    Path("last_upload_result.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print("\n".join(summary_lines))

    if quota_exceeded:
        print(
            f"\n[エラー] 全プロジェクトのクォータが超過しました（完了: {uploaded_count} 本）。\n"
            "明日UTC 0:00にクォータがリセットされます。",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n=== アップロード処理完了: {uploaded_count} 本 ===")


if __name__ == "__main__":
    main()
