#!/usr/bin/env python3
"""Wikimedia 以外の写真サイト（Flickr 本体・Geograph・Unsplash・Pexels・Pixabay）で
自由ライセンスの馬の写真を探す。GitHub Actions で実行する（ローカルは通信制限あり）。

使い方: python scripts/photo_hunt.py data/photo_hunt/jc2026.json out/hunt
出力: out/hunt/report.txt（調べた全件と結果）, out/hunt/<key>/*.jpg（使えそうな写真）
"""
import html
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

# Flickr のライセンス番号。商用・改変OKのものだけ「使える」
FLICKR_LIC = {0: "All rights reserved", 1: "CC BY-NC-SA", 2: "CC BY-NC", 3: "CC BY-NC-ND",
              4: "CC BY", 5: "CC BY-SA", 6: "CC BY-ND", 7: "No known copyright",
              8: "US Government", 9: "CC0", 10: "Public Domain Mark"}
FLICKR_OK = {4, 5, 7, 8, 9, 10}

LOG = []


def log(s):
    print(s, flush=True)
    LOG.append(s)


def get(url, **kw):
    for i in range(2):
        try:
            r = S.get(url, timeout=15, **kw)
            if r.status_code == 429:
                time.sleep(10 * (i + 1))
                continue
            return r
        except Exception as e:  # noqa: BLE001
            log(f"   ! {url} {e}")
            time.sleep(3)
    return None


def relevant(text, must):
    t = text.lower()
    return any(m.lower() in t for m in must)


# ---------------- Flickr ----------------
def flickr_search(q, lic=None):
    url = "https://www.flickr.com/search/?text=" + urllib.parse.quote(q)
    if lic:
        url += "&license=" + lic
    r = get(url)
    if r is None:
        return None, []
    if not lic and not Path("out/debug/flickr_search.html").exists():
        Path("out/debug").mkdir(parents=True, exist_ok=True)
        Path("out/debug/flickr_search.html").write_text(r.text, encoding="utf-8")
    ids = []
    for owner, pid in re.findall(r'/photos/([^/"\\]+)/(\d{6,})', r.text):
        if owner not in ("tags", "search") and (owner, pid) not in ids:
            ids.append((owner, pid))
    # ページ内の JSON にも id が入っている
    for pid in re.findall(r'"id":"(\d{8,})"', r.text):
        if pid not in [p for _, p in ids]:
            ids.append(("", pid))
    return r.status_code, ids


DUMPED = []


def flickr_photo(owner, pid):
    url = (f"https://www.flickr.com/photos/{owner}/{pid}/" if owner
           else f"https://www.flickr.com/photo.gne?id={pid}")
    r = get(url)
    if r is not None and r.status_code != 200 and owner:
        r = get(f"https://www.flickr.com/photo.gne?id={pid}")
    if r is None or r.status_code != 200:
        log(f"    ! 写真ページ {pid} HTTP {getattr(r, 'status_code', None)}")
        return None
    t = r.text
    if len(DUMPED) < 2:  # 解析確認用に生HTMLを少し残す
        DUMPED.append(pid)
        Path("out/debug").mkdir(parents=True, exist_ok=True)
        Path(f"out/debug/flickr_{pid}.html").write_text(t, encoding="utf-8")
    m = re.search(r'<meta property="og:title" content="([^"]*)"', t)
    title = html.unescape(m.group(1)) if m else ""
    m = re.search(r'<meta property="og:description" content="([^"]*)"', t)
    desc = html.unescape(m.group(1)) if m else ""
    m = re.search(r'<meta property="og:image" content="([^"]*)"', t)
    img = m.group(1) if m else ""
    m = re.search(r'"license":(\d+)', t)
    lic = int(m.group(1)) if m else None
    if lic is None:
        m = re.search(r'creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)', t)
        lic_s = ("CC " + m.group(2).upper()) if m else ("All rights reserved"
                                                         if "All rights reserved" in t else "?")
    else:
        lic_s = FLICKR_LIC.get(lic, str(lic))
    m = re.search(r'"ownerNsid":"[^"]*","realname":"([^"]*)"', t) or \
        re.search(r'"realname":"([^"]+)"', t) or re.search(r'"username":"([^"]+)"', t)
    author = m.group(1) if m else owner
    tags = " ".join(re.findall(r'"raw":"([^"]+)"', t)[:60])
    m = re.search(r'<link rel="canonical" href="([^"]+)"', t)
    page = m.group(1) if m else url
    return dict(title=title, desc=desc, img=img, lic=lic, lic_s=lic_s, author=author,
                tags=tags, page=page)


def do_flickr(item, out):
    seen = {}
    ok_set = set()
    log(f"  [Flickr]")
    for q in item["flickr"]:
        st_all, all_ids = flickr_search(q)
        st_ok, ok_ids = flickr_search(q, "4,5,7,8,9,10")
        log(f"   「{q}」 全ライセンス {len(all_ids)}件 / 商用改変OK {len(ok_ids)}件 (HTTP {st_all}/{st_ok})")
        for o, p in all_ids[:25]:
            seen.setdefault(p, (o, q))
        for o, p in ok_ids[:40]:
            seen[p] = (o, q)
            ok_set.add(p)
        time.sleep(1)
    hits = 0
    fails = 0
    # 使えるライセンスの写真は全部、全ライセンスの写真は馬名だけの検索から少しだけ確認する
    check = [(p, oq) for p, oq in seen.items() if p in ok_set]
    check += [(p, oq) for p, oq in seen.items() if (p, oq) not in check][:15]
    for pid, (owner, q) in check:
        if fails >= 4:
            log("    ! Flickr が応答しなくなったため打ち切り")
            break
        info = flickr_photo(owner, pid)
        time.sleep(2)
        if not info:
            fails += 1
            continue
        fails = 0
        text = " ".join([info["title"], info["desc"], info["tags"]])
        rel = relevant(text, item["must"])
        ok = info["lic"] in FLICKR_OK if info["lic"] is not None else info["lic_s"] in (
            "CC BY", "CC BY-SA", "CC ZERO", "CC MARK")
        log(f"    {'★' if rel and ok else '・'} {info['lic_s']:<20} | "
            f"{info['title'][:70]} | {info['author']} | {info['page']}")
        if ok and info["img"]:
            sub = out / item["key"] / ("named" if rel else "event")
            sub.mkdir(parents=True, exist_ok=True)
            r = get(info["img"])
            if r is not None and r.status_code == 200:
                (sub / f"flickr_{pid}.jpg").write_bytes(r.content)
                (sub / f"flickr_{pid}.txt").write_text(json.dumps(info, ensure_ascii=False, indent=1))
                hits += rel
    log(f"   → Flickr 名前入り・使用可: {hits}件（調べた写真 {len(seen)}件）")


# ---------------- Geograph（英国・アイルランド、全て CC BY-SA 2.0） ----------------
def do_geograph(item, out):
    log("  [Geograph]")
    for q in item["words"]:
        r = get("https://api.geograph.org.uk/syndicator.php?format=JSON&perpage=50&q="
                + urllib.parse.quote(q))
        if r is None:
            continue
        try:
            items = r.json().get("items", [])
        except ValueError:
            items = []
        rel = [x for x in items if relevant(str(x.get("title", "")) + " " + str(x.get("description", "")),
                                            item["must"])]
        log(f"   Geograph API「{q}」 HTTP {r.status_code} 結果{len(items)}件 名前入り{len(rel)}件")
        for x in rel[:10]:
            log(f"    ★ CC BY-SA 2.0 | {x.get('title')} | {x.get('link')}")
    for host in ("www.geograph.org.uk", "www.geograph.ie"):
        for q in item["words"]:
            r = get(f"https://{host}/of/" + urllib.parse.quote(q.replace(" ", "+"), safe="+"))
            if r is None:
                continue
            found = re.findall(r'href="(?:https?://[^"/]+)?/photo/(\d+)"[^>]*title="([^"]*)"', r.text)
            found += re.findall(r'<a [^>]*href="(?:https?://[^"/]+)?/photo/(\d+)"[^>]*>([^<]{3,})</a>', r.text)
            rel = [(i, html.unescape(t)) for i, t in found if relevant(t, item["must"])]
            log(f"   {host}「{q}」 HTTP {r.status_code} 結果{len(found)}件 名前入り{len(rel)}件")
            for i, t in rel[:10]:
                log(f"    ★ CC BY-SA 2.0 | {t} | https://{host}/photo/{i}")
            time.sleep(1)


# ---------------- Unsplash / Pexels / Pixabay（各サイト独自の無料ライセンス） ----------------
def do_free_sites(item, out):
    log("  [Unsplash / Pexels / Pixabay]")
    for q in item["words"]:
        r = get("https://unsplash.com/napi/search/photos?per_page=30&query=" + urllib.parse.quote(q))
        if r is not None and r.status_code == 200:
            try:
                res = r.json().get("results", [])
            except ValueError:
                res = []
            rel = [x for x in res if relevant(" ".join(str(x.get(k) or "") for k in (
                "description", "alt_description", "slug")), item["must"])]
            log(f"   Unsplash「{q}」 結果{len(res)}件 名前入り{len(rel)}件")
            for x in rel:
                log(f"    ★ Unsplash License | {x.get('description') or x.get('alt_description')} | "
                    f"{x['links']['html']}")
        else:
            log(f"   Unsplash「{q}」 HTTP {getattr(r, 'status_code', None)}")
        slug = urllib.parse.quote(q.lower().replace(" ", "-"))
        for name, url in (("Pexels", f"https://www.pexels.com/search/{slug}/"),
                          ("Pixabay", f"https://pixabay.com/images/search/{slug}/")):
            r = get(url)
            if r is None:
                continue
            n = len(set(re.findall(r'/photo/[a-z0-9\-]*?(\d{5,})', r.text)))
            hit = relevant(re.sub(r"<[^>]+>", " ", r.text).replace(q, ""), item["must"][:1])
            log(f"   {name}「{q}」 HTTP {r.status_code} 写真{n}件 名前の記載{'あり' if hit else 'なし'}")
        time.sleep(1)


def main():
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for item in cfg["items"]:
        log(f"== {item['key']}")
        for fn in (do_flickr, do_geograph, do_free_sites):
            try:
                fn(item, out)
            except Exception as e:  # noqa: BLE001
                log(f"   ! {fn.__name__}: {e}")
    (out / "report.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
