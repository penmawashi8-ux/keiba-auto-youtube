#!/usr/bin/env python3
"""セレクトセール高額落札ランキングの専用横型動画（1280×720）を生成する。

landscape_video.py の汎用「字幕垂れ流し」ではなく、テレビ番組のランキング
発表のような構造化された画面を作る:

- 常設タイトルバー + 進行インジケーター（10→1、現在の順位をハイライト）
- 順位ごとの情報カード（巨大な順位数字・馬名・父・購買者・特大の落札価格）
- 落札価格の比較バー（█ブロック、drawbox不使用でdrawtextのみ）
- TOP3は 金/銀/銅 の色演出
- オープニングカードとまとめカード（TOP3リキャップ）

カードの切り替え時刻は ASS 字幕の「第N位、」「以上、」の実測開始時刻を
使うためナレーションと正確に同期する。

入力:  news.json / output/ranking_meta_0.json / output/audio_0.mp3 /
       output/subtitles_0.ass
出力:  output/landscape_video_0.mp4 / output/thumbnail_0.jpg
（後続の upload_landscape_youtube.py は変更不要）
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

sys.path.insert(0, str(Path(__file__).parent))
from landscape_video import (
    find_font, fetch_images, audio_duration, _esc, _ass_time_to_s, wrap_text,
)
from create_select_sale_ranking import format_price

OUTPUT_DIR = "output"
NEWS_JSON = "news.json"
BGM_DIR = "assets/bgm"
W, H = 1280, 720
FPS = 30
BGM_VOL = 0.10

GOLD = "0xFFD700"
SILVER = "0xC8C8C8"
BRONZE = "0xCD8032"
WHITE = "0xFFFFFF"
GRAY = "0x9A9A9A"
PRICE_COL = "0xFFE14D"
PANEL = "0x000000@0.62"
PANEL_DARK = "0x000000@0.78"

RANK_COLORS = {1: GOLD, 2: SILVER, 3: BRONZE}


def parse_ass_dialogues(ass_path: str) -> list[tuple[float, float, str]]:
    """ASSのDialogue行を (start_s, end_s, text) のリストで返す。"""
    out = []
    for line in Path(ass_path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        try:
            t1, t2 = _ass_time_to_s(parts[1]), _ass_time_to_s(parts[2])
        except Exception:
            continue
        text = parts[9].replace("\\N", "\n").strip()
        if text:
            out.append((t1, t2, text))
    return out


def compute_card_times(dialogues, ranks: list[int], total_dur: float) -> dict:
    """各順位カード・まとめカードの開始時刻をASS字幕から特定する。

    Returns: {"rank_start": {rank: t}, "outro_start": t}
    """
    rank_start: dict[int, float] = {}
    outro_start = None
    for t1, _t2, text in dialogues:
        # 「【第N位】」（見出し）「第N位、馬名。」「第N位第N位、…」（TTSが
        # 見出しと本文を連結したケース）のいずれもセグメント先頭の第N位で認識する
        m = re.match(r"【?第(\d+)位", text)
        if m:
            r = int(m.group(1))
            if r in ranks and r not in rank_start:
                rank_start[r] = t1
        elif outro_start is None and (text.startswith("以上") or text.startswith("【まとめ】")):
            outro_start = t1

    # フォールバック: 見つからない順位は等間隔で補間
    missing = [r for r in ranks if r not in rank_start]
    if missing:
        print(f"  [警告] ASSから開始時刻が取れない順位: {missing}（等間隔で補間）", file=sys.stderr)
        seg = total_dur / (len(ranks) + 2)
        for i, r in enumerate(sorted(ranks, reverse=True)):
            rank_start.setdefault(r, seg * (i + 1))
    if outro_start is None:
        outro_start = total_dur - 12.0
    return {"rank_start": rank_start, "outro_start": outro_start}


class FilterBuilder:
    """drawtextフィルター列を組み立てる（textfile方式・drawbox不使用）。"""

    def __init__(self, font: str, tmp_dir: str):
        self.font = _esc(font)
        self.tmp = tmp_dir
        self.filters: list[str] = []
        self._n = 0

    def text(self, s: str, *, size: int, color: str, x: str, y: str,
             box: str | None = None, borderw: int = 0, border_color: str = "0x000000",
             boxborderw: int = 14, enable: str | None = None) -> None:
        tf = f"{self.tmp}/t{self._n}.txt"
        self._n += 1
        Path(tf).write_text(s, encoding="utf-8")
        f = (f"drawtext=textfile='{_esc(tf)}':fontfile='{self.font}'"
             f":fontsize={size}:fontcolor={color}:x={x}:y={y}")
        if box:
            f += f":box=1:boxcolor={box}:boxborderw={boxborderw}"
        if borderw:
            f += f":borderw={borderw}:bordercolor={border_color}"
        if enable:
            f += f":enable='{enable}'"
        self.filters.append(f)


def price_bar(price_man: int, max_price_man: int, max_blocks: int = 16) -> str:
    n = max(1, round(price_man / max_price_man * max_blocks))
    return "█" * n


def build_filters(fb: FilterBuilder, meta: dict, ranking: list[dict],
                  times: dict, total_dur: float, dialogues) -> None:
    year, session = meta["year"], meta["session"]
    rank_start = times["rank_start"]
    outro_t = times["outro_start"]
    intro_end = min(rank_start.values()) if rank_start else 10.0
    max_price = max(r["price_man"] for r in ranking)

    def seg_end(rank: int) -> float:
        return rank_start.get(rank - 1, outro_t) if rank > 1 else outro_t

    # ===== 常設: タイトルバー =====
    fb.text(f"セレクトセール{year} {session}セール 高額落札ランキング",
            size=30, color=WHITE, x="(w-text_w)/2", y="16",
            box=PANEL_DARK, boxborderw=12)

    # ===== 常設: 進行インジケーター 10..1 =====
    strip_y = 74
    cell = 52
    x0 = (W - cell * 10) // 2
    for i, r in enumerate(range(10, 0, -1)):
        x = x0 + i * cell
        on = f"between(t,{rank_start.get(r, 1e9):.2f},{seg_end(r):.2f})"
        fb.text(str(r), size=26, color=GRAY, x=str(x), y=str(strip_y),
                box="0x000000@0.55", boxborderw=8)
        fb.text(str(r), size=30, color=RANK_COLORS.get(r, GOLD), x=str(x - 2),
                y=str(strip_y - 3), box="0xAA0000@0.9", boxborderw=10, enable=on)

    # ===== オープニングカード =====
    en_intro = f"between(t,0,{intro_end:.2f})"
    fb.text(f"セレクトセール{year}", size=64, color=WHITE,
            x="(w-text_w)/2", y="200", box=PANEL_DARK, boxborderw=26,
            borderw=2, enable=en_intro)
    fb.text(f"{session}セール 高額落札ランキング", size=46, color=WHITE,
            x="(w-text_w)/2", y="320", box=PANEL_DARK, boxborderw=20, enable=en_intro)
    fb.text("TOP 10", size=110, color=GOLD, x="(w-text_w)/2", y="410",
            box="0xAA0000@0.9", boxborderw=28, borderw=3, enable=en_intro)
    fb.text(f"{meta['date_str']}  ノーザンホースパーク", size=30, color=WHITE,
            x="(w-text_w)/2", y="590", borderw=2, enable=en_intro)

    # ===== 順位カード =====
    for lot in ranking:
        r = lot["rank"]
        t1, t2 = rank_start.get(r), seg_end(r)
        if t1 is None or t2 <= t1 + 0.1:
            continue
        en = f"between(t,{t1:.2f},{t2:.2f})"
        col = RANK_COLORS.get(r, WHITE)

        fb.text(f"第{r}位", size=92, color=col, x="64", y="128",
                box=PANEL_DARK, boxborderw=22, borderw=3, enable=en)
        fb.text(lot["name"], size=58, color=WHITE, x="64", y="272",
                box=PANEL, boxborderw=16, borderw=2, enable=en)
        detail = f"父 {lot['sire']}" if lot["sire"] else ""
        if detail:
            fb.text(detail, size=38, color=WHITE, x="64", y="368",
                    box="0x14325A@0.85", boxborderw=12, enable=en)
        if lot["buyer"]:
            fb.text(f"購買者 {lot['buyer']}", size=34, color=WHITE, x="64", y="436",
                    box="0x14325A@0.85", boxborderw=12, enable=en)
        fb.text(format_price(lot["price_man"]), size=82, color=PRICE_COL,
                x="64", y="500", box=PANEL_DARK, boxborderw=20,
                borderw=3, border_color="0x7A4A00", enable=en)
        fb.text(price_bar(lot["price_man"], max_price), size=24, color=col,
                x="66", y="630", enable=en)

    # ===== まとめカード（TOP3リキャップ）=====
    en_out = f"between(t,{outro_t:.2f},{total_dur + 1:.2f})"
    fb.text("結果まとめ  TOP 3", size=48, color=WHITE, x="(w-text_w)/2", y="150",
            box=PANEL_DARK, boxborderw=20, enable=en_out)
    for i, lot in enumerate(ranking[:3]):
        line = f"{lot['rank']}位  {lot['name']}  {format_price(lot['price_man'])}"
        fb.text(line, size=42, color=RANK_COLORS[lot["rank"]],
                x="(w-text_w)/2", y=str(260 + i * 92),
                box=PANEL_DARK, boxborderw=16, borderw=2, enable=en_out)

    # ===== ナレーション字幕（画面下部・小さめ）=====
    for t1, t2, text in dialogues:
        if t2 <= t1 + 0.05:
            continue
        # 見出しだけのセグメント（【第N位】等）はカードと重複するので表示しない
        if re.fullmatch(r"【[^】]*】", text):
            continue
        # 折り返し保険（通常はASS側で30文字改行済み）
        if "\n" not in text and len(text) > 32:
            text = wrap_text(text, 30)
        fb.text(text, size=30, color=WHITE, x="(w-text_w)/2", y="h-text_h-14",
                box="0x000000@0.6", boxborderw=10, borderw=2,
                enable=f"between(t,{t1:.3f},{t2:.3f})")


def generate_thumbnail(meta: dict, ranking: list[dict], font: str,
                       bg_img: str | None, tmp_dir: str) -> None:
    """ランキング専用サムネイル: TOP10 + 最高額フック + タイトル。"""
    thumb_path = f"{OUTPUT_DIR}/thumbnail_0.jpg"
    fb = FilterBuilder(font, tmp_dir)

    fb.text(f"セレクトセール{meta['year']} {meta['session']}セール", size=48,
            color=WHITE, x="(w-text_w)/2", y="42", box=PANEL_DARK, boxborderw=20)
    fb.text("高額落札ランキング", size=74, color=WHITE, x="(w-text_w)/2", y="160",
            box="0x14325A@0.92", boxborderw=22, borderw=3)
    fb.text("TOP10", size=140, color=GOLD, x="(w-text_w)/2", y="300",
            box="0xAA0000@0.92", boxborderw=28, borderw=4)
    hook = f"最高額 {format_price(ranking[0]['price_man'])}!"
    fb.text(hook, size=72, color="0xFFFF00", x="(w-text_w)/2", y="540",
            box=PANEL_DARK, boxborderw=24, borderw=4, border_color="0xFF6600")

    if bg_img and Path(bg_img).exists():
        inputs = ["-loop", "1", "-i", bg_img]
        pre = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
               f"crop={W}:{H},eq=brightness=-0.35:contrast=1.05,")
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c=#101C30:s={W}x{H}:r=1"]
        pre = ""
    fc = "[0:v]" + pre + ",".join(fb.filters) + "[vout]"
    r = subprocess.run(
        ["ffmpeg", "-y"] + inputs + ["-filter_complex", fc, "-map", "[vout]",
         "-frames:v", "1", "-q:v", "2", thumb_path],
        capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  サムネイル: {thumb_path} ({Path(thumb_path).stat().st_size // 1024} KB)")
    else:
        print(f"  [警告] サムネイル生成失敗:\n{r.stderr[-300:]}", file=sys.stderr)


def main() -> None:
    news = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    item = news[0] if isinstance(news, list) else news
    meta = json.loads(Path(f"{OUTPUT_DIR}/ranking_meta_0.json").read_text(encoding="utf-8"))
    ranking = meta["ranking"]

    audio_path = f"{OUTPUT_DIR}/audio_0.mp3"
    ass_path = f"{OUTPUT_DIR}/subtitles_0.ass"
    out_path = f"{OUTPUT_DIR}/landscape_video_0.mp4"
    if not Path(audio_path).exists():
        raise FileNotFoundError(audio_path)

    total_dur = audio_duration(audio_path)
    dialogues = parse_ass_dialogues(ass_path) if Path(ass_path).exists() else []

    # select_sale_audio.py が出力する正確なカード時刻を最優先で使う
    # （文単位TTSのクリップ境界なのでナレーションと構造的にズレない）
    ct_path = Path(f"{OUTPUT_DIR}/card_times_0.json")
    if ct_path.exists():
        card = json.loads(ct_path.read_text(encoding="utf-8"))
        times = {
            "rank_start": {int(k): float(v) for k, v in card["rank_start"].items()},
            "outro_start": float(card["outro_start"]),
        }
        print("  カード時刻: card_times_0.json（実測クリップ境界）を使用")
    else:
        times = compute_card_times(dialogues, [r["rank"] for r in ranking], total_dur)
    print(f"  カード時刻: intro→{min(times['rank_start'].values()):.1f}s, "
          f"まとめ {times['outro_start']:.1f}s〜 (全体 {total_dur:.1f}s)")

    font = find_font()
    if not font:
        print("[エラー] CJKフォントが見つかりません。", file=sys.stderr)
        sys.exit(1)

    bg_imgs = fetch_images(4, horse_names=item.get("horses"))
    valid = [p for p in bg_imgs if p and Path(p).exists()]

    tmp_dir = tempfile.mkdtemp(prefix="ssrank_")
    try:
        # 背景: 写真をゆっくり切替、ぼかし+暗めでカードの可読性を確保
        img_proc = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},boxblur=6:1,eq=brightness=-0.28:contrast=1.05,setsar=1")
        N = min(len(valid), 4)
        if N >= 2:
            seg = total_dur / N
            bg_inputs = []
            for i, img in enumerate(valid[:N]):
                d = seg if i < N - 1 else (total_dur - (N - 1) * seg + 1.5)
                bg_inputs += ["-r", str(FPS), "-loop", "1", "-t", f"{d:.3f}", "-i", img]
            pre = [f"[{i}:v]{img_proc}[vi{i}]" for i in range(N)]
            pre.append("".join(f"[vi{i}]" for i in range(N)) + f"concat=n={N}:v=1:a=0[bg]")
            vstart, audio_idx = "[bg]", N
        elif N == 1:
            bg_inputs = ["-loop", "1", "-i", valid[0]]
            pre = [f"[0:v]{img_proc}[bg]"]
            vstart, audio_idx = "[bg]", 1
        else:
            bg_inputs = ["-f", "lavfi", "-i", f"color=c=#101C30:s={W}x{H}:r={FPS}"]
            pre = []
            vstart, audio_idx = "[0:v]", 1

        fb = FilterBuilder(font, tmp_dir)
        build_filters(fb, meta, ranking, times, total_dur, dialogues)
        print(f"  drawtextフィルター: {len(fb.filters)} 個")

        vid_chain = vstart + ",".join(fb.filters) + "[vout]"
        fc_parts = pre + [vid_chain]

        bgm_files = sorted(glob.glob(f"{BGM_DIR}/*.mp3") + glob.glob(f"{BGM_DIR}/*.m4a"))
        bgm = random.choice(bgm_files) if bgm_files else None
        if bgm:
            print(f"  BGM: {Path(bgm).name}")
            fc = (";".join(fc_parts) +
                  f";[{audio_idx}:a]apad=whole_dur={total_dur:.3f}[narr];"
                  f"[narr][{audio_idx+1}:a]amix=inputs=2:duration=first:weights=1 {BGM_VOL}[aout]")
            cmd = (["ffmpeg", "-y"] + bg_inputs +
                   ["-i", audio_path, "-stream_loop", "-1", "-i", bgm,
                    "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"])
        else:
            fc = (";".join(fc_parts) +
                  f";[{audio_idx}:a]apad=whole_dur={total_dur:.3f}[aout]")
            cmd = (["ffmpeg", "-y"] + bg_inputs + ["-i", audio_path,
                    "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"])

        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "192k", "-t", str(total_dur + 0.5), out_path]

        print(f"  動画生成中... (音声長 {total_dur:.1f}s)")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            print(f"[エラー] ffmpeg失敗:\n{r.stderr[-1000:]}", file=sys.stderr)
            sys.exit(1)
        print(f"✅ {out_path} ({Path(out_path).stat().st_size / 1048576:.1f} MB)")

        generate_thumbnail(meta, ranking, font, valid[0] if valid else None, tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
