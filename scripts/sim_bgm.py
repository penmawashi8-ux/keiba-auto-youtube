#!/usr/bin/env python3
"""夢の対決シミュレーション用のオリジナルBGM（著作権・Content ID リスクなし）。

場面ごとに曲を切り替える:
  calm    … 説明ページ用。88BPM、Cメジャー寄りの落ち着いたパッド＋アルペジオ（分析している雰囲気）
  race    … レース用。150BPM、Eマイナー。キック4つ打ち＋蹄のようなギャロップのベース＋16分ハイハット
  fanfare … ゴール用。2小節の金管風ファンファーレ
  result  … 結果ページ用。96BPM、明るめのパッド＋ゆったりしたアルペジオ

numpy / Pillow は使わず純Pythonで波形を作り、ffmpeg で場面の長さに合わせてつなぐ
（ループ＋クロスフェード、リバーブ、ラウドネス調整）。打楽器などの部品は make_prediction_bgm.py を流用する。

使い方: sim_dream_race.py から make_bgm(区間リスト, 出力パス) を呼ぶ
"""

import math
import random
import struct
import subprocess
import tempfile
import wave
from array import array
from pathlib import Path

import make_prediction_bgm as base   # kick / clap / hat / pluck / bass_note / mfreq を流用

SR = base.SR
TWO_PI = 2 * math.pi


class Loop:
    """ループ長で折り返して加算するステレオバッファ"""

    def __init__(self, seconds: float):
        self.n = int(round(seconds * SR))
        self.seconds = self.n / SR
        self.l = array("f", [0.0]) * self.n
        self.r = array("f", [0.0]) * self.n

    def add(self, start: float, samples: list[float], gl: float, gr: float | None = None, wrap: bool = True):
        gr = gl if gr is None else gr
        s0 = int(start * SR)
        for i, v in enumerate(samples):
            j = s0 + i
            if j >= self.n:
                if not wrap:
                    break
                j %= self.n
            self.l[j] += v * gl
            self.r[j] += v * gr

    def pad(self, chords: list[tuple[int, ...]], bar: float, gain: float, bright: float = 0.35):
        fade = 0.3
        for b, chord in enumerate(chords):
            freqs = [base.mfreq(m) for m in chord]
            length = bar + fade
            s0 = int(b * bar * SR)
            for i in range(int(length * SR)):
                t = i / SR
                env = min(1.0, t / fade) * min(1.0, (length - t) / fade)
                vl = vr = 0.0
                for f in freqs:
                    for h, a in ((1, 1.0), (2, bright), (3, bright * 0.4)):
                        vl += a * math.sin(TWO_PI * f * h * 0.9985 * t)
                        vr += a * math.sin(TWO_PI * f * h * 1.0015 * t)
                j = (s0 + i) % self.n
                self.l[j] += vl * env * gain
                self.r[j] += vr * env * gain

    def normalized(self, peak_to: float = 0.89) -> "Loop":
        peak = max(max(abs(v) for v in self.l), max(abs(v) for v in self.r)) or 1.0
        k = peak_to / peak
        for i in range(self.n):
            self.l[i] *= k
            self.r[i] *= k
        return self


def brass(freq: float, length: float) -> list[float]:
    """金管風の音（のこぎり波に近い倍音＋立ち上がりのアタック）"""
    out = []
    for i in range(int(length * SR)):
        t = i / SR
        env = min(1.0, t * 25) * (0.75 + 0.25 * math.exp(-t * 4)) * min(1.0, (length - t) * 12)
        v = sum(math.sin(TWO_PI * freq * h * t) / h for h in range(1, 7))
        out.append(v * env * 0.5)
    return out


def calm_loop() -> Loop:
    bpm, bars = 88, 8
    beat = 60 / bpm
    bar = beat * 4
    lp = Loop(bar * bars)
    prog = [(48, (60, 64, 67)), (45, (57, 60, 64)), (41, (57, 60, 65)), (43, (55, 59, 62))] * 2   # C Am F G
    lp.pad([c for _, c in prog], bar, 0.035)
    order = [0, 1, 2, 1]
    for b, (root, chord) in enumerate(prog):
        s0 = b * bar
        lp.add(s0, base.bass_note(base.mfreq(root), bar * 0.95), 0.10)
        for e in range(8):
            m = chord[order[e % 4]] + 12
            pan = 0.3 if e % 2 == 0 else -0.3
            lp.add(s0 + e * beat / 2, base.pluck(base.mfreq(m), 0.5), 0.05 * (1 - pan), 0.05 * (1 + pan))
    return lp.normalized()


def race_loop() -> Loop:
    rng = random.Random(150)
    bpm, bars = 150, 8
    beat = 60 / bpm
    bar = beat * 4
    lp = Loop(bar * bars)
    prog = [(40, (52, 55, 59)), (36, (48, 52, 55)), (38, (50, 54, 57)), (35, (47, 51, 54))] * 2  # Em C D B
    lp.pad([c for _, c in prog], bar, 0.022, bright=0.5)
    k = base.kick()
    c = base.clap(rng)
    s16 = beat / 4
    for b, (root, chord) in enumerate(prog):
        s0 = b * bar
        for bt in range(4):
            t = s0 + bt * beat
            lp.add(t, k, 0.34)
            if bt in (1, 3):
                lp.add(t, c, 0.16)
            for s in range(4):
                lp.add(t + s * s16, base.hat(rng, 0.04, 90), 0.06 if s % 2 else 0.035, 0.035 if s % 2 else 0.06)
            # ギャロップ（タ・タ・ターン）のベース
            bf = base.mfreq(root)
            for off, ln, g in ((0.0, s16 * 0.9, 0.13), (s16 * 2, s16 * 0.9, 0.10), (s16 * 3, s16 * 0.9, 0.12)):
                lp.add(t + off, base.bass_note(bf * (2 if off else 1), ln), g)
        for s in range(16):
            m = chord[[0, 1, 2, 1][s % 4]] + 24
            if s % 2 == 0:
                lp.add(s0 + s * s16, base.pluck(base.mfreq(m), 0.18), 0.035, 0.03)
        if b % 4 == 3:   # 4小節ごとのフィル
            for s in range(4):
                lp.add(s0 + 3 * beat + s * s16, c, 0.07 + 0.03 * s)
    return lp.normalized()


def fanfare() -> Loop:
    bpm = 120
    beat = 60 / bpm
    lp = Loop(beat * 8 + 1.0)
    notes = [(0.0, 0.5, (67, 72, 76)), (0.5, 0.5, (67, 72, 76)), (1.0, 0.5, (67, 72, 76)),
             (1.5, 1.5, (72, 76, 79)), (3.0, 0.5, (69, 72, 77)), (3.5, 0.5, (71, 74, 79)),
             (4.0, 3.5, (72, 76, 84))]
    for st, ln, chord in notes:
        for m in chord:
            lp.add(st * beat, brass(base.mfreq(m), ln * beat), 0.10, wrap=False)
    lp.add(4.0 * beat, base.kick(), 0.4, wrap=False)
    return lp.normalized(0.8)


def result_loop() -> Loop:
    bpm, bars = 96, 8
    beat = 60 / bpm
    bar = beat * 4
    lp = Loop(bar * bars)
    prog = [(41, (57, 60, 65)), (43, (59, 62, 67)), (40, (55, 59, 64)), (45, (57, 60, 64))] * 2   # F G Em Am
    lp.pad([c for _, c in prog], bar, 0.035, bright=0.45)
    k = base.kick()
    for b, (root, chord) in enumerate(prog):
        s0 = b * bar
        lp.add(s0, k, 0.16)
        lp.add(s0 + 2 * beat, k, 0.12)
        lp.add(s0, base.bass_note(base.mfreq(root), bar * 0.48), 0.10)
        lp.add(s0 + bar / 2, base.bass_note(base.mfreq(root), bar * 0.48), 0.09)
        for e in range(8):
            m = chord[[0, 2, 1, 2][e % 4]] + 12
            lp.add(s0 + e * beat / 2, base.pluck(base.mfreq(m), 0.45), 0.045)
    return lp.normalized()


def _write(path: Path, lp: Loop) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        pack = struct.Struct("<hh").pack
        frames = bytearray()
        for a, b in zip(lp.l, lp.r):
            frames += pack(int(max(-1.0, min(1.0, a)) * 32767), int(max(-1.0, min(1.0, b)) * 32767))
        w.writeframes(bytes(frames))


def make_bgm(sections: list[tuple[str, float]], out: Path, xfade: float = 1.2) -> Path:
    """sections = [("calm", 秒), ("race", 秒), ("fanfare", 秒), ("result", 秒)] の順につないだBGMを作る"""
    makers = {"calm": calm_loop, "race": race_loop, "fanfare": fanfare, "result": result_loop}
    with tempfile.TemporaryDirectory(prefix="simbgm_") as tmp:
        tmp = Path(tmp)
        inputs, chains = [], []
        for i, (kind, sec) in enumerate(sections):
            p = tmp / f"{kind}.wav"
            if not p.exists():
                _write(p, makers[kind]())
            inputs += ["-i", str(p)]
            # 次の曲とのクロスフェード分だけ長めに切り出す（最後の区間はそのまま）
            length = sec + (xfade if i + 1 < len(sections) else 0.0)
            loop = "" if kind == "fanfare" else "aloop=loop=-1:size=2e9,"
            chains.append(f"[{i}:a]{loop}atrim=0:{length:.3f},asetpts=N/SR/TB,"
                          f"apad=whole_dur={length:.3f}[s{i}]")
        cur = "s0"
        for i in range(1, len(sections)):
            chains.append(f"[{cur}][s{i}]acrossfade=d={xfade}:c1=tri:c2=tri[x{i}]")
            cur = f"x{i}"
        total = sum(sec for _, sec in sections)
        chains.append(f"[{cur}]aecho=0.8:0.5:60|110:0.15|0.08,lowpass=f=10000,highpass=f=35,"
                      f"loudnorm=I=-20:TP=-2:LRA=9,afade=t=in:d=0.8,afade=t=out:st={total - 2.0:.2f}:d=2.0[out]")
        script = tmp / "bgm_filter.txt"
        script.write_text(";".join(chains), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex_script", str(script),
                        "-map", "[out]", "-ar", str(SR), "-t", f"{total:.3f}", str(out)], check=True)
    return out
