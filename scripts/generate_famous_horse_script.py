#!/usr/bin/env python3
"""名馬列伝 AI脚本生成スクリプト

API呼び出しを最小限に抑えて Gemini のレート制限を回避する設計。
  呼び出し1回目: 名馬選定 + 脚本生成 + ファクトチェック + 修正（全て1プロンプトで）
  呼び出し2回目: YouTubeメタデータ生成（タグ・サムネイルテキスト）

出力:
  data/famous_horses/{key}.json, .txt
  output/script_0.txt, news.json  （既存パイプライン用）
  $GITHUB_OUTPUT: horse_key, horse_name
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# 実際に利用可能なモデルのみ（403/404 が出たモデルは除外）
# 1.5 系はAPIキーによっては 403/404 を返すため除外済み
PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemma-3-4b-it",
    "gemma-3-1b-it",
]
# 429 時のリトライ待機（秒）: 30s・60s の 2 回リトライ。それでも駄目なら次モデルへ。
RATE_LIMIT_WAITS = [30, 60]
# これらの HTTP ステータスはリトライせず即座に次モデルへ
NON_RETRY_STATUS = {403, 404}

DATA_DIR = Path("data/famous_horses")
OUTPUT_DIR = Path("output")

STYLE_EXAMPLES = """\
【例1: ラインクラフト】
桜花賞を勝った馬が、オークスに出なかった。
その馬の名前、ラインクラフト。
桜花賞とNHKマイルカップを連覇した、史上初の変則二冠牝馬。
桜花賞馬がオークスじゃなくて、牡馬相手のNHKマイルCに挑む。
前例ゼロの選択。
でも蓋を開けたらデアリングハートに1馬身3/4差をつける完勝。
しかも実はこの馬、桜花賞の1週前まで体の疲れが取れず、回避の可能性すらあった。
それでも勝った。
2冠を達成して、次はスプリンターズSを目指していた4歳の夏。
放牧先の牧場で調教中に突然倒れ、急性心不全で死亡。
たった4歳だった。
血統は残らなかった。
でも彼女が切り拓いた桜花賞からNHKマイルへの道は、今も続いてる。

【例2: シルポート】
GⅠで前半1000m…56秒5で逃げた馬がいる。
その名はシルポート。
マイラーズカップ2連覇、重賞3勝の本物の逃げ馬。
2011年天皇賞（秋）、スタートと同時に後続を5〜6馬身ちぎり捨てる。
前半1000mのタイム、56秒5。
サイレンススズカすら上回る超ハイペース。東京競馬場がどよめいた。
当然残り300mで失速して16着。
でもトーセンジョーダンのレコードはこの逃げが作り上げたもの。
そして8歳、ラストランの宝塚記念。
相手はゴールドシップ、ジェンティルドンナという超豪華11頭。
それでもシルポートはハイペースで大逃げ。
結果は10着。でも最後まで自分の競馬を貫いた。
その後、放牧中に骨膜炎を発症してひっそり引退。
派手に逃げて、静かに去った馬。それがシルポートです。

【例3: テイエムプリキュア】
GⅠ馬が、24連敗した。
その馬の名前、テイエムプリキュア。
2005年、阪神ジュベナイルフィリーズを8番人気で差し切り勝ち。最優秀2歳牝馬を受賞した。
でもそこから3年間、まったく勝てない。
連敗は24まで伸びて、陣営はついに引退を決意。
引退レースに選ばれた2009年の日経新春杯。なんと11番人気。
そこで大逃げを打って…3馬身半差で圧勝。引退撤回。
そして同じ年のエリザベス女王杯。12番人気。
クィーンスプマンテとふたりで後続を25馬身近く引き離す大逃げ。
1番人気ブエナビスタの猛追をクビ差で振り切って2着。
三連単154万円の大波乱を演出した。
GⅠ馬が、24連敗して、また伝説を作った。
これが競馬の恐ろしさ。
"""


# ---------------------------------------------------------------------------
# Gemini API 呼び出し
# ---------------------------------------------------------------------------

def load_api_keys() -> list[str]:
    """環境変数から Gemini API キーを最大3件ロードする。"""
    keys = []
    for env_var in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"]:
        k = os.environ.get(env_var, "").strip()
        if k:
            keys.append(k)
    print(f"[診断] Gemini APIキー: {len(keys)} 件ロード", file=sys.stderr)
    return keys


def call_gemini(api_keys: list[str], prompt: str, temperature: float = 0.7) -> str:
    """Gemini API を呼び出す（マルチキー対応）。
    - キー × モデルの全組み合わせを試みる
    - 403 / 404: このモデルは使えないので即次へ（リトライなし）
    - 429: RATE_LIMIT_WAITS に従ってリトライし、上限後に次の組み合わせへ
    - その他エラー: 即次へ
    """
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 8192,
        },
    }

    # (APIキー, モデル名) の全組み合わせ（キー優先でローテーション）
    pairs = [(key, model) for key in api_keys for model in PREFERRED_MODELS]

    for api_key, model in pairs:
        key_label = f"{api_key[:8]}..."
        # gemma はシステムインストラクション非対応のためスキップしない（userコンテンツのみ送る）
        url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"
        waits = [0] + RATE_LIMIT_WAITS  # [0, 30, 60]

        for attempt, wait in enumerate(waits):
            if wait:
                print(f"  [key={key_label} {model}] 429 レート制限。{wait}秒待機...", file=sys.stderr)
                time.sleep(wait)

            try:
                resp = requests.post(url, json=payload, timeout=60)

                if resp.status_code in NON_RETRY_STATUS:
                    print(f"  [key={key_label} {model}] HTTP {resp.status_code} → 次へ", file=sys.stderr)
                    break  # この組み合わせはスキップ

                if resp.status_code == 503:
                    print(f"  [key={key_label} {model}] 503 サービス停止。リトライ...", file=sys.stderr)
                    if attempt < len(waits) - 1:
                        continue
                    print(f"  [key={key_label} {model}] 503 リトライ上限 → 次へ", file=sys.stderr)
                    break

                if resp.status_code == 429:
                    safe_body = resp.text.replace(api_key, "***") if api_key in resp.text else resp.text
                    print(f"  [key={key_label} {model}] 429 詳細: {safe_body[:300]}", file=sys.stderr)
                    if attempt < len(waits) - 1:
                        continue  # 次の待機時間でリトライ
                    print(f"  [key={key_label} {model}] 429 リトライ上限 → 次へ", file=sys.stderr)
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
                    print(f"  [key={key_label} {model}] HTTP {status} → 次へ", file=sys.stderr)
                    break
                if status == 429:
                    if attempt < len(waits) - 1:
                        continue
                    print(f"  [key={key_label} {model}] 429 リトライ上限 → 次へ", file=sys.stderr)
                    break
                print(f"  [key={key_label} {model}] HTTP エラー {status} → 次へ", file=sys.stderr)
                break

            except Exception as e:
                safe_msg = str(e).replace(api_key, "***")
                print(f"  [key={key_label} {model}] エラー: {safe_msg} → 次へ", file=sys.stderr)
                break

        time.sleep(3)  # 組み合わせ切り替え時の短いインターバル

    raise RuntimeError(
        "Gemini API: 全キー×全モデルで失敗しました。\n"
        "・APIキーの残クォータを Google AI Studio で確認してください。\n"
        "・GEMINI_API_KEY_2 / GEMINI_API_KEY_3 を設定すると複数キーでフォールバックできます。\n"
        "・ニュースワークフローと同時実行するとクォータが枯渇する場合があります。"
    )


# ---------------------------------------------------------------------------
# ステップ1: 名馬選定 + 脚本生成 + ファクトチェック + 修正（1回の呼び出し）
# ---------------------------------------------------------------------------

def select_and_generate(api_keys: list[str], covered: list[dict]) -> dict:
    """名馬の選定・脚本生成・ファクトチェック・修正を1回の API 呼び出しで実行する。

    Returns:
        dict: name / key / era / catchphrase / script
    """
    covered_lines = "\n".join(f"  - {h['name']} ({h['key']})" for h in covered)
    if not covered_lines:
        covered_lines = "  （まだなし）"

    prompt = f"""\
あなたは日本競馬の専門家かつ名馬列伝シリーズのナレーター作家です。
以下の3ステップを順番に実行し、指定の形式で出力してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1: 名馬を選定する
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【紹介済みの馬（この中から選ばないこと）】
{covered_lines}

【選定条件】
- 日本中央競馬（JRA）の歴史に残る名馬
- 強烈なストーリー（劇的な勝利、悲劇、記録、逆転、独特の戦法など）
- 具体的な数字・史実・固有名詞が豊富
- YouTube Shorts として面白く伝えられる

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2: 脚本を書く
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
以下のスタイルサンプルに完全に倣って脚本を書く。

{STYLE_EXAMPLES}

【脚本の必須ルール】
- 全体200〜500文字
- 短い文（1文40字以内）を積み重ねる
- 冒頭に衝撃的な事実または問いかけで始める
- 具体的な数字・日付・レース名・タイム・着差を含める
- 感情語より事実で語る（「感動した」「すごい」は使わない）
- 最後は詩的・哲学的な1〜2行で締める
- 挨拶・呼びかけ（「みなさん」「こんにちは」）禁止
- 改行で文を区切る（句点「。」は不要）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3: ファクトチェックして誤りを修正する
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
書いた脚本の以下を検証し、明らかな誤りがあれば修正する。
- レース名・開催年・着順・着差・タイムの正確性
- 騎手名・調教師名・血統（父・母）の正確性
- GⅠ勝利数・重賞勝利数等の記録

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【出力形式】この形式を厳守すること（余分な説明・コメント不要）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HORSE_NAME: [日本語での正式名称]
HORSE_KEY: [英小文字・数字・アンダースコアのみのローマ字（例: oguri_cap）]
ERA: [例: 1990年代]
CATCHPHRASE: [その馬を象徴する20文字以内の一言]
---SCRIPT---
[修正済みの最終脚本テキストのみ]
---END---
"""
    response = call_gemini(api_keys, prompt, temperature=0.7)
    print(f"[API応答]\n{response[:600]}...\n", file=sys.stderr)
    return _parse_select_response(response)


def _parse_select_response(response: str) -> dict:
    """select_and_generate のレスポンスをパースする。"""
    meta: dict[str, str] = {}
    for line in response.strip().splitlines():
        for sep in (":", "："):
            if sep in line:
                k, _, v = line.partition(sep)
                meta[k.strip()] = v.strip()
                break

    # ---SCRIPT--- ～ ---END--- 間の脚本を抽出
    # gemma など ---END--- を省略するモデルのため、なければ ---SCRIPT--- 以降を全て使う
    m = re.search(r"---SCRIPT---\s*(.*?)\s*---END---", response, re.DOTALL)
    if m:
        script = m.group(1).strip()
    else:
        m2 = re.search(r"---SCRIPT---\s*(.*)", response, re.DOTALL)
        script = m2.group(1).strip() if m2 else ""

    horse_name = meta.get("HORSE_NAME", "").strip()
    horse_key = re.sub(r"[^a-z0-9_]", "_", meta.get("HORSE_KEY", "").lower()).strip("_")
    era = meta.get("ERA", "").strip()
    catchphrase = meta.get("CATCHPHRASE", "").strip()

    if not horse_name:
        raise ValueError(f"馬名を取得できませんでした。レスポンス:\n{response}")
    if not horse_key:
        horse_key = re.sub(r"[^a-z0-9_]", "_", horse_name.lower()).strip("_") or "unknown"
    if not script:
        raise ValueError(f"脚本を取得できませんでした。レスポンス:\n{response}")

    return {"name": horse_name, "key": horse_key, "era": era,
            "catchphrase": catchphrase, "script": script}


# ---------------------------------------------------------------------------
# ステップ2: メタデータ生成（タグ・サムネイルテキスト）
# ---------------------------------------------------------------------------

def generate_metadata(api_keys: list[str], horse_name: str, script: str,
                      era: str, catchphrase: str) -> dict:
    """YouTube 用タグ・サムネイルテキストを生成する。"""
    prompt = f"""\
以下の名馬列伝脚本から、YouTube動画用のメタデータを生成してください。

馬名: {horse_name}
時代: {era}

【脚本】
{script}

以下の形式のみで出力してください（余分なコメント不要）：
TAGS: [馬名, GⅠレース名1, レース名2, キーワード... をカンマ区切りで計6〜8個]
THUMBNAIL_TOP: [3〜8文字の短いテキスト（例: 史上初の / 伝説の / 幻の / 奇跡の）]
THUMBNAIL_MAIN: [5〜12文字のメインテキスト（例: 変則二冠 / 超ハイペース / 24連敗）]
"""
    response = call_gemini(api_keys, prompt, temperature=0.5)
    print(f"[メタデータ応答]\n{response}\n", file=sys.stderr)

    meta: dict[str, str] = {}
    for line in response.strip().splitlines():
        for sep in (":", "："):
            if sep in line:
                k, _, v = line.partition(sep)
                meta[k.strip()] = v.strip()
                break

    tags_raw = meta.get("TAGS", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    if horse_name not in tags:
        tags.insert(0, horse_name)

    def strip_brackets(s: str) -> str:
        return s.strip("「」")

    return {
        "name": horse_name,
        "catchphrase": catchphrase,
        "era": era,
        "tags_extra": tags,
        "thumbnail_top": strip_brackets(meta.get("THUMBNAIL_TOP", "")),
        "thumbnail_main": strip_brackets(meta.get("THUMBNAIL_MAIN", "")),
    }


# ---------------------------------------------------------------------------
# 共通: パイプライン用ファイル書き出し
# ---------------------------------------------------------------------------

def get_existing_horses() -> list[dict]:
    """data/famous_horses/*.json から既紹介済み馬のリストを取得する。"""
    horses = []
    for json_file in sorted(DATA_DIR.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            horses.append({"key": json_file.stem, "name": data.get("name", json_file.stem)})
        except Exception:
            horses.append({"key": json_file.stem, "name": json_file.stem})
    return horses


def write_pipeline_files(horse_key: str, horse_name: str, catchphrase: str,
                         script: str, metadata: dict) -> None:
    """既存パイプライン（generate_audio.py 等）が期待するファイルを生成する。"""
    OUTPUT_DIR.mkdir(exist_ok=True)

    news_item = {
        "id": f"famous_horse_{horse_key}",
        "title": horse_name,
        "summary": catchphrase,
        "url": "",
        "image_url": None,
        "thumbnail_top": metadata.get("thumbnail_top", ""),
        "thumbnail_main": metadata.get("thumbnail_main", ""),
    }
    Path("news.json").write_text(
        json.dumps([news_item], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "script_0.txt").write_text(script, encoding="utf-8")

    print(f"[パイプライン用ファイル書き出し]", file=sys.stderr)
    print(f"  news.json: タイトル「{horse_name}」", file=sys.stderr)
    print(f"  output/script_0.txt: {len(script)} 文字", file=sys.stderr)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    api_keys = load_api_keys()
    if not api_keys:
        print("[エラー] GEMINI_API_KEY が設定されていません", file=sys.stderr)
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 既存の名馬リスト確認
    covered = get_existing_horses()
    print(f"[既存の名馬] {len(covered)} 頭:", file=sys.stderr)
    for h in covered:
        print(f"  - {h['name']} ({h['key']})", file=sys.stderr)

    # ── API呼び出し1回目 ──────────────────────────────────────────────────
    # 名馬選定 + 脚本生成 + ファクトチェック + 修正（1プロンプトで全て実行）
    print("\n[名馬選定・脚本生成・ファクトチェック中...]", file=sys.stderr)
    result = select_and_generate(api_keys, covered)

    horse_name = result["name"]
    horse_key = result["key"]
    era = result["era"]
    catchphrase = result["catchphrase"]
    script = result["script"]

    # 名前の重複チェック（AIが紹介済みリストを無視した場合の安全弁）
    existing_names = {h["name"] for h in covered}
    if horse_name in existing_names:
        print(f"[エラー] 「{horse_name}」は既に紹介済みです。AIが指示を無視しました。中止します。", file=sys.stderr)
        sys.exit(1)

    # キー重複チェック（同一馬の可能性が高い）
    if (DATA_DIR / f"{horse_key}.json").exists():
        print(f"[エラー] キー「{horse_key}」({horse_name})は既に存在します。重複投稿を防ぐため中止します。", file=sys.stderr)
        sys.exit(1)

    print(f"[選定完了] 馬名: {horse_name}  キー: {horse_key}", file=sys.stderr)
    print(f"[最終脚本]\n{script}\n", file=sys.stderr)

    # ── API呼び出し2回目（20秒インターバル後） ──────────────────────────
    print("[20秒待機中（レート制限対策）...]", file=sys.stderr)
    time.sleep(20)
    print("[メタデータ生成中...]", file=sys.stderr)
    metadata = generate_metadata(api_keys, horse_name, script, era, catchphrase)

    # data/famous_horses/ に保存
    (DATA_DIR / f"{horse_key}.txt").write_text(script, encoding="utf-8")
    (DATA_DIR / f"{horse_key}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[名馬データ保存]", file=sys.stderr)
    print(f"  {DATA_DIR}/{horse_key}.txt", file=sys.stderr)
    print(f"  {DATA_DIR}/{horse_key}.json", file=sys.stderr)

    # パイプライン用ファイル生成
    write_pipeline_files(horse_key, horse_name, catchphrase, script, metadata)

    # $GITHUB_OUTPUT に書き出し
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"horse_key={horse_key}\n")
            f.write(f"horse_name={horse_name}\n")
    else:
        print(f"horse_key={horse_key}")
        print(f"horse_name={horse_name}")


if __name__ == "__main__":
    main()
