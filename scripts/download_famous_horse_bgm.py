#!/usr/bin/env python3
"""名馬シリーズ専用 BGMダウンロードスクリプト

Content ID リスクをゼロにするため、以下の順で取得する:
  1. Musopen (CC0 クラシック録音: 演奏者も著作権を放棄したパブリックドメイン)
  2. archive.org の CC0 アンビエント/自然環境音 (音楽ではなくSFX → Content ID 登録なし)

assets/bgm/horse_drama_bgm.mp3 に保存する。
"""
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BGM_DIR  = "assets/bgm"
BGM_NAME = "horse_drama_bgm"

MUSOPEN_API = "https://api.musopen.org"

# archive.org 自然環境音 CC0 コレクション
# 「音楽」ではなく「環境音・アンビエント」なのでContent ID登録リスクがほぼゼロ
AMBIENT_QUERIES = [
    "nature ambience relaxing",
    "gentle rain ambient sound",
    "wind ambient peaceful",
    "forest sounds ambient",
    "ambient drone meditation",
]
CC0_FILTER = 'licenseurl:"https://creativecommons.org/publicdomain/zero/1.0/"'


# ---------------------------------------------------------------------------
# Source 1: Musopen (CC0 クラシック録音)
# ---------------------------------------------------------------------------

def try_musopen(dest: str) -> bool:
    """Musopen APIからCC0クラシック音楽を取得する。
    Musopenの録音は演奏者自身が著作権を放棄しており、Content ID未登録。"""
    print("  [Musopen] CC0クラシック録音を検索中...")
    try:
        url = f"{MUSOPEN_API}/recordings?" + urllib.parse.urlencode({
            "limit": 30,
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

        # ピアノ・弦楽・管弦楽を優先して選択
        preferred = [r for r in recordings if any(
            kw in r.get("title", "").lower() for kw in
            ["piano", "nocturne", "adagio", "andante", "lento", "sonata"]
        )]
        candidates = preferred or recordings

        for rec in candidates[:10]:
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
# Source 2: archive.org CC0 アンビエント/自然環境音
# ---------------------------------------------------------------------------

def search_archive_ambient(query: str, rows: int = 15) -> list[str]:
    """archive.org でCC0の環境音・アンビエント音源を検索する。"""
    params = urllib.parse.urlencode({
        "q": (
            f"({query}) AND mediatype:audio AND format:MP3 AND {CC0_FILTER}"
            " AND NOT subject:(music) AND NOT subject:(song)"
        ),
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
        return [doc["identifier"] for doc in data.get("response", {}).get("docs", [])]
    except Exception as e:
        print(f"  [archive.org] 検索失敗: {e}")
        return []


def get_mp3_from_archive(identifier: str) -> list[str]:
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
            and 50_000 < int(f.get("size", 0)) < 30_000_000
        ]
    except Exception as e:
        print(f"  [archive.org] ファイル一覧取得失敗: {e}")
        return []


def try_archive_ambient(dest: str) -> bool:
    """archive.org のCC0環境音をBGMとして取得する。
    環境音・自然音はContent ID登録がほぼなく、著作権問題が発生しない。"""
    for query in AMBIENT_QUERIES:
        print(f"  [archive.org] 環境音検索: 「{query}」（CC0）")
        identifiers = search_archive_ambient(query)
        for identifier in identifiers:
            mp3_urls = get_mp3_from_archive(identifier)
            if not mp3_urls:
                continue
            url = mp3_urls[0]
            print(f"  [archive.org] 試行: {identifier}")
            if _download_and_normalize(url, dest):
                return True
            time.sleep(1)
        time.sleep(2)
    return False


# ---------------------------------------------------------------------------
# 共通: ダウンロード + ffmpegで正規化
# ---------------------------------------------------------------------------

def _download_and_normalize(url: str, dest: str, timeout: int = 120) -> bool:
    tmp = dest + ".tmp"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 30_000:
            print(f"    ファイルが小さすぎます ({len(data)} bytes)")
            return False
        Path(tmp).write_bytes(data)
        print(f"    ダウンロード完了: {len(data)//1024} KB")
    except Exception as e:
        print(f"    ダウンロード失敗: {e}")
        Path(tmp).unlink(missing_ok=True)
        return False

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", tmp,
            "-t", "120",
            "-af", "loudnorm=I=-18:TP=-1.5:LRA=11,volume=0.75",
            "-c:a", "libmp3lame", "-b:a", "128k", "-ac", "2",
            dest,
        ], check=True, capture_output=True)
        Path(tmp).unlink(missing_ok=True)
        size_kb = Path(dest).stat().st_size // 1024
        print(f"    正規化完了: {dest} ({size_kb} KB)")
        return True
    except subprocess.CalledProcessError:
        if Path(tmp).exists():
            Path(tmp).rename(dest)
            return True
        return False
    finally:
        Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    Path(BGM_DIR).mkdir(parents=True, exist_ok=True)
    dest_mp3 = f"{BGM_DIR}/{BGM_NAME}.mp3"

    if Path(dest_mp3).exists():
        size_kb = Path(dest_mp3).stat().st_size // 1024
        print(f"{BGM_NAME}.mp3 は既存のためスキップ ({size_kb} KB)")
        return

    print("=== 名馬シリーズ BGM取得開始 ===")
    print("  ソース1: Musopen（CC0クラシック録音・Content ID未登録）")
    print("  ソース2: archive.org CC0環境音（Content IDリスクほぼゼロ）")

    # Source 1: Musopen CC0クラシック
    if try_musopen(dest_mp3):
        print(f"\n=== BGM取得完了 (Musopen): {dest_mp3} ===")
        return

    # Source 2: archive.org CC0 アンビエント/自然環境音
    print("\n  Musopen失敗。archive.org CC0環境音にフォールバック...")
    if try_archive_ambient(dest_mp3):
        print(f"\n=== BGM取得完了 (archive.org環境音): {dest_mp3} ===")
        return

    print("\n[警告] BGMを取得できませんでした。BGMなしで動画を生成します。")
    # BGMがなくても動画生成は続行できる


if __name__ == "__main__":
    main()
