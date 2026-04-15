#!/usr/bin/env python3
"""名馬列伝 AI脚本生成スクリプト

フロー:
  1. data/famous_horses/*.json を読んで既紹介済み馬を確認
  2. Gemini で次の名馬を選定
  3. Gemini で名馬列伝スタイルの脚本を生成
  4. Gemini でファクトチェック（史実・数字・固有名詞の検証）
  5. ❌指摘がある箇所のみを修正
  6. Gemini でYouTube用メタデータ（タグ・サムネイルテキスト）を生成
  7. data/famous_horses/{key}.json と .txt に保存
  8. output/script_0.txt と news.json に書き出し（既存パイプライン用）
  9. $GITHUB_OUTPUT に horse_key / horse_name を書き出し
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
PREFERRED_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
]

DATA_DIR = Path("data/famous_horses")
OUTPUT_DIR = Path("output")

# 既存スタイルのサンプル（プロンプトに埋め込む）
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

def call_gemini(api_key: str, prompt: str, temperature: float = 0.7, model_index: int = 0) -> str:
    """Gemini API を呼び出してテキスト生成する。失敗時は次のモデルにフォールバック。"""
    model = PREFERRED_MODELS[model_index % len(PREFERRED_MODELS)]
    url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 2048,
        },
    }

    for model in PREFERRED_MODELS:
        url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"
        waits = [0] + RATE_LIMIT_WAITS  # [0, 30]

        for attempt, wait in enumerate(waits):
            if wait:
                print(f"  [{model}] 429 レート制限。{wait}秒待機...", file=sys.stderr)
                time.sleep(wait)

            try:
                resp = requests.post(url, json=payload, timeout=60)

                if resp.status_code in NON_RETRY_STATUS:
                    print(f"  [{model}] HTTP {resp.status_code} → 次のモデルへ", file=sys.stderr)
                    break  # このモデルはスキップ

                if resp.status_code == 429:
                    if attempt < len(waits) - 1:
                        continue  # 次の待機時間でリトライ
                    print(f"  [{model}] 429 リトライ上限 → 次のモデルへ", file=sys.stderr)
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
                    print(f"  [{model}] HTTP {status} → 次のモデルへ", file=sys.stderr)
                    break
                if status == 429:
                    if attempt < len(waits) - 1:
                        continue
                    print(f"  [{model}] 429 リトライ上限 → 次のモデルへ", file=sys.stderr)
                    break
                print(f"  [{model}] HTTP エラー {status} → 次のモデルへ", file=sys.stderr)
                break

            except Exception as e:
                safe_msg = str(e).replace(api_key, "***")
                print(f"  [{model}] エラー: {safe_msg} → 次のモデルへ", file=sys.stderr)
                break

        time.sleep(3)  # モデル切り替え時の短いインターバル

    raise RuntimeError(
        "Gemini API: 全モデルで 429 レート制限。\n"
        "ニュースワークフローと同時実行するとAPIクォータが枯渇します。\n"
        "17:00 JST のスケジュール実行（ニュースワークフローが動いていない時間帯）なら通るはずです。\n"
        "手動テストする場合は news ワークフローが動いていない時間帯を選んでください。"
    )


# ---------------------------------------------------------------------------
# 各ステップの実装
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


def select_horse(api_key: str, covered: list[dict]) -> dict:
    """Gemini で次に紹介する名馬を選定する。

    Returns:
        dict: name / key / era / catchphrase
    """
    covered_lines = "\n".join(f"  - {h['name']} ({h['key']})" for h in covered)
    if not covered_lines:
        covered_lines = "  （まだなし）"

    prompt = f"""\
あなたは日本競馬の専門家です。名馬列伝シリーズで次に紹介すべき名馬を1頭選んでください。

【既に紹介済みの馬】
{covered_lines}

【選定条件】
- 上のリストにない馬であること
- 日本中央競馬（JRA）の歴史に残る名馬
- 強烈なストーリーがある（劇的な勝利、記録達成、悲劇、逆転劇、独特の戦法など）
- 具体的な数字・史実・固有名詞が豊富にある
- YouTube Shorts動画として視聴者の心に刺さる内容

以下の形式のみで出力してください（余分な説明は一切不要）：
馬名: [日本語での正式名称]
キー: [英小文字・数字・アンダースコアのみのローマ字表記（例: oguri_cap, narita_brian）]
時代: [例: 1990年代]
キャッチフレーズ: [その馬を象徴する20文字以内の一言]
"""
    response = call_gemini(api_key, prompt, temperature=0.8)
    print(f"[名馬選定]\n{response}\n", file=sys.stderr)

    result: dict[str, str] = {}
    for line in response.strip().splitlines():
        for sep in (":", "："):
            if sep in line:
                k, _, v = line.partition(sep)
                result[k.strip()] = v.strip()
                break

    horse_name = result.get("馬名", "").strip()
    horse_key = re.sub(r"[^a-z0-9_]", "_", result.get("キー", "").lower()).strip("_")
    era = result.get("時代", "").strip()
    catchphrase = result.get("キャッチフレーズ", "").strip()

    if not horse_name:
        raise ValueError(f"馬名を取得できませんでした。レスポンス:\n{response}")
    if not horse_key:
        horse_key = re.sub(r"[^a-z0-9_]", "_", horse_name.lower()).strip("_") or "unknown"

    return {"name": horse_name, "key": horse_key, "era": era, "catchphrase": catchphrase}


def generate_script(api_key: str, horse_name: str) -> str:
    """Gemini で名馬列伝スタイルの脚本を生成する。"""
    prompt = f"""\
あなたは名馬列伝シリーズのナレーター作家です。
{horse_name}についての脚本を、以下のスタイルサンプルに完全に倣って書いてください。

{STYLE_EXAMPLES}
【{horse_name}の脚本を書く際の必須ルール】
- 全体の文字数は200〜500文字
- 短い文を積み重ねる（1文が40字を超えないように）
- 冒頭に衝撃的な事実や問いかけで始める
- 具体的な数字・日付・レース名・タイム・着差を必ず含める
- 感情語より事実で語る（「感動した」「すごい」「素晴らしい」は使わない）
- 最後は詩的・哲学的な1〜2行で締める
- 挨拶・呼びかけ（「みなさん」「こんにちは」等）は禁止
- 「。」ではなく改行で文を区切る（末尾に「。」は不要）
- 競馬用語は正確に（GⅠ/G1、馬身、クビ差、重賞名など）

脚本のテキストのみを出力してください。説明・前置き・コメント不要。
"""
    return call_gemini(api_key, prompt, temperature=0.7)


def fact_check(api_key: str, horse_name: str, script: str) -> str:
    """生成した脚本のファクトチェックを Gemini に依頼する。"""
    prompt = f"""\
以下は{horse_name}についての名馬列伝脚本です。ファクトチェックをしてください。

【脚本】
{script}

【確認してほしい項目】
1. レース名・レース種別・開催年の正確性
2. 着順・着差・タイム・上がり3Fなどの数値
3. 馬の誕生年・引退年・死亡年（存在する場合）
4. 騎手名・調教師名・血統（父・母）の正確性
5. 対戦相手の馬名と成績
6. GⅠ勝利数・重賞勝利数等の記録
7. その他明らかな事実誤認

【出力ルール】
- 問題がなければ: 「✓ 問題なし」
- 問題がある場合: 「❌ [誤った記述]: [正しい内容]」を誤りごとに1行
- 不確かな場合: 「△ 要確認: [該当箇所と理由]」
- 最後に1行: 「修正必要箇所: N件」（Nは❌の数）
"""
    return call_gemini(api_key, prompt, temperature=0.2)


def correct_script(api_key: str, original: str, fact_check_result: str) -> str:
    """ファクトチェック結果に基づいて脚本を修正する。❌指摘がなければそのまま返す。"""
    if "❌" not in fact_check_result:
        print("[修正] ❌指摘なし。元の脚本をそのまま使用。", file=sys.stderr)
        return original

    prompt = f"""\
以下の脚本にファクトチェックの指摘があります。❌で示された明確な誤りのみを修正してください。

【元の脚本】
{original}

【ファクトチェック結果】
{fact_check_result}

【修正ルール】
- ❌の指摘は必ず修正する
- △（要確認）は慎重に判断し、確信が持てない場合はそのまま残す
- スタイル・文体・改行の形式は変えない
- 修正した脚本のテキストのみ出力（説明・コメント不要）
"""
    return call_gemini(api_key, prompt, temperature=0.3)


def generate_metadata(api_key: str, horse_name: str, script: str, era: str, catchphrase: str) -> dict:
    """YouTube用タグ・サムネイルテキストを Gemini で生成する。"""
    prompt = f"""\
以下の名馬列伝脚本から、YouTube動画用のメタデータを生成してください。

馬名: {horse_name}
時代: {era}

【脚本】
{script}

以下の形式のみで出力してください：
タグ: [馬名, GⅠレース名1, レース名2, キーワード... をカンマ区切りで計6〜8個]
サムネイル上段: [3〜8文字の短いテキスト（例: 史上初の / 伝説の / 幻の / 奇跡の）]
サムネイル中段: [5〜12文字のメインテキスト（例: 変則二冠 / 超ハイペース / 24連敗）]
"""
    response = call_gemini(api_key, prompt, temperature=0.5)
    print(f"[メタデータ]\n{response}\n", file=sys.stderr)

    result: dict[str, str] = {}
    for line in response.strip().splitlines():
        for sep in (":", "："):
            if sep in line:
                k, _, v = line.partition(sep)
                result[k.strip()] = v.strip()
                break

    tags_raw = result.get("タグ", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    if horse_name not in tags:
        tags.insert(0, horse_name)

    # 「」があれば除去
    def strip_brackets(s: str) -> str:
        return s.strip("「」")

    return {
        "name": horse_name,
        "catchphrase": catchphrase,
        "era": era,
        "tags_extra": tags,
        "thumbnail_top": strip_brackets(result.get("サムネイル上段", "")),
        "thumbnail_main": strip_brackets(result.get("サムネイル中段", "")),
    }


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

    print(f"[パイプライン用ファイル書き出し完了]", file=sys.stderr)
    print(f"  news.json : タイトル「{horse_name}」", file=sys.stderr)
    print(f"  output/script_0.txt : {len(script)} 文字", file=sys.stderr)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("[エラー] GEMINI_API_KEY が設定されていません", file=sys.stderr)
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 1. 既存の名馬リスト確認
    covered = get_existing_horses()
    print(f"[既存の名馬] {len(covered)} 頭:", file=sys.stderr)
    for h in covered:
        print(f"  - {h['name']} ({h['key']})", file=sys.stderr)

    # 2. 名馬選定
    print("\n[名馬選定中...]", file=sys.stderr)
    selection = select_horse(api_key, covered)
    horse_name = selection["name"]
    horse_key = selection["key"]
    era = selection["era"]
    catchphrase = selection["catchphrase"]

    # キー重複回避（同名馬が再選定された場合など）
    base_key = horse_key
    suffix = 2
    while (DATA_DIR / f"{horse_key}.json").exists():
        horse_key = f"{base_key}_{suffix}"
        suffix += 1

    print(f"[選定完了] 馬名: {horse_name}  キー: {horse_key}", file=sys.stderr)

    # 3. 脚本生成
    print(f"\n[脚本生成中: {horse_name}...]", file=sys.stderr)
    script = generate_script(api_key, horse_name)
    print(f"[生成脚本]\n{script}\n", file=sys.stderr)

    # 4. ファクトチェック
    print("[ファクトチェック中...]", file=sys.stderr)
    fact_check_result = fact_check(api_key, horse_name, script)
    print(f"[ファクトチェック結果]\n{fact_check_result}\n", file=sys.stderr)

    # 5. 脚本修正
    print("[修正中...]", file=sys.stderr)
    final_script = correct_script(api_key, script, fact_check_result)
    if final_script != script:
        print(f"[修正後脚本]\n{final_script}\n", file=sys.stderr)

    # 6. メタデータ生成
    print("[メタデータ生成中...]", file=sys.stderr)
    metadata = generate_metadata(api_key, horse_name, final_script, era, catchphrase)

    # 7. data/famous_horses/ に保存
    (DATA_DIR / f"{horse_key}.txt").write_text(final_script, encoding="utf-8")
    (DATA_DIR / f"{horse_key}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[名馬データ保存]", file=sys.stderr)
    print(f"  {DATA_DIR}/{horse_key}.txt", file=sys.stderr)
    print(f"  {DATA_DIR}/{horse_key}.json", file=sys.stderr)

    # 8. パイプライン用ファイル生成（famous_horse_prepare.py の役割を代替）
    write_pipeline_files(horse_key, horse_name, catchphrase, final_script, metadata)

    # 9. $GITHUB_OUTPUT に書き出し（GitHub Actions 用）
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"horse_key={horse_key}\n")
            f.write(f"horse_name={horse_name}\n")
    else:
        # ローカル実行時は stdout に出力
        print(f"horse_key={horse_key}")
        print(f"horse_name={horse_name}")


if __name__ == "__main__":
    main()
