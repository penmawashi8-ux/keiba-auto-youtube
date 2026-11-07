"""WIN5 バックテスト用データ収集（netkeiba）。

指定期間の土日月について WIN5 ページを確認し、対象5レースの
  - 5走表示（出走馬ごとの近走セル生テキスト）
  - 結果（着順表）
を取得して win5_bt_data/ に保存する。既に保存済みのレースはスキップ（再開可能）。

使い方: python scripts/win5_bt/collect.py 2025-11-01 2026-11-07 [max_minutes]
"""
import datetime as dt
import gzip
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
OUT = Path("win5_bt_data")
RACES = OUT / "races"
DAYS = OUT / "win5_days.jsonl"
SLEEP = 1.2
SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA


def get(url: str) -> str:
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
            m = re.search(rb'charset=["\']?([\w-]+)', r.content[:3000])
            enc = m.group(1).decode() if m else "utf-8"
            time.sleep(SLEEP)
            return r.content.decode(enc, errors="replace")
        except Exception as e:  # noqa: BLE001
            print(f"  retry {attempt + 1}: {url} {e}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"failed: {url}")


def txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def parse_win5_day(html: str, date: str):
    soup = BeautifulSoup(html, "html.parser")
    body = txt(soup.body or soup)
    if f"{date[:4]}年{int(date[4:6])}月{int(date[6:])}日" not in body and \
            f"{date[:4]}年{date[4:6]}月{date[6:]}日のWIN5" not in body:
        return None
    ids = []
    for a in soup.find_all("a", href=True):
        m = re.search(r"race_id=(\d{12})", a["href"])
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    m_pay = re.search(r"払戻金\s*([0-9万億円,]+)", body)
    m_hit = re.search(r"的中票数\s*([0-9,]+)票", body)
    m_sales = re.search(r"発売票数\s*([0-9,]+)票", body)
    m_nums = re.search(r"的中馬番\s*((?:\d+\s+){4}\d+)", body)
    if len(ids) < 5:
        return None
    return {
        "date": date,
        "race_ids": ids[:5],
        "payout": m_pay.group(1) if m_pay else None,
        "hit_tickets": m_hit.group(1) if m_hit else None,
        "sales_tickets": m_sales.group(1) if m_sales else None,
        "win_nums": m_nums.group(1).split() if m_nums else None,
    }


def parse_past(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    meta = {
        "name": txt(soup.select_one(".RaceName")),
        "data1": txt(soup.select_one(".RaceData01")),
        "data2": txt(soup.select_one(".RaceData02")),
    }
    horses = []
    for tr in soup.select("tr.HorseList"):
        h = {"cells": []}
        for td in tr.find_all("td", recursive=False):
            cls = " ".join(td.get("class", []))
            h["cells"].append([cls, txt(td)])
        horses.append(h)
    return {"meta": meta, "horses": horses}


def parse_result(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("table.RaceTable01 tr"):
        cells = [txt(td) for td in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)
    return rows


def main() -> None:
    start = dt.date.fromisoformat(sys.argv[1])
    end = dt.date.fromisoformat(sys.argv[2])
    max_min = float(sys.argv[3]) if len(sys.argv) > 3 else 150
    t0 = time.time()
    RACES.mkdir(parents=True, exist_ok=True)
    known = {}
    if DAYS.exists():
        for line in DAYS.read_text().splitlines():
            d = json.loads(line)
            known[d["date"]] = d
    checked_file = OUT / "checked_dates.txt"
    checked = set(checked_file.read_text().split()) if checked_file.exists() else set()

    d = start
    while d <= end:
        ds = d.strftime("%Y%m%d")
        if d.weekday() in (5, 6, 0) and ds not in checked and ds not in known:
            try:
                info = parse_win5_day(get(f"https://race.netkeiba.com/top/win5.html?date={ds}"), ds)
            except Exception as e:  # noqa: BLE001
                print(f"{ds}: day error {e}")
                info = None
            if info:
                known[ds] = info
                with DAYS.open("a") as f:
                    f.write(json.dumps(info, ensure_ascii=False) + "\n")
                print(f"{ds}: WIN5 {info['race_ids']} {info['payout']}")
            else:
                with checked_file.open("a") as f:
                    f.write(ds + "\n")
        d += dt.timedelta(days=1)

    for ds in sorted(known):
        for rid in known[ds]["race_ids"]:
            path = RACES / f"{rid}.json.gz"
            if path.exists():
                continue
            if (time.time() - t0) / 60 > max_min:
                print("time limit reached; stopping (resume on next run)")
                return
            try:
                past = parse_past(get(f"https://race.netkeiba.com/race/shutuba_past.html?race_id={rid}"))
                res = parse_result(get(f"https://race.netkeiba.com/race/result.html?race_id={rid}"))
            except Exception as e:  # noqa: BLE001
                print(f"{rid}: error {e}")
                continue
            rec = {"race_id": rid, "date": ds, **past, "result": res}
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            print(f"{ds} {rid}: {len(past['horses'])} horses, {len(res)} result rows")


if __name__ == "__main__":
    main()
