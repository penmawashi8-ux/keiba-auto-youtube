#!/usr/bin/env python3
"""競馬ニュースをRSSフィードから取得してnews.jsonに保存する。"""

import json
import re
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

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KeibaBot/1.0)"}


def load_posted_ids() -> set:
    path = Path(POSTED_IDS_FILE)
    if not path.exists():
        return set()
    return set(path.read_text(encoding="utf-8").splitlines())


def extract_og_image(url: str) -> str:
    """記事URLの og:image メタタグから画像URLを取得する。"""
    try:
        resp = requests.get(url, timeout=10, headers=HEADERS)
        resp.raise_for_status()
        html = resp.text

        # property="og:image" content="..." の順
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE
        )
        if not m:
            # content="..." property="og:image" の順（逆順）
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                html, re.IGNORECASE
            )
        if m:
            img_url = m.group(1).strip()
            # 相対URLを絶対URLに変換
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/"):
                from urllib.parse import urlparse
                parsed = urlparse(url)
                img_url = f"{parsed.scheme}://{parsed.netloc}{img_url}"
            return img_url
    except Exception as e:
        print(f"  [警告] OG画像取得失敗 ({url[:60]}): {e}", file=sys.stderr)
    return ""


def extract_rss_image(entry) -> str:
    """RSSエントリーのメディア情報から画像URLを取得する。"""
    # media:content
    media_content = entry.get("media_content", [])
    for m in media_content:
        url = m.get("url", "")
        medium = m.get("medium", "")
        if url and (medium == "image" or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
            return url

    # enclosure
    enclosures = entry.get("enclosures", [])
    for enc in enclosures:
        if enc.get("type", "").startswith("image/"):
            return enc.get("href", "")

    # media:thumbnail
    media_thumbnail = entry.get("media_thumbnail", [])
    if media_thumbnail:
        return media_thumbnail[0].get("url", "")

    return ""


def fetch_entry_text(url: str, html_cache: dict) -> str:
    """記事HTMLから本文テキストを簡易取得する（OG画像取得と共有）。"""
    if url in html_cache:
        html = html_cache[url]
    else:
        try:
            resp = requests.get(url, timeout=10, headers=HEADERS)
            resp.raise_for_status()
            html = resp.text
            html_cache[url] = html
        except Exception as e:
            print(f"  [警告] 本文取得失敗 ({url[:60]}): {e}", file=sys.stderr)
            return ""

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def extract_og_image_from_html_cache(url: str, html_cache: dict) -> str:
    """キャッシュ済みHTMLから og:image を取得する。"""
    html = html_cache.get(url, "")
    if not html:
        return ""

    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            html, re.IGNORECASE
        )
    if m:
        img_url = m.group(1).strip()
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        elif img_url.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(url)
            img_url = f"{parsed.scheme}://{parsed.netloc}{img_url}"
        return img_url
    return ""


def fetch_news() -> list[dict]:
    posted_ids = load_posted_ids()
    news_items = []
    html_cache: dict[str, str] = {}

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

            # 1. RSSのメディア情報から画像を取得
            image_url = extract_rss_image(entry)

            # 2. 本文・サマリー取得（HTMLキャッシュを活用）
            if not summary or not image_url:
                fetch_entry_text(link, html_cache)  # キャッシュに乗せる
                if not summary:
                    summary = fetch_entry_text(link, html_cache)
                # 3. キャッシュ済みHTMLからog:imageを取得
                if not image_url:
                    image_url = extract_og_image_from_html_cache(link, html_cache)

            if image_url:
                print(f"  画像取得: {image_url[:80]}")
            else:
                print(f"  [情報] 画像なし（フォールバック背景を使用）")

            news_items.append({
                "id": entry_id,
                "title": title,
                "url": link,
                "summary": summary,
                "image_url": image_url,
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
    for item in news_items:
        has_img = "あり" if item["image_url"] else "なし"
        print(f"  - {item['title'][:50]} [画像: {has_img}]")


if __name__ == "__main__":
    main()
