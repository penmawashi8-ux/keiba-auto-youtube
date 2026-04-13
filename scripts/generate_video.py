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
LINE_MAX_CHARS = 15       # 字幕1行最大文字数


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

def wrap_text(text: str, max_chars: int = LINE_MAX_CHARS) -> str:
    lines = []
    for para in text.split("\n"):
        while len(para) > max_chars:
            lines.append(para[:max_chars])
            para = para[max_chars:]
        if para:
            lines.append(para)
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

    if font_path:
        fp = font_path.replace("'", "\\'")

        if is_thumbnail:
            # サムネイルフレーム: タイトルを大きく中央に表示
            title_file = f"{tmp_dir}/thumb_title_{idx:04d}.txt"
            wrapped = wrap_text(thumb_title, max_chars=10)
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
                # ── ニュース系サムネイル（既存デザイン） ──
                badge_file = f"{tmp_dir}/thumb_badge_{idx:04d}.txt"
                Path(badge_file).write_text("競馬速報", encoding="utf-8")
                bf = badge_file.replace("'", "\\'")

                # 赤バッジ（左上）
                chain += (
                    f",drawtext=textfile='{bf}':fontfile='{fp}':"
                    f"fontsize=54:fontcolor=0xFFFFFF:"
                    f"x=44:y=70:"
                    f"box=1:boxcolor=0xD21E1E@0.95:boxborderw=22"
                )
                # タイトルテキスト（中央・黄色）
                chain += (
                    f",drawtext=textfile='{tf}':fontfile='{fp}':"
                    f"fontsize=96:fontcolor=0xFFEB00:"
                    f"x=(w-text_w)/2:y=720:"
                    f"line_spacing=16:"
                    f"box=1:boxcolor=0x000000@0.65:boxborderw=24:"
                    f"borderw=4:bordercolor=0x000000"
                )

        elif is_ending:
            ending_file = f"{tmp_dir}/ending_text.txt"
            Path(ending_file).write_text(
                "チャンネル登録\nよろしく！\n\n毎日更新中！",
                encoding="utf-8",
            )
            ef = ending_file.replace("'", "\\'")
            chain += (
                f",drawtext=textfile='{ef}':fontfile='{fp}':"
                f"fontsize=100:fontcolor=0xFFD700:"
                f"x=(w-text_w)/2:y=760:"
                f"line_spacing=24:"
                f"box=1:boxcolor=0x000000@0.75:boxborderw=32:"
                f"borderw=4:bordercolor=0x000000"
            )

        else:
            # 通常字幕クリップ（下部パネル）
            text_file = f"{tmp_dir}/text_{idx:04d}.txt"
            Path(text_file).write_text(wrap_text(text), encoding="utf-8")
            tf = text_file.replace("'", "\\'")
            chain += (
                f",drawtext=textfile='{tf}':fontfile='{fp}':"
                f"fontsize={FONT_SIZE}:fontcolor=0xFFFFFF:"
                f"x=(w-text_w)/2:y=h-text_h-700:"
                f"line_spacing=14:"
                f"box=1:boxcolor=0x000014@0.88:boxborderw=36:"
                f"borderw=3:bordercolor=0x000014"
            )

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
    raw = [s.strip() for s in script.split("。") if s.strip()]
    sentences = [s + "。" for s in raw]

    if not sentences:
        print("  [警告] セリフが空です。スキップします。")
        return

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
                    thumb_top=thumb_top, thumb_main=thumb_main,
                )
            )
            print(f"  サムネイルフレーム: {thumb_duration:.2f}秒")

        # --- 字幕クリップ ---
        for i, (sentence, duration) in enumerate(zip(sentences, durations)):
            bg_img = assets_images[(i + 1) % len(assets_images)] if assets_images else None
            clip_paths.append(
                make_clip(i + 1, bg_img, sentence, duration, font_path, tmp_dir)
            )
            print(f"  [{i+1}/{len(sentences)}] {duration:.2f}s 「{sentence[:20]}」")

        # --- エンディングカード ---
        ending_bg = assets_images[len(sentences) % len(assets_images)] if assets_images else None
        clip_paths.append(
            make_clip(
                len(sentences) + 1, ending_bg, "", ENDING_DURATION, font_path, tmp_dir,
                is_ending=True,
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
        print("[エラー] assets/ai_*.jpg が見つかりません。", file=sys.stderr)
        print("  generate_images.py を先に実行してください。", file=sys.stderr)
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
