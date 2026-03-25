#!/usr/bin/env python3
"""
generate_images.py - Gemini APIのnative image generationでAI画像を生成。
"""

import base64
import json
import os
import sys
from pathlib import Path

import requests

NEWS_JSON = "news.json"
ASSETS_DIR = Path("assets")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

DEFAULT_PROMPTS = [
    "cinematic photo of horses racing at sunset on a beautiful racecourse, dramatic lighting, high quality",
    "cinematic photo of jockey riding horse on racecourse, motion blur, high quality",
    "cinematic photo of horse racing crowd cheering at finish line, high quality",
    "cinematic photo of thoroughbred horse in paddock, golden hour, high quality",
]


def list_models(api_key: str) -> tuple[list[str], list[str]]:
    try:
        r = _safe_request("get", GEMINI_API_BASE, api_key, timeout=15)
        r.raise_for_status()
        models = r.json().get("models", [])
        all_names = [m["name"].replace("models/", "") for m in models]
        print(f"  利用可能モデル数: {len(all_names)}")
        print(f"  モデル一覧（先頭15件）: {all_names[:15]}")
        image_models = [n for n in all_names if any(k in n.lower() for k in ["image", "imagen"]) and n.startswith("gemini-")]
        print(f"  画像生成対応Geminiモデル: {image_models}")
        return all_names, image_models
    except Exception as e:
        print(f"  [警告] モデル一覧取得失敗: {type(e).__name__}: {str(e).replace(api_key, '***')}")
        return [], []


def _safe_request(method: str, url: str, api_key: str, **kwargs) -> requests.Response:
    """APIキーをURLパラメータに含めてリクエスト（例外メッセージにキーを含めない）"""
    try:
        return getattr(requests, method)(url, params={"key": api_key}, **kwargs)
    except requests.exceptions.RequestException as e:
        # URLからキーを除去してエラーメッセージを安全にする
        safe_msg = str(e).replace(api_key, "***")
        raise requests.exceptions.RequestException(safe_msg) from None


def get_prompts_from_gemini(api_key: str, news_items: list[dict]) -> list[str]:
    item = news_items[0] if news_items else {}
    title = item.get("title", "")
    body = item.get("body", item.get("summary", ""))[:300]
    prompt = (
        "以下の競馬ニュースの内容に合った、"
        "AI画像生成用の英語プロンプトを4つ作成してください。"
        "競馬場・馬・騎手・レースの雰囲気が伝わるシーンを描写してください。"
        "各プロンプトは「cinematic photo of [描写], horse racing, dramatic lighting, "
        "high quality」の形式で、50語以内で書いてください。"
        "JSON配列で返してください。余分なテキストは不要です。\n\n"
        f"タイトル: {title}\n本文: {body}"
    )
    url = f"{GEMINI_API_BASE}/gemini-2.5-flash:generateContent"
    try:
        r = _safe_request("post", url, api_key,
                          json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        prompts = json.loads(text)
        if isinstance(prompts, list) and len(prompts) >= 4:
            print(f"  Geminiプロンプト生成成功: {len(prompts)}件")
            return prompts[:4]
    except Exception as e:
        print(f"  [警告] プロンプト生成失敗: {type(e).__name__}: {str(e).replace(api_key, '***')}")
    return DEFAULT_PROMPTS


def generate_image_via_gemini(api_key: str, model: str, prompt: str, filepath: str) -> bool:
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    try:
        r = _safe_request("post", url, api_key, json=payload, timeout=120)
        print(f"  → status={r.status_code}")
        if r.status_code != 200:
            print(f"  エラー詳細: {r.text[:400]}")
            return False
        parts = r.json()["candidates"][0]["content"]["parts"]
        for part in parts:
            if "inlineData" in part:
                img_data = base64.b64decode(part["inlineData"]["data"])
                with open(filepath, "wb") as f:
                    f.write(img_data)
                print(f"  ✅ 成功: {filepath} ({len(img_data)//1024}KB)")
                return True
        print(f"  ❌ inlineDataなし。part keys: {[list(p.keys()) for p in parts]}")
    except Exception as e:
        print(f"  ❌ エラー: {type(e).__name__}: {str(e).replace(api_key, '***')}")
    return False


def main() -> None:
    print("=== AI画像生成開始 ===", flush=True)
    ASSETS_DIR.mkdir(exist_ok=True)

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("[エラー] GEMINI_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    # 利用可能モデルを確認
    print("  Geminiモデル一覧を取得中...", flush=True)
    all_models, image_models = list_models(api_key)

    # 画像生成対応モデルを選定（優先順）
    preferred_candidates = [
        "gemini-2.5-flash-image",
        "gemini-3.1-flash-image-preview",
        "gemini-3-pro-image-preview",
    ]
    if all_models:
        # 優先候補のうち利用可能なものを選ぶ
        image_gen_candidates = [m for m in preferred_candidates if m in all_models]
        if not image_gen_candidates and image_models:
            # フォールバック: API一覧から動的に取得したimage系モデルを使用
            image_gen_candidates = image_models[:3]
            print(f"  [フォールバック] 動的取得モデルを使用", flush=True)
        if not image_gen_candidates:
            image_gen_candidates = preferred_candidates  # 最終フォールバック
    else:
        image_gen_candidates = preferred_candidates
    print(f"  試行する画像生成モデル: {image_gen_candidates}", flush=True)

    # ニュース読み込み
    try:
        news_items = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [警告] news.json読み込み失敗: {e}")
        news_items = []

    # プロンプト生成
    print("  プロンプト生成中...", flush=True)
    prompts = get_prompts_from_gemini(api_key, news_items)
    for i, p in enumerate(prompts, 1):
        print(f"    [{i}] {p[:70]}")

    # 画像生成（各プロンプトで候補モデルを順に試す）
    failed = []
    for i, prompt in enumerate(prompts, 1):
        out_path = str(ASSETS_DIR / f"ai_{i}.jpg")
        print(f"\n  [{i}/4] 画像生成中...", flush=True)
        success = False
        for model in image_gen_candidates:
            print(f"    モデル: {model}")
            if generate_image_via_gemini(api_key, model, prompt, out_path):
                success = True
                break
        if not success:
            failed.append(i)

    ai_files = sorted(ASSETS_DIR.glob("ai_*.jpg"))
    print(f"\n=== 結果: {4 - len(failed)}/4 枚生成 ===", flush=True)
    print(f"  生成ファイル: {[f.name for f in ai_files]}")

    if failed:
        print(f"[エラー] {len(failed)}枚の生成失敗（インデックス: {failed}）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
