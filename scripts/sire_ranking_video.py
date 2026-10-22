#!/usr/bin/env python3
"""種牡馬の産駒 獲得賞金ランキングの横動画（1920×1080）を作る。

構成: タイトル → 種牡馬の紹介 → 10位から1位まで（実際の馬の写真＋賞金のカウントアップ＋成績）
      → 全10頭の賞金グラフ → 写真の出典・締め
写真は Wikimedia Commons の自由ライセンス（CC BY / BY-SA / パブリックドメイン）のものだけ。
各馬の場面に撮影者・ライセンスを表示し、最後の場面と sim/<prefix>_credits.txt にもまとめる。

入力: data/sire_video/<名前>.json（台本・表示する成績・使う写真。下の CONFIG の説明を参照）
出力: sim/<prefix>.mp4, sim/<prefix>_thumb.jpg, sim/<prefix>_credits.txt, sim/<prefix>_frame_*.jpg

使い方: python scripts/sire_ranking_video.py data/sire_video/kurofune.json
Pillow / numpy / drawbox は使わない（プロジェクトルール）。描画はすべて ffmpeg。
"""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sim_bgm as sb  # noqa: E402
import sim_thumbnail as st  # noqa: E402
from sim_dream_race import find_font  # noqa: E402

W, H, FPS = 1920, 1080, 30
VOICE = "ja-JP-KeitaNeural"          # ランキング系（ニュースと同じ男性の声）
SIM_DIR = Path("sim")
BG = "0x14161c"
GOLD, SILVER, BRONZE = "0xF5C542", "0xD9DEE5", "0xD08A4E"
# CONFIG（data/sire_video/*.json）
#   prefix, sire, title: [行...], subtitle, source_note,
#   intro: {lines: [...], narration}, photos_intro: [ファイル名...],
#   horses: [{rank, name, prize_man, info: [表示する行...], narration,
#             photo: {file, x, y} | null}],   ← x, y は写真の注目点（0〜1）
#   chart_narration, outro: {lines: [...], narration}


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise SystemExit(f"ffmpeg 失敗: {' '.join(cmd[:8])}")


def duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "default=noprint_wrappers=1:nokey=1", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def prize_text(man: float) -> str:
    """53902.3 → 「5億3,902万円」（1万円未満は切り捨て）"""
    m = int(man)
    oku, rest = divmod(m, 10000)
    return f"{oku}億{rest:,}万円" if oku else f"{rest:,}万円"


class Text:
    """drawtext の部品。日本語は textfile= で渡す（エスケープ問題の回避）"""

    def __init__(self, work: Path, font: str):
        self.work, self.font, self.n = work, font, 0

    def __call__(self, text: str, x, y, size: int, color: str = "white", enable: str | None = None,
                 fade_at: float | None = None, border: int = 0, box: str | None = None, raw: bool = False) -> str:
        if raw:      # 数字・記号だけの式（カウントアップ）
            src = f"text='{text}'"
        else:
            self.n += 1
            p = self.work / f"t{self.n}.txt"
            p.write_text(text, encoding="utf-8")
            src = f"textfile='{p}'"
        s = f"drawtext=fontfile='{self.font}':{src}:x={x}:y={y}:fontsize={size}:fontcolor={color}"
        if border:
            s += f":borderw={border}:bordercolor=black@0.7"
        if box:
            s += f":box=1:boxcolor={box}:boxborderw=18"
        if fade_at is not None:
            s += f":alpha='clip((t-{fade_at})/0.4,0,1)'"
        if enable:
            s += f":enable='{enable}'"
        return s


def render(inputs: list[str], chains: list[str], last: str, dur: float, out: Path, work: Path) -> None:
    script = work / (out.stem + "_filter.txt")
    script.write_text(";\n".join(chains), encoding="utf-8")
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex_script", str(script), "-map", f"[{last}]",
         "-t", f"{dur:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", str(out)])


def photo_chains(i_bg: int, i_still: int, x: float, y: float, dur: float, pw: int, ph: int, tag: str) -> list[str]:
    """ぼかした背景 [bg{tag}]（入力 i_bg）と、ゆっくり寄っていく写真 [ph{tag}]（入力 i_still、pw×ph）。
    寄りは zoompan で作る。小さい画像のまま切り抜くと位置が1画素単位で飛んでプルプル揺れるので、
    いったん4倍に拡大してから切り抜き、縮小する（1/4画素単位で動くので滑らかになる）"""
    n = int(round(dur * FPS))
    zw, zh = pw * 4, ph * 4
    return [
        f"[{i_bg}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,"
        f"eq=brightness=-0.28:saturation=0.75[bg{tag}]",
        f"[{i_still}:v]scale={zw}:{zh}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={zw}:{zh}:x='(iw-{zw})*{x}':y='(ih-{zh})*{y}',"
        f"zoompan=z='1+0.08*on/{n}':x='(iw-iw/zoom)*{x}':y='(ih-ih/zoom)*{y}':d={n}:s={pw}x{ph}:fps={FPS},"
        f"setsar=1,pad={pw + 12}:{ph + 12}:6:6:color=white,format=rgba,"
        f"fade=t=in:st=0:d=0.5:alpha=1[ph{tag}]",
    ]


def fit(text: str, max_w: int, size: int) -> int:
    """1行が max_w ピクセルに収まる文字の大きさ（全角1文字 ≒ size、半角 ≒ size×0.55 とみなす）"""
    w = sum(1.0 if ord(c) > 0x2E7F else 0.55 for c in text)
    return min(size, int(max_w / max(w, 1)))


def rank_color(rank: int) -> str:
    return {1: GOLD, 2: SILVER, 3: BRONZE}.get(rank, "0x7FC8F8")


def horse_segment(cfg: dict, h: dict, photo: Path | None, credit: dict | None, dur: float, out: Path,
                  T: Text, work: Path) -> None:
    pw, ph, px, py = 1120, 740, 740, 150
    inputs, chains = [], []
    if photo:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(photo), "-i", str(photo)]
        chains += photo_chains(0, 1, h["photo"].get("x", 0.5), h["photo"].get("y", 0.5), dur, pw, ph, "0")
        chains.append(f"[bg0][ph0]overlay={px - 6}:{py - 6}:eof_action=repeat[v0]")
    else:
        inputs += ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0=0x1d2433:c1=0x3b2f1a:x0=0:y0=0:x1={W}:y1={H}:d={dur:.2f}:r={FPS}"]
        chains.append("[0:v]format=yuv420p[v0]")
    inputs += ["-f", "lavfi", "-i", f"color=c=black@0.55:s=690x{H}:d={dur:.2f}:r={FPS}"]
    chains.append(f"[{2 if photo else 1}:v]format=rgba[pan];[v0][pan]overlay=0:0[v1]")
    rk = h["rank"]
    col = rank_color(rk)
    name = h["name"]
    nsize = fit(name, 620, 88)
    final = prize_text(h["prize_man"])
    total = int(h["prize_man"])
    count_end = 1.8
    d = [
        T(f"{cfg['sire']}産駒 獲得賞金ランキング", 50, 40, 32, "0xBDBDBD"),
        T(f"第{rk}位", 50, 100, 120 if rk <= 3 else 104, col, border=4),
        T(name, 50, 270, nsize, "white", border=3),
        T("獲得賞金", 54, 400, 34, "0xBDBDBD", fade_at=0.3),
        # カウントアップ（万円）→ 終わったら「〇億〇,〇〇〇万円」に切り替え
        T(f"%{{eif\\:clip(t/{count_end}\\,0\\,1)*{total}\\:d}}", 50, 450, 78, col, enable=f"lt(t,{count_end})",
          raw=True, border=3),
        T(final, 50, 450, 78, col, enable=f"gte(t,{count_end})", border=3),
    ]
    for j, line in enumerate(h["info"]):
        d.append(T(line, 54, 600 + j * 62, fit(line, 610, 36), "white", fade_at=count_end + 0.2 + 0.25 * j))
    if photo and credit:
        d.append(T(f"Photo: {credit['author'][:60]} / {credit['license']}（Wikimedia Commons）",
                   px, py + ph + 22, 22, "0xCFCFCF"))
    if not photo:
        d.append(T(name, 760, 430, 120, "white@0.13"))
        d.append(T("※自由に使える写真が見つからなかったため、写真なしで紹介", 760, 980, 26, "0xBDBDBD"))
    d.append(T(cfg["source_note"], 50, 1020, fit(cfg["source_note"], 620, 22), "0x9E9E9E"))
    chains.append("[v1]" + ",".join(d) + ",fade=t=in:st=0:d=0.35[out]")
    render(inputs, chains, "out", dur, out, work)


def title_segment(cfg: dict, photos: list[Path], dur: float, out: Path, T: Text, work: Path, thumb: bool = False) -> None:
    inputs, chains = [], []
    n = len(photos)
    # サムネイルは写真を明るく鮮やかに（動画のタイトル画面は文字を読ませるため暗め）
    look = "eq=brightness=0.04:contrast=1.06:saturation=1.15" if thumb else "eq=brightness=-0.32:saturation=0.85"
    # 写真が4枚なら 2×2、それ以外は横に並べる
    tw, th = (W // 2, H // 2) if n == 4 else (W // max(n, 1), H)
    for i, p in enumerate(photos):
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(p)]
        chains.append(f"[{i}:v]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},"
                      f"{look},setsar=1[p{i}]")
    if n == 4:
        chains.append(f"[p0][p1][p2][p3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0,setsar=1[v0]")
    elif n > 1:
        chains.append("".join(f"[p{i}]" for i in range(n)) + f"hstack=inputs={n},setsar=1[v0]")
    elif n == 1:
        chains.append("[p0]null[v0]")
    else:
        inputs += ["-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={dur:.2f}:r={FPS}"]
        chains.append("[0:v]null[v0]")
    k = n if n else 1
    inputs += ["-f", "lavfi", "-i", f"color=c=black@{0.5 if thumb else 0.55}:s={W}x520:d={dur:.2f}:r={FPS}"]
    # サムネイルは帯を半透明にして、後ろの馬が透けて見えるように
    alpha = ",colorchannelmixer=aa=0.5" if thumb else ""
    chains.append(f"[{k}:v]format=rgba{alpha}[band];[v0][band]overlay=0:280[v1]")
    lines = cfg["thumb_title"] if thumb else cfg["title"]
    d = []
    sizes = [96, 132, 52]
    colors = ["white", GOLD, "white"]
    y = 310
    for j, line in enumerate(lines):
        s = fit(line, W - 120, sizes[min(j, 2)])
        d.append(T(line, "(w-text_w)/2", y, s, colors[min(j, 2)], border=5, fade_at=None if thumb else 0.3 * j))
        y += s + 40
    if not thumb:
        d.append(T(cfg["source_note"], "(w-text_w)/2", 1010, 26, "0xBDBDBD"))
    chains.append("[v1]" + ",".join(d) + "[out]")
    render(inputs, chains, "out", dur, out, work)


def text_segment(lines: list[str], dur: float, out: Path, T: Text, work: Path, heading: str,
                 photo: Path | None = None, small: bool = False) -> None:
    inputs, chains = [], []
    if photo:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(photo)]
        chains.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=18:2,"
                      f"eq=brightness=-0.35:saturation=0.7[v0]")
    else:
        inputs += ["-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={dur:.2f}:r={FPS}"]
        chains.append("[0:v]null[v0]")
    d = [T(heading, 120, 110, 72, GOLD, border=4)]
    size, step = (24, 40) if small else (44, 82)
    for j, line in enumerate(lines):
        d.append(T(line, 130, 260 + j * step, size, "white", border=2,
                   fade_at=None if small else 0.4 + 0.5 * j))
    chains.append("[v0]" + ",".join(d) + ",fade=t=in:st=0:d=0.4[out]")
    render(inputs, chains, "out", dur, out, work)


def chart_segment(cfg: dict, dur: float, out: Path, T: Text, work: Path) -> None:
    horses = sorted(cfg["horses"], key=lambda h: h["rank"])
    x0, maxlen, top, step, bh = 560, 1000, 190, 84, 56
    vmax = max(h["prize_man"] for h in horses)
    inputs = ["-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={dur:.2f}:r={FPS}"]
    chains, cur = [], "0:v"
    n = len(horses)
    for j, h in enumerate(horses):
        L = max(int(maxlen * h["prize_man"] / vmax), 8)
        col = rank_color(h["rank"])
        inputs += ["-f", "lavfi", "-i", f"color=c={col}:s={L}x{bh}:d={dur:.2f}:r={FPS}"]
        start = 0.6 + 0.35 * (n - 1 - j)          # 10位から順に伸びる
        chains.append(f"[{cur}][{j + 1}:v]overlay=x='{x0}-{L}+{L}*clip((t-{start:.2f})/0.9,0,1)':"
                      f"y={top + j * step}[c{j}]")
        cur = f"c{j}"
    # 棒の伸び始めの部分（x0 より左）を背景色で隠し、そこに馬名を書く
    inputs += ["-f", "lavfi", "-i", f"color=c={BG}:s={x0}x{H}:d={dur:.2f}:r={FPS}"]
    chains.append(f"[{cur}][{n + 1}:v]overlay=0:0[cv]")
    d = [T(cfg.get("chart_title", "獲得賞金 トップ10"), 80, 70, 64, GOLD, border=3)]
    for j, h in enumerate(horses):
        y = top + j * step
        L = max(int(maxlen * h["prize_man"] / vmax), 8)
        start = 0.6 + 0.35 * (n - 1 - j)
        d.append(T(f"{h['rank']:>2}位", 80, y + 8, 40, rank_color(h["rank"])))
        d.append(T(h["name"], 190, y + 8, 40 if len(h["name"]) <= 8 else 34, "white"))
        d.append(T(prize_text(h["prize_man"]), x0 + L + 20, y + 10, 36, "white",
                   fade_at=start + 0.9))
    d.append(T(cfg["source_note"], 80, 1030, 22, "0x9E9E9E"))
    chains.append("[cv]" + ",".join(d) + ",fade=t=in:st=0:d=0.4[out]")
    render(inputs, chains, "out", dur, out, work)


def make_speaker(work: Path, readings: dict):
    import edge_tts
    work.mkdir(parents=True, exist_ok=True)

    def speak(text: str, key: str) -> tuple[Path, float]:
        for k, v in readings.items():        # 読み間違える言葉（産駒 → さんく など）はかなで渡す
            text = text.replace(k, v)
        out = work / f"voice_{key}.mp3"
        for attempt in range(4):
            try:
                asyncio.run(edge_tts.Communicate(text, VOICE, rate="+6%").save(str(out)))
                return out, duration(out)
            except Exception as e:     # noqa: BLE001
                print(f"  読み上げ再試行（{key}）: {e}")
                time.sleep(5 * (attempt + 1))
        raise SystemExit(f"読み上げに失敗: {key}")
    return speak


def fetch_photos(cfg: dict, work: Path) -> dict:
    """設定の写真を Commons から取る。{ファイル名: (パス, 出典情報)}"""
    want = [h["photo"]["file"] for h in cfg["horses"] if h.get("photo")] + cfg.get("photos_intro", [])
    got = {}
    for title in dict.fromkeys(want):
        t = title if title.startswith("File:") else "File:" + title
        info = st.file_infos([t])
        if not info:
            print(f"[警告] 写真の情報が取れない（ライセンス外か削除）: {t}")
            continue
        p = work / f"photo_{len(got)}.jpg"
        if st.download(info[0]["url"], p):
            got[title] = (p, info[0])
            print(f"  写真: {t} / {info[0]['license']} / {info[0]['author']}")
        time.sleep(1)
    return got


def designed_thumb(td: dict, photos: dict, out: Path, T: Text, work: Path) -> None:
    """左に主役の写真を大きく、右の濃紺パネルに文字、下に脇役の写真3枚を並べたサムネイル。
    td（cfg["thumb_design"]）: hero {file, x, y}, subs [{file, x, y, name}], tag, lines [3行], badge"""
    panel = "0x10131c"
    hx = 1040                                   # 写真とパネルの境目
    hero = td["hero"]
    inputs = ["-loop", "1", "-i", str(photos[hero["file"]][0]),
              "-f", "lavfi", "-i", f"color=c={panel}:s={W}x{H}:d=1:r={FPS}",
              # 写真の右端をパネルの色へなじませるグラデーション（透明 → 濃紺）
              "-f", "lavfi", "-i", f"color=c={panel}:s=360x{H}:d=1:r={FPS}",
              "-f", "lavfi", "-i", f"color=c={GOLD}:s=760x6:d=1:r={FPS}"]
    hw = hx + 120
    chains = [f"[0:v]scale={hw}:{H}:force_original_aspect_ratio=increase,"
              f"crop={hw}:{H}:x='(iw-{hw})*{hero.get('x', 0.5)}':y='(ih-{H})*{hero.get('y', 0.5)}',"
              f"eq=brightness=0.03:contrast=1.08:saturation=1.15,setsar=1[hero]",
              "[1:v][hero]overlay=0:0[a0]",
              f"[2:v]format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*pow(X/W,1.3)'[gr];"
              f"[a0][gr]overlay={hw - 360}:0[a1]",
              f"[a1][3:v]overlay={hx + 60}:575[a2]"]
    cur, k = "a2", 4
    sw, sh, gap = 250, 168, 20
    for j, sub in enumerate(td.get("subs", [])[:3]):
        if sub["file"] not in photos:
            continue
        inputs += ["-loop", "1", "-i", str(photos[sub["file"]][0])]
        x0 = hx + 60 + j * (sw + gap)
        chains.append(f"[{k}:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,"
                      f"crop={sw}:{sh}:x='(iw-{sw})*{sub.get('x', 0.5)}':y='(ih-{sh})*{sub.get('y', 0.5)}',"
                      f"eq=saturation=1.1,setsar=1,pad={sw + 8}:{sh + 8}:4:4:color=white[s{j}]")
        chains.append(f"[{cur}][s{j}]overlay={x0 - 4}:{830 - 4}[b{j}]")
        cur, k = f"b{j}", k + 1
    tx = hx + 60
    l1, l2, l3 = td["lines"]
    d = [T(td["tag"], tx + 14, 80, fit(td["tag"], 740, 40), panel, box=GOLD),
         T(l1, tx, 170, fit(l1, 780, 118), "white", border=0),
         T(l2, tx, 320, fit(l2, 780, 136), GOLD),
         T(l3, tx, 610, fit(l3, 780, 60), "white"),
         # 「%」は drawtext の展開記号なので全角の「％」にする
         T(td["badge"].replace("%", "％"), tx, 690, fit(td["badge"], 780, 110), GOLD)]
    for j, sub in enumerate(td.get("subs", [])[:3]):
        d.append(T(sub["name"], tx + j * (sw + gap), 1012, fit(sub["name"], sw, 30), "white"))
    if td.get("hero_name"):
        d.append(T(td["hero_name"], 40, 990, 60, "white", border=4))
    chains.append(f"[{cur}]" + ",".join(d) + "[out]")
    render(inputs, chains, "out", 1.0, out, work)


def make_thumb(cfg: dict, photos: dict, T: Text, work: Path) -> None:
    """サムネイル（専用の1枚を描いてからフレームを抜き出す。縦横比は動画のまま）"""
    thumb_v = work / "thumb.mp4"
    td = cfg.get("thumb_design")
    if td and td["hero"]["file"] in photos:
        designed_thumb(td, photos, thumb_v, T, work)
    else:
        tp = [photos[f][0] for f in cfg.get("photos_thumb", cfg.get("photos_intro", [])) if f in photos]
        title_segment(cfg, tp, 1.0, thumb_v, T, work, thumb=True)
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", str(thumb_v), "-vframes", "1", "-q:v", "2",
         str(SIM_DIR / f"{cfg['prefix']}_thumb.jpg")])
    print(f"保存: sim/{cfg['prefix']}_thumb.jpg")


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    prefix = cfg["prefix"]
    work = SIM_DIR / f"work_{prefix}"
    work.mkdir(parents=True, exist_ok=True)
    SIM_DIR.mkdir(exist_ok=True)
    T = Text(work, find_font())
    photos = fetch_photos(cfg, work)
    if "--thumb-only" in sys.argv:        # サムネイルだけ作り直す
        make_thumb(cfg, photos, T, work)
        return
    speak = make_speaker(work / "voice", cfg.get("readings", {}))

    # 場面ごとの読み上げ → 長さを決める
    scenes = []          # (種類, 引数, 読み上げ)
    scenes.append(("title", None, cfg["title_narration"]))
    scenes.append(("intro", None, cfg["intro"]["narration"]))
    for h in sorted(cfg["horses"], key=lambda h: -h["rank"]):
        scenes.append(("horse", h, h["narration"]))
    scenes.append(("chart", None, cfg["chart_narration"]))
    scenes.append(("outro", None, cfg["outro"]["narration"]))
    lead = 0.7
    plan = []
    for k, (kind, arg, text) in enumerate(scenes):
        v, vd = speak(text, f"{k:02d}")
        dur = max(vd + lead + 1.3, {"title": 5.0, "horse": 9.0, "chart": 9.0}.get(kind, 6.0))
        if kind == "chart":
            dur = max(dur, 0.6 + 0.35 * 9 + 2.5)
        plan.append((kind, arg, v, dur))
        print(f"場面 {k:02d} {kind:<5} {dur:5.1f}秒  {text[:40]}…")

    segs, credits, short_credits = [], [], []
    intro_photos = [photos[f][0] for f in cfg.get("photos_intro", []) if f in photos]
    for k, (kind, arg, _v, dur) in enumerate(plan):
        out = work / f"seg_{k:02d}.mp4"
        if kind == "title":
            title_segment(cfg, intro_photos, dur, out, T, work)
        elif kind == "intro":
            text_segment(cfg["intro"]["lines"], dur, out, T, work, cfg["intro"]["heading"],
                         intro_photos[0] if intro_photos else None)
        elif kind == "horse":
            ph = photos.get(arg["photo"]["file"]) if arg.get("photo") else None
            horse_segment(cfg, arg, ph[0] if ph else None, ph[1] if ph else None, dur, out, T, work)
            if ph:
                c = ph[1]
                short_credits.append(f"{arg['name']}：{c['author'][:50]}（{c['license']}）")
                credits.append(f"{arg['name']}: \"{c['title'].removeprefix('File:')}\" by {c['author']}, "
                               f"{c['license']} {c['license_url']} （{c['page']}）")
        elif kind == "chart":
            chart_segment(cfg, dur, out, T, work)
        elif kind == "outro":
            lines = cfg["outro"]["lines"] + [""] + [
                "写真の出典（Wikimedia Commons・自由ライセンス／詳しくは概要欄）"] + short_credits
            text_segment(lines, dur, out, T, work, cfg["outro"]["heading"], small=True)
        segs.append(out)
        print(f"  描画 {out.name}")

    # 映像をつなぐ
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segs), encoding="utf-8")
    silent = work / "video_silent.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)])
    starts, t = [], 0.0
    for _kind, _arg, _v, dur in plan:
        starts.append(t)
        t += dur
    total = t

    # BGM: 10位〜4位は落ち着いた曲、3位・2位で盛り上げ、1位の発表でファンファーレ、その後はまとめの曲
    i1 = next(k for k, (kind, arg, _v, _d) in enumerate(plan) if kind == "horse" and arg["rank"] == 1)
    i3 = next(k for k, (kind, arg, _v, _d) in enumerate(plan) if kind == "horse" and arg["rank"] == 3)
    fan = 3.5
    sections = [("calm", starts[i3]), ("race", starts[i1] - starts[i3]), ("fanfare", fan),
                ("result", total - starts[i1] - fan)]
    bgm = sb.make_bgm(sections, work / "bgm.m4a")

    # 読み上げを場面の頭（+lead 秒）に置き、BGM と混ぜる
    inputs = ["-i", str(silent), "-i", str(bgm)]
    chains, labels = [], []
    for k, (_kind, _arg, v, _dur) in enumerate(plan):
        inputs += ["-i", str(v)]
        ms = int((starts[k] + lead) * 1000)
        chains.append(f"[{k + 2}:a]adelay={ms}|{ms},apad=whole_dur={total:.2f}[n{k}]")
        labels.append(f"[n{k}]")
    chains.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0,volume=1.0[nar]")
    chains.append("[1:a]volume=0.30[bgm]")
    chains.append("[nar][bgm]amix=inputs=2:normalize=0,loudnorm=I=-15:TP=-1.5:LRA=11[aout]")
    script = work / "mix_filter.txt"
    script.write_text(";".join(chains), encoding="utf-8")
    final = SIM_DIR / f"{prefix}.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex_script", str(script), "-map", "0:v",
         "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", str(final)])
    print(f"保存: {final}（{total:.1f}秒）")

    make_thumb(cfg, photos, T, work)
    # 確認用のフレーム
    for k, (kind, arg, _v, dur) in enumerate(plan):
        tag = f"rank{arg['rank']:02d}" if kind == "horse" else kind
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{starts[k] + min(dur - 0.5, 4.0):.2f}", "-i", str(final),
             "-vframes", "1", "-q:v", "3", str(SIM_DIR / f"{prefix}_frame_{tag}.jpg")])
    (SIM_DIR / f"{prefix}_credits.txt").write_text(
        "写真出典（Wikimedia Commons）※動画内で切り抜き・ぼかし・文字入れなどの加工をしています\n"
        + "\n".join(credits) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
