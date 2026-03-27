#!/usr/bin/env python3
"""
フリーBGMダウンロードスクリプト
Pixabay Music API で音楽を検索してダウンロードする。
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
    ("bgm_1", "upbeat happy"),
    ("bgm_2", "calm relaxing"),
    ("bgm_3", "exciting energetic"),
]


def search_pixabay_music(query: str, api_key: str) -> list[dict]:
    """Pixabay Music API で検索してヒットリストを返す。"""
    url = (
        "https://pixabay.com/api/music/"
        f"?key={api_key}"
        f"&q={urllib.parse.quote(query)}"
        "&per_page=5"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        hits = data.get("hits", [])
        print(f"  Pixabay Music 検索結果: {len(hits)} 件")
        return hits
    except urllib.error.HTTPError as e:
        print(f"  [警告] Pixabay Music API HTTP {e.code}: {e.reason}")
        return []
    except Exception as e:
        print(f"  [警告] Pixabay Music API エラー: {e}")
        return []


def download_and_normalize(url: str, dest: str, timeout: int = 120) -> bool:
    """URLからMP3をダウンロードして音量を正規化する。"""
    tmp = dest + ".tmp"
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
        Path(tmp).write_bytes(data)
        print(f"  ダウンロード完了: {len(data)//1024} KB")
    except Exception as e:
        print(f"  [警告] ダウンロード失敗: {e}")
        return False

    # ffmpeg で音量正規化 + ステレオ確認
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", tmp,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,volume=0.85",
            "-c:a", "libmp3lame", "-b:a", "128k", "-ac", "2",
            dest,
        ], check=True, capture_output=True)
        Path(tmp).unlink(missing_ok=True)
        print(f"  正規化完了: {dest}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [警告] 正規化失敗 ({e.returncode}): {e.stderr.decode()[:200]}")
        # 正規化失敗でもrawを使う
        Path(tmp).rename(dest)
        print(f"  正規化なしで保存: {dest}")
        return True


def main():
    if not PIXABAY_API_KEY:
        print("[エラー] PIXABAY_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(1)

    Path(BGM_DIR).mkdir(parents=True, exist_ok=True)

    success = 0
    for name, query in BGM_QUERIES:
        dest_mp3 = f"{BGM_DIR}/{name}.mp3"
        if Path(dest_mp3).exists():
            size_kb = Path(dest_mp3).stat().st_size // 1024
            print(f"{name}.mp3 は既存のためスキップ ({size_kb} KB)")
            success += 1
            continue

        print(f"\n--- {name}: 「{query}」で検索 ---")
        hits = search_pixabay_music(query, PIXABAY_API_KEY)

        downloaded = False
        for hit in hits:
            # Pixabay Music API のレスポンス形式に対応
            audio_url = (
                hit.get("audio")
                or hit.get("audioUrl")
                or hit.get("url")
                or hit.get("previewURL")
                or ""
            )
            title = hit.get("title", hit.get("tags", "unknown"))[:50]
            print(f"  候補: {title} → {audio_url[:60] if audio_url else '(URLなし)'}")

            if not audio_url:
                continue

            if download_and_normalize(audio_url, dest_mp3):
                downloaded = True
                success += 1
                break

        if not downloaded:
            print(f"  [エラー] {name} のBGM取得に失敗しました。")

        time.sleep(1)

    print(f"\n=== BGM取得完了: {success}/{len(BGM_QUERIES)} 件 ===")
    if success == 0:
        print("[エラー] BGMを1件も取得できませんでした。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
