#!/usr/bin/env python3
"""名馬シリーズ用 動画生成スクリプト（ffmpegのみ・Pillow不使用）

# ============================================================
# IMPORTANT: Pillow (PIL) は絶対に使用禁止。
# 画像の生成・変換はすべて ffmpeg で行うこと。
# from PIL import ... / import PIL と書いたら即削除。
# ============================================================

流れ:
  1. output/famous_horse_audio.mp3 の尺を取得
  2. output/famous_horse_subtitles.ass にシリーズブランドとエンディングカードを追記
  3. ffmpeg で背景+字幕+BGMを合成して output/famous_horse_video.mp4 を出力

ニュース速報との違い:
  - 背景: 暖かみのあるダークブラウン (#12100A)
  - 効果: ビネット + フィルムグレイン（シネマチック）
  - テキスト: ゴールド字幕（ASSファイル経由）
  - BGM: ドラマチック/ノスタルジック系（bgm/horse_drama_bgm.mp3 優先）
"""

import glob
import os
import re
import subprocess
import sys
from pathlib import Path

OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
BGM_DIR    = f"{ASSETS_DIR}/bgm"

VIDEO_WIDTH  = 1080
VIDEO_HEIGHT = 1920
FPS          = 30
BGM_VOLUME   = 0.15   # ニュース(0.12)よりやや大きめ
ENDING_DURATION = 4.0  # エンディングカード表示秒数


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def find_font() -> str | None:
    """Noto CJK フォントファイルのパスを返す。"""
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


def get_audio_duration(audio_path: str) -> float:
    """音声ファイルの再生時間を秒で返す。"""
    try:
        from mutagen.mp3 import MP3
        dur = MP3(audio_path).info.length
        print(f"  音声尺 (mutagen): {dur:.2f}秒")
        return dur
    except Exception as e:
        print(f"  [警告] mutagen失敗: {e}")
    result = subprocess.run(["ffmpeg", "-i", audio_path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        dur = h * 3600 + mi * 60 + s
        print(f"  音声尺 (ffmpeg): {dur:.2f}秒")
        return dur
    print("  [警告] 音声尺取得失敗。30秒にフォールバック。")
    return 30.0


def secs_to_ass(t: float) -> str:
    """秒 → ASS時刻（H:MM:SS.cc）"""
    cs = round(t * 100)
    s, cs = divmod(cs, 100)
    m, s  = divmod(s, 60)
    h, m  = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def find_bgm() -> str | None:
    """BGMファイルを選択する（名馬シリーズ専用 → 通常BGMの順にフォールバック）。"""
    # 名馬シリーズ専用BGMを優先
    drama_files = sorted(glob.glob(f"{BGM_DIR}/horse_drama_bgm*.mp3"))
    if drama_files:
        print(f"  BGM (名馬専用): {drama_files[0]}")
        return drama_files[0]
    # フォールバック: calm piano (bgm_2) → 任意のBGM
    for fallback in [f"{BGM_DIR}/bgm_2.mp3", f"{BGM_DIR}/bgm_1.mp3"]:
        if Path(fallback).exists():
            print(f"  BGM (フォールバック): {fallback}")
            return fallback
    all_bgm = sorted(glob.glob(f"{BGM_DIR}/*.mp3"))
    if all_bgm:
        print(f"  BGM (汎用): {all_bgm[0]}")
        return all_bgm[0]
    return None


# ---------------------------------------------------------------------------
# ASS ファイル拡張（シリーズブランド・エンディングカード追加）
# ---------------------------------------------------------------------------

def extend_ass(ass_path: str, audio_duration: float, horse_name: str) -> None:
    """既存のASSファイルに以下を追記する:
    1. シリーズラベル TopBrand（動画全体を通して表示）
    2. エンディングカード（音声終了後 ENDING_DURATION 秒）
    """
    total_duration = audio_duration + ENDING_DURATION
    brand_start = secs_to_ass(0.0)
    brand_end   = secs_to_ass(total_duration)

    end_start = secs_to_ass(audio_duration + 0.3)
    end_end   = secs_to_ass(total_duration)

    with open(ass_path, "a", encoding="utf-8") as f:
        # シリーズラベル（常時・上部）
        f.write(
            f"Dialogue: 0,{brand_start},{brand_end},TopBrand,,0,0,0,,名馬列伝\n"
        )
        # エンディングカード
        f.write(
            f"Dialogue: 0,{end_start},{end_end},Default,,0,0,0,,"
            r"{\pos(540,880)\an5\fs68\c&H0000D7FF}"
            "チャンネル登録お願いします！\n"
        )
        f.write(
            f"Dialogue: 0,{end_start},{end_end},Default,,0,0,0,,"
            r"{\pos(540,990)\an5\fs50\c&H00FFFFFF}"
            "また次の名馬でお会いしましょう\n"
        )
        f.write(
            f"Dialogue: 0,{end_start},{end_end},Default,,0,0,0,,"
            r"{\pos(540,1100)\an5\fs40\c&H0060A8FF\alpha&H60}"
            "#名馬列伝 #競馬\n"
        )

    print(f"  ASSファイルにシリーズブランドとエンディングを追記しました。")
    print(f"  エンディング: {end_start} → {end_end}")


# ---------------------------------------------------------------------------
# 動画生成
# ---------------------------------------------------------------------------

def generate_video(
    audio_path: str,
    ass_path: str,
    output_path: str,
    horse_name: str,
) -> None:
    """ffmpegのみで名馬シリーズ動画を生成する。"""
    audio_duration = get_audio_duration(audio_path)
    total_duration = audio_duration + ENDING_DURATION + 0.5
    print(f"  音声: {audio_duration:.2f}秒 / 総尺: {total_duration:.2f}秒")

    extend_ass(ass_path, audio_duration, horse_name)

    font_path = find_font()
    bgm_path  = find_bgm()

    # --- ffmpegコマンド構築 ---
    cmd = ["ffmpeg", "-y"]

    # Input 0: 背景（暖かみのあるダークブラウン）
    cmd += [
        "-f", "lavfi",
        "-i", f"color=c=#12100A:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FPS}:d={total_duration}",
    ]

    # Input 1: ナレーション音声
    cmd += ["-i", audio_path]

    # Input 2: BGM（存在する場合のみ）
    if bgm_path:
        cmd += ["-stream_loop", "-1", "-i", bgm_path]

    # --- フィルターグラフ ---
    # 1. フィルムグレイン + ビネット（シネマチック感）
    video_chain = (
        "[0:v]"
        "noise=c0s=4:c0f=t+u,"        # 微細なフィルムグレイン
        "vignette=PI/3.5"              # 周辺減光
    )

    # 2. シリーズラベル（drawtext）
    if font_path:
        label = "名馬列伝"
        fp_esc = font_path.replace("'", "\\'")
        video_chain += (
            f",drawtext="
            f"fontfile='{fp_esc}':"
            f"text={label}:"
            f"fontsize=44:"
            f"fontcolor=0xC8A200@0.9:"
            f"x=(w-text_w)/2:"
            f"y=90:"
            f"box=1:"
            f"boxcolor=0x000000@0.55:"
            f"boxborderw=18"
        )

    # 3. ASS字幕（ナレーション + エンディングカード）
    ass_esc  = ass_path.replace("'", "\\'")
    font_dir = str(Path(font_path).parent) if font_path else ""
    if font_dir:
        fd_esc = font_dir.replace("'", "\\'")
        video_chain += f",subtitles='{ass_esc}':fontsdir='{fd_esc}'"
    else:
        video_chain += f",subtitles='{ass_esc}'"

    video_chain += "[vout]"

    # BGM有り: ナレーション + BGMをミックス
    if bgm_path:
        audio_filter = (
            f"[1:a]volume=1.0[narr];"
            f"[2:a]volume={BGM_VOLUME}[bgm];"
            f"[narr][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        filter_complex = video_chain + ";" + audio_filter
        audio_map = "[aout]"
        print(f"  BGMミックス: {bgm_path} (volume={BGM_VOLUME})")
    else:
        filter_complex = video_chain
        audio_map = "1:a"
        print("  BGMなし: ナレーション音声のみ")

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", audio_map,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(total_duration),
        output_path,
    ]

    print("  ffmpeg 実行中...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[エラー] ffmpeg 失敗:\n{result.stderr[-3000:]}", file=sys.stderr)
        sys.exit(1)

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"  動画生成完了: {output_path} ({size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# サムネイル生成（ffmpegでフレーム抽出）
# ---------------------------------------------------------------------------

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

    # Step 1: フレーム抽出（1280x720）
    extract = subprocess.run([
        "ffmpeg", "-y", "-ss", "1", "-i", video_path,
        "-vframes", "1", "-s", "1280x720", "-f", "image2", tmp_raw,
    ], capture_output=True, text=True)
    if extract.returncode != 0 or not Path(tmp_raw).exists():
        print(f"  [警告] フレーム抽出失敗", file=sys.stderr)
        return False

    if not horse_name or not font_path:
        Path(tmp_raw).rename(thumb_path)
        size_kb = Path(thumb_path).stat().st_size // 1024
        print(f"  サムネイル生成完了: {thumb_path} ({size_kb} KB)")
        return True

    # Step 2: drawtext でタイトルオーバーレイ
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

    # 「名馬列伝」シリーズラベル（上部・ゴールド）
    chain += (
        f",drawtext=textfile='{lf}':fontfile='{fp}':"
        f"fontsize=46:fontcolor=0xC8A200:"
        f"x=(w-text_w)/2:y=36:"
        f"box=1:boxcolor=0x000000@0.65:boxborderw=18:"
        f"borderw=2:bordercolor=0x000000"
    )

    # 馬名（中央より上・大きく黄色）
    chain += (
        f",drawtext=textfile='{nf}':fontfile='{fp}':"
        f"fontsize=130:fontcolor=0xFFEB00:"
        f"x=(w-text_w)/2:y=230:"
        f"box=1:boxcolor=0x000000@0.72:boxborderw=32:"
        f"borderw=5:bordercolor=0x000000"
    )

    # キャッチフレーズ（馬名の下・白）
    if catchphrase:
        chain += (
            f",drawtext=textfile='{cf}':fontfile='{fp}':"
            f"fontsize=48:fontcolor=0xFFFFFF:"
            f"x=(w-text_w)/2:y=440:"
            f"box=1:boxcolor=0x000000@0.60:boxborderw=18:"
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


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("使用法: python scripts/famous_horse_video.py <horse_key> [horse_display_name]",
              file=sys.stderr)
        print("例:     python scripts/famous_horse_video.py silport シルポート",
              file=sys.stderr)
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
