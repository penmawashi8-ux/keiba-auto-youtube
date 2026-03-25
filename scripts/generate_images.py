#!/usr/bin/env python3
"""
generate_images.py - 無料で競馬AI画像を生成する。
HuggingFace FLUX.1-schnell (HF_TOKEN が設定されている場合) を使用。
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

NEWS_JSON = "news.json"
ASSETS_DIR = Path("assets")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

HF_MODEL_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

DEFAULT_PROMPTS = [
    "cinematic photo of horses racing at sunset on a beautiful racecourse, dramatic lighting, high quality",
    "cinematic photo of jockey riding horse on racecourse, motion blur, high quality",
    "cinematic photo of horse racing crowd cheering at finish line, high quality",
    "cinematic photo of thoroughbred horse in paddock, golden hour, high quality",
]


def get_prompts_from_gemini(api_key: str, news_items: list[dict]) -> list[str]:
    """Geminiテキストモデルで画像プロンプトを生成（失敗時はデフォルト使用）"""
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
    try:
        r = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt_text}]}]},
            params={"key": api_key},
            timeout=30,
        )
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        prompts = json.loads(text)
        if isinstance(prompts, list) and len(prompts) >= 4:
            # 要素が文字列でない場合（dictなど）は文字列に変換
            result = []
            for item in prompts:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(item, dict):
                    # "prompt"/"text"/"description"などのキーを探す
                    val = next((item[k] for k in ("prompt", "text", "description", "content") if k in item), None)
                    if val is None and item:
                        val = str(list(item.values())[0])
                    if val:
                        result.append(str(val))
                else:
                    result.append(str(item))
            if len(result) >= 4:
                print(f"  Geminiプロンプト生成成功: {len(result)}件", flush=True)
                return result[:4]
    except Exception as e:
        safe = str(e).replace(api_key, "***") if api_key else str(e)
        print(f"  [警告] プロンプト生成失敗: {safe}", flush=True)
    return DEFAULT_PROMPTS


def generate_via_huggingface(hf_token: str, prompt: str, filepath: str) -> bool:
    """HuggingFace Inference API (FLUX.1-schnell) で画像生成"""
    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {"inputs": prompt}
    for attempt in range(4):
        try:
            r = requests.post(HF_MODEL_URL, headers=headers, json=payload, timeout=120)
            print(f"    [HF] status={r.status_code}", flush=True)
            if r.status_code == 200 and len(r.content) > 1000:
                Path(filepath).write_bytes(r.content)
                print(f"    ✅ HF成功: {filepath} ({len(r.content)//1024}KB)", flush=True)
                return True
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

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    hf_token = os.environ.get("HF_TOKEN", "")

    # プロンプト生成
    try:
        news_items = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    except Exception:
        news_items = []

    print("  プロンプト生成中...", flush=True)
    prompts = get_prompts_from_gemini(gemini_key, news_items) if gemini_key else DEFAULT_PROMPTS
    for i, p in enumerate(prompts, 1):
        print(f"    [{i}] {p[:80]}", flush=True)

    # 画像生成（並列）
    def generate_one(args):
        i, prompt = args
        out_path = str(ASSETS_DIR / f"ai_{i}.jpg")
        print(f"\n  [{i}/4] 画像生成中...", flush=True)

        # HuggingFace FLUX.1-schnell
        if hf_token:
            print(f"  [{i}] → HuggingFace FLUX.1-schnell を試行", flush=True)
            if generate_via_huggingface(hf_token, prompt, out_path):
                return i, True

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
        print(f"[エラー] {len(failed)}枚の生成失敗（インデックス: {failed}）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
