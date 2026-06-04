#!/usr/bin/env python3
"""POG2026-2027 横向き（1280×720）動画生成。

主な特徴：
- Pixabay の馬写真 + タイプ別色処理（本命=暗赤金、大穴①=ブルー、大穴②=クリムゾン）
- ffmpeg ass= フィルターでネイティブ字幕レンダリング（マージバグ解消）
- 血統表カード（父=青系・母=桃系）を章頭に表示
- 各章の背景は章全体で持続
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
OPEN_DUR      = 3.5
PEDIGREE_DUR  = 5.0
BGM_VOL       = 0.12

# ── Pixabay クエリ（馬ごと）: category=animals と組み合わせて使用 ────
PIXABAY_QUERIES: dict[str, list[str]] = {
    "ダノンダックス": ["racehorse galloping track", "thoroughbred horse racing", "horse running field"],
    "ジャンゴッド":   ["horse jockey racing", "thoroughbred horse track", "racehorse running"],
    "ソブリオ":      ["horse portrait elegant", "thoroughbred horse standing", "horse paddock"],
    "ノイエルング":   ["horse running evening", "racehorse dark", "horse racing sunset"],
    "レニュアージュ": ["horse mare portrait", "filly racehorse", "horse racing female"],
    "__general__":   ["horse racing track", "thoroughbred horse", "racehorse"],
}

# ── タイプ別 ffmpeg 色処理フィルター ───────────────────────────
# 写真を自然に見せるため、色処理は最小限にとどめる
_COLOR_VF: dict[str, str] = {
    "本命":    ("scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                "eq=brightness=-0.08:contrast=1.05:saturation=1.05,"
                "vignette=PI/4"),
    "大穴_blue": ("scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  "eq=brightness=-0.08:contrast=1.05:saturation=0.95,"
                  "vignette=PI/4"),
    "大穴_red":  ("scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  "eq=brightness=-0.08:contrast=1.05:saturation=1.00,"
                  "vignette=PI/4"),
    "__general__": ("scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                    "eq=brightness=-0.05:contrast=1.00,"
                    "vignette=PI/4"),
}

# ── 馬名 → 色タイプ ─────────────────────────────────────────
_NAME_TO_COLOR: dict[str, str] = {
    "ダノンダックス": "本命",
    "ジャンゴッド":   "本命",
    "ソブリオ":      "本命",
    "ノイエルング":   "大穴_blue",
    "レニュアージュ": "大穴_red",
}

# ── geq フォールバック（Pixabay 失敗時）: 画面全体に渡って視認可能なグラデーション
_GEQ_HORSE: dict[str, dict] = {
    # 本命3頭: 暖かいゴールド〜ブラウン系グラデーション
    "ダノンダックス": dict(
        r="clip(40+155*pow(Y/H,1.2),0,255)",
        g="clip(22+68*pow(Y/H,1.5),0,255)",
        b="clip(8+28*pow(Y/H,2.0),0,255)",
    ),
    "ジャンゴッド": dict(
        r="clip(35+145*pow(Y/H,1.2),0,255)",
        g="clip(18+55*pow(Y/H,1.6),0,255)",
        b="clip(6+20*pow(Y/H,2.2),0,255)",
    ),
    "ソブリオ": dict(
        r="clip(38+150*pow(Y/H,1.2),0,255)",
        g="clip(28+90*pow(Y/H,1.4),0,255)",
        b="clip(10+35*pow(Y/H,1.8),0,255)",
    ),
    # 大穴_blue: 深いブルー〜ネイビー系グラデーション
    "ノイエルング": dict(
        r="clip(8+22*pow(Y/H,2.0),0,255)",
        g="clip(15+50*pow(Y/H,1.6),0,255)",
        b="clip(45+155*pow(1-abs(2*Y/H-1),1.2),0,255)",
    ),
    # 大穴_red: クリムゾン〜ダークレッド系グラデーション
    "レニュアージュ": dict(
        r="clip(40+155*pow(Y/H,1.2),0,255)",
        g="clip(8+28*pow(Y/H,2.0),0,255)",
        b="clip(8+18*pow(Y/H,2.5),0,255)",
    ),
    "__general__": dict(
        r="clip(25+80*pow(Y/H,1.4),0,255)",
        g="clip(20+60*pow(Y/H,1.5),0,255)",
        b="clip(30+90*pow(Y/H,1.3),0,255)",
    ),
}

# ── 横動画用 ASS ヘッダーテンプレート ──────────────────────────
_LANDSCAPE_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},40,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,20,20,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


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


# ─────────────────────────────────────────────────────────
# 背景画像取得・生成
# ─────────────────────────────────────────────────────────

def _apply_color_vf(src: str, color_key: str, out: str) -> bool:
    """src 画像にタイプ別色処理を適用して out に保存。"""
    template = _COLOR_VF.get(color_key, _COLOR_VF["__general__"])
    vf = template.format(W=W, H=H)
    res = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-vf", vf, "-frames:v", "1", "-q:v", "2", out],
        capture_output=True,
    )
    return res.returncode == 0


def _geq_fallback(name: str, out: str) -> None:
    """馬名ごとに異なるgeqグラデーション背景を生成する。"""
    g = _GEQ_HORSE.get(name, _GEQ_HORSE["__general__"])
    geq = f"r='{g['r']}':g='{g['g']}':b='{g['b']}'"
    res = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=1",
        "-vf", f"geq={geq},vignette=PI/2.5",
        "-frames:v", "1", "-q:v", "2", out,
    ], capture_output=True)
    if res.returncode != 0:
        print(f"  [警告] geq失敗 ({name}), 単色フォールバック", file=sys.stderr)
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=#1A1008:s={W}x{H}:r={FPS}",
            "-frames:v", "1", "-q:v", "3", out,
        ], capture_output=True)


def fetch_bg(name: str, queries: list[str], color_key: str, out: str, pixabay_key: str) -> str:
    """Pixabay から馬写真を取得して色処理を適用。失敗時は geq フォールバック。"""
    if Path(out).exists() and Path(out).stat().st_size > 5000:
        return out

    fetched = False
    if pixabay_key:
        try:
            import requests as req_lib
            for q in queries:
                try:
                    r = req_lib.get("https://pixabay.com/api/", params={
                        "key": pixabay_key, "q": q, "image_type": "photo",
                        "orientation": "horizontal", "min_width": 1280, "min_height": 720,
                        "per_page": 20, "safesearch": "true",
                        "category": "animals",
                    }, timeout=30)
                    hits = r.json().get("hits", [])
                    if not hits:
                        continue
                    hit = random.choice(hits)
                    # 高解像度を優先（largeImageURL > webformatURL）
                    url = hit.get("largeImageURL") or hit.get("webformatURL", "")
                    if not url:
                        continue
                    img = req_lib.get(url, timeout=30)
                    if img.status_code != 200:
                        continue
                    tmp = out + ".dl.jpg"
                    Path(tmp).write_bytes(img.content)
                    ok = _apply_color_vf(tmp, color_key, out)
                    Path(tmp).unlink(missing_ok=True)
                    if ok:
                        fetched = True
                        print(f"  Pixabay取得: {name} ({q})")
                        break
                except Exception as e:
                    print(f"  Pixabay失敗 {q}: {e}", file=sys.stderr)
        except ImportError:
            pass

    if not fetched:
        _geq_fallback(name, out)
        print(f"  geqフォールバック: {name}")

    return out


def prepare_backgrounds(horses: list[dict], pixabay_key: str) -> dict[str, str]:
    """全章分の背景画像を準備してパスマップを返す。"""
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    imgs: dict[str, str] = {}
    for horse in horses:
        name      = horse["name"]
        color_key = _NAME_TO_COLOR.get(name, "__general__")
        out       = f"{ASSETS_DIR}/pog_bg_{name}.jpg"
        queries   = PIXABAY_QUERIES.get(name, PIXABAY_QUERIES["__general__"])
        imgs[name] = fetch_bg(name, queries, color_key, out, pixabay_key)
    imgs["__general__"] = fetch_bg(
        "__general__",
        PIXABAY_QUERIES["__general__"],
        "__general__",
        f"{ASSETS_DIR}/pog_bg_general.jpg",
        pixabay_key,
    )
    return imgs


# ─────────────────────────────────────────────────────────
# ASS 字幕 → 横動画用に変換（ass= フィルターで使うため）
# ─────────────────────────────────────────────────────────

def prepare_landscape_ass(src_ass: str, tmp_dir: str) -> str | None:
    """縦動画用 ASS（PlayResX:1080 PlayResY:1920）を横動画用に変換して tmp_dir に保存。"""
    if not Path(src_ass).exists() or Path(src_ass).stat().st_size < 100:
        return None

    content = Path(src_ass).read_text(encoding="utf-8")

    # 元ファイルのフォント名を引き継ぐ
    font_name = "Noto Sans CJK JP"
    for line in content.splitlines():
        if line.startswith("Style:"):
            parts = line.split(",")
            if len(parts) > 1:
                font_name = parts[1].strip()
            break

    # 章マーカー（【...】だけの行）を除いたダイアログ行のみ抽出
    marker_re = re.compile(r'^【[^】]+】$')
    dialogue_lines = []
    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        text = parts[9].replace("\\N", " ").strip()
        if marker_re.match(text):
            continue
        dialogue_lines.append(line)

    if not dialogue_lines:
        return None

    out = f"{tmp_dir}/landscape_subs.ass"
    with open(out, "w", encoding="utf-8") as f:
        f.write(_LANDSCAPE_ASS_HEADER.format(font_name=font_name))
        for line in dialogue_lines:
            f.write(line + "\n")

    return out


# ─────────────────────────────────────────────────────────
# 章タイミング
# ─────────────────────────────────────────────────────────

def chapters_from_meta(meta_chapters: list[dict], stripped_len: int,
                        aud_dur: float, timings_path: str | None = None) -> list[tuple[str, float]]:
    """チャプター開始時刻リストを返す。
    generate_audio.pyが保存したWordBoundaryベースのJSONがあればそちらを優先する。
    """
    if timings_path and Path(timings_path).exists():
        try:
            data = json.loads(Path(timings_path).read_text(encoding="utf-8"))
            times = [(d["title"], float(d["time_s"])) for d in data]
            print(f"  チャプタータイミング使用: {timings_path}")
            return times
        except Exception as e:
            print(f"  [警告] チャプタータイミング読み込み失敗: {e}", file=sys.stderr)
    # フォールバック: 文字数比率で近似
    return [
        (ch["title"], (ch.get("stripped_char_pos", 0) / max(stripped_len, 1)) * aud_dur)
        for ch in meta_chapters
    ]


def match_horse(chapter_title: str, horses: list[dict]) -> dict | None:
    for h in horses:
        if h["name"] in chapter_title:
            return h
    return None


# ─────────────────────────────────────────────────────────
# 血統表カード
# ─────────────────────────────────────────────────────────

def pedigree_card_filters(horse: dict, t1: float, t2: float,
                           ci: int, tmp_dir: str, fp: str) -> list[str]:
    enable = f"between(t,{t1:.3f},{t2:.3f})"
    vf: list[str] = []

    def tf(name: str, text: str) -> str:
        p = f"{tmp_dir}/{name}.txt"
        Path(p).write_text(text, encoding="utf-8")
        return _esc(p)

    # 背景パネル
    vf.append(
        f"drawtext=textfile='{tf(f'panel_{ci}', chr(12288)*22)}':fontfile='{fp}':"
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

    # 父セル（青）
    vf.append(
        f"drawtext=textfile='{tf(f'sl_{ci}', '父')}':fontfile='{fp}':"
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

    # 母セル（桃）
    vf.append(
        f"drawtext=textfile='{tf(f'dl_{ci}', '母')}':fontfile='{fp}':"
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

    # 落札額・注記・厩舎
    y_off = 108
    sale = horse.get("sale_price", "―")
    if sale and sale != "―":
        vf.append(
            f"drawtext=textfile='{tf(f'sale_{ci}', '落札額　' + sale)}':fontfile='{fp}':"
            f"fontsize=34:fontcolor=0xFFFF99:"
            f"x=(w-text_w)/2:y=h/2+{y_off}:"
            f"borderw=2:bordercolor=0x000000:"
            f"enable='{enable}'"
        )
        y_off += 44

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

    trainer = horse.get("trainer", "")
    if trainer:
        trainer_text = "厩舎：" + trainer
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

def corner_filters(fp: str, tmp_dir: str) -> list[str]:
    vf: list[str] = []
    sym = "◆"
    for name, cx, cy in [
        ("c_tl", "22",          "18"),
        ("c_tr", "w-text_w-22", "18"),
        ("c_bl", "22",          "h-text_h-18"),
        ("c_br", "w-text_w-22", "h-text_h-18"),
    ]:
        p = f"{tmp_dir}/{name}.txt"
        Path(p).write_text(sym, encoding="utf-8")
        vf.append(
            f"drawtext=textfile='{_esc(p)}':fontfile='{fp}':"
            f"fontsize=32:fontcolor=0xFFD700@0.80:"
            f"x={cx}:y={cy}:borderw=1:bordercolor=0x806000"
        )
    return vf


# ─────────────────────────────────────────────────────────
# 動画生成
# ─────────────────────────────────────────────────────────

def generate_video(meta: dict, font: str | None, bg_imgs: dict[str, str]) -> str:
    script_path   = Path(f"{OUTPUT_DIR}/script_0.txt")
    audio_path    = f"{OUTPUT_DIR}/audio_0.mp3"
    ass_path      = f"{OUTPUT_DIR}/subtitles_0.ass"
    output_path   = f"{OUTPUT_DIR}/pog_landscape_video_0.mp4"

    if not script_path.exists():
        raise FileNotFoundError(f"{script_path} が見つかりません。")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"{audio_path} が見つかりません。")

    stripped_script = script_path.read_text(encoding="utf-8").strip()
    aud_dur         = audio_duration(audio_path)
    horses          = meta.get("horses", [])
    meta_chapters   = meta.get("chapters", [])

    ch_times  = chapters_from_meta(
        meta_chapters, len(stripped_script), aud_dur,
        timings_path=f"{OUTPUT_DIR}/chapter_timings_0.json",
    )
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

            # コーナー装飾（常時）
            video_filters.extend(corner_filters(fp, tmp_dir))

            # オープニングカード
            video_filters.append(
                f"drawtext=textfile='{tf('open_t', wrap_text(meta.get('title', 'POG2026-2027'), 18))}':"
                f"fontfile='{fp}':fontsize=64:fontcolor=0xFFD700:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-22:"
                f"box=1:boxcolor=0x000000@0.85:boxborderw=28:"
                f"borderw=3:bordercolor=0x000000:"
                f"enable='between(t,0,{OPEN_DUR})'"
            )
            video_filters.append(
                f"drawtext=textfile='{tf('open_s', '本命3頭＋大穴2頭　完全解説')}':"
                f"fontfile='{fp}':fontsize=38:fontcolor=0xFFFFFF:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+60:"
                f"borderw=2:bordercolor=0x000000:"
                f"enable='between(t,0,{OPEN_DUR})'"
            )

            # 各章の血統カード（章全体で表示）
            for ci, seg in enumerate(segments):
                if not seg["horse"]:
                    continue
                t1 = seg["t_start"]
                t2 = seg["t_end"]  # 章終了まで表示し続ける
                if t2 <= t1 + 0.1:
                    continue
                video_filters.extend(
                    pedigree_card_filters(seg["horse"], t1, t2, ci, tmp_dir, fp)
                )

        if not video_filters:
            video_filters.append(f"scale={W}:{H}")

        vid_chain = "[bgout]" + ",".join(video_filters) + "[vout]"
        fc_parts  = list(pre_filters) + [vid_chain]

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
                   ["-i", audio_path, "-filter_complex", fc,
                    "-map", "[vout]", "-map", "[aout]"])

        cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(aud_dur + 0.5),
            output_path,
        ]

        print(f"  動画生成中... (音声: {aud_dur:.1f}s, 章: {N})")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            print(f"  [エラー] ffmpeg:\n{res.stderr[-800:]}", file=sys.stderr)
            raise RuntimeError(f"ffmpeg 失敗 rc={res.returncode}")

        size_mb = Path(output_path).stat().st_size / 1024 / 1024
        print(f"✅ {output_path} ({size_mb:.1f} MB)")
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

    meta         = json.loads(Path(POG_META_JSON).read_text(encoding="utf-8"))
    horses       = meta.get("horses", [])
    font         = find_font()
    pixabay_key  = os.environ.get("PIXABAY_API_KEY", "")
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    print("=== 背景画像を準備中 ===")
    bg_imgs = prepare_backgrounds(horses, pixabay_key)

    print("\n=== POG横動画生成中 ===")
    generate_video(meta, font, bg_imgs)


if __name__ == "__main__":
    main()
