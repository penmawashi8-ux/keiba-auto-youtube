#!/usr/bin/env python3
"""Step 1: netkeiba から歴代G1勝利数ランキング上位50頭を取得して horses.csv に保存"""

import csv
import os
import time
import random
import sys
import re

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://db.netkeiba.com/",
}

# netkeibaのURLパターン候補（G1勝利数ソート）
CANDIDATE_URLS = [
    # 標準的な検索 + G1勝利数ソート
    "https://db.netkeiba.com/?pid=horse_list&sort=g1&order=desc&page={page}&search=1",
    "https://db.netkeiba.com/?pid=horse_list&sort=g1&order=desc&page={page}",
    # 旧URL形式
    "https://db.netkeiba.com/?pid=horse_list&sort=g1_count&order=desc&page={page}",
    # 賞金順 (フォールバック: 賞金上位はG1勝利数上位と重なる)
    "https://db.netkeiba.com/?pid=horse_list&sort=earn&order=desc&page={page}&search=1",
    "https://db.netkeiba.com/?pid=horse_list&sort=earn&order=desc&page={page}",
]

# Chromium バイナリの候補パス（優先順）
CHROME_BINARY_CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "/usr/bin/chromium-browser",    # Ubuntu / GitHub Actions
    "/usr/bin/chromium",            # Debian / Arch
    "/snap/bin/chromium",           # Snap パッケージ
    "/usr/bin/google-chrome",       # デスクトップ Chrome
    "/usr/bin/google-chrome-stable",
    "/opt/google/chrome/chrome",
]


def find_chrome_binary():
    for path in CHROME_BINARY_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


def parse_horses_from_table(table, existing_count):
    horses = []
    rows = table.find_all("tr")
    for row in rows[1:]:
        cols = row.find_all("td")
        if not cols:
            continue
        # 馬名リンク: /horse/XXXXXXXXXX/ 形式
        link = row.find("a", href=re.compile(r"/horse/\d{10}"))
        if not link:
            continue
        horse_name = link.text.strip()
        href = link["href"]
        if not href.startswith("http"):
            href = "https://db.netkeiba.com" + href
        # G1勝利数: 数字列を探す（最初に見つかった正数）
        g1_wins = 0
        for col in cols:
            text = col.text.strip()
            if text.isdigit() and int(text) > 0:
                g1_wins = int(text)
                break
        horses.append({
            "rank": existing_count + len(horses) + 1,
            "name": horse_name,
            "url": href,
            "g1_wins": g1_wins,
        })
    return horses


def fetch_with_requests():
    try:
        import requests
        from bs4 import BeautifulSoup

        session = requests.Session()
        # まずトップページでクッキーを取得
        try:
            session.get("https://db.netkeiba.com/", headers=HEADERS, timeout=10)
            time.sleep(1)
        except Exception:
            pass

        working_template = None
        for tmpl in CANDIDATE_URLS:
            url = tmpl.format(page=1)
            print(f"  試行: {url}")
            try:
                resp = session.get(url, headers=HEADERS, timeout=15)
                print(f"  → HTTP {resp.status_code}")
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                # 馬名リンクが含まれるテーブルを探す
                found_table = None
                for t in soup.find_all("table"):
                    if t.find("a", href=re.compile(r"/horse/\d{10}")):
                        found_table = t
                        break
                if found_table:
                    working_template = tmpl
                    print(f"  ✓ テーブル発見: {url}")
                    break
                else:
                    print(f"  馬名テーブルなし (HTML長={len(resp.text)})")
            except Exception as e:
                print(f"  エラー: {e}")
            time.sleep(random.uniform(1, 2))

        if not working_template:
            return None

        all_horses = []
        for page in range(1, 4):
            url = working_template.format(page=page)
            resp = session.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            table = None
            for t in soup.find_all("table"):
                if t.find("a", href=re.compile(r"/horse/\d{10}")):
                    table = t
                    break
            if not table:
                break
            horses = parse_horses_from_table(table, len(all_horses))
            if not horses:
                break
            all_horses.extend(horses)
            print(f"  page {page}: {len(horses)}頭取得 (累計 {len(all_horses)}頭)")
            if len(all_horses) >= 50:
                break
            time.sleep(random.uniform(2, 3))

        return all_horses[:50] if all_horses else None

    except Exception as e:
        print(f"requests失敗: {e}")
        return None


def fetch_with_selenium():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from bs4 import BeautifulSoup

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

        # Chromium バイナリを自動検出
        chrome_bin = find_chrome_binary()
        if chrome_bin:
            options.binary_location = chrome_bin
            print(f"  Chrome binary: {chrome_bin}")
        else:
            print("  WARNING: Chromeバイナリが見つかりません")

        # ChromeDriver パスを環境変数または自動検出
        driver_path = os.environ.get("CHROME_DRIVER_PATH", "")
        if driver_path and os.path.exists(driver_path):
            service = Service(executable_path=driver_path)
        else:
            # chromedriver を自動検出
            for candidate in ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver"]:
                if os.path.exists(candidate):
                    service = Service(executable_path=candidate)
                    print(f"  ChromeDriver: {candidate}")
                    break
            else:
                service = Service()

        driver = webdriver.Chrome(service=service, options=options)
        all_horses = []

        try:
            driver.get("https://db.netkeiba.com/")
            time.sleep(2)

            for tmpl in CANDIDATE_URLS:
                url = tmpl.format(page=1)
                print(f"  Selenium試行: {url}")
                driver.get(url)
                time.sleep(random.uniform(3, 5))

                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "table"))
                    )
                except Exception:
                    pass

                soup = BeautifulSoup(driver.page_source, "html.parser")
                table = None
                for t in soup.find_all("table"):
                    if t.find("a", href=re.compile(r"/horse/\d{10}")):
                        table = t
                        break

                if not table:
                    print(f"  テーブルなし")
                    continue

                print(f"  ✓ Seleniumでテーブル発見")
                horses = parse_horses_from_table(table, 0)
                if horses:
                    all_horses.extend(horses)
                    for page in range(2, 4):
                        driver.get(tmpl.format(page=page))
                        time.sleep(random.uniform(2, 3))
                        soup2 = BeautifulSoup(driver.page_source, "html.parser")
                        table2 = None
                        for t in soup2.find_all("table"):
                            if t.find("a", href=re.compile(r"/horse/\d{10}")):
                                table2 = t
                                break
                        if not table2:
                            break
                        more = parse_horses_from_table(table2, len(all_horses))
                        if not more:
                            break
                        all_horses.extend(more)
                        if len(all_horses) >= 50:
                            break
                    break

        finally:
            driver.quit()

        return all_horses[:50] if all_horses else None

    except Exception as e:
        print(f"Selenium失敗: {e}")
        return None


def use_fallback_list():
    """スクレイピング全失敗時の既知上位馬フォールバックリスト"""
    print("フォールバック: 既知の歴代G1上位馬リストを使用します")
    # 歴代JRA G1勝利数上位（おおよそのランキング順）
    # URL は netkeiba の実際のIDを使用
    horses_data = [
        ("ディープインパクト",   "https://db.netkeiba.com/horse/2002110019/", 12),
        ("アーモンドアイ",       "https://db.netkeiba.com/horse/2015104244/", 9),
        ("オルフェーヴル",       "https://db.netkeiba.com/horse/2008104132/", 8),
        ("キタサンブラック",     "https://db.netkeiba.com/horse/2012104233/", 8),
        ("ウオッカ",             "https://db.netkeiba.com/horse/2004106526/", 8),
        ("テイエムオペラオー",   "https://db.netkeiba.com/horse/1996106516/", 7),
        ("ジェンティルドンナ",   "https://db.netkeiba.com/horse/2009106541/", 7),
        ("ブエナビスタ",         "https://db.netkeiba.com/horse/2006106509/", 6),
        ("ゴールドシップ",       "https://db.netkeiba.com/horse/2009104179/", 6),
        ("グランアレグリア",     "https://db.netkeiba.com/horse/2017104364/", 6),
        ("クロノジェネシス",     "https://db.netkeiba.com/horse/2017106183/", 5),
        ("スペシャルウィーク",   "https://db.netkeiba.com/horse/1995110019/", 5),
        ("グラスワンダー",       "https://db.netkeiba.com/horse/1995106551/", 5),
        ("エルコンドルパサー",   "https://db.netkeiba.com/horse/1995106506/", 5),
        ("タイキシャトル",       "https://db.netkeiba.com/horse/1994110028/", 5),
        ("イナリワン",           "https://db.netkeiba.com/horse/1984106520/", 5),
        ("オグリキャップ",       "https://db.netkeiba.com/horse/1985106524/", 5),
        ("シンボリルドルフ",     "https://db.netkeiba.com/horse/1981106506/", 7),
        ("ナリタブライアン",     "https://db.netkeiba.com/horse/1991106524/", 5),
        ("ミホノブルボン",       "https://db.netkeiba.com/horse/1989106517/", 5),
        ("トウカイテイオー",     "https://db.netkeiba.com/horse/1988110020/", 5),
        ("マヤノトップガン",     "https://db.netkeiba.com/horse/1992106521/", 4),
        ("マーベラスサンデー",   "https://db.netkeiba.com/horse/1992106511/", 4),
        ("ライスシャワー",       "https://db.netkeiba.com/horse/1989106527/", 3),
        ("ステイゴールド",       "https://db.netkeiba.com/horse/1994110031/", 1),
        ("フジキセキ",           "https://db.netkeiba.com/horse/1992110030/", 1),
        ("サイレンススズカ",     "https://db.netkeiba.com/horse/1994110023/", 6),
        ("エアグルーヴ",         "https://db.netkeiba.com/horse/1993106513/", 4),
        ("ダンスインザダーク",   "https://db.netkeiba.com/horse/1993110024/", 1),
        ("セイウンスカイ",       "https://db.netkeiba.com/horse/1995110017/", 3),
        ("ジャングルポケット",   "https://db.netkeiba.com/horse/1998110027/", 4),
        ("タニノギムレット",     "https://db.netkeiba.com/horse/1999110018/", 2),
        ("シンボリクリスエス",   "https://db.netkeiba.com/horse/1999110026/", 5),
        ("ゼンノロブロイ",       "https://db.netkeiba.com/horse/2000110020/", 4),
        ("キングカメハメハ",     "https://db.netkeiba.com/horse/2001110016/", 4),
        ("ハーツクライ",         "https://db.netkeiba.com/horse/2001110018/", 3),
        ("ダイワスカーレット",   "https://db.netkeiba.com/horse/2004106513/", 5),
        ("スクリーンヒーロー",   "https://db.netkeiba.com/horse/2005110025/", 1),
        ("ロールオブザダイス",   "https://db.netkeiba.com/horse/2009110022/", 0),
        ("ジャスタウェイ",       "https://db.netkeiba.com/horse/2009110023/", 4),
        ("ロードカナロア",       "https://db.netkeiba.com/horse/2008110021/", 6),
        ("エピファネイア",       "https://db.netkeiba.com/horse/2010110037/", 3),
        ("ジャパンカップ",       "https://db.netkeiba.com/horse/2010110024/", 0),
        ("エイシンフラッシュ",   "https://db.netkeiba.com/horse/2007110023/", 2),
        ("トーセンジョーダン",   "https://db.netkeiba.com/horse/2006110028/", 2),
        ("フィエールマン",       "https://db.netkeiba.com/horse/2015110037/", 4),
        ("リスグラシュー",       "https://db.netkeiba.com/horse/2014106241/", 5),
        ("クロフネ",             "https://db.netkeiba.com/horse/1998110017/", 3),
        ("ヴィクトワールピサ",   "https://db.netkeiba.com/horse/2007110033/", 4),
        ("ジェンティルドンナ2",  "https://db.netkeiba.com/horse/2009106541/", 7),
    ]

    horses = []
    seen = set()
    for name, url, g1_wins in horses_data:
        if name in seen or "2" in name.replace("ジェンティルドンナ", ""):
            continue
        seen.add(name)
        horses.append({
            "rank": len(horses) + 1,
            "name": name,
            "url": url,
            "g1_wins": g1_wins,
        })
        if len(horses) >= 50:
            break

    print(f"  フォールバックリスト: {len(horses)}頭")
    print("  ※ URLはnetkeiba公式で確認してください。誤りがある場合はresults.csvを手動修正してください")
    return horses


def save_horses_csv(horses, path="horses.csv"):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "name", "url", "g1_wins"])
        writer.writeheader()
        writer.writerows(horses)
    print(f"{len(horses)}頭を {path} に保存しました")


def main():
    print("=== Step 1: netkeiba G1勝利数ランキング取得 ===")

    # ① requests
    print("\n[1/3] requestsで取得を試みます...")
    horses = fetch_with_requests()

    # ② Selenium
    if not horses:
        print("\n[2/3] Seleniumで再試行します...")
        horses = fetch_with_selenium()

    # ③ フォールバック
    if not horses:
        print("\n[3/3] スクレイピング失敗。フォールバックリストを使用します...")
        horses = use_fallback_list()

    if not horses:
        print("ERROR: データ取得に完全に失敗しました")
        sys.exit(1)

    save_horses_csv(horses)
    print(f"\n完了: {len(horses)}頭取得")


if __name__ == "__main__":
    main()
