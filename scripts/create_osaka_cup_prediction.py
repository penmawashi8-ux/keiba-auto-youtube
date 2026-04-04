#!/usr/bin/env python3
"""
大阪杯2026 予想動画用の news.json・output/script_0.txt・専用背景画像を生成する。

通常の fetch_news.py + generate_script.py の代わりに実行するスクリプト。
"""

import json
import subprocess
from pathlib import Path

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"

# ──────────────────────────────────────────────────────────────────────────────
# 大阪杯2026 レース情報
# ──────────────────────────────────────────────────────────────────────────────
NEWS_ENTRY = {
    "id": "osaka_cup_2026_prediction_wakuban",
    "title": "【大阪杯2026枠順確定予想】クロワデュノール大外15番！ダービー馬2頭の枠順を徹底分析",
    "url": "https://www.jra.go.jp/keiba/g1/osaka/syutsuba.html",
    "summary": (
        "2026年4月5日(日)阪神競馬場・芝2000m 大阪杯G1の枠順が確定。"
        "注目は本命クロワデュノールが大外8枠15番に入ったこと。阪神2000mは内枠有利のコースで痛恨の外枠。"
        "一方、ダノンデサイルは3枠4番の好枠を引き当てた。坂井瑠星騎手との新コンビで内目からスムーズな競馬が期待できる。"
        "逃げ宣言のメイショウタバルは4枠6番と理想的な中枠。武豊騎手が自分のペースで逃げやすい。"
        "ショウヘイは3枠5番で川田将雅騎手が好位をとりやすい枠。レーベンスティールは7枠12番でルメール騎手が外から差してくる展開。"
        "枠順を踏まえた最終予想：本命ダノンデサイル、対抗クロワデュノール、3着ショウヘイ。"
    ),
    "image_url": "",
    "published_date": "2026-04-05T15:40:00+09:00",
}

# ──────────────────────────────────────────────────────────────────────────────
# AI予想ナレーション脚本
# 句点(。)ごとに動画のシーンが切り替わる。最後はCTAで締める。
# ──────────────────────────────────────────────────────────────────────────────
PREDICTION_SCRIPT = (
    "大阪杯2026、枠順が確定した。"
    "最大の注目はクロワデュノールが大外8枠15番に入ったこと。"
    "阪神2000メートルは4角で内を通れるかが勝負の分かれ目、大外枠は相当なロスになる。"
    "対照的に3枠4番を引いたダノンデサイルは絶好枠。"
    "坂井瑠星騎手との新コンビで内目からスムーズに立ち回れる。"
    "逃げ宣言のメイショウタバルは4枠6番、武豊騎手が自分のペースで逃げやすい理想的な枠だ。"
    "3枠5番ショウヘイは川田将雅騎手が好位をとりやすく、7枠12番レーベンスティールはルメール騎手が外から差す展開。"
    "枠順を踏まえた最終予想、本命はダノンデサイル。"
    "大外のクロワデュノールは対抗、3着にはショウヘイを狙う。"
    "みんなの予想はどう？コメントで教えてくれ！"
)


ASSETS_DIR = "assets"


def generate_prediction_backgrounds() -> None:
    """予想動画専用の背景画像3枚を ffmpeg geq フィルターで生成する（Pillow不使用）。
    assets/ai_N.jpg に保存し、generate_video.py が優先的に使用する。
    """
    Path(ASSETS_DIR).mkdir(exist_ok=True)

    # geq フィルター式: X/Y/W/H が利用可能。値は 0〜255。
    backgrounds = [
        # 0: 漆黒→深紅 縦グラデーション（下に行くほど赤く）
        (
            "ai_0.jpg",
            "geq="
            "r='clip(12+148*pow(Y/H,1.6),12,160)':"
            "g='clip(4*pow(Y/H,2),0,4)':"
            "b='clip(4*pow(Y/H,2),0,4)'",
        ),
        # 1: 漆黒→深金 縦グラデーション（上に行くほどゴールド）
        (
            "ai_1.jpg",
            "geq="
            "r='clip(8+100*pow(1-Y/H,1.4),8,108)':"
            "g='clip(6+68*pow(1-Y/H,1.6),6,74)':"
            "b='clip(2,0,2)'",
        ),
        # 2: 漆黒→深青 縦グラデーション（上に行くほど深いブルー）
        (
            "ai_2.jpg",
            "geq="
            "r='clip(4*pow(1-Y/H,2),0,4)':"
            "g='clip(6*pow(1-Y/H,2),0,6)':"
            "b='clip(10+105*pow(1-Y/H,1.5),10,115)'",
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
        print(f"✅ 背景画像を生成: {out_path}")


def main() -> None:
    # 予想動画専用背景を生成（assets/ai_*.jpg → generate_video.py が優先使用）
    generate_prediction_backgrounds()

    # news.json を生成
    Path(NEWS_JSON).write_text(
        json.dumps([NEWS_ENTRY], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ {NEWS_JSON} を生成しました。")

    # output/script_0.txt を生成
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)
    script_path = output_dir / "script_0.txt"
    script_path.write_text(PREDICTION_SCRIPT, encoding="utf-8")
    print(f"✅ {script_path} を生成しました。")
    print(f"   文字数: {len(PREDICTION_SCRIPT)} 字")
    print(f"   プレビュー: {PREDICTION_SCRIPT[:80]}...")


if __name__ == "__main__":
    main()
