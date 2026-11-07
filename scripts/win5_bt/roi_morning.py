"""朝オッズ（購入時点）を使った回収率検証。

- 購入時点のオッズ = 当日 CUTOFF 時刻までの最新スナップショット（keibaodds.com 時系列）
- 勝率 = α·log(モデル勝率) + β·log(朝オッズ支持率) のレース内ソフトマックス（α,β は DEV で推定）
- 払戻は実際の最終オッズ・実際の WIN5 配当で計算（朝→最終の変動も込み）
- 戦略の基準値は DEV で決め、HOLDOUT で1回だけ評価

使い方: python scripts/win5_bt/roi_morning.py <wf_res.pkl> <out_dir> [cutoff=10:00]
"""
import gzip
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest import parse_yen  # noqa: E402
from select_features import DEV_MONTHS, HOLDOUT_MONTHS  # noqa: E402


def load_morning(cutoff: str) -> pd.DataFrame:
    rows = []
    for p in Path("win5_bt_data/kodds").glob("*.json.gz"):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            d = json.load(f)
        times, tan = d.get("times"), d.get("tan")
        if not times or not tan:
            continue
        # times は新しい順（先頭が「最終オッズ」）。cutoff 以前で最も新しいものを使う
        idx = None
        for i, t in enumerate(times):
            if t != "最終オッズ" and t <= cutoff:
                idx = i
                break
        if idx is None or idx >= len(tan):
            continue
        snap = tan[idx]
        for um, o in snap.items():
            if o:
                rows.append({"race_id": d["race_id"], "umaban": int(um), "m_odds": float(o), "m_time": times[idx]})
    m = pd.DataFrame(rows)
    m["q_m"] = (1 / m["m_odds"]) / (1 / m["m_odds"]).groupby(m["race_id"]).transform("sum")
    return m


def race_ll(df, col):
    w = df[df["finish"] == 1].drop_duplicates("race_id")
    return float(-np.log(np.clip(w[col], 1e-9, 1)).mean())


def blend(df, al, be):
    z = al * np.log(df["p"].clip(1e-6)) + be * np.log(df["q_m"].clip(1e-6))
    z = z - z.groupby(df["race_id"]).transform("max")
    return np.exp(z) / np.exp(z).groupby(df["race_id"]).transform("sum")


def main():
    res = pd.read_pickle(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    cutoff = sys.argv[3] if len(sys.argv) > 3 else "10:00"
    m = load_morning(cutoff)
    df = res.merge(m, on=["race_id", "umaban"], how="inner")
    # 勝ち馬がいるレースだけ
    df = df[df.groupby("race_id")["finish"].transform(lambda s: (s == 1).any())]
    df["month"] = df["date"].str[:6]
    df["q_final"] = (1 / df["odds"]) / (1 / df["odds"]).groupby(df["race_id"]).transform("sum")
    dev, hold = df[df["month"].isin(DEV_MONTHS)], df[df["month"].isin(HOLDOUT_MONTHS)]
    print(f"races: DEV {dev.race_id.nunique()} / HOLDOUT {hold.race_id.nunique()}  (cutoff {cutoff})")

    grid = [(a, b) for a in np.arange(0, 1.01, 0.05) for b in np.arange(0.5, 1.51, 0.05)]
    best = min(grid, key=lambda ab: race_ll(dev.assign(pb=blend(dev, *ab)), "pb"))
    al, be = best
    df["pb"] = blend(df, al, be)
    dev, hold = df[df["month"].isin(DEV_MONTHS)], df[df["month"].isin(HOLDOUT_MONTHS)]
    rep = {"cutoff": cutoff, "alpha": al, "beta": be}
    print(f"blend on DEV: alpha(モデル)={al:.2f} beta(朝オッズ)={be:.2f}")
    for name, d in (("DEV", dev), ("HOLDOUT", hold)):
        rep[name] = {k: race_ll(d, c) for k, c in (("model", "p"), ("morning_odds", "q_m"), ("blend", "pb"), ("final_odds", "q_final"))}
        print(name, {k: round(v, 4) for k, v in rep[name].items()})

    # ---- 1レース単位の検証: 割安度 = 結合勝率 / 朝オッズ支持率。払戻は最終オッズ ----
    df["val"] = df["pb"] / df["q_m"]
    df["ret"] = np.where(df["finish"] == 1, df["odds"] * 100, 0)
    rep["single"] = {}
    for name, ms in (("DEV", DEV_MONTHS), ("HOLDOUT", HOLDOUT_MONTHS)):
        d = df[df["month"].isin(ms) & df["odds"].notna()].copy()
        d["vb"] = pd.cut(d["val"], [0, 0.9, 1.0, 1.05, 1.1, 1.2, 1.4, 10])
        t = d.groupby("vb", observed=True).agg(bets=("ret", "size"), wins=("finish", lambda s: int((s == 1).sum())),
                                              roi=("ret", lambda s: s.sum() / (len(s) * 100)))
        print(name)
        print(t.round(3).to_string())
        rep["single"][name] = {str(k): v for k, v in t.round(4).to_dict("index").items()}

    # ---- WIN5 ----
    days = {json.loads(l)["date"]: json.loads(l) for l in Path("win5_bt_data/win5_days.jsonl").read_text().splitlines()}
    frames = {}
    for ds, info in days.items():
        sub = [df[df["race_id"] == r] for r in info["race_ids"]]
        if all(len(s) > 0 and (s["finish"] == 1).any() for s in sub):
            frames[ds] = sub
    devd = [d for d in sorted(frames) if d[:6] in DEV_MONTHS and days[d].get("hit_tickets")]
    X, Y = [], []
    for ds in devd:
        info = days[ds]
        ht = int(str(info["hit_tickets"]).replace(",", ""))
        st = int(str(info["sales_tickets"]).replace(",", ""))
        if ht > 0:
            X.append(sum(math.log(s.loc[s["finish"] == 1, "q_m"].iloc[0]) for s in frames[ds]))
            Y.append(math.log(ht / st))
    a, b = np.polyfit(X, Y, 1)
    r = float(np.corrcoef(X, Y)[0, 1])
    print(f"WIN5 share model (DEV n={len(X)}): log(hit/sales) = {a:.3f}·Σlog q_m + {b:.3f}  r={r:.3f}")
    rep["share"] = {"a": a, "b": b, "r": r, "n": len(X)}
    pools = [parse_yen(days[d]["payout"]) * int(str(days[d]["hit_tickets"]).replace(",", ""))
             for d in devd if parse_yen(days[d].get("payout")) and days[d].get("hit_tickets")]
    sales = np.median([int(str(days[d]["sales_tickets"]).replace(",", "")) for d in devd])
    pool = float(np.median(pools))

    def run(dates, theta, budget=100, topk=8):
        rows = []
        for ds in dates:
            sub = frames[ds]
            cand = [list(zip(s.sort_values("pb", ascending=False).head(topk)["umaban"].astype(int),
                             s.sort_values("pb", ascending=False).head(topk)["pb"],
                             s.sort_values("pb", ascending=False).head(topk)["q_m"])) for s in sub]
            ch = []
            for combo in itertools.product(*cand):
                pc = math.prod(c[1] for c in combo)
                share = math.exp(a * sum(math.log(c[2]) for c in combo) + b)
                est = min(pool / max(share * sales, 1.0), 6e8)
                ev = pc * est / 100
                if ev >= theta:
                    ch.append((ev, tuple(c[0] for c in combo), pc))
            ch.sort(reverse=True)
            ch = ch[:budget]
            win = tuple(int(s.loc[s["finish"] == 1, "umaban"].iloc[0]) for s in sub)
            hit = any(c[1] == win for c in ch)
            pay = parse_yen(days[ds].get("payout")) or 0
            rows.append({"date": ds, "points": len(ch), "hit": hit, "return": pay if hit else 0,
                         "p_hit": sum(c[2] for c in ch), "payout": pay})
        e = pd.DataFrame(rows)
        cost = int(e["points"].sum() * 100)
        return e, {"days": len(e), "bet_days": int((e["points"] > 0).sum()), "points": int(e["points"].sum()),
                   "hits": int(e["hit"].sum()), "exp_hits": round(float(e["p_hit"].sum()), 3),
                   "cost": cost, "return": int(e["return"].sum()),
                   "roi": round(e["return"].sum() / cost, 3) if cost else None}

    hold_d = [d for d in sorted(frames) if d[:6] in HOLDOUT_MONTHS]
    rep["win5_dev"], rep["win5_holdout"] = {}, {}
    for th in (0.8, 1.0, 1.2, 1.5, 2.0):
        rep["win5_dev"][th] = run(devd, th)[1]
        print("WIN5 DEV θ", th, rep["win5_dev"][th])
    for th in (0.8, 1.0, 1.2, 1.5, 2.0):
        e, sm = run(hold_d, th)
        rep["win5_holdout"][th] = sm
        print("WIN5 HOLDOUT θ", th, sm)
        e.to_csv(out / f"win5_holdout_theta{th}.csv", index=False)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
