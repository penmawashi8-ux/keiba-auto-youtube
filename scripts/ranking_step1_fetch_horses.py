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
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://db.netkeiba.com/",
}

# netkeibaのG1勝利数でソートした馬一覧ページ候補
CANDIDATE_URLS = [
    "https://db.netkeiba.com/?pid=horse_list&sort=g1&order=desc&page={page}",
    "https://db.netkeiba.com/?pid=horse_list&sort=g1_count&order=desc&page={page}",
    "https://db.netkeiba.com/?pid=horse_list&sort=win_g1&order=desc&page={page}",
]


def try_url(session, url_template, page=1):
    import requests
    from bs4 import BeautifulSoup

    url = url_template.format(page=page)
    resp = session.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return None, None
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_=re.compile(r"nk_tb|horse"))
    if not table:
        tables = soup.find_all("table")
        # 馬名リンクを含むテーブルを選ぶ
        for t in tables:
            if t.find("a", href=re.compile(r"/horse/\d{10}")):
                table = t
                break
    return soup, table


def parse_horses_from_table(table, existing_count):
    horses = []
    rows = table.find_all("tr")
    for row in rows[1:]:
        cols = row.find_all("td")
        if not cols:
            continue
        link = row.find("a", href=re.compile(r"/horse/\d{10}"))
        if not link:
            continue
        horse_name = link.text.strip()
        href = link["href"]
        if not href.startswith("http"):
            href = "https://db.netkeiba.com" + href
        # G1勝利数: 数字の列を探す
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
        session = requests.Session()

        # どのURLテンプレートが有効かを最初のページで確認
        working_template = None
        for tmpl in CANDIDATE_URLS:
            soup, table = try_url(session, tmpl, page=1)
            if table:
                working_template = tmpl
                print(f"有効なURL: {tmpl.format(page=1)}")
                break
            time.sleep(1)

        if not working_template:
            print("有効なURLが見つかりませんでした")
            return None

        all_horses = []
        for page in range(1, 4):
            soup, table = try_url(session, working_template, page=page)
            if not table:
                print(f"page {page}: テーブルが見つかりません")
                break
            horses = parse_horses_from_table(table, len(all_horses))
            if not horses:
                break
            all_horses.extend(horses)
            print(f"page {page}: {len(horses)}頭取得 (累計 {len(all_horses)}頭)")
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
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from bs4 import BeautifulSoup

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        # GitHub Actions / CI 環境の chromium パスに対応
        chrome_bin = os.environ.get("CHROME_BIN", "")
        if chrome_bin:
            options.binary_location = chrome_bin

        chrome_driver = os.environ.get("CHROME_DRIVER_PATH", "")
        from selenium.webdriver.chrome.service import Service
        service = Service(executable_path=chrome_driver) if chrome_driver else Service()

        driver = webdriver.Chrome(service=service, options=options)
        all_horses = []

        try:
            for tmpl in CANDIDATE_URLS:
                url = tmpl.format(page=1)
                driver.get(url)
                time.sleep(random.uniform(3, 5))

                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "a[href*='/horse/']")
                        )
                    )
                except Exception:
                    continue

                soup = BeautifulSoup(driver.page_source, "html.parser")
                table = None
                for t in soup.find_all("table"):
                    if t.find("a", href=re.compile(r"/horse/\d{10}")):
                        table = t
                        break

                if table:
                    print(f"Selenium 有効URL: {url}")
                    horses = parse_horses_from_table(table, 0)
                    all_horses.extend(horses)

                    for page in range(2, 4):
                        url2 = tmpl.format(page=page)
                        driver.get(url2)
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


def save_horses_csv(horses, path="horses.csv"):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "name", "url", "g1_wins"])
        writer.writeheader()
        writer.writerows(horses)
    print(f"{len(horses)}頭を {path} に保存しました")


def main():
    print("=== Step 1: netkeiba G1勝利数ランキング取得 ===")

    print("requestsで取得を試みます...")
    horses = fetch_with_requests()

    if not horses:
        print("requestsが失敗しました。Seleniumで再試行します...")
        horses = fetch_with_selenium()

    if not horses:
        print("ERROR: スクレイピングに失敗しました")
        sys.exit(1)

    save_horses_csv(horses)
    print(f"完了: {len(horses)}頭取得")


if __name__ == "__main__":
    main()
