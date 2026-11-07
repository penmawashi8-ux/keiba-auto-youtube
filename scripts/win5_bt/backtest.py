"""WIN5 ウォークフォワード・バックテスト。

月ごとに「その月より前のデータだけで学習 → その月のWIN5を予想 → 答え合わせ」を繰り返す。
人気・オッズは特徴量に使わない。比較用に「単勝オッズから作った確率」で同じ買い方をした場合も出す。

使い方: python scripts/win5_bt/backtest.py [--budget 100] [--report path.md]
"""
import argparse
import itertools
import json
import math
import re
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from features import load_all  # noqa: E402

EXCLUDE = {"behind", "res_time", "is_win5", "race_id", "date", "umaban", "finish", "name", "pop", "odds", "jockey", "sex",
           "style", "race_surface", "race_baba", "race_place", "month", "y", "race_heads"}

PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=40,
              feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0,
              verbose=-1, seed=7)
ROUNDS = 400


def feature_cols(df: pd.DataFrame) -> list[str]:
    cols = [c for c in df.columns if c not in EXCLUDE and pd.api.types.is_numeric_dtype(df[c])]
    return cols


def add_style_dummies(df: pd.DataFrame) -> pd.DataFrame:
    for s in ("逃", "先", "差", "追"):
        df[f"style_{s}"] = (df["style"] == s).astype(int)
    return df


def normalize(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col] / df.groupby("race_id")[col].transform("sum")


def parse_yen(s: str | None) -> int | None:
    if not s:
        return None
    s = s.replace(",", "").replace("円", "")
    total = 0
    m = re.match(r"^(?:(\d+)億)?(?:(\d+)万)?(\d*)$", s)
    if not m:
        return None
    total += int(m.group(1) or 0) * 100_000_000
    total += int(m.group(2) or 0) * 10_000
    total += int(m.group(3) or 0)
    return total


def best_allocation(probs_by_race: list[np.ndarray], budget: int, max_n: int = 8):
    """各レースで上位n頭を買う。Π n ≤ budget で的中確率 Π cum_p(n) を最大化。"""
    cums = []
    for p in probs_by_race:
        sp = np.sort(p)[::-1]
        cums.append(np.concatenate([[0.0], np.cumsum(sp)]))
    best, best_ns = -1.0, None
    rng = [range(1, min(max_n, len(c) - 1) + 1) for c in cums]
    for ns in itertools.product(*rng):
        if math.prod(ns) > budget:
            continue
        pr = math.prod(c[n] for c, n in zip(cums, ns))
        if pr > best:
            best, best_ns = pr, ns
    return best_ns, best


def add_jockey_rate(tr: pd.DataFrame, te: pd.DataFrame, prior: int = 30) -> None:
    """学習期間のデータだけで騎手の勝率（平滑化）を計算し、学習・評価の両方に付与する。"""
    base = tr["y"].mean()
    g = tr.groupby("jockey")["y"].agg(["sum", "count"])
    rate = (g["sum"] + prior * base) / (g["count"] + prior)
    # 学習側は自分のレースを含むので軽いリークがあるが、平滑化で影響を抑える
    tr["jockey_rate"] = tr["jockey"].map(rate).fillna(base)
    te["jockey_rate"] = te["jockey"].map(rate).fillna(base)


def add_jockey_rate_expanding(df: pd.DataFrame, prior: int = 30) -> pd.DataFrame:
    """各月について「その月より前」の成績だけで騎手勝率・連対率を計算する（リークなし）。"""
    df = df.copy()
    df["jockey_rate"] = np.nan
    df["jockey_top3"] = np.nan
    df["_top3"] = (df["finish"] <= 3).astype(float)
    for m in sorted(df["month"].unique()):
        past = df[(df["month"] < m) & df["finish"].notna()]
        idx = df["month"] == m
        if len(past) < 500:
            continue
        for col, tgt in (("jockey_rate", "y"), ("jockey_top3", "_top3")):
            base = past[tgt].mean()
            g = past.groupby("jockey")[tgt].agg(["sum", "count"])
            rate = (g["sum"] + prior * base) / (g["count"] + prior)
            df.loc[idx, col] = df.loc[idx, "jockey"].map(rate).fillna(base)
    return df.drop(columns=["_top3"])


def walk_forward(df: pd.DataFrame, min_train_months: int = 3, params=None, rounds=ROUNDS):
    params = params or PARAMS
    df = df.copy()
    df["month"] = df["date"].str[:6]
    months = sorted(df["month"].unique())
    df = add_jockey_rate_expanding(df)
    cols = feature_cols(df)
    out = []
    for i, m in enumerate(months):
        if i < min_train_months:
            continue
        tr = df[df["month"] < m]
        te = df[df["month"] == m].copy()
        tr = tr[tr["finish"].notna()]
        dtrain = lgb.Dataset(tr[cols], label=tr["y"])
        model = lgb.train(params, dtrain, num_boost_round=rounds)
        te["score"] = model.predict(te[cols])
        out.append(te[te["is_win5"] == 1])
    res = pd.concat(out)
    res["p_model"] = normalize(res, "score")
    res["inv_odds"] = 1.0 / res["odds"]
    res["p_odds"] = normalize(res, "inv_odds")
    return res, cols, model


def evaluate(res: pd.DataFrame, days: dict, budget: int, prob_col: str):
    rows = []
    for ds, info in sorted(days.items()):
        rids = info["race_ids"]
        sub = [res[res["race_id"] == r] for r in rids]
        if any(len(s) == 0 or s["finish"].isna().all() for s in sub):
            continue
        probs = [s[prob_col].fillna(0).to_numpy() for s in sub]
        ns, pr = best_allocation(probs, budget)
        hits, picks = [], []
        for s, n in zip(sub, ns):
            top = s.sort_values(prob_col, ascending=False).head(n)
            picks.append(top["umaban"].astype(int).tolist())
            hits.append(bool((top["finish"] == 1).any()))
        cost = math.prod(ns) * 100
        payout = parse_yen(info.get("payout")) or 0
        win = all(hits)
        rows.append({"date": ds, "ns": ns, "points": math.prod(ns), "est_prob": pr,
                     "races_hit": sum(hits), "hit": win, "cost": cost,
                     "return": payout if win else 0, "payout": payout, "picks": picks,
                     "winners": [int(s.loc[s["finish"] == 1, "umaban"].iloc[0]) if (s["finish"] == 1).any() else None for s in sub]})
    return pd.DataFrame(rows)


def race_metrics(res: pd.DataFrame, prob_col: str) -> dict:
    r = res.copy()
    r["rank"] = r.groupby("race_id")[prob_col].rank(ascending=False, method="first")
    w = r[r["finish"] == 1]
    ll = -np.log(np.clip(w[prob_col].to_numpy(), 1e-6, 1)).mean()
    return {"races": int(w["race_id"].nunique()),
            "top1": float((w["rank"] <= 1).mean()),
            "top3": float((w["rank"] <= 3).mean()),
            "top5": float((w["rank"] <= 5).mean()),
            "winner_logloss": float(ll)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=100)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    df = load_all()
    df = add_style_dummies(df)
    df["y"] = (df["finish"] == 1).astype(int)
    days = {}
    for line in Path("win5_bt_data/win5_days.jsonl").read_text().splitlines():
        d = json.loads(line)
        days[d["date"]] = d

    res, cols, model = walk_forward(df)
    summary = {}
    for name, col in (("model", "p_model"), ("odds_baseline", "p_odds")):
        ev = evaluate(res, days, args.budget, col)
        rm = race_metrics(res, col)
        summary[name] = {**rm, "days": len(ev), "win5_hits": int(ev["hit"].sum()),
                         "avg_races_hit": float(ev["races_hit"].mean()),
                         "cost": int(ev["cost"].sum()), "return": int(ev["return"].sum()),
                         "roi": float(ev["return"].sum() / ev["cost"].sum()) if len(ev) else 0}
        summary[name]["detail"] = ev
    for k, v in summary.items():
        print(k, {kk: vv for kk, vv in v.items() if kk != "detail"})
    imp = pd.Series(model.feature_importance("gain"), index=cols).sort_values(ascending=False)
    print(imp.head(20))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report, "w") as f:
            json.dump({k: {kk: vv for kk, vv in v.items() if kk != "detail"} for k, v in summary.items()},
                      f, ensure_ascii=False, indent=2, default=str)
        summary["model"]["detail"].to_csv(Path(args.report).with_suffix(".days.csv"), index=False)


if __name__ == "__main__":
    main()
