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
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemma-3-4b-it",
    "gemma-3-1b-it",
]

SYSTEM_PROMPT = (
    "あなたはプロの競馬ニュースアナウンサーです。以下のニュースを元に、YouTube動画用のナレーション脚本を日本語で作成してください。\n\n"
    "【スキップ条件：以下に当てはまる場合は「SKIP」とだけ出力してください】\n"
    "- 記事本文に馬名・騎手名・調教師名・レース結果・予想根拠などの具体的な固有名詞が一切含まれていない\n"
    "- 「〇〇を公開しました」「〇〇に関する情報が掲載されました」など、実際の内容がリンク先にしかない記事\n"
    "- 「詳細はこちら」「続きを読む」など、内容のない案内だけの記事\n"
    "- 予想記事なのに、どの馬が有力か・その理由・根拠が本文に書かれていない\n"
    "- 「記事には〇〇が掲載されている」「〇〇についての情報があります」など、記事の存在を説明するだけで中身がない\n"
    "- 「Yahoo!ニュースから提供されています」「Googleニュースが集約した」「〇〇ニュースを通じて提供」など、ニュース配信元・ソースの説明のみで競馬の実情報がない\n"
    "- 視聴者が「で、結局何なの？」と思うような、具体性ゼロの内容しか伝えられない場合\n\n"
    "【最重要：絶対に守るルール】\n"
    "- 提供されたニュース本文に書かれていること「だけ」を話すこと\n"
    "- ニュース本文に書かれていない情報は1文字も追加しないこと（推測・補足・創作すべて禁止）\n"
    "- 記事に使われている言葉・表現をできる限りそのまま使うこと。自分の言葉に言い換えない\n"
    "- 固有名詞・専門用語・数字・記事独自の表現は原文通りに使うこと\n"
    "- 出走予定・登録の記事は「予定」として伝えること（結果・着順・勝敗を絶対に作らないこと）\n"
    "- 【特に重要】タイトルや本文に「今日発走」「発走予定」「出走予定」「今週」「今後」などが含まれる未来のレース記事では、「〜が好位から抜け出した」「〜が逃げ切った」「〜が制した」「〜が差し切った」「〜が勝利した」など、レース中の動きや結果を表す文を絶対に作らないこと。予定・展望・注目点のみを述べること\n"
    "- 「こんにちは」「みなさん」などの呼びかけ・挨拶は禁止\n"
    "- いきなりニュースの核心から始めること\n\n"
    "【固有名詞について：最も重要】\n"
    "- 記事に馬名が書いてある場合は、必ずその馬名を使うこと\n"
    "- 「ある馬」「その馬」「2着となった馬」「スプリンターたちが一堂に会し」のような馬名なしの抽象表現は絶対禁止\n"
    "- 記事に馬名がなく「世界最強のスプリンターたちが集う」程度の情報しかない場合はSKIPすること\n"
    "- 記事に騎手名・調教師名が書いてある場合は、必ずその名前を使うこと\n"
    "- 固有名詞は省略せず、正確に伝えること\n\n"
    "【内容について】\n"
    "- その記事で「何が一番ニュース価値があるか」を判断して、そこを中心に伝えること\n"
    "- 【数字・金額・変更点は必ず含めること】記事に金額・賞金・倍率・着順・タイム・頭数・増減など具体的な数字が書いてある場合は、必ずそれを脚本に含めること。数字こそがニュースの核心であることが多い\n"
    "- 「〇〇が変わった」「〇〇が増額された」などの変更・発表がある記事では、変更前と変更後の両方の数字・内容を明示すること\n"
    "- 記事に書かれている情報を丁寧に伝えること。必ず150文字以上・250文字以内にすること\n"
    "- 必ず複数の文（句点「。」が2つ以上）で構成すること。1文だけで終わらないこと\n"
    "- 情報が少ない記事でも、記事に書かれた内容を複数の角度から言い換えて150文字程度にまとめること\n"
    "- 無理に膨らませたり、ニュースにない情報を補わないこと\n"
    "- 必ず句点「。」で終わること（文の途中で終わらないこと）\n"
    "- 「詳細は〇〇のウェブサイトをご確認ください」「詳しくは〇〇をご覧ください」など、外部サイトへの誘導文は絶対に含めないこと\n"
    "- 「このコメントは〇〇で公開されています」「この記事は〇〇に掲載されています」など、記事の出典・掲載場所に言及する文は絶対に含めないこと\n\n"
    "テキストのみ出力し、ト書きや記号は不要です。"
)

# 重賞レース結果速報モード用プロンプト（SCRIPT_MODE=results で使用）
RESULTS_SYSTEM_PROMPT = (
    "あなたはプロの競馬ニュースアナウンサーです。以下のレース結果ニュースを元に、YouTube動画用のナレーション脚本を日本語で作成してください。\n\n"
    "【スキップ条件：以下に当てはまる場合は「SKIP」とだけ出力してください】\n"
    "- 記事本文にレース結果（1着馬名・騎手名など）が一切書かれていない予想・登録記事\n"
    "- 記事本文が極めて短く、具体的な情報が何もない\n\n"
    "【最重要：絶対に守るルール】\n"
    "- 提供されたニュース本文に書かれていること「だけ」を話すこと\n"
    "- ニュース本文に書かれていない情報は1文字も追加しないこと（推測・補足・創作すべて禁止）\n"
    "- 「こんにちは」「みなさん」などの呼びかけ・挨拶は禁止\n"
    "- いきなりレース結果の核心から始めること\n\n"
    "【結果速報として必ず含める内容（記事に書いてある場合）】\n"
    "- レース名・開催場・距離・馬場状態\n"
    "- 1着馬名・騎手名・調教師名（あれば馬番・人気）\n"
    "- 2着・3着馬名（記事に書いてあれば）\n"
    "- タイム・レコードの有無\n"
    "- 単勝配当・馬連配当・3連単配当など（記事に書いてあれば）\n"
    "- 波乱か本命決着かのひと言\n"
    "- 勝ち馬の次走予定・重要コメント（記事に書いてあれば）\n\n"
    "【文体について】\n"
    "- 必ず150文字以上・280文字以内にすること\n"
    "- 必ず複数の文（句点「。」が2つ以上）で構成すること\n"
    "- 必ず句点「。」で終わること\n"
    "- 数字・馬名・騎手名は原文通りに使うこと\n"
    "- 外部サイトへの誘導文・出典言及は含めないこと\n\n"
    "テキストのみ出力し、ト書きや記号は不要です。"
)


def get_system_prompt() -> str:
    """環境変数 SCRIPT_MODE に応じてシステムプロンプトを返す。"""
    mode = os.environ.get("SCRIPT_MODE", "news").strip().lower()
    if mode == "results":
        print("スクリプトモード: results（重賞結果速報）")
        return RESULTS_SYSTEM_PROMPT
    return SYSTEM_PROMPT


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
        print(f"利用可能モデル ({len(available)}個): {available[:10]}")
        return available
    except Exception as e:
        safe_msg = str(e).replace(api_key, "***")
        print(f"  [警告] ListModels失敗: {safe_msg}", file=sys.stderr)
        return []


class QuotaExceeded(Exception):
    pass


def call_gemini(api_key: str, model_name: str, prompt: str, system_prompt: str = "") -> str:
    url = f"{GEMINI_API_BASE}/{model_name}:generateContent"
    body: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 1200, "temperature": 0.4},
    }
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    for attempt, wait in enumerate([0, 30, 60]):
        if wait:
            print(f"  {wait}秒待機後にリトライ... (attempt {attempt + 1})")
            time.sleep(wait)
        resp = requests.post(url, json=body, params={"key": api_key}, timeout=30)
        print(f"  HTTP {resp.status_code}")
        if resp.status_code == 429:
            err = resp.json().get("error", {})
            print(f"  [警告] 429 クォータ超過: {err.get('message','')[:200]}", file=sys.stderr)
            continue
        if resp.status_code == 503:
            print(f"  [警告] 503 サービス一時停止。リトライします。", file=sys.stderr)
            continue
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            safe_msg = str(e).replace(api_key, "***")
            raise requests.exceptions.HTTPError(safe_msg) from None
        data = resp.json()
        try:
            candidate = data["candidates"][0]
            finish_reason = candidate.get("finishReason", "UNKNOWN")
            # 全partsを結合して返す
            text = "".join(p.get("text", "") for p in candidate["content"]["parts"]).strip()
            if finish_reason not in ("STOP", "MAX_TOKENS"):
                print(f"  [警告] finishReason={finish_reason} ({len(text)}文字)", file=sys.stderr)
            elif finish_reason == "MAX_TOKENS":
                print(f"  [警告] finishReason=MAX_TOKENS: トークン上限で打ち切り ({len(text)}文字)", file=sys.stderr)
            return text
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
    print(f"使用候補モデル: {candidates}")

    # (APIキー, モデル名) の全組み合わせリスト（キー優先でローテーション）
    key_model_pairs = [(key, model) for key in api_keys for model in candidates]
    print(f"試行組み合わせ数: {len(key_model_pairs)} (キー×モデル)")

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    def generate_one(args):
        i, item = args
        summary_text = item.get('summary', '')
        print(f"\n--- 記事[{i}]: {item['title'][:60]} ---")
        print(f"[{i}] Gemini入力本文 {len(summary_text)}文字: {summary_text[:120]!r}")
        sys_prompt = get_system_prompt()
        user_content = (
            f"【ニュース】\n"
            f"タイトル: {item['title']}\n"
            f"内容: {summary_text[:1500]}"
        )
        lenient_sys_prompt = (
            sys_prompt
            + "\n\n【追加指示】元のニュース本文には情報が含まれています。"
            "SKIPは内容が本当にゼロの場合のみ使用してください。"
            "情報が少なくても、記事に書かれていることを最大限活用して必ず脚本を生成してください。"
        )
        skip_count = 0
        for key, model_name in key_model_pairs:
            key_label = f"***{key[-4:]}"
            print(f"[{i}] 使用: key={key_label} model={model_name}")
            current_sys = lenient_sys_prompt if skip_count > 0 else sys_prompt
            try:
                script = call_gemini(key, model_name, user_content, system_prompt=current_sys)
                print(f"[{i}]  [Gemini生出力 {len(script)}文字]: {script!r}")
                # 内容が薄い記事はスキップ（初回は強調プロンプトで再試行）
                if script.strip().upper() == "SKIP":
                    if skip_count == 0:
                        skip_count += 1
                        print(f"[{i}]  → SKIPが返されました。強調プロンプトで再試行します（入力: {len(summary_text)}文字）")
                        continue
                    print(f"[{i}]  → 再試行後もSKIPのためスキップ（動画生成しない）")
                    return i, True
                # プロンプトリーク検出：システムプロンプトの文言が混入している場合はリトライ
                import re as _re
                PROMPT_LEAK_PATTERNS = [
                    "提供されたニュース本文に書かれていること",
                    "ニュース本文に書かれていない情報は",
                    "推測・補足・創作すべて禁止",
                    "【最重要：絶対に守るルール】",
                    "【スキップ条件",
                    "の指示:",
                ]
                if any(p in script for p in PROMPT_LEAK_PATTERNS):
                    print(f"[{i}]  [警告] プロンプトリーク検出。次のキー/モデルへ切り替えます。", file=sys.stderr)
                    continue
                redirect_pattern = _re.compile(
                    r"[^。]*(?:"
                    r"(?:詳細|詳しく|詳しい情報|最新情報)[^。]*(?:サイト|ウェブ|ページ|公式|こちら|ご確認|ご覧)"
                    r"|(?:この(?:コメント|記事|情報|内容))[^。]*(?:公開|掲載)されています"
                    r"|[^。]*(?:記事|ページ|サイト)[^。]*(?:公開|掲載)されています"
                    r"|[^。]*(?:予想|情報|ニュース)[^。]*について(?:報道|掲載|公開|紹介)されました"
                    r"|[^。]*(?:動画|番組)[^。]*(?:後半|前半|解説|紹介)[^。]*をお届けします"
                    r"|[^。]*(?:解説|情報|視点)をお届けします"
                    r"|[^。]*(?:Yahoo|Google|ヤフー|グーグル)[^。]*(?:ニュース|News)[^。]*(?:提供|配信|掲載|集約)[^。]*(?:されています|されました|しています)"
                    r"|[^。]*(?:ニュース|情報)[^。]*(?:提供元|配信元|ソース)[^。]*(?:から|より)[^。]*(?:提供|配信)[^。]*(?:されています|されました)"
                    r"|[^。]*(?:最新情報|ニュース)[^。]*(?:掲載されました|掲載されています|公開されました|公開されています)"
                    r"|[^。]*(?:No\.1競馬|利用者数\d+万人|netkeiba公式|netkeiba姉妹|netkeiba 姉妹)[^。]*"
                    r"|[^。]*フィルタ(?:ON|OFF)[^。]*"
                    r"|[^。]*コメント非表示[^。]*"
                    r"|[^。]*(?:公式SNS|SNSも展開)[^。]*(?:しています|しました|います)"
                    r")[^。]*。"
                )
                _before_redirect = len(script)
                script = redirect_pattern.sub("", script).strip()
                if len(script) != _before_redirect:
                    print(f"[{i}]  [redirectフィルター] {_before_redirect}文字 → {len(script)}文字")
                # 三点リーダー（…）が含まれている場合は除去して句点前まで切る
                if "…" in script or "..." in script:
                    script = script.replace("...", "").replace("…", "")
                    script = script.strip()
                    last_period = script.rfind("。")
                    script = script[:last_period + 1] if last_period != -1 else ""
                # フィルター後に内容がなくなった場合はスキップ
                if not script or len(script) < 80:
                    print(f"[{i}]  → フィルター後に内容が不十分（{len(script)}文字）のためスキップ")
                    return i, True
                # 「〇〇が紹介されています」など記事の存在を説明するだけの間接表現を検出
                indirect_pattern = _re.compile(
                    r"(?:コメント|情報|内容|記事|動向|経緯|詳細|見解|意気込み|期待)[^。]*"
                    r"(?:紹介されています|紹介されました|掲載されています|掲載されました|報じられています|報じられました)"
                )
                if indirect_pattern.search(script):
                    print(f"[{i}]  → 間接表現（記事の存在説明）を検出。次のキー/モデルへ切り替えます: {script[:60]}", file=sys.stderr)
                    continue
                # コード・JavaScript が混入している場合はスキップ
                code_leak_pattern = _re.compile(
                    r"window\.[A-Za-z_]\w*\s*[=({]"
                    r"|function\s*\("
                    r"|var\s+\w+\s*="
                    r"|const\s+\w+\s*="
                    r"|let\s+\w+\s*="
                    r"|\{\s*['\"]?\w+['\"]?\s*:"
                    r"|=>|&&|\|\|"
                    r"|document\.|window\.|console\."
                )
                if code_leak_pattern.search(script):
                    print(f"[{i}]  → コード混入を検出。次のキー/モデルへ切り替えます: {script[:60]}", file=sys.stderr)
                    continue
                # 未来レース記事なのにレース結果・展開の創作が含まれている場合はスキップ
                future_race_keywords = _re.compile(
                    r"今日発走|本日発走|発走予定|出走予定|今日の(?:レース|競馬)|今週(?:の)?(?:レース|競馬|注目)|"
                    r"今後|展望|注目馬|出走登録|登録馬|今後の(?:レース|出走)"
                )
                fabricated_result_pattern = _re.compile(
                    r"抜け出した|抜け出し[。、]|逃げ切った|逃げ切り[。、]|差し切った|差し切り[。、]|"
                    r"押し切った|押し切り[。、]|突き抜けた|粘り切った|"
                    r"(?:が|は)制した|(?:が|は)勝利した|(?:が|は)優勝した|(?:が|は)快勝した|"
                    r"(?:が|は)連覇した|(?:が|は)勝ち切った"
                )
                article_text = item.get("title", "") + item.get("body", item.get("summary", ""))
                if future_race_keywords.search(article_text) and fabricated_result_pattern.search(script):
                    print(f"[{i}]  → 未来レース記事にレース結果の創作を検出。次のキー/モデルへ切り替えます: {script[:60]}", file=sys.stderr)
                    continue
                # 馬を抽象的に表現している場合はスキップ
                # 「ある馬」「2着となった馬」「スプリンターたち」「注目激走馬」など、馬名なしの曖昧表現を検出
                abstract_horse_pattern = _re.compile(
                    r"(?:ある馬|その馬|この馬|同馬|該当馬|"
                    r"\d+着(?:と)?なった馬|\d+着の馬|"
                    r"優勝した馬|勝利した馬|連覇した馬|"
                    r"注目激走馬|注目の激走馬|注目馬(?!の[ァ-ン])|"
                    r"スプリンターたち|出走馬たち|各馬(?:が|は|も|に)|強豪馬たち|"
                    r"馬たちが|馬たちは|一堂に会)"
                )
                if abstract_horse_pattern.search(script):
                    print(f"[{i}]  → 馬名を使わず抽象表現のためスキップ: {script[:60]}")
                    return i, True
                # 文の途中で終わっている場合は最後の句点で切る
                if script and not script.endswith("。"):
                    last_period = script.rfind("。")
                    if last_period != -1:
                        script = script[:last_period + 1]
                out_path = Path(f"{OUTPUT_DIR}/script_{i}.txt")
                out_path.write_text(script, encoding="utf-8")
                print(f"[{i}]  → {out_path} 保存 ({len(script)}文字)")
                print(f"[{i}]  プレビュー: {script[:80]}...")
                return i, True
            except QuotaExceeded:
                print(f"[{i}]  [key={key_label} / {model_name}] クォータ超過。20秒待機後に次へ切り替えます。", file=sys.stderr)
                time.sleep(20)
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

    written = list(Path(OUTPUT_DIR).glob("script_*.txt"))
    if not written:
        print("\n全ての記事がスキップされました。動画生成をスキップします。")
        sys.exit(0)

    print(f"\n{len(written)} 件の脚本を生成しました。")


if __name__ == "__main__":
    main()
