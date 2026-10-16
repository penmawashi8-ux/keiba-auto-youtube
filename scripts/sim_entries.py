#!/usr/bin/env python3
"""実際のレースのシミュレーション用: netkeiba の出馬表から出走（登録）馬を集める。

枠順が決まる前は「登録馬」（馬番は空欄）、決まった後は枠番・馬番も入る。
同じスクリプトを枠順確定後にもう一度走らせれば、馬番入りの一覧に上書きされる。

1. その日のレース一覧（race_list_sub）から、レース名でレースIDを探す
2. 出馬表（shutuba）から、馬名・馬ID・性齢・斤量・騎手・調教師・枠番・馬番とレース条件を取る

出力: sim/entries.json
  {race_id, date, race_name, venue, surface, distance, direction, course, post_time, drawn,
   horses: [{horse_id, horse, sex, age, weight, jockey, trainer, waku, umaban}, ...]}

使い方: python scripts/sim_entries.py 20261018 秋華賞
"""

import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from sim_fetch import JRA_VENUES, get, to_float  # noqa: E402

SIM_DIR = Path("sim")
OUT = SIM_DIR / "entries.json"
LIST_URL = "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={date}"
SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
# race_id の5〜6桁目 = 競馬場コード
VENUE_CODES = {"01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京", "06": "中山", "07": "中京",
               "08": "京都", "09": "阪神", "10": "小倉"}


def find_race_id(session, date: str, name: str) -> str:
    soup = BeautifulSoup(get(session, LIST_URL.format(date=date)), "html.parser")
    found = []
    for a in soup.find_all("a", href=re.compile(r"race_id=\d{12}")):
        rid = re.search(r"race_id=(\d{12})", a["href"]).group(1)
        text = a.get_text(" ", strip=True)
        found.append((rid, text))
        if name in text:
            return rid
    print("レース一覧のリンク:", found[:40])
    raise SystemExit(f"{date} のレース一覧に「{name}」が見つかりません")


def fetch_entries(session, race_id: str, date: str) -> dict:
    html = get(session, SHUTUBA_URL.format(race_id=race_id))
    soup = BeautifulSoup(html, "html.parser")
    name = soup.select_one(".RaceName")
    data1 = soup.select_one(".RaceData01")
    info = data1.get_text(" ", strip=True) if data1 else ""
    course = re.search(r"(芝|ダ)\s*(\d{3,4})m\s*\(([^)]*)\)", info)
    post = re.search(r"(\d{1,2}:\d{2})発走", info)
    horses = []
    for tr in soup.select("tr.HorseList"):
        a = tr.select_one("td.HorseInfo a[href*='/horse/']") or tr.find("a", href=re.compile(r"/horse/\d+"))
        if not a:
            continue
        hid = re.search(r"/horse/(\w+)", a["href"]).group(1)
        tds = tr.find_all("td")
        texts = [td.get_text(" ", strip=True) for td in tds]
        sexage = next((re.match(r"(牡|牝|セ)(\d+)", t) for t in texts if re.match(r"(牡|牝|セ)\d+$", t)), None)
        weight = next((to_float(t) for t in texts if re.fullmatch(r"\d{2}(\.\d)?", t)), None)
        waku = tr.select_one("td[class^='Waku']")
        umaban = tr.select_one("td[class^='Umaban']")
        jockey = tr.select_one("td.Jockey a")
        trainer = tr.select_one("td.Trainer a")
        horses.append({
            "horse_id": hid, "horse": a.get_text(strip=True) or a.get("title", ""),
            "sex": sexage.group(1) if sexage else "", "age": int(sexage.group(2)) if sexage else None,
            "weight": weight,
            "jockey": jockey.get_text(strip=True) if jockey else "",
            "trainer": trainer.get_text(strip=True) if trainer else "",
            "waku": int(waku.get_text(strip=True)) if waku and waku.get_text(strip=True).isdigit() else None,
            "umaban": int(umaban.get_text(strip=True)) if umaban and umaban.get_text(strip=True).isdigit() else None,
        })
    # 出馬表に同じ馬の行が2つ出ることがある（馬番なしの行が重複）。馬番のある行を残す
    uniq = {}
    for h in horses:
        if h["horse_id"] not in uniq or (h["umaban"] and not uniq[h["horse_id"]]["umaban"]):
            uniq[h["horse_id"]] = h
    if len(uniq) < len(horses):
        print(f"[注意] 出馬表の重複行を除いた: {len(horses)}行 → {len(uniq)}頭")
    horses = list(uniq.values())
    if not horses:
        print(html[:3000])
        raise SystemExit("出馬表に馬が見つかりません（ページ形式が変わった可能性）")
    return {
        "race_id": race_id, "date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
        "race_name": name.get_text(" ", strip=True) if name else "",
        "venue": VENUE_CODES.get(race_id[4:6], ""),
        "surface": course.group(1) if course else "", "distance": int(course.group(2)) if course else None,
        "direction": ("右" if "右" in course.group(3) else "左" if "左" in course.group(3) else "") if course else "",
        "course": course.group(3) if course else "", "post_time": post.group(1) if post else "",
        "drawn": all(h["umaban"] for h in horses), "horses": horses,
    }


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: sim_entries.py YYYYMMDD レース名")
    date, name = sys.argv[1], sys.argv[2]
    SIM_DIR.mkdir(exist_ok=True)
    session = requests.Session()
    rid = find_race_id(session, date, name)
    ent = fetch_entries(session, rid, date)
    assert ent["venue"] in JRA_VENUES, ent["venue"]
    OUT.write_text(json.dumps(ent, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{ent['race_name']}（{ent['venue']} {ent['surface']}{ent['distance']}m {ent['course']}・"
          f"{ent['post_time']}発走） race_id={rid}")
    print(f"{'枠順確定' if ent['drawn'] else '枠順未確定（登録馬）'}: {len(ent['horses'])}頭")
    for h in ent["horses"]:
        print(f"  {h['waku'] or '-'} {h['umaban'] or '-':>2} {h['horse']}（{h['sex']}{h['age']}・{h['weight']}kg）"
              f" {h['jockey']} / {h['trainer']}  id={h['horse_id']}")
    print(f"保存: {OUT}")


if __name__ == "__main__":
    main()
