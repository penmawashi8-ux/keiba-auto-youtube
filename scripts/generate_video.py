#!/usr/bin/env python3
"""generate_video.py - ffmpegのみで字幕動画を生成する（Pillow不使用）

# ============================================================
# IMPORTANT: Pillow (PIL) は絶対に使用禁止。
# 画像の生成・変換はすべて ffmpeg (lavfi, drawtext, etc.) で行うこと。
# from PIL import ... / import PIL と書いたら即削除。
# 背景画像は generate_images.py が取得した ai_*.jpg を使う。
# 画像が0枚なら動画生成を失敗させること（フォールバック生成禁止）。
# ============================================================

流れ:
  1. news.json からタイトル・概要を取得
  2. script_N.txt を句点で分割してセリフリスト生成
  3. mutagen で audio_N.mp3 の総再生時間を取得
  4. 各セリフの表示時間を計算（総時間 × 文字数 / 総文字数、最低1.5秒）
  5. ffmpegで字幕付きクリップ（clip_N.mp4）を生成（drawtext使用）
  6. ffmpeg concat で silent.mp4 を生成
  7. ffmpeg で silent.mp4 + audio_N.mp3 + BGM → output/video_N.mp4
"""

import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
BGM_DIR = f"{ASSETS_DIR}/bgm"
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
FONT_SIZE = 64
ENDING_DURATION = 4.0    # エンディングカード表示秒数
THUMBNAIL_DURATION = 1.5  # 先頭サムネイルフレーム最低表示秒数
BGM_VOLUME = 0.12        # BGM音量（ナレーションに対する比率）
MIN_CUT_DURATION = 1.5
LINE_MAX_CHARS = 13       # 字幕1行最大文字数

# ---------------------------------------------------------------------------
# スタイル定義（バリエーション用パレット）
# ---------------------------------------------------------------------------

_SUBTITLE_COLORS = [
    "0xFFFFFF", "0xFFEB00", "0x00FFFF", "0xFFAA00",
    "0xAAFFFF", "0xFFFF88", "0xAAFF88", "0xFFD700",
    "0x88FFFF", "0xFFCCAA",
]
_BOX_COLORS = [
    "0x000014", "0x000000", "0x0A0A1A", "0x140000",
    "0x001400", "0x100010", "0x0A0A0A", "0x000A14",
]
_BADGE_COLORS = [
    "0xD21E1E", "0x1E3AD2", "0x1E9A1E", "0x8B1ED2",
    "0xD27D1E", "0x1E8BD2", "0xBD0000", "0x006400",
    "0x8B0057", "0x003366",
]
_BADGE_TEXTS = [
    "競馬速報", "競馬NEWS", "最新情報", "速報",
    "注目", "競馬情報", "今日の競馬", "重賞情報",
]
_TITLE_COLORS = [
    "0xFFEB00", "0xFFFFFF", "0xFF8C00", "0x00FFFF",
    "0xFFD700", "0xFFA500", "0xADFF2F", "0xFF6347",
]
_ENDING_TEXTS = [
    "チャンネル登録\nよろしく！\n\n毎日更新中！",
    "高評価・登録\nお願いします！\n\n毎日配信！",
    "チャンネル登録で\n最新情報をGET！\n\n毎日ニュースお届け！",
    "登録して\n競馬情報をGET！\n\n毎日更新中！",
    "チャンネル登録\nお忘れなく！\n\n毎日競馬速報！",
    "競馬好きは\nチャンネル登録！\n\n毎日配信中！",
    "通知ONで\n速報をGET！\n\n毎日更新！",
    "登録&高評価\nよろしく！\n\n最新ニュース毎日！",
    "チャンネル登録で\n競馬ニュース毎日！\n\n見逃さないで！",
    "競馬ファン必見！\nチャンネル登録！\n\n毎日情報更新！",
    "最新情報を\n見逃さないで！\n\n毎日配信！",
    "競馬情報は\nこのチャンネルで！\n\n365日更新！",
    "ベル通知で\n速報を受け取ろう！\n\n毎日競馬情報！",
    "チャンネル登録\nしてね！\n\n毎日最新情報！",
    "役に立ったら\n高評価お願いします！\n\n毎日更新中！",
]

# サムネイルタイトル用カラーペア（行ごとに交互適用）
_THUMB_COLOR_PAIRS = [
    ("0xFFFFFF", "0xFFEB00"),   # 白 + 黄
    ("0xFFEB00", "0x00FFFF"),   # 黄 + シアン
    ("0xFFD700", "0xFFFFFF"),   # 金 + 白
    ("0xFF8C00", "0xFFFF88"),   # オレンジ + 薄黄
    ("0x00FFFF", "0xFFD700"),   # シアン + 金
    ("0xADFF2F", "0xFFFFFF"),   # 黄緑 + 白
    ("0xFF6347", "0xFFFFFF"),   # 赤橙 + 白
    ("0xFFFF88", "0x88FFFF"),   # 薄黄 + 薄シアン
    ("0xFFEB00", "0xFFFFFF"),   # 黄 + 白
    ("0xFFFFFF", "0xFF8C00"),   # 白 + オレンジ
]

# サムネイルタイトル用ボックススタイル（OP = title_box_opacity のプレースホルダ）
_THUMB_BOX_STYLES = [
    # 暗めブラック（従来）
    "box=1:boxcolor=0x000000@OP:boxborderw=24:borderw=4:bordercolor=0x000000",
    # ダークネイビー
    "box=1:boxcolor=0x0A0A2E@OP:boxborderw=24:borderw=4:bordercolor=0x000033",
    # ダークパープル
    "box=1:boxcolor=0x1A0028@OP:boxborderw=24:borderw=4:bordercolor=0x3D0070",
    # ダークレッド
    "box=1:boxcolor=0x2A0000@OP:boxborderw=24:borderw=4:bordercolor=0x550000",
    # ダークグリーン
    "box=1:boxcolor=0x001A00@OP:boxborderw=24:borderw=4:bordercolor=0x004400",
    # ボックスなし（シャドウのみ）
    "box=0:borderw=6:bordercolor=0x000000:shadowcolor=0x000000@0.9:shadowx=4:shadowy=4",
    # ボックスなし（太ボーダー+シャドウ）
    "box=0:borderw=9:bordercolor=0x000000:shadowcolor=0x000000@0.8:shadowx=3:shadowy=3",
    # 半透明ダークブルーグレー
    "box=1:boxcolor=0x0D1B2A@OP:boxborderw=24:borderw=4:bordercolor=0x1C3A5E",
    # 半透明ダークブラウン
    "box=1:boxcolor=0x1A0F00@OP:boxborderw=24:borderw=4:bordercolor=0x3D2200",
]

# ---------------------------------------------------------------------------
# サムネイルキーワードハイライト
# ---------------------------------------------------------------------------
_KW_PATTERNS = [
    re.compile(r'[ァ-ヶーｦ-ﾟ]{3,}'),  # カタカナ3文字以上（馬名・騎手名等）
    re.compile(r'\d+(?:勝|着|億|万|番人気|連勝|頭|回)'),  # 数字＋競馬用語
    re.compile(
        r'初勝利|初制覇|優勝|制覇|連勝|引退|復活|重賞'
        r'|G[123]|G[ⅠⅡⅢ]|逃げ切り|差し切り|追い込み'
        r'|奇跡|悲劇|伝説|衝撃|圧勝|惜敗|復帰|電撃'
    ),
]


def _char_px(ch: str, fs: int) -> int:
    return fs if ord(ch) > 0x7F else int(fs * 0.55)


def _text_px(text: str, fs: int) -> int:
    return sum(_char_px(c, fs) for c in text)


def _find_keywords(text: str) -> list:
    spans = []
    for pat in _KW_PATTERNS:
        for m in pat.finditer(text):
            spans.append([m.start(), m.end()])
    spans.sort()
    merged: list = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


# ---------------------------------------------------------------------------
# 字幕アニメーション パターン (87種)
# (x_expr, y_expr, alpha_expr)
# BX = base_x, BY = base_y (レンダリング時に置換)
# t = 時間(秒), w/h = 動画幅高さ, text_w/text_h = テキストサイズ
# ---------------------------------------------------------------------------
_ANIM_PATTERNS = [
    # 0: 静止
    ("BX", "BY", "1"),
    # 1-4: 右からスライド（超速→遅）
    ("BX+w*(1-min(t,0.08)/0.08)", "BY", "1"),
    ("BX+w*(1-min(t,0.20)/0.20)", "BY", "1"),
    ("BX+w*(1-min(t,0.40)/0.40)", "BY", "1"),
    ("BX+w*(1-min(t,0.65)/0.65)", "BY", "1"),
    # 5-8: 左からスライド（超速→遅）
    ("BX-w*(1-min(t,0.08)/0.08)", "BY", "1"),
    ("BX-w*(1-min(t,0.20)/0.20)", "BY", "1"),
    ("BX-w*(1-min(t,0.40)/0.40)", "BY", "1"),
    ("BX-w*(1-min(t,0.65)/0.65)", "BY", "1"),
    # 9-12: 下からスライド（超速→遅）
    ("BX", "BY+h*(1-min(t,0.08)/0.08)", "1"),
    ("BX", "BY+h*(1-min(t,0.20)/0.20)", "1"),
    ("BX", "BY+h*(1-min(t,0.40)/0.40)", "1"),
    ("BX", "BY+h*(1-min(t,0.65)/0.65)", "1"),
    # 13-16: 上からスライド（超速→遅）
    ("BX", "BY-h*(1-min(t,0.08)/0.08)", "1"),
    ("BX", "BY-h*(1-min(t,0.20)/0.20)", "1"),
    ("BX", "BY-h*(1-min(t,0.40)/0.40)", "1"),
    ("BX", "BY-h*(1-min(t,0.65)/0.65)", "1"),
    # 17-21: フェードインのみ
    ("BX", "BY", "min(t/0.10,1)"),
    ("BX", "BY", "min(t/0.25,1)"),
    ("BX", "BY", "min(t/0.45,1)"),
    ("BX", "BY", "min(t/0.70,1)"),
    ("BX", "BY", "min(t/1.20,1)"),
    # 22-26: 右スライド＋フェード（ビューン）
    ("BX+w*(1-min(t,0.10)/0.10)", "BY", "min(t/0.10,1)"),
    ("BX+w*(1-min(t,0.25)/0.25)", "BY", "min(t/0.25,1)"),
    ("BX+w*(1-min(t,0.40)/0.40)", "BY", "min(t/0.40,1)"),
    ("BX+w*(1-min(t,0.55)/0.55)", "BY", "min(t/0.55,1)"),
    ("BX+w*(1-min(t,0.70)/0.70)", "BY", "min(t/0.70,1)"),
    # 27-31: 左スライド＋フェード
    ("BX-w*(1-min(t,0.10)/0.10)", "BY", "min(t/0.10,1)"),
    ("BX-w*(1-min(t,0.25)/0.25)", "BY", "min(t/0.25,1)"),
    ("BX-w*(1-min(t,0.40)/0.40)", "BY", "min(t/0.40,1)"),
    ("BX-w*(1-min(t,0.55)/0.55)", "BY", "min(t/0.55,1)"),
    ("BX-w*(1-min(t,0.70)/0.70)", "BY", "min(t/0.70,1)"),
    # 32-35: 下スライド＋フェード
    ("BX", "BY+h*(1-min(t,0.20)/0.20)", "min(t/0.20,1)"),
    ("BX", "BY+h*(1-min(t,0.35)/0.35)", "min(t/0.35,1)"),
    ("BX", "BY+h*(1-min(t,0.50)/0.50)", "min(t/0.50,1)"),
    ("BX", "BY+h*(1-min(t,0.70)/0.70)", "min(t/0.70,1)"),
    # 36-39: 上スライド＋フェード
    ("BX", "BY-h*(1-min(t,0.20)/0.20)", "min(t/0.20,1)"),
    ("BX", "BY-h*(1-min(t,0.35)/0.35)", "min(t/0.35,1)"),
    ("BX", "BY-h*(1-min(t,0.50)/0.50)", "min(t/0.50,1)"),
    ("BX", "BY-h*(1-min(t,0.70)/0.70)", "min(t/0.70,1)"),
    # 40-43: 横シェイク（バウンス減衰）
    ("BX+30*sin(t*40)*exp(-t*8)", "BY", "1"),
    ("BX+20*sin(t*50)*exp(-t*10)", "BY", "1"),
    ("BX+45*sin(t*35)*exp(-t*7)", "BY", "1"),
    ("BX+55*sin(t*28)*exp(-t*6)", "BY", "1"),
    # 44-46: 縦バウンス（弾む）
    ("BX", "BY+28*abs(sin(t*26))*exp(-t*5)", "1"),
    ("BX", "BY+40*abs(sin(t*20))*exp(-t*4)", "1"),
    ("BX", "BY+18*abs(sin(t*32))*exp(-t*7)", "1"),
    # 47-50: 右からバネ（弾性スライド）
    ("BX+w*exp(-t*7)*sin(t*22+1.5708)", "BY", "1"),
    ("BX+w*exp(-t*5)*sin(t*18+1.5708)", "BY", "1"),
    ("BX+w*0.7*exp(-t*9)*sin(t*26+1.5708)", "BY", "min(t/0.12,1)"),
    ("BX+w*0.5*exp(-t*11)*sin(t*30+1.5708)", "BY", "min(t/0.08,1)"),
    # 51-53: 左からバネ
    ("BX-w*exp(-t*7)*sin(t*22+1.5708)", "BY", "1"),
    ("BX-w*exp(-t*5)*sin(t*18+1.5708)", "BY", "1"),
    ("BX-w*0.7*exp(-t*9)*sin(t*26+1.5708)", "BY", "min(t/0.12,1)"),
    # 54-56: 下からバネ
    ("BX", "BY+h*exp(-t*7)*sin(t*22+1.5708)", "1"),
    ("BX", "BY+h*exp(-t*5)*sin(t*18+1.5708)", "1"),
    ("BX", "BY+h*0.6*exp(-t*9)*sin(t*26+1.5708)", "min(t/0.12,1)"),
    # 57-60: 斜めスライド（4方向）
    ("BX+w*(1-min(t,0.35)/0.35)", "BY+200*(1-min(t,0.35)/0.35)", "min(t/0.35,1)"),
    ("BX+w*(1-min(t,0.35)/0.35)", "BY-200*(1-min(t,0.35)/0.35)", "min(t/0.35,1)"),
    ("BX-w*(1-min(t,0.35)/0.35)", "BY+200*(1-min(t,0.35)/0.35)", "min(t/0.35,1)"),
    ("BX-w*(1-min(t,0.35)/0.35)", "BY-200*(1-min(t,0.35)/0.35)", "min(t/0.35,1)"),
    # 61-64: チラチラ（フリッカー）
    ("BX", "BY", "if(lt(t,0.15),mod(floor(t*20),2),1)"),
    ("BX", "BY", "if(lt(t,0.20),mod(floor(t*15),2),1)"),
    ("BX", "BY", "if(lt(t,0.25),mod(floor(t*25),2),1)"),
    ("BX", "BY", "if(lt(t,0.10),mod(floor(t*30),2),1)"),
    # 65-67: ゆらぎ（ゾワー）
    ("BX+6*sin(t*2.1)", "BY+3*cos(t*1.8)", "min(t/0.45,1)"),
    ("BX+9*sin(t*1.6)", "BY+5*sin(t*2.3)", "min(t/0.55,1)"),
    ("BX+4*cos(t*2.8)", "BY+7*sin(t*1.9)", "1"),
    # 68-71: 部分スライド（画面半分）
    ("BX+w*0.4*(1-min(t,0.28)/0.28)", "BY", "min(t/0.22,1)"),
    ("BX-w*0.4*(1-min(t,0.28)/0.28)", "BY", "min(t/0.22,1)"),
    ("BX+w*0.6*(1-min(t,0.22)/0.22)", "BY", "min(t/0.18,1)"),
    ("BX-w*0.6*(1-min(t,0.22)/0.22)", "BY", "min(t/0.18,1)"),
    # 72-75: シェイク＋フェード合わせ技
    ("BX+28*sin(t*45)*exp(-t*10)", "BY", "min(t/0.28,1)"),
    ("BX+38*cos(t*38)*exp(-t*8)", "BY+16*sin(t*40)*exp(-t*9)", "min(t/0.22,1)"),
    ("BX", "BY+32*sin(t*50)*exp(-t*12)", "min(t/0.18,1)"),
    ("BX+22*sin(t*55)*exp(-t*10)", "BY+22*cos(t*45)*exp(-t*10)", "min(t/0.28,1)"),
    # 76-79: 超高速スライド（ビューン！ドン！）
    ("BX+w*(1-min(t,0.06)/0.06)", "BY", "1"),
    ("BX-w*(1-min(t,0.06)/0.06)", "BY", "1"),
    ("BX", "BY+h*(1-min(t,0.06)/0.06)", "1"),
    ("BX+w*(1-min(t,0.09)/0.09)", "BY", "min(t/0.09,1)"),
    # 80-83: ゆっくり出現（ゾゾゾ…）
    ("BX+w*(1-min(t,1.20)/1.20)", "BY", "min(t/0.80,1)"),
    ("BX-w*(1-min(t,1.00)/1.00)", "BY", "min(t/0.70,1)"),
    ("BX", "BY+h*(1-min(t,0.90)/0.90)", "min(t/0.60,1)"),
    ("BX", "BY-h*(1-min(t,0.80)/0.80)", "min(t/0.50,1)"),
    # 84-86: バネ＋フェード（右・左・下）
    ("BX+w*exp(-t*7)*sin(t*22+1.5708)", "BY", "min(t/0.18,1)"),
    ("BX-w*exp(-t*7)*sin(t*22+1.5708)", "BY", "min(t/0.18,1)"),
    ("BX", "BY+h*exp(-t*7)*sin(t*22+1.5708)", "min(t/0.18,1)"),
]

# 字幕フォントサイズ一覧（小〜超大、20種）
_SUBTITLE_FONT_SIZES = [
    35, 38, 42, 44, 48, 54, 58, 62, 66, 70,
    74, 78, 82, 86, 90, 96, 100, 108, 115, 120,
]


# ---------------------------------------------------------------------------
# 背景パターン生成（100種）
# ---------------------------------------------------------------------------

def _build_bg_patterns() -> list[str]:
    """ffmpeg geq フィルターによる背景パターン100種を文字列で生成する。"""
    pats: list[str] = []

    def grad(b: int, m: int, t: str, pw: float) -> str:
        if m == b:
            return str(b)
        return f"clip({b}+{m - b}*pow({t},{pw}),{min(b, m)},{max(b, m)})"

    def wave_t(fy: int, fx: int) -> str:
        if fy > 0 and fx > 0:
            return f"(sin(Y/H*{fy}*6.28318)+sin(X/W*{fx}*6.28318))/2"
        if fy > 0:
            return f"sin(Y/H*{fy}*6.28318)"
        return f"sin(X/W*{fx}*6.28318)"

    def mkpat(r: str, g: str, b: str) -> str:
        return f"geq=r='{r}':g='{g}':b='{b}'"

    # ---- グループA: 縦グラデ（下が明るい）20種 ----
    A = [
        (8, 153,   5,   5), (8,   5,   8, 148), (8,   5, 128,   8),
        (8,  85,   5, 128), (8, 118,  82,   5), (8,   5, 108, 100),
        (8, 138,  58,   5), (8, 128,   8,  68), (8,   5,  98, 118),
        (8,  28,   5, 140), (8,  98,  48,   8), (8, 118,   5,  78),
        (8,  18, 118,   5), (8, 128,  88,   5), (8,  52,  78, 112),
        (8, 178,   8,   8), (8,   8,  18, 158), (8,   5, 138,  48),
        (8, 100,   8, 148), (8, 148,  78,  28),
    ]
    for base, rm, gm, bm in A:
        pats.append(mkpat(grad(base, rm, "Y/H", 1.6), grad(base, gm, "Y/H", 1.8), grad(base, bm, "Y/H", 1.7)))

    # ---- グループB: 縦グラデ（上が明るい）15種 ----
    B = [
        (8, 155,   8,   8), (8,   8,   8, 155), (8,   8, 135,   8),
        (8,  88,   8, 135), (8, 125,  88,   8), (8,   8, 115, 108),
        (8, 145,  62,   8), (8, 135,   8,  72), (8,   8, 108, 128),
        (8,  28,   8, 148), (8, 108,  52,   8), (8, 128,   8,  82),
        (8,  25, 125,   8), (8, 138,  92,   8), (8,  48,  82, 118),
    ]
    for base, rm, gm, bm in B:
        pats.append(mkpat(grad(base, rm, "1-Y/H", 1.6), grad(base, gm, "1-Y/H", 1.8), grad(base, bm, "1-Y/H", 1.7)))

    # ---- グループC: 斜めグラデ（TL-BR / TR-BL 交互）15種 ----
    C = [
        (8, 148,   6,   6), (8,   6,   6, 148), (8,   6, 128,   6),
        (8,  88,   6, 128), (8, 118,  82,   6), (8,   6, 108, 102),
        (8, 138,  58,   6), (8, 128,   6,  68), (8,   6,  98, 118),
        (8,  28,   6, 142), (8,  98,  48,   6), (8, 118,   6,  78),
        (8,  22, 118,   6), (8, 128,  88,   6), (8,  52,  78, 112),
    ]
    for i, (base, rm, gm, bm) in enumerate(C):
        td = "(X/W+Y/H)/2" if i % 2 == 0 else "((W-X)/W+Y/H)/2"
        pats.append(mkpat(grad(base, rm, td, 1.5), grad(base, gm, td, 1.7), grad(base, bm, td, 1.6)))

    # ---- グループD: 放射状グラデ 15種 ----
    D = [
        (8, 148,   6,   6, 0.5, 0.5, False), (8,   6,   6, 148, 0.5, 0.5, False),
        (8,   6, 128,   6, 0.5, 0.5, False), (8,  88,   6, 128, 0.5, 0.5, False),
        (8, 118,  82,   6, 0.5, 0.5, False),
        (6, 155,   8,   8, 0.5, 0.3,  True), (6,   8,   8, 155, 0.5, 0.3,  True),
        (6,   8, 135,   8, 0.5, 0.3,  True), (6,  90,   8, 135, 0.5, 0.3,  True),
        (6, 128,  88,   8, 0.5, 0.3,  True),
        (6, 148,   6,   6, 0.0, 0.0,  True), (6,   6,   6, 148, 1.0, 0.0,  True),
        (6,   6, 128,   6, 0.5, 1.0,  True), (6,  88,   6, 128, 0.0, 1.0,  True),
        (6, 118,  82,   6, 0.5, 0.0,  True),
    ]
    for base, rm, gm, bm, cx, cy, dark_center in D:
        dist = f"hypot(X-{cx}*W,Y-{cy}*H)/hypot(W,H)"
        tr = f"(1-min({dist}*2,1))" if dark_center else f"min({dist}*1.5,1)"
        pats.append(mkpat(grad(base, rm, tr, 1.5), grad(base, gm, tr, 1.8), grad(base, bm, tr, 1.6)))

    # ---- グループE: 波・縞パターン 20種 ----
    E = [
        (10,  5, 25,  30,  5, 65, 6, 0), (25,  5, 10, 65,  5, 30,  0, 6),
        ( 5, 25, 10,   5, 65, 30, 4, 0), (15, 10, 30, 40, 15, 65,  8, 0),
        (20, 15,  5,  55, 40,  5, 0, 8), ( 5, 20, 25,  5, 55, 65,  5, 5),
        (20,  5, 15,  55,  5, 40, 6, 6), ( 5,  5, 20,  5,  5, 60, 10, 0),
        (20,  5,  5,  60,  5,  5, 0, 10), ( 5, 20,  5,  5, 60,  5, 12, 0),
        (10,  5, 20,  35,  5, 55, 6, 6), (20, 10,  5, 55, 35,  5,  6, 6),
        ( 5, 15, 20,   5, 45, 55, 8, 8), (15,  5, 20, 45,  5, 55,  4, 4),
        (20, 15,  5,  55, 45,  5, 5, 5), ( 8,  6, 22, 28,  8, 58,  3, 0),
        (22,  8,  6,  58, 28,  8, 0, 3), ( 6, 22,  8,  8, 58, 28,  3, 3),
        ( 8,  8, 22,  25, 25, 62, 7, 0), (22,  8,  8, 62, 25, 25,  0, 7),
    ]
    for br, bg, bb, ar, ag, ab, fy, fx in E:
        wt = wave_t(fy, fx)
        def wv(base: int, amp: int, _wt: str = wt) -> str:
            return f"clip({base}+{amp}*(({_wt})+1)/2,{base},{base + amp})"
        pats.append(mkpat(wv(br, ar), wv(bg, ag), wv(bb, ab)))

    # ---- グループF: 縦グラデ＋波オーバーレイ 15種 ----
    F = [
        (8, 148,   6,   6, 25,  5,  5, 4), (8,   6,   6, 148,  5,  5, 25, 5),
        (8,   6, 128,   6,  5, 22,  5, 6), (8,  88,   6, 128, 15,  5, 22, 4),
        (8, 118,  82,   6, 20, 14,  5, 5), (8,   6, 108, 102,  5, 18, 18, 6),
        (8, 138,  58,   6, 22, 10,  5, 3), (8, 128,   6,  68, 22,  5, 12, 5),
        (8,   6,  98, 118,  5, 16, 20, 4), (8,  28,   6, 142,  6,  5, 24, 6),
        (8,  98,  48,   6, 16,  8,  5, 5), (8, 118,   6,  78, 20,  5, 14, 4),
        (8,  22, 118,   6,  5, 20,  5, 7), (8, 128,  88,   6, 22, 15,  5, 5),
        (8,  52,  78, 112,  9, 13, 18, 4),
    ]
    for base, rm, gm, bm, wr, wg, wb, freq in F:
        wt = f"sin(Y/H*{freq}*6.28318)"
        def cb(b: int, m: int, wa: int, _wt: str = wt) -> str:
            return f"clip({b}+{m - b}*pow(Y/H,1.6)+{wa}*(({_wt})+1)/2,{b},{m + wa})"
        pats.append(mkpat(cb(base, rm, wr), cb(base, gm, wg), cb(base, bm, wb)))

    return pats


_BG_PATTERNS: list[str] = _build_bg_patterns()


def make_video_style() -> dict:
    """動画ごとのランダムスタイルを生成する。"""
    sub_box = random.choice(_BOX_COLORS)
    # 字幕スタイルタイプ: box(ボックスあり) / no_box(影のみ) / diagonal(斜め)
    sub_type = random.choices(
        ["box", "no_box", "diagonal"],
        weights=[5, 3, 2],
        k=1,
    )[0]
    # 字幕の縦位置（画面下からの距離）
    sub_y = random.choice([200, 350, 500, 650, 800])
    # 字幕の横揃え
    sub_x_align = random.choices(
        ["center", "left", "right"],
        weights=[6, 2, 2],
        k=1,
    )[0]
    # 斜め角度（ラジアン、±5〜12度）
    angle_deg = random.choice([-12, -10, -8, -6, 6, 8, 10, 12])
    diagonal_angle = angle_deg * 3.14159 / 180
    sub_fs  = random.choice(_SUBTITLE_FONT_SIZES)
    sub_lmc = max(5, int(840 // sub_fs))   # フォントサイズに連動した行最大文字数
    return {
        "subtitle_type":           sub_type,
        "subtitle_y":              sub_y,
        "subtitle_x_align":        sub_x_align,
        "diagonal_angle_rad":      diagonal_angle,
        "subtitle_color":          random.choice(_SUBTITLE_COLORS),
        "subtitle_box_color":      sub_box,
        "subtitle_box_opacity":    round(random.uniform(0.78, 0.96), 2),
        "subtitle_font_size":      sub_fs,
        "subtitle_line_max_chars": sub_lmc,
        "subtitle_border_w":       random.randint(2, 10),
        "subtitle_line_spacing":   random.randint(8, 22),
        "anim_idx":                random.randint(0, len(_ANIM_PATTERNS) - 1),
        "badge_color":             random.choice(_BADGE_COLORS),
        "badge_text":              random.choice(_BADGE_TEXTS),
        "title_color":             random.choice(_TITLE_COLORS),
        "title_font_size":         random.randint(84, 104),
        "title_box_opacity":       round(random.uniform(0.55, 0.75), 2),
        "ending_text":             random.choice(_ENDING_TEXTS),
        "ending_color":            random.choice(_TITLE_COLORS),
        "ending_font_size":        random.randint(88, 112),
        "ending_box_opacity":      round(random.uniform(0.65, 0.85), 2),
        "ending_line_spacing":     random.randint(18, 30),
    }


# ---------------------------------------------------------------------------
# フォント検索
# ---------------------------------------------------------------------------

def find_japanese_font() -> str | None:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    return hits[0] if hits else None


def find_japanese_fonts() -> list[str]:
    """利用可能な日本語フォントをすべて返す（ランダム選択用）。"""
    candidates = [
        # Noto Sans CJK Regular
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        # Noto Sans CJK Bold（明らかに太く見た目が異なる）
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        # IPA Gothic
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
        # WQY ZenHei（デザインが明らかに異なる太ゴシック）
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/wqy/wqy-zenhei.ttc",
        # Unifont（ピクセルフォント風、見た目が全く異なる）
        "/usr/share/fonts/truetype/unifont/unifont.ttf",
        "/usr/share/fonts/unifont/unifont.ttf",
    ]
    found = [p for p in candidates if Path(p).exists()]
    if not found:
        found = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    # assets/fonts/ にダウンロードされたフォントも追加
    found += glob.glob("assets/fonts/*.ttf") + glob.glob("assets/fonts/*.otf")
    return list(dict.fromkeys(found)) or []  # 重複排除・順序保持


_PIXABAY_QUERIES = [
    "horse racing", "thoroughbred horse racing", "jockey horse race",
    "horse racing track", "horse galloping racecourse", "horse racing Japan",
    "racetrack horses action", "horse racing finish line",
]
_HF_PROMPTS = [
    "cinematic photo of horses racing on a beautiful racecourse, dramatic lighting, high quality",
    "cinematic photo of jockey riding thoroughbred horse on racecourse, motion blur, high quality",
    "cinematic photo of horse galloping at sunset on racetrack, golden hour, high quality",
    "cinematic photo of horse racing crowd cheering at finish line, dramatic atmosphere, high quality",
]


def _save_image_bytes(content: bytes, filepath: str) -> bool:
    """バイト列をffmpegでJPEGに変換して保存する（Pillow不使用）。"""
    if len(content) < 1000:
        return False
    tmp = filepath + ".tmp"
    try:
        Path(tmp).write_bytes(content)
        res = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp, "-frames:v", "1", "-q:v", "2", filepath],
            capture_output=True, timeout=30,
        )
        return res.returncode == 0 and Path(filepath).exists() and Path(filepath).stat().st_size > 1000
    except Exception:
        return False
    finally:
        Path(tmp).unlink(missing_ok=True)


def generate_fallback_backgrounds(count: int = 3) -> list[str]:
    """競馬写真を Pixabay → HuggingFace → geqパターン の順で取得して assets/ai_*.jpg に保存する。"""
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "")
    hf_tokens = [t for t in [
        os.environ.get("HF_TOKEN", ""),
        os.environ.get("HF_TOKEN_2", ""),
        os.environ.get("HF_TOKEN_3", ""),
    ] if t]
    paths: list[str] = []

    for i in range(count):
        out = f"{ASSETS_DIR}/ai_{i}.jpg"

        # 1. Pixabay（実写競馬写真）
        if pixabay_key:
            query = _PIXABAY_QUERIES[i % len(_PIXABAY_QUERIES)]
            try:
                r = requests.get(
                    "https://pixabay.com/api/",
                    params={"key": pixabay_key, "q": query, "image_type": "photo",
                            "category": "animals", "min_width": 640,
                            "per_page": 20, "safesearch": "true"},
                    timeout=30,
                )
                hits = r.json().get("hits", [])
                if hits:
                    img_url = random.choice(hits).get("webformatURL", "")
                    if img_url:
                        img_r = requests.get(img_url, timeout=30)
                        if img_r.status_code == 200 and _save_image_bytes(img_r.content, out):
                            print(f"  Pixabay背景: {out} (query='{query}')")
                            paths.append(out)
                            continue
            except Exception as e:
                print(f"  [警告] Pixabay失敗: {e}", file=sys.stderr)

        # 2. HuggingFace（FLUX.1-schnell でAI競馬写真生成）
        if hf_tokens:
            prompt = _HF_PROMPTS[i % len(_HF_PROMPTS)]
            for token in hf_tokens:
                try:
                    r = requests.post(
                        "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"inputs": prompt},
                        timeout=120,
                    )
                    if r.status_code == 200 and _save_image_bytes(r.content, out):
                        print(f"  HF背景生成: {out}")
                        paths.append(out)
                        break
                    if r.status_code in (402, 403):
                        break
                except Exception as e:
                    print(f"  [警告] HF失敗: {e}", file=sys.stderr)
            if Path(out).exists() and Path(out).stat().st_size > 1000:
                continue

        # 3. 最終手段: geqパターン（ネット不要）
        vf_expr = random.choice(_BG_PATTERNS)
        res = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"color=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=1",
             "-vf", vf_expr, "-frames:v", "1", "-q:v", "3", out],
            capture_output=True,
        )
        if res.returncode == 0:
            print(f"  geqパターン背景（最終手段）: {out}")
            paths.append(out)

    return paths


# ---------------------------------------------------------------------------
# 音声尺取得
# ---------------------------------------------------------------------------

def get_audio_duration(audio_path: str) -> float:
    try:
        from mutagen.mp3 import MP3
        duration = MP3(audio_path).info.length
        print(f"  音声の総再生時間（mutagen）: {duration:.2f}秒")
        return duration
    except Exception as e:
        print(f"  [警告] mutagen失敗: {e}", file=sys.stderr)
    result = subprocess.run(["ffmpeg", "-i", audio_path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        duration = h * 3600 + mi * 60 + s
        print(f"  音声の総再生時間（ffmpeg）: {duration:.2f}秒")
        return duration
    print("  [警告] 音声長取得失敗。10秒にフォールバック。", file=sys.stderr)
    return 10.0


# ---------------------------------------------------------------------------
# テキスト折り返し
# ---------------------------------------------------------------------------

def wrap_text(text: str, max_chars: int = LINE_MAX_CHARS, max_lines: int = 0) -> str:
    lines = []
    for para in text.split("\n"):
        while len(para) > max_chars:
            lines.append(para[:max_chars])
            para = para[max_chars:]
            if max_lines and len(lines) >= max_lines:
                break
        if para and (not max_lines or len(lines) < max_lines):
            lines.append(para)
        if max_lines and len(lines) >= max_lines:
            break
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# クリップ生成（ffmpegのみ）
# ---------------------------------------------------------------------------

def make_clip(
    idx: int,
    bg_img: str | None,
    text: str,
    duration: float,
    font_path: str | None,
    tmp_dir: str,
    is_thumbnail: bool = False,
    thumb_title: str = "",
    thumb_subtitle: str = "",
    thumb_top: str = "",
    thumb_main: str = "",
    is_ending: bool = False,
    style: dict | None = None,
) -> str:
    """1セグメント分のMP4クリップを生成して返す。"""
    clip_path = f"{tmp_dir}/clip_{idx:04d}.mp4"
    duration = max(duration, 0.5)

    cmd = ["ffmpeg", "-y"]

    if bg_img and Path(bg_img).exists():
        cmd += ["-loop", "1", "-i", bg_img]
        chain = (
            f"[0:v]"
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            f"eq=brightness=-0.04,"
            f"vignette=PI/5"
        )
    else:
        cmd += ["-f", "lavfi", "-i",
                f"color=c=#0F0F28:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FPS}"]
        chain = "[0:v]vignette=PI/3.5"

    _diag_params: dict | None = None
    if font_path:
        fp = font_path.replace("'", "\\'")

        if is_thumbnail:
            # サムネイルフレーム: タイトルを大きく中央に表示
            _st = style or {}
            _tfs = _st.get("title_font_size", 96)
            # 全角CJK文字幅(=fontsize px)×文字数 + boxborderw(24)×2 ≤ VIDEO_WIDTH を保証
            _title_max_chars = max(5, int((VIDEO_WIDTH - 48) // _tfs))
            title_file = f"{tmp_dir}/thumb_title_{idx:04d}.txt"
            wrapped = wrap_text(thumb_title, max_chars=_title_max_chars)
            Path(title_file).write_text(wrapped, encoding="utf-8")
            tf = title_file.replace("'", "\\'")

            is_famous = os.environ.get("FAMOUS_HORSE_UPLOAD") == "1"

            if is_famous and thumb_main:
                # ── 映画ポスター風サムネイル ──

                # シネマティックな色調補正（暗め・コントラスト強・ウォームトーン）
                chain += (
                    ",eq=brightness=-0.18:saturation=1.30:contrast=1.12"
                    ",colorchannelmixer=rr=1.05:gg=0.95:bb=0.88"
                )

                main_file = f"{tmp_dir}/thumb_main_{idx:04d}.txt"
                Path(main_file).write_text(thumb_main, encoding="utf-8")
                mf = main_file.replace("'", "\\'")

                # 「狂気の」（左上・白・影付き）
                if thumb_top:
                    top_file = f"{tmp_dir}/thumb_top_{idx:04d}.txt"
                    Path(top_file).write_text(thumb_top, encoding="utf-8")
                    tpf = top_file.replace("'", "\\'")
                    chain += (
                        f",drawtext=textfile='{tpf}':fontfile='{fp}':"
                        f"fontsize=78:fontcolor=0xFFFFFF:"
                        f"x=60:y=160:"
                        f"borderw=4:bordercolor=0x000000:"
                        f"shadowcolor=0x000000@0.9:shadowx=3:shadowy=3"
                    )

                # 「大逃げ」グロー外層（赤・広め・低透明）
                chain += (
                    f",drawtext=textfile='{mf}':fontfile='{fp}':"
                    f"fontsize=180:fontcolor=0xFF2200@0.22:"
                    f"x=60:y=760:"
                    f"borderw=32:bordercolor=0xFF2200@0.18"
                )
                # 「大逃げ」グロー中層
                chain += (
                    f",drawtext=textfile='{mf}':fontfile='{fp}':"
                    f"fontsize=180:fontcolor=0xFF3300@0.38:"
                    f"x=60:y=760:"
                    f"borderw=16:bordercolor=0xFF3300@0.40"
                )
                # 「大逃げ」グロー内層（鮮明赤）
                chain += (
                    f",drawtext=textfile='{mf}':fontfile='{fp}':"
                    f"fontsize=180:fontcolor=0xFF4400@0.52:"
                    f"x=60:y=760:"
                    f"borderw=7:bordercolor=0xFF4400@0.62"
                )
                # 「大逃げ」本体（白・黒縁・ドロップシャドウ）
                chain += (
                    f",drawtext=textfile='{mf}':fontfile='{fp}':"
                    f"fontsize=180:fontcolor=0xFFFFFF:"
                    f"x=60:y=760:"
                    f"borderw=5:bordercolor=0x000000:"
                    f"shadowcolor=0x000000@0.9:shadowx=6:shadowy=6"
                )

                # 「馬名」（中サイズ・影付き）
                chain += (
                    f",drawtext=textfile='{tf}':fontfile='{fp}':"
                    f"fontsize=84:fontcolor=0xFFFFFF:"
                    f"x=60:y=1070:"
                    f"borderw=4:bordercolor=0x000000:"
                    f"shadowcolor=0x000000@0.9:shadowx=3:shadowy=3"
                )

            elif is_famous:
                # fallback: thumb_main 未設定時のシンプルデザイン
                chain += ",eq=brightness=-0.15"
                if thumb_top:
                    top_file = f"{tmp_dir}/thumb_top_{idx:04d}.txt"
                    Path(top_file).write_text(thumb_top, encoding="utf-8")
                    tpf = top_file.replace("'", "\\'")
                    chain += (
                        f",drawtext=textfile='{tpf}':fontfile='{fp}':"
                        f"fontsize=80:fontcolor=0xFFFFFF:"
                        f"x=60:y=900:borderw=7:bordercolor=0x000000"
                    )
                chain += (
                    f",drawtext=textfile='{tf}':fontfile='{fp}':"
                    f"fontsize=130:fontcolor=0xFFFFFF:"
                    f"x=60:y=1050:borderw=8:bordercolor=0x000000"
                )
            else:
                # ── ニュース系サムネイル（スタイルランダム） ──
                s = style or {}
                badge_file = f"{tmp_dir}/thumb_badge_{idx:04d}.txt"
                Path(badge_file).write_text(s.get("badge_text", "競馬速報"), encoding="utf-8")
                bf = badge_file.replace("'", "\\'")

                badge_col = s.get("badge_color", "0xD21E1E")
                title_op  = s.get("title_box_opacity", 0.65)

                # バッジ（左上）
                chain += (
                    f",drawtext=textfile='{bf}':fontfile='{fp}':"
                    f"fontsize=54:fontcolor=0xFFFFFF:"
                    f"x=44:y=70:"
                    f"box=1:boxcolor={badge_col}@0.95:boxborderw=22"
                )
                # タイトルテキスト（キーワードハイライト＋ボックススタイルランダム）
                _color_pair = random.choice(_THUMB_COLOR_PAIRS)
                _color_base = _color_pair[0]   # 通常テキスト色
                _color_hi   = _color_pair[1]   # キーワード強調色
                _box_style_tpl = random.choice(_THUMB_BOX_STYLES)
                _box_style = _box_style_tpl.replace("OP", str(title_op))
                _t_lines = [l for l in wrapped.split("\n") if l]
                _line_h = _tfs + 16
                for _li, _line in enumerate(_t_lines):
                    _lf = f"{tmp_dir}/thumb_line_{idx:04d}_{_li}.txt"
                    Path(_lf).write_text(_line, encoding="utf-8")
                    _lfe = _lf.replace("'", "\\'")
                    _y = 720 + _li * _line_h

                    # Pass1: 行全体を基本色＋ボックスで描画
                    chain += (
                        f",drawtext=textfile='{_lfe}':fontfile='{fp}':"
                        f"fontsize={_tfs}:fontcolor={_color_base}:"
                        f"x=(w-text_w)/2:y={_y}:"
                        f"{_box_style}"
                    )

                    # Pass2: キーワードのみ上から強調色で重ねて描画
                    _kw_spans = _find_keywords(_line)
                    if _kw_spans:
                        _line_w = _text_px(_line, _tfs)
                        _x_start = max(0, (VIDEO_WIDTH - _line_w) // 2)
                        for _ks, _ke in _kw_spans:
                            _kw_text = _line[_ks:_ke]
                            _kw_x = _x_start + _text_px(_line[:_ks], _tfs)
                            _kwf = f"{tmp_dir}/thumb_kw_{idx:04d}_{_li}_{_ks}.txt"
                            Path(_kwf).write_text(_kw_text, encoding="utf-8")
                            _kwfe = _kwf.replace("'", "\\'")
                            chain += (
                                f",drawtext=textfile='{_kwfe}':fontfile='{fp}':"
                                f"fontsize={_tfs}:fontcolor={_color_hi}:"
                                f"x={_kw_x}:y={_y}:"
                                f"borderw=3:bordercolor=0x000000"
                            )

        elif is_ending:
            s = style or {}
            ending_file = f"{tmp_dir}/ending_text.txt"
            Path(ending_file).write_text(
                s.get("ending_text", "チャンネル登録\nよろしく！\n\n毎日更新中！"),
                encoding="utf-8",
            )
            ef = ending_file.replace("'", "\\'")
            e_col = s.get("ending_color", "0xFFD700")
            e_fs  = s.get("ending_font_size", 100)
            e_op  = s.get("ending_box_opacity", 0.75)
            e_ls  = s.get("ending_line_spacing", 24)
            chain += (
                f",drawtext=textfile='{ef}':fontfile='{fp}':"
                f"fontsize={e_fs}:fontcolor={e_col}:"
                f"x=(w-text_w)/2:y=760:"
                f"line_spacing={e_ls}:"
                f"box=1:boxcolor=0x000000@{e_op}:boxborderw=32:"
                f"borderw=4:bordercolor=0x000000"
            )

        else:
            # 通常字幕クリップ
            s = style or {}
            sub_type = s.get("subtitle_type", "box")
            sub_fs   = s.get("subtitle_font_size", FONT_SIZE)
            lmc      = s.get("subtitle_line_max_chars", LINE_MAX_CHARS)
            text_file = f"{tmp_dir}/text_{idx:04d}.txt"
            Path(text_file).write_text(wrap_text(text, max_chars=lmc, max_lines=7), encoding="utf-8")
            tf = text_file.replace("'", "\\'")
            sub_col = s.get("subtitle_color", "0xFFFFFF")
            sub_box = s.get("subtitle_box_color", "0x000014")
            sub_op  = s.get("subtitle_box_opacity", 0.88)
            sub_bw  = s.get("subtitle_border_w", 3)
            sub_ls  = s.get("subtitle_line_spacing", 14)
            sub_y   = s.get("subtitle_y", 700)
            x_align = s.get("subtitle_x_align", "center")
            x_expr  = (
                "(w-text_w)/2" if x_align == "center"
                else ("60" if x_align == "left" else "w-text_w-60")
            )
            # アニメーション適用
            anim = _ANIM_PATTERNS[s.get("anim_idx", 0) % len(_ANIM_PATTERNS)]
            ax = anim[0].replace("BX", f"({x_expr})")
            ay = anim[1].replace("BY", f"(h-text_h-{sub_y})")
            aa = anim[2]
            if sub_type == "no_box":
                # ボックスなし：縁取り＋シャドウ
                chain += (
                    f",drawtext=textfile='{tf}':fontfile='{fp}':"
                    f"fontsize={sub_fs}:fontcolor={sub_col}:"
                    f"x='{ax}':y='{ay}':alpha='{aa}':"
                    f"line_spacing={sub_ls}:"
                    f"borderw={sub_bw}:bordercolor=0x000000:"
                    f"shadowcolor=0x000000@0.8:shadowx=4:shadowy=4"
                )
            elif sub_type == "diagonal":
                # 斜めテキスト（アニメなし・filter_complex で後処理）
                _diag_params = {
                    "tf": tf, "fp": fp,
                    "sub_col": sub_col, "sub_fs": sub_fs, "sub_ls": sub_ls,
                    "angle_rad": s.get("diagonal_angle_rad", 0.2),
                }
            else:
                # ボックスあり
                chain += (
                    f",drawtext=textfile='{tf}':fontfile='{fp}':"
                    f"fontsize={sub_fs}:fontcolor={sub_col}:"
                    f"x='{ax}':y='{ay}':alpha='{aa}':"
                    f"line_spacing={sub_ls}:"
                    f"box=1:boxcolor={sub_box}@{sub_op}:boxborderw=36:"
                    f"borderw={sub_bw}:bordercolor={sub_box}"
                )

    if _diag_params:
        d = _diag_params
        chain += (
            f"[bg];"
            f"nullsrc=size={VIDEO_WIDTH}x{VIDEO_HEIGHT}:rate={FPS},format=rgba[canvas];"
            f"[canvas]drawtext=textfile='{d['tf']}':fontfile='{d['fp']}':"
            f"fontsize={d['sub_fs']}:fontcolor={d['sub_col']}:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:"
            f"line_spacing={d['sub_ls']}:"
            f"borderw=7:bordercolor=0x000000:"
            f"shadowcolor=0x000000@0.8:shadowx=4:shadowy=4[text_base];"
            f"[text_base]rotate=angle={d['angle_rad']:.4f}:c=0x00000000:ow=iw:oh=ih[text_rot];"
            f"[bg][text_rot]overlay=0:0:format=auto[vout]"
        )
    else:
        chain += "[vout]"

    cmd += [
        "-filter_complex", chain,
        "-map", "[vout]",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-t", str(duration),
        clip_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [警告] クリップ{idx}生成失敗:\n{result.stderr[-600:]}", file=sys.stderr)
        # フォールバック: 単色クリップ
        fb = [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c=#0F0F28:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FPS}:d={duration}",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-t", str(duration), clip_path,
        ]
        subprocess.run(fb, check=True, capture_output=True)

    return clip_path


# ---------------------------------------------------------------------------
# 1本の動画を生成
# ---------------------------------------------------------------------------

def build_video(
    script_path: Path,
    audio_path: str,
    output_path: str,
    assets_images: list[str],
    font_path: str | None,
    title: str = "",
    subtitle: str = "",
    thumb_top: str = "",
    thumb_main: str = "",
) -> None:
    script = script_path.read_text(encoding="utf-8").strip()
    # 「。」でも改行でも分割する
    # 名馬列伝スクリプトは改行区切り、ニューススクリプトは「。」区切りのため両対応
    raw = [s.strip() for s in re.split(r"[。\n]+", script) if s.strip()]
    sentences = raw

    if not sentences:
        print("  [警告] セリフが空です。スキップします。")
        return

    # 動画ごとにランダムスタイルを生成（名馬列伝はスタイル変更なし）
    is_famous = os.environ.get("FAMOUS_HORSE_UPLOAD") == "1"
    style = None if is_famous else make_video_style()
    # フォントをランダム選択（複数利用可能な場合）
    if not is_famous:
        font_candidates = find_japanese_fonts()
        if len(font_candidates) > 1:
            font_path = random.choice(font_candidates)
    if style:
        print(f"  スタイル: badge={style['badge_text']} col={style['subtitle_color']} "
              f"fs={style['subtitle_font_size']} font={Path(font_path).name if font_path else 'なし'}")

    audio_duration = get_audio_duration(audio_path)

    # タイトル読み上げ分を含む総文字数で按分
    title_chars = len(title + "。") if title else 0
    script_chars = sum(len(s) for s in sentences)
    total_chars = title_chars + script_chars

    durations: list[float] = []
    for s in sentences:
        d = audio_duration * len(s) / total_chars if total_chars > 0 else audio_duration / len(sentences)
        d = max(MIN_CUT_DURATION, d)
        durations.append(d)

    print(f"  セリフ数: {len(sentences)}")
    for i, (s, d) in enumerate(zip(sentences, durations)):
        print(f"    [{i}] 「{s[:20]}」 → {d:.2f}秒")

    tmp_dir = tempfile.mkdtemp(prefix="keiba_video_")
    try:
        clip_paths: list[str] = []

        # --- サムネイルフレーム（タイトル読み上げ尺） ---
        if title:
            thumb_duration = (audio_duration * title_chars / total_chars) if total_chars > 0 else THUMBNAIL_DURATION
            thumb_duration = max(THUMBNAIL_DURATION, thumb_duration)
            thumb_bg = assets_images[0] if assets_images else None
            clip_paths.append(
                make_clip(
                    0, thumb_bg, "", thumb_duration, font_path, tmp_dir,
                    is_thumbnail=True, thumb_title=title, thumb_subtitle=subtitle,
                    thumb_top=thumb_top, thumb_main=thumb_main, style=style,
                )
            )
            print(f"  サムネイルフレーム: {thumb_duration:.2f}秒")

        # --- 字幕クリップ ---
        for i, (sentence, duration) in enumerate(zip(sentences, durations)):
            bg_img = assets_images[(i + 1) % len(assets_images)] if assets_images else None
            clip_paths.append(
                make_clip(i + 1, bg_img, sentence, duration, font_path, tmp_dir, style=style)
            )
            print(f"  [{i+1}/{len(sentences)}] {duration:.2f}s 「{sentence[:20]}」")

        # --- エンディングカード ---
        ending_bg = assets_images[len(sentences) % len(assets_images)] if assets_images else None
        clip_paths.append(
            make_clip(
                len(sentences) + 1, ending_bg, "", ENDING_DURATION, font_path, tmp_dir,
                is_ending=True, style=style,
            )
        )
        print(f"  エンディング: {ENDING_DURATION}秒")

        # --- concat ---
        concat_txt = f"{tmp_dir}/concat.txt"
        with open(concat_txt, "w", encoding="utf-8") as f:
            for cp in clip_paths:
                f.write(f"file '{cp}'\n")

        silent_mp4 = f"{tmp_dir}/silent.mp4"
        print("  クリップ結合中...")
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_txt,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
            silent_mp4,
        ], check=True, capture_output=True)

        # --- 音声 + BGM ミックス ---
        print("  音声結合中...")
        bgm_files = sorted(glob.glob(f"{BGM_DIR}/*.mp3") + glob.glob(f"{BGM_DIR}/*.m4a"))
        bgm_path = random.choice(bgm_files) if bgm_files else None

        thumb_dur = (audio_duration * title_chars / total_chars) if (title and total_chars > 0) else (THUMBNAIL_DURATION if title else 0.0)
        thumb_dur = max(THUMBNAIL_DURATION, thumb_dur) if title else 0.0
        total_duration = thumb_dur + sum(durations) + ENDING_DURATION

        # 名馬列伝シリーズはドラマチックBGMを少し大きめにミックス
        is_famous = os.environ.get("FAMOUS_HORSE_UPLOAD") == "1"
        bgm_vol = 0.22 if is_famous else BGM_VOLUME

        cmd = ["ffmpeg", "-y", "-i", silent_mp4, "-i", audio_path]
        if bgm_path:
            print(f"  BGM使用: {Path(bgm_path).name} (volume weight={bgm_vol})")
            cmd += ["-stream_loop", "-1", "-i", bgm_path]
            narr_filter = f"[1:a]apad=whole_dur={total_duration:.3f}[narr]"
            cmd += [
                "-filter_complex",
                f"{narr_filter};[narr][2:a]amix=inputs=2:duration=first:weights=1 {bgm_vol}[aout]",
                "-map", "0:v", "-map", "[aout]",
            ]
        else:
            print("  BGMなし（assets/bgm/ に .mp3 を置くと自動適用されます）")
            cmd += [
                "-af", f"apad=whole_dur={total_duration:.3f}",
                "-map", "0:v", "-map", "1:a",
            ]

        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", output_path]
        subprocess.run(cmd, check=True, capture_output=True)

        size_mb = Path(output_path).stat().st_size / (1024 * 1024)
        print(f"  最終動画生成完了: {output_path} ({size_mb:.1f} MB)")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("  tmpフォルダを削除しました。")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== 動画生成開始 ===")

    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("ニュースが0件のためスキップします。")
        sys.exit(0)

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    # 背景画像収集: generate_images.py が取得した ai_*.jpg を使う
    # 画像が0枚なら失敗する（フォールバック生成は行わない）
    assets_images = sorted(
        p for p in glob.glob(f"{ASSETS_DIR}/ai_*.jpg")
        if Path(p).stat().st_size > 1000
    )
    if not assets_images:
        print("  [情報] assets/ai_*.jpg なし。バリエーション背景を自動生成します。")
        assets_images = generate_fallback_backgrounds(count=3)
        if not assets_images:
            print("[エラー] 背景画像の生成に失敗しました。", file=sys.stderr)
            sys.exit(1)
    print(f"  AI画像を使用 ({len(assets_images)}枚): {[Path(p).name for p in assets_images]}")

    font_path = find_japanese_font()
    print(f"  日本語フォント: {font_path or '見つからず（テキストなし）'}")

    script_files = sorted(Path(OUTPUT_DIR).glob("script_*.txt"))
    if not script_files:
        print(f"[エラー] {OUTPUT_DIR}/script_*.txt が見つかりません。", file=sys.stderr)
        sys.exit(1)

    for script_file in script_files:
        idx = int(script_file.stem.split("_")[1])
        audio_path = f"{OUTPUT_DIR}/audio_{idx}.mp3"
        output_path = f"{OUTPUT_DIR}/video_{idx}.mp4"

        if not Path(audio_path).exists():
            print(f"  [警告] {audio_path} が見つかりません。スキップします。")
            continue

        item = news_items[idx] if idx < len(news_items) else {}
        title = item.get("title", "")
        print(f"\n--- 動画生成 [{idx}]: {title[:50]} ---")

        subtitle   = item.get("summary", "")
        thumb_top  = item.get("thumbnail_top", "")
        thumb_main = item.get("thumbnail_main", "")
        build_video(
            script_file, audio_path, output_path, assets_images, font_path,
            title=title, subtitle=subtitle, thumb_top=thumb_top, thumb_main=thumb_main,
        )

    video_files = list(Path(OUTPUT_DIR).glob("video_*.mp4"))
    print(f"\n{len(video_files)} 本の動画を生成しました。")


if __name__ == "__main__":
    main()
