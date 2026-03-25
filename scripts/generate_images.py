#!/usr/bin/env python3
"""
generate_images.py - Gemini APIでAI画像を生成してassetsに保存する。

Gemini 2.0 Flash の native image generation を使用。
既存の GEMINI_API_KEY で動作する。
"""

import base64
import json
import os
import sys
from pathlib import Path

import requests

NEWS_JSON = "news.json"
ASSETS_DIR = Path("assets")

DEFAULT_PROMPTS = [
    "cinematic photo of horses racing at sunset on a beautiful racecourse, dramatic lighting, high quality",
    "cinematic photo of jockey riding horse on racecourse, motion blur, high quality",
    "cinematic photo of horse racing crowd cheering at finish line, high quality",
    "cinematic photo of thoroughbred horse in paddock, golden hour, high quality",
]


def get_prompts_from_gemini(api_key: str, news_items: list[dict]) -> list[str]:
    item = news_items[0] if news_items else {}
    title = item.get("title", "")
    body = item.get("body", item.get("summary", ""))[:300]
    news_text = f"タイトル: {title}\n本文: {body}"

    prompt = (
        "以下の競馬ニュースの内容に合った、"
        "AI画像生成用の英語プロンプトを4つ作成してください。"
        "競馬場・馬・騎手・レースの雰囲気が伝わるシーンを描写してください。"
        "各プロンプトは「cinematic photo of [描写], horse racing, dramatic lighting, "
        "high quality」の形式で、50語以内で書いてください。"
        "JSON配列で返してください。例: [\"prompt1\", \"prompt2\", \"prompt3\", \"prompt4\"]"
        "余分なテキストやマークダウンは不要です。\n\n"
        f"{news_text}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        prompts = json.loads(text)
        if isinstance(prompts, list) and len(prompts) >= 4:
            print(f"  Geminiからプロンプト {len(prompts)} 件取得")
            return prompts[:4]
        print("  [警告] Geminiの返却形式が不正。デフォルトプロンプトを使用します。")
    except Exception as e:
        print(f"  [警告] Geminiプロンプト生成失敗: {e} → デフォルトプロンプトを使用します。")

    return DEFAULT_PROMPTS


def generate_image_via_gemini(api_key: str, prompt: str, filepath: str) -> bool:
    """Gemini 2.0 Flash の native image generation でAI画像を生成して保存する。"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-preview-image-generation:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    print(f"  Gemini画像生成リクエスト送信...")
    try:
        r = requests.post(url, json=payload, timeout=120)
        print(f"  → status={r.status_code}")
        if r.status_code != 200:
            print(f"  レスポンス: {r.text[:300]}")
            return False
        parts = r.json()["candidates"][0]["content"]["parts"]
        for part in parts:
            if "inlineData" in part:
                img_data = base64.b64decode(part["inlineData"]["data"])
                with open(filepath, "wb") as f:
                    f.write(img_data)
                size_kb = len(img_data) // 1024
                print(f"  ✅ 画像生成成功: {filepath} ({size_kb}KB)")
                return True
        print(f"  ❌ レスポンスにinlineDataなし。parts: {[list(p.keys()) for p in parts]}")
    except Exception as e:
        print(f"  ❌ エラー: {type(e).__name__}: {e}")
    return False


def main() -> None:
    print("=== AI画像生成開始（Gemini native image generation）===")
    ASSETS_DIR.mkdir(exist_ok=True)

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("[エラー] GEMINI_API_KEY が未設定です。", file=sys.stderr)
        sys.exit(1)

    # news.json 読み込み
    try:
        news_items = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [警告] news.json 読み込み失敗: {e}")
        news_items = []

    # Geminiでプロンプト生成
    print("  Gemini APIでプロンプト生成中...")
    prompts = get_prompts_from_gemini(api_key, news_items)
    for i, p in enumerate(prompts, 1):
        print(f"    プロンプト{i}: {p[:70]}...")

    # Gemini native image generation で画像生成
    failed = []
    for i, prompt in enumerate(prompts, 1):
        out_path = str(ASSETS_DIR / f"ai_{i}.jpg")
        print(f"\n  [{i}/4] 画像生成: {prompt[:60]}...")
        if not generate_image_via_gemini(api_key, prompt, out_path):
            failed.append(i)

    ai_files = sorted(ASSETS_DIR.glob("ai_*.jpg"))
    print(f"\n=== 結果: {4 - len(failed)}/4 枚生成 ===")
    print(f"  生成ファイル: {[f.name for f in ai_files]}")

    if failed:
        print(f"  [エラー] {len(failed)}枚の生成に失敗しました（インデックス: {failed}）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
