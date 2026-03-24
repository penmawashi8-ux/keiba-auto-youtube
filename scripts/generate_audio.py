#!/usr/bin/env python3
"""script.txtを読み込みedge-ttsで音声ファイルを生成する。"""

import asyncio
import sys
from pathlib import Path

import edge_tts

SCRIPT_TXT = "script.txt"
OUTPUT_DIR = "output"
OUTPUT_AUDIO = f"{OUTPUT_DIR}/audio.mp3"

VOICE = "ja-JP-KeitaNeural"
RATE = "+0%"
VOLUME = "+0%"


async def generate_audio(script: str) -> None:
    print(f"edge-tts で音声合成中（声: {VOICE}）...")
    communicate = edge_tts.Communicate(script, VOICE, rate=RATE, volume=VOLUME)
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    try:
        await communicate.save(OUTPUT_AUDIO)
    except Exception as e:
        print(f"[エラー] edge-tts 失敗: {e}", file=sys.stderr)
        sys.exit(1)
    size_kb = Path(OUTPUT_AUDIO).stat().st_size / 1024
    print(f"音声ファイルを {OUTPUT_AUDIO} に保存しました（{size_kb:.1f} KB）。")


def main() -> None:
    print("=== 音声生成開始 ===")

    script_path = Path(SCRIPT_TXT)
    if not script_path.exists():
        print(f"[エラー] {SCRIPT_TXT} が見つかりません。generate_script.py を先に実行してください。", file=sys.stderr)
        sys.exit(1)

    script = script_path.read_text(encoding="utf-8").strip()
    if not script:
        print(f"[エラー] {SCRIPT_TXT} が空です。", file=sys.stderr)
        sys.exit(1)

    print(f"脚本文字数: {len(script)} 文字")
    asyncio.run(generate_audio(script))


if __name__ == "__main__":
    main()
