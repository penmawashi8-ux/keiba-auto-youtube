#!/usr/bin/env python3
"""Step 6: 音声・字幕・動画を生成して final_output.mp4 を出力 (PIL禁止・ffmpeg使用)"""

import os
import sys
import json
import time
import math
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path


# ─── 設定 ────────────────────────────────────────────────
VOICEVOX_URL = os.environ.get("VOICEVOX_URL", "http://localhost:50021")
VOICEVOX_SPEAKER = int(os.environ.get("VOICEVOX_SPEAKER", "1"))
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
BGM_VOLUME = 0.3
# zoompan は CPU 負荷が高いため CI 環境ではオフにする（ENABLE_ZOOMPAN=1 で有効化）
ENABLE_ZOOMPAN = os.environ.get("ENABLE_ZOOMPAN", "0") == "1"
BGM_PATH = os.environ.get("BGM_PATH", "bgm.mp3")
GRAPHS_DIR = Path("graphs")
SCRIPT_PATH = "script.txt"
NARRATION_PATH = "narration.mp3"
SUBTITLES_PATH = "subtitles.srt"
OUTPUT_PATH = "final_output.mp4"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
# ─────────────────────────────────────────────────────────


def run_cmd(cmd, step_name="", check=True):
    """コマンドを実行してエラー時はステップ名付きで終了"""
    print(f"  $ {' '.join(str(c) for c in cmd[:6])}{'...' if len(cmd) > 6 else ''}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"\n[{step_name}] ERROR (exit {result.returncode}):")
        print(result.stderr[-2000:])
        sys.exit(1)
    return result


# ─── Step ①: VOICEVOX TTS ────────────────────────────────

def split_text_for_voicevox(text, max_chars=200):
    """長いテキストを句点・改行で区切って分割"""
    import re
    # 句点・感嘆符・疑問符・改行で分割
    sentences = re.split(r"(?<=[。！？\n])", text)
    chunks = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) > max_chars:
            if current:
                chunks.append(current.strip())
            current = sent
        else:
            current += sent
    if current.strip():
        chunks.append(current.strip())
    return chunks


def voicevox_synthesize(text, speaker=VOICEVOX_SPEAKER):
    """VOICEVOX API で1チャンクを音声合成してバイト列を返す"""
    # audio_query
    query_url = f"{VOICEVOX_URL}/audio_query?speaker={speaker}"
    data = urllib.parse.urlencode({"text": text}).encode()
    req = urllib.request.Request(query_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        query = json.load(resp)

    # synthesis
    synth_url = f"{VOICEVOX_URL}/synthesis?speaker={speaker}"
    body = json.dumps(query).encode("utf-8")
    req2 = urllib.request.Request(synth_url, data=body, method="POST")
    req2.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req2, timeout=60) as resp2:
        return resp2.read()


def check_voicevox_available():
    try:
        req = urllib.request.Request(f"{VOICEVOX_URL}/version", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ver = resp.read().decode()
            print(f"  VOICEVOX バージョン: {ver.strip()}")
            return True
    except Exception as e:
        print(f"  VOICEVOX 接続不可: {e}")
        return False


def generate_narration():
    """script.txt → narration.mp3 (Step ①)"""
    print("\n[Step ①] 音声合成 (VOICEVOX)")

    if not Path(SCRIPT_PATH).exists():
        print(f"ERROR: {SCRIPT_PATH} が見つかりません")
        sys.exit(1)

    with open(SCRIPT_PATH, encoding="utf-8") as f:
        script = f.read().strip()

    if not check_voicevox_available():
        print("VOICEVOX が起動していません。")
        print("  起動方法: docker run -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest")
        print("  またはローカルで VOICEVOX を起動してから再実行してください")
        sys.exit(1)

    chunks = split_text_for_voicevox(script, max_chars=200)
    print(f"  テキストを {len(chunks)} チャンクに分割")

    wav_files = []
    tmp_dir = tempfile.mkdtemp(prefix="voicevox_")

    for i, chunk in enumerate(chunks):
        print(f"  [{i+1}/{len(chunks)}] {chunk[:30]}...")
        try:
            wav_data = voicevox_synthesize(chunk, VOICEVOX_SPEAKER)
            wav_path = os.path.join(tmp_dir, f"chunk_{i:04d}.wav")
            with open(wav_path, "wb") as f:
                f.write(wav_data)
            wav_files.append(wav_path)
        except Exception as e:
            print(f"  WARNING: チャンク{i}失敗: {e}、スキップ")
            time.sleep(1)

    if not wav_files:
        print("ERROR: 音声合成が全て失敗しました")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        sys.exit(1)

    # WAVファイルを連結して MP3 に変換 (ffmpeg)
    concat_list = os.path.join(tmp_dir, "concat.txt")
    with open(concat_list, "w") as f:
        for wav in wav_files:
            f.write(f"file '{wav}'\n")

    run_cmd(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-ar", "44100", "-ac", "1", "-b:a", "192k", NARRATION_PATH],
        step_name="Step①:音声連結"
    )

    shutil.rmtree(tmp_dir, ignore_errors=True)

    duration = get_audio_duration(NARRATION_PATH)
    print(f"  narration.mp3 生成完了 ({duration:.1f}秒)")


# ─── Step ②: スライドショー動画生成 ──────────────────────

def get_audio_duration(audio_path):
    """ffprobeで音声の長さ（秒）を取得"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 600.0


def get_image_files():
    """graphs/ から連番順に画像を取得"""
    images = sorted(GRAPHS_DIR.glob("*.png")) + sorted(GRAPHS_DIR.glob("*.jpg"))
    if not images:
        print(f"ERROR: {GRAPHS_DIR}/ に画像がありません")
        sys.exit(1)
    return images


def create_slideshow(output_path="slideshow_raw.mp4"):
    """グラフ画像をスライドショー動画に変換 (Step ②)"""
    print("\n[Step ②] スライドショー動画生成")

    images = get_image_files()
    audio_duration = get_audio_duration(NARRATION_PATH)
    slide_duration = audio_duration / len(images)

    print(f"  画像 {len(images)} 枚、音声 {audio_duration:.1f}秒、各スライド {slide_duration:.1f}秒")

    tmp_dir = tempfile.mkdtemp(prefix="slides_")
    clip_files = []

    for i, img_path in enumerate(images):
        clip_path = os.path.join(tmp_dir, f"clip_{i:03d}.mp4")

        # 画像をスケール・パッドして1920×1080に合わせる
        scale_pad = (
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black"
        )
        if ENABLE_ZOOMPAN:
            zoom_start = 1.0 + (0.05 * (i % 2))
            zoom_end = zoom_start + 0.05
            vf = (
                f"{scale_pad},"
                f"zoompan=z='if(lte(zoom,{zoom_start}),{zoom_end},max({zoom_start},zoom-0.001))':"
                f"d={int(slide_duration * 25)}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps=25"
            )
        else:
            vf = scale_pad

        run_cmd(
            ["ffmpeg", "-y",
             "-loop", "1", "-i", str(img_path),
             "-t", str(slide_duration),
             "-vf", vf,
             "-c:v", "libx264", "-preset", "fast", "-crf", "23",
             "-pix_fmt", "yuv420p",
             clip_path],
            step_name=f"Step②:スライド{i+1}"
        )
        clip_files.append(clip_path)

    # クリップを連結
    concat_list = os.path.join(tmp_dir, "concat.txt")
    with open(concat_list, "w") as f:
        for clip in clip_files:
            f.write(f"file '{clip}'\n")

    run_cmd(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
         "-pix_fmt", "yuv420p", output_path],
        step_name="Step②:クリップ連結"
    )

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"  スライドショー動画生成完了: {output_path}")
    return output_path


# ─── Step ③: Whisper で字幕生成 ──────────────────────────

def generate_subtitles():
    """Whisper で narration.mp3 → subtitles.srt (Step ③)"""
    print("\n[Step ③] Whisper 字幕生成")

    try:
        import whisper
    except ImportError:
        print("  WARNING: openai-whisper がインストールされていません")
        print("    pip install openai-whisper でインストールしてください")
        print("  空の字幕ファイルを作成して続行します")
        with open(SUBTITLES_PATH, "w", encoding="utf-8") as f:
            f.write("")
        return

    print(f"  Whisper モデル '{WHISPER_MODEL}' をロード中...")
    try:
        model = whisper.load_model(WHISPER_MODEL)
        result = model.transcribe(NARRATION_PATH, language="ja", verbose=False)

        with open(SUBTITLES_PATH, "w", encoding="utf-8") as f:
            for i, seg in enumerate(result["segments"], 1):
                start = _format_srt_time(seg["start"])
                end = _format_srt_time(seg["end"])
                text = seg["text"].strip()
                f.write(f"{i}\n{start} --> {end}\n{text}\n\n")

        print(f"  subtitles.srt 生成完了 ({len(result['segments'])}セグメント)")

    except Exception as e:
        print(f"  WARNING: Whisper 失敗: {e}")
        with open(SUBTITLES_PATH, "w", encoding="utf-8") as f:
            f.write("")


def _format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─── Step ④⑤: 字幕焼き込み・BGMミックス・出力 ─────────────

def find_japanese_font():
    """ffmpegの drawtext 用日本語フォントパスを返す"""
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/local/share/fonts/NotoSansCJKjp-Regular.otf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""


def build_final_video(slideshow_path):
    """字幕焼き込み + BGM ミックス → final_output.mp4 (Step ④⑤)"""
    print("\n[Step ④⑤] 字幕焼き込み・BGMミックス・最終出力")

    has_subtitles = Path(SUBTITLES_PATH).exists() and os.path.getsize(SUBTITLES_PATH) > 10
    has_bgm = Path(BGM_PATH).exists()

    font_path = find_japanese_font()

    # フィルターグラフを構築
    video_filters = []

    if has_subtitles:
        # SRT字幕を焼き込み
        abs_srt = os.path.abspath(SUBTITLES_PATH)
        # ファイルパスのコロンをエスケープ (ffmpegのWindows互換)
        escaped_srt = abs_srt.replace("\\", "/").replace(":", "\\:")
        sub_filter = f"subtitles={escaped_srt}"
        if font_path:
            escaped_font = font_path.replace(":", "\\:")
            sub_filter += f":force_style='FontName={escaped_font},FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1'"
        video_filters.append(sub_filter)
        print("  字幕あり: SRTを焼き込みます")
    else:
        print("  字幕なし: 字幕なしで進めます")

    vf_str = ",".join(video_filters) if video_filters else "copy"

    # 音声入力の構築
    inputs = ["-i", slideshow_path, "-i", NARRATION_PATH]

    if has_bgm:
        inputs += ["-i", BGM_PATH]
        # BGM をループして長さを合わせ、ナレーションと amix
        audio_filter = (
            f"[1:a]volume=1.0[narr];"
            f"[2:a]volume={BGM_VOLUME},aloop=loop=-1:size=2e+09[bgm_loop];"
            f"[narr][bgm_loop]amix=inputs=2:duration=first:dropout_transition=3[aout]"
        )
        audio_map = ["-map", "0:v", "-map", "[aout]"]
        print(f"  BGM あり: {BGM_VOLUME*100:.0f}% ミックス")
    else:
        audio_filter = None
        audio_map = ["-map", "0:v", "-map", "1:a"]
        print(f"  BGM なし ({BGM_PATH} が見つかりません)")

    # ffmpeg コマンド構築
    cmd = ["ffmpeg", "-y"] + inputs

    if has_subtitles or video_filters:
        cmd += ["-vf", vf_str]
    else:
        cmd += ["-c:v", "copy"]

    if audio_filter:
        cmd += ["-filter_complex", audio_filter]

    cmd += audio_map

    if not (has_subtitles or video_filters):
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]

    cmd += [
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100",
        "-shortest",
        "-movflags", "+faststart",
        OUTPUT_PATH,
    ]

    run_cmd(cmd, step_name="Step④⑤:最終動画")

    duration = get_audio_duration(OUTPUT_PATH)
    size_mb = os.path.getsize(OUTPUT_PATH) / (1024 * 1024)
    print(f"\n  ✓ {OUTPUT_PATH} 生成完了 ({duration:.1f}秒 / {size_mb:.1f}MB)")


# ─── メイン ──────────────────────────────────────────────

def main():
    print("=== Step 6: 動画生成パイプライン ===")
    print(f"  解像度: {VIDEO_WIDTH}×{VIDEO_HEIGHT} (横向き)")

    current_step = "Step①"
    try:
        # ① VOICEVOX TTS
        generate_narration()

        # ② スライドショー動画生成
        current_step = "Step②"
        slideshow_path = create_slideshow("slideshow_raw.mp4")

        # ③ Whisper 字幕生成
        current_step = "Step③"
        generate_subtitles()

        # ④⑤ 字幕焼き込み + BGM + 最終出力
        current_step = "Step④⑤"
        build_final_video(slideshow_path)

        # 中間ファイルを削除
        if Path("slideshow_raw.mp4").exists():
            os.remove("slideshow_raw.mp4")

        print("\n=== 完了: final_output.mp4 ===")

    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[{current_step}] 予期しないエラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
