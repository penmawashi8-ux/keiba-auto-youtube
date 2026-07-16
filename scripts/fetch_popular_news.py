#!/usr/bin/env python3
"""netkeibaのアクセスランキング（閲覧数の多いニュース）を取得してnews.jsonに保存する。

通常のニュースパイプライン（fetch_news.py）とは別系統で、
「今日いちばん読まれているニュース」を動画化するために使う。

仕組み（2026年7月の調査で確認済み・scripts/test_popular_news.py 参照）:
  netkeibaニュースのサイドバー「アクセスランキング」は
  showNewsRanking() が以下の内部APIをJSONPで呼んで描画している。
    GET https://news.netkeiba.com/?pid=api_get_news_rank
        &rank_type=2 (2=アクセスランキング, 3=注目度ランキング)
        &category_id=3&limit=N&page=1&input=UTF-8&output=jsonp&callback=cb
  レスポンスはランキングHTML断片のJSONP。記事IDは既存RSSと同じ
  news_view&no=XXXXXX 形式なので posted_ids.txt での重複管理を共有できる。

出力する news.json は fetch_news.py と同一フォーマット
（id/title/url/summary/image_url/published_date）。
後続の generate_script.py 以降はそのまま流用する。
"""

import html as _html_lib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_news import (  # noqa: E402
    extract_og_image,
    fix_distance_typo,
    http_get,
    http_get_article,
    _parse_date,
)

NEWS_JSON = "news.json"
POSTED_IDS_FILE = "posted_ids.txt"
MAX_POPULAR_NEWS = 3          # 1回の実行で動画化する最大記事数
RANKING_FETCH_LIMIT = 30      # ランキングAPIから取得する件数（投稿済みスキップの余裕分）
MAX_AGE_HOURS = 72            # 公開日時がこれより古い記事はスキップ（日付不明は通す）

RANKING_API_URL = (
    "https://news.netkeiba.com/?pid=api_get_news_rank"
    "&rank_type=2&category_id=3&subcategory_id="
    f"&limit={RANKING_FETCH_LIMIT}&page=1"
    "&input=UTF-8&output=jsonp&callback=cb"
)

ARTICLE_URL_TMPL = "https://news.netkeiba.com/?pid=news_view&no={no}"

# netkeibaサイトのフッター・UIノイズ（fetch_news.py と同じマーカー）
_FOOTER_MARKERS = [
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


# ---------------------------------------------------------------------------
# ランキングAPI
# ---------------------------------------------------------------------------

def _parse_jsonp(text: str) -> str:
    """JSONP レスポンス cb("...html...") から HTML 文字列を取り出す。"""
    m = re.search(r"\((.*)\)\s*;?\s*$", text, re.DOTALL)
    payload = m.group(1) if m else text
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        # JSONとして読めない場合はエスケープを素朴に戻す
        return payload.replace("\\/", "/").replace('\\"', '"')
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        # HTMLらしき最長の文字列値を採用
        vals = [v for v in data.values() if isinstance(v, str)]
        return max(vals, key=len) if vals else ""
    return ""


def fetch_ranking() -> list[dict]:
    """アクセスランキングを取得して [{no, title, views}] を順位順に返す。"""
    raw = http_get(RANKING_API_URL)
    if not raw:
        return []
    html = _parse_jsonp(raw.decode("utf-8", errors="replace"))

    items: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a[^>]+href=["\'][^"\']*news_view[^"\']*?no=(\d+)[^"\']*["\'][^>]*>(.*?)</a>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        no, inner = m.group(1), m.group(2)
        text = re.sub(r"<[^>]+>", "\n", inner)
        text = _html_lib.unescape(text)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines:
            continue
        # 閲覧数: "70,900" の直後の行が "view"
        views = 0
        for i, line in enumerate(lines):
            if line.lower() == "view" and i > 0:
                v = lines[i - 1].replace(",", "")
                if v.isdigit():
                    views = int(v)
                break
        # タイトル: 最長の行（順位・閲覧数は短い数字行）
        title = max(lines, key=len)
        if no in seen or not title or title.replace(",", "").isdigit():
            continue
        seen.add(no)
        items.append({"no": no, "title": title, "views": views})
    return items


# ---------------------------------------------------------------------------
# 記事本文
# ---------------------------------------------------------------------------

def fetch_article(no: str, ranking_title: str) -> dict | None:
    """netkeiba記事ページから本文・画像・公開日時を取得してnews.jsonアイテムを作る。"""
    url = ARTICLE_URL_TMPL.format(no=no)
    raw, fetched_url = http_get_article(url)
    if not raw:
        return None

    # エンコーディング検出（fetch_news.py と同じ方式）
    enc = "utf-8"
    m = re.search(rb'<meta[^>]+charset=["\']?\s*([a-zA-Z0-9_-]+)', raw[:2000], re.IGNORECASE)
    if m:
        enc = m.group(1).decode("ascii", errors="replace").strip()
    try:
        html = raw.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        html = raw.decode("utf-8", errors="replace")

    # 公開日時: article:published_time → JSON-LD datePublished
    published = ""
    pm = re.search(
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if pm:
        published = pm.group(1)

    # 本文: JSON-LD articleBody を優先
    body = ""
    for jld_m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            jld = json.loads(jld_m.group(1))
            if isinstance(jld, list):
                jld = next((x for x in jld if isinstance(x, dict)), {})
            if not published:
                dp = jld.get("datePublished", "")
                if isinstance(dp, str):
                    published = dp
            ab = jld.get("articleBody") or ""
            if isinstance(ab, str) and len(ab) >= 100:
                body = ab
                break
        except (json.JSONDecodeError, ValueError):
            continue

    # script/style除去（以降のフォールバック抽出用）
    html_clean = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html_clean = re.sub(r"<style[^>]*>.*?</style>", " ", html_clean, flags=re.DOTALL | re.IGNORECASE)

    # 画像: og:image
    image_url = extract_og_image(url, html_clean)
    if image_url and re.search(r"google\.com|googleusercontent\.com|gstatic\.com", image_url, re.I):
        image_url = ""

    # 本文フォールバック: <p>タグ結合
    if len(body.strip()) < 100:
        paras = re.findall(r"<p[^>]*>(.*?)</p>", html_clean, re.DOTALL | re.IGNORECASE)
        body = " ".join(re.sub(r"<[^>]+>", "", p) for p in paras)

    # og:description を先頭に補完
    m_desc = re.search(
        r'<meta[^>]+(?:name=["\']description["\']|property=["\']og:description["\'])[^>]+content=["\']([^"\']{20,})["\']',
        html_clean, re.IGNORECASE,
    )
    og_desc = m_desc.group(1).strip() if m_desc else ""

    body = _html_lib.unescape(re.sub(r"\s+", " ", body)).strip()
    # フッターノイズ除去
    footer_idx = len(body)
    for fm in _FOOTER_MARKERS:
        fi = body.find(fm)
        if 0 < fi < footer_idx:
            footer_idx = fi
    if footer_idx < len(body):
        body = body[:footer_idx].strip()
    if og_desc and og_desc not in body:
        body = (og_desc + " " + body).strip()
    body = body[:2000]

    if len(body) < 100:
        print(f"  [スキップ] 本文抽出不足 ({len(body)}文字): no={no}", file=sys.stderr)
        return None

    # ページ側の正式タイトル（og:title）があれば優先
    title = ranking_title
    tm = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html_clean, re.IGNORECASE,
    )
    if tm:
        t = _html_lib.unescape(tm.group(1).strip())
        t = re.sub(r"\s*[|｜]\s*(競馬ニュース.*|netkeiba.*)$", "", t).strip()
        if len(t) >= 10:
            title = t

    return {
        "id": url,
        "title": title,
        "url": url,
        "summary": body,
        "image_url": image_url,
        "published_date": published,
    }


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def load_posted_article_nos() -> set[str]:
    """posted_ids.txt から netkeiba 記事番号（no=XXXXXX）を集める。
    通常ニュースパイプラインと重複投稿しないための共有チェック。"""
    path = Path(POSTED_IDS_FILE)
    if not path.exists():
        return set()
    nos: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        for m in re.finditer(r"news_view[^0-9]*no=(\d+)", line):
            nos.add(m.group(1))
    return nos


def main() -> None:
    print("=== 人気ニュース（netkeibaアクセスランキング）取得開始 ===")
    posted_nos = load_posted_article_nos()
    print(f"投稿済みnetkeiba記事番号: {len(posted_nos)}件")

    ranking = fetch_ranking()
    if not ranking:
        print("[エラー] ランキング取得失敗。news.jsonを空にして終了。", file=sys.stderr)
        Path(NEWS_JSON).write_text("[]", encoding="utf-8")
        sys.exit(0)

    print(f"ランキング取得: {len(ranking)}件")
    for i, r in enumerate(ranking[:10], 1):
        print(f"  {i:2d}. [{r['views']:,}view] no={r['no']} {r['title'][:50]}")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)

    news_items: list[dict] = []
    for r in ranking:
        if len(news_items) >= MAX_POPULAR_NEWS:
            break
        if r["no"] in posted_nos:
            print(f"  [スキップ] 投稿済み: no={r['no']} {r['title'][:40]}")
            continue
        item = fetch_article(r["no"], r["title"])
        if not item:
            continue
        # 古すぎる記事（コラム等がランキング入りするケース）を除外
        pub_dt = _parse_date(item["published_date"])
        if pub_dt is not None:
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt < cutoff:
                print(f"  [スキップ] {MAX_AGE_HOURS}時間より古い: no={r['no']} [{item['published_date'][:19]}]")
                continue
            item["published_date"] = pub_dt.isoformat()
        item["view_count"] = r["views"]
        print(f"  取得: [{r['views']:,}view] {item['title'][:50]}")
        news_items.append(item)

    if not news_items:
        print("投稿対象なし（全て投稿済み/スキップ）。")
        Path(NEWS_JSON).write_text("[]", encoding="utf-8")
        sys.exit(0)

    for item in news_items:
        item["title"] = fix_distance_typo(item["title"])
        item["summary"] = fix_distance_typo(item["summary"])

    Path(NEWS_JSON).write_text(
        json.dumps(news_items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n{len(news_items)} 件の人気ニュースを {NEWS_JSON} に保存しました。")
    for item in news_items:
        has_img = "あり" if item["image_url"] else "なし"
        print(f"  - [{item.get('view_count', 0):,}view] {item['title'][:50]} [画像: {has_img}]")


if __name__ == "__main__":
    main()
