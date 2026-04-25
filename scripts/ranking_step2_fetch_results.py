#!/usr/bin/env python3
"""Step 2: horses.csv の各馬のレース成績を uma-channel.jp から取得して results.csv に保存"""

import csv
import re
import sys
from pathlib import Path

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

RESULT_FIELDNAMES = [
    "horse_name", "horse_url", "race_name", "year", "venue",
    "position", "grade", "distance", "time", "popularity", "margin", "prize_man",
]

GRADE_PATTERNS = [
    (re.compile(r"G1|GI", re.IGNORECASE), "G1"),
    (re.compile(r"G2|GII", re.IGNORECASE), "G2"),
    (re.compile(r"G3|GIII", re.IGNORECASE), "G3"),
    (re.compile(r"OP|オープン|L\b", re.IGNORECASE), "OP"),
]


def detect_grade(race_name):
    for pat, grade in GRADE_PATTERNS:
        if pat.search(race_name):
            return grade
    return "OP"


def parse_time_to_seconds(time_str):
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
    if not margin_str or margin_str.strip() in ("-", "同着", ""):
        return 0.0
    s = margin_str.strip()
    mapping = {
        "ハナ": 0.1, "クビ": 0.2, "アタマ": 0.15,
        "1/2": 0.3, "3/4": 0.4, "1.1/4": 0.7,
        "1.1/2": 0.9, "1.3/4": 1.0, "2.1/2": 1.5,
    }
    for k, v in mapping.items():
        if k in s:
            return v
    m = re.search(r"(\d+)", s)
    return float(m.group(1)) * 0.6 if m else 0.0


def fetch_uma_channel_results(horse_name, horse_url, session):
    """uma-channel.jp の個別馬ページからレース成績を取得"""
    from bs4 import BeautifulSoup

    resp = session.get(horse_url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code}: {horse_url}")
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    # 成績テーブルを探す: "着" "レース" などが含まれるテーブル
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        header_text = rows[0].get_text()
        if not any(k in header_text for k in ["着", "レース", "年", "距離"]):
            continue

        headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]

        def ci(candidates):
            for c in candidates:
                for i, h in enumerate(headers):
                    if c in h:
                        return i
            return None

        idx_date  = ci(["年月日", "日付", "年"])
        idx_race  = ci(["レース名", "レース"])
        idx_pos   = ci(["着順", "着"])
        idx_time  = ci(["タイム"])
        idx_margin = ci(["着差"])
        idx_pop   = ci(["人気"])
        idx_prize = ci(["賞金"])
        idx_venue = ci(["開催", "場"])
        idx_dist  = ci(["距離", "コース"])

        if idx_race is None and idx_pos is None:
            continue  # このテーブルは成績表でない

        for row in rows[1:]:
            cols = row.find_all("td")
            if not cols:
                continue

            def gc(idx):
                if idx is None or idx >= len(cols):
                    return ""
                return cols[idx].get_text(strip=True)

            date_str  = gc(idx_date)
            race_name = gc(idx_race)
            pos_str   = gc(idx_pos)

            if not race_name and not date_str:
                continue

            year = ""
            m = re.search(r"(\d{4})", date_str)
            if m:
                year = m.group(1)

            try:
                position = int(re.sub(r"[^\d]", "", pos_str)) if pos_str else 0
            except ValueError:
                position = 0

            try:
                popularity = int(re.sub(r"[^\d]", "", gc(idx_pop))) if idx_pop is not None else 0
            except ValueError:
                popularity = 0

            time_sec   = parse_time_to_seconds(gc(idx_time))
            margin_sec = parse_margin(gc(idx_margin))

            prize_val = 0
            if idx_prize is not None:
                pz = re.sub(r"[^\d]", "", gc(idx_prize))
                if pz:
                    prize_val = int(pz)

            dist_num = ""
            m2 = re.search(r"(\d{3,4})", gc(idx_dist) if idx_dist is not None else "")
            if m2:
                dist_num = m2.group(1)

            grade = detect_grade(race_name)

            results.append({
                "horse_name": horse_name,
                "horse_url":  horse_url,
                "race_name":  race_name,
                "year":       year,
                "venue":      gc(idx_venue),
                "position":   position,
                "grade":      grade,
                "distance":   dist_num,
                "time":       time_sec if time_sec else "",
                "popularity": popularity,
                "margin":     margin_sec,
                "prize_man":  prize_val,
            })

        if results:
            return results  # 最初に見つかった有効テーブルを使用

    raise ValueError(f"成績テーブルが見つかりません: {horse_url}")


def make_synthetic_results(horse_name, horse_url, g1_wins, birth_year):
    """成績取得失敗時: horses.csv のG1勝利数からスコア計算用の合成レコードを生成"""
    results = []
    year = str(birth_year + 3) if birth_year > 1900 else "2000"
    for i in range(g1_wins):
        results.append({
            "horse_name": horse_name,
            "horse_url":  horse_url,
            "race_name":  f"G1レース{i+1}",
            "year":       year,
            "venue":      "",
            "position":   1,
            "grade":      "G1",
            "distance":   "2000",
            "time":       "",
            "popularity": 1,
            "margin":     0.5,
            "prize_man":  10000,
        })
    return results


MAX_WORKERS = 5


def _fetch_one(args):
    """並列取得用ワーカー: (index, horse_dict) → (index, results, error_str|None, g1_count)"""
    import requests as _requests

    i, horse = args
    name = horse["name"]
    url = horse["url"]
    g1_wins = int(horse.get("g1_wins", 0))
    birth_year = int(horse.get("birth_year", 0))

    session = _requests.Session()
    try:
        results = fetch_uma_channel_results(name, url, session)
        g1_count = sum(1 for r in results if r["grade"] == "G1" and r["position"] == 1)
        return i, results, None, g1_count
    except Exception as e:
        synthetic = make_synthetic_results(name, url, g1_wins, birth_year)
        return i, synthetic, str(e), 0


def main():
    print("=== Step 2: レース成績取得 (uma-channel.jp) ===")

    horses_path = "horses.csv"
    if not Path(horses_path).exists():
        print(f"ERROR: {horses_path} が見つかりません。Step 1 を先に実行してください。")
        sys.exit(1)

    with open(horses_path, encoding="utf-8") as f:
        horses = list(csv.DictReader(f))

    total = len(horses)
    print(f"{total}頭の成績を取得します (並列 {MAX_WORKERS} ワーカー)")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results_by_idx = {}
    errors = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, (i, h)): i for i, h in enumerate(horses)}
        for fut in as_completed(futures):
            i, results, err, g1_count = fut.result()
            name = horses[i]["name"]
            url = horses[i]["url"]
            if err:
                print(f"[{i+1}/{total}] {name} WARNING: 取得失敗 → 合成レコード生成")
                errors.append(f"{name}\t{url}\t{err}")
            else:
                print(f"[{i+1}/{total}] {name}: {len(results)}戦取得 (G1勝利:{g1_count})")
            results_by_idx[i] = results

    all_results = []
    for i in range(total):
        all_results.extend(results_by_idx[i])

    with open("results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_results)
    print(f"\nresults.csv に {len(all_results)} 件保存しました")

    if errors:
        with open("errors.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(errors) + "\n")
        print(f"errors.txt にエラー {len(errors)} 件を記録しました")
    else:
        print("エラーなし")

    print("完了")


if __name__ == "__main__":
    main()
