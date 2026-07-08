#!/usr/bin/env python3
"""TTS誤読補正の共通ロジック。

data/readings.json（一般用語のカナ読み辞書）と data/jockeys.json（騎手名を
姓・名に分割したデータ）から置換パターンを構築し、TTSに渡す前のテキストに
適用する。generate_audio.py と quiz_step3_video.py の両方から使う。

騎手名は1人につき以下のバリエーションを自動展開する:

  1. フルネーム            鮫島克駿   → さめしまかつま
  2. 姓+名の1文字(省略表記) 鮫島駿     → さめしまかつま
  3. 姓のみ(2文字以上)      鮫島       → さめしま
  4. 姓のみ(1文字)          武(?=騎手) → たけ

適用は「一般用語+フルネーム(長い順) → 省略表記(長い順) → 姓のみ(長い順)」。
長い名前が先にカナ化されるので、例えば「横山武騎手」は省略表記の
「横山武」→よこやまたけし が先に効き、1文字姓の「武」ルールが誤爆しない。

曖昧なパターンは生成しない:
  - 省略表記が別人と衝突する場合（石神深一/石神深道 の「石神深」）
  - 同じ姓に複数の読みがある場合（菅原隆一すがはら/菅原明良すがわら）

誤爆防止のガード:
  - 省略表記と姓のみは前に漢字が続く場合は置換しない（長い別名の一部とみなす）
  - 後ろに漢字が続く場合も置換しないが、騎手・君・氏は人名の後続語として許可
  - 1文字姓（武・林・森・原・幸など）は一般語に埋もれやすいので、
    後ろに騎手・ジョッキー・さん・君が続く場合のみ置換する
"""

import json
import re
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_KANJI = "一-鿿々"
# 人名の直後に続いても人名の区切りとみなす語
_NAME_SUFFIX = "騎手|ジョッキー|君|氏|さん"

_rules_cache: tuple[list, list] | None = None


def _load_json(name: str, default):
    path = _DATA_DIR / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _build_rules() -> tuple[list, list]:
    """(単純置換リスト, 正規表現置換リスト) を構築する。どちらも適用順。"""
    readings: dict = _load_json("readings.json", {})
    jockeys: list = _load_json("jockeys.json", [])

    # --- 単純置換: 一般用語 + フルネーム（従来と同じ挙動） ---
    literal = {k: v for k, v in readings.items() if isinstance(v, str)}
    for j in jockeys:
        full = j["surname"] + j["given"]
        literal[full] = j["surname_kana"] + j["given_kana"]

    # --- 省略表記: 姓+名の1文字 → フルネームの読み ---
    abbrevs: dict[str, str] = {}
    ambiguous: set[str] = set()
    for j in jockeys:
        given = j["given"]
        if len(given) < 2:
            continue  # 名が1文字なら省略表記はフルネームと同じ
        full_kana = j["surname_kana"] + j["given_kana"]
        for ch in given:
            if not re.fullmatch(f"[{_KANJI}]", ch) or ch == "々":
                continue
            key = j["surname"] + ch
            if key in literal:
                continue  # 正式名や一般用語と同形なら単純置換に任せる
            if key in abbrevs and abbrevs[key] != full_kana:
                ambiguous.add(key)
                continue
            abbrevs[key] = full_kana
    for key in ambiguous:
        abbrevs.pop(key, None)

    # --- 姓のみ ---
    surname_kanas: dict[str, set[str]] = {}
    for j in jockeys:
        surname_kanas.setdefault(j["surname"], set()).add(j["surname_kana"])

    regex_rules: list[tuple[re.Pattern, str]] = []
    for key in sorted(abbrevs, key=len, reverse=True):
        pat = re.compile(
            rf"(?<![{_KANJI}]){re.escape(key)}(?={_NAME_SUFFIX}|$|[^{_KANJI}])")
        regex_rules.append((pat, abbrevs[key]))
    for surname in sorted(surname_kanas, key=len, reverse=True):
        kanas = surname_kanas[surname]
        if len(kanas) != 1:
            continue  # 同姓で読みが割れる場合は判別不能なので触らない
        kana = next(iter(kanas))
        if len(surname) >= 2:
            pat = re.compile(
                rf"(?<![{_KANJI}]){re.escape(surname)}(?={_NAME_SUFFIX}|$|[^{_KANJI}])")
        else:
            # 1文字姓は「武器」「森の中」「幸せ」等に誤爆するため後続語必須
            pat = re.compile(
                rf"(?<![{_KANJI}]){re.escape(surname)}(?={_NAME_SUFFIX})")
        regex_rules.append((pat, kana))

    literal_rules = [(k, literal[k]) for k in sorted(literal, key=len, reverse=True)]
    return literal_rules, regex_rules


def apply_readings(text: str) -> str:
    """辞書を使ってTTSの誤読を補正する。長いパターンを優先して適用する。"""
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = _build_rules()
    literal_rules, regex_rules = _rules_cache
    for kanji, kana in literal_rules:
        if kanji in text:
            text = text.replace(kanji, kana)
    for pat, kana in regex_rules:
        text = pat.sub(kana, text)
    return text
