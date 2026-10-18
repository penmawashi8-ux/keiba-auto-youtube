#!/usr/bin/env python3
"""種牡馬の産駒の獲得賞金ランキングを netkeiba から作る（横動画「〇〇産駒 獲得賞金ランキング」用）。

1. netkeiba の馬検索を「父＝種牡馬名」で引き、産駒の一覧（獲得賞金つき）を集める
2. 獲得賞金の上位 N 頭について、馬のページから通算成績・主な勝鞍・母・母父などを取る
3. 競走成績から重賞の勝ち鞍（G1/Jpn1 など）を数える

出力: sim/<prefix>_ranking.json
  {sire, fetched_at, n_offspring, top: [{rank, horse_id, name, name_en, sex, birth_year, prize_man,
   prize_raw, record, main_wins, dam, bms, trainer, owner, g1_wins: [..], graded_wins: [..], starts, wins}, ...]}
検索結果のページが想定と違ったときは sim/<prefix>_search_debug.html に保存する。

使い方: python scripts/sire_ranking_fetch.py クロフネ kurofune [上位の頭数=10]
"""

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from sim_fetch import HEADERS, get  # noqa: E402
from sim_fetch_horses import fetch_horse_rows  # noqa: E402

SIM_DIR = Path("sim")
SEARCH = "https://db.netkeiba.com/"


def to_man(s: str) -> float | None:
    """「12,345.6万円」「1億2,345万円」などを万円に"""
    s = s.replace(",", "").replace(" ", "")
    m = re.search(r"(?:(\d+)億)?(\d+(?:\.\d+)?)万", s)
    if m:
        return int(m.group(1) or 0) * 10000 + float(m.group(2))
    m = re.fullmatch(r"\d+(?:\.\d+)?", s)
    return float(s) if m else None


def parse_list(html: str) -> list[dict]:
    """検索結果の表（馬名・父・獲得賞金の列があるもの）を読む"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for t in soup.find_all("table"):
        first = t.find("tr")
        heads = [c.get_text(strip=True) for c in first.find_all(["th", "td"])] if first else []
        if not any("馬名" in h for h in heads) or not any("賞金" in h for h in heads):
            continue
        col = {h: i for i, h in enumerate(heads)}
        i_name = next(i for h, i in col.items() if "馬名" in h)
        i_prize = next(i for h, i in col.items() if "賞金" in h)
        i_sire = next((i for h, i in col.items() if h == "父"), None)
        for tr in t.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) <= max(i_name, i_prize):
                continue
            a = tds[i_name].find("a", href=re.compile(r"/horse/\w+"))
            if not a:
                continue
            out.append({"horse_id": re.search(r"/horse/(\w+)", a["href"]).group(1),
                        "name": a.get_text(strip=True),
                        "sire": tds[i_sire].get_text(strip=True) if i_sire is not None and i_sire < len(tds) else "",
                        "prize_raw": tds[i_prize].get_text(strip=True),
                        "cells": [td.get_text(" ", strip=True) for td in tds]})
        if out:
            print("検索結果の列:", heads)
            break
    return out


def search(session, sire: str, prefix: str) -> list[dict]:
    enc = urllib.parse.quote(sire.encode("euc-jp"))
    base = f"{SEARCH}?pid=horse_list&word=&match=partial_match&sire={enc}&mare=&bms=&trainer=&owner=&breeder="
    variants = [base + "&sort=prize&list=100", f"{SEARCH}?pid=horse_list&sire={enc}&sort=prize&list=100"]
    for url in variants:
        print("検索:", url)
        rows, page = [], 1
        while page <= 30:
            html = get(session, url + (f"&page={page}" if page > 1 else ""))
            got = parse_list(html)
            if page == 1 and not got:
                (SIM_DIR / f"{prefix}_search_debug.html").write_text(html, encoding="utf-8")
                print("  表が見つからない（デバッグ用にHTMLを保存）")
                break
            new = [r for r in got if r["horse_id"] not in {x["horse_id"] for x in rows}]
            if not new:
                break
            rows += new
            print(f"  {page}ページ目: {len(new)}頭")
            # 賞金の多い順に並んでいれば2ページで十分。並んでいなければ全ページ（最大30）を取る
            vals = [v for v in (to_man(x["prize_raw"]) for x in rows) if v is not None]
            if page >= 2 and vals == sorted(vals, reverse=True):
                break
            page += 1
        if rows:
            return rows
    # 最後の手段: POST の検索フォーム
    data = {"pid": "horse_list", "sire": sire.encode("euc-jp"), "sort": "prize", "list": "100"}
    time.sleep(1)
    r = session.post(SEARCH, data=data, headers=HEADERS, timeout=20)
    r.encoding = "EUC-JP"
    rows = parse_list(r.text)
    if not rows:
        (SIM_DIR / f"{prefix}_search_debug_post.html").write_text(r.text, encoding="utf-8")
    return rows


def profile(session, horse_id: str) -> dict:
    soup = BeautifulSoup(get(session, f"https://db.netkeiba.com/horse/{horse_id}/"), "html.parser")
    info = {}
    for tr in soup.select("table.db_prof_table tr"):
        th, td = tr.find("th"), tr.find("td")
        if th and td:
            info[th.get_text(strip=True)] = td.get_text(" ", strip=True)
    title = soup.select_one(".horse_title")
    eng = soup.select_one(".horse_title .eng_name") or soup.select_one(".eng_name")
    blood = [a.get_text(strip=True) for a in soup.select("table.blood_table a")]
    return {"info": info, "title": title.get_text(" ", strip=True) if title else "",
            "name_en": eng.get_text(strip=True) if eng else "", "blood": blood[:14]}


GRADE = re.compile(r"\((G1|GI|Jpn1|JpnI|G2|GII|Jpn2|JpnII|G3|GIII|Jpn3|JpnIII)\)")


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: sire_ranking_fetch.py 種牡馬名 prefix [頭数]")
    sire, prefix = sys.argv[1], sys.argv[2]
    top_n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    SIM_DIR.mkdir(exist_ok=True)
    session = requests.Session()
    rows = search(session, sire, prefix)
    if not rows:
        sys.exit("産駒の一覧が取れなかった（sim/*_search_debug*.html を確認）")
    # 部分一致で別の種牡馬（名前に同じ文字列を含む）が混ざるので、父の列が完全一致のものだけ
    if any(r["sire"] for r in rows):
        rows = [r for r in rows if r["sire"] == sire]
    for r in rows:
        r["prize_man"] = to_man(r["prize_raw"])
    rows = [r for r in rows if r["prize_man"] is not None]
    rows.sort(key=lambda r: -r["prize_man"])
    print(f"{sire}産駒 {len(rows)}頭（賞金がわかる馬）")
    top = []
    for k, r in enumerate(rows[:top_n + 5], 1):     # 同額・取り違えに備えて少し多めに詳細を取る
        p = profile(session, r["horse_id"])
        info = p["info"]
        career = fetch_horse_rows(session, r["horse_id"]) or []
        won = [c for c in career if c.get("pos") == 1]
        g1 = [c["race_name"] for c in won if re.search(r"\((G1|GI|Jpn1|JpnI)\)", c["race_name"])]
        graded = [c["race_name"] for c in won if GRADE.search(c["race_name"])]
        prize_prof = to_man(info.get("獲得賞金", "").split("(")[0]) if info.get("獲得賞金") else None
        h = {"rank": k, "horse_id": r["horse_id"], "name": r["name"], "name_en": p["name_en"],
             "title": p["title"], "prize_man": r["prize_man"], "prize_raw": r["prize_raw"],
             "prize_profile": info.get("獲得賞金", ""), "prize_profile_man": prize_prof,
             "birth": info.get("生年月日", ""), "trainer": info.get("調教師", ""), "owner": info.get("馬主", ""),
             "breeder": info.get("生産者", ""), "record": info.get("通算成績", ""), "main_wins": info.get("主な勝鞍", ""),
             "blood": p["blood"], "g1_wins": g1, "graded_wins": graded,
             "win_races": [f"{c.get('date', '')} {c['race_name']}" for c in won],
             "starts": len([c for c in career if c.get("pos") is not None]), "wins": len(won),
             "list_cells": r["cells"]}
        top.append(h)
        print(f"{k:>2}. {h['name']}（{h['name_en']}） {h['prize_raw']} / 通算 {h['record']} / G1 {len(g1)}勝 {g1}")
    out = {"sire": sire, "fetched_at": time.strftime("%Y-%m-%d %H:%M"), "n_offspring": len(rows),
           "top": top, "next": [{"name": r["name"], "prize_raw": r["prize_raw"]} for r in rows[top_n + 5:top_n + 20]]}
    (SIM_DIR / f"{prefix}_ranking.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"保存: sim/{prefix}_ranking.json")


if __name__ == "__main__":
    main()
