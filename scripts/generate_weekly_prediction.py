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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

RACE_LIST_JSON = "race_list.json"
OUTPUT_DIR = "output"
NEWS_JSON = "news.json"

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
PREFERRED_MODELS = [
    "gemini-2.0-flash-lite",  # 30 RPM: 最もレート上限が高い
    "gemini-2.0-flash",       # 15 RPM
    "gemini-2.5-flash",       # 10 RPM: 最も制限が厳しい
]
RATE_LIMIT_WAITS = [30, 60]
NON_RETRY_STATUS = {403, 404}

# API呼び出しを直列化して同時アクセスによるレート制限を防ぐ
_api_lock = threading.Lock()
_INTER_CALL_SLEEP = 5  # 成功後にAPIを休ませる秒数


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

    with _api_lock:
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
                    result = candidates[0]["content"]["parts"][0]["text"].strip()
                    time.sleep(_INTER_CALL_SLEEP)
                    return result
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

        raise RuntimeError("Gemini API: 全キー×全モデルで失敗しました。")


def build_combined_prompt(race_info: dict) -> str:
    """脚本とメタデータを1回のAPI呼び出しで生成するプロンプト。"""
    race_name = race_info["race_name"]
    grade     = race_info.get("grade", "")
    date      = race_info.get("date", "今週末")
    venue     = race_info.get("venue", "")
    distance  = race_info.get("distance", "")
    snippets  = race_info.get("news_snippets", [])

    course_info = f"{venue} {distance}".strip()
    snippets_text = "\n".join(f"  - {s}" for s in snippets[:10]) if snippets else "  （なし）"
    is_overseas = race_info.get("overseas", False) or race_info.get("source") == "overseas_search"
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

   【レース紹介】
   レース名・グレード・開催場所・距離・今年の注目ポイントを紹介。

   【レース傾向・過去の結果】
   過去数年の優勝馬・好走馬の傾向・有利な脚質・枠順傾向など具体的なデータを示す。

   【本命馬：馬名】
   本命馬の近走成績・強み・このレースに向く理由を詳述。

   【対抗馬：馬名】
   対抗馬の近走成績・脅威となるポイント・不安要素。

   【消し馬】
   データや適性から消せる人気馬とその根拠。1〜2頭を具体的に挙げる。

   【エンディング】
   予想まとめ（本命・対抗・消しの一言）＋チャンネル登録とコメントへの呼びかけ。

3. 各セクションの 1 行目は必ず【...】のヘッダー行にする
4. 本文の文は句点（。）で終わること
5. 具体的な数字・レース名・騎手名・前走成績を積極的に入れる
6. 感情語より事実と分析で語る
7. 情報が不確かな箇所は「〜とみられる」「〜が想定される」など推測表現にとどめる
8. 挨拶・呼びかけ（「みなさん」「こんにちは」など）は禁止。【エンディング】内のCTAは除く

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
出力形式
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
脚本本文を出力した後、必ず以下の3行を最後に追加してください（説明・コメント不要）:

VIDEO_TITLE: [60文字以内のYouTubeタイトル。「【重賞予想】」で始め、レース名・年・ポイントを含める]
THUMBNAIL_HOOK: [サムネイル用の煽り文句。15文字以内。「〇〇は危険！？」「これが本命だ！」「人気馬が消える！」など視聴者の興味を引く一文]
TAGS: [レース名, 競馬予想, G1, 馬名など カンマ区切り 8〜12個]
"""


def _parse_combined_response(raw: str, race_name: str, grade: str) -> tuple[str, dict]:
    """脚本+メタデータの統合レスポンスをスクリプト本文とmetaに分離する。"""
    lines = raw.splitlines()
    meta: dict[str, str] = {}
    script_end = len(lines)

    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        matched = False
        for key in ("TAGS", "VIDEO_TITLE", "THUMBNAIL_HOOK"):
            if line.startswith(f"{key}:") or line.startswith(f"{key}："):
                for sep in (":", "："):
                    if sep in line:
                        _, _, v = line.partition(sep)
                        meta[key] = v.strip()
                        break
                script_end = i
                matched = True
                break
        if not matched and meta:
            break

    script = "\n".join(lines[:script_end]).strip()
    return script, meta


def generate_one(race_info: dict, idx: int, api_keys: list[str]) -> dict:
    """1レース分の脚本・メタデータを生成して news_item を返す。"""
    race_name = race_info["race_name"]
    grade     = race_info.get("grade", "G1")

    print(f"\n[{idx}] 脚本生成中: {race_name}（{grade}）...")
    prompt = build_combined_prompt(race_info)
    combined = call_gemini(api_keys, prompt, temperature=0.75)
    print(f"  レスポンス取得: {len(combined)} 文字", file=sys.stderr)

    script, meta = _parse_combined_response(combined, race_name, grade)

    script_path = Path(OUTPUT_DIR) / f"script_{idx}.txt"
    script_path.write_text(script, encoding="utf-8")
    print(f"  ✅ {script_path} ({len(script)} 文字)")

    video_title     = meta.get("VIDEO_TITLE", f"【重賞予想】{race_name} 2026年 徹底分析")
    thumbnail_hook  = meta.get("THUMBNAIL_HOOK", f"{race_name}を徹底分析！")
    tags_raw        = meta.get("TAGS", "")
    tags            = [t.strip() for t in tags_raw.split(",") if t.strip()]
    if race_name not in tags:
        tags.insert(0, race_name)

    return {
        "id":             race_info.get("race_id", f"weekly_prediction_{race_name}_{race_info.get('date', '')}"),
        "title":          video_title,
        "thumbnail_hook": thumbnail_hook,
        "race_name":      race_name,
        "grade":          grade,
        "date":           race_info.get("date", ""),
        "venue":          race_info.get("venue", ""),
        "distance":       race_info.get("distance", ""),
        "summary":        f"{race_name}（{grade}）予想解説",
        "url":            "",
        "tags_extra":     tags,
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

    # 直前の fetch_weekly_race.py がGemini APIを使用したため短い回復待機
    print("Geminiレート制限回復待機（15秒）...", file=sys.stderr)
    time.sleep(15)

    api_keys = load_api_keys()
    if not api_keys:
        print("[エラー] GEMINI_API_KEY が設定されていません。", file=sys.stderr)
        sys.exit(1)

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    def _generate_with_rotated_key(args: tuple) -> dict:
        idx, race_info = args
        n = len(api_keys)
        rotated = api_keys[idx % n:] + api_keys[:idx % n]
        return generate_one(race_info, idx, rotated)

    # _api_lock で直列化しているため workers を増やしても並列API呼び出しは起きない。
    # ファイルI/O等の非API部分だけ並列化する。
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
