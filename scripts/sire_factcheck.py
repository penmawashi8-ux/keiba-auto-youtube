#!/usr/bin/env python3
"""産駒ランキング動画のファクトチェック用に、netkeiba から確認材料を集めて書き出す。

1. 上位馬の全成績（地方・海外も含む。日付・開催・レース名・距離・着順）
2. 上位馬の血統表の父・母（全きょうだいの確認）
3. 種牡馬本人の全成績
4. 産駒のうち若い世代（生年 >= 指定年）の現役・抹消（中央に現役の産駒が残っているか）

入力: sim/<prefix>_ranking.json（sire_ranking_fetch.py の出力）
出力: sim/<prefix>_factcheck.txt
使い方: python scripts/sire_factcheck.py クロフネ kurofune [若い世代の生年=2017]
"""

import json
import re
import sys
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from sim_fetch import get  # noqa: E402
from sire_ranking_fetch import SEARCH, parse_list  # noqa: E402

SIM_DIR = Path("sim")


def results(session, horse_id: str) -> list[str]:
    soup = BeautifulSoup(get(session, f"https://db.netkeiba.com/horse/result/{horse_id}/"), "html.parser")
    for t in soup.find_all("table"):
        first = t.find("tr")
        heads = [c.get_text(strip=True) for c in first.find_all(["th", "td"])] if first else []
        if {"レース名", "着順"} <= set(heads):
            col = {h: i for i, h in enumerate(heads)}
            out = []
            for tr in t.find_all("tr")[1:]:
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(tds) < len(heads) - 3:
                    continue
                g = lambda n: tds[col[n]] if n in col and col[n] < len(tds) else ""  # noqa: E731
                out.append(f"{g('日付')} {g('開催')} {g('レース名')} {g('距離')} {g('頭数')}頭 {g('着順')}着")
            return out[::-1]          # 古い順
    return []


def pedigree(session, horse_id: str) -> str:
    soup = BeautifulSoup(get(session, f"https://db.netkeiba.com/horse/ped/{horse_id}/"), "html.parser")
    t = soup.select_one("table.blood_table")
    if not t:
        return "（血統表なし）"
    rows = t.find_all("tr")
    sire = rows[0].find("td").get_text(" ", strip=True) if rows else ""
    half = len(rows) // 2
    dam = rows[half].find("td").get_text(" ", strip=True) if len(rows) > half else ""
    return f"父 {sire[:30]} / 母 {dam[:30]}"


def status(session, horse_id: str) -> str:
    soup = BeautifulSoup(get(session, f"https://db.netkeiba.com/horse/{horse_id}/"), "html.parser")
    t = soup.select_one(".horse_title")
    return t.get_text(" ", strip=True) if t else ""


def find_horse(session, name: str) -> str | None:
    enc = urllib.parse.quote(name.encode("euc-jp"))
    html = get(session, f"{SEARCH}?pid=horse_list&word={enc}&match=perfect_match")
    m = re.search(r"/horse/(\d{10})/", html)
    return m.group(1) if m else None


def main() -> None:
    sire, prefix = sys.argv[1], sys.argv[2]
    young = int(sys.argv[3]) if len(sys.argv) > 3 else 2017
    rk = json.loads((SIM_DIR / f"{prefix}_ranking.json").read_text(encoding="utf-8"))
    session = requests.Session()
    out = []
    for h in rk["top"][:10]:
        out += [f"## {h['rank']}位 {h['name']}（{h['title']}） 賞金 {h['prize_raw']} 通算 {h['record']}",
                pedigree(session, h["horse_id"])]
        out += ["  " + r for r in results(session, h["horse_id"])]
        out.append("")
    sid = find_horse(session, sire)
    out.append(f"## 種牡馬 {sire} id={sid}")
    if sid:
        out.append(status(session, sid))
        out += ["  " + r for r in results(session, sid)]
    out.append("")

    # 若い世代の産駒が中央で現役かどうか（産駒一覧を全ページ読み、生年の列で絞る）
    enc = urllib.parse.quote(sire.encode("euc-jp"))
    base = f"{SEARCH}?pid=horse_list&word=&match=partial_match&sire={enc}&list=100"
    rows, page = [], 1
    while page <= 40:
        got = parse_list(get(session, base + (f"&page={page}" if page > 1 else "")))
        new = [r for r in got if r["horse_id"] not in {x["horse_id"] for x in rows}]
        if not new:
            break
        rows += new
        page += 1
    rows = [r for r in rows if not r["sire"] or r["sire"] == sire]
    out.append(f"## 産駒一覧 {len(rows)}頭（{page - 1}ページ）")
    yrows = []
    for r in rows:
        by = next((int(c) for c in r["cells"] if re.fullmatch(r"(19|20)\d\d", c)), None)
        if by is None:
            by = int(r["horse_id"][:4]) if r["horse_id"][:4].isdigit() else 0
        if by >= young:
            yrows.append((by, r))
    out.append(f"生年 {young} 年以降: {len(yrows)}頭")
    active = []
    for by, r in sorted(yrows, key=lambda t: -t[0]):
        st = status(session, r["horse_id"])
        flag = "現役" if "現役" in st else ("抹消" if "抹消" in st else "?")
        out.append(f"  {by} {r['name']} {flag} | {st[:60]} | {' / '.join(r['cells'][:8])[:120]}")
        if flag != "抹消":
            active.append(r["name"])
    out.append(f"抹消になっていない産駒: {active}")
    (SIM_DIR / f"{prefix}_factcheck.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out[-40:]))


if __name__ == "__main__":
    main()
