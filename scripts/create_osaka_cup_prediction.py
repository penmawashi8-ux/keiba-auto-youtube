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
    "id": "osaka_cup_2026_prediction",
    "title": "【大阪杯2026予想】ダービー馬対決！クロワデュノールvsダノンデサイル 展開・結果予想",
    "url": "https://www.jra.go.jp/keiba/g1/osaka/syutsuba.html",
    "summary": (
        "2026年4月5日(日)阪神競馬場で行われる大阪杯G1。"
        "91代ダービー馬ダノンデサイルはドバイシーマクラシック制覇の実績を引っさげ参戦。"
        "昨年のダービー馬クロワデュノールは1週前追いで好時計をマークし完全復活の兆し。"
        "宝塚記念覇者メイショウタバルも阪神巧者として有力視。"
        "展開予想はメイショウタバルの逃げでスローペース。"
        "本命クロワデュノール、対抗ダノンデサイル、三着争いにメイショウタバルとショウヘイ。"
    ),
    "image_url": "",
    "published_date": "2026-04-05T15:40:00+09:00",
}

# ──────────────────────────────────────────────────────────────────────────────
# AI予想ナレーション脚本
# 句点(。)ごとに動画のシーンが切り替わる。最後はCTAで締める。
# ──────────────────────────────────────────────────────────────────────────────
PREDICTION_SCRIPT = (
    "大阪杯2026、前代未聞のダービー馬対決に激震走る。"
    "なんとダノンデサイルは戸崎騎手の騎乗停止で坂井瑠星に乗替り、"
    "新コンビで臨む一戦となった。"
    "対する1週前追いで11秒1の好時計をマークしたクロワデュノールは完全復活の態勢が整った。"
    "展開は阪神芝2000メートルで3戦3勝の実績を持つ武豊メイショウタバルがハナを主張し、"
    "スローからのヨーイドンが濃厚。"
    "4角5番手以内が鉄則のこのレース、好位から豪脚を炸裂させるクロワデュノールが一着と予想する。"
    "本命クロワデュノール、対抗ダノンデサイル、穴はショウヘイに注目。"
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
