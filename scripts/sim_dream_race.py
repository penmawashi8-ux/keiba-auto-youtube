#!/usr/bin/env python3
"""夢の対決シミュレーション（試作6号）: 学習した能力モデルで、仮想レースを1万回走らせる。

1. sim_learn.build で物差し（基準タイム・着差×相手関係）を作り、複数の手がかりを組み合わせる
   2頭比較のモデルを学習（2008〜2018年）・検証（2019〜2025年、単勝人気と比較）する
2. 対象馬それぞれの全盛期のスコアを出す（その日の事情に当たる手がかりは全馬同じにそろえる）
3. 1回のレース = スコア + ガンベル分布の乱数 で着順を決める（学習した2頭比較の確率と一致）
4. 1万回のうち代表的な1回をレース映像にし、前提条件・注意事項 → レース → 結果・分析 の動画を作る
   （描画は sim_render.py、オリジナルBGMは sim_bgm.py）

使い方: python scripts/sim_dream_race.py ディープインパクト=2002100816 イクイノックス=2019105219 ...
出力: sim/report.md, sim/result.json, sim/race.mp4, sim/frame_*.jpg
numpy / Pillow は使わない（プロジェクトルール）。描画は ffmpeg drawtext のみ。
"""

import glob
import json
import math
import random
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sim_learn as sl
import sim_model as sm
import sim_bgm as sb
import sim_render as sr

SIM_DIR = Path("sim")
TARGET = {"venue": "東京", "distance": 2400, "going": "良", "label": "東京 芝2400m（良）"}
N_RUNS = 10000


# ---------------------------------------------------------------------------
# モンテカルロ（スコア + ガンベル乱数 → 2頭比較の確率がロジスティックになる）
# ---------------------------------------------------------------------------
def monte_carlo(horses: dict, seed: int = 20261012) -> dict:
    rnd = random.Random(seed)
    names = list(horses)
    rank_counts = {n: [0] * len(names) for n in names}
    for _ in range(N_RUNS):
        perf = {n: horses[n]["score"] - math.log(-math.log(rnd.random() or 1e-12)) for n in names}
        for i, n in enumerate(sorted(names, key=lambda n: -perf[n])):
            rank_counts[n][i] += 1
    return rank_counts


# ---------------------------------------------------------------------------
# レポート
# ---------------------------------------------------------------------------
def fmt_time(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def write_report(horses, kit, rank_counts, race) -> None:
    names = sorted(horses, key=lambda n: -rank_counts[n][0])
    tr, te, pm = kit["train"], kit["test"], kit["pm"]
    md = [f"# 夢の対決シミュレーション（試作5号） {TARGET['label']}", "",
          "## 結果", "", "| 馬 | 勝率 | 2着内 | 3着内 | 全盛期のスコア | 全盛期 | 脚質(0逃げ〜1追込) |",
          "|---|---:|---:|---:|---:|---|---:|"]
    for n in names:
        rc, h = rank_counts[n], horses[n]
        md.append(f"| {n} | {rc[0] / N_RUNS:.1%} | {sum(rc[:2]) / N_RUNS:.1%} | {sum(rc[:3]) / N_RUNS:.1%} | "
                  f"{h['score']:+.2f} | 〜{h['peak_date']} | {h['style']:.2f} |")
    md += ["", "スコア: 学習した重み × 手がかり。差が1あると、2頭比較で上の馬が先着する確率は約73%",
           f"（スコア差1 ≒ {tr['sec_per_score']:.2f}秒差 ≒ {tr['sec_per_score'] * 6:.1f}馬身。学習期間の実際の着差から換算）", "",
           "## 代表レース（スコアどおりに走った場合）", "", "| 着 | 馬 | タイム | 着差 |", "|---|---|---|---|"]
    win_t = race[0][1]
    for i, (n, t) in enumerate(race, 1):
        md.append(f"| {i} | {n} | {fmt_time(t)} | {'' if i == 1 else f'+{t - win_t:.1f}秒'} |")
    md += ["", "## モデルの精度（バックテスト）", "",
           "各重賞について、そのレースより前の成績だけで手がかりを作り、実際の着順と比べた。",
           "", "| 期間 | 2頭比較の正答率（モデル） | 同（単勝人気） | 比較した組 | 対数損失 | 1位評価の勝率 | 1番人気の勝率 | 対象レース |",
           "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for label, ev in (("学習 2008〜2018", tr), ("検証 2019〜2025", te)):
        md.append(f"| {label} | {ev['acc_model']:.1%} | {ev['acc_market']:.1%} | {ev['pairs']:,} | "
                  f"{ev['logloss']:.4f} | {ev['win_model']:.1%} | {ev['win_market']:.1%} | {ev['n_full']} |")
    md += ["", "### 手がかりの重み（標準化した値1つ分あたり。正 = 大きいほど先着しやすい）", "",
           "| 手がかり | 重み | 夢の対決での扱い |", "|---|---:|---|"]
    for name, w in sorted(zip(sl.FEATS, pm.w), key=lambda t: -abs(t[1])):
        how = "全馬同じ値" if name in kit["situational"] else ("東京2400mに合わせて計算" if "適性" in name else "全盛期の値")
        md.append(f"| {name} | {w:+.3f} | {how} |")
    md += ["", "### 年齢曲線（同じ馬の中での変化、4歳0か月 = 0、秒）", "",
           " / ".join(f"{a:.2f}歳〜: {e:+.2f}" for a, e in zip(sl.AGE_BINS, kit["age_eff"])), "",
           f"- 展開の効き（スピード値の補正に使用）: {kit['beta']:+.3f}",
           f"- 基準データ: {kit['model'].n_rows:,}レース", ""]
    md += ["## 各馬の全盛期の手がかり", "", "| 手がかり | " + " | ".join(names) + " |",
           "|---|" + "---:|" * len(names)]
    for i, name in enumerate(sl.FEATS):
        md.append(f"| {name} | " + " | ".join(f"{horses[n]['feats'][i]:.2f}" for n in names) + " |")
    md += ["", "## 各馬のスピード値（着差×相手関係、JRA芝1400〜3600mの全成績）", ""]
    for n in names:
        md += [f"### {n}", "", "| 値 | 日付 | レース | 着 | コース | 馬場 |", "|---:|---|---|---:|---|---|"]
        for date, fig, r, x in horses[n]["history"]:
            md.append(f"| {fig:+.2f} | {date} | {r['race_name']} | {x['pos']} | {r['venue']}{r['distance']} | {r['going']} |")
        md.append("")
    (SIM_DIR / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (SIM_DIR / "result.json").write_text(json.dumps({
        "win_rate": {n: rank_counts[n][0] / N_RUNS for n in names},
        "horses": {n: {"score": horses[n]["score"], "peak": horses[n]["peak_date"], "style": horses[n]["style"],
                       "features": dict(zip(sl.FEATS, horses[n]["feats"]))} for n in names},
        "weights": dict(zip(sl.FEATS, pm.w)),
        "backtest": {"train": tr, "test": te},
        "race": race,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# 動画（説明ページ → レース → 結果ページ）
# ---------------------------------------------------------------------------
STYLE_NAMES = [(0.25, "逃げ・先行"), (0.45, "先行"), (0.65, "好位〜中団"), (0.8, "差し"), (1.01, "追込")]
JRA_VENUES = {"札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"}


def career_stats(horse_id: str, careers: dict, graded: list[dict]) -> dict:
    """分析ページ用の、だれが見てもわかる成績（JRAの芝・平地の全成績）"""
    rows = [r for r in careers.get(horse_id, [])
            if r.get("venue") in JRA_VENUES and r.get("surface") == "芝" and r.get("pos")]
    wins = [r for r in rows if r["pos"] == 1]
    g1 = [r for r in rows if "(GI)" in r["race_name"] or "(G1)" in r["race_name"]]
    mid = [r for r in rows if 2200 <= (r.get("distance") or 0) <= 2600]
    tokyo = [r for r in rows if r["venue"] == "東京"]
    win_m = [-r["margin"] for r in wins if isinstance(r.get("margin"), (int, float)) and r["margin"] < 0]
    lose_m = [r["margin"] for r in rows if r["pos"] > 1 and isinstance(r.get("margin"), (int, float))]
    # 上がり最速: 全出走馬の上がりがそろう G1/G2 で、出走馬の中でいちばん速かった割合
    fast = runs = 0
    for race in graded:
        mine = next((x for x in race["runners"] if x["horse_id"] == horse_id), None)
        if not mine or not mine.get("last3f"):
            continue
        runs += 1
        fast += mine["last3f"] <= min(x["last3f"] for x in race["runners"] if x.get("last3f"))
    return {"starts": len(rows), "wins": len(wins), "top3": sum(r["pos"] <= 3 for r in rows),
            "g1_wins": sum(r["pos"] == 1 for r in g1), "g1_starts": len(g1),
            "win_margin": statistics.mean(win_m) if win_m else 0.0,
            "lose_margin": statistics.mean(lose_m) if lose_m else 0.0,
            "fast_rate": fast / runs if runs else 0.0, "fast_runs": runs,
            "mid": (len(mid), sum(r["pos"] == 1 for r in mid)),
            "tokyo": (len(tokyo), sum(r["pos"] == 1 for r in tokyo))}


def stat_items(s: dict) -> list[tuple[str, float, str]]:
    """(項目名, 比べる値（大きいほど良い）, 表示する文)"""
    top3 = s["top3"] / s["starts"] if s["starts"] else 0.0
    items = [("G1の勝ち星", s["g1_wins"], f"JRAの芝G1を{s['g1_wins']}勝（{s['g1_starts']}戦）"),
             ("勝ちっぷり", s["win_margin"], f"勝つときは平均{lengths(s['win_margin'])}差をつける"),
             ("負けても崩れない", -s["lose_margin"],
              f"負けたときも勝ち馬から平均{lengths(s['lose_margin'])}差" if s["starts"] > s["wins"] else "負けなし"),
             ("安定感", top3, f"3着以内に入った割合 {top3:.0%}（{s['starts']}戦{s['top3']}回）"),
             ("末脚の切れ", s["fast_rate"], f"G1・G2で上がり最速を出した割合 {s['fast_rate']:.0%}")]
    for key, label in (("mid", "2400m前後の実績"), ("tokyo", "東京コースの実績")):
        n, w = s[key]
        where = "2200〜2600m" if key == "mid" else "東京の芝"
        items.append((label, (w + 0.5) / (n + 1.5), f"{where}で{n}戦{w}勝" if n else f"{where}は走ったことがない"))
    return items


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


def style_name(v: float) -> str:
    return next(name for th, name in STYLE_NAMES if v < th)


def ym(date: str) -> str:
    return f"{date[:4]}年{int(date[5:7])}月"


def lengths(sec: float) -> str:
    """秒差を馬身に（1馬身 ≒ 0.17秒）"""
    return sr.margin_label(sec * 2400 / 143.0)


def before_pages(horses: dict, kit: dict, famous: list[dict]) -> list[dict]:
    names = list(horses)
    te, tr = kit["test"], kit["train"]
    n_horses = len(kit["rel"])
    n_runs = sum(len(v) for v in kit["rel"].values())
    fi = {name: i for i, name in enumerate(sl.FEATS)}
    pages = [{"sec": 3.5, "items": [
        {"text": "夢の対決シミュレーション", "x": "(w-text_w)/2", "y": 210, "size": 64, "color": "white"},
        {"text": "・".join(names), "x": "(w-text_w)/2", "y": 320, "size": 28, "color": "0xFFF59D"},
        {"text": f"{TARGET['label']}　データで{N_RUNS:,}回走らせたら？", "x": "(w-text_w)/2", "y": 380,
         "size": 30, "color": "white"}]}]

    pages.append(sr.text_page("前提条件① レースの条件", [
        "・競馬場：東京競馬場 芝2400m（左回り＝反時計回り）",
        f"・コース：1周 約{sr.LAP_M:,.0f}m、最後の直線 {sr.STRAIGHT_M}m",
        f"・スタート：正面スタンド前（ゴールの約{TARGET['distance'] - sr.LAP_M:.0f}m手前）→ ゴール前を通過して1周",
        "・馬場状態：良（芝が乾いた、いちばん速い状態）",
        f"・頭数：{len(names)}頭立て（枠順・内外の有利不利はなし）",
        "・斤量：全馬「定量」＝年齢・性別に応じた通常の重さ（牝馬は2kg軽い）",
        "・馬場の速さ：2023年の東京の馬場に換算（年ごとに速くなった分を補正）",
        "・展開：1万回それぞれでペースが変わる（速い流れ・遅い流れの両方を含む）",
        "・考慮しないもの：騎手・調教・当日の体調・天候・風",
    ], 10.0))

    rows = [[{"text": "馬名", "x": 60, "color": "0xBDBDBD"}, {"text": "全盛期", "x": 330, "color": "0xBDBDBD"},
             {"text": "当時の年齢", "x": 520, "color": "0xBDBDBD"}, {"text": "JRA芝G1勝利", "x": 680, "color": "0xBDBDBD"},
             {"text": "3着内率", "x": 870, "color": "0xBDBDBD"}, {"text": "脚質", "x": 1010, "color": "0xBDBDBD"}]]
    for i, (n, h) in enumerate(horses.items()):
        f = h["feats"]
        age = int(h["peak_date"][:4]) - (sm.birth_year(h["id"]) or int(h["peak_date"][:4]))
        rows.append([{"text": n, "x": 60, "badge": i},
                     {"text": ym(h["peak_date"]), "x": 330}, {"text": f"{age}歳", "x": 520},
                     {"text": f"{int(f[fi['G1勝数']])}勝", "x": 680},
                     {"text": f"{f[fi['3着内率']]:.0%}", "x": 870}, {"text": style_name(h["style"]), "x": 1010}])
    rows += ["", {"text": "・全盛期＝現役中で、データ上の能力スコアがいちばん高かった時点", "size": 22},
             {"text": "・G1勝利と3着内率は、全盛期までのJRA芝1400〜3600mの成績から計算（海外・地方・ダートは含まない）",
              "size": 20},
             {"text": "・脚質は、レース序盤の位置取り（通過順位）の平均から判定", "size": 22}]
    pages.append(sr.text_page("前提条件② 出走馬と「全盛期」", rows, 11.0))

    pages.append(sr.text_page("前提条件③ 使ったデータと計算方法", [
        f"・データ：netkeibaで公開されている成績（2003〜2025年）",
        f"　JRA重賞に出走した{n_horses:,}頭の、芝1400〜3600m・全クラスの全成績（のべ{n_runs:,}走）",
        "",
        "・計算の流れ",
        "　① 1走ごとに「どれだけ強い走りだったか」を点数にする",
        "　　強い馬に勝てば高得点、弱い相手に負ければ低得点（一緒に走った馬の実力と着差から計算）",
        "　② 条件の差をならす：年ごとの馬場の速さ・ペースの向き不向き・背負った重さ・年齢",
        "　③ 過去のレースで「2頭のうちどちらが先にゴールするか」を当てる式を作る",
        "　　使ったデータ：最近の点数・走破タイム・距離の得意不得意・重賞の勝ち星・3着以内の割合など",
        "　④ 各馬のいちばん強かった時期（全盛期）の力で、調子を毎回ランダムに変えて1万回走らせる",
    ], 12.0, size=23))

    rows = [f"・2008〜2018年の重賞で学習 → 2019〜2025年の重賞{te['n_full'] if te['n_full'] else ''}レースで検証",
            f"・2頭のどちらが先着するか（{te['pairs']:,}組）",
            {"text": f"　このモデル {te['acc_model']:.1%}　／　単勝人気 {te['acc_market']:.1%}　／　でたらめなら 50%",
             "color": "0xFFF59D"},
            "　（単勝人気は騎手・調教・当日の気配まで織り込んだ予想。それに近い精度）", "",
            "・実例：レース前の情報だけで出した評価順（左から評価の高い順）"]
    for c in famous[:4]:
        top = "　→　".join(f"{h}（{p:.0%}）＝{pos}着" for h, p, pos, _pop in c["top"])
        rows.append({"text": f"　{c['race']}", "size": 21, "color": "0xFFF59D"})
        rows.append({"text": f"　　評価順：{top}", "size": 20})
    pages.append(sr.text_page("前提条件④ このモデルの精度", rows, 12.0, size=23))

    pages.append(sr.text_page("注意事項", [
        "・これは実際のレース映像ではありません。公開データから計算したシミュレーションです",
        f"・映像のレースは、{N_RUNS:,}回走らせたうちの代表的な1回です",
        "　毎回この着順になるわけではありません（勝率は最後に表示します）",
        "・騎手・調教・当日の状態・枠順・馬場の内外の差は考慮していません",
        "・海外（凱旋門賞など）・地方・ダートのレースは計算に含めていません",
        "・時代が違う馬の比較には、どうしても不確かさが残ります",
        "・どの馬が最強かを断定するものではありません。楽しみ方のひとつとしてご覧ください",
    ], 10.0))
    return pages


def pick_display_race(horses: dict, rank_counts: dict, base: float, k: float, ref: float,
                      seed: int = 7) -> list[tuple[str, float]]:
    """映像で見せるレースを、1万回のシミュレーションと同じ方法で走らせた中から1回選ぶ。

    条件: 最も勝率が高い馬が勝ち、1着〜最下位の差が約3〜8馬身（0.5〜1.4秒）。
    その中で、着順が「全馬スコアどおり」の順に最も近い回を選ぶ（毎回同じ結果になるよう乱数は固定）。
    """
    rnd = random.Random(seed)
    names = list(horses)
    fav = max(names, key=lambda n: rank_counts[n][0])
    expected = sorted(names, key=lambda n: -horses[n]["score"])
    best = None
    for _ in range(N_RUNS):
        perf = {n: horses[n]["score"] - math.log(-math.log(rnd.random() or 1e-12)) for n in names}
        order = sorted(names, key=lambda n: -perf[n])
        times = {n: base - k * (perf[n] - ref) for n in names}
        spread = times[order[-1]] - times[order[0]]
        if order[0] != fav or not (0.5 <= spread <= 1.4):
            continue
        dist = sum(abs(order.index(n) - expected.index(n)) for n in names)
        if best is None or dist < best[0]:
            best = (dist, sorted(times.items(), key=lambda t: t[1]))
        if dist == 0:
            break
    if best is None:     # 条件に合う回が無ければ、スコアどおりの順
        return sorted(((n, base - k * (horses[n]["score"] - ref)) for n in names), key=lambda t: t[1])
    return best[1]


def after_pages(horses: dict, kit: dict, rank_counts: dict, race: list[tuple[str, float]]) -> list[dict]:
    """結果ページ。1画面の情報は少なめ・文字は大きめにする"""
    names = list(horses)
    idx = {n: i for i, n in enumerate(names)}
    win_t = race[0][1]
    rows = [[{"text": "着順", "x": 90, "color": "0xBDBDBD"}, {"text": "馬名", "x": 210, "color": "0xBDBDBD"},
             {"text": "タイム", "x": 660, "color": "0xBDBDBD"}, {"text": "着差", "x": 880, "color": "0xBDBDBD"}]]
    for k, (n, t) in enumerate(race, 1):
        rows.append([{"text": f"{k}着", "x": 90, "size": 34}, {"text": n, "x": 210, "size": 34, "badge": idx[n]},
                     {"text": fmt_time(t), "x": 660, "size": 34},
                     {"text": "" if k == 1 else lengths(t - win_t), "x": 880, "size": 34}])
    rows += ["", {"text": f"※映像のレースは{N_RUNS:,}回のうちの代表的な1回。毎回この着順になるわけではありません",
                  "size": 21, "color": "0xBDBDBD"}]
    pages = [sr.text_page("レース結果", rows, 8.0, size=30, top=130)]

    order = sorted(names, key=lambda n: -rank_counts[n][0])
    top_rate = rank_counts[order[0]][0] / N_RUNS
    rows = []
    for n in order:
        p = rank_counts[n][0] / N_RUNS
        rows.append([{"text": n, "x": 60, "size": 32, "badge": idx[n]},
                     {"text": f"勝率 {p:.1%}", "x": 520, "size": 34, "color": "0xFFF59D"}])
        rows.append({"text": "■" * max(1, round(p / max(top_rate, 1e-9) * 34)), "x": 110, "size": 16,
                     "color": sr.COLORS[idx[n]], "extra": sr.DOT_BORDER[idx[n]]})
    pages.append(sr.text_page(f"{N_RUNS:,}回走らせた勝率", rows, 9.0, size=30, top=120))

    rows = [[{"text": "", "x": 60}, {"text": "勝率", "x": 470, "color": "0xBDBDBD"},
             {"text": "2着以内", "x": 640, "color": "0xBDBDBD"}, {"text": "3着以内", "x": 840, "color": "0xBDBDBD"}]]
    for n in order:
        rc = rank_counts[n]
        rows.append([{"text": n, "x": 60, "size": 30, "badge": idx[n]},
                     {"text": f"{rc[0] / N_RUNS:.1%}", "x": 470, "size": 30},
                     {"text": f"{sum(rc[:2]) / N_RUNS:.1%}", "x": 640, "size": 30},
                     {"text": f"{sum(rc[:3]) / N_RUNS:.1%}", "x": 840, "size": 30}])
    pages.append(sr.text_page("2着以内・3着以内に入る確率", rows, 8.0, size=28, top=130))

    # 1頭1画面の分析
    # 強み・弱みは、だれでも確かめられる実際の成績で比べる（JRA芝の通算成績）
    items = {n: stat_items(horses[n]["stats"]) for n in names}
    for rank, n in enumerate(order, 1):
        h, s = horses[n], horses[n]["stats"]
        strong, weak = [], []
        for j, (label, val, text) in enumerate(items[n]):
            vals = [items[m][j][1] for m in names]
            better = sum(v > val + 1e-9 for v in vals)
            if better == 0:
                strong.append((label, text))
            elif better == len(names) - 1:
                weak.append((label, text))
        best = max((x for x in h["history"] if x[0] <= h["peak_date"]), key=lambda x: x[1])
        rows = [[{"text": n, "size": 40, "badge": idx[n], "x": 60},
                 {"text": f"勝率 {rank_counts[n][0] / N_RUNS:.1%}（{rank}位）", "x": 700, "size": 34,
                  "color": "0xFFF59D"}],
                {"text": f"JRA芝 {s['starts']}戦{s['wins']}勝 ／ 脚質：{style_name(h['style'])}", "size": 24,
                 "color": "0xE0E0E0"},
                {"text": f"データ上いちばん強かったレース：{ym(best[0])} {best[2]['race_name']}（{best[3]['pos']}着）",
                 "size": 24, "color": "0xE0E0E0"},
                "",
                {"text": f"{len(names)}頭の中でいちばん", "size": 26, "color": "0x80CBC4"}]
        rows += ([{"text": f"　◎ {lb}：{tx}", "size": 27} for lb, tx in strong[:3]]
                 or [{"text": "　とび抜けた項目はなし（バランス型）", "size": 27}])
        rows += ["", {"text": f"{len(names)}頭の中ではいちばん下", "size": 26, "color": "0xEF9A9A"}]
        rows += ([{"text": f"　△ {lb}：{tx}", "size": 27} for lb, tx in weak[:2]]
                 or [{"text": "　なし", "size": 27}])
        pages.append(sr.text_page(f"分析 {rank}/{len(names)}", rows, 8.0, top=110))

    first, second, last = order[0], order[1], order[-1]
    p1, p2, p5 = (rank_counts[n][0] / N_RUNS for n in (first, second, last))
    # 1万回の勝率の誤差は ±0.4ポイント程度（標準誤差）。差が1ポイント未満なら「並んだ」と書く
    if p1 - p2 < 0.01:
        head = [{"text": f"{first}と{second}が", "size": 40, "color": "0xFFF59D"},
                {"text": f"勝率{p1:.1%}・{p2:.1%}で並んだ", "size": 40, "color": "0xFFF59D"},
                "", {"text": "差は誤差の範囲。どちらが勝ってもおかしくない互角", "size": 30}]
    else:
        head = [{"text": f"最も多く勝ったのは {first}", "size": 40, "color": "0xFFF59D"},
                {"text": f"勝率 {p1:.1%}", "size": 40, "color": "0xFFF59D"},
                "", {"text": f"2位の{second}は{p2:.1%}（差 {(p1 - p2) * 100:.1f}ポイント）", "size": 30}]
    pages.append(sr.text_page("まとめ", head, 7.0, top=150))
    pages.append(sr.text_page("まとめ", [
        {"text": f"最下位評価の{last}でも", "size": 34},
        {"text": f"{N_RUNS:,}回中{rank_counts[last][0]:,}回は勝つ（{p5:.1%}）", "size": 34, "color": "0xFFF59D"},
        "",
        {"text": "データ上の差はわずか。どの馬にも十分に勝つチャンスがある", "size": 30},
        "", "",
        {"text": "あなたの予想は？コメントで教えてください", "size": 36, "color": "0xFFEB3B"},
    ], 8.0, top=150))
    return pages


# ---------------------------------------------------------------------------
# 実況の読み上げ（edge-tts）と音声の合成
# ---------------------------------------------------------------------------
VOICE = "ja-JP-KeitaNeural"     # 実況は男性の声


def make_tts(work: Path):
    """実況を読み上げる関数を返す。edge-tts が使えない環境では None を返す関数（テロップだけになる）"""
    import asyncio
    import hashlib
    import os
    import ssl
    try:
        import edge_tts
        import edge_tts.communicate as ec
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        extra = False
        # プロキシ経由の環境では、その証明書も信頼する（GitHub Actions では読めないので飛ばす）
        for ca in (os.environ.get("SSL_CERT_FILE"), os.environ.get("REQUESTS_CA_BUNDLE"), "/root/.ccr/ca-bundle.crt"):
            try:
                if ca and Path(ca).is_file():
                    ctx.load_verify_locations(cafile=ca)
                    extra = True
            except OSError:
                pass
        if extra:
            ec._SSL_CTX = ctx
    except Exception as e:     # noqa: BLE001
        print(f"[警告] edge-tts が使えないため、実況はテロップのみ: {e}")
        return lambda text: None
    work.mkdir(parents=True, exist_ok=True)
    state = {"ok": True}

    def speak(text: str):
        if not state["ok"]:
            return None
        out = work / f"voice_{hashlib.md5(text.encode()).hexdigest()[:10]}.mp3"
        try:
            if not out.exists():
                comm = edge_tts.Communicate(text, VOICE, rate="+18%", pitch="+4Hz")
                asyncio.run(comm.save(str(out)))
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                        "default=noprint_wrappers=1:nokey=1", str(out)],
                                       capture_output=True, text=True, check=True).stdout)
            return str(out), dur
        except Exception as e:     # noqa: BLE001
            print(f"[警告] 実況の読み上げに失敗（以後はテロップのみ）: {e}")
            state["ok"] = False
            return None

    return speak


def mix_audio(video: Path, bgm: Path, marks: dict, out: Path) -> None:
    """BGM（実況中は自動で音量を下げる）＋実況＋歓声（最後の直線で大きくなり、ゴールで最高潮）"""
    total = marks["total"]
    s0, st, gl, end = marks["race_start"], marks["straight"], marks["goal"], marks["race_end"]
    env = sr.piecewise([(0.0, 0.0), (s0 - 0.3, 0.0), (s0, 0.55), (s0 + 2.0, 0.22), (st - 1.0, 0.25),
                        (gl - 0.3, 0.95), (gl + 1.0, 1.0), (gl + 5.0, 0.4), (end + 0.5, 0.15), (end + 2.5, 0.0)])
    inputs = ["-i", str(video), "-i", str(bgm)]
    chains = [f"anoisesrc=color=pink:sample_rate=44100:amplitude=0.6:duration={total:.2f},"
              f"bandpass=f=1100:width_type=h:w=1800,lowpass=f=5000,volume=volume='{env}':eval=frame,"
              f"aformat=channel_layouts=stereo[crowd]"]
    voices = marks.get("voice") or []
    for j, (t0, path) in enumerate(voices):
        inputs += ["-i", path]
        ms = int(t0 * 1000)
        chains.append(f"[{2 + j}:a]aformat=sample_rates=44100:channel_layouts=stereo,adelay={ms}|{ms},"
                      f"volume=2.2[v{j}]")
    chains.append("[1:a]aformat=sample_rates=44100:channel_layouts=stereo[bg]")
    if voices:
        chains.append("".join(f"[v{j}]" for j in range(len(voices))) +
                      f"amix=inputs={len(voices)}:normalize=0,apad=whole_dur={total:.2f},asplit=2[vo][sc]")
        chains.append("[bg][sc]sidechaincompress=threshold=0.03:ratio=8:attack=30:release=500[bgd]")
        chains.append("[bgd][vo][crowd]amix=inputs=3:normalize=0,alimiter=limit=0.95[a]")
    else:
        chains.append("[bg][crowd]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]")
    script = video.parent / "work" / "audio_filter.txt"
    script.write_text(";".join(chains), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex_script", str(script),
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    "-t", f"{total:.2f}", str(out)], check=True)


SHORT_NAMES = {"ディープインパクト": "ディープ", "オルフェーヴル": "オルフェ", "キタサンブラック": "キタサン",
               "アーモンドアイ": "アーモンド", "イクイノックス": "イクイノ"}


def main() -> None:
    targets = [a.split("=", 1) for a in sys.argv[1:]]
    if not targets:
        sys.exit("usage: sim_dream_race.py 馬名=netkeibaID ...")
    races = sm.load_races()
    kit = sl.build(races)
    careers = json.loads((SIM_DIR / "horses_cache.json").read_text(encoding="utf-8"))
    graded = json.loads((SIM_DIR / "races.json").read_text(encoding="utf-8"))
    horses = {}
    for name, hid in targets:
        peak = sl.peak_score(kit, hid, TARGET["distance"], TARGET["venue"])
        if not peak:
            print(f"[警告] {name}（{hid}）の成績が見つかりません")
            continue
        hist = kit["rel"][hid]
        styles = [x["style"] for _, _, _, x in hist if x.get("style") is not None]
        horses[name] = {"id": hid, "score": peak[0], "peak_date": peak[1], "feats": peak[2],
                        "style": statistics.mean(styles) if styles else 0.5, "history": hist,
                        "stats": career_stats(hid, careers, graded)}
    rank_counts = monte_carlo(horses)
    # タイムへの換算: 平均的なG1勝ち馬（東京2400m・良・基準年の想定タイム）からのスコア差を秒に直す
    base = kit["model"].coef[f"c:{TARGET['venue']}{TARGET['distance']}"]
    k = kit["train"]["sec_per_score"]
    expected = sorted(((n, base - k * (h["score"] - kit["g1_winner_score"])) for n, h in horses.items()),
                      key=lambda x: x[1])
    shown = pick_display_race(horses, rank_counts, base, k, kit["g1_winner_score"])
    write_report(horses, kit, rank_counts, expected)
    print((SIM_DIR / "report.md").read_text(encoding="utf-8")[:3500])
    print("映像のレース:", " / ".join(f"{n} {fmt_time(t)}" for n, t in shown))

    names = list(horses)
    finish = dict(shown)
    after = after_pages(horses, kit, rank_counts, shown)
    video = SIM_DIR / "race_video.mp4"
    marks = sr.render(video, SIM_DIR / "work", find_font(), names,
                      [horses[n]["style"] for n in names], [finish[n] for n in names],
                      before_pages(horses, kit, sl.famous_checks(kit, races)), after, dist=TARGET["distance"],
                      frames={"title": 1.0, "premise1": 7.0, "premise2": 18.0, "premise3": 30.0, "premise4": 42.0,
                              "caution": 52.0, "gate": ("gate", 1.0), "start": ("race_start", 0.5),
                              "corner": ("race_start", 24.0), "stretch": ("straight", 1.5), "slow": ("slow", 3.0),
                              "goal": ("goal", 0.3), "result1": ("race_end", 4.0), "result2": ("race_end", 12.0),
                              "analysis1": ("race_end", 28.0), "summary": ("total", -4.0)},
                      short=[SHORT_NAMES.get(n, n[:5]) for n in names], tts=make_tts(SIM_DIR / "work" / "voice"))
    print("実況の読み上げ:", len(marks["voice"]), "本")
    # BGM: 説明（落ち着いた曲）→ ゲートイン〜ゴール（レース曲）→ ファンファーレ → 結果（明るめの曲）
    fan = 3.5
    sections = [("calm", marks["gate"]), ("race", marks["goal"] - marks["gate"]), ("fanfare", fan),
                ("result", marks["total"] - marks["goal"] - fan)]
    bgm = sb.make_bgm(sections, SIM_DIR / "work" / "bgm.m4a")
    mix_audio(video, bgm, marks, SIM_DIR / "race.mp4")
    print(f"保存: {SIM_DIR / 'race.mp4'}")


if __name__ == "__main__":
    main()
