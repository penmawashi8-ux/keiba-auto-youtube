"""CG 動画のサムネイル（横 1280×720）。テロップの乗っていない CG のコマに、レース名を大きく入れる。

使い方: python scripts/cg/thumb.py <フレームのディレクトリ> <plan.json> <出力.jpg> <太字フォント>
研究（docs/thumbnail_research.md）の型: レースの場面＋レース名を極太の縁取り文字で大きく＋「1万回シミュレーション」の帯＋一言。
答えは伏せる（「勝つのは…？」）。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import race_names as rn      # noqa: E402


def main():
    frames, plan_p, out, font = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    plan = json.loads(plan_p.read_text(encoding="utf-8"))
    fps = plan["fps"]
    race = next(s for s in plan["segments"] if s["name"] == "race")
    shots = race.get("shots", [])
    # ゴール前を横から並走するショートの少し後（馬群が大きく映る）。なければ直線の正面
    t = next((t for s, t in shots if s == "finish"), None)
    t = (t + 1.5) if t is not None else (race["t1"] - 6)
    src = frames / f"f{int(t * fps):05d}.jpg"
    if not src.exists():
        src = sorted(frames.glob("f*.jpg"))[len(list(frames.glob("f*.jpg"))) // 2]
    name = rn.short_name(plan["race"]["race_name"])
    year = plan["race"]["date"][:4]
    size = min(200, int(1100 / max(len(name), 1) * 1.15))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        texts = {"banner": "1万回シミュレーション", "name": name, "year": year, "q": "勝つのは…？"}
        for k, v in texts.items():
            (tmp / f"{k}.txt").write_text(v, encoding="utf-8")
        d = lambda k, size, color, x, y, extra="": (                   # noqa: E731
            f"drawtext=textfile='{tmp / (k + '.txt')}':fontfile='{font}':fontsize={size}:fontcolor={color}:"
            f"x={x}:y={y}:expansion=none{extra}")
        vf = ",".join([
            "scale=1440:810", "crop=1280:720:80:40", "eq=saturation=1.25:contrast=1.08", "vignette=PI/5",
            d("banner", 40, "white", 40, 32, ":box=1:boxcolor=0xD7141A:boxborderw=12"),
            d("name", size, "white", 34, 100, f":borderw={size // 9}:bordercolor=white"),
            d("name", size, "0xFFE14D", 34, 100, f":borderw={size // 18}:bordercolor=0x111111"),
            d("year", 44, "white", f"34+{size}*{len(name)}*0.92-text_w", 90, ":borderw=6:bordercolor=0x111111"),
            d("q", 84, "white", 40, 580, ":borderw=9:bordercolor=0xD7141A"),
        ])
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-q:v", "2", str(out)], check=True)
    print(f"サムネイル: {out}（元のコマ {src.name}）")


if __name__ == "__main__":
    main()
