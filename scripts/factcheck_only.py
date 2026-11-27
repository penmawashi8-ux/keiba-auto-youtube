#!/usr/bin/env python3
"""既存の名馬スクリプトをファクトチェックして修正する（Google検索グラウンディング使用）。

使用法: python scripts/factcheck_only.py <horse_key>
例:     python scripts/factcheck_only.py hearts_cry
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
SEARCH_CAPABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]
PREFERRED_MODELS = SEARCH_CAPABLE_MODELS + ["gemma-3-4b-it", "gemma-3-1b-it"]
RATE_LIMIT_WAITS = [30, 60]
NON_RETRY_STATUS = {403, 404}

DATA_DIR = Path("data/famous_horses")


def load_api_keys() -> list[str]:
    keys = []
    for env_var in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"]:
        k = os.environ.get(env_var, "").strip()
        if k:
            keys.append(k)
    print(f"[診断] Gemini APIキー: {len(keys)} 件", file=sys.stderr)
    return keys


def call_gemini(api_keys: list[str], prompt: str, temperature: float = 0.7,
                extra_tools: list | None = None,
                model_list: list[str] | None = None) -> str:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 8192},
    }
    if extra_tools:
        payload["tools"] = extra_tools

    models = model_list if model_list is not None else PREFERRED_MODELS
    pairs = [(key, model) for key in api_keys for model in models]

    for api_key, model in pairs:
        key_label = f"{api_key[:8]}..."
        url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"
        waits = [0] + RATE_LIMIT_WAITS

        for attempt, wait in enumerate(waits):
            if wait:
                print(f"  [{key_label} {model}] 429 待機 {wait}s...", file=sys.stderr)
                time.sleep(wait)
            try:
                resp = requests.post(url, json=payload, timeout=60)
                if resp.status_code in NON_RETRY_STATUS:
                    print(f"  [{key_label} {model}] HTTP {resp.status_code} → 次へ", file=sys.stderr)
                    break
                if resp.status_code in (503, 429):
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
                print(f"  [{key_label} {model}] {str(e).replace(api_key, '***')[:80]}", file=sys.stderr)
                break
        time.sleep(3)

    raise RuntimeError("Gemini API: 全キー×全モデルで失敗しました。")


def call_gemini_with_search(api_keys: list[str], prompt: str, temperature: float = 0.1) -> str:
    try:
        result = call_gemini(api_keys, prompt, temperature,
                             extra_tools=[{"google_search": {}}],
                             model_list=SEARCH_CAPABLE_MODELS)
        print("    [Google検索グラウンディング使用]", file=sys.stderr)
        return result
    except RuntimeError:
        print("    [警告] 検索対応モデル全滅 → 通常モードにフォールバック", file=sys.stderr)
        return call_gemini(api_keys, prompt, temperature)


def fact_check_sentence_by_sentence(api_keys: list[str], horse_name: str, script: str) -> str:
    lines = script.split('\n')
    non_empty = [l for l in lines if l.strip()]
    print(f"[1文ずつファクトチェック] 対象: {len(non_empty)} 文", file=sys.stderr)

    corrected_lines = []
    changes = []
    checked = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            corrected_lines.append(line)
            continue

        checked += 1
        print(f"  [{checked}/{len(non_empty)}] {stripped[:55]}", file=sys.stderr)

        prompt = f"""\
あなたは日本中央競馬（JRA）の専門家です。
以下の1文について事実確認をしてください。

【馬名】{horse_name}
【チェックする1文】
{stripped}

【出力ルール（厳守）】
・事実として確認できる明確な誤りがない → 「PASS」とだけ出力
・明確な誤りがある → 「CORRECTED: 修正後の1文」の形式のみで出力
・不確実・曖昧な情報は誤りと見なさない
・余分な説明・コメントは書かない
"""
        try:
            resp = call_gemini_with_search(api_keys, prompt, temperature=0.1)
            resp = resp.strip()
            if resp.upper().startswith("PASS"):
                corrected_lines.append(line)
                print(f"    PASS", file=sys.stderr)
            elif resp.upper().startswith("CORRECTED:"):
                fixed = resp.split(":", 1)[1].strip().strip("「」\"'")
                corrected_lines.append(fixed)
                changes.append((stripped, fixed))
                print(f"    修正 → {fixed[:55]}", file=sys.stderr)
            else:
                corrected_lines.append(line)
                print(f"    応答不明 → 元を保持: {resp[:50]}", file=sys.stderr)
        except RuntimeError:
            corrected_lines.append(line)
            print(f"    API失敗 → 元を保持", file=sys.stderr)

        if checked < len(non_empty):
            time.sleep(3)

    print(f"\n[1文ずつ完了] {len(changes)} 箇所を修正", file=sys.stderr)
    for orig, corr in changes:
        print(f"  「{orig[:40]}」→「{corr[:40]}」", file=sys.stderr)

    return '\n'.join(corrected_lines)


def fact_check_and_revise(api_keys: list[str], horse_name: str, script: str) -> str:
    prompt = f"""\
あなたは日本中央競馬（JRA）の専門家です。
以下の名馬列伝ナレーション脚本を厳密にファクトチェックしてください。

【馬名】{horse_name}

【脚本】
{script}

【チェック項目】
- レース名・開催年・着順・着差・タイムの正確性
- 騎手名・調教師名・父・母など血統情報の正確性
- GⅠ勝利数・重賞勝利数・その他の数字・記録の正確性
- 他の馬・騎手との比較情報の正確性
- 実際には起きていない出来事や結果の捏造がないか
- 文間の矛盾（同じ事実を別の箇所で違う数字で書いているなど）

【出力ルール】
- 確認できる明確な誤りのみ指摘（不確実な情報は指摘しない）
- 誤りがない場合は「PASS」とだけ出力

誤りがある場合:
ISSUES:
- [誤りの説明と正しい情報]
---CORRECTED---
[修正済みの脚本全文（元のスタイルを維持すること）]
---END---
"""
    try:
        response = call_gemini_with_search(api_keys, prompt, temperature=0.2)
    except RuntimeError as e:
        print(f"[全体ファクトチェック] API失敗 → 元の脚本を使用: {e}", file=sys.stderr)
        return script

    print(f"[全体ファクトチェック応答]\n{response[:800]}\n", file=sys.stderr)

    if response.strip().upper().startswith("PASS"):
        print("[全体ファクトチェック] 問題なし（PASS）", file=sys.stderr)
        return script

    m = re.search(r"---CORRECTED---\s*(.*?)\s*---END---", response, re.DOTALL)
    if not m:
        m = re.search(r"---CORRECTED---\s*(.*)", response, re.DOTALL)
    if m:
        corrected = m.group(1).strip()
        if len(corrected) >= 50:
            print("[全体ファクトチェック] 修正あり → 採用", file=sys.stderr)
            return corrected

    print("[全体ファクトチェック] 修正脚本の抽出失敗 → 元を使用", file=sys.stderr)
    return script


def build_issue_body(horse_name: str, original: str, final: str) -> str:
    lines = [f"## {horse_name} ファクトチェック結果\n"]
    lines += ["### 元の脚本", f"```\n{original}\n```\n"]
    lines += ["### 修正後の脚本", f"```\n{final}\n```\n"]

    orig_lines = [l for l in original.split('\n') if l.strip()]
    final_lines = [l for l in final.split('\n') if l.strip()]
    changes = [(o, f) for o, f in zip(orig_lines, final_lines) if o != f]
    if not changes and original.strip() == final.strip():
        lines.append("**変更なし（全文PASS）**")
    elif changes:
        lines.append("### 修正箇所")
        for orig, fixed in changes:
            lines.append(f"- `{orig}`\n  → `{fixed}`")

    return "\n".join(lines)


def post_github_issue(title: str, body: str) -> None:
    import urllib.request
    repo  = os.environ.get("GH_REPO", "")
    token = os.environ.get("GH_TOKEN", "")
    if not repo or not token:
        print("[GitHub Issue] GH_REPO / GH_TOKEN 未設定 → スキップ", file=sys.stderr)
        return
    payload = json.dumps({"title": title, "body": body[:65000]}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
        print(f"[GitHub Issue] 作成: {data.get('html_url')}")


def main() -> None:
    if len(sys.argv) < 2:
        print("使用法: python scripts/factcheck_only.py <horse_key>", file=sys.stderr)
        sys.exit(1)

    horse_key = sys.argv[1]
    txt_path  = DATA_DIR / f"{horse_key}.txt"
    json_path = DATA_DIR / f"{horse_key}.json"

    if not txt_path.exists():
        print(f"[エラー] {txt_path} が見つかりません", file=sys.stderr)
        sys.exit(1)

    original = txt_path.read_text(encoding="utf-8").strip()
    horse_name = horse_key
    if json_path.exists():
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        horse_name = meta.get("name", horse_key)

    api_keys = load_api_keys()
    if not api_keys:
        print("[エラー] GEMINI_API_KEY が設定されていません", file=sys.stderr)
        sys.exit(1)

    print(f"=== ファクトチェック: {horse_name} ===\n")
    print(f"【元の脚本】\n{original}\n")

    # 1文ずつチェック
    print("[1文ずつファクトチェック中...]", file=sys.stderr)
    script_v1 = fact_check_sentence_by_sentence(api_keys, horse_name, original)

    time.sleep(10)

    # 全体チェック
    print("[全体ファクトチェック中...]", file=sys.stderr)
    script_final = fact_check_and_revise(api_keys, horse_name, script_v1)

    print(f"\n【修正後の脚本】\n{script_final}\n")
    changed = original.strip() != script_final.strip()
    print(f"【結果】{'変更あり' if changed else '変更なし（全文PASS）'}")

    # GitHub Issue に投稿
    import datetime
    title = f"[ファクトチェック] {horse_name} {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
    body  = build_issue_body(horse_name, original, script_final)
    post_github_issue(title, body)

    # 修正があれば上書き保存
    if changed:
        txt_path.write_text(script_final, encoding="utf-8")
        print(f"\n{txt_path} を更新しました", file=sys.stderr)


if __name__ == "__main__":
    main()
