"""今週末の JRA の G1 で、枠順が確定していて、まだ動画を作っていないレースを探す。

使い方: python scripts/g1_watch.py [race_id 日付YYYYMMDD]   （引数があればそのレースを対象にする）
出力: GITHUB_OUTPUT に found / race_id / date / prefix / name（見つからなければ found=false）

対象: 芝・ダートの G1 で、描けるコース（sim_render.COURSES）のもの。
処理済みの印: research-output の sim/auto_<race_id>.json（あれば飛ばす）。
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
import race_names as rn                     # noqa: E402
import sim_entries as se                     # noqa: E402
import sim_render as sr                      # noqa: E402
from sim_fetch import get                    # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))


def done(race_id: str) -> bool:
    r = subprocess.run(["git", "cat-file", "-e", f"FETCH_HEAD:sim/auto_{race_id}.json"], capture_output=True)
    return r.returncode == 0


def out(**kw):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in kw.items()]
    print("\n".join(lines))
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def check(session, race_id: str, date: str, force: bool = False) -> dict | None:
    ent = se.fetch_entries(session, race_id, date)
    name = ent["race_name"]
    print(f"  {date} {race_id} {name} {ent['venue']} {ent['surface']}{ent['distance']} 枠順{'確定' if ent['drawn'] else '未確定'}")
    if not ent["drawn"]:
        return None
    if ent["surface"] not in ("芝", "ダ"):
        print("  → 馬場がわからない")
        return None
    if sr.course_key(ent["venue"], ent["distance"], ent["surface"]) not in sr.COURSES:
        print("  → このコースはまだ描けない")
        return None
    if not force and done(race_id):
        print("  → 作成済み")
        return None
    return ent


def main():
    subprocess.run(["git", "fetch", "-q", "origin", "research-output"], check=False)
    session = requests.Session()
    if len(sys.argv) >= 3 and sys.argv[1]:
        race_id, date = sys.argv[1], sys.argv[2]
        ent = check(session, race_id, date, force=True)
        if ent:
            return out(found="true", race_id=race_id, date=date, prefix=f"auto{race_id}", name=ent["race_name"])
        return out(found="false")
    today = dt.datetime.now(JST).date()
    for k in range(0, 5):
        d = today + dt.timedelta(days=k)
        if d.weekday() not in (5, 6):                      # 土日の開催を見る
            continue
        date = d.strftime("%Y%m%d")
        try:
            soup = BeautifulSoup(get(session, se.LIST_URL.format(date=date)), "html.parser")
        except Exception as e:      # noqa: BLE001
            print(date, "一覧を取れなかった:", e)
            continue
        seen = set()
        for a in soup.find_all("a", href=re.compile(r"race_id=\d{12}")):
            rid = re.search(r"race_id=(\d{12})", a["href"]).group(1)
            if rid in seen:
                continue
            seen.add(rid)
            li = a.find_parent("li") or a
            text = rn.norm(li.get_text(" ", strip=True))
            html = str(li)
            if not (any(g in text for g in rn.G1_NAMES) or "Icon_GradeType1\"" in html or "Icon_GradeType1 " in html):
                continue
            if rid[4:6] not in se.VENUE_CODES:
                continue
            print(f"G1 候補: {date} {rid} {text[:40]}")
            ent = check(session, rid, date)
            if ent:
                return out(found="true", race_id=rid, date=date, prefix=f"auto{rid}", name=ent["race_name"])
    out(found="false")


if __name__ == "__main__":
    main()
