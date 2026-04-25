#!/usr/bin/env python3
"""競馬知識クイズ動画パイプライン: Step 1-3 を順番に実行"""

import subprocess
import sys
from pathlib import Path


def run_step(name: str, script: str):
    print(f"\n{'='*50}")
    print(f"  {name}")
    print(f"{'='*50}")
    script_path = Path(__file__).parent / script
    result = subprocess.run(
        [sys.executable, str(script_path)],
        check=False,
    )
    if result.returncode != 0:
        print(f"\nERROR: {name} が失敗しました (終了コード {result.returncode})")
        sys.exit(result.returncode)


def main():
    print("=== 競馬知識クイズ動画パイプライン ===")

    run_step("Step 1: クイズ問題生成 (Claude API)", "quiz_step1_questions.py")
    run_step("Step 2: スライド生成 (matplotlib)", "quiz_step2_slides.py")
    run_step("Step 3: 動画生成 (edge-tts + ffmpeg)", "quiz_step3_video.py")

    print("\n" + "="*50)
    print("  全ステップ完了！")
    print("="*50)
    print("出力: quiz_video.mp4")


if __name__ == "__main__":
    main()
