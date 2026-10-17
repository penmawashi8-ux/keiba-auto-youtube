#!/usr/bin/env python3
"""実際のレースのシミュレーション用: 出走馬が過去2年に走ったレースの「一緒に走った馬」を集める。

能力の物差しは「強い相手に何馬身差だったか」で作るので、出走馬だけでなく対戦相手の成績も要る。
今年の重賞は日程ページからレースIDが取れない（特集ページへのリンクになっている）ため、
出走馬の成績にあるレースIDから結果ページを開き、全出走馬のIDを集める（重賞・条件戦とも）。

入力: sim/entries.json, sim/horses_cache.json（出走馬の成績は取得済みであること）
出力: sim/related_ids.txt（対戦相手の馬ID。sim_fetch_horses.py の追加の馬IDに渡す）
      sim/related_refresh.txt（取得済みだが成績が古い若い馬。--refresh に渡す）
      結果ページは sim/races_cache.json にキャッシュする（sim_fetch.py と共用）

使い方: python scripts/sim_fetch_related.py
"""

import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from sim_fetch import CACHE, fetch_race  # noqa: E402

SIM_DIR = Path("sim")
YEARS_BACK = 2


def main() -> None:
    ent = json.loads((SIM_DIR / "entries.json").read_text(encoding="utf-8"))
    careers = json.loads((SIM_DIR / "horses_cache.json").read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    since = f"{int(ent['date'][:4]) - YEARS_BACK}{ent['date'][4:]}"
    race_ids = sorted({r["race_id"] for h in ent["horses"] for r in careers.get(h["horse_id"], [])
                       if r.get("surface") == ent["surface"] and r.get("date", "") >= since})
    todo = [rid for rid in race_ids if rid not in cache]
    print(f"出走馬が過去{YEARS_BACK}年に走った{ent['surface']}のレース {len(race_ids)} / 取得 {len(todo)}")
    session = requests.Session()
    for i, rid in enumerate(todo, 1):
        res = fetch_race(session, rid)
        if res and any(x["pos"] for x in res["runners"]):
            cache[rid] = res
        if i % 20 == 0:
            print(f"  {i}/{len(todo)}")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    own = {h["horse_id"] for h in ent["horses"]}
    ids = sorted({x["horse_id"] for rid in race_ids for x in cache.get(rid, {}).get("runners", [])
                  if x["horse_id"]} - own)
    (SIM_DIR / "related_ids.txt").write_text("\n".join(ids) + "\n", encoding="utf-8")
    # 取得済みでも、若い馬（生まれ年 = 馬IDの先頭4桁 が4年以内）は成績が増えているので取り直す
    young = int(ent["date"][:4]) - 4
    stale = [h for h in ids if h in careers and h[:4].isdigit() and int(h[:4]) >= young]
    (SIM_DIR / "related_refresh.txt").write_text("\n".join(stale) + "\n", encoding="utf-8")
    new = sum(1 for h in ids if h not in careers)
    print(f"対戦相手 {len(ids)}頭（成績が未取得 {new}頭・取り直し {len(stale)}頭）→ sim/related_ids.txt")


if __name__ == "__main__":
    main()
