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
競馬ファン向けのYouTube Shorts「競馬知識クイズ」の問題を5問作成してください。

## 要件
- 視聴者は競馬に興味がある一般人〜中級者
- 問題の難易度は「普通」〜「やや難」
- 有名馬・伝説レース・競馬ルール・馬の知識など幅広いテーマから出題
- 答えは簡潔（数字・人名・馬名・用語など）
- 解説は2〜3文で面白い豆知識を含める

## 動画タイトル
「競馬知識クイズ！何問正解できる？」

## 出力形式（JSONのみ、マークダウン不可）
{
  "title": "競馬知識クイズ！何問正解できる？",
  "questions": [
    {
      "number": 1,
      "display_question": "スライドに表示する問題文（30字以内）",
      "display_answer": "スライドに表示する答え（10字以内）",
      "display_explanation": "スライドに表示する解説（40字以内）",
      "tts_question": "ナレーションで読む問題文（句読点含む、自然な話し言葉）",
      "tts_answer": "ナレーションで読む答えと解説（句読点含む、2〜3文）"
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
