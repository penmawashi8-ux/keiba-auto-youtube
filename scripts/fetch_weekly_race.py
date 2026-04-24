#!/usr/bin/env python3
"""週末の重賞レース情報を取得して race_list.json に保存する。

実行タイミング:
  木曜 15:00 JST → 木曜14時に枠番確定する主要G1の対象週かチェック。
                   対象外なら exit 0（後続ステップをスキップ）。
  金曜 10:30 JST → Geminiに今週末の全重賞を問い合わせ。
  それ以外       → 環境変数で手動指定された場合のみ動作。

環境変数:
  RACE_NAME, RACE_DATE, RACE_VENUE, RACE_DISTANCE, RACE_GRADE  (手動上書き)
  GEMINI_API_KEY, GEMINI_API_KEY_2, GEMINI_API_KEY_3           (金曜自動検出)

出力: race_list.json（レース情報のリスト）
      投稿済みレースは posted_landscape_ids.txt を参照してスキップ。
"""

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

JST = timezone(timedelta(hours=9))
RACE_LIST_JSON = "race_list.json"
POSTED_LANDSCAPE_IDS_FILE = "posted_landscape_ids.txt"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

# ── 木曜14時に枠番確定するG1（2026年） ───────────────────────────────────────
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


# ── Gemini API ───────────────────────────────────────────────────────────────

def load_api_keys() -> list[str]:
    keys = []
    for env_var in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"]:
        k = os.environ.get(env_var, "").strip()
        if k:
            keys.append(k)
    return keys


def call_gemini(api_keys: list[str], prompt: str) -> str:
    """Gemini API を呼び出してテキストを返す。失敗時は RuntimeError。

    全キー×全モデルを一括試行してから待機→再試行する方式。
    1キーで全待機を使い切ってから次のキーへ移動する旧方式より大幅に速い。
    """
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
    }
    pairs = [(key, model) for key in api_keys for model in GEMINI_MODELS]
    inter_pass_waits = [0, 30, 60]  # パス間の待機秒数

    for pass_idx, wait in enumerate(inter_pass_waits):
        if wait:
            print(f"  [全キー429のため {wait}秒待機してリトライ...]", file=sys.stderr)
            time.sleep(wait)
        for api_key, model in pairs:
            key_label = f"{api_key[:8]}..."
            url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"
            try:
                resp = requests.post(url, json=payload, timeout=60)
                if resp.status_code in {403, 404}:
                    continue
                if resp.status_code == 429:
                    continue  # このパスでは待機せず次のキー/モデルへ
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    return candidates[0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                safe = str(e).replace(api_key, "***")
                print(f"  [警告] {key_label} {model}: {safe}", file=sys.stderr)

    raise RuntimeError("Gemini API: 全キー×全モデルで失敗しました。")


def _fetch_race_news_hints(sat: datetime, sun: datetime) -> str:
    """今週末の重賞関連ニュースをGoogle Newsから取得してヒント文字列を返す。"""
    queries = [
        f"{sat.month}月{sat.day}日 重賞 JRA",
        f"{sun.month}月{sun.day}日 重賞 JRA",
        "今週末 重賞 出馬表",
    ]
    seen: set[str] = set()
    lines: list[str] = []
    for q in queries:
        for item in fetch_google_news(q, max_items=6):
            title = item["title"]
            if title and title not in seen:
                seen.add(title)
                lines.append(f"  - {title}")
        if len(lines) >= 15:
            break
    return "\n".join(lines[:15]) if lines else "  （ニュースなし）"


def ask_gemini_for_races(api_keys: list[str], now: datetime) -> list[dict]:
    """Geminiに今週末の重賞レース一覧を問い合わせ、dictリストで返す。"""
    sat = now + timedelta(days=1)
    sun = now + timedelta(days=2)
    weekday_jp = "月火水木金土日"
    sat_str = f"{sat.month}月{sat.day}日（{weekday_jp[sat.weekday()]}）"
    sun_str = f"{sun.month}月{sun.day}日（{weekday_jp[sun.weekday()]}）"

    print("今週末の重賞ニュースを取得中...", file=sys.stderr)
    news_hints = _fetch_race_news_hints(sat, sun)

    prompt = f"""\
今日は{now.year}年{now.month}月{now.day}日（金曜日）です。
今週末（{sat_str}・{sun_str}）に開催される競馬の重賞レース（G1・G2・G3）を**全て**教えてください。
JRAの国内重賞だけでなく、同じ週末に開催される主要な海外G1レースも含めてください。

以下は今週末の競馬に関するニュース見出し（参考情報）です。
このリストに載っているレースは必ず含めてください:
{news_hints}

【重要】dateフィールドは必ず {sat_str} か {sun_str} のどちらかにしてください。
それ以外の日付のレースは絶対に含めないでください。

以下のJSON配列形式のみで出力してください（コードブロック・説明文は不要）:
[
  {{"race_name": "レース名", "grade": "G1", "venue": "東京", "distance": "芝2400m", "date": "{sat_str}", "overseas": false}},
  ...
]

注意:
- race_name は正式名称で（略称・通称不可）
- grade は "G1" / "G2" / "G3" のいずれか
- overseas は海外開催なら true、JRAなら false
- 地方競馬（NAR）は含めない
- G1・G2・G3 全グレードを漏れなく含めること
"""
    print("Geminiに今週末の重賞一覧を問い合わせ中...", file=sys.stderr)
    try:
        raw = call_gemini(api_keys, prompt)
    except RuntimeError as e:
        print(f"[エラー] Gemini呼び出し失敗: {e}", file=sys.stderr)
        return []

    # JSON部分を抽出（```json ... ``` が混入している場合も対応）
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    races: list[dict] = []
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("リストでない")
        races = parsed
    except json.JSONDecodeError as e:
        # 途中で切れた場合でも個別オブジェクトを救済する
        print(f"[警告] JSONパース失敗（{e}）。個別オブジェクトの抽出を試みます。", file=sys.stderr)
        for obj_str in re.findall(r'\{[^{}]+\}', raw, re.DOTALL):
            try:
                obj = json.loads(obj_str)
                if isinstance(obj, dict) and obj.get("race_name"):
                    races.append(obj)
            except json.JSONDecodeError:
                pass
        if races:
            print(f"  救済成功: {len(races)} 件のオブジェクトを取得しました。", file=sys.stderr)
        else:
            print(f"  救済失敗。---\n{raw[:500]}\n---", file=sys.stderr)
            return []
    except Exception as e:
        print(f"[警告] Geminiレスポンスのパース失敗: {e}\n---\n{raw[:500]}\n---", file=sys.stderr)
        return []

    # 日付フィールドを検証：今週末（土・日）以外の日付を含むレースを除外
    valid_monthdays = {(sat.month, sat.day), (sun.month, sun.day)}

    def _parse_md(date_str: str) -> tuple[int, int] | None:
        m = re.search(r'(\d+)月(\d+)日', date_str)
        return (int(m.group(1)), int(m.group(2))) if m else None

    # G1_THURSDAY_SCHEDULE の逆引き辞書（レース名 → 実際の開催月日）
    g1_actual_dates: dict[str, tuple[int, int]] = {
        name: (m, d) for (m, d), (name, *_) in G1_THURSDAY_SCHEDULE.items()
    }

    filtered = []
    for r in races:
        race_name = r.get("race_name", "?")
        date_str  = r.get("date", "")
        date_md   = _parse_md(date_str)

        # ① 日付フィールドが今週末以外を指している場合は除外
        if date_md is not None and date_md not in valid_monthdays:
            print(
                f"  [除外] {race_name} 日付不一致: {date_str} "
                f"（今週末は {sat_str}・{sun_str}）",
                file=sys.stderr,
            )
            continue

        # ② 既知G1スケジュールと照合：実際の開催日が今週末でなければ除外
        # （Geminiが日付を偽って返すケース対策）
        if not r.get("overseas", False) and race_name in g1_actual_dates:
            actual_md = g1_actual_dates[race_name]
            if actual_md not in valid_monthdays:
                print(
                    f"  [除外] {race_name} はG1スケジュール上 "
                    f"{actual_md[0]}月{actual_md[1]}日開催（今週末外）",
                    file=sys.stderr,
                )
                continue

        filtered.append(r)

    return filtered


# ── Google News (スニペット取得用) ──────────────────────────────────────────

def fetch_google_news(query: str, max_items: int = 8) -> list[dict]:
    """Google News RSSからタイトル・概要を取得する。"""
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=ja&gl=JP&ceid=JP:ja"
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


# ── 共通ユーティリティ ───────────────────────────────────────────────────────

def load_posted_ids() -> set[str]:
    path = Path(POSTED_LANDSCAPE_IDS_FILE)
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def make_race_id(race_name: str, now: datetime) -> str:
    return f"{race_name}_{now.year}{now.month:02d}"


def _day_jp(d: datetime) -> str:
    weekday_jp = "月火水木金土日"[d.weekday()]
    return f"{d.month}月{d.day}日（{weekday_jp}）"


def _build_race_entry(race_name: str, grade: str, date: str, venue: str,
                      distance: str, source: str, race_id: str,
                      news_items: list[dict], is_overseas: bool = False) -> dict:
    snippets = [x["title"] for x in news_items[:12]]
    return {
        "race_name":    race_name,
        "grade":        grade,
        "date":         date,
        "venue":        venue,
        "distance":     distance,
        "news_snippets": snippets,
        "source":       source,
        "race_id":      race_id,
        "overseas":     is_overseas,
    }


# ── メイン ──────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(JST)
    weekday = now.weekday()

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
            print(f"手動指定 {manual_name} は投稿済みのためスキップ。")
        else:
            news = fetch_google_news(f"{manual_name} {now.year} 出馬表 予想")
            race_list.append(_build_race_entry(
                race_name=manual_name,
                grade=os.environ.get("RACE_GRADE", "G1").strip() or "G1",
                date=os.environ.get("RACE_DATE", "今週末").strip() or "今週末",
                venue=os.environ.get("RACE_VENUE", "").strip(),
                distance=os.environ.get("RACE_DISTANCE", "").strip(),
                source="manual",
                race_id=race_id,
                news_items=news,
            ))
            print(f"手動指定: {manual_name}")
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

        news: list[dict] = []
        for q in [f"{race_name} {now.year} 出馬表", f"{race_name} 枠順 予想"]:
            news.extend(fetch_google_news(q, max_items=5))

        race_list.append(_build_race_entry(
            race_name=race_name, grade=grade, date=date_str,
            venue=venue, distance=distance, source="thursday_g1",
            race_id=race_id, news_items=news,
        ))
        print(f"G1確定: {race_name}（{date_str} {venue} {distance}）")

    # ── 金曜実行: Geminiで全重賞を取得 ─────────────────────────────────────
    elif weekday == 4:
        api_keys = load_api_keys()
        if not api_keys:
            print("[エラー] GEMINI_API_KEY が未設定です。金曜の自動検出にはGemini APIが必要です。", file=sys.stderr)
            sys.exit(1)

        gemini_races = ask_gemini_for_races(api_keys, now)
        if not gemini_races:
            print("Geminiから今週末の重賞情報を取得できませんでした。スキップします。")
            sys.exit(0)

        print(f"Gemini検出: {len(gemini_races)} 件")
        for r in gemini_races:
            race_name = r.get("race_name", "").strip()
            grade     = r.get("grade", "G3").strip()
            venue     = r.get("venue", "").strip()
            distance  = r.get("distance", "").strip()
            date      = r.get("date", "今週末").strip()
            is_overseas = bool(r.get("overseas", False))

            if not race_name:
                continue

            race_id = make_race_id(race_name, now)
            if race_id in posted_ids:
                print(f"  スキップ（投稿済み）: {race_name}")
                continue

            suffix = "海外競馬 出走予定" if is_overseas else f"{now.year} 出馬表 予想"
            news = fetch_google_news(f"{race_name} {suffix}", max_items=8)

            race_list.append(_build_race_entry(
                race_name=race_name, grade=grade, date=date,
                venue=venue, distance=distance,
                source="overseas_gemini" if is_overseas else "friday_gemini",
                race_id=race_id, news_items=news, is_overseas=is_overseas,
            ))
            print(f"  追加: {race_name}（{grade}）{'🌍 海外' if is_overseas else ''}")

    # ── それ以外 ────────────────────────────────────────────────────────────
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
