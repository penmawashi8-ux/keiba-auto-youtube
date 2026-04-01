#!/usr/bin/env python3
"""
大阪杯2026 予想動画用の news.json と output/script_0.txt を生成する。

通常の fetch_news.py + generate_script.py の代わりに実行するスクリプト。
"""

import json
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
    "大阪杯2026、史上最高レベルのダービー馬対決が阪神2000メートルで実現する。"
    "ドバイシーマクラシックを制したダノンデサイルと、"
    "1週前追いで好時計をマークし完全復活の兆しを見せるクロワデュノールが激突。"
    "展開はメイショウタバルが先手を主張してスローペースとなり、"
    "直線は内回りの末脚比べが濃厚。"
    "クロワデュノールが好位から豪脚を炸裂させ、"
    "ダノンデサイルをクビ差抑えて一着と予想する。"
    "本命クロワデュノール、対抗ダノンデサイル、"
    "三着争いにメイショウタバルとショウヘイに注目。"
    "みんなはどう思う？コメントしてね！"
)


def main() -> None:
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
