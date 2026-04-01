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
import random
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
BGM_DIR = f"{ASSETS_DIR}/bgm"
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
FONT_SIZE = 64
ENDING_DURATION = 4.0   # エンディングカード表示秒数
THUMBNAIL_DURATION = 1.5  # 先頭サムネイルフレーム表示秒数
BGM_VOLUME = 0.12       # BGM音量（ナレーションに対する比率）
SUBTITLE_MAX_WIDTH_RATIO = 0.85   # 字幕の最大横幅割合
LINE_SPACING = 14
SUBTITLE_CENTER_Y = 920       # 字幕パネルの中心Y座標（画面中央寄り）
SUBTITLE_PANEL_PADDING_V = 36  # パネル上下パディング
SUBTITLE_PANEL_PADDING_H = 48  # パネル左右パディング
ACCENT_LINE_H = 6              # ゴールドアクセントラインの太さ
ACCENT_COLOR = (255, 195, 40)  # ゴールド
PANEL_BG_COLOR = (10, 10, 20, 200)  # 半透明ダークパネル
SHADOW_OFFSET = (3, 4)         # ドロップシャドウのオフセット
OUTLINE_WIDTH = 3
MIN_CUT_DURATION = 1.5


# ---------------------------------------------------------------------------
# 背景画像自動生成（Pillow）
# ---------------------------------------------------------------------------

def _add_watermark(img: Image.Image, font_path: str | None) -> Image.Image:
    draw = ImageDraw.Draw(img)
    try:
        wm_font = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default()
    except Exception:
        wm_font = ImageFont.load_default()
    text = "競馬速報"
    try:
        bbox = draw.textbbox((0, 0), text, font=wm_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = 60, 30
    draw.text((VIDEO_WIDTH - tw - 20, VIDEO_HEIGHT - th - 20), text, font=wm_font, fill=(255, 255, 255))
    return img


def generate_bg_images() -> None:
    """assetsに競馬らしい背景画像5種をPillowで生成して保存する。"""
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    W, H = VIDEO_WIDTH, VIDEO_HEIGHT
    font_path = find_japanese_font()

    # --- 画像1: 夕暮れの競馬場 ---
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = int(180 + (80 - 180) * y / H)
        g = int(80 + (20 - 80) * y / H)
        b = int(20 + (60 - 20) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    sun = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sun).ellipse([340, 200, 740, 600], fill=(255, 180, 50, 180))
    img = Image.alpha_composite(img.convert("RGBA"), sun).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.line([(0, 1400), (W, 1400)], fill=(100, 60, 20), width=8)
    draw.rectangle([(0, 1400), (W, H)], fill=(30, 80, 30))
    _add_watermark(img, font_path).save(f"{ASSETS_DIR}/bg_1.jpg", "JPEG", quality=90)
    print("  bg_1.jpg 生成完了（夕暮れの競馬場）")

    # --- 画像2: 夜の競馬場（ナイター）---
    img = Image.new("RGB", (W, H), (10, 10, 40))
    lights = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lights)
    for lx in [200, 540, 880]:
        ld.ellipse([lx - 150, -100, lx + 150, 200], fill=(255, 255, 255, 60))
    img = Image.alpha_composite(img.convert("RGBA"), lights).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.ellipse([80, 600, W - 80, 1600], outline=(200, 180, 100), width=6)
    draw.rectangle([(0, 1600), (W, H)], fill=(20, 60, 20))
    _add_watermark(img, font_path).save(f"{ASSETS_DIR}/bg_2.jpg", "JPEG", quality=90)
    print("  bg_2.jpg 生成完了（夜の競馬場）")

    # --- 画像3: 青空の競馬場 ---
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = int(50 + (150 - 50) * y / H)
        g = int(130 + (210 - 130) * y / H)
        b = int(220 + (255 - 220) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    cloud = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(cloud)
    for cx, cy in [(150, 200), (400, 150), (700, 250), (900, 180)]:
        for ox, oy in [(0, 0), (50, -20), (100, 0), (25, -40), (75, -40)]:
            cd.ellipse([cx + ox - 60, cy + oy - 40, cx + ox + 60, cy + oy + 40], fill=(255, 255, 255, 220))
    img = Image.alpha_composite(img.convert("RGBA"), cloud).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 1400), (W, H)], fill=(20, 120, 40))
    for fy in [1420, 1470, 1520]:
        draw.line([(50, fy), (W - 50, fy)], fill=(255, 255, 255), width=4)
    _add_watermark(img, font_path).save(f"{ASSETS_DIR}/bg_3.jpg", "JPEG", quality=90)
    print("  bg_3.jpg 生成完了（青空の競馬場）")

    # --- 画像4: ゴール前の興奮 ---
    img = Image.new("RGB", (W, H), (15, 60, 15))
    draw = ImageDraw.Draw(img)
    random.seed(42)
    for _ in range(2000):
        px = random.randint(0, W)
        py = random.randint(0, 400)
        draw.ellipse([px - 3, py - 3, px + 3, py + 3],
                     fill=(random.randint(100, 255), random.randint(50, 200), random.randint(50, 200)))
    lights4 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld4 = ImageDraw.Draw(lights4)
    for lx, ly in [(0, 0), (W, 0), (0, H), (W, H)]:
        ld4.ellipse([lx - 300, ly - 300, lx + 300, ly + 300], fill=(255, 255, 200, 40))
    img = Image.alpha_composite(img.convert("RGBA"), lights4).convert("RGB")
    draw = ImageDraw.Draw(img)
    for px in [350, 730]:
        draw.line([(px, 700), (px, 1300)], fill=(255, 255, 255), width=10)
    draw.line([(350, 700), (730, 700)], fill=(255, 255, 255), width=10)
    _add_watermark(img, font_path).save(f"{ASSETS_DIR}/bg_4.jpg", "JPEG", quality=90)
    print("  bg_4.jpg 生成完了（ゴール前の興奮）")

    # --- 画像5: 朝の調教 ---
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = 255
        g = int(200 + (240 - 200) * y / H)
        b = int(100 + (180 - 100) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    fog = Image.new("RGBA", (W, H), (255, 255, 255, 60))
    img = Image.alpha_composite(img.convert("RGBA"), fog).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 1400), (W, H)], fill=(40, 100, 40))
    for tx in [150, 540, 900]:
        draw.rectangle([tx - 15, 1100, tx + 15, 1400], fill=(30, 20, 10))
        draw.ellipse([tx - 80, 950, tx + 80, 1120], fill=(20, 40, 20))
    _add_watermark(img, font_path).save(f"{ASSETS_DIR}/bg_5.jpg", "JPEG", quality=90)
    print("  bg_5.jpg 生成完了（朝の調教）")

    print(f"  背景画像5枚を {ASSETS_DIR}/ に生成しました。")


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
        print(f"  背景画像読み込み: {path}")
        try:
            img = Image.open(path).convert("RGB")
            img = ImageOps.fit(img, (VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)
            print(f"  背景画像読み込み成功: {path} → {img.size}")
            return img
        except Exception as e:
            import traceback
            print(f"  [警告] 画像読み込み失敗 ({path}): {e}")
            traceback.print_exc()
    # 単色背景（濃紺）
    print("  単色背景（濃紺）を使用します。")
    return Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (15, 15, 40))


def calc_max_chars_per_line(font: ImageFont.ImageFont) -> int:
    """フォントサイズから1行の最大文字数を動的に計算する。"""
    max_width = VIDEO_WIDTH * SUBTITLE_MAX_WIDTH_RATIO
    try:
        char_width = font.getlength("あ")
    except AttributeError:
        # Pillow 8未満のフォールバック
        char_width = FONT_SIZE
    chars = max(1, int(max_width / char_width))
    print(f"  字幕1行最大文字数: {chars}（1文字幅={char_width:.1f}px, 最大幅={max_width:.0f}px）")
    return chars


def make_frame(text: str, assets_images: list[str], index: int, font: ImageFont.ImageFont) -> Image.Image:
    """字幕付きフレーム画像を生成して返す。下部テロップパネルスタイル。"""
    bg = load_background(assets_images, index)

    # 軽めのグローバルオーバーレイ（背景を活かす）
    overlay = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 55))
    img = bg.convert("RGBA")
    img = Image.alpha_composite(img, overlay)

    # テキスト折り返し
    max_chars = calc_max_chars_per_line(font)
    lines = textwrap.wrap(text, width=max_chars)
    if not lines:
        lines = [text]

    line_height = FONT_SIZE + LINE_SPACING
    total_text_h = len(lines) * line_height

    # 字幕エリアを画面下部に配置
    panel_inner_h = total_text_h + SUBTITLE_PANEL_PADDING_V * 2
    panel_total_h = panel_inner_h + ACCENT_LINE_H
    panel_top = SUBTITLE_CENTER_Y - panel_total_h // 2

    # --- ダークパネル + グラデーション下辺ぼかし ---
    panel = Image.new("RGBA", (VIDEO_WIDTH, panel_total_h), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)

    # ゴールドアクセントライン（パネル上端）
    panel_draw.rectangle(
        [0, 0, VIDEO_WIDTH, ACCENT_LINE_H],
        fill=(*ACCENT_COLOR, 255),
    )
    # 半透明ダークパネル本体
    panel_draw.rectangle(
        [0, ACCENT_LINE_H, VIDEO_WIDTH, panel_total_h],
        fill=PANEL_BG_COLOR,
    )
    img = Image.alpha_composite(img, Image.new("RGBA", img.size, (0, 0, 0, 0)))
    img.paste(panel, (0, panel_top), panel)

    draw = ImageDraw.Draw(img)

    # テキスト描画（ドロップシャドウ + 薄い縁取り）
    text_start_y = panel_top + ACCENT_LINE_H + SUBTITLE_PANEL_PADDING_V
    for i, line in enumerate(lines):
        y = text_start_y + i * line_height
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(line) * (FONT_SIZE // 2)
        x = max((VIDEO_WIDTH - text_w) // 2, SUBTITLE_PANEL_PADDING_H)

        # ドロップシャドウ
        sx, sy = SHADOW_OFFSET
        draw.text(
            (x + sx, y + sy), line, font=font,
            fill=(0, 0, 0, 160),
        )
        # メインテキスト（白 + 細い縁取り）
        try:
            draw.text(
                (x, y), line, font=font,
                fill=(255, 255, 255),
                stroke_width=OUTLINE_WIDTH,
                stroke_fill=(0, 0, 30),
            )
        except TypeError:
            for dx in (-OUTLINE_WIDTH, 0, OUTLINE_WIDTH):
                for dy in (-OUTLINE_WIDTH, 0, OUTLINE_WIDTH):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 30))
            draw.text((x, y), line, font=font, fill=(255, 255, 255))

    return img.convert("RGB")


# ---------------------------------------------------------------------------
# エンディングカード生成
# ---------------------------------------------------------------------------

def make_thumbnail_frame(title: str, assets_images: list[str], index: int, font_path: str | None) -> Image.Image:
    """動画先頭に挿入するサムネイルフレーム（Shorts用縦型1080x1920）を生成する。
    YouTubeがこのフレームを選択肢として認識し、サムネイルとして使えるようになる。"""
    bg = load_background(assets_images, index)

    overlay = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 150))
    img = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    def load_font(size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()

    # 「競馬速報」赤バッジ（左上）
    badge_font = load_font(56)
    badge_text = "競馬速報"
    pad = 22
    try:
        bb = draw.textbbox((0, 0), badge_text, font=badge_font)
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    except Exception:
        bw, bh = 220, 64
    draw.rounded_rectangle(
        [44, 70, 44 + bw + pad * 2, 70 + bh + pad],
        radius=14, fill=(210, 30, 30),
    )
    draw.text((44 + pad, 70 + pad // 2), badge_text, font=badge_font,
              fill=(255, 255, 255), stroke_width=2, stroke_fill=(150, 0, 0))

    # タイトルテキスト（中央・黄色）
    import re
    clean_title = re.sub(r"[\u3000\s]+", " ", title).strip()
    title_font = load_font(100)
    lines = textwrap.wrap(clean_title, width=10)[:4]
    line_h = 120
    total_h = len(lines) * line_h
    start_y = (VIDEO_HEIGHT - total_h) // 2 - 80

    for i, line in enumerate(lines):
        try:
            bb = draw.textbbox((0, 0), line, font=title_font)
            tw = bb[2] - bb[0]
        except Exception:
            tw = len(line) * 60
        x = max((VIDEO_WIDTH - tw) // 2, 20)
        y = start_y + i * line_h
        draw.text((x, y), line, font=title_font,
                  fill=(255, 235, 0), stroke_width=7, stroke_fill=(0, 0, 0))

    return img


def make_ending_frame(font_path: str | None) -> Image.Image:
    """チャンネル登録促進のエンディングカード画像を生成する。"""
    img = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT))
    draw_bg = ImageDraw.Draw(img)
    for y in range(VIDEO_HEIGHT):
        r = int(5  + 15  * y / VIDEO_HEIGHT)
        g = int(5  + 10  * y / VIDEO_HEIGHT)
        b = int(30 + 40  * y / VIDEO_HEIGHT)
        draw_bg.line([(0, y), (VIDEO_WIDTH, y)], fill=(r, g, b))

    draw = ImageDraw.Draw(img)

    def load_font(size):
        try:
            return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()

    rows = [
        ("チャンネル登録", load_font(110), (255, 215, 0)),
        ("よろしく！",     load_font(120), (255, 215, 0)),
        ("",               None,            None),
        ("毎日 8:00〜20:00",   load_font(52), (200, 230, 255)),
        ("2時間おきに投稿中！", load_font(52), (200, 230, 255)),
    ]

    line_heights = []
    for text, font, _ in rows:
        if font is None:
            line_heights.append(40)
            continue
        try:
            bb = draw.textbbox((0, 0), text, font=font)
            line_heights.append(bb[3] - bb[1] + 24)
        except Exception:
            line_heights.append(80)

    total_h = sum(line_heights)
    y = (VIDEO_HEIGHT - total_h) // 2

    for (text, font, color), lh in zip(rows, line_heights):
        if font is None:
            y += lh
            continue
        try:
            bb = draw.textbbox((0, 0), text, font=font)
            tw = bb[2] - bb[0]
        except Exception:
            tw = len(text) * 55
        x = max((VIDEO_WIDTH - tw) // 2, 20)
        draw.text((x, y), text, font=font, fill=color, stroke_width=5, stroke_fill=(0, 0, 0))
        y += lh

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
    title: str = "",
) -> None:
    script = script_path.read_text(encoding="utf-8").strip()
    raw = [s.strip() for s in script.split("。") if s.strip()]
    # 句点（。）で区切った一文をそのまま一画面に表示する
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
        # --- サムネイルフレームを先頭クリップとして生成 ---
        clip_paths: list[str] = []

        if title:
            font_path_for_thumb = find_japanese_font()
            thumb_frame = make_thumbnail_frame(title, assets_images, 0, font_path_for_thumb)
            thumb_frame_path = os.path.join(tmp_dir, "frame_thumb.png")
            thumb_frame.save(thumb_frame_path, "PNG")
            thumb_clip_path = os.path.join(tmp_dir, "clip_thumb.mp4")
            run_ffmpeg([
                "ffmpeg", "-y",
                "-loop", "1", "-i", thumb_frame_path,
                "-t", str(THUMBNAIL_DURATION),
                "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-r", str(FPS),
                thumb_clip_path,
            ])
            clip_paths.append(thumb_clip_path)
            print(f"  サムネイルフレーム生成完了: {THUMBNAIL_DURATION}秒")

        # --- 4. 字幕フレーム画像生成 ---
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
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-r", str(FPS),
                clip_path,
            ])
            clip_paths.append(clip_path)
            print(f"  クリップ生成完了: clip_{i}.mp4 ({duration:.2f}秒)")

        # --- エンディングカードクリップ追加 ---
        font_path_for_ending = find_japanese_font()
        ending_frame = make_ending_frame(font_path_for_ending)
        ending_frame_path = os.path.join(tmp_dir, "frame_ending.png")
        ending_frame.save(ending_frame_path, "PNG")
        ending_clip_path = os.path.join(tmp_dir, "clip_ending.mp4")
        run_ffmpeg([
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", ending_frame_path,
            "-t", str(ENDING_DURATION),
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-r", str(FPS),
            ending_clip_path,
        ])
        clip_paths.append(ending_clip_path)
        print(f"  エンディングカード追加: {ENDING_DURATION}秒")

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

        # --- 8. 音声結合（BGMミックス対応） → output/video_N.mp4 ---
        print("  音声結合中...")
        # 実際の映像尺（サムネイルフレーム＋MIN_CUT_DURATION補正後の合計）に合わせる
        thumb_offset = THUMBNAIL_DURATION if title else 0.0
        total_duration = thumb_offset + sum(durations) + ENDING_DURATION
        # ナレーション遅延（ms）: サムネイルフレーム分だけずらす
        narr_delay_ms = int(thumb_offset * 1000)
        bgm_files = sorted(glob.glob(f"{BGM_DIR}/*.mp3") + glob.glob(f"{BGM_DIR}/*.m4a"))
        bgm_path = random.choice(bgm_files) if bgm_files else None
        if bgm_path:
            print(f"  BGM使用: {Path(bgm_path).name}")
            if narr_delay_ms > 0:
                narr_filter = f"[1:a]adelay={narr_delay_ms}|{narr_delay_ms},apad=whole_dur={total_duration:.3f}[narr]"
            else:
                narr_filter = f"[1:a]apad=whole_dur={total_duration:.3f}[narr]"
            run_ffmpeg([
                "ffmpeg", "-y",
                "-i", silent_mp4,
                "-i", audio_path,
                "-stream_loop", "-1", "-i", bgm_path,
                "-filter_complex",
                f"{narr_filter};[narr][2:a]amix=inputs=2:duration=first:weights=1 {BGM_VOLUME}[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                output_path,
            ])
        else:
            print("  BGMなし（assets/bgm/ に .mp3 を置くと自動適用されます）")
            if narr_delay_ms > 0:
                af_filter = f"adelay={narr_delay_ms}|{narr_delay_ms},apad=whole_dur={total_duration:.3f}"
            else:
                af_filter = f"apad=whole_dur={total_duration:.3f}"
            run_ffmpeg([
                "ffmpeg", "-y",
                "-i", silent_mp4,
                "-i", audio_path,
                "-af", af_filter,
                "-c:v", "copy",
                "-c:a", "aac",
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

    # 1. AI生成画像（ai_*.jpg）を優先して収集
    ai_images = sorted(
        p for p in glob.glob(f"{ASSETS_DIR}/ai_*.jpg")
        if Path(p).stat().st_size > 1000
    )
    if ai_images:
        assets_images = ai_images
        print(f"  AI画像を使用 ({len(assets_images)}枚): {[Path(p).name for p in assets_images]}")
    else:
        # 2. assets内の全jpg/pngを使用
        all_images = sorted(
            p for p in (
                glob.glob(f"{ASSETS_DIR}/*.jpg") + glob.glob(f"{ASSETS_DIR}/*.png")
            )
            if Path(p).stat().st_size > 1000
        )
        if all_images:
            assets_images = all_images
            print(f"  assets画像を使用 ({len(assets_images)}枚): {[Path(p).name for p in assets_images]}")
        else:
            # 3. 画像なし → Pillowでグラデーション背景を自動生成
            print("  assetsに画像がないため、背景画像を自動生成します。")
            generate_bg_images()
            assets_images = sorted(
                p for p in glob.glob(f"{ASSETS_DIR}/*.jpg")
                if Path(p).stat().st_size > 1000
            )
            print(f"  生成画像を使用 ({len(assets_images)}枚): {[Path(p).name for p in assets_images]}")

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
        title = item.get("title", "")
        print(f"\n--- 動画生成 [{idx}]: {title[:50]} ---")

        build_video(script_file, audio_path, output_path, assets_images, font, title=title)

    video_files = list(Path(OUTPUT_DIR).glob("video_*.mp4"))
    print(f"\n{len(video_files)} 本の動画を生成しました。")


if __name__ == "__main__":
    main()
