"""URL一覧を取得して生HTMLを gzip 保存する（調査用）。
使い方: python scripts/win5_bt/fetch_raw.py <urls.txt> <outdir>
urls.txt の各行: <保存名> <URL>
"""
import gzip
import re
import sys
import time
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for line in src.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, url = line.split(maxsplit=1)
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*", "X-Requested-With": "XMLHttpRequest"}, timeout=30)
            m = re.search(rb'charset=["\']?([\w-]+)', r.content[:3000])
            enc = m.group(1).decode() if m else "utf-8"
            html = r.content.decode(enc, errors="replace")
            print(f"{name}: {r.status_code} {len(html)} chars")
        except Exception as e:  # noqa: BLE001
            html = f"ERROR {e}"
            print(f"{name}: ERROR {e}")
        with gzip.open(out / f"{name}.html.gz", "wt", encoding="utf-8") as f:
            f.write(html)
        time.sleep(1.5)


if __name__ == "__main__":
    main()
