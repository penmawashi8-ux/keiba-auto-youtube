#!/usr/bin/env python3
"""夢の対決シミュレーション（試作）: sim/data.json から能力値を推定し、仮想レースを1万回走らせる。

モデル（すべて公開成績のタイムから自前で計算。netkeiba のタイム指数は使わない）:
  1. レース基準タイム: 各レースの1〜5着タイムの中央値を「そのレースの水準」とし、
     「コース(場・距離) + 馬場状態 + 年」の線形回帰で年ごとの馬場の高速化を推定する
  2. 1走ごとのスピード値 = 基準年に換算した期待タイム − その馬のタイム（秒、2400m換算、斤量57kg換算）
  3. 馬の能力 = スピード値の上位5走の平均（全盛期）、ばらつき = その標準偏差
  4. 脚質 = 通過順の最初の位置 ÷ 頭数 の平均（0=逃げ, 1=追込）
  5. 1回のレース: 能力 + ばらつき×乱数 + 展開（ハイペースなら差し有利）で着順を決める

出力: sim/report.md, sim/result.json, sim/race.mp4, sim/frame_*.jpg
numpy / Pillow は使わない（プロジェクトルール）。描画は ffmpeg drawtext のみ。
"""

import glob
import json
import math
import random
import statistics
import subprocess
from pathlib import Path

SIM_DIR = Path("sim")
REF_YEAR = 2023            # 馬場の速さをこの年に揃える
TARGET = {"venue": "東京", "distance": 2400, "going": "良", "label": "東京 芝2400m（良）"}
N_RUNS = 10000
SEC_PER_KG = 0.15          # 斤量1kg ≈ 2000mで0.15秒
STYLE_PACE = 0.6           # 展開の効き（秒）。ハイペースで追込に最大この程度有利
MARE_ALLOWANCE_KG = 2.0    # 牝馬は2kg軽い（実際のJCと同じ条件）
TOP_N = 5
SIGMA_FLOOR = 0.5         # 1走ごとの調子の波の下限（秒）。上位5走だけだと小さく出すぎるため
GOINGS = ["良", "稍", "重", "不"]
COLORS = ["#E53935", "#1E88E5", "#FDD835", "#43A047", "#FFFFFF", "#8E24AA", "#FB8C00", "#00ACC1"]


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


def course_key(venue: str, distance: int) -> str:
    return f"{venue}{distance}"


def fit_par_model(rows: list[dict]):
    """rows: {course, going, year, par} → 期待タイム関数 f(course, going, year)"""
    courses = sorted({r["course"] for r in rows})
    feats = ["c:" + c for c in courses] + ["g:" + g for g in GOINGS[1:]] + ["year"]

    def x(course, going, year):
        v = [1.0 if course == c else 0.0 for c in courses]
        v += [1.0 if going == g else 0.0 for g in GOINGS[1:]]
        v.append(year - REF_YEAR)
        return v

    k = len(feats)
    ata = [[0.0] * k for _ in range(k)]
    atb = [0.0] * k
    for r in rows:
        xi = x(r["course"], r["going"], r["year"])
        for i in range(k):
            atb[i] += xi[i] * r["par"]
            for j in range(k):
                ata[i][j] += xi[i] * xi[j]
    for i in range(k):
        ata[i][i] += 1e-3
    beta = solve(ata, atb)
    coef = dict(zip(feats, beta))

    def predict(course, going, year):
        if "c:" + course not in coef:
            return None
        return coef["c:" + course] + coef.get("g:" + going, 0.0) + coef["year"] * (year - REF_YEAR)

    return predict, coef


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


def build_ratings(data: dict):
    par_rows = []
    for rid, info in data["races"].items():
        times = sorted(info["top_times"])
        if len(times) < 3:
            continue
        # レース条件は出走馬側の成績行から引く
        row = next((r for h in data["horses"].values() for r in h["races"] if r["race_id"] == rid), None)
        if not row or not row["distance"]:
            continue
        par_rows.append({"rid": rid, "course": course_key(row["venue"], row["distance"]),
                         "distance": row["distance"], "going": row["going"] or "良",
                         "year": info["year"], "par": statistics.median(times)})
    predict, coef = fit_par_model(par_rows)

    horses = {}
    for name, h in data["horses"].items():
        figs, styles = [], []
        for r in h["races"]:
            if r["race_id"] not in data["races"] or not r["time"] or not r["distance"]:
                continue
            if not (1600 <= r["distance"] <= 3200):
                continue
            year = data["races"][r["race_id"]]["year"]
            exp = predict(course_key(r["venue"], r["distance"]), r["going"] or "良", REF_YEAR)
            if exp is None:
                continue
            scale = 2400 / r["distance"]
            fig = (exp - r["time"]) * scale
            if r["weight"]:
                fig += (r["weight"] - 57) * SEC_PER_KG * 1.2
            figs.append({"fig": round(fig, 2), "race": r["race_name"], "date": r["date"],
                         "pos": r["position"], "time": r["time"], "course": f"{r['venue']}{r['distance']}",
                         "going": r["going"], "year": year})
            s = first_pass_ratio(r["passing"], r["field_size"])
            if s is not None:
                styles.append(s)
        figs.sort(key=lambda f: -f["fig"])
        top = [f["fig"] for f in figs[:TOP_N]]
        if not top:
            print(f"[警告] {name}: 有効な成績がありません")
            continue
        horses[name] = {
            "mu": statistics.mean(top),
            "sigma": max(statistics.pstdev(top) if len(top) > 1 else 0.5, SIGMA_FLOOR),
            "style": statistics.mean(styles) if styles else 0.5,
            "sex": h["sex"],
            "figs": figs,
        }
    return horses, coef, par_rows, predict


# ---------------------------------------------------------------------------
# 5. モンテカルロ
# ---------------------------------------------------------------------------
def perf(h: dict, z: float, pace: float) -> float:
    p = h["mu"] + h["sigma"] * z + pace * (h["style"] - 0.5) * STYLE_PACE
    if h["sex"] == "牝":
        p += MARE_ALLOWANCE_KG * SEC_PER_KG * 1.2
    return p


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


def write_report(horses, coef, par_rows, rank_counts, base_time, race) -> None:
    names = sorted(horses, key=lambda n: -rank_counts[n][0])
    md = [f"# 夢の対決シミュレーション（試作） {TARGET['label']}", "",
          f"- 試行回数: {N_RUNS:,}回 / 基準年: {REF_YEAR}年の馬場に換算 / 斤量57kg（牝馬55kg）",
          f"- 年ごとの馬場高速化の推定: 1年あたり {coef['year']:+.3f}秒（基準レース{len(par_rows)}件から回帰）",
          f"- 想定勝ちタイム水準: {fmt_time(base_time)}", "",
          "## 勝率", "", "| 馬 | 勝率 | 2着内 | 3着内 | 能力値 | ばらつき | 脚質(0逃げ〜1追込) |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for n in names:
        c = rank_counts[n]
        h = horses[n]
        md.append(f"| {n} | {c[0] / N_RUNS:.1%} | {sum(c[:2]) / N_RUNS:.1%} | {sum(c[:3]) / N_RUNS:.1%} | "
                  f"{h['mu']:+.2f} | {h['sigma']:.2f} | {h['style']:.2f} |")
    md += ["", "## 代表レース（展開・調子が平均的な場合）", "", "| 着 | 馬 | タイム | 着差 |", "|---|---|---|---|"]
    win_t = race[0][1]
    for i, (n, t) in enumerate(race, 1):
        md.append(f"| {i} | {n} | {fmt_time(t)} | {'' if i == 1 else f'+{t - win_t:.1f}秒'} |")
    md += ["", "## 各馬のスピード値（上位5走が能力値）", ""]
    for n in names:
        md += [f"### {n}", "", "| 値 | 日付 | レース | 着 | コース | 馬場 | タイム |", "|---:|---|---|---:|---|---|---|"]
        for i, f in enumerate(horses[n]["figs"]):
            mark = "**" if i < TOP_N else ""
            md.append(f"| {mark}{f['fig']:+.2f}{mark} | {f['date']} | {f['race']} | {f['pos']} | {f['course']} | "
                      f"{f['going']} | {fmt_time(f['time'])} |")
        md.append("")
    md += ["## 基準タイム回帰の係数", "", "| 項目 | 値 |", "|---|---:|"]
    md += [f"| {k} | {v:.3f} |" for k, v in coef.items()]
    (SIM_DIR / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (SIM_DIR / "result.json").write_text(json.dumps({
        "win_rate": {n: rank_counts[n][0] / N_RUNS for n in names},
        "ratings": {n: {k: horses[n][k] for k in ("mu", "sigma", "style", "sex")} for n in names},
        "year_coef": coef["year"], "race": race,
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
    horses, coef, par_rows, predict = build_ratings(data)
    rank_counts = monte_carlo(horses)
    base = predict(course_key(TARGET["venue"], TARGET["distance"]), TARGET["going"], REF_YEAR) or 143.5
    # 代表レース: 調子・展開が平均（z=0, pace=0）のときのタイム
    race = sorted(((n, base - perf(h, 0.0, 0.0)) for n, h in horses.items()), key=lambda x: x[1])
    write_report(horses, coef, par_rows, rank_counts, base, race)
    print((SIM_DIR / "report.md").read_text(encoding="utf-8")[:1500])
    render(horses, race)


if __name__ == "__main__":
    main()
