#!/usr/bin/env python3
"""南関東競馬「格付け解説」動画ジェネレーター（ffmpegのみ・Pillow/自前numpy不使用）

1本のナレーション原稿(.txt)から、縦型ショート(1080x1920)と
横型フル版(1920x1080)の両方を生成できる自己完結スクリプト。

設計:
  - 原稿は「1行 = 1字幕セグメント」。行ごとに音声を合成して尺を測り、
    その行の長さで字幕クリップを作る。WordBoundary に依存しないため
    どの TTS エンジンでも字幕が正確に同期する。
  - TTS は edge-tts（本番標準・ja-JP-NanamiNeural）を優先し、ネットワーク
    制限で使えない環境では pyopenjtalk（オフライン）へ自動フォールバック。

プロジェクトルール（CLAUDE.md）順守:
  - 背景・字幕パネルはすべて ffmpeg（lavfi gradients + drawtext box=1）で生成。
    Pillow / 自前の numpy 画像処理・drawbox は一切使わない。
  - 日本語テキストは textfile= でファイル経由（エスケープ回避）。
  - サムネイルは動画ネイティブ解像度のままフレーム抽出（-s リサイズ禁止）。

使い方:
  python scripts/create_nankan_class_video.py                 # 両方まとめて生成
  python scripts/create_nankan_class_video.py \
      --script data/nankan_class_short.txt \
      --orientation portrait --out output/nankan_class_short.mp4
"""

import argparse
import asyncio
import glob
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

VOICE  = "ja-JP-NanamiNeural"   # 解説向け女性ナレーター（edge-tts）
RATE   = "-2%"
VOLUME = "+0%"

FPS          = 30
BGM_VOLUME   = 0.12
ENDING_DUR   = 3.5
ENDING_DUR_CTA = 4.2           # 本編誘導カードは読めるよう少し長め（等速）
GAP          = 0.35            # 行間の無音（読みやすさ）
SERIES_LABEL = "南関東 格付け講座"
CTA_BADGE    = "▶本編で詳しく"   # ショートに常時表示する本編誘導バッジ
CTA_ENDING   = "詳しくは本編で！\n▶ 概要欄・コメントから"

# 競馬場のターフをイメージしたディープグリーン基調のグラデーション
GRAD_TOP    = "0x0d3b1e"
GRAD_BOTTOM = "0x041410"

# インフォグラフィック配色（0xRRGGBB）
C_GOLD  = "FFD24A"   # Aクラス / 強調
C_BLUE  = "5AA9E6"   # Bクラス
C_GREEN = "66BB6A"   # Cクラス
C_RED   = "E57373"   # 降級 / 注意
C_DARK  = "06251a"   # チップ内文字
C_WHITE = "FFFFFF"

CLASSES = ["A1", "A2", "B1", "B2", "B3", "C1", "C2", "C3"]


def _class_color(name: str) -> str:
    return C_GOLD if name[0] == "A" else C_BLUE if name[0] == "B" else C_GREEN


# ---------------------------------------------------------------------------
# TLS: edge-tts は certifi 固定 SSL を使うため実行環境のプロキシ CA を信頼させる
# ---------------------------------------------------------------------------
def _trust_proxy_ca() -> None:
    import os
    import ssl
    try:
        import certifi
        import edge_tts.communicate as _ec
    except Exception:
        return
    ctx = ssl.create_default_context(cafile=certifi.where())
    for ca in (
        os.environ.get("SSL_CERT_FILE"),
        os.environ.get("REQUESTS_CA_BUNDLE"),
        "/root/.ccr/ca-bundle.crt",
    ):
        if ca and Path(ca).exists():
            try:
                ctx.load_verify_locations(cafile=ca)
            except Exception:
                pass
    _ec._SSL_CTX = ctx


# ---------------------------------------------------------------------------
# 環境ヘルパー
# ---------------------------------------------------------------------------
def find_font() -> str | None:
    for p in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]:
        if Path(p).exists():
            return p
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    return hits[0] if hits else None


def find_bgm() -> str | None:
    for c in ["assets/bgm/horse_drama_bgm.mp3", "assets/bgm/bgm_1.mp3", "assets/bgm/bgm_2.mp3"]:
        if Path(c).exists():
            return c
    allb = sorted(glob.glob("assets/bgm/*.mp3"))
    return allb[0] if allb else None


def probe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 2.0


def load_narration(script_path: str) -> list[str]:
    """注釈行（【】や ─、★ 等）を除いたナレーション行リストを返す。"""
    lines = []
    for raw in Path(script_path).read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith(("【", "─", "│", "#")):
            continue
        s = s.replace("★", "").strip()
        if s:
            lines.append(s)
    return lines


# ---------------------------------------------------------------------------
# TTS（edge-tts 優先 / pyopenjtalk フォールバック）
# ---------------------------------------------------------------------------
async def _edge_one(text: str, out_mp3: str) -> None:
    import edge_tts
    comm = edge_tts.Communicate(text, VOICE, rate=RATE, volume=VOLUME)
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])


def edge_synth(text: str, out_mp3: str) -> bool:
    try:
        asyncio.run(_edge_one(text, out_mp3))
        return Path(out_mp3).exists() and Path(out_mp3).stat().st_size > 500
    except Exception:
        return False


def oj_synth(text: str, out_wav: str) -> bool:
    """pyopenjtalk によるオフライン合成。soundfile を使わず wave で書き出す。"""
    try:
        import wave
        import numpy as np  # pyopenjtalk が内部依存。音声バッファ変換にのみ使用
        import pyopenjtalk
        x, sr = pyopenjtalk.tts(text)
        pcm = np.clip(x, -32768, 32767).astype("<i2").tobytes()
        with wave.open(out_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sr))
            w.writeframes(pcm)
        return Path(out_wav).exists() and Path(out_wav).stat().st_size > 500
    except Exception as e:
        print(f"  [警告] pyopenjtalk 合成失敗: {e}", file=sys.stderr)
        return False


_G_CTX = ssl.create_default_context()
for _ca in ("/root/.ccr/ca-bundle.crt", os.environ.get("SSL_CERT_FILE"),
            os.environ.get("REQUESTS_CA_BUNDLE")):
    if _ca and Path(_ca).exists():
        try:
            _G_CTX.load_verify_locations(cafile=_ca)
        except Exception:
            pass


def _split_for_tts(text: str, maxlen: int = 160) -> list[str]:
    """translate_tts の長さ制限対策。句読点で maxlen 以下に分割。"""
    if len(text) <= maxlen:
        return [text]
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in "、。！？" and len(cur) >= maxlen * 0.6:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out or [text]


def google_synth(text: str, out_mp3: str) -> bool:
    """Google 翻訳の TTS（translate.googleapis.com）で自然な日本語音声を合成。
    ニューラル系の edge-tts が使えない環境向けの自然声フォールバック。"""
    data = b""
    for chunk in _split_for_tts(text):
        q = urllib.parse.quote(chunk)
        url = ("https://translate.googleapis.com/translate_tts"
               f"?ie=UTF-8&client=gtx&tl=ja&q={q}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        ok = False
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20, context=_G_CTX) as r:
                    data += r.read()
                ok = True
                break
            except Exception:
                time.sleep(1.2 * (attempt + 1))
        if not ok:
            return False
    if len(data) < 400:
        return False
    Path(out_mp3).write_bytes(data)
    return True


def choose_engine() -> str:
    _trust_proxy_ca()
    if edge_synth("テスト", "/tmp/_engine_probe.mp3"):
        return "edge"
    if google_synth("テスト", "/tmp/_engine_probe_g.mp3"):
        print("  [情報] edge-tts不可 → Google音声(自然声)を使用", file=sys.stderr)
        return "google"
    print("  [情報] ネットTTS不可 → pyopenjtalk(オフライン機械音声)に切替", file=sys.stderr)
    return "oj"


def synth_line(engine: str, text: str, tmp_dir: str, idx: int) -> tuple[str, float]:
    if engine == "edge":
        out = f"{tmp_dir}/a_{idx:04d}.mp3"
        if edge_synth(text, out):
            return out, probe_duration(out)
        engine = "google"
    if engine == "google":
        out = f"{tmp_dir}/a_{idx:04d}.mp3"
        if google_synth(text, out):
            return out, probe_duration(out)
        engine = "oj"
    out = f"{tmp_dir}/a_{idx:04d}.wav"
    if oj_synth(text, out):
        return out, probe_duration(out)
    # 最終手段: 無音
    out = f"{tmp_dir}/a_{idx:04d}_sil.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", "1.6", out],
                   capture_output=True)
    return out, 1.6


# ---------------------------------------------------------------------------
# 動画クリップ生成
# ---------------------------------------------------------------------------
def geometry(orientation: str) -> dict:
    if orientation == "portrait":
        return {"orient": "portrait", "w": 1080, "h": 1920,
                "label_size": 46, "label_y": 150,
                "sub_size": 58, "sub_y": "h-text_h-560", "sub_chars": 14, "end_size": 60,
                "cx": 540}
    return {"orient": "landscape", "w": 1920, "h": 1080,
            "label_size": 46, "label_y": 60,
            "sub_size": 56, "sub_y": "h-text_h-80", "sub_chars": 30, "end_size": 66,
            "cx": 960}


def wrap_text(text: str, max_chars: int) -> str:
    out = []
    for para in text.split("\n"):
        while len(para) > max_chars:
            out.append(para[:max_chars])
            para = para[max_chars:]
        if para:
            out.append(para)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 図解シーン（ffmpeg drawtext のみ・Pillow/drawbox 不使用）
#   各 build_* は [0:v] に続けて連結する drawtext 群（先頭"," 付き）を返す。
#   テキストは textfile= でファイル経由（エスケープ回避）。
# ---------------------------------------------------------------------------
def pick_scene(line: str) -> str:
    """ナレーション1行の内容から表示する図解シーンを選ぶ。
    ※ ナレーションと図がズレないよう、判定の優先順位が重要。
       「上のクラスへ昇級」のような文は『ポイント』の語が含まれても
       まず昇級アニメ(ladder_up)を出す。"""
    s = line
    def has(*ws): return any(w in s for w in ws)

    # 1) イントロ（4競馬場）
    if has("大井", "4つの競馬場", "4競馬場"):
        return "intro"
    # 2) 昇級初戦の注意（最優先で拾う）
    if has("昇級初戦", "取りこぼし", "慎重"):
        return "caution"
    # 3) 狙い目チャート（「狙い目」「力量差リセット」など）
    if has("狙い目", "妙味", "配当", "力量差", "実力馬", "格下相手"):
        return "target"
    # 4) 昇級（上方向アニメ）— 「ポイント」より先に判定
    if has("昇級", "上のクラス", "積み直し", "最低ポイント", "ポイントにリセット"):
        return "ladder_up"
    # 5) 編成替えカレンダー（馬齢降級もここ）
    if has("年末年始", "馬齢", "上半期", "下半期", "編成替え", "年に2回", "年2回",
           "2か月", "2ヶ月", "顔ぶれ", "組み直"):
        return "calendar"
    # 6) 降級（下方向アニメ）
    if has("降級", "落ちて", "1つ下", "下のクラス", "近3走", "近走成績", "振るわない"):
        return "ladder_down"
    # 7) 格付ポイント制の仕組み
    if has("ポイント", "1着から5着", "着ポイント", "賞金", "加算", "合計"):
        return "points"
    # 8) クラス階層図
    if has("A1", "8つ", "8段階", "C3", "クラスは", "クラスに", "B1", "別の編成", "格付け"):
        return "ladder"
    return "plain"


def _chip(p, cxv, y, size, fontcolor, boxcolor, border=18, alpha=None,
          enable=None, ls=10, fp=""):
    """中心 x=cxv に揃えたラベル付きチップ(drawtext)。"""
    s = (f"drawtext=textfile='{p}':fontfile='{fp}':fontsize={size}:"
         f"fontcolor=0x{fontcolor}:x={cxv}-text_w/2:y={y}:"
         f"box=1:boxcolor=0x{boxcolor}@0.95:boxborderw={border}:"
         f"line_spacing={ls}:borderw=2:bordercolor=0x{C_DARK}")
    if alpha is not None:
        s += f":alpha='{alpha}'"
    if enable is not None:
        s += f":enable='{enable}'"
    return s


def _text(p, x, y, size, fontcolor, fp, alpha=None, enable=None, ls=10,
          box=False, boxcolor=C_DARK, border=14):
    s = (f"drawtext=textfile='{p}':fontfile='{fp}':fontsize={size}:"
         f"fontcolor=0x{fontcolor}:x={x}:y={y}:line_spacing={ls}:"
         f"borderw=2:bordercolor=0x000000")
    if box:
        s += f":box=1:boxcolor=0x{boxcolor}@0.9:boxborderw={border}"
    if alpha is not None:
        s += f":alpha='{alpha}'"
    if enable is not None:
        s += f":enable='{enable}'"
    return s


def build_scene(scene, geom, tmp_dir, idx, font_path):
    """シーン名に応じた drawtext 連結文字列（先頭"," 付き）を返す。"""
    if scene in ("plain", None):
        return ""
    fp = font_path.replace("'", "\\'")
    cx = geom["cx"]
    port = geom["orient"] == "portrait"
    parts = []

    def wf(key, text):
        path = f"{tmp_dir}/s_{idx:04d}_{key}.txt"
        Path(path).write_text(text, encoding="utf-8")
        return path.replace("'", "\\'")

    PULSE = "0.45+0.45*sin(2*PI*t*1.2)"   # 点滅(0〜0.9)

    # ----- クラス階層ラダー（ladder / ladder_up / ladder_down 共通） -----
    def ladder(marker=None):
        sz   = 46 if port else 42
        y0   = 360 if port else 250
        gap  = 92 if port else 74
        # 上下の「強い/弱い」ガイド
        parts.append(_chip(wf("strong", "▲ 強い・格上"), cx, y0 - 66,
                           int(sz * 0.66), C_GOLD, C_DARK, border=10, fp=fp))
        parts.append(_chip(wf("weak", "▼ 弱い・格下"), cx, y0 + 8 * gap,
                           int(sz * 0.66), C_GREEN, C_DARK, border=10, fp=fp))
        for i, name in enumerate(CLASSES):
            parts.append(_chip(wf(f"c{i}", name), cx, y0 + i * gap, sz,
                               C_DARK, _class_color(name), border=16, fp=fp))
        if marker == "up":
            r_from, r_to, arr, col = 6, 4, "▲", C_GREEN   # C2→B3
        elif marker == "down":
            r_from, r_to, arr, col = 3, 5, "▼", C_RED     # B2→C1
        else:
            return
        y_from, y_to = y0 + r_from * gap, y0 + r_to * gap
        mx = cx + (150 if port else 150)
        yexpr = f"{y_from}-({y_from - y_to})*clip((t-0.4)/1.8\\,0\\,1)"
        # 馬マーカー
        parts.append(
            f"drawtext=textfile='{wf('uma', '馬')}':fontfile='{fp}':"
            f"fontsize={sz}:fontcolor=0x{C_WHITE}:x={mx}-text_w/2:y='{yexpr}':"
            f"box=1:boxcolor=0x{col}@0.95:boxborderw=14:borderw=2:bordercolor=0x{C_DARK}")
        # 方向矢印（点滅）
        parts.append(
            f"drawtext=text='{arr}':fontfile='{fp}':fontsize={int(sz*1.1)}:"
            f"fontcolor=0x{col}:x={mx + (70 if port else 64)}:y='{yexpr}':"
            f"alpha='{PULSE}'")

    if scene == "ladder":
        ladder(None)
    elif scene == "ladder_up":
        ladder("up")
    elif scene == "ladder_down":
        ladder("down")

    # ----- 格付ポイント制の仕組み（points） -----
    # 着順=ポイント獲得 と 合計→昇級ライン到達 を①②で分け、
    # 昇級ラインは着順チップの下ではなく「合計バーの右端のゴール」に置く。
    elif scene == "points":
        sz = 40 if port else 38
        # ① 着順に応じてポイント獲得
        h1_y = 280 if port else 175
        parts.append(_chip(wf("ph1", "① 着順に応じてポイント獲得"), cx, h1_y,
                           int(sz * 0.72), C_WHITE, C_DARK, border=12, fp=fp))
        row_y = h1_y + (78 if port else 70)
        step  = 150 if port else 220
        # 1着ほど高ポイント＝大きく金、下位ほど小さく
        for k in range(5):
            cxk = cx + (k - 2) * step
            parts.append(_chip(wf(f"p{k}", f"{k+1}着"), cxk, row_y,
                               sz + (10 - 2 * k), C_DARK,
                               C_GOLD if k == 0 else C_BLUE, border=12, fp=fp))
        # ② 合計が昇級ラインを超えたら昇級
        h2_y = row_y + (150 if port else 130)
        parts.append(_chip(wf("ph2", "② 合計ポイントが昇級ラインを超えたら昇級"), cx,
                           h2_y, int(sz * 0.66), C_WHITE, C_DARK, border=12, fp=fp))
        # 合計ポイントの進捗バー（左から灯る）＋右端に昇級ラインのゴール
        track_y = h2_y + (95 if port else 82)
        n = 8
        dstep = 74 if port else 104
        base_x = cx - n * dstep // 2          # ゴール分を右に空ける
        parts.append(_text(wf("sumlbl", "合計pt"), base_x - (120 if port else 150),
                           track_y - int(sz * 0.2), int(sz * 0.6), C_WHITE, fp))
        for k in range(n):
            x = base_x + k * dstep
            enable = f"gte(t\\,{0.3 + k*0.28})"
            parts.append(
                f"drawtext=text='●':fontfile='{fp}':fontsize={int(sz*1.0)}:"
                f"fontcolor=0x355135:x={x}-text_w/2:y={track_y}")
            parts.append(
                f"drawtext=text='●':fontfile='{fp}':fontsize={int(sz*1.0)}:"
                f"fontcolor=0x{C_GOLD}:x={x}-text_w/2:y={track_y}:enable='{enable}'")
        # 右端ゴール＝昇級ライン（縦バー＋ラベル、点滅）
        goal_x = base_x + n * dstep
        parts.append(
            f"drawtext=text='┃':fontfile='{fp}':fontsize={int(sz*1.7)}:"
            f"fontcolor=0x{C_GOLD}:x={goal_x}-text_w/2:y={track_y - int(sz*0.35)}:"
            f"alpha='{PULSE}'")
        parts.append(_chip(wf("line", "昇級\nライン"), goal_x + (44 if port else 52),
                           track_y - int(sz * 0.35), int(sz * 0.6),
                           C_GOLD, C_DARK, border=8, alpha=PULSE, ls=4, fp=fp))

    # ----- 編成替えカレンダー（calendar） -----
    elif scene == "calendar":
        sz = 50 if port else 46
        parts.append(_chip(wf("ctitle", "編成替えは 年2回"), cx,
                           250 if port else 170, int(sz*0.84),
                           C_WHITE, C_DARK, border=14, fp=fp))
        if port:
            parts.append(_chip(wf("h1", "上半期\n1〜6月"), cx, 430, sz,
                               C_DARK, C_BLUE, border=22, fp=fp))
            parts.append(_chip(wf("h2", "下半期\n7〜12月"), cx, 700, sz,
                               C_DARK, C_GREEN, border=22, fp=fp))
            hy = 980
        else:
            parts.append(_chip(wf("h1", "上半期 1〜6月"), cx - 380, 380, sz,
                               C_DARK, C_BLUE, border=22, fp=fp))
            parts.append(_chip(wf("h2", "下半期 7〜12月"), cx + 380, 380, sz,
                               C_DARK, C_GREEN, border=22, fp=fp))
            hy = 600
        parts.append(_chip(wf("ny", "年末年始 → 馬齢降級でメンバー一変"), cx, hy,
                           int(sz*0.74), C_WHITE, C_RED, border=18,
                           alpha=PULSE, fp=fp))

    # ----- 狙い目（target） -----
    elif scene == "target":
        sz = 52 if port else 48
        parts.append(_chip(wf("ttitle", "◎ 狙い目"), cx, 300 if port else 200,
                           int(sz*1.2), C_DARK, C_GOLD, border=22,
                           alpha=PULSE, fp=fp))
        y1 = 500 if port else 400
        y2 = y1 + (150 if port else 130)
        y3 = y2 + (150 if port else 130)
        parts.append(_chip(wf("t1", "▼ 降級してきた実力馬"), cx, y1, int(sz*0.82),
                           C_WHITE, C_GREEN, border=18, fp=fp))
        parts.append(_chip(wf("t2", "編成替え直後＝力量差リセット"), cx, y2,
                           int(sz*0.74), C_WHITE, C_BLUE, border=18, fp=fp))
        parts.append(_chip(wf("t3", "△ 昇級初戦は慎重に"), cx, y3, int(sz*0.74),
                           C_WHITE, C_RED, border=18, fp=fp))

    # ----- 昇級初戦の注意（caution） -----
    elif scene == "caution":
        sz = 54 if port else 50
        parts.append(_chip(wf("cau", "⚠ 昇級初戦は慎重に"), cx, 360 if port else 260,
                           int(sz*1.0), C_DARK, C_RED, border=22,
                           alpha=PULSE, fp=fp))
        parts.append(_chip(wf("cau2", "相手強化で\n人気でも取りこぼし"), cx,
                           560 if port else 440, int(sz*0.78),
                           C_WHITE, C_DARK, border=18, fp=fp))

    # ----- イントロ（intro：4競馬場） -----
    elif scene == "intro":
        sz = 56 if port else 52
        parts.append(_chip(wf("ititle", "南関東 4競馬場"), cx, 280 if port else 190,
                           int(sz*0.9), C_DARK, C_GOLD, border=18, fp=fp))
        tracks = ["大井", "川崎", "船橋", "浦和"]
        cols = [C_GOLD, C_BLUE, C_GREEN, C_GOLD]
        if port:
            pos = [(cx-190, 520), (cx+190, 520), (cx-190, 740), (cx+190, 740)]
        else:
            pos = [(cx-540, 470), (cx-180, 470), (cx+180, 470), (cx+540, 470)]
        for (px, py), t, c in zip(pos, tracks, cols):
            parts.append(_chip(wf(f"tr{t}", t), px, py, sz, C_DARK, c,
                               border=24, fp=fp))

    return ("," + ",".join(parts)) if parts else ""


def make_clip(idx, text, audio_path, duration, font_path, tmp_dir, geom,
              is_ending=False, scene="plain", cta=False) -> str:
    """背景(グラデ)+図解シーン+字幕+音声を持つ1クリップを生成。
    cta=True のときは本編誘導バッジ／誘導エンディングを表示（ショート用）。"""
    W, H = geom["w"], geom["h"]
    clip_path  = f"{tmp_dir}/clip_{idx:04d}.mp4"
    label_file = f"{tmp_dir}/label.txt"
    text_file  = f"{tmp_dir}/text_{idx:04d}.txt"
    badge_file = f"{tmp_dir}/badge.txt"
    duration   = max(duration, 0.6)

    Path(label_file).write_text(SERIES_LABEL, encoding="utf-8")
    if is_ending:
        end_text = CTA_ENDING if cta else \
            "チャンネル登録お願いします\nまた次の競馬でお会いしましょう"
        Path(text_file).write_text(end_text, encoding="utf-8")
    else:
        Path(text_file).write_text(wrap_text(text, geom["sub_chars"]),
                                   encoding="utf-8")
    Path(badge_file).write_text(CTA_BADGE, encoding="utf-8")

    # d は gradients ソースの総尺。clip長より十分長く取らないと映像が途中で
    # 打ち切られA/Vがズレるため、duration+余裕を確保する。
    src = (f"gradients=s={W}x{H}:c0={GRAD_TOP}:c1={GRAD_BOTTOM}:"
           f"x0=0:y0=0:x1=0:y1={H}:type=linear:d={duration + 2:.2f}:r={FPS}")
    chain = "[0:v]vignette=PI/4.2,format=yuv420p"

    fp = font_path.replace("'", "\\'")
    lf = label_file.replace("'", "\\'")
    tf = text_file.replace("'", "\\'")
    bf = badge_file.replace("'", "\\'")

    # 図解シーン（字幕より下のレイヤー）
    if not is_ending:
        chain += build_scene(scene, geom, tmp_dir, idx, font_path)

    chain += (f",drawtext=textfile='{lf}':fontfile='{fp}':"
              f"fontsize={geom['label_size']}:fontcolor=0xFFD24A@0.96:"
              f"x=(w-text_w)/2:y={geom['label_y']}:"
              f"box=1:boxcolor=0x000000@0.55:boxborderw=16:"
              f"borderw=2:bordercolor=0x06251a")

    # 本編誘導バッジ（ショートの本編クリップに常時・右上）
    if cta and not is_ending:
        chain += (f",drawtext=textfile='{bf}':fontfile='{fp}':"
                  f"fontsize=34:fontcolor=0x000000:"
                  f"x=w-text_w-28:y={geom['label_y']+4}:"
                  f"box=1:boxcolor=0xFFD24A@0.92:boxborderw=12:"
                  f"borderw=1:bordercolor=0x06251a")

    if is_ending:
        chain += (f",drawtext=textfile='{tf}':fontfile='{fp}':"
                  f"fontsize={geom['end_size']}:fontcolor=0xFFEB3B:"
                  f"x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=18:"
                  f"box=1:boxcolor=0x000000@0.6:boxborderw=26")
    else:
        chain += (f",drawtext=textfile='{tf}':fontfile='{fp}':"
                  f"fontsize={geom['sub_size']}:fontcolor=0xFFFFFF:"
                  f"x=(w-text_w)/2:y={geom['sub_y']}:line_spacing=14:"
                  f"box=1:boxcolor=0x062b1c@0.82:boxborderw=28:"
                  f"borderw=2:bordercolor=0x000000")
    chain += "[vout]"

    # 音声は映像長(duration)に合わせて無音パディング（A/V長を厳密一致させる）
    chain += ";[1:a]aresample=44100,apad[aout]"

    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src]
    if audio_path:
        cmd += ["-i", audio_path]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-filter_complex", chain, "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-t", f"{duration}", clip_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [警告] クリップ{idx}生成失敗:\n{r.stderr[-600:]}", file=sys.stderr)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"color=c={GRAD_BOTTOM}:s={W}x{H}:r={FPS}",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-t", f"{duration}", clip_path],
            check=True, capture_output=True)
    return clip_path


def _concat(clips, out, tmp_dir, tag):
    lst = f"{tmp_dir}/concat_{tag}.txt"
    with open(lst, "w") as f:
        for p in clips:
            f.write(f"file '{p}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", out],
                   check=True, capture_output=True)


def build_video(script_path, orientation, out_path, font_path, bgm_path, engine,
                speed=1.0, cta=False):
    geom = geometry(orientation)
    Path("output").mkdir(exist_ok=True)
    tmp_dir = f"/tmp/nankan_{orientation}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)

    lines = load_narration(script_path)
    print(f"\n=== {orientation} ({geom['w']}x{geom['h']}) 生成開始 ===")
    print(f"  原稿: {script_path} ({len(lines)}行) / エンジン: {engine} / "
          f"速度: {speed}x / CTA: {cta}")

    narration_clips = []
    for i, line in enumerate(lines):
        audio, dur = synth_line(engine, line, tmp_dir, i)
        scene = pick_scene(line)
        narration_clips.append(
            make_clip(i, line, audio, dur + GAP, font_path, tmp_dir, geom,
                      scene=scene, cta=cta))
        print(f"  [{i+1}/{len(lines)}] {dur+GAP:.2f}s [{scene}] 「{line[:22]}」")

    # ナレーション本編を連結 → 必要なら倍速（映像setpts + 音声atempo）
    body = f"{tmp_dir}/body.mp4"
    _concat(narration_clips, body, tmp_dir, "body")
    if abs(speed - 1.0) > 1e-3:
        fast = f"{tmp_dir}/body_fast.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", body, "-filter_complex",
             f"[0:v]setpts=PTS/{speed}[v];[0:a]atempo={speed}[a]",
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
             "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", fast],
            check=True, capture_output=True)
        body = fast

    # エンディング（誘導カードは等速・読める尺）を後ろに連結
    ending = make_clip(len(lines), "", None,
                       ENDING_DUR_CTA if cta else ENDING_DUR,
                       font_path, tmp_dir, geom, is_ending=True, cta=cta)
    base = f"{tmp_dir}/base.mp4"
    _concat([body, ending], base, tmp_dir, "final")

    if bgm_path:
        subprocess.run(
            ["ffmpeg", "-y", "-i", base, "-stream_loop", "-1", "-i", bgm_path,
             "-filter_complex",
             f"[0:a][1:a]amix=inputs=2:duration=first:weights=1 {BGM_VOLUME}[aout]",
             "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-shortest", out_path],
            check=True, capture_output=True)
        print(f"  BGM: {bgm_path} (vol={BGM_VOLUME})")
    else:
        shutil.move(base, out_path)
        print("  BGM: なし（ナレーションのみ）")

    thumb = str(Path(out_path).with_suffix("")) + "_thumb.jpg"
    subprocess.run(["ffmpeg", "-y", "-ss", "1.5", "-i", out_path,
                    "-vframes", "1", "-q:v", "2", thumb], capture_output=True)

    dur = probe_duration(out_path)
    size_mb = Path(out_path).stat().st_size / (1024 * 1024)
    print(f"  完成: {out_path}  {dur:.1f}s / {size_mb:.1f}MB / サムネ: {thumb}")
    shutil.rmtree(tmp_dir, ignore_errors=True)


def preview_scenes(orientation, font_path, at_t=2.0):
    """各シーンを1フレームだけ書き出して見た目を確認する。"""
    geom = geometry(orientation)
    tmp_dir = f"/tmp/nankan_prev_{orientation}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)
    Path("output/preview").mkdir(parents=True, exist_ok=True)
    demo = {
        "intro": "大井、川崎、船橋、浦和の4つの競馬場",
        "ladder": "クラスはA1からC3までの8段階です",
        "ladder_up": "ポイントを貯めると上のクラスへ昇級します",
        "ladder_down": "成績が振るわないと下のクラスへ降級します",
        "points": "1着から5着までポイントが入り合計で決まります",
        "calendar": "編成替えは年2回、年末年始は馬齢降級",
        "target": "狙い目は降級馬と編成替え直後のリセット",
        "caution": "昇級初戦は人気でも取りこぼしに注意",
    }
    W, H = geom["w"], geom["h"]
    for i, (scene, line) in enumerate(demo.items()):
        Path(f"{tmp_dir}/label.txt").write_text(SERIES_LABEL, encoding="utf-8")
        Path(f"{tmp_dir}/text_{i:04d}.txt").write_text(
            wrap_text(line, geom["sub_chars"]), encoding="utf-8")
        fp = font_path.replace("'", "\\'")
        chain = "[0:v]vignette=PI/4.2,format=yuv420p"
        chain += build_scene(scene, geom, tmp_dir, i, font_path)
        chain += (f",drawtext=textfile='{tmp_dir}/label.txt':fontfile='{fp}':"
                  f"fontsize={geom['label_size']}:fontcolor=0x{C_GOLD}@0.96:"
                  f"x=(w-text_w)/2:y={geom['label_y']}:box=1:boxcolor=0x000000@0.55:"
                  f"boxborderw=16:borderw=2:bordercolor=0x{C_DARK}")
        chain += (f",drawtext=textfile='{tmp_dir}/text_{i:04d}.txt':fontfile='{fp}':"
                  f"fontsize={geom['sub_size']}:fontcolor=0x{C_WHITE}:"
                  f"x=(w-text_w)/2:y={geom['sub_y']}:line_spacing=14:"
                  f"box=1:boxcolor=0x062b1c@0.82:boxborderw=28:borderw=2:bordercolor=0x000000")
        chain += "[vout]"
        out = f"output/preview/{orientation}_{i}_{scene}.png"
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"gradients=s={W}x{H}:c0={GRAD_TOP}:c1={GRAD_BOTTOM}:x0=0:y0=0:x1=0:y1={H}",
             "-filter_complex", chain.replace("[0:v]", "[0:v]"),
             "-map", "[vout]", "-ss", f"{at_t}", "-frames:v", "1", out],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  [NG] {scene}:\n{r.stderr[-500:]}", file=sys.stderr)
        else:
            print(f"  [OK] {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script")
    ap.add_argument("--orientation", choices=["portrait", "landscape"])
    ap.add_argument("--out")
    ap.add_argument("--speed", type=float)
    ap.add_argument("--cta", action="store_true")
    ap.add_argument("--preview-scenes", action="store_true")
    args = ap.parse_args()

    font_path = find_font()
    if not font_path:
        print("[エラー] CJKフォントが見つかりません。", file=sys.stderr)
        sys.exit(1)

    if args.preview_scenes:
        preview_scenes(args.orientation or "portrait", font_path)
        return

    bgm_path = find_bgm()
    engine = choose_engine()

    # (script, orientation, out, speed, cta)
    if args.script and args.orientation and args.out:
        spd = args.speed if args.speed else 1.0
        jobs = [(args.script, args.orientation, args.out, spd, args.cta)]
    else:
        jobs = [
            # ショート: 2倍速 + 本編誘導CTA
            ("data/nankan_class_short.txt", "portrait",  "output/nankan_class_short.mp4", 2.0, True),
            # 本編(フル): 1.4倍速
            ("data/nankan_class_full.txt",  "landscape", "output/nankan_class_full.mp4", 1.4, False),
        ]
    for script, orientation, out, speed, cta in jobs:
        build_video(script, orientation, out, font_path, bgm_path, engine,
                    speed=speed, cta=cta)

    print("\n=== すべて完了 ===")


if __name__ == "__main__":
    main()
