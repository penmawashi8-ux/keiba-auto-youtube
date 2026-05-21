#!/usr/bin/env python3
"""
オークス2026 枠順確定予想動画生成スクリプト。

フロー:
  1. 予想上位10頭をGemini+Google検索で個別調査（事実データのみ取得）
  2. 取得した事実データのみを素材にGeminiで予想ナレーション脚本を生成
  3. news.json / output/script_0.txt / 背景画像を出力
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

# ── Gemini API ───────────────────────────────────────────────────────────────
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
SEARCH_CAPABLE_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
PREFERRED_MODELS = SEARCH_CAPABLE_MODELS + ["gemma-3-4b-it", "gemma-3-1b-it"]
RATE_LIMIT_WAITS = [30, 60]
NON_RETRY_STATUS = {403, 404}

NEWS_JSON  = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"

# ── 出走表（枠順確定・2026-05-24）────────────────────────────────────────────
ENTRIES = [
    {"frame": 1, "number":  1, "name": "ミツカネベネラ",    "jockey": "横山和生",   "odds_rank": 15},
    {"frame": 1, "number":  2, "name": "レイクラシック",    "jockey": "M.ディー",   "odds_rank": 11},
    {"frame": 2, "number":  3, "name": "アランカール",      "jockey": "武豊",       "odds_rank":  3},
    {"frame": 2, "number":  4, "name": "ロングトールサリー","jockey": "戸崎圭太",   "odds_rank": 17},
    {"frame": 3, "number":  5, "name": "リアライズルミナス","jockey": "津村明秀",   "odds_rank": 16},
    {"frame": 3, "number":  6, "name": "ロンギングセリーヌ","jockey": "石橋脩",     "odds_rank": 14},
    {"frame": 4, "number":  7, "name": "スタニングレディ",  "jockey": "三浦皇成",   "odds_rank": 18},
    {"frame": 4, "number":  8, "name": "スマートプリエール","jockey": "原優介",     "odds_rank":  9},
    {"frame": 5, "number":  9, "name": "トリニティ",        "jockey": "西村淳也",   "odds_rank":  8},
    {"frame": 5, "number": 10, "name": "スターアニス",      "jockey": "松山弘平",   "odds_rank":  1},
    {"frame": 6, "number": 11, "name": "アメティスタ",      "jockey": "横山武史",   "odds_rank": 10},
    {"frame": 6, "number": 12, "name": "ドリームコア",      "jockey": "C.ルメール", "odds_rank":  4},
    {"frame": 7, "number": 13, "name": "エンネ",            "jockey": "坂井瑠星",   "odds_rank":  5},
    {"frame": 7, "number": 14, "name": "ソルパッサーレ",    "jockey": "浜中俊",     "odds_rank": 13},
    {"frame": 7, "number": 15, "name": "アンジュドジョワ",  "jockey": "岩田望来",   "odds_rank":  7},
    {"frame": 8, "number": 16, "name": "ジュウリョクピエロ","jockey": "今村聖奈",   "odds_rank":  6},
    {"frame": 8, "number": 17, "name": "スウィートハピネス","jockey": "高杉史麒",   "odds_rank": 12},
    {"frame": 8, "number": 18, "name": "ラフターラインズ",  "jockey": "D.レーン",   "odds_rank":  2},
]

# 調査対象: 予想オッズ上位10頭（穴馬候補も含む）
RESEARCH_TARGETS = sorted(
    [e for e in ENTRIES if e["odds_rank"] <= 10],
    key=lambda x: x["odds_rank"],
)


# ── Gemini API 呼び出し ──────────────────────────────────────────────────────
def load_api_keys() -> list[str]:
    keys = []
    for v in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"]:
        k = os.environ.get(v, "").strip()
        if k:
            keys.append(k)
    print(f"[Gemini] APIキー {len(keys)} 件", file=sys.stderr)
    return keys


def call_gemini(api_keys: list[str], prompt: str, temperature: float = 0.1,
                extra_tools: list | None = None,
                model_list: list[str] | None = None) -> str:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 4096},
    }
    if extra_tools:
        payload["tools"] = extra_tools

    models = model_list or PREFERRED_MODELS
    pairs  = [(k, m) for k in api_keys for m in models]

    for api_key, model in pairs:
        url   = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"
        waits = [0] + RATE_LIMIT_WAITS
        for attempt, wait in enumerate(waits):
            if wait:
                time.sleep(wait)
            try:
                resp = requests.post(url, json=payload, timeout=60)
                if resp.status_code in NON_RETRY_STATUS:
                    break
                if resp.status_code in (429, 503):
                    if attempt < len(waits) - 1:
                        continue
                    break
                resp.raise_for_status()
                candidates = resp.json().get("candidates", [])
                if not candidates:
                    raise ValueError("candidates が空")
                return candidates[0]["content"]["parts"][0]["text"].strip()
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else 0
                if status in NON_RETRY_STATUS:
                    break
                if status == 429 and attempt < len(waits) - 1:
                    continue
                break
            except Exception as e:
                print(f"  [{model}] {str(e)[:60]}", file=sys.stderr)
                break
        time.sleep(3)

    raise RuntimeError("Gemini API: 全キー×全モデルで失敗")


def call_gemini_with_search(api_keys: list[str], prompt: str) -> str:
    """Google検索グラウンディング付き（検索対応モデルのみ・失敗時は通常モードへ）。"""
    try:
        return call_gemini(api_keys, prompt, temperature=0.0,
                           extra_tools=[{"google_search": {}}],
                           model_list=SEARCH_CAPABLE_MODELS)
    except RuntimeError:
        print("  [警告] 検索モデル全滅 → 通常モードにフォールバック", file=sys.stderr)
        return call_gemini(api_keys, prompt, temperature=0.0)


# ── Phase 1: 各馬の事実データ取得 ────────────────────────────────────────────
def fetch_horse_facts(api_keys: list[str], horse: dict) -> str:
    """Google検索で馬の事実データだけを取得する。分析・推測は求めない。"""
    prompt = f"""\
netkeiba.com などの競馬情報サイトを検索し、以下の馬の事実データのみを返してください。
分析・推測・コメントは一切不要です。見つからない情報は「不明」と書いてください。

【馬名】{horse['name']}（2026年オークス出走・牝3歳）
【騎手】{horse['jockey']}

【返してほしい情報】
1. 父馬名
2. 母父馬名
3. 直近3走（各走：開催年月・レース名・芝ダ別・距離・着順・人気）
4. 東京競馬場での出走歴（あれば：レース名・距離・着順）
5. 芝2000m以上での出走歴（あれば：レース名・距離・着順）
6. 前走の上がり3ハロンタイム（あれば）

箇条書きで事実のみ。分析・評価・コメント不要。
"""
    try:
        result = call_gemini_with_search(api_keys, prompt)
        print(f"  ✓ {horse['name']}", file=sys.stderr)
        return result
    except RuntimeError as e:
        print(f"  ✗ {horse['name']} 失敗: {e}", file=sys.stderr)
        return "（データ取得失敗）"


# ── Phase 2: 事実データのみで脚本生成 ────────────────────────────────────────
def generate_script(api_keys: list[str], facts: list[dict]) -> str:
    """取得した事実データを素材に、補完なしで予想ナレーションを生成する。"""

    entries_text = "\n".join(
        f"  {e['frame']}枠{e['number']}番 {e['name']}（鞍上:{e['jockey']}）予想{e['odds_rank']}番人気"
        for e in ENTRIES
    )

    facts_text = ""
    for item in facts:
        h = item["horse"]
        facts_text += (
            f"\n▼{h['frame']}枠{h['number']}番 {h['name']}"
            f"（鞍上:{h['jockey']} / 予想{h['odds_rank']}番人気）\n"
        )
        facts_text += item["facts"] + "\n"

    prompt = f"""\
あなたは競馬予想ナレーターです。
以下の【各馬の事実データ】のみを使って、オークス2026の予想ナレーション脚本を書いてください。

【絶対ルール】
・提供されたデータにある事実のみで根拠を述べること
・データにない情報・推測・分析は一切書かない
・「〜と思われる」「〜だろう」「〜のはず」などの推測表現禁止
・データが不足している馬については根拠の言及を省略する
・事実データにある数字（着順・タイム・上がり・距離実績）をそのまま使う

【レース情報】
オークス2026 / 東京競馬場GI / 芝2400m / 2026年5月24日(日)

【全出走馬・枠順】
{entries_text}

【各馬の事実データ（検索で取得）】
{facts_text}

【脚本の条件】
・全体200〜400文字
・本命・対抗・3着・穴馬の4頭を選ぶ（事実データと枠番を根拠に）
・各馬：枠番・馬番・馬名を明記し、根拠は取得データにある事実のみ1〜2文
・最後に「みんなの本命は？コメントで教えてくれ！」で締める
・句点（。）で文を区切る
・挨拶・呼びかけ禁止

脚本のみ出力。余分な説明不要。
"""
    return call_gemini(api_keys, prompt, temperature=0.2)


# ── 背景画像生成 ──────────────────────────────────────────────────────────────
def generate_backgrounds() -> None:
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    backgrounds = [
        ("ai_0.jpg",
         "geq=r='clip(4+30*pow(Y/H,2),4,34)':g='clip(8+170*pow(Y/H,1.4),8,178)':b='clip(6+80*pow(Y/H,1.6),6,86)'"),
        ("ai_1.jpg",
         "geq=r='clip(4+30*pow(Y/H,1.8),4,34)':g='clip(6+60*pow(Y/H,1.8),6,66)':b='clip(10+180*pow(Y/H,1.4),10,190)'"),
        ("ai_2.jpg",
         "geq=r='clip(8+160*pow(1-Y/H,1.4),8,168)':g='clip(6+120*pow(1-Y/H,1.6),6,126)':b='clip(4+20*pow(Y/H,2),4,24)'"),
    ]
    for filename, vf in backgrounds:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=black:s=1080x1920:r=1",
             "-vf", vf, "-frames:v", "1", "-q:v", "3", f"{ASSETS_DIR}/{filename}"],
            check=True, capture_output=True,
        )
        print(f"背景画像生成: {ASSETS_DIR}/{filename}")


# ── メイン ────────────────────────────────────────────────────────────────────
def main() -> None:
    api_keys = load_api_keys()
    if not api_keys:
        print("[エラー] GEMINI_API_KEY が設定されていません", file=sys.stderr)
        sys.exit(1)

    # ── Phase 1: 各馬の事実データをGoogle検索で取得 ──────────────────────────
    print(f"\n[Phase 1] {len(RESEARCH_TARGETS)} 頭の事実データを取得中...", file=sys.stderr)
    facts = []
    for i, horse in enumerate(RESEARCH_TARGETS):
        print(f"  [{i+1}/{len(RESEARCH_TARGETS)}] {horse['name']} 検索中...", file=sys.stderr)
        result = fetch_horse_facts(api_keys, horse)
        facts.append({"horse": horse, "facts": result})
        if i < len(RESEARCH_TARGETS) - 1:
            time.sleep(5)  # レート制限対策

    # 取得データをログ出力
    print("\n[Phase 1 完了] 取得データ:", file=sys.stderr)
    for item in facts:
        h = item["horse"]
        print(f"\n▼{h['number']}番 {h['name']}", file=sys.stderr)
        print(item["facts"][:300], file=sys.stderr)

    # ── Phase 2: 事実データのみで脚本生成 ────────────────────────────────────
    print("\n[Phase 2] 事実データのみで脚本生成中...", file=sys.stderr)
    time.sleep(10)
    script = generate_script(api_keys, facts)
    print(f"\n[生成された脚本]\n{script}\n", file=sys.stderr)

    # ── 背景・ファイル出力 ────────────────────────────────────────────────────
    generate_backgrounds()

    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    (Path(OUTPUT_DIR) / "script_0.txt").write_text(script, encoding="utf-8")
    print(f"output/script_0.txt を生成しました（{len(script)} 文字）")

    news_entry = {
        "id": "oaks_2026_post_prediction",
        "title": "【オークス2026枠順確定予想】事実データで徹底分析",
        "url": "https://www.jra.go.jp/",
        "summary": script[:200],
        "image_url": "",
        "published_date": "2026-05-24T12:00:00+09:00",
    }
    Path(NEWS_JSON).write_text(
        json.dumps([news_entry], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"{NEWS_JSON} を生成しました。")


if __name__ == "__main__":
    main()
