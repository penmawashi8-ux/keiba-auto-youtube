#!/usr/bin/env python3
"""news.jsonの各記事ごとにGemini APIでナレーション脚本を生成し、output/script_N.txtに保存する。"""

import json
import os
import random
import re
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
    "- いきなりニュースの核心から始めること\n"
    "- タイトルを冒頭に【タイトル】形式や「タイトル。」形式で繰り返さないこと\n"
    "- ウェブサイトのナビゲーション・サイドバー・掲示板リンク（「全成績と掲示板」「スポーツ報知」「スポニチ」「日刊スポーツ」などのサイト名・ページ名）は一切含めないこと\n"
    "- 同じ文章を2回以上繰り返さないこと\n\n"
    "【固有名詞について：最も重要】\n"
    "- 記事に馬名が書いてある場合は、必ずその馬名を使うこと\n"
    "- 「ある馬」「その馬」「2着となった馬」「スプリンターたちが一堂に会し」のような馬名なしの抽象表現は絶対禁止\n"
    "- 記事に馬名がなく「世界最強のスプリンターたちが集う」程度の情報しかない場合はSKIPすること\n"
    "- 記事に騎手名・調教師名が書いてある場合は、必ずその名前を使うこと\n"
    "- 固有名詞は省略せず、正確に伝えること\n"
    "- 馬名・騎手名が不確かな場合は「?」「..」「…」などの記号で補わず、記事本文に書かれている文字だけを使うこと\n"
    "- 馬名・騎手名の直後に続く括弧書き（性別・年齢・所属・厩舎・読み仮名など）は絶対に含めないこと。\n"
    "  例：「チャリングクロス（牡3、美浦・奥村武厩舎）」→「チャリングクロス」\n"
    "  例：「メイショウボヌール(牝5＝森沢、父ミッキーアイル)」→「メイショウボヌール」\n"
    "  例：「高橋洸佑(こう、18＝保利平)」→「高橋洸佑」\n\n"
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
    "- いきなりレース結果の核心から始めること\n"
    "- タイトルを冒頭に【タイトル】形式で繰り返さないこと\n"
    "- ウェブナビゲーション・掲示板リンク・サイト名は含めないこと\n"
    "- 同じ文章を繰り返さないこと\n\n"
    "【結果速報として必ず含める内容（記事に書いてある場合）】\n"
    "- レース名・開催場・距離・馬場状態\n"
    "- 1着馬名・騎手名・調教師名（あれば馬番・人気）。馬名・騎手名直後の括弧書き（性別・年齢・厩舎・読み仮名など）は不要。例：「メイショウボヌール(牝5＝森沢、父ミッキーアイル)」→「メイショウボヌール」、「高橋洸佑(こう、18＝保利平)」→「高橋洸佑」\n"
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


def fact_check_script(
    api_key: str, model_name: str,
    article_title: str, article_body: str, script: str,
) -> tuple[bool, str]:
    """生成された脚本が元記事の内容に忠実かGeminiでファクトチェックする。
    Returns: (is_ok, reason)
    gemmaモデルは精度が低いためスキップ（OK扱い）。
    ファクトチェックAPI自体が失敗した場合もOK扱いとしてブロックしない。
    """
    if model_name.startswith("gemma"):
        return True, "SKIPPED(gemma)"

    prompt = (
        "以下の【元のニュース記事】と【生成されたナレーション脚本】を照合してください。\n\n"
        f"【元のニュース記事】\n"
        f"タイトル: {article_title}\n"
        f"本文: {article_body[:1200]}\n\n"
        f"【生成されたナレーション脚本】\n{script}\n\n"
        "【判定基準】脚本に以下が含まれていればNGです:\n"
        "- 元記事に存在しない馬名・騎手名・調教師名などの固有名詞\n"
        "- 元記事に書かれていないレース結果・着順・勝敗\n"
        "- 元記事が予定・展望記事なのに脚本にレース結果・展開の描写がある\n"
        "- 元記事に存在しない賞金額・配当・オッズ・タイムなどの数字\n\n"
        "問題がなければ「OK」とだけ答えてください。\n"
        "NGの場合は「NG: （元記事にない具体的な内容）」の形式で答えてください。\n"
        "「本日の競馬ニュースです。」などの導入フレーズはチェック対象外です。"
    )
    url = f"{GEMINI_API_BASE}/{model_name}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 256, "temperature": 0.1},
    }
    try:
        resp = requests.post(url, json=body, params={"key": api_key}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        result = "".join(
            p.get("text", "") for p in data["candidates"][0]["content"]["parts"]
        ).strip()
        is_ok = result.upper().startswith("OK")
        return is_ok, result
    except Exception as e:
        print(f"  [警告] ファクトチェックAPI失敗: {e}", file=sys.stderr)
        return True, f"SKIPPED(error:{type(e).__name__})"


# 書き出しパターン（毎動画ランダム選択してGeminiに指示する）
_OPENING_PATTERNS = [
    "本日の競馬ニュースです。",
    "競馬速報です。",
    "最新のレース情報が入ってきました。",
    "注目の競馬情報をお届けします。",
    "今週の競馬注目情報をお伝えします。",
    "レース関連の最新情報です。",
    "今日の競馬情報です。",
    "重要な競馬ニュースをお伝えします。",
    "競馬ファン注目の情報です。",
    "最新情報をお届けします。",
]


def _check_consecutive_endings(script: str, idx: int) -> None:
    """同じ語尾が3文以上連続している場合に警告を出す（品質チェック）。"""
    sentences = [s.strip() for s in script.split("。") if len(s.strip()) >= 2]
    if len(sentences) < 3:
        return
    endings = [s[-2:] for s in sentences]
    for i in range(len(endings) - 2):
        if endings[i] == endings[i + 1] == endings[i + 2]:
            print(
                f"[{idx}]  [警告] 語尾の連続パターン検出: 「〜{endings[i]}」が3文以上連続",
                file=sys.stderr,
            )
            return


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
    # gemma モデルは systemInstruction 非対応のため、システムプロンプトをユーザー入力に結合する
    is_gemma = model_name.startswith("gemma")
    if is_gemma and system_prompt:
        full_prompt = system_prompt + "\n\n" + prompt
    else:
        full_prompt = prompt
    body: dict = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.4},
    }
    if system_prompt and not is_gemma:
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
        # 書き出しパターンをランダム選択（動画ごとに変化させる）
        opening_pattern = random.choice(_OPENING_PATTERNS)
        print(f"\n--- 記事[{i}] 書き出しパターン: 「{opening_pattern}」 ---")

        summary_text = item.get('summary', '')
        # ウェブナビゲーション的な文言を除去してからGeminiに渡す
        # 例: 「ジュウリョクピエロの全成績と掲示板」「今村聖奈の全成績」など
        summary_text = re.sub(r'[^\n。]*(?:全成績と掲示板|全成績\s|全成績$|\s掲示板)[^\n。]*', '', summary_text)
        # 重複行を除去
        _seen_sum: list[str] = []
        _clean_sum: list[str] = []
        for _sl in summary_text.split('\n'):
            _norm_sl = re.sub(r'\s+', '', _sl).strip()
            if _norm_sl and _norm_sl not in _seen_sum:
                _seen_sum.append(_norm_sl)
                _clean_sum.append(_sl)
        summary_text = '\n'.join(_clean_sum).strip()
        print(f"\n--- 記事[{i}]: {item['title'][:60]} ---")
        print(f"[{i}] Gemini入力本文 {len(summary_text)}文字: {summary_text[:120]!r}")
        sys_prompt = get_system_prompt()
        user_content = (
            f"【ニュース】\n"
            f"タイトル: {item['title']}\n"
            f"内容: {summary_text[:1500]}\n\n"
            f"【書き出し指示】このナレーションは必ず「{opening_pattern}」という一文で始めること。"
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
                if "…" in script or "..." in script or ".." in script:
                    script = script.replace("...", "").replace("…", "").replace("..", "")
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
                # ただし、カタカナ5文字以上の固有名詞（≒競走馬名）がある場合は馬名あり扱いでスキップしない
                abstract_horse_pattern = _re.compile(
                    r"(?:ある馬|その馬|この馬|同馬|該当馬|"
                    r"\d+着(?:と)?なった馬|\d+着の馬|"
                    r"優勝した馬|勝利した馬|連覇した馬|"
                    r"注目激走馬|注目の激走馬|注目馬(?!の[ァ-ン])|"
                    r"スプリンターたち|出走馬たち|各馬(?:が|は|も|に)|強豪馬たち|"
                    r"馬たちが|馬たちは|一堂に会)"
                )
                horse_name_pattern = _re.compile(r'[ァ-ヴー]{5,}')
                if abstract_horse_pattern.search(script) and not horse_name_pattern.search(script):
                    print(f"[{i}]  → 馬名を使わず抽象表現のためスキップ: {script[:60]}")
                    return i, True
                # 馬名後の括弧書き（性別・年齢・所属・厩舎）を除去
                # 例: 「チャリングクロス（牡3、美浦・奥村武厩舎）」→「チャリングクロス」
                # 例: 「メイショウボヌール(牝5＝森沢、父ミッキーアイル)」→「メイショウボヌール」
                # 例: 「スティンガーグラス（牡5歳、栗東・友道康夫厩舎、父キズナ）」→「スティンガーグラス」
                script = _re.sub(r'[（(][牡牝セ騸]\d+歳?[、，,＝=][^）)]*?[）)]', '', script)
                # 騎手・人名後の括弧書き（読み仮名・年齢・所属）を除去
                # 例: 「高橋洸佑(こう、18＝保利平)」→「高橋洸佑」
                script = _re.sub(r'[（(][ぁ-ん]{1,6}[、，,]\d+[＝=][^）)]*?[）)]', '', script)
                # 冒頭の【タイトル】形式を除去（タイトル2重表示防止）
                script = _re.sub(r'^【[^】]{5,}】\s*', '', script).strip()
                # ウェブナビゲーション的な文言を含む文を除去
                # 例: 「スポーツ報知 全成績と掲示板」など
                _nav_pat = _re.compile(
                    r'[^。]*(?:全成績と掲示板|スポーツ報知|スポニチ|日刊スポーツ|競馬ブック)[^。]*。?'
                )
                _before_nav = len(script)
                script = _nav_pat.sub('', script).strip()
                if len(script) != _before_nav:
                    print(f"[{i}]  [ナビ文言除去] {_before_nav}文字 → {len(script)}文字")
                # 重複文を除去（同じ文が2回以上）
                _raw_sents_d = [s.strip() for s in script.split('。') if s.strip()]
                _seen_sents_d: list[str] = []
                _deduped_d: list[str] = []
                for _sd in _raw_sents_d:
                    _norm_sd = _re.sub(r'\s+', '', _sd)
                    if _norm_sd not in _seen_sents_d:
                        _seen_sents_d.append(_norm_sd)
                        _deduped_d.append(_sd + '。')
                _deduped_script = ''.join(_deduped_d).strip()
                if len(_deduped_script) != len(script):
                    print(f"[{i}]  [重複除去] {len(script)}文字 → {len(_deduped_script)}文字")
                script = _deduped_script
                # 空白の重複を整理
                script = _re.sub(r'　+', '　', script).strip()
                # 文の途中で終わっている場合は最後の句点で切る
                if script and not script.endswith("。"):
                    last_period = script.rfind("。")
                    if last_period != -1:
                        script = script[:last_period + 1]
                # ファクトチェック: 元記事にない情報が混入していないか検証
                fc_ok, fc_reason = fact_check_script(
                    key, model_name, item["title"], summary_text, script
                )
                if not fc_ok:
                    print(f"[{i}]  [ファクトチェックNG] {fc_reason[:150]}", file=sys.stderr)
                    print(f"[{i}]  → 元記事にない情報を検出。次のキー/モデルで再生成します。", file=sys.stderr)
                    continue
                print(f"[{i}]  ファクトチェック: {fc_reason[:60]}")

                out_path = Path(f"{OUTPUT_DIR}/script_{i}.txt")
                out_path.write_text(script, encoding="utf-8")
                print(f"[{i}]  → {out_path} 保存 ({len(script)}文字)")
                print(f"[{i}]  プレビュー: {script[:80]}...")
                _check_consecutive_endings(script, i)
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
