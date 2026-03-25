#!/usr/bin/env python3
"""
generate_images.py - ニュース内容からGemini APIでプロンプトを生成し、
Pollinations.ai APIでAI画像を生成してassetsに保存する。
"""

import json
import os
import random
import sys
from pathlib import Path
from urllib.parse import quote

import requests

NEWS_JSON = "news.json"
ASSETS_DIR = Path("assets")

DEFAULT_PROMPTS = [
    "cinematic photo of horses racing at sunset, dramatic lighting, high quality",
    "cinematic photo of jockey riding horse on racecourse, motion blur, high quality",
    "cinematic photo of horse racing crowd cheering at finish line, high quality",
    "cinematic photo of thoroughbred horse in paddock, golden hour, high quality",
]


def get_prompts_from_gemini(news_items: list[dict]) -> list[str]:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("  [警告] GEMINI_API_KEY が未設定。デフォルトプロンプトを使用します。")
        return DEFAULT_PROMPTS

    # 最初のニュースのタイトルと本文を使用
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
        # マークダウンコードブロックを除去
        text = text.replace("```json", "").replace("```", "").strip()
        prompts = json.loads(text)
        if isinstance(prompts, list) and len(prompts) >= 4:
            print(f"  Geminiからプロンプト {len(prompts)} 件取得")
            return prompts[:4]
        print("  [警告] Geminiの返却形式が不正。デフォルトプロンプトを使用します。")
    except Exception as e:
        print(f"  [警告] Geminiプロンプト生成失敗: {e} → デフォルトプロンプトを使用します。")

    return DEFAULT_PROMPTS


def generate_image(prompt: str, filepath: str) -> bool:
    encoded = quote(prompt)
    seed = random.randint(1, 9999)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1920&model=flux&nologo=true&seed={seed}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://pollinations.ai/",
    }
    print(f"  リクエスト: {url[:80]}...")
    try:
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code == 200 and len(r.content) > 10000:
            with open(filepath, "wb") as f:
                f.write(r.content)
            size_kb = len(r.content) // 1024
            print(f"  ✅ 画像生成成功: {filepath} ({size_kb}KB)")
            return True
        print(f"  ❌ 失敗: status={r.status_code} size={len(r.content)}")
    except Exception as e:
        print(f"  ❌ エラー: {e}")
    return False


def main() -> None:
    print("=== AI画像生成開始 ===")
    ASSETS_DIR.mkdir(exist_ok=True)

    # news.json 読み込み
    try:
        news_items = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [警告] news.json 読み込み失敗: {e}")
        news_items = []

    if not news_items:
        print("  ニュースが0件のためデフォルトプロンプトを使用します。")

    # Geminiでプロンプト生成
    print("  Gemini APIでプロンプト生成中...")
    prompts = get_prompts_from_gemini(news_items)
    for i, p in enumerate(prompts, 1):
        print(f"    プロンプト{i}: {p[:60]}...")

    # Pollinations.ai で画像生成（1枚でも失敗したらexit(1)）
    failed = []
    for i, prompt in enumerate(prompts, 1):
        out_path = str(ASSETS_DIR / f"ai_{i}.jpg")
        print(f"\n  [{i}/4] 画像生成: {prompt[:50]}...")
        if not generate_image(prompt, out_path):
            failed.append(i)

    ai_files = sorted(ASSETS_DIR.glob("ai_*.jpg"))
    print(f"\n=== 結果: {4 - len(failed)}/4 枚生成 ===")
    print(f"  生成ファイル: {[f.name for f in ai_files]}")

    if failed:
        print(f"  [エラー] {len(failed)}枚の生成に失敗しました（ai_{failed}）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
