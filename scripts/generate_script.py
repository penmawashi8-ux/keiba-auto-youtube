#!/usr/bin/env python3
"""news.jsonの各記事ごとにGemini APIでナレーション脚本を生成し、output/script_N.txtに保存する。"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
PREFERRED_MODELS = [
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemma-3-4b-it",
    "gemma-3-1b-it",
]

SYSTEM_PROMPT = (
    "あなたはプロの競馬実況アナウンサーです。以下のニュースを元に、YouTube動画（90〜120秒程度）用のナレーション脚本を日本語で作成してください。\n\n"
    "【絶対に守るルール】\n"
    "- 「競馬ファンの皆さん」「皆さん」「みなさん」などの呼びかけは一切禁止\n"
    "- 「こんにちは」「どうも」などの挨拶も禁止\n"
    "- いきなりニュースの核心から始めること\n"
    "- ニュースに書かれていない事実は絶対に推測・追加・創作しないこと\n"
    "- 出走予定・出走登録・予定の記事は、予定として伝えること（勝利・着順・結果を推測しないこと）\n"
    "- 過去のレース結果が書かれていない場合は、結果に触れないこと\n\n"
    "構成：\n"
    "1. 冒頭：いきなりニュースの核心・最も重要な情報から入る（3〜4文）\n"
    "2. 中盤：詳細・背景・数字・関連情報など、ニュース記載の情報を丁寧に解説（5〜7文）\n"
    "3. 締め：視聴者が次のレースや続報を楽しみにできるような締めのコメント（2〜3文）\n\n"
    "合計で400〜500文字程度になるよう、十分な情報量で書いてください。\n"
    "テキストのみ出力し、ト書きや記号は不要です。"
)


def load_api_keys() -> list[str]:
    """環境変数から Gemini API キーを最大3件ロードする。"""
    keys = []
    for env_var in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"]:
        k = os.environ.get(env_var, "").strip()
        if k:
            keys.append(k)
    print(f"Gemini APIキー: {len(keys)} 件ロード")
    return keys


def list_available_models(api_key: str) -> list[str]:
    try:
        resp = requests.get(GEMINI_API_BASE, params={"key": api_key}, timeout=30)
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
        safe_msg = str(e).replace(api_key, "***")
        print(f"  [警告] ListModels失敗: {safe_msg}", file=sys.stderr)
        return []


class QuotaExceeded(Exception):
    pass


def call_gemini(api_key: str, model_name: str, prompt: str) -> str:
    url = f"{GEMINI_API_BASE}/{model_name}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 1200, "temperature": 0.7},
    }
    for attempt, wait in enumerate([0, 5, 15]):
        if wait:
            print(f"  {wait}秒待機後にリトライ... (attempt {attempt + 1})")
            time.sleep(wait)
        resp = requests.post(url, json=body, params={"key": api_key}, timeout=30)
        print(f"  HTTP {resp.status_code}")
        if resp.status_code == 429:
            err = resp.json().get("error", {})
            print(f"  [警告] 429 クォータ超過: {err.get('message','')[:200]}", file=sys.stderr)
            continue
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            safe_msg = str(e).replace(api_key, "***")
            raise requests.exceptions.HTTPError(safe_msg) from None
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as e:
            print(f"[エラー] レスポンス解析失敗: {e}\n{json.dumps(data)[:300]}", file=sys.stderr)
            sys.exit(1)
    raise QuotaExceeded(model_name)


def main() -> None:
    print("=== 脚本生成開始 ===")

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のためスキップします。")
        sys.exit(0)

    api_keys = load_api_keys()
    if not api_keys:
        print("[エラー] GEMINI_API_KEY が1件も設定されていません。", file=sys.stderr)
        sys.exit(1)

    # 最初のキーでモデル一覧を取得（全キー共通のモデルを使用）
    available = list_available_models(api_keys[0])
    candidates = [m for m in PREFERRED_MODELS if m in available]
    if not candidates:
        candidates = available[:3] if available else []
    if not candidates:
        print("[エラー] 利用可能なモデルが見つかりません。", file=sys.stderr)
        sys.exit(1)

    # (APIキー, モデル名) の全組み合わせリスト（キー優先でローテーション）
    key_model_pairs = [(key, model) for key in api_keys for model in candidates]
    print(f"試行組み合わせ数: {len(key_model_pairs)} (キー×モデル)")

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    def generate_one(args):
        i, item = args
        print(f"\n--- 記事[{i}]: {item['title'][:60]} ---")
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"【ニュース】\n"
            f"タイトル: {item['title']}\n"
            f"内容: {item.get('summary', '')[:600]}"
        )
        for key, model_name in key_model_pairs:
            key_label = f"***{key[-4:]}"
            print(f"[{i}] 使用: key={key_label} model={model_name}")
            try:
                script = call_gemini(key, model_name, prompt)
                out_path = Path(f"{OUTPUT_DIR}/script_{i}.txt")
                out_path.write_text(script, encoding="utf-8")
                print(f"[{i}]  → {out_path} 保存 ({len(script)}文字)")
                print(f"[{i}]  プレビュー: {script[:80]}...")
                return i, True
            except QuotaExceeded:
                print(f"[{i}]  [key={key_label} / {model_name}] クォータ超過。次へ切り替えます。", file=sys.stderr)
        print(f"[{i}] [エラー] 全キー・全モデルでクォータ超過。", file=sys.stderr)
        return i, False

    with ThreadPoolExecutor(max_workers=len(news_items)) as executor:
        futures = {executor.submit(generate_one, (i, item)): i for i, item in enumerate(news_items)}
        failed = []
        for future in as_completed(futures):
            i, ok = future.result()
            if not ok:
                failed.append(i)

    if failed:
        print(f"[エラー] 記事 {failed} のスクリプト生成失敗。", file=sys.stderr)
        sys.exit(1)

    print(f"\n{len(news_items)} 件の脚本を生成しました。")


if __name__ == "__main__":
    main()
