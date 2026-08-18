#!/usr/bin/env python3
"""効果音とフォールバックBGMを ffmpeg だけで合成する（Pillow/numpy不使用）。

音源をどこからも取ってこないので、ライセンスの心配も、ネットワーク制限で
落ちてこない心配もない。生成物は assets/sfx/ と assets/bgm/ に置く。

  impact.wav … 着順が出るときの「バーン」（サブベースの落下＋アタック）
  tick.wav   … カードの箇条書きが1行出るときの軽い「トッ」
  whoosh.wav … 章タイトルの登場音

BGMは本来 scripts/download_bgm.py が archive.org の CC0 音源を取ってくる。
それが使えない環境向けのフォールバックとして、ここで簡単なパッドを合成する。

  python scripts/make_sfx.py            # 効果音だけ
  python scripts/make_sfx.py --bgm      # BGMが1本も無ければパッドも作る
"""

import argparse
import subprocess
import sys
from pathlib import Path

SFX_DIR = Path("assets/sfx")
BGM_DIR = Path("assets/bgm")


def _run(args: list[str]) -> bool:
    r = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-700:], file=sys.stderr)
    return r.returncode == 0


def make_impact(dest: Path) -> bool:
    """「バーン」。160Hz→45Hz へ落ちるサブベースに、短いアタックを重ねる。

    aevalsrc の位相を t の2次式にすると瞬間周波数が直線的に下がるので、
    サンプル音源なしでも太い打撃音になる。
    """
    return _run([
        # 落下するサブベース（瞬間周波数 = 160 - 192t Hz）
        "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*(160*t-96*t*t))':d=1.1:s=44100",
        # 胴鳴りの倍音（少し高い成分を足すとスマホでも聞こえる）
        "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*(320*t-192*t*t))':d=1.1:s=44100",
        # 打撃のアタック（ブラウンノイズを一瞬だけ）
        "-f", "lavfi", "-i", "anoisesrc=d=0.25:c=brown:a=0.9:r=44100",
        "-filter_complex",
        "[0:a]afade=t=out:st=0.05:d=1.05:curve=exp,volume=1.0[sub];"
        "[1:a]afade=t=out:st=0:d=0.45:curve=exp,volume=0.35[bod];"
        "[2:a]afade=t=out:st=0:d=0.22:curve=exp,highpass=f=200,volume=0.5[atk];"
        "[sub][bod][atk]amix=inputs=3:duration=longest:normalize=0,"
        "lowpass=f=4000,alimiter=limit=0.9,"
        "aformat=sample_fmts=s16:sample_rates=44100:channel_layouts=stereo",
        "-t", "1.1", str(dest),
    ])


def make_tick(dest: Path) -> bool:
    """箇条書きが1行出るときの軽い「トッ」。"""
    return _run([
        "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*(760*t-900*t*t))':d=0.3:s=44100",
        "-f", "lavfi", "-i", "anoisesrc=d=0.12:c=pink:a=0.5:r=44100",
        "-filter_complex",
        "[0:a]afade=t=out:st=0:d=0.28:curve=exp,volume=0.55[t];"
        "[1:a]afade=t=out:st=0:d=0.1:curve=exp,highpass=f=900,volume=0.3[n];"
        "[t][n]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.9,"
        "aformat=sample_fmts=s16:sample_rates=44100:channel_layouts=stereo",
        "-t", "0.3", str(dest),
    ])


def make_whoosh(dest: Path) -> bool:
    """章タイトルの登場音。ノイズを膨らませてから絞る。"""
    return _run([
        "-f", "lavfi", "-i", "anoisesrc=d=0.8:c=brown:a=0.8:r=44100",
        "-filter_complex",
        "[0:a]highpass=f=300,lowpass=f=2600,"
        "afade=t=in:st=0:d=0.35:curve=exp,afade=t=out:st=0.35:d=0.45:curve=exp,"
        "volume=0.7,alimiter=limit=0.9,"
        "aformat=sample_fmts=s16:sample_rates=44100:channel_layouts=stereo",
        "-t", "0.8", str(dest),
    ])


# フォールバックBGM: Am → F → C → G を1コード6秒で回す静かなパッド。
_CHORDS = [(220.0, 261.63, 329.63),      # Am
           (174.61, 220.0, 261.63),      # F
           (196.0, 261.63, 329.63),      # C
           (196.0, 246.94, 293.66)]      # G
_CHORD_DUR = 6.0


def make_pad_bgm(dest: Path) -> bool:
    """BGMが1本も無いときのフォールバック。合成のやわらかいパッド。"""
    parts = Path("/tmp/_gen25_pad")
    parts.mkdir(parents=True, exist_ok=True)
    files = []
    for i, (f1, f2, f3) in enumerate(_CHORDS):
        p = parts / f"c{i}.wav"
        expr = (f"0.30*sin(2*PI*{f1}*t)+0.22*sin(2*PI*{f2}*t)"
                f"+0.16*sin(2*PI*{f3}*t)+0.10*sin(2*PI*{f1 / 2}*t)")
        ok = _run([
            "-f", "lavfi", "-i", f"aevalsrc='{expr}':d={_CHORD_DUR}:s=44100",
            "-af", (f"afade=t=in:st=0:d=1.6,afade=t=out:st={_CHORD_DUR - 1.8}:d=1.8,"
                    "lowpass=f=1100,tremolo=f=0.22:d=0.25"),
            "-ac", "2", str(p),
        ])
        if not ok:
            return False
        files.append(p)

    lst = parts / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in files), encoding="utf-8")
    return _run([
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-af", ("aecho=0.8:0.85:420|780:0.28|0.18,lowpass=f=1400,"
                "loudnorm=I=-23:TP=-3:LRA=9"),
        "-c:a", "libmp3lame", "-b:a", "128k", "-ac", "2", str(dest),
    ])


def ensure_sfx() -> dict[str, str]:
    """効果音を（無ければ）作って、名前→パスの辞書を返す。"""
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    builders = {"impact": make_impact, "tick": make_tick, "whoosh": make_whoosh}
    out: dict[str, str] = {}
    for name, build in builders.items():
        dest = SFX_DIR / f"{name}.wav"
        if not dest.exists() or dest.stat().st_size < 1000:
            if not build(dest):
                print(f"  [警告] 効果音の生成に失敗: {name}", file=sys.stderr)
                continue
        out[name] = str(dest)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bgm", action="store_true",
                    help="BGMが1本も無ければ合成パッドを作る")
    args = ap.parse_args()

    for name, path in ensure_sfx().items():
        print(f"  効果音: {name} → {path}")

    if args.bgm:
        BGM_DIR.mkdir(parents=True, exist_ok=True)
        if any(BGM_DIR.glob("*.mp3")):
            print("  BGM: 既存の音源があるので合成はしません")
        else:
            dest = BGM_DIR / "pad_fallback.mp3"
            print("  BGM: 音源が無いので合成パッドを作ります →", dest)
            if not make_pad_bgm(dest):
                print("  [警告] BGMの合成に失敗しました", file=sys.stderr)


if __name__ == "__main__":
    main()
