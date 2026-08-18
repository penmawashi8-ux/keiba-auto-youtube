#!/usr/bin/env python3
"""競馬25世代解説シリーズの動画ジェネレーター（ffmpegのみ・Pillow/numpy不使用）

1本のナレーション原稿(.txt)から、横型フル尺(1920x1080)と
縦型ショート(1080x1920)の両方を生成できる自己完結スクリプト。

原稿フォーマット:
    #  から始まる行           … コメント（無視）
    @chapter 見出し           … 章タイトルカード
    @card タイトル|行|行      … 箇条書きカード
    @result レース名|行|行    … 着順ボード（1着=金 2着=銀 3着=銅 回避=赤 他=紺）
    @plain                    … 図なし（字幕のみ）
    それ以外の行              … ナレーション1行 ＝ 字幕1枚
    行末に「||読み」を付けるとTTSにはそちらを渡す（字幕は元テキストのまま）

@ 行で指定した図は、次の @ 行が来るまでのナレーション行に適用される。

設計:
  - 「1行 = 1字幕セグメント」。行ごとに音声を合成して尺を測り、その長さで
    クリップを作るため、どのTTSエンジンでも字幕と音声が必ず同期する。
  - TTS は edge-tts（本番標準）→ Google翻訳TTS → pyopenjtalk → 無音 の順で
    フォールバック。ネットワーク制限のある環境でも必ず動画が完成する。

プロジェクトルール（CLAUDE.md）順守:
  - 画面はすべて ffmpeg（lavfi gradients + drawtext box=1）で生成。
    Pillow / numpy画像処理 / drawbox は一切使わない。
  - 日本語テキストは textfile= でファイル経由（エスケープ回避）。
  - サムネイルは動画ネイティブ解像度のままフレーム抽出（-s リサイズ禁止）。

使い方:
  python scripts/create_gen25_video.py                      # フル尺+ショート5本
  python scripts/create_gen25_video.py --only full          # 横型フル尺だけ
  python scripts/create_gen25_video.py --only shorts        # 縦型ショートだけ
  python scripts/create_gen25_video.py \
      --script data/gen25_short_1.txt --orientation portrait \
      --out output/gen25_short_1.mp4 --cta
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from reading_utils import apply_readings
except Exception:                                    # 単体実行時の保険
    def apply_readings(text: str) -> str:
        return text

VOICE  = "ja-JP-KeitaNeural"     # 解説シリーズ（ニュース系と同じ男性ナレーター）
RATE   = "+10%"                  # 早口寄り（テンポ重視）
VOLUME = "+0%"

FPS        = 30
BGM_VOLUME = 0.18
GAP        = 0.18                # 行間の無音（テンポ優先で短め）
ENDING_DUR = 4.0

SERIES_LABEL = "競馬25世代 解説"
CTA_BADGE    = "▶本編は概要欄"
CTA_ENDING   = "つづきは本編で！\n▶ 概要欄・コメント欄から"
END_TEXT     = "チャンネル登録・高評価\nよろしくお願いします"

# 背景はターフ色のフラットな単色＋弱いビネット。
# 2色グラデーションは安っぽく見えるので使わない。
BG_COLOR = "0x16463a"

# TTSエンジンごとの標準再生速度（かなり早口寄りに設定）。
# edge-tts は rate=+10% でおよそ8字/秒、Google音声は素で約3.9字/秒。
ENGINE_SPEED = {"edge": 1.12, "google": 2.2, "oj": 1.4}

C_GOLD   = "FFD24A"    # 1着
C_SILVER = "C7CDD4"    # 2着
C_BRONZE = "D08B4A"    # 3着
C_RED    = "E06C6C"    # 回避・凡走
C_NAVY   = "13314a"    # その他の着順
C_WHITE  = "FFFFFF"
C_DARK   = "05221a"


# ---------------------------------------------------------------------------
# 環境ヘルパー
# ---------------------------------------------------------------------------
def find_font() -> str | None:
    for p in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    ]:
        if Path(p).exists():
            return p
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    return hits[0] if hits else None


def find_bgm() -> str | None:
    for c in ["assets/bgm/horse_drama_bgm.mp3", "assets/bgm/bgm_1.mp3"]:
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


# ---------------------------------------------------------------------------
# 原稿パーサ
# ---------------------------------------------------------------------------
def parse_script(script_path: str) -> list[dict]:
    """原稿を [{text, speech, scene}] のセグメント列に変換する。"""
    segs: list[dict] = []
    scene: dict = {"kind": "plain"}
    for raw in Path(script_path).read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("@"):
            head, _, body = s[1:].partition(" ")
            parts = [p.strip() for p in body.split("|") if p.strip()]
            if head == "chapter":
                scene = {"kind": "chapter", "title": parts[0] if parts else ""}
            elif head == "card":
                scene = {"kind": "card", "title": parts[0] if parts else "",
                         "rows": parts[1:]}
            elif head == "result":
                scene = {"kind": "result", "title": parts[0] if parts else "",
                         "rows": parts[1:]}
            else:
                scene = {"kind": "plain"}
            continue
        text, sep, reading = s.partition("||")
        segs.append({"text": text.strip(),
                     "speech": (reading.strip() if sep else text.strip()),
                     "scene": scene})
    return segs


# ---------------------------------------------------------------------------
# TTS（edge-tts → Google翻訳TTS → pyopenjtalk → 無音）
# ---------------------------------------------------------------------------
def _trust_proxy_ca() -> None:
    """edge-tts は certifi 固定のSSLを使うため、実行環境のプロキシCAを信頼させる。"""
    try:
        import certifi
        import edge_tts.communicate as _ec
    except Exception:
        return
    ctx = ssl.create_default_context(cafile=certifi.where())
    for ca in (os.environ.get("SSL_CERT_FILE"),
               os.environ.get("REQUESTS_CA_BUNDLE"),
               "/root/.ccr/ca-bundle.crt"):
        if ca and Path(ca).exists():
            try:
                ctx.load_verify_locations(cafile=ca)
            except Exception:
                pass
    _ec._SSL_CTX = ctx


_G_CTX = ssl.create_default_context()
for _ca in ("/root/.ccr/ca-bundle.crt", os.environ.get("SSL_CERT_FILE"),
            os.environ.get("REQUESTS_CA_BUNDLE")):
    if _ca and Path(_ca).exists():
        try:
            _G_CTX.load_verify_locations(cafile=_ca)
        except Exception:
            pass


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


def _split_for_tts(text: str, maxlen: int = 120) -> list[str]:
    """Google翻訳TTSの長さ制限対策。句読点で maxlen 以下に分割する。"""
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
    data = b""
    for chunk in _split_for_tts(text):
        url = ("https://translate.googleapis.com/translate_tts"
               f"?ie=UTF-8&client=gtx&tl=ja&q={urllib.parse.quote(chunk)}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20, context=_G_CTX) as r:
                    data += r.read()
                break
            except Exception:
                time.sleep(1.2 * (attempt + 1))
        else:
            return False
    if len(data) < 400:
        return False
    Path(out_mp3).write_bytes(data)
    return True


def oj_synth(text: str, out_wav: str) -> bool:
    """pyopenjtalk によるオフライン合成（最終フォールバック）。"""
    try:
        import wave
        import numpy as np          # pyopenjtalkの内部依存。PCM変換にのみ使用
        import pyopenjtalk
        x, sr = pyopenjtalk.tts(text)
        pcm = np.clip(x, -32768, 32767).astype("<i2").tobytes()
        with wave.open(out_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sr))
            w.writeframes(pcm)
        return Path(out_wav).stat().st_size > 500
    except Exception:
        return False


def choose_engine() -> str:
    _trust_proxy_ca()
    if edge_synth("テスト", "/tmp/_gen25_probe.mp3"):
        return "edge"
    if google_synth("テスト", "/tmp/_gen25_probe_g.mp3"):
        print("  [情報] edge-tts不可 → Google音声にフォールバック", file=sys.stderr)
        return "google"
    print("  [情報] ネットTTS不可 → pyopenjtalk(オフライン)にフォールバック",
          file=sys.stderr)
    return "oj"


def synth_line(engine: str, text: str, tmp_dir: str, idx: int) -> tuple[str, float]:
    text = apply_readings(text)
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
    out = f"{tmp_dir}/a_{idx:04d}_sil.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", "1.6", out],
                   capture_output=True)
    return out, 1.6


# ---------------------------------------------------------------------------
# レイアウト
# ---------------------------------------------------------------------------
def geometry(orientation: str) -> dict:
    if orientation == "portrait":
        return {"orient": "portrait", "w": 1080, "h": 1920, "cx": 540,
                "label_size": 44, "label_y": 120,
                "board_title_y": 520, "board_row_y": 680, "board_row_max": 60,
                "board_avail": 940,
                "chapter_size": 76, "chapter_y": 800,
                "sub_size": 54, "sub_chars": 16, "sub_y": "h-text_h-440",
                "end_size": 58}
    return {"orient": "landscape", "w": 1920, "h": 1080, "cx": 960,
            "label_size": 40, "label_y": 48,
            "board_title_y": 200, "board_row_y": 330, "board_row_max": 54,
            "board_avail": 1500,
            "chapter_size": 82, "chapter_y": 430,
            "sub_size": 52, "sub_chars": 32, "sub_y": "h-text_h-72",
            "end_size": 66}


def wrap_text(text: str, max_chars: int) -> str:
    out = []
    for para in text.split("\n"):
        while len(para) > max_chars:
            out.append(para[:max_chars])
            para = para[max_chars:]
        if para:
            out.append(para)
    return "\n".join(out)


def _row_colors(row: str) -> tuple[str, str]:
    """行頭の着順表記から (文字色, 背景色) を決める。"""
    if row.startswith("1着"):
        return C_DARK, C_GOLD
    if row.startswith("2着"):
        return C_DARK, C_SILVER
    if row.startswith("3着"):
        return C_DARK, C_BRONZE
    if row.startswith(("回避", "中止", "除外")):
        return C_WHITE, C_RED
    return C_WHITE, C_NAVY


# ---------------------------------------------------------------------------
# 図解（drawtext のみ・先頭 "," 付きのフィルタ列を返す）
# ---------------------------------------------------------------------------
def build_scene(scene: dict, geom: dict, tmp_dir: str, idx: int, font: str) -> str:
    kind = scene.get("kind", "plain")
    if kind == "plain":
        return ""
    fp = font.replace("'", "\\'")
    cx = geom["cx"]
    parts: list[str] = []

    def wf(key: str, text: str) -> str:
        p = f"{tmp_dir}/s_{idx:04d}_{key}.txt"
        Path(p).write_text(text, encoding="utf-8")
        return p.replace("'", "\\'")

    def chip(path, y, size, fg, bg, border=18, enable=None, ls=8):
        s = (f"drawtext=textfile='{path}':fontfile='{fp}':fontsize={size}:"
             f"fontcolor=0x{fg}:x={cx}-text_w/2:y={y}:line_spacing={ls}:"
             f"box=1:boxcolor=0x{bg}@0.94:boxborderw={border}:"
             f"borderw=2:bordercolor=0x000000")
        if enable is not None:
            s += f":enable='{enable}'"
        parts.append(s)

    if kind == "chapter":
        title = scene.get("title", "")
        chip(wf("ch", wrap_text(title, 11 if geom["orient"] == "portrait" else 18)),
             geom["chapter_y"], geom["chapter_size"], C_GOLD, "000000",
             border=34, ls=20)
        return ("," + ",".join(parts)) if parts else ""

    title = scene.get("title", "")
    rows = scene.get("rows", [])

    # 行数・文字数から自動でフォントサイズを決める（はみ出し防止）
    longest = max([len(r) for r in rows] + [1])
    size = min(geom["board_row_max"], int(geom["board_avail"] / max(longest, 1)))
    size = max(size, 26)
    gap = int(size * 2.0)

    t_chars = 18 if geom["orient"] == "portrait" else 30
    t_size = min(int(size * 0.86), 46)
    chip(wf("t", wrap_text(title, t_chars)), geom["board_title_y"], t_size,
         C_WHITE, "000000", border=20, ls=10)

    for i, row in enumerate(rows):
        fg, bg = _row_colors(row) if kind == "result" else (C_WHITE, C_NAVY)
        chip(wf(f"r{i}", row), geom["board_row_y"] + i * gap, size, fg, bg,
             border=16, enable=f"gte(t\\,{0.25 + i * 0.30:.2f})")

    return ("," + ",".join(parts)) if parts else ""


# ---------------------------------------------------------------------------
# クリップ生成
# ---------------------------------------------------------------------------
def make_clip(idx, text, scene, audio_path, duration, font, tmp_dir, geom,
              is_ending=False, cta=False) -> str:
    W, H = geom["w"], geom["h"]
    clip_path  = f"{tmp_dir}/clip_{idx:04d}.mp4"
    # 並列生成するのでテキストファイルはクリップごとに分ける（書き込み競合の回避）
    label_file = f"{tmp_dir}/label_{idx:04d}.txt"
    text_file  = f"{tmp_dir}/text_{idx:04d}.txt"
    badge_file = f"{tmp_dir}/badge_{idx:04d}.txt"
    duration   = max(duration, 0.6)

    Path(label_file).write_text(SERIES_LABEL, encoding="utf-8")
    Path(badge_file).write_text(CTA_BADGE, encoding="utf-8")
    if is_ending:
        Path(text_file).write_text(CTA_ENDING if cta else END_TEXT,
                                   encoding="utf-8")
    else:
        Path(text_file).write_text(wrap_text(text, geom["sub_chars"]),
                                   encoding="utf-8")

    src = f"color=c={BG_COLOR}:s={W}x{H}:r={FPS}:d={duration + 2:.2f}"
    chain = "[0:v]vignette=PI/8,format=yuv420p"

    fp = font.replace("'", "\\'")
    lf = label_file.replace("'", "\\'")
    tf = text_file.replace("'", "\\'")
    bf = badge_file.replace("'", "\\'")

    if not is_ending:
        chain += build_scene(scene, geom, tmp_dir, idx, font)

    chain += (f",drawtext=textfile='{lf}':fontfile='{fp}':"
              f"fontsize={geom['label_size']}:fontcolor=0x{C_GOLD}@0.96:"
              f"x=(w-text_w)/2:y={geom['label_y']}:"
              f"box=1:boxcolor=0x000000@0.55:boxborderw=16:"
              f"borderw=2:bordercolor=0x000000")

    if cta and not is_ending:
        chain += (f",drawtext=textfile='{bf}':fontfile='{fp}':fontsize=32:"
                  f"fontcolor=0x000000:x=w-text_w-26:y={geom['label_y'] + 4}:"
                  f"box=1:boxcolor=0x{C_GOLD}@0.92:boxborderw=12")

    if is_ending:
        chain += (f",drawtext=textfile='{tf}':fontfile='{fp}':"
                  f"fontsize={geom['end_size']}:fontcolor=0x{C_GOLD}:"
                  f"x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=20:"
                  f"box=1:boxcolor=0x000000@0.6:boxborderw=28")
    else:
        chain += (f",drawtext=textfile='{tf}':fontfile='{fp}':"
                  f"fontsize={geom['sub_size']}:fontcolor=0x{C_WHITE}:"
                  f"x=(w-text_w)/2:y={geom['sub_y']}:line_spacing=14:"
                  f"box=1:boxcolor=0x041b12@0.85:boxborderw=26:"
                  f"borderw=2:bordercolor=0x000000")
    chain += "[vout];[1:a]aresample=44100,apad[aout]"

    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src]
    if audio_path:
        cmd += ["-i", audio_path]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    # 中間クリップは最後の連結時に必ず再エンコードされるので、ここでは
    # ultrafast + 低CRF（＝ほぼ劣化なし）で速度を優先する。
    cmd += ["-filter_complex", chain, "-map", "[vout]", "-map", "[aout]",
            "-threads", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "ultrafast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-t", f"{duration}", clip_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [警告] クリップ{idx}生成失敗:\n{r.stderr[-700:]}", file=sys.stderr)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"color=c={BG_COLOR}:s={W}x{H}:r={FPS}",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-t", f"{duration}", clip_path], check=True, capture_output=True)
    return clip_path


def _atempo_chain(speed: float) -> str:
    """atempo は1回あたり0.5〜2.0倍までなので、必要なら複数段に分ける。"""
    parts, remain = [], speed
    while remain > 2.0:
        parts.append("atempo=2.0")
        remain /= 2.0
    while remain < 0.5:
        parts.append("atempo=0.5")
        remain /= 0.5
    parts.append(f"atempo={remain:.6f}")
    return ",".join(parts)


def _concat(clips: list[str], out: str, tmp_dir: str, tag: str) -> None:
    lst = f"{tmp_dir}/concat_{tag}.txt"
    with open(lst, "w") as f:
        for p in clips:
            f.write(f"file '{p}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", out],
                   check=True, capture_output=True)


def build_video(script_path, orientation, out_path, font, bgm, engine,
                speed=1.0, cta=False) -> float:
    geom = geometry(orientation)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = f"/tmp/gen25_{Path(out_path).stem}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)

    segs = parse_script(script_path)
    print(f"\n=== {Path(out_path).name} / {orientation} "
          f"({geom['w']}x{geom['h']}) ===")
    print(f"  原稿: {script_path} ({len(segs)}行) / エンジン: {engine} / "
          f"速度: {speed}x / CTA: {cta}")

    # 1) 音声を先にまとめて合成（TTSは1行あたり1秒未満）
    audios = []
    for i, seg in enumerate(segs):
        audio, dur = synth_line(engine, seg["speech"], tmp_dir, i)
        audios.append((audio, dur))
        print(f"  [音声 {i+1}/{len(segs)}] {dur:5.2f}s "
              f"[{seg['scene']['kind']:7}] 「{seg['text'][:24]}」")

    # 2) 映像クリップは重いのでコア数に応じて並列生成する
    workers = max(1, min(4, (os.cpu_count() or 2)))
    print(f"  クリップ生成: {len(segs)}本を{workers}並列で処理します")
    clips: list[str] = [""] * len(segs)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(make_clip, i, seg["text"], seg["scene"], audios[i][0],
                      audios[i][1] + GAP, font, tmp_dir, geom, cta=cta): i
            for i, seg in enumerate(segs)
        }
        for n, fut in enumerate(as_completed(futures), 1):
            i = futures[fut]
            clips[i] = fut.result()
            if n % 10 == 0 or n == len(segs):
                print(f"  クリップ {n}/{len(segs)} 完了")

    # YouTube概要欄に貼るチャプター一覧（@chapter の位置＝章の開始時刻）
    chapters, t = [], 0.0
    for seg, (_, dur) in zip(segs, audios):
        if seg["scene"]["kind"] == "chapter" and (
                not chapters or chapters[-1][1] != seg["scene"]["title"]):
            chapters.append((t / speed, seg["scene"]["title"]))
        t += dur + GAP
    ch_path = str(Path(out_path).with_suffix("")) + "_chapters.txt"
    Path(ch_path).write_text(
        "\n".join(f"{int(s // 60):d}:{int(s % 60):02d} {title}"
                  for s, title in chapters) + "\n", encoding="utf-8")

    body = f"{tmp_dir}/body.mp4"
    _concat(clips, body, tmp_dir, "body")
    if abs(speed - 1.0) > 1e-3:
        fast = f"{tmp_dir}/body_fast.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", body, "-filter_complex",
             f"[0:v]setpts=PTS/{speed}[v];[0:a]{_atempo_chain(speed)}[a]",
             "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-preset", "fast", "-c:a", "aac",
             "-b:a", "192k", "-ar", "44100", "-ac", "2", fast],
            check=True, capture_output=True)
        body = fast

    ending = make_clip(len(segs), "", {"kind": "plain"}, None, ENDING_DUR,
                       font, tmp_dir, geom, is_ending=True, cta=cta)
    base = f"{tmp_dir}/base.mp4"
    _concat([body, ending], base, tmp_dir, "final")

    if bgm:
        subprocess.run(
            ["ffmpeg", "-y", "-i", base, "-stream_loop", "-1", "-i", bgm,
             "-filter_complex",
             f"[0:a][1:a]amix=inputs=2:duration=first:weights=1 {BGM_VOLUME}[aout]",
             "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
             "-b:a", "192k", "-shortest", out_path],
            check=True, capture_output=True)
    else:
        shutil.move(base, out_path)

    thumb = str(Path(out_path).with_suffix("")) + "_thumb.jpg"
    subprocess.run(["ffmpeg", "-y", "-ss", "1.2", "-i", out_path,
                    "-vframes", "1", "-q:v", "2", thumb], capture_output=True)

    dur = probe_duration(out_path)
    mb = Path(out_path).stat().st_size / (1024 * 1024)
    print(f"  完成: {out_path}  {dur:.1f}s ({int(dur // 60)}分{dur % 60:02.0f}秒)"
          f" / {mb:.1f}MB / サムネ: {thumb}")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return dur


SHORTS = [(f"data/gen25_short_{i}.txt", f"output/gen25_short_{i}.mp4")
          for i in range(1, 6)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script")
    ap.add_argument("--orientation", choices=["portrait", "landscape"])
    ap.add_argument("--out")
    ap.add_argument("--speed", type=float,
                    help="再生速度。未指定ならTTSエンジンに応じて自動決定")
    ap.add_argument("--cta", action="store_true")
    ap.add_argument("--only", choices=["full", "shorts", "all"], default="all")
    ap.add_argument("--no-bgm", action="store_true")
    args = ap.parse_args()

    font = find_font()
    if not font:
        print("[エラー] CJKフォントが見つかりません。", file=sys.stderr)
        sys.exit(1)
    bgm = None if args.no_bgm else find_bgm()
    print(f"フォント: {font}")
    print(f"BGM: {bgm or 'なし（ナレーションのみ）'}")
    engine = choose_engine()
    speed = args.speed if args.speed else ENGINE_SPEED.get(engine, 1.0)
    print(f"TTS: {engine} / 再生速度: {speed}x")

    if args.script and args.orientation and args.out:
        jobs = [(args.script, args.orientation, args.out, args.cta)]
    else:
        jobs = []
        if args.only in ("full", "all"):
            jobs.append(("data/gen25_full.txt", "landscape",
                         "output/gen25_full.mp4", False))
        if args.only in ("shorts", "all"):
            jobs += [(s, "portrait", o, True) for s, o in SHORTS]

    results = []
    for script, orientation, out, cta in jobs:
        results.append((out, build_video(script, orientation, out, font, bgm,
                                         engine, speed=speed, cta=cta)))

    print("\n=== 完了 ===")
    for out, dur in results:
        flag = ""
        if "short" in out and dur > 180:
            flag = "  ← Shorts上限(3分)超え！分割し直してください"
        print(f"  {out}: {int(dur // 60)}分{dur % 60:02.0f}秒{flag}")


if __name__ == "__main__":
    main()
