#!/usr/bin/env python3
"""POG2026-2027 横向き（1280×720）動画生成。

主な特徴：
- Pixabay の馬写真 + タイプ別色処理（本命=暗赤金、大穴①=ブルー、大穴②=クリムゾン）
- ASS 字幕の単語イベントをセンテンス単位にマージして字幕ちらつき・途切れを解消
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
SUB_WRAP      = 22    # 字幕折り返し文字数
MERGE_GAP     = 0.25  # この秒数以内の ASS イベントを1センテンスにまとめる

# ── Pixabay クエリ（馬ごと） ────────────────────────────────────
PIXABAY_QUERIES: dict[str, list[str]] = {
    "ダノンダックス": ["horse portrait dark dramatic gold", "thoroughbred horse dark background", "horse racing dark spotlight"],
    "ジャンゴッド":   ["horse jockey racing dramatic", "horse galloping dark dramatic", "horse racing action dark"],
    "ソブリオ":      ["black stallion dark background dramatic", "horse portrait black dark", "stallion dark cinematic"],
    "ノイエルング":   ["horse blue night dramatic", "horse dark blue portrait", "stallion blue dramatic night"],
    "レニュアージュ": ["horse portrait brown red dramatic", "thoroughbred dark red portrait", "horse dark red atmospheric"],
    "__general__":   ["horse racing dramatic dark", "horse silhouette dramatic", "thoroughbred dark"],
}

# ── タイプ別 ffmpeg 色処理フィルター ───────────────────────────
_COLOR_VF: dict[str, str] = {
    # 本命: ダーク＋暖色ゴールド（gamma_r↑, gamma_b↓, 強コントラスト）
    "本命":    ("scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                "eq=brightness=-0.55:contrast=1.30:saturation=1.20:gamma_r=1.45:gamma_b=0.48,"
                "vignette=PI/2.5"),
    # 大穴_blue: ダーク＋電気ブルー（gamma_b↑↑, gamma_r↓↓, 低彩度）
    "大穴_blue": ("scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  "eq=brightness=-0.55:contrast=1.35:saturation=0.75:gamma_r=0.42:gamma_b=1.75,"
                  "vignette=PI/2.5"),
    # 大穴_red: ダーク＋クリムゾン赤（gamma_r↑↑, gamma_b↓↓）
    "大穴_red":  ("scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  "eq=brightness=-0.55:contrast=1.30:saturation=1.15:gamma_r=1.65:gamma_b=0.35,"
                  "vignette=PI/2.5"),
    "__general__": ("scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                    "eq=brightness=-0.58:contrast=1.15,"
                    "vignette=PI/2.5"),
}

# ── 馬名 → 色タイプ ─────────────────────────────────────────
_NAME_TO_COLOR: dict[str, str] = {
    "ダノンダックス": "本命",
    "ジャンゴッド":   "本命",
    "ソブリオ":      "本命",
    "ノイエルング":   "大穴_blue",
    "レニュアージュ": "大穴_red",
}

# ── geq フォールバック（Pixabay 失敗時）: 馬ごとに異なるスポットライト配置 ──
_GEQ_HORSE: dict[str, dict] = {
    # ダノンダックス: 左上からのアンバー日射し（競馬場の朝日）
    "ダノンダックス": dict(
        r="clip(8+210*pow(max(0,1-2.0*sqrt(pow((X/W-0.22)*1.2,2)+pow(Y/H-0.12,2))),1.8)+28*pow(1-Y/H,5),0,255)",
        g="clip(4+72*pow(max(0,1-2.0*sqrt(pow((X/W-0.22)*1.2,2)+pow(Y/H-0.12,2))),2.6)+6*pow(1-Y/H,5),0,255)",
        b="clip(2+8*pow(max(0,1-2.0*sqrt(pow((X/W-0.22)*1.2,2)+pow(Y/H-0.12,2))),4.5),0,255)",
    ),
    # ジャンゴッド: 右上からのゴールドスポット（疾走・力強さ）
    "ジャンゴッド": dict(
        r="clip(10+215*pow(max(0,1-2.1*sqrt(pow((X/W-0.78)*1.3,2)+pow(Y/H-0.15,2))),1.9),0,255)",
        g="clip(5+68*pow(max(0,1-2.1*sqrt(pow((X/W-0.78)*1.3,2)+pow(Y/H-0.15,2))),2.7),0,255)",
        b="clip(2+7*pow(max(0,1-2.1*sqrt(pow((X/W-0.78)*1.3,2)+pow(Y/H-0.15,2))),5.0),0,255)",
    ),
    # ソブリオ: 真上中央からの純金スポット（最高格の威厳）
    "ソブリオ": dict(
        r="clip(8+230*pow(max(0,1-2.3*sqrt(pow((X/W-0.5)*1.4,2)+pow(Y/H-0.18,2))),2.0),0,255)",
        g="clip(4+82*pow(max(0,1-2.3*sqrt(pow((X/W-0.5)*1.4,2)+pow(Y/H-0.18,2))),2.8),0,255)",
        b="clip(1+5*pow(max(0,1-2.3*sqrt(pow((X/W-0.5)*1.4,2)+pow(Y/H-0.18,2))),6.0),0,255)",
    ),
    # ノイエルング: 中心から広がるエレクトリックブルー（宇宙・星空）
    "ノイエルング": dict(
        r="clip(3+18*pow(max(0,1-2.8*sqrt(pow(X/W-0.5,2)+pow(Y/H-0.5,2))),2.5),0,255)",
        g="clip(5+45*pow(max(0,1-2.8*sqrt(pow(X/W-0.5,2)+pow(Y/H-0.5,2))),2.0),0,255)",
        b="clip(16+225*pow(max(0,1-2.3*sqrt(pow(X/W-0.5,2)+pow(Y/H-0.5,2))),1.6),0,255)",
    ),
    # レニュアージュ: 右中央からのクリムゾン大気光
    "レニュアージュ": dict(
        r="clip(12+210*pow(max(0,1-2.0*sqrt(pow((X/W-0.62)*1.2,2)+pow((Y/H-0.5)*1.3,2))),1.8),0,255)",
        g="clip(3+14*pow(max(0,1-2.0*sqrt(pow((X/W-0.62)*1.2,2)+pow((Y/H-0.5)*1.3,2))),3.5),0,255)",
        b="clip(2+6*pow(max(0,1-2.0*sqrt(pow((X/W-0.62)*1.2,2)+pow((Y/H-0.5)*1.3,2))),4.5),0,255)",
    ),
    # 汎用: 中心の微弱な白グロー（オープニング/エンディング）
    "__general__": dict(
        r="clip(6+70*pow(max(0,1-3.2*sqrt(pow(X/W-0.5,2)+pow(Y/H-0.5,2))),3.0),0,255)",
        g="clip(5+60*pow(max(0,1-3.2*sqrt(pow(X/W-0.5,2)+pow(Y/H-0.5,2))),3.0),0,255)",
        b="clip(4+50*pow(max(0,1-3.2*sqrt(pow(X/W-0.5,2)+pow(Y/H-0.5,2))),3.0),0,255)",
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
    """馬名ごとに異なるgeqスポットライト背景を生成する。"""
    g = _GEQ_HORSE.get(name, _GEQ_HORSE["__general__"])
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
                    }, timeout=30)
                    hits = r.json().get("hits", [])
                    if not hits:
                        continue
                    url = random.choice(hits).get("webformatURL", "")
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
# ASS 字幕 → drawtext 変換（センテンスマージ対応）
# ─────────────────────────────────────────────────────────

def _ass_time_to_s(t: str) -> float:
    h, m, rest = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def ass_to_drawtext_filters(ass_path: str, font: str | None, tmp_dir: str) -> list[str]:
    """ASS の単語イベントをセンテンス単位にマージして drawtext フィルターに変換する。

    edge-tts は単語ごとに Dialogue イベントを生成する。そのまま使うと
    0.3 秒ごとに1単語が点滅して「字幕がずれている」「途切れる」ように見える。
    MERGE_GAP 秒以内に連続するイベントをまとめることで自然な字幕表示にする。
    """
    content = Path(ass_path).read_text(encoding="utf-8")
    fp       = _esc(font) if font else ""
    marker_re = re.compile(r'^【[^】]+】$')

    # ① 全イベントをパース
    raw: list[tuple[float, float, str]] = []
    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        text = parts[9].replace("\\N", " ").strip()
        if not text or marker_re.match(text):
            continue
        try:
            t1 = _ass_time_to_s(parts[1])
            t2 = _ass_time_to_s(parts[2])
        except Exception:
            continue
        if t2 <= t1 + 0.01:
            continue
        raw.append((t1, t2, text))

    if not raw:
        return []

    # ② センテンス単位にマージ（MERGE_GAP 秒以内のギャップは同一センテンスとみなす）
    groups: list[tuple[float, float, str]] = []
    g_start, g_end, g_words = raw[0][0], raw[0][1], [raw[0][2]]
    for t1, t2, text in raw[1:]:
        if t1 - g_end <= MERGE_GAP:
            g_end = t2
            g_words.append(text)
        else:
            groups.append((g_start, g_end, "".join(g_words)))
            g_start, g_end, g_words = t1, t2, [text]
    groups.append((g_start, g_end, "".join(g_words)))

    # ③ drawtext フィルターに変換
    filters: list[str] = []
    for i, (t1, t2, text) in enumerate(groups):
        wrapped = wrap_text(text, SUB_WRAP)
        tf = f"{tmp_dir}/sub{i}.txt"
        Path(tf).write_text(wrapped, encoding="utf-8")
        f = f"drawtext=textfile='{_esc(tf)}'"
        if fp:
            f += f":fontfile='{fp}'"
        f += (
            f":fontsize=40:fontcolor=0xFFFFFF"
            f":x=(w-text_w)/2:y=h-text_h-55"
            f":box=1:boxcolor=0x000000@0.75:boxborderw=16"
            f":borderw=2:bordercolor=0x000000"
            f":line_spacing=10"
            f":enable='between(t,{t1:.3f},{t2:.3f})'"
        )
        filters.append(f)

    return filters


# ─────────────────────────────────────────────────────────
# 章タイミング
# ─────────────────────────────────────────────────────────

def chapters_from_meta(meta_chapters: list[dict], stripped_len: int,
                        aud_dur: float) -> list[tuple[str, float]]:
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
# サムネイル
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

    fc  = "[0:v]" + ",".join(filters) + "[vout]"
    res = subprocess.run(
        ["ffmpeg", "-y"] + inputs + ["-filter_complex", fc, "-map", "[vout]",
                                      "-frames:v", "1", "-q:v", "2", thumb_path],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        print(f"  サムネイル: {thumb_path} ({Path(thumb_path).stat().st_size//1024} KB)")
    else:
        print(f"  [警告] サムネイル失敗:\n{res.stderr[-300:]}", file=sys.stderr)


# ─────────────────────────────────────────────────────────
# 動画生成
# ─────────────────────────────────────────────────────────

def generate_video(meta: dict, font: str | None, bg_imgs: dict[str, str]) -> str:
    script_path   = Path(f"{OUTPUT_DIR}/script_0.txt")
    audio_path    = f"{OUTPUT_DIR}/audio_0.mp3"
    ass_path      = f"{OUTPUT_DIR}/subtitles_0.ass"
    output_path   = f"{OUTPUT_DIR}/pog_landscape_video_0.mp4"
    thumb_path    = f"{OUTPUT_DIR}/thumbnail_0.jpg"

    if not script_path.exists():
        raise FileNotFoundError(f"{script_path} が見つかりません。")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"{audio_path} が見つかりません。")

    stripped_script = script_path.read_text(encoding="utf-8").strip()
    aud_dur         = audio_duration(audio_path)
    horses          = meta.get("horses", [])
    meta_chapters   = meta.get("chapters", [])

    ch_times  = chapters_from_meta(meta_chapters, len(stripped_script), aud_dur)
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

            # 各章の血統カード
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

            # 字幕
            has_ass = Path(ass_path).exists() and Path(ass_path).stat().st_size > 100
            if has_ass:
                sub_fts = ass_to_drawtext_filters(ass_path, font, tmp_dir)
                video_filters.extend(sub_fts)
                print(f"  字幕: {len(sub_fts)} センテンス")
            else:
                print("  [警告] ASS字幕なし", file=sys.stderr)

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
