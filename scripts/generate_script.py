#!/usr/bin/env python3
"""news.jsonの各記事ごとにGemini APIでナレーション脚本を生成し、output/script_N.txtに保存する。"""

import json
import os
import sys
from pathlib import Path

import requests

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemma-3-4b-it",
    "gemma-3-1b-it",
]

SYSTEM_PROMPT = (
    "あなたはプロの競馬実況アナウンサーです。"
    "以下のニュース1件を元に、YouTubeショート動画（45〜60秒）用のナレーション脚本を日本語で作成してください。"
    "視聴者が引き込まれる冒頭、ニュースの要点、締めの一言の構成にしてください。"
    "必ず最後まで完結した文章で終わらせてください。途中で終わらないようにしてください。"
    "テキストのみ出力し、ト書きや記号は不要です。"
)


def list_available_models(api_key: str) -> list[str]:
    params = {"key": api_key}
    try:
        resp = requests.get(GEMINI_API_BASE, params=params, timeout=30)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        available = [
            m["name"].replace("models/", "")
            for m in models
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        print(f"利用可能モデル: {available[:6]}")
        return available
    except Exception as e:
        print(f"  [警告] ListModels失敗: {e}", file=sys.stderr)
        return []


def call_gemini(api_key: str, model_name: str, prompt: str) -> str:
    url = f"{GEMINI_API_BASE}/{model_name}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.7},
    }
    resp = requests.post(url, json=body, params={"key": api_key}, timeout=60)
    print(f"  HTTP {resp.status_code}")
    if resp.status_code == 429:
        err = resp.json().get("error", {})
        print(f"[エラー] 429 クォータ超過: {err.get('message','')[:200]}", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        print(f"[エラー] レスポンス解析失敗: {e}\n{json.dumps(data)[:300]}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    print("=== 脚本生成開始 ===")

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のためスキップします。")
        sys.exit(0)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[エラー] GEMINI_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(1)

    available = list_available_models(api_key)
    model_name = next(
        (m for m in PREFERRED_MODELS if m in available),
        available[0] if available else None,
    )
    if not model_name:
        print("[エラー] 利用可能なモデルが見つかりません。", file=sys.stderr)
        sys.exit(1)
    print(f"使用モデル: {model_name}")

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    for i, item in enumerate(news_items):
        print(f"\n--- 記事[{i}]: {item['title'][:60]} ---")
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"【ニュース】\n"
            f"タイトル: {item['title']}\n"
            f"内容: {item.get('summary', '')[:300]}"
        )
        script = call_gemini(api_key, model_name, prompt)
        out_path = Path(f"{OUTPUT_DIR}/script_{i}.txt")
        out_path.write_text(script, encoding="utf-8")
        print(f"  → {out_path} 保存 ({len(script)}文字)")
        print(f"  プレビュー: {script[:80]}...")

    print(f"\n{len(news_items)} 件の脚本を生成しました。")


if __name__ == "__main__":
    main()
