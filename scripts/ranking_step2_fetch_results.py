#!/usr/bin/env python3
"""Step 2: horses.csv の各馬のレース成績を netkeiba から取得して results.csv に保存"""

import csv
import time
import random
import re
import sys
from pathlib import Path


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

RESULT_FIELDNAMES = [
    "horse_name",
    "horse_url",
    "race_name",
    "year",
    "venue",
    "position",
    "grade",
    "distance",
    "time",
    "popularity",
    "margin",
    "prize_man",
]

GRADE_PATTERNS = [
    (re.compile(r"G1|GI", re.IGNORECASE), "G1"),
    (re.compile(r"G2|GII", re.IGNORECASE), "G2"),
    (re.compile(r"G3|GIII", re.IGNORECASE), "G3"),
    (re.compile(r"OP|オープン", re.IGNORECASE), "OP"),
]


def detect_grade(race_name):
    for pat, grade in GRADE_PATTERNS:
        if pat.search(race_name):
            return grade
    return "OP"


def parse_time_to_seconds(time_str):
    """'2:23.4' → 143.4 秒"""
    if not time_str or time_str.strip() in ("-", ""):
        return None
    m = re.match(r"(\d+):(\d+)\.(\d+)", time_str.strip())
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 10
    m2 = re.match(r"(\d+)\.(\d+)", time_str.strip())
    if m2:
        return int(m2.group(1)) + int(m2.group(2)) / 10
    return None


def parse_margin(margin_str):
    """着差文字列を秒換算 (概算)"""
    if not margin_str or margin_str.strip() in ("-", "同着", ""):
        return 0.0
    margin_str = margin_str.strip()
    margin_map = {
        "ハナ": 0.1, "クビ": 0.2, "アタマ": 0.15,
        "1/2": 0.3, "3/4": 0.4,
        "1": 0.6, "1.1/4": 0.7, "1.1/2": 0.9, "1.3/4": 1.0,
        "2": 1.2, "2.1/2": 1.5, "3": 1.8,
    }
    for k, v in margin_map.items():
        if k in margin_str:
            return v
    m = re.search(r"(\d+)", margin_str)
    if m:
        return float(m.group(1)) * 0.6
    return 0.0


def fetch_horse_results(horse_name, horse_url, session):
    """1頭分のレース成績を取得"""
    from bs4 import BeautifulSoup

    # horse_url = https://db.netkeiba.com/horse/XXXXXXXXXX/
    result_url = horse_url.rstrip("/") + "/"

    resp = session.get(result_url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code}: {result_url}")

    soup = BeautifulSoup(resp.text, "html.parser")

    # レース成績テーブルを探す
    # netkeibaの馬詳細ページでは class="db_h_race_results" または "nk_tb_common"
    table = soup.find("table", class_=re.compile(r"race_results|db_h_race|nk_tb"))
    if not table:
        # フォールバック: tbody を持つテーブルを探す
        for t in soup.find_all("table"):
            rows = t.find_all("tr")
            if len(rows) >= 3:
                # ヘッダー行に「レース名」または「着順」があるか確認
                header_text = rows[0].get_text()
                if "レース名" in header_text or "着順" in header_text or "着" in header_text:
                    table = t
                    break

    if not table:
        raise ValueError(f"成績テーブルが見つかりません: {result_url}")

    rows = table.find_all("tr")
    # ヘッダー行からカラム位置を推定
    header_row = rows[0]
    headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]

    def col_index(candidates):
        for c in candidates:
            for i, h in enumerate(headers):
                if c in h:
                    return i
        return None

    idx_date = col_index(["日付", "年月日"])
    idx_race = col_index(["レース名", "レース"])
    idx_pos = col_index(["着順", "着"])
    idx_time = col_index(["タイム"])
    idx_margin = col_index(["着差"])
    idx_pop = col_index(["人気"])
    idx_prize = col_index(["賞金", "本賞金"])
    idx_venue = col_index(["開催", "場名"])
    idx_dist = col_index(["距離", "コース"])

    results = []
    for row in rows[1:]:
        cols = row.find_all("td")
        if not cols:
            continue

        def gcol(idx):
            if idx is None or idx >= len(cols):
                return ""
            return cols[idx].get_text(strip=True)

        date_str = gcol(idx_date)
        race_name = gcol(idx_race)
        position_str = gcol(idx_pos)
        time_str = gcol(idx_time)
        margin_str = gcol(idx_margin)
        pop_str = gcol(idx_pop)
        prize_str = gcol(idx_prize)
        venue_str = gcol(idx_venue)
        dist_str = gcol(idx_dist)

        if not race_name or not date_str:
            continue

        year = ""
        m = re.search(r"(\d{4})", date_str)
        if m:
            year = m.group(1)

        try:
            position = int(re.sub(r"[^\d]", "", position_str)) if position_str else 0
        except ValueError:
            position = 0

        try:
            popularity = int(re.sub(r"[^\d]", "", pop_str)) if pop_str else 0
        except ValueError:
            popularity = 0

        time_sec = parse_time_to_seconds(time_str)
        margin_sec = parse_margin(margin_str)

        prize_val = 0
        if prize_str:
            prize_clean = re.sub(r"[^\d]", "", prize_str)
            if prize_clean:
                prize_val = int(prize_clean)

        # 距離を数字で抽出
        dist_num = ""
        m2 = re.search(r"(\d{3,4})", dist_str)
        if m2:
            dist_num = m2.group(1)

        grade = detect_grade(race_name)

        results.append({
            "horse_name": horse_name,
            "horse_url": horse_url,
            "race_name": race_name,
            "year": year,
            "venue": venue_str,
            "position": position,
            "grade": grade,
            "distance": dist_num,
            "time": time_sec if time_sec else "",
            "popularity": popularity,
            "margin": margin_sec,
            "prize_man": prize_val,
        })

    return results


def main():
    print("=== Step 2: レース成績取得 ===")

    import requests

    horses_path = "horses.csv"
    if not Path(horses_path).exists():
        print(f"ERROR: {horses_path} が見つかりません。Step 1 を先に実行してください。")
        sys.exit(1)

    with open(horses_path, encoding="utf-8") as f:
        horses = list(csv.DictReader(f))

    print(f"{len(horses)}頭の成績を取得します")

    session = requests.Session()
    all_results = []
    errors = []

    for i, horse in enumerate(horses):
        name = horse["name"]
        url = horse["url"]
        print(f"[{i+1}/{len(horses)}] {name} ...")

        try:
            results = fetch_horse_results(name, url, session)
            all_results.extend(results)
            print(f"  → {len(results)}戦取得")
        except Exception as e:
            msg = f"{name}\t{url}\t{e}"
            print(f"  ERROR: {e}")
            errors.append(msg)

        # 2〜3秒ランダムウェイト
        if i < len(horses) - 1:
            time.sleep(random.uniform(2, 3))

    # results.csv に保存
    with open("results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_results)
    print(f"\nresults.csv に {len(all_results)} 件保存しました")

    # errors.txt に保存
    if errors:
        with open("errors.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(errors) + "\n")
        print(f"errors.txt にエラー {len(errors)} 件を記録しました")
    else:
        print("エラーなし")

    print("完了")


if __name__ == "__main__":
    main()
