#!/usr/bin/env python3
"""名馬シリーズ用 前処理スクリプト

data/famous_horses/<horse_key>.txt の脚本を
既存パイプライン（generate_audio.py / generate_video.py）が
読めるフォーマットに変換するだけのシンプルなスクリプト。

- output/script_0.txt  ← 脚本テキスト
- news.json            ← タイトルのみ含む最小構造（generate_audio.py 用）
"""

import json
import sys
from pathlib import Path

OUTPUT_DIR = "output"
DATA_DIR   = "data/famous_horses"


def main() -> None:
    if len(sys.argv) < 2:
        print("使用法: python scripts/famous_horse_prepare.py <horse_key>", file=sys.stderr)
        sys.exit(1)

    horse_key = sys.argv[1]
    script_path = Path(f"{DATA_DIR}/{horse_key}.txt")
    meta_path   = Path(f"{DATA_DIR}/{horse_key}.json")

    if not script_path.exists():
        print(f"[エラー] 脚本が見つかりません: {script_path}", file=sys.stderr)
        sys.exit(1)

    script = script_path.read_text(encoding="utf-8").strip()
    meta   = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    horse_name  = meta.get("name", horse_key)
    catchphrase = meta.get("catchphrase", "")

    # 既存 generate_audio.py が期待する news.json（タイトルのみ使用）
    # タイトルはナレーションの冒頭に読み上げられる
    news_item = {
        "id": f"famous_horse_{horse_key}",
        "title": horse_name,
        "summary": catchphrase,
        "url": "",
        "image_url": None,
        "thumbnail_top":  meta.get("thumbnail_top", ""),
        "thumbnail_main": meta.get("thumbnail_main", ""),
    }
    Path("news.json").write_text(
        json.dumps([news_item], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 既存 generate_audio.py が読む output/script_0.txt
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    out_script = Path(f"{OUTPUT_DIR}/script_0.txt")
    out_script.write_text(script, encoding="utf-8")

    print(f"=== 名馬シリーズ 前処理完了 ===")
    print(f"  馬名: {horse_name}")
    print(f"  脚本: {len(script)} 文字 → {out_script}")
    print(f"  news.json: タイトル「{horse_name}」で生成")


if __name__ == "__main__":
    main()
