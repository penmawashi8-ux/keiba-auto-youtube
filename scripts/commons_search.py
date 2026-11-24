"""Wikimedia Commons で写真を探し、自由ライセンス（CC0 / CC BY / CC BY-SA / PD）のものだけを候補として保存する。

使い方: python scripts/commons_search.py data/photo_search/<名前>.json <出力ディレクトリ>
  {"items": [{"key": "calandagan", "queries": ["Calandagan", ...]}, ...]}
出力: <出力>/<key>_<n>.jpg（幅1600pxまで）, <出力>/candidates.json（作者・ライセンス・元のページ）, <出力>/sheet_<key>.jpg
NC（非営利）・ND（改変禁止）は使えないので除外する。
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "KeibaPhotoSearch/1.0 (free-license photo lookup; python-requests)"}
OK = re.compile(r"^(CC0|Public domain|PD|CC BY(-SA)? [0-9.]+|CC BY(-SA)?)", re.I)


def call(params):
    """API を呼ぶ（混んでいて JSON 以外が返ったら待って再試行）"""
    for k in range(5):
        r = requests.post(API, data={**params, "format": "json", "maxlag": 5}, headers=UA, timeout=60)
        try:
            return r.json()
        except ValueError:
            print("  再試行", r.status_code, r.text[:120].replace("\n", " "))
            time.sleep(5 * (k + 1))
    return {}


def search(q, n=12):
    d = call({"action": "query", "list": "search", "srsearch": q, "srnamespace": 6, "srlimit": n})
    return [x["title"] for x in d.get("query", {}).get("search", [])]


def info(titles):
    d = call({"action": "query", "titles": "|".join(titles), "prop": "imageinfo",
              "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 1600})
    out = []
    for p in d.get("query", {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata", {})
        lic = md.get("LicenseShortName", {}).get("value", "")
        artist = re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value", "")).strip()
        out.append({"title": p["title"], "license": lic, "author": artist, "mime": ii.get("mime"),
                    "w": ii.get("width"), "h": ii.get("height"), "thumb": ii.get("thumburl"),
                    "page": ii.get("descriptionurl"),
                    "desc": (re.sub(r"<[^>]+>", "", md.get("ImageDescription", {}).get("value", ""))[:600] + " | "
                             + md.get("Categories", {}).get("value", "")[:600])})
    return out


def category_files(cat, n=50):
    d = call({"action": "query", "list": "categorymembers", "cmtitle": cat, "cmtype": "file", "cmlimit": n})
    return [x["title"] for x in d.get("query", {}).get("categorymembers", [])]


def category_search(q):
    d = call({"action": "query", "list": "search", "srsearch": q, "srnamespace": 14, "srlimit": 10})
    return [x["title"] for x in d.get("query", {}).get("search", [])]


def wiki_page_images(lang, title):
    """各言語版ウィキペディアの記事に使われている画像（Commons のもの）"""
    try:
        r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", params={
            "action": "query", "format": "json", "titles": title, "prop": "images|pageprops", "imlimit": 50,
            "redirects": 1}, headers=UA, timeout=30)
        pages = r.json().get("query", {}).get("pages", {})
    except Exception as e:      # noqa: BLE001
        print("  wiki 失敗", lang, title, e)
        return [], None
    files, qid = [], None
    for pg in pages.values():
        qid = qid or pg.get("pageprops", {}).get("wikibase_item")
        files += [x["title"].replace("ファイル:", "File:").replace("Archivo:", "File:").replace("Fichier:", "File:")
                  .replace("Datei:", "File:") for x in pg.get("images", [])]
    return files, qid


def wikidata_image(qid):
    try:
        r = requests.get("https://www.wikidata.org/w/api.php", params={"action": "wbgetclaims", "format": "json",
                                                                       "entity": qid, "property": "P18"}, headers=UA, timeout=30)
        return ["File:" + c["mainsnak"]["datavalue"]["value"] for c in r.json().get("claims", {}).get("P18", [])]
    except Exception:      # noqa: BLE001
        return []


def openverse(q, n=20):
    """Openverse（Flickr などの CC 画像をまとめて検索）。商用利用・改変ができるものだけ"""
    try:
        r = requests.get("https://api.openverse.org/v1/images/", params={
            "q": q, "license_type": "commercial,modification", "page_size": n}, headers=UA, timeout=60)
        res = r.json().get("results", [])
    except Exception as e:      # noqa: BLE001
        print("  openverse 失敗", q, e)
        return []
    out = []
    for x in res:
        lic = (x.get("license") or "").lower()
        if lic not in ("by", "by-sa", "cc0", "pdm"):
            continue
        name = {"by": "CC BY", "by-sa": "CC BY-SA", "cc0": "CC0", "pdm": "Public domain"}[lic]
        out.append({"title": x.get("title") or "", "license": f"{name} {x.get('license_version') or ''}".strip(),
                    "author": x.get("creator") or "", "mime": "image/jpeg", "w": x.get("width"), "h": x.get("height"),
                    "thumb": x.get("url"), "page": x.get("foreign_landing_url"), "desc": x.get("source") or "",
                    "via": "openverse"})
    return out


def main():
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    for it in cfg["items"]:
        titles = []
        for q in it["queries"]:
            for t in search(q):
                if t not in titles:
                    titles.append(t)
            time.sleep(0.5)
        for cq in it.get("categories", []):              # 名前の付いたカテゴリ（Category:Ombudsman (horse) など）
            cats = [cq] if cq.startswith("Category:") else category_search(cq)
            for c in cats[:5]:
                for t in category_files(c):
                    if t not in titles:
                        titles.append(t)
            time.sleep(0.5)
        for lang, title in it.get("wiki", []):            # 各言語版の記事と、Wikidata の画像
            files, qid = wiki_page_images(lang, title)
            if qid:
                files += wikidata_image(qid)
            for t in files:
                if t not in titles and not t.lower().endswith((".svg", ".png")) or (t.lower().endswith(".png") and t not in titles):
                    titles.append(t)
            time.sleep(0.5)
        cands = []
        for i in range(0, len(titles), 10):
            cands += info(titles[i:i + 10])
            time.sleep(1)
        for q in it.get("openverse", []):
            cands += openverse(q)
            time.sleep(1)
        good = [c for c in cands if c["mime"] in ("image/jpeg", "image/png") and OK.match(c["license"] or "")
                and "NC" not in c["license"] and "ND" not in c["license"]]
        need = [w.lower() for w in it.get("must", [])]          # 題名・説明にこの語がある画像だけ（同名の別物を除く）
        if need:
            good = [c for c in good if any(w in (c["title"] + " " + c["desc"] + " " + (c.get("page") or "")).lower()
                                           for w in need)]
        report[it["key"]] = []
        files = []
        print(f"  {it['key']}: 調べた画像 {len(titles)}件")
        for n, c in enumerate(good[:16]):
            p = out / f"{it['key']}_{n}.jpg"
            try:
                b = requests.get(c["thumb"], headers=UA, timeout=60).content
                p.write_bytes(b)
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-q:v", "3", str(p) + ".jpg"], check=True)
                Path(str(p) + ".jpg").replace(p)
                c["file"] = p.name
                report[it["key"]].append(c)
                files.append(p)
            except Exception as e:      # noqa: BLE001
                print("取得失敗", c["title"], e)
            time.sleep(0.5)
        print(it["key"], "候補", len(cands), "自由ライセンス", len(good), "保存", len(files))
        if files:
            args = ["ffmpeg", "-v", "error", "-y"]
            for f in files:
                args += ["-i", str(f)]
            k = len(files)
            fc = "".join(f"[{i}]scale=400:300:force_original_aspect_ratio=decrease,pad=400:300:(ow-iw)/2:(oh-ih)/2,"
                         f"drawtext=text='{i}':fontcolor=yellow:fontsize=40:x=8:y=8[s{i}];" for i in range(k))
            cols = min(k, 5)
            lay = "|".join(f"{(i % cols) * 400}_{(i // cols) * 300}" for i in range(k))
            fc += "".join(f"[s{i}]" for i in range(k)) + (f"xstack=inputs={k}:layout={lay}:fill=black" if k > 1 else "null")
            subprocess.run(args + ["-filter_complex", fc, str(out / f"sheet_{it['key']}.jpg")], check=True)
    (out / "candidates.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
