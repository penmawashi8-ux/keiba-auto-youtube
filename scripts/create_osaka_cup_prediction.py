#!/usr/bin/env python3
"""
大阪杯2026 予想動画用の news.json・output/script_0.txt・専用背景画像を生成する。

通常の fetch_news.py + generate_script.py の代わりに実行するスクリプト。
"""

import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

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
W, H = 1080, 1920


def _save(img: Image.Image, path: str) -> None:
    img.convert("RGB").save(path, "JPEG", quality=92)
    print(f"✅ 背景画像を生成: {path}")


def generate_prediction_backgrounds() -> None:
    """予想動画専用の背景画像3枚を assets/ai_N.jpg として生成する。"""
    Path(ASSETS_DIR).mkdir(exist_ok=True)

    # ── 背景1: 深夜の闘技場（深紅 × 黒、スポットライト） ──────────────────
    img1 = Image.new("RGB", (W, H), (0, 0, 0))
    d1 = ImageDraw.Draw(img1)
    # 上から下へ黒→深紅グラデーション
    for y in range(H):
        t = y / H
        r = int(10 + 120 * t ** 2)
        g = int(0 + 5 * t)
        b = int(0 + 8 * t)
        d1.line([(0, y), (W, y)], fill=(r, g, b))
    # 中央下部にスポットライト（楕円グロー）
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = W // 2, int(H * 0.72)
    for r in range(500, 0, -20):
        alpha = int(60 * (1 - r / 500))
        gd.ellipse([cx - r, cy - r // 2, cx + r, cy + r // 2], fill=(255, 80, 30, alpha))
    img1 = Image.alpha_composite(img1.convert("RGBA"), glow).convert("RGB")
    # 水平スキャンライン（薄いグリッド感）
    d1 = ImageDraw.Draw(img1)
    for y in range(0, H, 40):
        d1.line([(0, y), (W, y)], fill=(80, 0, 0), width=1)
    _save(img1, f"{ASSETS_DIR}/ai_0.jpg")

    # ── 背景2: サイバーデータグリッド（黒 × 金、六角形網） ────────────────
    img2 = Image.new("RGB", (W, H), (4, 4, 12))
    d2 = ImageDraw.Draw(img2)
    # 縦グラデーション（上部を少し明るく）
    for y in range(H):
        t = 1 - y / H
        lum = int(8 + 18 * t)
        d2.line([(0, y), (W, y)], fill=(lum, lum, int(lum * 1.5)))
    # 六角形グリッド
    hex_size = 70
    for row in range(-1, H // (hex_size) + 2):
        for col in range(-1, W // (hex_size) + 2):
            cx = col * hex_size * 1.73
            cy = row * hex_size * 2 + (hex_size if col % 2 else 0)
            pts = [
                (cx + hex_size * math.cos(math.radians(60 * i + 30)),
                 cy + hex_size * math.sin(math.radians(60 * i + 30)))
                for i in range(6)
            ]
            d2.polygon(pts, outline=(50, 38, 0), width=1)
    # ゴールドの縦ラインアクセント
    for x in [int(W * 0.15), int(W * 0.85)]:
        for y in range(0, H, 2):
            alpha_val = int(120 * abs(math.sin(y / 80)))
            d2.line([(x, y), (x, y)], fill=(alpha_val, int(alpha_val * 0.75), 0))
    # 中央に薄いゴールドグロー
    glow2 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd2 = ImageDraw.Draw(glow2)
    for r in range(400, 0, -30):
        a = int(40 * (1 - r / 400))
        gd2.ellipse([W // 2 - r, H // 2 - r, W // 2 + r, H // 2 + r], fill=(200, 150, 0, a))
    img2 = Image.alpha_composite(img2.convert("RGBA"), glow2).convert("RGB")
    _save(img2, f"{ASSETS_DIR}/ai_1.jpg")

    # ── 背景3: 漆黒 × 電光ブルー（雷 × スピード感） ──────────────────────
    img3 = Image.new("RGB", (W, H), (0, 0, 0))
    d3 = ImageDraw.Draw(img3)
    # 上部を深い紺に
    for y in range(H):
        t = y / H
        b_val = int(30 * (1 - t) ** 1.5)
        d3.line([(0, y), (W, y)], fill=(0, 0, b_val))
    # 斜めスピードライン（右下方向）
    random.seed(42)
    for _ in range(60):
        sx = random.randint(-200, W)
        sy = random.randint(0, H)
        length = random.randint(80, 350)
        brightness = random.randint(20, 90)
        ex = sx + int(length * 0.6)
        ey = sy + length
        d3.line([(sx, sy), (ex, ey)], fill=(brightness, brightness * 2, 255), width=1)
    # 中央縦に電光ブルーグロー
    glow3 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd3 = ImageDraw.Draw(glow3)
    for r in range(300, 0, -20):
        a = int(50 * (1 - r / 300))
        gd3.ellipse([W // 2 - r * 2, H // 2 - r, W // 2 + r * 2, H // 2 + r], fill=(0, 100, 255, a))
    img3 = Image.alpha_composite(img3.convert("RGBA"), glow3).convert("RGB")
    # ブラーで柔らかく
    img3 = img3.filter(ImageFilter.GaussianBlur(radius=1))
    _save(img3, f"{ASSETS_DIR}/ai_2.jpg")


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
