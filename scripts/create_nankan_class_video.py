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
import shutil
import subprocess
import sys
from pathlib import Path

VOICE  = "ja-JP-NanamiNeural"   # 解説向け女性ナレーター（edge-tts）
RATE   = "-2%"
VOLUME = "+0%"

FPS          = 30
BGM_VOLUME   = 0.12
ENDING_DUR   = 3.5
GAP          = 0.35            # 行間の無音（読みやすさ）
SERIES_LABEL = "南関東 格付け講座"

# 競馬場のターフをイメージしたディープグリーン基調のグラデーション
GRAD_TOP    = "0x0d3b1e"
GRAD_BOTTOM = "0x041410"


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


def choose_engine() -> str:
    _trust_proxy_ca()
    if edge_synth("テスト", "/tmp/_engine_probe.mp3"):
        return "edge"
    print("  [情報] edge-tts が使用不可のため pyopenjtalk(オフライン) に切替", file=sys.stderr)
    return "oj"


def synth_line(engine: str, text: str, tmp_dir: str, idx: int) -> tuple[str, float]:
    if engine == "edge":
        out = f"{tmp_dir}/a_{idx:04d}.mp3"
        if edge_synth(text, out):
            return out, probe_duration(out)
        engine = "oj"  # 途中で失敗したら以降フォールバック
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
        return {"w": 1080, "h": 1920, "label_size": 46, "label_y": 150,
                "sub_size": 58, "sub_y": "h-text_h-560", "sub_chars": 14, "end_size": 60}
    return {"w": 1920, "h": 1080, "label_size": 46, "label_y": 60,
            "sub_size": 58, "sub_y": "h-text_h-90", "sub_chars": 28, "end_size": 66}


def wrap_text(text: str, max_chars: int) -> str:
    out = []
    for para in text.split("\n"):
        while len(para) > max_chars:
            out.append(para[:max_chars])
            para = para[max_chars:]
        if para:
            out.append(para)
    return "\n".join(out)


def make_clip(idx, text, audio_path, duration, font_path, tmp_dir, geom,
              is_ending=False) -> str:
    """背景(グラデ)+字幕+音声を持つ1クリップを生成。"""
    W, H = geom["w"], geom["h"]
    clip_path  = f"{tmp_dir}/clip_{idx:04d}.mp4"
    label_file = f"{tmp_dir}/label.txt"
    text_file  = f"{tmp_dir}/text_{idx:04d}.txt"
    duration   = max(duration, 0.6)

    Path(label_file).write_text(SERIES_LABEL, encoding="utf-8")
    Path(text_file).write_text(
        "チャンネル登録お願いします\nまた次の競馬でお会いしましょう" if is_ending
        else wrap_text(text, geom["sub_chars"]),
        encoding="utf-8",
    )

    src = (f"gradients=s={W}x{H}:c0={GRAD_TOP}:c1={GRAD_BOTTOM}:"
           f"x0=0:y0=0:x1=0:y1={H}:type=linear:d=1:r={FPS}")
    chain = "[0:v]vignette=PI/4.2,format=yuv420p"

    fp = font_path.replace("'", "\\'")
    lf = label_file.replace("'", "\\'")
    tf = text_file.replace("'", "\\'")

    chain += (f",drawtext=textfile='{lf}':fontfile='{fp}':"
              f"fontsize={geom['label_size']}:fontcolor=0xFFD24A@0.96:"
              f"x=(w-text_w)/2:y={geom['label_y']}:"
              f"box=1:boxcolor=0x000000@0.55:boxborderw=16:"
              f"borderw=2:bordercolor=0x06251a")

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

    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src]
    if audio_path:
        cmd += ["-i", audio_path]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-filter_complex", chain, "-map", "[vout]", "-map", "1:a",
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


def build_video(script_path, orientation, out_path, font_path, bgm_path, engine):
    geom = geometry(orientation)
    Path("output").mkdir(exist_ok=True)
    tmp_dir = f"/tmp/nankan_{orientation}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)

    lines = load_narration(script_path)
    print(f"\n=== {orientation} ({geom['w']}x{geom['h']}) 生成開始 ===")
    print(f"  原稿: {script_path}  ({len(lines)}行) / エンジン: {engine}")

    clip_paths = []
    for i, line in enumerate(lines):
        audio, dur = synth_line(engine, line, tmp_dir, i)
        clip_paths.append(
            make_clip(i, line, audio, dur + GAP, font_path, tmp_dir, geom))
        print(f"  [{i+1}/{len(lines)}] {dur+GAP:.2f}s 「{line[:24]}」")

    clip_paths.append(
        make_clip(len(lines), "", None, ENDING_DUR, font_path, tmp_dir, geom,
                  is_ending=True))

    concat_path = f"{tmp_dir}/concat.txt"
    with open(concat_path, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")

    base = f"{tmp_dir}/base.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_path,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k", base], check=True, capture_output=True)

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script")
    ap.add_argument("--orientation", choices=["portrait", "landscape"])
    ap.add_argument("--out")
    args = ap.parse_args()

    font_path = find_font()
    if not font_path:
        print("[エラー] CJKフォントが見つかりません。", file=sys.stderr)
        sys.exit(1)
    bgm_path = find_bgm()
    engine = choose_engine()

    if args.script and args.orientation and args.out:
        jobs = [(args.script, args.orientation, args.out)]
    else:
        jobs = [
            ("data/nankan_class_short.txt", "portrait",  "output/nankan_class_short.mp4"),
            ("data/nankan_class_full.txt",  "landscape", "output/nankan_class_full.mp4"),
        ]
    for script, orientation, out in jobs:
        build_video(script, orientation, out, font_path, bgm_path, engine)

    print("\n=== すべて完了 ===")


if __name__ == "__main__":
    main()
