#!/usr/bin/env python3
"""netkeiba の重賞日程ページ・レース結果ページの構造を調べる一時的なプローブ。"""

import re

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "ja,en-US;q=0.9",
}


def fetch(s, url):
    r = s.get(url, headers=HEADERS, timeout=20)
    m = re.search(rb'charset=["\']?([\w-]+)', r.content[:3000])
    return r.status_code, r.content.decode(m.group(1).decode() if m else "utf-8", errors="replace")


def main() -> None:
    s = requests.Session()
    for year in (2005, 2023):
        code, text = fetch(s, f"https://race.netkeiba.com/top/schedule.html?year={year}")
        soup = BeautifulSoup(text, "html.parser")
        table = soup.select_one("table.race_table_01")
        rows = table.find_all("tr") if table else []
        print(f"== 重賞日程 {year}: HTTP {code} 行数={len(rows)}")
        for tr in rows[:4]:
            print("   ", [c.get_text(" ", strip=True)[:30] for c in tr.find_all(["th", "td"])])
            print("    links:", [a["href"][:90] for a in tr.find_all("a", href=True)][:4])
    code, text = fetch(s, "https://db.netkeiba.com/race/list/20231126/")
    hrefs = re.findall(r'href="([^"]*race[^"]*)"', text)
    print(f"== 日付別一覧: HTTP {code} race系リンク数={len(hrefs)}")
    print("   ", sorted(set(h for h in hrefs if re.search(r"\d{10,}", h)))[:12])
    code, text = fetch(s, "https://db.netkeiba.com/race/202305050812/")
    soup = BeautifulSoup(text, "html.parser")
    print(f"== db レース結果 202305050812: HTTP {code} title={soup.title.get_text(strip=True)[:60] if soup.title else None}")
    for t in soup.find_all("table")[:3]:
        first = t.find("tr")
        print("    table", t.get("class"), [c.get_text(strip=True) for c in first.find_all(["th", "td"])][:20] if first else None)
    info = soup.select_one(".data_intro") or soup.select_one("diary_snap_cut")
    print("    data_intro:", re.sub(r"\s+", " ", info.get_text(" ", strip=True))[:200] if info else None)
    m = re.search(r"(芝|ダ)[右左直].{0,40}", soup.get_text(" ", strip=True))
    print("    条件:", m.group(0) if m else None)
    lap = re.search(r"(ラップ|ペース).{0,120}", soup.get_text(" ", strip=True))
    print("    ラップ:", lap.group(0) if lap else None)


if __name__ == "__main__":
    main()
