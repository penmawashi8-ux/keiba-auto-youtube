#!/usr/bin/env python3
"""夢の対決シミュレーションの物差し（基準タイムとスピード値）。学習モデルは sim_learn.py。

データ: sim/races.json（sim_fetch.py が集めた JRA芝1600〜3200m G1/G2 の全出走馬成績）
      sim/horses_cache.json（sim_fetch_horses.py が集めた、それらの馬の全キャリア。JRA芝1400〜3600m）

■ 基準タイム（ParModel）
  平均的なG1勝ち馬が、そのコース・馬場・年・ペースで走ったときのタイム。全クラスの勝ちタイムを
      コース(場・距離) + 馬場 + 年 + ペース + クラス(G2〜新馬) + 3歳限定 + 牝馬限定
  で回帰して求め、予測ではクラスや限定戦の項を使わず「古馬混合G1」の物差しにそろえる。
  年の項は隣接年の差に罰則をかけて滑らかにする（馬場の高速化を表す）。

■ スピード値（1走ごとの出来。単位は「2400m換算の秒」）
  時計ベース（speed_figures）:
      (基準タイム − その馬のタイム) × 2400/距離 + 定量との斤量差 − 展開の有利不利
  着差×相手関係（relative_figures）:
      レース水準 = 出走馬それぞれの「事前のふだんの出来 + 勝ち馬との着差」の中央値（ペースに左右されない）
      今回の出来 = レース水準 − 着差 + 斤量補正 − 展開の有利不利
      世代をまたぐと水準がずれるので、年ごとの平均を時計ベースに合わせる（anchor_years）
  展開の有利不利 = 展開の効き ×（脚質 − 0.5）×（ハイペース度）。効きは同じ馬の中での回帰で推定。

■ これまでの検証で分かったこと（2頭比較の正答率、検証期間 2019〜2025、単勝人気は約69%）
  時計ベースのみ 59〜60% → 着差×相手関係 62% → 最近重視・年齢曲線 63% → 複数の手がかりの学習 65.7%
  各馬自身の前半タイムでのペース補正（own）は、逃げ・先行を不当に低く評価したため不採用。

numpy は使わない（プロジェクトルール）。
"""

import json
import statistics
from pathlib import Path

SIM_DIR = Path("sim")
REF_YEAR = 2023
GOINGS = ["良", "稍", "重", "不"]
SEC_PER_KG = 0.15 * 1.2       # 斤量1kgあたりの秒（2400m換算）
YEAR_SMOOTH = 15.0
TRAIN_YEARS = range(2008, 2019)
TEST_YEARS = range(2019, 2026)


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


CLASSES = ["G1", "G2", "G3", "L", "OP", "3勝", "2勝", "1勝", "未勝利", "新馬"]
CAREER_DIST_MIN, CAREER_DIST_MAX = 1400, 3600


def race_class(name: str) -> str:
    """レース名から格・クラスを判定する（旧表記の500万下などにも対応）"""
    for cls, keys in (("G1", ["(GI)", "(G1)"]), ("G2", ["(GII)", "(G2)"]), ("G3", ["(GIII)", "(G3)"]),
                      ("L", ["(L)"]), ("新馬", ["新馬"]), ("未勝利", ["未勝利"]),
                      ("1勝", ["1勝クラス", "500万"]), ("2勝", ["2勝クラス", "1000万"]),
                      ("3勝", ["3勝クラス", "1600万"])):
        if any(k in name for k in keys):
            return cls
    return "OP"


def birth_year(horse_id: str) -> int | None:
    return int(horse_id[:4]) if horse_id[:4].isdigit() and 1980 < int(horse_id[:4]) < 2030 else None


def _prep_runners(r: dict, field: int) -> None:
    for x in r["runners"]:
        x["early"] = x["time"] - x["last3f"] if x.get("time") and x.get("last3f") else None
        x["style"] = first_pass_ratio(x.get("passing", ""), field)


def load_races(path: Path = SIM_DIR / "races.json",
               careers_path: Path = SIM_DIR / "horses_cache.json") -> list[dict]:
    """重賞（全出走馬）に、出走馬の全キャリア（条件戦・OP・G3など）を合わせたレース一覧を作る。

    重賞以外のレースは「データに含まれる馬の分だけ」の出走馬しか持たないが、勝ちタイムは
    各馬のタイムと着差から求められるので基準タイムの回帰に使える。
    graded=True のレース（G1/G2、全出走馬と単勝人気がそろう）だけをバックテストの採点に使う。
    """
    graded = json.loads(path.read_text(encoding="utf-8"))
    out, sex_of, graded_ids = [], {}, set()
    for r in graded:
        graded_ids.add(r["race_id"])
        for x in r["runners"]:
            if x.get("sex"):
                sex_of[x["horse_id"]] = x["sex"]
        if r["age2"] or not r.get("date"):
            continue
        win = next((x for x in r["runners"] if x["pos"] == 1 and x.get("time")), None)
        if not win:
            continue
        r.update(graded=True, cls=r["grade"], win_time=win["time"],
                 front=front600(r["distance"], r.get("pace_cum") or []))
        _prep_runners(r, len(r["runners"]))
        out.append(r)

    careers = json.loads(careers_path.read_text(encoding="utf-8")) if careers_path.exists() else {}
    by_race = {}
    for hid, rows in careers.items():
        for row in rows:
            if (row["race_id"] in graded_ids or row["surface"] != "芝"
                    or not (CAREER_DIST_MIN <= row["distance"] <= CAREER_DIST_MAX) or not row.get("date")):
                continue
            by_race.setdefault(row["race_id"], []).append((hid, row))
    for rid, rows in by_race.items():
        row0 = rows[0][1]
        name = row0["race_name"]
        if "2歳" in name or "障害" in name:
            continue
        wins = [rw["time"] - max(rw["margin"] or 0.0, 0.0) for _, rw in rows
                if rw.get("time") and rw.get("pos") and rw.get("margin") is not None]
        if not wins:
            continue
        front = None
        m = row0.get("pace", "").split("-")
        if len(m) == 2:
            try:
                front = float(m[0])
            except ValueError:
                front = None
        year = int(row0["date"][:4])
        runners = []
        for hid, rw in rows:
            by = birth_year(hid)
            runners.append({"pos": rw.get("pos"), "horse_id": hid, "horse": "", "sex": sex_of.get(hid, ""),
                            "age": year - by if by else None, "weight": rw.get("weight"), "time": rw.get("time"),
                            "passing": rw.get("passing", ""), "last3f": rw.get("last3f"), "pop": rw.get("pop")})
        r = {"race_id": rid, "year": year, "date": row0["date"], "race_name": name, "grade": "",
             "venue": row0["venue"], "distance": row0["distance"], "going": row0["going"],
             "age2": False, "age3": name.startswith("3歳") and "以上" not in name and "3歳上" not in name,
             "mare": "牝" in name, "graded": False, "cls": race_class(name),
             "win_time": statistics.median(wins), "front": front, "runners": runners}
        _prep_runners(r, row0.get("field") or len(rows))
        out.append(r)
    out.sort(key=lambda r: r["date"])
    print(f"データ: 重賞 {sum(r['graded'] for r in out)}レース + その他 {sum(not r['graded'] for r in out)}レース"
          f" / のべ {sum(len(r['runners']) for r in out)}走")
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
    """平均的なG1勝ち馬のタイムを、コース・馬場・年・ペースから予測する。

    回帰には全クラスの勝ちタイムを使い、クラス（G2〜新馬）・3歳限定・牝馬限定の項で
    レースの水準差を吸収する。予測（par）ではこれらの項を使わず、古馬混合G1の物差しにそろえる。"""

    def __init__(self, races: list[dict], pace: str):
        self.pace = pace
        rows = []
        for r in races:
            win = next((x for x in r["runners"] if x["pos"] == 1 and x.get("time")), None)
            # 各馬自身のペースで補正する場合は、勝ち馬の前半タイムが分かるレースだけを使う
            if pace == "own" and not (win and win.get("early")):
                continue
            rows.append((r, win))
        # ペースの基準（コースごとの平均）
        self.pace_mean = {}
        for r, win in rows:
            v = self._pace_value(r, win or {})
            if v is not None:
                self.pace_mean.setdefault(course_key(r), []).append(v)
        self.pace_mean = {c: statistics.mean(v) for c, v in self.pace_mean.items()}
        self.courses = sorted({course_key(r) for r, _ in rows})
        self.years = sorted({r["year"] for r, _ in rows} - {REF_YEAR})
        self.feats = (["c:" + c for c in self.courses] + ["g:" + g for g in GOINGS[1:]]
                      + [f"y:{y}" for y in self.years] + ["pace", "3歳限定", "牝馬限定"]
                      + ["cls:" + c for c in CLASSES[1:]])
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
            xi[idx["pace"]] = self.pace_dev(r, win or {})
            xi[idx["3歳限定"]] = float(r["age3"])
            xi[idx["牝馬限定"]] = float(r["mare"])
            if r["cls"] != "G1":
                xi[idx["cls:" + r["cls"]]] = 1.0
            for i, v in xi.items():
                atb[i] += v * r["win_time"]
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
            return x["early"] / (r["distance"] - 600) * 600 if x.get("early") else None
        return None

    def pace_dev(self, r: dict, x: dict) -> float:
        v = self._pace_value(r, x)
        m = self.pace_mean.get(course_key(r))
        return v - m if v is not None and m is not None else 0.0

    def par(self, r: dict, x: dict) -> float | None:
        """古馬混合G1の物差しでの基準タイム（格・限定戦の項は使わない）。回帰に無いコースは None"""
        c = self.coef
        if "c:" + course_key(r) not in c:
            return None
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
            par = model.par(r, x)
            if par is None:
                continue
            fig = (par - x["time"]) * 2400 / r["distance"]
            if beta and x.get("style") is not None:
                fig -= beta * (x["style"] - 0.5) * fast
            if x.get("weight") and x.get("age"):
                fig += (x["weight"] - wfa_weight(r["year"], x["age"], x["sex"], r["age3"])) * SEC_PER_KG
            # 出走馬の辞書には書き込まない（ペース補正の候補ごとに値が違うため）
            figs.setdefault(x["horse_id"], []).append((r["date"], fig, r, x))
    for v in figs.values():
        v.sort(key=lambda t: t[0])
    return figs


def relative_figures(races: list[dict], model: ParModel, beta: float, blend: float) -> dict:
    """着差×相手関係のスピード値（古い順に1レースずつ、その時点までの情報だけで計算する）。

    レースの水準 L（勝ち馬の出来）を2通りで見積もって混ぜる:
      時計: L_time = (基準タイム − 勝ちタイム) × 2400/距離            … 時代・馬場の物差し
      相手: L_riv  = 出走馬それぞれの「事前のふだんの出来 + 勝ち馬との着差」の中央値 … ペースに左右されない
      L = blend × L_time + (1 − blend) × L_riv   （相手の事前評価が2頭未満なら L_time）
    各馬の今回の出来 = L − 着差（2400m換算、4秒で打ち切り）+ 斤量補正 − 展開の有利不利
    「ふだんの出来」は直近1年の最近3走の平均（上位だけを使うと水準が年々上振れするため）。
    """
    fmean = course_front_means(races)
    hist = {}
    for r in races:
        win = next((x for x in r["runners"] if x["pos"] == 1), None)
        par = model.par(r, win or {})
        if par is None or not r.get("win_time"):
            continue
        scale = 2400 / r["distance"]
        l_time = (par - r["win_time"]) * scale
        fast = fmean[course_key(r)] - r["front"] if r["front"] is not None else 0.0
        rows, ests = [], []
        for x in r["runners"]:
            if not x.get("time") or not x["horse_id"] or x["pos"] is None:
                continue
            margin = min(max(x["time"] - r["win_time"], 0.0), 4.0) * scale
            adj = 0.0
            if x.get("weight") and x.get("age"):
                adj += (x["weight"] - wfa_weight(r["year"], x["age"], x["sex"], r["age3"])) * SEC_PER_KG
            if beta and x.get("style") is not None:
                adj -= beta * (x["style"] - 0.5) * fast
            rows.append((x, margin, adj))
            typ = typical_level(hist.get(x["horse_id"], []), r["date"])
            if typ is not None:
                ests.append(typ + margin - adj)
        level = l_time
        if len(ests) >= 2 and blend < 1.0:
            level = blend * l_time + (1 - blend) * statistics.median(ests)
        for x, margin, adj in rows:
            hist.setdefault(x["horse_id"], []).append((r["date"], level - margin + adj, r, x))
        r["_l_time"] = l_time
    return anchor_years(hist) if blend < 1.0 else hist


def anchor_years(hist: dict) -> dict:
    """年ごとの水準を時計の物差しに合わせる。

    相手関係でつないだ物差しは、世代をまたいで少しずつずれる（実データで2005年前後が
    2023年前後より約0.7秒低く出た）。年ごとに「勝ち馬の出来 − 時計で測った勝ち馬の出来」の
    平均を求め（前後1年で平滑化）、その年の全成績から差し引く。同じ年の中の並びは変わらない。
    """
    gaps = {}
    for v in hist.values():
        for _date, perf, r, x in v:
            if x["pos"] == 1 and "_l_time" in r:
                gaps.setdefault(r["year"], []).append(perf - r["_l_time"])
    raw = {y: statistics.mean(g) for y, g in gaps.items()}
    offset = {}
    for y in raw:
        near = [raw[z] for z in (y - 1, y, y + 1) if z in raw]
        offset[y] = statistics.mean(near)
    return {h: [(d, perf - offset.get(r["year"], 0.0), r, x) for d, perf, r, x in v]
            for h, v in hist.items()}


def typical_level(history: list[tuple], before: str, last: int = 3) -> float | None:
    """直近1年の最近 last 走の出来の平均（レース水準の見積もり用）"""
    y, rest = int(before[:4]) - 1, before[4:]
    lo = f"{y}{rest}"
    vals = [h[1] for h in history if lo <= h[0] < before][-last:]
    return statistics.mean(vals) if vals else None


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
