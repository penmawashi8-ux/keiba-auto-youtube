#!/usr/bin/env python3
"""夢の対決シミュレーション（試作5号）: 複数の手がかりを学習で組み合わせる能力モデル。

sim_model.py の物差し（基準タイム・着差×相手関係のスピード値）を材料に、出走時点で分かる
手がかりから「2頭のどちらが先着するか」を当てるロジスティック回帰を学習する。

■ 手がかり（すべてそのレースより前の情報だけで作る）
  相手関係(最近重視) … 着差×相手関係のスピード値を、半減期180日の重みで平均（直近2年）
  時計(最近重視)     … 時計ベースのスピード値を同様に平均
  2年内ベスト / 前走 / 出走数(log) / 休み明け(年) / 距離適性 / 同じ競馬場の適性
  年齢の伸びしろ     … 同じ馬の中での成績の変化から推定した年齢曲線（3歳春 −0.6秒 → 4歳後半が頂点）
  G1勝数 / 重賞勝数(log) / 勝率 / 3着内率 / 前走のクラス / 過去3走の平均人気(log)
  スピード値は年齢曲線の分を差し引いてから平均する（若い頃の未完成な走りで低く見積もらないため）

■ 学習と検証
  2008〜2018年の G1/G2 の全出走馬の組で学習し、2019〜2025年で検証（単勝人気と比べる）。

■ 夢の対決での使い方
  各馬の「スコア」= 学習した重み × 手がかり。現役中で最も高かった時点（全盛期）を使う。
  その日の事情に当たる手がかり（休み明け・前走のクラス）は全馬同じ値にそろえ、
  距離適性・競馬場の適性は対象レース（東京2400m）に合わせて計算する。
  1回のレース = スコア + ガンベル分布の乱数 で着順を決める（学習した2頭比較の確率と一致する）。
  着差（秒）はスコア差と実際の着差の関係から換算する。

numpy は使わない（プロジェクトルール）。
"""

import math
import statistics

import sim_model as sm

HALF_LIFE_DAYS = 180
LOOKBACK_YEARS = 2
AGE_BINS = [3.0, 3.34, 3.67, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 99.0]
FEATS = ["相手関係(最近重視)", "時計(最近重視)", "2年内ベスト", "前走", "出走数(log)", "休み明け(年)",
         "距離適性", "年齢の伸びしろ", "G1勝数", "重賞勝数(log)", "勝率", "3着内率", "前走のクラス",
         "過去3走の平均人気(log)", "同じ競馬場の適性"]
SITUATIONAL = {"休み明け(年)", "前走のクラス"}   # 夢の対決では全馬同じ値にそろえる手がかり
CLS_RANK = {c: i for i, c in enumerate(sm.CLASSES)}
EPOCHS = 400
LEARNING_RATE = 0.5
L2 = 1e-3


def day_num(s: str) -> float:
    return int(s[:4]) * 365 + int(s[5:7]) * 30.4 + int(s[8:10])


def age_frac(date: str, horse_id: str) -> float | None:
    by = sm.birth_year(horse_id)
    return int(date[:4]) - by + (int(date[5:7]) - 1) / 12 if by else None


def _bin(a: float | None) -> int | None:
    if a is None:
        return None
    for i in range(len(AGE_BINS) - 1):
        if AGE_BINS[i] <= a < AGE_BINS[i + 1]:
            return i
    return None


def fit_age_curve(figs: dict) -> list[float]:
    """同じ馬の中での年齢による変化（4歳0か月 = 0）を学習期間のデータで推定する"""
    by_h = {}
    for h, v in figs.items():
        for d, f, r, _x in v:
            b = _bin(age_frac(d, h))
            if r["year"] in sm.TRAIN_YEARS and b is not None:
                by_h.setdefault(h, []).append((b, f))
    eff = [0.0] * (len(AGE_BINS) - 1)
    for _ in range(30):
        hm = {h: statistics.mean(f - eff[b] for b, f in v) for h, v in by_h.items()}
        sums = [[0.0, 0] for _ in eff]
        for h, v in by_h.items():
            for b, f in v:
                sums[b][0] += f - hm[h]
                sums[b][1] += 1
        eff = [s / n if n else 0.0 for s, n in sums]
        eff = [e - eff[3] for e in eff]
    return eff


class Features:
    def __init__(self, rel: dict, tim: dict, age_eff: list[float]):
        self.rel, self.tim, self.age_eff = rel, tim, age_eff

    def curve(self, date: str, horse_id: str) -> float:
        b = _bin(age_frac(date, horse_id))
        return self.age_eff[b] if b is not None else 0.0

    def _expw(self, pool: list[tuple[str, float]], ref: str) -> float:
        num = den = 0.0
        for d, p in pool:
            w = 0.5 ** ((day_num(ref) - day_num(d)) / HALF_LIFE_DAYS)
            num += w * p
            den += w
        return num / den

    def of(self, horse_id: str, date: str, distance: int, venue: str) -> list[float] | None:
        """date より前の成績から、そのレース（距離・競馬場）に向けた手がかりを作る"""
        lo = f"{int(date[:4]) - LOOKBACK_YEARS}{date[4:]}"
        hr = [(d, f - self.curve(d, horse_id), r) for d, f, r, _x in self.rel.get(horse_id, []) if lo <= d < date]
        if not hr:
            return None
        ht = [(d, f - self.curve(d, horse_id)) for d, f, _r, _x in self.tim.get(horse_id, []) if lo <= d < date]
        allh = [(r, x) for d, _f, r, x in self.rel.get(horse_id, []) if d < date]
        f_rel = self._expw([(d, f) for d, f, _ in hr], date)
        mean_all = statistics.mean(f for _, f, _ in hr)
        near = [f for _, f, r in hr if abs(r["distance"] - distance) <= 200]
        same = [f for _, f, r in hr if r["venue"] == venue]
        pops = [x["pop"] for _r, x in allh[-3:] if x.get("pop")]
        return [
            f_rel,
            self._expw(ht, date) if ht else f_rel,
            max(f for _, f, _ in hr),
            hr[-1][1],
            math.log(1 + len(hr)),
            (day_num(date) - day_num(hr[-1][0])) / 365,
            statistics.mean(near) - mean_all if near else 0.0,
            self.curve(date, horse_id),
            sum(1 for r, x in allh if x["pos"] == 1 and r["cls"] == "G1"),
            math.log(1 + sum(1 for r, x in allh if x["pos"] == 1 and r["cls"] in ("G1", "G2", "G3"))),
            sum(1 for _r, x in allh if x["pos"] == 1) / len(allh),
            sum(1 for _r, x in allh if x["pos"] and x["pos"] <= 3) / len(allh),
            CLS_RANK.get(allh[-1][0]["cls"], 5),
            math.log(statistics.mean(pops)) if pops else math.log(8),
            statistics.mean(same) - mean_all if same else 0.0,
        ]


def race_rows(races: list[dict], feats: Features, years) -> list[list[tuple[dict, list[float]]]]:
    out = []
    for r in races:
        if r["year"] not in years or not r.get("graded"):
            continue
        rows = []
        for x in r["runners"]:
            if x["pos"] is None or not x["horse_id"]:
                continue
            f = feats.of(x["horse_id"], r["date"], r["distance"], r["venue"])
            if f:
                rows.append((x, f))
        out.append((r, rows))
    return out


class PairModel:
    """2頭比較のロジスティック回帰（手がかりは学習期間の平均・標準偏差で標準化）"""

    def __init__(self, train: list):
        allf = [f for _r, rows in train for _x, f in rows]
        self.mu = [statistics.mean(c) for c in zip(*allf)]
        self.sd = [statistics.pstdev(c) or 1.0 for c in zip(*allf)]
        pairs = self.pairs(train)
        w = [0.0] * len(FEATS)
        for _ in range(EPOCHS):
            g = [0.0] * len(w)
            for d, y, *_rest in pairs:
                p = 1 / (1 + math.exp(-sum(wi * di for wi, di in zip(w, d))))
                for k in range(len(w)):
                    g[k] += (p - y) * d[k]
            w = [wi - LEARNING_RATE * (gk / len(pairs) + L2 * wi) for wi, gk in zip(w, g)]
        self.w = w

    def z(self, f: list[float]) -> list[float]:
        return [(v - m) / s for v, m, s in zip(f, self.mu, self.sd)]

    def score(self, f: list[float]) -> float:
        return sum(wi * zi for wi, zi in zip(self.w, self.z(f)))

    def pairs(self, data: list) -> list[tuple]:
        out = []
        for r, rows in data:
            zs = [(x, self.z(f)) for x, f in rows]
            for i in range(len(zs)):
                for j in range(i + 1, len(zs)):
                    (a, fa), (b, fb) = zs[i], zs[j]
                    if a["pos"] == b["pos"]:
                        continue
                    margin = None
                    if a.get("time") and b.get("time"):
                        margin = min(max(b["time"] - a["time"], -4.0), 4.0) * 2400 / r["distance"]
                    out.append(([u - v for u, v in zip(fa, fb)], 1 if a["pos"] < b["pos"] else 0,
                                a.get("pop"), b.get("pop"), margin))
        return out

    def evaluate(self, data: list) -> dict:
        ok = mk = n = 0
        ll = 0.0
        sxy = sxx = 0.0
        win_hit = fav_hit = n_full = 0
        for d, y, pa, pb, margin in self.pairs(data):
            s = sum(wi * di for wi, di in zip(self.w, d))
            p = min(max(1 / (1 + math.exp(-s)), 1e-6), 1 - 1e-6)
            n += 1
            ok += (s > 0) == (y == 1)
            ll -= math.log(p if y else 1 - p)
            if pa and pb:
                mk += (pa < pb) == (y == 1)
            if margin is not None:
                sxy += s * margin
                sxx += s * s
        for r, rows in data:
            finishers = [x for x in r["runners"] if x["pos"] is not None]
            if len(rows) >= 2 and len(rows) == len(finishers):
                n_full += 1
                win_hit += max(rows, key=lambda t: self.score(t[1]))[0]["pos"] == 1
                fav = min((t for t in rows if t[0].get("pop")), key=lambda t: t[0]["pop"], default=None)
                fav_hit += bool(fav and fav[0]["pos"] == 1)
        return {"pairs": n, "acc_model": ok / n, "acc_market": mk / n, "logloss": ll / n,
                "sec_per_score": sxy / sxx if sxx else 1.0, "n_full": n_full,
                "win_model": win_hit / n_full if n_full else 0.0, "win_market": fav_hit / n_full if n_full else 0.0}


def build_features(races: list[dict]) -> dict:
    """物差し（相対スコア・タイム）と手がかりだけを作る。学習済みの PairModel を別の馬場（ダート）に流用するとき用"""
    model = sm.ParModel(races, "race")
    beta = sm.fit_style_effect(races, sm.speed_figures(races, model))
    rel = sm.relative_figures(races, model, beta, 0.0)
    tim = sm.speed_figures(races, model, beta)
    age_eff = fit_age_curve(rel)
    return {"model": model, "beta": beta, "rel": rel, "tim": tim, "age_eff": age_eff,
            "feats": Features(rel, tim, age_eff)}


def build(races: list[dict], log=print) -> dict:
    """物差しを作り、学習・検証して、夢の対決に使う部品一式を返す"""
    model = sm.ParModel(races, "race")
    beta = sm.fit_style_effect(races, sm.speed_figures(races, model))
    rel = sm.relative_figures(races, model, beta, 0.0)
    tim = sm.speed_figures(races, model, beta)
    age_eff = fit_age_curve(rel)
    feats = Features(rel, tim, age_eff)
    train = race_rows(races, feats, sm.TRAIN_YEARS)
    test = race_rows(races, feats, sm.TEST_YEARS)
    pm = PairModel(train)
    ev_train, ev_test = pm.evaluate(train), pm.evaluate(test)
    log(f"学習 {ev_train['acc_model']:.2%}（人気 {ev_train['acc_market']:.2%}） / "
        f"検証 {ev_test['acc_model']:.2%}（人気 {ev_test['acc_market']:.2%}）")
    situ = {name: statistics.median(f[i] for _r, rows in train for _x, f in rows)
            for i, name in enumerate(FEATS) if name in SITUATIONAL}
    situ["前走のクラス"] = float(CLS_RANK["G1"])
    # 「平均的なG1勝ち馬」のスコア（代表レースのタイムの基準）
    g1_win = [pm.score(f) for r, rows in train for x, f in rows if r["cls"] == "G1" and x["pos"] == 1]
    return {"model": model, "beta": beta, "rel": rel, "tim": tim, "age_eff": age_eff, "feats": feats,
            "pm": pm, "train": ev_train, "test": ev_test, "situational": situ,
            "g1_winner_score": statistics.mean(g1_win)}


def peak_score(kit: dict, horse_id: str, distance: int, venue: str) -> tuple[float, str, list[float]] | None:
    """全盛期のスコア: 各出走の翌日時点の手がかりでスコアを出し、最大の時点を選ぶ"""
    hist = kit["rel"].get(horse_id, [])
    best = None
    for d, *_rest in hist:
        f = kit["feats"].of(horse_id, d + "~", distance, venue)   # "~" はその日のレースまでを含める印
        if not f:
            continue
        for i, name in enumerate(FEATS):
            if name in kit["situational"]:
                f[i] = kit["situational"][name]
        s = kit["pm"].score(f)
        if best is None or s > best[0]:
            best = (s, d, f)
    return best


FAMOUS_RACES = [("ジャパンカップ", 2023), ("有馬記念", 2006), ("天皇賞(秋)", 2020), ("有馬記念", 2013),
                ("宝塚記念", 2022), ("ジャパンカップ", 2020), ("天皇賞(秋)", 2023), ("日本ダービー", 2005)]


def famous_checks(kit: dict, races: list[dict]) -> list[dict]:
    """有名レースについて、レース前の情報だけで出したモデルの評価順と実際の着順を並べる"""
    out = []
    for name, year in FAMOUS_RACES:
        r = next((r for r in races if r.get("graded") and r["year"] == year and name in r["race_name"]), None)
        if not r:
            continue
        rows = []
        for x in r["runners"]:
            if x["pos"] is None:
                continue
            f = kit["feats"].of(x["horse_id"], r["date"], r["distance"], r["venue"])
            if f:
                rows.append((x, kit["pm"].score(f)))
        if len(rows) < 3:
            continue
        tot = sum(math.exp(s) for _, s in rows)
        rows.sort(key=lambda t: -t[1])
        out.append({"race": f"{year}年 {r['race_name']}",
                    "top": [(x["horse"], math.exp(s) / tot, x["pos"], x.get("pop")) for x, s in rows[:3]]})
    return out
