#!/usr/bin/env python3
"""POG2026-2027 横向き（1280×720）動画生成。

ニュース動画と同じ「センテンスごとにクリップ作成→concat」方式で音声ズレを防ぐ。
馬紹介章では全センテンスを通じて血統カードを常時表示。
背景はgeqフィルターのみ（Pixabay不使用）。
"""
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

POG_META_JSON = "pog_meta.json"
OUTPUT_DIR    = "output"
ASSETS_DIR    = "assets"
BGM_DIR       = f"{ASSETS_DIR}/bgm"
W, H          = 1280, 720
FPS           = 30
BGM_VOL       = 0.12
SUB_WRAP      = 22
MIN_CLIP_DUR  = 0.5

_MARKER_RE = re.compile(r'^【[^】]+】$')

# ── geq背景: 馬ごとに異なるスポットライト配置（pow(x,0.5) でffmpeg互換性確保）──
_GEQ_HORSE: dict[str, dict] = {
    # ダノンダックス: 左上からのアンバー日射し
    "ダノンダックス": dict(
        r="clip(8+210*pow(max(0,1-2.0*pow(pow((X/W-0.22)*1.2,2)+pow(Y/H-0.12,2),0.5)),1.8)+28*pow(1-Y/H,5),0,255)",
        g="clip(4+72*pow(max(0,1-2.0*pow(pow((X/W-0.22)*1.2,2)+pow(Y/H-0.12,2),0.5)),2.6)+6*pow(1-Y/H,5),0,255)",
        b="clip(2+8*pow(max(0,1-2.0*pow(pow((X/W-0.22)*1.2,2)+pow(Y/H-0.12,2),0.5)),4.5),0,255)",
    ),
    # ジャンゴッド: 右上からのゴールドスポット
    "ジャンゴッド": dict(
        r="clip(10+215*pow(max(0,1-2.1*pow(pow((X/W-0.78)*1.3,2)+pow(Y/H-0.15,2),0.5)),1.9),0,255)",
        g="clip(5+68*pow(max(0,1-2.1*pow(pow((X/W-0.78)*1.3,2)+pow(Y/H-0.15,2),0.5)),2.7),0,255)",
        b="clip(2+7*pow(max(0,1-2.1*pow(pow((X/W-0.78)*1.3,2)+pow(Y/H-0.15,2),0.5)),5.0),0,255)",
    ),
    # ソブリオ: 真上中央からの純金スポット
    "ソブリオ": dict(
        r="clip(8+230*pow(max(0,1-2.3*pow(pow((X/W-0.5)*1.4,2)+pow(Y/H-0.18,2),0.5)),2.0),0,255)",
        g="clip(4+82*pow(max(0,1-2.3*pow(pow((X/W-0.5)*1.4,2)+pow(Y/H-0.18,2),0.5)),2.8),0,255)",
        b="clip(1+5*pow(max(0,1-2.3*pow(pow((X/W-0.5)*1.4,2)+pow(Y/H-0.18,2),0.5)),6.0),0,255)",
    ),
    # ノイエルング: 中心からのエレクトリックブルー
    "ノイエルング": dict(
        r="clip(3+18*pow(max(0,1-2.8*pow(pow(X/W-0.5,2)+pow(Y/H-0.5,2),0.5)),2.5),0,255)",
        g="clip(5+45*pow(max(0,1-2.8*pow(pow(X/W-0.5,2)+pow(Y/H-0.5,2),0.5)),2.0),0,255)",
        b="clip(16+225*pow(max(0,1-2.3*pow(pow(X/W-0.5,2)+pow(Y/H-0.5,2),0.5)),1.6),0,255)",
    ),
    # レニュアージュ: 右中央からのクリムゾン
    "レニュアージュ": dict(
        r="clip(12+210*pow(max(0,1-2.0*pow(pow((X/W-0.62)*1.2,2)+pow((Y/H-0.5)*1.3,2),0.5)),1.8),0,255)",
        g="clip(3+14*pow(max(0,1-2.0*pow(pow((X/W-0.62)*1.2,2)+pow((Y/H-0.5)*1.3,2),0.5)),3.5),0,255)",
        b="clip(2+6*pow(max(0,1-2.0*pow(pow((X/W-0.62)*1.2,2)+pow((Y/H-0.5)*1.3,2),0.5)),4.5),0,255)",
    ),
    # 汎用: 中心の微弱な白グロー
    "__general__": dict(
        r="clip(6+70*pow(max(0,1-3.2*pow(pow(X/W-0.5,2)+pow(Y/H-0.5,2),0.5)),3.0),0,255)",
        g="clip(5+60*pow(max(0,1-3.2*pow(pow(X/W-0.5,2)+pow(Y/H-0.5,2),0.5)),3.0),0,255)",
        b="clip(4+50*pow(max(0,1-3.2*pow(pow(X/W-0.5,2)+pow(Y/H-0.5,2),0.5)),3.0),0,255)",
    ),
}


# ─────────────────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────────────────

def find_font() -> str | None:
    for p in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]:
        if Path(p).exists():
            return p
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    return hits[0] if hits else None


def audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 60.0


def _esc(path: str) -> str:
    return path.replace("'", "\\'")


def wrap_text(text: str, max_chars: int) -> str:
    lines, para = [], text
    while len(para) > max_chars:
        lines.append(para[:max_chars])
        para = para[max_chars:]
    if para:
        lines.append(para)
    return "\n".join(lines)


def match_horse(chapter_title: str, horses: list[dict]) -> dict | None:
    for h in horses:
        if h["name"] in chapter_title:
            return h
    return None


# ─────────────────────────────────────────────────────────
# 背景画像生成（geqのみ、Pixabay不使用）
# ─────────────────────────────────────────────────────────

def generate_bg(name: str, out: str) -> None:
    g   = _GEQ_HORSE.get(name, _GEQ_HORSE["__general__"])
    geq = f"r='{g['r']}':g='{g['g']}':b='{g['b']}'"
    res = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=1",
        "-vf", f"geq={geq},vignette=PI/2.5",
        "-frames:v", "1", "-q:v", "2", out,
    ], capture_output=True)
    if res.returncode != 0:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=#0A0A14:s={W}x{H}:r={FPS}",
            "-frames:v", "1", "-q:v", "3", out,
        ], capture_output=True)


def prepare_backgrounds(horses: list[dict]) -> dict[str, str]:
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    imgs: dict[str, str] = {}
    for horse in horses:
        name = horse["name"]
        out  = f"{ASSETS_DIR}/pog_bg_{name}.jpg"
        if not Path(out).exists() or Path(out).stat().st_size < 5000:
            generate_bg(name, out)
            print(f"  背景生成: {name}")
        imgs[name] = out
    out_g = f"{ASSETS_DIR}/pog_bg_general.jpg"
    if not Path(out_g).exists() or Path(out_g).stat().st_size < 5000:
        generate_bg("__general__", out_g)
        print(f"  背景生成: general")
    imgs["__general__"] = out_g
    return imgs


# ─────────────────────────────────────────────────────────
# スクリプト解析
# ─────────────────────────────────────────────────────────

def parse_script_chapters(chapters_script: str, horses: list[dict]) -> list[dict]:
    """章マーカー付きスクリプトを章ごとのセンテンスリストに変換する。"""
    chapters: list[dict]      = []
    current_title: str | None = None
    current_texts: list[str]  = []

    def _flush(title: str, texts: list[str]) -> None:
        if not texts:
            return
        full  = " ".join(texts)
        sents = [s.strip() for s in re.split(r"(?<=[。！？])", full) if s.strip()]
        if not sents:
            sents = [full.strip()]
        chapters.append({
            "title":     title,
            "horse":     match_horse(title, horses),
            "sentences": sents,
        })

    for line in chapters_script.splitlines():
        s = line.strip()
        if _MARKER_RE.match(s):
            if current_title is not None:
                _flush(current_title, current_texts)
            current_title = s
            current_texts = []
        elif s:
            current_texts.append(s)

    if current_title is not None:
        _flush(current_title, current_texts)

    return chapters


# ─────────────────────────────────────────────────────────
# クリップ生成
# ─────────────────────────────────────────────────────────

def make_pog_clip(
    idx:      int,
    bg:       str | None,
    sentence: str,
    duration: float,
    font:     str | None,
    tmp_dir:  str,
    horse:    dict | None = None,
) -> str:
    """1センテンス分のMP4クリップを生成する。馬章では血統カードを常時表示。"""
    clip_path = f"{tmp_dir}/clip_{idx:04d}.mp4"
    duration  = max(duration, MIN_CLIP_DUR)

    cmd = ["ffmpeg", "-y"]
    if bg and Path(bg).exists():
        cmd  += ["-loop", "1", "-i", bg]
        chain = (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1"
        )
    else:
        cmd  += ["-f", "lavfi", "-i", f"color=c=#0A0A14:s={W}x{H}:r={FPS}"]
        chain = "[0:v]setsar=1"

    if font:
        fp = _esc(font)

        def tf(label: str, text: str) -> str:
            p = f"{tmp_dir}/{label}_{idx}.txt"
            Path(p).write_text(text, encoding="utf-8")
            return _esc(p)

        # ── コーナー装飾 ──────────────────────────────────
        for cname, cx, cy in [
            ("ctl", "22",          "18"),
            ("ctr", "w-text_w-22", "18"),
            ("cbl", "22",          "h-text_h-18"),
            ("cbr", "w-text_w-22", "h-text_h-18"),
        ]:
            p = f"{tmp_dir}/{cname}_{idx}.txt"
            Path(p).write_text("◆", encoding="utf-8")
            chain += (
                f",drawtext=textfile='{_esc(p)}':fontfile='{fp}':"
                f"fontsize=32:fontcolor=0xFFD700@0.80:"
                f"x={cx}:y={cy}:borderw=1:bordercolor=0x806000"
            )

        # ── 馬章の血統カード ─────────────────────────────
        if horse:
            horse_type = horse.get("type", "本命")
            badge_col  = "0x8B0000" if horse_type == "本命" else "0xBF4500"
            name_col   = "0xFFD700" if horse_type == "本命" else "0xFF8C00"

            # タイプバッジ
            chain += (
                f",drawtext=textfile='{tf('badge', f'【{horse_type}】')}':"
                f"fontfile='{fp}':fontsize=42:fontcolor=0xFFFFFF:"
                f"x=(w-text_w)/2:y=22:"
                f"box=1:boxcolor={badge_col}@0.97:boxborderw=18"
            )

            # 馬名（大きく、半透明ボックス付き）
            chain += (
                f",drawtext=textfile='{tf('hname', horse['name'])}':"
                f"fontfile='{fp}':fontsize=100:fontcolor={name_col}:"
                f"x=(w-text_w)/2:y=90:"
                f"box=1:boxcolor=0x000000@0.55:boxborderw=20:"
                f"borderw=4:bordercolor=0x000000"
            )

            # 父ラベル（青）
            chain += (
                f",drawtext=textfile='{tf('sl', '父')}':"
                f"fontfile='{fp}':fontsize=46:fontcolor=0x1A3A6E:"
                f"x=(w-text_w)/2-240:y=238:"
                f"box=1:boxcolor=0xB8D8F0@0.95:boxborderw=28"
            )
            # 父名
            chain += (
                f",drawtext=textfile='{tf('sire', horse['sire'])}':"
                f"fontfile='{fp}':fontsize=46:fontcolor=0x1A3A6E:"
                f"x=(w-text_w)/2+40:y=238:"
                f"box=1:boxcolor=0xD6EAF8@0.92:boxborderw=24:"
                f"borderw=1:bordercolor=0x5B9BD5"
            )

            # 母ラベル（桃）
            chain += (
                f",drawtext=textfile='{tf('dl', '母')}':"
                f"fontfile='{fp}':fontsize=46:fontcolor=0x7A1A3A:"
                f"x=(w-text_w)/2-240:y=320:"
                f"box=1:boxcolor=0xFFB6C1@0.95:boxborderw=28"
            )
            # 母名
            chain += (
                f",drawtext=textfile='{tf('dam', horse['dam'])}':"
                f"fontfile='{fp}':fontsize=46:fontcolor=0x7A1A3A:"
                f"x=(w-text_w)/2+40:y=320:"
                f"box=1:boxcolor=0xFAD7E0@0.92:boxborderw=24:"
                f"borderw=1:bordercolor=0xE07090"
            )

            # 落札額・注記
            y_note = 418
            sale = horse.get("sale_price", "―")
            if sale and sale != "―":
                chain += (
                    f",drawtext=textfile='{tf('sale', '落札額　' + sale)}':"
                    f"fontfile='{fp}':fontsize=36:fontcolor=0xFFFF99:"
                    f"x=(w-text_w)/2:y={y_note}:"
                    f"borderw=2:bordercolor=0x000000"
                )
                y_note += 50

            note = horse.get("note", "")
            if note:
                chain += (
                    f",drawtext=textfile='{tf('note', note)}':"
                    f"fontfile='{fp}':fontsize=36:fontcolor=0x7FFF00:"
                    f"x=(w-text_w)/2:y={y_note}:"
                    f"borderw=2:bordercolor=0x000000"
                )

        # ── センテンス字幕（画面下部に常時表示） ─────────────
        if sentence:
            wrapped = wrap_text(sentence, SUB_WRAP)
            chain += (
                f",drawtext=textfile='{tf('sub', wrapped)}':"
                f"fontfile='{fp}':fontsize=44:fontcolor=0xFFFFFF:"
                f"x=(w-text_w)/2:y=h-text_h-40:"
                f"box=1:boxcolor=0x000000@0.78:boxborderw=22:"
                f"borderw=2:bordercolor=0x000000:"
                f"line_spacing=10"
            )

    chain += "[vout]"

    cmd += [
        "-filter_complex", chain,
        "-map", "[vout]",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-t", str(duration),
        clip_path,
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        print(f"  [警告] clip_{idx} 失敗: {res.stderr[-300:]}", file=sys.stderr)
        fb = [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c=#0A0A14:s={W}x{H}:r={FPS}:d={duration}",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-t", str(duration), clip_path,
        ]
        subprocess.run(fb, check=True, capture_output=True)

    return clip_path


# ─────────────────────────────────────────────────────────
# 動画生成（ニュース動画と同じclip concat方式）
# ─────────────────────────────────────────────────────────

def generate_video(meta: dict, font: str | None, bg_imgs: dict[str, str]) -> str:
    chapters_script_path = Path(f"{OUTPUT_DIR}/script_chapters_0.txt")
    audio_path  = f"{OUTPUT_DIR}/audio_0.mp3"
    output_path = f"{OUTPUT_DIR}/pog_landscape_video_0.mp4"
    thumb_path  = f"{OUTPUT_DIR}/thumbnail_0.jpg"

    if not Path(audio_path).exists():
        raise FileNotFoundError(f"{audio_path} が見つかりません。")
    if not chapters_script_path.exists():
        raise FileNotFoundError(f"{chapters_script_path} が見つかりません。")

    aud_dur = audio_duration(audio_path)
    horses  = meta.get("horses", [])

    chapters = parse_script_chapters(
        chapters_script_path.read_text(encoding="utf-8"), horses,
    )
    print(f"  章数: {len(chapters)}, 総音声: {aud_dur:.1f}s")

    # 全センテンスをフラット化して（文章, 馬）のリストにする
    all_items: list[tuple[str, dict | None]] = []
    for ch in chapters:
        for sent in ch["sentences"]:
            all_items.append((sent, ch["horse"]))

    if not all_items:
        raise RuntimeError("センテンスが0件です。スクリプトを確認してください。")

    # 文字数比でクリップ尺を計算（ニュース動画と同じ方式 → 音声ズレなし）
    total_chars = max(sum(len(s) for s, _ in all_items), 1)
    durations   = [max(MIN_CLIP_DUR, aud_dur * len(s) / total_chars) for s, _ in all_items]
    total_dur   = sum(durations)

    print(f"  センテンス数: {len(all_items)}, 動画尺: {total_dur:.1f}s")

    tmp_dir = tempfile.mkdtemp(prefix="pog_ls_")
    try:
        clip_paths: list[str] = []
        for i, ((sent, horse), dur) in enumerate(zip(all_items, durations)):
            name = horse["name"] if horse else "__general__"
            bg   = bg_imgs.get(name, bg_imgs["__general__"])
            clip_paths.append(make_pog_clip(i, bg, sent, dur, font, tmp_dir, horse=horse))
            if (i + 1) % 10 == 0 or (i + 1) == len(all_items):
                print(f"  クリップ {i+1}/{len(all_items)} 完了")

        # クリップ結合
        concat_txt = f"{tmp_dir}/concat.txt"
        with open(concat_txt, "w", encoding="utf-8") as f:
            for cp in clip_paths:
                f.write(f"file '{cp}'\n")

        silent_mp4 = f"{tmp_dir}/silent.mp4"
        print("  クリップ結合中...")
        res = subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_txt,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
            silent_mp4,
        ], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"concat失敗:\n{res.stderr[-400:]}")

        # 音声 + BGM ミックス
        bgm_files = sorted(glob.glob(f"{BGM_DIR}/*.mp3") + glob.glob(f"{BGM_DIR}/*.m4a"))
        bgm_file  = random.choice(bgm_files) if bgm_files else None

        if bgm_file:
            print(f"  BGM: {Path(bgm_file).name}")
            fc  = (
                f"[1:a]apad=whole_dur={total_dur:.3f}[narr];"
                f"[narr][2:a]amix=inputs=2:duration=first:weights=1 {BGM_VOL}[aout]"
            )
            cmd = (["ffmpeg", "-y", "-i", silent_mp4,
                    "-i", audio_path, "-stream_loop", "-1", "-i", bgm_file,
                    "-filter_complex", fc, "-map", "0:v", "-map", "[aout]"])
        else:
            cmd = (["ffmpeg", "-y", "-i", silent_mp4, "-i", audio_path,
                    "-af", f"apad=whole_dur={total_dur:.3f}",
                    "-map", "0:v", "-map", "1:a"])

        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", output_path]
        print("  音声結合中...")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"音声結合失敗:\n{res.stderr[-400:]}")

        size_mb = Path(output_path).stat().st_size / 1024 / 1024
        print(f"✅ {output_path} ({size_mb:.1f} MB)")

        # サムネイル（先頭フレーム抽出）
        subprocess.run([
            "ffmpeg", "-y", "-ss", "0.5", "-i", output_path,
            "-vframes", "1", "-q:v", "2", thumb_path,
        ], capture_output=True)

        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────────────────────

def main() -> None:
    if not Path(POG_META_JSON).exists():
        print(f"[エラー] {POG_META_JSON} が見つかりません。先に create_pog_video.py を実行してください。",
              file=sys.stderr)
        sys.exit(1)

    meta   = json.loads(Path(POG_META_JSON).read_text(encoding="utf-8"))
    horses = meta.get("horses", [])
    font   = find_font()
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    print("=== 背景画像を生成中 ===")
    bg_imgs = prepare_backgrounds(horses)

    print("\n=== POG横動画生成中 ===")
    generate_video(meta, font, bg_imgs)


if __name__ == "__main__":
    main()
