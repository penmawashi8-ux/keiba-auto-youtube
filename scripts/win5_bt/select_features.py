"""特徴量の後ろ向き選抜 → 最終評価。

- 選抜用期間（DEV_MONTHS）: 各月を「その月より前で学習→その月の全レースで評価」。
  指標は勝ち馬のlog loss（レース内で確率を正規化）。
- 重要度の低い特徴を一定割合ずつ削り、DEV成績が最良の集合を採用。
- 最終評価期間（HOLDOUT_MONTHS）は選抜中は一切見ない。最後に1回だけ評価する。

使い方: python scripts/win5_bt/select_features.py <wide.pkl> <out_dir> ['{"params": {...}, "rounds": 300, "drop": 0.2}']
"""
import json
import math
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest import best_allocation, parse_yen  # noqa: E402
from features_wide import feature_list  # noqa: E402

DEV_MONTHS = ["202511", "202512", "202601", "202602", "202603", "202604"]
HOLDOUT_MONTHS = ["202605", "202606", "202607", "202608", "202609", "202610", "202611"]
PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=15, min_data_in_leaf=60,
              feature_fraction=0.5, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0,
              verbose=-1, seed=11, num_threads=0, max_bin=63)
ROUNDS = 300


def softmax_by_race(df: pd.DataFrame, raw: np.ndarray) -> np.ndarray:
    s = pd.Series(raw, index=df.index)
    e = np.exp(s - s.groupby(df["race_id"]).transform("max"))
    return (e / e.groupby(df["race_id"]).transform("sum")).to_numpy()


_DF = {}


def _fold(args):
    m, cols, params, rounds = args
    df = _DF["df"]
    tr = df[(df["month"] < m) & df["finish"].notna()]
    te = df[df["month"] == m]
    if len(te) == 0:
        return None
    model = lgb.train(params, lgb.Dataset(tr[cols], label=tr["y"]), num_boost_round=rounds)
    raw = model.predict(te[cols], raw_score=True)
    out = te[[c for c in ("race_id", "date", "umaban", "finish", "odds", "pop", "name", "is_win5") if c in te]].copy()
    out["p"] = softmax_by_race(te, raw)
    return out, pd.Series(model.feature_importance("gain"), index=cols)


def fit_predict(df, cols, months, params=PARAMS, rounds=ROUNDS, collect_imp=False):
    """月ごとの学習は互いに独立なのでプロセス並列で回す（各プロセスは1スレッド）。"""
    import multiprocessing as mp
    _DF["df"] = df
    n = min(len(months), mp.cpu_count())
    p = dict(params, num_threads=max(1, mp.cpu_count() // n))
    with mp.get_context("fork").Pool(n) as pool:
        outs = [o for o in pool.map(_fold, [(m, cols, p, rounds) for m in months]) if o is not None]
    preds = [o[0] for o in outs]
    imps = [o[1] for o in outs]
    res = pd.concat(preds)
    imp = pd.concat(imps, axis=1).mean(axis=1) if collect_imp else None
    return res, imp


def metrics(res: pd.DataFrame, col="p") -> dict:
    r = res[res.groupby("race_id")["finish"].transform(lambda s: (s == 1).any())].copy()
    r["rank"] = r.groupby("race_id")[col].rank(ascending=False, method="first")
    w = r[r["finish"] == 1].drop_duplicates("race_id")
    return {"races": len(w), "ll": float(-np.log(np.clip(w[col], 1e-6, 1)).mean()),
            "top1": float((w["rank"] <= 1).mean()), "top3": float((w["rank"] <= 3).mean())}


def win5_eval(res: pd.DataFrame, days: dict, months, col="p", budget=100):
    rows = []
    for ds, info in sorted(days.items()):
        if ds[:6] not in months:
            continue
        sub = [res[res["race_id"] == r] for r in info["race_ids"]]
        if any(len(s) == 0 or not (s["finish"] == 1).any() for s in sub):
            continue
        ns, pr = best_allocation([s[col].fillna(0).to_numpy() for s in sub], budget)
        hits = []
        for s, n in zip(sub, ns):
            hits.append(bool((s.sort_values(col, ascending=False).head(n)["finish"] == 1).any()))
        win = all(hits)
        rows.append({"date": ds, "points": math.prod(ns), "races_hit": sum(hits), "hit": win,
                     "payout": parse_yen(info.get("payout")) or 0})
    ev = pd.DataFrame(rows)
    cost = ev["points"].sum() * 100
    ret = (ev["payout"] * ev["hit"]).sum()
    return {"days": len(ev), "hits": int(ev["hit"].sum()), "avg_races_hit": float(ev["races_hit"].mean()),
            "cost": int(cost), "return": int(ret), "roi": float(ret / cost) if cost else 0.0}, ev


def main():
    global PARAMS, ROUNDS
    df = pd.read_pickle(sys.argv[1])
    out_dir = Path(sys.argv[2])
    cfg = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    PARAMS = dict(PARAMS, **cfg.get("params", {}))
    ROUNDS = cfg.get("rounds", ROUNDS)
    drop = cfg.get("drop", 0.2)
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = feature_list(df)
    log = []
    print(f"start: {len(cols)} features", flush=True)
    res, imp = fit_predict(df, cols, DEV_MONTHS, params=PARAMS, rounds=ROUNDS, collect_imp=True)
    m = metrics(res)
    log.append({"n": len(cols), **m})
    print(f"  n={len(cols)} dev ll={m['ll']:.4f} top1={m['top1']:.3f} top3={m['top3']:.3f}", flush=True)
    best = (m["ll"], cols)
    cur = [c for c in cols if imp[c] > 0]
    while len(cur) >= 15:
        res, imp = fit_predict(df, cur, DEV_MONTHS, params=PARAMS, rounds=ROUNDS, collect_imp=True)
        m = metrics(res)
        log.append({"n": len(cur), **m})
        print(f"  n={len(cur)} dev ll={m['ll']:.4f} top1={m['top1']:.3f} top3={m['top3']:.3f}", flush=True)
        if m["ll"] < best[0]:
            best = (m["ll"], list(cur))
        keep = max(int(len(cur) * (1 - drop)), len(cur) - 60)
        cur = list(imp.sort_values(ascending=False).index[:keep])
        if keep < 15:
            break
    sel = best[1]
    print(f"selected {len(sel)} features (dev ll={best[0]:.4f})", flush=True)
    (out_dir / "selected_features.json").write_text(json.dumps(sel, ensure_ascii=False, indent=1))
    pd.DataFrame(log).to_csv(out_dir / "selection_log.csv", index=False)

    # ---- 最終評価（ここで初めて holdout を見る） ----
    days = {json.loads(l)["date"]: json.loads(l) for l in Path("win5_bt_data/win5_days.jsonl").read_text().splitlines()}
    summary = {}
    for name, fc in (("all_features", cols), ("selected", sel)):
        res, imp = fit_predict(df, fc, HOLDOUT_MONTHS, params=PARAMS, rounds=ROUNDS, collect_imp=True)
        res["p_odds"] = (1 / res["odds"]) / (1 / res["odds"]).groupby(res["race_id"]).transform("sum")
        summary[name] = {"all_races": metrics(res), "win5_races": metrics(res[res["is_win5"] == 1]),
                         "win5": win5_eval(res, days, HOLDOUT_MONTHS)[0]}
        if name == "selected":
            summary["odds_baseline"] = {"all_races": metrics(res, "p_odds"),
                                        "win5_races": metrics(res[res["is_win5"] == 1], "p_odds"),
                                        "win5": win5_eval(res, days, HOLDOUT_MONTHS, "p_odds")[0]}
            imp.sort_values(ascending=False).head(40).to_csv(out_dir / "top_importance.csv")
            win5_eval(res, days, HOLDOUT_MONTHS)[1].to_csv(out_dir / "holdout_win5_days.csv", index=False)
    summary["config"] = cfg
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
