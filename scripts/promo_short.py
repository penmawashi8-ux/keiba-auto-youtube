#!/usr/bin/env python3
"""本編（横動画）から、フル版へ誘導する縦ショート（1080×1920）を作る。

構成: 本編のレース部分（ゲートイン〜ゴール直前）を倍速で中央に置き、上に見出し、下に案内。
      ゴールの手前で止めて、結果は見せずに「続きはフル版で」の締めカードを出す。
背景はぼかした本編。音声は atempo で倍速（声の高さは変えない）。
サムネイルは動画のフレーム抽出で作る（縦横比はそのまま。CLAUDE.md のルール）。

設定: data/promo/<名前>.json
  {"source": 本編（research-output 上のパス）, "marks": 場面の時刻 JSON（同）, "prefix": 出力名, "speed": 2.0,
   "from": "gate", "to": "goal", "to_offset": -2.5,   ← 切り出す範囲（marks のキーと秒のずれ）
   "top": [見出しの行...], "bottom": [レース中の案内の行...], "end": [締めカードの行...], "end_sec": 4.0}
出力: sim/<prefix>_short.mp4, sim/<prefix>_short_thumb.jpg

使い方: python scripts/promo_short.py data/promo/tenno_aki2026.json 本編.mp4 marks.json
Pillow / numpy / drawbox は使わない（プロジェクトルール）。
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sim_dream_race import find_font  # noqa: E402

W, H, FPS = 1080, 1920, 30
GOLD = "0xF5C542"
SIM_DIR = Path("sim")


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise SystemExit("ffmpeg 失敗")


class Texts:
    def __init__(self, work: Path, font: str):
        self.work, self.font, self.n = work, font, 0

    def __call__(self, text: str, y: int, size: int, color: str, box: str | None = None) -> str:
        self.n += 1
        p = self.work / f"t{self.n}.txt"
        p.write_text(text, encoding="utf-8")
        s = (f"drawtext=fontfile='{self.font}':textfile='{p}':x=(w-text_w)/2:y={y}:fontsize={size}:fontcolor={color}"
             f":borderw=4:bordercolor=black@0.8")
        if box:
            s += f":box=1:boxcolor={box}:boxborderw=16"
        return s


def stack(lines: list[str], y: int, sizes: list[int], T: Texts, first_gold: bool = True, gap: int = 40) -> list[str]:
    out = []
    for j, line in enumerate(lines):
        size = sizes[min(j, len(sizes) - 1)]
        out.append(T(line, y, size, GOLD if (j == 0 and first_gold) else "white"))
        y += size + gap
    return out


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    src, marks = Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    prefix, speed = cfg["prefix"], float(cfg.get("speed", 2.0))
    work = SIM_DIR / f"work_{prefix}_short"
    work.mkdir(parents=True, exist_ok=True)
    T = Texts(work, find_font())
    t0 = max(marks[cfg.get("from", "gate")] + cfg.get("from_offset", 0.0), 0.0)
    t1 = marks[cfg.get("to", "goal")] + cfg.get("to_offset", -2.5)
    print(f"切り出し: {t0:.1f}〜{t1:.1f}秒（{(t1 - t0) / speed:.1f}秒に）")

    # ① レース部分（倍速・縦レイアウト）
    clip = work / "clip.mp4"
    texts = stack(cfg["top"], 150, [100, 62, 46], T) + stack(cfg["bottom"], 1340, [60, 44, 40], T)
    chains = [f"[0:v]setpts=PTS/{speed},split[a][b]",
              f"[a]scale=-2:{H},crop={W}:{H},boxblur=30:3,eq=brightness=-0.35[bg]",
              f"[b]scale={W}:-2,pad={W}+8:ih+8:4:4:color=white[fg]",
              f"[bg][fg]overlay=-4:(H-h)/2-40," + ",".join(texts) + ",fps=30,setsar=1[v]",
              f"[0:a]atempo={speed},aresample=48000[au]"]
    (work / "clip_filter.txt").write_text(";\n".join(chains), encoding="utf-8")
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t0:.2f}", "-t", f"{t1 - t0:.2f}", "-i", str(src),
         "-filter_complex_script", str(work / "clip_filter.txt"), "-map", "[v]", "-map", "[au]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
         str(clip)])

    # ② 締めカード（ゴール直前の画面をぼかして止め、続きはフル版へ）
    still = work / "still.jpg"
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t1:.2f}", "-i", str(src), "-vframes", "1", str(still)])
    end_sec = float(cfg.get("end_sec", 4.0))
    end = work / "end.mp4"
    etexts = stack(cfg["end"], 640, [96, 64, 52, 44], T, gap=56)
    chains = [f"[0:v]scale=-2:{H},crop={W}:{H},boxblur=20:2,eq=brightness=-0.45,setsar=1,"
              + ",".join(etexts) + f",fade=t=in:st=0:d=0.3,format=yuv420p[v]"]
    (work / "end_filter.txt").write_text(";\n".join(chains), encoding="utf-8")
    run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(still),
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex_script", str(work / "end_filter.txt"),
         "-map", "[v]", "-map", "1:a", "-t", f"{end_sec:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(end)])

    # ③ つなぐ
    out = SIM_DIR / f"{prefix}_short.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), "-i", str(end), "-filter_complex",
         "[0:v]setsar=1[v0];[1:v]setsar=1[v1];[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(out)])
    # サムネイル: 縦のままフレームを抜き出す（-s でのリサイズはしない）
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(cfg.get("thumb_at", 1.0)), "-i", str(out),
         "-vframes", "1", "-q:v", "2", str(SIM_DIR / f"{prefix}_short_thumb.jpg")])
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.strip()
    print(f"保存: {out}（{dur}秒）")


if __name__ == "__main__":
    main()
