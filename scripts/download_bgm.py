#!/usr/bin/env python3
"""
フリーBGMダウンロードスクリプト
Pixabay 動画API（既存キー）で音楽動画を検索 → ffmpegで音声抽出してBGMを生成する。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

BGM_DIR = "assets/bgm"
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")

# BGM種別ごとの検索クエリ
BGM_QUERIES = [
    ("bgm_1", "upbeat happy background music"),
    ("bgm_2", "calm relaxing piano music"),
    ("bgm_3", "exciting sport action music"),
]


def search_pixabay_videos(query: str, api_key: str) -> list[dict]:
    """Pixabay動画APIで検索し、ヒットリストを返す。"""
    url = (
        "https://pixabay.com/api/videos/"
        f"?key={api_key}"
        f"&q={urllib.parse.quote(query)}"
        "&per_page=5"
        "&video_type=film"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read()).get("hits", [])
    except Exception as e:
        print(f"  [警告] Pixabay動画検索失敗: {e}")
        return []


def download_file(url: str, dest: str, timeout: int = 120) -> bool:
    """URLからファイルをダウンロード。成功でTrue。"""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; keiba-bgm/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 10_000:
            print(f"  [警告] ファイルが小さすぎます ({len(data)} bytes)")
            return False
        Path(dest).write_bytes(data)
        print(f"  ダウンロード完了: {Path(dest).name} ({len(data)//1024} KB)")
        return True
    except Exception as e:
        print(f"  [警告] ダウンロード失敗: {e}")
        return False


def extract_audio(video_path: str, mp3_path: str, duration: int = 90) -> bool:
    """動画ファイルから音声を抽出してMP3に変換する（最大duration秒）。"""
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-t", str(duration),
            "-vn",
            "-af", (f"afade=t=in:st=0:d=3,"
                    f"afade=t=out:st={duration-3}:d=3,"
                    f"loudnorm=I=-16:TP=-1.5:LRA=11,"
                    f"volume=0.85"),
            "-c:a", "libmp3lame", "-b:a", "128k", "-ac", "2",
            mp3_path,
        ], check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [警告] 音声抽出失敗: {e.stderr.decode()[:300]}")
        return False


def get_video_url(hit: dict) -> str:
    """Pixabayのヒットから最も軽いビデオURLを取得する。"""
    videos = hit.get("videos", {})
    for quality in ("tiny", "small", "medium", "large"):
        v = videos.get(quality, {})
        url = v.get("url", "")
        if url:
            return url
    return ""


def main():
    if not PIXABAY_API_KEY:
        print("[エラー] PIXABAY_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(1)

    Path(BGM_DIR).mkdir(parents=True, exist_ok=True)

    success = 0
    for name, query in BGM_QUERIES:
        dest_mp3 = f"{BGM_DIR}/{name}.mp3"
        if Path(dest_mp3).exists():
            print(f"{name}.mp3 は既存のためスキップ")
            success += 1
            continue

        print(f"\n--- {name}: 「{query}」で検索 ---")
        hits = search_pixabay_videos(query, PIXABAY_API_KEY)

        downloaded = False
        for hit in hits:
            video_url = get_video_url(hit)
            if not video_url:
                continue
            title = hit.get("tags", "unknown")[:40]
            print(f"  候補: {title}")
            tmp_video = f"{BGM_DIR}/{name}_tmp.mp4"
            if download_file(video_url, tmp_video):
                if extract_audio(tmp_video, dest_mp3):
                    Path(tmp_video).unlink(missing_ok=True)
                    print(f"  {name}.mp3 → 保存完了")
                    downloaded = True
                    success += 1
                    break
                else:
                    Path(tmp_video).unlink(missing_ok=True)

        if not downloaded:
            print(f"  [エラー] {name} のBGM取得に失敗しました。")

        time.sleep(1)

    print(f"\n=== BGM取得完了: {success}/{len(BGM_QUERIES)} 件 ===")
    if success == 0:
        print("[エラー] BGMを1件も取得できませんでした。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
