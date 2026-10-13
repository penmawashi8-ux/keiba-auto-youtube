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


def piecewise(keys: list[tuple[float, float]]) -> str:
    """[(t, v), ...] を線形補間する ffmpeg 式。

    ffmpeg の式は入れ子が約100段までしか解釈できないので、if を一列に並べず
    二分探索の形（深さ log2(キー数)）に組む。
    """
    keys = [k for i, k in enumerate(keys) if i == 0 or k[0] - keys[i - 1][0] > 1e-6]
    if len(keys) == 1:
        return f"{keys[0][1]:.1f}"

    def seg(i: int) -> str:            # keys[i] と keys[i+1] の間の直線
        (t0, v0), (t1, v1) = keys[i], keys[i + 1]
        return f"{v0:.1f}+({v1 - v0:.1f})*(t-{t0:.3f})/{t1 - t0:.3f}"

    def tree(lo: int, hi: int) -> str:  # 区間 lo..hi-1 のどれかを選ぶ
        if hi - lo == 1:
            return seg(lo)
        mid = (lo + hi) // 2
        return f"if(lt(t,{keys[mid][0]:.3f}),{tree(lo, mid)},{tree(mid, hi)})"

    body = tree(0, len(keys) - 1)
    return (f"if(lt(t,{keys[0][0]:.3f}),{keys[0][1]:.1f},"
            f"if(gte(t,{keys[-1][0]:.3f}),{keys[-1][1]:.1f},{body}))")


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
# 追従カメラ用の拡大コース図と隊列
# ---------------------------------------------------------------------------
SB = 8.0                   # 拡大図の 1mあたりのピクセル（1馬身 ≒ 19px）
TRACK_W_M = 25.0           # コース幅（内ラチ〜外ラチ）
BIG_MARGIN = 640           # 拡大図の外側の余白（px）
BIG_A = STRAIGHT_M * SB / 2
BIG_RIN = CURVE_R_M * SB
BIG_ROUT = (CURVE_R_M + TRACK_W_M) * SB
BIG_W = int(2 * (BIG_A + BIG_ROUT + BIG_MARGIN))
BIG_H = int(2 * (BIG_ROUT + BIG_MARGIN))
BCX, BCY = BIG_W / 2, BIG_H / 2
VIEW_W = PANEL_X           # 追従カメラで見えるコースの幅（右側は順位パネル）
RACE_SPEEDUP = 3.0
# 右下の全体図（ゲートイン画面のコース図を切り抜いて縮小したもの）
MINI_CROP_Y, MINI_CROP_H = 170, 440
MINI_W, MINI_H = 330, 156
MINI_X, MINI_Y = PANEL_X + 10, 520
MINI_SX, MINI_SY = MINI_W / PANEL_X, MINI_H / MINI_CROP_H
ARROWS = "→↗↑↖←↙↓↘"


def big_point(s: float, lat: float) -> tuple[float, float]:
    """拡大図での座標。s は直線の始まりからの距離[m]、lat は内ラチからの距離[m]"""
    s %= LAP_M
    r = BIG_RIN + lat * SB
    if s < STRAIGHT_M:
        return BCX - BIG_A + s * SB, BCY + r
    s -= STRAIGHT_M
    if s < math.pi * CURVE_R_M:
        th = math.pi / 2 - s / CURVE_R_M
        return BCX + BIG_A + r * math.cos(th), BCY + r * math.sin(th)
    s -= math.pi * CURVE_R_M
    if s < STRAIGHT_M:
        return BCX + BIG_A - s * SB, BCY - r
    s -= STRAIGHT_M
    th = -math.pi / 2 - s / CURVE_R_M
    return BCX - BIG_A + r * math.cos(th), BCY + r * math.sin(th)


def arrow_at(s: float) -> str:
    """進行方向の矢印（画面上の向き。y は下向きが正）"""
    x0, y0 = big_point(s, 0)
    x1, y1 = big_point(s + 1.0, 0)
    ang = math.atan2(-(y1 - y0), x1 - x0)          # 数学の向き（上が正）
    return ARROWS[int(round(ang / (math.pi / 4))) % 8]


def make_big_track_png(path: Path, font: str, start_s: float, dist: int) -> None:
    """追従カメラ用の拡大コース図（芝の刈り目・内外ラチ・ゴール線・ハロン棒・矢印・コーナー名）"""
    d = f"hypot(max(abs(X-{BCX:.1f})-{BIG_A:.1f},0),Y-{BCY:.1f})"
    fx, _ = big_point(STRAIGHT_M, 0)
    on_track = f"between(ld(0),{BIG_RIN:.1f},{BIG_ROUT:.1f})"

    # スタート地点は2400mだとゴール前の直線上にあり、最後の直線で紛らわしいので拡大図には描かない
    # （どこからスタートするかはゲートインの全体図で見せる）
    def ch(rail, goal, t1, t2, infield, outside):
        return (f"st(0,{d});st(1,{on_track});"
                f"if(lt(abs(ld(0)-{BIG_RIN:.1f}),3)+lt(abs(ld(0)-{BIG_ROUT:.1f}),3),{rail},"
                f"if(ld(1)*lt(abs(X-{fx:.1f}),4)*gt(Y,{BCY:.1f}),{goal},"
                f"if(ld(1),if(mod(floor(X/56)+floor(Y/56),2),{t1},{t2}),"
                f"if(lt(ld(0),{BIG_RIN:.1f}),{infield},{outside}))))")

    geq = (f"geq=r='{ch(240, 230, 66, 58, 34, 22)}':g='{ch(240, 40, 156, 142, 100, 64)}'"
           f":b='{ch(240, 40, 68, 60, 40, 30)}'")
    texts = []

    def label(text, x, y, size, color):
        p = path.parent / f"big_label_{len(texts):03d}.txt"
        p.write_text(text, encoding="utf-8")
        texts.append(f"drawtext=textfile='{p}':fontfile='{font}':fontsize={size}:fontcolor={color}:"
                     f"x={x:.0f}-text_w/2:y={y:.0f}-text_h/2:borderw=3:bordercolor=black@0.6")

    for k in range(200, dist, 200):                    # ハロン棒（残り距離）
        x, y = big_point(STRAIGHT_M - k, TRACK_W_M + 7)
        label(f"残り{k}m", x, y, 52, "white")
    s = 0.0
    while s < LAP_M:                                   # 進行方向の矢印（内側）
        x, y = big_point(s, -9)
        label(arrow_at(s), x, y, 64, "0xFFFFFF@0.8")
        s += 50.0
    for txt, s in (("1コーナー", STRAIGHT_M + math.pi * CURVE_R_M * 0.25),
                   ("2コーナー", STRAIGHT_M + math.pi * CURVE_R_M * 0.75),
                   ("3コーナー", 2 * STRAIGHT_M + math.pi * CURVE_R_M * 1.25),
                   ("4コーナー", 2 * STRAIGHT_M + math.pi * CURVE_R_M * 1.75)):
        x, y = big_point(s, -30)
        label(txt, x, y, 80, "0xFFF59D")
    for s in (STRAIGHT_M * 0.12, STRAIGHT_M * 0.66):             # スタート・ゴールの表示と重ならない位置
        x, y = big_point(s, TRACK_W_M + 24)
        label("正面スタンド", x, y, 64, "0xE0E0E0")
    for s in (STRAIGHT_M * 0.2, STRAIGHT_M * 0.55, STRAIGHT_M * 0.9):
        x, y = big_point(STRAIGHT_M + math.pi * CURVE_R_M + s, TRACK_W_M + 24)
        label("向こう正面", x, y, 64, "0xE0E0E0")
    x, y = big_point(STRAIGHT_M, TRACK_W_M + 12)
    label("ゴール", x, y, 72, "0xFF5252")
    for s in (STRAIGHT_M * 0.25, STRAIGHT_M * 0.8, 2 * STRAIGHT_M + math.pi * CURVE_R_M * 0.5):
        x, y = big_point(s, -22)
        label("左回り（反時計回り）", x, y, 48, "0xC8E6C9")
    script = path.with_suffix(".filter.txt")
    script.write_text(f"color=c=black:s={BIG_W}x{BIG_H}:d=1,format=rgb24,{geq}," + ",".join(texts),
                      encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-filter_complex_script", str(script),
                    "-frames:v", "1", str(path)], check=True)


def formation(finish: list[float], styles: list[float], dist: int, taus: list[float]):
    """各時刻の走行距離 D[i][k] と内ラチからの距離 LAT[i][k]。

    道中: 逃げ・先行は前、差し・追込は後ろで縦長の隊列（最大10馬身ほど）。
    残り4割で後ろの馬が差を詰め、勝ち馬のゴール時点で各馬の着差に収束する。
    横位置: 基本は内ラチ沿い。前後4.8m・左右3.4m以内に馬がいるときだけ、その外側に並ぶ。
    スタート直後はゲートの並びから内に寄り、最後の直線では差し・追込が外に持ち出す。
    """
    n = len(finish)
    win_t = min(finish)
    v = dist / win_t
    D = [[0.0] * len(taus) for _ in range(n)]
    LAT = [[0.0] * len(taus) for _ in range(n)]
    for k, tau in enumerate(taus):
        u = tau / win_t
        for i in range(n):
            if tau >= win_t:
                D[i][k] = min(dist - (finish[i] - win_t) * v + (tau - win_t) * v, dist)
                continue
            ramp = _smooth(u / 0.1)
            late = _smooth((u - 0.6) / 0.4)
            gap = (3.0 + styles[i] * 26.0) * ramp
            wob = 0.6 * math.sin(2 * math.pi * (2.5 * u + i * 0.37)) * ramp * (1 - late)
            D[i][k] = max(v * tau - gap * (1 - late) - (finish[i] - win_t) * v * late + wob, 0.0)
        prev = [LAT[i][k - 1] if k > 0 else 2.0 + i * 3.6 for i in range(n)]
        # 内にいる馬ほど優先して今の車線を保つ。抜きにかかる馬のほうが外を回る
        # （抜いた馬が内に切れ込んで、抜かれた馬を外へ押し出すとふらついて見えるため）
        if k > 0 and tau >= win_t:     # ゴール後は横に動かさない
            for i in range(n):
                LAT[i][k] = prev[i]
            continue
        order = sorted(range(n), key=lambda i: (round(prev[i], 1), -D[i][k]))
        placed = []
        for i in order:
            rem = dist - D[i][k]
            want = 1.8 + _smooth((520 - rem) / 140) * max(styles[i] - 0.3, 0.0) * 14.0   # 直線で外に持ち出す
            g = _smooth(D[i][k] / 160)                                                  # ゲートから内へ
            lat = (2.0 + i * 3.6) * (1 - g) + want * g
            if k > 0:                      # 内へは少しずつしか寄らない
                lat = max(lat, prev[i] - 0.5)

            def hits(x):
                # 前後4.8m・左右3.4m以内に馬がいたら重なるとみなす。
                # 前の馬に追いついている最中は前方12mまで見て、早めに外へ出す
                out = []
                for j, pd, pl in placed:
                    ahead = pd - D[i][k]
                    closing = k > 0 and (D[i][k] - D[i][k - 1]) - (D[j][k] - D[j][k - 1]) > 0.3
                    if -4.8 < ahead < (12.0 if closing else 4.8) and abs(pl - x) < 3.4:
                        out.append(pl)
                return out

            for _ in range(n):
                h = hits(lat)
                if not h:
                    break
                lat = max(h) + 3.6
            lat = min(lat, TRACK_W_M - 1.5)
            if k > 0:                      # 外へも急には動かない
                lat = min(lat, prev[i] + 1.2)
            placed.append((i, D[i][k], lat))
            LAT[i][k] = lat
    # 細かい揺れを残さないよう、横位置を前後の時刻でならす
    for i in range(n):
        row = LAT[i][:]
        for k in range(len(taus)):
            win = row[max(0, k - 2):k + 3]
            LAT[i][k] = sum(win) / len(win)
    return D, LAT


def camera_keys(D, taus, start_s: float, dist: int) -> list[tuple[float, float, float]]:
    """各時刻のカメラ（切り出し位置の左上）。馬群の真ん中を画面の左右中央に置く"""
    keys = []
    for k, tau in enumerate(taus):
        ds = [row[k] for row in D]
        c = (max(ds) + min(ds)) / 2
        if max(ds) - min(ds) > 120:
            c = max(ds) - 60
        c = min(c, dist - 18)
        x, y = big_point(start_s + c, TRACK_W_M / 2)
        cx = min(max(x - VIEW_W / 2, 0), BIG_W - W)
        cy = min(max(y - H / 2, 0), BIG_H - H)
        keys.append((tau, cx, cy))
    return keys


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
           gate_sec: float = 3.0, frames: dict | None = None) -> dict:
    """動画（映像のみ）を書き出し、各場面の開始時刻を返す。finish は各馬の走破タイム（names と同じ順）

    場面: 説明ページ → ゲートイン（コース全体図）→ レース（追従カメラ＋順位パネル＋全体図）→ 結果ページ
    """
    rd = Renderer(font, work)
    start_s = STRAIGHT_M - (dist - LAP_M)          # 直線の始まりからのスタート地点
    overview, big = work / "overview.png", work / "big_track.png"
    make_track_png(overview)
    make_big_track_png(big, font, start_s, dist)

    t = 0.0
    page_windows = []
    for pg in before:
        t1 = rd.page(pg, t)
        page_windows.append((t, t1))
        t = t1
    gate_t = t
    race_start = gate_t + gate_sec
    win_t, t_end = min(finish), max(finish)
    race_end = race_start + t_end / RACE_SPEEDUP + 2.0
    vt = lambda tau: race_start + tau / RACE_SPEEDUP   # noqa: E731
    n = len(names)

    # ---- 隊列とカメラ（0.25秒（動画時間）ごとのキーフレーム）
    step = 0.25 * RACE_SPEEDUP
    taus = sorted({round(i * step, 3) for i in range(int(t_end / step) + 2)} | {round(win_t, 3)})
    D, LAT = formation(finish, styles, dist, taus)
    cam = camera_keys(D, taus, start_s, dist)
    cam_x = piecewise([(vt(tau), cx) for tau, cx, _ in cam])
    cam_y = piecewise([(vt(tau), cy) for tau, _, cy in cam])

    # ---- ゲートイン（コース全体図で、どこからどちらへ走るかを見せる）
    g0, g1 = gate_t, race_start
    sx, sy = course_point(start_s)
    fx, fy = course_point(STRAIGHT_M)
    rd.dt("東京競馬場 芝2400m", 32, "white", "40", "24", g0, g1)
    rd.dt("左回り（反時計回り）に1周してゴール", 24, "0xFFEB3B", f"{CX}-text_w/2", f"{CY}-30", g0, g1)
    for txt, x, y in (("→　→　→", CX - 60, CY + R - TRACK_HALF - 40), ("←　←　←", CX - 60, CY - R + TRACK_HALF + 12),
                      ("↑", CX + A + R - TRACK_HALF - 34, CY - 14), ("↓", CX - A - R + TRACK_HALF + 14, CY - 14)):
        rd.dt(txt, 26, "0xFFFFFF@0.85", str(int(x)), str(int(y)), g0, g1)
    rd.dt("|", 46, "0xFF5252", f"{fx:.0f}-text_w/2", f"{fy - 26:.0f}", g0, g1)
    rd.dt("ゴール", 22, "0xFF5252", f"{fx:.0f}-text_w/2", f"{fy + TRACK_HALF + 6:.0f}", g0, g1)
    rd.dt("|", 46, "0xFFEB3B", f"{sx:.0f}-text_w/2", f"{sy - 26:.0f}", g0, g1)
    rd.dt("スタート", 22, "0xFFEB3B", f"{sx:.0f}-text_w/2", f"{sy + TRACK_HALF + 6:.0f}", g0, g1)
    rd.dt("ゲートイン", 36, "white", f"{CX}-text_w/2", "118", g0, g1 - 0.6, ":box=1:boxcolor=black@0.5:boxborderw=10")
    for i in range(n):
        x, y = course_point(start_s, -TRACK_HALF + 6 + i * (2 * TRACK_HALF - 12) / max(n - 1, 1))
        rd.dt("●", 26, COLORS[i], f"{x:.0f}-text_w/2", f"{y:.0f}-text_h/2", g0, g1, DOT_BORDER[i])

    # ---- レース（追従カメラ）
    r0, r1 = race_start, race_end
    rd.dt("スタート！", 72, "0xFFEB3B", f"{VIEW_W // 2}-text_w/2", "90", g1 - 0.6, r0 + 1.4,
          ":borderw=6:bordercolor=black")
    rd.dt("東京 芝2400m", 22, "white", "24", "18", r0, r1, ":box=1:boxcolor=black@0.45:boxborderw=6")
    rd.dt(f"{RACE_SPEEDUP:g}倍速", 18, "white", "24", "54", r0, r1, ":box=1:boxcolor=black@0.45:boxborderw=5")
    for i in range(n):
        pts = [big_point(start_s + D[i][k], LAT[i][k]) for k in range(len(taus))]
        xs = [(vt(tau), p[0] - c[1]) for tau, p, c in zip(taus, pts, cam)]
        ys = [(vt(tau), p[1] - c[2]) for tau, p, c in zip(taus, pts, cam)]
        xe, ye = piecewise(xs), piecewise(ys)
        rd.dt("●", 30, COLORS[i], f"'{xe}-text_w/2'", f"'{ye}-text_h/2'", r0, r1, DOT_BORDER[i])
        rd.dt(str(i + 1), 17, NUM_COLORS[i], f"'{xe}-text_w/2'", f"'{ye}-text_h/2+1'", r0, r1)
        # 全体図（右下）の小さな点
        mpts = [course_point(start_s + D[i][k], -TRACK_HALF + 4 + LAT[i][k] * 1.2) for k in range(len(taus))]
        mxe = piecewise([(vt(tau), MINI_X + p[0] * MINI_SX) for tau, p in zip(taus, mpts)])
        mye = piecewise([(vt(tau), MINI_Y + (p[1] - MINI_CROP_Y) * MINI_SY) for tau, p in zip(taus, mpts)])
        rd.dt("●", 13, COLORS[i], f"'{mxe}-text_w/2'", f"'{mye}-text_h/2'", r0, r1, DOT_BORDER[i])
    rd.dt("コース全体", 18, "0xBDBDBD", str(MINI_X + 6), str(MINI_Y - 26), r0, r1)

    lead = [max(D[i][k] for i in range(n)) for k in range(len(taus))]
    for rem in range(dist - 200, -1, -200):
        a0 = next((vt(tau) for tau, d in zip(taus, lead) if dist - d <= rem + 200), None)
        a1 = min(next((vt(tau) for tau, d in zip(taus, lead) if dist - d <= rem), vt(win_t)), vt(win_t) - 0.05)
        if a0 is not None and a1 > a0:
            rd.dt(f"残り {rem + 200}m", 34, "white", f"{VIEW_W // 2}-text_w/2", "24", max(a0, r0 + 1.4), a1 - 0.04,
                  ":box=1:boxcolor=black@0.55:boxborderw=10")
    rd.dt("ゴール！", 64, "0xFF5252", f"{VIEW_W // 2}-text_w/2", "90", vt(win_t), r1,
          ":borderw=6:bordercolor=black")

    # ---- 右側の順位パネル（順位が変わると行が入れ替わる）
    p0 = g0
    rd.dt("現在の順位", 26, "0xFFEB3B", str(PANEL_X + 22), "84", p0, r1)
    rd.dt("先頭との差", 18, "0xBDBDBD", "w-text_w-14", "92", p0, r1)
    row_y = lambda r: 138 + r * 54   # noqa: E731
    seg_ts = [g0] + [r0 + k * 1.0 for k in range(int(r1 - r0) + 1)]
    ranks_at = []
    for t0 in seg_ts:
        tau = max((t0 - r0) * RACE_SPEEDUP, 0.0)
        k = min(range(len(taus)), key=lambda j: abs(taus[j] - tau))
        ds = [D[i][k] for i in range(n)]
        order = sorted(range(n), key=lambda i: (finish[i] if ds[i] >= dist - 0.01 else 1e9, -ds[i]))
        ranks_at.append((t0, order, ds))
    for i in range(n):
        ykeys = []
        for k, (t0, order, _ds) in enumerate(ranks_at):
            y = row_y(order.index(i))
            ykeys.append((t0, y))
            if k + 1 < len(ranks_at):
                ykeys.append((ranks_at[k + 1][0] - 0.4, y))   # 入れ替わりは0.4秒で動かす
        ye = piecewise(sorted(ykeys))
        rd.dt("●", 28, COLORS[i], str(PANEL_X + 18), f"'{ye}'", p0, r1, DOT_BORDER[i])
        rd.dt(str(i + 1), 15, NUM_COLORS[i], str(PANEL_X + 27), f"'{ye}+7'", p0, r1)
        rd.dt(names[i], 21, "white", str(PANEL_X + 50), f"'{ye}+2'", p0, r1)
    for k, (t0, order, ds) in enumerate(ranks_at):
        t1 = ranks_at[k + 1][0] if k + 1 < len(ranks_at) else r1
        lead_d = max(ds)
        for r, i in enumerate(order):
            label = "先頭" if r == 0 else margin_label(lead_d - ds[i])
            if lead_d < 5:
                label = "－"
            if all(d >= dist - 0.01 for d in ds) and r > 0:
                label = margin_label((finish[i] - finish[order[0]]) * dist / finish[order[0]])
            rd.dt(label, 19, "0xFFF59D", "w-text_w-14", str(row_y(r) + 5), t0, t1 - 0.001)

    # ---- 結果ページ
    t = race_end
    for pg in after:
        t1 = rd.page(pg, t)
        page_windows.append((t, t1))
        t = t1
    total = t

    # ---- 合成
    pages_on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in page_windows) or "0"
    graph = (
        f"[0:v]format=yuv420p,loop=loop=-1:size=1:start=0,setpts=N/{FPS}/TB,"
        f"crop=w={W}:h={H}:x='{cam_x}':y='{cam_y}'[cam];"
        f"[1:v]format=yuv420p,loop=loop=-1:size=1:start=0,setpts=N/{FPS}/TB,split=2[ov][ovm];"
        f"[ovm]crop={PANEL_X}:{MINI_CROP_H}:0:{MINI_CROP_Y},scale={MINI_W}:{MINI_H}[mini];"
        f"color=c=0x0E1812:s={W - PANEL_X}x{H}:r={FPS}:d={total:.2f}[pnl];"
        f"color=c={BG_PAGE}:s={W}x{H}:r={FPS}:d={total:.2f}[pg];"
        f"[cam][pnl]overlay=x={PANEL_X}:y=0[c1];[c1][mini]overlay=x={MINI_X}:y={MINI_Y}[c2];"
        f"[c2][ov]overlay=enable='between(t,{g0:.3f},{g1:.3f})'[c3];"
        f"[c3][pg]overlay=enable='{pages_on}'[base];[base]" + ",".join(rd.chain) + "[v]")
    script = work / "filter.txt"
    script.write_text(graph, encoding="utf-8")
    # 大きなコース図は1回だけ読み込み、loop フィルターでメモリ上の1枚を繰り返す（毎フレームのPNG展開を避ける）
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(big), "-i",
                    str(overview), "-filter_complex_script", str(script), "-map", "[v]", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{total:.2f}", str(out)], check=True)
    marks = {"gate": gate_t, "race_start": race_start, "goal": vt(win_t), "race_end": race_end, "total": total,
             "pages": page_windows, "before_end": gate_t}
    for label, sec in (frames or {}).items():
        if isinstance(sec, tuple):            # ("race_end", 3.0) = レース終了の3秒後
            sec = marks[sec[0]] + sec[1]
        elif isinstance(sec, str):
            sec = marks[sec]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{sec:.2f}", "-i", str(out),
                        "-vframes", "1", "-q:v", "2", str(out.parent / f"frame_{label}.jpg")], check=True)
    return marks
