#!/usr/bin/env python3
"""閲覧数の多い競馬ニュースを取得して news.json に保存する（既存ニュース投稿とは別系統）。

方法1（本命）: netkeiba の非公開ランキングAPI api_get_news_rank を呼ぶ。
  - _contents_action_api_url をトップページから動的に発見（現状 https://news.netkeiba.com/）
  - rank_type=2 = アクセスランキング（実閲覧数付き）
  - 記事IDは既存の news_view&no=XXXXXX 形式なので posted_ids.txt とそのまま突合できる

方法2（フォールバック）: 既存RSSフィード群の記事タイトルを類似度クラスタリングし、
  「複数媒体が報じている話題」を人気の代理指標として選ぶ。

出力の news.json は既存パイプライン（generate_script.py 以降）と同一契約:
  [{"id", "title", "url", "summary", "image_url", "published_date"}, ...]
"""

import html as _html_lib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from fetch_news import (  # noqa: E402
    RSS_FEEDS,
    extract_og_image,
    fix_distance_typo,
    http_get,
    http_get_article,
    is_keiba_related,
    load_posted_ids,
    parse_feed,
)

NEWS_JSON = "news.json"
JST = timezone(timedelta(hours=9))

# 1回の実行で投稿する記事数（既存ニュースの3件より控えめに）
MAX_NEWS = int(os.environ.get("MAX_POPULAR_NEWS", "1"))
# 人気でも古すぎる記事は除外する（公開日時が判明した場合のみ適用）
MAX_AGE_HOURS = int(os.environ.get("POPULAR_MAX_AGE_HOURS", "72"))
# ランキングから取得する件数（フィルタで減る分を見込んで多めに）
RANKING_LIMIT = 30

NETKEIBA_TOP = "https://news.netkeiba.com/"
DEFAULT_API_URL = "https://news.netkeiba.com/"


# ---------------------------------------------------------------------------
# 方法1: netkeiba アクセスランキングAPI
# ---------------------------------------------------------------------------

def discover_api_url() -> str:
    """トップページのJS変数 _contents_action_api_url からAPIベースURLを取得する。"""
    raw = http_get(NETKEIBA_TOP)
    if raw:
        text = raw.decode("utf-8", errors="replace")
        m = re.search(r'_contents_action_api_url\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            print(f"[Ranking] _contents_action_api_url = {m.group(1)!r}")
            return m.group(1)
    print(f"[Ranking] API URL自動発見失敗。既定値 {DEFAULT_API_URL} を使用", file=sys.stderr)
    return DEFAULT_API_URL


def _decode_jsonp(body: str) -> str:
    """JSONPレスポンスから文字列ペイロードを取り出し、HTML片として返す。"""
    m = re.search(r"^[^(]*\((.*)\)\s*;?\s*$", body, re.DOTALL)
    payload = m.group(1) if m else body
    try:
        obj = json.loads(payload)
        parts: list[str] = []

        def _walk(o) -> None:
            if isinstance(o, str):
                parts.append(o)
            elif isinstance(o, dict):
                for v in o.values():
                    _walk(v)
            elif isinstance(o, list):
                for v in o:
                    _walk(v)

        _walk(obj)
        return "\n".join(parts)
    except (json.JSONDecodeError, ValueError):
        # JSONとして読めない場合は手動でアンエスケープ
        s = payload.replace("\\/", "/").replace('\\"', '"')
        s = s.replace("\\n", "\n").replace("\\t", " ")
        return re.sub(r"\\u([0-9a-fA-F]{4})", lambda mm: chr(int(mm.group(1), 16)), s)


def parse_ranking_items(html: str) -> list[dict]:
    """ランキングHTML片から {"no", "title", "views"} を出現順（=順位順）に抽出する。"""
    items: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a[^>]+href=["\'][^"\']*news_view[^"\']*?no=(\d+)[^"\']*["\'][^>]*>(.*?)</a>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        no, inner = m.group(1), m.group(2)
        if no in seen:
            continue
        seen.add(no)
        text = re.sub(r"<[^>]+>", " ", inner)
        text = re.sub(r"\s+", " ", _html_lib.unescape(text)).strip()
        views = 0
        title = text
        vm = re.search(r"(\d[\d,]*)\s*view", text, re.IGNORECASE)
        if vm:
            # 例: "1 70,900 view サイバー攻撃で..." → 順位・閲覧数・タイトル
            views = int(vm.group(1).replace(",", ""))
            title = text[vm.end():].strip()
        else:
            # 例: "1 228 タイトル"（注目度ランキング等）
            m2 = re.match(r"^\s*\d+\s+(\d[\d,]*)\s+(.+)$", text)
            if m2:
                views = int(m2.group(1).replace(",", ""))
                title = m2.group(2).strip()
        items.append({"no": no, "title": title, "views": views})
    return items


def fetch_ranking(api_url: str, rank_type: int = 2) -> list[dict]:
    url = (
        f"{api_url}?pid=api_get_news_rank&rank_type={rank_type}"
        f"&category_id=3&subcategory_id=&limit={RANKING_LIMIT}&page=1"
        f"&input=UTF-8&output=jsonp&callback=cb"
    )
    raw = http_get(url)
    if not raw or len(raw) < 50:
        print(f"[Ranking] rank_type={rank_type} 取得失敗", file=sys.stderr)
        return []
    html = _decode_jsonp(raw.decode("utf-8", errors="replace"))
    items = parse_ranking_items(html)
    print(f"[Ranking] rank_type={rank_type}: {len(items)}件抽出")
    for i, it in enumerate(items[:10], 1):
        print(f"  {i:2d}. no={it['no']} views={it['views']:,} {it['title'][:50]}")
    return items


# ---------------------------------------------------------------------------
# 記事ページから本文・画像・公開日時を取得
# ---------------------------------------------------------------------------

_FOOTER_MARKERS = [
    "の全成績と掲示板",  # 記事下の関連リンク（「〇〇の全成績と掲示板」）以降はサイドバー
    "No.1競馬情報サイト「netkeiba」",
    "No.1競馬サイト「netkeiba",
    "フィルタON",
    "利用者数1700万人突破",
    "netkeiba公式SNS",
    "netkeiba 姉妹サイト",
    "netkeiba姉妹サイト",
    "URLリンクをコピーしました",
    "AIに非推奨判定",
    "ニュースコメントを表示するには",
    "コメント非表示",
]


def _extract_published(html: str) -> datetime | None:
    """記事HTMLから公開日時を抽出する（JSON-LD → meta → 日本語表記の順）。"""
    for pat in [
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            try:
                dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=JST)
            except ValueError:
                continue
    m = re.search(r"(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?[^\d]{0,10}?(\d{1,2}):(\d{2})", html)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), tzinfo=JST,
            )
        except ValueError:
            pass
    return None


def _extract_body(html: str) -> str:
    """記事HTMLから本文テキストを抽出する（netkeiba向け・汎用フォールバック付き）。"""
    body = ""
    # 1. JSON-LD の articleBody
    for jld_m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            jld = json.loads(jld_m.group(1))
            if isinstance(jld, list):
                jld = next((x for x in jld if isinstance(x, dict)), {})
            ab = jld.get("articleBody") or ""
            if isinstance(ab, str) and len(ab) >= 100:
                body = ab
                break
        except (json.JSONDecodeError, ValueError):
            continue

    stripped = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    stripped = re.sub(r"<style[^>]*>.*?</style>", " ", stripped, flags=re.DOTALL | re.IGNORECASE)

    # 2. <article> / class系div / <p>結合 の順に試す
    if len(body.strip()) < 100:
        m = re.search(r"<article[^>]*>(.*?)</article>", stripped, re.DOTALL | re.IGNORECASE)
        if m:
            body = re.sub(r"<[^>]+>", " ", m.group(1))
    if len(body.strip()) < 100:
        best = ""
        for div_m in re.finditer(
            r'<div[^>]+(?:class|id)=["\'][^"\']*(?:article|content|body|entry|text|paragraph|story)[^"\']*["\'][^>]*>(.*?)</div>',
            stripped, re.DOTALL | re.IGNORECASE,
        ):
            candidate = re.sub(r"<[^>]+>", " ", div_m.group(1)).strip()
            if len(candidate) > len(best):
                best = candidate
        if len(best) >= 100:
            body = best
    if len(body.strip()) < 100:
        paras = re.findall(r"<p[^>]*>(.*?)</p>", stripped, re.DOTALL | re.IGNORECASE)
        body = " ".join(re.sub(r"<[^>]+>", "", p) for p in paras)

    text = re.sub(r"\s+", " ", _html_lib.unescape(body)).strip()
    return _cut_footer(text)


def _cut_footer(text: str) -> str:
    """フッター・UIノイズ・関連リンク以降を除去する。"""
    cut = len(text)
    for marker in _FOOTER_MARKERS:
        i = text.find(marker)
        if 0 < i < cut:
            cut = i
    return text[:cut].strip()


def _clean_ranking_title(title: str) -> str:
    """ランキングHTML由来のタイトル末尾ゴミ（"22時間前 37 123" 等）を除去する。"""
    return re.sub(r"\s*\d+(?:分|時間|日)前(?:\s+[\d,]+)*\s*$", "", title).strip()


def _extract_og_meta(html: str, prop: str) -> str:
    for pat in [
        rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']',
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return _html_lib.unescape(m.group(1)).strip()
    return ""


def build_news_item(
    entry_id: str, url: str, title: str, views: int, rss_summary: str = ""
) -> dict | None:
    """記事ページを取得して news.json 契約のアイテムを構築する。

    公開日時が判明して MAX_AGE_HOURS より古い場合は None（除外）。
    """
    raw_html, fetched_url = http_get_article(url)
    summary = ""
    image_url = ""
    pub_str = ""
    title = _clean_ranking_title(title)
    if raw_html:
        # エンコーディングを meta charset から検出（EUC-JP等に対応）
        enc = "utf-8"
        m = re.search(rb'<meta[^>]+charset=["\']?\s*([a-zA-Z0-9_-]+)', raw_html[:2000], re.IGNORECASE)
        if m:
            enc = m.group(1).decode("ascii", errors="replace").strip()
        try:
            html = raw_html.decode(enc, errors="replace")
        except (LookupError, UnicodeDecodeError):
            html = raw_html.decode("utf-8", errors="replace")
        published = _extract_published(html)
        if published:
            age = datetime.now(timezone.utc) - published.astimezone(timezone.utc)
            pub_str = published.isoformat()
            if age > timedelta(hours=MAX_AGE_HOURS):
                print(f"  [除外] {age.days}日前の記事のためスキップ: {title[:50]}")
                return None
        # og:title があれば正としてタイトルを差し替える（ランキングHTML由来の
        # 切れ・ゴミ混入を避ける）。サイト名サフィックスは除去。
        og_title = _extract_og_meta(html, "title")
        og_title = re.sub(r"\s*[|｜-]\s*(?:競馬)?ニュース?\s*[-|｜]?\s*netkeiba.*$", "", og_title).strip()
        if len(og_title) >= 8:
            title = og_title
        image_url = extract_og_image(url, html) or ""
        body = _extract_body(html)
        # og:description は記事のリード文。本文抽出がサイドバー等のノイズを
        # 拾った場合の保険として先頭に付与する（既存 fetch_news.py と同じ方針）
        og_desc = _extract_og_meta(html, "description")
        if og_desc and og_desc not in body:
            body = (og_desc + " " + body).strip()
        if rss_summary and rss_summary not in body:
            body = (rss_summary + " " + body).strip()
        # og:description にもサイト定型文が含まれるため、結合後にもう一度カット
        summary = _cut_footer(body)[:2000]
    if len(title) < 4:
        print(f"  [除外] タイトル取得失敗: no={entry_id}", file=sys.stderr)
        return None
    if not summary:
        print(f"  [警告] 本文取得失敗。タイトルのみで続行: {title[:50]}", file=sys.stderr)
        summary = title
    print(f"  取得: {title[:60]} [views={views:,}] [{pub_str[:19]}]")
    return {
        "id": entry_id,
        "title": title,
        "url": url,
        "summary": summary,
        "image_url": image_url,
        "published_date": pub_str,
        "views": views,
    }


NETKEIBA_RSS = "https://rss.netkeiba.com/?pid=rss_netkeiba&site=netkeiba"


def _load_netkeiba_rss_summaries() -> dict[str, str]:
    """netkeiba RSS の記事ID→本文抜粋マップ。記事ページより長いリード文が取れる。"""
    summaries: dict[str, str] = {}
    raw = http_get(NETKEIBA_RSS)
    if not raw:
        return summaries
    for e in parse_feed(raw):
        text = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
        text = re.sub(r"\s+", " ", _html_lib.unescape(text)).strip()
        if text:
            summaries[e["id"]] = text
    print(f"[Ranking] netkeiba RSSサマリー {len(summaries)}件を突き合わせ用に取得")
    return summaries


def fetch_popular_via_ranking(posted_ids: set) -> list[dict]:
    api_url = discover_api_url()
    ranking = fetch_ranking(api_url, rank_type=2)
    if not ranking:
        return []

    rss_summaries = _load_netkeiba_rss_summaries()
    news_items: list[dict] = []
    for it in ranking:
        if len(news_items) >= MAX_NEWS:
            break
        url = f"https://news.netkeiba.com/?pid=news_view&no={it['no']}"
        if url in posted_ids or it["no"] in posted_ids:
            print(f"  [投稿済み] no={it['no']} {it['title'][:40]}")
            continue
        if _is_generic_title(it["title"]):
            print(f"  [除外] 定型タイトル: {it['title'][:50]}")
            continue
        if not is_keiba_related({"title": it["title"], "summary": ""}):
            continue
        item = build_news_item(
            url, url, it["title"], it["views"],
            rss_summary=rss_summaries.get(url, ""),
        )
        if item:
            news_items.append(item)
    return news_items


# ---------------------------------------------------------------------------
# 方法2（フォールバック）: 複数媒体掲載数クラスタリング
# ---------------------------------------------------------------------------

_GENERIC_TITLE_PATTERNS = [
    r"^レース結果",
    r"ダイジェスト/JRAレース結果",
    r"全レース中継",
    r"ジョッキーカメラ映像",
    r"出来事[＆&]制裁",
    r"競馬制裁",
    r"^\d+\.\d+\s",  # "7.12 中央競馬..." のような日付だけの定型
]


def _is_generic_title(title: str) -> bool:
    t = unicodedata.normalize("NFKC", title)
    return any(re.search(p, t) for p in _GENERIC_TITLE_PATTERNS)


def _normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title)
    t = re.sub(r"\s*[-‐－—―|｜].{0,40}$", "", t)  # 末尾の媒体名を除去
    t = re.sub(r"[【】\[\]（）()「」『』・…｟｠\s　]", "", t)
    return t.lower()


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


def _similarity(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def fetch_popular_via_clustering(posted_ids: set) -> list[dict]:
    print("[Cluster] フォールバック: 複数媒体掲載数クラスタリング")
    all_entries: list[dict] = []
    for feed_url in RSS_FEEDS:
        raw = http_get(feed_url)
        if not raw:
            continue
        all_entries.extend(parse_feed(raw))

    seen_ids: set[str] = set()
    unique: list[dict] = []
    for e in all_entries:
        if e["id"] not in seen_ids:
            seen_ids.add(e["id"])
            unique.append(e)

    candidates = [
        e for e in unique
        if e["id"] not in posted_ids
        and not _is_generic_title(e.get("title", ""))
        and is_keiba_related(e)
    ]
    print(f"[Cluster] 候補 {len(candidates)}件（全{len(unique)}件）")

    # 貪欲クラスタリング: 類似タイトルをまとめ、報じた媒体数を数える
    clusters: list[dict] = []
    for e in candidates:
        norm = _normalize_title(e.get("title", ""))
        if len(norm) < 4:
            continue
        domain = urlparse(e.get("link", "")).netloc
        src_m = re.search(
            r"[-–|｜]\s*([^-–|｜]{2,25})\s*$",
            unicodedata.normalize("NFKC", e.get("title", "")),
        )
        source_key = re.sub(r"\s", "", src_m.group(1) if src_m else domain).lower()[:4]
        for c in clusters:
            if _similarity(norm, c["norm"]) >= 0.5:
                c["entries"].append(e)
                c["sources"].add(source_key)
                break
        else:
            clusters.append({"norm": norm, "entries": [e], "sources": {source_key}})

    clusters.sort(key=lambda c: (len(c["sources"]), len(c["entries"])), reverse=True)

    news_items: list[dict] = []
    for c in clusters:
        if len(news_items) >= MAX_NEWS:
            break
        if len(c["sources"]) < 2:
            break  # 複数媒体に載っていない話題は「人気」とみなさない
        # Google News 経由でない直接URLの記事を代表に選ぶ（本文取得の成功率が高い）
        rep = next(
            (e for e in c["entries"] if "news.google.com" not in e.get("link", "")),
            None,
        )
        if rep is None:
            continue
        print(f"[Cluster] [{len(c['sources'])}媒体/{len(c['entries'])}記事] {rep['title'][:55]}")
        rss_summary = re.sub(r"<[^>]+>", " ", rep.get("summary", ""))
        rss_summary = re.sub(r"\s+", " ", _html_lib.unescape(rss_summary)).strip()
        item = build_news_item(rep["id"], rep["link"], rep["title"], 0, rss_summary=rss_summary)
        if item:
            news_items.append(item)
    return news_items


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== 人気競馬ニュース取得開始 ===")
    posted_ids = load_posted_ids()
    print(f"投稿済みID数: {len(posted_ids)}")

    news_items = fetch_popular_via_ranking(posted_ids)
    if not news_items:
        news_items = fetch_popular_via_clustering(posted_ids)

    if not news_items:
        print("対象ニュースなし。処理を終了します。")
        Path(NEWS_JSON).write_text("[]", encoding="utf-8")
        return

    for item in news_items:
        item["title"] = fix_distance_typo(item["title"])
        item["summary"] = fix_distance_typo(item["summary"])

    Path(NEWS_JSON).write_text(
        json.dumps(news_items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n{len(news_items)} 件を {NEWS_JSON} に保存しました。")
    for item in news_items:
        has_img = "あり" if item["image_url"] else "なし"
        print(f"  - {item['title'][:50]} [views={item.get('views', 0):,}] [画像: {has_img}]")


if __name__ == "__main__":
    main()
