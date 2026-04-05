#!/usr/bin/env python3
"""土日の重賞レース結果を Google News RSS から取得し news.json に保存する。
keiba_results.yml ワークフローから呼び出す専用スクリプト。
通常の fetch_news.py より時間窓を短く（4時間以内）し、結果記事に絞る。
"""

import json
import re
import sys
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# fetch_news.py の共通ユーティリティを再利用
sys.path.insert(0, str(Path(__file__).parent))
from fetch_news import (  # noqa: E402
    http_get,
    http_get_article,
    parse_feed,
    extract_og_image,
    load_posted_ids,
    _extract_next_data_body,
    _DENY_KEYWORDS,
    _DENY_TITLE_PREFIXES,
)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

# フォールバック用汎用フィード（レース名が取得できなかった場合に使用）
_FALLBACK_RSS_FEEDS = [
    "https://news.google.com/rss/search?q=%E9%87%8D%E8%B3%9E+%E7%B5%90%E6%9E%9C+%E7%AB%B6%E9%A6%AC&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC+G1+%E7%B5%90%E6%9E%9C&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC+%E9%87%8D%E8%B3%9E+%E5%84%AA%E5%8B%9D&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC+1%E7%9D%80+%E9%87%8D%E8%B3%9E&hl=ja&gl=JP&ceid=JP:ja",
]

# JST タイムゾーン
_JST = timezone(timedelta(hours=9))

# 重賞レース名のパターン: 日本語・英字に続いて典型的な語尾
_GRADE_RACE_PATTERN = re.compile(
    r'[A-Za-z\u30A0-\u30FF\u4E00-\u9FFF\u3040-\u309F\uFF00-\uFFEF]{2,}'
    r'(?:ステークス|カップ|賞|記念|ハンデキャップ|ハンデ|オークス|ダービー|リレー|マイル)',
)

NEWS_JSON = "news.json"
POSTED_IDS_FILE = "posted_ids.txt"
MAX_NEWS = 3
HOURS_WINDOW = 4  # レース終了後4時間以内の記事のみ対象

# 予想・登録など「結果ではない」記事タイトルを除外するパターン
_DENY_RESULT_PATTERN = re.compile(
    r"予想|出走登録|出走予定|枠順確定|枠順発表|オッズ|見解|展望|今週の|次走|次回|注目馬|"
    r"前売り|登録馬|確認|募集|お知らせ",
    re.IGNORECASE,
)

# 結果記事であることを示すキーワード（タイトルに1つ以上含まれること）
_RESULT_TITLE_KEYWORDS = [
    "結果", "優勝", "1着", "制した", "制覇", "勝利", "V ", "初V", "連覇",
    "レコード", "完勝", "快勝", "逃げ切り", "差し切り",
]


def discover_todays_races() -> list[str]:
    """今日の重賞レース名を Google News から取得する。
    「今日の重賞」に関連する記事タイトルを複数検索し、
    レース名らしい語句を頻度順に返す。
    """
    today = datetime.now(_JST)
    date_str = f"{today.month}月{today.day}日"

    discovery_queries = [
        f"{date_str} 重賞 競馬",
        "今日 重賞 競馬",
        "本日 重賞 競馬",
    ]

    race_name_counts: Counter[str] = Counter()
    for query in discovery_queries:
        encoded = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
        print(f"レース名探索クエリ: {query}")
        raw = http_get(url)
        if raw is None:
            continue
        entries = parse_feed(raw)
        for entry in entries[:30]:
            title = entry.get("title", "")
            for match in _GRADE_RACE_PATTERN.findall(title):
                if len(match) >= 3:
                    race_name_counts[match] += 1

    # 出現頻度の高い順に最大5レース名を返す
    top_races = [name for name, _ in race_name_counts.most_common(5)]
    print(f"検出された重賞レース名: {top_races}")
    return top_races


def build_race_feeds() -> list[str]:
    """今日の重賞レース名を使って「[レース名] 結果」専用フィードを構築する。
    レース名が取得できなければフォールバック用の汎用フィードを返す。
    """
    race_names = discover_todays_races()
    feeds: list[str] = []

    if race_names:
        for name in race_names:
            query = f"{name} 結果"
            encoded = urllib.parse.quote(query)
            feeds.append(
                f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
            )
            print(f"動的フィード追加: {query}")
    else:
        print("レース名が取得できませんでした。フォールバック用フィードを使用します。")
        feeds.extend(_FALLBACK_RSS_FEEDS)

    return feeds


def is_result_article(entry: dict) -> bool:
    """結果記事かどうかを判定する。予想・登録記事は除外。"""
    title = entry.get("title", "")
    # 動画タイトル・否定キーワードを除外
    if any(title.lower().startswith(p.lower()) for p in _DENY_TITLE_PREFIXES):
        return False
    text = (title + " " + entry.get("summary", "")).lower()
    for kw in _DENY_KEYWORDS:
        if kw in text:
            return False
    # 予想・登録記事を除外
    if _DENY_RESULT_PATTERN.search(title):
        print(f"  [除外] 予想/登録記事のためスキップ: {title[:60]}")
        return False
    # 結果キーワードが含まれていることを確認（緩め: タイトルになくても通す）
    return True


def fetch_race_results() -> list[dict]:
    """重賞結果フィードを取得し、直近 HOURS_WINDOW 時間以内の記事を返す。"""
    now = datetime.now(timezone.utc)
    posted_ids = load_posted_ids()

    rss_feeds = build_race_feeds()

    all_entries: list[dict] = []
    feed_errors = 0
    for feed_url in rss_feeds:
        print(f"フィード取得: {feed_url[:80]}")
        raw = http_get(feed_url)
        if raw is None:
            feed_errors += 1
            continue
        entries = parse_feed(raw)
        print(f"  有効エントリー: {len(entries)} 件")
        all_entries.extend(entries)

    if feed_errors == len(rss_feeds):
        print("[エラー] 全フィードの取得に失敗しました。", file=sys.stderr)
        sys.exit(1)

    # 公開日時で降順ソート
    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    all_entries.sort(key=lambda e: e.get("published_date") or _epoch, reverse=True)

    # 重複除去
    seen: set[str] = set()
    unique: list[dict] = []
    for e in all_entries:
        if e["id"] not in seen:
            seen.add(e["id"])
            unique.append(e)

    # 投稿済みを除外
    unposted = [e for e in unique if e["id"] not in posted_ids]
    print(f"未投稿エントリー: {len(unposted)} 件")

    # 結果記事フィルタ
    unposted = [e for e in unposted if is_result_article(e)]
    print(f"結果記事フィルタ後: {len(unposted)} 件")

    # 時間フィルタ: HOURS_WINDOW 時間以内 → なければ最新3件
    cutoff = now - timedelta(hours=HOURS_WINDOW)
    recent = [
        e for e in unposted
        if e.get("published_date") and e["published_date"] >= cutoff
    ]
    if recent:
        selected = recent[:MAX_NEWS]
        print(f"直近{HOURS_WINDOW}時間以内で {len(selected)} 件を選択")
    else:
        selected = unposted[:MAX_NEWS]
        if selected:
            print(f"時間内の記事なし。最新 {len(selected)} 件を選択")
        else:
            print("対象ニュースなし。")
            return []

    # OG画像・本文を補完して news_items を構築
    news_items: list[dict] = []
    for entry in selected:
        title = entry["title"]
        link = entry["link"]
        entry_id = entry["id"]
        summary = re.sub(r"<[^>]+>", " ", entry.get("summary", "")).strip()
        image_url = entry.get("image_url", "")
        published_dt: datetime | None = entry.get("published_date")

        if image_url and re.search(r"google\.com|googleusercontent\.com|gstatic\.com", image_url, re.I):
            image_url = ""

        raw_html = http_get_article(link)
        if raw_html:
            html = raw_html.decode("utf-8", errors="replace")
            body = _extract_next_data_body(html)
            html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
            if not image_url:
                og_img = extract_og_image(link, html)
                if og_img and not re.search(r"google\.com|googleusercontent\.com|gstatic\.com", og_img, re.I):
                    image_url = og_img
            if len(body.strip()) < 100:
                m = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL | re.IGNORECASE)
                if m:
                    body = re.sub(r"<[^>]+>", " ", m.group(1))
            if len(body.strip()) < 100:
                m = re.search(r"<main[^>]*>(.*?)</main>", html, re.DOTALL | re.IGNORECASE)
                if m:
                    body = re.sub(r"<[^>]+>", " ", m.group(1))
            if len(body.strip()) < 100:
                m = re.search(
                    r'<div[^>]+(?:class|id)=["\'][^"\']*(?:article|content|body|entry|text)[^"\']*["\'][^>]*>(.*?)</div>',
                    html, re.DOTALL | re.IGNORECASE,
                )
                if m:
                    body = re.sub(r"<[^>]+>", " ", m.group(1))
            if len(body.strip()) < 100:
                paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL | re.IGNORECASE)
                body = " ".join(re.sub(r"<[^>]+>", "", p) for p in paras)
            og_desc = ""
            m_desc = re.search(
                r'<meta[^>]+(?:name=["\']description["\']|property=["\']og:description["\'])[^>]+content=["\']([^"\']{20,})["\']',
                html, re.IGNORECASE,
            )
            if not m_desc:
                m_desc = re.search(
                    r'<meta[^>]+content=["\']([^"\']{20,})["\'][^>]+(?:name=["\']description["\']|property=["\']og:description["\'])',
                    html, re.IGNORECASE,
                )
            if m_desc:
                og_desc = m_desc.group(1).strip()
            if len(body.strip()) < 100:
                body = re.sub(r"<[^>]+>", " ", html)
            full_body = re.sub(r"\s+", " ", body).strip()[:2000]
            if og_desc and og_desc not in full_body:
                full_body = (og_desc + " " + full_body).strip()[:2000]
            if len(full_body) > len(summary):
                summary = full_body
            print(f"  [本文] {len(summary)}文字: {summary[:80]!r}")

        pub_str = published_dt.isoformat() if published_dt else ""
        print(f"  取得: {title[:60]} [{pub_str[:19]}]")

        news_items.append({
            "id": entry_id,
            "title": title,
            "url": link,
            "summary": summary,
            "image_url": image_url,
            "published_date": pub_str,
        })

    return news_items


def main() -> None:
    print("=== 重賞レース結果取得開始 ===")
    items = fetch_race_results()
    if not items:
        print("対象記事なし。空の news.json を出力します。")
        Path(NEWS_JSON).write_text("[]", encoding="utf-8")
        return
    Path(NEWS_JSON).write_text(
        json.dumps(items, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"=== {len(items)} 件を {NEWS_JSON} に保存 ===")


if __name__ == "__main__":
    main()
