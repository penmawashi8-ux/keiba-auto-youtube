#!/usr/bin/env python3
"""JRA の G1 プレレーティング（出走予定馬の国際レーティング。外国馬も同じ物差し）を取得する。

外国馬は JRA の成績が無い（または少ない）ので、JRA のデータだけで計算すると力を測れない。
JRA が G1 ごとに公開しているプレレーティング表（https://www.jra.go.jp/datafile/ranking/g1/<年>.html から
各レースのページへ）から「2026年度レーティングの最高値」を読み、馬名 → レーティング の辞書にする。

使い方: python scripts/sim_ratings.py <年> <レース名>   → sim/ratings.json に保存
        load(race_name, year) をインポートして使ってもよい（取得できなければ {}）
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import race_names as rn

BASE = "https://www.jra.go.jp"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
OUT = Path("sim/ratings.json")


def _get(url: str) -> str:
    import requests
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
    return r.text


def parse(page: str) -> dict[str, int]:
    """表の各行: <td class="name">馬名</td>（外国馬は class="name_gai" で「カナ<br>英字」）… <span class="num">124</span>。
    レーティングが無い馬（−）は入れない"""
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        m = re.search(r'<td class="name(?:_gai)?">\s*([^<]+)', tr)
        r = re.search(r'<span class="num">\s*(\d{2,3})\s*</span>', tr)
        if m and r:
            out[m.group(1).strip()] = int(r.group(1))
    return out


def find_page(race_name: str, year: int) -> str | None:
    idx = _get(f"{BASE}/datafile/ranking/g1/{year}.html")
    full = rn.full_name(race_name)
    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', idx, re.S):
        label = re.sub(r"<[^>]+>|\s", "", label)
        if f"/g1/{year}/" in href and (full in label or rn.norm(label) == rn.norm(race_name)):
            return href if href.startswith("http") else BASE + href
    return None


def load(race_name: str, year: int) -> dict[str, int]:
    if OUT.exists():
        d = json.loads(OUT.read_text(encoding="utf-8"))
        if d.get("race") == race_name and d.get("year") == year:
            return d["ratings"]
    try:
        url = find_page(race_name, year)
        if not url:
            print(f"[情報] {year}年 {race_name} のプレレーティング表が見つからない")
            return {}
        ratings = parse(_get(url))
    except Exception as e:  # noqa: BLE001
        print(f"[情報] プレレーティングを取得できず: {e}")
        return {}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"race": race_name, "year": year, "url": url, "ratings": ratings},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"プレレーティング {len(ratings)}頭: {url}")
    return ratings


if __name__ == "__main__":
    print(json.dumps(load(sys.argv[2], int(sys.argv[1])), ensure_ascii=False, indent=1))
