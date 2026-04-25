#!/usr/bin/env python3
"""Step 4: ranking.csv をもとにグラフを生成して graphs/ フォルダに保存 (PIL禁止・matplotlib使用)"""

import csv
import sys
import os
import math
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches


OUTPUT_DIR = Path("graphs")
FIGSIZE = (19.2, 10.8)  # 1920×1080px at 100dpi
DPI = 100


def setup_japanese_font():
    """日本語フォントを設定"""
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
        "/usr/local/share/fonts/NotoSansCJKjp-Regular.otf",
    ]
    for path in candidates:
        if os.path.exists(path):
            fm.fontManager.addfont(path)
            prop = fm.FontProperties(fname=path)
            plt.rcParams["font.family"] = prop.get_name()
            print(f"日本語フォント設定: {path}")
            return True

    # システム内を検索
    for f in fm.findSystemFonts(fontpaths=None, fontext="ttf"):
        if "noto" in f.lower() and ("cjk" in f.lower() or "jp" in f.lower()):
            fm.fontManager.addfont(f)
            prop = fm.FontProperties(fname=f)
            plt.rcParams["font.family"] = prop.get_name()
            print(f"日本語フォント設定(検索): {f}")
            return True

    print("WARNING: 日本語フォントが見つかりません。文字化けする可能性があります")
    return False


def load_ranking(path="ranking.csv", top_n=None):
    if not Path(path).exists():
        print(f"ERROR: {path} が見つかりません")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if top_n:
        rows = rows[:top_n]
    return rows


def graph_01_bar(rows):
    """① 総合スコア横棒グラフ（上位20頭）"""
    top20 = rows[:20]
    names = [r["name"] for r in reversed(top20)]
    scores = [float(r["total_score"]) for r in reversed(top20)]

    cmap = plt.cm.get_cmap("tab20", len(names))
    colors = [cmap(i) for i in range(len(names))]

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    bars = ax.barh(names, scores, color=colors, height=0.7, edgecolor="#0f3460", linewidth=0.5)

    # スコア値をバーの右端に表示
    for bar, score in zip(bars, scores):
        ax.text(
            bar.get_width() + max(scores) * 0.005,
            bar.get_y() + bar.get_height() / 2,
            f"{score:.1f}",
            va="center",
            ha="left",
            color="white",
            fontsize=11,
            fontweight="bold",
        )

    ax.set_xlabel("総合スコア", color="white", fontsize=14)
    ax.set_title(
        "歴代最強馬ランキング TOP20 ─ 総合スコア",
        color="white",
        fontsize=18,
        fontweight="bold",
        pad=20,
    )
    ax.tick_params(colors="white", labelsize=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for spine in ["bottom", "left"]:
        ax.spines[spine].set_color("#404060")

    ax.set_xlim(0, max(scores) * 1.12)
    ax.xaxis.label.set_color("white")
    ax.yaxis.label.set_color("white")

    # グリッド
    ax.xaxis.grid(True, color="#304060", linewidth=0.5, alpha=0.7)
    ax.set_axisbelow(True)

    # 1位を強調
    bars[-1].set_edgecolor("gold")
    bars[-1].set_linewidth(2)

    plt.tight_layout(pad=1.5)
    out_path = OUTPUT_DIR / "01_bar.png"
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  保存: {out_path}")


def graph_02_radar(rows):
    """② 指標別レーダーチャート（上位5頭）"""
    top5 = rows[:5]
    metrics = ["G1勝利", "安定性", "強敵撃破", "着差", "時代補正"]
    metric_keys = ["score_g1", "score_stability", "score_upset", "score_margin", "score_era"]
    n = len(metrics)

    # 各指標の最大値（正規化用）
    max_vals = []
    for key in metric_keys:
        vals = [float(r[key]) for r in rows if r[key]]
        max_vals.append(max(vals) if vals else 1.0)

    angles = [2 * math.pi * i / n for i in range(n)]
    angles += angles[:1]  # 閉じる

    colors = ["#e94560", "#0f3460", "#533483", "#f5a623", "#2ecc71"]

    fig, ax = plt.subplots(figsize=(10.8, 10.8), dpi=DPI, subplot_kw=dict(polar=True))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    for i, horse in enumerate(top5):
        values = []
        for key, maxv in zip(metric_keys, max_vals):
            v = float(horse[key]) if horse[key] else 0.0
            values.append(v / maxv if maxv > 0 else 0.0)
        values += values[:1]

        ax.plot(angles, values, color=colors[i], linewidth=2, label=horse["name"])
        ax.fill(angles, values, color=colors[i], alpha=0.15)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(metrics, color="white", fontsize=13)
    ax.set_yticklabels([])
    ax.grid(color="#304060", linewidth=0.8, alpha=0.7)
    ax.spines["polar"].set_color("#404060")

    ax.set_title(
        "歴代最強馬ランキング TOP5 ─ 指標別レーダーチャート",
        color="white",
        fontsize=16,
        fontweight="bold",
        pad=30,
        y=1.08,
    )

    legend = ax.legend(
        loc="upper right",
        bbox_to_anchor=(1.35, 1.15),
        framealpha=0.3,
        facecolor="#16213e",
        edgecolor="#404060",
        labelcolor="white",
        fontsize=12,
    )

    plt.tight_layout(pad=1.5)
    out_path = OUTPUT_DIR / "02_radar.png"
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  保存: {out_path}")


def graph_03_compare(rows):
    """③ 獲得賞金ランキングとの順位比較表（上位20頭）"""
    top20 = rows[:20]

    # 順位データを整理
    names = [r["name"] for r in top20]
    score_ranks = [int(r["score_rank"]) for r in top20]
    prize_ranks = [int(r["prize_rank"]) if r["prize_rank"] and r["prize_rank"] != "0" else i + 21
                   for i, r in enumerate(top20)]

    rank_diffs = [prize_ranks[i] - score_ranks[i] for i in range(len(names))]

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")
    ax.set_xlim(-0.5, 3.5)
    ax.set_ylim(-0.5, len(names) - 0.5)
    ax.axis("off")

    # ヘッダー
    col_headers = ["馬名", "スコア順位", "賞金順位", "順位差"]
    col_x = [0.3, 1.2, 2.1, 3.0]
    for cx, ch in zip(col_x, col_headers):
        ax.text(
            cx, len(names) + 0.1, ch,
            ha="center", va="bottom",
            color="gold", fontsize=14, fontweight="bold"
        )

    # 行
    row_height = 0.9
    for i, (name, sr, pr, diff) in enumerate(
        zip(names, score_ranks, prize_ranks, rank_diffs)
    ):
        y = len(names) - 1 - i
        bg_color = "#1e2a4a" if i % 2 == 0 else "#1a2040"
        rect = mpatches.FancyBboxPatch(
            (-0.45, y - 0.4), 3.9, row_height * 0.85,
            boxstyle="round,pad=0.02",
            facecolor=bg_color, edgecolor="#304060", linewidth=0.5
        )
        ax.add_patch(rect)

        # 馬名
        ax.text(col_x[0], y, name, ha="center", va="center",
                color="white", fontsize=12, fontweight="bold")

        # スコア順位 (金色でハイライト)
        rank_color = "gold" if sr <= 3 else "white"
        ax.text(col_x[1], y, f"{sr}位", ha="center", va="center",
                color=rank_color, fontsize=12)

        # 賞金順位
        ax.text(col_x[2], y, f"{pr}位", ha="center", va="center",
                color="white", fontsize=12)

        # 順位差（緑=上昇、赤=下降）
        if diff > 0:
            diff_color = "#2ecc71"
            diff_str = f"▲{diff}"
        elif diff < 0:
            diff_color = "#e74c3c"
            diff_str = f"▼{abs(diff)}"
        else:
            diff_color = "#95a5a6"
            diff_str = "─"
        ax.text(col_x[3], y, diff_str, ha="center", va="center",
                color=diff_color, fontsize=13, fontweight="bold")

    ax.set_title(
        "スコアランキング vs 獲得賞金ランキング 順位比較",
        color="white", fontsize=18, fontweight="bold", pad=20
    )

    # 凡例
    green_patch = mpatches.Patch(color="#2ecc71", label="スコア > 賞金（評価UP）")
    red_patch = mpatches.Patch(color="#e74c3c", label="スコア < 賞金（評価DOWN）")
    ax.legend(
        handles=[green_patch, red_patch],
        loc="lower right",
        framealpha=0.3,
        facecolor="#16213e",
        edgecolor="#404060",
        labelcolor="white",
        fontsize=11,
    )

    plt.tight_layout(pad=1.5)
    out_path = OUTPUT_DIR / "03_compare.png"
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  保存: {out_path}")


def main():
    print("=== Step 4: グラフ生成 ===")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    setup_japanese_font()
    plt.style.use("dark_background")

    rows = load_ranking(top_n=20)
    if len(rows) < 5:
        print(f"ERROR: ranking.csv のデータが不足しています ({len(rows)}頭)")
        sys.exit(1)

    print("① 総合スコア横棒グラフ生成中...")
    all_rows = load_ranking()
    graph_01_bar(all_rows[:20])

    print("② レーダーチャート生成中...")
    graph_02_radar(all_rows)

    print("③ 順位比較表生成中...")
    graph_03_compare(all_rows[:20])

    print("\n完了: graphs/ フォルダを確認してください")


if __name__ == "__main__":
    main()
