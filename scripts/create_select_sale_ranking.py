#!/usr/bin/env python3
"""セレクトセールの高額落札ランキング動画用コンテンツを生成する。

news.json と output/script_0.txt を書き出し、後続は既存パイプライン
（generate_audio.py → landscape_video.py → upload_landscape_youtube.py）が処理する。

データソース（優先順）:
1. JRHA公式 result_detail（セール終了後に掲載される。将来の実行用）
2. racing-book.net の結果速報記事（開催当日から掲載される）

環境変数:
- SALE_YEAR:    セール開催年（デフォルト: 現在のJST年）
- SALE_SESSION: "1歳" または "当歳"（デフォルト: 1歳）
- RBN_URL:      racing-book.net の結果速報記事URL（フォールバック用）
"""

import datetime
import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

JST = datetime.timezone(datetime.timedelta(hours=9))
NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
TOP_N = 10

UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}

JRHA_BASE = "https://www.jrha.or.jp"
DEFAULT_RBN_URL = "https://racing-book.net/other/news/12406"


def _get(url: str, attempts: int = 3) -> str:
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            r = requests.get(url, headers=UA_HEADERS, timeout=45)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            return r.text
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                wait = 10 * (i + 1)
                print(f"  [警告] {url} 取得失敗 ({e.__class__.__name__})。{wait}秒後にリトライ...",
                      file=sys.stderr)
                import time
                time.sleep(wait)
    raise last_err  # type: ignore[misc]


def parse_price_man(text: str) -> int | None:
    """「42,000万円」→ 42000（万円単位）。億表記にも対応する。"""
    text = text.strip().replace(",", "").replace("，", "")
    m = re.search(r"(\d+)億(\d+)?万?円?", text)
    if m:
        return int(m.group(1)) * 10000 + int(m.group(2) or 0)
    m = re.search(r"(\d+)万円?", text)
    if m:
        return int(m.group(1))
    return None


def format_price(man: int) -> str:
    """42000（万円）→「4億2000万円」"""
    oku, rem = divmod(man, 10000)
    if oku and rem:
        return f"{oku}億{rem}万円"
    if oku:
        return f"{oku}億円"
    return f"{rem}万円"


def clean_buyer(name: str) -> str:
    """購買者名の法人格・余分な空白を除去する（ナレーション読みやすさ対策）。"""
    name = re.sub(r"[（(]株[）)]|[（(]有[）)]|[（(]同[）)]|株式会社|有限会社|合同会社", "", name)
    return re.sub(r"[\s　]+", " ", name).strip()


# ---------------------------------------------------------------------------
# ソース1: JRHA公式 result_detail
# ---------------------------------------------------------------------------

def fetch_jrha(target_suffix: str, sale_year: int) -> list[dict]:
    """JRHA公式の価格順結果ページから落札リストを取得する。

    URL形式: /sp/selectsale/result_detail/{session}/{sale_id}/7 （7=価格順）
    session・sale_id は年により変わるため、対象世代の馬名サフィックス
    （例「の2025」）が多数含まれるページを探索して特定する。

    注意: 前年セールの当歳と当年セールの1歳は同じサフィックスになるため、
    結果トップページの「セール結果(YYYY)」見出しで掲載年を必ず確認する。
    """
    try:
        top_html = _get(f"{JRHA_BASE}/selectsale/result")
        m = re.search(r"セール結果[（(](\d{4})[）)]", top_html)
        published_year = int(m.group(1)) if m else None
    except Exception as e:
        print(f"  [警告] JRHA結果ページ取得失敗: {e}", file=sys.stderr)
        return []
    if published_year != sale_year:
        print(f"  JRHA公式の掲載は{published_year}年分（{sale_year}年分は未掲載）。スキップします。")
        return []

    for sale_id in range(9, 13):
        for session in (0, 1):
            url = f"{JRHA_BASE}/sp/selectsale/result_detail/{session}/{sale_id}/7"
            try:
                html = _get(url)
            except Exception:
                continue
            if html.count(target_suffix) < 20:
                continue

            gen_year = target_suffix.replace("の", "")
            soup = BeautifulSoup(html, "html.parser")
            lots: list[dict] = []
            for table in soup.find_all("table"):
                fields: dict[str, str] = {}
                for row in table.find_all("tr"):
                    cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
                    for i in range(0, len(cells) - 1, 2):
                        fields[cells[i]] = cells[i + 1]
                price = parse_price_man(fields.get("価格", ""))
                dam = fields.get("母", "")
                if not price or not dam:
                    continue
                lots.append({
                    "name": f"{dam}の{gen_year}",
                    "sire": fields.get("父", ""),
                    "sex": "",
                    "buyer": fields.get("購買者", ""),
                    "price_man": price,
                })
            if len(lots) >= TOP_N:
                print(f"  JRHA公式から {len(lots)} 頭取得: {url}")
                return lots
    return []


# ---------------------------------------------------------------------------
# ソース2: racing-book.net 結果速報
# ---------------------------------------------------------------------------

def fetch_rbn(url: str, target_suffix: str) -> list[dict]:
    """RBNの結果速報テーブル（No/上場馬名/父/性/購買者/落札価格）を取得する。"""
    try:
        html = _get(url)
    except Exception as e:
        print(f"  [警告] RBN取得失敗: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")
    lots: list[dict] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
        if "上場馬名" not in header or not any("価格" in h for h in header):
            continue
        col = {h: i for i, h in enumerate(header)}
        price_col = next(i for h, i in col.items() if "価格" in h)
        for row in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
            if len(cells) < len(header):
                continue
            name = cells[col["上場馬名"]]
            price = parse_price_man(cells[price_col])
            if not price or target_suffix not in name:
                continue
            lots.append({
                "name": name,
                "sire": cells[col.get("父", 2)],
                "sex": cells[col["性"]] if "性" in col else "",
                "buyer": cells[col["購買者"]] if "購買者" in col else "",
                "price_man": price,
            })
    if lots:
        print(f"  RBNから {len(lots)} 頭取得: {url}")
    return lots


def extract_summary_stats(text: str) -> dict:
    """ページ本文から落札総額・平均・落札率を抽出する（見つからなければ空）。"""
    stats: dict[str, str] = {}
    m = re.search(r"落札総額[^\d]{0,10}(\d+)億(\d+)?万?", text.replace(",", ""))
    if m:
        stats["total"] = f"{m.group(1)}億{m.group(2) or ''}万円" if m.group(2) else f"{m.group(1)}億円"
    m = re.search(r"平均落札価格[^\d]{0,10}(\d+)万", text.replace(",", ""))
    if m:
        stats["average"] = f"{int(m.group(1))}万円"
    m = re.search(r"落札率[^\d]{0,10}([\d.]+)[%％]", text)
    if m:
        stats["rate"] = f"{m.group(1)}パーセント"
    return stats


# ---------------------------------------------------------------------------
# コンテンツ生成
# ---------------------------------------------------------------------------

def sex_word(sex: str) -> str:
    if "牡" in sex:
        return "牡馬"
    if "牝" in sex or "めす" in sex:
        return "牝馬"
    return ""


def build_script(year: int, session: str, top: list[dict], stats: dict, date_str: str) -> str:
    blocks: list[str] = []

    intro = [f"【セレクトセール{year} {session}セール】"]
    intro.append(
        f"{date_str}、北海道ノーザンホースパークで国内最大の競走馬セリ市、"
        f"セレクトセール{year}の{session}セールが開催された。"
    )
    if stats.get("total"):
        line = f"落札総額は{stats['total']}"
        if stats.get("average"):
            line += f"、平均落札価格は{stats['average']}"
        line += "にのぼった。"
        intro.append(line)
    intro.append("それでは高額落札ランキング、トップ10を発表する。")
    blocks.append(intro[0] + "\n" + "".join(intro[1:]))

    for i, lot in enumerate(reversed(top)):
        rank = len(top) - i
        sentences = [f"第{rank}位、{lot['name']}。"]
        detail = f"父は{lot['sire']}"
        sw = sex_word(lot.get("sex", ""))
        if sw:
            detail += f"、{sw}だ"
        sentences.append(detail + "。")
        price_str = format_price(lot["price_man"])
        if rank == 1:
            sentences.append(f"落札価格はなんと{price_str}。")
            if lot.get("buyer"):
                sentences.append(f"{clean_buyer(lot['buyer'])}が本セール最高額で競り落とした。")
        else:
            line = f"落札価格は{price_str}"
            if lot.get("buyer"):
                line += f"、購買者は{clean_buyer(lot['buyer'])}"
            sentences.append(line + "。")
        blocks.append(f"【第{rank}位】\n" + "".join(sentences))

    outro = ["【まとめ】"]
    # 冒頭文は select_sale_ranking_video.py がまとめカードの表示開始時刻を
    # ASS字幕から特定するためのマーカーを兼ねる。「以上、」で始めること。
    outro.append("以上、高額落札ランキングトップ10だった。")
    sire_counts: dict[str, int] = {}
    for lot in top:
        if lot["sire"]:
            sire_counts[lot["sire"]] = sire_counts.get(lot["sire"], 0) + 1
    if sire_counts:
        top_sire, cnt = max(sire_counts.items(), key=lambda kv: kv[1])
        if cnt >= 2:
            outro.append(f"トップ10のうち{cnt}頭が{top_sire}産駒。種牡馬人気の高さが際立つ結果となった。")
    outro.append("この中から未来のG1馬は現れるのか。数年後が楽しみだ。")
    outro.append("気になる馬がいたらコメントで教えてくれ！チャンネル登録と高評価もよろしく！")
    blocks.append(outro[0] + "\n" + "".join(outro[1:]))

    return "\n\n".join(blocks)


def main() -> None:
    now = datetime.datetime.now(JST)
    # 深夜（〜6時）の実行はセール当日の深夜とみなし、前日をセール開催日とする
    sale_day = (now - datetime.timedelta(hours=6)).date()
    year = int(os.environ.get("SALE_YEAR", sale_day.year))
    session = os.environ.get("SALE_SESSION", "1歳")
    gen_year = year - 1 if session == "1歳" else year
    target_suffix = f"の{gen_year}"
    date_str = os.environ.get("SALE_DATE", f"{sale_day.month}月{sale_day.day}日")

    print(f"=== セレクトセール{year} {session}セール ランキング生成 (対象: 〜{target_suffix}) ===")

    lots = fetch_jrha(target_suffix, year)
    source = "JRHA公式"
    stats: dict = {}
    if not lots:
        rbn_url = os.environ.get("RBN_URL", DEFAULT_RBN_URL)
        print("  JRHA公式に本年の結果なし。RBNにフォールバックします。")
        try:
            rbn_html = _get(rbn_url)
        except Exception as e:
            print(f"[エラー] RBN取得失敗: {e}", file=sys.stderr)
            rbn_html = ""
        if rbn_html:
            soup_text = BeautifulSoup(rbn_html, "html.parser").get_text(" ", strip=True)
            stats = extract_summary_stats(soup_text)
            lots = fetch_rbn(rbn_url, target_suffix)
            source = "racing-book.net"

    # 最終フォールバック: 手動データファイル（外部ソース全滅時・再実行用）
    if not lots:
        manual_path = Path(f"data/select_sale_{year}_{session}.json")
        if manual_path.exists():
            print(f"  外部ソース全滅。手動データ {manual_path} を使用します。")
            manual = json.loads(manual_path.read_text(encoding="utf-8"))
            lots = manual.get("lots", [])
            stats = manual.get("stats", {})
            # 手動データに開催日があれば実行日ではなくそちらを使う
            if manual.get("date_str") and not os.environ.get("SALE_DATE"):
                date_str = manual["date_str"]
                print(f"  開催日を手動データから取得: {date_str}")
            source = f"手動データ ({manual_path})"

    # 同一馬の重複を除去（JRHAはレスポンシブ用にテーブルが2重に存在する）
    seen: set[str] = set()
    unique_lots = []
    for lot in lots:
        if lot["name"] not in seen:
            seen.add(lot["name"])
            unique_lots.append(lot)
    lots = unique_lots

    if len(lots) < TOP_N:
        print(f"[エラー] 落札データが{len(lots)}頭しか取得できませんでした（{TOP_N}頭必要）。", file=sys.stderr)
        sys.exit(1)

    lots.sort(key=lambda x: x["price_man"], reverse=True)
    top = lots[:TOP_N]

    print(f"\nソース: {source} / 集計: {stats or 'なし'}")
    for i, lot in enumerate(top):
        print(f"  {i+1:>2}位 {format_price(lot['price_man']):>10}  {lot['name']}（父{lot['sire']}） {lot['buyer']}")

    script = build_script(year, session, top, stats, date_str)

    sires = list(dict.fromkeys(lot["sire"] for lot in top if lot["sire"]))[:4]
    ranking_lines = "\n".join(
        f"第{i+1}位 {lot['name']}（父{lot['sire']}）{format_price(lot['price_man'])} {clean_buyer(lot['buyer'])}"
        for i, lot in enumerate(top)
    )
    hook = f"最高額{format_price(top[0]['price_man'])}！"

    news_entry = {
        "id": f"select_sale_{year}_{session}_{sale_day.strftime('%Y%m%d')}",
        "title": f"セレクトセール{year} {session}セール 高額落札ランキングTOP10",
        "url": "https://www.jrha.or.jp/selectsale/result",
        "summary": f"セレクトセール{year} {session}セールの高額落札ランキング。",
        "published_date": now.isoformat(),
        # landscape_video.py / upload_landscape_youtube.py が参照するフィールド
        "race_name": f"セレクトセール{year}",
        "grade": f"{session}セール",
        "date": date_str,
        "venue": "ノーザンホースパーク",
        "distance": "",
        "thumbnail_hook": hook,
        "horses": sires,
        "youtube_title": f"【セレクトセール{year}】{session}セール 高額落札ランキングTOP10｜{date_str}",
        "youtube_description": (
            f"セレクトセール{year} {session}セール（{date_str}・ノーザンホースパーク）の"
            f"高額落札ランキングTOP10です。\n\n{ranking_lines}\n\n"
            f"#競馬 #セレクトセール #セリ #競馬ニュース #keiba #JRA"
        ),
        "extra_tags": [
            "セレクトセール", f"セレクトセール{year}", "セリ", "競走馬セリ",
            "高額落札", "ランキング", f"{session}馬", "ノーザンファーム",
        ] + [f"{s}産駒" for s in sires[:3]],
    }

    Path(NEWS_JSON).write_text(
        json.dumps([news_entry], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n{NEWS_JSON} を生成しました。")

    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    script_path = Path(OUTPUT_DIR) / "script_0.txt"
    script_path.write_text(script, encoding="utf-8")
    print(f"{script_path} を生成しました（{len(script)}文字）。")

    # select_sale_ranking_video.py（専用レンダラー）用のランキングデータ
    ranking_meta = {
        "year": year,
        "session": session,
        "date_str": date_str,
        "stats": stats,
        "ranking": [
            {"rank": i + 1, "name": lot["name"], "sire": lot["sire"],
             "sex": lot.get("sex", ""), "buyer": clean_buyer(lot.get("buyer", "")),
             "price_man": lot["price_man"]}
            for i, lot in enumerate(top)
        ],
    }
    meta_path = Path(OUTPUT_DIR) / "ranking_meta_0.json"
    meta_path.write_text(json.dumps(ranking_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{meta_path} を生成しました。")
    print("---- 脚本 ----")
    print(script)


if __name__ == "__main__":
    main()
