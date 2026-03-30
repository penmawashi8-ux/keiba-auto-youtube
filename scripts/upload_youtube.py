#!/usr/bin/env python3
"""YouTube Data API v3 でOAuth2（refresh_token方式）を使って動画をアップロードする。"""

import glob
import io
import json
import os
import sys
import textwrap
from pathlib import Path

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from PIL import Image, ImageDraw, ImageFont, ImageOps

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
POSTED_IDS_FILE = "posted_ids.txt"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # サムネイルアップロードに必要
]
CATEGORY_ID = "17"  # スポーツ
TAGS = ["競馬", "競馬ニュース", "keiba", "Shorts", "競馬速報"]
CHARACTER_TAGS = ["競馬", "競馬豆知識", "keiba", "Shorts", "ウマコ", "競馬雑学", "競馬初心者"]

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

THUMB_W, THUMB_H = 1280, 720


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


def find_japanese_font() -> str | None:
    for candidate in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]:
        if Path(candidate).exists():
            return candidate
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    return hits[0] if hits else None


def generate_thumbnail(title: str, idx: int) -> bytes:
    """1280x720のサムネイル画像を生成してJPEGバイト列で返す。"""
    # --- 背景画像 ---
    ai_images = sorted(
        p for p in glob.glob(f"{ASSETS_DIR}/ai_*.jpg")
        if Path(p).stat().st_size > 1000
    )
    bg_path = ai_images[idx % len(ai_images)] if ai_images else None

    if bg_path:
        bg = Image.open(bg_path).convert("RGB")
        bg = ImageOps.fit(bg, (THUMB_W, THUMB_H), Image.LANCZOS)
    else:
        bg = Image.new("RGB", (THUMB_W, THUMB_H))
        draw_bg = ImageDraw.Draw(bg)
        for y in range(THUMB_H):
            r = int(15 + 45 * y / THUMB_H)
            g = int(10 + 20 * y / THUMB_H)
            b = int(50 + 50 * y / THUMB_H)
            draw_bg.line([(0, y), (THUMB_W, y)], fill=(r, g, b))

    # --- 半透明オーバーレイ（全体を均一に少し暗く） ---
    overlay = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 110))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(bg)
    font_path = find_japanese_font()

    try:
        badge_font = ImageFont.truetype(font_path, 36) if font_path else ImageFont.load_default()
    except Exception:
        badge_font = ImageFont.load_default()

    # --- 「競馬速報」赤バッジ（左上） ---
    badge_text = "競馬速報"
    pad = 16
    try:
        bb = draw.textbbox((0, 0), badge_text, font=badge_font)
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    except Exception:
        bw, bh = 160, 44
    draw.rounded_rectangle(
        [36, 36, 36 + bw + pad * 2, 36 + bh + pad],
        radius=10,
        fill=(210, 30, 30),
    )
    draw.text(
        (36 + pad, 36 + pad // 2),
        badge_text,
        font=badge_font,
        fill=(255, 255, 255),
        stroke_width=1,
        stroke_fill=(150, 0, 0),
    )

    # --- タイトル（一言どーん！スタイル）---
    import re

    clean_title = re.sub(r"[\u3000\s]+", "", title).strip()

    # 3段構成: 【レース名】 / 馬名・人名 / 要点アクション
    bracket_match = re.match(r"(【[^】]{1,10}】)(.*)", clean_title)
    if bracket_match:
        label = bracket_match.group(1)   # 例: 【京浜盃】
        rest  = bracket_match.group(2)   # 例: ロックターミガンで砂クラシック戦線に名乗り…
    else:
        label = None
        rest  = clean_title

    # 主語（最初の助詞の前）
    p = re.search(r"[でがはにをもとから]", rest)
    if p and p.start() >= 2:
        subject = rest[:p.start()]       # 例: ロックターミガン
        after   = rest[p.start():]       # 例: で砂クラシック戦線に名乗り…
    else:
        subject = rest[:10]
        after   = rest[10:]

    # アクション: after の最初の区切り（句読点・「・引用符）まで、最大12文字
    action_raw = re.split(r"[。、！？「」『』]", after.lstrip("でがはにをもとから"))[0]
    action = action_raw[:12]
    if action and not action[-1] in "！？":
        action += "！"

    max_w = THUMB_W - 80

    def fit_font(text: str, max_size: int, min_size: int = 36) -> tuple:
        for sz in range(max_size, min_size - 1, -8):
            try:
                f = ImageFont.truetype(font_path, sz) if font_path else ImageFont.load_default()
            except Exception:
                f = ImageFont.load_default()
            try:
                bb = draw.textbbox((0, 0), text, font=f)
                w = bb[2] - bb[0]
            except Exception:
                w = len(text) * sz
            if w <= max_w:
                return f, sz
        try:
            f = ImageFont.truetype(font_path, min_size) if font_path else ImageFont.load_default()
        except Exception:
            f = ImageFont.load_default()
        return f, min_size

    subject_font, subject_size = fit_font(subject, 120)
    label_size   = max(36, int(subject_size * 0.50))
    action_size  = max(40, int(subject_size * 0.60))
    try:
        label_font  = ImageFont.truetype(font_path, label_size)  if font_path else ImageFont.load_default()
        action_font = ImageFont.truetype(font_path, action_size) if font_path else ImageFont.load_default()
    except Exception:
        label_font = action_font = ImageFont.load_default()

    # 描画位置: 下寄せ 3行
    rows = []
    if label:
        rows.append((label,   label_font,   label_size,  (255, 255, 255), 3))
    rows.append(    (subject, subject_font, subject_size,(255, 235,   0), 8))
    if action:
        rows.append((action,  action_font,  action_size, (255, 255, 255), 4))

    total_h = sum(sz + 14 for _, _, sz, _, _ in rows)
    y = THUMB_H - total_h - 60

    for text, font, sz, color, stroke in rows:
        try:
            bb = draw.textbbox((0, 0), text, font=font)
            tw = bb[2] - bb[0]
        except Exception:
            tw = len(text) * sz
        x = max((THUMB_W - tw) // 2, 40)
        draw.text((x, y), text, font=font, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += sz + 14

    buf = io.BytesIO()
    bg.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def save_thumbnail(thumbnail_bytes: bytes, idx: int) -> str:
    """サムネイル画像をファイルに保存して、パスを返す。"""
    thumb_path = f"{OUTPUT_DIR}/thumbnail_{idx}.jpg"
    Path(thumb_path).write_bytes(thumbnail_bytes)
    print(f"  サムネイル保存: {thumb_path}")
    return thumb_path


def upload_thumbnail(youtube, video_id: str, thumbnail_bytes: bytes) -> None:
    """動画にサムネイルをアップロードする（失敗は警告のみ）。"""
    media = MediaIoBaseUpload(
        io.BytesIO(thumbnail_bytes),
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


def upload_video(
    youtube,
    title: str,
    description: str,
    video_path: str,
    tags: list[str] | None = None,
    title_prefix: str = "【競馬速報】",
) -> str | None:
    """YouTube に動画をアップロードして videoId を返す。
    クォータ超過の場合は None を返す（呼び出し元で判定）。
    """
    # YouTubeタイトルの上限は100文字
    if tags is None:
        tags = TAGS
    prefix, suffix = title_prefix, " #Shorts"
    max_body = 100 - len(prefix) - len(suffix)
    short_title = title if len(title) <= max_body else title[:max_body - 1] + "…"
    body = {
        "snippet": {
            "title": f"{prefix}{short_title}{suffix}",
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

    # キャラクター動画のみの実行（ニュースなし）でも処理を続ける
    char_video_only = not news_items
    if char_video_only:
        char_video_path = Path(f"{OUTPUT_DIR}/character_video.mp4")
        if not char_video_path.exists():
            print("ニュースが0件かつキャラクター動画もないためスキップします。")
            sys.exit(0)
        print("ニュースが0件ですが、キャラクター動画が存在するためアップロードを続行します。")

    if not char_video_only:
        # video_[数字].mp4 のみ対象（moviepy の一時ファイルを除外）
        video_files = sorted(
            f for f in Path(OUTPUT_DIR).glob("video_*.mp4")
            if f.stem.split("_")[1].isdigit()
        )
        if not video_files:
            print(f"[エラー] {OUTPUT_DIR}/video_*.mp4 が見つかりません。", file=sys.stderr)
            sys.exit(1)
    else:
        video_files = []

    all_creds, load_log = load_all_credentials()
    cred_idx = 0
    youtube = build("youtube", "v3", credentials=all_creds[cred_idx])

    uploaded_count = 0
    quota_exceeded = False
    upload_log = []

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
        video_id = None
        while video_id is None:
            result = upload_video(youtube, title, description, str(video_file))
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

        # サムネイル生成・保存・アップロード
        print("  サムネイル生成中...")
        try:
            thumb_bytes = generate_thumbnail(title, idx)
            save_thumbnail(thumb_bytes, idx)
            upload_thumbnail(youtube, video_id, thumb_bytes)
        except Exception as e:
            print(f"[警告] サムネイル処理失敗: {e}", file=sys.stderr)

        upload_log.append(f"OK project={cred_idx+1} video_id={video_id} title={title[:40]}")
        uploaded_count += 1

    update_posted_ids(news_items)

    # ---- キャラクター動画のアップロード（存在する場合） ----
    char_video = Path(f"{OUTPUT_DIR}/character_video.mp4")
    if char_video.exists():
        char_script_path = Path(f"{OUTPUT_DIR}/character_script.txt")
        char_script_text = char_script_path.read_text(encoding="utf-8").strip() if char_script_path.exists() else ""
        char_title = "ウマコの競馬豆知識コーナー！"
        char_description = char_script_text + "\n\n#競馬 #競馬豆知識 #keiba #Shorts #ウマコ #競馬雑学"
        print(f"\n--- キャラクター動画アップロード: {char_video} ---")
        char_video_id = None
        char_cred_idx = 0
        char_youtube = build("youtube", "v3", credentials=all_creds[char_cred_idx])
        while char_video_id is None:
            result = upload_video(
                char_youtube, char_title, char_description, str(char_video),
                tags=CHARACTER_TAGS, title_prefix="【ウマコの競馬豆知識】",
            )
            if result == "CHANNEL_LIMIT":
                upload_log.append(f"CHANNEL_LIMIT (character video)")
                break
            elif result is None:
                char_cred_idx += 1
                if char_cred_idx < len(all_creds):
                    char_youtube = build("youtube", "v3", credentials=all_creds[char_cred_idx])
                else:
                    print("[警告] キャラクター動画: 全プロジェクトのクォータ超過。スキップします。")
                    break
            else:
                char_video_id = result
                upload_log.append(f"OK (character) video_id={char_video_id}")
                uploaded_count += 1
                print(f"キャラクター動画アップロード完了: {char_video_id}")

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
