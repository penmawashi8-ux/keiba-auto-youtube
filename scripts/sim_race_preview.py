#!/usr/bin/env python3
"""実際のレース（sim/entries.json）の出走馬で、能力モデルが使えるかを確かめる仮計算。

夢の対決と同じモデル（sim_learn）で、レース当日時点の各馬のスコアを出し、1万回走らせた
勝率・3着内率と、データの足りない馬を一覧にする。動画は作らない（枠順確定後に別途作る）。

入力: sim/entries.json（sim_entries.py）, sim/races.json, sim/horses_cache.json
出力: sim/preview.md, sim/preview.json
"""

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sim_learn as sl
import sim_model as sm
from sim_dream_race import career_stats, lengths

SIM_DIR = Path("sim")
N_RUNS = 10000


def main() -> None:
    ent = json.loads((SIM_DIR / "entries.json").read_text(encoding="utf-8"))
    careers = json.loads((SIM_DIR / "horses_cache.json").read_text(encoding="utf-8"))
    graded = json.loads((SIM_DIR / "races.json").read_text(encoding="utf-8"))
    races = sm.load_races()
    kit = sl.build(races)
    date, dist, venue = ent["date"], ent["distance"], ent["venue"]

    rows = []
    for h in ent["horses"]:
        hid = h["horse_id"]
        f = kit["feats"].of(hid, date, dist, venue)
        n_rel = len([1 for d, *_ in kit["rel"].get(hid, []) if d < date])
        st = career_stats(hid, careers, graded)
        rows.append({**h, "feats": f, "score": kit["pm"].score(f) if f else None, "n_rel": n_rel,
                     "n_career": len(careers.get(hid, [])), "stats": st})
    scored = sorted(r["score"] for r in rows if r["score"] is not None)
    # 2年以内にJRA芝の成績が無い馬はスコアが出ないので、出走馬の下位1割の値で仮に走らせる（注記する）
    fill = scored[max(0, len(scored) // 10 - 1)] if scored else 0.0
    for r in rows:
        r["score_used"] = r["score"] if r["score"] is not None else fill

    rnd = random.Random(20261018)
    wins = {r["horse_id"]: 0 for r in rows}
    top3 = {r["horse_id"]: 0 for r in rows}
    for _ in range(N_RUNS):
        perf = sorted(rows, key=lambda r: -(r["score_used"] - math.log(-math.log(rnd.random() or 1e-12))))
        wins[perf[0]["horse_id"]] += 1
        for r in perf[:3]:
            top3[r["horse_id"]] += 1

    rows.sort(key=lambda r: -wins[r["horse_id"]])
    fi = {n: i for i, n in enumerate(sl.FEATS)}
    md = [f"# {ent['race_name']} 仮計算（{ent['date']} {venue} {ent['surface']}{dist}m {ent['course']}）", "",
          f"- {'枠順確定' if ent['drawn'] else '枠順未確定（登録馬で計算）'}・{len(rows)}頭・{N_RUNS:,}回",
          f"- モデルの検証成績: 2頭比較の正答率 {kit['test']['acc_model']:.1%}（単勝人気 {kit['test']['acc_market']:.1%}）",
          "- 映像・動画はまだ作らない（枠順確定後）", "",
          "| 馬番 | 馬 | 勝率 | 3着内 | スコア | 直近の相手関係 | 距離適性 | 同コース適性 | 計算に使えた走数 | 全成績 | JRA芝 | 騎手 |",
          "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for r in rows:
        f, st = r["feats"], r["stats"]
        sc = f"{r['score']:+.2f}" if r["score"] is not None else "不足"
        cols = ([f"{f[fi[k]]:+.2f}" for k in ("相手関係(最近重視)", "距離適性", "同じ競馬場の適性")] if f
                else ["-", "-", "-"])
        md.append(" | ".join([f"| {r['umaban'] or '-'}", r["horse"], f"{wins[r['horse_id']] / N_RUNS:.1%}",
                              f"{top3[r['horse_id']] / N_RUNS:.1%}", sc, *cols, str(r["n_rel"]), str(r["n_career"]),
                              f"{st['starts']}戦{st['wins']}勝（G1 {st['g1_wins']}勝・勝ち平均{lengths(st['win_margin'])}差）",
                              f"{r['jockey']} |"]))
    lack = [r["horse"] for r in rows if r["score"] is None]
    if lack:
        md += ["", f"※データ不足（2年以内のJRA芝の成績なし）: {'、'.join(lack)} … 出走馬の下位1割のスコアで仮に計算"]
    (SIM_DIR / "preview.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (SIM_DIR / "preview.json").write_text(json.dumps({
        "race": {k: v for k, v in ent.items() if k != "horses"},
        "horses": [{k: v for k, v in r.items() if k != "stats"} | {"stats": r["stats"],
                    "win": wins[r["horse_id"]] / N_RUNS, "top3": top3[r["horse_id"]] / N_RUNS} for r in rows],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
