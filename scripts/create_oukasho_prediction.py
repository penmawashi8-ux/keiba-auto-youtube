#!/usr/bin/env python3
"""
桜花賞2026 前日予想動画用の news.json・output/script_0.txt・専用背景画像を生成する。

通常の fetch_news.py + generate_script.py の代わりに実行するスクリプト。
"""

import json
import subprocess
from pathlib import Path

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"

# ──────────────────────────────────────────────────────────────────────────────
# 桜花賞2026 レース情報
# ──────────────────────────────────────────────────────────────────────────────
NEWS_ENTRY = {
    "id": "oukasho_2026_prediction",
    "title": "【桜花賞2026前日予想】本命スターアニス！ドリームコアは消し？アランカール好枠で激走狙い",
    "url": "https://www.jra.go.jp/keiba/g1/ouka.html",
    "summary": (
        "2026年4月12日(日)阪神競馬場・芝1600m 桜花賞G1前日予想。"
        "本命はスターアニス（7枠15番・松山騎手）。昨年の阪神JF覇者が直行ローテで参戦し、"
        "最終追い切りは全馬最高のS評価。リバティアイランド・ソダシと同じ阪神JF直行のローテ。"
        "対抗はアランカール（4枠7番・武豊騎手）。エルフィンS勝ちで調教評価も高く、4枠7番の好枠が光る。"
        "3着候補はリリージョワ（7枠13番・浜中騎手）。3戦3勝の無敗馬で紅梅Sを快勝。"
        "ドリームコア（7枠14番・ルメール騎手）は外枠14番に加え前走馬体重502kgが不安材料。"
        "近10年桜花賞で前走500kg超の馬は1頭も馬券に絡んでいない。"
    ),
    "image_url": "",
    "published_date": "2026-04-11T20:00:00+09:00",
}

# ──────────────────────────────────────────────────────────────────────────────
# 予想ナレーション脚本
# 句点(。)ごとに動画のシーンが切り替わる。
# ──────────────────────────────────────────────────────────────────────────────
PREDICTION_SCRIPT = (
    "明日の桜花賞2026、18頭のヒロインが阪神芝1600メートルの夢舞台に挑む。"
    "本命はスターアニス。昨年の阪神ジュベナイルフィリーズを制したGⅠ馬が直行ローテで参戦し、最終追い切りは全馬最高のS評価を獲得した。"
    "7枠15番の外枠は気になるが、リバティアイランドやソダシと同じ阪神JF直行ローテで、能力差は本物だ。"
    "対抗に推すのは4枠7番のアランカール。武豊騎手が内目の好枠を活かしてスムーズに立ち回れる。エルフィンSを勝ちあがり調教評価もトップクラス、枠の恩恵を最大限に活かせる。"
    "3着には7枠13番リリージョワ。3戦3勝の無敗馬で紅梅Sを快勝、浜中騎手もすごくポテンシャルを感じると自信のコメントを残している。"
    "2番人気のドリームコアは思い切って消す。外枠14番のロスに加え、前走の馬体重が502キロで、近10年の桜花賞では500キロ超の馬が1頭も馬券に絡んでいない。"
    "本命スターアニス、対抗アランカール、3着にリリージョワで春のGⅠを制する。"
    "みんなの本命はどの馬？コメントで教えてくれ！"
)


def generate_prediction_backgrounds() -> None:
    """予想動画専用の背景画像3枚を ffmpeg geq フィルターで生成する（Pillow不使用）。
    桜花賞イメージ（ピンク系グラデーション）で3パターン生成。
    assets/ai_N.jpg に保存し、generate_video.py が優先的に使用する。
    """
    Path(ASSETS_DIR).mkdir(exist_ok=True)

    backgrounds = [
        # 0: 深黒→桜ピンク 縦グラデーション
        (
            "ai_0.jpg",
            "geq="
            "r='clip(10+160*pow(Y/H,1.5),10,170)':"
            "g='clip(4+40*pow(Y/H,2),4,44)':"
            "b='clip(8+60*pow(Y/H,2),8,68)'",
        ),
        # 1: 漆黒→深紅ピンク（サクラレッド）縦グラデーション
        (
            "ai_1.jpg",
            "geq="
            "r='clip(8+120*pow(1-Y/H,1.4),8,128)':"
            "g='clip(4+20*pow(1-Y/H,2),4,24)':"
            "b='clip(6+40*pow(1-Y/H,1.8),6,46)'",
        ),
        # 2: 深紺→薄紫ピンク（夜桜）縦グラデーション
        (
            "ai_2.jpg",
            "geq="
            "r='clip(6+80*pow(Y/H,1.6),6,86)':"
            "g='clip(4+18*pow(Y/H,2),4,22)':"
            "b='clip(14+90*pow(Y/H,1.4),14,104)'",
        ),
    ]

    for filename, vf_expr in backgrounds:
        out_path = f"{ASSETS_DIR}/{filename}"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=black:s=1080x1920:r=1",
                "-vf", vf_expr,
                "-frames:v", "1",
                "-q:v", "3",
                out_path,
            ],
            check=True,
            capture_output=True,
        )
        print(f"背景画像を生成: {out_path}")


def main() -> None:
    # 予想動画専用背景を生成
    generate_prediction_backgrounds()

    # news.json を生成
    Path(NEWS_JSON).write_text(
        json.dumps([NEWS_ENTRY], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"{NEWS_JSON} を生成しました。")

    # output/script_0.txt を生成
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)
    script_path = output_dir / "script_0.txt"
    script_path.write_text(PREDICTION_SCRIPT, encoding="utf-8")
    print(f"{script_path} を生成しました。")
    print(f"   文字数: {len(PREDICTION_SCRIPT)} 字")
    print(f"   プレビュー: {PREDICTION_SCRIPT[:80]}...")


if __name__ == "__main__":
    main()
