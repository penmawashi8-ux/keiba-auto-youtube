#!/usr/bin/env python3
"""人気ニュース（閲覧数の多いニュース）取得の実現可能性テスト。

GitHub Actions 上で実行し、以下を検証する:
  テスト1: netkeiba アクセスランキングページの取得と記事ID抽出
  テスト2: Yahoo!ニュース アクセスランキングページの取得可否
  テスト3: 既存RSSフィードの記事を話題ごとにクラスタリングし、
           複数ソースで報じられている話題（=注目度の代理指標）を抽出

結果はすべて標準出力にログとして出す。本番コードには影響しない。
"""

import re
import sys
import html as _html_lib
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from fetch_news import HEADERS, RSS_FEEDS, http_get, parse_feed  # noqa: E402


# ---------------------------------------------------------------------------
# テスト1: netkeiba アクセスランキング
# ---------------------------------------------------------------------------

NETKEIBA_RANKING_URLS = [
    "https://news.netkeiba.com/?pid=news_ranking",
    "https://news.netkeiba.com/?pid=news_ranking&type=daily",
    "https://news.netkeiba.com/?pid=news_top",
    "https://news.netkeiba.com/",
]


def extract_ranking_items(html: str) -> list[tuple[str, str]]:
    """HTML から news_view 記事の (ID, タイトル) を出現順に抽出する。"""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    # <a href="...news_view...no=NNNN...">タイトル</a> （タグ入れ子対応で内側テキストを収集）
    for m in re.finditer(
        r'<a[^>]+href=["\'][^"\']*news_view[^"\']*?no=(\d+)[^"\']*["\'][^>]*>(.*?)</a>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        no, inner = m.group(1), m.group(2)
        title = re.sub(r"<[^>]+>", " ", inner)
        title = re.sub(r"\s+", " ", _html_lib.unescape(title)).strip()
        if no in seen:
            continue
        seen.add(no)
        items.append((no, title))
    return items


def test_netkeiba_ranking() -> None:
    print("=" * 70)
    print("テスト1: netkeiba showNewsRanking() のAJAX URL特定")
    print("=" * 70)
    # 第3回テストの結果: news_backnumber ページもガワのみ（JSで一覧を読み込む）。
    # showNewsRanking() の定義を外部JSから取得してAJAXエンドポイントを特定する。
    js_urls = [
        "https://snews.netkeiba.com/common/js/officialnews.action.js?2015081401",
        "https://cdnv2.netkeiba.com/img.newsapi/common/js/newsapi.action.js?2015081401",
        "https://cdnv2.netkeiba.com/img.news/common/js/contents.action.js?2017101702",
        "https://cdnv2.netkeiba.com/img.news/common/js/ajaxtabs.js?2015081401",
    ]
    func_body = ""
    for js_url in js_urls:
        raw = http_get(js_url)
        if not raw:
            continue
        js = raw.decode("utf-8", errors="replace")
        hits = [f for f in ["showNewsRanking", "NewsRanking"] if f in js]
        print(f"  {js_url.split('/')[-1].split('?')[0]}: {len(raw)}bytes / 言及: {hits}")
        m = re.search(r"function\s+showNewsRanking\s*\([^)]*\)\s*\{", js)
        if m:
            # 関数本体をブレース対応で抽出（最大3000文字）
            start = m.start()
            depth, i = 0, js.index("{", m.start())
            for i in range(js.index("{", m.start()), min(len(js), start + 3000)):
                if js[i] == "{":
                    depth += 1
                elif js[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
            func_body = js[start:i + 1]
            print(f"\n  [showNewsRanking 定義発見] {js_url.split('/')[-1]}")
            print("  " + "-" * 60)
            print(func_body[:2500])
            print("  " + "-" * 60)
            break
    if not func_body:
        print("  [結果] showNewsRanking の定義が見つからず")
        return

    # 関数本体から URL / pid 候補を抽出して実際に叩いてみる
    print("\n  [関数内のURL/pid候補と直接テスト]")
    candidates: list[str] = []
    for m in re.finditer(r'["\']([^"\']*(?:pid|http)[^"\']*)["\']', func_body):
        v = m.group(1)
        if v not in candidates:
            candidates.append(v)
            print(f"    候補文字列: {v[:120]}")
    for base in candidates:
        if not (base.startswith("http") or "pid" in base):
            continue
        url = base if base.startswith("http") else f"https://news.netkeiba.com/{base.lstrip('/')}"
        for extra in ["", "&rank_type=2", "&rank_type=2&limit=10", "?rank_type=2"]:
            test_url = url + extra
            raw2 = http_get(test_url)
            if raw2 and len(raw2) > 200:
                body = raw2.decode("utf-8", errors="replace")
                items = extract_ranking_items(body)
                print(f"    {test_url[:100]} → {len(raw2)}bytes / news_view {len(items)}件")
                for i, (no, title) in enumerate(items[:10], 1):
                    print(f"      {i:2d}. no={no}  {title[:55]}")
                if items:
                    return
            else:
                print(f"    {test_url[:100]} → 空/失敗")


# ---------------------------------------------------------------------------
# テスト2: Yahoo!ニュース アクセスランキング
# ---------------------------------------------------------------------------

YAHOO_RANKING_URLS = [
    "https://news.yahoo.co.jp/ranking/access/news/horse-racing",
    "https://news.yahoo.co.jp/ranking/access/news/sports",
    "https://news.yahoo.co.jp/ranking/access/news",
]


def test_yahoo_ranking() -> None:
    print("\n" + "=" * 70)
    print("テスト2: Yahoo!ニュース アクセスランキング")
    print("=" * 70)
    for url in YAHOO_RANKING_URLS:
        print(f"\n--- URL: {url}")
        raw = http_get(url)
        if not raw:
            print("  [結果] 取得失敗")
            continue
        html = raw.decode("utf-8", errors="replace")
        # /articles/ を含む href をすべて表示（相対パス含む）
        hrefs = re.findall(r'href=["\']([^"\']*articles[^"\']*)["\']', html)
        print(f"  [href調査] articles を含む href: {len(hrefs)} 件")
        for h in hrefs[:5]:
            print(f"    {h[:100]}")
        # __NEXT_DATA__ / preloadedState 内の記事URL+タイトルを抽出
        json_links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in re.finditer(
            r'"(?:url|link)"\s*:\s*"(https?:\\?/\\?/news\.yahoo\.co\.jp\\?/articles\\?/[a-z0-9]+)"[^{}]*?"title"\s*:\s*"([^"]{5,})"',
            html,
        ):
            link = m.group(1).replace("\\/", "/")
            title = m.group(2)
            if link not in seen:
                seen.add(link)
                json_links.append((link, title))
        # 順序逆（title が先）のパターンも
        for m in re.finditer(
            r'"title"\s*:\s*"([^"]{5,})"[^{}]*?"(?:url|link)"\s*:\s*"(https?:\\?/\\?/news\.yahoo\.co\.jp\\?/articles\\?/[a-z0-9]+)"',
            html,
        ):
            link = m.group(2).replace("\\/", "/")
            title = m.group(1)
            if link not in seen:
                seen.add(link)
                json_links.append((link, title))
        print(f"  [結果] JSON内記事リンク {len(json_links)} 件抽出")
        for link, title in json_links[:10]:
            print(f"    {title[:50]}  {link}")
        if not json_links and "__NEXT_DATA__" in html:
            print("  [情報] __NEXT_DATA__ は存在するがパターン不一致。構造サンプル:")
            i = html.find("articles")
            print("    " + re.sub(r"\s+", " ", html[max(0, i - 200):i + 300]))


# ---------------------------------------------------------------------------
# テスト3: 複数ソース掲載数によるクラスタリング（代理指標）
# ---------------------------------------------------------------------------

import unicodedata


def _normalize_title(title: str) -> str:
    """タイトルを比較用に正規化する。"""
    t = unicodedata.normalize("NFKC", title)  # 全角英数→半角の統一
    t = re.sub(r"\s*[-‐－—―|｜].{0,40}$", "", t)  # 末尾の媒体名（" - スポニチ" 等）を除去
    t = re.sub(r"[【】\[\]（）()「」『』・…｟｠\s　]", "", t)
    return t.lower()


# 定型・機械生成タイトル（人気の指標にならないもの）を除外
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


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


def _similarity(a: str, b: str) -> float:
    """文字2-gramのJaccard類似度。"""
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def test_cross_source_clustering() -> None:
    print("\n" + "=" * 70)
    print("テスト3: 複数ソース掲載数クラスタリング（人気の代理指標）")
    print("=" * 70)

    all_entries: list[dict] = []
    for feed_url in RSS_FEEDS:
        raw = http_get(feed_url)
        if not raw:
            continue
        entries = parse_feed(raw)
        print(f"  フィード {feed_url[:60]} : {len(entries)} 件")
        all_entries.extend(entries)

    # ID 重複除去
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for e in all_entries:
        if e["id"] not in seen_ids:
            seen_ids.add(e["id"])
            unique.append(e)
    print(f"\n  重複除去後: {len(unique)} 件")

    # 定型タイトルを除外
    filtered = [e for e in unique if not _is_generic_title(e.get("title", ""))]
    print(f"  定型タイトル除外後: {len(filtered)} 件")

    # 貪欲クラスタリング: 類似タイトルをまとめる
    clusters: list[dict] = []  # {"norm": str, "titles": [], "domains": set()}
    THRESHOLD = 0.5
    for e in filtered:
        norm = _normalize_title(e.get("title", ""))
        if len(norm) < 4:
            continue
        # ソース判定: Google News 経由はドメインが news.google.com になるため、
        # タイトル末尾の媒体名（" - スポニチ" 等）をソースとして使う
        domain = urlparse(e.get("link", "")).netloc
        src_m = re.search(r"[-–|｜]\s*([^-–|｜]{2,25})\s*$", unicodedata.normalize("NFKC", e.get("title", "")))
        source = src_m.group(1).strip() if src_m else domain
        # 同一媒体の表記ゆれ（"スポニチ競馬Web" / "スポニチ Sponichi Annex"）を先頭4文字で吸収
        source_key = re.sub(r"\s", "", source).lower()[:4]
        for c in clusters:
            if _similarity(norm, c["norm"]) >= THRESHOLD:
                c["titles"].append(e["title"])
                c["domains"].add(domain)
                c["sources"].add(source_key)
                break
        else:
            clusters.append({
                "norm": norm, "titles": [e["title"]],
                "domains": {domain}, "sources": {source_key},
            })

    # 媒体数（=何社が報じたか）を主キーに順位付け
    clusters.sort(key=lambda c: (len(c["sources"]), len(c["titles"])), reverse=True)
    print(f"  クラスタ数: {len(clusters)}")
    print("\n  --- 掲載媒体数トップ20の話題 ---")
    for c in clusters[:20]:
        print(f"  [{len(c['sources'])}媒体 / {len(c['titles'])}記事] {c['titles'][0][:60]}")
        print(f"      sources: {sorted(c['sources'])}")
        for t in c["titles"][1:3]:
            print(f"      ├ {t[:60]}")


def main() -> None:
    # Yahoo（JSレンダリングで取得不可と判明）とクラスタリング（検証済み）は
    # 今回スキップし、netkeiba AJAX URL特定に絞る
    test_netkeiba_ranking()
    print("\n=== テスト完了 ===")


if __name__ == "__main__":
    main()
