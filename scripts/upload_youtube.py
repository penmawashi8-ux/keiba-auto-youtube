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

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
# サムネイルAPIには youtube.force-ssl スコープが必要。
# 既存トークンが youtube.upload のみの場合、thumbnails.set は 403 になるが
# 動画アップロード自体には影響しない（upload_thumbnail が警告のみで継続）。
# 再発行手順は scripts/get_refresh_token.py を参照。
CATEGORY_ID = "17"  # スポーツ
TAGS = ["競馬", "競馬ニュース", "keiba", "Shorts", "競馬速報"]

# YouTube API クォータ: 1日10,000ユニット / videos.insert = 1,600ユニット
QUOTA_EXCEEDED_REASONS = {"quotaExceeded", "userRateLimitExceeded", "dailyLimitExceeded"}

THUMB_W, THUMB_H = 1280, 720


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
        scopes=YOUTUBE_SCOPES,
    )

    try:
        request = google.auth.transport.requests.Request()
        creds.refresh(request)
        print("OAuth2トークンのリフレッシュ成功。")
    except Exception as e:
        print(f"[エラー] トークンリフレッシュ失敗: {e}", file=sys.stderr)
        sys.exit(1)

    return creds


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

    # 【レース名】と主語（馬名・人名）を分離
    bracket_match = re.match(r"(【[^】]{1,10}】)(.*)", clean_title)
    if bracket_match:
        label = bracket_match.group(1)          # 例: 【京浜盃】
        rest  = bracket_match.group(2)          # 例: ロックターミガンで砂クラシック...
        # 最初の助詞（で/が/は/に/を/も/と/から）の直前までを主語として抽出
        p = re.search(r"[でがはにをもとから]", rest)
        key = rest[:p.start()] if p and p.start() >= 2 else rest[:8]
    else:
        label = None
        key   = clean_title[:10]

    max_w = THUMB_W - 80

    # key を1行に収まる最大フォントサイズで描画（120px〜60px）
    for key_size in range(120, 59, -8):
        try:
            key_font = ImageFont.truetype(font_path, key_size) if font_path else ImageFont.load_default()
        except Exception:
            key_font = ImageFont.load_default()
        try:
            bb = draw.textbbox((0, 0), key, font=key_font)
            key_w = bb[2] - bb[0]
        except Exception:
            key_w = len(key) * key_size
        if key_w <= max_w:
            break

    # label フォント（key の 55%サイズ）
    label_size = max(36, int(key_size * 0.55))
    try:
        label_font = ImageFont.truetype(font_path, label_size) if font_path else ImageFont.load_default()
    except Exception:
        label_font = ImageFont.load_default()

    # 描画位置：下から中心に2行（label → key）
    key_line_h = key_size + 16
    label_line_h = label_size + 10
    total_h = (label_line_h if label else 0) + key_line_h
    start_y = THUMB_H - total_h - 72

    # label 行（白・細ストローク）
    if label:
        try:
            bb = draw.textbbox((0, 0), label, font=label_font)
            lw = bb[2] - bb[0]
        except Exception:
            lw = len(label) * label_size
        draw.text(
            (max((THUMB_W - lw) // 2, 40), start_y),
            label,
            font=label_font,
            fill=(255, 255, 255),
            stroke_width=4,
            stroke_fill=(0, 0, 0),
        )
        start_y += label_line_h

    # key 行（黄色・太ストローク）
    try:
        bb = draw.textbbox((0, 0), key, font=key_font)
        kw = bb[2] - bb[0]
    except Exception:
        kw = len(key) * key_size
    draw.text(
        (max((THUMB_W - kw) // 2, 40), start_y),
        key,
        font=key_font,
        fill=(255, 235, 0),
        stroke_width=8,
        stroke_fill=(0, 0, 0),
    )

    buf = io.BytesIO()
    bg.save(buf, "JPEG", quality=92)
    return buf.getvalue()


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
        except Exception:
            reason = ""
        if e.resp.status == 403 and reason in ("forbidden", "channelNotEligible"):
            print(
                "[警告] サムネイル設定には YouTube チャンネルの電話番号認証が必要です。\n"
                "       YouTube Studio > 設定 > チャンネル > 機能の利用資格 で確認してください。",
                file=sys.stderr,
            )
        else:
            print(f"[警告] サムネイルアップロード失敗 (HTTP {e.resp.status}): {e}", file=sys.stderr)
    except Exception as e:
        print(f"[警告] サムネイルアップロード失敗: {e}", file=sys.stderr)


def update_posted_ids(news_items: list[dict]) -> None:
    """投稿済みIDをposted_ids.txtに追記する。"""
    path = Path(POSTED_IDS_FILE)
    existing = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
    new_ids = {item["id"] for item in news_items}
    all_ids = existing | new_ids
    path.write_text("\n".join(sorted(all_ids)), encoding="utf-8")
    print(f"投稿済みID {len(new_ids)} 件を {POSTED_IDS_FILE} に追記しました。")


def is_quota_exceeded(http_error: HttpError) -> bool:
    """HttpError がクォータ超過かどうかを判定する。"""
    try:
        content = json.loads(http_error.content.decode("utf-8"))
        errors = content.get("error", {}).get("errors", [])
        for err in errors:
            if err.get("reason") in QUOTA_EXCEEDED_REASONS:
                return True
        message = content.get("error", {}).get("message", "").lower()
        if "quota" in message or "rate limit" in message:
            return True
    except Exception:
        pass
    return http_error.resp.status == 403


def upload_video(youtube, title: str, description: str, video_path: str) -> str | None:
    """YouTube に動画をアップロードして videoId を返す。
    クォータ超過の場合は None を返す（呼び出し元で判定）。
    """
    body = {
        "snippet": {
            "title": f"【競馬速報】{title} #Shorts",
            "description": description,
            "tags": TAGS,
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

        if is_quota_exceeded(e):
            print(
                "[警告] YouTube APIのクォータ（1日10,000ユニット）を超過しました。\n"
                "       明日UTC 0:00にリセットされるまでアップロードはスキップします。",
                file=sys.stderr,
            )
            return None  # クォータ超過は呼び出し元で処理

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

    creds = load_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    uploaded_count = 0
    quota_exceeded = False

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
        video_id = upload_video(youtube, title, description, str(video_file))

        if video_id is None:
            # クォータ超過: 以降のアップロードも不可なのでループを抜ける
            quota_exceeded = True
            break

        # サムネイル生成・アップロード
        print("  サムネイル生成中...")
        try:
            thumb_bytes = generate_thumbnail(title, idx)
            upload_thumbnail(youtube, video_id, thumb_bytes)
        except Exception as e:
            print(f"[警告] サムネイル処理失敗: {e}", file=sys.stderr)

        uploaded_count += 1

    update_posted_ids(news_items)

    if quota_exceeded:
        print(
            f"\nクォータ超過のためアップロードを中断しました（完了: {uploaded_count} 本）。\n"
            "明日UTC 0:00にクォータがリセットされます。"
        )
        # posted_ids は更新済みなので次回は重複しない
        # ワークフローとしては成功扱い（クォータは外部要因）
        sys.exit(0)

    print(f"\n=== アップロード処理完了: {uploaded_count} 本 ===")


if __name__ == "__main__":
    main()
