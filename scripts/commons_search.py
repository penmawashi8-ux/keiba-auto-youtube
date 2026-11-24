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
                    "page": ii.get("descriptionurl"), "desc": re.sub(r"<[^>]+>", "", md.get("ImageDescription", {}).get("value", ""))[:200]})
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
        cands = []
        for i in range(0, len(titles), 10):
            cands += info(titles[i:i + 10])
            time.sleep(1)
        good = [c for c in cands if c["mime"] in ("image/jpeg", "image/png") and OK.match(c["license"] or "")
                and "NC" not in c["license"] and "ND" not in c["license"]]
        report[it["key"]] = []
        files = []
        for n, c in enumerate(good[:10]):
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
