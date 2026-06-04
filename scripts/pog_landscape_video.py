#!/usr/bin/env python3
"""POG2026-2027 横向き（1280×720）動画生成。

landscape_video.py と異なる点：
- pog_meta.json を読み込み、章ごとに専用の背景画像（父/母の Wikipedia 写真）を使用
- 各馬の章冒頭に netkeiba 風の血統表カードオーバーレイを表示
  （父セル＝青系、母セル＝ピンク系の色分け）
"""
import glob
import json
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

POG_META_JSON = "pog_meta.json"
OUTPUT_DIR    = "output"
ASSETS_DIR    = "assets"
BGM_DIR       = f"{ASSETS_DIR}/bgm"
W, H          = 1280, 720
FPS           = 30
OPEN_DUR      = 3.5   # オープニングカード表示秒数
PEDIGREE_DUR  = 4.5   # 血統カード表示秒数
BGM_VOL       = 0.12

_SSL_CTX = ssl.create_default_context()
_UA      = "keiba-auto-youtube/1.0"
_WP_SKIP = {".svg", ".ogv", ".ogg", ".webm", ".gif"}


# ──────────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# 画像取得：章ごとに専用写真を使う
# ──────────────────────────────────────────────

def _fetch_wikipedia_image(queries: list[str], out_path: str) -> bool:
    """複数クエリを順番に試して Wikipedia(ja→en)から馬の写真を取得する。"""
    for query in queries:
        for lang in ("ja", "en"):
            encoded = urllib.parse.quote(query)
            api_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
            try:
                req = urllib.request.Request(
                    api_url, headers={"User-Agent": _UA, "Accept": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as r:
                    data = json.loads(r.read())
            except Exception:
                continue
            src = (data.get("originalimage") or data.get("thumbnail") or {}).get("source", "")
            if not src or Path(src.split("?")[0]).suffix.lower() in _WP_SKIP:
                continue
            ext = Path(src.split("?")[0]).suffix.lower() or ".jpg"
            try:
                req2 = urllib.request.Request(src, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req2, timeout=30, context=_SSL_CTX) as r:
                    raw = r.read()
            except Exception:
                continue
            tmp = out_path + ".raw" + ext
            Path(tmp).write_bytes(raw)
            res = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp, "-frames:v", "1", "-q:v", "2", out_path],
                capture_output=True,
            )
            Path(tmp).unlink(missing_ok=True)
            if res.returncode == 0:
                print(f"  Wikipedia画像取得: {query}")
                return True
    return False


def _geq_fallback(out_path: str, color_idx: int) -> None:
    """グラデーション画像を geq で生成（競馬らしい暗めの色調）。"""
    palettes = [
        ("clip(10+120*pow(Y/H,1.5),10,130)", "clip(4*pow(Y/H,2),0,4)", "clip(4*pow(Y/H,2),0,4)"),
        ("clip(5*pow(Y/H,2),0,5)", "clip(5*pow(Y/H,2),0,5)", "clip(12+100*pow(Y/H,1.4),12,112)"),
        ("clip(8+110*pow(1-Y/H,1.4),8,118)", "clip(6+60*pow(1-Y/H,1.6),6,66)", "clip(2,0,2)"),
        ("clip(5*pow(1-Y/H,2),0,5)", "clip(8+70*pow(1-Y/H,1.5),8,78)", "clip(8+80*pow(Y/H,1.5),8,88)"),
        ("clip(12+90*pow(Y/H,1.3),12,102)", "clip(10+70*pow(1-Y/H,1.5),10,80)", "clip(3,0,3)"),
    ]
    r_e, g_e, b_e = palettes[color_idx % len(palettes)]
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=1",
        "-vf", f"geq=r='{r_e}':g='{g_e}':b='{b_e}'",
        "-frames:v", "1", "-q:v", "3", out_path,
    ], capture_output=True)


def fetch_chapter_images(horses: list[dict]) -> dict[str, str]:
    """馬名 → 背景画像パス のマッピングを返す。
    各馬の image_queries で Wikipedia を試し、失敗時は geq フォールバック。
    オープニング/エンディング用の汎用画像も用意する。
    """
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    images: dict[str, str] = {}

    for i, horse in enumerate(horses):
        out = f"{ASSETS_DIR}/pog_{i}_{horse['name']}.jpg"
        if Path(out).exists() and Path(out).stat().st_size > 5000:
            print(f"  キャッシュ使用: {out}")
            images[horse["name"]] = out
            continue
        if not _fetch_wikipedia_image(horse.get("image_queries", []), out):
            _geq_fallback(out, i)
        images[horse["name"]] = out

    # オープニング/エンディング用（競馬一般画像 or Pixabay）
    general_out = f"{ASSETS_DIR}/pog_general.jpg"
    if not (Path(general_out).exists() and Path(general_out).stat().st_size > 5000):
        pixabay_key = os.environ.get("PIXABAY_API_KEY", "")
        fetched = False
        if pixabay_key:
            try:
                import requests as req_lib
                r = req_lib.get("https://pixabay.com/api/", params={
                    "key": pixabay_key, "q": "horse racing japan", "image_type": "photo",
                    "orientation": "horizontal", "min_width": 1280, "min_height": 720,
                    "per_page": 20, "safesearch": "true",
                }, timeout=30)
                hits = r.json().get("hits", [])
                if hits:
                    url = random.choice(hits).get("webformatURL", "")
                    if url:
                        img = req_lib.get(url, timeout=30)
                        if img.status_code == 200:
                            tmp = general_out + ".tmp"
                            Path(tmp).write_bytes(img.content)
                            res = subprocess.run(
                                ["ffmpeg", "-y", "-i", tmp, "-frames:v", "1", "-q:v", "2", general_out],
                                capture_output=True,
                            )
                            Path(tmp).unlink(missing_ok=True)
                            fetched = res.returncode == 0
            except Exception as e:
                print(f"  [警告] Pixabay失敗: {e}", file=sys.stderr)
        if not fetched:
            _geq_fallback(general_out, len(horses))
    images["__general__"] = general_out

    return images


# ──────────────────────────────────────────────
# ASS 字幕 → drawtext 変換
# ──────────────────────────────────────────────

def _ass_time_to_s(t: str) -> float:
    h, m, rest = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def ass_to_drawtext_filters(ass_path: str, font: str | None, tmp_dir: str) -> list[str]:
    content = Path(ass_path).read_text(encoding="utf-8")
    fp = _esc(font) if font else ""
    filters: list[str] = []
    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        text = parts[9].replace("\\N", "\n").strip()
        if not text:
            continue
        try:
            t1 = _ass_time_to_s(parts[1])
            t2 = _ass_time_to_s(parts[2])
        except Exception:
            continue
        if t2 <= t1 + 0.01:
            continue
        tf = f"{tmp_dir}/s{len(filters)}.txt"
        Path(tf).write_text(text, encoding="utf-8")
        base = f"drawtext=textfile='{_esc(tf)}'"
        if fp:
            base += f":fontfile='{fp}'"
        base += (
            f":fontsize=36:fontcolor=0xFFFFFF"
            f":x=(w-text_w)/2:y=h-text_h-40"
            f":box=1:boxcolor=0x000000@0.65:boxborderw=10"
            f":borderw=2:bordercolor=0x000000"
            f":enable='between(t,{t1:.3f},{t2:.3f})'"
        )
        filters.append(base)
    return filters


# ──────────────────────────────────────────────
# 章タイミング計算
# ──────────────────────────────────────────────

def parse_chapters(script: str) -> list[tuple[str, int]]:
    chapters: list[tuple[str, int]] = []
    pos = 0
    for block in script.split("\n\n"):
        stripped = block.strip()
        if stripped.startswith("【") and "】" in stripped:
            header = stripped.split("\n")[0].strip()
            chapters.append((header, pos))
        pos += len(block) + 2
    return chapters


def match_horse(chapter_title: str, horses: list[dict]) -> dict | None:
    for h in horses:
        if h["name"] in chapter_title:
            return h
    return None


# ──────────────────────────────────────────────
# 血統表カード（netkeiba 風）drawtext フィルター生成
# ──────────────────────────────────────────────

def pedigree_card_filters(
    horse: dict, t1: float, t2: float, ci: int, tmp_dir: str, fp: str
) -> list[str]:
    """章の冒頭 PEDIGREE_DUR 秒間に血統表カードを表示するフィルターを返す。

    レイアウト（横型 1280×720）：
    ┌──────────────────────────────────────┐
    │  【本命①】  ダノンダックス            │ ← 馬名（金/赤）
    │  ┌────────────────────────────────┐  │
    │  │ 父（青）  サートゥルナーリア    │  │
    │  ├────────────────────────────────┤  │
    │  │ 母（桃）  ヤンキーローズ        │  │
    │  └────────────────────────────────┘  │
    │  半姉：リバティアイランド（牝馬三冠） │ ← 注記（緑）
    │  田中博康（美浦）                     │ ← 厩舎（白小）
    └──────────────────────────────────────┘
    """
    enable = f"between(t,{t1:.3f},{t2:.3f})"
    vf: list[str] = []

    def tf(name: str, text: str) -> str:
        p = f"{tmp_dir}/{name}.txt"
        Path(p).write_text(text, encoding="utf-8")
        return _esc(p)

    # ── 背景パネル（全体）──────────────────────────────
    # 全角スペースを大量に並べた幅広テキストで大きな box を作る
    panel_f = tf(f"panel_{ci}", "　" * 22)
    vf.append(
        f"drawtext=textfile='{panel_f}':fontfile='{fp}':fontsize=52:fontcolor=0x000000@0:"
        f"x=(w-text_w)/2:y=h/2-180:"
        f"box=1:boxcolor=0x0A0A1A@0.88:boxborderw=200:"
        f"enable='{enable}'"
    )

    # ── タイプバッジ（本命＝深紅、大穴＝オレンジ）──────
    horse_type = horse.get("type", "本命")
    badge_col  = "0x990000" if horse_type == "本命" else "0xCC4400"
    badge_f    = tf(f"badge_{ci}", f"【{horse_type}】")
    vf.append(
        f"drawtext=textfile='{badge_f}':fontfile='{fp}':fontsize=36:fontcolor=0xFFFFFF:"
        f"x=(w-text_w)/2:y=h/2-176:"
        f"box=1:boxcolor={badge_col}@0.95:boxborderw=14:"
        f"enable='{enable}'"
    )

    # ── 馬名（本命＝金、大穴＝赤橙）──────────────────
    name_col = "0xFFD700" if horse_type == "本命" else "0xFF6347"
    name_f   = tf(f"hname_{ci}", horse["name"])
    vf.append(
        f"drawtext=textfile='{name_f}':fontfile='{fp}':fontsize=78:fontcolor={name_col}:"
        f"x=(w-text_w)/2:y=h/2-116:"
        f"borderw=3:bordercolor=0x000000:"
        f"enable='{enable}'"
    )

    # ── 父セル（青系：netkeiba の青列を模倣）────────────
    sire_label_f = tf(f"sire_label_{ci}", "父")
    vf.append(
        f"drawtext=textfile='{sire_label_f}':fontfile='{fp}':fontsize=38:fontcolor=0x1A2A6C:"
        f"x=(w-text_w)/2-200:y=h/2-22:"
        f"box=1:boxcolor=0xADD8E6@0.90:boxborderw=22:"
        f"enable='{enable}'"
    )
    sire_f = tf(f"sire_{ci}", horse["sire"])
    vf.append(
        f"drawtext=textfile='{sire_f}':fontfile='{fp}':fontsize=46:fontcolor=0x1A2A6C:"
        f"x=(w-text_w)/2+30:y=h/2-28:"
        f"box=1:boxcolor=0xD6EAF8@0.85:boxborderw=18:"
        f"borderw=1:bordercolor=0x5B9BD5:"
        f"enable='{enable}'"
    )

    # ── 母セル（桃系：netkeiba の桃列を模倣）────────────
    dam_label_f = tf(f"dam_label_{ci}", "母")
    vf.append(
        f"drawtext=textfile='{dam_label_f}':fontfile='{fp}':fontsize=38:fontcolor=0x6C1A2A:"
        f"x=(w-text_w)/2-200:y=h/2+42:"
        f"box=1:boxcolor=0xFFB6C1@0.90:boxborderw=22:"
        f"enable='{enable}'"
    )
    dam_f = tf(f"dam_{ci}", horse["dam"])
    vf.append(
        f"drawtext=textfile='{dam_f}':fontfile='{fp}':fontsize=46:fontcolor=0x6C1A2A:"
        f"x=(w-text_w)/2+30:y=h/2+36:"
        f"box=1:boxcolor=0xFAD7E0@0.85:boxborderw=18:"
        f"borderw=1:bordercolor=0xE07090:"
        f"enable='{enable}'"
    )

    # ── セール価格（あれば）──────────────────────────
    sale = horse.get("sale_price", "")
    if sale and sale != "―":
        sale_f = tf(f"sale_{ci}", f"落札額　{sale}")
        vf.append(
            f"drawtext=textfile='{sale_f}':fontfile='{fp}':fontsize=32:fontcolor=0xFFFF99:"
            f"x=(w-text_w)/2:y=h/2+102:"
            f"borderw=2:bordercolor=0x000000:"
            f"enable='{enable}'"
        )

    # ── 注記（緑）────────────────────────────────────
    note = horse.get("note", "")
    if note:
        note_y = "h/2+142" if (sale and sale != "―") else "h/2+102"
        note_f = tf(f"note_{ci}", note)
        vf.append(
            f"drawtext=textfile='{note_f}':fontfile='{fp}':fontsize=34:fontcolor=0x7FFF00:"
            f"x=(w-text_w)/2:y={note_y}:"
            f"borderw=2:bordercolor=0x000000:"
            f"enable='{enable}'"
        )

    # ── 厩舎（白小）─────────────────────────────────
    trainer_y = "h/2+182" if (sale and sale != "―") else "h/2+142"
    trainer_f = tf(f"trainer_{ci}", f"厩舎：{horse.get('trainer', '')}")
    vf.append(
        f"drawtext=textfile='{trainer_f}':fontfile='{fp}':fontsize=30:fontcolor=0xCCCCCC:"
        f"x=(w-text_w)/2:y={trainer_y}:"
        f"borderw=1:bordercolor=0x000000:"
        f"enable='{enable}'"
    )

    return vf


# ──────────────────────────────────────────────
# メイン動画生成
# ──────────────────────────────────────────────

def generate_thumbnail(meta: dict, font: str | None, bg_img: str | None,
                        thumb_path: str, tmp_dir: str) -> None:
    fp = _esc(font or "")
    hook = meta.get("thumbnail_hook", "")
    title = meta.get("title", "")

    if bg_img and Path(bg_img).exists():
        inputs = ["-loop", "1", "-i", bg_img]
        v_init = [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}"]
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c=#0D1B2A:s={W}x{H}:r=1"]
        v_init = []

    filters: list[str] = v_init + ["eq=brightness=-0.40:contrast=1.05"]

    if font:
        def tf(name: str, text: str) -> str:
            p = f"{tmp_dir}/{name}.txt"
            Path(p).write_text(text, encoding="utf-8")
            return _esc(p)

        badge_f = tf("th_badge", "POG 2026-2027")
        filters.append(
            f"drawtext=textfile='{badge_f}':fontfile='{fp}':fontsize=38:fontcolor=0xFFFFFF:"
            f"x=30:y=30:box=1:boxcolor=0x990000@0.95:boxborderw=16"
        )
        if hook:
            hook_f = tf("th_hook", wrap_text(hook, 14))
            filters.append(
                f"drawtext=textfile='{hook_f}':fontfile='{fp}':fontsize=74:fontcolor=0xFFFF00:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-30:"
                f"box=1:boxcolor=0x000000@0.88:boxborderw=30:"
                f"borderw=5:bordercolor=0xFF6600"
            )
        if title:
            title_f = tf("th_title", wrap_text(title, 18))
            filters.append(
                f"drawtext=textfile='{title_f}':fontfile='{fp}':fontsize=38:fontcolor=0xFFFFFF:"
                f"x=(w-text_w)/2:y=h-text_h-50:"
                f"box=1:boxcolor=0x000000@0.80:boxborderw=16"
            )

    fc = "[0:v]" + ",".join(filters) + "[vout]"
    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc, "-map", "[vout]",
        "-frames:v", "1", "-q:v", "2", thumb_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        size_kb = Path(thumb_path).stat().st_size // 1024
        print(f"  サムネイル: {thumb_path} ({size_kb} KB)")
    else:
        print(f"  [警告] サムネイル生成失敗:\n{result.stderr[-300:]}", file=sys.stderr)


def generate_video(meta: dict, font: str | None, chapter_images: dict[str, str]) -> str:
    script_path = Path(f"{OUTPUT_DIR}/script_0.txt")
    audio_path  = f"{OUTPUT_DIR}/audio_0.mp3"
    ass_path    = f"{OUTPUT_DIR}/subtitles_0.ass"
    output_path = f"{OUTPUT_DIR}/pog_landscape_video_0.mp4"
    thumb_path  = f"{OUTPUT_DIR}/thumbnail_0.jpg"

    if not script_path.exists():
        raise FileNotFoundError(f"{script_path} が見つかりません。")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"{audio_path} が見つかりません。")

    script     = script_path.read_text(encoding="utf-8").strip()
    aud_dur    = audio_duration(audio_path)
    total_dur  = aud_dur
    total_chars = max(len(script), 1)
    horses     = meta.get("horses", [])
    chapters   = parse_chapters(script)

    def ch_t(char_pos: int) -> float:
        return (char_pos / total_chars) * aud_dur

    # 章ごとの開始・終了時刻と対応画像を計算
    chapter_segments: list[dict] = []
    for ci, (title, pos) in enumerate(chapters):
        t_start = ch_t(pos)
        t_end   = ch_t(chapters[ci + 1][1]) if ci + 1 < len(chapters) else total_dur
        horse   = match_horse(title, horses)
        bg_img  = chapter_images.get(horse["name"]) if horse else chapter_images.get("__general__")
        chapter_segments.append({
            "title":   title,
            "t_start": t_start,
            "t_end":   t_end,
            "dur":     max(t_end - t_start, 0.1),
            "horse":   horse,
            "bg_img":  bg_img or chapter_images.get("__general__"),
        })

    tmp_dir = tempfile.mkdtemp(prefix="pog_ls_")
    try:
        fp = _esc(font or "")

        img_scale = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{H},eq=brightness=-0.10,setsar=1")

        N = len(chapter_segments)
        bg_inputs: list[str] = []
        pre_filters: list[str] = []

        for i, seg in enumerate(chapter_segments):
            bg = seg["bg_img"]
            if bg and Path(bg).exists():
                bg_inputs += ["-r", str(FPS), "-loop", "1", "-t", f"{seg['dur']:.3f}", "-i", bg]
            else:
                bg_inputs += [
                    "-f", "lavfi", "-i",
                    f"color=c=#0D1B2A:s={W}x{H}:r={FPS}:d={seg['dur']:.3f}",
                ]
            pre_filters.append(f"[{i}:v]{img_scale}[vi{i}]")

        concat_in = "".join(f"[vi{i}]" for i in range(N))
        pre_filters.append(f"{concat_in}concat=n={N}:v=1:a=0[bgout]")
        audio_idx = N

        video_filters: list[str] = []

        if font:
            # ── オープニングカード ────────────────────────────
            def tf(name: str, text: str) -> str:
                p = f"{tmp_dir}/{name}.txt"
                Path(p).write_text(text, encoding="utf-8")
                return _esc(p)

            title_f = tf("open_title", wrap_text(meta.get("title", "POG2026-2027"), 16))
            video_filters.append(
                f"drawtext=textfile='{title_f}':fontfile='{fp}':fontsize=68:fontcolor=0xFFD700:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-20:"
                f"box=1:boxcolor=0x000000@0.85:boxborderw=28:"
                f"borderw=3:bordercolor=0x000000:"
                f"enable='between(t,0,{OPEN_DUR})'"
            )
            sub_title_f = tf("open_sub", "本命3頭＋大穴2頭　完全解説")
            video_filters.append(
                f"drawtext=textfile='{sub_title_f}':fontfile='{fp}':fontsize=38:fontcolor=0xFFFFFF:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+60:"
                f"borderw=2:bordercolor=0x000000:"
                f"enable='between(t,0,{OPEN_DUR})'"
            )

            # ── 各章：血統カード ＋ 章タイトル ───────────────
            for ci, seg in enumerate(chapter_segments):
                t1 = seg["t_start"]
                t2 = min(t1 + PEDIGREE_DUR, seg["t_end"])
                if t2 <= t1 + 0.1:
                    continue

                if seg["horse"]:
                    # 血統カードオーバーレイ
                    video_filters.extend(
                        pedigree_card_filters(seg["horse"], t1, t2, ci, tmp_dir, fp)
                    )
                else:
                    # 馬のない章（オープニング/エンディング）はシンプルな章タイトルのみ
                    ch_f = tf(f"ch_{ci}", wrap_text(seg["title"], 14))
                    video_filters.append(
                        f"drawtext=textfile='{ch_f}':fontfile='{fp}':fontsize=54:fontcolor=0xFFD700:"
                        f"x=(w-text_w)/2:y=(h-text_h)/2:"
                        f"box=1:boxcolor=0x000000@0.85:boxborderw=36:"
                        f"borderw=4:bordercolor=0x000000:"
                        f"enable='between(t,{t1:.3f},{t2:.3f})'"
                    )

            # ── ASS 字幕 ────────────────────────────────────
            has_ass = Path(ass_path).exists() and Path(ass_path).stat().st_size > 100
            if has_ass:
                sub_filters = ass_to_drawtext_filters(ass_path, font, tmp_dir)
                video_filters.extend(sub_filters)
                print(f"  字幕 drawtext: {len(sub_filters)} セグメント")
            else:
                print("  [警告] ASS字幕なし。字幕なしで生成します。", file=sys.stderr)

        if not video_filters:
            video_filters.append(f"scale={W}:{H}")

        vid_chain = "[bgout]" + ",".join(video_filters) + "[vout]"
        fc_parts  = list(pre_filters) + [vid_chain]

        # ── BGM ────────────────────────────────────────────
        bgm_files = sorted(glob.glob(f"{BGM_DIR}/*.mp3") + glob.glob(f"{BGM_DIR}/*.m4a"))
        bgm_file  = random.choice(bgm_files) if bgm_files else None

        if bgm_file:
            print(f"  BGM: {Path(bgm_file).name}")
            audio_chain = (
                f"[{audio_idx}:a]apad=whole_dur={total_dur:.3f}[narr];"
                f"[narr][{audio_idx+1}:a]amix=inputs=2:duration=first:weights=1 {BGM_VOL}[aout]"
            )
            fc = ";".join(fc_parts) + ";" + audio_chain
            cmd = (["ffmpeg", "-y"] + bg_inputs +
                   ["-i", audio_path, "-stream_loop", "-1", "-i", bgm_file,
                    "-filter_complex", fc,
                    "-map", "[vout]", "-map", "[aout]"])
        else:
            audio_chain = f"[{audio_idx}:a]apad=whole_dur={total_dur:.3f}[aout]"
            fc = ";".join(fc_parts) + ";" + audio_chain
            cmd = (["ffmpeg", "-y"] + bg_inputs +
                   ["-i", audio_path,
                    "-filter_complex", fc,
                    "-map", "[vout]", "-map", "[aout]"])

        cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(total_dur + 0.5),
            output_path,
        ]

        print(f"  動画生成中... (音声長: {total_dur:.1f}s, 章数: {N})")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"  [エラー] ffmpeg失敗:\n{result.stderr[-800:]}", file=sys.stderr)
            raise RuntimeError(f"ffmpeg失敗 returncode={result.returncode}")

        size_mb = Path(output_path).stat().st_size / (1024 * 1024)
        print(f"✅ {output_path} ({size_mb:.1f} MB)")

        bg_img = chapter_images.get("__general__")
        generate_thumbnail(meta, font, bg_img, thumb_path, tmp_dir)
        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ──────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────

def main() -> None:
    if not Path(POG_META_JSON).exists():
        print(f"[エラー] {POG_META_JSON} が見つかりません。先に create_pog_video.py を実行してください。",
              file=sys.stderr)
        sys.exit(1)

    meta   = json.loads(Path(POG_META_JSON).read_text(encoding="utf-8"))
    horses = meta.get("horses", [])
    font   = find_font()
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    print("=== 馬ごとの背景画像を取得中 ===")
    chapter_images = fetch_chapter_images(horses)

    print("\n=== POG横動画生成中 ===")
    generate_video(meta, font, chapter_images)


if __name__ == "__main__":
    main()
