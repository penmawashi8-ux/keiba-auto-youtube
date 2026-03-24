#!/usr/bin/env python3
"""news.jsonを読み込みGemini APIで動画ナレーション脚本を生成してscript.txtに保存する。"""

import json
import os
import sys
from pathlib import Path

import requests

NEWS_JSON = "news.json"
SCRIPT_TXT = "script.txt"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# 優先モデル順（利用可能な最初のものを使用）
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
    "以下のニュースを元に、YouTubeショート動画（60秒以内）用のナレーション脚本を日本語で作成してください。"
    "視聴者が引き込まれる冒頭、ニュースの要点、締めの一言の構成にしてください。"
    "テキストのみ出力し、ト書きや記号は不要です。"
)


def build_news_text(news_items: list[dict]) -> str:
    lines = []
    for i, item in enumerate(news_items, 1):
        lines.append(f"【ニュース{i}】")
        lines.append(f"タイトル: {item['title']}")
        if item.get("summary"):
            lines.append(f"内容: {item['summary'][:300]}")
        lines.append("")
    return "\n".join(lines)


def list_available_models(api_key: str) -> list[str]:
    """利用可能でgenerateContentをサポートするモデル名のリストを返す。"""
    url = f"{GEMINI_API_BASE}"
    params = {"key": api_key}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        available = [
            m["name"].replace("models/", "")
            for m in models
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        print(f"利用可能モデル ({len(available)}個): {available[:10]}")
        return available
    except Exception as e:
        print(f"  [警告] ListModels失敗: {e}", file=sys.stderr)
        return []


def generate_script(news_items: list[dict]) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[エラー] 環境変数 GEMINI_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(1)

    # 利用可能なモデルを取得して優先順に選択
    available = list_available_models(api_key)
    model_name = next(
        (m for m in PREFERRED_MODELS if m in available),
        available[0] if available else None,
    )
    if not model_name:
        print("[エラー] 利用可能なモデルが見つかりません。", file=sys.stderr)
        sys.exit(1)
    print(f"使用モデル: {model_name}")

    news_text = build_news_text(news_items)
    full_prompt = f"{SYSTEM_PROMPT}\n\n以下の競馬ニュースを元に脚本を作成してください。\n\n{news_text}"

    url = f"{GEMINI_API_BASE}/{model_name}:generateContent"
    params = {"key": api_key}
    body = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"maxOutputTokens": 512, "temperature": 0.7},
    }

    print(f"Gemini REST API に脚本生成リクエスト送信中...")
    try:
        resp = requests.post(url, json=body, params=params, timeout=60)
        print(f"HTTP {resp.status_code}")
        if resp.status_code == 429:
            err = resp.json().get("error", {})
            print(f"[エラー] 429 レート制限/クォータ超過: {err.get('message','')[:300]}", file=sys.stderr)
            print("[ヒント] Google AI Studio (https://aistudio.google.com) で課金設定を確認してください。", file=sys.stderr)
            sys.exit(1)
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        print(f"[エラー] HTTP {e.response.status_code}: {e.response.text[:500]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[エラー] API呼び出し失敗: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        script = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        print(f"[エラー] レスポンス解析失敗: {e}\nレスポンス: {json.dumps(data, ensure_ascii=False)[:500]}", file=sys.stderr)
        sys.exit(1)

    return script


def main() -> None:
    print("=== 脚本生成開始 ===")

    news_path = Path(NEWS_JSON)
    if not news_path.exists():
        print(f"[エラー] {NEWS_JSON} が見つかりません。fetch_news.py を先に実行してください。", file=sys.stderr)
        sys.exit(1)

    news_items: list[dict] = json.loads(news_path.read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のため脚本生成をスキップします。")
        sys.exit(0)

    script = generate_script(news_items)

    Path(SCRIPT_TXT).write_text(script, encoding="utf-8")
    print(f"脚本を {SCRIPT_TXT} に保存しました。")
    print(f"--- 脚本プレビュー（先頭200文字） ---\n{script[:200]}\n---")


if __name__ == "__main__":
    main()
