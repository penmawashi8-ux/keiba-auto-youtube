"""特徴量・学習方法の比較実験（ウォークフォワード）。

使い方: python scripts/win5_bt/experiment.py <cache.pkl> <実験名...>
"""
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest import (PARAMS, add_jockey_rate_expanding, add_style_dummies, evaluate,  # noqa: E402
                      feature_cols, race_metrics)
from features import load_all  # noqa: E402


def load(cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pd.read_pickle(cache)
    df = load_all()
    df = add_style_dummies(df)
    df["y"] = (df["finish"] == 1).astype(int)
    df["month"] = df["date"].str[:6]
    df.to_pickle(cache)
    return df


def add_group_rate(df: pd.DataFrame, key: str, name: str, prior: int = 50) -> pd.DataFrame:
    """key（厩舎・種牡馬×芝ダなど）ごとの勝率を、その月より前のデータだけで計算する。"""
    df[name] = np.nan
    for m in sorted(df["month"].unique()):
        past = df[(df["month"] < m) & df["finish"].notna()]
        if len(past) < 500:
            continue
        base = past["y"].mean()
        g = past.groupby(key)["y"].agg(["sum", "count"])
        rate = (g["sum"] + prior * base) / (g["count"] + prior)
        idx = df["month"] == m
        df.loc[idx, name] = df.loc[idx, key].map(rate).fillna(base)
    return df


def run(df, variant: str, min_month="202508"):
    months = sorted(df["month"].unique())
    cols = feature_cols(df)
    drop = set()
    if "no_jockey" in variant:
        drop |= {"jockey_rate", "jockey_top3"}
    cols = [c for c in cols if c not in drop]
    p = dict(PARAMS)
    rounds = 400
    if "rank" in variant:
        p.update(objective="lambdarank", metric="ndcg", lambdarank_truncation_level=5)
    if "slow" in variant:
        p.update(learning_rate=0.015)
        rounds = 900
    if "big" in variant:
        p.update(num_leaves=31, min_data_in_leaf=80)
    out = []
    for m in months:
        if m < min_month or not ((df["month"] == m) & (df["is_win5"] == 1)).any():
            continue
        tr = df[(df["month"] < m) & df["finish"].notna()].sort_values("race_id")
        te = df[(df["month"] == m) & (df["is_win5"] == 1)].copy()
        if "rank" in variant:
            rel = np.select([tr["finish"] == 1, tr["finish"] == 2, tr["finish"] == 3], [3, 1, 0.5], 0)
            ds = lgb.Dataset(tr[cols], label=np.round(rel * 2).astype(int),
                             group=tr.groupby("race_id", sort=False).size().loc[tr["race_id"].unique()].to_numpy())
            p["label_gain"] = list(range(0, 7))
        elif "reg" in variant:
            trr = tr[tr["behind"].notna()]
            # 秒差を1000mあたりに正規化して距離の影響を除く
            lab = trr["behind"] * 1000 / trr["race_dist"]
            p.update(objective="huber", alpha=1.0)
            ds = lgb.Dataset(trr[cols], label=-lab)
        else:
            ds = lgb.Dataset(tr[cols], label=tr["y"])
        model = lgb.train(p, ds, num_boost_round=rounds)
        raw = model.predict(te[cols], raw_score=True)
        te["raw"] = raw
        out.append(te)
    res = pd.concat(out)
    # レース内ソフトマックス（温度Tは学習期間外で決めず、固定）
    T = 1.0
    if "rank" in variant:
        T = 0.6
    for tok in variant.split("_"):
        if tok.startswith("T"):
            T = float(tok[1:])
    e = np.exp((res["raw"] - res.groupby("race_id")["raw"].transform("max")) / T)
    res["p_model"] = e / e.groupby(res["race_id"]).transform("sum")
    res["p_odds"] = (1 / res["odds"]) / (1 / res["odds"]).groupby(res["race_id"]).transform("sum")
    return res, model, cols


def main():
    cache = Path(sys.argv[1])
    df = load(cache)
    df = add_jockey_rate_expanding(df)
    df["trainer"] = df.get("trainer")
    if "sire" in df.columns:
        df["sire_surf"] = df["sire"].astype(str) + "_" + df["race_surface"].astype(str)
        df = add_group_rate(df, "sire_surf", "sire_surf_rate")
    if "trainer" in df.columns and df["trainer"].notna().any():
        df = add_group_rate(df, "trainer", "trainer_rate")
    days = {json.loads(l)["date"]: json.loads(l) for l in Path("win5_bt_data/win5_days.jsonl").read_text().splitlines()}
    for variant in sys.argv[2:]:
        res, model, cols = run(df, variant)
        for name, col in (("model", "p_model"), ("odds", "p_odds")):
            if name == "odds" and variant != sys.argv[2]:
                continue
            ev = evaluate(res, days, 100, col)
            rm = race_metrics(res, col)
            print(f"[{variant}] {name}: top1={rm['top1']:.3f} top3={rm['top3']:.3f} "
                  f"top5={rm['top5']:.3f} ll={rm['winner_logloss']:.3f} days={len(ev)} "
                  f"hits={int(ev['hit'].sum())} avg_hit={ev['races_hit'].mean():.2f} "
                  f"roi={ev['return'].sum() / ev['cost'].sum():.2f}")
        if variant == sys.argv[-1]:
            imp = pd.Series(model.feature_importance("gain"), index=cols).sort_values(ascending=False)
            print(imp.head(15).round(0).to_string())


if __name__ == "__main__":
    main()
