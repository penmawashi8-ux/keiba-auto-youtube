#!/usr/bin/env python3
"""競馬ニュースをRSSフィードから取得してnews.jsonに保存する。
- 公開日時（published）を取得して降順ソート
- 24時間以内 → 48時間以内 → 最新3件 の順に条件を緩和
- 投稿済み（posted_ids.txt）はスキップ
"""

import base64
import email.utils
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
from urllib.parse import urlparse

import requests as _requests

RSS_FEEDS = [
    # --- 競馬専門・スポーツ紙（直接RSS）---
    "https://uma-jin.net/feed",                            # うまじん (WordPress標準)
    "https://uma-jin.net/news/feed",                       # うまじん (旧URL)
    "https://www.sponichi.co.jp/gambling/rss/",            # スポニチ競馬
    "https://www.nikkansports.com/keiba/rss/keiba-g-rss.xml",  # 日刊スポーツ競馬
    # --- Google News（記事URLはCAPTCHAで取得不可だがタイトルのフォールバックとして）---
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC+%E3%83%AC%E3%83%BC%E3%82%B9&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E9%87%8D%E8%B3%9E+%E7%AB%B6%E9%A6%AC+%E5%8B%9D%E5%88%A9&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC+%E3%83%AC%E3%83%BC%E3%82%B9%E7%B5%90%E6%9E%9C&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=JRA+%E7%AB%B6%E9%A6%AC+%E9%A8%8E%E6%89%8B&hl=ja&gl=JP&ceid=JP:ja",
    # --- Yahoo ニュース（Google News 経由）---
    "https://news.google.com/rss/search?q=%E7%AB%B6%E9%A6%AC+news.yahoo.co.jp&hl=ja&gl=JP&ceid=JP:ja",
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


# Google が要求する同意クッキー（リダイレクトを機能させるため）
_GNEWS_COOKIES = {
    "CONSENT": "YES+cb.20230629-02-p0.ja+FX+301",
    "SOCS": "CAISHAgCEhIaAB",
}


def _gnews_resolve(rss_url: str, timeout: int = 12) -> str:
    """Google News RSS URL（/rss/articles/TOKEN）を実記事 URL に解決する。
    __i/rss/rd/articles/ 形式のみ試みる（/articles/ はGitHub ActionsのIPがCAPTCHA対象）。
    レスポンスボディに URL が含まれる場合も検出する。"""
    m = re.search(r'/rss/articles/([A-Za-z0-9_-]+)', rss_url)
    if not m:
        return ""
    token = m.group(1)

    # /articles/ は GitHub Actions IP から CAPTCHA になるため試さない
    rd_url = f"https://news.google.com/__i/rss/rd/articles/{token}"

    _EXCLUDE = re.compile(r"google\.com|googleusercontent\.com|gstatic\.com", re.I)

    for ua_label, ua in [
        ("desktop", HEADERS["User-Agent"]),
        ("mobile", (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        )),
    ]:
        try:
            session = _requests.Session()
            session.headers.update({
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Referer": "https://news.google.com/",
            })
            session.cookies.update(_GNEWS_COOKIES)

            resp = session.get(rd_url, allow_redirects=True, timeout=timeout, stream=True)
            final = resp.url

            # HTTP リダイレクトで非 Google URL へ到達した場合
            if not _EXCLUDE.search(final):
                resp.close()
                print(f"  [GNews] __i/rss/rd redirect成功 ({ua_label}): {final[:100]}")
                return final

            # canonical は <head> 先頭付近にあるため 2KB で十分
            content = b""
            for chunk in resp.iter_content(chunk_size=2048):
                content += chunk
                if len(content) >= 2048:
                    break
            resp.close()

            body = content.decode("utf-8", errors="replace")
            # canonical URL をログに残す（次の確認のため）
            canon_m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', body, re.I)
            canon_url = canon_m.group(1) if canon_m else "(not found)"
            print(f"  [GNews] __i/rss/rd canonical ({ua_label}): {canon_url!r}")

            # JSON {"redirect": "..."} 形式
            try:
                import json as _json
                data = _json.loads(body)
                for key in ("redirect", "url", "articleUrl", "targetUrl"):
                    val = data.get(key, "")
                    if isinstance(val, str) and val.startswith("http") and not _EXCLUDE.search(val):
                        print(f"  [GNews] JSON {key}: {val[:100]}")
                        return val
            except Exception:
                pass

            # プレーンテキスト URL
            if body.strip().startswith("http") and not _EXCLUDE.search(body.strip()):
                url_plain = body.strip().split()[0]
                print(f"  [GNews] plaintext URL: {url_plain[:100]}")
                return url_plain

            # HTML/JS から URL 抽出
            real_url = _resolve_google_news_url(body)
            if real_url:
                print(f"  [GNews] body HTML抽出成功 ({ua_label}): {real_url[:100]}")
                return real_url

            print(f"  [GNews] __i/rss/rd 失敗 ({ua_label}): status={resp.status_code} final={final[:60]!r}", file=sys.stderr)

        except Exception as e:
            print(f"  [GNews] 例外 ({ua_label}): {e}", file=sys.stderr)

    return ""


def _decode_google_news_url(google_url: str) -> str:
    """Google News RSS リンクに含まれる Base64 エンコードされた実記事 URL を取得する。
    HTTP リクエスト不要。例: https://news.google.com/rss/articles/CBMiSmh0dHBz..."""
    # /articles/ または /read/ 形式に対応
    m = re.search(r'/(?:articles|read)/([A-Za-z0-9_-]+)', google_url)
    if not m:
        print(f"  [GNews] URLデコード: パターン不一致 {google_url[:80]!r}", file=sys.stderr)
        return ""
    encoded = m.group(1)
    # URL-safe base64 のパディング調整
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        data = base64.urlsafe_b64decode(padded)
    except Exception as e:
        print(f"  [GNews] base64デコード失敗: {e}", file=sys.stderr)
        return ""
    # protobuf バイナリ内の https:// で始まる文字列を正規表現で抽出
    try:
        text = data.decode("latin-1")
    except Exception:
        return ""
    url_m = re.search(r'https?://[a-zA-Z0-9._~:/?#\[\]@!$&\'()*+,;=%\-]+', text)
    if url_m:
        url = url_m.group(0).rstrip(".,;)")
        if "google.com" not in url:
            return url
    print(f"  [GNews] base64デコード成功だがURL抽出失敗。decoded={text[:60]!r}", file=sys.stderr)
    return ""


def _resolve_google_news_url(html: str) -> str:
    """Google News ページから実際の記事 URL を抽出する。
    Google News の RSS リンクは JS リダイレクト経由のため urlopen が辿れない。"""
    _EXCLUDE = re.compile(
        r'google\.com|googleusercontent\.com|gstatic\.com|googleapis\.com|youtube\.com|goo\.gl',
        re.I,
    )

    def _clean(url: str) -> str:
        return url.replace(r'\/', '/').replace(r'\\.', '.').rstrip(".,;)\"'\\")

    # 1. <link rel="canonical"> — Google News が実記事 URL を canonical に設定する場合がある
    for pat in [
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            url = _clean(m.group(1))
            if url.startswith("http") and not _EXCLUDE.search(url):
                print(f"  [GNews resolve] canonical: {url[:80]}")
                return url
    # 2. data-n-au 属性
    m = re.search(r'data-n-au=["\']([^"\']+)["\']', html)
    if m:
        url = _clean(m.group(1))
        if url.startswith("http") and not _EXCLUDE.search(url):
            print(f"  [GNews resolve] data-n-au: {url[:80]}")
            return url
    # 2. JSON の "url" / "articleUrl" フィールド（通常形式 + エスケープ形式）
    for url_m in re.finditer(
        r'"(?:url|articleUrl|targetUrl|originalUrl|sourceUrl)"\s*:\s*"((?:https?:|https?:\\\/)(?:\\/|/)[^"]{10,})"',
        html,
    ):
        url = _clean(url_m.group(1))
        if not _EXCLUDE.search(url):
            print(f"  [GNews resolve] JSON url field: {url[:80]}")
            return url
    # 3. meta refresh
    m = re.search(
        r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^;]*;\s*url=([^"\'>\s]+)',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    # 4. window.location
    m = re.search(r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']', html)
    if m:
        url = _clean(m.group(1))
        if url.startswith("http") and not _EXCLUDE.search(url):
            return url
    # 5. og:url
    for pat in [
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            url = _clean(m.group(1))
            if url.startswith("http") and not _EXCLUDE.search(url):
                return url
    # 6. 既知ニュースドメインのURL（通常形式 + \/エスケープ形式 の両方）
    _NEWS_DOMAINS = (
        r'news\.yahoo\.co\.jp|www\.nikkansports\.com|www\.sponichi\.co\.jp'
        r'|www\.daily\.co\.jp|www\.hochi\.com|www\.tokyosports\.co\.jp'
        r'|www\.nikkei\.com|mainichi\.jp|www\.yomiuri\.co\.jp'
        r'|www\.asahi\.com|www\.sankei\.com|www3\.nhk\.or\.jp'
        r'|uma-jin\.net|news\.netkeiba\.com|race\.sanspo\.com'
    )
    _news_domains_escaped = _NEWS_DOMAINS.replace(".", "\\.")
    for pat in [
        rf'https?://(?:{_NEWS_DOMAINS})/[^\s"\'<>\\]{{10,}}',             # 通常
        rf'https?:\\/\\/(?:{_news_domains_escaped})\\/[^\s"\'<>]{{10,}}', # \/エスケープ
    ]:
        for url_m in re.finditer(pat, html, re.IGNORECASE):
            url = _clean(url_m.group(0))
            if not _EXCLUDE.search(url):
                print(f"  [GNews resolve] news domain fallback: {url[:80]}")
                return url
    print(f"  [GNews resolve] 全パターン失敗", file=sys.stderr)
    return ""


def _is_google_news_page(url: str, html: str) -> bool:
    """URL または HTML の内容から Google News ページかどうかを判定する。"""
    # URL で判定（最も確実）
    if "news.google.com" in url:
        return True
    # コンテンツで判定（全体を対象に）
    indicators = ["世界中のニュース提供元から集約", "Google ニュース"]
    return all(ind in html for ind in indicators)


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


def _make_session() -> "_requests.Session":
    """共通ヘッダーを設定した requests.Session を返す。"""
    s = _requests.Session()
    s.headers.update(HEADERS)
    return s


def http_get(url: str, timeout: int = 20) -> bytes | None:
    try:
        resp = _make_session().get(url, timeout=timeout, allow_redirects=True)
        print(f"  [HTTP] {resp.status_code} {len(resp.content)} bytes")
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"  [警告] HTTP取得失敗 ({url[:60]}): {e}", file=sys.stderr)
    return None


def http_get_article(url: str, timeout: int = 20) -> tuple[bytes | None, str]:
    """記事本文取得用。(data, final_url) を返す。requests を使いセッション維持でリダイレクト追跡。"""
    attempt_headers = [
        # 1st: Desktop Chrome + Google News Referer
        {
            **HEADERS,
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://news.google.com/",
        },
        # 2nd: Mobile Safari
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
    for i, headers in enumerate(attempt_headers, 1):
        try:
            session = _requests.Session()
            session.headers.update(headers)
            resp = session.get(url, timeout=timeout, allow_redirects=True)
            final_url = resp.url
            data = resp.content
            print(f"  [HTTP] {resp.status_code} {len(data)} bytes (attempt {i})")
            if final_url != url:
                print(f"  [HTTP] リダイレクト先: {final_url[:100]}")
            if resp.status_code >= 400:
                print(f"  [警告] HTTP {resp.status_code} (attempt {i})", file=sys.stderr)
                continue
            return data, final_url
        except Exception as e:
            print(f"  [警告] HTTP取得失敗 attempt={i} ({url[:60]}): {e}", file=sys.stderr)
    return None, url


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

    valid = [e for e in entries if e.get("title") and e.get("link")]
    if not valid and entries:
        print(f"  [警告] エントリー{len(entries)}件あるがtitle/linkが空。root.tag={root.tag!r}", file=sys.stderr)
        if entries:
            print(f"  [警告] 先頭エントリー: {entries[0]}", file=sys.stderr)
    elif not valid and not entries:
        print(f"  [警告] エントリー0件。root.tag={root.tag!r} children={[c.tag for c in list(root)[:5]]}", file=sys.stderr)
    return valid


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

    # Google News RSS の <description> には実際の記事 URL が <a href="..."> として含まれる。
    # これが最も確実に実記事 URL を取得できる方法。
    source_url = ""
    if summary:
        for href_m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\']', summary, re.IGNORECASE):
            href = href_m.group(1)
            if href.startswith("http") and "google.com" not in href:
                source_url = href
                break
    # ===== DEBUG RSS ===== （最初の10件だけ表示）
    if not hasattr(_parse_rss_item, "_debug_count"):
        _parse_rss_item._debug_count = 0
    if _parse_rss_item._debug_count < 10:
        _parse_rss_item._debug_count += 1
        print(f"  [RSS#{_parse_rss_item._debug_count}] raw_link={link!r}")
        print(f"  [RSS#{_parse_rss_item._debug_count}] desc={summary!r}")
    # =====================
    if source_url:
        link = source_url  # 実際の記事 URL を優先

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

    # description の <a href> から実記事 URL を取得
    if summary:
        for href_m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\']', summary, re.IGNORECASE):
            href = href_m.group(1)
            if href.startswith("http") and "google.com" not in href:
                link = href
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
# RSS 自動検出 & 代替スクレイパー
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GNews API（実記事URL + 本文を取得）
# ---------------------------------------------------------------------------

_GNEWS_QUERIES = [
    "競馬 レース 騎手",
    "JRA 重賞 競走馬",
]

def fetch_gnews_articles() -> list[dict]:
    """GNews API で競馬ニュースを取得する。
    実記事 URL と content（本文冒頭）が得られる。
    環境変数 GNEWS_API_KEY が必要。無料枠: 100 req/日。"""
    api_key = os.environ.get("GNEWS_API_KEY", "")
    if not api_key:
        print("  [GNews] GNEWS_API_KEY 未設定。スキップ。", file=sys.stderr)
        return []

    all_articles: list[dict] = []
    seen_urls: set[str] = set()

    for q in _GNEWS_QUERIES:
        url = (
            "https://gnews.io/api/v4/search"
            f"?q={_requests.utils.quote(q)}"
            "&lang=ja&country=jp&max=10"
            f"&apikey={api_key}"
        )
        try:
            resp = _requests.get(url, timeout=15)
            print(f"  [GNews] query={q!r} status={resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [GNews] 取得失敗 ({q!r}): {e}", file=sys.stderr)
            continue

        for art in data.get("articles", []):
            article_url = art.get("url", "")
            if not article_url or article_url in seen_urls:
                continue
            seen_urls.add(article_url)

            # content > description の順で本文を選択
            content = (art.get("content") or art.get("description") or "").strip()
            # gnews の content は末尾に "[N chars]" が付くので除去
            content = re.sub(r"\s*\[\d+ chars\]\s*$", "", content).strip()

            pub_dt = _parse_date(art.get("publishedAt", ""))
            all_articles.append({
                "id": article_url,
                "title": art.get("title", ""),
                "link": article_url,
                "summary": content,
                "image_url": art.get("image", "") or "",
                "published_date": pub_dt,
            })

    print(f"  [GNews] 計 {len(all_articles)} 件取得")
    return all_articles


def _autodiscover_rss(html: str, base_url: str) -> str:
    """HTML 内の <link type="application/rss+xml"> からフィード URL を自動検出する。"""
    from urllib.parse import urljoin
    for pat in [
        r'<link[^>]+type=["\']application/rss\+xml["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/rss\+xml["\']',
        r'<link[^>]+type=["\']application/atom\+xml["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/atom\+xml["\']',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            return urljoin(base_url, m.group(1))
    return ""


def scrape_jra_news() -> list[dict]:
    """JRA公式サイトのニュース一覧を直接スクレイピングする。
    JRA (jra.go.jp) は政府関連サイトのため GitHub Actions からもアクセス可能。"""
    base = "https://www.jra.go.jp"
    urls_to_try = [
        f"{base}/news/",
        f"{base}/news/index.html",
    ]
    for list_url in urls_to_try:
        raw = http_get(list_url)
        if not raw:
            continue
        html = raw.decode("utf-8", errors="replace")
        entries = []
        seen: set[str] = set()
        # JRA ニュースリンクパターン（/news/YYYYMM/NNNNN.html 形式）
        for m in re.finditer(
            r'href=["\'](/news/[^"\']+\.html)["\'][^>]*>([^<]{5,})',
            html, re.I,
        ):
            path, title = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
            url = base + path
            if url in seen or not title:
                continue
            seen.add(url)
            entries.append({
                "id": url, "title": title, "link": url,
                "summary": "", "image_url": "", "published_date": None,
            })
        if entries:
            print(f"  [JRA] {len(entries)} 件スクレイプ")
            return entries[:20]
        # RSS 自動検出を試みる
        rss_url = _autodiscover_rss(html, list_url)
        if rss_url:
            print(f"  [JRA] RSS自動検出: {rss_url}")
            raw2 = http_get(rss_url)
            if raw2:
                return parse_feed(raw2)
    print(f"  [JRA] 取得失敗", file=sys.stderr)
    return []


def scrape_yahoo_news() -> list[dict]:
    """Yahoo ニュース Japan の競馬検索ページを直接スクレイピングする。
    Yahoo Japan は RSS を廃止しているため HTML を解析して記事リストを取得する。
    Next.js の __NEXT_DATA__ JSON → href パターン の順に試みる。"""
    from urllib.parse import urljoin, quote

    YAHOO_BASE = "https://news.yahoo.co.jp"
    search_query = quote("競馬")
    urls_to_try = [
        f"{YAHOO_BASE}/search?p={search_query}&ei=UTF-8",
        f"{YAHOO_BASE}/topics/horse-racing",
        f"{YAHOO_BASE}/search?p={quote('競馬 レース')}&ei=UTF-8",
    ]

    YAHOO_HEADERS = {
        **HEADERS,
        "Referer": "https://news.yahoo.co.jp/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cookie": "B=; T=; Y=",
    }

    def _extract_articles_from_next_data(html: str) -> list[dict]:
        """__NEXT_DATA__ JSON を再帰的に探索して記事リストを構築する。"""
        m = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>',
            html, re.DOTALL | re.IGNORECASE,
        )
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            return []

        articles: list[dict] = []
        seen_urls: set[str] = set()

        def _walk(obj, depth: int = 0) -> None:
            if depth > 15 or not obj:
                return
            if isinstance(obj, dict):
                url = obj.get("url") or obj.get("link") or obj.get("articleUrl") or ""
                title = obj.get("title") or obj.get("headline") or ""
                # Yahoo 記事 URL パターンに一致するかチェック
                if (
                    isinstance(url, str)
                    and "yahoo.co.jp/articles/" in url
                    and isinstance(title, str)
                    and len(title) >= 5
                    and url not in seen_urls
                ):
                    seen_urls.add(url)
                    summary = (
                        obj.get("description") or obj.get("summary")
                        or obj.get("body") or ""
                    )
                    if not isinstance(summary, str):
                        summary = ""
                    image_url = ""
                    thumbnail = obj.get("thumbnail") or obj.get("image") or {}
                    if isinstance(thumbnail, dict):
                        image_url = thumbnail.get("url") or thumbnail.get("src") or ""
                    elif isinstance(thumbnail, str):
                        image_url = thumbnail
                    pub_raw = (
                        obj.get("publishedAt") or obj.get("published")
                        or obj.get("createdAt") or ""
                    )
                    published_dt = _parse_date(pub_raw) if pub_raw else None
                    articles.append({
                        "id": url,
                        "title": title.strip(),
                        "link": url,
                        "summary": summary.strip(),
                        "image_url": image_url,
                        "published_date": published_dt,
                    })
                for val in obj.values():
                    _walk(val, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item, depth + 1)

        _walk(data)
        return articles

    def _extract_articles_from_html(html: str, base_url: str) -> list[dict]:
        """href パターンで Yahoo 記事 URL とタイトルを抽出するフォールバック。"""
        articles: list[dict] = []
        seen_urls: set[str] = set()
        # <a href="/articles/..." ...>タイトル</a> もしくは絶対URL
        for m in re.finditer(
            r'<a\s[^>]*href=["\']((https://news\.yahoo\.co\.jp)?/articles/([a-z0-9]+))["\'][^>]*>([^<]{5,100})</a>',
            html, re.IGNORECASE,
        ):
            path, _, article_id, title = m.group(1), m.group(2), m.group(3), m.group(4)
            url = path if path.startswith("http") else urljoin(base_url, path)
            title = re.sub(r"\s+", " ", title).strip()
            if url in seen_urls or not title:
                continue
            seen_urls.add(url)
            articles.append({
                "id": url,
                "title": title,
                "link": url,
                "summary": "",
                "image_url": "",
                "published_date": None,
            })
        return articles

    for page_url in urls_to_try:
        print(f"  [YahooNews] 取得中: {page_url}")
        try:
            session = _requests.Session()
            session.headers.update(YAHOO_HEADERS)
            resp = session.get(page_url, timeout=20, allow_redirects=True)
            print(f"  [YahooNews] status={resp.status_code} size={len(resp.content)} bytes")
            if resp.status_code >= 400:
                print(f"  [YahooNews] HTTP {resp.status_code}。スキップ。", file=sys.stderr)
                continue
            html = resp.content.decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  [YahooNews] 取得例外: {e}", file=sys.stderr)
            continue

        # 1st: __NEXT_DATA__ から抽出
        articles = _extract_articles_from_next_data(html)
        if articles:
            print(f"  [YahooNews] __NEXT_DATA__ から {len(articles)} 件取得")
            return articles[:20]

        # 2nd: href パターンで抽出
        articles = _extract_articles_from_html(html, page_url)
        if articles:
            print(f"  [YahooNews] href パターンから {len(articles)} 件取得")
            return articles[:20]

        print(f"  [YahooNews] {page_url} から記事抽出できず。次URLへ。", file=sys.stderr)

    print(f"  [YahooNews] 全URL失敗。スキップ。", file=sys.stderr)
    return []


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

    # --- GNews API（最優先: 実記事URL + 本文が取得できる）---
    print("=== GNews API ===")
    gnews_api_entries = fetch_gnews_articles()
    all_entries.extend(gnews_api_entries)

    # 全フィードからエントリーを収集
    print("=== RSS フィード ===")
    for feed_url in RSS_FEEDS:
        print(f"フィード取得中: {feed_url[:80]}")
        raw = http_get(feed_url)
        if not raw:
            feed_errors += 1
            continue
        if len(raw) == 0:
            print(f"  [警告] レスポンス0bytes。スキップ。", file=sys.stderr)
            feed_errors += 1
            continue
        entries = parse_feed(raw)
        if not entries:
            # XML 解析失敗 → HTML が返った可能性。RSS 自動検出を試みる
            html_str = raw.decode("utf-8", errors="replace")
            if "<html" in html_str[:500].lower():
                discovered = _autodiscover_rss(html_str, feed_url)
                if discovered and discovered != feed_url:
                    print(f"  [RSS自動検出] {discovered[:80]}")
                    raw2 = http_get(discovered)
                    if raw2:
                        entries = parse_feed(raw2)
                        if entries:
                            print(f"  [RSS自動検出] {len(entries)} 件取得成功")
            if not entries:
                print(f"  [DEBUG] 0件フィードRAW先頭300bytes: {raw[:300]!r}", file=sys.stderr)
        print(f"  有効エントリー: {len(entries)} 件")
        all_entries.extend(entries)

    # JRA 公式ニューススクレイピング（Google News とは独立したソース）
    print("JRA公式ニュースをスクレイピング中...")
    jra_entries = scrape_jra_news()
    all_entries.extend(jra_entries)

    # Yahoo ニュース直接スクレイピング
    print("Yahoo ニュースをスクレイピング中...")
    yahoo_entries = scrape_yahoo_news()
    all_entries.extend(yahoo_entries)

    if feed_errors == len(RSS_FEEDS) and not jra_entries and not gnews_api_entries and not yahoo_entries:
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

        # --- 実記事URLの解決 ---
        prefetched_html: bytes | None = None
        if "news.google.com" in link:
            # 1. base64 デコード（旧フォーマット用）
            decoded = _decode_google_news_url(link)
            if decoded:
                print(f"  [GNews] URLデコード成功: {decoded[:80]}")
                link = decoded
            else:
                # 2. _gnews_resolve: HEAD/GET/HTML抽出 を複数URL形式で試みる
                print(f"  [GNews] URL解決試行中...")
                resolved = _gnews_resolve(link)
                if resolved:
                    link = resolved
                else:
                    print(f"  [GNews] 全手段失敗。RSSサマリーのみ使用", file=sys.stderr)
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
                    continue

        # --- 記事HTMLの取得（prefetchedがあればそれを使う）---
        if prefetched_html is not None:
            raw_html, fetched_url = prefetched_html, link
        else:
            raw_html, fetched_url = http_get_article(link)
            if raw_html and fetched_url != link and "google.com" not in fetched_url:
                link = fetched_url
                print(f"  [GNews] リダイレクトで実URL取得: {link[:100]}")

        if raw_html:
            html = raw_html.decode("utf-8", errors="replace")
            print(f"  [DEBUG] フェッチURL: {fetched_url[:100]}")
            print(f"  [DEBUG] HTML長: {len(html)}文字")
            # Google News ページが返ってきた場合は HTML から URL を抽出して再フェッチ
            if _is_google_news_page(fetched_url, html):
                real_url = _resolve_google_news_url(html)
                if real_url:
                    print(f"  [GNews] HTMLから実URLを検出: {real_url[:80]}")
                    raw2, fetched_url2 = http_get_article(real_url)
                    if raw2:
                        html = raw2.decode("utf-8", errors="replace")
                        link = fetched_url2 if "google.com" not in fetched_url2 else real_url
                else:
                    print(f"  [GNews] 実URL抽出失敗。RSSサマリーのみ使用", file=sys.stderr)
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
                    continue
            # __NEXT_DATA__ (Next.js SSR) を script タグ除去前に抽出
            body = _extract_next_data_body(html)
            _method = "__NEXT_DATA__" if len(body.strip()) >= 100 else ""
            # JSON-LD (application/ld+json) から articleBody を抽出
            if len(body.strip()) < 100:
                for jld_m in re.finditer(
                    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    html, re.DOTALL | re.IGNORECASE,
                ):
                    try:
                        jld = json.loads(jld_m.group(1))
                        if isinstance(jld, list):
                            jld = next((x for x in jld if isinstance(x, dict)), {})
                        ab = jld.get("articleBody") or jld.get("description") or ""
                        if isinstance(ab, str) and len(ab) >= 100:
                            body = ab
                            _method = "JSON-LD"
                            break
                    except (json.JSONDecodeError, ValueError):
                        continue
            # <script> / <style> タグとその中身を除去（JSコードの混入を防ぐ）
            html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
            if not image_url:
                og_img = extract_og_image(link, html)
                if og_img and not re.search(r"google\.com|googleusercontent\.com|gstatic\.com", og_img, re.I):
                    image_url = og_img
            # 本文抽出: JSON-LD/__NEXT_DATA__ → <article> → <main> → class/idに"article/content/body/entry"を含む<div> → <p>タグ → og:description → 全体
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
            # 3. class/id に article/content/body/entry/text/paragraph を含む <div>（複数マッチして最長を採用）
            if len(body.strip()) < 100:
                best_div = ""
                for div_m in re.finditer(
                    r'<div[^>]+(?:class|id)=["\'][^"\']*(?:article|content|body|entry|text|paragraph|story)[^"\']*["\'][^>]*>(.*?)</div>',
                    html, re.DOTALL | re.IGNORECASE,
                ):
                    candidate = re.sub(r"<[^>]+>", " ", div_m.group(1)).strip()
                    if len(candidate) > len(best_div):
                        best_div = candidate
                if len(best_div) >= 100:
                    body = best_div
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
            full_body_raw = re.sub(r"\s+", " ", body).strip()
            if og_desc and og_desc not in full_body_raw:
                full_body_raw = (og_desc + " " + full_body_raw).strip()
            full_body = full_body_raw[:2000]
            if len(full_body) > len(summary):
                summary = full_body
            # ===== DEBUG LOG（原因調査用・後で削除） =====
            print(f"  [DEBUG] RSS元サマリー({len(rss_summary)}文字): {rss_summary[:100]!r}")
            print(f"  [DEBUG] 抽出方法: {_method or '不明'} / og:desc({len(og_desc)}文字)")
            print(f"  [DEBUG] 最終本文全文({len(full_body_raw)}文字):\n{full_body_raw}")
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
