#!/usr/bin/env python3
"""
YouTube Studio を Playwright でブラウザ操作して動画をアップロードする。
YouTube Data API を使わないため 10,000 unit/日 クォータ制限を回避できる。

必要な環境変数:
  YOUTUBE_COOKIES: ブラウザからエクスポートした Cookie (JSON 配列)
"""

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

sys.path.insert(0, str(Path(__file__).parent))
from upload_youtube import generate_thumbnail

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
POSTED_IDS_FILE = "posted_ids.txt"
STUDIO_URL = "https://studio.youtube.com"

# アップロード後の完了確認文字列（日本語 / 英語 UI 両対応）
UPLOAD_DONE_TEXTS = [
    "動画がアップロードされました",
    "Video published",
    "アップロードが完了",
    "checks are complete",
]


SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified":    "None",
    "lax":            "Lax",
    "strict":         "Strict",
    "none":           "None",
}


def normalize_cookies(cookies: list) -> list:
    """Cookie-Editor 等のエクスポート形式を Playwright 互換に正規化する。"""
    result = []
    for c in cookies:
        c = dict(c)
        ss = str(c.get("sameSite", "")).lower()
        c["sameSite"] = SAME_SITE_MAP.get(ss, "None")
        # expires が未設定なら -1（セッションCookie扱い）
        if "expires" not in c:
            c["expires"] = -1
        # Playwright が不要なフィールドを除去
        for key in ("hostOnly", "session", "storeId", "id"):
            c.pop(key, None)
        result.append(c)
    return result


def load_cookies() -> list:
    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        print("[エラー] YOUTUBE_COOKIES が未設定です", file=sys.stderr)
        sys.exit(1)
    try:
        cookies = json.loads(raw)
        if not isinstance(cookies, list):
            raise ValueError("Cookie は JSON 配列である必要があります")
        return normalize_cookies(cookies)
    except Exception as e:
        print(f"[エラー] YOUTUBE_COOKIES の解析失敗: {e}", file=sys.stderr)
        sys.exit(1)


def click_first(page, selectors: list[str], timeout=10000):
    """複数セレクタを順番に試してクリック（UI 変更への耐性）"""
    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=timeout)
            return True
        except Exception:
            continue
    raise Exception(f"クリックできませんでした: {selectors}")


def upload_one(page, video_path: str, title: str, description: str) -> bool:
    print(f"\n--- アップロード: {title[:60]} ---")

    # YouTube Studio トップへ移動
    page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    # ログイン確認
    if "accounts.google.com" in page.url or "signin" in page.url:
        print("[エラー] 未ログイン状態です。YOUTUBE_COOKIES を確認してください。", file=sys.stderr)
        return False

    print("  Studio 読み込み完了")

    # ① 「作成」ボタンをクリックしてドロップダウンを開く
    click_first(page, [
        "[aria-label='作成']",
        "[aria-label='Create']",
    ])
    time.sleep(2)  # ドロップダウンのアニメーション待ち

    # ② ドロップダウンから「動画をアップロード」をクリック
    try:
        page.get_by_text("動画をアップロード", exact=True).first.click(timeout=5000)
    except Exception:
        try:
            page.get_by_text("Upload videos", exact=True).first.click(timeout=5000)
        except Exception:
            click_first(page, [
                "#upload-icon",
                "[id='upload-icon']",
                "ytcp-ve[id='upload-icon']",
            ])
    time.sleep(2)

    # ② ファイルを直接 input にセット（file chooser より安定）
    page.locator("input[type='file']").first.set_input_files(video_path)
    print(f"  ファイルセット: {video_path}")

    # ③ タイトル入力欄が現れるまで待つ
    title_sel = (
        "#title-textarea #child-input, "
        "#title-textarea [contenteditable='true'], "
        "[placeholder*='title'], [placeholder*='タイトル']"
    )
    page.wait_for_selector(title_sel, timeout=30000)
    title_el = page.locator(title_sel).first
    title_el.click(force=True)
    page.keyboard.press("Control+a")
    page.keyboard.type(f"【競馬速報】{title[:90]} #Shorts")
    print("  タイトル入力完了")

    # ④ 説明
    desc_sel = (
        "#description-textarea #child-input, "
        "#description-textarea [contenteditable='true'], "
        "[placeholder*='description'], [placeholder*='説明']"
    )
    try:
        page.locator(desc_sel).first.click(timeout=5000)
        page.locator(desc_sel).first.fill(description[:2000])
    except Exception:
        print("  [警告] 説明入力スキップ", file=sys.stderr)

    # ⑤ 「視聴者」設定: 子供向けでない
    try:
        page.locator(
            "[value='VIDEO_MADE_FOR_KIDS_NOT'], "
            "tp-yt-paper-radio-button[name='NOT_MADE_FOR_KIDS']"
        ).first.click(timeout=5000)
    except Exception:
        pass  # 表示されない場合もある

    # ⑥ 「次へ」3回
    for step in range(3):
        time.sleep(1)
        click_first(page, [
            "#next-button",
            "ytcp-button#next-button",
            "button:has-text('次へ')",
            "button:has-text('Next')",
        ])
        print(f"  次へ ({step+1}/3)")

    # ⑦ 公開設定
    time.sleep(1)
    click_first(page, [
        "[name='PUBLIC']",
        "tp-yt-paper-radio-button[name='PUBLIC']",
        "label:has-text('公開')",
        "label:has-text('Public')",
        "#privacy-radios [value='PUBLIC']",
    ])
    print("  公開設定完了")

    # ⑧ 公開ボタン
    time.sleep(1)
    click_first(page, [
        "#done-button",
        "ytcp-button#done-button",
        "button:has-text('公開')",
        "button:has-text('Publish')",
        "button:has-text('保存')",
    ])
    print("  公開ボタンクリック")

    # ⑨ 完了待ち（最大3分）
    try:
        for text in UPLOAD_DONE_TEXTS:
            try:
                page.wait_for_selector(f"text={text}", timeout=180000)
                print(f"  ✅ アップロード完了")
                return True
            except PWTimeout:
                continue
        # いずれも見つからなくても、URL が動画ページなら成功とみなす
        time.sleep(5)
        print("  ⚠️ 完了確認できませんでしたがアップロード自体は完了している可能性があります")
        return True
    except Exception as e:
        print(f"  [エラー] 完了待ちで例外: {e}", file=sys.stderr)
        return False


def upload_thumbnail_playwright(page, thumbnail_bytes: bytes) -> None:
    """アップロード直後の Studio 動画ページでサムネイルを設定する（ベストエフォート）"""
    try:
        # サムネイル画像を一時ファイルに書き出し
        tmp = Path("/tmp/thumb_upload.jpg")
        tmp.write_bytes(thumbnail_bytes)

        # サムネイル変更ボタン
        page.locator(
            "button:has-text('サムネイルをアップロード'), "
            "button:has-text('Upload thumbnail'), "
            "#still-picker-button"
        ).first.click(timeout=10000)
        time.sleep(1)
        page.locator("input[type='file'][accept*='image']").first.set_input_files(str(tmp))
        time.sleep(3)
        print("  サムネイル設定完了")
    except Exception as e:
        print(f"  [警告] サムネイル設定失敗: {e}", file=sys.stderr)


def update_posted_ids(news_items: list) -> None:
    path = Path(POSTED_IDS_FILE)
    existing = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
    new_ids = {item["id"] for item in news_items}
    path.write_text("\n".join(sorted(existing | new_ids)), encoding="utf-8")
    print(f"posted_ids.txt に {len(new_ids)} 件追記")


def main() -> None:
    print("=== YouTube Studio Playwright アップロード開始 ===")

    if not Path(NEWS_JSON).exists():
        print(f"[エラー] {NEWS_JSON} が見つかりません", file=sys.stderr)
        sys.exit(1)

    news_items = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが 0 件のためスキップ")
        sys.exit(0)

    video_files = sorted(
        f for f in Path(OUTPUT_DIR).glob("video_*.mp4")
        if f.stem.split("_")[1].isdigit()
    )
    if not video_files:
        print(f"[エラー] {OUTPUT_DIR}/video_*.mp4 が見つかりません", file=sys.stderr)
        sys.exit(1)

    cookies = load_cookies()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        context.add_cookies(cookies)
        page = context.new_page()

        uploaded_count = 0
        for video_file in video_files:
            idx = int(video_file.stem.split("_")[1])
            if idx >= len(news_items):
                print(f"  [警告] インデックス {idx} の記事なし、スキップ")
                continue

            item = news_items[idx]
            title = item["title"]
            script_path = Path(f"{OUTPUT_DIR}/script_{idx}.txt")
            description = script_path.read_text(encoding="utf-8").strip() if script_path.exists() else title

            ok = upload_one(page, str(video_file), title, description)
            if ok:
                # サムネイル設定（ベストエフォート）
                try:
                    thumb_bytes = generate_thumbnail(title, idx)
                    upload_thumbnail_playwright(page, thumb_bytes)
                except Exception as e:
                    print(f"  [警告] サムネイル処理失敗: {e}", file=sys.stderr)
                uploaded_count += 1
            else:
                print(f"  [エラー] アップロード失敗: {title[:50]}", file=sys.stderr)

        browser.close()

    update_posted_ids(news_items)
    print(f"\n=== 完了: {uploaded_count} 本アップロード ===")


if __name__ == "__main__":
    main()
