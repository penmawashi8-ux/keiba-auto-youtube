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
    print("テスト1: netkeiba アクセスランキング（AJAXエンドポイント特定）")
    print("=" * 70)
    # 第1回テストの結果: トップページに id="NewAccessRankList" の空divがあり
    # JavaScript で中身が読み込まれる。読み込み元エンドポイントを特定する。
    url = "https://news.netkeiba.com/"
    print(f"\n--- URL: {url}")
    raw = http_get(url)
    if not raw:
        print("  [結果] 取得失敗")
        return
    html = raw.decode("utf-8", errors="replace")

    # 1. AccessRankList / RankList に言及する箇所の前後を表示（AJAX URL 特定用）
    for kw in ["NewAccessRankList", "AccessRank", "news_rank", "ranking"]:
        for m in re.finditer(re.escape(kw), html):
            s, e = max(0, m.start() - 300), min(len(html), m.end() + 300)
            ctx = re.sub(r"\s+", " ", html[s:e])
            print(f"\n  [context:{kw}] ...{ctx}...")
            break  # 各キーワード最初の1箇所だけ

    # 2. 外部スクリプト URL 一覧
    print("\n  [scriptタグ一覧]")
    for m in re.finditer(r'<script[^>]+src=["\']([^"\']+)["\']', html):
        print(f"    {m.group(1)[:120]}")

    # 3. inline JS 内の URL らしき文字列（api/ajax/rank を含むもの）
    print("\n  [JS内のURL候補 (api/ajax/rank/list)]")
    for m in re.finditer(r'["\']([^"\']*(?:api|ajax|rank|List)[^"\']*)["\']', html, re.IGNORECASE):
        v = m.group(1)
        if ("/" in v or "pid" in v) and len(v) < 150 and not v.endswith((".css", ".png", ".jpg", ".gif")):
            print(f"    {v[:140]}")

    # 4. よくある AJAX エンドポイント候補を直接叩いてみる
    print("\n  [AJAXエンドポイント候補の直接テスト]")
    candidates = [
        "https://news.netkeiba.com/?pid=news_access_ranking",
        "https://news.netkeiba.com/?pid=news_ranking_list",
        "https://news.netkeiba.com/?pid=api_access_rank",
        "https://news.netkeiba.com/api/?pid=news_rank",
        "https://news.sp.netkeiba.com/?pid=news_ranking",
    ]
    for c in candidates:
        raw2 = http_get(c)
        if raw2 and len(raw2) > 100:
            body = raw2.decode("utf-8", errors="replace")
            items = extract_ranking_items(body)
            print(f"    {c} → {len(raw2)}bytes / news_viewリンク {len(items)}件")
            for no, title in items[:10]:
                print(f"      no={no}  {title[:60]}")
        else:
            print(f"    {c} → 空/失敗")


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
        domain = urlparse(e.get("link", "")).netloc
        for c in clusters:
            if _similarity(norm, c["norm"]) >= THRESHOLD:
                c["titles"].append(e["title"])
                c["domains"].add(domain)
                break
        else:
            clusters.append({"norm": norm, "titles": [e["title"]], "domains": {domain}})

    # ドメイン数（=何社が報じたか）を主キーに順位付け
    clusters.sort(key=lambda c: (len(c["domains"]), len(c["titles"])), reverse=True)
    print(f"  クラスタ数: {len(clusters)}")
    print("\n  --- 掲載ドメイン数トップ20の話題 ---")
    for c in clusters[:20]:
        print(f"  [{len(c['domains'])}ドメイン / {len(c['titles'])}記事] {c['titles'][0][:60]}")
        print(f"      domains: {sorted(c['domains'])}")
        for t in c["titles"][1:3]:
            print(f"      ├ {t[:60]}")


def main() -> None:
    test_netkeiba_ranking()
    test_yahoo_ranking()
    test_cross_source_clustering()
    print("\n=== テスト完了 ===")


if __name__ == "__main__":
    main()
