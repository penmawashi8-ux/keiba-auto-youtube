#!/usr/bin/env python3
"""Step 3: quiz.json + slides/ から音声合成・動画組み立てを行い quiz_video.mp4 に保存"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SLIDES_DIR = Path("slides")
AUDIO_DIR = Path("audio")
OUTPUT_VIDEO = Path("quiz_video.mp4")

# TTS ボイス（競馬ニュース系は KeitaNeural 男性）
TTS_VOICE = "ja-JP-KeitaNeural"

# スライド表示時間（秒）
TITLE_DURATION = 4
THINK_DURATION = 15   # シンキングタイム（カウントダウン表示）
ANSWER_EXTRA = 1      # 回答読み上げ後の余韻
RESULT_DURATION = 5

# 動画設定
FPS = 30
WIDTH = 1920
HEIGHT = 1080


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


async def synthesize_one(text: str, voice: str, out_path: Path):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_path))


async def synthesize_all(quiz: dict):
    """全問の TTS を並列生成"""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    tasks = []
    paths = []

    # タイトル TTS
    title_audio = AUDIO_DIR / "00_title.mp3"
    tasks.append(synthesize_one(
        f"競馬知識クイズ、始めます！全{len(quiz['questions'])}問、何問正解できるか挑戦してみてください！",
        TTS_VOICE, title_audio
    ))
    paths.append(("title", title_audio))

    # 各問TTS（問題文は読まない：シンキングタイムで視聴者が自分で読む）
    for q in quiz["questions"]:
        a_audio = AUDIO_DIR / f"{q['number']:02d}a.mp3"
        tasks.append(synthesize_one(q["tts_answer"], TTS_VOICE, a_audio))
        paths.append((f"q{q['number']}_answer", a_audio))

    # 結果TTS
    result_audio = AUDIO_DIR / "99_result.mp3"
    tasks.append(synthesize_one(
        "全問終了です！いくつ正解できましたか？チャンネル登録と高評価もよろしくお願いします！次回もお楽しみに！",
        TTS_VOICE, result_audio
    ))
    paths.append(("result", result_audio))

    print(f"  {len(tasks)} 個の音声を並列生成中...")
    await asyncio.gather(*tasks)
    print("  音声生成完了")
    return paths


def get_audio_duration(audio_path: Path) -> float:
    """ffprobe で音声の長さを取得"""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(audio_path),
        ],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if "duration" in stream:
            return float(stream["duration"])
    return 3.0


def make_clip(slide_path: Path, audio_path: Path | None, extra_secs: float, out_path: Path):
    """スライド画像 + 音声から動画クリップを生成"""
    if audio_path and audio_path.exists():
        duration = get_audio_duration(audio_path) + extra_secs
    else:
        duration = extra_secs

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(slide_path),
    ]

    if audio_path and audio_path.exists():
        cmd += ["-i", str(audio_path)]
        cmd += [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=#0d1b2a",
            "-c:a", "aac",
            "-b:a", "128k",
            "-af", f"apad=whole_dur={duration}",
            "-t", str(duration),
        ]
    else:
        cmd += [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=#0d1b2a",
            "-an",
            "-t", str(duration),
        ]

    cmd.append(str(out_path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


def make_question_silence_clip(slide_path: Path, out_path: Path, duration: float = 15.0):
    """問題スライド + カウントダウンタイマーの無音クリップ（シンキングタイム）"""
    duration_int = int(duration)
    countdown_text = "%{eif\\:" + str(duration_int) + "-t\\:d}"

    font_path = find_noto_font()

    scale_f = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=#0d1b2a"
    )
    countdown_f = (
        f"drawtext=text='{countdown_text}':"
        f"fontsize=180:fontcolor=white@0.95:"
        f"x=(w-tw)/2:y=h*0.82-th/2:"
        f"box=1:boxcolor=black@0.55:boxborderw=35"
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
            f"fontsize=58:fontcolor=#e8c84a:"
            f"x=(w-tw)/2:y=h*0.73-th/2"
        )
        vf = f"{scale_f},{countdown_f},{label_f}"
    else:
        label_file = None
        vf = f"{scale_f},{countdown_f}"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(slide_path),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", str(duration),
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if label_file:
        os.unlink(label_file)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


def concat_clips(clip_paths: list[Path], out_path: Path):
    """クリップを結合して最終動画を生成"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{p.resolve()}'\n")
        list_file = f.name

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-500:]}")
    finally:
        os.unlink(list_file)


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

    questions = quiz.get("questions", [])

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
    make_clip(
        SLIDES_DIR / "00_title.png",
        AUDIO_DIR / "00_title.mp3",
        TITLE_DURATION,
        title_clip,
    )
    clip_paths.append(title_clip)

    for q in questions:
        n = q["number"]
        print(f"  Q{n} 問題クリップ...")

        # シンキングタイムクリップ（カウントダウン表示・問題文は読まない）
        q_think_clip = clips_dir / f"{n:02d}q_think.mp4"
        make_question_silence_clip(
            SLIDES_DIR / f"{n:02d}q_question.png",
            q_think_clip,
            THINK_DURATION,
        )
        clip_paths.append(q_think_clip)

        # 回答クリップ
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
    make_clip(
        SLIDES_DIR / "99_result.png",
        AUDIO_DIR / "99_result.mp3",
        RESULT_DURATION,
        result_clip,
    )
    clip_paths.append(result_clip)

    # --- 結合 ---
    print("③ クリップ結合中...")
    concat_clips(clip_paths, OUTPUT_VIDEO)

    size_mb = OUTPUT_VIDEO.stat().st_size / 1024 / 1024
    print(f"\n{OUTPUT_VIDEO} に保存しました ({size_mb:.1f} MB)")
    print(f"クリップ数: {len(clip_paths)}")
    print("完了")


if __name__ == "__main__":
    main()
