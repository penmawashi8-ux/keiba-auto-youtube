#!/usr/bin/env python3
"""競馬25世代解説シリーズの動画ジェネレーター（ffmpegのみ・Pillow/numpy不使用）

1本のナレーション原稿(.txt)から、横型フル尺(1920x1080)と
縦型ショート(1080x1920)の両方を生成できる自己完結スクリプト。

原稿フォーマット:
    #  から始まる行           … コメント（無視）
    @chapter 見出し           … 章タイトルカード
    @card タイトル|行|行      … 箇条書きカード
    @result 見出し|行|行      … 着順ボード（着順に応じて文字色が変わる）
    @plain                    … 図なし（字幕のみ）
    それ以外の行              … ナレーション1行 ＝ 字幕1枚
    行末に「||読み」を付けるとTTSにはそちらを渡す（字幕は元テキストのまま）

@ 行で指定した図は、次の @ 行が来るまでのナレーション行に適用される。

設計:
  - 「1行 = 1字幕セグメント」。行ごとに音声を合成して尺を測るため、どのTTS
    エンジンでも字幕と音声が必ず同期する。
  - 映像は「1シーン = 1クリップ」。同じ図解を使う連続行はまとめて描き、字幕は
    drawtext の enable で切り替える。行ごとにクリップを切ると連結の境目で
    画面が描き直されてチカチカするため。
  - TTS は edge-tts（本番標準）→ Google翻訳TTS → pyopenjtalk → 無音 の順で
    フォールバック。ネットワーク制限のある環境でも必ず動画が完成する。

プロジェクトルール（CLAUDE.md）順守:
  - 画面はすべて ffmpeg（lavfi color + vignette + drawtext）で生成。
    Pillow / numpy画像処理 / drawbox は一切使わない。
  - 日本語テキストは textfile= でファイル経由（エスケープ回避）。
  - サムネイルは動画ネイティブ解像度のままフレーム抽出（-s リサイズ禁止）。

使い方:
  python scripts/create_gen25_video.py                      # フル尺+ショート5本
  python scripts/create_gen25_video.py --only full          # 横型フル尺だけ
  python scripts/create_gen25_video.py --only shorts        # 縦型ショートだけ
  python scripts/create_gen25_video.py --no-sfx             # 効果音なし
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

try:
    from make_sfx import ensure_sfx                  # 効果音は ffmpeg で合成する
except Exception:
    def ensure_sfx() -> dict[str, str]:
        return {}

VOICE  = "ja-JP-KeitaNeural"     # 解説シリーズ（ニュース系と同じ男性ナレーター）
RATE   = "+10%"                  # 早口寄り（テンポ重視）
VOLUME = "+0%"

FPS        = 30
BGM_VOLUME = 0.18
GAP        = 0.18                # 行間の無音（テンポ優先で短め）
ENDING_DUR = 4.0

# 横型フル尺は腰を据えて見る動画なので、ショートよりゆっくり喋らせる。
ORIENT_SPEED = {"landscape": 0.86, "portrait": 1.0}

# 図解の行が出るタイミング（完成した動画での秒数）。ここに効果音を当てる。
ROW_LEAD = 0.35      # シーンの頭から1行目まで
ROW_STEP = 0.45      # 2行目以降の間隔
SFX_VOL = {"impact": 0.8, "tick": 0.5, "whoosh": 0.45}
SFX_DECAY = 0.86     # 2行目以降、1行ごとに音量をこの比率で下げる

SERIES_LABEL = "競馬25世代 解説"
CTA_BADGE    = "▶本編は概要欄"
CTA_ENDING   = "つづきは本編で！\n▶ 概要欄・コメント欄から"
END_TEXT     = "チャンネル登録・高評価\nよろしくお願いします"

# 背景はターフ色のフラットな単色＋弱いビネット。
# 2色グラデーションは安っぽく見えるので使わない。
# vignette は dither=0 が必須。既定の dither=1 だとフレームごとに乱数ノイズが
# 乗り、単色背景ではそれが「チカチカ」として見えてしまう。
BG_COLOR = "0x16463a"
BG_FILTER = "vignette=PI/8:dither=0"

# TTSエンジンごとの標準再生速度（かなり早口寄りに設定）。
# edge-tts は rate=+10% でおよそ8字/秒、Google音声は素で約3.9字/秒。
ENGINE_SPEED = {"edge": 1.12, "google": 2.2, "oj": 1.4}

# 着順の違いは「文字色」だけで表す（背景の帯は敷かない）。
C_GOLD   = "FFD24A"    # 1着
C_SILVER = "D8DEE5"    # 2着
C_BRONZE = "E09A56"    # 3着
C_RED    = "F08A8A"    # 回避・中止・除外
C_WHITE  = "FFFFFF"

# 背景の帯を敷くのは字幕だけ。図解・見出し・ラベルは地の背景に直接置き、
# 太めの黒フチと影だけで読ませる（背景色を重ねると画面がうるさくなるため）。
TEXT_EDGE = ("borderw=6:bordercolor=0x04180f@0.92:"
             "shadowcolor=0x000000@0.55:shadowx=3:shadowy=4")


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


def _row_color(row: str) -> str:
    """行頭の着順表記から文字色を決める（背景は全行共通の暗色）。"""
    if row.startswith("1着"):
        return C_GOLD
    if row.startswith("2着"):
        return C_SILVER
    if row.startswith("3着"):
        return C_BRONZE
    if row.startswith(("回避", "中止", "除外")):
        return C_RED
    return C_WHITE


# ---------------------------------------------------------------------------
# 図解（drawtext のみ・先頭 "," 付きのフィルタ列を返す）
# ---------------------------------------------------------------------------
def build_scene(scene: dict, geom: dict, tmp_dir: str, idx: int, font: str,
                speed: float = 1.0) -> str:
    """図解を1枚だけ描く。シーンが続くあいだ描き直さないので画面は静止する。"""
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

    def chip(path, y, size, fg, enable=None, ls=8):
        s = (f"drawtext=textfile='{path}':fontfile='{fp}':fontsize={size}:"
             f"fontcolor=0x{fg}:x={cx}-text_w/2:y={y}:line_spacing={ls}:"
             f"{TEXT_EDGE}")
        if enable is not None:
            s += f":enable='{enable}'"
        parts.append(s)

    if kind == "chapter":
        title = scene.get("title", "")
        chip(wf("ch", wrap_text(title, 11 if geom["orient"] == "portrait" else 18)),
             geom["chapter_y"], geom["chapter_size"], C_GOLD, ls=20)
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
         C_WHITE, ls=10)

    for i, row in enumerate(rows):
        fg = _row_color(row) if kind == "result" else C_WHITE
        # 最後に倍速をかけるので、その分だけ引き伸ばして指定する。
        # こうすると完成した動画では ROW_LEAD + i*ROW_STEP 秒の等間隔で出る。
        chip(wf(f"r{i}", row), geom["board_row_y"] + i * gap, size, fg,
             enable=f"gte(t\\,{(ROW_LEAD + i * ROW_STEP) * speed:.3f})")

    return ("," + ",".join(parts)) if parts else ""


# ---------------------------------------------------------------------------
# クリップ生成
# ---------------------------------------------------------------------------
def make_scene_clip(gidx, lines, scene, audios, font, tmp_dir, geom,
                    is_ending=False, cta=False, speed=1.0) -> str:
    """同じ図解を使うナレーション行をまとめて1クリップに描く。

    以前は「1行＝1クリップ」だったため、字幕が変わるたびに背景と図解が
    描き直され、連結の切れ目で画面がチカチカしていた。ここでは背景と図解を
    通しで1回だけ描き、字幕だけを enable=between(t,…) で切り替えるので、
    シーンが続くあいだ画面は完全に静止する。
    """
    W, H = geom["w"], geom["h"]
    clip_path  = f"{tmp_dir}/clip_{gidx:04d}.mp4"
    # 並列生成するのでテキストファイルはクリップごとに分ける（書き込み競合の回避）
    label_file = f"{tmp_dir}/label_{gidx:04d}.txt"
    badge_file = f"{tmp_dir}/badge_{gidx:04d}.txt"
    Path(label_file).write_text(SERIES_LABEL, encoding="utf-8")
    Path(badge_file).write_text(CTA_BADGE, encoding="utf-8")

    # 字幕の表示区間 = その行の音声長 + 行間の無音
    spans: list[tuple[float, float]] = []
    t = 0.0
    for _, dur in audios:
        spans.append((t, t + dur + GAP))
        t += dur + GAP
    total = max(t, 0.6) if audios else ENDING_DUR

    fp = font.replace("'", "\\'")
    lf = label_file.replace("'", "\\'")
    bf = badge_file.replace("'", "\\'")

    src = f"color=c={BG_COLOR}:s={W}x{H}:r={FPS}:d={total + 1:.2f}"
    chain = f"[0:v]{BG_FILTER},format=yuv420p"

    if not is_ending:
        chain += build_scene(scene, geom, tmp_dir, gidx, font, speed)

    chain += (f",drawtext=textfile='{lf}':fontfile='{fp}':"
              f"fontsize={geom['label_size']}:fontcolor=0x{C_GOLD}@0.96:"
              f"x=(w-text_w)/2:y={geom['label_y']}:{TEXT_EDGE}")

    if cta and not is_ending:
        chain += (f",drawtext=textfile='{bf}':fontfile='{fp}':fontsize=32:"
                  f"fontcolor=0x{C_GOLD}:x=w-text_w-40:"
                  f"y={geom['label_y'] + 4}:{TEXT_EDGE}")

    if is_ending:
        p = f"{tmp_dir}/text_{gidx:04d}.txt"
        Path(p).write_text(CTA_ENDING if cta else END_TEXT, encoding="utf-8")
        chain += (f",drawtext=textfile='{p}':fontfile='{fp}':"
                  f"fontsize={geom['end_size']}:fontcolor=0x{C_GOLD}:"
                  f"x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=20:"
                  f"{TEXT_EDGE}")
    else:
        for i, text in enumerate(lines):
            p = f"{tmp_dir}/text_{gidx:04d}_{i:02d}.txt"
            Path(p).write_text(wrap_text(text, geom["sub_chars"]),
                               encoding="utf-8")
            s, e = spans[i]
            # 最終行は端数で字幕が消えないよう gte で開きっぱなしにする
            cond = (f"gte(t\\,{s:.3f})" if i == len(lines) - 1
                    else f"between(t\\,{s:.3f}\\,{e:.3f})")
            chain += (f",drawtext=textfile='{p}':fontfile='{fp}':"
                      f"fontsize={geom['sub_size']}:fontcolor=0x{C_WHITE}:"
                      f"x=(w-text_w)/2:y={geom['sub_y']}:line_spacing=14:"
                      f"box=1:boxcolor=0x041b12@0.85:boxborderw=26:"
                      f"borderw=2:bordercolor=0x000000:enable='{cond}'")

    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src]
    if audios:
        for path, _ in audios:
            cmd += ["-i", path]
        legs = ";".join(
            f"[{k + 1}:a]aformat=sample_fmts=fltp:sample_rates=44100:"
            f"channel_layouts=stereo,apad=pad_dur={GAP}[na{k}]"
            for k in range(len(audios)))
        joined = "".join(f"[na{k}]" for k in range(len(audios)))
        chain += (f"[vout];{legs};{joined}"
                  f"concat=n={len(audios)}:v=0:a=1,apad[aout]")
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        chain += "[vout];[1:a]aresample=44100[aout]"

    # 中間クリップは最後の連結時に必ず再エンコードされるので、ここでは
    # ultrafast + 低CRF（＝ほぼ劣化なし）で速度を優先する。
    cmd += ["-filter_complex", chain, "-map", "[vout]", "-map", "[aout]",
            "-threads", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "ultrafast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-t", f"{total:.3f}", clip_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [警告] クリップ{gidx}生成失敗:\n{r.stderr[-900:]}", file=sys.stderr)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"color=c={BG_COLOR}:s={W}x{H}:r={FPS}",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-t", f"{total:.3f}", clip_path], check=True, capture_output=True)
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


def sfx_events(groups: list[dict], speed: float) -> list[tuple[float, str, float]]:
    """(完成動画での秒数, 効果音名, 音量) の一覧を作る。

    図解の行が出る瞬間に音を当てる。@result は「バーン」、@card は軽い
    「トッ」、@chapter は登場音。行が下にいくほど音量を落として、
    1着の一発がいちばん目立つようにする。
    """
    events: list[tuple[float, str, float]] = []
    t = 0.0                       # 完成動画での、そのシーンの開始時刻
    for grp in groups:
        scene = grp["scene"]
        kind = scene.get("kind", "plain")
        if kind == "chapter":
            events.append((t + 0.15, "whoosh", SFX_VOL["whoosh"]))
        elif kind in ("result", "card"):
            name = "impact" if kind == "result" else "tick"
            for i in range(len(scene.get("rows", []))):
                events.append((t + ROW_LEAD + i * ROW_STEP, name,
                               SFX_VOL[name] * (SFX_DECAY ** i)))
        t += sum(dur + GAP for _, dur in grp["audios"]) / speed
    return events


def mix_audio(base: str, out_path: str,
              events: list[tuple[float, str, float]],
              sfx: dict[str, str], bgm: str | None) -> None:
    """完成尺の動画に効果音とBGMを重ねる。映像は再エンコードしない。"""
    events = [(t, n, v) for t, n, v in events if n in sfx]
    if not events and not bgm:
        shutil.move(base, out_path)
        return

    cmd = ["ffmpeg", "-y", "-i", base]
    legs, mixed = [], ["[0:a]"]

    # 同じ効果音ファイルを何度も使うので、入力は1本にして asplit で分ける
    used = sorted({n for _, n, _ in events})
    slot: dict[str, int] = {}
    for idx, name in enumerate(used, start=1):
        cmd += ["-i", sfx[name]]
        slot[name] = idx
    idx = len(used) + 1

    counts = {n: sum(1 for _, m, _ in events if m == n) for n in used}
    for name in used:
        outs = "".join(f"[{name}_{k}]" for k in range(counts[name]))
        legs.append(f"[{slot[name]}:a]asplit={counts[name]}{outs}")

    seen = {n: 0 for n in used}
    for t, name, vol in events:
        k = seen[name]
        seen[name] += 1
        tag = f"e_{name}_{k}"
        legs.append(f"[{name}_{k}]adelay={int(max(t, 0) * 1000)}:all=1,"
                    f"volume={vol:.3f}[{tag}]")
        mixed.append(f"[{tag}]")

    if bgm:
        cmd += ["-stream_loop", "-1", "-i", bgm]
        legs.append(f"[{idx}:a]volume={BGM_VOLUME}[bgm]")
        mixed.append("[bgm]")

    legs.append(f"{''.join(mixed)}amix=inputs={len(mixed)}:duration=first:"
                f"normalize=0,alimiter=limit=0.95[aout]")
    cmd += ["-filter_complex", ";".join(legs), "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-ac", "2", "-shortest", out_path]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [警告] 効果音・BGMのミックスに失敗（音なしで続行）:\n"
              f"{r.stderr[-700:]}", file=sys.stderr)
        shutil.move(base, out_path)


def build_video(script_path, orientation, out_path, font, bgm, engine,
                sfx=None, speed=1.0, cta=False) -> float:
    sfx = sfx or {}
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

    # 2) 同じ図解を使う連続した行を1シーンにまとめる。
    #    こうすると背景と図解がシーン中ずっと描かれっぱなしになり、
    #    字幕が変わるたびに画面が描き直されてチカチカする問題がなくなる。
    groups: list[dict] = []
    for i, seg in enumerate(segs):
        if groups and groups[-1]["scene"] is seg["scene"]:
            groups[-1]["lines"].append(seg["text"])
            groups[-1]["audios"].append(audios[i])
        else:
            groups.append({"scene": seg["scene"], "lines": [seg["text"]],
                           "audios": [audios[i]]})

    # 3) 映像クリップは重いのでコア数に応じて並列生成する
    workers = max(1, min(4, (os.cpu_count() or 2)))
    print(f"  クリップ生成: {len(segs)}行を{len(groups)}シーンにまとめ、"
          f"{workers}並列で処理します")
    clips: list[str] = [""] * len(groups)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(make_scene_clip, g, grp["lines"], grp["scene"],
                      grp["audios"], font, tmp_dir, geom, cta=cta,
                      speed=speed): g
            for g, grp in enumerate(groups)
        }
        for n, fut in enumerate(as_completed(futures), 1):
            g = futures[fut]
            clips[g] = fut.result()
            if n % 5 == 0 or n == len(groups):
                print(f"  シーン {n}/{len(groups)} 完了")

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

    ending = make_scene_clip(len(groups), [], {"kind": "plain"}, [],
                             font, tmp_dir, geom, is_ending=True, cta=cta)
    base = f"{tmp_dir}/base.mp4"
    _concat([body, ending], base, tmp_dir, "final")

    # 効果音は倍速をかけたあとの完成尺に対して置く。クリップの中に混ぜると
    # あとから倍速がかかって「バーン」が「バッ」に潰れてしまうため。
    events = sfx_events(groups, speed)
    mix_audio(base, out_path, events, sfx, bgm)

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
    ap.add_argument("--no-sfx", action="store_true")
    args = ap.parse_args()

    font = find_font()
    if not font:
        print("[エラー] CJKフォントが見つかりません。", file=sys.stderr)
        sys.exit(1)
    bgm = None if args.no_bgm else find_bgm()
    sfx = {} if args.no_sfx else ensure_sfx()
    print(f"フォント: {font}")
    print(f"BGM: {bgm or 'なし（ナレーションのみ）'}")
    print(f"効果音: {'/'.join(sorted(sfx)) or 'なし'}")
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
        # 横型フル尺は落ち着いて見る動画なので、ショートより一段ゆっくり喋らせる
        sp = speed * (1.0 if args.speed else ORIENT_SPEED.get(orientation, 1.0))
        results.append((out, build_video(script, orientation, out, font, bgm,
                                         engine, sfx=sfx, speed=sp, cta=cta)))

    print("\n=== 完了 ===")
    for out, dur in results:
        flag = ""
        if "short" in out and dur > 180:
            flag = "  ← Shorts上限(3分)超え！分割し直してください"
        print(f"  {out}: {int(dur // 60)}分{dur % 60:02.0f}秒{flag}")


if __name__ == "__main__":
    main()
