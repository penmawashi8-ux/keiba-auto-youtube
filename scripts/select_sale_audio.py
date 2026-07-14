#!/usr/bin/env python3
"""セレクトセールランキング動画用の音声・字幕・カード時刻を文単位TTSで生成する。

generate_audio.py（全文一括TTS + 推定字幕）は単語境界イベントが取れないと
字幕が文字数比例の推定になりナレーションとズレる。このビルダーは:

1. 脚本を文単位に分割し、1文ずつ個別にTTS生成する
2. 各クリップの実測時間（ffprobe）から正確なタイムラインを構築する
3. クリップを無音ギャップ入りで結合して audio_0.mp3 を作る
4. 字幕（subtitles_0.ass）は各文のクリップ境界そのものなので構造的にズレない
5. カード切替時刻（card_times_0.json）も同じタイムラインから出力する

また、脚本の見出し行（【第N位】等）は読み上げ対象から除外するため、
「第10位」を2回読む問題も起きない。

入力:  output/script_0.txt / output/ranking_meta_0.json
出力:  output/audio_0.mp3 / output/subtitles_0.ass / output/card_times_0.json
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import edge_tts

sys.path.insert(0, str(Path(__file__).parent))
from reading_utils import apply_readings
from generate_audio import (
    ASS_HEADER, detect_font_name, normalize_racing_terms, ticks_to_ass_time,
)

OUTPUT_DIR = "output"
VOICE = os.environ.get("TTS_VOICE", "ja-JP-KeitaNeural")
RATE = os.environ.get("TTS_RATE", "+10%")
GAP_S = 0.30  # 文間の無音（秒）
SAMPLE_RATE = 24000  # edge-tts のMP3出力に合わせる


# ---------------------------------------------------------------------------
# 脚本 → 発話ユニット
# ---------------------------------------------------------------------------

def build_units(script: str, meta: dict) -> list[dict]:
    """脚本を発話ユニット（1文=1クリップ）に分解する。

    見出し（【…】）は読み上げず、直後のユニットに chapter として付与する。
    Returns: [{"text": 字幕文, "chapter": "【第10位】" or None}, ...]
    """
    units: list[dict] = []
    intro_title = f"セレクトセール{meta['year']} {meta['session']}セール 高額落札ランキングTOP10。"
    units.append({"text": intro_title, "chapter": "【イントロ】"})

    for block in script.split("\n\n"):
        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        if not lines:
            continue
        header = lines[0] if lines[0].startswith("【") else None
        body = "".join(lines[1:] if header else lines)
        first = True
        for sent in re.split(r"(?<=[。！？])", body):
            sent = sent.strip()
            if not sent:
                continue
            units.append({"text": sent, "chapter": header if first else None})
            first = False
    return units


# ---------------------------------------------------------------------------
# TTS（文単位・リトライ付き）
# ---------------------------------------------------------------------------

async def _tts_one(text: str, path: str) -> None:
    comm = edge_tts.Communicate(text, VOICE, rate=RATE)
    with open(path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])


def tts_unit(text: str, path: str, attempts: int = 3) -> None:
    spoken = apply_readings(normalize_racing_terms(text))
    for i in range(attempts):
        try:
            asyncio.run(_tts_one(spoken, path))
            if Path(path).stat().st_size > 500:
                return
            raise RuntimeError("音声が空です")
        except Exception as e:
            if i == attempts - 1:
                raise RuntimeError(f"TTS失敗: {text[:30]}... ({e})")
            time.sleep(5 * (i + 1))


def audio_duration_s(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


# ---------------------------------------------------------------------------
# 結合・出力
# ---------------------------------------------------------------------------

def to_wav(src: str, dst: str) -> None:
    """MP3のエンコーダー遅延によるズレを避けるため、計測・結合はWAVで行う。"""
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ar", str(SAMPLE_RATE), "-ac", "1", dst],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"WAV変換失敗: {src}")


def concat_units(wav_paths: list[str], gap_wav: str, out_path: str) -> None:
    """WAVクリップを無音ギャップを挟んでサンプル精度で結合し、MP3化する。"""
    list_path = Path(gap_wav).parent / "concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for i, p in enumerate(wav_paths):
            f.write(f"file '{Path(p).resolve()}'\n")
            if i < len(wav_paths) - 1:
                f.write(f"file '{Path(gap_wav).resolve()}'\n")
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
         "-c:a", "libmp3lame", "-b:a", "128k", out_path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"[エラー] 音声結合失敗:\n{r.stderr[-500:]}", file=sys.stderr)
        sys.exit(1)


def wrap30(text: str) -> str:
    if len(text) <= 30:
        return text
    return "\n".join(text[i:i + 30] for i in range(0, len(text), 30))


def main() -> None:
    script = Path(f"{OUTPUT_DIR}/script_0.txt").read_text(encoding="utf-8").strip()
    meta = json.loads(Path(f"{OUTPUT_DIR}/ranking_meta_0.json").read_text(encoding="utf-8"))

    units = build_units(script, meta)
    print(f"=== 文単位TTS: {len(units)} ユニット (voice={VOICE}, rate={RATE}) ===")

    tmp = tempfile.mkdtemp(prefix="ssaudio_")
    wav_paths: list[str] = []
    for i, u in enumerate(units):
        p = f"{tmp}/u{i:03d}.mp3"
        tts_unit(u["text"], p)
        w = f"{tmp}/u{i:03d}.wav"
        to_wav(p, w)
        wav_paths.append(w)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(units)} 文完了")

    # 無音ギャップ（WAV・サンプル精度）
    gap_path = f"{tmp}/gap.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"anullsrc=r={SAMPLE_RATE}:cl=mono", "-t", f"{GAP_S}", gap_path],
        capture_output=True,
    )
    gap_dur = audio_duration_s(gap_path)

    # 実測時間からタイムライン構築（WAVなので誤差なし）
    t = 0.0
    segments: list[dict] = []
    chapter_starts: list[tuple[str, float]] = []
    for u, p in zip(units, wav_paths):
        d = audio_duration_s(p)
        if u["chapter"]:
            chapter_starts.append((u["chapter"], t))
        segments.append({
            "start": int(t * 10_000_000),
            "end": int((t + d) * 10_000_000),
            "text": wrap30(u["text"]),
        })
        t += d + gap_dur
    total = t - gap_dur

    # 結合
    audio_path = f"{OUTPUT_DIR}/audio_0.mp3"
    concat_units(wav_paths, gap_path, audio_path)
    actual = audio_duration_s(audio_path)
    drift = abs(actual - total)
    print(f"  タイムライン: {total:.2f}s / 結合音声実測: {actual:.2f}s (差 {drift:.3f}s)")
    if drift > 0.5:
        print(f"  [警告] 結合音声とタイムラインの差が大きい: {drift:.2f}s", file=sys.stderr)

    # ASS字幕（クリップ境界そのもの）
    ass_path = f"{OUTPUT_DIR}/subtitles_0.ass"
    font_name = detect_font_name()
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER.format(font_name=font_name))
        for seg in segments:
            ass_text = seg["text"].replace("\n", "\\N")
            f.write(
                f"Dialogue: 0,{ticks_to_ass_time(seg['start'])},"
                f"{ticks_to_ass_time(seg['end'])},Default,,0,0,0,,{ass_text}\n"
            )
    print(f"  字幕: {ass_path} ({len(segments)} セグメント・クリップ境界に完全一致)")

    # カード切替時刻
    rank_start: dict[int, float] = {}
    outro_start = None
    for ch, ts in chapter_starts:
        m = re.match(r"【第(\d+)位】", ch)
        if m:
            rank_start[int(m.group(1))] = ts
        elif ch == "【まとめ】":
            outro_start = ts
    card = {
        "rank_start": rank_start,
        "outro_start": outro_start if outro_start is not None else total - 10.0,
        "total": total,
    }
    ct_path = f"{OUTPUT_DIR}/card_times_0.json"
    Path(ct_path).write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  カード時刻: {ct_path} (順位 {len(rank_start)} 件, まとめ {card['outro_start']:.1f}s)")

    if len(rank_start) < len(meta.get("ranking", [])):
        print("[エラー] 順位チャプターが不足しています。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
