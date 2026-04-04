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
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC+%E3%83%AC%E3%83%BC%E3%82%B9&hl=ja&gl=JP&ceid=JP:ja",
    # 重賞・G1など情報量が多い記事が出やすいクエリ
    "https://news.google.com/rss/search?q=%E9%87%8D%E8%B3%9E+%E7%AB%B6%E9%A6%AC+%E5%8B%9D%E5%88%A9&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC+%E3%83%AC%E3%83%BC%E3%82%B9%E7%B5%90%E6%9E%9C&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=JRA+%E7%AB%B6%E9%A6%AC+%E9%A8%8E%E6%89%8B&hl=ja&gl=JP&ceid=JP:ja",
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

# タイトルがこのプレフィックスで始まる記事はニュース記事ではなく動画説明等のため除外
_DENY_TITLE_PREFIXES = [
    "video:", "watch:", "【動画】", "（動画）", "(動画)",
]


def is_keiba_related(entry: dict) -> bool:
    """競馬関連の記事かどうかを判定する。除外キーワード優先。"""
    title = entry.get("title", "")
    # 動画説明タイトルを除外
    if any(title.lower().startswith(p.lower()) for p in _DENY_TITLE_PREFIXES):
        print(f"  [除外] 動画タイトルのためスキップ: {title[:60]}")
        return False
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


def _extract_next_data_body(html: str) -> str:
    """Next.js の __NEXT_DATA__ JSON から記事本文を抽出する。
    Yahoo News Japan など Next.js ベースのサイト向け。"""
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return ""
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return ""

    def _collect_text(obj, depth=0) -> list[str]:
        """JSON オブジェクトを再帰的に辿り、"body" / "text" / "content" キーのテキストを収集。"""
        if depth > 10:
            return []
        texts: list[str] = []
        if isinstance(obj, dict):
            for key in ("body", "text", "content", "description"):
                val = obj.get(key)
                if isinstance(val, str) and len(val) > 30:
                    texts.append(val)
            for val in obj.values():
                texts.extend(_collect_text(val, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                texts.extend(_collect_text(item, depth + 1))
        return texts

    texts = _collect_text(data)
    # 最長のテキストを本文として採用（ナビゲーション等の短いテキストを除く）
    if not texts:
        return ""
    best = max(texts, key=len)
    return re.sub(r"<[^>]+>", " ", best)  # HTML タグが混じっている場合も除去


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


def _decompress(data: bytes, encoding: str) -> bytes:
    """Content-Encoding に応じてデータを展開する。Brotli は非対応のため gzip/deflate のみ。"""
    if encoding == "gzip":
        return gzip.decompress(data)
    if encoding == "deflate":
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    # br (Brotli) は標準ライブラリ非対応のため生データをそのまま返す
    return data


def http_get_article(url: str, timeout: int = 20) -> bytes | None:
    """記事本文取得用。Refererを付与し、失敗時はUser-Agentを変えてリトライ。
    Accept-Encoding は gzip/deflate のみ指定（Brotli は展開不可のため除外）。"""
    _article_headers_base = {
        **HEADERS,
        "Accept-Encoding": "gzip, deflate",  # br を除外して文字化けを防ぐ
    }
    attempts = [
        {**_article_headers_base, "Referer": "https://news.google.com/"},
        {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://news.google.com/",
        },
    ]
    for i, headers in enumerate(attempts, 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                encoding = resp.headers.get("Content-Encoding", "")
                data = _decompress(data, encoding)
                print(f"  [HTTP] {resp.status} {len(data)} bytes (attempt {i})")
                return data
        except URLError as e:
            print(f"  [警告] HTTP取得失敗 attempt={i} ({url[:60]}): {e}", file=sys.stderr)
        except Exception as e:
            print(f"  [警告] 取得エラー attempt={i} ({url[:60]}): {e}", file=sys.stderr)
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

    # 重複除去（ID）
    seen: set[str] = set()
    unique: list[dict] = []
    for e in all_entries:
        if e["id"] not in seen:
            seen.add(e["id"])
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
            candidates = unposted[:MAX_NEWS * 5]

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
        rss_summary = summary  # RSS から取得した元サマリーを保持
        raw_html = http_get_article(link)
        if raw_html:
            html = raw_html.decode("utf-8", errors="replace")
            # __NEXT_DATA__ (Next.js SSR) を script タグ除去前に抽出
            body = _extract_next_data_body(html)
            _method = "__NEXT_DATA__" if len(body.strip()) >= 100 else ""
            # <script> / <style> タグとその中身を除去（JSコードの混入を防ぐ）
            html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
            if not image_url:
                og_img = extract_og_image(link, html)
                if og_img and not re.search(r"google\.com|googleusercontent\.com|gstatic\.com", og_img, re.I):
                    image_url = og_img
            # 本文抽出: __NEXT_DATA__ → <article> → <main> → class/idに"article/content/body/entry"を含む<div> → <p>タグ → og:description → 全体
            # 1. <article> タグ
            if len(body.strip()) < 100:
                m = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL | re.IGNORECASE)
                if m:
                    body = re.sub(r"<[^>]+>", " ", m.group(1))
                    if len(body.strip()) >= 100:
                        _method = "<article>"
            # 2. <main> タグ
            if len(body.strip()) < 100:
                m = re.search(r"<main[^>]*>(.*?)</main>", html, re.DOTALL | re.IGNORECASE)
                if m:
                    body = re.sub(r"<[^>]+>", " ", m.group(1))
                    if len(body.strip()) >= 100:
                        _method = "<main>"
            # 3. class/id に article/content/body/entry/text を含む <div>
            if len(body.strip()) < 100:
                m = re.search(
                    r'<div[^>]+(?:class|id)=["\'][^"\']*(?:article|content|body|entry|text)[^"\']*["\'][^>]*>(.*?)</div>',
                    html, re.DOTALL | re.IGNORECASE,
                )
                if m:
                    body = re.sub(r"<[^>]+>", " ", m.group(1))
                    if len(body.strip()) >= 100:
                        _method = "<div.article>"
            # 4. <p> タグを全部結合
            if len(body.strip()) < 100:
                paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL | re.IGNORECASE)
                body = " ".join(re.sub(r"<[^>]+>", "", p) for p in paras)
                if len(body.strip()) >= 100:
                    _method = "<p>タグ"
            # 5. og:description を補完テキストとして追加
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
            # 6. <p> でも不十分なら全体テキスト
            if len(body.strip()) < 100:
                body = re.sub(r"<[^>]+>", " ", html)
                _method = "全体HTML"
            full_body = re.sub(r"\s+", " ", body).strip()[:2000]
            if og_desc and og_desc not in full_body:
                full_body = (og_desc + " " + full_body).strip()[:2000]
            if len(full_body) > len(summary):
                summary = full_body
            # ===== DEBUG LOG（原因調査用・後で削除） =====
            print(f"  [DEBUG] RSS元サマリー({len(rss_summary)}文字): {rss_summary[:100]!r}")
            print(f"  [DEBUG] 抽出方法: {_method or '不明'} / og:desc({len(og_desc)}文字)")
            print(f"  [DEBUG] 最終本文({len(summary)}文字):\n{summary}")
            print(f"  [DEBUG] ---END---")

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
