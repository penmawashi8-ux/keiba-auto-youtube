#!/usr/bin/env python3
"""夢の対決シミュレーションの能力推定モデルとバックテスト。

データ: sim/races.json（sim_fetch.py が集めた JRA芝1600〜3200m G1/G2 の全出走馬成績）

■ スピード値（1走ごとの能力の物差し。単位は「2400m換算の秒」）
  基準タイム = 平均的なG1勝ち馬が、そのコース・馬場・年・ペースで走ったときのタイム
  スピード値 = (基準タイム − その馬のタイム) × 2400 / 距離 + 定量との斤量差の補正
              − 展開の有利不利（脚質とペースの噛み合い。効きの大きさは過去データから推定）
  基準タイムは勝ち馬のタイムを
      コース(場・距離) + 馬場 + 年 + ペース + 格(G2) + 3歳限定 + 牝馬限定
  で回帰して求める。格や限定戦の項は回帰を歪ませないためだけに使い、スピード値は
  すべて「古馬混合G1」の物差しで測る（3歳春の走りは未完成なぶん低く出る）。
  年の項は隣接年の差に罰則をかけて滑らかにする（馬場の高速化を表す）。

■ ペース補正の候補（バックテストで選ぶ）
  none : 補正しない
  race : レース全体の前半600m通過タイム（逃げ馬のペース）で補正
  own  : 各馬自身の前半（走破タイム − 上がり3F）で補正。大逃げがいたレースで後続を正しく扱える

■ 能力値（ある時点での馬の強さ）
  直近 W 年以内（距離が対象レース±TOL のレースに限ることもできる）のスピード値の上位 N 走を、
  出走数が少ない馬ほど全体平均 M0 に寄せて平均する
      能力値 = (上位N走の合計 + K × M0) / (使った走数 + K)

■ バックテスト
  各重賞について「そのレースより前の成績だけ」で全出走馬の能力値を出し、実際の着順と比べる。
  2頭ずつの組で先着を当てた割合（2頭比較の正答率）を、同じ組の単勝人気の正答率と比べる。
  2008〜2018年で設定を選び、2019〜2025年で検証する（選んだ年で検証しない）。
  調子の波 σ は「2頭比較の勝率予測」の対数損失が最小になる値に合わせる。

numpy は使わない（プロジェクトルール）。
"""

import bisect
import json
import math
import statistics
from pathlib import Path

SIM_DIR = Path("sim")
REF_YEAR = 2023
GOINGS = ["良", "稍", "重", "不"]
SEC_PER_KG = 0.15 * 1.2       # 斤量1kgあたりの秒（2400m換算）
YEAR_SMOOTH = 15.0
TRAIN_YEARS = range(2008, 2019)
TEST_YEARS = range(2019, 2026)
PACE_OPTIONS = ["none", "race", "own"]
GRID_N = [1, 2, 3, 4, 6]
GRID_W = [1, 2, 99]
GRID_K = [0.0, 1.0, 3.0]
GRID_TOL = [9999, 600, 400]   # 能力値に使うレースの距離の幅（9999 = 制限なし）


# ---------------------------------------------------------------------------
# 前処理
# ---------------------------------------------------------------------------
def front600(distance: int, cum: list[float]) -> float | None:
    """スタートから600m地点の通過タイム（距離が200で割り切れないと最初のラップが100m）"""
    if not cum:
        return None
    first = 100 if distance % 200 == 100 else 200
    marks = [first + 200 * i for i in range(len(cum))]
    for (m0, t0), (m1, t1) in zip(zip(marks, cum), zip(marks[1:], cum[1:])):
        if m0 <= 600 <= m1:
            return t0 + (t1 - t0) * (600 - m0) / (m1 - m0)
    return None


def first_pass_ratio(passing: str, field: int) -> float | None:
    try:
        first = int(passing.split("-")[0])
    except (ValueError, AttributeError):
        return None
    return min(max((first - 1) / max(field - 1, 1), 0.0), 1.0)


def wfa_weight(year: int, age: int | None, sex: str, age3_race: bool) -> float:
    """定量（馬齢・性別に応じた通常の斤量）。2023年から古馬の基準が1kg増えた"""
    base = 58.0 if year >= 2023 else 57.0
    if age3_race:
        base = 57.0
    elif age == 3:
        base -= 2.0
    if sex == "牝":
        base -= 2.0
    return base


def course_key(r: dict) -> str:
    return f"{r['venue']}{r['distance']}"


def load_races(path: Path = SIM_DIR / "races.json") -> list[dict]:
    races = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in races:
        if r["age2"] or not r.get("date"):
            continue
        field = len(r["runners"])
        r["front"] = front600(r["distance"], r.get("pace_cum") or [])
        for x in r["runners"]:
            x["early"] = x["time"] - x["last3f"] if x.get("time") and x.get("last3f") else None
            x["style"] = first_pass_ratio(x.get("passing", ""), field)
        out.append(r)
    out.sort(key=lambda r: r["date"])
    return out


# ---------------------------------------------------------------------------
# 基準タイムの回帰
# ---------------------------------------------------------------------------
def solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        piv = m[c][c] or 1e-12
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c] / piv
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / (m[i][i] or 1e-12) for i in range(n)]


class ParModel:
    """平均的なG1勝ち馬のタイムを、コース・馬場・年・ペースから予測する"""

    def __init__(self, races: list[dict], pace: str):
        self.pace = pace
        rows = []
        for r in races:
            win = next((x for x in r["runners"] if x["pos"] == 1 and x.get("time")), None)
            if win:
                rows.append((r, win))
        # ペースの基準（コースごとの平均）
        self.pace_mean = {}
        for r, win in rows:
            v = self._pace_value(r, win)
            if v is not None:
                self.pace_mean.setdefault(course_key(r), []).append(v)
        self.pace_mean = {c: statistics.mean(v) for c, v in self.pace_mean.items()}
        self.courses = sorted({course_key(r) for r, _ in rows})
        self.years = sorted({r["year"] for r, _ in rows} - {REF_YEAR})
        self.feats = (["c:" + c for c in self.courses] + ["g:" + g for g in GOINGS[1:]]
                      + [f"y:{y}" for y in self.years] + ["pace", "G2", "3歳限定", "牝馬限定"])
        self.n_rows = len(rows)
        idx = {f: i for i, f in enumerate(self.feats)}
        k = len(self.feats)
        ata = [[0.0] * k for _ in range(k)]
        atb = [0.0] * k
        for r, win in rows:
            xi = {idx["c:" + course_key(r)]: 1.0}
            if r["going"] in GOINGS[1:]:
                xi[idx["g:" + r["going"]]] = 1.0
            if r["year"] != REF_YEAR:
                xi[idx[f"y:{r['year']}"]] = 1.0
            xi[idx["pace"]] = self.pace_dev(r, win)
            xi[idx["G2"]] = float(r["grade"] == "G2")
            xi[idx["3歳限定"]] = float(r["age3"])
            xi[idx["牝馬限定"]] = float(r["mare"])
            for i, v in xi.items():
                atb[i] += v * win["time"]
                for j, w in xi.items():
                    ata[i][j] += v * w
        for i in range(k):
            ata[i][i] += 1e-3
        ypos = {y: idx[f"y:{y}"] for y in self.years}
        all_years = sorted(self.years + [REF_YEAR])
        for y0, y1 in zip(all_years, all_years[1:]):
            i, j = ypos.get(y0), ypos.get(y1)
            for a in (i, j):
                if a is not None:
                    ata[a][a] += YEAR_SMOOTH
            if i is not None and j is not None:
                ata[i][j] -= YEAR_SMOOTH
                ata[j][i] -= YEAR_SMOOTH
        self.coef = dict(zip(self.feats, solve(ata, atb)))

    def _pace_value(self, r: dict, x: dict) -> float | None:
        if self.pace == "race":
            return r["front"]
        if self.pace == "own":
            return x["early"] / (r["distance"] - 600) * 600 if x["early"] else None
        return None

    def pace_dev(self, r: dict, x: dict) -> float:
        v = self._pace_value(r, x)
        m = self.pace_mean.get(course_key(r))
        return v - m if v is not None and m is not None else 0.0

    def par(self, r: dict, x: dict) -> float:
        """古馬混合G1の物差しでの基準タイム（格・限定戦の項は使わない）"""
        c = self.coef
        return (c["c:" + course_key(r)] + c.get("g:" + r["going"], 0.0) + c.get(f"y:{r['year']}", 0.0)
                + c["pace"] * self.pace_dev(r, x))


def course_front_means(races: list[dict]) -> dict:
    fronts = {}
    for r in races:
        if r["front"] is not None:
            fronts.setdefault(course_key(r), []).append(r["front"])
    return {c: statistics.mean(v) for c, v in fronts.items()}


def speed_figures(races: list[dict], model: ParModel, beta: float = 0.0) -> dict:
    """馬ごとのスピード値の時系列 {horse_id: [(date, fig, race, runner), ...]}

    beta が 0 でなければ「展開の有利不利」（脚質とペースの噛み合い）の分を差し引く。
    """
    fmean = course_front_means(races)
    figs = {}
    for r in races:
        fast = fmean[course_key(r)] - r["front"] if r["front"] is not None else 0.0
        for x in r["runners"]:
            if not x.get("time") or not x["horse_id"] or x["pos"] is None:
                continue
            fig = (model.par(r, x) - x["time"]) * 2400 / r["distance"]
            if beta and x.get("style") is not None:
                fig -= beta * (x["style"] - 0.5) * fast
            if x.get("weight") and x.get("age"):
                fig += (x["weight"] - wfa_weight(r["year"], x["age"], x["sex"], r["age3"])) * SEC_PER_KG
            # 出走馬の辞書には書き込まない（ペース補正の候補ごとに値が違うため）
            figs.setdefault(x["horse_id"], []).append((r["date"], fig, r, x))
    for v in figs.values():
        v.sort(key=lambda t: t[0])
    return figs


# ---------------------------------------------------------------------------
# 能力値
# ---------------------------------------------------------------------------
def rating(history: list[tuple], before: str | None, n: int, w: int, k: float, m0: float,
           dist: int | None = None, tol: int = 9999) -> tuple[float, int] | None:
    """date < before（before=None なら全期間）の直近 w 年の成績から能力値を出す。

    dist と tol を渡すと、距離が dist±tol のレースだけを使う（該当が無ければ全距離を使う）。
    """
    dates = [h[0] for h in history]
    hi = bisect.bisect_left(dates, before) if before else len(history)
    lo = 0
    if w < 99 and before:
        y, rest = int(before[:4]) - w, before[4:]
        lo = bisect.bisect_left(dates, f"{y}{rest}")
    pool = history[lo:hi]
    if dist is not None and tol < 9999:
        near = [h for h in pool if abs(h[2]["distance"] - dist) <= tol]
        pool = near or pool
    vals = sorted((h[1] for h in pool), reverse=True)[:n]
    if not vals:
        return None
    return (sum(vals) + k * m0) / (len(vals) + k), len(vals)


def phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def backtest(races: list[dict], figs: dict, n: int, w: int, k: float, m0: float, years,
             tol: int = 9999) -> dict:
    """years のレースについて、2頭比較の正答率（モデル・単勝人気）と比較用の差分リストを返す"""
    pairs_model = pairs_market = pairs = 0
    diffs = []          # (能力値の差, 先着したら1)
    win_hit = fav_hit = n_full = 0
    for r in races:
        if r["year"] not in years:
            continue
        rated = []
        for x in r["runners"]:
            if x["pos"] is None or not x["horse_id"]:
                continue
            rt = rating(figs.get(x["horse_id"], []), r["date"], n, w, k, m0, r["distance"], tol)
            if rt:
                rated.append((x, rt[0]))
        finishers = [x for x in r["runners"] if x["pos"] is not None]
        if len(rated) >= 2 and len(rated) == len(finishers):
            n_full += 1
            win_hit += max(rated, key=lambda t: t[1])[0]["pos"] == 1
            fav = min((t for t in rated if t[0].get("pop")), key=lambda t: t[0]["pop"], default=None)
            fav_hit += bool(fav and fav[0]["pos"] == 1)
        for i in range(len(rated)):
            for j in range(i + 1, len(rated)):
                (a, ra), (b, rb) = rated[i], rated[j]
                if a["pos"] == b["pos"] or ra == rb:
                    continue
                a_first = a["pos"] < b["pos"]
                pairs += 1
                pairs_model += (ra > rb) == a_first
                if a.get("pop") and b.get("pop"):
                    pairs_market += (a["pop"] < b["pop"]) == a_first
                diffs.append((ra - rb, 1 if a_first else 0))
    return {"pairs": pairs, "acc_model": pairs_model / pairs if pairs else 0.0,
            "acc_market": pairs_market / pairs if pairs else 0.0,
            "n_full": n_full, "win_model": win_hit / n_full if n_full else 0.0,
            "win_market": fav_hit / n_full if n_full else 0.0, "diffs": diffs}


def fit_sigma(diffs: list[tuple[float, int]]) -> tuple[float, float]:
    """P(先着) = Φ(差 / s) の対数損失が最小になる s を探し、1走ごとの調子の波 σ = s/√2 を返す"""
    def loss(s):
        tot = 0.0
        for d, y in diffs:
            p = min(max(phi(d / s), 1e-6), 1 - 1e-6)
            tot -= math.log(p if y else 1 - p)
        return tot / len(diffs)
    best = min((loss(s), s) for s in [0.5 + 0.1 * i for i in range(36)])
    return best[1] / math.sqrt(2), best[0]


def select_model(races: list[dict], log=print) -> dict:
    """ペース補正・能力値の設定をバックテストで選ぶ"""
    results = []
    cache = {}
    for pace in PACE_OPTIONS:
        model = ParModel(races, pace)
        # 展開の効きを推定してから、その分を差し引いたスピード値を作る
        beta = fit_style_effect(races, speed_figures(races, model))
        figs = speed_figures(races, model, beta)
        m0 = statistics.median(f for v in figs.values() for _, f, _, _ in v)
        cache[pace] = (model, figs, m0, beta)
        for n in GRID_N:
            for w in GRID_W:
                for k in GRID_K:
                    for tol in GRID_TOL:
                        bt = backtest(races, figs, n, w, k, m0, TRAIN_YEARS, tol)
                        results.append((bt["acc_model"], pace, n, w, k, tol, bt["pairs"]))
        log(f"  ペース補正={pace}: 展開の効き {beta:+.3f} / 設定 {len(results)}件まで評価")
    results.sort(reverse=True)
    log("学習期間（2008〜2018）の2頭比較の正答率 上位10設定:")
    for acc, pace, n, w, k, tol, p in results[:10]:
        log(f"  {acc:.3%}  ペース補正={pace} 上位{n}走 直近{w if w < 99 else '全'}年 縮小K={k} "
            f"距離±{tol if tol < 9999 else '制限なし'}  ({p}組)")
    best_by_pace = {}
    for acc, pace, n, w, k, tol, p in results:
        best_by_pace.setdefault(pace, (acc, n, w, k, tol))
    best_by_tol = {}
    for acc, pace, n, w, k, tol, p in results:
        best_by_tol.setdefault(tol, (acc, pace, n, w, k))
    _, pace, n, w, k, tol, _ = results[0]
    model, figs, m0, style_beta = cache[pace]
    train = backtest(races, figs, n, w, k, m0, TRAIN_YEARS, tol)
    sigma, train_loss = fit_sigma(train["diffs"])
    test = backtest(races, figs, n, w, k, m0, TEST_YEARS, tol)
    s = sigma * math.sqrt(2)
    test_loss = -sum(math.log(min(max(phi(d / s) if y else 1 - phi(d / s), 1e-6), 1 - 1e-6))
                     for d, y in test["diffs"]) / max(len(test["diffs"]), 1)
    return {"pace": pace, "n": n, "w": w, "k": k, "tol": tol, "m0": m0, "sigma": sigma, "model": model,
            "figs": figs, "train": train, "test": test, "train_loss": train_loss, "test_loss": test_loss,
            "best_by_pace": best_by_pace, "best_by_tol": best_by_tol, "style_beta": style_beta,
            "top": results[:10]}


def fit_style_effect(races, figs) -> float:
    """展開の効き: 同じ馬の中で (スピード値 − その馬の平均) を (脚質−0.5)×(ハイペース度) に回帰した傾き。

    ハイペース度 = コース平均の前半600m − そのレースの前半600m（正ならハイペース）。
    ハイペースで差し・追込が有利なら正になる。馬ごとの平均を引くことで、能力差の影響を除く
    （上位N走の平均を基準にすると、良かったレースだけを選んだ偏りで傾きが小さく出る）。
    """
    fmean = course_front_means(races)
    by_horse = {}
    for h, hist in figs.items():
        for _date, fig, r, x in hist:
            if r["year"] in TRAIN_YEARS and r["front"] is not None and x.get("style") is not None:
                fast = fmean[course_key(r)] - r["front"]
                by_horse.setdefault(h, []).append(((x["style"] - 0.5) * fast, fig))
    sxy = sxx = 0.0
    for v in by_horse.values():
        if len(v) < 2:
            continue
        mu = statistics.mean(u for u, _ in v)
        mf = statistics.mean(f for _, f in v)
        for u, f in v:
            sxy += (u - mu) * (f - mf)
            sxx += (u - mu) ** 2
    return sxy / sxx if sxx else 0.0


def peak_rating(history: list[tuple], n: int, w: int, k: float, m0: float,
                dist: int | None = None, tol: int = 9999) -> tuple[float, str]:
    """全盛期の能力値: 各出走日を終点とする直近W年の能力値の最大値"""
    best = (-99.0, "")
    for date, *_rest in history:
        # date + "~" は同じ日付の文字列より後ろに並ぶので、その日のレースまでを含む
        rt = rating(history, date + "~", n, w, k, m0, dist, tol)
        if rt and rt[0] > best[0]:
            best = (rt[0], date)
    return best
