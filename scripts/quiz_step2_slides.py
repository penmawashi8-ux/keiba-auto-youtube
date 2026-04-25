#!/usr/bin/env python3
"""Step 2: quiz.json をもとに問題・回答スライドPNGを slides/ フォルダに保存 (matplotlib使用)"""

import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches

OUTPUT_DIR = Path("slides")
FIGSIZE = (19.2, 10.8)  # 1920×1080
DPI = 100

BG = "#0d1b2a"
ACCENT = "#e8c84a"
WHITE = "#ffffff"
PANEL = "#1a2f4a"
CHOICE_BG = "#162238"
CHOICE_BORDER = "#304870"
CORRECT_BG = "#0a3d1f"
CORRECT_BORDER = "#2ecc71"
WRONG_BG = "#3d0a0a"
WRONG_BORDER = "#e74c3c"
CLUE_BG = "#0f2235"

CHOICE_LABELS = ["A", "B", "C", "D"]


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


def draw_banner(ax, text, bg=ACCENT, fg="#0d1b2a", fontsize=30):
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.0, 0.895), 1.0, 0.105,
        boxstyle="square,pad=0",
        facecolor=bg, edgecolor="none", transform=ax.transAxes, clip_on=False,
    ))
    ax.text(0.5, 0.948, text, ha="center", va="center",
            color=fg, fontsize=fontsize, fontweight="bold", transform=ax.transAxes)


def draw_choice_box(ax, x, y, w, h, label, text, bg, border, fontsize=22):
    ax.add_patch(mpatches.FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.01",
        facecolor=bg, edgecolor=border, linewidth=2,
    ))
    # ラベル円
    ax.add_patch(plt.Circle((x + 0.035, y + h / 2), 0.028, color=border, zorder=3))
    ax.text(x + 0.035, y + h / 2, label, ha="center", va="center",
            color=WHITE, fontsize=fontsize - 2, fontweight="bold", zorder=4)
    ax.text(x + 0.085, y + h / 2, text, ha="left", va="center",
            color=WHITE, fontsize=fontsize)


def make_question_slide(q: dict, out_path: Path):
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    draw_banner(ax, f"名馬当てクイズ  ─  第{q['number']}問")

    # 「この馬は誰？」
    ax.text(0.5, 0.82, "この馬は誰？", ha="center", va="center",
            color=ACCENT, fontsize=36, fontweight="bold")

    # G1勝利歴（全件・2列レイアウト）
    clues = q["clues"]
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.03, 0.38), 0.94, 0.42,
        boxstyle="round,pad=0.015",
        facecolor=CLUE_BG, edgecolor=ACCENT, linewidth=1.5,
    ))
    ax.text(0.5, 0.77, "G1 勝利歴", ha="center", va="center",
            color=ACCENT, fontsize=20, fontweight="bold")

    # 2列に分割
    half = (len(clues) + 1) // 2
    col_left = clues[:half]
    col_right = clues[half:]
    row_h = min(0.30 / max(half, 1), 0.07)
    base_y = 0.71 - row_h

    for i, clue in enumerate(col_left):
        y = base_y - i * row_h
        ax.text(0.06, y, f"◆ {clue}", ha="left", va="center",
                color=WHITE, fontsize=19, fontweight="bold")
    for i, clue in enumerate(col_right):
        y = base_y - i * row_h
        ax.text(0.54, y, f"◆ {clue}", ha="left", va="center",
                color=WHITE, fontsize=19, fontweight="bold")

    # 4択（2×2グリッド）
    choices = q["choices"]
    box_w, box_h = 0.44, 0.10
    positions = [
        (0.03, 0.38), (0.53, 0.38),
        (0.03, 0.26), (0.53, 0.26),
    ]
    for i, (bx, by) in enumerate(positions):
        if i < len(choices):
            draw_choice_box(ax, bx, by, box_w, box_h,
                            CHOICE_LABELS[i], choices[i],
                            CHOICE_BG, CHOICE_BORDER, fontsize=24)

    # プログレス
    ax.text(0.97, 0.03, f"{q['number']} / 5問", ha="right", va="center",
            color="#606080", fontsize=20)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_answer_slide(q: dict, out_path: Path):
    correct_idx = q["correct_index"]
    choices = q["choices"]
    correct_label = CHOICE_LABELS[correct_idx]
    correct_name = choices[correct_idx]

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    draw_banner(ax, f"第{q['number']}問  ─  答え合わせ！", bg="#1a5c32", fg=WHITE)

    # 正解ラベル
    ax.text(0.5, 0.82, f"正解は  {correct_label}. {correct_name}！",
            ha="center", va="center",
            color="#2ecc71", fontsize=38, fontweight="bold")

    # 4択（正解=緑、不正解=暗赤色）
    box_w, box_h = 0.44, 0.10
    positions = [
        (0.03, 0.56), (0.53, 0.56),
        (0.03, 0.44), (0.53, 0.44),
    ]
    for i, (bx, by) in enumerate(positions):
        if i < len(choices):
            if i == correct_idx:
                bg, border = CORRECT_BG, CORRECT_BORDER
            else:
                bg, border = WRONG_BG, WRONG_BORDER
            draw_choice_box(ax, bx, by, box_w, box_h,
                            CHOICE_LABELS[i], choices[i],
                            bg, border, fontsize=24)

    # 解説パネル
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.03, 0.10), 0.94, 0.26,
        boxstyle="round,pad=0.015",
        facecolor=PANEL, edgecolor="#304060", linewidth=1,
    ))
    ax.text(0.06, 0.32, "◆ 解説", ha="left", va="center",
            color=ACCENT, fontsize=20, fontweight="bold")
    ax.text(0.5, 0.20, q["display_explanation"], ha="center", va="center",
            color=WHITE, fontsize=26, multialignment="center")

    ax.text(0.97, 0.03, f"{q['number']} / 5問", ha="right", va="center",
            color="#606080", fontsize=20)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_title_slide(title: str, out_path: Path):
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    draw_banner(ax, "競馬チャンネル")

    ax.add_patch(mpatches.FancyBboxPatch(
        (0.05, 0.34), 0.90, 0.44,
        boxstyle="round,pad=0.02",
        facecolor=PANEL, edgecolor=ACCENT, linewidth=3,
    ))
    ax.text(0.50, 0.65, title, ha="center", va="center",
            color=ACCENT, fontsize=38, fontweight="bold", multialignment="center")
    ax.text(0.50, 0.48, "全5問  ─  あなたは何問わかる？", ha="center", va="center",
            color=WHITE, fontsize=26)

    ax.text(0.5, 0.16, "チャンネル登録・高評価よろしくお願いします！",
            ha="center", va="center", color="#8090a0", fontsize=22)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_result_slide(out_path: Path):
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    draw_banner(ax, "名馬当てクイズ  ─  全問終了！")

    ax.add_patch(mpatches.FancyBboxPatch(
        (0.05, 0.34), 0.90, 0.44,
        boxstyle="round,pad=0.02",
        facecolor=PANEL, edgecolor=ACCENT, linewidth=2,
    ))
    ax.text(0.50, 0.65, "全問終了！", ha="center", va="center",
            color=ACCENT, fontsize=44, fontweight="bold")
    ax.text(0.50, 0.50, "いくつ正解できましたか？", ha="center", va="center",
            color=WHITE, fontsize=28)

    ax.text(0.5, 0.16, "高評価 & チャンネル登録お願いします！　次回もお楽しみに！",
            ha="center", va="center", color="#9090b0", fontsize=22)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG)
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
    title = quiz.get("title", "名馬当てクイズ！この馬は誰？")

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
