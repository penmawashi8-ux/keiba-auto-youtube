#!/usr/bin/env python3
"""効果音とフォールバックBGMを ffmpeg だけで合成する（Pillow/numpy不使用）。

音源をどこからも取ってこないので、ライセンスの心配も、ネットワーク制限で
落ちてこない心配もない。生成物は assets/sfx/ と assets/bgm/ に置く。

  impact.wav … 着順が出るときの「バーン」（中音域の落下＋倍音＋アタック）
  tick.wav   … カードの箇条書きが1行出るときの軽い「トッ」
  whoosh.wav … 章タイトルの登場音

BGMはここでは作らない。scripts/download_bgm.py が archive.org から取ってくる
CC0のクラシック音源だけを使う（合成パッドを敷くくらいならBGMなしのほうがマシ）。

  python scripts/make_sfx.py            # 効果音を作る
"""

import argparse
import subprocess
import sys
from pathlib import Path

SFX_DIR = Path("assets/sfx")


def _run(args: list[str]) -> bool:
    r = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-700:], file=sys.stderr)
    return r.returncode == 0


def make_impact(dest: Path) -> bool:
    """「バーン」。160Hz→45Hz へ落ちるサブベースに、短いアタックを重ねる。

    aevalsrc の位相を t の2次式にすると瞬間周波数が直線的に下がるので、
    サンプル音源なしでも打撃音になる。

    以前は 160→45Hz のサブベースだったが、音程が低すぎてスマホやノートPCの
    スピーカーでは「ボフッ」としか鳴らず聞き取りにくかった。中音域まで
    持ち上げて（440→170Hz）、上に倍音と金属質のアタックを足している。
    """
    return _run([
        # 主音。瞬間周波数 = 440 - 540t Hz（0.5秒で440→170Hz）
        "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*(440*t-270*t*t))':d=0.6:s=44100",
        # 1オクターブ上。抜けを作って小さいスピーカーでも輪郭が出るようにする
        "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*(880*t-540*t*t))':d=0.6:s=44100",
        # 土台のローエンド（鳴らしすぎない程度に厚みだけ足す）
        "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*(150*t-90*t*t))':d=0.6:s=44100",
        # 打撃のアタック
        "-f", "lavfi", "-i", "anoisesrc=d=0.18:c=pink:a=0.9:r=44100",
        "-filter_complex",
        "[0:a]afade=t=out:st=0.02:d=0.5:curve=exp,volume=1.0[mid];"
        "[1:a]afade=t=out:st=0:d=0.3:curve=exp,volume=0.45[hi];"
        "[2:a]afade=t=out:st=0.02:d=0.45:curve=exp,volume=0.5[low];"
        "[3:a]afade=t=out:st=0:d=0.14:curve=exp,highpass=f=700,volume=0.55[atk];"
        "[mid][hi][low][atk]amix=inputs=4:duration=longest:normalize=0,"
        "highpass=f=90,lowpass=f=9000,alimiter=limit=0.9,"
        "aformat=sample_fmts=s16:sample_rates=44100:channel_layouts=stereo",
        "-t", "0.6", str(dest),
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
    argparse.ArgumentParser().parse_args()
    for name, path in ensure_sfx().items():
        print(f"  効果音: {name} → {path}")


if __name__ == "__main__":
    main()
