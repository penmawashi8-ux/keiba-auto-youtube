#!/usr/bin/env python3
"""POG2026-2027 横向き（1280×720）動画生成。

landscape_video.py との主な違い：
- 章ごとにffmpeg geqで生成したドラマチックな暗い背景を使用（馬写真なし）
- 各馬の章冒頭に血統表カード（父=青系、母=桃系）を表示
- 章背景は章の全期間持続
- 字幕はTTS生成のASSを使用（章マーカーなしで同期）
- 字幕の長い行は折り返し処理
"""
import glob
import json
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
OPEN_DUR      = 3.5   # オープニングカード表示秒数
PEDIGREE_DUR  = 5.0   # 血統カード表示秒数
BGM_VOL       = 0.12
SUB_WRAP      = 20    # 字幕の1行最大文字数

# ── 馬ごとの背景テーマ（ffmpeg geq 式） ────────────────────────
# 全体的に暗く、下部や中心に色のグロー効果
_THEMES: dict[str, dict] = {
    # ダノンダックス: 暗い闘技場＋アンバー下部グロー
    "ダノンダックス": dict(
        r="clip(10+200*pow(Y/H,2.0)*(0.4+0.6*pow(1-abs(2*X/W-1),1.5)),0,255)",
        g="clip(5+85*pow(Y/H,2.2)*(0.4+0.6*pow(1-abs(2*X/W-1),2.0)),0,255)",
        b="clip(3+18*pow(Y/H,2.5),0,255)",
    ),
    # ジャンゴッド: 深紅の闘技場
    "ジャンゴッド": dict(
        r="clip(14+170*pow(Y/H,1.8)*(0.3+0.7*pow(1-abs(2*X/W-1),1.2)),0,255)",
        g="clip(4+30*pow(Y/H,2.5),0,255)",
        b="clip(3+8*pow(Y/H,3.0),0,255)",
    ),
    # ソブリオ: 暗い金・オリーブグロー
    "ソブリオ": dict(
        r="clip(10+150*pow(Y/H,2.0)*(0.35+0.65*pow(1-abs(2*X/W-1),1.4)),0,255)",
        g="clip(8+120*pow(Y/H,2.0)*(0.35+0.65*pow(1-abs(2*X/W-1),1.6)),0,255)",
        b="clip(3+22*pow(Y/H,2.8),0,255)",
    ),
    # ノイエルング: コズミックブルー（宇宙・星空）
    "ノイエルング": dict(
        r="clip(5+12*pow(1-abs(2*Y/H-1),4),0,255)",
        g="clip(8+22*pow(1-abs(2*Y/H-1),3.5),0,255)",
        b="clip(18+155*pow(1-abs(2*Y/H-1),2.5)*(0.5+0.5*pow(1-abs(2*X/W-1),1.5)),0,255)",
    ),
    # レニュアージュ: ダーククリムゾン
    "レニュアージュ": dict(
        r="clip(14+180*pow(Y/H,1.7)*(0.3+0.7*pow(1-abs(2*X/W-1),1.0)),0,255)",
        g="clip(4+16*pow(Y/H,2.8),0,255)",
        b="clip(3+6*pow(Y/H,3.5),0,255)",
    ),
    # 汎用（オープニング・エンディング）: 暗い中心グロー
    "__general__": dict(
        r="clip(8+55*pow(1-abs(2*X/W-1),3)*pow(1-abs(2*Y/H-1),2.5),0,255)",
        g="clip(6+42*pow(1-abs(2*X/W-1),3)*pow(1-abs(2*Y/H-1),2.5),0,255)",
        b="clip(5+30*pow(1-abs(2*X/W-1),3)*pow(1-abs(2*Y/H-1),2.5),0,255)",
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
    """日本語混じりテキストを max_chars 文字で折り返す。"""
    lines, para = [], text
    while len(para) > max_chars:
        lines.append(para[:max_chars])
        para = para[max_chars:]
    if para:
        lines.append(para)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# 背景画像生成（ffmpeg geq）
# ─────────────────────────────────────────────────────────

def generate_bg(key: str, out_path: str) -> str:
    """geq 式でドラマチックな暗い背景画像を生成してパスを返す。"""
    if Path(out_path).exists() and Path(out_path).stat().st_size > 3000:
        return out_path
    theme = _THEMES.get(key, _THEMES["__general__"])
    geq = f"r='{theme['r']}':g='{theme['g']}':b='{theme['b']}'"
    res = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=1",
        "-vf", f"geq={geq},vignette=PI/4",
        "-frames:v", "1", "-q:v", "2", out_path,
    ], capture_output=True)
    if res.returncode != 0:
        # vignette 失敗時のフォールバック
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=1",
            "-vf", f"geq={geq}",
            "-frames:v", "1", "-q:v", "2", out_path,
        ], capture_output=True)
    print(f"  背景生成: {key} → {out_path}")
    return out_path


def prepare_backgrounds(horses: list[dict]) -> dict[str, str]:
    """全章分の背景画像を生成してパスマップを返す。"""
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    imgs: dict[str, str] = {}
    for horse in horses:
        name = horse["name"]
        out = f"{ASSETS_DIR}/pog_bg_{name}.jpg"
        imgs[name] = generate_bg(name, out)
    imgs["__general__"] = generate_bg("__general__", f"{ASSETS_DIR}/pog_bg_general.jpg")
    return imgs


# ─────────────────────────────────────────────────────────
# ASS 字幕 → drawtext 変換（折り返し対応）
# ─────────────────────────────────────────────────────────

def _ass_time_to_s(t: str) -> float:
    h, m, rest = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def ass_to_drawtext_filters(ass_path: str, font: str | None, tmp_dir: str) -> list[str]:
    """ASSファイルの Dialogue を drawtext フィルターに変換する。
    ・長い行は SUB_WRAP 文字で折り返す
    ・章マーカー行（【...】のみ）はスキップ
    """
    content = Path(ass_path).read_text(encoding="utf-8")
    fp = _esc(font) if font else ""
    filters: list[str] = []
    marker_re = re.compile(r'^【[^】]+】$')

    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue

        raw = parts[9].replace("\\N", "\n").strip()
        if not raw:
            continue
        # ASS 内に章マーカーが残っていたらスキップ
        if marker_re.match(raw.strip()):
            continue

        try:
            t1 = _ass_time_to_s(parts[1])
            t2 = _ass_time_to_s(parts[2])
        except Exception:
            continue
        if t2 <= t1 + 0.01:
            continue

        # 折り返し
        wrapped = "\n".join(
            wrap_text(seg, SUB_WRAP) for seg in raw.split("\n")
        )

        tf = f"{tmp_dir}/sub{len(filters)}.txt"
        Path(tf).write_text(wrapped, encoding="utf-8")

        f = f"drawtext=textfile='{_esc(tf)}'"
        if fp:
            f += f":fontfile='{fp}'"
        f += (
            f":fontsize=38:fontcolor=0xFFFFFF"
            f":x=(w-text_w)/2:y=h-text_h-50"
            f":box=1:boxcolor=0x000000@0.72:boxborderw=14"
            f":borderw=2:bordercolor=0x000000"
            f":line_spacing=8"
            f":enable='between(t,{t1:.3f},{t2:.3f})'"
        )
        filters.append(f)
    return filters


# ─────────────────────────────────────────────────────────
# 章タイミング計算
# ─────────────────────────────────────────────────────────

def chapters_from_meta(meta_chapters: list[dict], stripped_len: int,
                        aud_dur: float) -> list[tuple[str, float]]:
    """pog_meta.json の chapters リストから (title, 開始秒) を返す。"""
    result = []
    for ch in meta_chapters:
        pos = ch.get("stripped_char_pos", 0)
        t   = (pos / max(stripped_len, 1)) * aud_dur
        result.append((ch["title"], t))
    return result


def match_horse(chapter_title: str, horses: list[dict]) -> dict | None:
    for h in horses:
        if h["name"] in chapter_title:
            return h
    return None


# ─────────────────────────────────────────────────────────
# 血統表カード（netkeiba 風オーバーレイ）
# ─────────────────────────────────────────────────────────

def pedigree_card_filters(horse: dict, t1: float, t2: float,
                           ci: int, tmp_dir: str, fp: str) -> list[str]:
    """血統カード drawtext フィルター群を返す。"""
    enable = f"between(t,{t1:.3f},{t2:.3f})"
    vf: list[str] = []

    def tf(name: str, text: str) -> str:
        p = f"{tmp_dir}/{name}.txt"
        Path(p).write_text(text, encoding="utf-8")
        return _esc(p)

    # 背景パネル（全角スペース多数 + 大boxborderw で面積を確保）
    vf.append(
        f"drawtext=textfile='{tf(f'panel_{ci}', '　'*22)}':fontfile='{fp}':"
        f"fontsize=52:fontcolor=0x000000@0:"
        f"x=(w-text_w)/2:y=h/2-185:"
        f"box=1:boxcolor=0x080818@0.92:boxborderw=210:"
        f"enable='{enable}'"
    )

    # タイプバッジ
    horse_type = horse.get("type", "本命")
    badge_col  = "0x8B0000" if horse_type == "本命" else "0xBF4500"
    vf.append(
        f"drawtext=textfile='{tf(f'badge_{ci}', f'【{horse_type}】')}':fontfile='{fp}':"
        f"fontsize=36:fontcolor=0xFFFFFF:"
        f"x=(w-text_w)/2:y=h/2-178:"
        f"box=1:boxcolor={badge_col}@0.97:boxborderw=14:"
        f"enable='{enable}'"
    )

    # 馬名
    name_col = "0xFFD700" if horse_type == "本命" else "0xFF6347"
    vf.append(
        f"drawtext=textfile='{tf(f'hname_{ci}', horse['name'])}':fontfile='{fp}':"
        f"fontsize=80:fontcolor={name_col}:"
        f"x=(w-text_w)/2:y=h/2-112:"
        f"borderw=4:bordercolor=0x000000:"
        f"enable='{enable}'"
    )

    # 父セル（青系）
    vf.append(
        f"drawtext=textfile='{tf(f'sire_lbl_{ci}', '父')}':fontfile='{fp}':"
        f"fontsize=40:fontcolor=0x1A3A6E:"
        f"x=(w-text_w)/2-205:y=h/2-18:"
        f"box=1:boxcolor=0xB8D8F0@0.92:boxborderw=24:"
        f"enable='{enable}'"
    )
    vf.append(
        f"drawtext=textfile='{tf(f'sire_{ci}', horse['sire'])}':fontfile='{fp}':"
        f"fontsize=48:fontcolor=0x1A3A6E:"
        f"x=(w-text_w)/2+30:y=h/2-24:"
        f"box=1:boxcolor=0xD6EAF8@0.88:boxborderw=20:"
        f"borderw=1:bordercolor=0x5B9BD5:"
        f"enable='{enable}'"
    )

    # 母セル（桃系）
    vf.append(
        f"drawtext=textfile='{tf(f'dam_lbl_{ci}', '母')}':fontfile='{fp}':"
        f"fontsize=40:fontcolor=0x7A1A3A:"
        f"x=(w-text_w)/2-205:y=h/2+46:"
        f"box=1:boxcolor=0xFFB6C1@0.92:boxborderw=24:"
        f"enable='{enable}'"
    )
    vf.append(
        f"drawtext=textfile='{tf(f'dam_{ci}', horse['dam'])}':fontfile='{fp}':"
        f"fontsize=48:fontcolor=0x7A1A3A:"
        f"x=(w-text_w)/2+30:y=h/2+40:"
        f"box=1:boxcolor=0xFAD7E0@0.88:boxborderw=20:"
        f"borderw=1:bordercolor=0xE07090:"
        f"enable='{enable}'"
    )

    # 落札額（あれば）
    sale  = horse.get("sale_price", "―")
    y_off = 108
    if sale and sale != "―":
        vf.append(
            f"drawtext=textfile='{tf(f'sale_{ci}', f'落札額　{sale}')}':fontfile='{fp}':"
            f"fontsize=34:fontcolor=0xFFFF99:"
            f"x=(w-text_w)/2:y=h/2+{y_off}:"
            f"borderw=2:bordercolor=0x000000:"
            f"enable='{enable}'"
        )
        y_off += 44

    # 注記（緑）
    note = horse.get("note", "")
    if note:
        vf.append(
            f"drawtext=textfile='{tf(f'note_{ci}', note)}':fontfile='{fp}':"
            f"fontsize=34:fontcolor=0x7FFF00:"
            f"x=(w-text_w)/2:y=h/2+{y_off}:"
            f"borderw=2:bordercolor=0x000000:"
            f"enable='{enable}'"
        )
        y_off += 44

    # 厩舎
    if horse.get("trainer"):
        trainer_text = "厩舎：" + horse["trainer"]
        vf.append(
            f"drawtext=textfile='{tf(f'trainer_{ci}', trainer_text)}':fontfile='{fp}':"
            f"fontsize=30:fontcolor=0xCCCCCC:"
            f"x=(w-text_w)/2:y=h/2+{y_off}:"
            f"borderw=1:bordercolor=0x000000:"
            f"enable='{enable}'"
        )

    return vf


# ─────────────────────────────────────────────────────────
# コーナー装飾
# ─────────────────────────────────────────────────────────

def corner_decoration_filters(fp: str, tmp_dir: str) -> list[str]:
    """4角に金色の◆装飾を追加する。"""
    vf: list[str] = []
    sym = "◆"
    positions = [
        ("cl_tl", "22",          "18"),
        ("cl_tr", "w-text_w-22", "18"),
        ("cl_bl", "22",          "h-text_h-18"),
        ("cl_br", "w-text_w-22", "h-text_h-18"),
    ]
    for name, cx, cy in positions:
        p = f"{tmp_dir}/{name}.txt"
        Path(p).write_text(sym, encoding="utf-8")
        vf.append(
            f"drawtext=textfile='{_esc(p)}':fontfile='{fp}':"
            f"fontsize=32:fontcolor=0xFFD700@0.80:"
            f"x={cx}:y={cy}:borderw=1:bordercolor=0x806000"
        )
    return vf


# ─────────────────────────────────────────────────────────
# サムネイル生成
# ─────────────────────────────────────────────────────────

def generate_thumbnail(meta: dict, font: str | None,
                        bg_img: str | None, thumb_path: str, tmp_dir: str) -> None:
    fp   = _esc(font or "")
    hook = meta.get("thumbnail_hook", "")

    if bg_img and Path(bg_img).exists():
        inputs = ["-loop", "1", "-i", bg_img]
        v_init = [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}"]
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c=#0D1B2A:s={W}x{H}:r=1"]
        v_init = []

    filters: list[str] = v_init + ["eq=brightness=-0.35:contrast=1.05"]

    if font:
        def tf(name: str, text: str) -> str:
            p = f"{tmp_dir}/{name}.txt"
            Path(p).write_text(text, encoding="utf-8")
            return _esc(p)

        filters.append(
            f"drawtext=textfile='{tf('th_badge', 'POG 2026-2027')}':fontfile='{fp}':"
            f"fontsize=38:fontcolor=0xFFFFFF:"
            f"x=30:y=28:box=1:boxcolor=0x8B0000@0.96:boxborderw=16"
        )
        if hook:
            filters.append(
                f"drawtext=textfile='{tf('th_hook', wrap_text(hook, 14))}':fontfile='{fp}':"
                f"fontsize=72:fontcolor=0xFFFF00:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-30:"
                f"box=1:boxcolor=0x000000@0.88:boxborderw=30:"
                f"borderw=5:bordercolor=0xFF6600"
            )
        title = meta.get("title", "")
        if title:
            filters.append(
                f"drawtext=textfile='{tf('th_title', wrap_text(title, 20))}':fontfile='{fp}':"
                f"fontsize=36:fontcolor=0xFFFFFF:"
                f"x=(w-text_w)/2:y=h-text_h-48:"
                f"box=1:boxcolor=0x000000@0.80:boxborderw=16"
            )

    fc = "[0:v]" + ",".join(filters) + "[vout]"
    res = subprocess.run(
        ["ffmpeg", "-y"] + inputs + ["-filter_complex", fc, "-map", "[vout]",
                                      "-frames:v", "1", "-q:v", "2", thumb_path],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        print(f"  サムネイル: {thumb_path} ({Path(thumb_path).stat().st_size//1024} KB)")
    else:
        print(f"  [警告] サムネイル生成失敗:\n{res.stderr[-300:]}", file=sys.stderr)


# ─────────────────────────────────────────────────────────
# メイン動画生成
# ─────────────────────────────────────────────────────────

def generate_video(meta: dict, font: str | None, bg_imgs: dict[str, str]) -> str:
    script_path    = Path(f"{OUTPUT_DIR}/script_0.txt")          # TTS用（マーカーなし）
    chapters_path  = Path(f"{OUTPUT_DIR}/script_chapters_0.txt") # 章検出用
    audio_path     = f"{OUTPUT_DIR}/audio_0.mp3"
    ass_path       = f"{OUTPUT_DIR}/subtitles_0.ass"
    output_path    = f"{OUTPUT_DIR}/pog_landscape_video_0.mp4"
    thumb_path     = f"{OUTPUT_DIR}/thumbnail_0.jpg"

    if not script_path.exists():
        raise FileNotFoundError(f"{script_path} が見つかりません。")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"{audio_path} が見つかりません。")

    stripped_script = script_path.read_text(encoding="utf-8").strip()
    aud_dur         = audio_duration(audio_path)
    horses          = meta.get("horses", [])
    meta_chapters   = meta.get("chapters", [])

    # 章ごとの開始時刻を算出（stripped スクリプトの文字位置ベース）
    ch_times = chapters_from_meta(meta_chapters, len(stripped_script), aud_dur)

    # 章ごとのセグメント情報を構築
    segments: list[dict] = []
    for ci, (title, t_start) in enumerate(ch_times):
        t_end = ch_times[ci + 1][1] if ci + 1 < len(ch_times) else aud_dur
        horse = match_horse(title, horses)
        bg    = bg_imgs.get(horse["name"]) if horse else bg_imgs.get("__general__")
        segments.append({
            "title":   title,
            "t_start": t_start,
            "t_end":   t_end,
            "dur":     max(t_end - t_start, 0.1),
            "horse":   horse,
            "bg":      bg or bg_imgs["__general__"],
        })

    tmp_dir = tempfile.mkdtemp(prefix="pog_ls_")
    try:
        fp = _esc(font or "")
        N  = len(segments)

        # ── 背景入力の構築 ─────────────────────────────────
        bg_inputs: list[str] = []
        pre_filters: list[str] = []
        img_scale = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{H},setsar=1")

        for i, seg in enumerate(segments):
            if seg["bg"] and Path(seg["bg"]).exists():
                bg_inputs += ["-r", str(FPS), "-loop", "1",
                               "-t", f"{seg['dur']:.3f}", "-i", seg["bg"]]
                pre_filters.append(f"[{i}:v]{img_scale}[vi{i}]")
            else:
                bg_inputs += ["-f", "lavfi", "-i",
                               f"color=c=#0D1B2A:s={W}x{H}:r={FPS}:d={seg['dur']:.3f}"]
                pre_filters.append(f"[{i}:v]setsar=1[vi{i}]")

        concat_in = "".join(f"[vi{i}]" for i in range(N))
        pre_filters.append(f"{concat_in}concat=n={N}:v=1:a=0[bgout]")
        audio_idx = N

        video_filters: list[str] = []

        if font:
            def tf(name: str, text: str) -> str:
                p = f"{tmp_dir}/{name}.txt"
                Path(p).write_text(text, encoding="utf-8")
                return _esc(p)

            # ── コーナー装飾（常時）────────────────────────
            video_filters.extend(corner_decoration_filters(fp, tmp_dir))

            # ── オープニングカード ─────────────────────────
            video_filters.append(
                f"drawtext=textfile='{tf('open_title', wrap_text(meta.get('title', 'POG2026-2027'), 18))}':"
                f"fontfile='{fp}':fontsize=64:fontcolor=0xFFD700:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-22:"
                f"box=1:boxcolor=0x000000@0.85:boxborderw=28:"
                f"borderw=3:bordercolor=0x000000:"
                f"enable='between(t,0,{OPEN_DUR})'"
            )
            video_filters.append(
                f"drawtext=textfile='{tf('open_sub', '本命3頭＋大穴2頭　完全解説')}':"
                f"fontfile='{fp}':fontsize=38:fontcolor=0xFFFFFF:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+60:"
                f"borderw=2:bordercolor=0x000000:"
                f"enable='between(t,0,{OPEN_DUR})'"
            )

            # ── 各章：血統カード ───────────────────────────
            for ci, seg in enumerate(segments):
                if not seg["horse"]:
                    continue
                t1 = seg["t_start"]
                t2 = min(t1 + PEDIGREE_DUR, seg["t_end"])
                if t2 <= t1 + 0.1:
                    continue
                video_filters.extend(
                    pedigree_card_filters(seg["horse"], t1, t2, ci, tmp_dir, fp)
                )

            # ── ASS字幕 → drawtext ─────────────────────────
            has_ass = Path(ass_path).exists() and Path(ass_path).stat().st_size > 100
            if has_ass:
                sub_fts = ass_to_drawtext_filters(ass_path, font, tmp_dir)
                video_filters.extend(sub_fts)
                print(f"  字幕: {len(sub_fts)} セグメント")
            else:
                print("  [警告] ASS字幕なし", file=sys.stderr)

        if not video_filters:
            video_filters.append(f"scale={W}:{H}")

        vid_chain = "[bgout]" + ",".join(video_filters) + "[vout]"
        fc_parts  = list(pre_filters) + [vid_chain]

        # ── BGM ────────────────────────────────────────────
        bgm_files = sorted(glob.glob(f"{BGM_DIR}/*.mp3") + glob.glob(f"{BGM_DIR}/*.m4a"))
        bgm_file  = random.choice(bgm_files) if bgm_files else None

        if bgm_file:
            print(f"  BGM: {Path(bgm_file).name}")
            ac = (f"[{audio_idx}:a]apad=whole_dur={aud_dur:.3f}[narr];"
                  f"[narr][{audio_idx+1}:a]amix=inputs=2:duration=first:weights=1 {BGM_VOL}[aout]")
            fc  = ";".join(fc_parts) + ";" + ac
            cmd = (["ffmpeg", "-y"] + bg_inputs +
                   ["-i", audio_path, "-stream_loop", "-1", "-i", bgm_file,
                    "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"])
        else:
            ac  = f"[{audio_idx}:a]apad=whole_dur={aud_dur:.3f}[aout]"
            fc  = ";".join(fc_parts) + ";" + ac
            cmd = (["ffmpeg", "-y"] + bg_inputs +
                   ["-i", audio_path,
                    "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"])

        cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(aud_dur + 0.5),
            output_path,
        ]

        print(f"  動画生成中... (音声: {aud_dur:.1f}s, 章: {N}個)")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            print(f"  [エラー] ffmpeg:\n{res.stderr[-800:]}", file=sys.stderr)
            raise RuntimeError(f"ffmpeg 失敗 rc={res.returncode}")

        size_mb = Path(output_path).stat().st_size / 1024 / 1024
        print(f"✅ {output_path} ({size_mb:.1f} MB)")

        generate_thumbnail(meta, font, bg_imgs.get("__general__"), thumb_path, tmp_dir)
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
