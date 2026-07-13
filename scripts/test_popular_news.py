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
    print("テスト1: netkeiba アクセスランキング")
    print("=" * 70)
    for url in NETKEIBA_RANKING_URLS:
        print(f"\n--- URL: {url}")
        raw = http_get(url)
        if not raw:
            print("  [結果] 取得失敗")
            continue
        html = raw.decode("euc-jp", errors="replace")
        # netkeiba は EUC-JP の場合と UTF-8 の場合があるため charset を確認
        m = re.search(rb'charset=["\']?\s*([a-zA-Z0-9_-]+)', raw[:2000], re.IGNORECASE)
        if m:
            enc = m.group(1).decode("ascii", errors="replace").lower()
            print(f"  [charset] {enc}")
            try:
                html = raw.decode(enc, errors="replace")
            except LookupError:
                pass
        # ランキングらしいキーワードの有無
        for kw in ["ランキング", "アクセス", "人気", "Ranking", "ranking"]:
            cnt = html.count(kw)
            if cnt:
                print(f"  [キーワード] {kw!r} x{cnt}")
        # ランキングセクションの周辺構造を表示（class/id に rank を含む要素）
        for sm in re.finditer(r'<[a-z]+[^>]+(?:class|id)=["\'][^"\']*[Rr]ank[^"\']*["\'][^>]*>', html):
            print(f"  [rank要素] {sm.group(0)[:120]}")
        items = extract_ranking_items(html)
        print(f"  [結果] news_view リンク {len(items)} 件抽出")
        for no, title in items[:15]:
            print(f"    no={no}  {title[:60]}")


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
        # 記事リンク抽出（/articles/xxxx 形式）
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in re.finditer(
            r'<a[^>]+href=["\'](https://news\.yahoo\.co\.jp/articles/[a-z0-9]+)["\'][^>]*>(.*?)</a>',
            html, re.DOTALL | re.IGNORECASE,
        ):
            link, inner = m.group(1), m.group(2)
            title = re.sub(r"<[^>]+>", " ", inner)
            title = re.sub(r"\s+", " ", _html_lib.unescape(title)).strip()
            if link in seen:
                continue
            seen.add(link)
            links.append((link, title))
        print(f"  [結果] 記事リンク {len(links)} 件抽出")
        for link, title in links[:10]:
            print(f"    {title[:50]}  {link}")


# ---------------------------------------------------------------------------
# テスト3: 複数ソース掲載数によるクラスタリング（代理指標）
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> str:
    """タイトルを比較用に正規化する。"""
    t = re.sub(r"[【】\[\]（）()「」『』・…｜|｟｠\s]", "", title)
    t = re.sub(r"[-‐－—―~〜].*$", "", t)  # 末尾の媒体名（" - スポニチ" 等）を除去
    return t.lower()


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

    # 貪欲クラスタリング: 類似タイトルをまとめる
    clusters: list[dict] = []  # {"norm": str, "titles": [], "domains": set()}
    THRESHOLD = 0.5
    for e in unique:
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

    clusters.sort(key=lambda c: (len(c["titles"]), len(c["domains"])), reverse=True)
    print(f"  クラスタ数: {len(clusters)}")
    print("\n  --- 掲載数トップ15の話題 ---")
    for c in clusters[:15]:
        print(f"  [{len(c['titles'])}記事 / {len(c['domains'])}ドメイン] {c['titles'][0][:60]}")
        for t in c["titles"][1:4]:
            print(f"      ├ {t[:60]}")


def main() -> None:
    test_netkeiba_ranking()
    test_yahoo_ranking()
    test_cross_source_clustering()
    print("\n=== テスト完了 ===")


if __name__ == "__main__":
    main()
