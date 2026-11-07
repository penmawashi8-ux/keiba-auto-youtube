"""指定日のWIN5を予想する（100点以内）。

最新までの全データで学習（シード違い3モデルの平均）し、対象5レースの勝率を出して
的中確率が最大になる点数配分（Π頭数 ≤ 予算）を選ぶ。人気・オッズは不使用。

使い方: python scripts/win5_bt/predict.py <YYYYMMDD> <out.md> [budget] [config.json]
"""
import json
import math
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import select_features as sf  # noqa: E402
from backtest import best_allocation  # noqa: E402
from features_wide import add_group_rates, build, feature_list  # noqa: E402


def main():
    date, out = sys.argv[1], Path(sys.argv[2])
    budget = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    cfg = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    pdir = Path("win5_bt_data/predict") / date
    rids = json.loads((pdir / "race_ids.json").read_text())
    df = build([Path("win5_bt_data/races"), Path("win5_bt_data/races_all"), pdir])
    df = add_group_rates(df)
    cols = cfg.get("features") or feature_list(df)
    tr = df[(df["date"] < date) & df["finish"].notna()]
    te = df[df["race_id"].isin(rids)].copy()
    raw = np.zeros(len(te))
    for seed in (11, 22, 33):
        params = dict(sf.PARAMS, **cfg.get("params", {}), seed=seed)
        m = lgb.train(params, lgb.Dataset(tr[cols], label=tr["y"]), num_boost_round=cfg.get("rounds", sf.ROUNDS))
        raw += m.predict(te[cols], raw_score=True) / 3
    te["p"] = sf.softmax_by_race(te, raw)
    # 馬名は調教ページの馬名欄から取る（5走表示は父名に空白を含むことがあり切り出しが不正確）
    import gzip
    names = {}
    for rid in rids:
        rec = json.load(gzip.open(pdir / f"{rid}.json.gz", "rt"))
        oik = json.load(gzip.open(pdir / f"{rid}.oikiri.json.gz", "rt"))
        for um, cells in oik.items():
            info = next((t for c, t in cells if c.startswith("Horse_Info")), "")
            names[(rid, int(um))] = info.replace(" 前走", "").strip()
        names[(rid, "title")] = rec["meta"]["name"] + "　" + rec["meta"]["data1"].split("/")[1].strip()
    subs = [te[te["race_id"] == r].sort_values("p", ascending=False) for r in rids]
    ns, prob = best_allocation([s["p"].to_numpy() for s in subs], budget)
    lines = [f"# WIN5 予想 {date[:4]}/{date[4:6]}/{date[6:]}", "",
             f"買い目 {math.prod(ns)}点（モデル上の的中確率 {prob:.1%}）", ""]
    for k, (s, n, rid) in enumerate(zip(subs, ns, rids)):
        lines += [f"## {k+1}. {names[(rid, 'title')]}　→ {n}頭", "",
                  "| 評価 | 馬番 | 馬名 | 勝率 | 調教 | 買い |", "|---|---|---|---|---|---|"]
        for i, (_, h) in enumerate(s.iterrows()):
            if i >= max(n + 3, 5):
                break
            ok = {4: "A", 3: "B", 2: "C", 1: "D"}.get(h["oikiri_rank"], "-")
            lines.append(f"| {i+1} | {int(h['umaban'])} | {names.get((rid, int(h['umaban'])), '')} | {h['p']:.1%} | {ok} | {'◯' if i < n else ''} |")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    te[["race_id", "umaban", "p", "oikiri_rank"]].to_csv(out.with_suffix(".csv"), index=False)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
