#!/usr/bin/env python3
"""
フリーBGMダウンロードスクリプト

AI生成BGMを避けるため、人間が演奏したCC0音源のみを使用する:
  1. Musopen (CC0 クラシック録音): 実演奏家によるクラシック音楽
  2. archive.org (CC0): パブリックドメインのクラシック・民謡・バロック

毎回プールから3種類をランダム選択してダウンロードし、
generate_video.py の random.choice() でさらに1つ選ばれる。
"""
import json
import random
import subprocess
import time
import urllib.request
import urllib.parse
from pathlib import Path

BGM_DIR = "assets/bgm"
BGM_SLOT_COUNT = 3  # 1回のワークフローでダウンロードするファイル数

MUSOPEN_API = "https://api.musopen.org"
CC0_FILTER = 'licenseurl:"https://creativecommons.org/publicdomain/zero/1.0/"'

# AI生成を避けるため、実演奏が存在するジャンルに限定した30種類以上のプール
BGM_QUERY_POOL = [
    # ピアノ独奏
    "chopin piano nocturne classical",
    "beethoven piano sonata classical",
    "debussy piano impressionist classical",
    "schubert piano impromptu classical",
    "schumann piano piece classical",
    "bach piano prelude fugue",
    "mozart piano sonata classical",
    "liszt piano etude classical",
    "brahms piano intermezzo classical",
    "ravel piano classical",
    # 弦楽
    "string quartet classical chamber",
    "violin sonata classical chamber",
    "cello solo classical",
    "violin concerto baroque classical",
    "string orchestra classical",
    # 管弦楽・バロック
    "classical symphony orchestra",
    "baroque ensemble harpsichord",
    "chamber orchestra classical",
    "flute classical baroque",
    "organ classical baroque",
    # ピアノ関連（スタイル別）
    "waltz piano classical",
    "ragtime piano 1920s",
    "minuet classical keyboard",
    "gavotte baroque harpsichord",
    "menuetto string quartet classical",
    # ムード別
    "andante classical music peaceful",
    "adagio classical strings calm",
    "allegretto classical cheerful",
    "folk traditional instrumental acoustic",
    "acoustic guitar classical fingerstyle",
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


# ---------------------------------------------------------------------------
# Source 1: Musopen (CC0 クラシック録音・人間演奏確定)
# ---------------------------------------------------------------------------

def try_musopen(dest: str, query: str) -> bool:
    """Musopen から query に関連するCC0クラシック音楽を取得する。"""
    keywords = [w for w in query.lower().split() if len(w) > 3]
    print(f"  [Musopen] CC0クラシック録音を検索中 (keywords: {keywords[:4]})...")
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

        # クエリキーワードに近い曲を優先、それ以外はシャッフル
        matched = [r for r in recordings if any(
            kw in r.get("title", "").lower() for kw in keywords
        )]
        others = [r for r in recordings if r not in matched]
        random.shuffle(matched)
        random.shuffle(others)
        ordered = matched + others

        for rec in ordered[:12]:
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
# Source 2: archive.org (CC0 クラシック・民謡・バロック)
# ---------------------------------------------------------------------------

def _search_archive(query: str, rows: int = 20) -> list[str]:
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
        print(f"  [archive.org] CC0検索結果: {len(ids)} 件")
        return ids
    except Exception as e:
        print(f"  [archive.org] 検索失敗: {e}")
        return []


def _get_mp3_files(identifier: str) -> list[str]:
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
        print(f"  [archive.org] ファイル一覧取得失敗 ({identifier}): {e}")
        return []


def try_archive(dest: str, query: str) -> bool:
    identifiers = _search_archive(query)
    random.shuffle(identifiers)  # 毎回違うファイルが選ばれるよう
    for identifier in identifiers[:8]:
        mp3_urls = _get_mp3_files(identifier)
        if not mp3_urls:
            continue
        url = random.choice(mp3_urls)
        print(f"  [archive.org] 試行: {identifier}")
        if _download_and_normalize(url, dest):
            return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    Path(BGM_DIR).mkdir(parents=True, exist_ok=True)

    # 毎回プールからランダムにBGM_SLOT_COUNT種類を選択
    selected_queries = random.sample(BGM_QUERY_POOL, BGM_SLOT_COUNT)
    print(f"今回のBGMスタイル: {selected_queries}")

    success = 0
    for i, query in enumerate(selected_queries, 1):
        name = f"bgm_{i}"
        dest_mp3 = f"{BGM_DIR}/{name}.mp3"
        # GitHub Actions では毎回新規ダウンロードだが、
        # ローカル多重実行時は既存をスキップ
        if Path(dest_mp3).exists():
            size_kb = Path(dest_mp3).stat().st_size // 1024
            print(f"{name}.mp3 は既存のためスキップ ({size_kb} KB)")
            success += 1
            continue

        print(f"\n--- {name}: 「{query}」（CC0・人間演奏限定）---")

        print("  ソース1: Musopen（CC0・実演奏クラシック）...")
        if try_musopen(dest_mp3, query):
            success += 1
            continue

        print(f"  ソース2: archive.org CC0...")
        if try_archive(dest_mp3, query):
            success += 1
            continue

        print(f"  [警告] {name} のBGM取得失敗。BGMなしで続行します。")
        time.sleep(2)

    print(f"\n=== BGM取得完了: {success}/{BGM_SLOT_COUNT} 件 ===")


if __name__ == "__main__":
    main()
