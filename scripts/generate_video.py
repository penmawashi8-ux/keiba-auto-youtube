#!/usr/bin/env python3
"""
script_N.txt を複数カットの字幕動画に変換する（ffmpeg直接方式）。
- 縦型 1080x1920
- 句点「。」で分割して各セリフを1カット化
- カット長さ = 音声総尺 ÷ セリフ数（mutagenで厳密に取得）
- assetsフォルダの画像をローテーション（失敗時はグラデーション背景）
- 背景に半透明黒オーバーレイを重ねて字幕を読みやすく
- 字幕Y座標は画面の65%（1248px）に配置
- Pillowでフレーム画像を tmp/ に保存 → ffmpegで各クリップ生成 → concat → 音声結合
- moviepy / imageio / imageio-ffmpeg は使わない
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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
MAX_CHARS_PER_LINE = 17

# 字幕Y座標（画面の65% = 1248px）
SUBTITLE_Y = int(VIDEO_HEIGHT * 0.65)

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
# 画像ダウンロード
# ---------------------------------------------------------------------------

def download_horse_images() -> list[str]:
    """Wikimedia Commons から競馬画像をダウンロードし、成功したパスのリストを返す。"""
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
    """mutagenでMP3の総再生時間（秒）を取得する。ffmpegでフォールバック。"""
    try:
        from mutagen.mp3 import MP3
        return MP3(audio_path).info.length
    except Exception as e:
        print(f"  [警告] mutagen失敗、ffmpegで代替: {e}", file=sys.stderr)
    result = subprocess.run(
        ["ffmpeg", "-i", audio_path],
        capture_output=True, text=True,
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + s
    return 10.0


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
    """テキストを max_chars 文字ごとに折り返す（最大3行）。"""
    lines = []
    while len(text) > max_chars:
        lines.append(text[:max_chars])
        text = text[max_chars:]
    if text:
        lines.append(text)
    return "\n".join(lines[:3])  # 最大3行


def add_subtitle_to_image(
    bg: Image.Image,
    subtitle: str,
    title: str,
    font_path: str | None,
) -> Image.Image:
    """背景画像にオーバーレイ・タイトル・字幕を合成して RGB 画像を返す。
    字幕Y座標は画面の65%（1248px）。
    """
    img_rgba = bg.convert("RGBA")

    # 半透明の黒オーバーレイ（全面）
    overlay_full = Image.new("RGBA", img_rgba.size, (0, 0, 0, 120))
    img_rgba = Image.alpha_composite(img_rgba, overlay_full)

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
    title_overlay = Image.new("RGBA", img_rgba.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(title_overlay)
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
    img_rgba = Image.alpha_composite(img_rgba, title_overlay)

    draw = ImageDraw.Draw(img_rgba)
    draw.text((ttl_x, ttl_y), title_short, font=font_ttl, fill=(255, 255, 255, 255))

    # ---- 字幕（画面65%の位置・縁取り付き）----
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
                max(len(ln) for ln in lines) * FONT_SIZE_SUBTITLE,
                len(lines) * FONT_SIZE_SUBTITLE,
            )

    sub_w = sub_bbox[2] - sub_bbox[0]
    max_sub_w = int(VIDEO_WIDTH * 0.9)
    sub_x = max((VIDEO_WIDTH - min(sub_w, max_sub_w)) // 2, 0)
    sub_y = SUBTITLE_Y  # 1248px（画面の65%）

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
                draw.text(
                    (sub_x + dx, sub_y + dy), wrapped,
                    font=font_sub, fill=(0, 0, 0, 255),
                )

    try:
        draw.multiline_text(
            (sub_x, sub_y), wrapped,
            font=font_sub, fill=(255, 255, 255, 255), align="center",
        )
    except TypeError:
        draw.text((sub_x, sub_y), wrapped, font=font_sub, fill=(255, 255, 255, 255))

    return img_rgba.convert("RGB")


# ---------------------------------------------------------------------------
# ffmpeg 動画生成
# ---------------------------------------------------------------------------

def _run_ffmpeg(cmd: list[str], label: str) -> None:
    """ffmpegコマンドを実行し、失敗時はエラーを出力して終了する。"""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[エラー] ffmpeg失敗 ({label}):", file=sys.stderr)
        # 最後の300文字だけ表示
        print(result.stderr[-300:], file=sys.stderr)
        sys.exit(1)


def generate_video_ffmpeg(
    frames: list[tuple[str, float]],
    audio_path: str,
    output_path: str,
    tmp_dir: str,
) -> None:
    """各フレームJPEGをffmpegでクリップ化 → concat → 音声結合して動画を生成する。"""
    clip_paths = []
    for i, (frame_path, duration) in enumerate(frames):
        clip_path = os.path.join(tmp_dir, f"clip_{i:04d}.mp4")
        _run_ffmpeg([
            "ffmpeg", "-y",
            "-loop", "1",
            "-framerate", str(FPS),
            "-i", frame_path,
            "-t", f"{duration:.6f}",
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            clip_path,
        ], f"clip_{i:04d}")
        clip_paths.append(clip_path)
        print(f"  クリップ {i + 1}/{len(frames)} 生成 ({duration:.2f}秒)")

    # concat リストファイル（絶対パスで記述）
    concat_list = os.path.join(tmp_dir, "concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for cp in clip_paths:
            f.write(f"file '{os.path.abspath(cp)}'\n")

    # 全クリップを結合（再エンコードなしで高速）
    merged_video = os.path.join(tmp_dir, "merged.mp4")
    _run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        merged_video,
    ], "concat")

    # 音声を結合して最終動画を出力
    _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", merged_video,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ], "audio-merge")

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"  → {output_path} ({size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== 動画生成開始（ffmpeg直接方式）===")

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のためスキップします。")
        sys.exit(0)

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    print("競馬関連画像をダウンロード中...")
    available_images = download_horse_images()
    print(f"  取得画像: {len(available_images)} 枚")
    if not available_images:
        print("  → 画像なし。グラデーション背景を使用します。")

    font_path = find_japanese_font()
    print(f"  日本語フォント: {font_path or '見つからず（デフォルト使用）'}")

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

        audio_duration = get_audio_duration(audio_path)
        print(f"  音声長: {audio_duration:.1f}秒")

        script = script_file.read_text(encoding="utf-8").strip()
        raw_sentences = [s.strip() for s in script.split("。") if s.strip()]
        sentences = [s + "。" for s in raw_sentences]
        print(f"  セリフ数: {len(sentences)}")

        if not sentences:
            print("  [警告] セリフが空です。スキップします。")
            continue

        # 1カットあたりの表示時間 = 音声総尺 ÷ セリフ数
        cut_duration = audio_duration / len(sentences)
        print(f"  1カット: {cut_duration:.2f}秒")

        tmp_dir = tempfile.mkdtemp(prefix="keiba_video_")
        try:
            frames: list[tuple[str, float]] = []
            for i, sentence in enumerate(sentences):
                if available_images:
                    bg = load_and_resize_image(available_images[i % len(available_images)])
                else:
                    bg = make_gradient_image()

                frame_img = add_subtitle_to_image(bg, sentence, title, font_path)
                frame_path = os.path.join(tmp_dir, f"frame_{i:04d}.jpg")
                frame_img.save(frame_path, "JPEG", quality=95)
                frames.append((frame_path, cut_duration))

                preview = sentence[:20].replace("\n", " ")
                print(f"  フレーム {i + 1}/{len(sentences)}: 「{preview}…」")

            generate_video_ffmpeg(frames, audio_path, output_path, tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print("  一時フォルダを削除しました。")

    video_files = sorted(
        f for f in Path(OUTPUT_DIR).glob("video_*.mp4")
        if f.stem.split("_")[1].isdigit()
    )
    print(f"\n{len(video_files)} 本の動画を生成しました。")


if __name__ == "__main__":
    main()
