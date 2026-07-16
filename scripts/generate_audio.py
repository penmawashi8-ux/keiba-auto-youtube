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

# 読み補正（騎手名の省略表記・姓のみ表記にも対応）は共通モジュールに集約
from reading_utils import apply_readings

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


def normalize_racing_terms(text: str, track: list | None = None) -> str:
    """GI/GII/GIII・数字Rなど競馬用語の読み上げを正規化する。

    track にリストを渡すと (かな, 元表記) を追記する（字幕の漢字復元用）。
    """
    for pattern, repl in _RACING_TERM_REPLACEMENTS:
        if track is not None and "\\" not in repl:
            def _sub(m, _repl=repl):
                track.append((_repl, m.group(0)))
                return _repl
            text = pattern.sub(_sub, text)
        else:
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
        rate_pct = random.randint(20, 30)
        rate_str = f"{rate_pct:+d}%"

    # ピッチ: ±2.0 半音 → 係数変換
    pitch_semitones = random.uniform(-2.0, 2.0)
    pitch_factor = 2 ** (pitch_semitones / 12)

    # 音量: ±1.5 dB
    volume_db = random.uniform(-1.5, 1.5)

    return voice, rate_str, pitch_factor, volume_db


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


# この文字の直後なら字幕を区切ってよい（読点・スペース・閉じ括弧）
_SOFT_BREAK_CHARS = "、。！？」』）　 "


def words_to_segments(words: list[dict], max_chars: int = 22) -> list[dict]:
    """ワード境界リスト → 字幕セグメントリスト。

    文末（。！？）で必ず区切り、長すぎる場合は読点・スペースの直後まで
    さかのぼって区切る。区切り位置が見つからない場合も数字の並びの
    途中（「ラスト1|1秒」など）では絶対に切らない。
    """
    segments: list[dict] = []
    current: list[dict] = []

    def flush(upto: int | None = None) -> None:
        nonlocal current
        take = current if upto is None else current[:upto]
        rest = [] if upto is None else current[upto:]
        # 行頭に読点・句点が来ると不自然なので取り除く
        text = "".join(w["text"] for w in take).strip().lstrip("、。")
        if take and text:
            segments.append({
                "start": take[0]["offset"],
                "end": take[-1]["offset"] + take[-1]["duration"],
                "text": text,
            })
        current = rest

    for word in words:
        current.append(word)
        text_so_far = "".join(w["text"] for w in current)
        if re.search(r"[。！？\n]\s*$", word["text"]):
            flush()
            continue
        if len(text_so_far) < max_chars:
            continue

        # ちょうど読点・スペースで終わっている場合はそのまま区切る
        if word["text"].rstrip()[-1:] in _SOFT_BREAK_CHARS:
            flush()
            continue

        # 読点・スペース直後で切れる最後の位置（セグメントが短くなりすぎない範囲）
        best = None
        acc = 0
        for i, w in enumerate(current[:-1]):
            acc += len(w["text"])
            if w["text"].rstrip()[-1:] in _SOFT_BREAK_CHARS and acc >= max_chars * 0.4:
                best = i + 1
        if best is None:
            # 数字同士の境界（金額・タイム表記の途中）と開き括弧の直後を避けて
            # 後ろから探す
            for i in range(len(current) - 1, 0, -1):
                prev, nxt = current[i - 1]["text"], current[i]["text"]
                if not prev or not nxt:
                    continue
                if prev[-1].isdigit() and nxt[0].isdigit():
                    continue
                if prev[-1] in "「『（(":
                    continue
                best = i
                break
        if best:
            flush(best)

    flush()
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


def _split_long_line(part: str, max_chars: int) -> list[str]:
    """長い文を読点・スペース優先で max_chars 以下のチャンクに分割する。

    区切り候補がない場合も数字の並びの途中（「ラスト1|1秒」など）では切らない。
    """
    chunks: list[str] = []
    while len(part) > max_chars:
        window = part[: max_chars + 1]
        cut = -1
        for i in range(len(window) - 1, int(max_chars * 0.4), -1):
            if window[i - 1] in _SOFT_BREAK_CHARS:
                cut = i
                break
        if cut <= 0:
            cut = max_chars
            while cut > 1 and (
                (part[cut - 1].isdigit() and part[cut].isdigit())
                or part[cut - 1] in "「『（("
            ):
                cut -= 1
        chunks.append(part[:cut].strip())
        part = part[cut:].strip()
    if part:
        chunks.append(part)
    return chunks


def _estimate_subtitle_segments(text: str, total_duration: float, max_chars: int = 26) -> list[dict]:
    """テキストと音声長から字幕セグメントを近似生成する。

    句点（。！？\n）で文に分け、長い文は読点・スペース優先で分割する
    （機械的な文字数ぶつ切りで語や数字が泣き別れになるのを防ぐ）。
    """
    parts = [p.strip() for p in re.split(r"(?<=[。！？\n])", text) if p.strip()]
    if not parts:
        return []

    chunks: list[str] = []
    for part in parts:
        chunks.extend(_split_long_line(part, max_chars))

    total_chars = max(sum(len(c) for c in chunks), 1)
    segments: list[dict] = []
    current_t = 0.0
    for chunk in chunks:
        chunk_dur = (len(chunk) / total_chars) * total_duration
        segments.append({
            "start": int(current_t * 10_000_000),
            "end": int((current_t + chunk_dur) * 10_000_000),
            "text": chunk,
        })
        current_t += chunk_dur

    return segments


def _save_chapter_timings(idx: str, words: list[dict]) -> None:
    """WordBoundaryイベントとpog_meta.jsonからチャプター開始時刻を計算してJSONに保存する。
    各チャプターのstripped_char_posとwordの累積文字位置を照合して正確な秒数を求める。
    """
    meta_path = Path("pog_meta.json")
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return
    chapters = meta.get("chapters", [])
    if not chapters:
        return

    # word → 累積文字位置 → 開始秒 のマッピングを構築
    cum_pos = 0
    char_times: list[tuple[int, float]] = []
    for w in words:
        char_times.append((cum_pos, w["offset"] / 10_000_000))
        cum_pos += len(w["text"])
    if not char_times:
        return

    result = []
    for ch in chapters:
        target = ch.get("stripped_char_pos", 0)
        # target以上の最初のwordの時刻を使う
        t_s = 0.0
        for pos, t in char_times:
            if pos >= target:
                t_s = t
                break
        else:
            t_s = char_times[-1][1]
        result.append({"title": ch["title"], "time_s": t_s})

    out = Path(OUTPUT_DIR) / f"chapter_timings_{idx}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  チャプタータイミング保存: {out} ({len(result)}件)")


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


async def _edge_tts_with_words(script: str, audio_path: str, voice: str, rate: str) -> list[dict]:
    """edge-tts で音声と WordBoundary イベントを同時取得する。
    戻り値: [{"text": ..., "offset": ...(100ns), "duration": ...(100ns)}, ...]
    """
    communicate = edge_tts.Communicate(script, voice, rate=rate, volume=VOLUME)
    words: list[dict] = []
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "text":     chunk["text"],
                    "offset":   chunk["offset"],
                    "duration": chunk["duration"],
                })
    size_kb = Path(audio_path).stat().st_size // 1024
    print(f"  音声: {audio_path} ({size_kb} KB), 単語境界: {len(words)} 件")
    return words


def _generate_pog_chapters() -> bool:
    """output/pog_ch*.txt をチャプターごとに音声生成して結合する。
    chapter_timings_0.json に正確な開始時刻を保存し、audio_0.mp3 として連結する。
    対象ファイルが存在しない場合は False を返す。
    """
    ch_scripts = sorted(
        Path(OUTPUT_DIR).glob("pog_ch*.txt"),
        key=lambda p: int(p.stem[len("pog_ch"):]),
    )
    if not ch_scripts:
        return False

    meta_path = Path("pog_meta.json")
    meta_chapters: list[dict] = []
    if meta_path.exists():
        try:
            meta_chapters = json.loads(meta_path.read_text(encoding="utf-8")).get("chapters", [])
        except Exception:
            pass

    print(f"\n=== POGチャプター別音声生成 ({len(ch_scripts)} チャプター) ===")
    voice, rate, pitch_factor, volume_db = pick_tts_params()
    print(f"voice={voice} rate={rate}")

    for ch_script in ch_scripts:
        i = int(ch_script.stem[len("pog_ch"):])
        script_text = ch_script.read_text(encoding="utf-8").strip()
        if not script_text:
            print(f"  [スキップ] {ch_script} が空")
            continue
        narration_text = normalize_racing_terms(script_text)
        narration_text = apply_readings(narration_text)
        ch_audio = str(Path(OUTPUT_DIR) / f"pog_ch{i}_audio.mp3")
        print(f"\n--- チャプター {i} ({len(narration_text)}文字) ---")
        for attempt in range(1, 4):
            try:
                asyncio.run(_edge_tts_with_words(
                    narration_text, ch_audio, voice=voice, rate=rate,
                ))
                break
            except Exception as e:
                print(f"  [失敗 {attempt}/3]: {e}", file=sys.stderr)
                if attempt < 3:
                    time.sleep(10 * attempt)
                else:
                    raise RuntimeError(f"チャプター {i} の音声生成失敗")
        apply_audio_variation(ch_audio, pitch_factor, volume_db)
        dur = _audio_duration_s(ch_audio)
        print(f"  → {ch_audio} ({dur:.1f}s)")

    # チャプターごとの音声長から正確な開始時刻を計算
    cum_t = 0.0
    timings: list[dict] = []
    ch_audio_paths: list[str] = []
    for ci, ch in enumerate(meta_chapters):
        ch_audio = str(Path(OUTPUT_DIR) / f"pog_ch{ci}_audio.mp3")
        if not Path(ch_audio).exists():
            print(f"  [警告] {ch_audio} が見つかりません", file=sys.stderr)
            continue
        timings.append({"title": ch["title"], "time_s": cum_t})
        ch_audio_paths.append(ch_audio)
        cum_t += _audio_duration_s(ch_audio)

    if timings:
        t_path = Path(OUTPUT_DIR) / "chapter_timings_0.json"
        t_path.write_text(json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  チャプタータイミング保存: {t_path} (合計 {cum_t:.1f}s)")

    # 全チャプター音声を audio_0.mp3 に連結
    if ch_audio_paths:
        concat_list = Path(OUTPUT_DIR) / "_pog_concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in ch_audio_paths:
                f.write(f"file '{Path(p).resolve()}'\n")
        audio_out = str(Path(OUTPUT_DIR) / "audio_0.mp3")
        res = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c:a", "libmp3lame", "-b:a", "128k", audio_out],
            capture_output=True,
        )
        concat_list.unlink(missing_ok=True)
        if res.returncode == 0:
            print(f"  音声連結完了: {audio_out} ({_audio_duration_s(audio_out):.1f}s)")
        else:
            print(f"  [警告] 音声連結失敗: {res.stderr[-300:]}", file=sys.stderr)

    return True


def main() -> None:
    print("=== 音声生成開始 ===")

    # POGチャプター別スクリプトがあれば優先して処理
    if _generate_pog_chapters():
        print("\nPOGチャプター別音声生成完了")
        return

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
        # subtitle_text: 原文テキスト（字幕表示用 — 人名は漢字のまま）
        subtitle_text = (title + "。" + script) if title else script
        # narration_text: 読み仮名置換済み（TTS用）。
        # 適用された置換を記録し、字幕側で元の漢字表記に復元する
        reading_track: list[tuple[str, str]] = []
        narration_text = normalize_racing_terms(subtitle_text, track=reading_track)
        narration_text = apply_readings(narration_text, track=reading_track)
        if title:
            print(f"  [{idx}] タイトル読み上げ追加: 「{title[:40]}」")

        audio_path = f"{OUTPUT_DIR}/audio_{idx}.mp3"
        ass_path = f"{OUTPUT_DIR}/subtitles_{idx}.ass"

        voice, rate, pitch_factor, volume_db = pick_tts_params()
        is_kokoro_voice = voice in _KOKORO_VOICE_POOL
        engine = "Kokoro" if (_KOKORO_AVAILABLE and is_kokoro_voice) else "edge-tts"
        print(f"\n--- 音声生成 [{idx}] ({len(narration_text)}文字) engine={engine} voice={voice} ---")
        words: list[dict] = []
        for attempt in range(1, 4):
            try:
                if _KOKORO_AVAILABLE and is_kokoro_voice:
                    speed = 1.0 + (int(rate.replace("%", "").replace("+", "")) / 100)
                    generate_audio_kokoro(narration_text, audio_path, voice=voice, speed=speed)
                else:
                    words = asyncio.run(_edge_tts_with_words(
                        narration_text, audio_path, voice=voice, rate=rate,
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
        aud_dur = _audio_duration_s(audio_path)
        if words:
            # WordBoundary イベントから正確なタイミングで字幕を生成
            segs = words_to_segments(words, max_chars=26)
            # WordBoundary はかな置換後のテキストなので、字幕表示用に
            # 元の漢字表記へ復元する（短いかなは誤置換しやすいので3文字以上のみ）
            restore = {}
            for kana, orig in reading_track:
                if len(kana) >= 3:
                    restore[kana] = orig
            for seg in segs:
                for kana in sorted(restore, key=len, reverse=True):
                    if kana in seg["text"]:
                        seg["text"] = seg["text"].replace(kana, restore[kana])
            # POGメタが存在する場合、WordBoundaryからチャプター開始時刻を計算して保存
            _save_chapter_timings(idx, words)
        else:
            # Kokoro TTS はタイミングイベントなし → 推定で補完
            segs = _estimate_subtitle_segments(subtitle_text, aud_dur)
        write_ass(segs, ass_path, font_name)
        print(f"  字幕: {ass_path} ({len(segs)} セグメント)")
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
