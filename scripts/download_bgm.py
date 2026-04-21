#!/usr/bin/env python3
"""
フリーBGMダウンロードスクリプト

AI生成BGMを避けるため、以下の順で人間が演奏したCC0音源のみを取得する:
  1. Musopen (CC0 クラシック録音): 人間の演奏家によるクラシック音楽
  2. archive.org (CC0 クラシック・ピアノ・管弦楽): パブリックドメイン演奏
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

MUSOPEN_API = "https://api.musopen.org"

# AI生成を避けるため、クラシック・ピアノ・管弦楽に限定した検索クエリ
# 「background music」のような汎用ワードはAI生成を拾うため使用しない
BGM_SEARCHES = [
    ("bgm_1", "piano classical solo"),
    ("bgm_2", "orchestral classical instrumental"),
    ("bgm_3", "chamber music string quartet"),
]

CC0_FILTER = 'licenseurl:"https://creativecommons.org/publicdomain/zero/1.0/"'

# ニュース動画向けに穏やかな曲を優先するキーワード
CALM_KEYWORDS = [
    "piano", "nocturne", "andante", "adagio", "waltz", "prelude",
    "minuet", "serenade", "ballade", "impromptu",
]

# BGMとして不向きなもの（ボーカル入り・非常にテンポが速い）は後回し
UNSUITABLE_KEYWORDS = [
    "vocal", "singing", "opera", "presto", "vivace", "march", "fanfare",
]


def _download_and_normalize(url: str, dest: str, timeout: int = 120) -> bool:
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
        Path(tmp).unlink(missing_ok=True)
        print(f"  正規化完了: {dest}")
        return True
    except subprocess.CalledProcessError:
        if Path(tmp).exists():
            Path(tmp).rename(dest)
            return True
        return False
    finally:
        Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Source 1: Musopen (CC0 クラシック録音)
# ---------------------------------------------------------------------------

def try_musopen(dest: str) -> bool:
    """Musopen からCC0クラシック音楽を取得する（人間演奏確定）。"""
    print("  [Musopen] CC0クラシック録音を検索中...")
    try:
        url = f"{MUSOPEN_API}/recordings?" + urllib.parse.urlencode({
            "limit": 50,
            "format": "mp3",
        })
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        recordings = data if isinstance(data, list) else data.get("results", data)
        if not recordings:
            print("  [Musopen] 結果なし")
            return False
        print(f"  [Musopen] {len(recordings)} 件取得")

        # ニュース向け穏やかな曲を優先
        calm = [r for r in recordings if any(
            kw in r.get("title", "").lower() for kw in CALM_KEYWORDS
        )]
        unsuitable = [r for r in recordings if any(
            kw in r.get("title", "").lower() for kw in UNSUITABLE_KEYWORDS
        )]
        neutral = [r for r in recordings if r not in calm and r not in unsuitable]
        ordered = calm + neutral + unsuitable
        print(f"  [Musopen] 穏やか={len(calm)}, ニュートラル={len(neutral)}, 不適={len(unsuitable)}")

        for rec in ordered[:15]:
            file_url = rec.get("file") or rec.get("url")
            if not file_url:
                continue
            title = rec.get("title", "unknown")
            print(f"  [Musopen] 試行: {title[:60]}")
            if _download_and_normalize(file_url, dest):
                print(f"  [Musopen] 取得成功: {title[:60]}")
                return True
            time.sleep(1)
    except Exception as e:
        print(f"  [Musopen] 失敗: {e}")
    return False


# ---------------------------------------------------------------------------
# Source 2: archive.org (CC0 クラシック・管弦楽)
# ---------------------------------------------------------------------------

def search_archive(query: str, rows: int = 20) -> list[str]:
    """archive.org でCC0限定のクラシック音楽を検索する。"""
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
        return [
            f"https://archive.org/download/{identifier}/{urllib.parse.quote(f['name'])}"
            for f in files
            if f.get("name", "").lower().endswith(".mp3")
            and 100_000 < int(f.get("size", 0)) < 30_000_000
        ]
    except Exception as e:
        print(f"  [警告] ファイル一覧取得失敗 ({identifier}): {e}")
        return []


def try_archive(dest: str, query: str) -> bool:
    """archive.org CC0から指定クエリでBGMを取得する。"""
    identifiers = search_archive(query)
    for identifier in identifiers[:8]:
        mp3_urls = get_mp3_files(identifier)
        if not mp3_urls:
            continue
        url = mp3_urls[0]
        print(f"  試行: {identifier} → {Path(url).name[:50]}")
        if _download_and_normalize(url, dest):
            return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

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

        print(f"\n--- {name}: 「{query}」（CC0・人間演奏限定）---")

        # Source 1: Musopen（人間演奏のCC0クラシック確定）
        print(f"  ソース1: Musopen（CC0・クラシック）...")
        if try_musopen(dest_mp3):
            success += 1
            continue

        # Source 2: archive.org CC0 クラシック
        print(f"  ソース2: archive.org CC0クラシック...")
        if try_archive(dest_mp3, query):
            success += 1
            continue

        print(f"  [警告] {name} のBGM取得失敗。BGMなしで続行します。")
        time.sleep(2)

    print(f"\n=== BGM取得完了: {success}/{len(BGM_SEARCHES)} 件 ===")
    # BGMが0件でも動画生成は続行できるため exit 1 しない


if __name__ == "__main__":
    main()
