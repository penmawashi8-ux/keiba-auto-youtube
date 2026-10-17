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
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from sim_fetch import fetch_race  # noqa: E402

SIM_DIR = Path("sim")
CACHE = SIM_DIR / "gate_cache.json"
FROM_YEAR = 2008


def solve(m: list[list[float]], v: list[float]) -> list[float]:
    """小さな連立一次方程式（ガウスの消去法）"""
    k = len(v)
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(a[r][c]))
        a[c], a[p] = a[p], a[c]
        for r in range(k):
            if r != c and a[c][c]:
                f = a[r][c] / a[c][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [a[i][k] / a[i][i] for i in range(k)]


def fit(pairs: list[tuple[list[float], int]], iters: int = 12) -> tuple[list[float], list[float]]:
    """y = 1(先着) を、特徴の差 x のロジスティック（切片なし）で。ニュートン法。係数と標準誤差を返す
    （同じ馬が何度も出てくるので、実際の誤差はもう少し大きい）"""
    k = len(pairs[0][0])
    w = [0.0] * k
    hess = [[0.0] * k for _ in range(k)]
    for _ in range(iters):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for x, y in pairs:
            p = 1 / (1 + math.exp(-sum(wi * xi for wi, xi in zip(w, x))))
            q = p * (1 - p)
            for i in range(k):
                grad[i] += (y - p) * x[i]
                for j in range(k):
                    hess[i][j] += q * x[i] * x[j]
        step = solve(hess, grad)
        w = [wi + si for wi, si in zip(w, step)]
    se = []
    for i in range(k):
        e = [0.0] * k
        e[i] = 1.0
        se.append(math.sqrt(max(solve(hess, e)[i], 0.0)))
    return w, se


def first_pos(x: dict, field: int) -> float | None:
    """通過順の最初の位置を 0（先頭）〜 1（最後方）に"""
    m = re.match(r"(\d+)", x.get("passing") or "")
    return (int(m.group(1)) - 1) / (field - 1) if m and field > 1 else None


def typical_styles(careers: dict, surface: str = "芝") -> dict:
    """各馬の、レースごとの序盤の位置（0=先頭〜1=最後方）の一覧 {horse_id: {race_id: 位置}}"""
    out = {}
    for hid, rows in careers.items():
        d = {}
        for r in rows:
            m = re.match(r"(\d+)", r.get("passing") or "")
            if m and (r.get("field") or 0) > 1 and r.get("surface") == surface:
                d[r["race_id"]] = (int(m.group(1)) - 1) / (r["field"] - 1)
        if d:
            out[hid] = d
    return out


def style_of(x: dict, race_id: str, field: int, typ: dict, mode: str) -> float | None:
    if mode == "race":                        # そのレースで実際に運んだ位置
        return first_pos(x, field)
    d = typ.get(x["horse_id"], {})            # 普段の脚質（このレース以外の平均。2走以上）
    vals = [v for rid, v in d.items() if rid != race_id]
    return sum(vals) / len(vals) if len(vals) >= 2 else None


def pairs_of(races: list[dict], typ: dict, mode: str) -> list[tuple[list[float], int]]:
    """2頭ずつの比較: 特徴 = [人気（−log オッズ）, ゲート位置(0内〜1外), 脚質(0前〜1後), ゲート×脚質] の差"""
    out = []
    for r in races:
        n = len(r["runners"])
        rs = []
        for x in r["runners"]:
            if not (x.get("pos") and x.get("odds") and x.get("umaban")):
                continue
            st = style_of(x, r.get("race_id", ""), n, typ, mode)
            if st is None:
                continue
            g = (x["umaban"] - 1) / (n - 1)
            rs.append(([-math.log(x["odds"]), g, st, g * st], x["pos"]))
        if len(rs) < 6:
            continue
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                (fx, px), (fz, pz) = rs[i], rs[j]
                out.append(([a - b for a, b in zip(fx, fz)], int(px < pz)))
    return out


def cross_table(races: list[dict], typ: dict) -> list[dict]:
    """枠（内・中・外）× 普段の脚質（前・中団・後方）ごとの勝率・3着内率"""
    cells = {}
    for r in races:
        n = len(r["runners"])
        for x in r["runners"]:
            if not (x.get("pos") and x.get("umaban")):
                continue
            st = style_of(x, r.get("race_id", ""), n, typ, "typical")
            if st is None:
                continue
            g = (x["umaban"] - 1) / (n - 1)
            key = (min(int(g * 3), 2), min(int(st * 3), 2))
            c = cells.setdefault(key, [0, 0, 0])
            c[0] += 1
            c[1] += x["pos"] == 1
            c[2] += x["pos"] <= 3
    return [{"gate": ["内", "中", "外"][g], "style": ["前", "中団", "後方"][s], "runs": c[0],
             "win": c[1] / c[0], "top3": c[2] / c[0]} for (g, s), c in sorted(cells.items())]


def main() -> None:
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
    ent = json.loads((SIM_DIR / "entries.json").read_text(encoding="utf-8"))
    careers = json.loads((SIM_DIR / "horses_cache.json").read_text(encoding="utf-8"))
    venue, dist, surf = ent["venue"], ent["distance"], ent["surface"]
    ids = sorted({r["race_id"] for v in careers.values() for r in v
                  if r.get("venue") == venue and r.get("surface") == surf and r.get("distance") == dist
                  and int(r["race_id"][:4]) >= FROM_YEAR and r.get("date", "") < ent["date"]})
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = [i for i in ids if i not in cache]
    print(f"{venue}{surf}{dist}m（{FROM_YEAR}年〜）{len(ids)}レース / 取得 {len(todo)}（制限 {budget:.0f}分）")
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

    races = [dict(cache[i], race_id=i) for i in ids if i in cache]
    typ = typical_styles(careers, surf)
    n_run = sum(1 for r in races for x in r["runners"] if x.get("pos"))
    n_typ = sum(1 for r in races for x in r["runners"]
                if x.get("pos") and style_of(x, r["race_id"], len(r["runners"]), typ, "typical") is not None)
    print(f"{len(races)}レース・のべ{n_run}頭のうち、普段の脚質がわかる馬 {n_typ}頭（{n_typ / max(n_run, 1):.0%}）")
    labels = ["人気", "ゲート", "脚質", "ゲート×脚質"]
    result = {}
    for mode in ("typical", "race"):
        pairs = pairs_of(races, typ, mode)
        if len(pairs) < 1000:
            print(f"  {mode}: 比較できる組が少なすぎる（{len(pairs)}組）")
            continue
        w, se = fit(pairs)
        result[mode] = {"n_pairs": len(pairs), "coef": dict(zip(labels, w)), "se": dict(zip(labels, se))}
        name = "普段の脚質" if mode == "typical" else "そのレースの位置取り（参考）"
        print(f"  [{name}] {len(pairs):,}組: " + " / ".join(f"{lb} {c:+.3f}（±{e:.3f}）" for lb, c, e in zip(labels, w, se)))
    if "typical" not in result:
        sys.exit("普段の脚質がわかる馬が少なすぎて測れません")
    # 枠番ごとの成績・枠×脚質の成績（表示用）
    by = {w: [0, 0, 0] for w in range(1, 9)}
    for r in races:
        for x in r["runners"]:
            if x.get("waku") and x.get("pos"):
                b = by[x["waku"]]
                b[0] += 1
                b[1] += x["pos"] == 1
                b[2] += x["pos"] <= 3
    c = result["typical"]["coef"]
    out = {"venue": venue, "surface": surf, "distance": dist, "from_year": FROM_YEAR, "n_races": len(races),
           "n_runs": n_run, "n_typical": n_typ, "models": result,
           # sim_real_race.py が使う係数（普段の脚質で測ったもの）。スコアに足す量 =
           #   ゲート×g + 脚質×s + ゲート×脚質×g×s（g: 0内〜1外、s: 0前〜1後。出走馬の平均を引いて使う）
           "gate": c["ゲート"], "style": c["脚質"], "gate_x_style": c["ゲート×脚質"],
           "by_waku": [{"waku": w, "runs": b[0], "win": b[1] / b[0] if b[0] else 0, "top3": b[2] / b[0] if b[0] else 0}
                       for w, b in by.items()],
           "cross": cross_table(races, typ)}
    (SIM_DIR / "gate_effect.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for g in (0.0, 1.0):
        for st in (0.0, 0.5, 1.0):
            print(f"  ゲート{'最内' if g == 0 else '大外'}・脚質{st:.1f}: "
                  f"{c['ゲート'] * g + c['脚質'] * st + c['ゲート×脚質'] * g * st:+.3f}")
    for b in out["cross"]:
        print(f"  {b['gate']}枠×{b['style']}: {b['runs']}走 勝率 {b['win']:.1%} 3着内率 {b['top3']:.1%}")


if __name__ == "__main__":
    main()
