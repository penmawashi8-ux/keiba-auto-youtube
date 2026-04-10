#!/usr/bin/env python3
"""
フリーBGMダウンロードスクリプト

Content ID クレームを避けるため、CC0（パブリックドメイン）に限定して
archive.org から音楽を取得する。
"""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

BGM_DIR = "assets/bgm"

# CC0 ライセンスに限定した検索クエリ
# licenseurl で CC0 を明示的に指定してContent IDリスクを最小化する
BGM_SEARCHES = [
    ("bgm_1", "background music instrumental"),
    ("bgm_2", "calm piano background"),
    ("bgm_3", "ambient music instrumental"),
]

# archive.org の CC0 ライセンスURL
CC0_FILTER = 'licenseurl:"https://creativecommons.org/publicdomain/zero/1.0/"'


def search_archive(query: str, rows: int = 20) -> list[str]:
    """archive.org でCC0限定のMP3音楽を検索してidentifierリストを返す。"""
    params = urllib.parse.urlencode({
        "q": f"({query}) AND mediatype:audio AND format:MP3 AND {CC0_FILTER}",
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
        print(f"  CC0検索結果: {len(ids)} 件")
        return ids
    except Exception as e:
        print(f"  [警告] archive.org 検索失敗: {e}")
        return []


def get_mp3_files(identifier: str) -> list[str]:
    """アイテムのファイル一覧からMP3のURLを返す。"""
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
            and int(f.get("size", 0)) > 100_000
            and int(f.get("size", 0)) < 30_000_000
        ]
        return mp3s
    except Exception as e:
        print(f"  [警告] ファイル一覧取得失敗 ({identifier}): {e}")
        return []


def download_and_normalize(url: str, dest: str, timeout: int = 120) -> bool:
    """MP3をダウンロードして音量正規化する。"""
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

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", tmp,
            "-t", "90",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,volume=0.85",
            "-c:a", "libmp3lame", "-b:a", "128k", "-ac", "2",
            dest,
        ], check=True, capture_output=True)
        Path(tmp).unlink(missing_ok=True)
        print(f"  正規化完了: {dest}")
        return True
    except subprocess.CalledProcessError:
        if Path(tmp).exists():
            Path(tmp).rename(dest)
            return True
        return False


def main():
    Path(BGM_DIR).mkdir(parents=True, exist_ok=True)

    success = 0
    for name, query in BGM_SEARCHES:
        dest_mp3 = f"{BGM_DIR}/{name}.mp3"
        if Path(dest_mp3).exists():
            size_kb = Path(dest_mp3).stat().st_size // 1024
            print(f"{name}.mp3 は既存のためスキップ ({size_kb} KB)")
            success += 1
            continue

        print(f"\n--- {name}: 「{query}」（CC0限定）---")
        identifiers = search_archive(query)

        downloaded = False
        for identifier in identifiers:
            mp3_urls = get_mp3_files(identifier)
            if not mp3_urls:
                continue
            url = mp3_urls[0]
            print(f"  試行: {identifier} → {Path(url).name[:50]}")
            if download_and_normalize(url, dest_mp3):
                downloaded = True
                success += 1
                break
            time.sleep(1)

        if not downloaded:
            print(f"  [警告] {name} のBGM取得失敗。BGMなしで続行します。")

        time.sleep(2)

    print(f"\n=== BGM取得完了: {success}/{len(BGM_SEARCHES)} 件 ===")
    # BGMが0件でも動画生成は続行できるため exit 1 しない


if __name__ == "__main__":
    main()
