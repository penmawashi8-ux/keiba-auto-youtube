#!/usr/bin/env python3
"""YouTube Studio のブラウザ操作でサムネイルを設定する。

YouTube Data API の thumbnails().set() が channelNotEligible（電話番号認証未済）の
場合の代替手段。upload_youtube.py で生成した output/upload_results.json を読み込み、
各動画のサムネイルを YouTube Studio 編集ページから設定する。

必要な環境変数:
  YOUTUBE_COOKIES: ブラウザからエクスポートした Cookie (JSON 配列)
"""

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

UPLOAD_RESULTS_JSON = "output/upload_results.json"

SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified": "None",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}


def normalize_cookies(cookies: list) -> list:
    """Cookie-Editor 等のエクスポート形式を Playwright 互換に正規化する。"""
    result = []
    for c in cookies:
        c = dict(c)
        ss = str(c.get("sameSite", "")).lower()
        c["sameSite"] = SAME_SITE_MAP.get(ss, "None")
        if "expires" not in c:
            c["expires"] = -1
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


def click_first(page, selectors: list[str], timeout: int = 10000) -> bool:
    """複数セレクタを順番に試してクリック（UI 変更への耐性）。"""
    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=timeout)
            return True
        except Exception:
            continue
    raise Exception(f"クリックできませんでした: {selectors}")


def wait_for_studio(page, timeout: int = 30000) -> bool:
    """YouTube Studio のSPA読み込み完了を待つ。"""
    selectors = [
        "ytcp-video-edit-url",
        "ytcp-thumbnails-compact-editor-desktop",
        "ytcp-still-picker",
        "#still-picker-button",
        "ytcp-video-metadata-editor",
    ]
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout)
            return True
        except Exception:
            continue
    return False


# shadow DOM を再帰的に探索してクリックする JS スニペット
_JS_CLICK_THUMBNAIL = """
() => {
    function findInShadow(root, selector) {
        try {
            const el = root.querySelector(selector);
            if (el) return el;
        } catch (e) {}
        const all = root.querySelectorAll('*');
        for (const host of all) {
            if (host.shadowRoot) {
                const found = findInShadow(host.shadowRoot, selector);
                if (found) return found;
            }
        }
        return null;
    }

    function findButtonByText(root, texts) {
        const candidates = root.querySelectorAll(
            'button, label, div[role="button"], span[role="button"]'
        );
        for (const el of candidates) {
            const label = (el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '');
            if (texts.some(t => label.includes(t))) return el;
        }
        const all = root.querySelectorAll('*');
        for (const host of all) {
            if (host.shadowRoot) {
                const found = findButtonByText(host.shadowRoot, texts);
                if (found) return found;
            }
        }
        return null;
    }

    // セレクタで探す
    const selectors = [
        '#still-picker-button',
        'ytcp-still-picker button',
        'ytcp-thumbnails-compact-editor-desktop button',
        'ytcp-thumbnails-compact-editor button',
        'ytcp-still-picker',
        'ytcp-thumbnails-compact-editor-desktop',
    ];
    for (const sel of selectors) {
        const el = findInShadow(document, sel);
        if (el) {
            el.scrollIntoView({block: 'center'});
            el.click();
            return 'clicked:' + sel;
        }
    }

    // テキストで探す
    const texts = ['サムネイル', 'thumbnail', 'Thumbnail', 'アップロード', 'Upload'];
    const btn = findButtonByText(document, texts);
    if (btn) {
        btn.scrollIntoView({block: 'center'});
        btn.click();
        return 'clicked:text';
    }

    return 'not_found';
}
"""

# shadow DOM を含め hidden な file input を強制表示する JS スニペット
_JS_SHOW_FILE_INPUTS = """
() => {
    function showInputs(root) {
        const inputs = root.querySelectorAll("input[type='file']");
        inputs.forEach(el => {
            el.style.cssText = [
                'display:block!important',
                'visibility:visible!important',
                'opacity:1!important',
                'position:fixed!important',
                'top:0', 'left:0',
                'width:100px', 'height:100px',
                'z-index:99999',
            ].join(';');
        });
        root.querySelectorAll('*').forEach(host => {
            if (host.shadowRoot) showInputs(host.shadowRoot);
        });
    }
    showInputs(document);
}
"""


def _try_click_thumbnail_button(page) -> None:
    """サムネイルアップロードボタンをあらゆる手段でクリックする。"""
    # サムネイルセクションへスクロール
    page.evaluate("""
        () => {
            const el = document.querySelector(
                'ytcp-thumbnails-compact-editor-desktop, ytcp-still-picker, ytcp-thumbnails-compact-editor'
            );
            if (el) el.scrollIntoView({block: 'center'});
        }
    """)
    time.sleep(0.5)

    # Playwright セレクタで試す
    selectors = [
        "#still-picker-button",
        "ytcp-still-picker button",
        "ytcp-thumbnails-compact-editor-desktop button",
        "ytcp-thumbnails-compact-editor button",
        "button[aria-label*='サムネイル']",
        "button[aria-label*='thumbnail']",
        "button[aria-label*='Thumbnail']",
        "button:has-text('サムネイルをアップロード')",
        "button:has-text('Upload thumbnail')",
        "button:has-text('カスタムサムネイルをアップロード')",
        "button:has-text('Upload custom thumbnail')",
        "button:has-text('アップロード')",
        "ytcp-thumbnails-compact-editor-desktop",
        "ytcp-still-picker",
    ]
    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=3000)
            print(f"  [情報] Playwright クリック成功: {sel}", file=sys.stderr)
            return
        except Exception:
            continue

    # JS で shadow DOM を含めて探してクリック
    result = page.evaluate(_JS_CLICK_THUMBNAIL)
    if result == "not_found":
        raise Exception("サムネイルボタンが見つかりませんでした (Playwright + JS 両方失敗)")
    print(f"  [情報] JS クリック成功: {result}", file=sys.stderr)


def set_thumbnail(page, video_id: str, thumbnail_path: str) -> bool:
    """YouTube Studio の動画編集ページでサムネイルを設定する。"""
    url = f"https://studio.youtube.com/video/{video_id}/edit"
    print(f"  YouTube Studio を開く: {url}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  [警告] ページ読み込みタイムアウト: {e}", file=sys.stderr)

    if "accounts.google.com" in page.url or "signin" in page.url:
        print("[エラー] 未ログイン状態です。YOUTUBE_COOKIES を確認してください。", file=sys.stderr)
        return False

    # SPA の非同期レンダリング完了を待つ
    if not wait_for_studio(page, timeout=30000):
        print("  [警告] Studio UIの読み込みタイムアウト。スクリーンショットを保存します。", file=sys.stderr)
        _save_debug_screenshot(page, video_id, "load_timeout")
        return False

    time.sleep(3)

    # --- アプローチ1: expect_file_chooser でファイル選択ダイアログをインターセプト ---
    thumbnail_set = False
    try:
        with page.expect_file_chooser(timeout=20000) as fc_info:
            _try_click_thumbnail_button(page)
        fc_info.value.set_files(thumbnail_path)
        print(f"  サムネイルファイルセット (file_chooser): {thumbnail_path}")
        thumbnail_set = True
    except Exception as e:
        print(f"  [警告] file_chooser 方式失敗: {e}", file=sys.stderr)
        _save_debug_screenshot(page, video_id, "file_chooser_error")

    # --- アプローチ2: hidden な input[type='file'] を強制表示してセット ---
    if not thumbnail_set:
        try:
            page.evaluate(_JS_SHOW_FILE_INPUTS)
            time.sleep(0.5)
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(thumbnail_path, timeout=10000)
            print(f"  サムネイルファイルセット (hidden input): {thumbnail_path}")
            thumbnail_set = True
        except Exception as e2:
            print(f"  [警告] hidden input 方式失敗: {e2}", file=sys.stderr)
            _save_debug_screenshot(page, video_id, "file_input_error")

    if not thumbnail_set:
        return False

    time.sleep(3)

    # 保存ボタンをクリック
    try:
        click_first(page, [
            "#save-button",
            "ytcp-button#save-button",
            "button:has-text('保存')",
            "button:has-text('Save')",
        ], timeout=10000)
        print("  保存ボタンクリック完了")
    except Exception as e:
        print(f"  [警告] 保存ボタンが見つかりません: {e}", file=sys.stderr)
        _save_debug_screenshot(page, video_id, "save_button_error")
        return False

    time.sleep(3)
    return True


def _save_debug_screenshot(page, video_id: str, label: str) -> None:
    """デバッグ用スクリーンショットを output/ に保存する。"""
    try:
        Path("output").mkdir(exist_ok=True)
        path = f"output/debug_{video_id}_{label}.png"
        page.screenshot(path=path)
        print(f"  [デバッグ] スクリーンショット保存: {path}", file=sys.stderr)
    except Exception:
        pass


def main() -> None:
    print("=== YouTube サムネイル自動設定 (Playwright) ===")

    # YOUTUBE_COOKIES が未設定ならスキップ（graceful degradation）
    if not os.environ.get("YOUTUBE_COOKIES", "").strip():
        print("YOUTUBE_COOKIES が未設定のためサムネイル設定をスキップします")
        sys.exit(0)

    if not Path(UPLOAD_RESULTS_JSON).exists():
        print(f"[警告] {UPLOAD_RESULTS_JSON} が見つかりません。スキップします。")
        sys.exit(0)

    results = json.loads(Path(UPLOAD_RESULTS_JSON).read_text(encoding="utf-8"))
    if not results:
        print("アップロード結果が空のためスキップします")
        sys.exit(0)

    # サムネイルファイルが存在するエントリのみ処理
    targets = [
        r for r in results
        if r.get("video_id") and r.get("thumbnail") and Path(r["thumbnail"]).exists()
    ]
    if not targets:
        print("サムネイルファイルが見つかりません。スキップします。")
        sys.exit(0)

    print(f"サムネイル設定対象: {len(targets)} 件")
    cookies = load_cookies()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36"
            ),
        )
        context.add_cookies(cookies)
        page = context.new_page()

        success_count = 0
        for entry in targets:
            video_id = entry["video_id"]
            thumb_path = entry["thumbnail"]
            title = entry.get("title", "")[:50]

            print(f"\n--- サムネイル設定: video_id={video_id} / {title} ---")
            ok = set_thumbnail(page, video_id, thumb_path)
            if ok:
                success_count += 1
                print(f"  ✅ 完了: {video_id}")
            else:
                print(f"  ❌ 失敗: {video_id}", file=sys.stderr)

        browser.close()

    print(f"\n=== 完了: {success_count}/{len(targets)} 件のサムネイルを設定 ===")
    if success_count < len(targets):
        sys.exit(1)


if __name__ == "__main__":
    main()
