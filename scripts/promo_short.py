#!/usr/bin/env python3
"""本編（横動画）から宣伝用の縦ショート（1080×1920）を作る。

本編を倍速にして中央に置き、背景はぼかした本編、上に見出し、下に案内の文字を入れる。
音声は atempo で倍速（声の高さは変えない）。
サムネイルは動画のフレーム抽出で作る（縦横比はそのまま。CLAUDE.md のルール）。

設定: data/promo/<名前>.json
  {"source": "sim/real_race.mp4"（research-output ブランチ上のパス）, "prefix": 出力名, "speed": 2.0,
   "top": [見出しの行...], "bottom": [案内の行...], "max_sec": 175}
出力: sim/<prefix>_short.mp4, sim/<prefix>_short_thumb.jpg

使い方: python scripts/promo_short.py data/promo/tenno_aki2026.json 入力動画.mp4
Pillow / numpy / drawbox は使わない（プロジェクトルール）。
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sim_dream_race import find_font  # noqa: E402

W, H = 1080, 1920
GOLD = "0xF5C542"
SIM_DIR = Path("sim")


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    src = Path(sys.argv[2])
    prefix, speed = cfg["prefix"], float(cfg.get("speed", 2.0))
    work = SIM_DIR / f"work_{prefix}_short"
    work.mkdir(parents=True, exist_ok=True)
    font = find_font()
    texts = []

    def dt(text, y, size, color, box=None):
        p = work / f"t{len(texts)}.txt"
        p.write_text(text, encoding="utf-8")
        s = (f"drawtext=fontfile='{font}':textfile='{p}':x=(w-text_w)/2:y={y}:fontsize={size}:fontcolor={color}"
             f":borderw=4:bordercolor=black@0.8")
        if box:
            s += f":box=1:boxcolor={box}:boxborderw=16"
        texts.append(s)

    y = 150
    for j, line in enumerate(cfg["top"]):
        size = [100, 62, 46][min(j, 2)]
        dt(line, y, size, GOLD if j == 0 else "white", box="black@0.5" if j == 2 else None)
        y += size + 44
    y = 1330
    for j, line in enumerate(cfg["bottom"]):
        size = [60, 44, 40][min(j, 2)]
        dt(line, y, size, GOLD if j == 0 else "white")
        y += size + 36

    chains = [
        f"[0:v]setpts=PTS/{speed},split[a][b]",
        f"[a]scale=-2:{H},crop={W}:{H},boxblur=30:3,eq=brightness=-0.35[bg]",
        f"[b]scale={W}:-2,pad={W}+8:ih+8:4:4:color=white[fg]",
        f"[bg][fg]overlay=-4:(H-h)/2-40," + ",".join(texts) + "[v]",
        f"[0:a]atempo={speed}[a1]",
    ]
    script = work / "filter.txt"
    script.write_text(";\n".join(chains), encoding="utf-8")
    out = SIM_DIR / f"{prefix}_short.mp4"
    max_sec = float(cfg.get("max_sec", 175))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex_script", str(script),
                    "-map", "[v]", "-map", "[a1]", "-t", f"{max_sec:.2f}", "-r", "30", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                    str(out)], check=True)
    # サムネイル: 縦のままフレームを抜き出す（-s でのリサイズはしない）
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(cfg.get("thumb_at", 0.5)), "-i", str(out),
                    "-vframes", "1", "-q:v", "2", str(SIM_DIR / f"{prefix}_short_thumb.jpg")], check=True)
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.strip()
    print(f"保存: {out}（{dur}秒）")


if __name__ == "__main__":
    main()
