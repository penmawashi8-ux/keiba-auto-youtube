#!/usr/bin/env python3
"""夢の対決シミュレーションのサムネイル（横 1280×720）を、実際の馬の写真で作る。

写真は Wikimedia Commons の自由ライセンス（CC・パブリックドメイン）のものだけを使う。
JRA や報道の写真は著作権があるため使わない。出典（撮影者・ライセンス）は
sim/thumb_credits.txt に書き出すので、動画の概要欄に必ず載せること（CC BY / BY-SA の条件）。

1. 各馬について Commons のカテゴリと検索から候補を集め、ライセンスを確認して取得する
2. 候補を一覧にした画像（sim/thumb_candidates_N.jpg）を作る … どの写真を使うかを目で選ぶため
3. data/sim_thumb_choice.json の指定（ファイル名・切り抜き位置 x, y = 0〜1・拡大率 zoom）でサムネイルを合成する
   （指定が無い馬は、カテゴリ内でいちばん大きい写真を中央で切り抜き）

使い方: python scripts/sim_thumbnail.py
出力: sim/thumbnail.jpg, sim/thumb_candidates_*.jpg, sim/thumb_credits.txt, sim/thumb_candidates.json
Pillow / numpy / drawbox は使わない（プロジェクトルール）。合成は ffmpeg のみ。
"""

import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sim_render as sr
from sim_dream_race import find_font

SIM_DIR = Path("sim")
WORK = SIM_DIR / "thumb_work"
CHOICE = Path("data/sim_thumb_choice.json")
API = "https://commons.wikimedia.org/w/api.php"
UA = "keiba-auto-youtube-thumbnail/1.0 (https://github.com/penmawashi8-ux/keiba-auto-youtube; python-urllib)"
N_CAND = 8
MAX_CHECK = 30             # 1頭あたりライセンスを確認する画像の数（記事・カテゴリの画像を先に見る）

# (表示名, Commons のカテゴリ候補, 検索語)
# (表示名, Commons のカテゴリ候補, 検索語, Wikipedia の記事名（ja / en）)
HORSES = [
    ("ディープインパクト", ["Deep Impact (horse)"],
     ["Deep Impact horse", "ディープインパクト", "Deep Impact Shadai stallion", "Deep Impact 2006", "Deep Impact Takeyutaka"],
     ["ディープインパクト (競走馬)", "Deep Impact (horse)"]),
    ("オルフェーヴル", ["Orfevre (horse)", "Orfevre"], ["Orfevre horse", "オルフェーヴル"],
     ["オルフェーヴル", "Orfevre (horse)"]),
    ("キタサンブラック", ["Kitasan Black"], ["Kitasan Black horse", "キタサンブラック"],
     ["キタサンブラック", "Kitasan Black"]),
    ("アーモンドアイ", ["Almond Eye (horse)", "Almond Eye"], ["Almond Eye horse", "アーモンドアイ"],
     ["アーモンドアイ", "Almond Eye (horse)"]),
    ("イクイノックス", ["Equinox (horse)"], ["Equinox racehorse", "イクイノックス"],
     ["イクイノックス (競走馬)", "Equinox (horse)"]),
]
FREE = ("cc", "public domain", "pd")
EXCLUDE = ("cosplay", "コスプレ", "ウマ娘", "uma musume", "uma_musume", "anime", "figure", "card", "illustration",
           "statue", "像", "grave", "墓", "logo", "poster", "map")


def api(params: dict, base: str = API) -> dict:
    url = base + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    for attempt in range(5):
        time.sleep(1.5)                # Wikimedia のアクセス制限（429）を避ける
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:     # noqa: BLE001
            print(f"  再試行: {e}")
            time.sleep(10 * (attempt + 1))
    print(f"  [警告] 取得できませんでした: {url[:120]}")
    return {}


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def file_infos(titles: list[str]) -> list[dict]:
    out = []
    # まとめて問い合わせると、詳しい情報（extmetadata）は一部の画像の分しか返らないので1件ずつ聞く
    for k in range(len(titles)):
        data = api({"action": "query", "titles": titles[k], "prop": "imageinfo",
                    "iiprop": "url|size|extmetadata|mediatype", "iiurlwidth": "1280"})
        for p in data.get("query", {}).get("pages", []):
            ii = (p.get("imageinfo") or [None])[0]
            # URL の末尾には ?utm_source=... が付くことがあるので、拡張子はファイル名で判定する
            if not ii or not re.search(r"\.(jpe?g|png)$", p.get("title", ""), re.I):
                print(f"    除外（写真ではない/情報なし）: {p.get('title')}")
                continue
            m = ii.get("extmetadata", {})
            lic = m.get("LicenseShortName", {}).get("value", "")
            # 改変禁止（ND）・非営利限定（NC）は、文字入れ・収益化があるサムネイルには使えない
            tokens = re.split(r"[\s\-]+", lic.lower())
            if not lic.lower().startswith(FREE) or "nd" in tokens or "nc" in tokens:
                print(f"    除外（ライセンス {lic or '不明'}）: {p['title']}")
                continue
            if any(x in p["title"].lower() for x in EXCLUDE) or ii.get("width", 0) < 600:
                print(f"    除外（対象外の内容か小さすぎる {ii.get('width')}px）: {p['title']}")
                continue
            out.append({"title": p["title"], "url": ii.get("thumburl") or ii["url"],
                        "w": ii["width"], "h": ii["height"], "license": lic,
                        "license_url": m.get("LicenseUrl", {}).get("value", ""),
                        "author": strip_html(m.get("Artist", {}).get("value", "")) or "不明",
                        "page": "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(p["title"].replace(" ", "_"))})
    return out


def candidates(cats: list[str], queries: list[str], articles: list[str], keys: list[str],
               first: str | None = None) -> list[dict]:
    """その馬の写真の候補。記事には産駒など別の馬の写真も載っているので、
    ファイル名に馬名（英語か日本語）が入っているものだけを残す。first は指定済みの写真"""
    titles = [first] if first else []
    # Wikipedia の記事に使われている写真（その馬だと確認されている。自由ライセンスなら Commons にある）
    for lang, art in zip(("ja", "en"), articles):
        data = api({"action": "query", "titles": art, "prop": "images", "imlimit": "50", "redirects": "1"},
                   base=f"https://{lang}.wikipedia.org/w/api.php")
        for p in data.get("query", {}).get("pages", []):
            titles += [("File:" + i["title"].split(":", 1)[1]) for i in p.get("images", [])
                       if re.search(r"\.(jpe?g|png)$", i["title"], re.I)]
    for c in cats:
        data = api({"action": "query", "list": "categorymembers", "cmtitle": f"Category:{c}", "cmtype": "file",
                    "cmlimit": "50"})
        titles += [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
    in_cat = set(titles)
    print(f"  記事・カテゴリの画像 {len(in_cat)} 件")
    for q in queries:
        data = api({"action": "query", "list": "search", "srsearch": q, "srnamespace": "6", "srlimit": "20"})
        titles += [m["title"] for m in data.get("query", {}).get("search", [])]
    print(f"  候補の合計 {len(titles)} 件")
    seen = []
    for t in titles:
        low = t.lower().replace("_", " ")
        if t not in seen and (t == first or not keys or any(k.lower() in low for k in keys)):
            seen.append(t)
    print(f"  馬名入りの写真 {len(seen)} 件")
    infos = file_infos(seen[:MAX_CHECK])
    # カテゴリに入っている写真（＝その馬だと確認されたもの）を優先し、次に大きい写真
    infos.sort(key=lambda x: (x["title"] != first, x["title"] not in in_cat, -x["w"] * x["h"]))
    return infos[:N_CAND]


def download(url: str, dest: Path) -> bool:
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                dest.write_bytes(r.read())
            return True
        except Exception as e:     # noqa: BLE001
            print(f"  取得失敗（再試行）: {e}")
            time.sleep(5 * (attempt + 1))
    return False


def contact_sheet(name: str, files: list[Path], out: Path, font: str) -> None:
    """候補を 4列×2段 に並べ、番号を付けた一覧画像"""
    inputs, chains = [], []
    for j in range(N_CAND):
        if j < len(files):
            inputs += ["-i", str(files[j])]
            chains.append(f"[{len(inputs) // 2 - 1}:v]scale=320:240:force_original_aspect_ratio=decrease,"
                          f"pad=320:240:(ow-iw)/2:(oh-ih)/2:color=0x202020,"
                          f"drawtext=fontfile='{font}':text='{j}':fontsize=40:fontcolor=yellow:x=8:y=4:"
                          f"borderw=3:bordercolor=black[c{j}]")
        else:
            chains.append(f"color=c=0x202020:s=320x240:d=1[c{j}]")
    chains.append("".join(f"[c{j}]" for j in range(N_CAND)) + "xstack=inputs=8:layout="
                  "0_0|320_0|640_0|960_0|0_240|320_240|640_240|960_240[v]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chains),
                    "-map", "[v]", "-frames:v", "1", "-q:v", "3", str(out)], check=True)


DREAM_TEXTS = {"title1": "伝説の名馬5頭が全盛期で戦ったら", "title2": "最強はどの馬？",
               "sub1": "データで1万回シミュレーション", "sub2": "東京芝2400m・JRA全成績から計算"}


def compose(chosen: list[tuple[str, Path, float, float, float]], font: str, out: Path,
            texts_: dict | None = None, colors: list[int] | None = None) -> None:
    """上に見出し、中段に馬の写真（枠色の名札付き）、下にシミュレーションの説明。写真の数は自由"""
    t = texts_ or DREAM_TEXTS
    colors = colors or list(range(len(chosen)))
    if len(chosen) == 1:
        compose_single(chosen[0], font, out, t, colors[0])
        return
    sw, ph, top = 1280 // len(chosen), 400, 168
    inputs, chains = [], []
    for i, (_name, path, fx, fy, zoom) in enumerate(chosen):
        inputs += ["-i", str(path)]
        # zoom > 1 で写真を拡大してから切り抜く（遠くから撮ったレース写真の馬を大きく見せる）
        chains.append(f"[{i}:v]scale={(sw - 4) * zoom:.0f}:{ph * zoom:.0f}:force_original_aspect_ratio=increase,"
                      f"crop={sw - 4}:{ph}:'(iw-{sw - 4})*{fx:.2f}':'(ih-{ph})*{fy:.2f}',"
                      f"eq=contrast=1.08:saturation=1.15,pad={sw}:{ph}:2:0:color=0x0B1020[p{i}]")
    chains.append("".join(f"[p{i}]" for i in range(len(chosen))) + f"hstack=inputs={len(chosen)}[row]")
    chains.append(f"color=c=0x0B1020:s=1280x720:d=1[bg];[bg][row]overlay=0:{top}[b]")
    texts = []

    def text(t, size, color, x, y, extra=""):
        p = WORK / f"txt_{len(texts)}.txt"
        p.write_text(t, encoding="utf-8")
        texts.append(f"drawtext=textfile='{p}':fontfile='{font}':fontsize={size}:fontcolor={color}:"
                     f"x={x}:y={y}{extra}")

    text(t["title1"], 46, "white", "(w-text_w)/2", "14", ":borderw=4:bordercolor=black")
    text(t["title2"], 74, "0xFFEB3B", "(w-text_w)/2", "70", ":borderw=6:bordercolor=0xB71C1C")
    for i, (name, *_rest) in enumerate(chosen):
        cx = i * sw + sw // 2
        c = colors[i]
        text(name, 25 if sw < 300 else 30, sr.NUM_COLORS[c], f"{cx}-text_w/2", str(top + ph - 48),
             f":box=1:boxcolor={sr.COLORS[c]}@0.95:boxborderw=8")
    text(t["sub1"], 44, "white", "(w-text_w)/2", str(top + ph + 22), ":borderw=4:bordercolor=black")
    text(t["sub2"], 30, "0xFFF59D", "(w-text_w)/2", str(top + ph + 86), ":borderw=3:bordercolor=black")
    chains.append("[b]" + ",".join(texts) + "[v]")
    script = WORK / "thumb_filter.txt"
    script.write_text(";".join(chains), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex_script", str(script),
                    "-map", "[v]", "-frames:v", "1", "-q:v", "2", str(out)], check=True)


def compose_single(ch: tuple, font: str, out: Path, t: dict, color: int) -> None:
    """写真が1頭分だけのとき: 写真を全面に敷き、左側を暗くして見出しを左寄せで載せる"""
    name, path, fx, fy, zoom = ch
    w, h = 1280, 720
    pw, px = 820, w - 820                      # 写真は右側 820px。左は見出しの帯
    chains = [f"[0:v]scale={pw * zoom:.0f}:{h * zoom:.0f}:force_original_aspect_ratio=increase,"
              f"crop={pw}:{h}:'(iw-{pw})*{fx:.2f}':'(ih-{h})*{fy:.2f}',eq=contrast=1.08:saturation=1.15[ph]",
              f"color=c=0x0B1020:s={w}x{h}:d=1[bg]", f"[bg][ph]overlay={px}:0[b]"]
    texts = []

    def text(tx, size, col, x, y, extra=""):
        p = WORK / f"txt1_{len(texts)}.txt"
        p.write_text(tx, encoding="utf-8")
        texts.append(f"drawtext=textfile='{p}':fontfile='{font}':fontsize={size}:fontcolor={col}:"
                     f"x={x}:y={y}{extra}")

    lines = t["title1"].split("を", 1)
    text(lines[0], 96, "0xFFEB3B", "48", "60", ":borderw=6:bordercolor=0xB71C1C")
    text(("を" + lines[1]) if len(lines) > 1 else "", 33, "white", "52", "190", ":borderw=4:bordercolor=black")
    text(t["title2"], 54, "white", "48", "300", ":box=1:boxcolor=0xC62828@0.92:boxborderw=16")
    text(t["sub1"], 34, "0xFFF59D", "52", "450", ":borderw=3:bordercolor=black")
    text(t["sub2"], 28, "0xE0E0E0", "52", "505", ":borderw=3:bordercolor=black")
    text(name, 36, sr.NUM_COLORS[color], "w-text_w-40", "h-text_h-40",
         f":box=1:boxcolor={sr.COLORS[color]}@0.95:boxborderw=10")
    chains.append("[b]" + ",".join(texts) + "[v]")
    script = WORK / "thumb_filter1.txt"
    script.write_text(";".join(chains), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-filter_complex_script", str(script),
                    "-map", "[v]", "-frames:v", "1", "-q:v", "2", str(out)], check=True)


def main_set(path: Path) -> None:
    """data/thumb_sets/*.json の馬で作る（実際のレース用）。写真が見つかった馬を上から max_photos 頭まで使う。
    出力: sim/<prefix>.jpg, sim/<prefix>_candidates_N.jpg, sim/<prefix>_credits.txt, sim/<prefix>_candidates.json"""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    prefix = cfg["prefix"]
    WORK.mkdir(parents=True, exist_ok=True)
    font = find_font()
    report, chosen, colors, credits = {}, [], [], []
    for n, h in enumerate(cfg["horses"]):
        if len(chosen) >= cfg.get("max_photos", 4) and not cfg.get("only_chosen"):
            break
        name = h["name"]
        print(f"=== {name}", flush=True)
        ch = cfg.get("choice", {}).get(name, {})
        # loose: ファイル名に馬名が無くても検索に出た写真を候補にする（候補一覧を目で確かめてから選ぶこと）
        keys = [] if cfg.get("loose") else [name] + h.get("en", [])
        queries = h.get("queries", [name, name + " 競走馬"] + [e + " horse" for e in h.get("en", [])])
        cands = candidates(h.get("cats", []), queries, h.get("articles", [name, name]), keys, ch.get("file"))
        files = []
        for j, c in enumerate(cands):
            p = WORK / f"{prefix}_h{n}_{j}.jpg"
            if download(c["url"], p):
                files.append(p)
                print(f"  {j}: {c['title']} / {c['license']} / {c['author']} ({c['w']}x{c['h']})")
            time.sleep(1)
        report[name] = cands[:len(files)]
        if not files:
            print(f"  自由ライセンスの写真なし → この馬は使わない")
            continue
        contact_sheet(name, files, SIM_DIR / f"{prefix}_candidates_{n}.jpg", font)
        # only_chosen: 候補一覧を見て写真を決めた馬（choice に file がある馬）だけを使う
        if ch.get("skip") or (cfg.get("only_chosen") and not ch.get("file")):
            continue
        if len(chosen) >= cfg.get("max_photos", 4):
            continue
        chosen.append((f"{h['num']} {name}" if h.get("num") else name, files[0],
                       ch.get("x", 0.5), ch.get("y", 0.5), ch.get("zoom", 1.0)))
        colors.append(h.get("waku", len(chosen)) - 1)
        c = cands[0]
        credits.append(f"{name}: \"{c['title'].removeprefix('File:')}\" by {c['author']}, "
                       f"{c['license']} {c['license_url']} （{c['page']}）")
    (SIM_DIR / f"{prefix}_candidates.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
    (SIM_DIR / f"{prefix}_credits.txt").write_text(
        "写真出典（Wikimedia Commons）※サムネイルで加工（切り抜き・色調整・文字入れ）\n" + "\n".join(credits) + "\n",
        encoding="utf-8")
    if chosen:
        compose(chosen, font, SIM_DIR / f"{prefix}.jpg", cfg["texts"], colors)
        print(f"保存: sim/{prefix}.jpg（{len(chosen)}頭）")
    else:
        print("[警告] 写真が1枚も見つからないので、サムネイルは作りませんでした")


def main() -> None:
    if len(sys.argv) > 1:
        main_set(Path(sys.argv[1]))
        return
    WORK.mkdir(parents=True, exist_ok=True)
    font = find_font()
    choice = json.loads(CHOICE.read_text(encoding="utf-8")) if CHOICE.exists() else {}
    report, chosen, credits = {}, [], []
    for n, (name, cats, queries, articles) in enumerate(HORSES):
        print(f"=== {name}", flush=True)
        ch = choice.get(name, {})
        keys = [name, articles[1].split(" (")[0]]
        cands = candidates(cats, queries, articles, keys, ch.get("file"))
        files = []
        for j, c in enumerate(cands):
            p = WORK / f"h{n}_{j}.jpg"
            if download(c["url"], p):
                files.append(p)
                print(f"  {j}: {c['title']} / {c['license']} / {c['author']} ({c['w']}x{c['h']})")
            time.sleep(1)
        report[name] = cands[:len(files)]
        if not files:
            print(f"[警告] {name} の自由ライセンス写真が見つかりません")
            continue
        contact_sheet(name, files, SIM_DIR / f"thumb_candidates_{n}.jpg", font)
        j = 0                     # 指定した写真（data/sim_thumb_choice.json の file）が先頭に来る
        chosen.append((name, files[j], ch.get("x", 0.5), ch.get("y", 0.5), ch.get("zoom", 1.0)))
        c = cands[j]
        credits.append(f"{name}: \"{c['title'].removeprefix('File:')}\" by {c['author']}, "
                       f"{c['license']} {c['license_url']} （{c['page']}）")
    (SIM_DIR / "thumb_candidates.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (SIM_DIR / "thumb_credits.txt").write_text(
        "写真出典（Wikimedia Commons）※サムネイルで加工（切り抜き・色調整・文字入れ）\n" + "\n".join(credits) + "\n",
        encoding="utf-8")
    if len(chosen) == len(HORSES):
        compose(chosen, font, SIM_DIR / "thumbnail.jpg", DREAM_TEXTS)
        print("保存: sim/thumbnail.jpg")
    else:
        print("[警告] 写真がそろわない馬があるため、サムネイルは作りませんでした")


if __name__ == "__main__":
    main()
