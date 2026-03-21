#!/usr/bin/env python3
"""ffmpegで背景画像・音声・テキストオーバーレイを合成してYouTubeショート動画を生成する。"""

import json
import subprocess
import sys
from pathlib import Path

NEWS_JSON = "news.json"
BACKGROUND_IMG = "assets/background.jpg"
AUDIO_FILE = "output/audio.mp3"
OUTPUT_DIR = "output"
OUTPUT_VIDEO = f"{OUTPUT_DIR}/video.mp4"

# 動画設定（YouTubeショート縦型）
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

# テキスト設定
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_FALLBACK = "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
FONT_SIZE = 52
TEXT_COLOR = "white"
BORDER_COLOR = "black"
BORDER_WIDTH = 3
TEXT_Y_RATIO = 0.75  # 画面高さの75%位置（下寄り）


def find_font() -> str:
    for path in [FONT_PATH, FONT_FALLBACK]:
        if Path(path).exists():
            return path
    # システムフォント検索
    result = subprocess.run(
        ["fc-list", ":lang=ja", "--format=%{file}\n"],
        capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if line.strip():
            return line.strip()
    print("[警告] 日本語フォントが見つかりません。デフォルトフォントを使用します。", file=sys.stderr)
    return ""


def escape_drawtext(text: str) -> str:
    """ffmpeg drawtext用に特殊文字をエスケープする。"""
    replacements = [
        ("\\", "\\\\"),
        ("'", "\\'"),
        (":", "\\:"),
        ("[", "\\["),
        ("]", "\\]"),
        (",", "\\,"),
        (";", "\\;"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def wrap_text(text: str, max_chars: int = 18) -> str:
    """日本語テキストを指定文字数で折り返す。"""
    lines = []
    while len(text) > max_chars:
        lines.append(text[:max_chars])
        text = text[max_chars:]
    if text:
        lines.append(text)
    return "\n".join(lines)


def get_audio_duration(audio_path: str) -> float:
    """ffprobeで音声ファイルの長さを取得する。"""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            audio_path,
        ],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(result.stdout)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            return float(stream.get("duration", 60))
    return 60.0


def generate_video(title: str) -> None:
    font_path = find_font()
    text_y = int(VIDEO_HEIGHT * TEXT_Y_RATIO)

    wrapped_title = wrap_text(title, max_chars=18)
    escaped_title = escape_drawtext(wrapped_title)

    # drawtext フィルタ構築
    if font_path:
        drawtext = (
            f"drawtext=fontfile='{font_path}':"
            f"text='{escaped_title}':"
            f"fontsize={FONT_SIZE}:"
            f"fontcolor={TEXT_COLOR}:"
            f"borderw={BORDER_WIDTH}:"
            f"bordercolor={BORDER_COLOR}:"
            f"x=(w-text_w)/2:"
            f"y={text_y}:"
            f"line_spacing=10"
        )
    else:
        drawtext = (
            f"drawtext=text='{escaped_title}':"
            f"fontsize={FONT_SIZE}:"
            f"fontcolor={TEXT_COLOR}:"
            f"borderw={BORDER_WIDTH}:"
            f"bordercolor={BORDER_COLOR}:"
            f"x=(w-text_w)/2:"
            f"y={text_y}:"
            f"line_spacing=10"
        )

    # ffmpegコマンド構築
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", BACKGROUND_IMG,
        "-i", AUDIO_FILE,
        "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
               f"{drawtext}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        OUTPUT_VIDEO,
    ]

    print(f"ffmpeg で動画生成中...")
    print(f"  背景: {BACKGROUND_IMG}")
    print(f"  音声: {AUDIO_FILE}")
    print(f"  出力: {OUTPUT_VIDEO}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[エラー] ffmpeg 実行失敗:\n{result.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)

    size_mb = Path(OUTPUT_VIDEO).stat().st_size / (1024 * 1024)
    print(f"動画を {OUTPUT_VIDEO} に保存しました（{size_mb:.1f} MB）。")


def main() -> None:
    print("=== 動画生成開始 ===")

    # 入力ファイル確認
    for path in [BACKGROUND_IMG, AUDIO_FILE]:
        if not Path(path).exists():
            print(f"[エラー] {path} が見つかりません。", file=sys.stderr)
            sys.exit(1)

    news_path = Path(NEWS_JSON)
    if not news_path.exists():
        print(f"[エラー] {NEWS_JSON} が見つかりません。", file=sys.stderr)
        sys.exit(1)

    news_items: list[dict] = json.loads(news_path.read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のため動画生成をスキップします。")
        sys.exit(0)

    title = news_items[0]["title"]
    print(f"メインタイトル: {title}")

    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    generate_video(title)


if __name__ == "__main__":
    main()
