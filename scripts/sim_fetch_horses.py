#!/usr/bin/env python3
"""夢の対決シミュレーション: 重賞に出走した全馬の「全成績」を netkeiba の馬ページから集める。

重賞だけでは1頭あたりの成績が少なすぎる（中央値2走）ため、条件戦・OP・G3も含めた
全キャリアを取る。取得済みの馬は sim/horses_cache.json に保存し、次回は取り直さない。
1回の実行は時間で区切り（既定 45 分）、残りは次回の実行で続きから取る。

入力: sim/races.json（sim_fetch.py の出力。ここに出てくる馬が対象）
出力: sim/horses_cache.json  {horse_id: [成績行, ...]}
      全馬そろったら sim/horses_done を作る
成績行: race_id, date, venue, race_name, field, pos, weight, surface, distance, going,
        time, margin(秒・勝ち馬は2着との差で負), passing, pace(前半3F-後半3F), last3f, pop

使い方: python scripts/sim_fetch_horses.py [制限時間(分)] [追加の馬ID ...] [--refresh 取り直す馬ID ...]
  --refresh の後の馬は、取得済みでも最新の成績を取り直す（これから走るレースの出走馬など）
"""

import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from sim_fetch import HEADERS, JRA_VENUES, get, parse_time, to_float  # noqa: E402

SIM_DIR = Path("sim")
CACHE = SIM_DIR / "horses_cache.json"
DONE = SIM_DIR / "horses_done"


def fetch_horse_rows(session, horse_id: str) -> list[dict] | None:
    soup = BeautifulSoup(get(session, f"https://db.netkeiba.com/horse/result/{horse_id}/"), "html.parser")
    table, heads = None, []
    for t in soup.find_all("table"):
        first = t.find("tr")
        heads = [c.get_text(strip=True) for c in first.find_all(["th", "td"])] if first else []
        if {"レース名", "タイム", "着順"} <= set(heads):
            table = t
            break
    if not table:
        return None
    col = {h: i for i, h in enumerate(heads)}
    rows = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < len(heads) - 3:
            continue

        def g(name):
            i = col.get(name)
            return tds[i].get_text(strip=True) if i is not None and i < len(tds) else ""

        a = tds[col["レース名"]].find("a", href=True)
        m = re.search(r"/race/(\d{12})/", a["href"]) if a else None
        dist = re.match(r"(芝|ダ|障)(\d+)", g("距離"))
        venue = next((v for v in JRA_VENUES if v in g("開催")), "")
        if not m or not dist or not venue:
            continue   # 海外・地方は対象外
        rows.append({
            "race_id": m.group(1), "date": g("日付").replace("/", "-"), "venue": venue,
            "race_name": g("レース名"), "field": int(g("頭数")) if g("頭数").isdigit() else None,
            "pos": int(g("着順")) if g("着順").isdigit() else None, "weight": to_float(g("斤量")),
            "surface": dist.group(1), "distance": int(dist.group(2)), "going": g("馬場")[:1] or "良",
            "time": parse_time(g("タイム")), "margin": to_float(g("着差")), "passing": g("通過"),
            "pace": g("ペース"), "last3f": to_float(g("上り")),
            "pop": int(g("人気")) if g("人気").isdigit() else None,
        })
    return rows


def main() -> None:
    budget_min = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    args = sys.argv[2:]
    refresh = set(args[args.index("--refresh") + 1:]) if "--refresh" in args else set()
    extra = [a for a in args if a != "--refresh"]
    races = json.loads((SIM_DIR / "races.json").read_text(encoding="utf-8"))
    ids = sorted({x["horse_id"] for r in races for x in r["runners"] if x["horse_id"]} | set(extra))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = [h for h in refresh] + [h for h in ids if h not in cache and h not in refresh]
    print(f"対象 {len(ids)}頭 / 取得済み {len(ids) - len(todo)} / 残り {len(todo)}（制限 {budget_min:.0f}分）")
    session = requests.Session()
    t0 = time.time()
    for i, hid in enumerate(todo, 1):
        if time.time() - t0 > budget_min * 60:
            print(f"制限時間に達したので中断（{i - 1}頭取得）。続きは次回の実行で取る")
            break
        try:
            cache[hid] = fetch_horse_rows(session, hid) or []
        except requests.RequestException as e:
            print(f"  {hid}: 取得失敗 {e}")
            continue
        if i % 100 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(todo)}  ({(time.time() - t0) / 60:.1f}分)")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    left = sum(1 for h in ids if h not in cache)
    n_rows = sum(len(v) for v in cache.values())
    print(f"保存: {CACHE}（{len(cache)}頭 / のべ{n_rows}走）残り {left}頭")
    if left == 0:
        DONE.write_text("ok", encoding="utf-8")


if __name__ == "__main__":
    main()
