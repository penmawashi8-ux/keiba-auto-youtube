"""海外から参戦する馬の紹介動画（横のまとめ版・縦のショート）を作る。

使い方: python scripts/foreign_intro.py data/foreign/jc2026.json <写真のディレクトリ> <背景CG.jpg> <出力ディレクトリ> <フォント>
  写真: <写真のディレクトリ>/<key>.jpg（無い馬は背景CGを暗くして使う）
  出力: long.mp4, short_1.mp4 …, long_thumb.jpg, short_N_thumb.jpg, voice/

文字だけの画面は使わない。写真（または CG の競馬場）の上に、中継風の小さな板で名前・主な勝ち鞍・騎手などを出し、
説明はナレーション（ニュースと同じ男性の声）で行う。
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "cg"))
import compose as cp          # noqa: E402  テロップの板（Card）
import sim_dream_race as sd   # noqa: E402  読み上げ
import sim_bgm as sb          # noqa: E402

FPS = 30
COUNTRY = {"フランス": "0x1F4E9C", "イギリス": "0xC8102E", "アイルランド": "0x169B62", "アメリカ": "0x3C3B6E",
           "ドイツ": "0x333333", "オーストラリア": "0x00247D"}
GOLD = "0xE8C547"


def run(args):
    subprocess.run(args, check=True)


def dur_of(path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


class Voice:
    def __init__(self, work: Path):
        self.tts = sd.make_tts(work)

    def __call__(self, text: str):
        clip = self.tts(text)
        if clip:
            return clip
        return None, max(2.5, len(text) / 7.0)       # 読み上げが使えないとき（試作用）は文字数から長さを見積もる


def background(src: Path, out: Path, w: int, h: int, sec: float, dark: float, pan: str):
    """写真をゆっくり動かす（ケン・バーンズ）。縦横比が合わないときはぼかした同じ写真で埋める"""
    big_w, big_h = int(w * 1.12) // 2 * 2, int(h * 1.12) // 2 * 2
    move = {"right": f"x='(iw-ow)*t/{sec:.2f}':y='(ih-oh)/2'", "left": f"x='(iw-ow)*(1-t/{sec:.2f})':y='(ih-oh)/2'",
            "up": f"x='(iw-ow)/2':y='(ih-oh)*(1-t/{sec:.2f})'"}[pan]
    fc = (f"[0]scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},boxblur=30:3,"
          f"eq=brightness=-0.12[bg];"
          f"[0]scale={big_w}:{big_h}:force_original_aspect_ratio=decrease[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,crop={w}:{h}:{move},eq=brightness={-dark:.2f}:saturation=1.1,"
          f"fps={FPS},format=yuv420p[v]")
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", f"{sec:.2f}", "-i", str(src), "-filter_complex", fc,
         "-map", "[v]", "-t", f"{sec:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])


def overlay_clip(bg: Path, cards: list[tuple[Path, float, float]], out: Path, sec: float):
    """背景の動画に、透明 PNG の板を時間を区切って重ねる（少しずつ現れるようにフェード）"""
    args = ["ffmpeg", "-v", "error", "-y", "-i", str(bg)]
    fc, cur = [], "[0]"
    for j, (png, a, b) in enumerate(cards, 1):
        args += ["-loop", "1", "-t", f"{sec:.2f}", "-i", str(png)]
        fc.append(f"[{j}]format=rgba,fade=t=in:st={a:.2f}:d=0.35:alpha=1,fade=t=out:st={max(b - 0.3, a):.2f}:d=0.3:alpha=1[c{j}]")
        fc.append(f"{cur}[c{j}]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})'[o{j}]")
        cur = f"[o{j}]"
    fc.append(f"{cur}format=yuv420p[v]")
    run(args + ["-filter_complex", ";".join(fc), "-map", "[v]", "-t", f"{sec:.2f}", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "18", str(out)])


def wrap_ja(text: str, width: int) -> list[str]:
    """日本語の折り返し。句読点の後ろ・「。」で優先して区切り、数字や英字の途中では切らない"""
    out, cur = [], ""
    i = 0
    while i < len(text):
        ch = text[i]
        cur += ch
        if ch in "。" :
            out.append(cur)
            cur = ""
        elif len(cur) >= width:
            cut = max(cur.rfind("、"), cur.rfind("）"), cur.rfind("。"))
            if cut >= width * 0.5:
                out.append(cur[:cut + 1])
                cur = cur[cut + 1:]
            else:
                j = len(cur)
                while j > width * 0.6 and (cur[j - 1].isascii() and cur[j - 1].isalnum()
                                           or cur[j - 1] in "倍%％万億"):
                    j -= 1                         # 「26倍」「ロンジン」の途中では切らない
                out.append(cur[:j])
                cur = cur[j:]
        i += 1
    if cur:
        out.append(cur)
    return [x for x in out if x]


def card(tmp: Path, name: str, font: str, draw) -> Path:
    c = cp.Card(tmp, font)
    draw(c)
    p = tmp / f"{name}.png"
    c.save(p)
    return p


def build(data: dict, photos: Path, cg_bg: Path, out: Path, font: str, vertical: bool, keys: list[str], tag: str,
          voice: Voice, part: str = ""):
    W, H = (1080, 1920) if vertical else (1920, 1080)
    cp.W, cp.H = W, H
    tmp = out / f"work_{tag}"
    tmp.mkdir(parents=True, exist_ok=True)
    race = data["race"]
    horses = {h["key"]: h for h in data["horses"]}
    segs, voices, t = [], [], 0.0
    s = 1.0 if not vertical else 1.0

    def add_seg(kind, src, text, cards_fn, pan="right", dark=0.05, extra=1.2, min_len=4.0):
        nonlocal t
        clip, d = voice(text) if text else (None, 0.0)
        sec = max(min_len, d + extra)
        bgv = tmp / f"bg{len(segs)}.mp4"
        background(src, bgv, W, H, sec, dark, pan)
        cards = cards_fn(sec)
        segv = tmp / f"seg{len(segs)}.mp4"
        overlay_clip(bgv, cards, segv, sec)
        if clip:
            voices.append((t + 0.4, (clip, d)))            # (開始時刻, (音声ファイル, 長さ))
        segs.append(segv)
        t += sec

    # ---- つかみ
    def intro_cards(sec):
        def d(c):
            if vertical:
                c.rect(60, 360, 960, 420, cp.PLATE)
                c.rect(60, 360, 960, 8, GOLD)
                c.text(f"{race['name']}{race['year']}", W / 2, 450, 76, cp.WHITE, anchor="c")
                c.text("外国馬紹介" + (f" {part}" if part else ""), W / 2, 560, 96, GOLD, anchor="c")
                c.text(f"{race['date']}　{race['course']}", W / 2, 670, 40, cp.SUB, anchor="c")
                c.text(f"※{race['asof']}", W / 2, 735, 30, cp.SUB, anchor="c")
            else:
                c.rect(80, 640, 6, 300, GOLD)
                c.text(f"{race['name']}{race['year']}", 112, 640, 64, cp.WHITE, shadow=True)
                c.text(f"海外から参戦する{len(data['horses'])}頭", 112, 730, 96, GOLD, shadow=True)
                c.text(f"{race['date']}　{race['course']}", 114, 860, 40, cp.WHITE, shadow=True)
                c.text(f"※{race['asof']}", 114, 915, 28, cp.SUB, shadow=True)
        return [(card(tmp, "intro", font, d), 0.3, sec)]

    if vertical:
        names = "、".join(horses[k]["name"] for k in keys)
        intro_text = f"{race['name']}に海外から参戦する馬を紹介します。今回は{names}です。"
    else:
        intro_text = data["intro"]
    def pick(f):
        if not f or f == "cg":
            return cg_bg
        if Path(f).exists():                   # リポジトリ内の画像（CG の馬など）
            return Path(f)
        return photos / f if (photos / f).exists() else cg_bg
    add_seg("intro", pick(data.get("intro_photo")), intro_text, intro_cards, pan="right", dark=0.1, extra=1.0)

    # ---- 1頭ずつ
    for idx, k in enumerate(keys, 1):
        h = horses[k]
        src = pick(h.get("photo", f"{k}.jpg"))
        if h.get("photo_crop") and src != cg_bg:   # 写真の一部（馬のいるところ）を切り出す
            x, y, cw, ch = h["photo_crop"]
            cropped = tmp / f"crop_{k}.jpg"
            run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                 f"crop=iw*{cw}:ih*{ch}:iw*{x}:ih*{y}", "-q:v", "2", str(cropped)])
            src = cropped
        own = h.get("photo_is_horse", False) and src != cg_bg
        kind = h.get("photo_kind", "own" if own else "place")
        text = h["short"] if vertical else h["narration"]
        n_all = len(data["horses"])
        pos = [x["key"] for x in data["horses"]].index(k) + 1

        def horse_cards(sec, h=h, pos=pos, n_all=n_all, idx=idx, own=own, src=src, kind=kind):
            col = COUNTRY.get(h["country"], "0x555555")

            def name_plate(c):
                if not own:                    # その馬の写真ではないことをはっきり書く
                    note = ("画像：イメージ（CG）" if kind == "cg" else
                            "画像：東京競馬場（CG）" if src == cg_bg else "写真：東京競馬場（イメージ）")
                    c.text(note, W - 24, (1100 if vertical else 50), 24, "0xFFFFFF@0.85", anchor="r", shadow=True)
                if vertical:
                    x0, y0, w = 50, 1160, 980
                    c.rect(x0, y0, w, 250, cp.PLATE)
                    c.rect(x0, y0, 12, 250, col)
                    c.text(f"{h['country']}調教", x0 + 36, y0 + 22, 34, cp.WHITE)
                    c.texts[-1] = (f"{h['country']}調教", x0 + 36, y0 + 22, 34, cp.WHITE, "l", False)
                    c.text(h["name"], x0 + 34, y0 + 72, 78 if len(h["name"]) <= 7 else 62, cp.WHITE)
                    c.text(h["en"], x0 + 38, y0 + 176, 32, cp.SUB)
                else:
                    x0, y0, w = 60, 760, 1000
                    c.rect(x0, y0, w, 250, cp.PLATE)
                    c.rect(x0, y0, 10, 250, col)
                    c.rect(x0 + 30, y0 + 22, cp.text_w(h["country"] + "調教", 30) + 30, 46, col)
                    c.text(f"{h['country']}調教", x0 + 45, y0 + 29, 30, cp.WHITE)
                    c.text(h["name"], x0 + 30, y0 + 80, 84 if len(h["name"]) <= 7 else 66, cp.WHITE)
                    c.text(f"{h['en']}　{h['profile']}", x0 + 32, y0 + 192, 28, cp.SUB)
                    c.text(f"外国馬紹介 {pos}/{n_all}", 60, 50, 30, cp.WHITE, shadow=True)

            def wins(c):
                name_plate(c)
                if vertical:
                    x0, y0, w = 50, 1440, 980
                    c.rect(x0, y0, w, 90 + 56 * len(h["wins"]), cp.PLATE)
                    c.rect(x0, y0, w, 6, GOLD)
                    c.text("主な成績", x0 + 30, y0 + 24, 34, GOLD)
                    for j, wv in enumerate(h["wins"]):
                        c.text(wv, x0 + 30, y0 + 82 + j * 56, 34 if len(wv) <= 24 else 30, cp.WHITE)
                else:
                    x0, y0, w = 1100, 120, 760
                    c.rect(x0, y0, w, 96 + 60 * len(h["wins"]), cp.PLATE)
                    c.rect(x0, y0, w, 6, GOLD)
                    c.text("主な成績", x0 + 30, y0 + 26, 34, GOLD)
                    for j, wv in enumerate(h["wins"]):
                        c.text(wv, x0 + 30, y0 + 86 + j * 60, 32 if len(wv) <= 24 else 28, cp.WHITE)

            def trivia(c):
                name_plate(c)
                lines = h.get("trivia_lines") or wrap_ja(h.get("trivia", ""), 17 if vertical else 22)
                if vertical:
                    x0, y0, w = 50, 1440, 980
                    c.rect(x0, y0, w, 100 + 62 * len(lines), cp.PLATE)
                    c.rect(x0, y0, w, 6, "0xD7141A")
                    c.text("ここに注目", x0 + 30, y0 + 24, 36, "0xFF6B6B")
                    for j, ln in enumerate(lines):
                        c.text(ln, x0 + 30, y0 + 84 + j * 62, 44, cp.WHITE)
                else:
                    x0, y0, w = 1100, 120, 760
                    c.rect(x0, y0, w, 100 + 62 * len(lines), cp.PLATE)
                    c.rect(x0, y0, w, 6, "0xD7141A")
                    c.text("ここに注目", x0 + 30, y0 + 26, 34, "0xFF6B6B")
                    for j, ln in enumerate(lines):
                        c.text(ln, x0 + 30, y0 + 86 + j * 62, 32, cp.WHITE)

            def full(c):
                wins(c)
                line = f"騎手 {h['jockey']}　調教師 {h['trainer']}"
                line2 = f"通算 {h['record']}　獲得賞金 {h['prize']}"
                if vertical:
                    y0 = 1440 + 90 + 56 * len(h["wins"]) + 20
                    c.rect(50, y0, 980, 120, cp.PLATE)
                    c.text(line, 80, y0 + 16, 34, cp.WHITE)
                    c.text(line2, 80, y0 + 66, 30, cp.SUB)
                else:
                    c.rect(1100, 900, 760, 120, cp.PLATE)
                    c.text(line, 1130, 914, 32, cp.WHITE)
                    c.text(line2, 1130, 962, 30, cp.SUB)
                    c.text(f"「{h['comment']}」", 1100 - 20, 1040 - 4, 24, cp.WHITE, anchor="r", shadow=True)
            a = card(tmp, f"{k}_a", font, name_plate)
            b = card(tmp, f"{k}_b", font, wins)
            tv = card(tmp, f"{k}_t", font, trivia)
            f = card(tmp, f"{k}_c", font, full)
            if vertical:                       # ショートは短いので: 名前 → 成績 → ここに注目
                t1, t2 = sec * 0.18, sec * 0.5
                return [(a, 0.3, t1 + 0.3), (b, t1, t2 + 0.3), (tv, t2, sec)]
            t1, t2, t3 = sec * 0.16, sec * 0.42, sec * 0.72
            return [(a, 0.3, t1 + 0.3), (b, t1, t2 + 0.3), (tv, t2, t3 + 0.3), (f, t3, sec)]
        add_seg(k, src, text, horse_cards, pan=("right" if idx % 2 else "left") if not vertical else "up",
                dark=0.0 if own or kind == "cg" else 0.2)

    # ---- 締め
    def outro_cards(sec):
        def d(c):
            if vertical:
                c.rect(60, 420, 960, 470, cp.PLATE)
                c.rect(60, 420, 960, 8, GOLD)
                c.text("全頭の詳しい紹介は", W / 2, 520, 56, cp.WHITE, anchor="c")
                c.text("本編で公開中", W / 2, 630, 90, GOLD, anchor="c")
                c.text("↓ 関連動画から", W / 2, 760, 40, cp.SUB, anchor="c")
            else:
                c.rect(W / 2 - 560, 180, 1120, 120 + 70 * len(data["horses"]), cp.PLATE)
                c.rect(W / 2 - 560, 180, 1120, 6, GOLD)
                c.text(f"{race['name']}{race['year']}　外国馬", W / 2, 236, 44, GOLD, anchor="c")
                for j, hh in enumerate(data["horses"]):
                    y = 310 + j * 70
                    c.rect(W / 2 - 500, y, 16, 46, COUNTRY.get(hh["country"], "0x555555"))
                    c.text(hh["name"], W / 2 - 460, y, 42, cp.WHITE)
                    c.text(hh["tag"], W / 2 + 480, y + 6, 32, cp.SUB, anchor="r")
                    c.texts[-1] = (hh["tag"], W / 2 + 480, y + 8, 32, cp.SUB, "r", False)
                c.text("あなたの注目は？コメントで教えてください", W / 2, 180 + 120 + 70 * len(data["horses"]) + 40, 40,
                       cp.WHITE, anchor="c", shadow=True)
        return [(card(tmp, "outro", font, d), 0.3, sec)]
    outro_text = ("全頭の詳しい紹介は本編でどうぞ。" if vertical else data["outro"])
    add_seg("outro", pick(data.get("outro_photo")), outro_text, outro_cards, pan="left", dark=0.1, extra=1.5)

    # ---- つなぐ・音
    lst = tmp / "segs.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segs), encoding="utf-8")   # 一覧の相対パスは一覧の場所から解釈される
    silent = tmp / "video.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)])
    total = dur_of(silent)
    bgm = sb.make_bgm([("calm", total)], tmp / "bgm.m4a")
    inputs = ["-i", str(silent), "-i", str(bgm)]
    fc = []
    duck = "1"
    for j, (t0, clip) in enumerate(voices):
        inputs += ["-i", clip[0]]
        ms = int(t0 * 1000)
        fc.append(f"[{j + 2}:a]aformat=sample_rates=44100:channel_layouts=stereo,adelay={ms}|{ms},volume=1.7[v{j}]")
        duck = f"if(between(t,{t0 - 0.2:.2f},{t0 + clip[1] + 0.3:.2f}),0.3,{duck})"
    fc.append(f"[1:a]volume=volume='{duck}':eval=frame,volume=0.7[bg]")
    fc.append("[bg]" + "".join(f"[v{j}]" for j in range(len(voices))) +
              f"amix=inputs={1 + len(voices)}:normalize=0:duration=first,alimiter=limit=0.95,loudnorm=I=-15:TP=-1.5[a]")
    final = out / f"{tag}.mp4"
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(final)])
    print(f"{final}: {total:.1f}秒")
    return final


def thumb(data, photos: Path, cg_bg: Path, out: Path, font_heavy: str):
    """まとめ版のサムネイル: 写真4枚を並べ、レース名と「外国馬6頭」を大きく"""
    race = data["race"]
    keys = [h["photo"] for h in data["horses"] if h.get("photo_is_horse") and (photos / h["photo"]).exists()][:4]
    tmp = out / "work_thumb"
    tmp.mkdir(parents=True, exist_ok=True)
    srcs = [photos / k for k in keys] or [cg_bg]
    args = ["ffmpeg", "-v", "error", "-y"]
    for p in srcs:
        args += ["-i", str(p)]
    n = len(srcs)
    w = 1280 // n
    fc = [f"[{i}]scale={w}:720:force_original_aspect_ratio=increase,crop={w}:720[p{i}]" for i in range(n)]
    fc.append("".join(f"[p{i}]" for i in range(n)) + (f"hstack=inputs={n}" if n > 1 else "null") + ",eq=saturation=1.2[b]")
    txt = {"t1": f"{race['short']}{race['year']}", "t2": f"外国馬{len(data['horses'])}頭", "t3": "世界のG1馬が集結"}
    for k, v in txt.items():
        (tmp / f"{k}.txt").write_text(v, encoding="utf-8")
    d = lambda k, size, color, x, y, extra="": (                   # noqa: E731
        f"drawtext=textfile='{tmp / (k + '.txt')}':fontfile='{font_heavy}':fontsize={size}:fontcolor={color}:"
        f"x={x}:y={y}:expansion=none{extra}")
    fc.append("[b]" + ",".join([
        "vignette=PI/4",
        d("t3", 44, "white", "(w-text_w)/2", 40, ":box=1:boxcolor=0xD7141A:boxborderw=14"),
        d("t1", 120, "white", "(w-text_w)/2", 360, ":borderw=16:bordercolor=white"),
        d("t1", 120, "0xFFE14D", "(w-text_w)/2", 360, ":borderw=8:bordercolor=0x111111"),
        d("t2", 150, "white", "(w-text_w)/2", 510, ":borderw=12:bordercolor=0xD7141A"),
    ]) + "[o]")
    p = out / "long_thumb.jpg"
    run(args + ["-filter_complex", ";".join(fc), "-map", "[o]", "-frames:v", "1", "-q:v", "2", str(p)])
    return p


def main():
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    photos, cg_bg, out, font = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]
    heavy = sys.argv[6] if len(sys.argv) > 6 else font
    only = sys.argv[7] if len(sys.argv) > 7 else "all"
    out.mkdir(parents=True, exist_ok=True)
    voice = Voice(out / "voice")
    if only in ("all", "long"):
        build(data, photos, cg_bg, out, font, False, [h["key"] for h in data["horses"]], "long", voice)
        thumb(data, photos, cg_bg, out, heavy)
    if only in ("all", "short"):
        for i, keys in enumerate(data["shorts"], 1):
            f = build(data, photos, cg_bg, out, font, True, keys, f"short_{i}", voice, part=f"{'①②③④⑤'[i - 1]}")
            run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", str(f), "-vframes", "1", "-q:v", "2",
                 str(out / f"short_{i}_thumb.jpg")])     # 縦長のまま（縮小しない）


if __name__ == "__main__":
    main()
