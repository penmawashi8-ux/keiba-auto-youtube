#!/usr/bin/env python3
"""
NHKマイルカップ2026 枠順確定前予想動画用の news.json・output/script_0.txt・専用背景画像を生成する。

通常の fetch_news.py + generate_script.py の代わりに実行するスクリプト。
ショート動画（縦向き 1080×1920）用。generate_video.py で処理する。
"""

import json
import subprocess
from pathlib import Path

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"

# ──────────────────────────────────────────────────────────────────────────────
# NHKマイルカップ2026 レース情報
# ──────────────────────────────────────────────────────────────────────────────
NEWS_ENTRY = {
    "id": "nhk_mile_2026_prediction",
    "title": "【NHKマイルカップ2026枠順確定前予想】PR116カヴァレリッツォより怖い馬がいる！東京マイルデータで本命ダイヤモンドノット激走狙い",
    "url": "https://www.jra.go.jp/keiba/g1/nhk_mile/syutsuba.html",
    "summary": (
        "2026年5月11日(日)東京競馬場・芝1600m NHKマイルカップG1の枠順確定前予想。"
        "出走メンバー最高PR116のカヴァレリッツォ（西村淳也騎手）が1番人気濃厚だが、"
        "東京マイルのデータが冷徹な真実を告げている。"
        "本命はダイヤモンドノット（川田将雅騎手・福永祐一調教師）。PR114で師弟コンビの"
        "初G1制覇を狙う東京マイル最適の舞台。"
        "対抗はサンダーストラック（C.ルメール騎手）。PR110で東京芝の帝王ルメール騎手との"
        "コンビは外せない。"
        "3着はアドマイヤクワッズ（坂井瑠星騎手・友道康夫調教師）。PR112で友道厩舎の"
        "仕上げが光るマイル巧者。"
    ),
    "image_url": "",
    "published_date": "2026-05-07T20:00:00+09:00",
}

# ──────────────────────────────────────────────────────────────────────────────
# 予想ナレーション脚本
# 句点(。)ごとに動画のシーンが切り替わる。
# ──────────────────────────────────────────────────────────────────────────────
PREDICTION_SCRIPT = (
    "NHKマイルカップ2026、18頭のメンバーが確定した。枠順発表前だが、データが本命を指している。"
    "最注目はPR116、カヴァレリッツォだ。西村淳也騎手鞍上で1番人気は確実だが、東京マイルのある事実が気になる。"
    "過去10年のNHKマイル、前走でマイルG2以上を使った馬の成績は圧倒的だ。カヴァレリッツォの前走ローテが問われる。"
    "本命はPR114、ダイヤモンドノット。鞍上は川田将雅騎手、厩舎は福永祐一調教師。"
    "師弟コンビの初G1制覇を狙う一戦で、東京の長い直線で末脚を爆発させる。"
    "東京芝マイルは差し馬が台頭しやすい舞台。枠番を問わずパフォーマンスを発揮できる能力の高さが光る。"
    "対抗はサンダーストラック。PR110にC.ルメール騎手の組み合わせは外せない。"
    "ルメール騎手の東京芝での勝率は全国トップクラス。東京マイルのコース適性と勝負強さで差のない競馬をする。"
    "3着候補はアドマイヤクワッズ。PR112で友道康夫調教師の仕上げが光るマイル巧者だ。坂井瑠星騎手との手が合う一頭で、好位から粘り込む。"
    "本命ダイヤモンドノット、対抗サンダーストラック、3着アドマイヤクワッズ。東京マイルの春G1を制する。"
    "みんなの本命はどの馬？コメントで教えてくれ！"
)


def generate_prediction_backgrounds() -> None:
    """予想動画専用の背景画像3枚を ffmpeg geq フィルターで生成する（Pillow不使用）。
    NHKマイルイメージ（東京競馬場・青空・若草）で3パターン生成。
    assets/ai_N.jpg に保存し、generate_video.py が優先的に使用する。
    """
    Path(ASSETS_DIR).mkdir(exist_ok=True)

    backgrounds = [
        # 0: 漆黒→ロイヤルブルー（東京競馬場の夜空）
        (
            "ai_0.jpg",
            "geq="
            "r='clip(4+20*pow(Y/H,2),4,24)':"
            "g='clip(6+50*pow(Y/H,1.8),6,56)':"
            "b='clip(12+160*pow(Y/H,1.4),12,172)'",
        ),
        # 1: 漆黒→深青緑（東京の芝のグリーン）
        (
            "ai_1.jpg",
            "geq="
            "r='clip(4+10*pow(Y/H,2),4,14)':"
            "g='clip(8+120*pow(Y/H,1.5),8,128)':"
            "b='clip(10+80*pow(Y/H,1.6),10,90)'",
        ),
        # 2: 漆黒→ネイビーゴールド（G1の栄光）
        (
            "ai_2.jpg",
            "geq="
            "r='clip(6+100*pow(1-Y/H,1.4),6,106)':"
            "g='clip(6+80*pow(1-Y/H,1.6),6,86)':"
            "b='clip(10+20*pow(Y/H,2),10,30)'",
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
