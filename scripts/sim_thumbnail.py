#!/usr/bin/env python3
"""夢の対決シミュレーションのサムネイル（横 1280×720）を、実際の馬の写真で作る。

写真は Wikimedia Commons の自由ライセンス（CC・パブリックドメイン）のものだけを使う。
JRA や報道の写真は著作権があるため使わない。出典（撮影者・ライセンス）は
sim/thumb_credits.txt に書き出すので、動画の概要欄に必ず載せること（CC BY / BY-SA の条件）。

1. 各馬について Commons のカテゴリと検索から候補を集め、ライセンスを確認して取得する
2. 候補を一覧にした画像（sim/thumb_candidates_N.jpg）を作る … どの写真を使うかを目で選ぶため
3. data/sim_thumb_choice.json の指定（候補番号と切り抜き位置）でサムネイルを合成する
   （指定が無い馬は候補0番・中央で切り抜き）

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
    ("ディープインパクト", ["Deep Impact (horse)"], ["Deep Impact horse", "ディープインパクト"],
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
            if not ii or not re.search(r"\.(jpe?g|png)$", ii.get("url", ""), re.I):
                print(f"    除外（写真ではない/情報なし）: {p.get('title')} 応答: {json.dumps(p, ensure_ascii=False)[:300]}")
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


def candidates(cats: list[str], queries: list[str], articles: list[str]) -> list[dict]:
    titles = []
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
        if t not in seen:
            seen.append(t)
    infos = file_infos(seen[:MAX_CHECK])
    # カテゴリに入っている写真（＝その馬だと確認されたもの）を優先し、次に大きい写真
    infos.sort(key=lambda x: (x["title"] not in in_cat, -x["w"] * x["h"]))
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


def compose(chosen: list[tuple[str, Path, float, float]], font: str, out: Path) -> None:
    """上に見出し、中段に5頭の写真（枠色の名札付き）、下にシミュレーションの説明"""
    sw, ph, top = 256, 400, 168
    inputs, chains = [], []
    for i, (_name, path, fx, fy) in enumerate(chosen):
        inputs += ["-i", str(path)]
        chains.append(f"[{i}:v]scale={sw - 4}:{ph}:force_original_aspect_ratio=increase,"
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

    text("伝説の名馬5頭が全盛期で戦ったら", 46, "white", "(w-text_w)/2", "14", ":borderw=4:bordercolor=black")
    text("最強はどの馬？", 74, "0xFFEB3B", "(w-text_w)/2", "70", ":borderw=6:bordercolor=0xB71C1C")
    for i, (name, *_rest) in enumerate(chosen):
        cx = i * sw + sw // 2
        text(name, 25, sr.NUM_COLORS[i], f"{cx}-text_w/2", str(top + ph - 44),
             f":box=1:boxcolor={sr.COLORS[i]}@0.95:boxborderw=8")
    text("データで1万回シミュレーション", 44, "white", "(w-text_w)/2", str(top + ph + 22),
         ":borderw=4:bordercolor=black")
    text("東京芝2400m・JRA全成績から計算", 30, "0xFFF59D", "(w-text_w)/2", str(top + ph + 86),
         ":borderw=3:bordercolor=black")
    chains.append("[b]" + ",".join(texts) + "[v]")
    script = WORK / "thumb_filter.txt"
    script.write_text(";".join(chains), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex_script", str(script),
                    "-map", "[v]", "-frames:v", "1", "-q:v", "2", str(out)], check=True)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    font = find_font()
    choice = json.loads(CHOICE.read_text(encoding="utf-8")) if CHOICE.exists() else {}
    report, chosen, credits = {}, [], []
    for n, (name, cats, queries, articles) in enumerate(HORSES):
        print(f"=== {name}", flush=True)
        cands = candidates(cats, queries, articles)
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
        ch = choice.get(name, {})
        j = min(ch.get("index", 0), len(files) - 1)
        chosen.append((name, files[j], ch.get("x", 0.5), ch.get("y", 0.5)))
        c = cands[j]
        credits.append(f"{name}: \"{c['title'].removeprefix('File:')}\" by {c['author']}, "
                       f"{c['license']} {c['license_url']} （{c['page']}）")
    (SIM_DIR / "thumb_candidates.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (SIM_DIR / "thumb_credits.txt").write_text(
        "写真出典（Wikimedia Commons）※サムネイルで加工（切り抜き・色調整・文字入れ）\n" + "\n".join(credits) + "\n",
        encoding="utf-8")
    if len(chosen) == len(HORSES):
        compose(chosen, font, SIM_DIR / "thumbnail.jpg")
        print("保存: sim/thumbnail.jpg")
    else:
        print("[警告] 写真がそろわない馬があるため、サムネイルは作りませんでした")


if __name__ == "__main__":
    main()
