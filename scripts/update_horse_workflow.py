#!/usr/bin/env python3
"""data/famous_horses/ の JSON ファイルを読み取り、
famous_horse_series.yml の horse_key ドロップダウンを自動更新するスクリプト。

新しい馬ファイルを追加すると GitHub Actions が自動でこのスクリプトを実行し、
ワークフローのドロップダウンに反映される。
"""

import re
import sys
from pathlib import Path

WORKFLOW_PATH = ".github/workflows/famous_horse_series.yml"
DATA_DIR = "data/famous_horses"


def main() -> None:
    keys = sorted(p.stem for p in Path(DATA_DIR).glob("*.json"))
    if not keys:
        print("[エラー] data/famous_horses/ に JSON ファイルがありません。", file=sys.stderr)
        sys.exit(1)

    workflow = Path(WORKFLOW_PATH).read_text(encoding="utf-8")

    # options ブロックを新しいキー一覧で置換
    options_lines = "\n".join(f"          - {k}" for k in keys)
    new_block = f"        options:\n{options_lines}\n"

    updated = re.sub(
        r"        options:\n(          - .+\n)+",
        new_block,
        workflow,
    )

    if updated == workflow:
        print("変更なし（オプションはすでに最新です）")
        return

    Path(WORKFLOW_PATH).write_text(updated, encoding="utf-8")
    print(f"ドロップダウンを更新しました（{len(keys)} 頭）:")
    for k in keys:
        print(f"  - {k}")


if __name__ == "__main__":
    main()
