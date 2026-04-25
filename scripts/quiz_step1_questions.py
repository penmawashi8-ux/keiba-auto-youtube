#!/usr/bin/env python3
"""Step 1: Claude API で競馬知識クイズ問題を生成して quiz.json に保存"""

import json
import sys
import os
import re

MODEL = "claude-opus-4-7"
NUM_QUESTIONS = 5


def generate_questions(api_key: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    prompt = """\
競馬ファン向けのYouTube動画「名馬当てクイズ」の問題を5問作成してください。

## フォーマット
- G1勝利歴のある有名馬を1頭選び、その馬が勝った代表的なレース名と年を3つヒントとして提示する
- 視聴者は4択からその馬の名前を当てる
- 5問全て異なる馬・異なる時代（1990年代〜2020年代）から出題する
- 不正解の選択肢3つは同時代・同距離適性の有名馬にする（難易度を上げるため）
- 解説は40字以内で馬の特徴や記録を1文で

## 出力形式（JSONのみ、マークダウン不可）
{
  "title": "名馬当てクイズ！この馬は誰？",
  "questions": [
    {
      "number": 1,
      "clues": ["YYYY年 レース名", "YYYY年 レース名", "YYYY年 レース名"],
      "choices": ["馬名A", "馬名B", "馬名C", "馬名D"],
      "correct_index": 0,
      "display_explanation": "解説文（40字以内）",
      "tts_question": "第N問！この馬は誰でしょう？ヒントは3つ。[cluesを読み上げ]。選択肢はA、[馬名A]。B、[馬名B]。C、[馬名C]。D、[馬名D]。さあ、どの馬でしょうか？",
      "tts_answer": "正解は[正解ラベル]、[馬名]です！[解説2〜3文]"
    }
  ]
}

JSONのみを出力し、前後に説明文を加えないこと。
"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )

    raw = ""
    for block in response.content:
        if block.type == "text":
            raw = block.text
            break

    # JSONを抽出（```json...``` などのフェンスがあれば除去）
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(f"JSONが見つかりません: {raw[:200]}")

    return json.loads(m.group(0))


def main():
    print("=== Step 1: クイズ問題生成 (Claude API) ===")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)

    print(f"モデル: {MODEL} / 問題数: {NUM_QUESTIONS}問")
    print("生成中...")

    quiz = generate_questions(api_key)

    questions = quiz.get("questions", [])
    print(f"  {len(questions)}問生成されました")

    for q in questions:
        print(f"  Q{q['number']}: {q['display_question']}")

    with open("quiz.json", "w", encoding="utf-8") as f:
        json.dump(quiz, f, ensure_ascii=False, indent=2)

    print("\nquiz.json に保存しました")
    print("完了")


if __name__ == "__main__":
    main()
