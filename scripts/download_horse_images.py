#!/usr/bin/env python3
"""正解の馬の写真を Wikipedia REST API からダウンロードして assets/horses/ に保存"""

import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ASSETS_DIR = Path("assets/horses")
UA = "keiba-auto-youtube/1.0"
CTX = ssl.create_default_context()

SKIP_EXTS = {".svg", ".ogv", ".ogg", ".webm"}


def _fetch_json(url: str) -> dict | None:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=12, context=CTX) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"    API error: {e}", file=sys.stderr)
        return None


def get_image_url(horse_name: str) -> str | None:
    for lang in ("ja", "en"):
        encoded = urllib.parse.quote(horse_name)
        d = _fetch_json(
            f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
        )
        if not d:
            continue
        src = (d.get("originalimage") or d.get("thumbnail") or {}).get("source", "")
        if not src:
            continue
        ext = Path(src.split("?")[0]).suffix.lower()
        if ext in SKIP_EXTS:
            continue
        return src
    return None


def download(url: str, out: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            out.write_bytes(r.read())
        return True
    except Exception as e:
        print(f"    download error: {e}", file=sys.stderr)
        return False


def collect_correct_horses(quiz_data: dict) -> list[str]:
    names: list[str] = []
    for part in quiz_data.get("parts", []):
        for q in part.get("questions", []):
            names.append(q["choices"][q["correct_index"]])
    for q in quiz_data.get("questions", []):
        names.append(q["choices"][q["correct_index"]])
    return names


def main() -> None:
    quiz_path = Path("quiz.json")
    if not quiz_path.exists():
        print("quiz.json が見つかりません。スキップします。")
        return

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    quiz_data = json.loads(quiz_path.read_text(encoding="utf-8"))
    horses = collect_correct_horses(quiz_data)

    print(f"馬の写真ダウンロード開始: {len(horses)} 頭")
    ok = 0
    for name in horses:
        # 既存ファイル確認（jpg/png どちらでも）
        existing = next(
            (ASSETS_DIR / f"{name}{e}" for e in (".jpg", ".png", ".jpeg")
             if (ASSETS_DIR / f"{name}{e}").exists()), None
        )
        if existing:
            print(f"  スキップ(既存): {name}")
            ok += 1
            continue

        print(f"  {name} ...", end=" ", flush=True)
        url = get_image_url(name)
        if not url:
            print("画像URL取得失敗")
            continue

        ext = Path(url.split("?")[0]).suffix.lower() or ".jpg"
        out = ASSETS_DIR / f"{name}{ext}"
        if download(url, out):
            print(f"完了 ({ext})")
            ok += 1
        else:
            print("ダウンロード失敗")
            out.unlink(missing_ok=True)

    print(f"完了: {ok}/{len(horses)} 頭取得")


if __name__ == "__main__":
    main()
