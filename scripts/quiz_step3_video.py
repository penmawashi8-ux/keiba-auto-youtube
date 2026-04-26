#!/usr/bin/env python3
"""Step 3: quiz.json + slides/ から音声合成・動画組み立てを行い quiz_video.mp4 に保存"""

import asyncio
import glob
import json
import os
import random
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
            f"全{total_q}問、3つのパートに分かれています。制限時間は各15秒！さあ、挑戦してみましょう！",
            TTS_VOICE, title_audio, sem
        ))
        paths.append(("title", title_audio))

        for part in parts:
            pn = part["part_number"]
            pt = part["part_title"]
            questions = part["questions"]

            part_audio = AUDIO_DIR / f"p{pn:02d}_00_intro.mp3"
            tasks.append(synthesize_one(
                f"第{pn}パート、{pt}です！全{len(questions)}問、制限時間は15秒！",
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
            f"全{total_q}問、制限時間は15秒！さあ、挑戦してみましょう！",
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


def make_clip(slide_path: Path, audio_path: Path | None, extra_secs: float, out_path: Path):
    """スライド画像 + 音声から動画クリップを生成。

    -shortest で音声終端にスライドを自動同期。duration 計算不要。
    apad=pad_dur で末尾に extra_secs 秒の余韻無音を追加する。
    -ar 44100 でシンキングタイムクリップ（44100Hz）と統一し concat での音ずれを防ぐ。
    """
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(slide_path),
    ]

    scale_vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=#0d1b2a"
    )

    if audio_path and audio_path.exists():
        cmd += ["-i", str(audio_path)]
        cmd += [
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-vf", scale_vf,
            "-c:a", "aac", "-b:a", "128k",
            "-ar", "44100",
            "-af", f"apad=pad_dur={extra_secs}",
            "-shortest",
        ]
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-vf", scale_vf,
            "-an",
            "-t", str(extra_secs),
        ]

    cmd.append(str(out_path))

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
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=#0d1b2a"
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

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(slide_path),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-c:a", "aac", "-b:a", "128k",
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
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-500:]}")
    finally:
        os.unlink(list_file)


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
