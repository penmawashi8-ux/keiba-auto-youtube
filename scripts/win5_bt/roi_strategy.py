"""回収率重視のWIN5戦略（直前オッズを使わない）。

- 勝率モデル p: 人気・オッズ不使用（ウォークフォワード予測を wf_res.pkl から読む）
- 予想人気モデル q: 出走前に分かる特徴量だけで「最終単勝オッズから出る支持率」を予測する
  （過去のオッズは学習の正解ラベルとしてのみ使用。予想時点ではオッズを見ない）
- WIN5の票の偏り: log(的中票数/総票数) = a * Σ log q_i + b を選抜用期間で推定
- 各組み合わせの期待回収率 = p_combo × 推定配当 / 100円。基準値以上を上限まで買う（0点＝見送りもあり）
- 基準値などは選抜用期間(DEV)で決め、最終評価期間(HOLDOUT)で1回だけ評価する

使い方: python scripts/win5_bt/roi_strategy.py <wide.pkl> <wf_res.pkl> <out_dir>
"""
import itertools
import json
import math
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest import parse_yen  # noqa: E402
from features_wide import feature_list  # noqa: E402
from select_features import DEV_MONTHS, HOLDOUT_MONTHS  # noqa: E402

POP_PARAMS = dict(objective="regression", learning_rate=0.05, num_leaves=31, min_data_in_leaf=50,
                  feature_fraction=0.5, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0,
                  verbose=-1, seed=5, max_bin=63)


def softmax(df, col, T=1.0):
    s = df[col] / T
    e = np.exp(s - s.groupby(df["race_id"]).transform("max"))
    return e / e.groupby(df["race_id"]).transform("sum")


def popularity_predictions(df: pd.DataFrame, months) -> pd.DataFrame:
    """予想人気（支持率）のウォークフォワード予測。正解ラベル = log(単勝オッズから出した支持率)。"""
    cols = feature_list(df)
    d = df[df["odds"].notna() & (df["odds"] > 0)].copy()
    d["q_true"] = (1 / d["odds"]) / (1 / d["odds"]).groupby(d["race_id"]).transform("sum")
    d["lq"] = np.log(d["q_true"])
    out = []
    for m in months:
        tr = d[d["month"] < m]
        te = df[df["month"] == m].copy()
        if len(te) == 0:
            continue
        model = lgb.train(POP_PARAMS, lgb.Dataset(tr[cols], label=tr["lq"]), num_boost_round=400)
        te["lq_hat"] = model.predict(te[cols])
        te["q_hat"] = softmax(te, "lq_hat")
        out.append(te[["race_id", "umaban", "q_hat"]])
        print(f"  pop model {m}: trained on {len(tr)} rows", flush=True)
    return pd.concat(out)


def day_frames(res, days):
    out = {}
    for ds, info in days.items():
        sub = [res[res["race_id"] == r] for r in info["race_ids"]]
        if any(len(s) == 0 or not (s["finish"] == 1).any() for s in sub):
            continue
        out[ds] = sub
    return out


def fit_share_model(frames, days, dates, qcol="q_hat"):
    """log(的中票/総票) = a * Σ log q(勝ち馬) + b を最小二乗で推定。"""
    X, Y = [], []
    for ds in dates:
        info = days[ds]
        ht = int(str(info.get("hit_tickets") or "0").replace(",", ""))
        st = int(str(info.get("sales_tickets") or "0").replace(",", ""))
        if ht <= 0 or st <= 0:
            continue
        x = sum(math.log(max(s.loc[s["finish"] == 1, qcol].iloc[0], 1e-6)) for s in frames[ds])
        X.append(x)
        Y.append(math.log(ht / st))
    X, Y = np.array(X), np.array(Y)
    a, b = np.polyfit(X, Y, 1)
    r = np.corrcoef(X, Y)[0, 1]
    return a, b, r, len(X)


def choose_combos(sub, a, b, sales, pool_per_ticket, theta, budget, topk=7, pcol="p"):
    """期待回収率 ≥ theta の組み合わせを期待値の高い順に最大 budget 点。"""
    cand = []
    for s in sub:
        s = s.sort_values(pcol, ascending=False).head(topk)
        cand.append(list(zip(s["umaban"].astype(int), s[pcol], s["q_hat"])))
    chosen = []
    for combo in itertools.product(*cand):
        p = math.prod(c[1] for c in combo)
        lq = sum(math.log(max(c[2], 1e-6)) for c in combo)
        share = math.exp(a * lq + b)              # この組み合わせに入る票の割合（推定）
        est_payout = pool_per_ticket / max(share * sales, 1.0)  # 100円あたり推定配当
        est_payout = min(est_payout, 600_000_000)
        ev = p * est_payout / 100
        if ev >= theta:
            chosen.append((ev, tuple(c[0] for c in combo), p, est_payout))
    chosen.sort(reverse=True)
    return chosen[:budget]


def simulate(frames, days, dates, a, b, theta, budget, pool_mode="typical", pcol="p"):
    rows = []
    typical_pool = np.median([parse_yen(days[d]["payout"]) * int(str(days[d]["hit_tickets"]).replace(",", ""))
                              for d in days if days[d].get("hit_tickets") and parse_yen(days[d].get("payout"))])
    typical_sales = np.median([int(str(days[d]["sales_tickets"]).replace(",", "")) for d in days if days[d].get("sales_tickets")])
    for ds in dates:
        sub = frames[ds]
        info = days[ds]
        # 締切前に売上は分からないので、過去の中央値で配当を見積もる
        chosen = choose_combos(sub, a, b, typical_sales, typical_pool * 0.7 / 0.7, theta, budget, pcol=pcol)
        win = tuple(int(s.loc[s["finish"] == 1, "umaban"].iloc[0]) for s in sub)
        hit = any(c[1] == win for c in chosen)
        payout = parse_yen(info["payout"]) or 0
        rows.append({"date": ds, "points": len(chosen), "hit": hit, "return": payout if hit else 0,
                     "payout": payout, "exp_ev": float(np.mean([c[0] for c in chosen])) if chosen else 0.0,
                     "p_hit": sum(c[2] for c in chosen)})
    ev = pd.DataFrame(rows)
    cost = int(ev["points"].sum() * 100)
    ret = int(ev["return"].sum())
    return ev, {"days": len(ev), "bet_days": int((ev["points"] > 0).sum()), "hits": int(ev["hit"].sum()),
                "cost": cost, "return": ret, "roi": ret / cost if cost else float("nan"),
                "exp_hits": float(ev["p_hit"].sum())}


def main():
    wide = pd.read_pickle(sys.argv[1])
    res = pd.read_pickle(sys.argv[2])
    out = Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    days = {json.loads(l)["date"]: json.loads(l) for l in Path("win5_bt_data/win5_days.jsonl").read_text().splitlines()}
    months = sorted({d[:6] for d in days})
    qcache = out / "q_hat_all.pkl"
    if qcache.exists():
        q = pd.read_pickle(qcache)
    else:
        q = popularity_predictions(wide, months)
        q.to_pickle(qcache)
    res = res.merge(q, on=["race_id", "umaban"], how="inner")
    res["q_true"] = (1 / res["odds"]) / (1 / res["odds"]).groupby(res["race_id"]).transform("sum")
    # 予想人気モデルの精度（実際の支持率との相関）
    ok = res["q_true"].notna()
    print("pop model corr(log q_hat, log q_true):",
          round(np.corrcoef(np.log(res.loc[ok, "q_hat"]), np.log(res.loc[ok, "q_true"]))[0, 1], 3))
    # ---- 勝率モデルと予想人気の結合（Benter方式）: logit = α·log p + β·log q_hat をDEV全レースで推定 ----
    w = res[res.groupby("race_id")["finish"].transform(lambda x: (x == 1).any())].copy()
    w["lp"], w["lq"] = np.log(w["p"].clip(1e-6)), np.log(w["q_hat"].clip(1e-6))

    def ll(sub, al, be):
        z = al * sub["lp"] + be * sub["lq"]
        z = z - z.groupby(sub["race_id"]).transform("max")
        lse = np.log(np.exp(z).groupby(sub["race_id"]).transform("sum"))
        return float(-(z - lse)[sub["finish"] == 1].mean())
    wd = w[w["month"].isin(DEV_MONTHS)] if "month" in w else w[w["date"].str[:6].isin(DEV_MONTHS)]
    grid = [(al, be) for al in np.arange(0, 1.61, 0.1) for be in np.arange(0, 1.61, 0.1) if al + be > 0]
    al, be = min(grid, key=lambda ab: ll(wd, *ab))
    wh = w[w["date"].str[:6].isin(HOLDOUT_MONTHS)]
    wh_ok = wh[wh.groupby("race_id")["q_true"].transform(lambda x: x.notna().all())]
    print(f"blend (DEV): alpha={al:.1f} (勝率モデル) beta={be:.1f} (予想人気)")
    print("HOLDOUT winner log loss: 勝率モデル", round(ll(wh, 1, 0), 4), " 予想人気", round(ll(wh, 0, 1), 4),
          " 結合", round(ll(wh, al, be), 4))
    wq = wh_ok.copy()
    wq["lq"] = np.log(wq["q_true"])
    print("  参考: 実際の最終オッズ", round(ll(wq, 0, 1), 4), "(締切前には分からない)")
    z = al * np.log(res["p"].clip(1e-6)) + be * np.log(res["q_hat"].clip(1e-6))
    z = z - z.groupby(res["race_id"]).transform("max")
    res["p"] = np.exp(z) / np.exp(z).groupby(res["race_id"]).transform("sum")
    report_blend = {"alpha": al, "beta": be}
    frames = day_frames(res, days)
    dev = [d for d in sorted(frames) if d[:6] in DEV_MONTHS]
    hold = [d for d in sorted(frames) if d[:6] in HOLDOUT_MONTHS]
    a, b, r, n = fit_share_model(frames, days, dev)
    print(f"share model (DEV, n={n}): log(hit/sales) = {a:.3f} * Σlog q + {b:.3f}  (r={r:.3f})")
    report = {"share_model": {"a": a, "b": b, "r": r, "n": n}, "blend": report_blend, "dev": {}, "holdout": {}}
    for pcol in ("p",):
        for theta in (1.0, 1.2, 1.4, 1.7, 2.0, 2.5, 3.0):
            _, sm = simulate(frames, days, dev, a, b, theta, 100, pcol=pcol)
            report["dev"][f"{pcol}_theta{theta}"] = sm
            print(f"DEV  {pcol} θ={theta}: {sm}", flush=True)
    # DEVで回収率が最良（かつ購入日が5日以上）の設定を選ぶ
    cands = {k: v for k, v in report["dev"].items() if v["bet_days"] >= 5 and v["cost"] > 0}
    best = max(cands, key=lambda k: cands[k]["roi"])
    pcol = best.split("_theta")[0]
    theta = float(best.split("_theta")[1])
    print("selected on DEV:", best)
    ev, sm = simulate(frames, days, hold, a, b, theta, 100, pcol=pcol)
    report["holdout"][best] = sm
    print("HOLDOUT:", sm)
    for k in ("p_theta1.0", "p_theta1.4", "p_theta2.0", "p_theta3.0"):
        if k != best:
            pc, th = k.split("_theta")[0], float(k.split("_theta")[1])
            report["holdout"][k + " (参考)"] = simulate(frames, days, hold, a, b, th, 100, pcol=pc)[1]
    ev.to_csv(out / "holdout_days.csv", index=False)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
