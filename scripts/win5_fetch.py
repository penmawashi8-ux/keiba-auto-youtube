"""WIN5対象レースの出馬表（5走表示）を netkeiba から取得し、テキストで保存する。

使い方: python scripts/win5_fetch.py <race_id> [<race_id> ...]
出力: win5_data/<race_id>.txt （1頭1ブロック、近5走付き）
"""
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
OUT = Path("win5_data")


def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    m = re.search(rb'charset=["\']?([\w-]+)', r.content[:3000])
    enc = m.group(1).decode() if m else "euc-jp"
    return r.content.decode(enc, errors="replace")


def clean(el) -> str:
    txt = el.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", txt)


def parse_past(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    lines = []
    title = soup.select_one(".RaceName")
    data1 = soup.select_one(".RaceData01")
    data2 = soup.select_one(".RaceData02")
    for el in (title, data1, data2):
        if el:
            lines.append(clean(el))
    lines.append("")
    rows = soup.select("tr.HorseList")
    for tr in rows:
        tds = tr.find_all("td")
        if not tds:
            continue
        lines.append("=" * 70)
        for i, td in enumerate(tds):
            t = clean(td)
            if not t:
                continue
            cls = " ".join(td.get("class", []))
            lines.append(f"[{i}:{cls}] {t}")
    if not rows:
        lines.append("(HorseList行が見つからず。本文テキストを出力)")
        lines.append(clean(soup.body or soup)[:20000])
    return "\n".join(lines)


def parse_result(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    lines = []
    title = soup.select_one(".RaceName")
    if title:
        lines.append(clean(title))
    for tr in soup.select("table.RaceTable01 tr"):
        cells = [clean(td) for td in tr.find_all(["td", "th"])]
        if cells:
            lines.append(" | ".join(cells))
    if len(lines) <= 1:
        lines.append("(結果テーブルなし。本文先頭を出力)")
        lines.append(clean(soup.body or soup)[:5000])
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for arg in sys.argv[1:]:
        is_result = arg.startswith("R:")
        rid = arg[2:] if is_result else arg
        page = "result" if is_result else "shutuba_past"
        url = f"https://race.netkeiba.com/race/{page}.html?race_id={rid}"
        try:
            html = fetch(url)
            text = parse_result(html) if is_result else parse_past(html)
        except Exception as e:  # noqa: BLE001
            text = f"ERROR {url}: {e}"
        name = f"result_{rid}" if is_result else rid
        (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
        print(f"{rid}: {len(text)} chars")
        time.sleep(2)


if __name__ == "__main__":
    main()
