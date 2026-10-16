#!/usr/bin/env python3
"""実際のレース（例: 秋華賞）のシミュレーション動画。夢の対決と同じ能力モデル・描画を使う。

違い:
- 各馬の力は「全盛期」ではなく「レース当日時点」のスコア（直近の成績を重く見る）
- コース・距離・頭数は出馬表（sim/entries.json）から。18頭立てにも対応
- 枠順確定前は馬番が無いので、出馬表の並び順で仮の番号を付け、画面にも「仮」と出す
  （枠順確定後に sim_entries.py → このスクリプトを再実行すれば正式な馬番になる）

入力: sim/entries.json, sim/races.json, sim/horses_cache.json
出力: sim/real_report.md, sim/real_result.json, sim/real_race.mp4, sim/real_frame_*.jpg
"""

import json
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sim_bgm as sb
import sim_dream_race as sd
import sim_learn as sl
import sim_model as sm
import sim_render as sr

SIM_DIR = Path("sim")
N_RUNS = 10000


def monte_carlo(horses: dict, seed: int = 20261018, upsets: dict | None = None) -> dict:
    """着順の回数を数える。upsets を渡すと「荒れたレース」の記録も入れる:
    評価（スコア）10位以下の馬が勝った回数と、1〜3着の評価順位の合計が最も大きかった回"""
    rnd = random.Random(seed)
    names = list(horses)
    counts = {n: [0] * len(names) for n in names}
    rank = {n: r for r, n in enumerate(sorted(names, key=lambda m: -horses[m]["score"]), 1)}
    for _ in range(N_RUNS):
        perf = {n: horses[n]["score"] - math.log(-math.log(rnd.random() or 1e-12)) for n in names}
        order = sorted(names, key=lambda n: -perf[n])
        for i, n in enumerate(order):
            counts[n][i] += 1
        if upsets is not None:
            upsets["low_winner"] = upsets.get("low_winner", 0) + (rank[order[0]] >= 10)
            upsets["low_top3"] = upsets.get("low_top3", 0) + all(rank[m] >= 6 for m in order[:3])
            sc = sum(rank[m] for m in order[:3])
            if sc > upsets.get("score", 0):
                upsets.update(score=sc, perf=perf, order=order, rank=rank)
    return counts


def pick_display_race(horses: dict, counts: dict, base: float, k: float, ref: float, seed: int = 7):
    """映像のレース: 1万回と同じ方法で走らせた中から、勝率1位の馬が勝ち、着順が
    スコア順に最も近い回を選ぶ。1着〜最下位の差は頭数に応じて（18頭で約1〜3秒）"""
    rnd = random.Random(seed)
    names = list(horses)
    n = len(names)
    lo, hi = 0.5 * (1 + (n - 5) / 13), 1.4 * (1 + (n - 5) / 13)
    fav = max(names, key=lambda m: counts[m][0])
    expected = sorted(names, key=lambda m: -horses[m]["score"])
    best = None
    for _ in range(N_RUNS):
        perf = {m: horses[m]["score"] - math.log(-math.log(rnd.random() or 1e-12)) for m in names}
        order = sorted(names, key=lambda m: -perf[m])
        times = {m: base - k * (perf[m] - ref) for m in names}
        if order[0] != fav or not (lo <= times[order[-1]] - times[order[0]] <= hi):
            continue
        d = sum(abs(order.index(m) - expected.index(m)) for m in names)
        if best is None or d < best[0]:
            best = (d, sorted(times.items(), key=lambda t: t[1]))
    if best is None:
        return sorted(((m, base - k * (horses[m]["score"] - ref)) for m in names), key=lambda t: t[1])
    return best[1]


def short_name(name: str) -> str:
    return name if len(name) <= 5 else name[:4]


# ---------------------------------------------------------------------------
# ページ
# ---------------------------------------------------------------------------
def badge_col(h: dict, x: int, size: int | None = None) -> dict:
    c = {"text": h["name"], "x": x, "badge": h["waku"] - 1, "num": h["num"]}
    if size:
        c["size"] = size
    return c


def before_pages(ent: dict, horses: dict, kit: dict, gate: dict | None = None) -> list[dict]:
    n = len(horses)
    drawn = ent["drawn"]
    course = sr.COURSES[ent["venue"]]
    te = kit["test"]
    lap = course["lap"]
    title = [{"text": f"{ent['race_name']} {ent['date'][:4]}", "x": "(w-text_w)/2", "y": 190, "size": 72,
              "color": "0xFFEB3B"},
             {"text": f"データで{N_RUNS:,}回シミュレーション", "x": "(w-text_w)/2", "y": 300, "size": 44,
              "color": "white"},
             {"text": f"{ent['venue']} 芝{ent['distance']}m・{n}頭立て", "x": "(w-text_w)/2", "y": 380, "size": 32,
              "color": "0xFFF59D"}]
    if not drawn:
        title.append({"text": "※枠順確定前のため、馬番は仮の番号です", "x": "(w-text_w)/2", "y": 450, "size": 26,
                      "color": "0xEF9A9A"})
    pages = [{"sec": 4.0, "items": title}]
    pages.append(sr.text_page("前提条件① レースの条件", [
        f"・競馬場：{course['name']} 芝{ent['distance']}m（{sr.TURN_FULL}・{course['note']}）",
        f"・コース：1周 約{lap:,.0f}m、最後の直線 {course['straight']}m",
        f"・スタート：正面スタンド前（ゴールの約{ent['distance'] - lap:.0f}m手前）→ ゴール前を通過して1周",
        "・馬場状態：良を想定",
        f"・頭数：{n}頭（{'実際の枠順。内外の有利不利も計算に入れる' if gate else '内外の有利不利は計算に入れていない'}）",
        "・斤量：全馬 " + "・".join(sorted({f'{h["weight"]:g}kg' for h in horses.values() if h['weight']})) + "（定量）",
        "・各馬の力：レース当日時点のデータ（直近のレースほど重く見る）",
        "・考慮しないもの：調教・当日の馬体重やパドック・天候・展開の駆け引き",
    ], 10.0))
    rows_all = list(horses.values())
    half = (len(rows_all) + 1) // 2
    for p, chunk in enumerate((rows_all[:half], rows_all[half:]), 1):
        rows = [[{"text": "馬番・馬名", "x": 60, "color": "0xBDBDBD"}, {"text": "騎手", "x": 520, "color": "0xBDBDBD"},
                 {"text": "JRA芝の成績", "x": 700, "color": "0xBDBDBD"}, {"text": "脚質", "x": 1000, "color": "0xBDBDBD"}]]
        for h in chunk:
            st = h["stats"]
            rows.append([badge_col(h, 60), {"text": h["jockey"], "x": 520},
                         {"text": f"{st['starts']}戦{st['wins']}勝", "x": 700},
                         {"text": sd.style_name(h["style"]), "x": 1000}])
        if not drawn:
            rows += ["", {"text": "※馬番は仮（出馬表の並び順）。枠順確定後に正式な馬番で作り直します", "size": 20,
                          "color": "0xEF9A9A"}]
        pages.append(sr.text_page(f"出走馬 {p}/2", rows, 9.0, size=24, top=104))
    if gate:
        rows = [f"・{ent['venue']}芝{ent['distance']}mの過去{gate['n_races']}レース（{gate['from_year']}年〜・全クラス）",
                "　枠（馬番を3等分）× 普段の脚質（序盤の位置取りの平均を3等分）ごとの成績",
                [{"text": "", "x": 90}, {"text": "前に行く馬", "x": 300, "color": "0xBDBDBD"},
                 {"text": "中団の馬", "x": 590, "color": "0xBDBDBD"}, {"text": "後ろから行く馬", "x": 860, "color": "0xBDBDBD"}]]
        cell = {(c["gate"], c["style"]): c for c in gate["cross"]}
        for gname in ("内", "中", "外"):
            row = [{"text": f"{gname}枠", "x": 90, "color": "0xBDBDBD"}]
            for x, sname in ((300, "前"), (590, "中団"), (860, "後方")):
                c = cell.get((gname, sname))
                row.append({"text": f"3着内 {c['top3']:.0%}（{c['runs']}走）" if c else "-", "x": x})
            rows.append(row)
        def edge(g, st):
            v = gate["gate"] * g + gate["style"] * st + gate["gate_x_style"] * g * st
            return (1 / (1 + math.exp(-v)) - 0.5) * 100
        rows += ["", {"text": "・同じくらいの人気の馬どうしで比べた、内枠の先行馬との差（先着する確率）", "color": "0xFFF59D"},
                 f"　内枠の差し馬 {edge(0, 1):+.1f}ポイント ／ 外枠の先行馬 {edge(1, 0):+.1f}ポイント ／ "
                 f"外枠の差し馬 {edge(1, 1):+.1f}ポイント",
                 "・この差を各馬の枠と脚質に合わせて力に足して計算"]
        pages.append(sr.text_page("前提条件 枠順と脚質の影響", rows, 12.0, size=22, top=100))
    pages.append(sr.text_page("前提条件② データと計算方法", [
        "・データ：netkeibaで公開されている成績（2003〜2026年）",
        "　出走馬と、その対戦相手の芝1400〜3600m・全クラスの全成績",
        "",
        "・計算の流れ",
        "　① 1走ごとに「どれだけ強い走りだったか」を点数にする",
        "　　強い馬に勝てば高得点、弱い相手に負ければ低得点（一緒に走った馬の実力と着差から計算）",
        "　② 条件の差をならす：年ごとの馬場の速さ・ペースの向き不向き・背負った重さ・年齢",
        "　③ 過去のレースで「2頭のうちどちらが先にゴールするか」を当てる式を作る",
        "　④ 各馬の今の力で、調子を毎回ランダムに変えて1万回走らせる",
        "",
        f"・精度：2019〜2025年の重賞で検証 → 2頭のどちらが先着するかの正答率 {te['acc_model']:.1%}",
        f"　（単勝人気は {te['acc_market']:.1%}。でたらめなら 50%）",
    ], 12.0, size=23))
    pages.append(sr.text_page("注意事項", [
        "・これは実際のレース映像ではありません。公開データから計算したシミュレーションです",
        f"・映像のレースは、{N_RUNS:,}回走らせたうちの代表的な1回です（毎回この着順ではありません）",
        "・3歳馬はキャリアが短く、データが少ない分だけ結果のぶれが大きくなります",
        "・調教・当日の状態・馬場の内外の差・展開の駆け引きは考慮していません",
        "・馬券の購入を勧めるものではありません。楽しみ方のひとつとしてご覧ください",
    ], 9.0))
    return pages


def after_pages(horses: dict, counts: dict, race: list[tuple[str, float]], drawn: bool,
                upsets: dict | None = None) -> list[dict]:
    names = list(horses)
    n = len(names)
    win_t = race[0][1]
    pages = []
    for p, chunk in enumerate((race[:9], race[9:]), 1):
        if not chunk:
            continue
        rows = [[{"text": "着順", "x": 70, "color": "0xBDBDBD"}, {"text": "馬番・馬名", "x": 180, "color": "0xBDBDBD"},
                 {"text": "タイム", "x": 680, "color": "0xBDBDBD"},
                 {"text": "着差（前の馬との差）", "x": 860, "color": "0xBDBDBD"}]]
        for k, (m, t) in enumerate(chunk, 1 + 9 * (p - 1)):
            big = 30 if k <= 3 else 26
            rows.append([{"text": f"{k}着", "x": 70, "size": big}, badge_col(horses[m], 180, big),
                         {"text": sd.fmt_time(t), "x": 680, "size": big},
                         {"text": "" if k == 1 else sd.jra_margin(t - race[k - 2][1]), "x": 860, "size": big}])
        pages.append(sr.text_page(f"レース結果 {p}/2", rows, 8.0 if p == 1 else 6.0, size=26, top=104))

    order = sorted(names, key=lambda m: -counts[m][0])
    top_rate = counts[order[0]][0] / N_RUNS
    rows = []
    for m in order[:9]:
        p = counts[m][0] / N_RUNS
        rows.append([badge_col(horses[m], 60, 26), {"text": f"勝率 {p:.1%}", "x": 470, "size": 28, "color": "0xFFF59D"},
                     {"text": "■" * max(1, round(p / max(top_rate, 1e-9) * 22)), "x": 700, "size": 16,
                      "color": sr.COLORS[horses[m]["waku"] - 1], "extra": sr.DOT_BORDER[horses[m]["waku"] - 1]}])
    pages.append(sr.text_page(f"{N_RUNS:,}回走らせた勝率（上位9頭）", rows, 9.0, size=26, top=110))

    rows = [[{"text": "", "x": 60}, {"text": "勝率", "x": 520, "color": "0xBDBDBD"},
             {"text": "2着以内", "x": 690, "color": "0xBDBDBD"}, {"text": "3着以内", "x": 880, "color": "0xBDBDBD"}]]
    for m in sorted(names, key=lambda m: -sum(counts[m][:3]))[:9]:
        rc = counts[m]
        rows.append([badge_col(horses[m], 60, 26), {"text": f"{rc[0] / N_RUNS:.1%}", "x": 520, "size": 26},
                     {"text": f"{sum(rc[:2]) / N_RUNS:.1%}", "x": 690, "size": 26},
                     {"text": f"{sum(rc[:3]) / N_RUNS:.1%}", "x": 880, "size": 26}])
    pages.append(sr.text_page("3着以内に入る確率（上位9頭）", rows, 8.0, size=26, top=110))

    # 1万回の中の大荒れのレース
    if upsets and upsets.get("race"):
        rk = upsets["rank"]
        up = upsets["race"]
        rows = [{"text": f"勝率10位以下の馬が勝った回数：{N_RUNS:,}回中 {upsets['low_winner']:,}回"
                         f"（{upsets['low_winner'] / N_RUNS:.1%}）", "size": 26},
                {"text": f"1〜3着が全部 勝率6位以下だった回数：{upsets['low_top3']:,}回"
                         f"（{upsets['low_top3'] / N_RUNS:.1%}）", "size": 26}, "",
                {"text": f"{N_RUNS:,}回でいちばん荒れたレース", "size": 30, "color": "0xFFF59D"}]
        for k, (m, t) in enumerate(up[:3], 1):
            rows.append([{"text": f"{k}着", "x": 90, "size": 32}, badge_col(horses[m], 200, 32),
                         {"text": f"（勝率{rk[m]}位）", "x": 680, "size": 28, "color": "0xEF9A9A"},
                         {"text": sd.fmt_time(t), "x": 940, "size": 28}])
        fav = min(horses, key=lambda m: rk[m])
        pos = next(i for i, (m, _t) in enumerate(up, 1) if m == fav)
        rows += ["", {"text": f"勝率1位の{fav}は{pos}着", "size": 26},
                 {"text": "データ上は不利でも、何が起きるかわからないのが競馬", "size": 26}]
        pages.append(sr.text_page("1万回の中には大荒れも", rows, 9.0, size=26, top=110))

    # 注目馬（勝率上位5頭）を1頭1画面で。強み・弱みは出走馬の中での順位
    items = {m: sd.stat_items(horses[m]["stats"]) for m in names}
    for rank, m in enumerate(order[:5], 1):
        h = horses[m]
        strong, weak = [], []
        for j, (label, val, text) in enumerate(items[m]):
            better = sum(items[o][j][1] > val + 1e-9 for o in names)
            ties = sum(abs(items[o][j][1] - val) < 1e-9 for o in names)
            if ties > 3 or (j == 0 and val == 0):      # 多くの馬が同じ値（G1未勝利など）の項目は強み・弱みにしない
                continue
            if better <= 1:
                strong.append((label, text, better + 1))
            elif better >= n - 3:
                weak.append((label, text))
        best = max(h["history"], key=lambda x: x[1]) if h["history"] else None
        rows = [[badge_col(h, 60, 40), {"text": f"勝率 {counts[m][0] / N_RUNS:.1%}（{rank}位）", "x": 720, "size": 32,
                                         "color": "0xFFF59D"}],
                {"text": f"JRA芝 {h['stats']['starts']}戦{h['stats']['wins']}勝 ／ 脚質：{sd.style_name(h['style'])}"
                         f" ／ 騎手：{h['jockey']}", "size": 24, "color": "0xE0E0E0"}]
        if best:
            rows.append({"text": f"データ上いちばん強かったレース：{sd.ym(best[0])} {best[2]['race_name']}（{best[3]['pos']}着）",
                         "size": 24, "color": "0xE0E0E0"})
        rows += ["", {"text": f"{n}頭の中で上位", "size": 26, "color": "0x80CBC4"}]
        rows += ([{"text": f"　◎ {lb}：{tx}（{r}位）", "size": 26} for lb, tx, r in strong[:3]]
                 or [{"text": "　とび抜けた項目はなし（バランス型）", "size": 26}])
        rows += ["", {"text": f"{n}頭の中では下位", "size": 26, "color": "0xEF9A9A"}]
        rows += ([{"text": f"　△ {lb}：{tx}", "size": 26} for lb, tx in weak[:2]] or [{"text": "　なし", "size": 26}])
        pages.append(sr.text_page(f"注目馬 {rank}/5", rows, 8.0, top=104))

    first, second = order[0], order[1]
    p1, p2 = counts[first][0] / N_RUNS, counts[second][0] / N_RUNS
    longshot = next((m for m in sorted(names, key=lambda m: -sum(counts[m][:3])) if m not in order[:5]), None)
    rows = [{"text": f"勝率1位は {horses[first]['num']}番 {first}", "size": 40, "color": "0xFFF59D"},
            {"text": f"勝率 {p1:.1%}（2位の{second}は{p2:.1%}）", "size": 32}, ""]
    if longshot:
        rows += [{"text": f"勝率6位以下で3着以内が多いのは {horses[longshot]['num']}番 {longshot}", "size": 28},
                 {"text": f"（3着以内 {sum(counts[longshot][:3]) / N_RUNS:.1%}）", "size": 26}, ""]
    rows += [{"text": "勝率1位でも勝つのは約" + f"{round(1 / max(p1, 1e-9))}回に1回。混戦です", "size": 28}, "",
             {"text": "あなたの予想は？コメントで教えてください", "size": 36, "color": "0xFFEB3B"}]
    if not drawn:
        rows.insert(0, {"text": "※枠順確定前の仮の馬番です", "size": 22, "color": "0xEF9A9A"})
    pages.append(sr.text_page("まとめ", rows, 9.0, top=130))
    return pages


# ---------------------------------------------------------------------------
def main() -> None:
    ent = json.loads((SIM_DIR / "entries.json").read_text(encoding="utf-8"))
    careers = json.loads((SIM_DIR / "horses_cache.json").read_text(encoding="utf-8"))
    # 上がり最速を数えるレース: 出走馬が走ったレースの結果ページ（全クラス。sim_fetch_related.py が取得）
    rc = json.loads((SIM_DIR / "races_cache.json").read_text(encoding="utf-8"))
    graded = [v for v in rc.values() if v.get("runners")]
    races = sm.load_races()
    kit = sl.build(races)
    date, dist, venue = ent["date"], ent["distance"], ent["venue"]
    sr.set_course(venue)
    n = len(ent["horses"])
    drawn = ent["drawn"]

    horses = {}
    for i, h in enumerate(sorted(ent["horses"], key=lambda h: h["umaban"] or 0) if drawn else ent["horses"]):
        hid = h["horse_id"]
        f = kit["feats"].of(hid, date, dist, venue)
        hist = [x for x in kit["rel"].get(hid, []) if x[0] < date]
        styles = [x[3]["style"] for x in hist if x[3].get("style") is not None]
        num = h["umaban"] if drawn else i + 1
        horses[h["horse"]] = {"id": hid, "name": h["horse"], "num": num, "waku": h["waku"] if drawn else sr.waku_of(num, n),
                              "feats": f, "score": kit["pm"].score(f) if f else None, "history": hist,
                              "style": statistics.mean(styles) if styles else 0.5, "jockey": h["jockey"],
                              "weight": h["weight"], "stats": sd.career_stats(hid, careers, graded, venue, dist, date, "出走したレース")}
    scored = sorted(h["score"] for h in horses.values() if h["score"] is not None)
    fill = scored[max(0, len(scored) // 10 - 1)]
    for h in horses.values():
        if h["score"] is None:
            print(f"[注意] {h['name']}: 2年以内のJRA芝の成績が無いので下位1割のスコアで計算")
            h["score"] = fill
    # 枠順の有利不利（sim_gate_effect.py が同じコースの過去レースから測ったもの）。枠順確定後だけ使う
    gate = None
    if drawn and (SIM_DIR / "gate_effect.json").exists():
        gate = json.loads((SIM_DIR / "gate_effect.json").read_text(encoding="utf-8"))
        if gate.get("venue") != venue or gate.get("distance") != dist or "gate_x_style" not in gate:
            gate = None
    counts_plain = monte_carlo(horses)
    # 枠と脚質の組み合わせ: ゲート×g + 脚質×s + ゲート×脚質×g×s（g: 0最内〜1大外、s: 0前〜1後方）
    for h in horses.values():
        g = (h["num"] - 1) / max(n - 1, 1)
        st = h["style"]
        h["gate_adj"] = (gate["gate"] * g + gate["style"] * st + gate["gate_x_style"] * g * st) if gate else 0.0
    mean_adj = statistics.mean(h["gate_adj"] for h in horses.values())
    for h in horses.values():
        h["gate_adj"] -= mean_adj                               # 出走馬の平均との差だけを足す
        h["score"] += h["gate_adj"]
    upsets = {}
    counts = monte_carlo(horses, upsets=upsets)

    key = f"c:{venue}{dist}"
    base = kit["model"].coef.get(key)
    if base is None:
        sys.exit(f"基準タイムのモデルに {key} がありません")
    k = kit["train"]["sec_per_score"]
    shown = pick_display_race(horses, counts, base, k, kit["g1_winner_score"])
    # 1万回のうち最も荒れた回（評価の低い馬ほど上位に来た回）のタイム
    upsets["race"] = sorted(((m, base - k * (upsets["perf"][m] - kit["g1_winner_score"])) for m in horses),
                            key=lambda t: t[1])
    names = list(horses)
    order = sorted(names, key=lambda m: -counts[m][0])
    md = [f"# {ent['race_name']} シミュレーション（{date} {venue} 芝{dist}m・{'枠順確定' if drawn else '馬番は仮'}）", "",
          "| 馬番 | 馬 | 勝率 | 2着内 | 3着内 | スコア | うち枠・脚質の補正 | 補正なしの勝率 | 脚質 |",
          "|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for m in order:
        rc, h = counts[m], horses[m]
        md.append(f"| {h['num']} | {m} | {rc[0] / N_RUNS:.1%} | {sum(rc[:2]) / N_RUNS:.1%} | {sum(rc[:3]) / N_RUNS:.1%} | "
                  f"{h['score']:+.2f} | {h['gate_adj']:+.2f} | {counts_plain[m][0] / N_RUNS:.1%} | {h['style']:.2f} |")
    if gate:
        md += ["", f"枠・脚質の効果（{gate['n_races']}レース・{gate['from_year']}年〜、人気をそろえた2頭比較）:"]
        for mode, mm in gate["models"].items():
            md.append(f"- {'普段の脚質' if mode == 'typical' else 'そのレースの位置取り（参考）'}"
                      f"（{mm['n_pairs']:,}組）: " + " / ".join(f"{k2} {v:+.3f}（±{mm['se'][k2]:.3f}）"
                                                         for k2, v in mm["coef"].items()))
        md += ["", "| 枠 | 脚質 | 走数 | 勝率 | 3着内率 |", "|---|---|---:|---:|---:|"]
        md += [f"| {c['gate']} | {c['style']} | {c['runs']} | {c['win']:.1%} | {c['top3']:.1%} |" for c in gate["cross"]]
    md += ["", "映像のレース: " + " / ".join(f"{horses[m]['num']} {m} {sd.fmt_time(t)}" for m, t in shown)]
    (SIM_DIR / "real_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (SIM_DIR / "real_result.json").write_text(json.dumps({
        "race": {k2: v for k2, v in ent.items() if k2 != "horses"},
        "win_rate": {m: counts[m][0] / N_RUNS for m in order},
        "top3": {m: sum(counts[m][:3]) / N_RUNS for m in order},
        "win_rate_without_gate": {m: counts_plain[m][0] / N_RUNS for m in order},
        "gate": gate,
        "shown": shown}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(md))

    finish = dict(shown)
    short = [short_name(m) if m in order[:6] else "" for m in names]   # 勝率上位6頭だけ馬名を出す
    video = SIM_DIR / "real_race_video.mp4"
    marks = sr.render(video, SIM_DIR / "work_real", sd.find_font(), names, [horses[m]["style"] for m in names],
                      [finish[m] for m in names], before_pages(ent, horses, kit, gate),
                      after_pages(horses, counts, shown, drawn, upsets), dist=dist, venue=venue,
                      nums=[horses[m]["num"] for m in names], short=short,
                      tts=sd.make_tts(SIM_DIR / "work_real" / "voice"),
                      frames={"title": 1.0, "entries": 16.0, "gate": ("gate", 1.0), "start": ("race_start", 1.0),
                              "mid": ("race_start", 15.0), "stretch": ("straight", 1.5), "slow": ("slow", 3.0),
                              "goal": ("goal", 0.3), "result": ("race_end", 3.0), "rates": ("race_end", 16.0),
                              "analysis": ("race_end", 34.0), "summary": ("total", -4.0)})
    for label in ("title", "entries", "gate", "start", "mid", "stretch", "slow", "goal", "result", "rates",
                  "analysis", "summary"):
        f = SIM_DIR / f"frame_{label}.jpg"
        if f.exists():
            f.replace(SIM_DIR / f"real_frame_{label}.jpg")
    print("実況の読み上げ:", len(marks["voice"]), "本")
    fan = 3.5
    sections = [("calm", marks["gate"]), ("race", marks["goal"] - marks["gate"]), ("fanfare", fan),
                ("result", marks["total"] - marks["goal"] - fan)]
    bgm = sb.make_bgm(sections, SIM_DIR / "work_real" / "bgm.m4a")
    (SIM_DIR / "work").mkdir(exist_ok=True)
    sd.mix_audio(video, bgm, marks, SIM_DIR / "real_race.mp4")
    print(f"保存: {SIM_DIR / 'real_race.mp4'}")


if __name__ == "__main__":
    main()
