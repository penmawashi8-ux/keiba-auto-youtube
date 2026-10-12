#!/usr/bin/env python3
"""夢の対決シミュレーション（試作2号）: sim/data.json から能力値を推定し、仮想レースを1万回走らせる。

モデル（すべて公開成績のタイムから自前で計算。netkeiba のタイム指数は使わない）:
  1. 基準タイム: 2000年以降の JRA芝 G1/G2 勝ちタイム（約1,000レース）を
       コース(場・距離) + 馬場状態 + 年 + 前半600mのペース + 格(G2) + 3歳限定 + 牝馬限定
     で回帰する。年の項が「馬場の高速化」、ペースの項が「スローで時計が遅くなる分」を吸収する
     （年の項は隣接年の差に罰則をかけて滑らかにする）
  2. 1走ごとのスピード値 = その年・その馬場・そのペースでの「平均的なG1勝ち馬」のタイム − その馬のタイム
     （2400m換算。定量より重い/軽い斤量を背負った分も補正）
     → 0 なら平均的なG1馬、+1.0 なら平均的なG1馬より2400mで1秒速い
  3. 能力値 = 対象レース（JRA芝1800〜2600mのG1/G2。2歳戦・牝馬限定戦は除く）のスピード値上位4走の平均
     ばらつき = 上位6走の標準偏差（0.45〜0.9秒に制限）
  4. 脚質 = 通過順の最初の位置 ÷ 頭数 の平均（0=逃げ, 1=追込）
  5. 1回のレース: 能力 + ばらつき×乱数 + 展開（ハイペースなら差し有利）で着順を決める。
     斤量は全馬「定量」（馬齢・性別に応じた通常の斤量）なので追加の補正はしない

出力: sim/report.md, sim/result.json, sim/race.mp4, sim/frame_*.jpg
numpy / Pillow は使わない（プロジェクトルール）。描画は ffmpeg drawtext のみ。
"""

import glob
import json
import random
import statistics
import subprocess
from pathlib import Path

SIM_DIR = Path("sim")
REF_YEAR = 2023            # この年を基準に、各年の馬場の速さを差として推定する
TARGET = {"venue": "東京", "distance": 2400, "going": "良", "label": "東京 芝2400m（良）"}
N_RUNS = 10000
SEC_PER_KG = 0.15          # 斤量1kg ≈ 2000mで0.15秒
STYLE_PACE = 0.6           # 展開の効き（秒）。ハイペースで追込に最大この程度有利
YEAR_SMOOTH = 15.0         # 年ごとの馬場の速さの平滑化の強さ
TOP_N = 4                  # 能力値に使う上位走数
SIGMA_TOP = 6              # ばらつきの計算に使う上位走数
SIGMA_MIN, SIGMA_MAX = 0.45, 0.9
DIST_MIN, DIST_MAX = 1800, 2600
GOINGS = ["良", "稍", "重", "不"]
COLORS = ["#E53935", "#1E88E5", "#FDD835", "#43A047", "#FFFFFF", "#8E24AA", "#FB8C00", "#00ACC1"]

def course_key(venue: str, distance: int) -> str:
    return f"{venue}{distance}"


# ---------------------------------------------------------------------------
# 1. 基準タイムの回帰（純Python の最小二乗 + 微小リッジ）
# ---------------------------------------------------------------------------
def solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        piv = m[c][c] or 1e-12
        for r in range(n):
            if r != c:
                f = m[r][c] / piv
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / (m[i][i] or 1e-12) for i in range(n)]


def front600(r: dict) -> float | None:
    """スタートから600m地点までの通過タイム。

    ラップは200mごとだが、距離が200で割り切れないレース（2500mなど）は最初の区間が100m。
    累計ペース（pace_cum）があれば600m地点を補間で求め、無い古いデータは
    「3つ目の累計値＝500m通過」を600m相当に換算する。
    """
    cum = r.get("pace_cum")
    first = 100 if r["distance"] % 200 == 100 else 200
    if cum:
        marks = [first + 200 * i for i in range(len(cum))]
        for (m0, t0), (m1, t1) in zip(zip(marks, cum), zip(marks[1:], cum[1:])):
            if m0 <= 600 <= m1:
                return t0 + (t1 - t0) * (600 - m0) / (m1 - m0)
        return None
    if r.get("front3f") is None:
        return None
    return r["front3f"] * 600 / 500 if first == 100 else r["front3f"]


def clean_baseline(rows: list[dict]) -> list[dict]:
    """重賞日程のリンク誤り（同じレースIDが別レースの行にも付いている）を除き、600m通過を付ける"""
    count = {}
    for r in rows:
        count[r["race_id"]] = count.get(r["race_id"], 0) + 1
    dup = {k for k, v in count.items() if v > 1}
    if dup:
        print(f"[基準] レースIDが重複した {len(dup)}件を除外: {sorted(dup)}")
    return [{**r, "front": front600(r)} for r in rows if r["race_id"] not in dup]


class ParModel:
    """平均的な重賞勝ち馬のタイムを、コース・馬場・年・ペース・レース区分から予測する"""

    def __init__(self, rows: list[dict]):
        rows = [r for r in rows if r["grade"] in ("G1", "G2") and not r["age2"]]
        by_course = {}
        for r in rows:
            if r["front"]:
                by_course.setdefault(course_key(r["venue"], r["distance"]), []).append(r["front"])
        self.front_mean = {c: statistics.mean(v) for c, v in by_course.items()}
        self.courses = sorted({course_key(r["venue"], r["distance"]) for r in rows})
        self.years = sorted({r["year"] for r in rows} - {REF_YEAR})
        self.feats = (["c:" + c for c in self.courses] + ["g:" + g for g in GOINGS[1:]]
                      + [f"y:{y}" for y in self.years] + ["front3f", "G2", "3歳限定", "牝馬限定"])
        self.n_rows = len(rows)
        k = len(self.feats)
        ata = [[0.0] * k for _ in range(k)]
        atb = [0.0] * k
        for r in rows:
            xi = self._x(course_key(r["venue"], r["distance"]), r["going"], r["year"], r["front"],
                         r["grade"] == "G2", r["age3"], r["mare"])
            for i, xv in enumerate(xi):
                if xv:
                    atb[i] += xv * r["time"]
                    for j, xw in enumerate(xi):
                        if xw:
                            ata[i][j] += xv * xw
        for i in range(k):
            ata[i][i] += 1e-3
        # 年ごとの馬場の速さは隣の年と大きく変わらないはずなので、隣接年の差に罰則をかけて滑らかにする
        # （1年あたり約45レースしかなく、そのままだと特定の年が±2秒も跳ねるため）
        ypos = {y: self.feats.index(f"y:{y}") for y in self.years}
        all_years = sorted(self.years + [REF_YEAR])
        for y0, y1 in zip(all_years, all_years[1:]):
            i, j = ypos.get(y0), ypos.get(y1)   # 基準年は係数0固定なので片側だけの罰則になる
            for a in (i, j):
                if a is not None:
                    ata[a][a] += YEAR_SMOOTH
            if i is not None and j is not None:
                ata[i][j] -= YEAR_SMOOTH
                ata[j][i] -= YEAR_SMOOTH
        self.coef = dict(zip(self.feats, solve(ata, atb)))

    def front_dev(self, course: str, front3f: float | None) -> float:
        if front3f is None or course not in self.front_mean:
            return 0.0
        return front3f - self.front_mean[course]

    def _x(self, course, going, year, front3f, g2, age3, mare) -> list[float]:
        v = [1.0 if course == c else 0.0 for c in self.courses]
        v += [1.0 if going == g else 0.0 for g in GOINGS[1:]]
        v += [1.0 if year == y else 0.0 for y in self.years]
        v += [self.front_dev(course, front3f), float(g2), float(age3), float(mare)]
        return v

    def predict(self, venue, distance, going, year, front3f=None, age3=False) -> float | None:
        # front3f は600m通過タイム（front600 で換算済みの値）
        """その条件での「平均的なG1勝ち馬」（古馬混合・牡馬基準）のタイム"""
        course = course_key(venue, distance)
        if "c:" + course not in self.coef:
            return None
        c = self.coef
        return (c["c:" + course] + c.get("g:" + (going or "良"), 0.0) + c.get(f"y:{year}", 0.0)
                + c["front3f"] * self.front_dev(course, front3f) + (c["3歳限定"] if age3 else 0.0))


# ---------------------------------------------------------------------------
# 2〜4. 能力値・脚質
# ---------------------------------------------------------------------------
def first_pass_ratio(passing: str, field: int | None) -> float | None:
    if not passing or not field:
        return None
    try:
        first = int(passing.split("-")[0])
    except ValueError:
        return None
    return min(max((first - 1) / max(field - 1, 1), 0.0), 1.0)


def wfa_weight(year: int, age: int, sex: str, age3_race: bool) -> float:
    """定量（馬齢・性別に応じた通常の斤量）。2023年から古馬の基準が1kg増えた"""
    base = 58.0 if year >= 2023 else 57.0
    if age3_race:
        base = 57.0
    elif age == 3:
        base -= 2.0
    if sex == "牝":
        base -= 2.0
    return base


def build_ratings(data: dict, model: ParModel) -> dict:
    base = {b["race_id"]: b for b in data["baseline"]}
    horses = {}
    for name, h in data["horses"].items():
        birth = int(h["id"][:4]) if h["id"][:4].isdigit() else None
        figs, styles = [], []
        for r in h["races"]:
            name_r = r["race_name"]
            b = base.get(r["race_id"])  # 重賞日程に載った JRA芝 G1/G2 だけが対象
            if (not b or not r["time"] or not (DIST_MIN <= b["distance"] <= DIST_MAX)
                    or b["age2"] or b["mare"]):
                continue
            if (b["venue"], b["distance"]) != (r["venue"], r["distance"]):
                print(f"[警告] {name} {name_r}: 基準データと条件が食い違うため除外 "
                      f"({r['venue']}{r['distance']} vs {b['venue']}{b['distance']})")
                continue
            year = b["year"]
            age3 = b["age3"]
            exp = model.predict(b["venue"], b["distance"], b["going"], year, b["front"], age3)
            if exp is None:
                continue
            fig = (exp - r["time"]) * 2400 / b["distance"]
            if r["weight"] and birth:
                extra = r["weight"] - wfa_weight(year, year - birth, h["sex"], age3)
                fig += extra * SEC_PER_KG * 1.2
            figs.append({"fig": round(fig, 2), "race": name_r, "date": r["date"], "pos": r["position"],
                         "time": r["time"], "course": f"{b['venue']}{b['distance']}", "going": b["going"],
                         "front3f": round(b["front"], 1) if b["front"] else None, "weight": r["weight"]})
            s = first_pass_ratio(r["passing"], r["field_size"])
            if s is not None:
                styles.append(s)
        figs.sort(key=lambda f: -f["fig"])
        if not figs:
            print(f"[警告] {name}: 対象レースがありません")
            continue
        top = [f["fig"] for f in figs[:TOP_N]]
        spread = [f["fig"] for f in figs[:SIGMA_TOP]]
        sigma = statistics.pstdev(spread) if len(spread) > 1 else SIGMA_MAX
        horses[name] = {
            "mu": statistics.mean(top),
            "sigma": min(max(sigma, SIGMA_MIN), SIGMA_MAX),
            "style": statistics.mean(styles) if styles else 0.5,
            "sex": h["sex"],
            "figs": figs,
        }
    return horses


# ---------------------------------------------------------------------------
# 5. モンテカルロ
# ---------------------------------------------------------------------------
def perf(h: dict, z: float, pace: float) -> float:
    return h["mu"] + h["sigma"] * z + pace * (h["style"] - 0.5) * STYLE_PACE


def monte_carlo(horses: dict, seed: int = 20261012) -> dict:
    rnd = random.Random(seed)
    names = list(horses)
    rank_counts = {n: [0] * len(names) for n in names}
    for _ in range(N_RUNS):
        pace = rnd.gauss(0, 1)
        scores = sorted(names, key=lambda n: -perf(horses[n], rnd.gauss(0, 1), pace))
        for i, n in enumerate(scores):
            rank_counts[n][i] += 1
    return rank_counts


# ---------------------------------------------------------------------------
# レポート
# ---------------------------------------------------------------------------
def fmt_time(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def write_report(horses, model: ParModel, rank_counts, base_time, race) -> None:
    names = sorted(horses, key=lambda n: -rank_counts[n][0])
    c = model.coef
    md = [f"# 夢の対決シミュレーション（試作2号） {TARGET['label']}", "",
          f"- 試行回数: {N_RUNS:,}回 / 斤量は全馬定量 / 馬場は{REF_YEAR}年の速さ",
          f"- 基準データ: JRA芝 G1/G2 {model.n_rows}レースの勝ちタイム",
          f"- ペース補正: 前半3Fが1秒遅いと勝ちタイムが {c['front3f']:+.2f}秒",
          f"- 想定タイム（平均的なG1勝ち馬）: {fmt_time(base_time)}", "",
          "## 勝率", "", "| 馬 | 勝率 | 2着内 | 3着内 | 能力値 | ばらつき | 脚質(0逃げ〜1追込) |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for n in names:
        rc = rank_counts[n]
        h = horses[n]
        md.append(f"| {n} | {rc[0] / N_RUNS:.1%} | {sum(rc[:2]) / N_RUNS:.1%} | {sum(rc[:3]) / N_RUNS:.1%} | "
                  f"{h['mu']:+.2f} | {h['sigma']:.2f} | {h['style']:.2f} |")
    md += ["", "能力値: 平均的なG1勝ち馬との差（2400m換算・秒）。+1.0 なら約6馬身先着する計算", "",
           "## 代表レース（展開・調子が平均的な場合）", "", "| 着 | 馬 | タイム | 着差 |", "|---|---|---|---|"]
    win_t = race[0][1]
    for i, (n, t) in enumerate(race, 1):
        md.append(f"| {i} | {n} | {fmt_time(t)} | {'' if i == 1 else f'+{t - win_t:.1f}秒'} |")
    md += ["", "## 馬場の速さ（年ごと、2023年との差）", "",
           "| 年 | 差（秒） |", "|---|---:|"]
    md += [f"| {y} | {c.get(f'y:{y}', 0.0):+.2f} |" for y in model.years + [REF_YEAR]]
    md += ["", f"## 各馬のスピード値（太字の上位{TOP_N}走が能力値）", ""]
    for n in names:
        md += [f"### {n}", "", "| 値 | 日付 | レース | 着 | コース | 馬場 | 前半3F | 斤量 | タイム |",
               "|---:|---|---|---:|---|---|---:|---:|---|"]
        for i, f in enumerate(horses[n]["figs"]):
            mark = "**" if i < TOP_N else ""
            md.append(f"| {mark}{f['fig']:+.2f}{mark} | {f['date']} | {f['race']} | {f['pos']} | {f['course']} | "
                      f"{f['going']} | {f['front3f'] or '-'} | {f['weight'] or '-'} | {fmt_time(f['time'])} |")
        md.append("")
    md += ["## 回帰係数（コース以外）", "", "| 項目 | 値 |", "|---|---:|"]
    md += [f"| {k} | {v:+.3f} |" for k, v in c.items() if not k.startswith(("c:", "y:"))]
    (SIM_DIR / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (SIM_DIR / "result.json").write_text(json.dumps({
        "win_rate": {n: rank_counts[n][0] / N_RUNS for n in names},
        "ratings": {n: {k: horses[n][k] for k in ("mu", "sigma", "style", "sex")} for n in names},
        "race": race,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# アニメーション（ffmpeg drawtext のみ）
# ---------------------------------------------------------------------------
W, H, FPS = 1280, 720, 30
SPEEDUP = 4.0
TITLE_SEC, RESULT_SEC = 3.0, 6.0
LEAD_X, PX_PER_M = 1050, 7.0
DOT_W = 38                # fontsize 46 の●の幅（px）


def find_font() -> str:
    for p in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"]:
        if Path(p).exists():
            return p
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    if not hits:
        raise RuntimeError("CJKフォントが見つかりません")
    return hits[0]


def _smooth(v: float) -> float:
    v = min(max(v, 0.0), 1.0)
    return v * v * (3 - 2 * v)


def distance_at(tau: float, my_t: float, win_t: float, style: float, dist: float) -> float:
    """経過時間 tau での走行距離。

    道中は勝ち馬と同じ平均ペースで、脚質ぶん前後（逃げは前・追込は後ろ）に隊列を組む。
    残り約3割で隊列が崩れ、勝ち馬のゴール時点で各馬の着差（走破タイム差×速度）に収束する。
    その後は同じ速度でゴールまで走る。
    """
    v = dist / win_t
    if tau >= win_t:
        return min(dist - (my_t - win_t) * v + (tau - win_t) * v, dist)
    u = tau / win_t
    late = _smooth((u - 0.7) / 0.3)
    style_offset = (0.5 - style) * 25.0 * min(u / 0.12, 1.0)
    return v * tau + style_offset * (1 - late) - (my_t - win_t) * v * late


def piecewise(keys: list[tuple[float, float]]) -> str:
    """[(t, v), ...] を線形補間する ffmpeg 式"""
    expr = f"{keys[-1][1]:.1f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(keys, keys[1:]))):
        expr = f"if(lt(t,{t1:.3f}),{v0:.1f}+({v1 - v0:.1f})*(t-{t0:.3f})/{t1 - t0:.3f},{expr})"
    return f"if(lt(t,{keys[0][0]:.3f}),{keys[0][1]:.1f},{expr})"


def render(horses: dict, race: list[tuple[str, float]]) -> None:
    font = find_font()
    tmp = SIM_DIR / "txt"
    tmp.mkdir(exist_ok=True)

    def tf(key: str, text: str) -> str:
        p = tmp / f"{key}.txt"
        p.write_text(text, encoding="utf-8")
        return str(p)

    dist = TARGET["distance"]
    finish = {n: t for n, t in race}
    t_end = max(finish.values())
    win_t = min(finish.values())
    race_video = t_end / SPEEDUP + 1.0
    total = TITLE_SEC + race_video + RESULT_SEC
    names = list(horses)
    lane_y = {n: 170 + i * (430 // max(len(names), 1)) for i, n in enumerate(names)}

    # 0.75秒（動画時間）ごとのキーフレーム
    step = 0.75 * SPEEDUP
    taus = sorted({round(i * step, 3) for i in range(int(t_end / step) + 2)} | {round(win_t, 3)})
    lead = [max(distance_at(tau, finish[n], win_t, horses[n]["style"], dist) for n in names) for tau in taus]
    vt = lambda tau: TITLE_SEC + tau / SPEEDUP

    f = [f"color=c=0x1B5E20:s={W}x{H}:r={FPS}:d={total:.2f}[bg]", "[bg]"]
    chain = []

    def dt(text_file, size, color, x, y, extra=""):
        chain.append(f"drawtext=textfile='{text_file}':fontfile='{font}':fontsize={size}:"
                     f"fontcolor={color}:x={x}:y={y}{extra}")

    # タイトル
    title_on = f":enable='lt(t,{TITLE_SEC})'"
    dt(tf("title", "夢の対決シミュレーション"), 64, "white", "(w-text_w)/2", "250", title_on)
    dt(tf("sub", f"{TARGET['label']}　{N_RUNS:,}回の期待値レース"), 34, "0xFFF59D", "(w-text_w)/2", "350", title_on)

    race_on = f":enable='between(t,{TITLE_SEC},{TITLE_SEC + race_video:.2f})'"
    # レーン区切り（文字の罫線）
    sep = tf("sep", "─" * 80)
    for n in names:
        dt(sep, 20, "0xFFFFFF@0.25", "0", str(lane_y[n] + 55), race_on)
    # ゴール板（先頭がゴール前250mに来たら画面右から入ってくる）
    goal_keys = [(vt(tau), LEAD_X + (dist - d) * PX_PER_M) for tau, d in zip(taus, lead)]
    goal = tf("goal", "\n".join(["|"] * 12))
    dt(goal, 40, "white", f"'{piecewise(goal_keys)}'", "150", race_on)
    # 残り距離の表示（200mごと）
    for rem in range(dist - 200, -1, -200):
        t0 = next((vt(tau) for tau, d in zip(taus, lead) if dist - d <= rem + 200), None)
        t1 = min(next((vt(tau) for tau, d in zip(taus, lead) if dist - d <= rem), vt(win_t)), vt(win_t))
        if t0 is None:
            continue
        dt(tf(f"rem{rem}", f"残り {rem + 200}m"), 40, "white", "40", "40",
           f":box=1:boxcolor=black@0.5:boxborderw=12:enable='between(t,{t0:.2f},{t1:.2f})'")
    goal_t = vt(win_t)
    dt(tf("goal_label", "ゴール！"), 40, "white", "40", "40",
       f":box=1:boxcolor=black@0.5:boxborderw=12:enable='between(t,{goal_t:.2f},{TITLE_SEC + race_video:.2f})'")
    dt(tf("speed", f"{int(SPEEDUP)}倍速"), 28, "0xFFFFFF@0.7", "w-text_w-40", "48", race_on)
    # 各馬
    for i, n in enumerate(names):
        xs = [(vt(tau), LEAD_X + (distance_at(tau, finish[n], win_t, horses[n]["style"], dist) - ld) * PX_PER_M)
              for tau, ld in zip(taus, lead)]
        x_expr = f"'{piecewise(xs)}-{DOT_W}'"  # ●の右端（鼻先）を走行位置に合わせる
        color = COLORS[i % len(COLORS)]
        dt(tf(f"dot{i}", "●"), 46, color, x_expr, str(lane_y[n]), race_on)
        dt(tf(f"name{i}", n), 26, "white", f"{x_expr}-text_w-8", str(lane_y[n] + 12),
           ":box=1:boxcolor=black@0.45:boxborderw=6" + race_on)

    # 結果ボード
    lines = ["結果（期待値レース）", ""] + [
        f"{i}着  {n}　{fmt_time(t)}{'' if i == 1 else f'  (+{t - win_t:.1f})'}" for i, (n, t) in enumerate(race, 1)]
    dt(tf("result", "\n".join(lines)), 40, "white", "(w-text_w)/2", "(h-text_h)/2",
       f":line_spacing=14:box=1:boxcolor=black@0.6:boxborderw=30:enable='gte(t,{TITLE_SEC + race_video:.2f})'")

    graph = f[0] + ";" + f[1] + ",".join(chain) + "[v]"
    script = SIM_DIR / "filter.txt"
    script.write_text(graph, encoding="utf-8")
    out = SIM_DIR / "race.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-filter_complex_script", str(script),
                    "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", f"{total:.2f}", str(out)],
                   check=True)
    # 静止画（レース中盤・ゴール前・結果）
    for label, sec in [("mid", vt(win_t * 0.5)), ("stretch", vt(win_t * 0.9)), ("goal", goal_t + 0.1),
                       ("result", total - 1.0)]:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{sec:.2f}", "-i", str(out),
                        "-vframes", "1", "-q:v", "2", str(SIM_DIR / f"frame_{label}.jpg")], check=True)
    print(f"保存: {out}")


def main() -> None:
    data = json.loads((SIM_DIR / "data.json").read_text(encoding="utf-8"))
    data["baseline"] = clean_baseline(data["baseline"])
    model = ParModel(data["baseline"])
    horses = build_ratings(data, model)
    rank_counts = monte_carlo(horses)
    base = model.predict(TARGET["venue"], TARGET["distance"], TARGET["going"], REF_YEAR)
    # 代表レース: 調子・展開が平均（z=0, pace=0）のときのタイム
    race = sorted(((n, base - perf(h, 0.0, 0.0)) for n, h in horses.items()), key=lambda x: x[1])
    write_report(horses, model, rank_counts, base, race)
    print((SIM_DIR / "report.md").read_text(encoding="utf-8")[:2500])
    render(horses, race)


if __name__ == "__main__":
    main()
