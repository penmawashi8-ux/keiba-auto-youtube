#!/usr/bin/env python3
"""YouTube Studio のブラウザ操作でサムネイルを設定する。

認証方法（優先順位順）:
  1. OAuth2リフレッシュトークン（GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN）
     → アップロード用と同じ認証情報を再利用できるため YOUTUBE_COOKIES は不要
  2. ブラウザからエクスポートした Cookie（YOUTUBE_COOKIES）

サムネイル設定方法（優先順位順）:
  1. カスタム画像アップロード（チャンネルの電話番号認証済みの場合）
  2. 動画フレームからスチル選択（認証不要・全チャンネル対応）
"""

import json
import os
import sys
import time
from pathlib import Path

import google.auth.transport.requests
import requests as http_requests
from google.oauth2.credentials import Credentials
from playwright.sync_api import sync_playwright

UPLOAD_RESULTS_JSON = "output/upload_results.json"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

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


def load_cookies_from_oauth() -> list | None:
    """OAuth2リフレッシュトークンからYouTube Studioセッションクッキーを取得する。

    Google の OAuthLogin エンドポイントに OAuth2 アクセストークンを渡すことで
    ブラウザセッション用クッキーを動的に生成する。毎回新鮮なセッションを取得
    できるため、手動エクスポートのクッキーが期限切れになる問題を解決する。
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        return None

    print("OAuth2リフレッシュトークンからセッションクッキーを取得中...")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=YOUTUBE_SCOPES,
    )
    try:
        creds.refresh(google.auth.transport.requests.Request())
    except Exception as e:
        print(f"[警告] OAuthトークンリフレッシュ失敗: {e}", file=sys.stderr)
        return None

    access_token = creds.token

    session = http_requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    })

    try:
        resp = session.get(
            "https://accounts.google.com/accounts/OAuthLogin",
            params={
                "source": "ogb",
                "service": "youtube",
                "ptok": access_token,
                "continue": "https://studio.youtube.com",
            },
            allow_redirects=True,
            timeout=30,
        )

        final_url = resp.url
        if "accounts.google.com/signin" in final_url or "ServiceLogin" in final_url:
            print(
                f"[警告] OAuthLogin後にGoogleサインインページへリダイレクトされました: {final_url}\n"
                "       トークンのスコープが不足している可能性があります。",
                file=sys.stderr,
            )
            return None

        cookies = []
        for c in session.cookies:
            domain = c.domain if c.domain else ".youtube.com"
            if not domain.startswith("."):
                domain = f".{domain}"
            cookies.append({
                "name": c.name,
                "value": c.value,
                "domain": domain,
                "path": c.path or "/",
                "sameSite": "None",
                "expires": int(c.expires) if c.expires else -1,
            })

        if not cookies:
            print("[警告] OAuthLogin後にクッキーが取得できませんでした", file=sys.stderr)
            return None

        print(f"OAuth2からクッキーを取得しました ({len(cookies)} 件)")
        return cookies

    except Exception as e:
        print(f"[警告] OAuthLoginクッキー取得失敗: {e}", file=sys.stderr)
        return None


def load_cookies() -> list:
    """利用可能な認証情報からクッキーを返す。

    優先順位:
      1. OAuth2リフレッシュトークン（GOOGLE_CLIENT_ID 等）
      2. YOUTUBE_COOKIES 環境変数（手動エクスポート・フォールバック）
    """
    # 1. OAuth2から自動取得を試みる
    oauth_cookies = load_cookies_from_oauth()
    if oauth_cookies:
        return oauth_cookies

    # 2. YOUTUBE_COOKIES から読み込む（フォールバック）
    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        print(
            "[エラー] YOUTUBE_COOKIES が未設定で OAuth2 からの取得も失敗しました。\n"
            "       GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN を\n"
            "       環境変数に設定するか、YOUTUBE_COOKIES にブラウザクッキーを設定してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        cookies = json.loads(raw)
        if not isinstance(cookies, list):
            raise ValueError("Cookie は JSON 配列である必要があります")
        print(f"YOUTUBE_COOKIES から {len(cookies)} 件のクッキーを読み込みました")
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
    """YouTube Studio の SPA 読み込み完了を待つ。"""
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


def try_upload_custom_thumbnail(page, thumbnail_path: str) -> bool:
    """カスタムサムネイル画像をアップロードする（チャンネル電話番号認証が必要）。"""
    try:
        click_first(page, [
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
        ], timeout=8000)
        time.sleep(1)
    except Exception as e:
        print(f"  [情報] アップロードボタンが見つかりません（スキップ）: {e}", file=sys.stderr)

    # file input に直接ファイルをセット（ボタンクリック不要な場合もある）
    for selector in [
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ]:
        try:
            page.locator(selector).first.set_input_files(thumbnail_path, timeout=8000)
            print(f"  カスタムサムネイルをセット: {thumbnail_path}")
            return True
        except Exception:
            continue

    print("  [情報] カスタムサムネイルのファイルセット失敗", file=sys.stderr)
    return False


def try_select_first_still(page) -> bool:
    """動画から自動生成されたスチル（静止画）の最初のコマを選択する。

    カスタムサムネイルアップロードと異なりチャンネル認証不要で全チャンネルで利用可能。
    YouTube が動画冒頭付近で自動生成したフレームを選択する。
    """
    still_selectors = [
        "ytcp-still-picker-item",
        "ytcp-thumbnail-still-preview",
        "#still-1",
        "[data-still-index='0']",
        "ytcp-still-picker .ytcp-still-picker-item",
        "ytcp-still-picker button",
        # Shorts 向け
        "ytcp-thumbnail-moment-picker",
        ".ytcp-thumbnail-still-preview",
    ]
    try:
        click_first(page, still_selectors, timeout=5000)
        print("  スチル選択: 動画先頭フレームを選択しました")
        return True
    except Exception as e:
        print(f"  [情報] スチル選択失敗: {e}", file=sys.stderr)
        return False


def set_thumbnail(page, video_id: str, thumbnail_path: str) -> bool:
    """YouTube Studio の動画編集ページでサムネイルを設定する。"""
    url = f"https://studio.youtube.com/video/{video_id}/edit"
    print(f"  YouTube Studio を開く: {url}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  [警告] ページ読み込みタイムアウト: {e}", file=sys.stderr)

    if "accounts.google.com" in page.url or "signin" in page.url:
        print(
            "[エラー] 未ログイン状態です。\n"
            "       GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN を確認してください。",
            file=sys.stderr,
        )
        _save_debug_screenshot(page, video_id, "login_redirect")
        return False

    if not wait_for_studio(page, timeout=30000):
        print("  [警告] Studio UI の読み込みタイムアウト。スクリーンショットを保存します。", file=sys.stderr)
        _save_debug_screenshot(page, video_id, "load_timeout")
        return False

    time.sleep(2)

    # まずカスタム画像アップロードを試みる（チャンネル認証済みの場合に成功）
    upload_ok = try_upload_custom_thumbnail(page, thumbnail_path)

    # アップロード失敗時はスチル選択にフォールバック（全チャンネル対応）
    if not upload_ok:
        print("  カスタムアップロード不可のため、動画フレームのスチル選択を試みます...")
        if not try_select_first_still(page):
            _save_debug_screenshot(page, video_id, "thumbnail_error")
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

    # 認証情報の確認（OAuth2 または YOUTUBE_COOKIES のいずれかが必要）
    has_oauth = all([
        os.environ.get("GOOGLE_CLIENT_ID"),
        os.environ.get("GOOGLE_CLIENT_SECRET"),
        os.environ.get("GOOGLE_REFRESH_TOKEN"),
    ])
    has_cookies = bool(os.environ.get("YOUTUBE_COOKIES", "").strip())

    if not has_oauth and not has_cookies:
        print(
            "YOUTUBE_COOKIES も OAuth2 認証情報も未設定のためサムネイル設定をスキップします。\n"
            "設定方法: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN を\n"
            "GitHub Secrets に追加するか、YOUTUBE_COOKIES を設定してください。"
        )
        sys.exit(0)

    if not Path(UPLOAD_RESULTS_JSON).exists():
        print(f"[警告] {UPLOAD_RESULTS_JSON} が見つかりません。スキップします。")
        sys.exit(0)

    results = json.loads(Path(UPLOAD_RESULTS_JSON).read_text(encoding="utf-8"))
    if not results:
        print("アップロード結果が空のためスキップします")
        sys.exit(0)

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
