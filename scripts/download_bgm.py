#!/usr/bin/env python3
"""
フリーBGMダウンロードスクリプト

CC0音源のみを使用:
  1. archive.org Musopen コレクション（CC0クラシック録音）
  2. archive.org パブリックドメイン検索（broader fallback）
"""
import random
import subprocess
import time
import urllib.request
import urllib.parse
from pathlib import Path

BGM_DIR = "assets/bgm"
BGM_SLOT_COUNT = 3

# archive.org で Musopen が公開している CC0 コレクション
# identifier を直接指定することで検索フィルタの不安定さを回避
MUSOPEN_ARCHIVE_IDENTIFIERS = [
    "musopen-chopin-nocturnes",
    "musopen-bach-inventions",
    "musopen-beethoven-piano-sonatas",
    "musopen-brahms-piano-pieces",
    "musopen-debussy-piano",
    "musopen-schubert-piano",
    "musopen-mozart-piano-sonatas",
    "musopen-piano-music",
    "musopen-string-quartets",
    "musopen-classical-guitar",
]

# archive.org 一般検索クエリ（Musopen コレクション失敗時のフォールバック）
ARCHIVE_SEARCH_QUERIES = [
    "creator:Musopen mediatype:audio",
    "subject:(classical piano nocturne) mediatype:audio NOT licenseurl:*licenses*",
    "subject:(baroque classical) mediatype:audio creator:musopen",
    "chopin nocturne piano classical mediatype:audio creator:musopen",
    "beethoven piano sonata mediatype:audio creator:musopen",
]


def _download_and_normalize(url: str, dest: str, timeout: int = 120) -> bool:
    """MP3をダウンロードしてffmpegで音量正規化する。"""
    tmp = dest + ".tmp.mp3"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 50_000:
            print(f"  [警告] ファイルが小さすぎます ({len(data)} bytes)")
            return False
        Path(tmp).write_bytes(data)
        print(f"  ダウンロード完了: {len(data) // 1024} KB")
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
        print(f"  正規化完了: {dest}")
        return True
    except subprocess.CalledProcessError:
        if Path(tmp).exists():
            Path(tmp).rename(dest)
            return True
        return False
    finally:
        Path(tmp).unlink(missing_ok=True)


def _get_mp3_files_from_identifier(identifier: str) -> list[str]:
    url = f"https://archive.org/metadata/{identifier}/files"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            import json
            data = json.loads(resp.read())
        files = data.get("result", [])
        urls = [
            f"https://archive.org/download/{identifier}/{urllib.parse.quote(f['name'])}"
            for f in files
            if f.get("name", "").lower().endswith(".mp3")
            and 100_000 < int(f.get("size", 0)) < 30_000_000
        ]
        print(f"  [archive.org] {identifier}: {len(urls)} 件のMP3")
        return urls
    except Exception as e:
        print(f"  [archive.org] ファイル一覧取得失敗 ({identifier}): {e}")
        return []


def try_musopen_archive(dest: str) -> bool:
    """archive.org の Musopen コレクションから CC0 音楽を取得する。"""
    identifiers = MUSOPEN_ARCHIVE_IDENTIFIERS.copy()
    random.shuffle(identifiers)
    for identifier in identifiers:
        mp3_urls = _get_mp3_files_from_identifier(identifier)
        if not mp3_urls:
            continue
        url = random.choice(mp3_urls)
        print(f"  [Musopen/archive] 試行: {identifier}")
        if _download_and_normalize(url, dest):
            print(f"  [Musopen/archive] 取得成功: {identifier}")
            return True
        time.sleep(1)
    return False


def try_archive_search(dest: str) -> bool:
    """archive.org の一般検索で CC0 クラシック音楽を取得する。"""
    import json
    query = random.choice(ARCHIVE_SEARCH_QUERIES)
    params = urllib.parse.urlencode({
        "q": query,
        "fl": "identifier",
        "output": "json",
        "rows": 30,
        "sort": "downloads desc",
    })
    url = f"https://archive.org/advancedsearch.php?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        ids = [doc["identifier"] for doc in data.get("response", {}).get("docs", [])]
        print(f"  [archive.org] 検索結果: {len(ids)} 件 (query={query[:40]})")
    except Exception as e:
        print(f"  [archive.org] 検索失敗: {e}")
        return False

    random.shuffle(ids)
    for identifier in ids[:10]:
        mp3_urls = _get_mp3_files_from_identifier(identifier)
        if not mp3_urls:
            continue
        url = random.choice(mp3_urls)
        print(f"  [archive.org] 試行: {identifier}")
        if _download_and_normalize(url, dest):
            return True
        time.sleep(1)
    return False


def main():
    Path(BGM_DIR).mkdir(parents=True, exist_ok=True)

    success = 0
    for i in range(1, BGM_SLOT_COUNT + 1):
        dest_mp3 = f"{BGM_DIR}/bgm_{i}.mp3"
        if Path(dest_mp3).exists():
            size_kb = Path(dest_mp3).stat().st_size // 1024
            print(f"bgm_{i}.mp3 は既存のためスキップ ({size_kb} KB)")
            success += 1
            continue

        print(f"\n--- bgm_{i} ---")
        print("  ソース1: archive.org Musopen コレクション...")
        if try_musopen_archive(dest_mp3):
            success += 1
            continue

        print("  ソース2: archive.org 一般検索...")
        if try_archive_search(dest_mp3):
            success += 1
            continue

        print(f"  [警告] bgm_{i} のBGM取得失敗。BGMなしで続行します。")
        time.sleep(2)

    print(f"\n=== BGM取得完了: {success}/{BGM_SLOT_COUNT} 件 ===")


if __name__ == "__main__":
    main()
