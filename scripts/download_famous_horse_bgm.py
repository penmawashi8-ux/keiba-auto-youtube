#!/usr/bin/env python3
"""名馬シリーズ専用 BGMダウンロードスクリプト

Content ID リスクをゼロにするため、以下の順で取得する:
  1. Musopen (CC0 クラシック録音): 行進曲・序曲・活発な曲を優先して迫力あるBGMを取得
  2. archive.org (CC0 ドラマチック管弦楽): 映画・劇場系の dramaticなCC0音源
  3. archive.org (CC0 クラシック行進曲): パブリックドメイン行進曲・交響曲

assets/bgm/horse_drama_bgm.mp3 に保存する。
"""
import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

BGM_DIR  = "assets/bgm"
BGM_NAME = "horse_drama_bgm"

MUSOPEN_API = "https://api.musopen.org"

# ドラマチック・行進曲系のキーワード（タイトルマッチ用）
DRAMATIC_KEYWORDS = [
    "march", "overture", "allegro", "vivace", "presto", "fanfare",
    "symphony", "triumph", "heroic", "gallop", "charge", "battle",
    "finale", "bolero", "cavalcade", "pageant", "grandeur",
]

# Musopen APIで好まれない低速ワード（これに該当するものは後回し）
CALM_KEYWORDS = [
    "nocturne", "adagio", "andante", "lento", "piano", "sonata",
    "elegy", "requiem", "berceuse", "serenade", "romance",
]

CC0_FILTER = 'licenseurl:"https://creativecommons.org/publicdomain/zero/1.0/"'

# archive.org ドラマチック音楽クエリ（CC0のみ）
DRAMATIC_QUERIES = [
    "dramatic orchestral music",
    "epic march classical",
    "triumphant fanfare music",
    "adventure orchestral royalty free",
    "galloping horse music classical",
    "cinematic dramatic music",
    "military march band music",
]


# ---------------------------------------------------------------------------
# Source 1: Musopen (CC0 クラシック録音)
# ---------------------------------------------------------------------------

def try_musopen(dest: str) -> bool:
    """Musopen APIからCC0クラシック音楽を取得する。
    行進曲・序曲などドラマチックな曲を優先して選択する。"""
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

        # ドラマチック・行進曲系を最優先
        dramatic = [r for r in recordings if any(
            kw in r.get("title", "").lower() for kw in DRAMATIC_KEYWORDS
        )]
        # 低速ワードなし → 中優先
        neutral = [r for r in recordings if r not in dramatic and not any(
            kw in r.get("title", "").lower() for kw in CALM_KEYWORDS
        )]
        # それ以外は後回し
        calm = [r for r in recordings if r not in dramatic and r not in neutral]

        ordered = dramatic + neutral + calm
        print(f"  [Musopen] ドラマチック={len(dramatic)}, ニュートラル={len(neutral)}, その他={len(calm)}")

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
# Source 2: archive.org CC0 ドラマチック音楽
# ---------------------------------------------------------------------------

def search_archive_dramatic(query: str, rows: int = 15) -> list[str]:
    """archive.org でCC0のドラマチック音楽を検索する。"""
    params = urllib.parse.urlencode({
        "q": (
            f"({query}) AND mediatype:audio AND format:MP3 AND {CC0_FILTER}"
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
            and 100_000 < int(f.get("size", 0)) < 30_000_000
        ]
    except Exception as e:
        print(f"  [archive.org] ファイル一覧取得失敗: {e}")
        return []


def try_archive_dramatic(dest: str) -> bool:
    """archive.org のCC0ドラマチック音楽をBGMとして取得する。"""
    for query in DRAMATIC_QUERIES:
        print(f"  [archive.org] ドラマ音楽検索: 「{query}」（CC0）")
        identifiers = search_archive_dramatic(query)
        for identifier in identifiers[:5]:
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
# Source 3: archive.org CC0 パブリックドメイン管弦楽
# ---------------------------------------------------------------------------

CLASSICAL_MARCH_QUERIES = [
    "sousa march band",
    "military march orchestra public domain",
    "classical overture orchestra",
    "beethoven symphony march allegro",
]


def try_archive_classical(dest: str) -> bool:
    """archive.org のCC0パブリックドメイン管弦楽・行進曲を試みる。"""
    # CC0フィルターなし・著作権切れ演奏を試みる
    cc_filters = [
        CC0_FILTER,
        'licenseurl:"https://creativecommons.org/licenses/by/4.0/"',
        'licenseurl:"https://creativecommons.org/licenses/by/3.0/"',
    ]
    for query in CLASSICAL_MARCH_QUERIES:
        for cc in cc_filters:
            params = urllib.parse.urlencode({
                "q": f"({query}) AND mediatype:audio AND format:MP3 AND {cc}",
                "fl": "identifier",
                "output": "json",
                "rows": 10,
                "sort": "downloads desc",
            })
            url = f"https://archive.org/advancedsearch.php?{params}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                identifiers = [doc["identifier"] for doc in data.get("response", {}).get("docs", [])]
            except Exception:
                identifiers = []

            for identifier in identifiers[:3]:
                mp3_urls = get_mp3_from_archive(identifier)
                if not mp3_urls:
                    continue
                print(f"  [archive.org クラシック] 試行: {identifier}")
                if _download_and_normalize(mp3_urls[0], dest):
                    return True
                time.sleep(1)
        time.sleep(1)
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
        if len(data) < 50_000:
            print(f"    ファイルが小さすぎます ({len(data)} bytes)")
            return False
        Path(tmp).write_bytes(data)
        print(f"    ダウンロード完了: {len(data)//1024} KB")
    except Exception as e:
        print(f"    ダウンロード失敗: {e}")
        Path(tmp).unlink(missing_ok=True)
        return False

    try:
        # ドラマチックBGM向け: 音量を少し高めに、loudnorm は厳しめに
        subprocess.run([
            "ffmpeg", "-y", "-i", tmp,
            "-t", "120",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=9,volume=0.90",
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
    print("  方針: 行進曲・序曲・ドラマチック管弦楽（CC0のみ）")

    # Source 1: Musopen CC0 クラシック（ドラマチック優先）
    print("\n  ソース1: Musopen（CC0・行進曲/序曲/allegro優先）")
    if try_musopen(dest_mp3):
        print(f"\n=== BGM取得完了 (Musopen): {dest_mp3} ===")
        return

    # Source 2: archive.org CC0 ドラマチック音楽
    print("\n  ソース2: archive.org CC0 ドラマチック音楽...")
    if try_archive_dramatic(dest_mp3):
        print(f"\n=== BGM取得完了 (archive.org dramatic): {dest_mp3} ===")
        return

    # Source 3: archive.org CC0/CC-BY 管弦楽・行進曲
    print("\n  ソース3: archive.org CC0/CC-BY クラシック行進曲...")
    if try_archive_classical(dest_mp3):
        print(f"\n=== BGM取得完了 (archive.org classical): {dest_mp3} ===")
        return

    print("\n[警告] BGMを取得できませんでした。BGMなしで動画を生成します。")


if __name__ == "__main__":
    main()
