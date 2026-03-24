#!/usr/bin/env python3
"""
記事のOG画像を背景に使い、ffmpegでYouTubeショート動画を生成する。
OG画像が取得できない場合はグラデーション背景にフォールバックする。
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.error import URLError

NEWS_JSON = "news.json"
AUDIO_FILE = "output/audio.mp3"
OUTPUT_DIR = "output"
OUTPUT_VIDEO = f"{OUTPUT_DIR}/video.mp4"

# 動画設定（YouTubeショート縦型）
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

# テキスト設定
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]
FONT_SIZE = 52
TEXT_COLOR = "white"
BORDER_COLOR = "black"
BORDER_WIDTH = 3
TEXT_Y_RATIO = 0.76  # 画面高さの76%位置（暗いバーの中央）
OVERLAY_Y_RATIO = 0.60  # 下部オーバーレイの開始位置

# フォールバック背景色（競馬場グリーン）
FALLBACK_COLOR = "0x1a3a1a"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KeibaBot/1.0)"}


def find_font() -> str:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    try:
        result = subprocess.run(
            ["fc-list", ":lang=ja", "--format=%{file}\n"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            f = line.strip()
            if f and Path(f).exists():
                return f
    except Exception:
        pass
    print("[警告] 日本語フォントが見つかりません。", file=sys.stderr)
    return ""


def download_image(url: str, dest: str) -> bool:
    """画像URLをダウンロードしてdestに保存。成功したらTrueを返す。"""
    if not url:
        return False
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                print(f"  [警告] 画像ではないレスポンス: {content_type}", file=sys.stderr)
                return False
            data = resp.read()
            if len(data) < 1000:
                print(f"  [警告] ダウンロードデータが小さすぎます ({len(data)} bytes)", file=sys.stderr)
                return False
            Path(dest).write_bytes(data)
            print(f"  画像ダウンロード完了: {len(data) // 1024} KB")
            return True
    except URLError as e:
        print(f"  [警告] 画像ダウンロード失敗 ({url[:60]}): {e}", file=sys.stderr)
    except Exception as e:
        print(f"  [警告] 画像ダウンロード失敗: {e}", file=sys.stderr)
    return False


def verify_image(path: str) -> bool:
    """ffprobeで画像ファイルが有効か確認する。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", path],
            capture_output=True, text=True, timeout=10,
        )
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                return True
    except Exception:
        pass
    return False


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


def build_video_filter(font_path: str, title: str, has_image: bool) -> str:
    """ffmpeg -vf フィルター文字列を構築する。"""
    text_y = int(VIDEO_HEIGHT * TEXT_Y_RATIO)
    overlay_y = int(VIDEO_HEIGHT * OVERLAY_Y_RATIO)
    overlay_h = VIDEO_HEIGHT - overlay_y

    wrapped_title = wrap_text(title, max_chars=18)
    escaped_title = escape_drawtext(wrapped_title)

    filters = []

    if has_image:
        # 画像をショート縦型にリサイズ・クロップ
        filters.append(
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}"
        )
        # 全体に微妙な暗さを加えて文字を読みやすくする
        filters.append("eq=brightness=-0.05:saturation=1.1")
    else:
        # フォールバック: 単色背景 + ffmpegで生成済みなので変換のみ
        filters.append(f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}")

    # テキスト背景用の半透明グラデーションボックス
    filters.append(
        f"drawbox=x=0:y={overlay_y}:w={VIDEO_WIDTH}:h={overlay_h}"
        f":color=black@0.65:t=fill"
    )

    # タイトルテキスト
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
            f"drawtext="
            f"text='{escaped_title}':"
            f"fontsize={FONT_SIZE}:"
            f"fontcolor={TEXT_COLOR}:"
            f"borderw={BORDER_WIDTH}:"
            f"bordercolor={BORDER_COLOR}:"
            f"x=(w-text_w)/2:"
            f"y={text_y}:"
            f"line_spacing=10"
        )
    filters.append(drawtext)

    return ",".join(filters)


def generate_video_from_image(image_path: str, title: str, font_path: str) -> None:
    """ダウンロード済み画像を背景に動画を生成する。"""
    vf = build_video_filter(font_path, title, has_image=True)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-i", AUDIO_FILE,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        OUTPUT_VIDEO,
    ]
    _run_ffmpeg(cmd)


def generate_video_fallback(title: str, font_path: str) -> None:
    """OG画像なしのフォールバック：グラデーション背景で動画を生成する。"""
    print("  フォールバック: グラデーション背景を生成します。")
    vf = build_video_filter(font_path, title, has_image=False)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={FALLBACK_COLOR}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30",
        "-i", AUDIO_FILE,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        OUTPUT_VIDEO,
    ]
    _run_ffmpeg(cmd)


def _run_ffmpeg(cmd: list[str]) -> None:
    print(f"ffmpeg 実行中...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[エラー] ffmpeg 失敗:\n{result.stderr[-3000:]}", file=sys.stderr)
        sys.exit(1)
    size_mb = Path(OUTPUT_VIDEO).stat().st_size / (1024 * 1024)
    print(f"動画を {OUTPUT_VIDEO} に保存しました（{size_mb:.1f} MB）。")


def main() -> None:
    print("=== 動画生成開始 ===")

    for path in [AUDIO_FILE, NEWS_JSON]:
        if not Path(path).exists():
            print(f"[エラー] {path} が見つかりません。", file=sys.stderr)
            sys.exit(1)

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のため動画生成をスキップします。")
        sys.exit(0)

    title = news_items[0]["title"]
    image_url = news_items[0].get("image_url", "")
    print(f"タイトル: {title}")
    print(f"画像URL: {image_url or '（なし）'}")

    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    font_path = find_font()
    print(f"フォント: {font_path or '（システムデフォルト）'}")

    # 画像をダウンロードして動画生成
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        downloaded = download_image(image_url, tmp_path)
        if downloaded:
            if verify_image(tmp_path):
                print(f"  OG画像を背景として使用します。")
                generate_video_from_image(tmp_path, title, font_path)
            else:
                print(f"  [警告] ダウンロードした画像が無効です。フォールバックします。", file=sys.stderr)
                generate_video_fallback(title, font_path)
        else:
            generate_video_fallback(title, font_path)
    finally:
        if Path(tmp_path).exists():
            os.unlink(tmp_path)


if __name__ == "__main__":
    main()
