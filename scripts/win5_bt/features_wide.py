"""大量特徴量版（できるだけ多く作ってから選抜する用）。

1行=1頭。人気・オッズは特徴量に使わない（結果の人気・オッズは比較用に保持）。
近走は当該レース日付より前のものだけを使う。
"""
import gzip
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from features import (PLACES_JRA, SURF_RE, ZEN, class_of, parse_race_meta, parse_rest,  # noqa: E402
                      to_sec)

BABA_CODE = {"良": 0, "稍": 1, "重": 2, "不": 3}
PLACE_CODE = {p: i for i, p in enumerate(sorted(PLACES_JRA))}


def parse_past(text: str) -> dict | None:
    toks = text.split()
    if len(toks) < 6 or not re.match(r"^\d{4}\.\d{2}\.\d{2}$", toks[0]):
        return None
    si = next((i for i, t in enumerate(toks) if SURF_RE.match(t)), None)
    if si is None or si < 3:
        return None
    sm = SURF_RE.match(toks[si])
    r = {"date": toks[0].replace(".", "-"), "place": toks[1],
         "finish": int(toks[2]) if toks[2].isdigit() else None,
         "cls": class_of(toks[3:si]), "surface": sm.group(1), "dist": int(sm.group(2)),
         "inner_outer": 1 if "(外)" in toks[si] else (-1 if "(内)" in toks[si] else 0)}
    r["jra"] = toks[1] in PLACES_JRA
    rest = toks[si + 1:]
    if rest and (re.match(r"^\d:\d{2}\.\d$", rest[0]) or (re.match(r"^\d{2}\.\d$", rest[0]) and r["dist"] <= 1000)):
        r["time"] = to_sec(rest[0])
        rest = rest[1:]
    after_umaban = False
    for t in rest:
        if t in BABA_CODE and "baba" not in r:
            r["baba"] = t
        elif re.match(r"^\d+頭$", t):
            r["heads"] = int(t[:-1])
        elif re.match(r"^\d+番$", t):
            r["umaban"] = int(t[:-1])
            after_umaban = True
        elif after_umaban and "jockey" not in r and not re.match(r"^\d+人$|^人$", t) and \
                not re.match(r"^[\d.]+$", t):
            r["jockey"] = t
        elif re.match(r"^\d{2}\.\d$", t) and "kinryo" not in r and "heads" in r:
            r["kinryo"] = float(t)
        elif (re.match(r"^\d+(-\d+)+$", t) or (re.match(r"^\d+$", t) and "kinryo" in r)) and "pos" not in r:
            r["pos"] = [int(x) for x in t.split("-")]
        elif re.match(r"^\(\d{2}\.\d\)$", t):
            r["last3f"] = float(t[1:-1])
        elif re.match(r"^\d{3}\([+-]?\d+\)$", t):
            r["bw"] = int(t[:3])
            r["bw_diff"] = int(t[4:-1])
    m = re.search(r"\((-?\d+\.\d)\)\s*$", text)
    if m and r["finish"] is not None:
        r["margin"] = float(m.group(1))
    return r


def _key(place, surface, dist, baba):
    bg = "g" if baba in ("良", None) else ("s" if baba == "稍" else "h")
    return (place, surface, dist, bg)


def mean(v):
    v = [x for x in v if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(v) / len(v) if v else None


def vmin(v):
    v = [x for x in v if x is not None]
    return min(v) if v else None


def vmax(v):
    v = [x for x in v if x is not None]
    return max(v) if v else None


def vstd(v):
    v = [x for x in v if x is not None]
    return float(np.std(v)) if len(v) >= 2 else None


def dist_band(d):
    if d is None:
        return None
    return 0 if d <= 1300 else 1 if d <= 1600 else 2 if d <= 1900 else 3 if d <= 2200 else 4


def load_records(dirs):
    recs = []
    for d in dirs:
        for p in sorted(Path(d).glob("*.json.gz")):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                rec = json.load(f)
            rec["is_win5"] = int(Path(d).name == "races")
            recs.append(rec)
    return recs


def parse_horses(rec):
    race = parse_race_meta(rec["meta"])
    rdate = f"{rec['date'][:4]}-{rec['date'][4:6]}-{rec['date'][6:]}"
    res = {}
    for cells in rec.get("result", [])[1:]:
        if len(cells) >= 11 and cells[2].isdigit():
            res[int(cells[2])] = {
                "finish": int(cells[0]) if cells[0].isdigit() else None,
                "pop": int(cells[9]) if cells[9].isdigit() else None,
                "odds": float(cells[10]) if re.match(r"^\d+(\.\d+)?$", cells[10]) else None,
                "res_time": to_sec(cells[7]) if len(cells) > 7 else None,
                "name": cells[3]}
    out = []
    for h in rec["horses"]:
        cells = h["cells"]
        waku = [t for c, t in cells if c.startswith("Waku")]
        if len(waku) < 2 or not waku[1].isdigit():
            continue
        info = next((t for c, t in cells if "Horse_Info" in c), "")
        jk = next((t for c, t in cells if c.startswith("Jockey")), "")
        mj = re.match(r"^(牡|牝|セ)(\d+)\S*\s+(\S+)\s+([\d.]+)", jk)
        past = []
        rest_m = None
        for c, t in cells:
            if c.startswith("Past"):
                p = parse_past(t)
                if p and p["date"] < rdate:
                    past.append(p)
            elif "休養" in t and rest_m is None and "Horse_Info" not in c:
                rest_m = parse_rest(t)
        mt = re.search(r"(美浦|栗東)・(\S+)", info)
        mb = re.search(r"\(([^)]+)\)", info)
        style = re.search(r"\s(逃|先|差|追)\s", info)
        out.append({
            "race_id": rec["race_id"], "date": rec["date"], "rdate": rdate, "is_win5": rec["is_win5"],
            "umaban": int(waku[1]), "waku": int(waku[0]) if waku[0].isdigit() else None,
            "sex": mj.group(1) if mj else None, "age": int(mj.group(2)) if mj else None,
            "jockey": mj.group(3) if mj else None, "kinryo": float(mj.group(4)) if mj else None,
            "trainer": mt.group(2) if mt else None, "stable_east": int(bool(mt and mt.group(1) == "美浦")),
            "sire": info.split(" (")[0].split()[0] if info else None,
            "bms": mb.group(1) if mb else None, "style": style.group(1) if style else None,
            "rest_months": rest_m, "_past": past, "_race": race, **res.get(int(waku[1]), {}),
        })
    return out


def standard_times(rows):
    seen, by = set(), {}
    for r in rows:
        for p in r["_past"]:
            if not p.get("jra") or p.get("time") is None or p.get("margin") is None:
                continue
            k = _key(p["place"], p["surface"], p["dist"], p.get("baba"))
            wt = round(p["time"] - max(p["margin"], 0.0), 1)
            uid = (p["date"], k, wt)
            if uid in seen:
                continue
            seen.add(uid)
            by.setdefault(k, []).append(wt)
    # 上がり3Fの基準（コース×距離×馬場）
    return {k: float(np.median(v)) for k, v in by.items() if len(v) >= 5}


def l3_standards(rows):
    by = {}
    for r in rows:
        for p in r["_past"]:
            if p.get("last3f") and p.get("jra"):
                by.setdefault(_key(p["place"], p["surface"], p["dist"], p.get("baba")), []).append(p["last3f"])
    return {k: float(np.median(v)) for k, v in by.items() if len(v) >= 10}


def horse_features(r, std, l3std):
    race, past = r["_race"], r["_past"]
    f = {}
    tcls, tsurf, tdist, tplace = race["cls"], race["surface"], race["dist"], race["place"]
    tdate = pd.Timestamp(r["rdate"])
    # --- 今回の条件 ---
    for k in ("cls", "handicap", "age2", "dist", "heads"):
        f[f"race_{k}"] = race.get(k)
    f["race_surf_d"] = int(tsurf == "ダ")
    f["race_baba"] = BABA_CODE.get(race.get("baba"), np.nan)
    f["race_place"] = PLACE_CODE.get(tplace, -1)
    f["race_distband"] = dist_band(tdist)
    f["race_month"] = int(r["date"][4:6])
    f["sex_f"] = int(r["sex"] == "牝")
    f["sex_g"] = int(r["sex"] == "セ")
    f["age"] = r["age"]
    f["kinryo"] = r["kinryo"]
    f["umaban"] = r["umaban"]
    f["waku"] = r["waku"]
    f["stable_east"] = r["stable_east"]
    f["rest_months"] = r["rest_months"]
    for s in ("逃", "先", "差", "追"):
        f[f"style_{s}"] = int(r["style"] == s)
    f["n_past"] = len(past)
    # --- 近走ごとの生特徴（最大5走） ---
    sps, l3rel = [], []
    for i in range(5):
        p = past[i] if i < len(past) else None
        pre = f"p{i+1}_"
        if p is None:
            sps.append(None)
            l3rel.append(None)
            continue
        heads = p.get("heads")
        pos = p.get("pos") or []
        st = std.get(_key(p["place"], p["surface"], p["dist"], p.get("baba")))
        sp = None
        if st is not None and p.get("time") is not None:
            sp = (st - p["time"]) * (1000 / p["dist"]) * 10
            if p.get("kinryo"):
                sp += (p["kinryo"] - 55) * 0.15 * (1000 / p["dist"]) * 10
        sps.append(sp)
        l3s = l3std.get(_key(p["place"], p["surface"], p["dist"], p.get("baba")))
        lr = (l3s - p["last3f"]) if (l3s and p.get("last3f")) else None
        l3rel.append(lr)
        nxt = past[i + 1] if i + 1 < len(past) else None
        f[pre + "finish"] = p.get("finish")
        f[pre + "fin_ratio"] = p["finish"] / heads if p.get("finish") and heads else None
        f[pre + "margin"] = p.get("margin")
        f[pre + "cls"] = p.get("cls")
        f[pre + "clsdiff"] = p["cls"] - tcls if p.get("cls") is not None else None
        f[pre + "same_surf"] = int(p["surface"] == tsurf)
        f[pre + "distdiff"] = p["dist"] - tdist if tdist else None
        f[pre + "dist"] = p["dist"]
        f[pre + "baba"] = BABA_CODE.get(p.get("baba"), np.nan)
        f[pre + "heads"] = heads
        f[pre + "gate_ratio"] = p["umaban"] / heads if p.get("umaban") and heads else None
        f[pre + "kinryo"] = p.get("kinryo")
        f[pre + "kinryo_vs_today"] = (r["kinryo"] - p["kinryo"]) if r["kinryo"] and p.get("kinryo") else None
        f[pre + "pos_first"] = pos[0] / heads if pos and heads else None
        f[pre + "pos_last"] = pos[-1] / heads if pos and heads else None
        f[pre + "pos_gain"] = (pos[0] - pos[-1]) / heads if len(pos) >= 2 and heads else None
        f[pre + "fin_vs_poslast"] = (pos[-1] - p["finish"]) / heads if pos and heads and p.get("finish") else None
        f[pre + "last3f"] = p.get("last3f")
        f[pre + "l3_rel"] = lr
        f[pre + "bw"] = p.get("bw")
        f[pre + "bw_diff"] = p.get("bw_diff")
        f[pre + "speed"] = sp
        f[pre + "days_ago"] = (tdate - pd.Timestamp(p["date"])).days
        f[pre + "gap_prev"] = (pd.Timestamp(p["date"]) - pd.Timestamp(nxt["date"])).days if nxt else None
        f[pre + "same_place"] = int(p["place"] == tplace)
        f[pre + "same_course"] = int(p["place"] == tplace and p["surface"] == tsurf and abs(p["dist"] - (tdist or 0)) <= 200)
        f[pre + "jra"] = int(p["jra"])
        f[pre + "inner_outer"] = p["inner_outer"]
        f[pre + "same_jockey"] = int(bool(p.get("jockey") and r["jockey"] and p["jockey"][:2] == r["jockey"][:2]))
        f[pre + "win"] = int(p.get("finish") == 1)
        f[pre + "top3"] = int(bool(p.get("finish") and p["finish"] <= 3))
        f["_pk" + str(i + 1)] = f'{p["date"]}|{p["place"]}|{p["surface"]}|{p["dist"]}'

    # --- 集約（直近3走・5走） ---
    for w in (3, 5):
        ps = past[:w]
        sw = sps[:w]
        lw = l3rel[:w]
        margins = [p.get("margin") for p in ps]
        fr = [p["finish"] / p["heads"] if p.get("finish") and p.get("heads") else None for p in ps]
        pf = [p["pos"][0] / p["heads"] if p.get("pos") and p.get("heads") else None for p in ps]
        pg = [(p["pos"][0] - p["pos"][-1]) / p["heads"] if p.get("pos") and len(p["pos"]) >= 2 and p.get("heads") else None for p in ps]
        adj = [p["margin"] - 0.4 * (p["cls"] - tcls) if p.get("margin") is not None and p.get("cls") is not None else None for p in ps]
        for name, v in (("margin", margins), ("fin_ratio", fr), ("speed", sw), ("l3_rel", lw),
                        ("pos_first", pf), ("pos_gain", pg), ("adj_margin", adj),
                        ("last3f", [p.get("last3f") for p in ps]), ("kinryo", [p.get("kinryo") for p in ps]),
                        ("clsdiff", [p["cls"] - tcls if p.get("cls") is not None else None for p in ps])):
            f[f"w{w}_{name}_mean"] = mean(v)
            f[f"w{w}_{name}_min"] = vmin(v)
            f[f"w{w}_{name}_max"] = vmax(v)
            f[f"w{w}_{name}_std"] = vstd(v)
        f[f"w{w}_wins"] = sum(1 for p in ps if p.get("finish") == 1)
        f[f"w{w}_top3"] = sum(1 for p in ps if p.get("finish") and p["finish"] <= 3)
        f[f"w{w}_top5"] = sum(1 for p in ps if p.get("finish") and p["finish"] <= 5)
        f[f"w{w}_out"] = sum(1 for p in ps if p.get("finish") is None)
        f[f"w{w}_same_surf"] = sum(1 for p in ps if p["surface"] == tsurf)
    # --- 条件付き集約 ---
    conds = {
        "surf": lambda p: p["surface"] == tsurf,
        "near": lambda p: p["surface"] == tsurf and tdist and abs(p["dist"] - tdist) <= 200,
        "course": lambda p: p["place"] == tplace and p["surface"] == tsurf,
        "samecls": lambda p: p.get("cls") is not None and p["cls"] >= tcls,
        "lowercls": lambda p: p.get("cls") is not None and p["cls"] < tcls,
        "goodbaba": lambda p: p.get("baba") == "良",
        "badbaba": lambda p: p.get("baba") in ("重", "不"),
        "sameband": lambda p: dist_band(p["dist"]) == dist_band(tdist),
        "longer": lambda p: tdist and p["dist"] > tdist,
        "shorter": lambda p: tdist and p["dist"] < tdist,
        "samejockey": lambda p: bool(p.get("jockey") and r["jockey"] and p["jockey"][:2] == r["jockey"][:2]),
    }
    for cn, fn in conds.items():
        idx = [i for i, p in enumerate(past) if fn(p)]
        f[f"c_{cn}_n"] = len(idx)
        f[f"c_{cn}_margin_mean"] = mean([past[i].get("margin") for i in idx])
        f[f"c_{cn}_margin_min"] = vmin([past[i].get("margin") for i in idx])
        f[f"c_{cn}_speed_max"] = vmax([sps[i] for i in idx])
        f[f"c_{cn}_speed_mean"] = mean([sps[i] for i in idx])
        f[f"c_{cn}_top3"] = sum(1 for i in idx if past[i].get("finish") and past[i]["finish"] <= 3)
        f[f"c_{cn}_l3_max"] = vmax([l3rel[i] for i in idx])
    # --- トレンド・変化 ---
    if len(sps) >= 2 and sps[0] is not None and sps[1] is not None:
        f["speed_trend12"] = sps[0] - sps[1]
    if len(past) >= 3 and all(p.get("margin") is not None for p in past[:3]):
        f["margin_trend13"] = past[2]["margin"] - past[0]["margin"]
    if past:
        p1 = past[0]
        f["cls_up"] = tcls - p1["cls"] if p1.get("cls") is not None else None
        f["dist_change"] = tdist - p1["dist"] if tdist else None
        f["surf_change"] = int(p1["surface"] != tsurf)
        f["days_since"] = (tdate - pd.Timestamp(p1["date"])).days
        f["kinryo_change"] = (r["kinryo"] - p1["kinryo"]) if r["kinryo"] and p1.get("kinryo") else None
        f["bw_last"] = p1.get("bw")
        f["first_after_win"] = int(p1.get("finish") == 1)
        f["max_cls_ever"] = vmax([p.get("cls") for p in past])
        f["max_cls_diff"] = f["max_cls_ever"] - tcls if f["max_cls_ever"] is not None else None
    return f


_G = {}


def _load_and_parse(args):
    path, is_win5 = args
    with gzip.open(path, "rt", encoding="utf-8") as f:
        rec = json.load(f)
    rec["is_win5"] = is_win5
    return parse_horses(rec)


def _feat_chunk(rows):
    out = []
    for r in rows:
        f = horse_features(r, _G["std"], _G["l3std"])
        f.update({k: r.get(k) for k in ("race_id", "date", "is_win5", "jockey", "trainer", "sire", "bms",
                                         "finish", "pop", "odds", "res_time", "name")})
        out.append(f)
    return out


def build(dirs=(Path("win5_bt_data/races"), Path("win5_bt_data/races_all")), workers=None) -> pd.DataFrame:
    dirs = [Path(d) for d in dirs]
    import multiprocessing as mp
    workers = workers or mp.cpu_count()
    jobs = [(p, int(Path(d).name != "races_all")) for d in dirs for p in sorted(Path(d).glob("*.json.gz"))
            if ".oikiri." not in p.name]
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        rows = [r for rr in pool.imap(_load_and_parse, jobs, chunksize=64) for r in rr]
    _G["std"] = standard_times(rows)
    _G["l3std"] = l3_standards(rows)
    size = max(1, len(rows) // (workers * 8))
    chunks = [rows[i:i + size] for i in range(0, len(rows), size)]
    with ctx.Pool(workers) as pool:  # fork後に _G を参照するので standard times は共有される
        feats = [f for ff in pool.imap(_feat_chunk, chunks) for f in ff]
    df = pd.DataFrame(feats).drop_duplicates(["race_id", "umaban"])
    df["month"] = df["date"].str[:6]
    df = add_race_level(df, rows)
    df = add_oikiri(df, extra=[d for d in dirs if Path(d).name not in ("races", "races_all")])
    df["y"] = (df["finish"] == 1).astype(int)
    df["y3"] = (df["finish"] <= 3).astype(int)
    return add_relative(df)


def add_race_level(df: pd.DataFrame, rows) -> pd.DataFrame:
    """各レースの『メンバーの強さ』（出走時点のスピード指数上位5頭の平均）を求め、
    各馬の近走がどのくらい強いメンバー相手だったかを特徴量にする。"""
    lvl = df.groupby("race_id")["w5_speed_max"].apply(
        lambda s: s.dropna().sort_values(ascending=False).head(5).mean())
    meta = {}
    for r in rows:
        race = r["_race"]
        meta[r["race_id"]] = f'{r["rdate"]}|{race["place"]}|{race["surface"]}|{race["dist"]}'
    key_lvl = {}
    for rid, v in lvl.items():
        k = meta.get(rid)
        if k and pd.notna(v):
            key_lvl.setdefault(k, []).append(v)
    key_lvl = {k: float(np.mean(v)) for k, v in key_lvl.items()}
    df["race_level_today"] = df["race_id"].map(lvl)
    cols = []
    for i in range(1, 6):
        c = f"p{i}_race_level"
        df[c] = df[f"_pk{i}"].map(key_lvl) if f"_pk{i}" in df else np.nan
        cols.append(c)
    lv = df[cols]
    df["w3_race_level_mean"] = lv.iloc[:, :3].mean(axis=1)
    df["w5_race_level_max"] = lv.max(axis=1)
    df["p1_level_vs_today"] = df["p1_race_level"] - df["race_level_today"]
    # 強いメンバー相手の着差ほど価値が高い: 着差 − (相手レベル − 今回レベル)×係数
    for i in range(1, 4):
        df[f"p{i}_margin_lvladj"] = df[f"p{i}_margin"] - 0.05 * (df[f"p{i}_race_level"] - df["race_level_today"])
    df["w3_margin_lvladj_mean"] = df[[f"p{i}_margin_lvladj" for i in range(1, 4)]].mean(axis=1)
    df["w3_margin_lvladj_min"] = df[[f"p{i}_margin_lvladj" for i in range(1, 4)]].min(axis=1)
    return df.drop(columns=[c for c in df.columns if c.startswith("_pk")])


OIKIRI_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}


def add_oikiri(df: pd.DataFrame, d=Path("win5_bt_data/oikiri"), extra=()) -> pd.DataFrame:
    """調教評価（A〜D）と短評。データがないレースは欠損のまま。"""
    rank, critic = {}, {}
    files = list(Path(d).glob("*.json.gz"))
    for e in extra:
        files += list(Path(e).glob("*.oikiri.json.gz"))
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            data = json.load(f)
        rid = p.name[:12]
        for um, cells in data.items():
            for c, t in cells:
                if c.startswith("Rank_"):
                    rank[(rid, int(um))] = OIKIRI_RANK.get(t)
                elif c == "Training_Critic":
                    critic[(rid, int(um))] = t
    keys = list(zip(df["race_id"], df["umaban"].astype(int)))
    df["oikiri_rank"] = [rank.get(k) for k in keys]
    df["oikiri_critic"] = [critic.get(k) for k in keys]
    g = df.groupby("race_id")["oikiri_rank"]
    df["oikiri_rank__rk"] = g.rank(pct=True)
    df["oikiri_rank__dv"] = df["oikiri_rank"] - g.transform("mean")
    df["oikiri_n_A"] = (df["oikiri_rank"] == 4).groupby(df["race_id"]).transform("sum")
    return df


REL_COLS = None


def add_relative(df: pd.DataFrame) -> pd.DataFrame:
    """レース内での相対値（順位・平均との差）を主要特徴に付ける。"""
    g = df.groupby("race_id")
    df["field_size"] = g["umaban"].transform("count")
    df["umaban_ratio"] = df["umaban"] / df["field_size"]
    base = [c for c in df.columns if (c.startswith("w3_") or c.startswith("w5_") or c.startswith("c_")
                                      or c.startswith("p1_")) and (c.endswith("_mean") or c.endswith("_min")
                                      or c.endswith("_max") or c.startswith("p1_"))]
    base += ["kinryo", "age", "rest_months", "days_since", "max_cls_diff", "speed_trend12"]
    new = {}
    for c in base:
        if c not in df.columns or not pd.api.types.is_numeric_dtype(df[c]):
            continue
        new[f"{c}__rk"] = g[c].rank(pct=True, na_option="keep")
        new[f"{c}__dv"] = df[c] - g[c].transform("mean")
    df = pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)
    # 展開
    pf = df["w3_pos_first_mean"]
    df["pace_n_front"] = (pf < 0.25).groupby(df["race_id"]).transform("sum")
    df["pace_front_mean"] = pf.groupby(df["race_id"]).transform("mean")
    df["pace_my_front_rank"] = pf.groupby(df["race_id"]).rank(pct=True)
    return df.copy()


GROUP_KEYS = {
    "jockey": ["jockey"], "trainer": ["trainer"], "sire": ["sire"], "bms": ["bms"],
    "sire_surf": ["sire", "race_surf_d"], "sire_band": ["sire", "race_distband"],
    "bms_surf": ["bms", "race_surf_d"], "jockey_place": ["jockey", "race_place"],
    "trainer_place": ["trainer", "race_place"], "jockey_trainer": ["jockey", "trainer"],
    "jockey_surf": ["jockey", "race_surf_d"], "trainer_cls": ["trainer", "race_cls"],
    "oikiri_critic": ["oikiri_critic"], "trainer_oikiri": ["trainer", "oikiri_rank"],
}


def add_group_rates(df: pd.DataFrame, prior: int = 30) -> pd.DataFrame:
    """各キーの勝率・複勝率を『その月より前』のデータだけで付与（リークなし）。"""
    months = sorted(df["month"].unique())
    keys = {}
    for name, cols in GROUP_KEYS.items():
        k = df[cols[0]].astype(object).where(df[cols[0]].notna(), "").map(str)
        for c in cols[1:]:
            k = k + "|" + df[c].astype(object).where(df[c].notna(), "").map(str)
        keys[name] = k
    new = {f"gr_{n}_{t}": np.full(len(df), np.nan) for n in GROUP_KEYS for t in ("win", "top3", "n")}
    mon = df["month"].to_numpy()
    valid = df["finish"].notna().to_numpy()
    y = df["y"].to_numpy()
    y3 = df["y3"].to_numpy()
    for m in months:
        pm = (mon < m) & valid
        if pm.sum() < 3000:
            continue
        idx = mon == m
        for name in GROUP_KEYS:
            k = keys[name]
            kp = k[pm]
            g = pd.DataFrame({"k": kp.to_numpy(), "y": y[pm], "y3": y3[pm]}).groupby("k").agg(["sum", "count"])
            cnt = g[("y", "count")]
            for t, col in (("win", "y"), ("top3", "y3")):
                base = (y if col == "y" else y3)[pm].mean()
                rate = (g[(col, "sum")] + prior * base) / (cnt + prior)
                new[f"gr_{name}_{t}"][idx] = k[idx].map(rate).fillna(base).to_numpy()
            new[f"gr_{name}_n"][idx] = k[idx].map(cnt).fillna(0).to_numpy()
    df = pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)
    g = df.groupby("race_id")
    extra = {}
    for c in ("gr_jockey_win", "gr_trainer_win", "gr_sire_surf_win", "gr_jockey_top3"):
        extra[f"{c}__rk"] = g[c].rank(pct=True)
    return pd.concat([df, pd.DataFrame(extra, index=df.index)], axis=1)


NON_FEATURES = {"oikiri_critic", "race_id", "date", "is_win5", "jockey", "trainer", "sire", "bms", "finish", "pop",
                "odds", "res_time", "name", "month", "y", "y3"}


def feature_list(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in NON_FEATURES and pd.api.types.is_numeric_dtype(df[c])]


if __name__ == "__main__":
    out = Path(sys.argv[1])
    df = build()
    df = add_group_rates(df)
    df.to_pickle(out)
    print(df.shape, len(feature_list(df)), "features")
