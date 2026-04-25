#!/usr/bin/env python3
"""最強ランキング動画 全ステップ自動実行スクリプト

使い方:
  python scripts/ranking_pipeline.py              # 全ステップ実行
  python scripts/ranking_pipeline.py --from 3     # Step 3 から再開
  python scripts/ranking_pipeline.py --steps 1,2  # Step 1,2 のみ実行
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path


STEPS = [
    (1, "netkeiba G1勝利数ランキング取得",   "scripts/ranking_step1_fetch_horses.py"),
    (2, "レース成績取得",                     "scripts/ranking_step2_fetch_results.py"),
    (3, "スコアリング",                        "scripts/ranking_step3_scoring.py"),
    (4, "グラフ生成",                          "scripts/ranking_step4_graphs.py"),
    (5, "台本生成",                            "scripts/ranking_step5_script.py"),
    (6, "動画生成",                            "scripts/ranking_step6_video.py"),
]

# 各ステップで生成されるファイル（存在確認に使用）
STEP_OUTPUTS = {
    1: ["horses.csv"],
    2: ["results.csv"],
    3: ["ranking.csv"],
    4: ["graphs/01_bar.png", "graphs/02_radar.png", "graphs/03_compare.png"],
    5: ["script.txt"],
    6: ["final_output.mp4"],
}


def print_banner():
    print("=" * 60)
    print("  歴代最強馬ランキング動画 自動生成パイプライン")
    print("=" * 60)


def run_step(step_num, desc, script_path):
    print(f"\n{'─'*60}")
    print(f"▶ Step {step_num}: {desc}")
    print(f"{'─'*60}")
    start = time.time()

    result = subprocess.run([sys.executable, script_path])

    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"\n✗ Step {step_num} 失敗 ({elapsed:.1f}秒)")
        print(f"  スクリプト: {script_path}")
        print(f"  再開するには: python scripts/ranking_pipeline.py --from {step_num}")
        return False

    # 出力ファイルの存在確認
    missing = [f for f in STEP_OUTPUTS.get(step_num, []) if not Path(f).exists()]
    if missing:
        print(f"\n⚠ Step {step_num} 完了しましたが、以下のファイルが見つかりません:")
        for m in missing:
            print(f"  - {m}")
        return False

    print(f"\n✓ Step {step_num} 完了 ({elapsed:.1f}秒)")
    return True


def check_prerequisites():
    """ffmpeg の存在確認"""
    import shutil
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg (sudo apt install ffmpeg)")
    if missing:
        print("ERROR: 以下のコマンドが見つかりません:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)


def main():
    print_banner()

    parser = argparse.ArgumentParser(description="最強ランキング動画生成パイプライン")
    parser.add_argument("--from", dest="from_step", type=int, default=1,
                        help="開始ステップ番号 (1-6)")
    parser.add_argument("--steps", type=str, default=None,
                        help="実行するステップをカンマ区切りで指定 (例: 1,2,3)")
    args = parser.parse_args()

    check_prerequisites()

    if args.steps:
        target_steps = set(int(s.strip()) for s in args.steps.split(","))
        steps_to_run = [(n, d, s) for n, d, s in STEPS if n in target_steps]
    else:
        steps_to_run = [(n, d, s) for n, d, s in STEPS if n >= args.from_step]

    if not steps_to_run:
        print("実行するステップがありません")
        sys.exit(1)

    print(f"\n実行予定ステップ: {', '.join(f'Step{n}' for n, _, _ in steps_to_run)}")

    total_start = time.time()
    failed = False

    for step_num, desc, script_path in steps_to_run:
        ok = run_step(step_num, desc, script_path)
        if not ok:
            failed = True
            break

    total_elapsed = time.time() - total_start

    print(f"\n{'='*60}")
    if failed:
        print(f"✗ パイプライン失敗 (経過時間: {total_elapsed/60:.1f}分)")
        sys.exit(1)
    else:
        print(f"✓ パイプライン完了 (経過時間: {total_elapsed/60:.1f}分)")
        if Path("final_output.mp4").exists():
            size_mb = Path("final_output.mp4").stat().st_size / (1024 * 1024)
            print(f"\n  出力: final_output.mp4 ({size_mb:.1f}MB)")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
