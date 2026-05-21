#!/usr/bin/env python3
"""
オークス2026 枠順確定予想動画生成スクリプト。
脚本はAIが枠順・騎手データから直接作成（Gemini不使用）。
"""

import json
import subprocess
from pathlib import Path

NEWS_JSON  = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"

PREDICTION_SCRIPT = (
    "本命は5枠10番スターアニス。"
    "桜花賞を制し、松山弘平騎手との黄金コンビが予想1番人気に応える。"
    "対抗は7枠13番エンネ。"
    "フローラS2着で上がり32秒8はラフターラインズと同タイムの最速。"
    "キャリア3戦目の急成長馬が一番手に迫る。"
    "3着は6枠12番ドリームコア。"
    "クイーンカップ勝ちのC.ルメール騎手はオークス4勝の実績を持ち、中枠から末脚を発揮する。"
    "穴は2枠3番アランカール。"
    "桜花賞5着から2400mへの距離延長で、武豊が絶好の内枠から巻き返しを図る。"
    "注目は予想2番人気ラフターラインズ、フローラSを最速上がりで快勝したが8枠18番の最外枠が痛い。"
    "みんなの本命は？コメントで教えてくれ！"
)

NEWS_ENTRY = {
    "id": "oaks_2026_post_prediction",
    "title": "【オークス2026枠順確定予想】桜花賞馬スターアニスvs最外枠ラフターラインズ！枠で変わる勝負の行方",
    "url": "https://www.jra.go.jp/",
    "summary": PREDICTION_SCRIPT[:200],
    "image_url": "",
    "published_date": "2026-05-24T12:00:00+09:00",
}


def generate_backgrounds() -> None:
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    backgrounds = [
        ("ai_0.jpg",
         "geq=r='clip(4+30*pow(Y/H,2),4,34)':g='clip(8+170*pow(Y/H,1.4),8,178)':b='clip(6+80*pow(Y/H,1.6),6,86)'"),
        ("ai_1.jpg",
         "geq=r='clip(4+30*pow(Y/H,1.8),4,34)':g='clip(6+60*pow(Y/H,1.8),6,66)':b='clip(10+180*pow(Y/H,1.4),10,190)'"),
        ("ai_2.jpg",
         "geq=r='clip(8+160*pow(1-Y/H,1.4),8,168)':g='clip(6+120*pow(1-Y/H,1.6),6,126)':b='clip(4+20*pow(Y/H,2),4,24)'"),
    ]
    for filename, vf in backgrounds:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=black:s=1080x1920:r=1",
             "-vf", vf, "-frames:v", "1", "-q:v", "3", f"{ASSETS_DIR}/{filename}"],
            check=True, capture_output=True,
        )
        print(f"背景画像生成: {ASSETS_DIR}/{filename}")


def main() -> None:
    generate_backgrounds()

    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    (Path(OUTPUT_DIR) / "script_0.txt").write_text(PREDICTION_SCRIPT, encoding="utf-8")
    print(f"output/script_0.txt を生成しました（{len(PREDICTION_SCRIPT)} 文字）")
    print(f"\n[脚本]\n{PREDICTION_SCRIPT}")

    Path(NEWS_JSON).write_text(
        json.dumps([NEWS_ENTRY], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"{NEWS_JSON} を生成しました。")


if __name__ == "__main__":
    main()
