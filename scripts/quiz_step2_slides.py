#!/usr/bin/env python3
"""Step 2: quiz.json をもとに問題・回答スライドPNGを slides/ フォルダに保存 (matplotlib使用)"""

import json
import os
import sys
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches

OUTPUT_DIR = Path("slides")
# 横型動画: 1920×1080
FIGSIZE = (19.2, 10.8)
DPI = 100

BG_COLOR = "#0d1b2a"
ACCENT_COLOR = "#e8c84a"
TEXT_COLOR = "#ffffff"
PANEL_COLOR = "#1a2f4a"
ANSWER_BG = "#0a3d1f"
ANSWER_ACCENT = "#2ecc71"


def setup_japanese_font():
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
            return True

    for f in fm.findSystemFonts(fontpaths=None, fontext="ttf"):
        if "noto" in f.lower() and ("cjk" in f.lower() or "jp" in f.lower()):
            fm.fontManager.addfont(f)
            prop = fm.FontProperties(fname=f)
            plt.rcParams["font.family"] = prop.get_name()
            return True

    print("WARNING: 日本語フォントが見つかりません")
    return False


def wrap_text(text, width=20):
    return "\n".join(textwrap.fill(line, width) for line in text.splitlines())


def make_question_slide(q: dict, out_path: Path):
    """問題スライド（横型 1920×1080）"""
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # 上部バナー
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.0, 0.88), 1.0, 0.12,
        boxstyle="square,pad=0",
        facecolor=ACCENT_COLOR, edgecolor="none",
    ))
    ax.text(0.5, 0.94, "競馬知識クイズ", ha="center", va="center",
            color="#0d1b2a", fontsize=34, fontweight="bold")

    # 問題番号バッジ（左側）
    ax.add_patch(plt.Circle((0.08, 0.62), 0.075, color=ACCENT_COLOR, zorder=3))
    ax.text(0.08, 0.62, f"Q{q['number']}", ha="center", va="center",
            color="#0d1b2a", fontsize=32, fontweight="bold", zorder=4)

    # 問題パネル（中央）
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.04, 0.34), 0.92, 0.40,
        boxstyle="round,pad=0.015",
        facecolor=PANEL_COLOR, edgecolor=ACCENT_COLOR, linewidth=2,
    ))
    question_text = wrap_text(q["display_question"], width=22)
    ax.text(0.5, 0.545, question_text, ha="center", va="center",
            color=TEXT_COLOR, fontsize=40, fontweight="bold",
            multialignment="center", linespacing=1.35)

    # 回答促進テキスト
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.25, 0.16), 0.50, 0.13,
        boxstyle="round,pad=0.01",
        facecolor="#1a1a2e", edgecolor="#404060", linewidth=1,
    ))
    ax.text(0.5, 0.225, "答えは何でしょう？", ha="center", va="center",
            color="#9090b0", fontsize=26)

    # プログレス（右下）
    ax.text(0.95, 0.05, f"{q['number']} / 5問", ha="right", va="center",
            color="#606080", fontsize=22)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_answer_slide(q: dict, out_path: Path):
    """回答スライド（横型 1920×1080）"""
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(ANSWER_BG)
    ax.set_facecolor(ANSWER_BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # 上部バナー
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.0, 0.88), 1.0, 0.12,
        boxstyle="square,pad=0",
        facecolor=ANSWER_ACCENT, edgecolor="none",
    ))
    ax.text(0.5, 0.94, f"第{q['number']}問 答え合わせ！", ha="center", va="center",
            color="#0a1a10", fontsize=34, fontweight="bold")

    # 左半分: 問題の再掲
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.02, 0.44), 0.44, 0.38,
        boxstyle="round,pad=0.015",
        facecolor="#0d2a1a", edgecolor="#204030", linewidth=1,
    ))
    ax.text(0.24, 0.83, "問題", ha="center", va="center",
            color="#80c090", fontsize=20, fontweight="bold")
    q_text = wrap_text(q["display_question"], width=16)
    ax.text(0.24, 0.63, q_text, ha="center", va="center",
            color="#c0d8c8", fontsize=24, multialignment="center", linespacing=1.3)

    # 右半分: 答え（大きく）
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.52, 0.44), 0.46, 0.38,
        boxstyle="round,pad=0.015",
        facecolor="#0a3d1f", edgecolor=ANSWER_ACCENT, linewidth=3,
    ))
    ax.text(0.75, 0.83, "正解", ha="center", va="center",
            color=ANSWER_ACCENT, fontsize=20, fontweight="bold")
    ax.text(0.75, 0.63, q["display_answer"], ha="center", va="center",
            color=ANSWER_ACCENT, fontsize=48, fontweight="bold")

    # 下部: 解説パネル
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.02, 0.10), 0.96, 0.28,
        boxstyle="round,pad=0.015",
        facecolor="#112a1c", edgecolor="#204030", linewidth=1,
    ))
    ax.text(0.08, 0.34, "📖 解説", ha="left", va="center",
            color="#80c090", fontsize=18)
    explanation_text = wrap_text(q["display_explanation"], width=40)
    ax.text(0.5, 0.22, explanation_text, ha="center", va="center",
            color=TEXT_COLOR, fontsize=26, multialignment="center", linespacing=1.35)

    # プログレス（右下）
    ax.text(0.95, 0.04, f"{q['number']} / 5問", ha="right", va="center",
            color="#406050", fontsize=22)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=ANSWER_BG)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_title_slide(title: str, out_path: Path):
    """タイトルスライド（横型 1920×1080）"""
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # 上部バナー
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.0, 0.88), 1.0, 0.12,
        boxstyle="square,pad=0",
        facecolor=ACCENT_COLOR, edgecolor="none",
    ))
    ax.text(0.5, 0.94, "競馬チャンネル", ha="center", va="center",
            color="#0d1b2a", fontsize=28, fontweight="bold")

    # 馬アイコン（左側）
    ax.text(0.15, 0.52, "🏇", ha="center", va="center", fontsize=100)

    # タイトルパネル（右側）
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.28, 0.34), 0.70, 0.42,
        boxstyle="round,pad=0.02",
        facecolor=PANEL_COLOR, edgecolor=ACCENT_COLOR, linewidth=3,
    ))
    title_wrapped = wrap_text(title, width=16)
    ax.text(0.63, 0.60, title_wrapped, ha="center", va="center",
            color=ACCENT_COLOR, fontsize=42, fontweight="bold",
            multialignment="center", linespacing=1.3)

    # サブテキスト
    ax.text(0.5, 0.22, "全5問  ─  何問正解できる？", ha="center", va="center",
            color=TEXT_COLOR, fontsize=28)

    ax.text(0.5, 0.10, "チャンネル登録・高評価よろしくお願いします！", ha="center", va="center",
            color="#8090a0", fontsize=20)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_result_slide(out_path: Path):
    """結果スライド（横型 1920×1080）"""
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # 上部バナー
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.0, 0.88), 1.0, 0.12,
        boxstyle="square,pad=0",
        facecolor=ACCENT_COLOR, edgecolor="none",
    ))
    ax.text(0.5, 0.94, "競馬知識クイズ", ha="center", va="center",
            color="#0d1b2a", fontsize=34, fontweight="bold")

    # トロフィー（左）
    ax.text(0.18, 0.52, "🏆", ha="center", va="center", fontsize=120)

    # メッセージ（右）
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.32, 0.34), 0.66, 0.42,
        boxstyle="round,pad=0.02",
        facecolor=PANEL_COLOR, edgecolor=ACCENT_COLOR, linewidth=2,
    ))
    ax.text(0.65, 0.65, "全問終了！", ha="center", va="center",
            color=ACCENT_COLOR, fontsize=46, fontweight="bold")
    ax.text(0.65, 0.52, "いくつ正解できましたか？", ha="center", va="center",
            color=TEXT_COLOR, fontsize=28)

    ax.text(0.5, 0.20, "👍 高評価 & チャンネル登録お願いします！　次回もお楽しみに🏇",
            ha="center", va="center", color="#9090b0", fontsize=22)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print(f"  保存: {out_path}")


def main():
    print("=== Step 2: スライド生成 ===")

    if not Path("quiz.json").exists():
        print("ERROR: quiz.json が見つかりません。Step 1 を先に実行してください。")
        sys.exit(1)

    with open("quiz.json", encoding="utf-8") as f:
        quiz = json.load(f)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    setup_japanese_font()
    plt.style.use("dark_background")

    questions = quiz.get("questions", [])
    title = quiz.get("title", "競馬知識クイズ")

    print("タイトルスライド生成中...")
    make_title_slide(title, OUTPUT_DIR / "00_title.png")

    for q in questions:
        print(f"Q{q['number']} スライド生成中...")
        make_question_slide(q, OUTPUT_DIR / f"{q['number']:02d}q_question.png")
        make_answer_slide(q, OUTPUT_DIR / f"{q['number']:02d}a_answer.png")

    print("結果スライド生成中...")
    make_result_slide(OUTPUT_DIR / "99_result.png")

    total = 1 + len(questions) * 2 + 1
    print(f"\nslides/ フォルダに {total} 枚のスライドを保存しました")
    print("完了")


if __name__ == "__main__":
    main()
