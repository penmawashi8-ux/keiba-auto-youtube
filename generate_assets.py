#!/usr/bin/env python3
"""
generate_assets.py - 競馬背景画像5枚をPillow+numpyで生成してassets/に保存する。
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS_DIR = Path("assets")
W, H = 1080, 1920
RNG = np.random.default_rng(42)


def _noise(shape, scale=8):
    """ランダムノイズ配列（RGB加算用）を返す。"""
    return (RNG.random(shape) * scale * 2 - scale).astype(np.int16)


def _gradient(top_color, bottom_color, w=W, h=H):
    """上から下へ線形グラデーションのRGB配列を返す。"""
    t = np.linspace(0, 1, h)[:, None, None]
    top = np.array(top_color, dtype=np.float32)
    bot = np.array(bottom_color, dtype=np.float32)
    arr = top + (bot - top) * t
    return np.broadcast_to(arr, (h, w, 3)).copy().astype(np.float32)


def _clamp(arr):
    return np.clip(arr, 0, 255).astype(np.uint8)


def _find_font(size=30):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    import glob as _glob
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    hits = _glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    if hits:
        return ImageFont.truetype(hits[0], size)
    return ImageFont.load_default()


def _watermark(img: Image.Image) -> Image.Image:
    draw = ImageDraw.Draw(img)
    font = _find_font(30)
    text = "競馬速報"
    try:
        bb = draw.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
    except Exception:
        tw, th = 60, 30
    draw.text((W - tw - 20, H - th - 20), text, font=font, fill=(255, 255, 255))
    return img


# ---------------------------------------------------------------------------
# 画像1: 夕暮れの競馬場
# ---------------------------------------------------------------------------
def bg_1():
    # 空（上半分）: オレンジ→赤紫
    sky = _gradient((230, 110, 30), (120, 30, 80), w=W, h=H // 2)
    # 地面（下半分）: 深緑→黒
    ground = _gradient((20, 70, 20), (5, 15, 5), w=W, h=H // 2)
    arr = np.vstack([sky, ground]).astype(np.float32)

    # 地平線: 明るいオレンジのぼんやりライン
    horizon_y = H // 2
    for dy in range(-15, 16):
        alpha = 1.0 - abs(dy) / 16
        arr[horizon_y + dy] += np.array([255 * alpha, 160 * alpha, 40 * alpha])

    # ノイズ
    arr += _noise((H, W, 3), scale=10)

    img = Image.fromarray(_clamp(arr))

    # 太陽
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.ellipse([390, 180, 690, 480], fill=(255, 200, 60, 160))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    _watermark(img).save(ASSETS_DIR / "bg_1.jpg", "JPEG", quality=92)
    print("✅ bg_1.jpg 生成完了（夕暮れの競馬場）")


# ---------------------------------------------------------------------------
# 画像2: ナイター競馬
# ---------------------------------------------------------------------------
def bg_2():
    # 空: 深紺→黒
    arr = _gradient((5, 8, 30), (2, 2, 8), w=W, h=H).astype(np.float32)

    # スタンドのシルエット（上部1/4）
    stand_top = int(H * 0.18)
    stand_bot = int(H * 0.42)
    for x_start, width in [(0, 160), (155, 200), (340, 130), (455, 170), (600, 200), (780, 150), (910, 170)]:
        h_var = RNG.integers(60, 130)
        arr[stand_bot - h_var:stand_bot, x_start:x_start + width] = [25, 25, 28]

    # 芝: 照明に照らされた緑
    arr[stand_bot:, :] = np.array([15, 80, 20], dtype=np.float32)
    # 芝に光のグラデーション
    for i, lx in enumerate([180, 540, 900]):
        for y in range(stand_bot, H):
            dx = np.abs(np.arange(W) - lx).astype(np.float32)
            strength = np.clip(1.0 - (dx / 350 + (y - stand_bot) / (H - stand_bot) * 0.6), 0, 1) * 60
            arr[y, :, 1] += strength
            arr[y, :, 0] += strength * 0.3
            arr[y, :, 2] += strength * 0.1

    # 照明: 3点の放射光
    light_overlay = np.zeros((H, W, 3), dtype=np.float32)
    for lx in [180, 540, 900]:
        ly = stand_top - 20
        for y in range(0, stand_bot + 200):
            dx = np.abs(np.arange(W) - lx).astype(np.float32)
            dist = np.sqrt(dx ** 2 + (y - ly) ** 2)
            strength = np.clip(1.0 - dist / 400, 0, 1) ** 1.5 * 180
            light_overlay[y, :, 0] += strength * 1.0
            light_overlay[y, :, 1] += strength * 0.95
            light_overlay[y, :, 2] += strength * 0.6

    arr += light_overlay
    arr += _noise((H, W, 3), scale=6)
    img = Image.fromarray(_clamp(arr))
    _watermark(img).save(ASSETS_DIR / "bg_2.jpg", "JPEG", quality=92)
    print("✅ bg_2.jpg 生成完了（ナイター競馬）")


# ---------------------------------------------------------------------------
# 画像3: 晴天の競馬場
# ---------------------------------------------------------------------------
def bg_3():
    # 空: 青→水色
    sky = _gradient((40, 120, 210), (140, 200, 255), w=W, h=int(H * 0.55))
    # 芝: 鮮やかな緑、ストライプ
    grass_h = H - int(H * 0.55)
    grass = _gradient((30, 140, 40), (20, 100, 25), w=W, h=grass_h)
    # ストライプ（コース感）
    stripe_w = 90
    for sx in range(0, W, stripe_w * 2):
        grass[:, sx:sx + stripe_w, 1] = np.clip(grass[:, sx:sx + stripe_w, 1] + 20, 0, 255)

    arr = np.vstack([sky, grass]).astype(np.float32)
    arr += _noise((H, W, 3), scale=8)
    img = Image.fromarray(_clamp(arr))

    # 雲（ガウシアンぼかし）
    cloud_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(cloud_layer)
    cloud_configs = [
        (120, 160, 220, 80), (350, 100, 180, 70), (600, 180, 200, 90),
        (820, 130, 160, 65), (200, 260, 140, 55), (700, 310, 150, 60),
    ]
    for cx, cy, rw, rh in cloud_configs:
        for ox, oy, ow, oh in [
            (0, 0, rw, rh), (rw // 3, -rh // 3, int(rw * 0.8), int(rh * 0.8)),
            (-rw // 4, rh // 5, int(rw * 0.7), int(rh * 0.7)),
            (rw // 2, rh // 4, int(rw * 0.6), int(rh * 0.65)),
        ]:
            cdraw.ellipse([cx + ox - ow // 2, cy + oy - oh // 2,
                           cx + ox + ow // 2, cy + oy + oh // 2],
                          fill=(255, 255, 255, 200))
    cloud_layer = cloud_layer.filter(ImageFilter.GaussianBlur(radius=12))
    img = Image.alpha_composite(img.convert("RGBA"), cloud_layer).convert("RGB")

    # フェンス（白線）
    draw = ImageDraw.Draw(img)
    fence_y = int(H * 0.56)
    for fy in [fence_y, fence_y + 55, fence_y + 110]:
        draw.line([(40, fy), (W - 40, fy)], fill=(255, 255, 255), width=4)
    # フェンス縦線
    for fx in range(40, W - 40, 80):
        draw.line([(fx, fence_y), (fx, fence_y + 110)], fill=(255, 255, 255), width=3)

    _watermark(img).save(ASSETS_DIR / "bg_3.jpg", "JPEG", quality=92)
    print("✅ bg_3.jpg 生成完了（晴天の競馬場）")


# ---------------------------------------------------------------------------
# 画像4: ゴール前
# ---------------------------------------------------------------------------
def bg_4():
    arr = np.full((H, W, 3), [12, 55, 12], dtype=np.float32)

    # 四隅から中央への光グラデーション
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = W / 2, H / 2
    dist_center = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    max_dist = np.sqrt(cx ** 2 + cy ** 2)
    light_map = np.clip(1.0 - dist_center / max_dist, 0, 1) ** 1.2 * 50
    arr[:, :, 0] += light_map * 0.5
    arr[:, :, 1] += light_map
    arr[:, :, 2] += light_map * 0.2

    # 観客席（上部1/3: カラフルなドット）
    audience_h = H // 3
    dot_colors = [
        (220, 50, 50), (50, 50, 220), (220, 220, 50), (220, 120, 50),
        (160, 50, 180), (50, 180, 160), (240, 240, 240), (200, 80, 120),
    ]
    for _ in range(4000):
        px = int(RNG.integers(0, W))
        py = int(RNG.integers(0, audience_h))
        col = dot_colors[int(RNG.integers(0, len(dot_colors)))]
        r = int(RNG.integers(3, 7))
        arr[max(0, py - r):py + r, max(0, px - r):px + r] = col

    arr += _noise((H, W, 3), scale=8)
    img = Image.fromarray(_clamp(arr))

    # ゴールポスト
    draw = ImageDraw.Draw(img)
    post_top, post_bot = int(H * 0.38), int(H * 0.72)
    lp, rp = int(W * 0.30), int(W * 0.70)
    draw.line([(lp, post_top), (lp, post_bot)], fill=(255, 255, 255), width=14)
    draw.line([(rp, post_top), (rp, post_bot)], fill=(255, 255, 255), width=14)
    draw.line([(lp, post_top), (rp, post_top)], fill=(255, 255, 255), width=12)
    # ゴールポストの影（立体感）
    draw.line([(lp + 4, post_top + 4), (lp + 4, post_bot)], fill=(0, 0, 0, 100), width=6)
    draw.line([(rp + 4, post_top + 4), (rp + 4, post_bot)], fill=(0, 0, 0, 100), width=6)

    _watermark(img).save(ASSETS_DIR / "bg_4.jpg", "JPEG", quality=92)
    print("✅ bg_4.jpg 生成完了（ゴール前）")


# ---------------------------------------------------------------------------
# 画像5: 早朝の調教
# ---------------------------------------------------------------------------
def bg_5():
    # 空: 薄オレンジ→白（夜明け）
    sky = _gradient((255, 190, 100), (255, 245, 220), w=W, h=int(H * 0.52))
    # 芝: くすんだ緑
    grass = _gradient((50, 100, 40), (35, 75, 28), w=W, h=H - int(H * 0.52))
    arr = np.vstack([sky, grass]).astype(np.float32)
    arr += _noise((H, W, 3), scale=12)

    img = Image.fromarray(_clamp(arr))

    # 霧: ガウシアンぼかしの白い半透明レイヤー
    fog_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fog_arr = np.zeros((H, W, 4), dtype=np.uint8)
    fog_strength = np.linspace(120, 0, H)  # 上が濃く、下は薄い
    fog_arr[:, :, 0] = 255
    fog_arr[:, :, 1] = 255
    fog_arr[:, :, 2] = 255
    fog_arr[:, :, 3] = fog_strength[:, None].astype(np.uint8)
    fog_img = Image.fromarray(fog_arr).filter(ImageFilter.GaussianBlur(radius=25))
    img = Image.alpha_composite(img.convert("RGBA"), fog_img).convert("RGB")

    # 木のシルエット
    draw = ImageDraw.Draw(img)
    tree_configs = [(100, 900), (260, 960), (820, 910), (950, 870)]
    for tx, ty in tree_configs:
        trunk_w, trunk_h = 22, int(H * 0.25)
        draw.rectangle([tx - trunk_w // 2, ty, tx + trunk_w // 2, ty + trunk_h],
                       fill=(20, 15, 10))
        for lx, ly, lr in [
            (tx, ty - 60, 95), (tx - 40, ty + 20, 75), (tx + 40, ty + 10, 70),
        ]:
            draw.ellipse([lx - lr, ly - int(lr * 1.1), lx + lr, ly + int(lr * 0.9)],
                         fill=(15, 20, 10))

    _watermark(img).save(ASSETS_DIR / "bg_5.jpg", "JPEG", quality=92)
    print("✅ bg_5.jpg 生成完了（早朝の調教）")


if __name__ == "__main__":
    ASSETS_DIR.mkdir(exist_ok=True)
    print("=== 競馬背景画像生成開始 ===")
    bg_1()
    bg_2()
    bg_3()
    bg_4()
    bg_5()
    print(f"\n完了: {list(ASSETS_DIR.glob('bg_*.jpg'))}")
