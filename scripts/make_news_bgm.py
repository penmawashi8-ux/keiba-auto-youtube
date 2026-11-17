#!/usr/bin/env python3
"""
ニュースShorts用の明るいオリジナルBGMを合成する（著作権・Content ID リスクなし）。

以前は download_bgm.py で Musopen のクラシック（ノクターン等）を流していたが、
曲調が暗くニュースに合わなかったため、長調・アップテンポの曲を自前で生成する。

パターン番号（0〜99）ごとに以下を決め、100通りの曲を作り分ける:
  キー（12種）/ テンポ（112〜136BPM）/ 長調のコード進行 / ドラムのノリ /
  ベースの刻み / アルペジオの型 / コードの鳴らし方 / 音色 / メロディ（乱数で作曲）
同じ番号からは常に同じ曲ができる。

numpy / Pillow は使わず、純Pythonで波形を作り ffmpeg で仕上げる（make_prediction_bgm.py と同方式）。

使い方:
  python scripts/make_news_bgm.py [出力ディレクトリ]            # ランダムに1曲（既定: assets/bgm）
  python scripts/make_news_bgm.py assets/bgm --pattern 42       # 指定番号の1曲
  python scripts/make_news_bgm.py out_dir --all                 # 100曲すべて（試聴用）
"""

import argparse
import math
import random
import subprocess
import sys
import tempfile
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_prediction_bgm import SR, TWO_PI, clap, hat, kick, mfreq, write_wav  # noqa: E402

PATTERN_COUNT = 100
BARS = 8
TARGET_SEC = 62.0  # Shorts1本分。MP3の継ぎ目の無音を避けるため1ファイルに収める

MAJOR = [0, 2, 4, 5, 7, 9, 11]

# 明るい長調のコード進行（スケール度数 0=I, 1=ii, 3=IV, 4=V, 5=vi）
PROGRESSIONS = [
    [0, 4, 5, 3, 0, 4, 3, 4],  # I-V-vi-IV
    [0, 3, 4, 0, 0, 3, 4, 4],  # I-IV-V-I
    [3, 4, 2, 5, 3, 4, 0, 0],  # 王道進行 IV-V-iii-vi
    [0, 5, 3, 4, 0, 5, 3, 4],  # I-vi-IV-V（50年代進行）
    [3, 0, 4, 5, 3, 0, 4, 4],  # IV-I-V-vi
    [0, 3, 0, 4, 0, 3, 4, 0],  # I-IV-I-V
    [0, 2, 3, 4, 0, 2, 3, 4],  # I-iii-IV-V
    [5, 3, 0, 4, 5, 3, 0, 4],  # vi-IV-I-V
    [0, 1, 4, 0, 0, 1, 4, 4],  # I-ii-V-I
    [0, 4, 3, 3, 0, 4, 3, 4],  # I-V-IV-IV
]

# アルペジオ（0,1,2=和音の構成音, 3=ルートの1オクターブ上, -1=休み）16分×8
ARP_PATTERNS = [
    [0, 1, 2, 3, 2, 1, 2, 3],
    [0, 2, 1, 3, 0, 2, 1, 3],
    [3, 2, 1, 0, 1, 2, 3, 2],
    [0, -1, 2, -1, 3, -1, 2, -1],
    [0, 1, 2, 1, 3, 1, 2, 1],
    [0, 2, 3, 2, 0, 2, 3, 2],
    [-1, 0, 2, 3, -1, 2, 3, 1],
]

DRUM_STYLES = ["four_on_floor", "pop_backbeat", "bouncy", "light"]
BASS_STYLES = ["octave", "root8", "syncopated", "walking"]
CHORD_STYLES = ["offbeat_stab", "sustain_pad", "quarter_stab"]
LEAD_TIMBRES = ["bell", "pluck", "square", "marimba"]


def _add(left: array, right: array, n: int, start: float, samples: list[float],
         gl: float, gr: float) -> None:
    s0 = int(start * SR)
    for i, v in enumerate(samples):
        j = (s0 + i) % n
        left[j] += v * gl
        right[j] += v * gr


def tone(freq: float, length: float, timbre: str) -> list[float]:
    out = []
    for i in range(int(length * SR)):
        t = i / SR
        tail = min(1.0, (length - t) * 120)
        if timbre == "bell":
            env = math.exp(-t * 12) * min(1.0, t * 600)
            v = (math.sin(TWO_PI * freq * t)
                 + 0.45 * math.sin(TWO_PI * 2 * freq * t) * math.exp(-t * 10)
                 + 0.18 * math.sin(TWO_PI * 4 * freq * t) * math.exp(-t * 25))
        elif timbre == "pluck":
            env = math.exp(-t * 9) * min(1.0, t * 400)
            v = (math.sin(TWO_PI * freq * t) + 0.35 * math.sin(TWO_PI * 2 * freq * t)
                 + 0.15 * math.sin(TWO_PI * 3 * freq * t))
        elif timbre == "square":  # チップチューン風（奇数倍音を控えめに）
            env = (0.5 + 0.5 * math.exp(-t * 8)) * min(1.0, t * 500)
            v = sum(math.sin(TWO_PI * freq * h * t) / h for h in (1, 3, 5, 7)) * 0.75
        else:  # marimba
            env = math.exp(-t * 16) * min(1.0, t * 800)
            v = math.sin(TWO_PI * freq * t) + 0.25 * math.sin(TWO_PI * 4 * freq * t) * math.exp(-t * 40)
        out.append(v * env * tail)
    return out


def bass_note(freq: float, length: float) -> list[float]:
    out = []
    for i in range(int(length * SR)):
        t = i / SR
        env = min(1.0, t * 300) * (0.4 + 0.6 * math.exp(-t * 9)) * min(1.0, (length - t) * 80)
        out.append(env * (math.sin(TWO_PI * freq * t) + 0.35 * math.sin(TWO_PI * 2 * freq * t)))
    return out


def chord_tone(freqs: list[float], length: float, decay: float, detune: float) -> list[float]:
    out = []
    for i in range(int(length * SR)):
        t = i / SR
        env = min(1.0, t * 250) * math.exp(-t * decay) * min(1.0, (length - t) * 60)
        v = 0.0
        for f in freqs:
            for h, a in ((1, 1.0), (2, 0.3), (3, 0.12)):
                v += a * math.sin(TWO_PI * f * h * detune * t)
        out.append(v * env)
    return out


def build_chords(transpose: int, degrees: list[int]) -> list[tuple[int, tuple[int, int, int]]]:
    """スケール度数 → (ベースMIDI, 和音MIDI3音)。和音は60付近に収める。"""
    result = []
    for d in degrees:
        pcs = [MAJOR[(d + k) % 7] + 12 * ((d + k) // 7) for k in (0, 2, 4)]
        notes = [p + 60 + transpose for p in pcs]
        while notes[0] > 64:
            notes = [x - 12 for x in notes]
        while notes[0] < 55:
            notes = [x + 12 for x in notes]
        bass = notes[0] - 12
        while bass > 50:
            bass -= 12
        result.append((bass, tuple(notes)))
    return result


def compose_melody(rng: random.Random, chords, transpose: int) -> list[tuple[int, int, int]]:
    """2小節モチーフを作って変形しながら繰り返す。戻り値: (8分位置, MIDI, 長さ8分数)。"""
    scale = [m + 72 + transpose for m in MAJOR] + [m + 84 + transpose for m in MAJOR[:3]]

    def nearest_chord_tone(pitch: int, chord) -> int:
        cands = [c + 12 * o for c in chord for o in (1, 2)]
        return min(cands, key=lambda c: abs(c - pitch))

    rhythm = []  # 2小節=16個の8分のうち、発音する位置と長さ
    pos = 0
    while pos < 16:
        length = rng.choice([1, 1, 2, 2, 3, 4])
        if pos + length > 16:
            length = 16 - pos
        if rng.random() < 0.8 or pos == 0:
            rhythm.append((pos, length))
        pos += length

    idx = rng.randrange(2, 7)
    contour = []
    for _ in rhythm:
        idx = max(0, min(len(scale) - 1, idx + rng.choice([-2, -1, -1, 0, 1, 1, 2])))
        contour.append(idx)

    notes = []
    for phrase in range(BARS // 2):
        shift = [0, 0, 1, -1][phrase] if rng.random() < 0.6 else 0
        for (p, length), ci in zip(rhythm, contour):
            bar = phrase * 2 + p // 8
            pitch = scale[max(0, min(len(scale) - 1, ci + shift))]
            if p % 4 == 0:  # 強拍はコードトーンに寄せて濁りを防ぐ
                pitch = nearest_chord_tone(pitch, chords[bar][1])
            notes.append((phrase * 16 + p, pitch, length))
    # 最後はトニックで終える
    last_pos, _, last_len = notes[-1]
    notes[-1] = (last_pos, 72 + transpose, last_len)
    return notes


def pattern_params(index: int) -> dict:
    rng = random.Random(9000 + index)
    return {
        "index": index,
        "transpose": rng.randrange(-5, 7),
        "bpm": rng.randrange(112, 137, 2),
        "progression": rng.randrange(len(PROGRESSIONS)),
        "arp": rng.randrange(len(ARP_PATTERNS)),
        "drum": rng.choice(DRUM_STYLES),
        "bass": rng.choice(BASS_STYLES),
        "chord": rng.choice(CHORD_STYLES),
        "arp_timbre": rng.choice(LEAD_TIMBRES),
        "lead_timbre": rng.choice(LEAD_TIMBRES),
        "seed": rng.randrange(1 << 30),
    }


def synthesize(p: dict) -> tuple[array, array, float]:
    rng = random.Random(p["seed"])
    beat = 60.0 / p["bpm"]
    bar = beat * 4
    eighth, sixteenth = beat / 2, beat / 4
    loop_sec = bar * BARS
    n = int(round(loop_sec * SR))
    left = array("f", [0.0]) * n
    right = array("f", [0.0]) * n
    chords = build_chords(p["transpose"], PROGRESSIONS[p["progression"]])

    def add(t, s, gl, gr):
        _add(left, right, n, t, s, gl, gr)

    # --- コード ---
    for b, (_, chord) in enumerate(chords):
        s0 = b * bar
        freqs = [mfreq(m) for m in chord]
        if p["chord"] == "sustain_pad":
            sl = chord_tone(freqs, bar + 0.1, 1.2, 0.998)
            sr = chord_tone(freqs, bar + 0.1, 1.2, 1.002)
            add(s0, sl, 0.030, 0.0)
            add(s0, sr, 0.0, 0.030)
        else:
            hits = [1, 3, 5, 7] if p["chord"] == "offbeat_stab" else [0, 2, 4, 6]
            length = eighth * 0.85
            sl = chord_tone(freqs, length, 7, 0.998)
            sr = chord_tone(freqs, length, 7, 1.002)
            for e in hits:
                add(s0 + e * eighth, sl, 0.045, 0.0)
                add(s0 + e * eighth, sr, 0.0, 0.045)

    # --- ドラム ---
    k, c = kick(), clap(rng)
    for b in range(BARS):
        s0 = b * bar
        for bt in range(4):
            t = s0 + bt * beat
            style = p["drum"]
            if style == "four_on_floor":
                add(t, k, 0.30, 0.30)
                add(t + eighth, hat(rng, 0.07, 45), 0.10, 0.08)
            elif style == "pop_backbeat":
                if bt in (0, 2):
                    add(t, k, 0.30, 0.30)
                if bt == 2 and b % 2 == 1:
                    add(t + eighth, k, 0.20, 0.20)
                add(t, hat(rng, 0.04, 90), 0.06, 0.05)
                add(t + eighth, hat(rng, 0.04, 90), 0.08, 0.06)
            elif style == "bouncy":
                if bt in (0, 2):
                    add(t, k, 0.30, 0.30)
                add(t + 3 * sixteenth, k, 0.14, 0.14)
                add(t + eighth, hat(rng, 0.06, 55), 0.09, 0.07)
            else:  # light
                if bt == 0:
                    add(t, k, 0.24, 0.24)
                add(t + eighth, hat(rng, 0.03, 120), 0.06, 0.08)
            if bt in (1, 3):
                add(t, c, 0.15 if p["drum"] != "light" else 0.09, 0.15 if p["drum"] != "light" else 0.09)
            add(t + sixteenth, hat(rng, 0.03, 120), 0.035, 0.05)
            add(t + 3 * sixteenth, hat(rng, 0.03, 120), 0.05, 0.035)
        if b == BARS - 1:
            for s in range(4):
                add(s0 + 3 * beat + s * sixteenth, c, 0.07 + 0.03 * s, 0.07 + 0.03 * s)

    # --- ベース ---
    for b, (root, chord) in enumerate(chords):
        s0 = b * bar
        bf = mfreq(root)
        style = p["bass"]
        if style == "octave":
            events = [(e * eighth, bf * (2 if e % 2 else 1), eighth * 0.8) for e in range(8)]
        elif style == "root8":
            events = [(e * eighth, bf, eighth * 0.75) for e in range(8)]
        elif style == "syncopated":
            events = [(x * sixteenth, bf * m, sixteenth * l)
                      for x, m, l in ((0, 1, 2.6), (3, 1, 1.6), (6, 2, 1.6), (8, 1, 2.6), (11, 1, 1.6), (14, 1.5, 1.6))]
        else:  # walking: ルート→3度→5度→オクターブ
            steps = [chord[0] - 12, chord[1] - 12, chord[2] - 12, chord[0]]
            while steps[0] - 12 >= root - 1:
                steps = [s - 12 for s in steps]
            events = [(q * beat, mfreq(steps[q]), beat * 0.85) for q in range(4)]
        for t, f, length in events:
            add(s0 + t, bass_note(f, length), 0.11, 0.11)

    # --- アルペジオ ---
    arp = ARP_PATTERNS[p["arp"]]
    cache: dict[int, list[float]] = {}
    for b, (_, chord) in enumerate(chords):
        s0 = b * bar
        notes = list(chord) + [chord[0] + 12]
        for s in range(16):
            a = arp[s % 8]
            if a < 0:
                continue
            m = notes[a] + 12
            if m not in cache:
                cache[m] = tone(mfreq(m), 0.25, p["arp_timbre"])
            gain = 0.042 if s % 4 == 0 else 0.028
            pan = 0.45 if s % 2 == 0 else -0.45
            add(s0 + s * sixteenth, cache[m], gain * (1 - pan), gain * (1 + pan))

    # --- メロディ ---
    for pos, m, length in compose_melody(rng, chords, p["transpose"]):
        dur = min(length * eighth * 0.95, 0.9)
        add(pos * eighth, tone(mfreq(m), dur, p["lead_timbre"]), 0.060, 0.060)

    peak = max(max(abs(v) for v in left), max(abs(v) for v in right)) or 1.0
    scale = 0.89 / peak
    for i in range(n):
        left[i] *= scale
        right[i] *= scale
    return left, right, loop_sec


def make_bgm(out_path: Path, index: int) -> Path:
    p = pattern_params(index)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    left, right, loop_sec = synthesize(p)
    loops = max(1, math.ceil(TARGET_SEC / loop_sec))
    with tempfile.TemporaryDirectory(prefix="newsbgm_") as tmp:
        raw = Path(tmp) / "raw.wav"
        write_wav(raw, left, right)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-stream_loop", str(loops - 1), "-i", str(raw),
             "-af",
             "aecho=0.8:0.4:50|95:0.15|0.08,"
             "highpass=f=35,lowpass=f=12000,"
             "loudnorm=I=-16:TP=-1.5:LRA=7",
             "-ar", str(SR), "-t", f"{loop_sec * loops:.4f}",
             "-c:a", "libmp3lame", "-b:a", "192k", str(out_path)],
            check=True,
        )
    print(f"BGM生成: {out_path} (pattern={index}, {p['bpm']}BPM, transpose={p['transpose']:+d}, "
          f"prog={p['progression']}, drum={p['drum']}, bass={p['bass']}, chord={p['chord']}, "
          f"arp={p['arp']}/{p['arp_timbre']}, lead={p['lead_timbre']})")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", nargs="?", default="assets/bgm")
    ap.add_argument("--pattern", type=int, help=f"0〜{PATTERN_COUNT - 1} の番号を指定")
    ap.add_argument("--all", action="store_true", help="全パターンを生成（試聴用）")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)

    if args.all:
        indices = list(range(PATTERN_COUNT))
    elif args.pattern is not None:
        indices = [args.pattern % PATTERN_COUNT]
    else:
        indices = [random.randrange(PATTERN_COUNT)]
    for i in indices:
        make_bgm(out_dir / f"news_bright_{i:03d}.mp3", i)


if __name__ == "__main__":
    main()
