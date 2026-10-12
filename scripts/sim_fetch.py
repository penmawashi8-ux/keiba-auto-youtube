#!/usr/bin/env python3
"""夢の対決シミュレーション: netkeiba DB から出走馬の全成績と、重賞の勝ちタイム一覧を取得する。

出力: sim/data.json
  {
    "horses":   {馬名: {"id": ..., "sex": "牡"|"牝", "races": [成績行, ...]}},
    "baseline": [2000〜2025年 JRA芝 G1/G2 の勝ちタイム行, ...]   # 時代補正・ペース補正用
  }
成績行: date, venue, race_name, race_id, field_size, position, weight(斤量), surface, distance,
        going, time, passing(通過), front3f(前半3F), last3f(上り)

使い方: python scripts/sim_fetch.py ディープインパクト=2002100816 イクイノックス=2019105219 ...
"""

import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUT = Path("sim/data.json")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "ja,en-US;q=0.9",
}
JRA_VENUES = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
WAIT_SEC = 1.0


def get(session: requests.Session, url: str) -> str:
    time.sleep(WAIT_SEC)
    r = session.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    # netkeiba DB は EUC-JP（ページによっては UTF-8）。meta の charset に従う
    m = re.search(rb'charset=["\']?([\w-]+)', r.content[:2000])
    r.encoding = m.group(1).decode() if m else "EUC-JP"
    return r.text


def parse_time(s: str) -> float | None:
    m = re.fullmatch(r"(\d+):(\d+(?:\.\d)?)", s.strip())
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.fullmatch(r"\d+\.\d", s.strip())
    return float(s) if m else None


def find_table(soup: BeautifulSoup, must: list[str]):
    for table in soup.find_all("table"):
        first = table.find("tr")
        if not first:
            continue
        heads = [c.get_text(strip=True) for c in first.find_all(["th", "td"])]
        if all(any(m == h for h in heads) for m in must):
            return table, heads
    return None, None


def col(heads: list[str], name: str) -> int | None:
    return heads.index(name) if name in heads else None


def fetch_horse(session, name: str, horse_id: str) -> dict:
    html = None
    for url in (f"https://db.netkeiba.com/horse/result/{horse_id}/",
                f"https://db.netkeiba.com/horse/{horse_id}/"):
        try:
            text = get(session, url)
        except requests.HTTPError as e:
            print(f"  {url} → {e}")
            continue
        soup = BeautifulSoup(text, "html.parser")
        table, heads = find_table(soup, ["レース名", "タイム", "着順"])
        if table:
            html = (soup, table, heads, url)
            break
        title = soup.title.get_text(strip=True) if soup.title else ""
        print(f"  {url}: 成績表なし (title={title[:60]!r}, tables={len(soup.find_all('table'))})")
    if not html:
        raise RuntimeError(f"{name}: 成績表が見つかりません")
    soup, table, heads, url = html

    page_title = soup.title.get_text(strip=True) if soup.title else ""
    if name not in page_title:
        # 結果専用ページはタイトルに馬名が無いことがあるので、プロフィールページで確認する
        prof = BeautifulSoup(get(session, f"https://db.netkeiba.com/horse/{horse_id}/"), "html.parser")
        page_title = prof.title.get_text(strip=True) if prof.title else ""
        if name not in page_title:
            raise RuntimeError(f"ID {horse_id} は {name} ではありません (title={page_title!r})")
        soup_for_sex = prof
    else:
        soup_for_sex = soup
    # 「引退　牡　鹿毛」のような性別＋毛色の並びから判定する（メニュー等の「牝馬」誤検知を避ける）
    m = re.search(r"(牡|牝|セ)\s*(栃栗毛|栗毛|鹿毛|黒鹿毛|青鹿毛|青毛|芦毛|白毛)",
                  soup_for_sex.get_text(" ", strip=True))
    sex = m.group(1) if m else "牡"
    print(f"  {name}: {url} / 列={heads}")

    idx = {k: col(heads, k) for k in
           ["日付", "開催", "レース名", "頭数", "着順", "斤量", "距離", "馬場", "タイム", "通過", "ペース", "上り"]}
    races = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < len(heads) - 2:
            continue

        def g(k):
            i = idx[k]
            return tds[i].get_text(strip=True) if i is not None and i < len(tds) else ""

        race_id = ""
        if idx["レース名"] is not None:
            a = tds[idx["レース名"]].find("a", href=True)
            m = re.search(r"/race/([0-9A-Za-z]+)/", a["href"]) if a else None
            race_id = m.group(1) if m else ""
        dist = re.match(r"(芝|ダ|障)(\d+)", g("距離"))
        venue = next((v for v in JRA_VENUES if v in g("開催")), g("開催"))
        try:
            position = int(g("着順"))
        except ValueError:
            position = None
        try:
            weight = float(g("斤量"))
        except ValueError:
            weight = None
        races.append({
            "date": g("日付"), "venue": venue, "race_name": g("レース名"), "race_id": race_id,
            "field_size": int(g("頭数")) if g("頭数").isdigit() else None,
            "position": position, "weight": weight,
            "surface": dist.group(1) if dist else "", "distance": int(dist.group(2)) if dist else None,
            "going": g("馬場")[:1], "time": parse_time(g("タイム")),
            "passing": g("通過"), "front3f": parse_front3f(g("ペース")), "last3f": float(g("上り")) if re.fullmatch(r"\d+\.\d", g("上り")) else None,
        })
    print(f"  {name}: {len(races)}走 / 性別 {sex}")
    return {"id": horse_id, "sex": sex, "races": races}


def parse_front3f(pace: str) -> float | None:
    """「35.0-34.5」形式のペース（前半3F-後半3F）から前半3Fを取り出す"""
    m = re.match(r"(\d+\.\d)-(\d+\.\d)", pace or "")
    return float(m.group(1)) if m else None


GRADE_RE = re.compile(r"\((?:J・)?G(I{1,3}|[123])\)")


def grade_of(race_name: str) -> str:
    """「ジャパンC(GI)」→ G1。表記ゆれ（G1 / GI）に対応"""
    m = GRADE_RE.search(race_name)
    if not m:
        return ""
    g = m.group(1)
    return f"G{g}" if g.isdigit() else f"G{len(g)}"


BASELINE_URL = ("https://db.netkeiba.com/?pid=race_list&word=&track%5B%5D=1"
                "&start_year={y0}&start_mon=none&end_year={y1}&end_mon=none"
                "&grade%5B%5D=1&grade%5B%5D=2&kyori_min=&kyori_max=&sort=date&list=100&page={page}")


def fetch_baseline(session, y0: int, y1: int) -> list[dict]:
    """JRA芝の G1/G2 の勝ちタイム一覧（時代補正・ペース補正の基準データ）"""
    rows, seen = [], set()
    for page in range(1, 40):
        soup = BeautifulSoup(get(session, BASELINE_URL.format(y0=y0, y1=y1, page=page)), "html.parser")
        table, heads = find_table(soup, ["レース名", "距離", "タイム"])
        if not table:
            title = soup.title.get_text(strip=True) if soup.title else ""
            print(f"  基準データ page{page}: 表なし (title={title[:60]!r})")
            break
        if page == 1:
            print(f"  基準データの列: {heads}")
        idx = {k: col(heads, k) for k in ["開催日", "開催", "レース名", "距離", "馬場", "タイム", "ペース"]}
        added = 0
        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")

            def g(k):
                i = idx[k]
                return tds[i].get_text(strip=True) if i is not None and i < len(tds) else ""

            a = tds[idx["レース名"]].find("a", href=True) if idx["レース名"] is not None and tds else None
            m = re.search(r"/race/(\d+)/", a["href"]) if a else None
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            dist = re.match(r"(芝|ダ|障)(\d+)", g("距離"))
            venue = next((v for v in JRA_VENUES if v in g("開催")), "")
            t = parse_time(g("タイム"))
            if not dist or dist.group(1) != "芝" or not venue or not t:
                continue
            rows.append({"race_id": m.group(1), "year": int(m.group(1)[:4]), "date": g("開催日"),
                         "venue": venue, "distance": int(dist.group(2)), "going": g("馬場")[:1] or "良",
                         "race_name": g("レース名"), "grade": grade_of(g("レース名")),
                         "time": t, "front3f": parse_front3f(g("ペース"))})
            added += 1
        print(f"  基準データ page{page}: +{added}件（累計{len(rows)}）")
        if added == 0:
            break
    return rows


def main() -> None:
    pairs = [a.split("=", 1) for a in sys.argv[1:]]
    if not pairs:
        sys.exit("usage: sim_fetch.py 馬名=netkeibaID ...")
    session = requests.Session()
    data = {"horses": {}, "baseline": []}
    for name, hid in pairs:
        print(f"[馬] {name} ({hid})")
        data["horses"][name] = fetch_horse(session, name, hid)

    print("[基準] JRA芝 G1/G2 の勝ちタイムを取得")
    data["baseline"] = fetch_baseline(session, 2000, 2025)
    grades = {}
    for r in data["baseline"]:
        grades[r["grade"]] = grades.get(r["grade"], 0) + 1
    print(f"  基準データ {len(data['baseline'])}件 / 格の内訳 {grades}")
    if len(data["baseline"]) < 300:
        raise RuntimeError("基準データが少なすぎます（検索ページの形式が変わった可能性）")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"保存: {OUT}")


if __name__ == "__main__":
    main()
