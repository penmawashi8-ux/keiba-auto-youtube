"""収集データ（5走表示＋結果）から 1行=1頭 の特徴量テーブルを作る。

方針:
- 人気・オッズは特徴量に一切使わない（結果の人気は比較用ベースラインとしてのみ保持）
- 近走は「当該レースの日付より前」のものだけ使う（リーク防止）
"""
import gzip
import json
import math
import re
from pathlib import Path

import pandas as pd

ZEN = str.maketrans("０１２３４５６７８９", "0123456789")
CLASS_LEVEL = {
    "新馬": 0, "未勝利": 0, "1勝": 1, "2勝": 2, "3勝": 3,
    "OP": 4, "L": 4.5, "GIII": 5, "JpnIII": 5, "GII": 6, "JpnII": 6, "GI": 7, "JpnI": 7,
}
PLACES_JRA = {"札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"}
SURF_RE = re.compile(r"^(芝|ダ|障)(\d{3,4})")


def class_of(tokens_before_surface: list[str]) -> float | None:
    for t in reversed(tokens_before_surface):
        if t in CLASS_LEVEL:
            return CLASS_LEVEL[t]
    joined = "".join(tokens_before_surface)
    for key in ("新馬", "未勝利", "1勝", "2勝", "3勝"):
        if key in joined:
            return CLASS_LEVEL[key]
    if re.search(r"ステークス|S$|賞|特別|杯|記念|C$|カップ", joined):
        return None
    return None


def to_sec(s: str) -> float | None:
    m = re.match(r"^(?:(\d+):)?(\d+)\.(\d)$", s)
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2)) + int(m.group(3)) / 10


def parse_past_cell(text: str) -> dict | None:
    toks = text.split()
    if len(toks) < 6 or not re.match(r"^\d{4}\.\d{2}\.\d{2}$", toks[0]):
        return None
    si = next((i for i, t in enumerate(toks) if SURF_RE.match(t)), None)
    if si is None or si < 3:
        return None
    sm = SURF_RE.match(toks[si])
    r = {
        "date": toks[0].replace(".", "-"),
        "place": toks[1],
        "finish_raw": toks[2],
        "cls": class_of(toks[3:si]),
        "surface": sm.group(1),
        "dist": int(sm.group(2)),
    }
    r["finish"] = int(toks[2]) if toks[2].isdigit() else None
    r["jra"] = toks[1] in PLACES_JRA
    rest = toks[si + 1:]
    if rest and re.match(r"^(\d:)?\d{2}\.\d$", rest[0]) and ":" in rest[0] or (
            rest and re.match(r"^\d{2}\.\d$", rest[0]) and r["dist"] <= 1000):
        r["time"] = to_sec(rest[0])
        rest = rest[1:]
    for t in rest:
        if t in ("良", "稍", "重", "不") and "baba" not in r:
            r["baba"] = t
        elif re.match(r"^\d+頭$", t):
            r["heads"] = int(t[:-1])
        elif re.match(r"^\d+番$", t):
            r["umaban"] = int(t[:-1])
        elif re.match(r"^\d{2}\.\d$", t) and "kinryo" not in r and "heads" in r:
            r["kinryo"] = float(t)
        elif re.match(r"^\d+(-\d+)+$", t) or (re.match(r"^\d+$", t) and "heads" in r and "kinryo" in r and "pos" not in r):
            r["pos"] = [int(x) for x in t.split("-")]
        elif re.match(r"^\(\d{2}\.\d\)$", t):
            r["last3f"] = float(t[1:-1])
        elif re.match(r"^\d{3}\([+-]?\d+\)$", t):
            r["bw"] = int(t[:3])
    m = re.search(r"\((-?\d+\.\d)\)\s*$", text)
    if m and r["finish"] is not None:
        r["margin"] = float(m.group(1))
    return r


def parse_rest(text: str) -> float | None:
    m = re.search(r"(?:(\d+)年)?(\d+)ヵ月(半)?休養", text)
    if not m:
        return None
    return int(m.group(1) or 0) * 12 + int(m.group(2)) + (0.5 if m.group(3) else 0)


def parse_race_meta(meta: dict) -> dict:
    d1 = meta.get("data1", "").translate(ZEN)
    d2 = meta.get("data2", "").translate(ZEN)
    m = re.search(r"(芝|ダ|障)(\d{3,4})m", d1)
    out = {"surface": m.group(1) if m else None, "dist": int(m.group(2)) if m else None}
    mb = re.search(r"(?:馬場|予報)\s*[:：]?\s*(良|稍|稍重|重|不良|不)", d1)
    out["baba"] = (mb.group(1)[0] if mb else None)
    if "新馬" in d2:
        cls = 0
    elif "未勝利" in d2:
        cls = 0
    elif "1勝クラス" in d2:
        cls = 1
    elif "2勝クラス" in d2:
        cls = 2
    elif "3勝クラス" in d2:
        cls = 3
    else:
        cls = 4
    out["cls"] = cls
    out["handicap"] = int("ハンデ" in d2)
    out["age2"] = int("2歳" in d2)
    mp = re.search(r"\d+回\s*(\S+?)\s*\d+日目", d2)
    out["place"] = mp.group(1) if mp else None
    mh = re.search(r"(\d+)頭", d2)
    out["heads"] = int(mh.group(1)) if mh else None
    return out


def parse_result(rows: list) -> dict:
    """馬番 -> {finish, pop, odds, name}"""
    out = {}
    for cells in rows[1:]:
        if len(cells) < 11 or not cells[2].isdigit():
            continue
        out[int(cells[2])] = {
            "finish": int(cells[0]) if cells[0].isdigit() else None,
            "name": cells[3],
            "pop": int(cells[9]) if cells[9].isdigit() else None,
            "odds": float(cells[10]) if re.match(r"^\d+(\.\d+)?$", cells[10]) else None,
        }
    return out


def horse_rows(rec: dict) -> list[dict]:
    race = parse_race_meta(rec["meta"])
    race_date = rec["date"]
    rdate = f"{race_date[:4]}-{race_date[4:6]}-{race_date[6:]}"
    res = parse_result(rec.get("result", []))
    rows = []
    for h in rec["horses"]:
        cells = h["cells"]
        texts = [t for _, t in cells]
        classes = [c for c, _ in cells]
        umaban = None
        waku_vals = [t for c, t in cells if c.startswith("Waku")]
        if len(waku_vals) >= 2 and waku_vals[1].isdigit():
            umaban = int(waku_vals[1])
        info = next((t for c, t in cells if "Horse_Info" in c), "")
        jockey = next((t for c, t in cells if c.startswith("Jockey")), "")
        mj = re.match(r"^(牡|牝|セ)(\d+)\S*\s+(\S+)\s+([\d.]+)", jockey)
        style = re.search(r"\s(逃|先|差|追)\s", info)
        interval = None
        mi = re.search(r"中(\d+)週", info)
        if mi:
            interval = int(mi.group(1))
        elif "連闘" in info:
            interval = 0
        past, rest_months = [], None
        for c, t in cells:
            if c.startswith("Past"):
                p = parse_past_cell(t)
                if p and p["date"] < rdate:
                    past.append(p)
            elif "休養" in t and rest_months is None and not c.startswith("Horse_Info"):
                rest_months = parse_rest(t)
        r = {
            "race_id": rec["race_id"], "date": race_date, "umaban": umaban,
            "sex": mj.group(1) if mj else None, "age": int(mj.group(2)) if mj else None,
            "jockey": mj.group(3) if mj else None, "kinryo": float(mj.group(4)) if mj else None,
            "style": style.group(1) if style else None, "interval_w": interval,
            "rest_months": rest_months,
            **{f"race_{k}": v for k, v in race.items()},
        }
        r.update(past_features(past, race))
        rr = res.get(umaban, {})
        r["finish"] = rr.get("finish")
        r["name"] = rr.get("name")
        r["pop"] = rr.get("pop")      # 比較用ベースラインのみ（特徴量には使わない）
        r["odds"] = rr.get("odds")    # 同上
        rows.append(r)
    return rows


def past_features(past: list[dict], race: dict) -> dict:
    f = {"n_past": len(past)}
    tcls = race["cls"]
    for i in range(3):
        p = past[i] if i < len(past) else {}
        f[f"p{i+1}_finish"] = p.get("finish")
        f[f"p{i+1}_margin"] = p.get("margin")
        f[f"p{i+1}_clsdiff"] = (p["cls"] - tcls) if p.get("cls") is not None else None
        f[f"p{i+1}_fin_ratio"] = (p["finish"] / p["heads"]) if p.get("finish") and p.get("heads") else None
        f[f"p{i+1}_last3f"] = p.get("last3f")
        f[f"p{i+1}_same_surf"] = int(p.get("surface") == race["surface"]) if p else None
        f[f"p{i+1}_distdiff"] = (p["dist"] - race["dist"]) if p and race["dist"] else None
        pos = p.get("pos")
        f[f"p{i+1}_pos1_ratio"] = (pos[0] / p["heads"]) if pos and p.get("heads") else None
        f[f"p{i+1}_poslast_ratio"] = (pos[-1] / p["heads"]) if pos and p.get("heads") else None
    margins = [p["margin"] for p in past if p.get("margin") is not None]
    f["mean_margin"] = sum(margins) / len(margins) if margins else None
    f["best_margin"] = min(margins) if margins else None
    # 補正着差: 格上で走った分を割り引く（クラス差1つ=0.4秒相当）
    adj = []
    for p in past:
        if p.get("margin") is None or p.get("cls") is None:
            continue
        adj.append(p["margin"] - 0.4 * (p["cls"] - tcls))
    f["adj_margin_mean"] = sum(adj) / len(adj) if adj else None
    f["adj_margin_best"] = min(adj) if adj else None
    same = [p for p in past if p.get("surface") == race["surface"] and p.get("margin") is not None]
    f["same_surf_margin"] = sum(p["margin"] for p in same) / len(same) if same else None
    near = [p for p in same if race["dist"] and abs(p["dist"] - race["dist"]) <= 200]
    f["near_dist_margin"] = sum(p["margin"] for p in near) / len(near) if near else None
    course = [p for p in near if p.get("place") == race["place"]]
    f["course_margin"] = sum(p["margin"] for p in course) / len(course) if course else None
    f["n_wins"] = sum(1 for p in past if p.get("finish") == 1)
    f["n_top3"] = sum(1 for p in past if p.get("finish") and p["finish"] <= 3)
    f["n_same_cls_top3"] = sum(1 for p in past if p.get("finish") and p["finish"] <= 3
                               and p.get("cls") is not None and p["cls"] >= tcls)
    f["max_cls_diff"] = max((p["cls"] - tcls for p in past if p.get("cls") is not None), default=None)
    pos1 = [p["pos"][0] / p["heads"] for p in past[:3] if p.get("pos") and p.get("heads")]
    f["front_ratio"] = sum(pos1) / len(pos1) if pos1 else None
    l3 = [p["last3f"] for p in past[:3] if p.get("last3f")]
    f["mean_last3f"] = sum(l3) / len(l3) if l3 else None
    if past and race.get("surface"):
        f["last_kinryo_diff"] = None
    return f


def add_race_relative(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("race_id")
    df["field_size"] = g["umaban"].transform("count")
    for col in ["kinryo", "adj_margin_mean", "adj_margin_best", "mean_margin", "p1_margin",
                "near_dist_margin", "same_surf_margin", "mean_last3f", "front_ratio",
                "n_same_cls_top3", "max_cls_diff"]:
        df[f"{col}_rank"] = g[col].rank(pct=True, na_option="keep")
        df[f"{col}_dev"] = df[col] - g[col].transform("mean")
    # 展開: 先行馬の数とその中での自分の位置
    df["n_front"] = g["front_ratio"].transform(lambda s: (s < 0.3).sum())
    df["is_front"] = (df["front_ratio"] < 0.3).astype(float)
    df["sex_f"] = (df["sex"] == "牝").astype(int)
    df["sex_g"] = (df["sex"] == "セ").astype(int)
    df["surf_d"] = (df["race_surface"] == "ダ").astype(int)
    df["baba_bad"] = df["race_baba"].isin(["重", "不"]).astype(int)
    return df


def load_all(data_dir: Path = Path("win5_bt_data/races")) -> pd.DataFrame:
    rows = []
    for p in sorted(data_dir.glob("*.json.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            rows.extend(horse_rows(json.load(f)))
    df = pd.DataFrame(rows)
    df = df[df["umaban"].notna()].copy()
    return add_race_relative(df)


if __name__ == "__main__":
    df = load_all()
    print(df.shape)
    print(df.head().T)
