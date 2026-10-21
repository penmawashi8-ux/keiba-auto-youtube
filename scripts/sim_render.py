#!/usr/bin/env python3
"""夢の対決シミュレーションの動画レンダラー（ffmpeg のみ。Pillow / numpy / drawbox は使わない）。

構成: 説明ページ（前提条件など）→ ゲートイン・スタート → レース（コース図）→ 結果ページ

■ レース画面
  競馬場の芝コースを上から見た図。背景のコース図（芝・内側のダート・
  観客スタンド）は ffmpeg の geq で1枚の大きな画像として作り、馬群を追うカメラで切り抜いて拡大する。
  馬は上から見た馬の形のアイコン（geq で作る透明PNG）を進行方向に回転させて重ね、位置は時間の関数
  （overlay / drawtext の x/y 式）で動かす。右側に「現在の順位と先頭からの馬身差」を常時表示する。
  演出: 実況テロップ（読み上げ音声は呼び出し側で合成）、最後の直線のスローモーション、ゴールの
  フラッシュと1着の表示。

  コース形状: 2本の直線（各 STRAIGHT_M）と2つの半円をつないだ形で、1周 LAP_M（set_course で切り替え）。
  下側が正面スタンド前の直線。左回り（東京）は左→右に走りゴールは右端、右回り（京都）は左右反転。
  ゴールの (距離 − 1周) m 手前からスタートし、ゴールを1回通過して1周する。
  頭数は18頭まで。枠色は馬番と頭数から JRA の決まり（waku_of）で決める。

■ 文字
  日本語は textfile= で渡す（エスケープ問題を避ける）。% を含む文は expansion=none で表示する。
"""

import math
import subprocess
from pathlib import Path

W, H, FPS = 1280, 720, 30
CX, CY = 470, 392          # コース図の中心
TRACK_HALF = 20            # コース幅の半分（px）
PANEL_X = 930              # 右側の順位パネルの左端
BG_PAGE = "0x0E1A12"
# JRAの枠色（1枠から 白・黒・赤・青・黄・緑・橙・桃）
COLORS = ["#FFFFFF", "#212121", "#E53935", "#1E88E5", "#FDD835", "#43A047", "#FB8C00", "#F48FB1"]
NUM_COLORS = ["black", "white", "white", "white", "black", "white", "black", "black"]
# 黒や白の●が背景に溶けないよう縁取りする
DOT_BORDER = [":borderw=2:bordercolor=0x333333", ":borderw=2:bordercolor=white"] + [":borderw=1:bordercolor=0x111111"] * 6

# ---------------------------------------------------------------------------
# コース（set_course で切り替える。形は「2本の直線＋2つの半円」で近似）
#   右回りのコースは左回りの図を左右反転して描く（正面の直線は右→左に走り、ゴールは左端）
# ---------------------------------------------------------------------------
COURSES = {
    "東京": {"name": "東京競馬場", "lap": 2083.1, "straight": 525.9, "right": False, "note": "Aコース"},
    "京都": {"name": "京都競馬場", "lap": 1782.8, "straight": 328.4, "right": True, "note": "内回り Aコース"},
    "京都外": {"name": "京都競馬場", "lap": 1894.3, "straight": 403.7, "right": True, "note": "外回り Aコース"},
}
# 外回りを使う距離（京都の芝。1600m は内・外の両方があるが、重賞は外回り）
OUTER = {"京都": {1600, 1800, 2200, 2400, 3000, 3200}}


def course_key(venue: str, dist: int) -> str:
    """COURSES のキー（京都の外回りは「京都外」）"""
    return venue + "外" if dist in OUTER.get(venue, set()) and venue + "外" in COURSES else venue


def set_course(venue: str) -> None:
    """コースの形に関わる値（モジュール全体で使う）を、その競馬場のものにする"""
    global COURSE, LAP_M, STRAIGHT_M, CURVE_R_M, PX, A, R, RIGHT, TURN, TURN_FULL
    global BIG_A, BIG_RIN, BIG_ROUT, BIG_W, BIG_H, BCX, BCY
    COURSE = COURSES[venue]
    LAP_M, STRAIGHT_M, RIGHT = COURSE["lap"], COURSE["straight"], COURSE["right"]
    TURN = "右回り" if RIGHT else "左回り"
    TURN_FULL = "右回り（時計回り）" if RIGHT else "左回り（反時計回り）"
    CURVE_R_M = (LAP_M - 2 * STRAIGHT_M) / (2 * math.pi)
    PX = min(860 / (STRAIGHT_M + 2 * CURVE_R_M), 330 / (2 * CURVE_R_M))   # 1mあたりのピクセル
    A = STRAIGHT_M * PX / 2    # 直線の半分の長さ（px）
    R = CURVE_R_M * PX         # カーブの半径（px）
    BIG_A = STRAIGHT_M * SB / 2
    BIG_RIN = CURVE_R_M * SB
    BIG_ROUT = (CURVE_R_M + TRACK_W_M) * SB
    BIG_W = int(2 * (BIG_A + BIG_ROUT + BIG_MARGIN))
    BIG_H = int(2 * (BIG_ROUT + BIG_MARGIN))
    BCX, BCY = BIG_W / 2, BIG_H / 2


def waku_of(umaban: int, n: int) -> int:
    """JRAの枠番。8頭以下は 馬番 = 枠番、それより多いと外の枠から2頭・3頭ずつ入る"""
    if n <= 8:
        return umaban
    base, extra = divmod(n, 8)
    sizes = [base + (1 if w >= 8 - extra else 0) for w in range(8)]   # 内の枠から順に頭数
    acc = 0
    for w, sz in enumerate(sizes, 1):
        acc += sz
        if umaban <= acc:
            return w
    return 8


def badge(i: int, x: int, size: int, num: int | None = None) -> list[dict]:
    """説明ページ用: 枠色の●と馬番（x は●の左端）。i は枠番-1、num は馬番（省略時は i+1）"""
    num = i + 1 if num is None else num
    dx = int(size * (0.33 if num < 10 else 0.16))
    return [{"text": "●", "x": x, "size": int(size * 1.15), "color": COLORS[i], "extra": DOT_BORDER[i], "dy": -2},
            {"text": str(num), "x": x + dx, "size": int(size * 0.62), "color": NUM_COLORS[i],
             "dy": int(size * 0.22)}]


# ---------------------------------------------------------------------------
# コース上の位置
# ---------------------------------------------------------------------------
def course_point(s: float, off: float = 0.0) -> tuple[float, float]:
    """直線の始まり（4コーナー出口）からの距離 s[m] の地点の画面座標。off は外側へのずれ（px）"""
    x, y = _course_point_left(s, off)
    return (2 * CX - x, y) if RIGHT else (x, y)


def _course_point_left(s: float, off: float) -> tuple[float, float]:
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


def jra_margin(m: float) -> str:
    """前の馬との距離差[m] を、JRAの着差の表記に（ハナ・アタマ・クビ・1/2・3/4・1 1/4 … 大差）"""
    b = m / 2.4
    for lim, label in ((0.1, "ハナ"), (0.2, "アタマ"), (0.35, "クビ"), (0.6, "1/2馬身"), (0.85, "3/4馬身")):
        if b < lim:
            return label
    if b < 2.125:                               # 1〜2馬身は 1/4 刻み
        q = max(round(b * 4) / 4, 1.0)          # 3/4 と 1 の境目で「0 3/4」にならないように
        whole, frac = int(q), q - int(q)
        return f"{whole}{' ' + {0.25: '1/4', 0.5: '1/2', 0.75: '3/4'}[frac] if frac else ''}馬身"
    if b < 3.75:                                # 2〜3.5馬身は 1/2 刻み
        q = round(b * 2) / 2
        return f"{int(q)}{' 1/2' if q % 1 else ''}馬身"
    if b < 10.5:
        return f"{round(b)}馬身"
    return "大差"


def margin_label(m: float) -> str:
    """距離差[m] を馬身表記に（1馬身 ≒ 2.4m）"""
    b = m / 2.4
    if b < 0.15:
        return "ハナ"
    if b < 0.3:
        return "アタマ"
    if b < 0.45:
        return "クビ"
    if b >= 10:
        return f"{b:.0f}馬身"
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
VIEW_W = PANEL_X           # 追従カメラで見えるコースの幅（右側は順位パネル）
RACE_SPEEDUP = 3.0
ZOOM = 1.5                 # 追従カメラの拡大率（拡大図を切り抜いて拡大。馬アイコンは拡大後に重ねる）
# 右下の全体図（ゲートイン画面のコース図を切り抜いて縮小したもの）
MINI_CROP_Y, MINI_CROP_H = 170, 440
MINI_W, MINI_H = 330, 156
MINI_X, MINI_Y = PANEL_X + 10, 520
MINI_SX, MINI_SY = MINI_W / PANEL_X, MINI_H / MINI_CROP_H
ARROWS = "→↗↑↖←↙↓↘"


def big_point(s: float, lat: float) -> tuple[float, float]:
    """拡大図での座標。s は直線の始まりからの距離[m]、lat は内ラチからの距離[m]"""
    x, y = _big_point_left(s, lat)
    return (2 * BCX - x, y) if RIGHT else (x, y)


def _big_point_left(s: float, lat: float) -> tuple[float, float]:
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
    # 内側のダートコース（芝の内側 3m あけて幅 22m）と、正面直線の外側の観客スタンド
    d_out, d_in = BIG_RIN - 3 * SB, BIG_RIN - 25 * SB
    st0, st1 = BIG_ROUT + 12 * SB, BIG_ROUT + 52 * SB
    stand = f"lt(abs(X-{BCX:.1f}),{BIG_A + 60:.1f})*between(Y-{BCY:.1f},{st0:.1f},{st1:.1f})"

    def crowd(seed, base):
        # 観客 = 9px ごとの点。色は座標から作る疑似乱数（背景は座席の灰色）
        return (f"if(lt(mod(X,8),5)*lt(mod(Y,10),6),{base}+40*sin(floor(X/8)*{seed}+floor(Y/10)*78.233),"
                f"if(gt(Y-{BCY:.1f},{st1 - 30:.1f}),40,62))")

    def ch(rail, goal, t1, t2, infield, outside, dirt, seed):
        return (f"st(0,{d});st(1,{on_track});"
                f"if(lt(abs(ld(0)-{BIG_RIN:.1f}),3)+lt(abs(ld(0)-{BIG_ROUT:.1f}),3),{rail},"
                f"if(ld(1)*lt(abs(X-{fx:.1f}),4)*gt(Y,{BCY:.1f}),{goal},"
                f"if(ld(1),if(mod(floor(X/56)+floor(Y/56),2),{t1},{t2}),"
                f"if(between(ld(0),{d_in:.1f},{d_out:.1f}),{dirt}+6*sin(X*0.9+Y*1.3),"
                f"if(lt(ld(0),{BIG_RIN:.1f}),{infield},if({stand},{crowd(seed, 105)},{outside}))))))")

    geq = (f"geq=r='{ch(240, 230, 66, 58, 34, 22, 150, 12.9898)}'"
           f":g='{ch(240, 40, 156, 142, 100, 64, 112, 39.346)}'"
           f":b='{ch(240, 40, 68, 60, 40, 30, 74, 7.233)}'")
    texts = []

    def label(text, x, y, size, color):
        p = path.parent / f"big_label_{len(texts):03d}.txt"
        p.write_text(text, encoding="utf-8")
        texts.append(f"drawtext=textfile='{p}':fontfile='{font}':fontsize={size / ZOOM:.0f}:fontcolor={color}:"
                     f"x={x:.0f}-text_w/2:y={y:.0f}-text_h/2:borderw=3:bordercolor=black@0.6")

    for k in range(200, dist, 200):                    # ハロン棒（残り距離）
        if k > LAP_M - 60:      # 1周より長い距離では、スタート直後の標識がゴール付近と重なるので描かない
            continue
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
    x, y = big_point(STRAIGHT_M, -14)
    label("ゴール", x, y, 72, "0xFF5252")
    for s in (STRAIGHT_M * 0.25, STRAIGHT_M * 0.8, 2 * STRAIGHT_M + math.pi * CURVE_R_M * 0.5):
        x, y = big_point(s, -22)
        label(TURN_FULL, x, y, 48, "0xC8E6C9")
    script = path.with_suffix(".filter.txt")
    script.write_text(f"color=c=black:s={BIG_W}x{BIG_H}:d=1,format=rgb24,{geq}," + ",".join(texts),
                      encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-filter_complex_script", str(script),
                    "-frames:v", "1", str(path)], check=True)


def formation(finish: list[float], styles: list[float], dist: int, taus: list[float]):
    """各時刻の走行距離 D[i][k] と内ラチからの距離 LAT[i][k]。

    道中: 逃げ・先行は前、差し・追込は後ろで縦長の隊列（最大10馬身ほど）。
    残り4割で後ろの馬が差を詰め、勝ち馬のゴール時点で各馬の着差に収束する。
    横位置: 基本は内ラチ沿い。前後4.8m・左右4.0m以内に馬がいるときだけ、その外側に並ぶ。
    スタート直後はゲートの並びから内に寄り、最後の直線では差し・追込が外に持ち出す。
    """
    n = len(finish)
    win_t = min(finish)
    v = dist / win_t
    # 多頭数ほど隊列が長くなる（5頭で最大約12馬身、18頭で約20馬身）
    spread = 1.0 + max(n - 5, 0) / 13 * 0.7
    gate_w = min(3.6, (TRACK_W_M - 4.0) / max(n - 1, 1))       # ゲートの並び（内から馬番順）
    lane = 4.2 if n <= 8 else 3.0                               # 横に並ぶときの間隔（多頭数は詰める）
    D = [[0.0] * len(taus) for _ in range(n)]
    LAT = [[0.0] * len(taus) for _ in range(n)]
    for k, tau in enumerate(taus):
        u = tau / win_t
        for i in range(n):
            if tau >= win_t:          # 勝ち馬のゴール後: 着差どおりに入線し、ゴール後は少し減速して走り抜ける
                d = tau - finish[i]
                D[i][k] = dist + v * d * (0.75 if d > 0 else 1.0)
                continue
            ramp = _smooth(u / 0.1)
            # 仕掛けのタイミングは馬ごとにずらす（差し・追込ほど早く動き、逃げ・先行は直線まで粘る）。
            # 全馬が同時に動くと、道中の順と着順が逆のとき直線入口で全馬が横一列に並んでしまう
            u0 = 0.66 + 0.14 * (1 - styles[i])
            late = _smooth((u - u0) / (1 - u0))
            gap = (3.0 + styles[i] * 26.0) * spread * ramp
            wob = 0.6 * math.sin(2 * math.pi * (2.5 * u + i * 0.37)) * ramp * (1 - late)
            D[i][k] = max(v * tau - gap * (1 - late) - (finish[i] - win_t) * v * late + wob, 0.0)
        prev = [LAT[i][k - 1] if k > 0 else 2.0 + i * gate_w for i in range(n)]
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
            want = 1.8 + _smooth((STRAIGHT_M - 5 - rem) / 140) * max(styles[i] - 0.3, 0.0) * 14.0   # 直線で外へ
            g = _smooth(D[i][k] / 160)                                                  # ゲートから内へ
            lat = (2.0 + i * gate_w) * (1 - g) + want * g
            if k > 0:                      # 内へは少しずつしか寄らない
                lat = max(lat, prev[i] - 0.5)

            def hits(x):
                # 前後4.8m・左右4.0m以内に馬がいたら重なるとみなす。
                # 前の馬に追いついている最中は前方12mまで見て、早めに外へ出す
                out = []
                for j, pd, pl in placed:
                    ahead = pd - D[i][k]
                    closing = k > 0 and (D[i][k] - D[i][k - 1]) - (D[j][k] - D[j][k - 1]) > 0.3
                    if -4.8 < ahead < (12.0 if closing else 4.8) and abs(pl - x) < lane - 0.2:
                        out.append(pl)
                return out

            for _ in range(n if g > 0.6 else 0):     # ゲートを出た直後は横並びのまま（重なり判定しない）
                h = hits(lat)
                if not h:
                    break
                lat = max(h) + lane
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
        c = max(c, max(ds) - 30)        # 馬群が長いときは先頭が見える位置を優先（映る範囲は約77m）
        x, y = big_point(start_s + c, TRACK_W_M / 2)
        cx = min(max(x - VIEW_W / 2 / ZOOM, 0), BIG_W - W / ZOOM)
        cy = min(max(y - H / 2 / ZOOM, 0), BIG_H - H / ZOOM)
        keys.append((tau, cx, cy))
    return keys


class TimeMap:
    """レース内の時刻 tau[秒] ⇔ 動画の時刻。

    ふだんは RACE_SPEEDUP 倍速。先頭が残り SLOW_FROM_M に来るまでに SLOW_SPEEDUP 倍速まで落とし、
    最後の直線の叩き合いからゴールまでをスローで見せる。
    """

    def __init__(self, start: float, win_t: float, t_end: float, lead_at):
        tau_s = lead_at(SLOW_FROM_M)
        tau_a = max(tau_s - 6.0, 0.0)
        self.taus, self.vts = [0.0], [start]
        step = 0.01
        tau = 0.0
        while tau < t_end + 10:
            r = min(max((tau - tau_a) / max(tau_s - tau_a, 1e-6), 0.0), 1.0)
            sp = RACE_SPEEDUP + (SLOW_SPEEDUP - RACE_SPEEDUP) * r
            tau += step
            self.taus.append(tau)
            self.vts.append(self.vts[-1] + step / sp)
        self.slow_from = self.vt(tau_a)

    @staticmethod
    def _interp(xs, ys, x):
        import bisect
        j = min(max(bisect.bisect_right(xs, x) - 1, 0), len(xs) - 2)
        return ys[j] + (ys[j + 1] - ys[j]) * (x - xs[j]) / (xs[j + 1] - xs[j])

    def vt(self, tau: float) -> float:
        return self._interp(self.taus, self.vts, tau)

    def tau(self, vt: float) -> float:
        return max(self._interp(self.vts, self.taus, vt), 0.0)


SLOW_SPEEDUP = 1.4          # 最後の直線の叩き合い（スロー）
SLOW_FROM_M = 230           # 先頭が残りこの距離になったらスロー
ICON = 96                   # 馬アイコンの画像サイズ（px、中心＝騎手）
COATS = [(112, 62, 30), (150, 78, 34), (66, 40, 26), (124, 70, 36), (48, 34, 28), (138, 84, 44),
         (90, 52, 30), (160, 96, 52)]   # 鹿毛・栗毛・黒鹿毛など


def make_horse_icon(path: Path, i: int, coat_i: int | None = None) -> None:
    """上から見た馬（右向き）と、枠色の勝負服の騎手。透明背景の PNG を geq で作る"""
    c = ICON / 2

    def ell(cx, cy, rx, ry):          # 楕円の内側で 1、境界はなめらかに 0 へ
        return f"clip((1-(pow((X-{cx})/{rx},2)+pow((Y-{cy})/{ry},2)))*6,0,1)"

    body = f"max(max({ell(c - 4, c, 23, 10.5)},{ell(c + 22, c, 13, 5.2)}),{ell(c - 30, c, 9, 2.4)})"
    jock = f"clip(({11.5}-hypot(X-{c},Y-{c}))*1.5,0,1)"
    ring = f"clip(({13.5}-hypot(X-{c},Y-{c}))*1.5,0,1)"
    silk = COLORS[i]
    sr_, sg, sb_ = int(silk[1:3], 16), int(silk[3:5], 16), int(silk[5:7], 16)
    coat = COATS[(i if coat_i is None else coat_i) % len(COATS)]
    edge = 235 if i == 1 else 25          # 黒い勝負服は白で縁取り

    def chan(cv, sv):
        return f"st(0,{body});st(1,{jock});st(2,{ring});{cv}*(1-ld(2))+({edge}*(1-ld(1))+{sv}*ld(1))*ld(2)"

    geq = (f"geq=r='{chan(coat[0], sr_)}':g='{chan(coat[1], sg)}':b='{chan(coat[2], sb_)}'"
           f":a='255*max(st(0,{body}),{ring})'")
    script = path.with_suffix(".filter.txt")
    script.write_text(f"color=c=black@0:s={ICON}x{ICON}:d=1,format=rgba,{geq}", encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-filter_complex_script", str(script),
                    "-frames:v", "1", str(path)], check=True)


def commentary(names: list[str], D, LAT, taus, finish, dist: int) -> list[tuple[float, str, str]]:
    """隊列から実況を作る。(レース内の時刻, 画面の文, 読み上げる文)"""
    n = len(names)
    win_t = min(finish)
    lead = [max(D[i][k] for i in range(n)) for k in range(len(taus))]

    def k_at(m):                       # 先頭が m 地点に来たキーフレーム
        return next((k for k, d in enumerate(lead) if d >= m), len(taus) - 1)

    def order(k):
        return sorted(range(n), key=lambda i: -D[i][k])

    out = [(0.2, "スタートしました！", "スタートしました！")]
    k = k_at(220)
    o = order(k)
    out.append((taus[k], f"ハナを切ったのは{names[o[0]]}、2番手に{names[o[1]]}",
                f"ハナを切ったのは{names[o[0]]}。2番手に{names[o[1]]}"))
    k = k_at(700)
    o = order(k)
    out.append((taus[k], f"最後方に{names[o[-1]]}、じっくり脚をためる",
                f"最後方に{names[o[-1]]}。じっくりと脚をためます"))
    k = k_at(1000)
    # キーフレームの間を補間して、先頭がちょうど1000mに来た時刻を出す
    t1000 = taus[k] if k == 0 else (taus[k - 1] + (taus[k] - taus[k - 1]) * (1000 - lead[k - 1])
                                    / max(lead[k] - lead[k - 1], 1e-6))
    mid = 59.8 if dist <= 2400 else 61.6     # 1000m通過の平均的なタイム（長距離は遅い）
    pace = "ややハイペース" if t1000 < mid - 1.0 else "落ち着いた流れ" if t1000 > mid + 1.0 else "平均ペース"
    out.append((t1000, f"1000m通過は{t1000:.1f}秒、{pace}", f"1000メートル通過は{t1000:.1f}秒。{pace}です"))
    if dist - LAP_M > 900:                   # 1周以上走るレース: 1周目のスタンド前
        k = k_at(dist - LAP_M + 150)
        o = order(k)
        out.append((taus[k], f"1周目のスタンド前、先頭は{names[o[0]]}", f"1周目のスタンド前を通過。先頭は{names[o[0]]}"))
    k = k_at(dist - (STRAIGHT_M + math.pi * CURVE_R_M * 0.5))      # 3〜4コーナーの中間
    out.append((taus[k], "3コーナーから4コーナー、後続が差を詰める！", "3コーナーから4コーナー。後ろの馬が差を詰めてきた！"))
    k = k_at(dist - STRAIGHT_M + 5)                                  # 直線に入ったところ
    o = order(k)
    out.append((taus[k], f"最後の直線！先頭は{names[o[0]]}！", f"さあ最後の直線！先頭は{names[o[0]]}！"))
    k, k2 = k_at(dist - STRAIGHT_M * 0.75), k_at(dist - 150)          # 直線の前半で伸びてくる馬
    o = order(k)
    top3 = sorted(range(n), key=lambda i: finish[i])[:3]
    cand = [i for i in o[1:] if i in top3] or o[1:]          # 上位に来る馬の中で、いちばん伸びる馬
    gain = max(cand, key=lambda i: (D[i][k2] - lead[k2]) - (D[i][k] - lead[k]))
    lat = LAT[gain][k2]
    side = "外から" if lat > 8 else "内から" if lat < 4 else "馬場の真ん中から"
    out.append((taus[k], f"{side}{names[gain]}が伸びてくる！", f"{side}{names[gain]}が伸びてくる！"))
    k = k_at(dist - min(200, STRAIGHT_M * 0.6))                     # 残り200m（短い直線では手前）
    o = order(k)
    a, b = o[0], o[1]
    if D[a][k] - D[b][k] < 2.4:
        out.append((taus[k], f"{names[a]}と{names[b]}、激しい叩き合い！", f"{names[a]}と{names[b]}！激しい叩き合い！"))
    else:
        out.append((taus[k], f"{names[a]}が抜け出した！", f"{names[a]}が抜け出した！{names[b]}も追う！"))
    fin = sorted(range(n), key=lambda i: finish[i])
    w = names[fin[0]]
    if finish[fin[1]] - finish[fin[0]] < 0.1:
        out.append((win_t - 1.6, f"{w}か！{w}だ！", f"{w}か！{w}だ！"))
    else:
        out.append((win_t - 1.6, f"{w}、先頭でゴールイン！", f"{w}、先頭でゴールイン！"))
    out.append((max(finish) + 0.3, f"2着{names[fin[1]]}、3着{names[fin[2]]}",
                f"2着は{names[fin[1]]}、3着は{names[fin[2]]}"))
    return out


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
                for b in badge(c["badge"], x, rsize, c.get("num")):
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
           gate_sec: float = 3.0, frames: dict | None = None, short: list[str] | None = None,
           tts=None, venue: str = "東京", nums: list[int] | None = None) -> dict:
    """動画（映像のみ）を書き出し、各場面の開始時刻を返す。finish は各馬の走破タイム（names と同じ順）

    short: レース中に馬の上に出す短い名前（"" なら出さない）。tts(文) → (音声ファイル, 秒) または None で実況を読み上げる
    venue: コース（COURSES のキー）。nums: 馬番（省略時は 1, 2, …）。枠色は馬番と頭数から決める
    （読み上げた音声と開始時刻は戻り値の "voice" に入れる。音声の合成は呼び出し側で行う）

    場面: 説明ページ → ゲートイン（コース全体図）→ レース（追従カメラ＋順位パネル＋全体図）→ 結果ページ
    """
    set_course(venue)
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
    n = len(names)
    short = short or names
    nums = nums or list(range(1, n + 1))
    wk = [waku_of(nums[i], n) - 1 for i in range(n)]      # 枠色の番号（0〜7）

    # ---- 時間の進み方（最後の直線はスロー）と、隊列・カメラ（動画の0.25秒ごとのキーフレーム）
    fine = [i * 0.1 for i in range(int((t_end + 2) / 0.1) + 1)]
    fd, _ = formation(finish, styles, dist, fine)
    lead_at = lambda m: next((tau for k, tau in enumerate(fine)          # noqa: E731
                              if max(row[k] for row in fd) >= dist - m), win_t)
    tm = TimeMap(race_start, win_t, t_end, lead_at)
    vt = tm.vt
    race_end = vt(t_end) + 3.0
    taus = sorted({round(tm.tau(race_start + j * 0.25), 3) for j in range(int((race_end - race_start) / 0.25) + 2)}
                  | {round(win_t, 3)})
    D, LAT = formation(finish, styles, dist, taus)
    cam = camera_keys(D, taus, start_s, dist)
    cam_x = piecewise([(vt(tau), cx) for tau, cx, _ in cam])
    cam_y = piecewise([(vt(tau), cy) for tau, _, cy in cam])

    # ---- ゲートイン（コース全体図で、どこからどちらへ走るかを見せる）
    g0, g1 = gate_t, race_start
    sx, sy = course_point(start_s)
    fx, fy = course_point(STRAIGHT_M)
    rd.dt(f"{COURSE['name']} 芝{dist}m", 32, "white", "40", "24", g0, g1)
    rd.dt(f"{TURN_FULL}に1周してゴール", 24, "0xFFEB3B", f"{CX}-text_w/2", f"{CY}-30", g0, g1)
    bottom, top, rside, lside = ("←　←　←", "→　→　→", "↓", "↑") if RIGHT else ("→　→　→", "←　←　←", "↑", "↓")
    for txt, x, y in ((bottom, CX - 60, CY + R - TRACK_HALF - 40), (top, CX - 60, CY - R + TRACK_HALF + 12),
                      (rside, CX + A + R - TRACK_HALF - 34, CY - 14), (lside, CX - A - R + TRACK_HALF + 14, CY - 14)):
        rd.dt(txt, 26, "0xFFFFFF@0.85", str(int(x)), str(int(y)), g0, g1)
    rd.dt("|", 46, "0xFF5252", f"{fx:.0f}-text_w/2", f"{fy - 26:.0f}", g0, g1)
    rd.dt("ゴール", 22, "0xFF5252", f"{fx:.0f}-text_w/2", f"{fy + TRACK_HALF + 6:.0f}", g0, g1)
    rd.dt("|", 46, "0xFFEB3B", f"{sx:.0f}-text_w/2", f"{sy - 26:.0f}", g0, g1)
    rd.dt("スタート", 22, "0xFFEB3B", f"{sx:.0f}-text_w/2", f"{sy + TRACK_HALF + 6:.0f}", g0, g1)
    rd.dt("ゲートイン", 36, "white", f"{CX}-text_w/2", "118", g0, g1 - 0.6, ":box=1:boxcolor=black@0.5:boxborderw=10")
    for i in range(n):
        x, y = course_point(start_s, -TRACK_HALF + 6 + i * (2 * TRACK_HALF - 12) / max(n - 1, 1))
        rd.dt("●", 26 if n <= 8 else 16, COLORS[wk[i]], f"{x:.0f}-text_w/2", f"{y:.0f}-text_h/2", g0, g1,
              DOT_BORDER[wk[i]])

    # ---- レース（追従カメラ）
    r0, r1 = race_start, race_end
    rd.dt("スタート！", 72, "0xFFEB3B", f"{VIEW_W // 2}-text_w/2", "90", g1 - 0.6, r0 + 1.4,
          ":borderw=6:bordercolor=black")
    rd.dt(f"{COURSE['name'][:2]} 芝{dist}m", 22, "white", "24", "18", r0, r1, ":box=1:boxcolor=black@0.45:boxborderw=6")
    rd.dt(f"{RACE_SPEEDUP:g}倍速", 18, "white", "24", "54", r0, tm.slow_from, ":box=1:boxcolor=black@0.45:boxborderw=5")
    rd.dt("スローモーション", 22, "0xFFEB3B", "24", "54", tm.slow_from, r1, ":box=1:boxcolor=0xB71C1C@0.8:boxborderw=6")
    icons = []                                   # (x式, y式, 角度式) … 合成のところで馬アイコンを重ねる
    for i in range(n):
        pts = [big_point(start_s + D[i][k], LAT[i][k]) for k in range(len(taus))]
        xs = [(vt(tau), (p[0] - c[1]) * ZOOM) for tau, p, c in zip(taus, pts, cam)]
        ys = [(vt(tau), (p[1] - c[2]) * ZOOM) for tau, p, c in zip(taus, pts, cam)]
        xe, ye = piecewise(xs), piecewise(ys)
        # 進行方向（画面の向き、時計回りが正）。一周で角度が飛ばないようにつなげる
        angs, prev_a = [], None
        for k in range(len(taus)):
            x0, y0 = big_point(start_s + D[i][k], LAT[i][k])
            x1, y1 = big_point(start_s + D[i][k] + 1.0, LAT[i][k])
            a = math.atan2(y1 - y0, x1 - x0)
            if prev_a is not None:
                a += round((prev_a - a) / (2 * math.pi)) * 2 * math.pi
            angs.append((vt(taus[k]), a * 100))      # piecewise は小数1桁なので100倍で渡す
            prev_a = a
        icons.append((xe, ye, f"({piecewise(angs)})/100"))
        rd.dt(str(nums[i]), 18 if nums[i] < 10 else 15, NUM_COLORS[wk[i]], f"'{xe}-text_w/2'",
              f"'{ye}-text_h/2+1'", r0, r1)
        if short[i]:
            rd.dt(short[i], 16, "white", f"'{xe}-text_w/2'", f"'{ye}-38'", r0, r1, ":borderw=3:bordercolor=black@0.8")
        if n > 8:          # 多頭数では順位パネルが縦いっぱいになるので、右下の全体図は出さない
            continue
        # 全体図（右下）の小さな点
        mpts = [course_point(start_s + D[i][k], -TRACK_HALF + 4 + LAT[i][k] * 1.2) for k in range(len(taus))]
        mxe = piecewise([(vt(tau), MINI_X + p[0] * MINI_SX) for tau, p in zip(taus, mpts)])
        mye = piecewise([(vt(tau), MINI_Y + (p[1] - MINI_CROP_Y) * MINI_SY) for tau, p in zip(taus, mpts)])
        rd.dt("●", 13, COLORS[wk[i]], f"'{mxe}-text_w/2'", f"'{mye}-text_h/2'", r0, r1, DOT_BORDER[wk[i]])
    if n <= 8:
        rd.dt("コース全体", 18, "0xBDBDBD", str(MINI_X + 6), str(MINI_Y - 26), r0, r1)

    lead = [max(D[i][k] for i in range(n)) for k in range(len(taus))]
    for rem in range(dist - 200, -1, -200):
        a0 = next((vt(tau) for tau, d in zip(taus, lead) if dist - d <= rem + 200), None)
        a1 = min(next((vt(tau) for tau, d in zip(taus, lead) if dist - d <= rem), vt(win_t)), vt(win_t) - 0.05)
        if a0 is not None and a1 > a0:
            rd.dt(f"残り {rem + 200}m", 34, "white", f"{VIEW_W // 2}-text_w/2", "24", max(a0, r0 + 1.4), a1 - 0.04,
                  ":box=1:boxcolor=black@0.55:boxborderw=10")
    w = min(range(n), key=lambda i: finish[i])
    rd.dt(f"1着　{names[w]}", 52, "white", f"{VIEW_W // 2}-text_w/2", str(H - 190), vt(win_t) + 0.6, r1,
          ":box=1:boxcolor=0xC62828@0.92:boxborderw=16")

    # ---- 実況（テロップと読み上げ）。前の実況と重なるときは後ろにずらし、遅れすぎる実況は省く
    voice, telops, cur = [], [], r0 - 0.5
    lines = commentary(names, D, LAT, taus, finish, dist)
    for j, (tau, text, speech) in enumerate(lines):
        ev = vt(min(tau, win_t - 2.2) if j == len(lines) - 2 else tau)
        clip = tts(speech) if tts else None
        dur = clip[1] if clip else max(1.8, len(text) * 0.13)
        start = max(ev, cur + 0.1)
        if start - ev > 2.5 and j < len(lines) - 2:
            continue
        if clip:
            voice.append((start, clip[0]))
        telops.append([start, start + dur + 0.6, text])
        cur = start + dur
    for j, (a, b, text) in enumerate(telops):
        if j + 1 < len(telops):
            b = min(b, telops[j + 1][0] - 0.02)
        # 長い実況は文字を小さくして、画面（コース部分の幅）からはみ出さないようにする
        units = sum(1.0 if ord(ch) > 255 else 0.6 for ch in text)
        size = max(22, min(34, int((VIEW_W - 60) / max(units, 1))))
        rd.dt(text, size, "white", f"{VIEW_W // 2}-text_w/2", str(H - 86 + (34 - size) // 2), a, min(b, r1),
              ":box=1:boxcolor=black@0.7:boxborderw=14")

    # ---- 右側の順位パネル（順位が変わると行が入れ替わる）
    p0 = g0
    rd.dt("現在の順位", 26, "0xFFEB3B", str(PANEL_X + 22), "84", p0, r1)
    rd.dt("先頭との差", 18, "0xBDBDBD", "w-text_w-14", "92", p0, r1)
    rh = 54 if n <= 8 else min(54, (H - 150) // n)     # 1行の高さ（18頭なら31px）
    fs = 1.0 if n <= 8 else 0.62                       # 文字の大きさの倍率
    row_y = lambda r: 138 + r * rh   # noqa: E731
    seg_ts = [g0] + [r0 + k * 0.5 for k in range(int((r1 - r0) / 0.5) + 1)]
    ranks_at = []
    for t0 in seg_ts:
        tau = tm.tau(t0) if t0 > r0 else 0.0
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
                ykeys.append((ranks_at[k + 1][0] - 0.3, y))   # 入れ替わりは0.3秒で動かす
        ye = piecewise(sorted(ykeys))
        rd.dt("●", int(28 * fs) + (4 if n > 8 else 0), COLORS[wk[i]], str(PANEL_X + 14), f"'{ye}'", p0, r1,
              DOT_BORDER[wk[i]])
        nx = PANEL_X + (27 if n <= 8 else (21 if nums[i] < 10 else 17))
        rd.dt(str(nums[i]), int(15 * fs) + (2 if n > 8 else 0), NUM_COLORS[wk[i]], str(nx),
              f"'{ye}+{7 if n <= 8 else 5}'", p0, r1)
        rd.dt(names[i], int(21 * fs) + (5 if n > 8 else 0), "white", str(PANEL_X + 46), f"'{ye}+{2 if n <= 8 else 1}'",
              p0, r1)
    for k, (t0, order, ds) in enumerate(ranks_at):
        t1 = ranks_at[k + 1][0] if k + 1 < len(ranks_at) else r1
        lead_d = max(ds)
        for r, i in enumerate(order):
            label = "先頭" if r == 0 else margin_label(lead_d - ds[i])
            if lead_d < 5:
                label = "－"
            if all(d >= dist - 0.01 for d in ds) and r > 0:
                label = margin_label((finish[i] - finish[order[0]]) * dist / finish[order[0]])
            rd.dt(label, 19 if n <= 8 else 16, "0xFFF59D", "w-text_w-14", str(row_y(r) + (5 if n <= 8 else 3)),
                  t0, t1 - 0.001)

    # ---- 結果ページ
    t = race_end
    for pg in after:
        t1 = rd.page(pg, t)
        page_windows.append((t, t1))
        t = t1
    total = t

    # ---- 合成
    pages_on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in page_windows) or "0"
    icon_inputs, horses_graph, cur = [], "", "cam0"
    for i, (xe, ye, ang) in enumerate(icons):
        p = work / f"horse_{i}.png"
        make_horse_icon(p, wk[i], i)
        icon_inputs += ["-i", str(p)]
        nxt = "cam" if i == n - 1 else f"cam{i + 1}"
        horses_graph += (f"[{2 + i}:v]format=rgba,loop=loop=-1:size=1:start=0,setpts=N/{FPS}/TB,"
                         f"rotate=a='{ang}':c=none:ow={ICON}:oh={ICON}[h{i}];"
                         f"[{cur}][h{i}]overlay=x='{xe}-{ICON // 2}':y='{ye}-{ICON // 2}':"
                         f"enable='between(t,{r0:.3f},{r1:.3f})'[{nxt}];")
        cur = nxt
    gw = vt(win_t)
    graph = (
        f"[0:v]format=yuv420p,loop=loop=-1:size=1:start=0,setpts=N/{FPS}/TB,"
        f"crop=w={W / ZOOM:.0f}:h={H / ZOOM:.0f}:x='{cam_x}':y='{cam_y}',scale={W}:{H}[cam0];" + horses_graph +
        f"[1:v]format=yuv420p,loop=loop=-1:size=1:start=0,setpts=N/{FPS}/TB"
        # 多頭数では右下の全体図を作らない（使わない出力を捨てると、もう一方の出力の分が際限なくメモリに溜まる）
        + (f",split=2[ov][ovm];[ovm]crop={PANEL_X}:{MINI_CROP_H}:0:{MINI_CROP_Y},scale={MINI_W}:{MINI_H}[mini];"
           if n <= 8 else "[ov];") +
        f"color=c=0x0E1812:s={W - PANEL_X}x{H}:r={FPS}:d={total:.2f}[pnl];"
        f"color=c={BG_PAGE}:s={W}x{H}:r={FPS}:d={total:.2f}[pg];"
        f"[cam][pnl]overlay=x={PANEL_X}:y=0[c1];"
        + (f"[c1][mini]overlay=x={MINI_X}:y={MINI_Y}[c2];" if n <= 8 else "[c1]null[c2];") +
        f"[c2][ov]overlay=enable='between(t,{g0:.3f},{g1:.3f})'[c3a];"
        f"color=c=white:s={W}x{H}:r={FPS}:d={total:.2f},format=yuva420p,colorchannelmixer=aa=0.6[fl];"
        f"[c3a][fl]overlay=enable='between(t,{gw:.3f},{gw + 0.16:.3f})'[c3];"
        f"[c3][pg]overlay=enable='{pages_on}'[base];[base]" + ",".join(rd.chain) + "[v]")
    script = work / "filter.txt"
    script.write_text(graph, encoding="utf-8")
    # 大きなコース図は1回だけ読み込み、loop フィルターでメモリ上の1枚を繰り返す（毎フレームのPNG展開を避ける）
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(big), "-i",
                    str(overview), *icon_inputs, "-filter_complex_script", str(script), "-map", "[v]", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{total:.2f}", str(out)], check=True)
    marks = {"gate": gate_t, "race_start": race_start, "goal": vt(win_t), "race_end": race_end, "total": total,
             "pages": page_windows, "before_end": gate_t, "voice": voice,
             "straight": vt(lead_at(STRAIGHT_M)), "slow": tm.slow_from}
    for label, sec in (frames or {}).items():
        if isinstance(sec, tuple):            # ("race_end", 3.0) = レース終了の3秒後
            sec = marks[sec[0]] + sec[1]
        elif isinstance(sec, str):
            sec = marks[sec]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{sec:.2f}", "-i", str(out),
                        "-vframes", "1", "-q:v", "2", str(out.parent / f"frame_{label}.jpg")], check=True)
    return marks


set_course("東京")
