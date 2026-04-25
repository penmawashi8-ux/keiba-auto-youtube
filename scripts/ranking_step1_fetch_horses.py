#!/usr/bin/env python3
"""Step 1: uma-channel.jp から歴代G1勝利数ランキング上位50頭を取得して horses.csv に保存"""

import csv
import os
import re
import sys
import time

SOURCE_URL = "https://uma-channel.jp/umafile_1/index_12.html"
SOURCE_BASE = "https://uma-channel.jp/umafile_1/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://uma-channel.jp/",
}


def parse_wins(text):
    """'8勝' → 8"""
    m = re.search(r"(\d+)", text.strip())
    return int(m.group(1)) if m else 0


def parse_horses_from_html(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    horses = []

    # G1勝利数表 (width=640) を特定
    main_table = None
    for t in soup.find_all("table"):
        if t.find("td", attrs={"colspan": "10"}):
            main_table = t
            break

    if not main_table:
        print("  ERROR: メインテーブルが見つかりません")
        return None

    current_g1_wins = 0

    for row in main_table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue

        # ▼N勝 のセクション行
        if len(cells) == 1:
            m = re.search(r"▼(\d+)勝", cells[0].text)
            if m:
                current_g1_wins = int(m.group(1))
            continue

        # 馬データ行: cells[2] に col_y / fil_y の <a> が必要
        if len(cells) < 9:
            continue

        name_cell = cells[2]
        link = name_cell.find("a")
        if not link:
            continue

        horse_name = link.text.strip()
        href = link.get("href", "")
        if href.startswith("./"):
            profile_url = SOURCE_BASE + href[2:]
        elif href.startswith("/"):
            profile_url = "https://uma-channel.jp" + href
        elif href.startswith("http"):
            profile_url = href
        else:
            profile_url = SOURCE_BASE + href

        # JRA G1: cells[5], 海外 G1: cells[7]
        jra_g1 = parse_wins(cells[5].text)
        overseas_g1 = parse_wins(cells[7].text)
        total_g1 = jra_g1 + overseas_g1

        # 生年: cells[1]
        birth_raw = cells[1].text.strip()
        birth_2d = int(birth_raw) if birth_raw.isdigit() else 0
        birth_year = (2000 + birth_2d) if birth_2d <= 30 else (1900 + birth_2d)

        horses.append({
            "rank": len(horses) + 1,
            "name": horse_name,
            "url": profile_url,
            "g1_wins": total_g1,
            "jra_g1": jra_g1,
            "overseas_g1": overseas_g1,
            "birth_year": birth_year,
        })

        if len(horses) >= 50:
            break

    return horses if horses else None


def fetch_html(url, retries=3):
    import requests
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return resp.text
            print(f"  HTTP {resp.status_code} (試行 {attempt+1}/{retries})")
        except Exception as e:
            print(f"  接続エラー: {e} (試行 {attempt+1}/{retries})")
        if attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
    return None


def save_horses_csv(horses, path="horses.csv"):
    fieldnames = ["rank", "name", "url", "g1_wins", "jra_g1", "overseas_g1", "birth_year"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(horses)
    print(f"{len(horses)}頭を {path} に保存しました")


def main():
    print("=== Step 1: G1勝利数ランキング取得 (uma-channel.jp) ===")
    print(f"  取得元: {SOURCE_URL}")

    html = fetch_html(SOURCE_URL)
    if not html:
        print("ERROR: ページの取得に失敗しました")
        sys.exit(1)

    print(f"  HTML取得成功 ({len(html)}文字)")

    horses = parse_horses_from_html(html)
    if not horses:
        print("ERROR: 馬データの解析に失敗しました")
        sys.exit(1)

    save_horses_csv(horses)

    print("\n=== 取得結果 (上位10頭) ===")
    for h in horses[:10]:
        print(
            f"  {h['rank']:2d}位 {h['name']:<14s} "
            f"G1合計:{h['g1_wins']}勝 (JRA:{h['jra_g1']} 海外:{h['overseas_g1']}) "
            f"生年:{h['birth_year']}"
        )
    print(f"\n完了: {len(horses)}頭取得")


if __name__ == "__main__":
    main()
