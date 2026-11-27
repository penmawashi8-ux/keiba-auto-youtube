#!/usr/bin/env python3
"""ニュースのショートの声の聞こえやすさを調べる。今の混ぜ方と改善案で同じナレーションを混ぜ、
音量（LUFS）と「声とBGMの差」を測り、聞き比べ用の音声を書き出す。

使い方: python scripts/news_audio_check.py <ナレーション mp3 のディレクトリ> <BGM mp3> <出力ディレクトリ>
"""
import json
import re
import subprocess
import sys
from pathlib import Path


def lufs(path_or_filter_args):
    r = subprocess.run(["ffmpeg", "-hide_banner", *path_or_filter_args, "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m else None


def mix(voice, bgm, out, mode, dur):
    if mode == "now":       # 今の generate_video.py と同じ（amix weights=1 0.2、音量の仕上げなし）
        fc = f"[0:a]apad=whole_dur={dur:.2f}[n];[n][1:a]amix=inputs=2:duration=first:weights=1 0.2[a]"
    else:                   # 改善案
        fc = (f"[0:a]apad=whole_dur={dur:.2f},highpass=f=80,acompressor=threshold=-20dB:ratio=3:attack=5:release=80,"
              f"asplit=2[n][key];"
              # BGM: 声の帯域（1〜4kHz）を下げ、声が出ている間はさらに下げる（ダッキング）
              f"[1:a]equalizer=f=2500:t=q:w=1.0:g=-6,volume=0.14[b];"
              f"[b][key]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=350[bd];"
              f"[n][bd]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=9[a]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(voice), "-stream_loop", "-1", "-i", str(bgm),
                    "-filter_complex", fc, "-map", "[a]", "-t", f"{dur:.2f}", "-c:a", "aac", "-b:a", "192k", str(out)],
                   check=True)


def main():
    vdir, bgm, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    rep = []
    for v in sorted(vdir.glob("audio_*.mp3"))[:3]:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(v)],
                                   capture_output=True, text=True).stdout) + 1.0
        row = {"voice": v.name, "voice_lufs": lufs(["-i", str(v)]), "bgm_lufs": lufs(["-i", str(bgm)])}
        for mode in ("now", "new"):
            f = out / f"{v.stem}_{mode}.m4a"
            mix(v, bgm, f, mode, dur)
            row[f"{mode}_lufs"] = lufs(["-i", str(f)])
        # 声とBGMの差（今の混ぜ方: amix は重みの合計で割るので 声×1/1.2・BGM×0.2/1.2）
        if row["voice_lufs"] is not None and row["bgm_lufs"] is not None:
            row["now_voice_minus_bgm_db"] = round(row["voice_lufs"] - (row["bgm_lufs"] + 20 * __import__("math").log10(0.2)), 1)
        rep.append(row)
        print(json.dumps(row, ensure_ascii=False))
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
