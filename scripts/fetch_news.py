#!/usr/bin/env python3
"""競馬ニュースをRSSフィードから取得してnews.jsonに保存する。
- 公開日時（published）を取得して降順ソート
- 24時間以内 → 48時間以内 → 最新3件 の順に条件を緩和
- 投稿済み（posted_ids.txt）はスキップ
"""

import email.utils
import gzip
import http.client
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

RSS_FEEDS = [
    # 直接記事URLを提供する日本競馬専門サイト（優先）
    "https://rss.netkeiba.com/?pid=rss_netkeiba&site=netkeiba",  # netkeiba公式RSS ✅
    "https://jra.jp/rss/jra-info.rdf",                           # JRA公式 ✅
    # Google News（googlenewsdecoderで記事URL解決）
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

# タイトルだけで除外できるLIVE配信・スケジュール系パターン
# 例: "岩手競馬LIVE - スポーツナビ", "地方競馬ライブ - YouTube"
_LIVE_STREAM_PATTERN = re.compile(
    r"競馬\s*(?:LIVE|ライブ|生中継|速報LIVE)",
    re.IGNORECASE,
)


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


def is_reporter_prediction(entry: dict) -> bool:
    """記者・トラックマン(TM)の予想・的中報告記事かどうかを判定する（除外対象）。"""
    text = entry.get("title", "") + " " + entry.get("summary", "")
    # 記者/TM + 予想/的中/馬券系 のパターン
    if re.search(
        r"(?:記者|TM|トラックマン)(?:の|が|は)(?:予想|的中|馬連|馬単|三連|万馬券|馬券)",
        text,
        re.IGNORECASE,
    ):
        return True
    if re.search(r"記者予想|記者の予想|記者陣の予想", text):
        return True
    return False


# ---------------------------------------------------------------------------
# Google News URLデコード
# ---------------------------------------------------------------------------

def decode_google_news_url(url: str) -> str:
    """googlenewsdecoderライブラリでGoogle News URLを実際の記事URLに変換する。
    失敗した場合はbase64urlデコードをフォールバックとして試みる。
    """
    try:
        from googlenewsdecoder import gnewsdecoder
        result = gnewsdecoder(url, interval=1)
        if result.get("status"):
            decoded = result["decoded_url"]
            print(f"  [gnewsdecoder] 成功: {decoded[:80]}")
            return decoded
        else:
            print(f"  [gnewsdecoder] 失敗: {result.get('message', '不明なエラー')}")
    except Exception as e:
        print(f"  [gnewsdecoder] 例外: {e}")

    # フォールバック: base64urlデコード
    import base64
    m = re.search(r"/articles/([^?#]+)", url)
    if not m:
        return url
    encoded = m.group(1)
    try:
        rem = len(encoded) % 4
        encoded_padded = encoded + ("=" * (4 - rem)) if rem else encoded
        decoded_bytes = base64.urlsafe_b64decode(encoded_padded)
        url_match = re.search(rb"https?://[\x21-\x7e]+", decoded_bytes)
        if url_match:
            actual = url_match.group(0).decode("ascii", errors="ignore").rstrip(".,;)")
            if not re.search(r"google\.com|googleapis\.com", actual):
                print(f"  [base64] フォールバック成功: {actual[:80]}")
                return actual
    except Exception:
        pass
    return url


def extract_real_url_from_google_news_html(html: str) -> str:
    """Google NewsのHTMLから実際の記事URLを抽出する。"""
    # data-n-au 属性
    m = re.search(r'data-n-au=["\']([^"\']+)["\']', html)
    if m:
        url = m.group(1).strip()
        if url.startswith("http") and not re.search(r"google\.com|googleapis\.com", url):
            return url

    # CBMiトークン直後の外部URL
    m = re.search(
        r'\["CBM[^"]+",\s*"(https?://(?!(?:[^/"]*\.)?google(?:apis)?\.com)[^"]{15,})"',
        html,
    )
    if m:
        return m.group(1).strip()

    # JSON内の url フィールド
    for m in re.finditer(
        r'"(?:url|sourceUrl|articleUrl|link)"\s*:\s*"(https?://(?!(?:[^/"]*\.)?google(?:apis)?\.com)[^"]{15,})"',
        html,
    ):
        url = m.group(1).strip()
        if not re.search(r"(?:static|cdn|image|img|logo|font|\.css|\.js|api\.)", url, re.I):
            return url

    return ""


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


def http_get_redirect_url(url: str, timeout: int = 15) -> str:
    """Google News URLのリダイレクトチェーンを手動追尾して実際の記事URLを返す。
    最大5ホップ追尾し、非Googleドメインに到達したらそのURLを返す。"""
    current_url = url
    for hop in range(5):
        try:
            parsed = urlparse(current_url)
            host = parsed.netloc
            path = parsed.path
            if parsed.query:
                path += "?" + parsed.query

            conn = http.client.HTTPSConnection(host, timeout=timeout)
            conn.request("GET", path, headers={
                "User-Agent": HEADERS["User-Agent"],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
            })
            resp = conn.getresponse()
            status = resp.status
            location = resp.getheader("Location", "")
            conn.close()

            print(f"  [リダイレクト hop{hop+1}] HTTP {status} → {location[:200] if location else '(なし)'}")

            if status in (301, 302, 303, 307, 308) and location:
                # 相対URLを絶対URLに変換
                if location.startswith("/"):
                    location = f"https://{host}{location}"
                elif not location.startswith("http"):
                    break

                # 非Googleドメインに到達したら成功
                if not re.search(r"(?:[^/]*\.)?google(?:apis|usercontent)?\.com", location):
                    print(f"  [リダイレクト解決] 記事URL発見: {location[:200]}")
                    return location

                # Google NewsへのリダイレクトでもCBMiトークンが長くなった場合はbase64デコード試行
                if "news.google.com" in location and "/articles/" in location:
                    decoded_url = decode_google_news_url(location)
                    if decoded_url != location:
                        print(f"  [リダイレクト+base64解決] {decoded_url[:200]}")
                        return decoded_url

                # Google News URL内のリダイレクト → 続けて追尾
                current_url = location
            else:
                # リダイレクトなし or Googleのまま終了
                break
        except Exception as e:
            print(f"  [リダイレクト確認失敗 hop{hop+1}] {e}")
            break

    return ""


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

    # Google News RSS の <description> は HTML形式で実際の記事URLを含む:
    # <a href="https://actual-article.com/...">タイトル</a><font>ソース名</font>
    # この href を source_url として保存する
    source_url = ""
    if summary:
        href_m = re.search(r'href=["\']?(https?://[^"\'<>\s]+)', summary)
        if href_m:
            candidate = href_m.group(1)
            if not re.search(r"(?:[^/]*\.)?google(?:apis|usercontent)?\.com", candidate):
                source_url = candidate

    # 公開日時（RSS 2.0: pubDate / RSS 1.0 RDF: dc:date）
    pub_date_raw = _get_text(item, "pubdate", "date")
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
        "source_url": source_url,  # RSSのdescriptionから抽出した実際の記事URL
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

    # 重複除去（ID → タイトル前50文字）
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[dict] = []
    for e in all_entries:
        title_key = e.get("title", "")[:50]
        if e["id"] not in seen_ids and title_key not in seen_titles:
            seen_ids.add(e["id"])
            seen_titles.add(title_key)
            unique.append(e)

    # 投稿済みを除外
    unposted = [e for e in unique if e["id"] not in posted_ids]
    print(f"未投稿エントリー: {len(unposted)} 件")

    # 競馬関連フィルタ（除外キーワードを含む記事を除去）
    unposted = [e for e in unposted if is_keiba_related(e)]
    print(f"競馬関連フィルタ後: {len(unposted)} 件")

    # 記者・TM の予想/的中報告記事を除外
    before = len(unposted)
    unposted = [e for e in unposted if not is_reporter_prediction(e)]
    if len(unposted) < before:
        print(f"記者予想フィルタで {before - len(unposted)} 件を除外 → {len(unposted)} 件")

    # オッズ・出馬表のみのページを除外（記事内容がない）
    before = len(unposted)
    odds_pattern = re.compile(r"オッズ|出馬表|払戻金|レース結果一覧|競馬場.*開催日程", re.IGNORECASE)
    unposted = [e for e in unposted if not odds_pattern.search(e.get("title", ""))]
    if len(unposted) < before:
        print(f"オッズ/出馬表フィルタで {before - len(unposted)} 件を除外 → {len(unposted)} 件")

    # LIVE配信・中継告知のみページを除外（タイトル or URL）
    _livestream_url_pat = re.compile(r"/livestream/", re.IGNORECASE)
    before = len(unposted)
    unposted = [
        e for e in unposted
        if not (_LIVE_STREAM_PATTERN.search(e.get("title", ""))
                or _livestream_url_pat.search(e.get("link", ""))
                or _livestream_url_pat.search(e.get("source_url", "")))
    ]
    if len(unposted) < before:
        print(f"LIVE配信フィルタで {before - len(unposted)} 件を除外 → {len(unposted)} 件")

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
        rss_summary_len = len(summary)
        source_url = entry.get("source_url", "")  # RSSのdescriptionから抽出した実際のURL
        print(f"  記事URL(RSS): {link[:80]}")
        if source_url:
            print(f"  記事URL(RSS description): {source_url[:80]}")

        # URL解決の優先順位:
        # 1. RSSのdescriptionから抽出した実際の記事URL（最も確実）
        # 2. CBMiトークンのbase64デコード
        # 3. HTTPリダイレクト先URL（手動追尾でLocationヘッダーを確認）
        # 4. Google NewsページのHTMLから抽出（フォールバック）
        if source_url:
            fetch_url = source_url
            print(f"  [URL解決] RSS description: {fetch_url[:80]}")
        elif "news.google.com" in link:
            fetch_url = decode_google_news_url(link)
            if fetch_url == link:
                # base64デコード失敗→リダイレクト先を手動確認
                redirect_url = http_get_redirect_url(link)
                if redirect_url:
                    fetch_url = redirect_url
        else:
            fetch_url = link
        # 上記すべて失敗した場合はGoogle NewsのURLのまま
        is_unresolved_google = (fetch_url == link and "news.google.com" in fetch_url)
        print(f"  記事URL(fetch): {fetch_url[:80]}")

        raw_html = http_get(fetch_url)
        if raw_html:
            html = raw_html.decode("utf-8", errors="replace")

            # Google NewsページだったらHTMLから実際の記事URLを抽出して再fetch
            if is_unresolved_google:
                actual_url = extract_real_url_from_google_news_html(html)
                if actual_url:
                    print(f"  [再fetch] 実際の記事URLを取得: {actual_url[:80]}")
                    raw2 = http_get(actual_url)
                    if raw2:
                        html = raw2.decode("utf-8", errors="replace")
                        fetch_url = actual_url
                        is_unresolved_google = False
                else:
                    print(f"  [警告] Google NewsページHTMLから記事URLを抽出できませんでした")

            if not image_url:
                og_img = extract_og_image(fetch_url, html)
                if og_img and not re.search(r"google\.com|googleusercontent\.com|gstatic\.com", og_img, re.I):
                    image_url = og_img
            # <article> タグ → <p> タグ → 全体テキスト の順に本文を抽出
            body = ""
            method = "none"
            # <article> タグ
            m = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL | re.IGNORECASE)
            if m:
                body = re.sub(r"<[^>]+>", " ", m.group(1))
                method = "article"
            # よくある本文ラッパークラス
            if len(body.strip()) < 50:
                for cls in ("article-body", "entry-content", "post-body", "main-content",
                            "article-text", "news-body", "body-text", "article__body"):
                    pat = rf'<[^>]+class="[^"]*{re.escape(cls)}[^"]*"[^>]*>(.*?)</(?:div|section|article)>'
                    cm = re.search(pat, html, re.DOTALL | re.IGNORECASE)
                    if cm:
                        body = re.sub(r"<[^>]+>", " ", cm.group(1))
                        method = f"class:{cls}"
                        break
            # netkeiba/JRA専用クラス（騎手コメント・レース結果ページ向け）
            if len(body.strip()) < 50:
                for cls in ("news_text", "newsText", "article_body", "detail_text",
                            "race_comment", "comment_list", "result_detail"):
                    pat = rf'<[^>]+(?:class|id)="[^"]*{re.escape(cls)}[^"]*"[^>]*>(.*?)</(?:div|section|table|article)>'
                    cm = re.search(pat, html, re.DOTALL | re.IGNORECASE)
                    if cm:
                        body = re.sub(r"<[^>]+>", " ", cm.group(1))
                        method = f"class:{cls}"
                        break
            # <p> タグ結合
            if len(body.strip()) < 50:
                paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL | re.IGNORECASE)
                # 短すぎる断片（ナビ等）を除外してから結合
                paras_text = [re.sub(r"<[^>]+>", "", p).strip() for p in paras]
                body = " ".join(p for p in paras_text if len(p) > 20)
                method = "p-tags"
            # <td>/<li> テキスト結合（騎手コメント表形式対応）
            if len(body.strip()) < 100:
                cells = re.findall(r"<(?:td|li)[^>]*>(.*?)</(?:td|li)>", html, re.DOTALL | re.IGNORECASE)
                cells_text = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                td_body = " ".join(c for c in cells_text if len(c) > 10)
                if len(td_body) > len(body.strip()):
                    body = td_body
                    method = "td-li-tags"
            # フォールバック: script/style/nav/header/footer除去後にbodyだけ抽出
            if len(body.strip()) < 50:
                clean = re.sub(
                    r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>",
                    " ", html, flags=re.DOTALL | re.IGNORECASE,
                )
                body_m = re.search(r"<body[^>]*>(.*?)</body>", clean, re.DOTALL | re.IGNORECASE)
                body = re.sub(r"<[^>]+>", " ", body_m.group(1) if body_m else clean)
                method = "cleaned-html"
            # JSテンプレート変数（{{ foo }} / {% bar %}）を除去
            body = re.sub(r"\{\{[^}]*\}\}", "", body)
            body = re.sub(r"\{%[^%]*%\}", "", body)
            full_body = re.sub(r"\s+", " ", body).strip()[:3000]
            if len(full_body) > len(summary):
                summary = full_body
            print(f"  本文取得: {method} / {len(full_body)}文字 (RSS概要: {rss_summary_len}文字)")
            if len(full_body) < 200:
                print(f"  [警告] 本文が短い（{len(full_body)}文字）- JS描画・会員限定・リダイレクト失敗の可能性")
        else:
            print(f"  [警告] 記事HTML取得失敗 - RSS概要のみ使用 ({rss_summary_len}文字)")

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
