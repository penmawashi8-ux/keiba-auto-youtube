#!/usr/bin/env python3
"""posted_ids.txt など「投稿済みID一覧」ファイルの安全な読み書き。

過去に「末尾改行なしで write_text → 追記モードで書き込み」が連鎖して、
1行に大量のURLが連結される破損が発生した（重複チェックが1行=1IDで
照合するため、連結された塊の中のURLは発見できず再投稿されていた）。

このモジュールは:
  - 読み込み時に連結（glue）をほどいて1件ずつに戻す
  - 書き込みは必ず「1行1件＋末尾改行」にする
  - 追記は末尾改行を保証してから行う（連結の再発を防ぐ）
"""
import re
from pathlib import Path


def _split_glued(text: str) -> list[str]:
    """改行に加え、連結された "https://" 境界でも分割して1件ずつに戻す。"""
    # 行頭以外の "https://" の直前に改行を挿入（URL同士の連結をほどく）
    text = re.sub(r"(?<!^)(?<!\n)(https://)", r"\n\1", text)
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def load_ids(path: str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    return set(_split_glued(p.read_text(encoding="utf-8")))


def load_ids_ordered(path: str) -> list[str]:
    """出現順を保ったまま重複を除いたIDリストを返す。"""
    p = Path(path)
    if not p.exists():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for x in _split_glued(p.read_text(encoding="utf-8")):
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def write_ids(path: str, ids) -> None:
    """全IDを「1行1件＋末尾改行」で保存する。"""
    ids = list(ids)
    Path(path).write_text("\n".join(ids) + "\n" if ids else "", encoding="utf-8")


def append_id(path: str, entry_id: str) -> bool:
    """既存になければ1行追記する。末尾改行を保証して連結を防ぐ。追記したらTrue。"""
    if entry_id in load_ids(path):
        return False
    p = Path(path)
    prefix = ""
    if p.exists():
        raw = p.read_text(encoding="utf-8")
        if raw and not raw.endswith("\n"):
            prefix = "\n"  # 末尾改行のない破損ファイルへ連結されるのを防ぐ
    with p.open("a", encoding="utf-8") as f:
        f.write(prefix + entry_id + "\n")
    return True


def repair_file(path: str) -> int:
    """連結で破損したファイルを「1行1件」に修復する。修復後の件数を返す。"""
    ordered = load_ids_ordered(path)
    write_ids(path, ordered)
    return len(ordered)


if __name__ == "__main__":
    import sys
    for target in sys.argv[1:]:
        n = repair_file(target)
        print(f"repaired {target}: {n} ids")
