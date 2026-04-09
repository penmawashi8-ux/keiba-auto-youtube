#!/usr/bin/env python3
"""名馬シリーズ専用 BGMダウンロードスクリプト

ニュース速報のアップビート/スポーツ系とは異なり、
ドラマチック・ノスタルジック系のBGMを archive.org から取得する。
assets/bgm/horse_drama_bgm.mp3 に保存する。
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BGM_DIR  = "assets/bgm"
BGM_NAME = "horse_drama_bgm"

# ドラマチック・ノスタルジック系の検索クエリ（優先順）
BGM_SEARCHES = [
    "dramatic orchestral instrumental royalty free",
    "nostalgic piano melody instrumental",
    "cinematic background music calm",
    "classical piano solo instrumental",
]


def search_archive(query: str, rows: int = 10) -> list[str]:
    params = urllib.parse.urlencode({
        "q": f"({query}) AND mediatype:audio AND format:MP3",
        "fl": "identifier",
        "output": "json",
        "rows": rows,
        "sort": "downloads desc",
    })
    url = f"https://archive.org/advancedsearch.php?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        ids = [doc["identifier"] for doc in data.get("response", {}).get("docs", [])]
        print(f"  検索結果: {len(ids)} 件")
        return ids
    except Exception as e:
        print(f"  [警告] archive.org 検索失敗: {e}")
        return []


def get_mp3_files(identifier: str) -> list[str]:
    url = f"https://archive.org/metadata/{identifier}/files"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        files = data.get("result", [])
        mp3s = [
            f"https://archive.org/download/{identifier}/{urllib.parse.quote(f['name'])}"
            for f in files
            if f.get("name", "").lower().endswith(".mp3")
            and 100_000 < int(f.get("size", 0)) < 30_000_000
        ]
        return mp3s
    except Exception as e:
        print(f"  [警告] ファイル一覧取得失敗 ({identifier}): {e}")
        return []


def download_and_normalize(url: str, dest: str, timeout: int = 120) -> bool:
    tmp = dest + ".tmp.mp3"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 50_000:
            print(f"  [警告] ファイルが小さすぎます ({len(data)} bytes)")
            return False
        Path(tmp).write_bytes(data)
        print(f"  ダウンロード完了: {len(data)//1024} KB")
    except Exception as e:
        print(f"  [警告] ダウンロード失敗: {e}")
        Path(tmp).unlink(missing_ok=True)
        return False

    # 正規化（loudnorm + 90秒にトリム）
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", tmp,
            "-t", "90",
            "-af", "loudnorm=I=-18:TP=-1.5:LRA=11,volume=0.80",
            "-c:a", "libmp3lame", "-b:a", "128k", "-ac", "2",
            dest,
        ], check=True, capture_output=True)
        Path(tmp).unlink(missing_ok=True)
        print(f"  正規化完了: {dest}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [警告] ffmpeg正規化失敗: {e.stderr.decode()[:200]}")
        if Path(tmp).exists():
            Path(tmp).rename(dest)
            return True
        return False


def main() -> None:
    Path(BGM_DIR).mkdir(parents=True, exist_ok=True)
    dest_mp3 = f"{BGM_DIR}/{BGM_NAME}.mp3"

    if Path(dest_mp3).exists():
        size_kb = Path(dest_mp3).stat().st_size // 1024
        print(f"{BGM_NAME}.mp3 は既存のためスキップ ({size_kb} KB)")
        return

    print("=== 名馬シリーズ用BGMダウンロード開始 ===")

    for query in BGM_SEARCHES:
        print(f"\n--- 検索: 「{query}」 ---")
        identifiers = search_archive(query)
        for identifier in identifiers:
            mp3_urls = get_mp3_files(identifier)
            if not mp3_urls:
                continue
            url = mp3_urls[0]
            print(f"  試行: {identifier} → {Path(url).name[:60]}")
            if download_and_normalize(url, dest_mp3):
                print(f"\n=== BGMダウンロード完了: {dest_mp3} ===")
                return
            time.sleep(1)
        time.sleep(2)

    # 全クエリ失敗: フォールバック（通常BGM bgm_2.mp3 を使用）
    fallback = f"{BGM_DIR}/bgm_2.mp3"
    if Path(fallback).exists():
        print(f"\n[警告] 専用BGM取得失敗。フォールバック bgm_2.mp3 を使用します。")
        import shutil
        shutil.copy(fallback, dest_mp3)
        print(f"  コピー完了: {fallback} → {dest_mp3}")
    else:
        print("[エラー] BGMを取得できませんでした。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
