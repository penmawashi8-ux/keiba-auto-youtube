"""3D CG のフレーム（Blender の出力）に、中継風のテロップと音声を重ねて動画にする。

使い方: python scripts/cg/compose.py <フレームのディレクトリ> <plan.json のあるディレクトリ> <出力.mp4> [フォント]

テロップは「時間の区切りごとに1枚の透明PNG」を作り、つないで1本の透明な動画にしてから重ねる
（drawbox は使わない。板は color ソースの overlay、文字は drawtext の textfile）。
"""
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sim_render as sr      # noqa: E402  枠の色
import sim_bgm as sb         # noqa: E402

W, H = 1280, 720
PLATE = "0x0B0F14@0.74"
ACCENT = "0x1B8F4A"
WHITE = "0xFFFFFF"
SUB = "0xC9D1D9"
WAKU_BG = [c.replace("#", "0x") for c in sr.COLORS]
WAKU_FG = ["0x111111", "0xFFFFFF", "0xFFFFFF", "0xFFFFFF", "0x111111", "0xFFFFFF", "0x111111", "0x111111"]


class Card:
    """透明な1枚に、板（矩形）と文字を置く"""

    def __init__(self, tmp: Path, font: str):
        self.tmp, self.font = tmp, font
        self.rects, self.texts = [], []

    def rect(self, x, y, w, h, color):
        self.rects.append((int(x), int(y), max(int(w), 2) // 2 * 2, max(int(h), 2) // 2 * 2, color))

    def text(self, s, x, y, size, color=WHITE, anchor="l", shadow=False):
        self.texts.append((s, x, y, size, color, anchor, shadow))

    def num(self, n, waku, x, y, size):
        """枠色の四角に馬番"""
        k = int(size * 1.25) // 2 * 2
        if waku in (1, 2):                      # 白・黒の枠は背景に溶けないよう縁取る
            self.rect(x - 2, y - 2, k + 4, k + 4, "0x9AA4AE")
        self.rect(x, y, k, k, WAKU_BG[waku - 1])
        self.text(str(n), x + k / 2, y + k / 2, size, WAKU_FG[waku - 1], anchor="c")
        return k

    def save(self, path: Path):
        args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black@0.0:s={W}x{H}:d=1,format=rgba"]
        chain, cur = [], "[0]"
        for j, (x, y, w, h, c) in enumerate(self.rects, 1):
            args += ["-f", "lavfi", "-i", f"color=c={c}:s={w}x{h}:d=1,format=rgba"]
            chain.append(f"{cur}[{j}]overlay={x}:{y}:format=auto[r{j}]")
            cur = f"[r{j}]"
        dts = []
        for k, (s, x, y, size, color, anchor, shadow) in enumerate(self.texts):
            tf = self.tmp / f"{path.stem}_{k}.txt"
            tf.write_text(s, encoding="utf-8")
            xs = f"{x}-text_w/2" if anchor == "c" else (f"{x}-text_w" if anchor == "r" else f"{x}")
            ys = f"{y}-text_h/2" if anchor == "c" else f"{y}"
            extra = ":shadowcolor=black@0.7:shadowx=2:shadowy=2" if shadow else ""
            dts.append(f"drawtext=textfile='{tf}':fontfile='{self.font}':fontsize={size}:fontcolor={color}:"
                       f"x={xs}:y={ys}:expansion=none{extra}")
        chain.append(f"{cur}" + (",".join(dts) if dts else "null") + ",format=rgba[out]")
        args += ["-filter_complex", ";".join(chain), "-map", "[out]", "-frames:v", "1", str(path)]
        subprocess.run(args, check=True)


def text_w(s: str, size: int) -> float:
    """おおよその文字幅（全角1・半角0.55）"""
    return sum(size * (0.55 if ord(ch) < 0x2E80 else 1.0) for ch in s)


def draw(card: Card, items: list[dict], plan: dict, t: float):
    race = plan["race"]
    hs = {h["num"]: h for h in plan["horses"]}
    for it in items:
        ty = it["type"]
        if ty == "title":
            card.rect(56, 506, 6, 128, ACCENT)
            full = {"マイルCS": "マイルチャンピオンシップ"}.get(race["race_name"], race["race_name"])
            card.text(full, 80, 500, 58, WHITE, shadow=True)
            card.text(f"{race['date'][:4]}年{int(race['date'][5:7])}月{int(race['date'][8:10])}日　"
                      f"{race['venue']}　芝{race['distance']}m", 82, 576, 26, WHITE, shadow=True)
            card.text("データで1万回シミュレーション", 82, 612, 22, SUB, shadow=True)
        elif ty == "race_tag":
            label = f"{race['venue']}11R　{race['race_name']}"
            w = text_w(label, 24) + 40
            card.rect(32, 28, w, 44, PLATE)
            card.rect(32, 28, 5, 44, ACCENT)
            card.text(label, 50, 50, 24, WHITE, anchor="l")
            card.texts[-1] = (label, 50, 37, 24, WHITE, "l", False)
            card.text("シミュレーション映像", W - 32, H - 30, 16, "0xFFFFFF@0.75", anchor="r")
        elif ty == "remaining":
            m = it["m"]
            label = "ゴール" if m == 0 else f"残り {m}m"
            w = text_w(label, 26) + 36
            card.rect(W - 32 - w, 28, w, 44, PLATE)
            card.text(label, W - 32 - w / 2, 50, 26, WHITE, anchor="c")
        elif ty == "order":
            order = it["order"]
            k = 30
            gap = 6
            total = len(order) * (k + gap) - gap + 120
            x0 = (W - total) / 2
            card.rect(x0, H - 86, total, 50, PLATE)
            card.text("隊列", x0 + 18, H - 61, 20, SUB, anchor="l")
            card.texts[-1] = ("隊列", x0 + 18, H - 72, 20, SUB, "l", False)
            x = x0 + 74
            for j, num in enumerate(order):
                card.num(num, hs[num]["waku"], x, H - 80, 24)
                x += k + gap
            card.text("先頭 →", x0 + 74, H - 106, 15, SUB)
        elif ty == "replay":
            card.rect(32, 28, 210, 44, "0xB71C1C@0.88")
            card.text("リプレイ　ゴール前", 137, 50, 22, WHITE, anchor="c")
        elif ty == "result":
            x0, y0 = W - 32 - 470, 150
            card.rect(x0, y0, 470, 54 + 58 * len(it["top"]), PLATE)
            card.rect(x0, y0, 470, 4, ACCENT)
            card.text("着順", x0 + 22, y0 + 16, 22, SUB)
            for j, r in enumerate(it["top"]):
                y = y0 + 54 + j * 58
                card.text(f"{j + 1}着", x0 + 22, y + 10, 26, WHITE)
                card.num(r["num"], r["waku"], x0 + 92, y + 6, 26)
                card.text(r["name"], x0 + 144, y + 8, 28, WHITE)
                card.text(r["time"], x0 + 448, y + 12, 22, SUB, anchor="r")
                card.texts[-1] = (r["time"], x0 + 448, y + 12, 22, SUB, "r", False)
        elif ty == "rates":
            rows = it["rows"]
            x0, y0, w = W - 32 - 520, 110, 520
            card.rect(x0, y0, w, 64 + 62 * len(rows), PLATE)
            card.rect(x0, y0, w, 4, ACCENT)
            card.text("1万回走らせた勝率", x0 + 22, y0 + 18, 24, WHITE)
            top = max(r["win"] for r in rows)
            for j, r in enumerate(rows):
                y = y0 + 66 + j * 62
                card.num(r["num"], r["waku"], x0 + 22, y, 24)
                card.text(r["name"], x0 + 66, y + 2, 26, WHITE)
                bw = 80 * r["win"] / top
                card.rect(x0 + 300, y + 10, bw, 14, "0xE8C547" if j == 0 else "0x9AA7B2")
                card.text(f"{r['win'] * 100:.1f}%", x0 + w - 22, y + 2, 26, WHITE, anchor="r")
        elif ty == "focus":
            x0, y0 = 40, H - 220
            card.rect(x0, y0, 700, 176, PLATE)
            card.rect(x0, y0, 6, 176, ACCENT)
            k = card.num(it["num"], it["waku"], x0 + 26, y0 + 22, 30)
            card.text(it["name"], x0 + 40 + k, y0 + 18, 38, WHITE)
            card.text(f"勝率 {it['win'] * 100:.1f}%（{it['rank']}位）", x0 + 676, y0 + 28, 26, "0xE8C547", anchor="r")
            card.text(f"{it['record']}　{it['style']}　{it['jockey']}", x0 + 28, y0 + 76, 22, SUB)
            for j, p in enumerate(it["points"][:2]):
                card.text("・" + p, x0 + 28, y0 + 110 + j * 30, 21, WHITE)
        elif ty == "outro":
            card.rect(W / 2 - 400, H - 190, 800, 170, PLATE)
            card.text("あなたの予想は？", W / 2, H - 150, 46, WHITE, anchor="c", shadow=True)
            card.text("コメントで教えてください", W / 2, H - 98, 26, WHITE, anchor="c", shadow=True)
            card.text("※データによるシミュレーションです。馬券の購入を勧めるものではありません", W / 2, H - 40, 17,
                      "0xFFFFFF@0.85", anchor="c", shadow=True)


def overlay_items(plan: dict) -> list[dict]:
    items = list(plan["overlays"])
    for o in plan["order_strip"]:
        items.append({"type": "order", **o})
    for r in plan["remaining"]:
        items.append({"type": "remaining", **r})
    return items


def build_overlay(plan: dict, tmp: Path, font: str) -> Path:
    items = [i for i in overlay_items(plan) if i["t1"] > i["t0"]]
    cuts = sorted({0.0, plan["total"]} | {round(i["t0"], 3) for i in items} | {round(i["t1"], 3) for i in items})
    cuts = [c for c in cuts if 0 <= c <= plan["total"]]
    lines = []
    blank = tmp / "blank.png"
    Card(tmp, font).save(blank)
    for j in range(len(cuts) - 1):
        a, b = cuts[j], cuts[j + 1]
        if b - a < 1e-3:
            continue
        mid = (a + b) / 2
        act = [i for i in items if i["t0"] <= mid < i["t1"]]
        if act:
            c = Card(tmp, font)
            draw(c, act, plan, mid)
            png = tmp / f"ov{j:04d}.png"
            c.save(png)
        else:
            png = blank
        lines.append(f"file '{png}'\nduration {b - a:.3f}")
    lines.append(f"file '{blank}'")
    lst = tmp / "ov.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp / "overlay.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-vf", f"fps={plan['fps']},format=rgba", "-c:v", "qtrle", "-t", f"{plan['total']:.3f}", str(out)],
                   check=True)
    return out


def build_audio(plan: dict, voice_dir: Path, tmp: Path) -> Path:
    """BGM（場面で切り替え・ナレーション中は下げる）＋歓声＋ナレーション・実況"""
    seg = {s["name"]: s for s in plan["segments"]}
    total = plan["total"]
    race = seg["race"]
    sections = [("calm", race["t0"]), ("race", race["t1"] - race["t0"]), ("fanfare", 3.0),
                ("result", max(total - race["t1"] - 3.0, 1.0))]
    bgm = sb.make_bgm(sections, tmp / "bgm.m4a")
    goal_t = next((t for s, t in race.get("shots", []) if s == "finish"), race["t1"] - 3.0) + 4.0
    env = sr.piecewise([(0.0, 0.12), (race["t0"] - 0.5, 0.12), (race["t0"] + 0.2, 0.5), (race["t0"] + 3, 0.22),
                        (goal_t - 8, 0.3), (goal_t - 1, 0.95), (race["t1"], 1.0), (race["t1"] + 4, 0.3),
                        (seg["result"]["t1"], 0.12), (total, 0.08)])
    inputs = ["-i", str(bgm)]
    chains = [f"anoisesrc=color=pink:sample_rate=44100:amplitude=0.6:duration={total:.2f},"
              f"bandpass=f=1100:width_type=h:w=1800,lowpass=f=5000,volume=volume='{env}':eval=frame,"
              f"aformat=channel_layouts=stereo[crowd]"]
    vs = plan["voice"]
    duck = []
    for j, v in enumerate(vs):
        inputs += ["-i", str(voice_dir / v["file"])]
        ms = int(v["t"] * 1000)
        chains.append(f"[{j + 1}:a]aformat=sample_rates=44100:channel_layouts=stereo,adelay={ms}|{ms},"
                      f"volume=1.6[v{j}]")
        duck.append((v["t"], v["t"] + v["dur"]))
    # ナレーション中は BGM を下げる
    expr = "1"
    for a, b in duck:
        expr = f"if(between(t,{a - 0.2:.2f},{b + 0.3:.2f}),0.35,{expr})"
    chains.append(f"[0:a]volume=volume='{expr}':eval=frame,volume=0.8[bg]")
    mix_in = "[bg][crowd]" + "".join(f"[v{j}]" for j in range(len(vs)))
    chains.append(f"{mix_in}amix=inputs={2 + len(vs)}:normalize=0:duration=first,"
                  f"alimiter=limit=0.95,loudnorm=I=-15:TP=-1.5:LRA=11[out]")
    script = tmp / "audio.txt"
    script.write_text(";".join(chains), encoding="utf-8")
    out = tmp / "audio.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex_script", str(script), "-map", "[out]",
                    "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "192k", str(out)], check=True)
    return out


def main():
    frames, pdir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    font = sys.argv[4] if len(sys.argv) > 4 else "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    plan = json.loads((pdir / "plan.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="cgcomp_") as tmp:
        tmp = Path(tmp)
        ov = build_overlay(plan, tmp, font)
        au = build_audio(plan, pdir / "voice", tmp)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(plan["fps"]), "-i", str(frames / "f%05d.jpg"),
                        "-i", str(ov), "-i", str(au),
                        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]",
                        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                        "-c:a", "copy", "-movflags", "+faststart", "-shortest", str(out)], check=True)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
