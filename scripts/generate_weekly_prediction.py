#!/usr/bin/env python3
"""週間重賞予想脚本を Gemini で生成する。

入力: race_list.json（fetch_weekly_race.py が生成）
出力:
  output/script_0.txt, script_1.txt, ...  ← generate_audio.py が読む
  news.json                                ← landscape_video.py・upload_landscape_youtube.py が読む

脚本フォーマット（landscape_video.py がセクション単位でパース）:
  【レース概要】
  〇〇競馬場・芝〇〇m。コースの特徴説明。

  【注目馬①：馬名】
  近走成績・強み・懸念点。

  ...

  【本命予想】
  本命は馬名。対抗は馬名。穴馬は馬名。
  予想根拠。
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

RACE_LIST_JSON = "race_list.json"
OUTPUT_DIR = "output"
NEWS_JSON = "news.json"

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]
RATE_LIMIT_WAITS = [30, 60]
NON_RETRY_STATUS = {403, 404}


def load_api_keys() -> list[str]:
    keys = []
    for env_var in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"]:
        k = os.environ.get(env_var, "").strip()
        if k:
            keys.append(k)
    print(f"[診断] Gemini APIキー: {len(keys)} 件", file=sys.stderr)
    return keys


def call_gemini(api_keys: list[str], prompt: str, temperature: float = 0.7) -> str:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 8192},
    }
    pairs = [(key, model) for key in api_keys for model in PREFERRED_MODELS]

    for api_key, model in pairs:
        key_label = f"{api_key[:8]}..."
        url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"
        waits = [0] + RATE_LIMIT_WAITS

        for attempt, wait in enumerate(waits):
            if wait:
                print(f"  [{key_label} {model}] 429 → {wait}秒待機...", file=sys.stderr)
                time.sleep(wait)
            try:
                resp = requests.post(url, json=payload, timeout=60)
                if resp.status_code in NON_RETRY_STATUS:
                    break
                if resp.status_code == 429:
                    if attempt < len(waits) - 1:
                        continue
                    break
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise ValueError("candidates が空")
                return candidates[0]["content"]["parts"][0]["text"].strip()
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in NON_RETRY_STATUS:
                    break
                if status == 429 and attempt < len(waits) - 1:
                    continue
                break
            except Exception as e:
                safe_msg = str(e).replace(api_key, "***")
                print(f"  [{key_label} {model}] エラー: {safe_msg}", file=sys.stderr)
                break

        time.sleep(3)

    raise RuntimeError("Gemini API: 全キー×全モデルで失敗しました。")


def build_prompt(race_info: dict) -> str:
    race_name = race_info["race_name"]
    grade     = race_info.get("grade", "")
    date      = race_info.get("date", "今週末")
    venue     = race_info.get("venue", "")
    distance  = race_info.get("distance", "")
    snippets  = race_info.get("news_snippets", [])

    course_info = f"{venue} {distance}".strip()
    snippets_text = "\n".join(f"  - {s}" for s in snippets[:10]) if snippets else "  （なし）"
    is_overseas = race_info.get("source") == "overseas_search" or venue == "海外"
    expert_desc = "海外競馬に精通した競馬予想解説者" if is_overseas else "日本中央競馬（JRA）の熟練した競馬予想解説者"

    return f"""\
あなたは{expert_desc}です。
以下の重賞レース情報をもとに、YouTube横向き動画（5〜8分）用の予想解説脚本を作成してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
レース情報
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
レース名: {race_name}（{grade}）
開催: {date}{f"　{course_info}" if course_info else ""}

関連ニュース見出し:
{snippets_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
脚本の必須ルール
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 全体 1500〜2500 文字（音声読み上げで 5〜8 分が目安）
2. 以下のセクション構成で書く。各セクションは空行で区切ること。
   【レース概要】
   【注目馬①：馬名】
   【注目馬②：馬名】
   （出走馬の情報が不明な場合は過去の有力馬や傾向から 3〜5 頭を取り上げる）
   ...
   【本命予想】
3. 各セクションの 1 行目は必ず【...】のヘッダー行にする
4. 本文の文は句点（。）で終わること
5. 具体的な数字・レース名・騎手名・前走成績を積極的に入れる
6. 感情語より事実と分析で語る
7. 最後の【本命予想】セクションで本命・対抗・穴馬を明示し、その根拠を述べる
8. 挨拶・呼びかけ（「みなさん」「こんにちは」など）は一切禁止
9. 情報が不確かな箇所は「〜とみられる」「〜が想定される」など推測表現にとどめる
   （事実と異なる情報を断定的に書かない）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
出力形式（余分な説明・コメント不要。脚本本文のみ出力）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def generate_metadata_prompt(race_name: str, grade: str, script: str) -> str:
    return f"""\
以下の競馬予想解説脚本から YouTube 動画用のメタデータを生成してください。

レース名: {race_name}（{grade}）

【脚本冒頭】
{script[:500]}

以下の形式のみで出力してください:
VIDEO_TITLE: [60文字以内のYouTubeタイトル。「【重賞予想】」で始め、レース名・年・ポイントを含める]
TAGS: [レース名, 競馬予想, G1, 馬名など カンマ区切り 8〜12個]
"""


def generate_one(race_info: dict, idx: int, api_keys: list[str]) -> dict:
    """1レース分の脚本・メタデータを生成して news_item を返す。"""
    race_name = race_info["race_name"]
    grade     = race_info.get("grade", "G1")

    print(f"\n[{idx}] 脚本生成中: {race_name}（{grade}）...")
    prompt = build_prompt(race_info)
    script = call_gemini(api_keys, prompt, temperature=0.75)
    print(f"  脚本生成完了: {len(script)} 文字", file=sys.stderr)

    script_path = Path(OUTPUT_DIR) / f"script_{idx}.txt"
    script_path.write_text(script, encoding="utf-8")
    print(f"  ✅ {script_path}")

    print(f"  [10秒待機（レート制限対策）...]", file=sys.stderr)
    time.sleep(10)
    print(f"  メタデータ生成中...", file=sys.stderr)
    meta_resp = call_gemini(api_keys, generate_metadata_prompt(race_name, grade, script), temperature=0.5)

    meta: dict[str, str] = {}
    for line in meta_resp.strip().splitlines():
        for sep in (":", "："):
            if sep in line:
                k, _, v = line.partition(sep)
                meta[k.strip()] = v.strip()
                break

    video_title = meta.get("VIDEO_TITLE", f"【重賞予想】{race_name} 2026年 徹底分析")
    tags_raw    = meta.get("TAGS", "")
    tags        = [t.strip() for t in tags_raw.split(",") if t.strip()]
    if race_name not in tags:
        tags.insert(0, race_name)

    return {
        "id":         race_info.get("race_id", f"weekly_prediction_{race_name}_{race_info.get('date', '')}"),
        "title":      video_title,
        "race_name":  race_name,
        "grade":      grade,
        "date":       race_info.get("date", ""),
        "venue":      race_info.get("venue", ""),
        "distance":   race_info.get("distance", ""),
        "summary":    f"{race_name}（{grade}）予想解説",
        "url":        "",
        "tags_extra": tags,
    }


def main() -> None:
    if not Path(RACE_LIST_JSON).exists():
        print(f"[エラー] {RACE_LIST_JSON} が見つかりません。fetch_weekly_race.py を先に実行してください。", file=sys.stderr)
        sys.exit(1)

    race_list = json.loads(Path(RACE_LIST_JSON).read_text(encoding="utf-8"))
    if not race_list:
        print("race_list.json が空です。スキップします。")
        sys.exit(0)

    print(f"対象レース: {len(race_list)} 件")

    api_keys = load_api_keys()
    if not api_keys:
        print("[エラー] GEMINI_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(1)

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    # APIキーをレースごとにローテーションして並列実行（レート制限分散）
    def _generate_with_rotated_key(args: tuple) -> dict:
        idx, race_info = args
        n = len(api_keys)
        rotated = api_keys[idx % n:] + api_keys[:idx % n]
        return generate_one(race_info, idx, rotated)

    max_workers = min(len(api_keys), len(race_list), 3)
    print(f"並列ワーカー数: {max_workers}")
    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_generate_with_rotated_key, (idx, race_info)): idx
            for idx, race_info in enumerate(race_list)
        }
        for future in as_completed(futures):
            idx = futures[future]
            race_name = race_list[idx]["race_name"]
            try:
                results[idx] = future.result()
            except Exception as e:
                print(f"[エラー] {race_name} の脚本生成失敗: {e}", file=sys.stderr)

    news_items = [results[i] for i in sorted(results)]
    if not news_items:
        print("[エラー] 全レースの脚本生成に失敗しました。", file=sys.stderr)
        sys.exit(1)

    Path(NEWS_JSON).write_text(
        json.dumps(news_items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✅ {NEWS_JSON}: {len(news_items)} 件")
    for item in news_items:
        print(f"  - 「{item['title']}」")


if __name__ == "__main__":
    main()
