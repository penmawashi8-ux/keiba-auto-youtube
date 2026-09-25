#!/usr/bin/env python3
"""
予想動画用のオリジナルBGMを合成する（著作権・Content ID リスクなし）。

104BPM・Aマイナーの8小節ループ（Am-F-C-G-Am-F-G-E）。
パッド＋ベース＋キック/クラップ/ハイハット＋16分アルペジオの構成で、
「データを分析している」落ち着いた緊張感を狙う。

numpy / Pillow は使わず、純Pythonで波形を作り ffmpeg で仕上げ（リバーブ・EQ・ラウドネス）する。

使い方:
  python scripts/make_prediction_bgm.py [出力パス]   # 既定: assets/bgm/prediction_bgm.wav
"""

import math
import random
import struct
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path

SR = 44100
BPM = 104
BEAT = 60.0 / BPM
BAR = BEAT * 4
BARS = 8
LOOP_SEC = BAR * BARS
N = int(round(LOOP_SEC * SR))

# (ベースのMIDI, パッド/アルペジオのMIDI3音)
PROGRESSION = [
    (45, (57, 60, 64)),  # Am
    (41, (53, 57, 60)),  # F
    (48, (55, 60, 64)),  # C
    (43, (55, 59, 62)),  # G
    (45, (57, 60, 64)),  # Am
    (41, (53, 57, 60)),  # F
    (43, (55, 59, 62)),  # G
    (40, (56, 59, 64)),  # E
]

TWO_PI = 2 * math.pi


def mfreq(m: int) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _add(buf: array, start: float, samples: list[float], gain_l: float, gain_r: float,
         buf_r: array) -> None:
    """ループ長で折り返しながらイベント波形を加算する（継ぎ目のないループ用）。"""
    s0 = int(start * SR)
    for i, v in enumerate(samples):
        j = (s0 + i) % N
        buf[j] += v * gain_l
        buf_r[j] += v * gain_r


def kick(length: float = 0.45) -> list[float]:
    out, phase = [], 0.0
    for i in range(int(length * SR)):
        t = i / SR
        f = 48 + 110 * math.exp(-t * 32)
        phase += TWO_PI * f / SR
        out.append(math.sin(phase) * math.exp(-t * 7.5))
    return out


def clap(rng: random.Random, length: float = 0.25) -> list[float]:
    out, prev = [], 0.0
    for i in range(int(length * SR)):
        t = i / SR
        n = rng.uniform(-1, 1)
        hp = n - prev  # 簡易ハイパス
        prev = n
        env = math.exp(-t * 16) + 0.6 * math.exp(-max(0.0, t - 0.012) * 40) * (t > 0.012)
        tone = math.sin(TWO_PI * 190 * t) * math.exp(-t * 30) * 0.4
        out.append((hp * 0.55 + tone) * env)
    return out


def hat(rng: random.Random, length: float = 0.06, decay: float = 70) -> list[float]:
    out, prev = [], 0.0
    for i in range(int(length * SR)):
        n = rng.uniform(-1, 1)
        out.append((n - prev) * math.exp(-(i / SR) * decay))
        prev = n
    return out


def pluck(freq: float, length: float = 0.32) -> list[float]:
    out = []
    for i in range(int(length * SR)):
        t = i / SR
        env = math.exp(-t * 11) * min(1.0, t * 400)
        out.append(env * (math.sin(TWO_PI * freq * t)
                          + 0.35 * math.sin(TWO_PI * 2 * freq * t)
                          + 0.12 * math.sin(TWO_PI * 3 * freq * t)))
    return out


def bass_note(freq: float, length: float) -> list[float]:
    out = []
    for i in range(int(length * SR)):
        t = i / SR
        env = min(1.0, t * 300) * (0.55 + 0.45 * math.exp(-t * 6))
        env *= min(1.0, (length - t) * 60)  # 末尾クリック防止
        out.append(env * (math.sin(TWO_PI * freq * t) + 0.3 * math.sin(TWO_PI * 2 * freq * t)))
    return out


def synthesize() -> tuple[array, array]:
    rng = random.Random(20260927)
    left = array("f", [0.0]) * N
    right = array("f", [0.0]) * N

    # パッド（小節ごとにクロスフェード、左右で微妙にデチューン）
    fade = 0.25
    for b, (_, chord) in enumerate(PROGRESSION):
        s0 = b * BAR
        length = BAR + fade
        freqs = [mfreq(m) for m in chord]
        for i in range(int(length * SR)):
            t = i / SR
            env = min(1.0, t / fade) * min(1.0, (length - t) / fade)
            vl = vr = 0.0
            for f in freqs:
                for h, a in ((1, 1.0), (2, 0.35), (3, 0.15)):
                    vl += a * math.sin(TWO_PI * f * h * 0.9985 * t)
                    vr += a * math.sin(TWO_PI * f * h * 1.0015 * t)
            j = (int(s0 * SR) + i) % N
            left[j] += vl * env * 0.040
            right[j] += vr * env * 0.040

    k = kick()
    c = clap(rng)
    eighth = BEAT / 2
    sixteenth = BEAT / 4
    arp_order = [0, 1, 2, 1, 2, 0, 1, 2]

    for b, (root, chord) in enumerate(PROGRESSION):
        s0 = b * BAR
        last_bar = b == BARS - 1
        # ドラム
        for beat in range(4):
            t = s0 + beat * BEAT
            if beat in (0, 2):
                _add(left, t, k, 0.30, 0.30, right)
            if beat in (1, 3):
                _add(left, t, c, 0.14, 0.14, right)
            _add(left, t + eighth, hat(rng), 0.10, 0.07, right)
            _add(left, t, hat(rng, 0.03, 120), 0.04, 0.06, right)
        if b % 2 == 1:
            _add(left, s0 + 3.5 * BEAT, k, 0.18, 0.18, right)  # 裏のキック
        if last_bar:  # ループ頭へのフィル
            for s in range(4):
                _add(left, s0 + 3 * BEAT + s * sixteenth, c, 0.08 + 0.03 * s, 0.08 + 0.03 * s, right)

        # ベース（8分）
        bf = mfreq(root)
        for e in range(8):
            f = bf * (2 if e in (3, 7) else 1)
            _add(left, s0 + e * eighth, bass_note(f, eighth * 0.92), 0.12, 0.12, right)

        # アルペジオ（16分、1オクターブ上）
        for s in range(16):
            m = chord[arp_order[s % 8]] + 12
            gain = 0.068 if s % 4 == 0 else 0.046
            pan = 0.35 if s % 2 == 0 else -0.35
            _add(left, s0 + s * sixteenth, pluck(mfreq(m)),
                 gain * (1 - pan), gain * (1 + pan), right)

    peak = max(max(abs(v) for v in left), max(abs(v) for v in right)) or 1.0
    scale = 0.89 / peak
    for i in range(N):
        left[i] *= scale
        right[i] *= scale
    return left, right


def write_wav(path: Path, left: array, right: array) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = bytearray()
        pack = struct.Struct("<hh").pack
        for l, r in zip(left, right):
            frames += pack(int(max(-1.0, min(1.0, l)) * 32767),
                           int(max(-1.0, min(1.0, r)) * 32767))
        w.writeframes(bytes(frames))


def make_bgm(out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    left, right = synthesize()
    with tempfile.TemporaryDirectory(prefix="bgm_") as tmp:
        raw = Path(tmp) / "raw.wav"
        write_wav(raw, left, right)
        # 軽いリバーブ＋高域を少し丸めてラウドネスを揃える（ループの継ぎ目を保つため長さは固定）
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
             "-af",
             "aecho=0.8:0.5:60|110:0.18|0.10,"
             "lowpass=f=9000,highpass=f=35,"
             "loudnorm=I=-18:TP=-2:LRA=7",
             "-ar", str(SR), "-t", f"{LOOP_SEC:.4f}", str(out)],
            check=True,
        )
    print(f"BGM生成: {out} ({LOOP_SEC:.2f}秒ループ, {BPM}BPM)")
    return out


if __name__ == "__main__":
    make_bgm(sys.argv[1] if len(sys.argv) > 1 else "assets/bgm/prediction_bgm.wav")
