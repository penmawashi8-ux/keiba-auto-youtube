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
    "オークス2026の枠順が確定した。"
    "東京芝2400mは内枠が有利で、枠番が明暗を大きく分ける。"
    "本命は5枠10番スターアニス。"
    "松山弘平騎手が予想1番人気を中枠からスムーズに運ぶ。"
    "対抗は2枠3番アランカール。"
    "内枠を知り尽くした武豊騎手の巧みな立ち回りに期待する。"
    "3着は6枠12番ドリームコア。"
    "C.ルメール騎手が中枠から東京の長い直線で末脚を爆発させる。"
    "穴は4枠8番スマートプリエール。"
    "内寄りの枠で予想9番人気の一発がある。"
    "一方、予想2番人気のラフターラインズは8枠18番の最外枠が気がかりだ。"
    "みんなの本命は？コメントで教えてくれ！"
)

NEWS_ENTRY = {
    "id": "oaks_2026_post_prediction",
    "title": "【オークス2026枠順確定予想】内枠の武豊vsルメール！枠順で変わる本命争い",
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
