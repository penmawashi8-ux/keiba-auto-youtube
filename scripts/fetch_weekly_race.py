#!/usr/bin/env python3
"""週末の重賞レース情報を取得して race_list.json に保存する。

実行タイミング:
  木曜 15:00 JST → 木曜14時に枠番確定する主要G1（10レース）の対象週かチェック。
                   対象外なら exit 0（後続ステップをスキップ）。
  金曜 10:30 JST → Google NewsでRSSを検索して今週末の全重賞を取得。
  それ以外       → 環境変数で手動指定された場合のみ動作。

環境変数（workflow_dispatch 手動上書き用）:
  RACE_NAME, RACE_DATE, RACE_VENUE, RACE_DISTANCE, RACE_GRADE

出力: race_list.json（レース情報のリスト）
      投稿済みレースは posted_landscape_ids.txt を参照してスキップ。
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

JST = timezone(timedelta(hours=9))
RACE_LIST_JSON = "race_list.json"
POSTED_LANDSCAPE_IDS_FILE = "posted_landscape_ids.txt"
MAX_RACES_PER_RUN = 3  # 1回の実行で処理するレース数の上限

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

# ── 木曜14時に枠番確定するG1（2026年） ───────────────────────────────────────
# key: レース日 (month, day) / value: (レース名, 会場, 距離, グレード)
# 毎年1月に次年度のスケジュールへ更新すること
G1_THURSDAY_SCHEDULE: dict[tuple[int, int], tuple[str, str, str, str]] = {
    (4, 12): ("桜花賞",               "阪神", "芝1600m", "G1"),
    (4, 19): ("皐月賞",               "中山", "芝2000m", "G1"),
    (5,  3): ("天皇賞（春）",         "京都", "芝3200m", "G1"),
    (5, 24): ("オークス",             "東京", "芝2400m", "G1"),
    (5, 31): ("日本ダービー",         "東京", "芝2400m", "G1"),
    (6, 14): ("宝塚記念",             "阪神", "芝2200m", "G1"),
    (10, 25): ("菊花賞",              "京都", "芝3000m", "G1"),
    (11, 29): ("ジャパンカップ",      "東京", "芝2400m", "G1"),
    (12,  6): ("チャンピオンズカップ", "中京", "ダ1800m", "G1"),
    (12, 27): ("有馬記念",            "中山", "芝2500m", "G1"),
}

# ── 重賞名抽出パターン ────────────────────────────────────────────────────────
_RACE_NAME_RE = re.compile(
    r"([ァ-ヶー一-鿿]{2,}"
    r"(?:賞|杯|カップ|ステークス|記念|フィリーズレビュー|チャレンジトロフィー|ハンデキャップ|Ｓ|Ｃ))"
)
_GRADE_RE = re.compile(r"\bG([123])\b|GI{1,3}\b")
_PRIORITY = {"G1": 3, "G2": 2, "G3": 1}


def _grade_str(text: str) -> str:
    """テキストから最上位グレードを返す。"""
    m = _GRADE_RE.search(text)
    if not m:
        return "G3"
    raw = m.group(0)
    if raw in ("G1", "GI"):
        return "G1"
    if raw in ("G2", "GII"):
        return "G2"
    return "G3"


def fetch_google_news(query: str, max_items: int = 10) -> list[dict]:
    """Google News RSSからタイトル・概要を取得する。"""
    url = (
        f"https://news.google.com/rss/search"
        f"?q={quote(query)}&hl=ja&gl=JP&ceid=JP:ja"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for item in root.iter("item"):
            title = item.findtext("title") or ""
            desc = item.findtext("description") or ""
            items.append({"title": title, "description": desc})
            if len(items) >= max_items:
                break
        return items
    except Exception as e:
        print(f"[警告] Google News取得失敗 query={query!r}: {e}", file=sys.stderr)
        return []


def extract_all_races_from_news(items: list[dict]) -> list[dict]:
    """ニュース記事リストから今週末の全重賞を抽出する（グレード降順）。"""
    seen: dict[str, dict] = {}  # race_name -> best grade candidate

    for item in items:
        text = item["title"] + " " + item["description"]
        if not re.search(r"出馬表|枠順|重賞|G[123I]|今週|週末", text):
            continue
        for m in _RACE_NAME_RE.finditer(text):
            race_name = m.group(1)
            # グレードはレース名周辺のコンテキストから判定
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 80)
            grade = _grade_str(text[start:end])
            if race_name not in seen or _PRIORITY[grade] > _PRIORITY.get(seen[race_name]["grade"], 0):
                seen[race_name] = {"race_name": race_name, "grade": grade}

    return sorted(seen.values(), key=lambda x: _PRIORITY.get(x["grade"], 1), reverse=True)


def load_posted_ids() -> set[str]:
    """投稿済みレースIDを読み込む。"""
    path = Path(POSTED_LANDSCAPE_IDS_FILE)
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def make_race_id(race_name: str, now: datetime) -> str:
    """レースIDを生成する（race_name_YYYYMM形式）。"""
    return f"{race_name}_{now.year}{now.month:02d}"


def _day_jp(race_date: "datetime") -> str:
    """日付を「5月3日（日）」形式に変換する。"""
    weekday_jp = "月火水木金土日"[race_date.weekday()]
    return f"{race_date.month}月{race_date.day}日（{weekday_jp}）"


def main() -> None:
    now = datetime.now(JST)
    weekday = now.weekday()  # 0=月 … 3=木 … 4=金

    print(
        f"実行日時（JST）: {now.strftime('%Y-%m-%d %H:%M')} "
        f"({['月','火','水','木','金','土','日'][weekday]}曜日)"
    )

    posted_ids = load_posted_ids()
    print(f"投稿済みID: {len(posted_ids)} 件")

    race_list: list[dict] = []

    # ── 手動上書き（workflow_dispatch）──────────────────────────────────────
    manual_name = os.environ.get("RACE_NAME", "").strip()
    if manual_name:
        race_id = make_race_id(manual_name, now)
        if race_id in posted_ids:
            print(f"手動指定レース {manual_name} は投稿済みのためスキップします。（ID: {race_id}）")
            print("スキップしたい場合は RACE_NAME を空にしてください。")
        else:
            race_info = {
                "race_name": manual_name,
                "grade":     os.environ.get("RACE_GRADE",    "G1").strip() or "G1",
                "date":      os.environ.get("RACE_DATE",     "今週末").strip() or "今週末",
                "venue":     os.environ.get("RACE_VENUE",    "").strip(),
                "distance":  os.environ.get("RACE_DISTANCE", "").strip(),
                "news_snippets": [],
                "source":    "manual",
                "race_id":   race_id,
            }
            items = fetch_google_news(f"{manual_name} 2026 出馬表 予想")
            race_info["news_snippets"] = [x["title"] for x in items[:8]]
            print(f"手動指定: {manual_name}")
            race_list.append(race_info)
        _write_list(race_list)
        return

    # ── 木曜実行: G1週チェック ───────────────────────────────────────────────
    if weekday == 3:
        race_date = (now + timedelta(days=3)).date()
        key = (race_date.month, race_date.day)

        if key not in G1_THURSDAY_SCHEDULE:
            print(f"今週末（{race_date}）は木曜枠番確定G1なし。スキップします。")
            sys.exit(0)

        race_name, venue, distance, grade = G1_THURSDAY_SCHEDULE[key]
        date_str = _day_jp(datetime(race_date.year, race_date.month, race_date.day))
        race_id = make_race_id(race_name, now)

        if race_id in posted_ids:
            print(f"{race_name} は投稿済みのためスキップします。")
            sys.exit(0)

        snippets: list[str] = []
        for q in [f"{race_name} 2026 出馬表", f"{race_name} 枠順 予想"]:
            items = fetch_google_news(q, max_items=5)
            snippets.extend(x["title"] for x in items)

        race_list.append({
            "race_name": race_name,
            "grade":     grade,
            "date":      date_str,
            "venue":     venue,
            "distance":  distance,
            "news_snippets": snippets[:10],
            "source":    "thursday_g1",
            "race_id":   race_id,
        })
        print(f"G1確定: {race_name}（{date_str} {venue} {distance}）")

    # ── 金曜実行: 全重賞検索 ────────────────────────────────────────────────
    elif weekday == 4:
        all_items: list[dict] = []
        for q in [
            "今週末 重賞 競馬 出馬表",
            "今週 G1 G2 重賞 競馬 枠順",
            "重賞 競馬 今週末",
            "今週末 G2 G3 競馬",
        ]:
            all_items.extend(fetch_google_news(q, max_items=10))

        candidates = extract_all_races_from_news(all_items)
        if not candidates:
            print("今週末の重賞情報を取得できませんでした。スキップします。")
            sys.exit(0)

        print(f"検出レース: {len(candidates)} 件（上限 {MAX_RACES_PER_RUN} 件）")
        for c in candidates:
            race_name = c["race_name"]
            grade = c["grade"]
            race_id = make_race_id(race_name, now)

            if race_id in posted_ids:
                print(f"  スキップ（投稿済み）: {race_name}（ID: {race_id}）")
                continue

            extra = fetch_google_news(f"{race_name} 2026 出馬表 予想", max_items=6)
            snippets = [x["title"] for x in (all_items + extra)][:12]

            race_list.append({
                "race_name": race_name,
                "grade":     grade,
                "date":      "今週末",
                "venue":     "",
                "distance":  "",
                "news_snippets": snippets,
                "source":    "friday_search",
                "race_id":   race_id,
            })
            print(f"  追加: {race_name}（{grade}）")
            if len(race_list) >= MAX_RACES_PER_RUN:
                print(f"  上限 {MAX_RACES_PER_RUN} 件に達したため残りをスキップ。")
                break

    # ── それ以外（テスト実行など）──────────────────────────────────────────
    else:
        print(f"木曜・金曜以外（weekday={weekday}）。RACE_NAME 環境変数で手動指定してください。")
        sys.exit(0)

    if not race_list:
        print("処理対象のレースがありません（全て投稿済み）。スキップします。")
        sys.exit(0)

    _write_list(race_list)


def _write_list(race_list: list[dict]) -> None:
    Path(RACE_LIST_JSON).write_text(
        json.dumps(race_list, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ {RACE_LIST_JSON} を生成しました。({len(race_list)} レース)")
    for r in race_list:
        print(f"  - {r['race_name']}（{r['grade']}）")


if __name__ == "__main__":
    main()
