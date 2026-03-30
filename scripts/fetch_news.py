#!/usr/bin/env python3
"""競馬ニュースをRSSフィードから取得してnews.jsonに保存する。
- 公開日時（published）を取得して降順ソート
- 24時間以内 → 48時間以内 → 最新3件 の順に条件を緩和
- 投稿済み（posted_ids.txt）はスキップ
"""

import email.utils
import gzip
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC+%E3%83%AC%E3%83%BC%E3%82%B9&hl=ja&gl=JP&ceid=JP:ja",
]

NEWS_JSON = "news.json"
POSTED_IDS_FILE = "posted_ids.txt"
MAX_NEWS = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

NS = {
    "media": "http://search.yahoo.com/mrss/",
    "atom": "http://www.w3.org/2005/Atom",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

# ---------------------------------------------------------------------------
# 競馬関連フィルタ
# ---------------------------------------------------------------------------

_ALLOW_KEYWORDS = [
    "競馬", "horse racing", "jra", "地方競馬", "騎手", "調教師",
    "レース", "厩舎", "g1", "重賞", "競走馬", "牡馬", "牝馬", "騸馬",
]
_DENY_KEYWORDS = [
    "ボートレース", "競艇", "オートレース", "競輪", "パチンコ", "スロット",
]


def is_keiba_related(entry: dict) -> bool:
    """競馬関連の記事かどうかを判定する。除外キーワード優先。"""
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    for kw in _DENY_KEYWORDS:
        if kw in text:
            return False
    for kw in _ALLOW_KEYWORDS:
        if kw in text:
            return True
    # RSSフィード自体が競馬クエリなので、どのキーワードにも引っかからない場合も通過させる
    return True


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def load_posted_ids() -> set:
    path = Path(POSTED_IDS_FILE)
    if not path.exists():
        return set()
    return set(path.read_text(encoding="utf-8").splitlines())


def http_get(url: str, timeout: int = 20) -> bytes | None:
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding == "gzip":
                data = gzip.decompress(data)
            print(f"  [HTTP] {resp.status} {len(data)} bytes")
            return data
    except URLError as e:
        print(f"  [警告] HTTP取得失敗 ({url[:60]}): {e}", file=sys.stderr)
    except Exception as e:
        print(f"  [警告] 取得エラー ({url[:60]}): {e}", file=sys.stderr)
    return None


def _parse_date(date_str: str) -> datetime | None:
    """RSS pubDate（RFC 2822）またはAtom published（ISO 8601）を datetimeに変換する。"""
    if not date_str:
        return None
    # RFC 2822
    try:
        return email.utils.parsedate_to_datetime(date_str)
    except Exception:
        pass
    # ISO 8601
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# フィード解析
# ---------------------------------------------------------------------------

import xml.etree.ElementTree as ET


def parse_feed(raw: bytes) -> list[dict]:
    """RSS 1.0 / RSS 2.0 / Atom フィードを解析してエントリーリストを返す。"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"  [警告] XML解析失敗: {e}", file=sys.stderr)
        return []

    tag = root.tag.lower()
    entries = []

    if "rss" in tag or root.tag == "rss":
        for item in root.findall(".//item"):
            entries.append(_parse_rss_item(item))
    elif "rdf" in root.tag or "rdf" in tag:
        for item in root.findall(".//{http://purl.org/rss/1.0/}item"):
            entries.append(_parse_rss_item(item))
    else:
        for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            entries.append(_parse_atom_entry(entry))

    return [e for e in entries if e.get("title") and e.get("link")]


def _localname(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _get_text(elem, *localnames) -> str:
    for child in elem:
        ln = _localname(child.tag).lower()
        if ln in localnames and child.text:
            return child.text.strip()
    return ""


def _parse_rss_item(item: ET.Element) -> dict:
    title = _get_text(item, "title")
    link = _get_text(item, "link")
    entry_id = _get_text(item, "guid") or link
    summary = _get_text(item, "description", "summary")

    # 公開日時
    pub_date_raw = _get_text(item, "pubdate")
    published_dt = _parse_date(pub_date_raw)

    # media:content から画像
    image_url = ""
    mc = item.find("media:content", NS)
    if mc is not None:
        medium = mc.get("medium", "")
        url = mc.get("url", "")
        if url and (medium == "image" or re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url, re.I)):
            image_url = url

    if not image_url:
        mt = item.find("media:thumbnail", NS)
        if mt is not None:
            image_url = mt.get("url", "")

    if not image_url:
        enc = item.find("enclosure")
        if enc is not None and enc.get("type", "").startswith("image/"):
            image_url = enc.get("url", "")

    return {
        "id": entry_id,
        "title": title,
        "link": link,
        "summary": summary,
        "image_url": image_url,
        "published_date": published_dt,
    }


def _parse_atom_entry(entry: ET.Element) -> dict:
    ATOM = "http://www.w3.org/2005/Atom"
    title = ""
    t = entry.find(f"{{{ATOM}}}title")
    if t is not None:
        title = (t.text or "").strip()

    link = ""
    for a in entry.findall(f"{{{ATOM}}}link"):
        if a.get("rel", "alternate") == "alternate":
            link = a.get("href", "")
            break
    if not link:
        l_elem = entry.find(f"{{{ATOM}}}link")
        if l_elem is not None:
            link = l_elem.get("href", "")

    entry_id = ""
    id_elem = entry.find(f"{{{ATOM}}}id")
    if id_elem is not None:
        entry_id = (id_elem.text or "").strip()
    if not entry_id:
        entry_id = link

    summary = ""
    for tag in [f"{{{ATOM}}}summary", f"{{{ATOM}}}content"]:
        s = entry.find(tag)
        if s is not None and s.text:
            summary = s.text.strip()
            break

    # 公開日時（published → updated の順に探す）
    pub_date_raw = ""
    for tag_name in ["published", "updated"]:
        e = entry.find(f"{{{ATOM}}}{tag_name}")
        if e is not None and e.text:
            pub_date_raw = e.text.strip()
            break
    published_dt = _parse_date(pub_date_raw)

    image_url = ""
    mt = entry.find("media:thumbnail", NS)
    if mt is not None:
        image_url = mt.get("url", "")
    if not image_url:
        mc = entry.find("media:content", NS)
        if mc is not None:
            image_url = mc.get("url", "")

    return {
        "id": entry_id,
        "title": title,
        "link": link,
        "summary": summary,
        "image_url": image_url,
        "published_date": published_dt,
    }


# ---------------------------------------------------------------------------
# OG画像抽出
# ---------------------------------------------------------------------------

def extract_og_image(url: str, html: str) -> str:
    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            html, re.IGNORECASE,
        )
    if m:
        img = m.group(1).strip()
        if img.startswith("//"):
            return "https:" + img
        elif img.startswith("/"):
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}{img}"
        return img
    return ""


# ---------------------------------------------------------------------------
# メイン取得ロジック
# ---------------------------------------------------------------------------

def fetch_news() -> list[dict]:
    posted_ids = load_posted_ids()
    print(f"投稿済みID数: {len(posted_ids)}")

    now = datetime.now(timezone.utc)
    all_entries: list[dict] = []
    feed_errors = 0

    # 全フィードからエントリーを収集
    for feed_url in RSS_FEEDS:
        print(f"フィード取得中: {feed_url[:80]}")
        raw = http_get(feed_url)
        if not raw:
            feed_errors += 1
            continue
        entries = parse_feed(raw)
        print(f"  有効エントリー: {len(entries)} 件")
        all_entries.extend(entries)

    if feed_errors == len(RSS_FEEDS):
        print("[エラー] 全フィードの取得に失敗しました。", file=sys.stderr)
        sys.exit(1)

    # 公開日時で降順ソート（日時なしは末尾）
    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    all_entries.sort(
        key=lambda e: e.get("published_date") or _epoch,
        reverse=True,
    )

    # 重複除去（IDとURL正規化の両方でチェック）
    # 同一記事がGoogle Newsの複数フィードに異なるIDで載ることがあるため
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for e in all_entries:
        # URLからクエリパラメータ・フラグメントを除去して正規化
        parsed = urlparse(e.get("link", ""))
        normalized_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/").lower()
        if e["id"] in seen_ids or normalized_url in seen_urls:
            continue
        seen_ids.add(e["id"])
        if normalized_url:
            seen_urls.add(normalized_url)
        unique.append(e)

    # 投稿済みを除外
    unposted = [e for e in unique if e["id"] not in posted_ids]
    print(f"未投稿エントリー: {len(unposted)} 件")

    # 競馬関連フィルタ（除外キーワードを含む記事を除去）
    unposted = [e for e in unposted if is_keiba_related(e)]
    print(f"競馬関連フィルタ後: {len(unposted)} 件")

    # 時間フィルタ: 24時間 → 48時間 → 最新3件（条件なし）
    selected: list[dict] = []
    for label, hours in [("24時間以内", 24), ("48時間以内", 48), ("条件なし（最新3件）", None)]:
        if hours is not None:
            cutoff = now - timedelta(hours=hours)
            candidates = [
                e for e in unposted
                if e.get("published_date") and e["published_date"] >= cutoff
            ]
        else:
            candidates = unposted[:MAX_NEWS]

        if candidates:
            selected = candidates[:MAX_NEWS]
            print(f"フィルタ「{label}」で {len(selected)} 件を選択")
            break

    if not selected:
        print("対象ニュースなし。")
        return []

    # OG画像・サマリーを補完してnews_itemsを構築
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

        # 常に記事本文を取得してsummaryを充実させる（RSSのサマリーは短いため）
        raw_html = http_get(link)
        if raw_html:
            html = raw_html.decode("utf-8", errors="replace")
            if not image_url:
                og_img = extract_og_image(link, html)
                if og_img and not re.search(r"google\.com|googleusercontent\.com|gstatic\.com", og_img, re.I):
                    image_url = og_img
            # <article> タグ → <p> タグ → 全体テキスト の順に本文を抽出
            body = ""
            m = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL | re.IGNORECASE)
            if m:
                body = re.sub(r"<[^>]+>", " ", m.group(1))
            if len(body.strip()) < 100:
                paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL | re.IGNORECASE)
                body = " ".join(re.sub(r"<[^>]+>", "", p) for p in paras)
            if len(body.strip()) < 100:
                body = re.sub(r"<[^>]+>", " ", html)
            full_body = re.sub(r"\s+", " ", body).strip()[:2000]
            if len(full_body) > len(summary):
                summary = full_body

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

    # 本文が薄すぎる記事を除外（Gemmaへの無駄な入力を削減）
    # 十分な内容の記事が残る場合のみフィルタを適用
    MIN_CONTENT_LENGTH = 150
    rich_items = [item for item in news_items if len(item.get("summary", "")) >= MIN_CONTENT_LENGTH]
    if rich_items:
        if len(rich_items) < len(news_items):
            removed = len(news_items) - len(rich_items)
            print(f"本文不足({MIN_CONTENT_LENGTH}文字未満)の記事を除外: {removed} 件 → 残り {len(rich_items)} 件")
        news_items = rich_items
    else:
        print(f"[警告] 全記事の本文が{MIN_CONTENT_LENGTH}文字未満のため除外せず継続")

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
        print(f"  - {item['title'][:50]} [画像: {has_img}] [{item['published_date'][:19]}]")


if __name__ == "__main__":
    main()
