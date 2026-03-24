#!/usr/bin/env python3
"""
output/audio_N.mp3 + subtitles_N.ass から動画を生成する。
- 画像あり: ゆっくりパン(Ken Burns風) + ASS字幕
- 画像なし: ダークネイビー背景 + ASS字幕
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.error import URLError

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}

# フォールバック背景色（ダークネイビー）
FALLBACK_BG_COLOR = "0x0a1628"


def download_image(url: str, dest: str) -> bool:
    if not url:
        return False
    if re.search(r"google\.com|googleusercontent\.com|gstatic\.com", url, re.I):
        print(f"  [スキップ] Google画像を除外")
        return False
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                return False
            data = resp.read()
            if len(data) < 1000:
                return False
            Path(dest).write_bytes(data)
            print(f"  画像ダウンロード: {len(data) // 1024} KB")
            return True
    except Exception as e:
        print(f"  [警告] 画像ダウンロード失敗: {e}", file=sys.stderr)
    return False


def verify_image(path: str) -> bool:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True, text=True, timeout=10,
        )
        info = json.loads(result.stdout)
        return any(s.get("codec_type") == "video" for s in info.get("streams", []))
    except Exception:
        return False


def get_audio_duration(audio_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True, timeout=10,
    )
    return float(result.stdout.strip())


def run_ffmpeg(cmd: list[str], output_path: str) -> None:
    print(f"  ffmpeg 実行中...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[エラー] ffmpeg 失敗:\n{result.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"  → {output_path} ({size_mb:.1f} MB)")


def generate_video(
    audio_path: str,
    ass_path: str,
    image_path: str | None,
    output_path: str,
    duration: float,
) -> None:
    """
    Ken Burnsパン + ASS字幕で動画を生成する。

    背景画像がある場合:
      - 110%スケールして、時間に応じてゆっくり横パン（Ken Burns風）
      - 下部30%に半透明の暗いバーを重ねて字幕を読みやすくする
    画像がない場合:
      - ダークネイビーの単色背景
    """
    abs_ass = str(Path(ass_path).resolve())
    # パス中のコロン・バックスラッシュをエスケープ（Windows対策は不要だがffmpeg解析対策）
    abs_ass_escaped = abs_ass.replace("\\", "/").replace(":", "\\:")

    if image_path:
        # 110%スケール → 横方向にゆっくりパン（Ken Burns風）
        pan_range_x = int(VIDEO_WIDTH * 0.1)   # 108px 横移動
        pan_range_y = int(VIDEO_HEIGHT * 0.1)  # 192px 縦移動
        scaled_w = VIDEO_WIDTH + pan_range_x
        scaled_h = VIDEO_HEIGHT + pan_range_y

        vf = (
            f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}"
            f":x='{pan_range_x}*t/{duration:.3f}'"
            f":y='{pan_range_y//2}*t/{duration:.3f}',"
            f"drawbox=x=0:y={int(VIDEO_HEIGHT * 0.70)}:w={VIDEO_WIDTH}"
            f":h={int(VIDEO_HEIGHT * 0.30)}:color=black@0.60:t=fill,"
            f"ass={abs_ass_escaped}"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-i", audio_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(duration + 0.5),
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        # フォールバック: ダークネイビー背景
        vf = (
            f"drawbox=x=0:y={int(VIDEO_HEIGHT * 0.70)}:w={VIDEO_WIDTH}"
            f":h={int(VIDEO_HEIGHT * 0.30)}:color=black@0.30:t=fill,"
            f"ass={abs_ass_escaped}"
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c={FALLBACK_BG_COLOR}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FPS}",
            "-i", audio_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(duration + 0.5),
            "-movflags", "+faststart",
            output_path,
        ]

    run_ffmpeg(cmd, output_path)


def main() -> None:
    print("=== 動画生成開始 ===")

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のため動画生成をスキップします。")
        sys.exit(0)

    audio_files = sorted(Path(OUTPUT_DIR).glob("audio_*.mp3"))
    if not audio_files:
        print(f"[エラー] {OUTPUT_DIR}/audio_*.mp3 が見つかりません。", file=sys.stderr)
        sys.exit(1)

    for audio_file in audio_files:
        idx = int(audio_file.stem.split("_")[1])
        ass_path = f"{OUTPUT_DIR}/subtitles_{idx}.ass"
        output_path = f"{OUTPUT_DIR}/video_{idx}.mp4"

        if idx >= len(news_items):
            print(f"  [警告] インデックス {idx} の記事がありません。スキップします。")
            continue

        if not Path(ass_path).exists():
            print(f"  [警告] {ass_path} が見つかりません。スキップします。")
            continue

        item = news_items[idx]
        title = item["title"]
        image_url = item.get("image_url", "")

        print(f"\n--- 動画生成 [{idx}]: {title[:50]} ---")
        print(f"  画像URL: {image_url[:70] or '（なし）'}")

        duration = get_audio_duration(str(audio_file))
        print(f"  音声長: {duration:.1f}秒")

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            downloaded = download_image(image_url, tmp_path)
            if downloaded and verify_image(tmp_path):
                print(f"  OG画像を背景に使用します。")
                generate_video(str(audio_file), ass_path, tmp_path, output_path, duration)
            else:
                print(f"  フォールバック背景（ダークネイビー）を使用します。")
                generate_video(str(audio_file), ass_path, None, output_path, duration)
        finally:
            if Path(tmp_path).exists():
                os.unlink(tmp_path)

    video_files = sorted(Path(OUTPUT_DIR).glob("video_*.mp4"))
    print(f"\n{len(video_files)} 本の動画を生成しました。")


if __name__ == "__main__":
    main()
