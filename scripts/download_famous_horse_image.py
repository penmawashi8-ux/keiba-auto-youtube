#!/usr/bin/env python3
"""Wikimedia Commons から名馬の画像を取得するスクリプト。

- 馬名でCommons APIを検索
- CC系・PDライセンスの画像のみ使用
- assets/ai_0.jpg に保存（サムネイルフレームに使用される）
- 引用元情報を assets/attribution.json に保存
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR  = "data/famous_horses"
ASSETS_DIR = "assets"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
UA = "keiba-auto-youtube/1.0 (educational non-commercial; github.com/penmawashi8-ux/keiba-auto-youtube)"

FREE_LICENSE_KEYWORDS = ["CC", "Public Domain", "PD", "cc-"]


def api_get(params: dict) -> dict:
    url = COMMONS_API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def strip_html(text: str) -> str:
    """HTMLタグを除去してプレーンテキストに変換。"""
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def is_free_license(license_str: str) -> bool:
    return any(kw.lower() in license_str.lower() for kw in FREE_LICENSE_KEYWORDS)


def search_commons(query: str, limit: int = 15) -> list[str]:
    """Commonsでファイル検索。ファイルタイトルのリストを返す。"""
    data = api_get({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": "6",
        "srlimit": str(limit),
    })
    return [r["title"] for r in data.get("query", {}).get("search", [])]


def get_image_info(file_title: str) -> dict | None:
    """ファイル情報を取得。フリーライセンスでない場合はNoneを返す。"""
    data = api_get({
        "action": "query",
        "titles": file_title,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size|mediatype",
    })
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return None
    page = pages[0] if isinstance(pages, list) else list(pages.values())[0]

    if "imageinfo" not in page:
        return None
    info = page["imageinfo"][0]

    # 画像以外（動画・音声など）は除外
    if info.get("mediatype", "") not in ("BITMAP", "DRAWING", ""):
        return None

    # ファイル拡張子チェック
    url = info.get("url", "")
    if not any(url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
        return None

    meta = info.get("extmetadata", {})
    license_short = meta.get("LicenseShortName", {}).get("value", "")
    if not is_free_license(license_short):
        print(f"    ライセンス不可: {license_short or '不明'} → スキップ")
        return None

    author_raw = meta.get("Artist", {}).get("value", "") or meta.get("Credit", {}).get("value", "Wikimedia Commons")
    author = strip_html(author_raw)

    # 小さすぎる画像を除外（幅400px未満）
    width = info.get("width", 0)
    if width and width < 400:
        print(f"    解像度不足 ({width}px) → スキップ")
        return None

    return {
        "url": url,
        "author": author,
        "license": license_short,
        "file_title": file_title,
        "commons_url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(file_title.replace(' ', '_'))}",
        "width": width,
    }


def download_image(url: str, dest: str, retries: int = 3) -> bool:
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                Path(dest).write_bytes(resp.read())
            size_kb = Path(dest).stat().st_size // 1024
            print(f"    保存完了: {dest} ({size_kb} KB)")
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5 * (attempt + 1)
                print(f"    レート制限 (429)。{wait}秒待機して再試行... ({attempt+1}/{retries})")
                time.sleep(wait)
            else:
                print(f"    ダウンロード失敗 HTTP {e.code}: {url}")
                return False
        except Exception as e:
            print(f"    ダウンロード失敗: {e}")
            return False
    print(f"    リトライ上限に達しました: {url}")
    return False


NUM_SLOTS = 6  # 動画で使う背景画像の枚数


def main() -> None:
    if len(sys.argv) < 2:
        print("使用法: python scripts/download_famous_horse_image.py <horse_key>", file=sys.stderr)
        sys.exit(1)

    horse_key = sys.argv[1]
    meta_path = Path(f"{DATA_DIR}/{horse_key}.json")
    if not meta_path.exists():
        print(f"[エラー] {meta_path} が見つかりません。", file=sys.stderr)
        sys.exit(1)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    horse_name = meta.get("name", horse_key)

    print(f"=== Wikimedia Commons 画像検索: {horse_name} ===")
    Path(ASSETS_DIR).mkdir(exist_ok=True)

    # 馬名単独 → 馬名+競走馬 の順で検索、最大NUM_SLOTS枚収集
    queries = [horse_name, f"{horse_name} 競走馬", f"{horse_name} racehorse"]
    collected: list[dict] = []

    for query in queries:
        if len(collected) >= NUM_SLOTS:
            break
        print(f"  検索クエリ: 「{query}」")
        titles = search_commons(query, limit=20)
        print(f"  ヒット数: {len(titles)} 件")

        for title in titles:
            if len(collected) >= NUM_SLOTS:
                break
            # 重複スキップ
            if any(c["file_title"] == title for c in collected):
                continue
            print(f"  チェック: {title}")
            info = get_image_info(title)
            if not info:
                time.sleep(0.3)
                continue
            collected.append(info)
            print(f"    OK: {info['author']} / {info['license']}")
            time.sleep(0.5)

    if not collected:
        print("[警告] フリーライセンスの適切な画像が見つかりませんでした。", file=sys.stderr)
        sys.exit(1)

    print(f"\n  取得画像: {len(collected)} 枚 → ダウンロード開始")

    # まず収集した画像を順番にダウンロード（一時ファイルに保存）
    tmp_files: list[tuple[str, dict]] = []
    for idx, info in enumerate(collected):
        tmp_path = f"{ASSETS_DIR}/ai_tmp_{idx}.jpg"
        if download_image(info["url"], tmp_path):
            tmp_files.append((tmp_path, info))
        time.sleep(2)  # レート制限対策

    if not tmp_files:
        print("[警告] 1枚もダウンロードできませんでした。", file=sys.stderr)
        sys.exit(1)

    print(f"\n  ダウンロード成功: {len(tmp_files)} 枚 → {NUM_SLOTS} スロットに配置")

    # 成功した写真をNUM_SLOTSに循環配置（余った外部画像が混入しない）
    import shutil
    attrs = []
    for i in range(NUM_SLOTS):
        tmp_path, info = tmp_files[i % len(tmp_files)]
        dest = f"{ASSETS_DIR}/ai_{i}.jpg"
        shutil.copy2(tmp_path, dest)
        print(f"    ai_{i}.jpg ← {Path(tmp_path).name}")
        attrs.append({
            "slot": i,
            "file_title": info["file_title"],
            "author": info["author"],
            "license": info["license"],
            "url": info["commons_url"],
        })

    # 一時ファイルを削除
    for tmp_path, _ in tmp_files:
        Path(tmp_path).unlink(missing_ok=True)

    # 引用元情報を保存（全画像分）
    attr_data = {
        "source": "Wikimedia Commons",
        "images": attrs,
    }
    Path(f"{ASSETS_DIR}/attribution.json").write_text(
        json.dumps(attr_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"=== 画像取得完了: {len(attrs)}/{NUM_SLOTS} 枚 ===")


if __name__ == "__main__":
    main()
