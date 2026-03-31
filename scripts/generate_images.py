#!/usr/bin/env python3
"""
generate_images.py - 競馬関連画像を取得する。

優先順位:
  1. Pixabay API（無料・実写競馬写真）
  2. HuggingFace Inference API（HF_TOKEN が設定されている場合）
  3. どちらも失敗 → グラデーション背景（generate_video.py が自動生成）

Pixabay APIキーは PIXABAY_API_KEY 環境変数にセット。
  取得先: https://pixabay.com/api/docs/ （無料アカウント登録後に即発行可能）
"""

import io
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

NEWS_JSON = "news.json"
ASSETS_DIR = Path("assets")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

PIXABAY_API_URL = "https://pixabay.com/api/"
HF_MODEL_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

# 競馬関連の検索クエリ（ローテーション）
PIXABAY_QUERIES = [
    "horse racing",
    "thoroughbred horse",
    "jockey horse race",
    "horse racing track",
]

DEFAULT_PROMPTS = [
    "cinematic photo of horses racing at sunset on a beautiful racecourse, dramatic lighting, high quality",
    "cinematic photo of jockey riding horse on racecourse, motion blur, high quality",
    "cinematic photo of horse racing crowd cheering at finish line, high quality",
    "cinematic photo of thoroughbred horse in paddock, golden hour, high quality",
]


def get_prompts_from_gemini(api_keys: list[str], news_items: list[dict]) -> list[str]:
    """Geminiテキストモデルで画像プロンプトを生成（全キー失敗時はデフォルト使用）"""
    item = news_items[0] if news_items else {}
    title = item.get("title", "")
    body = item.get("body", item.get("summary", ""))[:300]
    prompt_text = (
        "以下の競馬ニュースの内容に合った、AI画像生成用の英語プロンプトを4つ作成してください。"
        "競馬場・馬・騎手・レースの雰囲気が伝わるシーンを描写してください。"
        "各プロンプトは「cinematic photo of [描写], horse racing, dramatic lighting, high quality」"
        "の形式で50語以内。JSON配列で返してください。\n\n"
        f"タイトル: {title}\n本文: {body}"
    )
    url = f"{GEMINI_API_BASE}/gemini-2.5-flash:generateContent"
    for api_key in api_keys:
        key_label = f"***{api_key[-4:]}"
        try:
            r = requests.post(
                url,
                json={"contents": [{"parts": [{"text": prompt_text}]}]},
                params={"key": api_key},
                timeout=30,
            )
            if r.status_code == 429:
                print(f"  [警告] key={key_label} 429 クォータ超過。次のキーへ切り替えます。", flush=True)
                continue
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = text.replace("```json", "").replace("```", "").strip()
            prompts = json.loads(text)
            if isinstance(prompts, list) and len(prompts) >= 4:
                result = []
                for it in prompts:
                    if isinstance(it, str):
                        result.append(it)
                    elif isinstance(it, dict):
                        val = next((it[k] for k in ("prompt", "text", "description", "content") if k in it), None)
                        if val is None and it:
                            val = str(list(it.values())[0])
                        if val:
                            result.append(str(val))
                    else:
                        result.append(str(it))
                if len(result) >= 4:
                    print(f"  Geminiプロンプト生成成功 (key={key_label}): {len(result)}件", flush=True)
                    return result[:4]
        except Exception as e:
            safe = str(e).replace(api_key, "***") if api_key else str(e)
            print(f"  [警告] key={key_label} プロンプト生成失敗: {safe}", flush=True)
    print("  [警告] 全キーでプロンプト生成失敗。デフォルトプロンプトを使用します。", flush=True)
    return DEFAULT_PROMPTS


def save_image_bytes(content: bytes, filepath: str) -> bool:
    """バイト列を画像として検証しJPEGで保存する。"""
    try:
        img = Image.open(io.BytesIO(content)).convert("RGB")
        img.save(filepath, "JPEG", quality=92)
        return True
    except Exception as e:
        print(f"    [エラー] 画像として開けません: {e}", flush=True)
        return False


def generate_via_pixabay(api_key: str, query: str, filepath: str) -> bool:
    """Pixabay API で競馬写真を取得して保存する。"""
    params = {
        "key": api_key,
        "q": query,
        "image_type": "photo",
        "category": "animals",
        "min_width": 640,
        "per_page": 20,
        "safesearch": "true",
    }
    try:
        r = requests.get(PIXABAY_API_URL, params=params, timeout=30)
        print(f"    [Pixabay] status={r.status_code} query='{query}'", flush=True)
        if r.status_code != 200:
            print(f"    エラー: {r.status_code} {r.text[:200]}", flush=True)
            return False
        hits = r.json().get("hits", [])
        if not hits:
            print(f"    [Pixabay] 該当画像なし: {query}", flush=True)
            return False
        # ランダムに1枚選んでダウンロード
        hit = random.choice(hits)
        img_url = hit.get("largeImageURL") or hit.get("webformatURL")
        if not img_url:
            return False
        img_r = requests.get(img_url, timeout=30)
        if img_r.status_code == 200 and len(img_r.content) > 1000:
            if save_image_bytes(img_r.content, filepath):
                size_kb = len(img_r.content) // 1024
                print(f"    ✅ Pixabay成功: {filepath} ({size_kb}KB) [{hit.get('pageURL','')[:60]}]", flush=True)
                return True
    except Exception as e:
        print(f"    例外: {type(e).__name__}: {e}", flush=True)
    return False


def generate_via_huggingface(hf_tokens: list[str], prompt: str, filepath: str) -> bool:
    """HuggingFace Inference API (FLUX.1-schnell) で画像生成（複数トークンをローテーション）"""
    payload = {"inputs": prompt}
    for token_idx, hf_token in enumerate(hf_tokens):
        token_label = f"token[{token_idx + 1}/{len(hf_tokens)}]"
        headers = {"Authorization": f"Bearer {hf_token}"}
        for attempt in range(3):
            try:
                r = requests.post(HF_MODEL_URL, headers=headers, json=payload, timeout=120)
                print(f"    [HF] {token_label} status={r.status_code}", flush=True)
                if r.status_code == 200 and len(r.content) > 1000:
                    if save_image_bytes(r.content, filepath):
                        size_kb = len(r.content) // 1024
                        print(f"    ✅ HF成功: {filepath} ({size_kb}KB)", flush=True)
                        return True
                elif r.status_code == 402:
                    print(f"    [HF] {token_label} クレジット枯渇(402)。次のトークンへ切り替えます。", flush=True)
                    break
                elif r.status_code == 503:
                    wait = 30 * (attempt + 1)
                    print(f"    モデル読み込み中... {wait}秒待機", flush=True)
                    time.sleep(wait)
                else:
                    print(f"    エラー: {r.status_code} {r.text[:200]}", flush=True)
                    break
            except Exception as e:
                print(f"    例外: {type(e).__name__}: {e}", flush=True)
                break
    return False


def main() -> None:
    print("=== AI画像生成開始 ===", flush=True)
    ASSETS_DIR.mkdir(exist_ok=True)

    gemini_keys = [
        k for k in [
            os.environ.get("GEMINI_API_KEY", ""),
            os.environ.get("GEMINI_API_KEY_2", ""),
            os.environ.get("GEMINI_API_KEY_3", ""),
        ] if k
    ]
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "")
    hf_tokens = [
        k for k in [
            os.environ.get("HF_TOKEN", ""),
            os.environ.get("HF_TOKEN_2", ""),
            os.environ.get("HF_TOKEN_3", ""),
        ] if k
    ]

    print(f"Gemini APIキー: {len(gemini_keys)} 件ロード", flush=True)
    print(f"Pixabay: {'あり' if pixabay_key else 'なし（PIXABAY_API_KEY未設定）'}", flush=True)
    print(f"HuggingFace: {len(hf_tokens)} トークンロード", flush=True)

    if not pixabay_key and not hf_tokens:
        print("[警告] Pixabay・HuggingFace どちらも未設定。グラデーション背景にフォールバックします。", flush=True)
        sys.exit(0)

    # プロンプト生成（Pixabayクエリにも活用）
    try:
        news_items = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    except Exception:
        news_items = []

    print("  プロンプト生成中...", flush=True)
    prompts = get_prompts_from_gemini(gemini_keys, news_items) if gemini_keys else DEFAULT_PROMPTS
    for i, p in enumerate(prompts, 1):
        print(f"    [{i}] {p[:80]}", flush=True)

    # 画像生成（並列）: Pixabay → HF の順にフォールバック
    def generate_one(args):
        i, prompt = args
        out_path = str(ASSETS_DIR / f"ai_{i}.jpg")
        query = PIXABAY_QUERIES[(i - 1) % len(PIXABAY_QUERIES)]
        print(f"\n  [{i}/4] 画像取得中...", flush=True)

        # 1st: Pixabay（無料・実写写真）
        if pixabay_key:
            print(f"  [{i}] → Pixabay を試行 (query='{query}')", flush=True)
            if generate_via_pixabay(pixabay_key, query, out_path):
                return i, True
            print(f"  [{i}] → Pixabay失敗。", flush=True)

        # 2nd: HuggingFace
        if hf_tokens:
            print(f"  [{i}] → HuggingFace にフォールバック", flush=True)
            if generate_via_huggingface(hf_tokens, prompt, out_path):
                return i, True

        print(f"  [{i}] → 全手段で画像取得失敗", flush=True)
        return i, False

    failed = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(generate_one, (i, p)): i for i, p in enumerate(prompts, 1)}
        for future in as_completed(futures):
            i, ok = future.result()
            if not ok:
                failed.append(i)

    ai_files = sorted(ASSETS_DIR.glob("ai_*.jpg"))
    print(f"\n=== 結果: {4 - len(failed)}/4 枚生成 ===", flush=True)
    print(f"  生成ファイル: {[f.name for f in ai_files]}", flush=True)

    if failed:
        print(f"[警告] {len(failed)}枚の取得失敗（インデックス: {failed}）。generate_video.py がグラデーション背景で代替します。", flush=True)


if __name__ == "__main__":
    main()
