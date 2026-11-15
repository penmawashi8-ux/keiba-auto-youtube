#!/usr/bin/env python3
"""
ニュースShorts用の明るいオリジナルBGMを合成する（著作権・Content ID リスクなし）。

以前は download_bgm.py で Musopen のクラシック（ノクターン等）を流していたが、
曲調が暗くニュースに合わなかったため、長調・アップテンポの曲を自前で生成する。

構成: 124BPM・長調の8小節ループ（I-V-vi-IV-I-V-IV-V）
  明るいパッド＋跳ねるベース＋4つ打ちキック/クラップ/ハイハット＋ベル系アルペジオ
キーを変えた数パターンを作り、generate_video.py がランダムに1曲選ぶ。

numpy / Pillow は使わず、純Pythonで波形を作り ffmpeg で仕上げる（make_prediction_bgm.py と同方式）。

使い方:
  python scripts/make_news_bgm.py [出力ディレクトリ]   # 既定: assets/bgm
"""

import math
import random
import subprocess
import sys
import tempfile
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_prediction_bgm import SR, TWO_PI, clap, hat, kick, mfreq, write_wav  # noqa: E402

BPM = 124
BEAT = 60.0 / BPM
BAR = BEAT * 4
BARS = 8
LOOP_SEC = BAR * BARS
N = int(round(LOOP_SEC * SR))
# MP3はエンコーダ遅延で継ぎ目に僅かな無音が入るため、Shorts1本分(約62秒)を1ファイルに収める
LOOPS_PER_FILE = 4

# Cメジャー基準 (ベースMIDI, 和音MIDI3音)  I-V-vi-IV-I-V-IV-V
BASE_PROGRESSION = [
    (48, (60, 64, 67)),  # C
    (43, (59, 62, 67)),  # G
    (45, (60, 64, 69)),  # Am
    (41, (60, 65, 69)),  # F
    (48, (60, 64, 67)),  # C
    (43, (59, 62, 67)),  # G
    (41, (60, 65, 69)),  # F
    (43, (59, 62, 67)),  # G
]

# (ファイル名, 半音移調, 乱数シード)
VARIANTS = [
    ("news_bright_1.mp3", 0, 1101),   # C
    ("news_bright_2.mp3", 2, 1102),   # D
    ("news_bright_3.mp3", -3, 1103),  # A
]


def _add(left: array, right: array, start: float, samples: list[float],
         gl: float, gr: float) -> None:
    s0 = int(start * SR)
    for i, v in enumerate(samples):
        j = (s0 + i) % N
        left[j] += v * gl
        right[j] += v * gr


def bell(freq: float, length: float = 0.28) -> list[float]:
    """明るいベル/マリンバ系の短い音。"""
    out = []
    for i in range(int(length * SR)):
        t = i / SR
        env = math.exp(-t * 14) * min(1.0, t * 600)
        out.append(env * (math.sin(TWO_PI * freq * t)
                          + 0.45 * math.sin(TWO_PI * 2 * freq * t) * math.exp(-t * 10)
                          + 0.18 * math.sin(TWO_PI * 4 * freq * t) * math.exp(-t * 25)))
    return out


def bass_note(freq: float, length: float) -> list[float]:
    out = []
    for i in range(int(length * SR)):
        t = i / SR
        env = min(1.0, t * 300) * (0.4 + 0.6 * math.exp(-t * 9))
        env *= min(1.0, (length - t) * 80)
        out.append(env * (math.sin(TWO_PI * freq * t) + 0.35 * math.sin(TWO_PI * 2 * freq * t)))
    return out


def synthesize(transpose: int, seed: int) -> tuple[array, array]:
    rng = random.Random(seed)
    prog = [(r + transpose, tuple(m + transpose for m in c)) for r, c in BASE_PROGRESSION]
    left = array("f", [0.0]) * N
    right = array("f", [0.0]) * N

    # パッド（スタブ気味に8分の裏で刻んで軽快に）
    eighth = BEAT / 2
    sixteenth = BEAT / 4
    for b, (_, chord) in enumerate(prog):
        s0 = b * BAR
        freqs = [mfreq(m) for m in chord]
        for e in range(8):
            if e % 2 == 0:
                continue  # 裏拍のみ
            length = eighth * 0.85
            samples_l, samples_r = [], []
            for i in range(int(length * SR)):
                t = i / SR
                env = min(1.0, t * 250) * math.exp(-t * 7) * min(1.0, (length - t) * 80)
                vl = vr = 0.0
                for f in freqs:
                    for h, a in ((1, 1.0), (2, 0.3), (3, 0.12)):
                        vl += a * math.sin(TWO_PI * f * h * 0.998 * t)
                        vr += a * math.sin(TWO_PI * f * h * 1.002 * t)
                samples_l.append(vl * env)
                samples_r.append(vr * env)
            _add(left, right, s0 + e * eighth, samples_l, 0.050, 0.0)
            _add(left, right, s0 + e * eighth, samples_r, 0.0, 0.050)

    k = kick()
    c = clap(rng)
    # メロディ風アルペジオ（上昇パターンで明るく）
    arp_order = [0, 1, 2, 3, 2, 1, 2, 3]  # 3 = ルートの1オクターブ上

    for b, (root, chord) in enumerate(prog):
        s0 = b * BAR
        for beat in range(4):
            t = s0 + beat * BEAT
            _add(left, right, t, k, 0.30, 0.30)                    # 4つ打ち
            if beat in (1, 3):
                _add(left, right, t, c, 0.15, 0.15)
            _add(left, right, t + eighth, hat(rng, 0.07, 45), 0.11, 0.08)   # オープン気味
            _add(left, right, t + sixteenth, hat(rng, 0.03, 120), 0.04, 0.06)
            _add(left, right, t + 3 * sixteenth, hat(rng, 0.03, 120), 0.06, 0.04)
        if b == BARS - 1:
            for s in range(4):
                _add(left, right, s0 + 3 * BEAT + s * sixteenth, c,
                     0.07 + 0.03 * s, 0.07 + 0.03 * s)

        # ベース（ルート→オクターブで跳ねる）
        bf = mfreq(root)
        for e in range(8):
            f = bf * (2 if e % 2 == 1 else 1)
            _add(left, right, s0 + e * eighth, bass_note(f, eighth * 0.8), 0.11, 0.11)

        # アルペジオ（16分、1オクターブ上）
        notes = list(chord) + [chord[0] + 12]
        for s in range(16):
            m = notes[arp_order[s % 8]] + 12
            gain = 0.060 if s % 4 == 0 else 0.040
            pan = 0.4 if s % 2 == 0 else -0.4
            _add(left, right, s0 + s * sixteenth, bell(mfreq(m)),
                 gain * (1 - pan), gain * (1 + pan))

    peak = max(max(abs(v) for v in left), max(abs(v) for v in right)) or 1.0
    scale = 0.89 / peak
    for i in range(N):
        left[i] *= scale
        right[i] *= scale
    return left, right


def make_bgm(out_path: Path, transpose: int, seed: int) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    left, right = synthesize(transpose, seed)
    with tempfile.TemporaryDirectory(prefix="newsbgm_") as tmp:
        raw = Path(tmp) / "raw.wav"
        write_wav(raw, left, right)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-stream_loop", str(LOOPS_PER_FILE - 1), "-i", str(raw),
             "-af",
             "aecho=0.8:0.4:50|95:0.15|0.08,"
             "highpass=f=35,lowpass=f=12000,"
             "loudnorm=I=-16:TP=-1.5:LRA=7",
             "-ar", str(SR), "-t", f"{LOOP_SEC * LOOPS_PER_FILE:.4f}",
             "-c:a", "libmp3lame", "-b:a", "192k", str(out_path)],
            check=True,
        )
    print(f"BGM生成: {out_path} ({LOOP_SEC:.2f}秒ループ, {BPM}BPM, transpose={transpose:+d})")
    return out_path


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "assets/bgm")
    for name, transpose, seed in VARIANTS:
        make_bgm(out_dir / name, transpose, seed)


if __name__ == "__main__":
    main()
