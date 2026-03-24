#!/usr/bin/env python3
"""
generate_video.py - ffmpegのみで字幕動画を生成する（moviepy不使用）

流れ:
  1. script_N.txt を句点で分割してセリフリスト生成
  2. mutagen で audio_N.mp3 の総再生時間を取得
  3. 各セリフの表示時間を計算（総時間 × 文字数 / 総文字数、最低1.5秒）
  4. Pillow で字幕画像（1080x1920）を tmp/frame_N.png に保存
  5. ffmpeg で frame_N.png → clip_N.mp4（無音）
  6. tmp/concat.txt にリスト書き出し
  7. ffmpeg concat で tmp/silent.mp4 を生成
  8. ffmpeg で silent.mp4 + audio_N.mp3 → output/video_N.mp4
  9. tmp フォルダを削除
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

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
LINE_SPACING = 10
SUBTITLE_CENTER_Y = 960   # 字幕中心Y座標
OUTLINE_WIDTH = 8
MIN_CUT_DURATION = 1.5


# ---------------------------------------------------------------------------
# フォント検索
# ---------------------------------------------------------------------------

def find_japanese_font() -> str | None:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    if hits:
        return hits[0]
    return None


# ---------------------------------------------------------------------------
# 音声尺取得
# ---------------------------------------------------------------------------

def get_audio_duration(audio_path: str) -> float:
    try:
        from mutagen.mp3 import MP3
        duration = MP3(audio_path).info.length
        print(f"  音声の総再生時間（mutagen）: {duration:.2f}秒")
        return duration
    except Exception as e:
        print(f"  [警告] mutagen失敗: {e}", file=sys.stderr)
    # ffmpeg でフォールバック
    result = subprocess.run(["ffmpeg", "-i", audio_path], capture_output=True, text=True)
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        duration = h * 3600 + mi * 60 + s
        print(f"  音声の総再生時間（ffmpeg）: {duration:.2f}秒")
        return duration
    print("  [警告] 音声長取得失敗。10秒にフォールバック。", file=sys.stderr)
    return 10.0


# ---------------------------------------------------------------------------
# 字幕フレーム画像生成（Pillow）
# ---------------------------------------------------------------------------

def load_background(assets_images: list[str], index: int) -> Image.Image:
    """assetsの画像をローテーションしてImageOps.fitで1080x1920にリサイズ。"""
    if assets_images:
        path = assets_images[index % len(assets_images)]
        try:
            img = Image.open(path).convert("RGB")
            img = ImageOps.fit(img, (VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)
            return img
        except Exception as e:
            print(f"  [警告] 画像読み込み失敗 ({path}): {e}", file=sys.stderr)
    # 単色背景（濃紺）
    return Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (15, 15, 40))


def make_frame(text: str, assets_images: list[str], index: int, font: ImageFont.ImageFont) -> Image.Image:
    """字幕付きフレーム画像を生成して返す。"""
    bg = load_background(assets_images, index)

    # 半透明黒オーバーレイ
    overlay = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 120))
    img = bg.convert("RGBA")
    img = Image.alpha_composite(img, overlay).convert("RGB")

    draw = ImageDraw.Draw(img)

    # テキスト折り返し（1行最大16文字）
    lines = textwrap.wrap(text, width=MAX_CHARS_PER_LINE)
    if not lines:
        lines = [text]

    line_height = FONT_SIZE + LINE_SPACING
    total_height = len(lines) * line_height

    # y=960px 中心、複数行は上方向に展開
    start_y = SUBTITLE_CENTER_Y - total_height // 2

    for i, line in enumerate(lines):
        y = start_y + i * line_height
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(line) * (FONT_SIZE // 2)
        x = max((VIDEO_WIDTH - text_w) // 2, 20)

        # 縁取り（黒、linewidth=8）
        try:
            draw.text(
                (x, y), line, font=font,
                fill=(255, 255, 255),
                stroke_width=OUTLINE_WIDTH,
                stroke_fill=(0, 0, 0),
            )
        except TypeError:
            # Pillow 7系以前のフォールバック
            for dx in (-OUTLINE_WIDTH, 0, OUTLINE_WIDTH):
                for dy in (-OUTLINE_WIDTH, 0, OUTLINE_WIDTH):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=(255, 255, 255))

    return img


# ---------------------------------------------------------------------------
# ffmpeg ヘルパー
# ---------------------------------------------------------------------------

def run_ffmpeg(cmd: list[str]) -> None:
    print(f"  $ {' '.join(cmd[:8])} ...")
    subprocess.run(cmd, check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# 1本の動画を生成
# ---------------------------------------------------------------------------

def build_video(
    script_path: Path,
    audio_path: str,
    output_path: str,
    assets_images: list[str],
    font: ImageFont.ImageFont,
) -> None:
    script = script_path.read_text(encoding="utf-8").strip()
    raw = [s.strip() for s in script.split("。") if s.strip()]
    sentences = [s + "。" for s in raw]

    if not sentences:
        print("  [警告] セリフが空です。スキップします。")
        return

    audio_duration = get_audio_duration(audio_path)
    total_chars = sum(len(s) for s in sentences)

    # 各セリフの表示時間を計算
    durations: list[float] = []
    for s in sentences:
        d = audio_duration * len(s) / total_chars if total_chars > 0 else audio_duration / len(sentences)
        d = max(MIN_CUT_DURATION, d)
        durations.append(d)

    print(f"  セリフ数: {len(sentences)}")
    for i, (s, d) in enumerate(zip(sentences, durations)):
        print(f"    [{i}] 「{s[:20]}」 → {d:.2f}秒")

    tmp_dir = tempfile.mkdtemp(prefix="keiba_video_")
    try:
        # --- 4. 字幕フレーム画像生成 ---
        clip_paths: list[str] = []
        for i, (sentence, duration) in enumerate(zip(sentences, durations)):
            frame_path = os.path.join(tmp_dir, f"frame_{i}.png")
            frame_img = make_frame(sentence, assets_images, i, font)
            frame_img.save(frame_path, "PNG")
            print(f"  フレーム画像生成完了: frame_{i}.png")

            # --- 5. frame_N.png → clip_N.mp4 ---
            clip_path = os.path.join(tmp_dir, f"clip_{i}.mp4")
            run_ffmpeg([
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", frame_path,
                "-t", f"{duration:.6f}",
                "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", str(FPS),
                clip_path,
            ])
            clip_paths.append(clip_path)
            print(f"  クリップ生成完了: clip_{i}.mp4 ({duration:.2f}秒)")

        # --- 6. concat.txt 書き出し ---
        concat_txt = os.path.join(tmp_dir, "concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            for cp in clip_paths:
                f.write(f"file '{os.path.abspath(cp)}'\n")

        # --- 7. クリップ結合 → silent.mp4 ---
        silent_mp4 = os.path.join(tmp_dir, "silent.mp4")
        print("  クリップ結合中...")
        run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_txt,
            "-c", "copy",
            silent_mp4,
        ])

        # --- 8. 音声結合 → output/video_N.mp4 ---
        print("  音声結合中...")
        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", silent_mp4,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ])

        size_mb = Path(output_path).stat().st_size / (1024 * 1024)
        print(f"  最終動画の生成完了: {output_path} ({size_mb:.1f} MB)")

    finally:
        # --- 9. tmp フォルダを削除 ---
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("  tmpフォルダを削除しました。")


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

    # assets フォルダの画像を収集
    assets_images = sorted(
        str(p) for p in Path(ASSETS_DIR).glob("*.jpg")
        if p.stat().st_size > 1000
    ) if Path(ASSETS_DIR).exists() else []
    print(f"  assets画像: {len(assets_images)} 枚")

    # フォント読み込み
    font_path = find_japanese_font()
    print(f"  日本語フォント: {font_path or '見つからず（デフォルト使用）'}")
    if font_path:
        try:
            font: ImageFont.ImageFont = ImageFont.truetype(font_path, FONT_SIZE)
        except Exception as e:
            print(f"  [警告] フォント読み込み失敗: {e}", file=sys.stderr)
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()

    # script_*.txt を処理
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
        print(f"\n--- 動画生成 [{idx}]: {item.get('title', '')[:50]} ---")

        build_video(script_file, audio_path, output_path, assets_images, font)

    video_files = list(Path(OUTPUT_DIR).glob("video_*.mp4"))
    print(f"\n{len(video_files)} 本の動画を生成しました。")


if __name__ == "__main__":
    main()
