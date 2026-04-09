#!/usr/bin/env python3
"""名馬シリーズ用 音声・字幕生成スクリプト

data/famous_horses/<horse_key>.txt を読み込み、ナレーション音声と
ASS字幕ファイルを生成する。

ニュース速報シリーズとの違い:
  - ボイス: ja-JP-NaokiNeural（落ち着いたナレーター系）
  - レート: -5%（ドラマチックにやや遅め）
  - 字幕スタイル: ゴールド系（ニュースの白より温かみのある色）
"""

import asyncio
import re
import sys
import time
from pathlib import Path

import edge_tts

# ニュース速報(ja-JP-KeitaNeural)とは異なるナレーターボイス
VOICE  = "ja-JP-NaokiNeural"
RATE   = "-5%"
VOLUME = "+0%"

OUTPUT_DIR = "output"

# 名馬シリーズ用ASSスタイル
# PrimaryColour &H0000D7FF = #FFD700(ゴールド) in ASS AABBGGRR 形式
# BackColour    &H80080808 = 半透明ダーク
ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},62,&H0000D7FF,&H000000FF,&H00000000,&H80080808,1,0,0,0,100,100,2,0,1,5,3,2,30,30,130,1
Style: TopBrand,{font_name},42,&H0000A2C8,&H000000FF,&H00000000,&H90000000,1,0,0,0,100,100,0,0,1,3,0,8,30,30,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ticks_to_ass_time(ticks: int) -> str:
    """100ナノ秒単位 → ASS時刻（H:MM:SS.cc）"""
    cs = ticks // 100_000
    s, cs = divmod(cs, 100)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def words_to_segments(words: list[dict], max_chars: int = 20) -> list[dict]:
    """ワード境界リスト → 字幕セグメントリスト"""
    segments: list[dict] = []
    current: list[dict] = []

    for word in words:
        current.append(word)
        text_so_far = "".join(w["text"] for w in current)
        ends = bool(re.search(r"[。！？\n]$", word["text"]))
        if ends or len(text_so_far) >= max_chars:
            text = "".join(w["text"] for w in current).strip()
            if text:
                segments.append({
                    "start": current[0]["offset"],
                    "end": current[-1]["offset"] + current[-1]["duration"],
                    "text": text,
                })
            current = []

    if current:
        text = "".join(w["text"] for w in current).strip()
        if text:
            segments.append({
                "start": current[0]["offset"],
                "end": current[-1]["offset"] + current[-1]["duration"],
                "text": text,
            })
    return segments


def write_ass(segments: list[dict], path: str, font_name: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER.format(font_name=font_name))
        for seg in segments:
            start = ticks_to_ass_time(seg["start"])
            end   = ticks_to_ass_time(seg["end"])
            text  = seg["text"].replace("\n", "\\N")
            f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")


def detect_font_name() -> str:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return "Noto Sans CJK JP"
    return "Sans"


async def _generate(script: str, audio_path: str) -> list[dict]:
    communicate = edge_tts.Communicate(script, VOICE, rate=RATE, volume=VOLUME)
    words: list[dict] = []
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "text": chunk["text"],
                    "offset": chunk["offset"],
                    "duration": chunk["duration"],
                })
    return words


def main() -> None:
    if len(sys.argv) < 2:
        print("使用法: python scripts/famous_horse_audio.py <horse_key>", file=sys.stderr)
        print("例:     python scripts/famous_horse_audio.py silport", file=sys.stderr)
        sys.exit(1)

    horse_key   = sys.argv[1]
    script_path = Path(f"data/famous_horses/{horse_key}.txt")
    if not script_path.exists():
        print(f"[エラー] スクリプトファイルが見つかりません: {script_path}", file=sys.stderr)
        sys.exit(1)

    script = script_path.read_text(encoding="utf-8").strip()
    if not script:
        print(f"[エラー] スクリプトファイルが空です: {script_path}", file=sys.stderr)
        sys.exit(1)

    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    font_name  = detect_font_name()
    audio_path = f"{OUTPUT_DIR}/famous_horse_audio.mp3"
    ass_path   = f"{OUTPUT_DIR}/famous_horse_subtitles.ass"

    print("=== 名馬シリーズ 音声生成開始 ===")
    print(f"  馬名キー : {horse_key}")
    print(f"  ボイス   : {VOICE} (rate={RATE})")
    print(f"  字幕フォント: {font_name}")
    print(f"  スクリプト: {len(script)} 文字")

    for attempt in range(1, 4):
        try:
            words = asyncio.run(_generate(script, audio_path))
            segments = words_to_segments(words)
            write_ass(segments, ass_path, font_name)
            size_kb = Path(audio_path).stat().st_size // 1024
            print(f"  音声: {audio_path} ({size_kb} KB)")
            print(f"  字幕: {ass_path} ({len(segments)} セグメント)")
            break
        except Exception as e:
            print(f"  [警告] 音声生成失敗 (attempt {attempt}/3): {e}", file=sys.stderr)
            if attempt < 3:
                time.sleep(10 * attempt)
            else:
                print("[エラー] 音声生成を3回試みましたが失敗しました。", file=sys.stderr)
                sys.exit(1)

    print("=== 音声生成完了 ===")


if __name__ == "__main__":
    main()
