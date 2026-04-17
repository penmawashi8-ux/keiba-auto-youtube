#!/usr/bin/env python3
"""
皐月賞2026 前日予想動画用の news.json・output/script_0.txt・専用背景画像を生成する。

通常の fetch_news.py + generate_script.py の代わりに実行するスクリプト。
"""

import json
import subprocess
from pathlib import Path

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"

# ──────────────────────────────────────────────────────────────────────────────
# 皐月賞2026 レース情報
# ──────────────────────────────────────────────────────────────────────────────
NEWS_ENTRY = {
    "id": "satsukisho_2026_prediction",
    "title": "【皐月賞2026枠順確定予想】1番人気カヴァレリッツォは消し！40年データで本命グリーンエナジー・パステール激走狙い",
    "url": "https://www.jra.go.jp/keiba/g1/satsuki/syutsuba.html",
    "summary": (
        "2026年4月19日(日)中山競馬場・芝2000m 皐月賞G1の枠順が確定。"
        "1番人気カヴァレリッツォ（1枠1番・レーン騎手）はマイル路線から2000m直行ローテで過去40年0勝の致命的データあり。"
        "本命はグリーンエナジー（6枠12番・戸崎圭太騎手）。本番と全く同じ中山2000mの京成杯を制した舞台巧者で"
        "2023年ソールオリエンスと同じ成功パターン。"
        "対抗はパステール（8枠18番・川田将雅騎手）。弥生賞で中山2000mを制した同舞台経験馬。"
        "3着はロブチェン（2枠4番・松山弘平騎手）。ホープフルS勝ちのG1馬で好枠から末脚を生かす。"
    ),
    "image_url": "",
    "published_date": "2026-04-18T20:00:00+09:00",
}

# ──────────────────────────────────────────────────────────────────────────────
# 予想ナレーション脚本
# 句点(。)ごとに動画のシーンが切り替わる。
# ──────────────────────────────────────────────────────────────────────────────
PREDICTION_SCRIPT = (
    "皐月賞2026、18頭の枠順が確定した。今年は大混戦だが、データが示す答えは明確だ。"
    "1番人気が予想されるカヴァレリッツォは思い切って消す。マイル路線から2000メートルへの直行ローテは過去40年で0勝という致命的なデータがある。"
    "本命は6枠12番グリーンエナジー。本番と全く同じ中山2000メートルの京成杯を差し切った舞台巧者。2023年ソールオリエンスと同じ成功パターンに乗る。戸崎圭太騎手の中山での勝負強さも心強い。"
    "対抗は8枠18番パステール。弥生賞で本番と同じ中山コースを制した川田将雅騎手コンビ。外枠のロスも川田騎手の技術でカバーできる。"
    "3着には2枠4番ロブチェン。ホープフルSで中山2000メートルを制したG1馬で、好枠に入った今回は末脚を生かしやすい。"
    "本命グリーンエナジー、対抗パステール、3着ロブチェンで春のクラシック第一弾を制する。"
    "みんなの本命はどの馬？コメントで教えてくれ！"
)


def generate_prediction_backgrounds() -> None:
    """予想動画専用の背景画像3枚を ffmpeg geq フィルターで生成する（Pillow不使用）。
    皐月賞イメージ（深青・新緑）で3パターン生成。
    assets/ai_N.jpg に保存し、generate_video.py が優先的に使用する。
    """
    Path(ASSETS_DIR).mkdir(exist_ok=True)

    backgrounds = [
        # 0: 漆黒→深青緑 縦グラデーション（中山の芝をイメージ）
        (
            "ai_0.jpg",
            "geq="
            "r='clip(6+20*pow(Y/H,2),6,26)':"
            "g='clip(8+100*pow(Y/H,1.5),8,108)':"
            "b='clip(10+140*pow(Y/H,1.4),10,150)'",
        ),
        # 1: 漆黒→深緑ゴールド（春クラシックの栄光）
        (
            "ai_1.jpg",
            "geq="
            "r='clip(6+90*pow(1-Y/H,1.5),6,96)':"
            "g='clip(8+100*pow(1-Y/H,1.4),8,108)':"
            "b='clip(4,0,4)'",
        ),
        # 2: 漆黒→ロイヤルブルー（夜の中山）
        (
            "ai_2.jpg",
            "geq="
            "r='clip(4+30*pow(Y/H,2),4,34)':"
            "g='clip(6+40*pow(Y/H,2),6,46)':"
            "b='clip(12+130*pow(Y/H,1.4),12,142)'",
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
