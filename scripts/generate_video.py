#!/usr/bin/env python3
"""
generate_video.py - 字幕動画生成スクリプト（書き直し版）
- Pillowで1080x1920にリサイズ（ImageOps.fit）→字幕描画→ffmpegで動画化
- 音声長に比例してカット尺を決定（文字数比）
- 字幕はy=1050px基準、複数行は上方向に展開、タイトル表示なし
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
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30

FONT_SIZE = 60
MAX_CHARS_PER_LINE = 16
LINE_SPACING = 12
SUBTITLE_BASE_Y = 1050   # 字幕最下行の上端Y座標
OUTLINE_WIDTH = 6

MIN_CUT_DURATION = 1.5
MAX_CUT_DURATION = 6.0

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
    primary = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    if Path(primary).exists():
        return primary
    # /usr/share/fonts/truetype/noto/ 以下を再帰的に検索
    for pattern in [
        "/usr/share/fonts/truetype/noto/*CJK*",
        "/usr/share/fonts/truetype/noto/*Noto*",
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
    """競馬画像をダウンロードしてキャッシュ。成功パスのリストを返す。"""
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
            print(f"  [警告] horse_{i}.jpg DL失敗: {e}", file=sys.stderr)
    return available


# ---------------------------------------------------------------------------
# 音声尺取得
# ---------------------------------------------------------------------------

def get_audio_duration(audio_path: str) -> float:
    """mutagenでMP3総再生時間（秒）を取得。失敗時はffmpegでフォールバック。"""
    try:
        from mutagen.mp3 import MP3
        duration = MP3(audio_path).info.length
        print(f"  音声長（mutagen）: {duration:.2f}秒")
        return duration
    except Exception as e:
        print(f"  [警告] mutagen失敗: {e}", file=sys.stderr)
    result = subprocess.run(["ffmpeg", "-i", audio_path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        duration = h * 3600 + mi * 60 + s
        print(f"  音声長（ffmpeg）: {duration:.2f}秒")
        return duration
    print("  [警告] 音声長取得失敗。10秒にフォールバック。", file=sys.stderr)
    return 10.0


# ---------------------------------------------------------------------------
# 背景画像
# ---------------------------------------------------------------------------

def make_gradient() -> Image.Image:
    """濃紺→黒グラデーション背景画像を生成。"""
    img = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(VIDEO_HEIGHT):
        ratio = y / VIDEO_HEIGHT
        r = int(10 * (1 - ratio))
        g = int(22 * (1 - ratio))
        b = int(40 * (1 - ratio))
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(r, g, b))
    return img


def load_background(path: str | None) -> Image.Image:
    """ImageOps.fitで1080x1920にリサイズ。失敗時はグラデーション。"""
    if path:
        try:
            img = Image.open(path).convert("RGB")
            print(f"  画像読み込み成功: {path}")
            img = ImageOps.fit(img, (VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)
            return img
        except Exception as e:
            print(f"  [警告] 画像読み込み失敗: {e}。グラデーションを使用。", file=sys.stderr)
    return make_gradient()


# ---------------------------------------------------------------------------
# テキスト処理・字幕描画
# ---------------------------------------------------------------------------

def wrap_text(text: str, max_chars: int) -> list[str]:
    """max_chars文字ごとに折り返す（最大3行）。"""
    lines: list[str] = []
    while len(text) > max_chars and len(lines) < 2:
        lines.append(text[:max_chars])
        text = text[max_chars:]
    lines.append(text)
    return lines[:3]


def draw_subtitle(img: Image.Image, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> Image.Image:
    """
    字幕をy=SUBTITLE_BASE_Y基準（下揃え）で描画する。
    - 複数行は上方向に展開
    - 縦中央付近（y=1050）に配置
    - 白文字、黒縁取り（OUTLINE_WIDTH=6）
    - タイトルは描画しない
    """
    # 半透明黒オーバーレイ（読みやすさ向上）
    img_rgba = img.convert("RGBA")
    dark = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 110))
    img_rgba = Image.alpha_composite(img_rgba, dark)
    img = img_rgba.convert("RGB")

    draw = ImageDraw.Draw(img)
    lines = wrap_text(text, MAX_CHARS_PER_LINE)
    line_height = FONT_SIZE + LINE_SPACING

    # y=SUBTITLE_BASE_Y を最下行のトップとし、行数分だけ上に伸ばす
    start_y = SUBTITLE_BASE_Y - (len(lines) - 1) * line_height

    for i, line in enumerate(lines):
        y = start_y + i * line_height
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(line) * (FONT_SIZE // 2)
        x = max((VIDEO_WIDTH - text_w) // 2, 20)

        # Pillow 8.0+ の stroke_width/stroke_fill でアウトライン描画
        try:
            draw.text(
                (x, y), line, font=font,
                fill=(255, 255, 255),
                stroke_width=OUTLINE_WIDTH,
                stroke_fill=(0, 0, 0),
            )
        except TypeError:
            # フォールバック：手動オフセット
            for dx in range(-OUTLINE_WIDTH, OUTLINE_WIDTH + 1, OUTLINE_WIDTH):
                for dy in range(-OUTLINE_WIDTH, OUTLINE_WIDTH + 1, OUTLINE_WIDTH):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=(255, 255, 255))

    return img


# ---------------------------------------------------------------------------
# ffmpeg 動画生成
# ---------------------------------------------------------------------------

def _run_ffmpeg(cmd: list[str], label: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[エラー] ffmpeg失敗 ({label}):", file=sys.stderr)
        print(result.stderr[-500:], file=sys.stderr)
        sys.exit(1)


def generate_video_ffmpeg(
    frames: list[tuple[str, float]],
    audio_path: str,
    output_path: str,
    tmp_dir: str,
) -> None:
    """フレームJPEGをffmpegでクリップ化→concat→音声結合して動画を生成。"""
    clip_paths: list[str] = []
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

    # concatリストファイル
    concat_list = os.path.join(tmp_dir, "concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for cp in clip_paths:
            f.write(f"file '{os.path.abspath(cp)}'\n")

    merged_video = os.path.join(tmp_dir, "merged.mp4")
    _run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        merged_video,
    ], "concat")

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

    print("  ffmpeg結合完了")
    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"  → {output_path} ({size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== 動画生成開始 ===")

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

    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    if font_path:
        try:
            font = ImageFont.truetype(font_path, FONT_SIZE)
            print(f"  フォント読み込み成功: {font_path}")
        except Exception as e:
            print(f"  [警告] フォント読み込み失敗: {e}。デフォルト使用。", file=sys.stderr)
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()

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

        # 音声長取得
        audio_duration = get_audio_duration(audio_path)

        # セリフ分割
        script = script_file.read_text(encoding="utf-8").strip()
        raw_sentences = [s.strip() for s in script.split("。") if s.strip()]
        sentences = [s + "。" for s in raw_sentences]
        print(f"  セリフ数: {len(sentences)}")

        if not sentences:
            print("  [警告] セリフが空です。スキップします。")
            continue

        # 文字数比でカット尺を計算
        total_chars = sum(len(s) for s in sentences)
        print(f"  総文字数: {total_chars}")

        durations: list[float] = []
        for s in sentences:
            d = audio_duration * len(s) / total_chars if total_chars > 0 else audio_duration / len(sentences)
            d = max(MIN_CUT_DURATION, min(MAX_CUT_DURATION, d))
            durations.append(d)

        tmp_dir = tempfile.mkdtemp(prefix="keiba_video_")
        try:
            frames: list[tuple[str, float]] = []
            for i, (sentence, duration) in enumerate(zip(sentences, durations)):
                img_path = available_images[i % len(available_images)] if available_images else None
                bg = load_background(img_path)
                frame_img = draw_subtitle(bg, sentence, font)
                frame_path = os.path.join(tmp_dir, f"frame_{i:04d}.jpg")
                frame_img.save(frame_path, "JPEG", quality=95)
                frames.append((frame_path, duration))
                print(f"  カット{i + 1}生成中: セリフ=「{sentence[:20]}」, 秒数={duration:.2f}秒")

            generate_video_ffmpeg(frames, audio_path, output_path, tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    video_files = list(Path(OUTPUT_DIR).glob("video_*.mp4"))
    print(f"\n{len(video_files)} 本の動画を生成しました。")


if __name__ == "__main__":
    main()
