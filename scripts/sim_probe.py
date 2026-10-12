#!/usr/bin/env python3
"""netkeiba のレース検索（重賞の勝ちタイム一覧）の取得方法を調べる一時的なプローブ。"""

import re

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "ja,en-US;q=0.9",
    "Referer": "https://db.netkeiba.com/?pid=race_search_detail",
}
PARAMS = [("pid", "race_list"), ("word", ""), ("track[]", "1"), ("start_year", "2023"), ("start_mon", "none"),
          ("end_year", "2023"), ("end_mon", "none"), ("grade[]", "1"), ("grade[]", "2"),
          ("kyori_min", ""), ("kyori_max", ""), ("sort", "date"), ("list", "100")]


def show(label: str, r: requests.Response) -> None:
    raw = r.content
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000])
    enc = m.group(1).decode() if m else "EUC-JP"
    text = raw.decode(enc, errors="replace")
    title = re.search(r"<title>(.*?)</title>", text, re.S)
    links = re.findall(r'href="(/race/\d{12}/)"', text)
    print(f"== {label}: HTTP {r.status_code} url={r.url[:160]} len={len(raw)} enc={enc}")
    print(f"   history={[h.status_code for h in r.history]} title={title.group(1).strip()[:80] if title else None!r}")
    print(f"   /race/ リンク数={len(links)} 先頭={links[:3]}")
    print(f"   table class={re.findall(r'<table[^>]*class=\"([^\"]+)\"', text)[:5]}")
    if not links:
        print("   先頭600字:", re.sub(r"\s+", " ", text[:600]))


def main() -> None:
    s = requests.Session()
    s.get("https://db.netkeiba.com/", headers=HEADERS, timeout=20)
    show("GET 検索(params)", s.get("https://db.netkeiba.com/", params=PARAMS, headers=HEADERS, timeout=20))
    show("POST 検索", s.post("https://db.netkeiba.com/", data=PARAMS, headers=HEADERS, timeout=20))
    show("GET 日付別一覧", s.get("https://db.netkeiba.com/race/list/20231126/", headers=HEADERS, timeout=20))
    show("GET race.netkeiba 日付別", s.get(
        "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=20231126", headers=HEADERS, timeout=20))
    show("GET 重賞日程(2023)", s.get(
        "https://race.netkeiba.com/top/schedule.html?year=2023", headers=HEADERS, timeout=20))


if __name__ == "__main__":
    main()
