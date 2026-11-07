"""1年分のWIN5を「その月より前のデータだけで学習→予想→答え合わせ」した振り返りレポートを作る。

使い方: python scripts/win5_bt/review_report.py <wide.pkl> <out.md> [features.json] [config.json]
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import select_features as sf  # noqa: E402
from backtest import best_allocation, parse_yen  # noqa: E402
from features_wide import feature_list  # noqa: E402


def main():
    df = pd.read_pickle(sys.argv[1])
    out = Path(sys.argv[2])
    cols = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 and sys.argv[3] != "-" else feature_list(df)
    cfg = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    params = dict(sf.PARAMS, **cfg.get("params", {}))
    rounds = cfg.get("rounds", sf.ROUNDS)
    days = {json.loads(l)["date"]: json.loads(l) for l in Path("win5_bt_data/win5_days.jsonl").read_text().splitlines()}
    months = sorted({d[:6] for d in days})
    res, _ = sf.fit_predict(df, cols, months, params=params, rounds=rounds)
    res["p_odds"] = (1 / res["odds"]) / (1 / res["odds"]).groupby(res["race_id"]).transform("sum")
    names = df.set_index(["race_id", "umaban"])["name"] if "name" in df else None

    lines = ["# WIN5 振り返り（ウォークフォワード）", "",
             "各月の予想は、その月より前のデータだけで学習したモデルで行っています。人気・オッズは特徴量に不使用。", ""]
    rows = []
    detail = []
    for ds, info in sorted(days.items()):
        sub = [res[res["race_id"] == r].sort_values("p", ascending=False) for r in info["race_ids"]]
        if any(len(s) == 0 or not (s["finish"] == 1).any() for s in sub):
            continue
        ns, pr = best_allocation([s["p"].to_numpy() for s in sub], 100)
        ns_o, _ = best_allocation([s["p_odds"].fillna(0).to_numpy() for s in sub], 100)
        hits, hits_o = [], []
        dl = [f"## {ds[:4]}/{ds[4:6]}/{ds[6:]}　配当 {info.get('payout')}　{math.prod(ns)}点", "",
              "| R | 買い目（評価順） | 勝ち馬 | 勝ち馬のモデル順位 | 結果 |", "|---|---|---|---|---|"]
        for k, (s, n, no) in enumerate(zip(sub, ns, ns_o)):
            picks = s.head(n)["umaban"].astype(int).tolist()
            w = s[s["finish"] == 1].iloc[0]
            wrank = int((s["p"] > w["p"]).sum()) + 1
            hit = int(w["umaban"]) in picks
            hits.append(hit)
            so = s.sort_values("p_odds", ascending=False)
            hits_o.append(int(w["umaban"]) in so.head(no)["umaban"].astype(int).tolist())
            wname = names.get((w["race_id"], w["umaban"]), "") if names is not None else ""
            dl.append(f"| {k+1} | {' '.join(map(str, picks))} | {int(w['umaban'])} {wname}（{int(w['pop']) if pd.notna(w['pop']) else '-'}番人気） "
                      f"| {wrank}位 | {'○' if hit else '×'} |")
        win = all(hits)
        payout = parse_yen(info.get("payout")) or 0
        rows.append({"date": ds, "points": math.prod(ns), "races_hit": sum(hits), "hit": win,
                     "payout": payout, "return": payout if win else 0,
                     "odds_points": math.prod(ns_o), "odds_hit": all(hits_o), "odds_races_hit": sum(hits_o)})
        dl += ["", f"→ {sum(hits)}/5レース的中　{'**WIN5的中！**' if win else '不的中'}", ""]
        detail += dl
    ev = pd.DataFrame(rows)
    cost = int(ev["points"].sum() * 100)
    ret = int(ev["return"].sum())
    ocost = int(ev["odds_points"].sum() * 100)
    oret = int((ev["payout"] * ev["odds_hit"]).sum())
    lines += ["## まとめ", "",
              f"- 対象: {len(ev)}回（{ev['date'].min()}〜{ev['date'].max()}）",
              f"- **モデル**: WIN5的中 {int(ev['hit'].sum())}回　平均 {ev['races_hit'].mean():.2f}/5レース的中　"
              f"投資 {cost:,}円　払戻 {ret:,}円　回収率 {ret / cost:.0%}",
              f"- 比較（単勝オッズ順で同じ買い方）: WIN5的中 {int(ev['odds_hit'].sum())}回　平均 {ev['odds_races_hit'].mean():.2f}/5　"
              f"投資 {ocost:,}円　払戻 {oret:,}円　回収率 {oret / ocost:.0%}",
              f"- 5レース中4レース的中: {int((ev['races_hit'] == 4).sum())}回", ""]
    by_m = ev.assign(m=ev["date"].str[:6]).groupby("m").agg(回数=("hit", "size"), 的中=("hit", "sum"),
                                                            平均的中レース=("races_hit", "mean"))
    lines += ["### 月別", "", by_m.round(2).to_markdown(), ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines + detail))
    ev.to_csv(out.with_suffix(".csv"), index=False)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
