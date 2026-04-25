#!/usr/bin/env python3
"""Step 5: ranking.csv をもとに YouTube 動画台本を生成して script.txt に保存"""

import csv
import os
import sys
import json
from pathlib import Path


def load_ranking(path="ranking.csv"):
    if not Path(path).exists():
        print(f"ERROR: {path} が見つかりません")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_prompt(rows):
    top10 = rows[:10]

    ranking_summary = []
    for h in top10:
        line = (
            f"{h['score_rank']}位: {h['name']} "
            f"(G1勝利:{h['g1_wins']}回, 総合スコア:{h['total_score']}, "
            f"賞金順位:{h['prize_rank']}位)"
        )
        ranking_summary.append(line)

    # 賞金順位との乖離が大きい馬を抽出
    interesting = []
    for h in top10:
        try:
            diff = int(h["prize_rank"]) - int(h["score_rank"])
        except (ValueError, TypeError):
            diff = 0
        if abs(diff) >= 3:
            direction = "大幅上昇" if diff > 0 else "大幅下降"
            interesting.append(f"{h['name']}（スコア{h['score_rank']}位→賞金{h['prize_rank']}位: {direction}）")

    ranking_text = "\n".join(ranking_summary)
    interesting_text = "\n".join(interesting) if interesting else "（特になし）"

    prompt = f"""あなたはプロの競馬YouTuberです。
以下のデータをもとに、YouTube動画の台本を作成してください。

【ランキングデータ（上位10頭）】
{ranking_text}

【賞金ランキングと大きく順位が変わった馬】
{interesting_text}

【台本の要件】
・尺：10分（6000字前後）
・ナレーション形式。句読点を多めに入れて、読みやすく自然な日本語で書いてください
・冒頭30秒（約200〜300字）：賞金ランキングと大きく順位が変わった馬をネタバレして、視聴者の興味を引く導入を作ってください
・本編：10位→1位のカウントダウン形式
・各馬の紹介内容：
  - スコア内訳の解説（G1勝利数、安定性、強敵撃破、着差、時代補正の各スコア）
  - 競走馬としての特徴や名レース、史実に基づいたエピソード
  - ファンが「なるほど！」と思える分析コメント
・締め（約200字）：「このランキングはおかしい！という馬をコメントで教えてください」で終わらせてください

【重要な指示】
・台本のみを出力してください。冒頭に「台本：」などのラベルは不要です
・「では始めましょう」「さて」など自然なつなぎ言葉を使ってください
・馬名は正確に使い、AIの推測で事実を歪めないでください
・スコアデータに基づいた客観的な分析を心がけてください
"""
    return prompt


def generate_with_gemini(prompt):
    """Gemini API で台本生成"""
    import google.generativeai as genai

    api_keys = [
        os.environ.get("GEMINI_API_KEY", ""),
        os.environ.get("GEMINI_API_KEY_2", ""),
        os.environ.get("GEMINI_API_KEY_3", ""),
    ]
    api_keys = [k for k in api_keys if k]

    if not api_keys:
        raise EnvironmentError("GEMINI_API_KEY が設定されていません")

    models_to_try = [
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]

    last_error = None
    for key in api_keys:
        genai.configure(api_key=key)
        for model_name in models_to_try:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.85,
                        max_output_tokens=8192,
                    ),
                )
                text = response.text.strip()
                if len(text) >= 1000:
                    print(f"  Gemini ({model_name}) で生成成功: {len(text)}字")
                    return text
                print(f"  {model_name}: 出力が短すぎます ({len(text)}字)")
            except Exception as e:
                last_error = e
                print(f"  {model_name} エラー: {e}")
                continue

    raise RuntimeError(f"Gemini API すべて失敗: {last_error}")


def generate_fallback_script(rows):
    """Gemini が使えない場合のフォールバック台本"""
    top10 = list(reversed(rows[:10]))  # 10位→1位

    lines = []
    lines.append(
        "皆さん、こんにちは！競馬動画チャンネルへようこそ。"
        "今日は、G1勝利数・安定性・強敵撃破数・着差・時代補正の五つの指標で算出した、"
        "独自の最強馬ランキングTOP10を発表します！"
    )

    for h in rows[:10]:
        prize_rank = int(h["prize_rank"]) if h["prize_rank"] else 999
        score_rank = int(h["score_rank"])
        diff = prize_rank - score_rank
        if abs(diff) >= 3:
            direction = "高く" if diff > 0 else "低く"
            lines.append(
                f"なお、{h['name']}は賞金ランキングより{abs(diff)}順位{direction}評価されています。"
                "このギャップがなぜ生まれるのか、本編でじっくり解説します。"
            )
            break

    lines.append("\nでは早速、カウントダウン形式で発表していきましょう。")

    for rank, h in enumerate(top10, 1):
        actual_rank = 10 - rank + 1
        lines.append(f"\n─────────────────")
        lines.append(f"第{actual_rank}位！{h['name']}！")
        lines.append(
            f"{h['name']}のG1勝利数は{h['g1_wins']}勝。"
            f"G1勝利スコアは{h['score_g1']}点、安定スコアは{h['score_stability']}点、"
            f"強敵撃破スコアは{h['score_upset']}点、着差スコアは{h['score_margin']}点、"
            f"時代補正スコアは{h['score_era']}点で、総合スコアは{h['total_score']}点です。"
        )
        lines.append(
            f"賞金ランキングでの順位は{h['prize_rank']}位となっており、"
            "このスコアとの差が、この馬の真の強さを示しています。"
        )

    lines.append(
        "\n以上が、独自指標による歴代最強馬ランキングTOP10でした。"
        "皆さんはどう思いましたか？"
        "このランキングはおかしい！という馬がいたら、ぜひコメントで教えてください。"
        "チャンネル登録と高評価もよろしくお願いします。それではまた次の動画でお会いしましょう！"
    )

    return "\n".join(lines)


def main():
    print("=== Step 5: 台本生成 ===")

    rows = load_ranking()
    prompt = build_prompt(rows)

    try:
        script = generate_with_gemini(prompt)
    except Exception as e:
        print(f"Gemini失敗: {e}")
        print("フォールバック台本を生成します...")
        script = generate_fallback_script(rows)

    with open("script.txt", "w", encoding="utf-8") as f:
        f.write(script)

    char_count = len(script)
    print(f"script.txt に保存しました ({char_count}字)")

    if char_count < 4000:
        print(f"WARNING: 台本が短すぎます ({char_count}字)。手動で確認・編集してください。")

    print("完了")


if __name__ == "__main__":
    main()
