#!/usr/bin/env python3
"""競馬ニュースをRSSフィードから取得してnews.jsonに保存する。"""

import json
import os
import sys
from pathlib import Path

import feedparser
import requests

RSS_FEEDS = [
    "https://news.netkeiba.com/?pid=news_rss",
    "https://www.sponichi.co.jp/gamble/rss/atom/index.rdf",
]

NEWS_JSON = "news.json"
POSTED_IDS_FILE = "posted_ids.txt"
MAX_NEWS = 3


def load_posted_ids() -> set:
    path = Path(POSTED_IDS_FILE)
    if not path.exists():
        return set()
    return set(path.read_text(encoding="utf-8").splitlines())


def save_posted_ids(ids: set) -> None:
    Path(POSTED_IDS_FILE).write_text(
        "\n".join(sorted(ids)), encoding="utf-8"
    )


def fetch_entry_text(url: str) -> str:
    """記事URLからテキストを取得する（簡易版）。"""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        # HTMLタグを簡易除去（BeautifulSoupなし）
        import re
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:500]
    except Exception as e:
        print(f"  [警告] 本文取得失敗 ({url}): {e}", file=sys.stderr)
        return ""


def fetch_news() -> list[dict]:
    posted_ids = load_posted_ids()
    news_items = []

    for feed_url in RSS_FEEDS:
        print(f"フィード取得中: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"  [エラー] フィード解析失敗: {e}", file=sys.stderr)
            continue

        if feed.bozo and feed.bozo_exception:
            print(f"  [警告] フィード解析エラー: {feed.bozo_exception}", file=sys.stderr)

        for entry in feed.entries:
            if len(news_items) >= MAX_NEWS:
                break

            entry_id = entry.get("id") or entry.get("link", "")
            if not entry_id:
                continue
            if entry_id in posted_ids:
                print(f"  スキップ（投稿済み）: {entry.get('title', '')[:40]}")
                continue

            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "").strip()

            if not title or not link:
                continue

            # サマリーがなければURLから取得を試みる
            if not summary:
                summary = fetch_entry_text(link)

            news_items.append({
                "id": entry_id,
                "title": title,
                "url": link,
                "summary": summary,
            })
            print(f"  取得: {title[:60]}")

        if len(news_items) >= MAX_NEWS:
            break

    return news_items


def main() -> None:
    print("=== 競馬ニュース取得開始 ===")
    news_items = fetch_news()

    if not news_items:
        print("新着ニュースなし。処理を終了します。")
        Path(NEWS_JSON).write_text("[]", encoding="utf-8")
        sys.exit(0)

    Path(NEWS_JSON).write_text(
        json.dumps(news_items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n{len(news_items)} 件のニュースを {NEWS_JSON} に保存しました。")


if __name__ == "__main__":
    main()
