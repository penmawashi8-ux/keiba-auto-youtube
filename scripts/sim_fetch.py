#!/usr/bin/env python3
"""夢の対決シミュレーション: netkeiba から JRA芝の重賞（G1/G2）全出走馬の成績を集める。

1. 年ごとの重賞日程ページから JRA芝1600〜3200m の G1/G2 のレースIDと出走条件を集める
2. 各レース結果ページから、レース条件（距離・馬場・累計ペース）と全出走馬の成績を取る

取得済みのレースは sim/races_cache.json に保存し、次回は取得し直さない。
出力: sim/races.json
  [{race_id, year, date, race_name, grade, venue, distance, going, cond, age2, age3, mare,
    pace_cum: [累計ペース], runners: [{pos, horse_id, horse, sex, age, weight, time, margin,
                                       passing, last3f, odds, pop}, ...]}, ...]

使い方: python scripts/sim_fetch.py [開始年] [終了年]
"""

import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SIM_DIR = Path("sim")
CACHE = SIM_DIR / "races_cache.json"
OUT = SIM_DIR / "races.json"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "ja,en-US;q=0.9",
}
JRA_VENUES = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
SCHEDULE_URL = "https://race.netkeiba.com/top/schedule.html?year={year}"
DIST_MIN, DIST_MAX = 1600, 3200
WAIT_SEC = 1.0


def get(session: requests.Session, url: str) -> str:
    time.sleep(WAIT_SEC)
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt == 2:
                raise
            print(f"  再試行 {url}: {e}")
            time.sleep(5 * (attempt + 1))
    # netkeiba は EUC-JP（ページによっては UTF-8）。meta の charset に従う
    m = re.search(rb'charset=["\']?([\w-]+)', r.content[:3000])
    r.encoding = m.group(1).decode() if m else "EUC-JP"
    return r.text


def parse_time(s: str) -> float | None:
    s = s.strip()
    m = re.fullmatch(r"(\d+):(\d+(?:\.\d)?)", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    return float(s) if re.fullmatch(r"\d+\.\d", s) else None


def to_float(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def fetch_schedule(session, year: int) -> list[dict]:
    """重賞日程ページから、その年の JRA芝 G1/G2 のレースIDと条件を取る"""
    soup = BeautifulSoup(get(session, SCHEDULE_URL.format(year=year)), "html.parser")
    table = soup.select_one("table.race_table_01")
    if not table:
        return []
    out = []
    for tr in table.find_all("tr")[1:]:
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        a = tr.find("a", href=re.compile(r"/race/\d{12}/"))
        if len(tds) < 6 or not a:
            continue
        name, grade, venue, dist, cond = tds[1], tds[2], tds[3], tds[4], tds[5]
        m = re.match(r"芝(\d+)", dist)
        if grade not in ("G1", "G2") or venue not in JRA_VENUES or not m:
            continue
        if not (DIST_MIN <= int(m.group(1)) <= DIST_MAX):
            continue
        out.append({"race_id": re.search(r"/race/(\d{12})/", a["href"]).group(1), "year": year,
                    "race_name": name, "grade": grade, "venue": venue, "distance": int(m.group(1)),
                    "cond": cond, "age2": "2歳" in cond,
                    "age3": cond.startswith("3歳") and "3歳上" not in cond, "mare": "牝" in cond})
    return out


def fetch_race(session, race_id: str) -> dict | None:
    """レース結果ページから、レース条件と全出走馬の成績を取る"""
    soup = BeautifulSoup(get(session, f"https://db.netkeiba.com/race/{race_id}/"), "html.parser")
    table, heads = None, []
    for t in soup.find_all("table"):
        first = t.find("tr")
        heads = [c.get_text(strip=True) for c in first.find_all(["th", "td"])] if first else []
        if {"着順", "馬名", "タイム"} <= set(heads):
            table = t
            break
    if not table:
        return None
    col = {h: i for i, h in enumerate(heads)}
    text = soup.get_text(" ", strip=True)
    dist = re.search(r"芝[^\d]{0,6}(\d{4})m", text)
    # 重賞日程のリンクが別レース（ダートなど）を指していることがあるので、コースの種類も記録する
    surface = re.search(r"(芝|ダ)[右左直外内\s]{0,4}\d{3,4}m", text)
    going = re.search(r"芝\s*:\s*(良|稍|重|不)", text)
    date = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    # 「ペース 12.7 - 24.0 - 35.5 ...」= スタートからの累計タイム（距離が200で割り切れないと最初が100m）
    pace = re.search(r"ペース\s*((?:[\d.]+\s*-\s*)+[\d.]+)", text)

    def cell(tds, name):
        i = col.get(name)
        return tds[i].get_text(strip=True) if i is not None and i < len(tds) else ""

    runners = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        link = tds[col["馬名"]].find("a", href=re.compile(r"/horse/\w+"))
        sexage = re.match(r"(牡|牝|セ)(\d+)", cell(tds, "性齢"))
        pos = cell(tds, "着順")
        runners.append({
            "pos": int(pos) if pos.isdigit() else None,
            "horse_id": re.search(r"/horse/(\w+)", link["href"]).group(1) if link else "",
            "horse": cell(tds, "馬名"),
            "sex": sexage.group(1) if sexage else "", "age": int(sexage.group(2)) if sexage else None,
            "weight": to_float(cell(tds, "斤量")), "time": parse_time(cell(tds, "タイム")),
            "margin": cell(tds, "着差"), "passing": cell(tds, "通過"),
            "last3f": to_float(cell(tds, "上り")), "odds": to_float(cell(tds, "単勝")),
            "pop": int(cell(tds, "人気")) if cell(tds, "人気").isdigit() else None,
        })
    return {
        "distance_page": int(dist.group(1)) if dist else None,
        "surface_page": surface.group(1) if surface else None,
        "going": going.group(1) if going else "良",
        "date": f"{date.group(1)}-{int(date.group(2)):02d}-{int(date.group(3)):02d}" if date else "",
        "pace_cum": [float(v) for v in re.findall(r"[\d.]+", pace.group(1))] if pace else [],
        "runners": runners,
    }


def main() -> None:
    y0, y1 = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) >= 3 else (2003, 2025)
    SIM_DIR.mkdir(exist_ok=True)
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    print(f"キャッシュ: {len(cache)}レース")
    session = requests.Session()

    sched = []
    for year in range(y0, y1 + 1):
        rows = fetch_schedule(session, year)
        sched += rows
        print(f"  {year}: G1/G2 芝{DIST_MIN}〜{DIST_MAX}m {len(rows)}レース")

    todo = [s for s in sched if s["race_id"] not in cache]
    print(f"結果ページ: 取得 {len(todo)} / キャッシュ利用 {len(sched) - len(todo)}")
    for i, s in enumerate(todo, 1):
        res = fetch_race(session, s["race_id"])
        if not res or not any(x["pos"] for x in res["runners"]):
            continue      # まだ行われていないレース（結果なし）はキャッシュせず、次回また取りにいく
        cache[s["race_id"]] = res
        if i % 50 == 0 or i == len(todo):
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(todo)}")

    races, dropped = [], []
    for s in sched:
        r = cache.get(s["race_id"]) or {}
        if not r.get("runners"):
            dropped.append((s["race_id"], s["race_name"], "結果なし"))
            continue
        # 重賞日程のリンク誤り対策: 結果ページの距離と日程の距離が違う行は捨てる
        if r.get("distance_page") and r["distance_page"] != s["distance"]:
            dropped.append((s["race_id"], s["race_name"], f"距離不一致 {s['distance']}≠{r['distance_page']}"))
            continue
        if r.get("surface_page") and r["surface_page"] != "芝":
            dropped.append((s["race_id"], s["race_name"], "芝ではない（日程のリンク誤り）"))
            continue
        races.append({**s, **{k: v for k, v in r.items() if k not in ("distance_page", "surface_page")}})
    for d in dropped:
        print(f"  除外: {d}")
    OUT.write_text(json.dumps(races, ensure_ascii=False), encoding="utf-8")
    n_run = sum(len(r["runners"]) for r in races)
    print(f"保存: {OUT}（{len(races)}レース / のべ{n_run}頭）")
    if len(races) < 500:
        raise RuntimeError("取得できたレースが少なすぎます（ページ形式が変わった可能性）")


if __name__ == "__main__":
    main()
