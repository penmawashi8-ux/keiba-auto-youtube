#!/usr/bin/env python3
"""
script_N.txt を複数カットの字幕動画に変換する。
- 縦型 1080x1920
- 句点「。」で分割して各セリフを1カット化
- カット長さ = 音声総尺 ÷ セリフ数（音声と同期）
- Wikimedia Commons の競馬画像（最大5枚）をローテーション / 失敗時はグラデーション背景
- Pillow で字幕合成（最大幅972px・1行17文字）→ moviepy で結合 → audio_N.mp3 を合成
- moviepy 1.x / 2.x 両対応
"""

import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30

FONT_SIZE_SUBTITLE = 55
FONT_SIZE_TITLE = 30
# 動画幅の90%（972px）÷ フォントサイズ55px ≒ 17文字
MAX_CHARS_PER_LINE = 17

# Wikimedia Commons 競馬関連画像（パブリックドメイン）
HORSE_IMAGE_URLS = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/8/83/Swifts_Creek_horse_race.jpg/1280px-Swifts_Creek_horse_race.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d4/Meydan_Race_Course_Dubai.jpg/1280px-Meydan_Race_Course_Dubai.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/Cheltenham_roar.jpg/1280px-Cheltenham_roar.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c4/Horses_racing_at_Hyderabad.jpg/1280px-Horses_racing_at_Hyderabad.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1e/Horse_racing_Aqueduct.jpg/1280px-Horse_racing_Aqueduct.jpg",
]

# ---------------------------------------------------------------------------
# フォント検索
# ---------------------------------------------------------------------------

def find_japanese_font() -> str | None:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    for pattern in [
        "/usr/share/fonts/**/*CJK*Regular*",
        "/usr/share/fonts/**/*Noto*Regular*",
    ]:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]
    return None


# ---------------------------------------------------------------------------
# 画像ダウンロード（Wikimedia Commons）
# ---------------------------------------------------------------------------

def download_horse_images() -> list[str]:
    """Wikimedia Commons から競馬画像をダウンロードし、成功したパスのリストを返す。
    assets/ にキャッシュ済みの場合はスキップする。
    """
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    available: list[str] = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; keiba-auto-youtube/1.0; "
            "https://github.com/penmawashi8-ux/keiba-auto-youtube)"
        )
    }

    for i, url in enumerate(HORSE_IMAGE_URLS):
        dest = Path(ASSETS_DIR) / f"horse_{i}.jpg"
        if dest.exists() and dest.stat().st_size > 10_000:
            available.append(str(dest))
            print(f"  [キャッシュ] {dest.name}")
            continue

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 10_000:
                dest.write_bytes(resp.content)
                available.append(str(dest))
                print(f"  [DL] horse_{i}.jpg ({len(resp.content) // 1024} KB)")
            else:
                print(f"  [スキップ] horse_{i}.jpg: HTTP {resp.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"  [警告] horse_{i}.jpg ダウンロード失敗: {e}", file=sys.stderr)

    return available


# ---------------------------------------------------------------------------
# 音声尺取得
# ---------------------------------------------------------------------------

def get_audio_duration(audio_path: str) -> float:
    """mutagenでMP3の総再生時間（秒）を取得する。"""
    try:
        from mutagen.mp3 import MP3
        return MP3(audio_path).info.length
    except Exception as e:
        print(f"  [警告] mutagen失敗、ffmpegで代替: {e}", file=sys.stderr)
    # fallback: ffmpeg
    import subprocess
    result = subprocess.run(
        ["ffmpeg", "-i", audio_path],
        capture_output=True, text=True,
    )
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if m:
        h, m_, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + m_ * 60 + s
    return 10.0  # ultimate fallback


# ---------------------------------------------------------------------------
# 画像処理
# ---------------------------------------------------------------------------

def make_gradient_image() -> Image.Image:
    """濃紺→黒のグラデーション背景画像を生成する。"""
    img = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(VIDEO_HEIGHT):
        ratio = y / VIDEO_HEIGHT
        r = int(10 * (1 - ratio))
        g = int(22 * (1 - ratio))
        b = int(40 * (1 - ratio))
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(r, g, b))
    return img


def load_and_resize_image(path: str) -> Image.Image:
    """画像を 1080x1920 にリサイズ・中央クロップする。失敗時はグラデーション。"""
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return make_gradient_image()

    target_ratio = VIDEO_WIDTH / VIDEO_HEIGHT
    src_ratio = img.width / img.height

    if src_ratio > target_ratio:
        new_h = VIDEO_HEIGHT
        new_w = int(new_h * src_ratio)
    else:
        new_w = VIDEO_WIDTH
        new_h = int(new_w / src_ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - VIDEO_WIDTH) // 2
    top = (new_h - VIDEO_HEIGHT) // 2
    return img.crop((left, top, left + VIDEO_WIDTH, top + VIDEO_HEIGHT))


def wrap_text(text: str, max_chars: int) -> str:
    """テキストを max_chars 文字ごとに折り返す。"""
    lines = []
    while len(text) > max_chars:
        lines.append(text[:max_chars])
        text = text[max_chars:]
    if text:
        lines.append(text)
    return "\n".join(lines)


def add_subtitle_to_image(
    bg: Image.Image,
    subtitle: str,
    title: str,
    font_path: str | None,
) -> Image.Image:
    """背景画像にタイトル（上部）と字幕（下部）を合成し RGB 画像を返す。
    字幕の最大横幅は動画幅の90%（972px）に制限する。
    """
    img_rgba = bg.convert("RGBA")

    def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if font_path:
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    font_sub = load_font(FONT_SIZE_SUBTITLE)
    font_ttl = load_font(FONT_SIZE_TITLE)

    # ---- タイトル（上部・半透明背景）----
    overlay = Image.new("RGBA", img_rgba.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    title_short = title[:40]
    try:
        ttl_bbox = odraw.textbbox((0, 0), title_short, font=font_ttl)
    except TypeError:
        ttl_bbox = (0, 0, len(title_short) * FONT_SIZE_TITLE // 2, FONT_SIZE_TITLE)
    ttl_w = ttl_bbox[2] - ttl_bbox[0]
    ttl_h = ttl_bbox[3] - ttl_bbox[1]
    ttl_x = (VIDEO_WIDTH - ttl_w) // 2
    ttl_y = 40
    pad = 10
    odraw.rectangle(
        [ttl_x - pad, ttl_y - pad, ttl_x + ttl_w + pad, ttl_y + ttl_h + pad],
        fill=(0, 0, 0, 180),
    )
    img_rgba = Image.alpha_composite(img_rgba, overlay)

    draw = ImageDraw.Draw(img_rgba)
    draw.text((ttl_x, ttl_y), title_short, font=font_ttl, fill=(255, 255, 255, 255))

    # ---- 字幕（下部・縁取り付き）----
    # 1行17文字で折り返し（動画幅90%=972px 相当）
    wrapped = wrap_text(subtitle, MAX_CHARS_PER_LINE)

    try:
        sub_bbox = draw.multiline_textbbox((0, 0), wrapped, font=font_sub)
    except (TypeError, AttributeError):
        try:
            sub_bbox = draw.textbbox((0, 0), wrapped, font=font_sub)
        except TypeError:
            lines = wrapped.split("\n")
            sub_bbox = (
                0, 0,
                max(len(l) for l in lines) * FONT_SIZE_SUBTITLE,
                len(lines) * FONT_SIZE_SUBTITLE,
            )

    sub_w = sub_bbox[2] - sub_bbox[0]
    sub_h = sub_bbox[3] - sub_bbox[1]

    # 最大幅 972px（動画幅90%）を超えないようにクランプ
    max_sub_w = int(VIDEO_WIDTH * 0.9)
    sub_x = max((VIDEO_WIDTH - min(sub_w, max_sub_w)) // 2, 0)
    # 字幕エリアは画面下部20%以内（上限 VIDEO_HEIGHT * 0.80）
    sub_y = max(VIDEO_HEIGHT - sub_h - 120, int(VIDEO_HEIGHT * 0.80))

    # 縁取り（黒・4px）
    outline = 4
    for dx in range(-outline, outline + 1):
        for dy in range(-outline, outline + 1):
            if dx == 0 and dy == 0:
                continue
            try:
                draw.multiline_text(
                    (sub_x + dx, sub_y + dy), wrapped,
                    font=font_sub, fill=(0, 0, 0, 255), align="center",
                )
            except TypeError:
                draw.text((sub_x + dx, sub_y + dy), wrapped, font=font_sub, fill=(0, 0, 0, 255))

    try:
        draw.multiline_text(
            (sub_x, sub_y), wrapped,
            font=font_sub, fill=(255, 255, 255, 255), align="center",
        )
    except TypeError:
        draw.text((sub_x, sub_y), wrapped, font=font_sub, fill=(255, 255, 255, 255))

    return img_rgba.convert("RGB")


# ---------------------------------------------------------------------------
# moviepy 動画生成（1.x / 2.x 両対応）
# ---------------------------------------------------------------------------

def _get_moviepy_major() -> int:
    try:
        import moviepy
        ver = getattr(moviepy, "__version__", "1.0.0")
        return int(ver.split(".")[0])
    except Exception:
        return 1


def generate_video_moviepy(
    cuts: list[tuple[np.ndarray, float]],
    audio_path: str,
    output_path: str,
) -> None:
    """カットリスト（numpy配列, 秒数）と音声から動画を生成する。"""
    major = _get_moviepy_major()
    print(f"  moviepy {major}.x を使用")

    if major >= 2:
        from moviepy import AudioFileClip, ImageClip, concatenate_videoclips

        clips = [ImageClip(frame).with_duration(dur) for frame, dur in cuts]
        video = concatenate_videoclips(clips)
        audio = AudioFileClip(audio_path)
        final_dur = min(video.duration, audio.duration)
        video = video.with_duration(final_dur)
        video = video.with_audio(audio.with_duration(final_dur))
    else:
        from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips

        clips = [ImageClip(frame).set_duration(dur) for frame, dur in cuts]
        video = concatenate_videoclips(clips)
        audio = AudioFileClip(audio_path)
        final_dur = min(video.duration, audio.duration)
        video = video.subclip(0, final_dur)
        video = video.set_audio(audio.subclip(0, final_dur))

    video.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        ffmpeg_params=["-crf", "23", "-movflags", "+faststart"],
    )
    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"  → {output_path} ({size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== 動画生成開始（マルチカット版）===")

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のためスキップします。")
        sys.exit(0)

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    # 競馬画像をダウンロード（Wikimedia Commons）
    print("競馬関連画像をダウンロード中...")
    available_images = download_horse_images()
    print(f"  取得画像: {len(available_images)} 枚")
    if not available_images:
        print("  → 画像なし。グラデーション背景を使用します。")

    # 日本語フォントを検索
    font_path = find_japanese_font()
    print(f"  日本語フォント: {font_path or '見つからず（デフォルト使用）'}")

    # script_N.txt を処理
    script_files = sorted(Path(OUTPUT_DIR).glob("script_*.txt"))
    if not script_files:
        print(f"[エラー] {OUTPUT_DIR}/script_*.txt が見つかりません。", file=sys.stderr)
        sys.exit(1)

    for script_file in script_files:
        idx = int(script_file.stem.split("_")[1])
        audio_path = f"{OUTPUT_DIR}/audio_{idx}.mp3"
        output_path = f"{OUTPUT_DIR}/video_{idx}.mp4"

        if not Path(audio_path).exists():
            print(f"  [警告] {audio_path} が見つかりません。スキップします。")
            continue

        item = news_items[idx] if idx < len(news_items) else {}
        title = item.get("title", "競馬ニュース")

        print(f"\n--- 動画生成 [{idx}]: {title[:50]} ---")

        # 音声総尺を取得
        audio_duration = get_audio_duration(audio_path)
        print(f"  音声長: {audio_duration:.1f}秒")

        # 脚本を句点で分割
        script = script_file.read_text(encoding="utf-8").strip()
        raw_sentences = [s.strip() for s in script.split("。") if s.strip()]
        sentences = [s + "。" for s in raw_sentences]

        print(f"  セリフ数: {len(sentences)}")

        # 1カットあたりの表示時間 = 音声総尺 ÷ セリフ数
        cut_duration = audio_duration / len(sentences) if sentences else audio_duration
        print(f"  1カット: {cut_duration:.2f}秒")

        # カット生成
        cuts: list[tuple[np.ndarray, float]] = []
        for i, sentence in enumerate(sentences):
            if available_images:
                bg = load_and_resize_image(available_images[i % len(available_images)])
            else:
                bg = make_gradient_image()

            frame_img = add_subtitle_to_image(bg, sentence, title, font_path)
            cuts.append((np.array(frame_img), cut_duration))

            preview = sentence[:20].replace("\n", " ")
            print(f"  カット {i + 1}/{len(sentences)}: 「{preview}…」({cut_duration:.2f}秒)")

        # moviepy で動画生成
        generate_video_moviepy(cuts, audio_path, output_path)

    video_files = sorted(Path(OUTPUT_DIR).glob("video_*.mp4"))
    print(f"\n{len(video_files)} 本の動画を生成しました。")


if __name__ == "__main__":
    main()
