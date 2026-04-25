#!/usr/bin/env python3
"""Step 3: results.csv をもとに全馬をスコアリングして ranking.csv に保存"""

import csv
import sys
from collections import defaultdict
from pathlib import Path


def load_results(path="results.csv"):
    if not Path(path).exists():
        print(f"ERROR: {path} が見つかりません")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_horses(path="horses.csv"):
    if not Path(path).exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {row["name"]: row for row in csv.DictReader(f)}


def compute_deviation(value, mean, std):
    """偏差値 (mean=50, std=10) を計算"""
    if std == 0:
        return 50.0
    return 10.0 * (value - mean) / std + 50.0


def score_horses(records, horse_meta):
    # 馬ごとにレコードをグループ化
    by_horse = defaultdict(list)
    for r in records:
        by_horse[r["horse_name"]].append(r)

    horse_scores = []

    for name, races in by_horse.items():
        # -------- 基本集計 --------
        g1_races = [r for r in races if r["grade"] == "G1"]
        g1_wins = [r for r in g1_races if _pos(r) == 1]
        g1_top2 = [r for r in g1_races if _pos(r) in (1, 2)]

        # ① G1勝利スコア
        score_g1 = len(g1_wins) * 3.0

        # ② 安定スコア (G1 top2率 × 10)
        if g1_races:
            score_stability = (len(g1_top2) / len(g1_races)) * 10.0
        else:
            score_stability = 0.0

        # ③ 強敵撃破スコア (1番人気以外で勝った回数 × 1.5)
        upsets = [r for r in races if _pos(r) == 1 and _pop(r) not in (0, 1)]
        score_upset = len(upsets) * 1.5

        # ④ 着差スコア (G1勝利時の平均着差)
        win_margins = [_margin(r) for r in g1_wins if _margin(r) is not None]
        if win_margins:
            avg_margin = sum(win_margins) / len(win_margins)
            score_margin = min(avg_margin * 5.0, 10.0)  # 最大10点
        else:
            score_margin = 0.0

        # 現役年を推定 (race yearの範囲)
        years = [int(r["year"]) for r in races if r["year"].isdigit()]
        active_start = min(years) if years else 2000
        active_end = max(years) if years else 2000
        active_era = (active_start + active_end) // 2  # 中間年をエラ代表値に

        horse_scores.append({
            "name": name,
            "url": horse_meta.get(name, {}).get("url", ""),
            "g1_wins": len(g1_wins),
            "g1_races": len(g1_races),
            "total_races": len(races),
            "active_era": active_era,
            "score_g1": round(score_g1, 2),
            "score_stability": round(score_stability, 2),
            "score_upset": round(score_upset, 2),
            "score_margin": round(score_margin, 2),
            "score_era": 0.0,  # 後で計算
            "total_score": 0.0,
            "prize_rank": 0,  # 後で計算
        })

    # ⑤ 時代補正: エラ（decade）ごとにG1勝利数の偏差値
    era_groups = defaultdict(list)
    for h in horse_scores:
        decade = (h["active_era"] // 10) * 10
        era_groups[decade].append(h["g1_wins"])

    era_stats = {}
    for decade, wins_list in era_groups.items():
        n = len(wins_list)
        mean = sum(wins_list) / n
        var = sum((x - mean) ** 2 for x in wins_list) / n if n > 1 else 0
        std = var ** 0.5
        era_stats[decade] = (mean, std)

    for h in horse_scores:
        decade = (h["active_era"] // 10) * 10
        mean, std = era_stats.get(decade, (0, 1))
        dev = compute_deviation(h["g1_wins"], mean, std)
        # 偏差値をスコアに変換 (50基準、最大15点)
        h["score_era"] = round(max(0.0, (dev - 50.0) * 0.3), 2)

    # 総合スコア計算
    for h in horse_scores:
        h["total_score"] = round(
            h["score_g1"]
            + h["score_stability"]
            + h["score_upset"]
            + h["score_margin"]
            + h["score_era"],
            2,
        )

    # 賞金ランキング計算
    prize_by_horse = defaultdict(float)
    for r in records:
        try:
            prize_by_horse[r["horse_name"]] += float(r["prize_man"] or 0)
        except ValueError:
            pass

    prize_sorted = sorted(prize_by_horse.items(), key=lambda x: -x[1])
    prize_rank_map = {name: i + 1 for i, (name, _) in enumerate(prize_sorted)}

    for h in horse_scores:
        h["prize_rank"] = prize_rank_map.get(h["name"], 0)
        h["prize_man"] = round(prize_by_horse.get(h["name"], 0) / 10000, 0)  # 万円

    # 総合スコア順にソート
    horse_scores.sort(key=lambda x: -x["total_score"])
    for i, h in enumerate(horse_scores):
        h["score_rank"] = i + 1

    return horse_scores


def _pos(r):
    try:
        return int(r["position"])
    except (ValueError, TypeError):
        return 0


def _pop(r):
    try:
        return int(r["popularity"])
    except (ValueError, TypeError):
        return 0


def _margin(r):
    try:
        v = float(r["margin"])
        return v if v >= 0 else None
    except (ValueError, TypeError):
        return None


RANKING_FIELDNAMES = [
    "score_rank",
    "name",
    "url",
    "g1_wins",
    "g1_races",
    "total_races",
    "active_era",
    "score_g1",
    "score_stability",
    "score_upset",
    "score_margin",
    "score_era",
    "total_score",
    "prize_rank",
    "prize_man",
]


def main():
    print("=== Step 3: スコアリング ===")

    records = load_results()
    horse_meta = load_horses()

    print(f"{len(records)}件のレースデータを処理中...")

    scores = score_horses(records, horse_meta)

    with open("ranking.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RANKING_FIELDNAMES)
        writer.writeheader()
        writer.writerows(scores)

    print(f"\n=== 上位10頭 ===")
    for h in scores[:10]:
        print(
            f"  {h['score_rank']:2d}位 {h['name']:<12s} "
            f"総合{h['total_score']:6.2f}点 "
            f"(G1勝利数:{h['g1_wins']} G1スコア:{h['score_g1']} "
            f"安定:{h['score_stability']} 着差:{h['score_margin']} 時代:{h['score_era']})"
        )

    print(f"\nranking.csv に {len(scores)}頭分を保存しました")
    print("完了")


if __name__ == "__main__":
    main()
