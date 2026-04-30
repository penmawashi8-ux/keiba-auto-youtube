#!/usr/bin/env python3
"""
download_fonts.py - Google Fonts から太字・ディスプレイ系日本語フォントをダウンロードする。

万代・キカカナ21 に近い太ゴシック・デザイン系フォントを取得し
assets/fonts/ に保存する。generate_video.py が自動的に使用する。
"""
import re
import sys
from pathlib import Path

import requests

FONTS_DIR = Path("assets/fonts")

# (Google Fonts family パラメータ, 保存ファイル名, 説明)
FONTS = [
    # 超極太ゴシック：万代・キカカナ系に最も近い
    ("Dela+Gothic+One",          "DelaGothicOne-Regular.ttf",  "Dela Gothic One（超極太ゴシック）"),
    # M PLUS 1p Black：重厚な角ゴシック
    ("M+PLUS+1p:wght@900",       "MPLUS1p-Black.ttf",          "M PLUS 1p Black（極太角ゴシック）"),
    # DotGothic16：ドット/ピクセル風ゴシック
    ("DotGothic16",              "DotGothic16-Regular.ttf",    "DotGothic16（ドットゴシック）"),
    # BIZ UDGothic Bold：太めの清書体
    ("BIZ+UDGothic:wght@700",    "BIZUDGothic-Bold.ttf",       "BIZ UDGothic Bold（太ゴシック）"),
    # Reggae One：手書き風ポップ太字
    ("Reggae+One",               "ReggaeOne-Regular.ttf",      "Reggae One（ポップ太字）"),
    # Rampart One：輪郭抜き太字
    ("Rampart+One",              "RampartOne-Regular.ttf",     "Rampart One（輪郭抜き太字）"),
    # Kaisei Tokumin ExtraBold：ホラー系ゴシック明朝（天皇賞(春)サムネ用）
    ("Kaisei+Tokumin:wght@800",  "KaiseiTokumin-ExtraBold.ttf", "Kaisei Tokumin ExtraBold（ホラー明朝）"),
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def download_font(family: str, filename: str, label: str) -> bool:
    css_url = f"https://fonts.googleapis.com/css2?family={family}&display=swap"
    try:
        r = requests.get(css_url, headers={"User-Agent": UA}, timeout=20)
        if r.status_code != 200:
            print(f"  [警告] CSS取得失敗 ({r.status_code}): {label}")
            return False
        urls = re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", r.text)
        if not urls:
            print(f"  [警告] フォントURL未検出: {label}")
            return False
        fr = requests.get(urls[0], timeout=30)
        if fr.status_code != 200 or len(fr.content) < 10000:
            print(f"  [警告] ダウンロード失敗 ({fr.status_code}): {label}")
            return False
        out = FONTS_DIR / filename
        out.write_bytes(fr.content)
        print(f"  ✓ {label}: {out} ({len(fr.content)//1024}KB)")
        return True
    except Exception as e:
        print(f"  [警告] {label}: {e}")
        return False


def main() -> None:
    print("=== フォントダウンロード ===")
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    ok = sum(download_font(fam, fn, lbl) for fam, fn, lbl in FONTS)
    print(f"\n{ok}/{len(FONTS)} フォントをダウンロードしました。")
    if ok == 0:
        print("[警告] フォント取得0件（ネットワーク不可）。既存フォントで動作継続。")


if __name__ == "__main__":
    main()
