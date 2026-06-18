#!/usr/bin/env python3
"""Step 3: quiz.json + slides/ から音声合成・動画組み立てを行い quiz_video.mp4 に保存"""

import asyncio
import glob
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SLIDES_DIR = Path("slides")
AUDIO_DIR = Path("audio")
OUTPUT_VIDEO = Path("quiz_video.mp4")

# TTS ボイス（競馬ニュース系は KeitaNeural 男性）
TTS_VOICE = "ja-JP-KeitaNeural"
TTS_VOLUME = "+50%"   # 音声が小さい場合に増幅

# スライド表示時間（秒）
TITLE_DURATION = 4
THINK_DURATION = 15   # シンキングタイム（カウントダウン表示）
ANSWER_EXTRA = 1      # 回答読み上げ後の余韻
RESULT_DURATION = 5

# BGM 設定
BGM_VOL = 0.12

# 動画設定
FPS = 30
WIDTH = 1920
HEIGHT = 1080


_RACING_TERM_RE = [
    (re.compile(r'GIII|GⅢ'), 'ジースリー'),
    (re.compile(r'GII|GⅡ'), 'ジーツー'),
    (re.compile(r'GI|GⅠ'), 'ジーワン'),
    (re.compile(r'G3'), 'ジースリー'),
    (re.compile(r'G2'), 'ジーツー'),
    (re.compile(r'G1'), 'ジーワン'),
]

_readings_cache: dict | None = None


def _apply_readings(text: str) -> str:
    global _readings_cache
    if _readings_cache is None:
        p = Path("data/readings.json")
        _readings_cache = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    for kanji in sorted(_readings_cache, key=len, reverse=True):
        reading = _readings_cache[kanji]
        if isinstance(reading, str) and kanji in text:
            text = text.replace(kanji, reading)
    for pattern, repl in _RACING_TERM_RE:
        text = pattern.sub(repl, text)
    return text


def find_noto_font() -> str | None:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
        "/usr/local/share/fonts/NotoSansCJKjp-Regular.otf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


async def synthesize_one(text: str, voice: str, out_path: Path, sem: asyncio.Semaphore):
    import edge_tts
    text = _apply_readings(text)
    for attempt in range(4):
        try:
            async with sem:
                communicate = edge_tts.Communicate(text, voice, volume=TTS_VOLUME)
                await communicate.save(str(out_path))
            return
        except Exception as e:
            if attempt == 3:
                raise
            await asyncio.sleep(2 ** attempt)


async def synthesize_all(quiz: dict):
    """全問の TTS を並列生成（シングルパート・マルチパート両対応）"""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(5)  # edge-tts の同時接続数を制限してレート制限エラーを防ぐ
    tasks = []
    paths = []

    if quiz.get("multipart"):
        parts = quiz["parts"]
        total_q = sum(len(p["questions"]) for p in parts)

        title_audio = AUDIO_DIR / "00_title.mp3"
        tasks.append(synthesize_one(
            f"名馬当てクイズ、スタートです！"
            f"重賞勝利歴のヒントから名馬を当ててください。"
            f"全{total_q}問、3つのパートに分かれています。制限時間は各{THINK_DURATION}秒！さあ、挑戦してみましょう！",
            TTS_VOICE, title_audio, sem
        ))
        paths.append(("title", title_audio))

        for part in parts:
            pn = part["part_number"]
            pt = part["part_title"]
            questions = part["questions"]

            part_audio = AUDIO_DIR / f"p{pn:02d}_00_intro.mp3"
            tasks.append(synthesize_one(
                f"第{pn}パート、{pt}です！全{len(questions)}問、制限時間は{THINK_DURATION}秒！",
                TTS_VOICE, part_audio, sem
            ))
            paths.append((f"p{pn}_intro", part_audio))

            for q in questions:
                a_audio = AUDIO_DIR / f"p{pn:02d}_{q['number']:02d}a.mp3"
                tasks.append(synthesize_one(q["tts_answer"], TTS_VOICE, a_audio, sem))
                paths.append((f"p{pn}_q{q['number']}_answer", a_audio))

    else:
        total_q = len(quiz["questions"])
        title_audio = AUDIO_DIR / "00_title.mp3"
        tasks.append(synthesize_one(
            f"名馬当てクイズ、スタートです！"
            f"G1の勝利歴ヒントから名馬を当ててください。"
            f"全{total_q}問、制限時間は{THINK_DURATION}秒！さあ、挑戦してみましょう！",
            TTS_VOICE, title_audio, sem
        ))
        paths.append(("title", title_audio))

        for q in quiz["questions"]:
            a_audio = AUDIO_DIR / f"{q['number']:02d}a.mp3"
            tasks.append(synthesize_one(q["tts_answer"], TTS_VOICE, a_audio, sem))
            paths.append((f"q{q['number']}_answer", a_audio))

    result_audio = AUDIO_DIR / "99_result.mp3"
    tasks.append(synthesize_one(
        "全問終了です！いくつ正解できましたか？チャンネル登録と高評価もよろしくお願いします！次回もお楽しみに！",
        TTS_VOICE, result_audio, sem
    ))
    paths.append(("result", result_audio))

    print(f"  {len(tasks)} 個の音声を並列生成中...")
    await asyncio.gather(*tasks)
    print("  音声生成完了")
    return paths


# 全クリップ共通の映像正規化（解像度・SAR・フレームレート・ピクセル形式を統一）。
# concat フィルタでの結合にはこれらが全入力で一致している必要がある。
_SCALE_VF = (
    f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=#0d1b2a,"
    f"setsar=1,fps={FPS},format=yuv420p"
)


def _probe_duration(path: Path) -> float:
    """音声/動画ファイルの尺（秒）を返す。失敗時は 0.0。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _quantize_to_frame(seconds: float) -> float:
    """秒数をフレーム境界（1/FPS の倍数）に切り上げる。
    これにより映像（フレーム単位）と音声（44100Hz）の尺を厳密一致でき、
    結合時の累積音ズレを根絶する（FPS=30 のとき 44100 はフレーム長で割り切れる）。
    """
    nframes = max(1, math.ceil(seconds * FPS))
    return nframes / FPS


def make_clip(slide_path: Path, audio_path: Path | None, extra_secs: float, out_path: Path):
    """スライド画像 + 音声から動画クリップを生成。

    音声尺と映像尺を「フレーム境界に量子化した同一の長さ」で厳密に切り揃える
    （-t を全ストリームに適用）。音声は apad で必要長まで無音パディングする。
    全クリップを CFR {FPS}fps / 44100Hz ステレオ AAC に統一し、
    concat フィルタでの再エンコード結合時に音ズレ・結合ノイズが出ないようにする。
    """
    if audio_path and audio_path.exists():
        target = _quantize_to_frame(_probe_duration(audio_path) + float(extra_secs))
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(slide_path),
            "-i", str(audio_path),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-r", str(FPS),
            "-vf", _SCALE_VF,
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-af", "apad",
            "-t", f"{target:.6f}",
            str(out_path),
        ]
    else:
        target = _quantize_to_frame(float(extra_secs))
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(slide_path),
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-r", str(FPS),
            "-vf", _SCALE_VF,
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-t", f"{target:.6f}",
            str(out_path),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


def make_question_silence_clip(slide_path: Path, out_path: Path, duration: float = 15.0):
    """問題スライド + カウントダウンタイマーの無音クリップ（シンキングタイム）"""
    duration_int = int(duration)
    countdown_text = r"%{eif\:" + str(duration_int) + r"-floor(t)\:d}"

    font_path = find_noto_font()

    scale_f = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=#0d1b2a,"
        f"setsar=1,fps={FPS},format=yuv420p"
    )
    # カウントダウン: choice下段底辺(918px from top)より下に配置
    countdown_f = (
        f"drawtext=text='{countdown_text}':"
        f"fontsize=95:fontcolor=white@0.95:"
        f"x=(w-tw)/2:y=h*0.935-th/2:"
        f"box=1:boxcolor=black@0.55:boxborderw=25"
    )

    if font_path:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tf:
            tf.write("シンキングタイム")
            label_file = tf.name
        label_f = (
            f"drawtext=fontfile={font_path}:"
            f"textfile={label_file}:"
            f"fontsize=50:fontcolor=#e8c84a:"
            f"x=(w-tw)/2:y=h*0.875-th/2"
        )
        vf = f"{scale_f},{countdown_f},{label_f}"
    else:
        label_file = None
        vf = f"{scale_f},{countdown_f}"

    target = _quantize_to_frame(float(duration))
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(slide_path),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-r", str(FPS),
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-t", f"{target:.6f}",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if label_file:
        os.unlink(label_file)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


def _concat_filter(clip_paths: list[Path], out_path: Path, crf: str = "23"):
    """concat フィルタで結合（各入力をデコードし1本の連続タイムラインに再エンコード）。

    -c copy のデムューサ結合と違い、各クリップの音声を一旦デコードするため
    AAC エンコーダ遅延（priming）由来の無音/ノイズが結合点に残らず、
    かつ全クリップで音声尺=映像尺が厳密一致しているため累積音ズレも発生しない。
    """
    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd += ["-i", str(p)]
    n = len(clip_paths)
    graph = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    cmd += [
        "-filter_complex", graph,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-800:]}")


def concat_clips(clip_paths: list[Path], out_path: Path, batch_size: int = 50):
    """クリップを結合して最終動画を生成。

    入力本数が多い場合は concat フィルタの入力数・開いるファイル数の上限を避けるため
    バッチ単位で中間ファイルに結合してから、その中間ファイル同士を再結合する
    （中間ファイルは連続した1本のクリップなので再結合でも音ズレ・ノイズは出ない）。
    """
    clip_paths = list(clip_paths)
    if len(clip_paths) <= batch_size:
        _concat_filter(clip_paths, out_path)
        return

    tmp_dir = Path(tempfile.mkdtemp(prefix="concat_"))
    try:
        intermediates: list[Path] = []
        for i in range(0, len(clip_paths), batch_size):
            batch = clip_paths[i:i + batch_size]
            inter = tmp_dir / f"part_{i // batch_size:03d}.mp4"
            # 中間段は再エンコード回数を抑えるため軽め(crf 23)で十分
            _concat_filter(batch, inter, crf="23")
            intermediates.append(inter)
        # 中間ファイル同士を最終結合
        _concat_filter(intermediates, out_path, crf="23")
    finally:
        for f in tmp_dir.glob("*.mp4"):
            f.unlink()
        tmp_dir.rmdir()


def add_bgm(video_path: Path, out_path: Path) -> bool:
    """BGMを動画にミックス。

    amix=duration=first のみ使用し、apadは不要。
    first（ナレーション側）が終わった時点で出力終了するため
    duration計算なしに正確な長さが得られる。
    """
    bgm_files = sorted(
        glob.glob("assets/bgm/*.mp3") + glob.glob("assets/bgm/*.m4a")
    )
    if not bgm_files:
        print("  BGMなし: assets/bgm/ に mp3 を置くと自動適用")
        return False
    bgm_path = random.choice(bgm_files)
    print(f"  BGM: {Path(bgm_path).name} (vol={BGM_VOL})")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        f"[0:a][1:a]amix=inputs=2:duration=first:weights=1 {BGM_VOL}[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  BGM追加失敗: {result.stderr[-300:]}", file=sys.stderr)
        return False
    return True


def main():
    print("=== Step 3: 動画生成 ===")

    if not Path("quiz.json").exists():
        print("ERROR: quiz.json が見つかりません。Step 1 を先に実行してください。")
        sys.exit(1)

    if not SLIDES_DIR.exists():
        print("ERROR: slides/ フォルダが見つかりません。Step 2 を先に実行してください。")
        sys.exit(1)

    with open("quiz.json", encoding="utf-8") as f:
        quiz = json.load(f)

    # シンキングタイムは quiz.json の "think_duration" で上書き可能（未指定なら既定値）
    global THINK_DURATION
    THINK_DURATION = int(quiz.get("think_duration", THINK_DURATION))
    print(f"シンキングタイム: {THINK_DURATION}秒")

    # --- 音声生成 ---
    print("① 音声生成中 (edge-tts)...")
    asyncio.run(synthesize_all(quiz))

    # --- クリップ生成 ---
    clips_dir = Path("clips")
    clips_dir.mkdir(parents=True, exist_ok=True)
    clip_paths = []

    print("② 動画クリップ生成中...")

    # タイトルクリップ
    print("  タイトル...")
    title_clip = clips_dir / "00_title.mp4"
    make_clip(SLIDES_DIR / "00_title.png", AUDIO_DIR / "00_title.mp3", TITLE_DURATION, title_clip)
    clip_paths.append(title_clip)

    if quiz.get("multipart"):
        for part in quiz["parts"]:
            pn = part["part_number"]
            pt = part["part_title"]
            questions = part["questions"]

            print(f"  パート{pn}（{pt}）導入クリップ...")
            intro_clip = clips_dir / f"p{pn:02d}_00_intro.mp4"
            make_clip(
                SLIDES_DIR / f"p{pn:02d}_00_intro.png",
                AUDIO_DIR / f"p{pn:02d}_00_intro.mp3",
                TITLE_DURATION,
                intro_clip,
            )
            clip_paths.append(intro_clip)

            for q in questions:
                n = q["number"]
                print(f"  P{pn} Q{n} 問題クリップ（シンキングタイム{THINK_DURATION}秒）...")
                q_think_clip = clips_dir / f"p{pn:02d}_{n:02d}q_think.mp4"
                make_question_silence_clip(
                    SLIDES_DIR / f"p{pn:02d}_{n:02d}q_question.png",
                    q_think_clip,
                    THINK_DURATION,
                )
                clip_paths.append(q_think_clip)

                print(f"  P{pn} Q{n} 回答クリップ...")
                a_clip = clips_dir / f"p{pn:02d}_{n:02d}a.mp4"
                make_clip(
                    SLIDES_DIR / f"p{pn:02d}_{n:02d}a_answer.png",
                    AUDIO_DIR / f"p{pn:02d}_{n:02d}a.mp3",
                    ANSWER_EXTRA,
                    a_clip,
                )
                clip_paths.append(a_clip)

    else:
        for q in quiz.get("questions", []):
            n = q["number"]
            print(f"  Q{n} 問題クリップ（シンキングタイム{THINK_DURATION}秒）...")
            q_think_clip = clips_dir / f"{n:02d}q_think.mp4"
            make_question_silence_clip(
                SLIDES_DIR / f"{n:02d}q_question.png", q_think_clip, THINK_DURATION
            )
            clip_paths.append(q_think_clip)

            print(f"  Q{n} 回答クリップ...")
            a_clip = clips_dir / f"{n:02d}a.mp4"
            make_clip(
                SLIDES_DIR / f"{n:02d}a_answer.png",
                AUDIO_DIR / f"{n:02d}a.mp3",
                ANSWER_EXTRA,
                a_clip,
            )
            clip_paths.append(a_clip)

    # 結果クリップ
    print("  結果クリップ...")
    result_clip = clips_dir / "99_result.mp4"
    make_clip(SLIDES_DIR / "99_result.png", AUDIO_DIR / "99_result.mp3", RESULT_DURATION, result_clip)
    clip_paths.append(result_clip)

    # --- 結合 ---
    print("③ クリップ結合中...")
    concat_clips(clip_paths, OUTPUT_VIDEO)

    # --- BGM追加 ---
    print("④ BGM追加中...")
    bgm_out = OUTPUT_VIDEO.parent / (OUTPUT_VIDEO.stem + "_bgm.mp4")
    if add_bgm(OUTPUT_VIDEO, bgm_out):
        bgm_out.replace(OUTPUT_VIDEO)

    size_mb = OUTPUT_VIDEO.stat().st_size / 1024 / 1024
    print(f"\n{OUTPUT_VIDEO} に保存しました ({size_mb:.1f} MB)")
    print(f"クリップ数: {len(clip_paths)}")
    print("完了")


if __name__ == "__main__":
    main()
