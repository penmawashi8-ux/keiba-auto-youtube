#!/usr/bin/env python3
"""output/script_N.txt を読み込み、音声(audio_N.mp3)とASS字幕(subtitles_N.ass)を生成する。"""

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import edge_tts

try:
    from kokoro import KPipeline
    import numpy as np
    import soundfile as sf
    _KOKORO_AVAILABLE = True
    print("Kokoro TTS が利用可能です。")
except ImportError:
    _KOKORO_AVAILABLE = False
    print("Kokoro TTS が見つかりません。edge-tts にフォールバックします。")

OUTPUT_DIR = "output"
NEWS_JSON = "news.json"
VOLUME = "+0%"

# 競馬用語の読み替えパターン（長いものを先に）
_RACING_TERM_REPLACEMENTS = [
    (re.compile(r'GIII|GⅢ'), 'ジースリー'),
    (re.compile(r'GII|GⅡ'), 'ジーツー'),
    (re.compile(r'GI|GⅠ'), 'ジーワン'),
    (re.compile(r'G3'), 'ジースリー'),
    (re.compile(r'G2'), 'ジーツー'),
    (re.compile(r'G1'), 'ジーワン'),
    (re.compile(r'(\d+)R'), r'\1レース'),
]


def normalize_racing_terms(text: str) -> str:
    """GI/GII/GIII・数字Rなど競馬用語の読み上げを正規化する。"""
    for pattern, repl in _RACING_TERM_REPLACEMENTS:
        text = pattern.sub(repl, text)
    return text

# edge-tts フォールバック用ボイスプール（確認済み有効ボイスのみ）
_EDGE_VOICE_POOL = ["ja-JP-KeitaNeural", "ja-JP-NanamiNeural"]

# Kokoro 日本語ボイスプール
_KOKORO_VOICE_POOL = ["jf_alpha", "jf_beta", "jm_alpha"]
_kokoro_pipeline: "KPipeline | None" = None


def _get_kokoro_pipeline() -> "KPipeline":
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        _kokoro_pipeline = KPipeline(lang_code="j")
    return _kokoro_pipeline


def generate_audio_kokoro(text: str, audio_path: str, voice: str, speed: float) -> None:
    """Kokoro TTS で音声を生成して MP3 に変換する。"""
    pipeline = _get_kokoro_pipeline()
    samples = []
    for _, _, audio in pipeline(text, voice=voice, speed=speed):
        samples.append(audio)
    if not samples:
        raise RuntimeError("Kokoro から音声データが得られませんでした")
    audio_data = np.concatenate(samples)
    wav_path = audio_path + ".wav"
    sf.write(wav_path, audio_data, 24000)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libmp3lame", "-b:a", "128k", audio_path],
        capture_output=True,
    )
    Path(wav_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg MP3変換失敗: {result.stderr[-200:]}")


def pick_tts_params() -> tuple[str, str, float, float]:
    """ランダムなTTSパラメータを返す (voice, rate_str, pitch_factor, volume_db)。
    TTS_VOICE / TTS_RATE 環境変数が設定されている場合はそちらを優先する。"""
    forced_voice = os.environ.get("TTS_VOICE", "")
    if forced_voice:
        voice = forced_voice
    elif _KOKORO_AVAILABLE:
        voice = random.choice(_KOKORO_VOICE_POOL)
    else:
        voice = random.choice(_EDGE_VOICE_POOL)

    forced_rate = os.environ.get("TTS_RATE", "")
    if forced_rate:
        rate_str = forced_rate
    else:
        rate_pct = random.randint(-15, 15)
        rate_str = f"{rate_pct:+d}%"

    # ピッチ: ±2.0 半音 → 係数変換
    pitch_semitones = random.uniform(-2.0, 2.0)
    pitch_factor = 2 ** (pitch_semitones / 12)

    # 音量: ±1.5 dB
    volume_db = random.uniform(-1.5, 1.5)

    return voice, rate_str, pitch_factor, volume_db


_readings_cache: dict | None = None


def apply_readings(text: str) -> str:
    """data/readings.json の辞書を使ってTTSの誤読を補正する。
    騎手名などカナ読みに置換してから TTS に渡すことで正確な発音を得る。
    長いキーを優先して処理することで部分一致による誤置換を防ぐ。"""
    global _readings_cache
    if _readings_cache is None:
        readings_path = Path("data/readings.json")
        if readings_path.exists():
            try:
                _readings_cache = json.loads(readings_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  [警告] readings.json 読み込み失敗: {e}", file=sys.stderr)
                _readings_cache = {}
        else:
            _readings_cache = {}
    if not _readings_cache:
        return text
    # 長いキーから順に置換（部分一致で短い語が先に置換されるのを防ぐ）
    for kanji in sorted(_readings_cache, key=len, reverse=True):
        reading = _readings_cache[kanji]
        if isinstance(reading, str) and kanji in text:
            text = text.replace(kanji, reading)
    return text


def apply_audio_variation(audio_path: str, pitch_factor: float, volume_db: float) -> None:
    """ffmpegでピッチ・音量をわずかに変化させて毎回異なる音声を生成する。"""
    if abs(pitch_factor - 1.0) < 0.001 and abs(volume_db) < 0.05:
        return  # 変化量が極小の場合はスキップ
    tmp_path = audio_path + ".tmp.mp3"
    sr = 24000
    new_sr = int(sr * pitch_factor)
    tempo = 1.0 / pitch_factor   # ピッチ変化で生じる速度変化を補正
    volume_factor = 10 ** (volume_db / 20)
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-af",
        f"asetrate={new_sr},aresample={sr},atempo={tempo:.6f},volume={volume_factor:.5f}",
        "-c:a", "libmp3lame", "-b:a", "128k",
        tmp_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        Path(tmp_path).replace(Path(audio_path))
        print(f"  音声バリエーション適用: pitch×{pitch_factor:.4f} vol{volume_db:+.2f}dB")
    else:
        print(f"  [警告] 音声バリエーション適用失敗: {result.stderr[-200:]}", file=sys.stderr)
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()

# ASS字幕ファイルのヘッダーテンプレート（PlayResX/Y=実際の動画サイズ）
ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},58,&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,1,4,1,2,20,20,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ticks_to_ass_time(ticks: int) -> str:
    """100ナノ秒単位 → ASS時刻（H:MM:SS.cc）"""
    cs = ticks // 100_000
    s, cs = divmod(cs, 100)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def words_to_segments(words: list[dict], max_chars: int = 22) -> list[dict]:
    """ワード境界リスト → 字幕セグメントリスト"""
    segments: list[dict] = []
    current: list[dict] = []

    for word in words:
        current.append(word)
        text_so_far = "".join(w["text"] for w in current)
        ends = bool(re.search(r"[。！？\n]$", word["text"]))
        if ends or len(text_so_far) >= max_chars:
            text = "".join(w["text"] for w in current).strip()
            if text:
                segments.append({
                    "start": current[0]["offset"],
                    "end": current[-1]["offset"] + current[-1]["duration"],
                    "text": text,
                })
            current = []

    if current:
        text = "".join(w["text"] for w in current).strip()
        if text:
            segments.append({
                "start": current[0]["offset"],
                "end": current[-1]["offset"] + current[-1]["duration"],
                "text": text,
            })
    return segments


def _audio_duration_s(audio_path: str) -> float:
    """音声ファイルの長さを秒で返す（ffprobe使用）。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 60.0


def _estimate_subtitle_segments(text: str, total_duration: float, max_chars: int = 22) -> list[dict]:
    """テキストと音声長から字幕セグメントを近似生成する（Kokoro TTS用）。
    ワードタイミングが得られない場合に文字数比率でタイミングを推定する。
    """
    parts = [p.strip() for p in re.split(r"(?<=[。！？、\n])", text) if p.strip()]
    if not parts:
        return []

    total_chars = max(sum(len(p) for p in parts), 1)
    segments: list[dict] = []
    current_t = 0.0

    for part in parts:
        part_dur = (len(part) / total_chars) * total_duration
        remaining_dur = part_dur
        while len(part) > max_chars:
            chunk = part[:max_chars]
            chunk_dur = part_dur * (max_chars / len(part))
            segments.append({
                "start": int(current_t * 10_000_000),
                "end": int((current_t + chunk_dur) * 10_000_000),
                "text": chunk,
            })
            current_t += chunk_dur
            remaining_dur -= chunk_dur
            part_dur = remaining_dur
            part = part[max_chars:]
        if part:
            segments.append({
                "start": int(current_t * 10_000_000),
                "end": int((current_t + remaining_dur) * 10_000_000),
                "text": part,
            })
        current_t += remaining_dur if part else 0

    return segments


def write_ass(segments: list[dict], path: str, font_name: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER.format(font_name=font_name))
        for seg in segments:
            start = ticks_to_ass_time(seg["start"])
            end = ticks_to_ass_time(seg["end"])
            text = seg["text"].replace("\n", "\\N")
            f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")


def detect_font_name() -> str:
    """インストール済みのNoto CJKフォント名を返す。"""
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return "Noto Sans CJK JP"
    return "Sans"


async def generate_audio_and_subtitles(
    script: str, audio_path: str, ass_path: str, font_name: str,
    voice: str = "ja-JP-KeitaNeural", rate: str = "+0%",
) -> None:
    communicate = edge_tts.Communicate(script, voice, rate=rate, volume=VOLUME)
    words: list[dict] = []

    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "text": chunk["text"],
                    "offset": chunk["offset"],
                    "duration": chunk["duration"],
                })

    segments = words_to_segments(words)

    # edge-tts が WordBoundary イベントを返さない場合のフォールバック
    if not segments:
        print("  [警告] WordBoundaryイベントなし。音声長から字幕タイミングを推定します。",
              file=sys.stderr)
        aud_dur = _audio_duration_s(audio_path)
        segments = _estimate_subtitle_segments(script, aud_dur)

    write_ass(segments, ass_path, font_name)

    size_kb = Path(audio_path).stat().st_size // 1024
    print(f"  音声: {audio_path} ({size_kb} KB)")
    print(f"  字幕: {ass_path} ({len(segments)} セグメント)")


def main() -> None:
    print("=== 音声生成開始 ===")

    script_files = sorted(Path(OUTPUT_DIR).glob("script_*.txt"))
    if not script_files:
        print(f"[エラー] {OUTPUT_DIR}/script_*.txt が見つかりません。", file=sys.stderr)
        sys.exit(1)

    # news.json からタイトルを取得（サムネイルタイトルの読み上げ用）
    news_path = Path(NEWS_JSON)
    news_items: list[dict] = json.loads(news_path.read_text(encoding="utf-8")) if news_path.exists() else []

    font_name = detect_font_name()
    print(f"字幕フォント: {font_name}")

    def _generate_one(script_file: Path) -> str:
        idx = script_file.stem.split("_")[1]
        script = script_file.read_text(encoding="utf-8").strip()
        if not script:
            print(f"  [警告] {script_file} が空です。スキップします。")
            return idx

        idx_int = int(idx)
        title = news_items[idx_int].get("title", "") if idx_int < len(news_items) else ""
        narration_text = (title + "。" + script) if title else script
        if title:
            print(f"  [{idx}] タイトル読み上げ追加: 「{title[:40]}」")

        audio_path = f"{OUTPUT_DIR}/audio_{idx}.mp3"
        ass_path = f"{OUTPUT_DIR}/subtitles_{idx}.ass"

        narration_text = normalize_racing_terms(narration_text)
        narration_text = apply_readings(narration_text)
        voice, rate, pitch_factor, volume_db = pick_tts_params()
        is_kokoro_voice = voice in _KOKORO_VOICE_POOL
        engine = "Kokoro" if (_KOKORO_AVAILABLE and is_kokoro_voice) else "edge-tts"
        print(f"\n--- 音声生成 [{idx}] ({len(narration_text)}文字) engine={engine} voice={voice} ---")
        for attempt in range(1, 4):
            try:
                if _KOKORO_AVAILABLE and is_kokoro_voice:
                    speed = 1.0 + (int(rate.replace("%", "").replace("+", "")) / 100)
                    generate_audio_kokoro(narration_text, audio_path, voice=voice, speed=speed)
                    aud_dur = _audio_duration_s(audio_path)
                    segs = _estimate_subtitle_segments(narration_text, aud_dur)
                    write_ass(segs, ass_path, font_name)
                else:
                    asyncio.run(generate_audio_and_subtitles(
                        narration_text, audio_path, ass_path, font_name,
                        voice=voice, rate=rate,
                    ))
                break
            except Exception as e:
                print(f"  [{idx}] 音声生成失敗 (attempt {attempt}/3): {e}", file=sys.stderr)
                if attempt == 1 and _KOKORO_AVAILABLE and is_kokoro_voice:
                    print(f"  [{idx}] Kokoro失敗。edge-tts にフォールバックします。", file=sys.stderr)
                    is_kokoro_voice = False
                    engine = "edge-tts"
                    voice = random.choice(_EDGE_VOICE_POOL)
                elif attempt < 3:
                    time.sleep(10 * attempt)
                else:
                    raise RuntimeError(f"音声生成を3回試みましたが失敗しました。idx={idx}")
        apply_audio_variation(audio_path, pitch_factor, volume_db)
        return idx

    max_workers = min(2, len(script_files))
    print(f"並列ワーカー数: {max_workers}")
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_generate_one, f): f for f in script_files}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"[エラー] {e}", file=sys.stderr)
                failed += 1

    if failed == len(script_files):
        print("[エラー] 全ての音声生成に失敗しました。", file=sys.stderr)
        sys.exit(1)
    print(f"\n{len(script_files) - failed}/{len(script_files)} 件の音声を生成しました。")


if __name__ == "__main__":
    main()
