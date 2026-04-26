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
import matplotlib.colors as mcolors

OUTPUT_DIR = Path("slides")
FIGSIZE = (19.2, 10.8)  # 1920×1080
DPI = 100

# ─── カラーパレット（アンティーク調 3色構成） ───────────────────────────
BG       = "#0c1520"   # ダークネイビー（少し青みを抑えた）
BG_MID   = "#13233a"   # グラデーション用の中間色
ACCENT   = "#c4a44a"   # アンティークゴールド（くすんだ金）
ACCENT2  = "#7a3d54"   # ディープローズ／プラム（第3アクセント）
WHITE    = "#e9e5dc"   # ウォームオフホワイト
GRAY     = "#8898aa"   # セカンダリテキスト
PANEL    = "#122030"   # パネル背景
CHOICE_BG     = "#101c2a"
CHOICE_BORDER = "#284060"
CORRECT_BG    = "#0c3420"
CORRECT_BORDER = "#2d9955"
WRONG_BG      = "#320d18"
WRONG_BORDER  = "#bb3344"
CLUE_BG  = "#0b1a26"

CHOICE_LABELS = ["A", "B", "C", "D"]
PART_COLORS   = ["#c0392b", "#27ae60", "#8e44ad"]


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


def _draw_bg(ax, fig):
    """ネイビー背景に上→下グラデーションで奥行きを付ける"""
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    rows = 120
    cols = 200
    gradient = [[i / (rows - 1)] * cols for i in range(rows)]
    cmap = mcolors.LinearSegmentedColormap.from_list("bg", [BG_MID, BG, "#090e16"])
    ax.imshow(
        gradient,
        aspect="auto",
        extent=[0, 1, 0, 1],
        cmap=cmap,
        alpha=0.45,
        transform=ax.transAxes,
        zorder=0,
        origin="upper",
    )


def draw_banner(ax, text, bg=ACCENT, fg="#0c1520", fontsize=36):
    """バナー帯。下辺にプラムのアクセントストライプを添える。"""
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.0, 0.895), 1.0, 0.105,
        boxstyle="square,pad=0",
        facecolor=bg, edgecolor="none",
        transform=ax.transAxes, clip_on=False, zorder=10,
    ))
    # 下辺アクセント
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.0, 0.887), 1.0, 0.010,
        boxstyle="square,pad=0",
        facecolor=ACCENT2, edgecolor="none",
        transform=ax.transAxes, clip_on=False, zorder=11,
    ))
    # 右寄せの小さなダイヤ装飾
    ax.text(0.973, 0.948, "✦", ha="right", va="center",
            color=ACCENT2, fontsize=22, transform=ax.transAxes, zorder=12)
    # 本文（左に5px押し出してバランスを崩す）
    ax.text(0.497, 0.948, text, ha="center", va="center",
            color=fg, fontsize=fontsize, fontweight="bold",
            transform=ax.transAxes, zorder=12)


def _draw_section_line(ax, y, x_left=0.04, x_right=0.96, zorder=3):
    """セクション区切りの細線（ACCENT2色）"""
    ax.plot([x_left, x_right], [y, y],
            color=ACCENT2, linewidth=0.8, alpha=0.6,
            transform=ax.transAxes, zorder=zorder)


def draw_clue_header(ax, y, label):
    """◆ の代わりに横線で挟んだセクションラベル"""
    ax.plot([0.07, 0.32], [y, y], color=ACCENT2, linewidth=1.2, zorder=3)
    ax.plot([0.68, 0.93], [y, y], color=ACCENT2, linewidth=1.2, zorder=3)
    ax.text(0.5, y, label, ha="center", va="center",
            color=ACCENT, fontsize=28, fontweight="bold", zorder=3)


def draw_choice_box(ax, x, y, w, h, label, text, bg, border, fontsize=32):
    ax.add_patch(mpatches.FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.012",
        facecolor=bg, edgecolor=border, linewidth=2,
        zorder=5,
    ))
    # ラベル背景（正方形に近い角丸）
    ax.add_patch(mpatches.FancyBboxPatch(
        (x + 0.010, y + h * 0.15), 0.056, h * 0.70,
        boxstyle="round,pad=0.005",
        facecolor=border, edgecolor="none",
        zorder=6,
    ))
    ax.text(x + 0.038, y + h / 2, label, ha="center", va="center",
            color=WHITE, fontsize=fontsize - 4, fontweight="bold", zorder=7)
    ax.text(x + 0.085, y + h / 2, text, ha="left", va="center",
            color=WHITE, fontsize=fontsize, zorder=7)


def make_question_slide(q: dict, out_path: Path, clue_header: str = "G1 勝利歴",
                        part_title: str = ""):
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    _draw_bg(ax, fig)

    banner = (f"名馬当てクイズ【{part_title}】  ─  第{q['number']}問"
              if part_title else f"名馬当てクイズ  ─  第{q['number']}問")
    draw_banner(ax, banner)

    # タイトル（大きめ）＋ カウンター（小さめ）でメリハリ
    ax.text(0.50, 0.857, "この馬は誰？", ha="center", va="center",
            color=ACCENT, fontsize=65, fontweight="bold")
    ax.text(0.96, 0.855, f"Q{q['number']} / 5", ha="right", va="center",
            color=GRAY, fontsize=22)

    # ヒントパネル
    clues = q["clues"]
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.035, 0.465), 0.930, 0.365,
        boxstyle="round,pad=0.015",
        facecolor=CLUE_BG, edgecolor=ACCENT, linewidth=1.2,
        zorder=2,
    ))
    # パネル内上部に細いアクセント線
    _draw_section_line(ax, 0.825, x_left=0.045, x_right=0.955, zorder=3)
    draw_clue_header(ax, 0.812, clue_header)

    # 2列に分割（左列x=0.065、右列x=0.555 ─ 完全対称にしない）
    half = (len(clues) + 1) // 2
    col_left  = clues[:half]
    col_right = clues[half:]
    row_h = min(0.24 / max(half, 1), 0.068)
    base_y = 0.760 - row_h

    for i, clue in enumerate(col_left):
        y = base_y - i * row_h
        ax.text(0.065, y, f"✦  {clue}", ha="left", va="center",
                color=WHITE, fontsize=30, zorder=3)
    for i, clue in enumerate(col_right):
        y = base_y - i * row_h
        ax.text(0.555, y, f"✦  {clue}", ha="left", va="center",
                color=WHITE, fontsize=30, zorder=3)

    # 4択（ほんの少しだけ非対称な配置）
    choices = q["choices"]
    # A/C はやや左寄り、B/D はほんの少し低め・右寄り
    positions = [
        (0.027, 0.318, 0.447),  # A: x, y, w
        (0.527, 0.310, 0.443),  # B: B行は4pxほど低く
        (0.027, 0.157, 0.450),  # C
        (0.527, 0.150, 0.443),  # D: 同様に7px低め
    ]
    box_h = 0.128
    for i, (bx, by, bw) in enumerate(positions):
        if i < len(choices):
            draw_choice_box(ax, bx, by, bw, box_h,
                            CHOICE_LABELS[i], choices[i],
                            CHOICE_BG, CHOICE_BORDER, fontsize=34)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_answer_slide(q: dict, out_path: Path, part_title: str = ""):
    correct_idx = q["correct_index"]
    choices = q["choices"]
    correct_label = CHOICE_LABELS[correct_idx]
    correct_name  = choices[correct_idx]

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    _draw_bg(ax, fig)

    banner = (f"【{part_title}】第{q['number']}問  ─  答え合わせ！"
              if part_title else f"第{q['number']}問  ─  答え合わせ！")
    draw_banner(ax, banner, bg="#1a5c32", fg=WHITE)

    # 正解ラベル（「正解は」を小さく、馬名を大きく）
    ax.text(0.50, 0.842, "正解は", ha="center", va="center",
            color=GRAY, fontsize=28)
    ax.text(0.50, 0.802, f"{correct_label}.  {correct_name}！", ha="center", va="center",
            color=CORRECT_BORDER, fontsize=58, fontweight="bold")

    # 4択（正解=緑、不正解=暗赤色）
    box_h = 0.105
    positions = [
        (0.027, 0.578, 0.447),
        (0.527, 0.571, 0.443),
        (0.027, 0.452, 0.450),
        (0.527, 0.445, 0.443),
    ]
    for i, (bx, by, bw) in enumerate(positions):
        if i < len(choices):
            bg     = CORRECT_BG     if i == correct_idx else WRONG_BG
            border = CORRECT_BORDER if i == correct_idx else WRONG_BORDER
            draw_choice_box(ax, bx, by, bw, box_h,
                            CHOICE_LABELS[i], choices[i],
                            bg, border, fontsize=34)

    # 解説パネル
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.035, 0.095), 0.930, 0.268,
        boxstyle="round,pad=0.015",
        facecolor=PANEL, edgecolor=ACCENT2, linewidth=1.2,
        zorder=2,
    ))
    # 解説ラベル（横線 + テキスト）
    ax.plot([0.055, 0.175], [0.343, 0.343], color=ACCENT2, linewidth=1.0, zorder=3)
    ax.text(0.215, 0.343, "解 説", ha="center", va="center",
            color=ACCENT, fontsize=26, fontweight="bold", zorder=3)
    ax.plot([0.255, 0.375], [0.343, 0.343], color=ACCENT2, linewidth=1.0, zorder=3)

    ax.text(0.50, 0.218, q["display_explanation"], ha="center", va="center",
            color=WHITE, fontsize=36, multialignment="center", zorder=3)

    ax.text(0.965, 0.033, f"Q{q['number']} / 5", ha="right", va="center",
            color=GRAY, fontsize=20)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_title_slide(title: str, out_path: Path, total_q: int = 5, multipart: bool = False):
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    _draw_bg(ax, fig)

    draw_banner(ax, "名馬当てクイズ")

    # メインパネル（左右非対称マージン）
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.055, 0.200), 0.885, 0.625,
        boxstyle="round,pad=0.02",
        facecolor=PANEL, edgecolor=ACCENT, linewidth=2.5,
        zorder=2,
    ))
    # パネル右下コーナーにプラムのアクセント
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.820, 0.200), 0.120, 0.038,
        boxstyle="round,pad=0.005",
        facecolor=ACCENT2, edgecolor="none",
        zorder=3,
    ))

    # タイトル（大）
    ax.text(0.490, 0.678, title, ha="center", va="center",
            color=ACCENT, fontsize=46, fontweight="bold", multialignment="center",
            zorder=4)
    # サブ（小・regular）
    ax.text(0.490, 0.540, "ヒントから名馬を当てよう！", ha="center", va="center",
            color=WHITE, fontsize=28, zorder=4)

    q_label = (f"全{total_q}問（3パート）  ─  制限時間 15秒"
               if multipart else f"全{total_q}問  ─  制限時間 15秒")
    ax.text(0.490, 0.408, q_label, ha="center", va="center",
            color=ACCENT, fontsize=26, zorder=4)

    _draw_section_line(ax, 0.300, x_left=0.120, x_right=0.880, zorder=4)
    ax.text(0.490, 0.258, "✦  チャンネル登録・高評価よろしくお願いします！  ✦",
            ha="center", va="center", color=GRAY, fontsize=22, zorder=4)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_part_intro_slide(part_number: int, part_title: str, total_q: int, out_path: Path):
    color = PART_COLORS[(part_number - 1) % len(PART_COLORS)]

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    _draw_bg(ax, fig)

    draw_banner(ax, f"名馬当てクイズ  ─  第{part_number}パート")

    # パネル（右端を少しはみ出させてダイナミックに）
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.050, 0.205), 0.895, 0.618,
        boxstyle="round,pad=0.02",
        facecolor=PANEL, edgecolor=color, linewidth=3,
        zorder=2,
    ))
    # 左側に縦のアクセントバー
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.050, 0.205), 0.014, 0.618,
        boxstyle="square,pad=0",
        facecolor=color, edgecolor="none",
        zorder=3,
    ))

    ax.text(0.510, 0.700, f"第{part_number}パート", ha="center", va="center",
            color=GRAY, fontsize=30)
    ax.text(0.510, 0.545, part_title, ha="center", va="center",
            color=color, fontsize=78, fontweight="bold", zorder=4)

    _draw_section_line(ax, 0.440, x_left=0.120, x_right=0.880, zorder=4)

    ax.text(0.510, 0.358, f"全{total_q}問  ─  制限時間 15秒！", ha="center", va="center",
            color=WHITE, fontsize=28, zorder=4)

    plt.tight_layout(pad=0)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  保存: {out_path}")


def make_result_slide(out_path: Path):
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    _draw_bg(ax, fig)

    draw_banner(ax, "名馬当てクイズ  ─  全問終了！")

    ax.add_patch(mpatches.FancyBboxPatch(
        (0.055, 0.200), 0.885, 0.625,
        boxstyle="round,pad=0.02",
        facecolor=PANEL, edgecolor=ACCENT, linewidth=2,
        zorder=2,
    ))
    ax.text(0.490, 0.690, "全問終了！", ha="center", va="center",
            color=ACCENT, fontsize=52, fontweight="bold", zorder=3)
    ax.text(0.490, 0.560, "いくつ正解できましたか？", ha="center", va="center",
            color=WHITE, fontsize=30, zorder=3)

    _draw_section_line(ax, 0.460, x_left=0.120, x_right=0.880, zorder=4)

    ax.text(0.490, 0.345, "✦  高評価 & チャンネル登録お願いします！  ✦",
            ha="center", va="center", color=GRAY, fontsize=24, zorder=3)
    ax.text(0.490, 0.268, "次回もお楽しみに！", ha="center", va="center",
            color=ACCENT2, fontsize=28, zorder=3)

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

    title = quiz.get("title", "名馬当てクイズ！この馬は誰？")

    if quiz.get("multipart"):
        parts = quiz["parts"]
        total_q = sum(len(p["questions"]) for p in parts)

        print("タイトルスライド生成中...")
        make_title_slide(title, OUTPUT_DIR / "00_title.png", total_q=total_q, multipart=True)

        slide_count = 1
        for part in parts:
            pn = part["part_number"]
            pt = part["part_title"]
            clue_header = part.get("clue_header", "G1 勝利歴")
            questions = part["questions"]

            print(f"パート{pn}（{pt}）導入スライド生成中...")
            make_part_intro_slide(pn, pt, len(questions), OUTPUT_DIR / f"p{pn:02d}_00_intro.png")
            slide_count += 1

            for q in questions:
                print(f"  P{pn} Q{q['number']} スライド生成中...")
                make_question_slide(q, OUTPUT_DIR / f"p{pn:02d}_{q['number']:02d}q_question.png",
                                    clue_header, part_title=pt)
                make_answer_slide(q, OUTPUT_DIR / f"p{pn:02d}_{q['number']:02d}a_answer.png",
                                  part_title=pt)
                slide_count += 2

        print("結果スライド生成中...")
        make_result_slide(OUTPUT_DIR / "99_result.png")
        slide_count += 1
        print(f"\nslides/ フォルダに {slide_count} 枚のスライドを保存しました")

    else:
        questions = quiz.get("questions", [])
        clue_header = quiz.get("clue_header", "G1 勝利歴")

        print("タイトルスライド生成中...")
        make_title_slide(title, OUTPUT_DIR / "00_title.png", total_q=len(questions))

        for q in questions:
            print(f"Q{q['number']} スライド生成中...")
            make_question_slide(q, OUTPUT_DIR / f"{q['number']:02d}q_question.png", clue_header)
            make_answer_slide(q, OUTPUT_DIR / f"{q['number']:02d}a_answer.png")

        print("結果スライド生成中...")
        make_result_slide(OUTPUT_DIR / "99_result.png")
        total = 1 + len(questions) * 2 + 1
        print(f"\nslides/ フォルダに {total} 枚のスライドを保存しました")

    print("完了")


if __name__ == "__main__":
    main()
