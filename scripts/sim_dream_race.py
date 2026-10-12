#!/usr/bin/env python3
"""夢の対決シミュレーション（試作3号）: バックテストで選んだ能力推定で、仮想レースを1万回走らせる。

1. sim_model.select_model で、ペース補正・能力値の設定をバックテスト（2008〜2018年で選択、
   2019〜2025年で検証）して決め、調子の波 σ と展開の効きも過去データから推定する
2. 対象馬それぞれの全盛期の能力値（直近W年の上位N走）を出す
3. 1回のレース: 能力値 + σ×乱数 + 展開の効き×(脚質−0.5)×ペース で着順を決める（斤量は全馬定量）
4. レポート・期待値レースのアニメーションを出力する

使い方: python scripts/sim_dream_race.py ディープインパクト=2002100816 イクイノックス=2019105219 ...
出力: sim/report.md, sim/result.json, sim/race.mp4, sim/frame_*.jpg
numpy / Pillow は使わない（プロジェクトルール）。描画は ffmpeg drawtext のみ。
"""

import glob
import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sim_model as sm

SIM_DIR = Path("sim")
TARGET = {"venue": "東京", "distance": 2400, "going": "良", "label": "東京 芝2400m（良）"}
N_RUNS = 10000
COLORS = ["#E53935", "#1E88E5", "#FDD835", "#43A047", "#FFFFFF", "#8E24AA", "#FB8C00", "#00ACC1"]


# ---------------------------------------------------------------------------
# モンテカルロ
# ---------------------------------------------------------------------------
def perf(h: dict, z: float, fast: float, sigma: float, beta: float) -> float:
    return h["mu"] + sigma * z + beta * (h["style"] - 0.5) * fast


def monte_carlo(horses: dict, sigma: float, beta: float, fast_sd: float, seed: int = 20261012) -> dict:
    rnd = random.Random(seed)
    names = list(horses)
    rank_counts = {n: [0] * len(names) for n in names}
    for _ in range(N_RUNS):
        fast = rnd.gauss(0, fast_sd)
        order = sorted(names, key=lambda n: -perf(horses[n], rnd.gauss(0, 1), fast, sigma, beta))
        for i, n in enumerate(order):
            rank_counts[n][i] += 1
    return rank_counts


# ---------------------------------------------------------------------------
# レポート
# ---------------------------------------------------------------------------
def fmt_time(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def write_report(horses, sel, rank_counts, base_time, race) -> None:
    names = sorted(horses, key=lambda n: -rank_counts[n][0])
    tr, te = sel["train"], sel["test"]
    c = sel["model"].coef
    w_label = "全期間" if sel["w"] >= 99 else f"直近{sel['w']}年"
    tol_label = "制限なし" if sel["tol"] >= 9999 else f"{TARGET['distance']}m±{sel['tol']}m"
    md = [f"# 夢の対決シミュレーション（試作3号） {TARGET['label']}", "",
          "## 結果", "", "| 馬 | 勝率 | 2着内 | 3着内 | 全盛期の能力値 | 全盛期 | 脚質(0逃げ〜1追込) |",
          "|---|---:|---:|---:|---:|---|---:|"]
    for n in names:
        rc, h = rank_counts[n], horses[n]
        md.append(f"| {n} | {rc[0] / N_RUNS:.1%} | {sum(rc[:2]) / N_RUNS:.1%} | {sum(rc[:3]) / N_RUNS:.1%} | "
                  f"{h['mu']:+.2f} | 〜{h['peak_date']} | {h['style']:.2f} |")
    md += ["", "能力値: 平均的なG1勝ち馬より2400m換算で何秒速いか（+1.0 ≒ 6馬身）", "",
           "## 代表レース（調子・展開が平均的な場合）", "", "| 着 | 馬 | タイム | 着差 |", "|---|---|---|---|"]
    win_t = race[0][1]
    for i, (n, t) in enumerate(race, 1):
        md.append(f"| {i} | {n} | {fmt_time(t)} | {'' if i == 1 else f'+{t - win_t:.1f}秒'} |")
    md += ["", "## モデルの精度（バックテスト）", "",
           "各重賞について、そのレースより前の成績だけで出走馬の能力値を出し、実際の着順と比べた。",
           "", "| 期間 | 2頭比較の正答率（モデル） | 同（単勝人気） | 比較した組 | 1位評価の勝率（モデル） | 1番人気の勝率 | 対象レース |",
           "|---|---:|---:|---:|---:|---:|---:|",
           f"| 学習 2008〜2018 | {tr['acc_model']:.1%} | {tr['acc_market']:.1%} | {tr['pairs']:,} | "
           f"{tr['win_model']:.1%} | {tr['win_market']:.1%} | {tr['n_full']} |",
           f"| 検証 2019〜2025 | {te['acc_model']:.1%} | {te['acc_market']:.1%} | {te['pairs']:,} | "
           f"{te['win_model']:.1%} | {te['win_market']:.1%} | {te['n_full']} |", "",
           f"- 選ばれた設定: ペース補正 **{sel['pace']}** / 能力値 = {w_label}の上位**{sel['n']}走** / 縮小 K={sel['k']}"
           f" / 距離 {tol_label}",
           f"- 調子の波 σ = {sel['sigma']:.2f}秒（対数損失 学習 {sel['train_loss']:.4f} / 検証 {sel['test_loss']:.4f}）",
           f"- 展開の効き = {sel['style_beta']:+.3f}（前半600mが1秒速いとき、追込は逃げより {sel['style_beta']:+.2f}秒有利）",
           f"- 基準データ: {sel['model'].n_rows}レース / ペース係数 {c['pace']:+.3f}", "",
           "### ペース補正ごとの最良設定（学習期間の正答率）", "", "| ペース補正 | 正答率 | 上位N走 | 期間 | K |",
           "|---|---:|---:|---|---:|"]
    for pace, (acc, n, w, k, tol) in sel["best_by_pace"].items():
        md.append(f"| {pace} | {acc:.2%} | {n} | {'全' if w >= 99 else w}年 | {k} |")
    md += ["", "### 距離の絞り込みごとの最良設定", ""] + [
        f"- 距離{'制限なし' if tol >= 9999 else f'±{tol}m'}: {acc:.2%}（{pace} / 上位{n}走 / {'全' if w >= 99 else w}年 / K={k}）"
        for tol, (acc, pace, n, w, k) in sel["best_by_tol"].items()]
    md += ["", "### 上位10設定", ""] + [
        f"- {acc:.2%}  {pace} / 上位{n}走 / {'全' if w >= 99 else w}年 / K={k} / 距離{'制限なし' if tol >= 9999 else f'±{tol}m'}"
        for acc, pace, n, w, k, tol, _ in sel["top"]]
    md += ["", "## 馬場の速さ（年ごと、2023年との差・秒）", "",
           " / ".join(f"{y}: {c.get(f'y:{y}', 0.0):+.2f}" for y in sel["model"].years + [sm.REF_YEAR]), "",
           "## 各馬のスピード値（重賞の全成績）", ""]
    for n in names:
        md += [f"### {n}", "", "| 値 | 日付 | レース | 着 | コース | 馬場 | 斤量 | タイム | 上がり |",
               "|---:|---|---|---:|---|---|---:|---|---:|"]
        for date, fig, r, x in horses[n]["history"]:
            md.append(f"| {fig:+.2f} | {date} | {r['race_name']} | {x['pos']} | {r['venue']}{r['distance']} | "
                      f"{r['going']} | {x['weight']} | {fmt_time(x['time'])} | {x['last3f']} |")
        md.append("")
    (SIM_DIR / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (SIM_DIR / "result.json").write_text(json.dumps({
        "win_rate": {n: rank_counts[n][0] / N_RUNS for n in names},
        "ratings": {n: {"mu": horses[n]["mu"], "style": horses[n]["style"], "peak": horses[n]["peak_date"]}
                    for n in names},
        "model": {k: sel[k] for k in ("pace", "n", "w", "k", "tol", "sigma", "style_beta")},
        "backtest": {p: {k: v for k, v in sel[p].items() if k != "diffs"} for p in ("train", "test")},
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
    targets = [a.split("=", 1) for a in sys.argv[1:]]
    if not targets:
        sys.exit("usage: sim_dream_race.py 馬名=netkeibaID ...")
    races = sm.load_races()
    sel = sm.select_model(races)
    horses = {}
    for name, hid in targets:
        hist = sel["figs"].get(hid)
        if not hist:
            print(f"[警告] {name}（{hid}）の重賞成績が見つかりません")
            continue
        mu, peak = sm.peak_rating(hist, sel["n"], sel["w"], sel["k"], sel["m0"], TARGET["distance"], sel["tol"])
        styles = [x["style"] for _, _, _, x in hist if x.get("style") is not None]
        horses[name] = {"mu": mu, "peak_date": peak, "style": statistics.mean(styles) if styles else 0.5,
                        "history": hist}
    # 東京2400mの前半600mのばらつき（展開の振れ幅）
    fronts = [r["front"] for r in races if r["venue"] == TARGET["venue"]
              and r["distance"] == TARGET["distance"] and r["front"] is not None]
    fast_sd = statistics.pstdev(fronts) if len(fronts) > 2 else 1.0
    rank_counts = monte_carlo(horses, sel["sigma"], sel["style_beta"], fast_sd)
    # 想定タイム: 基準年・良馬場・平均ペースでの平均的なG1勝ち馬
    c = sel["model"].coef
    base = c[f"c:{TARGET['venue']}{TARGET['distance']}"]
    race = sorted(((n, base - h["mu"]) for n, h in horses.items()), key=lambda x: x[1])
    write_report(horses, sel, rank_counts, base, race)
    print((SIM_DIR / "report.md").read_text(encoding="utf-8")[:3000])
    render(horses, race)


if __name__ == "__main__":
    main()
