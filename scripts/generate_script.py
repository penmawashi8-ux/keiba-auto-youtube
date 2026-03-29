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
    "あなたはプロの競馬ニュースアナウンサーです。以下のニュースを元に、YouTube動画用のナレーション脚本を日本語で作成してください。\n\n"
    "【スキップ条件：以下に当てはまる場合は「SKIP」とだけ出力してください】\n"
    "- 記事本文に馬名・騎手名・調教師名・レース結果・予想根拠などの具体的な固有名詞が一切含まれていない\n"
    "- 「〇〇を公開しました」「〇〇に関する情報が掲載されました」など、実際の内容がリンク先にしかない記事\n"
    "- 「詳細はこちら」「続きを読む」など、内容のない案内だけの記事\n"
    "- 予想記事なのに、どの馬が有力か・その理由・根拠が本文に書かれていない\n"
    "- 「記事には〇〇が掲載されている」「〇〇についての情報があります」など、記事の存在を説明するだけで中身がない\n"
    "- 視聴者が「で、結局何なの？」と思うような、具体性ゼロの内容しか伝えられない場合\n"
    "- 記者・ライター・トラックマン(TM)が予想を公開した、的中した、馬券を当てた等を主題とする記事（レース結果や馬の情報ではなく、記者個人の予想活動が中心の記事）\n"
    "- 「〇〇へのインタビューが実施されています」「インタビューが公開されました」など、インタビューの存在を告知するだけで内容が一切書かれていない記事\n"
    "- 「〇〇レースが〇月〇日に開催されます/行われます」という開催日程だけで、出走馬・騎手・注目馬・見どころなどの具体的な情報が一切ない記事\n"
    "- 「〇〇において消せる馬に関する情報です」「〇〇についての情報をお伝えします」など、タイトルをそのまま言い換えただけで実際の内容が何も伝わらない記事\n"
    "- 「〇〇という記事が△△から公開されました」など、記事タイトルと掲載元を紹介するだけで記事の中身が何も書かれていない記事\n\n"
    "【最重要：絶対に守るルール】\n"
    "- 提供されたニュース本文に書かれていること「だけ」を話すこと\n"
    "- ニュース本文に書かれていない情報は1文字も追加しないこと（推測・補足・創作すべて禁止）\n"
    "- 出走予定・登録・今後開催予定のレースは必ず「予定」「見込み」「行われます」など未来形・予定形で伝えること\n"
    "- 【時制の厳守】記事がレース予告・出走登録・今後の予定を伝えている場合、「行われました」「開催されました」「出走しました」などの過去形は絶対に使わないこと。必ず「行われます」「予定です」「出走します」など未来形を使うこと\n"
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
    "- 必ず5文以上・300文字以上500文字以内で書くこと。これは絶対条件であり、短くまとめることは禁止\n"
    "- レース結果・着順・タイム・馬体重・馬場状態など、記事にある数値・事実をできるだけ盛り込むこと\n"
    "- 騎手のコメントが記事にある場合は、そのコメントを必ず引用して伝えること（「○○騎手は『…』と語りました」など）\n"
    "- 調教師・馬主・血統など、記事に書かれた補足情報も積極的に使うこと\n"
    "- 情報が少ない記事でも、記事に書かれた内容を5文以上で丁寧に言い換えて300文字以上にすること\n"
    "- 無理に膨らませたり、ニュースにない情報を補わないこと\n"
    "- 必ず句点「。」で終わること（文の途中で終わらないこと）\n"
    "- 「詳細は〇〇のウェブサイトをご確認ください」「詳しくは〇〇をご覧ください」など、外部サイトへの誘導文は絶対に含めないこと\n"
    "- 「このコメントは〇〇で公開されています」「この記事は〇〇に掲載されています」など、記事の出典・掲載場所に言及する文は絶対に含めないこと\n\n"
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
        "generationConfig": {"maxOutputTokens": 1200, "temperature": 0.4},
    }
    for attempt, wait in enumerate([0, 5, 15]):
        if wait:
            print(f"  {wait}秒待機後にリトライ... (attempt {attempt + 1})")
            time.sleep(wait)
        resp = requests.post(url, json=body, params={"key": api_key}, timeout=30)
        print(f"  HTTP {resp.status_code}")
        if resp.status_code == 429:
            err = resp.json().get("error", {})
            msg = err.get("message", "")
            print(f"  [警告] 429 クォータ超過: {msg[:200]}", file=sys.stderr)
            # 日次上限(free_tier_requests/input_token_count)は待機しても無意味 → 即切り替え
            if "free_tier" in msg or attempt >= 1:
                raise QuotaExceeded(model_name)
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
            f"内容: {item.get('summary', '')[:3000]}"
        )
        for key, model_name in key_model_pairs:
            key_label = f"***{key[-4:]}"
            print(f"[{i}] 使用: key={key_label} model={model_name}")
            try:
                script = call_gemini(key, model_name, prompt)
                # 内容が薄い記事はスキップ
                if script.strip().upper() == "SKIP":
                    print(f"[{i}]  → 内容が薄いためスキップ（動画生成しない）")
                    return i, True
                # 外部サイト誘導文を文単位で除去
                import re as _re
                redirect_pattern = _re.compile(
                    r"[^。]*(?:"
                    r"(?:詳細|詳しく|詳しい情報|最新情報)[^。]*(?:サイト|ウェブ|ページ|公式|こちら|ご確認|ご覧)"
                    r"|(?:この(?:コメント|記事|情報|内容))[^。]*(?:公開|掲載)されています"
                    r"|[^。]*(?:記事|ページ|サイト)[^。]*(?:公開|掲載)されています"
                    r"|[^。]*(?:予想|情報|ニュース)[^。]*について(?:報道|掲載|公開|紹介)されました"
                    r"|[^。]*(?:動画|番組)[^。]*(?:後半|前半|解説|紹介)[^。]*をお届けします"
                    r"|[^。]*(?:解説|情報|視点)をお届けします"
                    r"|[^。]*インタビュー(?:が|は|も)(?:実施|公開|掲載|配信)されています"
                    r"|[^。]*インタビュー(?:が|は|も)(?:実施|公開|掲載|配信)されました"
                    r"|[^。]*に関する情報です"
                    r"|[^。]*という記事が[^。]*(?:公開|掲載|配信)されました"
                    r"|[^。]*という記事が[^。]*(?:公開|掲載|配信)されています"
                    r")[^。]*。"
                )
                script = redirect_pattern.sub("", script).strip()
                # 三点リーダー（…）が含まれている場合は除去して句点前まで切る
                if "…" in script or "..." in script:
                    script = script.replace("...", "").replace("…", "")
                    script = script.strip()
                    last_period = script.rfind("。")
                    script = script[:last_period + 1] if last_period != -1 else ""
                # フィルター後に内容がなくなった場合はスキップ
                if not script or len(script) < 30:
                    print(f"[{i}]  → フィルター後に内容がなくなったためスキップ")
                    return i, True
                # 150文字未満は短すぎる → 次のキー/モデルで再試行
                if len(script) < 150:
                    print(f"[{i}]  → スクリプトが短すぎる({len(script)}文字)。次のキー/モデルで再試行")
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
                # 1文だけで開催日程・存在告知しか書かれていないスクリプトをスキップ
                sentences = [s for s in script.split("。") if s.strip()]
                if len(sentences) == 1 and _re.search(
                    r"(?:行われました|開催されました|実施されました|行われます|開催されます|行われる予定|開催される予定)",
                    script,
                ):
                    print(f"[{i}]  → 開催日程のみ1文で内容が薄いためスキップ: {script[:60]}")
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
                # 次の記事への連続リクエストを避けるため少し待機
                if i < len(news_items) - 1:
                    time.sleep(2)
                return i, True
            except QuotaExceeded:
                print(f"[{i}]  [key={key_label} / {model_name}] クォータ超過。次へ切り替えます。", file=sys.stderr)
        print(f"[{i}] [エラー] 全キー・全モデルでクォータ超過。", file=sys.stderr)
        return i, False

    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = {executor.submit(generate_one, (i, item)): i for i, item in enumerate(news_items)}
        # 注: max_workers=1 で順次実行されるため、submitの順番通りに処理される
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
