#!/usr/bin/env python3
"""夢の対決シミュレーションの動画レンダラー（ffmpeg のみ。Pillow / numpy / drawbox は使わない）。

構成: 説明ページ（前提条件など）→ ゲートイン・スタート → レース（コース図）→ 結果ページ

■ レース画面
  東京競馬場の芝コースを上から見た図（左回り＝反時計回り）。背景のコース図は ffmpeg の geq で
  1枚の画像として作り、馬（●と番号）はコース上の位置を時間の関数（drawtext の x/y 式）で動かす。
  コース全体を映すと僅差が見えないため、右側に「現在の順位と先頭からの馬身差」を常時表示する。

  コース形状: 2本の直線（各 STRAIGHT_M）と2つの半円をつないだ形で、1周 LAP_M。
  下側が正面スタンド前の直線（左→右に走る）、ゴールはその右端。2400m はゴールの
  (2400 − 1周) m 手前からスタートし、ゴールを1回通過して1周する。

■ 文字
  日本語は textfile= で渡す（エスケープ問題を避ける）。% を含む文は expansion=none で表示する。
"""

import math
import subprocess
from pathlib import Path

W, H, FPS = 1280, 720, 30
SPEEDUP = 4.0
LAP_M = 2083.1             # 東京 芝 Aコース 1周
STRAIGHT_M = 525.9         # 最後の直線
CURVE_R_M = (LAP_M - 2 * STRAIGHT_M) / (2 * math.pi)
PX = min(860 / (STRAIGHT_M + 2 * CURVE_R_M), 330 / (2 * CURVE_R_M))   # 1mあたりのピクセル
CX, CY = 470, 392          # コース図の中心
A = STRAIGHT_M * PX / 2    # 直線の半分の長さ（px）
R = CURVE_R_M * PX         # カーブの半径（px）
TRACK_HALF = 20            # コース幅の半分（px）
PANEL_X = 930              # 右側の順位パネルの左端
BG_PAGE = "0x0E1A12"
# JRAの枠色（1枠から 白・黒・赤・青・黄・緑・橙・桃）。8頭以下なので 馬番 = 枠番 として扱う
COLORS = ["#FFFFFF", "#212121", "#E53935", "#1E88E5", "#FDD835", "#43A047", "#FB8C00", "#F48FB1"]
NUM_COLORS = ["black", "white", "white", "white", "black", "white", "black", "black"]
# 黒や白の●が背景に溶けないよう縁取りする
DOT_BORDER = [":borderw=2:bordercolor=0x333333", ":borderw=2:bordercolor=white"] + [":borderw=1:bordercolor=0x111111"] * 6


def badge(i: int, x: int, size: int) -> list[dict]:
    """説明ページ用: 枠色の●と馬番（x は●の左端）"""
    return [{"text": "●", "x": x, "size": int(size * 1.15), "color": COLORS[i], "extra": DOT_BORDER[i], "dy": -2},
            {"text": str(i + 1), "x": x + int(size * 0.33), "size": int(size * 0.62), "color": NUM_COLORS[i],
             "dy": int(size * 0.22)}]


# ---------------------------------------------------------------------------
# コース上の位置
# ---------------------------------------------------------------------------
def course_point(s: float, off: float = 0.0) -> tuple[float, float]:
    """直線の始まり（4コーナー出口）からの距離 s[m] の地点の画面座標。off は外側へのずれ（px）"""
    s %= LAP_M
    if s < STRAIGHT_M:                                   # 正面の直線（左→右）
        return CX - A + s * PX, CY + R + off
    s -= STRAIGHT_M
    if s < math.pi * CURVE_R_M:                          # 1〜2コーナー（右側の半円、下→上）
        th = math.pi / 2 - s / CURVE_R_M
        return CX + A + (R + off) * math.cos(th), CY + (R + off) * math.sin(th)
    s -= math.pi * CURVE_R_M
    if s < STRAIGHT_M:                                   # 向こう正面（右→左）
        return CX + A - s * PX, CY - R - off
    s -= STRAIGHT_M                                      # 3〜4コーナー（左側の半円、上→下）
    th = -math.pi / 2 - s / CURVE_R_M
    return CX - A + (R + off) * math.cos(th), CY + (R + off) * math.sin(th)


def _smooth(v: float) -> float:
    v = min(max(v, 0.0), 1.0)
    return v * v * (3 - 2 * v)


def distance_at(tau: float, my_t: float, win_t: float, style: float, dist: float) -> float:
    """経過時間 tau [秒] での走行距離 [m]。

    道中は勝ち馬と同じ平均ペースで、脚質ぶん前後（逃げは前・追込は後ろ）に隊列を組む。
    残り約3割で隊列が崩れ、勝ち馬のゴール時点で各馬の着差に収束する。その後は同じ速度でゴールまで走る。
    """
    v = dist / win_t
    if tau >= win_t:
        return min(dist - (my_t - win_t) * v + (tau - win_t) * v, dist)
    u = tau / win_t
    late = _smooth((u - 0.7) / 0.3)
    style_offset = (0.5 - style) * 25.0 * min(u / 0.12, 1.0)
    return max(v * tau + style_offset * (1 - late) - (my_t - win_t) * v * late, 0.0)


def piecewise(keys: list[tuple[float, float]]) -> str:
    """[(t, v), ...] を線形補間する ffmpeg 式"""
    expr = f"{keys[-1][1]:.1f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(keys, keys[1:]))):
        if t1 - t0 < 1e-6:
            continue
        expr = f"if(lt(t,{t1:.3f}),{v0:.1f}+({v1 - v0:.1f})*(t-{t0:.3f})/{t1 - t0:.3f},{expr})"
    return f"if(lt(t,{keys[0][0]:.3f}),{keys[0][1]:.1f},{expr})"


def margin_label(m: float) -> str:
    """距離差[m] を馬身表記に（1馬身 ≒ 2.4m）"""
    b = m / 2.4
    if b < 0.15:
        return "ハナ"
    if b < 0.3:
        return "アタマ"
    if b < 0.45:
        return "クビ"
    if b > 10:
        return "大差"
    return f"{b:.1f}馬身"


# ---------------------------------------------------------------------------
# 背景（コース図）
# ---------------------------------------------------------------------------
def make_track_png(path: Path) -> None:
    d = f"hypot(max(abs(X-{CX})-{A:.1f},0),Y-{CY})"
    rin, rout = R - TRACK_HALF, R + TRACK_HALF

    def ch(panel, rail, track, infield, outside):
        return (f"st(0,{d});if(gt(X,{PANEL_X}),{panel},"
                f"if(lt(abs(ld(0)-{rin:.1f}),1.6)+lt(abs(ld(0)-{rout:.1f}),1.6),{rail},"
                f"if(between(ld(0),{rin:.1f},{rout:.1f}),{track},if(lt(ld(0),{rin:.1f}),{infield},{outside}))))")

    expr = (f"geq=r='{ch(14, 235, 64, 30, 18)}':g='{ch(24, 235, 150, 96, 40)}':b='{ch(18, 235, 66, 36, 24)}'")
    script = path.with_suffix(".filter.txt")
    script.write_text(f"color=c=black:s={W}x{H}:d=1,format=rgb24,{expr}", encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-filter_complex_script", str(script),
                    "-frames:v", "1", str(path)], check=True)


# ---------------------------------------------------------------------------
# 説明ページ
# ---------------------------------------------------------------------------
def text_page(title: str, rows: list, sec: float, size: int = 24, top: int = 112) -> dict:
    """タイトル＋本文のページ。rows の要素は 文字列 / {"text","color","size","x"} / 列のリスト"""
    items = [{"text": title, "x": 60, "y": 38, "size": 40, "color": "0xFFEB3B"}]
    y = top
    for row in rows:
        if row is None or row == "":
            y += int(size * 0.8)
            continue
        cols = row if isinstance(row, list) else [row]
        rsize = size
        for c in cols:
            c = {"text": c} if isinstance(c, str) else dict(c)
            rsize = c.get("size", size)
            x = c.get("x", 60)
            if "badge" in c:               # 馬名の前に枠色の●と馬番を付ける
                for b in badge(c["badge"], x, rsize):
                    items.append({**b, "y": y + b.pop("dy", 0)})
                x += int(rsize * 1.5)
            items.append({"x": x, "y": y, "size": rsize, "color": c.get("color", "white"),
                          "text": c["text"], "extra": c.get("extra", "")})
        y += int(rsize * 1.62)
    return {"sec": sec, "items": items}


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
class Renderer:
    def __init__(self, font: str, work: Path):
        self.font = font
        self.work = work
        self.work.mkdir(parents=True, exist_ok=True)
        self.chain = []
        self.n = 0

    def tf(self, text: str) -> str:
        self.n += 1
        p = self.work / f"t{self.n:04d}.txt"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def dt(self, text: str, size: int, color: str, x: str, y: str, t0: float, t1: float, extra: str = ""):
        self.chain.append(
            f"drawtext=textfile='{self.tf(text)}':fontfile='{self.font}':fontsize={size}:fontcolor={color}:"
            f"expansion=none:x={x}:y={y}{extra}:enable='between(t,{t0:.3f},{t1:.3f})'")

    def page(self, pg: dict, t0: float) -> float:
        t1 = t0 + pg["sec"]
        for it in pg["items"]:
            self.dt(it["text"], it["size"], it["color"], str(it["x"]), str(it["y"]), t0, t1 - 0.001,
                    it.get("extra", ""))
        return t1


def render(out: Path, work: Path, font: str, names: list[str], styles: list[float],
           finish: list[float], before: list[dict], after: list[dict], dist: int = 2400,
           gate_sec: float = 2.0, frames: dict | None = None) -> dict:
    """動画を書き出し、各場面の開始時刻を返す。finish は各馬の走破タイム（names と同じ順）"""
    rd = Renderer(font, work)
    track = work / "track.png"
    make_track_png(track)

    # 説明ページ
    t = 0.0
    page_windows = []
    for pg in before:
        t1 = rd.page(pg, t)
        page_windows.append((t, t1))
        t = t1
    gate_t = t
    race_start = gate_t + gate_sec
    win_t, t_end = min(finish), max(finish)
    race_end = race_start + t_end / SPEEDUP + 1.5
    vt = lambda tau: race_start + tau / SPEEDUP   # noqa: E731

    # ---- レース画面の固定要素（コース名・方向・コーナー・スタート/ゴール）
    on0, on1 = gate_t, race_end
    start_s = STRAIGHT_M - (dist - LAP_M)          # 直線の始まりからのスタート地点
    fx, fy = course_point(STRAIGHT_M)
    sx, sy = course_point(start_s)
    rd.dt("東京競馬場 芝2400m（左回り）", 30, "white", "40", "24", on0, on1)
    rd.dt("左回り＝反時計回りに進みます", 22, "0xFFEB3B", f"{CX}-text_w/2", f"{CY}-40", on0, on1)
    rd.dt("矢印の方向に1周してゴール", 22, "0xC8E6C9", f"{CX}-text_w/2", f"{CY}", on0, on1)
    for txt, x, y in (("→　→　→", CX - 60, CY + R - TRACK_HALF - 40), ("←　←　←", CX - 60, CY - R + TRACK_HALF + 12),
                      ("↑", CX + A + R - TRACK_HALF - 34, CY - 14), ("↓", CX - A - R + TRACK_HALF + 14, CY - 14)):
        rd.dt(txt, 26, "0xFFFFFF@0.85", str(int(x)), str(int(y)), on0, on1)
    for txt, s, off in (("1コーナー", STRAIGHT_M + math.pi * CURVE_R_M * 0.25, 62),
                        ("2コーナー", STRAIGHT_M + math.pi * CURVE_R_M * 0.75, 62),
                        ("3コーナー", 2 * STRAIGHT_M + math.pi * CURVE_R_M * 1.25, 62),
                        ("4コーナー", 2 * STRAIGHT_M + math.pi * CURVE_R_M * 1.75, 62)):
        x, y = course_point(s, off)
        rd.dt(txt, 20, "0xE0E0E0", f"{x:.0f}-text_w/2", f"{y:.0f}-text_h/2", on0, on1)
    rd.dt("正面スタンド", 22, "0xBDBDBD", f"{CX}-text_w/2", f"{CY + R + TRACK_HALF + 34:.0f}", on0, on1)
    rd.dt("向こう正面", 20, "0xBDBDBD", f"{CX}-text_w/2", f"{CY - R - TRACK_HALF - 34:.0f}", on0, on1)
    rd.dt("|", 46, "0xFF5252", f"{fx:.0f}-text_w/2", f"{fy - 26:.0f}", on0, on1)
    rd.dt("ゴール", 22, "0xFF5252", f"{fx:.0f}-text_w/2", f"{fy + TRACK_HALF + 6:.0f}", on0, on1)
    rd.dt("|", 46, "0xFFEB3B", f"{sx:.0f}-text_w/2", f"{sy - 26:.0f}", on0, on1)
    rd.dt("スタート", 22, "0xFFEB3B", f"{sx:.0f}-text_w/2", f"{sy + TRACK_HALF + 6:.0f}", on0, on1)
    rd.dt(f"{int(SPEEDUP)}倍速", 20, "0xFFFFFF@0.7", "40", "66", on0, on1)
    rd.dt("ゲートイン", 34, "white", f"{CX}-text_w/2", "120", gate_t, race_start - 0.5,
          ":box=1:boxcolor=black@0.5:boxborderw=10")
    rd.dt("スタート！", 64, "0xFFEB3B", f"{CX}-text_w/2", "104", race_start - 0.5, race_start + 1.2,
          ":borderw=5:bordercolor=black")

    # ---- 馬の位置（0.5秒（動画時間）ごとのキーフレーム）
    step = 0.5 * SPEEDUP
    taus = sorted({round(i * step, 3) for i in range(int(t_end / step) + 2)} | {round(win_t, 3)})
    n = len(names)
    lane = [-TRACK_HALF + 6 + i * (2 * TRACK_HALF - 12) / max(n - 1, 1) for i in range(n)]
    dpos = [[distance_at(tau, finish[i], win_t, styles[i], dist) for tau in taus] for i in range(n)]
    for i in range(n):
        pts = [course_point(start_s + d, lane[i]) for d in dpos[i]]
        xs = [(vt(tau), p[0]) for tau, p in zip(taus, pts)]
        ys = [(vt(tau), p[1]) for tau, p in zip(taus, pts)]
        xe, ye = piecewise(xs), piecewise(ys)
        rd.dt("●", 30, COLORS[i % len(COLORS)], f"'{xe}-text_w/2'", f"'{ye}-text_h/2'", on0, on1,
              DOT_BORDER[i % len(DOT_BORDER)])
        rd.dt(str(i + 1), 16, NUM_COLORS[i % len(NUM_COLORS)], f"'{xe}-text_w/2'", f"'{ye}-text_h/2+1'", on0, on1)

    # ---- 残り距離（先頭基準、200mごと）
    lead = [max(dpos[i][k] for i in range(n)) for k in range(len(taus))]
    for rem in range(dist - 200, -1, -200):
        a0 = next((vt(tau) for tau, d in zip(taus, lead) if dist - d <= rem + 200), None)
        a1 = min(next((vt(tau) for tau, d in zip(taus, lead) if dist - d <= rem), vt(win_t)), vt(win_t) - 0.05)
        if a0 is not None and a1 > a0:
            rd.dt(f"残り {rem + 200}m", 30, "white", f"{CX}-text_w/2", "116", max(a0, race_start + 1.2), a1 - 0.04,
                  ":box=1:boxcolor=black@0.5:boxborderw=8")
    rd.dt("ゴール！", 40, "0xFF5252", f"{CX}-text_w/2", "110", vt(win_t), race_end,
          ":box=1:boxcolor=black@0.6:boxborderw=10")

    # ---- 右側の順位パネル（順位が変わると行が入れ替わる）
    rd.dt("現在の順位", 26, "0xFFEB3B", str(PANEL_X + 22), "110", on0, on1)
    rd.dt("先頭との差", 18, "0xBDBDBD", "w-text_w-14", "118", on0, on1)
    row_y = lambda r: 168 + r * 56   # noqa: E731
    seg = 1.0
    seg_ts = [gate_t] + [race_start + k * seg for k in range(int((race_end - race_start) / seg) + 1)]
    ranks_at = []
    for t0 in seg_ts:
        tau = max((t0 - race_start) * SPEEDUP, 0.0)
        ds = [distance_at(tau, finish[i], win_t, styles[i], dist) for i in range(n)]
        # ゴール後は着順（走破タイム順）で並べる
        order = sorted(range(n), key=lambda i: (finish[i] if ds[i] >= dist else 1e9, -ds[i]))
        ranks_at.append((t0, order, ds))
    for i in range(n):
        ykeys = []
        for k, (t0, order, _ds) in enumerate(ranks_at):
            y = row_y(order.index(i))
            ykeys.append((t0, y))
            if k + 1 < len(ranks_at):
                ykeys.append((ranks_at[k + 1][0] - 0.35, y))   # 入れ替わりは0.35秒で動かす
        ye = piecewise(sorted(ykeys))
        rd.dt("●", 28, COLORS[i % len(COLORS)], str(PANEL_X + 18), f"'{ye}'", on0, on1,
              DOT_BORDER[i % len(DOT_BORDER)])
        rd.dt(str(i + 1), 15, NUM_COLORS[i % len(NUM_COLORS)], str(PANEL_X + 27), f"'{ye}+7'", on0, on1)
        rd.dt(names[i], 21, "white", str(PANEL_X + 50), f"'{ye}+2'", on0, on1)
    for k, (t0, order, ds) in enumerate(ranks_at):
        t1 = ranks_at[k + 1][0] if k + 1 < len(ranks_at) else on1
        lead_d = max(ds)
        for r, i in enumerate(order):
            label = "先頭" if r == 0 else margin_label(lead_d - ds[i])
            if lead_d < 5:            # スタート直後で隊列ができる前
                label = "－"
            if all(d >= dist for d in ds) and r > 0:
                label = margin_label((finish[i] - finish[order[0]]) * dist / finish[order[0]])
            rd.dt(label, 19, "0xFFF59D", "w-text_w-14", str(row_y(r) + 5), t0, t1 - 0.001)

    # ---- 結果ページ
    t = race_end
    for pg in after:
        t1 = rd.page(pg, t)
        page_windows.append((t, t1))
        t = t1
    total = t

    # ---- 合成: コース図の上に、説明・結果ページの間だけ無地の背景を重ねる
    pages_on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in page_windows) or "0"
    graph = (f"[0:v]fps={FPS},format=yuv420p[trk];color=c={BG_PAGE}:s={W}x{H}:r={FPS}:d={total:.2f}[pg];"
             f"[trk][pg]overlay=enable='{pages_on}'[base];[base]" + ",".join(rd.chain) + "[v]")
    script = work / "filter.txt"
    script.write_text(graph, encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(track),
                    "-filter_complex_script", str(script), "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-r", str(FPS), "-t", f"{total:.2f}", str(out)], check=True)
    marks = {"gate": gate_t, "race_start": race_start, "goal": vt(win_t), "race_end": race_end, "total": total,
             "pages": page_windows}
    for label, sec in (frames or {}).items():
        if isinstance(sec, tuple):            # ("race_end", 3.0) = レース終了の3秒後
            sec = marks[sec[0]] + sec[1]
        elif isinstance(sec, str):
            sec = marks[sec]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{sec:.2f}", "-i", str(out),
                        "-vframes", "1", "-q:v", "2", str(out.parent / f"frame_{label}.jpg")], check=True)
    return marks
