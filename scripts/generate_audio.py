#!/usr/bin/env python3
"""output/script_N.txt を読み込み、音声(audio_N.mp3)とASS字幕(subtitles_N.ass)を生成する。"""

import asyncio
import json
import re
import sys
import time
from pathlib import Path

import edge_tts

import os

OUTPUT_DIR = "output"
NEWS_JSON = "news.json"
# 環境変数 TTS_VOICE で上書き可能（名馬シリーズ等で別ボイスを使う場合）
VOICE = os.environ.get("TTS_VOICE", "ja-JP-KeitaNeural")
RATE  = os.environ.get("TTS_RATE",  "+0%")
VOLUME = "+0%"

# ASS字幕ファイルのヘッダーテンプレート（PlayResX/Y=実際の動画サイズ）
ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},58,&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,1,4,1,2,20,20,80,1

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


def words_to_segments(words: list[dict], max_chars: int = 22) -> list[dict]:
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
            end = ticks_to_ass_time(seg["end"])
            text = seg["text"].replace("\n", "\\N")
            f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")


def detect_font_name() -> str:
    """インストール済みのNoto CJKフォント名を返す。"""
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return "Noto Sans CJK JP"
    return "Sans"


async def generate_audio_and_subtitles(
    script: str, audio_path: str, ass_path: str, font_name: str
) -> None:
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

    segments = words_to_segments(words)
    write_ass(segments, ass_path, font_name)

    size_kb = Path(audio_path).stat().st_size // 1024
    print(f"  音声: {audio_path} ({size_kb} KB)")
    print(f"  字幕: {ass_path} ({len(segments)} セグメント)")


def main() -> None:
    print("=== 音声生成開始 ===")

    script_files = sorted(Path(OUTPUT_DIR).glob("script_*.txt"))
    if not script_files:
        print(f"[エラー] {OUTPUT_DIR}/script_*.txt が見つかりません。", file=sys.stderr)
        sys.exit(1)

    # news.json からタイトルを取得（サムネイルタイトルの読み上げ用）
    news_path = Path(NEWS_JSON)
    news_items: list[dict] = json.loads(news_path.read_text(encoding="utf-8")) if news_path.exists() else []

    font_name = detect_font_name()
    print(f"字幕フォント: {font_name}")

    for script_file in script_files:
        idx = script_file.stem.split("_")[1]
        script = script_file.read_text(encoding="utf-8").strip()
        if not script:
            print(f"  [警告] {script_file} が空です。スキップします。")
            continue

        # タイトルをナレーションの先頭に追加
        idx_int = int(idx)
        title = news_items[idx_int].get("title", "") if idx_int < len(news_items) else ""
        if title:
            narration_text = title + "。" + script
            print(f"  タイトル読み上げ追加: 「{title[:40]}」")
        else:
            narration_text = script

        audio_path = f"{OUTPUT_DIR}/audio_{idx}.mp3"
        ass_path = f"{OUTPUT_DIR}/subtitles_{idx}.ass"

        print(f"\n--- 音声生成 [{idx}] ({len(narration_text)}文字) ---")
        for attempt in range(1, 4):
            try:
                asyncio.run(generate_audio_and_subtitles(narration_text, audio_path, ass_path, font_name))
                break
            except Exception as e:
                print(f"  [警告] 音声生成失敗 (attempt {attempt}/3): {e}", file=sys.stderr)
                if attempt < 3:
                    time.sleep(10 * attempt)
                else:
                    print(f"[エラー] 音声生成を3回試みましたが失敗しました。", file=sys.stderr)
                    sys.exit(1)

    print(f"\n{len(script_files)} 件の音声を生成しました。")


if __name__ == "__main__":
    main()
