#!/usr/bin/env python3
"""枠順（馬番）の有利不利を、同じコースの過去のレースから測る。

方法: 同じレースの2頭ずつを比べ、「どちらが先着したか」を
      ・単勝オッズの差（＝その時点の実力の評価。騎手や調子も含む）
      ・ゲートの位置の差（馬番を 0=最内 〜 1=大外 に直したもの）
  の2つで説明するロジスティック回帰を当てはめる。ゲートの係数が「大外と最内の差」で、
  能力モデル（2頭比較のロジスティック）のスコアと同じ単位になるので、そのままスコアに足せる。

対象: 出走馬・対戦相手の成績に出てくる、同じ競馬場・同じ距離の芝のレース（全クラス、2008年以降）
入力: sim/entries.json, sim/horses_cache.json
出力: sim/gate_effect.json {coef, se, n_races, n_pairs, by_waku: [{waku, runs, win, top3}], recent: {...}}
      結果ページは sim/gate_cache.json にキャッシュ（馬番・枠番・オッズ入り）

使い方: python scripts/sim_gate_effect.py [制限時間(分)]
"""

import json
import math
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from sim_fetch import fetch_race  # noqa: E402

SIM_DIR = Path("sim")
CACHE = SIM_DIR / "gate_cache.json"
FROM_YEAR = 2008


def fit(pairs: list[tuple[float, float, int]], epochs: int = 300) -> tuple[float, float, float]:
    """y = 1(先着) を、オッズ差 a と ゲート差 g のロジスティック（切片なし）で。係数と標準誤差を返す"""
    wa = wg = 0.0
    lr = 0.5
    n = len(pairs)
    for _ in range(epochs):
        ga = gg = 0.0
        for a, g, y in pairs:
            p = 1 / (1 + math.exp(-(wa * a + wg * g)))
            ga += (y - p) * a
            gg += (y - p) * g
        wa += lr * ga / n
        wg += lr * gg / n
    # 標準誤差（フィッシャー情報量の逆行列。2頭比較は同じ馬が何度も出てくるので実際はもう少し大きい）
    iaa = iag = igg = 0.0
    for a, g, _y in pairs:
        p = 1 / (1 + math.exp(-(wa * a + wg * g)))
        w = p * (1 - p)
        iaa += w * a * a
        iag += w * a * g
        igg += w * g * g
    det = iaa * igg - iag * iag
    se = math.sqrt(iaa / det) if det > 0 else float("nan")
    return wa, wg, se


def pairs_of(races: list[dict]) -> list[tuple[float, float, int]]:
    out = []
    for r in races:
        rs = [x for x in r["runners"] if x.get("pos") and x.get("odds") and x.get("umaban")]
        n = len(r["runners"])
        if len(rs) < 6:
            continue
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                x, z = rs[i], rs[j]
                a = -(math.log(x["odds"]) - math.log(z["odds"]))      # オッズが低い（人気）ほど正
                g = (x["umaban"] - z["umaban"]) / (n - 1)               # 外の馬ほど正
                out.append((a, g, int(x["pos"] < z["pos"])))
    return out


def main() -> None:
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
    ent = json.loads((SIM_DIR / "entries.json").read_text(encoding="utf-8"))
    careers = json.loads((SIM_DIR / "horses_cache.json").read_text(encoding="utf-8"))
    venue, dist = ent["venue"], ent["distance"]
    ids = sorted({r["race_id"] for v in careers.values() for r in v
                  if r.get("venue") == venue and r.get("surface") == "芝" and r.get("distance") == dist
                  and int(r["race_id"][:4]) >= FROM_YEAR and r.get("date", "") < ent["date"]})
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = [i for i in ids if i not in cache]
    print(f"{venue}芝{dist}m（{FROM_YEAR}年〜）{len(ids)}レース / 取得 {len(todo)}（制限 {budget:.0f}分）")
    session = requests.Session()
    t0 = time.time()
    for k, rid in enumerate(todo, 1):
        if time.time() - t0 > budget * 60:
            print("制限時間に達したので中断（続きは次回）")
            break
        try:
            res = fetch_race(session, rid)
        except requests.RequestException as e:
            print(f"  {rid}: 取得失敗 {e}")
            continue
        if res and any(x.get("umaban") for x in res["runners"]):
            cache[rid] = res
        if k % 50 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {k}/{len(todo)}")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    races = [cache[i] for i in ids if i in cache]
    pairs = pairs_of(races)
    if len(pairs) < 1000:
        sys.exit(f"比較できる組が少なすぎます（{len(pairs)}組）")
    wa, wg, se = fit(pairs)
    # 改修後の京都（2023年4月〜）だけでも測る（参考）
    recent = [cache[i] for i in ids if i in cache and i[:4] >= "2023"]
    rp = pairs_of(recent)
    rwa, rwg, rse = fit(rp) if len(rp) > 500 else (float("nan"),) * 3
    # 枠番ごとの成績（表示用）
    by = {w: [0, 0, 0] for w in range(1, 9)}
    for r in races:
        for x in r["runners"]:
            if x.get("waku") and x.get("pos"):
                b = by[x["waku"]]
                b[0] += 1
                b[1] += x["pos"] == 1
                b[2] += x["pos"] <= 3
    out = {"venue": venue, "distance": dist, "from_year": FROM_YEAR, "n_races": len(races), "n_pairs": len(pairs),
           "coef": wg, "se": se, "odds_coef": wa,
           "recent": {"n_races": len(recent), "coef": rwg, "se": rse},
           "by_waku": [{"waku": w, "runs": b[0], "win": b[1] / b[0] if b[0] else 0, "top3": b[2] / b[0] if b[0] else 0}
                       for w, b in by.items()]}
    (SIM_DIR / "gate_effect.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(races)}レース・{len(pairs):,}組: ゲート係数 {wg:+.3f}（±{se:.3f}）"
          f"＝大外は最内より、2頭比較で先着する確率が {1 / (1 + math.exp(-wg)) - 0.5:+.1%}")
    print(f"  改修後（2023年〜）{len(recent)}レース: {rwg:+.3f}（±{rse:.3f}）")
    for b in out["by_waku"]:
        print(f"  {b['waku']}枠: {b['runs']}走 勝率 {b['win']:.1%} 3着内率 {b['top3']:.1%}")


if __name__ == "__main__":
    main()
