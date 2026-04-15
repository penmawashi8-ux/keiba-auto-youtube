#!/usr/bin/env python3
"""名馬シリーズ用 動画生成スクリプト（ffmpegのみ・Pillow不使用）

# ============================================================
# IMPORTANT: Pillow (PIL) は絶対に使用禁止。
# 画像の生成・変換はすべて ffmpeg で行うこと。
# from PIL import ... / import PIL と書いたら即削除。
# ============================================================

subtitlesフィルターの代わりにセグメントごとのクリップ+drawtext方式を採用。
BGMはCC0フリー素材（Musopen / archive.org環境音）を使用。
"""

import glob
import re
import shutil
import subprocess
import sys
from pathlib import Path

OUTPUT_DIR     = "output"
ASSETS_DIR     = "assets"
BGM_DIR        = f"{ASSETS_DIR}/bgm"
TMP_DIR        = "/tmp/famous_horse_tmp"

VIDEO_WIDTH    = 1080
VIDEO_HEIGHT   = 1920
FPS            = 30
FONT_SIZE      = 64
BGM_VOLUME     = 0.15
ENDING_DUR     = 4.0
LINE_MAX_CHARS = 13   # fontsize=64 × 13chars = 832px + border60 = 892px < 1080px
LABEL_TEXT     = "名馬列伝"
PANEL_Y        = 1180   # 字幕パネルの上端Y座標
PANEL_H        = 360    # 字幕パネルの高さ


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


def get_audio_duration(audio_path: str) -> float:
    try:
        from mutagen.mp3 import MP3
        return MP3(audio_path).info.length
    except Exception:
        pass
    result = subprocess.run(["ffmpeg", "-i", audio_path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + s
    return 30.0


def ass_time_to_secs(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_ass_segments(ass_path: str) -> list[dict]:
    """ASSのDefault styleダイアログのみ解析してセグメントリストを返す。"""
    pattern = re.compile(
        r"^Dialogue:\s*\d+,"
        r"([^,]+),"    # start
        r"([^,]+),"    # end
        r"(Default),"  # style
        r"[^,]*,\d+,\d+,\d+,[^,]*,"
        r"(.+)$"       # text
    )
    segs = []
    with open(ass_path, encoding="utf-8") as f:
        for line in f:
            m = pattern.match(line.strip())
            if not m:
                continue
            start = ass_time_to_secs(m.group(1).strip())
            end   = ass_time_to_secs(m.group(2).strip())
            raw   = m.group(4).strip()
            text  = re.sub(r"\{[^}]*\}", "", raw)
            text  = text.replace("\\N", "\n").replace("\\n", "\n").strip()
            if text:
                segs.append({"start": start, "end": end, "text": text})
    segs.sort(key=lambda s: s["start"])
    return segs


def wrap_text(text: str) -> str:
    lines = []
    for para in text.split("\n"):
        while len(para) > LINE_MAX_CHARS:
            lines.append(para[:LINE_MAX_CHARS])
            para = para[LINE_MAX_CHARS:]
        if para:
            lines.append(para)
    return "\n".join(lines)


def find_bg_images() -> list[str]:
    ai = sorted(p for p in glob.glob(f"{ASSETS_DIR}/ai_*.jpg") if Path(p).stat().st_size > 10_000)
    bg = sorted(p for p in glob.glob(f"{ASSETS_DIR}/bg_*.jpg") if Path(p).stat().st_size > 10_000)
    return ai or bg


def find_bgm() -> str | None:
    for c in [f"{BGM_DIR}/horse_drama_bgm.mp3", f"{BGM_DIR}/bgm_2.mp3", f"{BGM_DIR}/bgm_1.mp3"]:
        if Path(c).exists():
            return c
    all_bgm = sorted(glob.glob(f"{BGM_DIR}/*.mp3"))
    return all_bgm[0] if all_bgm else None


def make_clip(
    idx: int,
    bg_img: str | None,
    text: str,
    duration: float,
    font_path: str | None,
    tmp_dir: str,
    is_ending: bool = False,
) -> str:
    """1セグメント分のMP4クリップを生成して返す。"""
    clip_path   = f"{tmp_dir}/clip_{idx:04d}.mp4"
    label_file  = f"{tmp_dir}/label.txt"
    text_file   = f"{tmp_dir}/text_{idx:04d}.txt"
    duration    = max(duration, 0.5)

    Path(label_file).write_text(LABEL_TEXT, encoding="utf-8")

    if is_ending:
        Path(text_file).write_text(
            "チャンネル登録お願いします！\nまた次の名馬でお会いしましょう",
            encoding="utf-8",
        )
    else:
        Path(text_file).write_text(wrap_text(text), encoding="utf-8")

    cmd = ["ffmpeg", "-y"]

    if bg_img and Path(bg_img).exists():
        cmd += ["-loop", "1", "-i", bg_img]
        base = (
            f"[0:v]"
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            f"eq=brightness=-0.04,"
            f"vignette=PI/5"
        )
    else:
        cmd += ["-f", "lavfi", "-i",
                f"color=c=#2A1F14:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FPS}"]
        base = "[0:v]vignette=PI/3.5"

    chain = base

    if font_path:
        lf = label_file.replace("'", "\\'")
        tf = text_file.replace("'", "\\'")
        fp = font_path.replace("'", "\\'")

        # シリーズラベル（上部 y=150: スマホのUI被りを避ける）
        chain += (
            f",drawtext=textfile='{lf}':fontfile='{fp}':"
            f"fontsize=42:fontcolor=0xC8A200@0.95:"
            f"x=(w-text_w)/2:y=150:"
            f"box=1:boxcolor=0x000000@0.75:boxborderw=18"
        )

        if is_ending:
            # エンディング（中央）
            chain += (
                f",drawtext=textfile='{tf}':fontfile='{fp}':"
                f"fontsize=62:fontcolor=0xFFD700:"
                f"x=(w-text_w)/2:y=870:"
                f"line_spacing=20:"
                f"box=1:boxcolor=0x000000@0.75:boxborderw=24"
            )
        else:
            # 字幕テキスト（下部・drawtext box方式）
            # drawbox は環境依存で失敗するため使わない
            chain += (
                f",drawtext=textfile='{tf}':fontfile='{fp}':"
                f"fontsize={FONT_SIZE}:fontcolor=0xFFD700:"
                f"x=(w-text_w)/2:y=h-text_h-700:"
                f"line_spacing=14:"
                f"box=1:boxcolor=0x080808@0.82:boxborderw=30"
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
        print(f"  [警告] クリップ{idx}生成失敗:\n{result.stderr[-800:]}", file=sys.stderr)
        # フォールバック: 単色クリップ
        fb = [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c=#2A1F14:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FPS}:d={duration}",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-t", str(duration), clip_path,
        ]
        subprocess.run(fb, check=True, capture_output=True)

    return clip_path


def generate_video(audio_path: str, ass_path: str, output_path: str, horse_name: str) -> None:
    audio_duration = get_audio_duration(audio_path)
    print(f"  音声: {audio_duration:.2f}秒 / 総尺: {audio_duration + ENDING_DUR:.2f}秒")

    segments  = parse_ass_segments(ass_path)
    font_path = find_font()
    bg_images = find_bg_images()
    bgm_path  = find_bgm()

    print(f"  字幕セグメント: {len(segments)} 件")
    print(f"  フォント: {font_path}")
    print(f"  背景画像: {len(bg_images)} 枚")
    print(f"  BGM: {bgm_path}")

    tmp_dir = TMP_DIR
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)

    clip_paths: list[str] = []

    for i, seg in enumerate(segments):
        duration = max(seg["end"] - seg["start"], 0.5)
        bg_img   = bg_images[i % len(bg_images)] if bg_images else None
        clip     = make_clip(i, bg_img, seg["text"], duration, font_path, tmp_dir)
        clip_paths.append(clip)
        print(f"  [{i+1}/{len(segments)}] {duration:.2f}s 「{seg['text'][:20].replace(chr(10),' ')}」")

    # エンディングカード
    ending_bg = bg_images[len(segments) % len(bg_images)] if bg_images else None
    clip_paths.append(
        make_clip(len(segments), ending_bg, "", ENDING_DUR, font_path, tmp_dir, is_ending=True)
    )
    print(f"  エンディング: {ENDING_DUR}秒")

    # concat
    concat_path = f"{tmp_dir}/concat.txt"
    with open(concat_path, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")

    silent_path = f"{tmp_dir}/silent.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        silent_path,
    ], check=True, capture_output=True)
    print("  無音動画 concat 完了")

    # 音声 + BGM ミックス
    cmd = ["ffmpeg", "-y", "-i", silent_path, "-i", audio_path]
    if bgm_path:
        cmd += ["-stream_loop", "-1", "-i", bgm_path]
        cmd += [
            "-filter_complex",
            f"[1:a][2:a]amix=inputs=2:duration=first:weights=1 {BGM_VOLUME}[aout]",
            "-map", "0:v", "-map", "[aout]",
        ]
        print(f"  BGMミックス: {bgm_path} (volume={BGM_VOLUME})")
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
        print("  BGMなし: ナレーション音声のみ")

    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", output_path]
    subprocess.run(cmd, check=True, capture_output=True)

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"  動画生成完了: {output_path} ({size_mb:.1f} MB)")
    shutil.rmtree(tmp_dir, ignore_errors=True)


def generate_thumbnail(
    video_path: str,
    thumb_path: str,
    horse_name: str = "",
    catchphrase: str = "",
    font_path: str | None = None,
) -> bool:
    """動画の1秒地点からフレームを抽出し、馬名・キャッチフレーズを重ねてサムネイルを生成する。
    Pillow は絶対に使用しない。ffmpeg drawtext のみで合成する。
    """
    tmp_raw = thumb_path + ".raw.jpg"

    # Step 1: フレーム抽出（1080x1920 縦向き・動画と同じアスペクト比）
    extract = subprocess.run([
        "ffmpeg", "-y", "-ss", "1", "-i", video_path,
        "-vframes", "1", "-s", "1080x1920", "-f", "image2", tmp_raw,
    ], capture_output=True, text=True)
    if extract.returncode != 0 or not Path(tmp_raw).exists():
        print("  [警告] フレーム抽出失敗", file=sys.stderr)
        return False

    if not horse_name or not font_path:
        Path(tmp_raw).rename(thumb_path)
        size_kb = Path(thumb_path).stat().st_size // 1024
        print(f"  サムネイル生成完了: {thumb_path} ({size_kb} KB)")
        return True

    # Step 2: drawtext でタイトルオーバーレイ（1080x1920 縦向き用レイアウト）
    tmp_dir = "/tmp/famous_horse_thumb"
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)

    label_file = f"{tmp_dir}/label.txt"
    name_file  = f"{tmp_dir}/name.txt"
    catch_file = f"{tmp_dir}/catch.txt"

    Path(label_file).write_text("名馬列伝", encoding="utf-8")
    Path(name_file).write_text(horse_name, encoding="utf-8")
    if catchphrase:
        Path(catch_file).write_text(f"〜{catchphrase}〜", encoding="utf-8")

    fp = font_path.replace("'", "\\'")
    lf = label_file.replace("'", "\\'")
    nf = name_file.replace("'", "\\'")
    cf = catch_file.replace("'", "\\'")

    # 全体を少し暗く
    chain = "[0:v]eq=brightness=-0.12"

    # 「名馬列伝」シリーズラベル（y=160: スマホUI被りを避ける）
    chain += (
        f",drawtext=textfile='{lf}':fontfile='{fp}':"
        f"fontsize=52:fontcolor=0xC8A200:"
        f"x=(w-text_w)/2:y=160:"
        f"box=1:boxcolor=0x000000@0.75:boxborderw=22:"
        f"borderw=2:bordercolor=0x000000"
    )

    # 馬名（縦中央付近・大きく黄色・fontsize=100でoverflow防止）
    chain += (
        f",drawtext=textfile='{nf}':fontfile='{fp}':"
        f"fontsize=100:fontcolor=0xFFEB00:"
        f"x=(w-text_w)/2:y=680:"
        f"box=1:boxcolor=0x000000@0.72:boxborderw=28:"
        f"borderw=4:bordercolor=0x000000"
    )

    # キャッチフレーズ（馬名の下・白）
    if catchphrase:
        chain += (
            f",drawtext=textfile='{cf}':fontfile='{fp}':"
            f"fontsize=50:fontcolor=0xFFFFFF:"
            f"x=(w-text_w)/2:y=840:"
            f"box=1:boxcolor=0x000000@0.65:boxborderw=20:"
            f"borderw=2:bordercolor=0x000000"
        )

    chain += "[vout]"

    overlay = subprocess.run([
        "ffmpeg", "-y", "-i", tmp_raw,
        "-filter_complex", chain,
        "-map", "[vout]",
        "-frames:v", "1", "-q:v", "2",
        thumb_path,
    ], capture_output=True, text=True)

    Path(tmp_raw).unlink(missing_ok=True)

    if overlay.returncode == 0 and Path(thumb_path).exists():
        size_kb = Path(thumb_path).stat().st_size // 1024
        print(f"  サムネイル生成完了: {thumb_path} ({size_kb} KB)")
        return True

    print(f"  [警告] サムネイルテキスト合成失敗: {overlay.stderr[-300:]}", file=sys.stderr)
    return False


def main() -> None:
    if len(sys.argv) < 2:
        print("使用法: python scripts/famous_horse_video.py <horse_key> [horse_name]", file=sys.stderr)
        sys.exit(1)

    horse_key  = sys.argv[1]
    horse_name = sys.argv[2] if len(sys.argv) > 2 else horse_key

    # メタデータからキャッチフレーズを取得
    import json
    meta_path = Path(f"data/famous_horses/{horse_key}.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    catchphrase = meta.get("catchphrase", "")

    audio_path = f"{OUTPUT_DIR}/famous_horse_audio.mp3"
    ass_path   = f"{OUTPUT_DIR}/famous_horse_subtitles.ass"
    video_path = f"{OUTPUT_DIR}/famous_horse_video.mp4"
    thumb_path = f"{OUTPUT_DIR}/famous_horse_thumbnail.jpg"

    for p in [audio_path, ass_path]:
        if not Path(p).exists():
            print(f"[エラー] ファイルが見つかりません: {p}", file=sys.stderr)
            sys.exit(1)

    print("=== 名馬シリーズ 動画生成開始 ===")
    print(f"  馬名: {horse_name} (key={horse_key})")
    print(f"  キャッチフレーズ: {catchphrase}")

    generate_video(audio_path, ass_path, video_path, horse_name)
    font_path = find_font()
    generate_thumbnail(video_path, thumb_path, horse_name, catchphrase, font_path)

    print("=== 動画生成完了 ===")


if __name__ == "__main__":
    main()
