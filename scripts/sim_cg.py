"""3D CG のシミュレーション動画の「台本」を作る。

入力: sim/<prefix>_cg_input.json（sim_real_race.py --cg-export が書く）
出力: <出力先>/spec.json     … Blender（scripts/cg/race3d.py）に渡す、各フレームの馬の位置とカメラ
      <出力先>/plan.json     … 合成（scripts/cg/compose.py）用。テロップ・ナレーションの時刻
      <出力先>/voice/*.mp3   … ナレーションと実況の音声

使い方: python scripts/sim_cg.py <prefix> <出力先ディレクトリ>

構成（文字だけの画面は使わない。説明はナレーションと、CG の上の小さなテロップで行う）:
  オープニング（コース全景→ゲート）→ レース（中継風のカメラワーク）→ ゴール前リプレイ（スロー）
  → 着順 → 1万回の勝率 → 勝率上位3頭の紹介 → エンディング
"""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sim_render as sr          # noqa: E402
import sim_dream_race as sd      # noqa: E402
import race_names as rn           # noqa: E402

FPS = 24
SHORT, SP_HI, SP_LO = False, 1.6, 1.0
SIM = Path(__file__).resolve().parent.parent / "sim"
SILKS = [(0.75, 0.08, 0.08), (0.08, 0.16, 0.55), (0.95, 0.75, 0.05), (0.05, 0.4, 0.18), (0.92, 0.92, 0.92),
         (0.45, 0.08, 0.45), (0.95, 0.4, 0.05), (0.05, 0.05, 0.05), (0.1, 0.55, 0.75), (0.85, 0.45, 0.6),
         (0.35, 0.2, 0.08), (0.6, 0.75, 0.1)]
CAP = [(0.95, 0.95, 0.95), (0.03, 0.03, 0.03), (0.85, 0.06, 0.06), (0.05, 0.25, 0.85), (0.98, 0.82, 0.05),
       (0.05, 0.55, 0.15), (1.0, 0.45, 0.05), (1.0, 0.55, 0.65)]


def ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def lerp(a, b, u):
    return [x + (y - x) * u for x, y in zip(a, b)]


def speech(text: str) -> str:
    """読み上げ用: かっこ書きを外す"""
    text = re.sub(r"（[^）]*）", "", text)
    text = re.sub(r"(\d)m", r"\1メートル", text)          # 1600m → 1600メートル
    return text.replace("%", "パーセント")


def formation3d(finish: list[float], styles: list[float], dist: int, taus: list[float], track_w: float):
    """3D 用の隊列。実際のレースに近い動きにする（2D の sim_render.formation より詰まった馬群）。

    縦: 道中は脚質の順に、前の馬から 1/3〜1馬身ずつ後ろ（馬群の長さは最大 約14馬身）。逃げ馬は少し離す。
        スタートは止まった状態から加速し、残り3〜4割から着順どおりの差に移っていく。
    横: 内ラチ沿いに 1.4m 間隔の「列」で並ぶ。列は数か所（隊列が決まるころ・3〜4コーナー・直線入口・ゴール前）
        でだけ決め直し、その間はゆっくり移る（すぐ横に動かないので、ふらつかない）。直線では差し・追込が外へ。
    """
    import random
    n = len(finish)
    win_t = min(finish)
    acc_t = 1.6                                         # 加速の時定数[秒]
    base = lambda t: t - acc_t * (1 - math.exp(-t / acc_t))      # noqa: E731
    v = dist / base(win_t)
    gate_w = max(min(3.6, (track_w - 4.0) / max(n - 1, 1)), 1.2)
    gate_lat = [1.4 + i * gate_w for i in range(n)]
    rng = random.Random(11)
    order = sorted(range(n), key=lambda i: styles[i] + 0.04 * i / max(n - 1, 1))
    gap, acc = [0.0] * n, 0.0
    for r, i in enumerate(order[1:], 1):
        g = 0.8 + rng.random() * 1.6
        if r == 1:
            g += 2.0 + max(styles[i] - styles[order[0]], 0.0) * 6
        acc += g
        gap[i] = acc
    if acc > 34.0:
        gap = [g * 34.0 / acc for g in gap]

    def D_at(tau):
        u = tau / win_t
        out = []
        for i in range(n):
            if tau >= win_t:
                d = tau - finish[i]
                out.append(dist + v * d * (0.8 if d > 0 else 1.0))
                continue
            ramp = _smooth(u / 0.14)
            u0 = 0.64 + 0.14 * (1 - styles[i])
            late = _smooth((u - u0) / (1 - u0))
            out.append(max(v * base(tau) - gap[i] * ramp * (1 - late) - (finish[i] - win_t) * v * late, 0.0))
        return out

    D = [[0.0] * len(taus) for _ in range(n)]
    for k, tau in enumerate(taus):
        for i, d in enumerate(D_at(tau)):
            D[i][k] = d

    def lead_time(m):
        lo, hi = 0.0, win_t
        for _ in range(40):
            mid = (lo + hi) / 2
            if max(D_at(mid)) < m:
                lo = mid
            else:
                hi = mid
        return hi

    W, R0 = gate_w, 1.4                               # 列の間隔はゲートの間隔と同じ（約1.2〜1.4m）
    ST = sr.STRAIGHT_M
    t1 = lead_time(min(320, dist * 0.2))
    t3 = lead_time(dist - ST + 30)
    pre = [int(round(2 * i / max(n - 1, 1))) for i in range(n)]          # 外枠の馬はしばらく外め
    closer = [(2 + int(styles[i] * 4)) if styles[i] > 0.45 else 0 for i in range(n)]
    # 3秒ごとに列を決め直す。動くのは隣の列までで、前後3m以内（前後の区間を通して）に馬がいない列だけ
    kt = [0.0] + [3.0 * q for q in range(1, int((win_t + 6) / 3.0) + 1)]
    cur = [max(0, round((gate_lat[i] - R0) / W)) for i in range(n)]
    lanes_at = [None]
    for q in range(1, len(kt)):
        tk = kt[q]
        win = [kt[q - 1] + (min(kt[q + 1] if q + 1 < len(kt) else tk + 3, win_t + 6) - kt[q - 1]) * r / 8
               for r in range(9)]
        dw = [D_at(t) for t in win]
        near = lambda i, j: min(abs(d[i] - d[j]) for d in dw) < 3.0        # noqa: E731
        d_now = D_at(tk)
        pref = [pre[i] if tk < t1 else (closer[i] if tk >= t3 else 0) for i in range(n)]
        new, placed = cur[:], []
        for i in sorted(range(n), key=lambda i: -d_now[i]):
            c = cur[i]
            if tk > win_t + 1:
                opts = [c]
            elif c > pref[i] and tk < t1 + 6 and c - pref[i] >= 2:
                opts = [c - 2, c - 1, c, c + 1]           # スタート直後は外枠の馬が内へ寄っていく
            elif c > pref[i]:
                opts = [c - 1, c, c + 1]
            elif c < pref[i]:
                opts = [c + 1, c, c - 1]
            else:
                opts = [c, c + 1, c - 1]
            opts = [o for o in opts if o >= 0] + list(range(c + 2, c + 16))

            def free(L):
                if L != c and any(cur[j] == L and near(i, j) for j in range(n) if j != i and j not in placed):
                    return False                      # まだ決めていない（後ろの）馬が今いる列には入らない
                for j in placed:
                    if near(i, j) and (new[j] == L or (cur[j] == L and new[j] == c) or
                                       (min(cur[j], new[j]) < max(c, L) and max(cur[j], new[j]) > min(c, L)
                                        and (cur[j] - c) * (new[j] - L) < 0)):
                        return False
                return True

            new[i] = next((o for o in opts if free(o)), None)
            if new[i] is None:
                new[i] = c
            placed.append(i)
        cur = new
        lanes_at.append(cur[:])
    LAT = [[0.0] * len(taus) for _ in range(n)]
    for k, tau in enumerate(taus):
        q = min(int(tau // 3.0), len(kt) - 2)
        w = _smooth((tau - kt[q]) / (kt[q + 1] - kt[q]))
        for i in range(n):
            a_ = gate_lat[i] if q == 0 else R0 + W * lanes_at[q][i]
            b_ = R0 + W * lanes_at[q + 1][i]
            LAT[i][k] = min(a_ + (b_ - a_) * w, track_w - 1.0)
    return D, LAT


def _smooth(v: float) -> float:
    v = min(max(v, 0.0), 1.0)
    return v * v * (3 - 2 * v)


def main() -> None:
    prefix, out = sys.argv[1], Path(sys.argv[2])
    global SHORT, SP_HI, SP_LO
    SHORT = "--short" in sys.argv                     # 縦長の宣伝ショート（レースだけ・本編へ誘導）
    SP_HI, SP_LO = (2.4, 1.15) if SHORT else (1.6, 1.0)
    out.mkdir(parents=True, exist_ok=True)
    inp = json.loads((SIM / f"{prefix}_cg_input.json").read_text(encoding="utf-8"))
    race = inp["race"]
    dist, venue = race["distance"], race["venue"]
    surf = race.get("surface", "芝")
    surf_word = "ダート" if surf == "ダ" else "芝"
    sr.set_course(sr.course_key(venue, dist, surf))
    LAP, ST, TW = sr.LAP_M, sr.STRAIGHT_M, sr.TRACK_W_M
    start_s = ST - (dist - LAP)
    hs = sorted(inp["horses"], key=lambda h: h["num"])
    names = [h["name"] for h in hs]
    n = len(hs)
    fin_map = dict(inp["shown"])
    finish = [fin_map[m] for m in names]
    styles = [h["style"] for h in hs]
    win_t, t_end = min(finish), max(finish)

    # ---- 隊列（0.05秒ごと）。補間して任意の時刻の位置を出す
    step = 0.05
    taus = [k * step for k in range(int((t_end + 12) / step) + 1)]
    D, LAT = formation3d(finish, styles, dist, taus, TW)

    def at(tau: float):
        x = min(max(tau / step, 0.0), len(taus) - 1.001)
        k = int(x)
        u = x - k
        d = [D[i][k] + (D[i][k + 1] - D[i][k]) * u for i in range(n)]
        la = [LAT[i][k] + (LAT[i][k + 1] - LAT[i][k]) * u for i in range(n)]
        return d, la

    def P(s, off=0.0, z=0.0):
        """race3d.py と同じコース座標（右回りは x を反転）"""
        R = sr.CURVE_R_M
        s %= LAP
        rr = R + off
        if s < ST:
            x, y = -ST / 2 + s, -rr
        else:
            s -= ST
            if s < math.pi * R:
                th = -math.pi / 2 + s / R
                x, y = ST / 2 + rr * math.cos(th), rr * math.sin(th)
            else:
                s -= math.pi * R
                if s < ST:
                    x, y = ST / 2 - s, rr
                else:
                    s -= ST
                    th = math.pi / 2 + s / R
                    x, y = -ST / 2 + rr * math.cos(th), rr * math.sin(th)
        return [-x if sr.RIGHT else x, y, z]

    frames, plan = [], {"fps": FPS, "segments": [], "overlays": [], "voice": [], "order_strip": [],
                        "remaining": [], "horses": hs, "race": race}

    def add(tau, cam, run=True, d_override=None):
        d, la = at(tau)
        if d_override is not None:
            d = d_override
        frames.append({"tau": round(tau, 3), "s": [round(start_s + x, 3) for x in d], "lat": [round(x, 3) for x in la],
                       "dist": [round(x, 3) for x in d], "run": [1 if run else 0] * n,
                       "cam": {"loc": [round(v, 3) for v in cam[0]], "look": [round(v, 3) for v in cam[1]],
                               "lens": round(cam[2], 2)}})

    def now() -> float:
        return len(frames) / FPS

    tts = sd.make_tts(out / "voice")

    def say(text: str, t0: float, kind: str = "narr"):
        clip = tts(speech(text))
        if clip:
            plan["voice"].append({"t": round(t0, 3), "file": Path(clip[0]).name, "dur": round(clip[1], 3),
                                  "kind": kind, "text": text})
            return clip[1]
        return 0.0

    def segment(name, t0, t1, **kw):
        plan["segments"].append({"name": name, "t0": round(t0, 3), "t1": round(t1, 3), **kw})

    by_rank = sorted(hs, key=lambda h: h["rank"])
    fav = by_rank[0]

    if not SHORT:
        # ---- オープニング: コース全景 → スタート地点
        t0 = now()
        narr = (f"{rn.spoken(race['race_name'])}。{venue}の{surf_word}{dist}メートルを、出走{n}頭の過去の成績から"
                f"1万回シミュレーションしました。まずは、その中の代表的な1回です。")
        dur = say(narr, t0 + 0.6)
        intro_len = max(9.0, dur + 1.6)
        gx = P(start_s, TW / 2)
        for f in range(int(intro_len * FPS)):
            u = f / (intro_len * FPS)
            a = ease(u / 0.55)
            b = ease((u - 0.55) / 0.45)
            cam_a = [0.0, -(sr.CURVE_R_M + TW + 380), 170.0]
            look_a = [0.0, 0.0, 0.0]
            cam_b = P(start_s - 120, TW + 70, 55)
            cam_c = P(start_s + 40, TW + 22, 7)
            look_c = P(start_s, TW / 2, 1.2)
            if u < 0.55:
                cam = lerp(cam_a, cam_b, a)
                look = lerp(look_a, gx, a)
                lens = 24 + 8 * a
            else:
                cam = lerp(cam_b, cam_c, b)
                look = lerp(gx, look_c, b)
                lens = 32 + 8 * b
            add(0.0, (cam, look, lens), run=False)
        segment("intro", t0, now())
        plan["overlays"].append({"type": "title", "t0": round(t0 + 0.8, 3), "t1": round(now() - 0.4, 3)})

    else:
        # ---- ショート: ゲート前から（つかみ）
        t0 = now()
        dur = say(f"{rn.spoken(race['race_name'])}を1万回シミュレーション。その代表的な1回がこちら！", t0 + 0.2)
        hook_len = max(3.5, dur + 0.5)
        for f in range(int(hook_len * FPS)):
            u = ease(f / (hook_len * FPS))
            cam = lerp(P(start_s + 60, TW + 14, 9), P(start_s + 40, TW + 6, 4), u)
            add(0.0, (cam, P(start_s, TW / 2, 1.2), 34 + 10 * u), run=False)
        segment("intro", t0, now())
        plan["overlays"].append({"type": "short_title", "t0": round(t0, 3), "t1": round(now(), 3)})

    # ---- レース（再生速度: 道中1.6倍 → 直線の後半から等倍）
    race_t0 = now()
    tau = 0.0
    lead_len = lambda d: max(d)               # noqa: E731
    shot_log = []
    start_cam = P(start_s + 70, TW + 8, 3.2)
    while tau < win_t + 3.2:
        d, la = at(tau)
        lead = lead_len(d)
        rem = dist - lead
        sp = SP_HI if rem > 520 else SP_LO + (SP_HI - SP_LO) * ease((rem - 340) / 180)
        order = sorted(range(n), key=lambda i: -d[i])
        mid = d[order[min(5, n - 1)]]
        sc = start_s + (lead + mid) / 2
        lead_s = start_s + lead
        s_on_lap = (start_s + lead) % LAP
        in_straight = lead >= dist - ST + 5
        if lead < 110:                                         # スタート: 斜め前から
            cam = (start_cam, P(sc, 7, 1.1), 62 + lead * 0.25)
            shot = "start"
        elif not in_straight and (s_on_lap > LAP - math.pi * sr.CURVE_R_M - 40 or lead > dist - ST - 260):
            cam = (P(sc - 20, 15, 13), P(sc + 6, 3, 0.5), 34)        # 3〜4コーナー: 斜め後ろの上から
            shot = "heli"
        elif not in_straight:
            cam = (P(sc + 3, 19, 6.5), P(sc, 3, 1.0), 40)            # 向こう正面: 外から並走（カメラ車）
            shot = "side"
        elif rem > 230:
            ll = sum(la[i] for i in order[:4]) / 4
            cam = (P(lead_s + 30, ll + 1.5, 3.0), P(lead_s - 6, ll, 1.3), 58)   # 直線: 先頭の前から（移動車）
            shot = "headon"
        else:
            cam = (P(lead_s - 4, TW + 26, 9), P(lead_s - 5, 7, 1.1), 62)   # ゴール前: スタンド側から並走
            shot = "finish"
        if not shot_log or shot_log[-1][0] != shot:
            shot_log.append((shot, now()))
        add(tau, cam)
        frames[-1]["shot"] = shot
        frames[-1]["tags"] = 1
        tau += sp / FPS
    race_t1 = now()
    # 同じショットの中ではカメラをならす（先頭が入れ替わるたびにカメラが揺れないように）
    k0 = int(round(race_t0 * FPS))
    k = k0
    while k < len(frames):
        k1 = k
        while k1 + 1 < len(frames) and frames[k1 + 1].get("shot") == frames[k].get("shot"):
            k1 += 1
        if frames[k].get("shot") in ("side", "heli", "finish", "headon"):
            src = [dict(frames[q]["cam"]) for q in range(k, k1 + 1)]
            h = 18
            for q in range(len(src)):
                lo, hi = max(0, q - h), min(len(src), q + h + 1)
                for key in ("loc", "look"):
                    frames[k + q]["cam"][key] = [round(sum(src[r][key][a] for r in range(lo, hi)) / (hi - lo), 3)
                                                 for a in range(3)]
        k = k1 + 1
    segment("race", race_t0, race_t1, shots=[(s, round(t, 3)) for s, t in shot_log])
    plan["overlays"].append({"type": "race_tag", "t0": round(race_t0, 3), "t1": round(race_t1, 3)})

    # 実況（レース内の時刻 → 動画の時刻。前の実況と重なるときは後ろにずらし、遅れすぎたら省く）
    vt_of = {}
    for k, fr in enumerate(frames):
        if k / FPS >= race_t0:
            vt_of.setdefault(round(fr["tau"], 1), k / FPS)

    def vt(tau_):
        best = min(vt_of, key=lambda x: abs(x - tau_))
        return vt_of[best]

    ktaus = [i * 0.25 for i in range(int((t_end + 3) / 0.25))]
    kD, kL = [[0.0] * len(ktaus) for _ in range(n)], [[0.0] * len(ktaus) for _ in range(n)]
    for j, tt in enumerate(ktaus):
        d, la = at(tt)
        for i in range(n):
            kD[i][j], kL[i][j] = d[i], la[i]
    lines = sr.commentary(names, kD, kL, ktaus, finish, dist)
    cur = race_t0 + 0.1
    for j, (tt, _scr, spoken) in enumerate(lines):
        want = vt(tt)
        last = j >= len(lines) - 2
        if want < cur - 0.1 and not last and cur - want > 2.5:
            continue
        start = max(want, cur)
        d_ = say(spoken, start, "call")
        cur = start + d_ + 0.25

    # 順位の帯（1秒ごと）と残り距離
    for k in range(0, len(frames) - int(race_t0 * FPS), FPS):
        fr = frames[int(race_t0 * FPS) + k]
        order = sorted(range(n), key=lambda i: -fr["dist"][i])
        plan["order_strip"].append({"t0": round(race_t0 + k / FPS, 3), "t1": round(race_t0 + (k + FPS) / FPS, 3),
                                    "order": [hs[i]["num"] for i in order]})
    for k in range(int(race_t0 * FPS), int(race_t1 * FPS)):
        lead = max(frames[k]["dist"])
        rem = dist - lead
        mark = (math.ceil(rem / 200) * 200) if rem > 0 else 0
        t = k / FPS
        if mark > 0 and mark < dist and (not plan["remaining"] or plan["remaining"][-1]["m"] != mark):
            if plan["remaining"]:
                plan["remaining"][-1]["t1"] = round(t, 3)
            plan["remaining"].append({"m": int(mark), "t0": round(t, 3), "t1": round(t, 3)})
        elif mark == 0 and plan["remaining"] and plan["remaining"][-1]["m"] != 0:
            plan["remaining"][-1]["t1"] = round(t, 3)
            plan["remaining"].append({"m": 0, "t0": round(t, 3), "t1": round(t, 3)})
    if plan["remaining"]:
        plan["remaining"][-1]["t1"] = round(race_t1, 3)

    if not SHORT:
        # ---- ゴール前のリプレイ（スロー）: ゴール板の真横から
        t0 = now()
        fs = start_s + dist
        a_tau, b_tau = win_t - 2.6, win_t + 0.7
        tau = a_tau
        while tau < b_tau:
            u = (tau - a_tau) / (b_tau - a_tau)
            dd, _ = at(tau)
            ls = start_s + max(dd)
            cam = (P(ls - 1, TW + 10, 1.5), P(ls - 3, 6, 1.2), 42)
            add(tau, cam)
            frames[-1]["tags"] = 1
            tau += 0.3 / FPS
        segment("replay", t0, now())
        plan["overlays"].append({"type": "replay", "t0": round(t0, 3), "t1": round(now(), 3)})

        # ---- 着順（ゴール後の流し）
        top = sorted(range(n), key=lambda i: finish[i])[:3]
        t0 = now()
        res_txt = "、".join(f"{k + 1}着は{hs[i]['num']}番{names[i]}" for k, i in enumerate(top))
        dur = say(f"ゴール。{res_txt}。", t0 + 0.4)
        res_len = max(6.5, dur + 1.2)
        tau = win_t + 1.0
        for f in range(int(res_len * FPS)):
            d, la = at(tau)
            c = start_s + sum(sorted(d, reverse=True)[:3]) / 3
            u = f / (res_len * FPS)
            cam = (P(c - 30 + 10 * u, TW + 40, 22 - 6 * u), P(c, 8, 0.8), 40)
            add(tau, cam)
            tau += 1.0 / FPS
        segment("result", t0, now())
        plan["overlays"].append({"type": "result", "t0": round(t0 + 0.6, 3), "t1": round(now() - 0.2, 3),
                                 "top": [{"num": hs[i]["num"], "waku": hs[i]["waku"], "name": names[i],
                                          "time": sd.fmt_time(finish[i])} for i in top]})

        # ---- 1万回の勝率（スタンド前の直線を上空から流しながら）
        t0 = now()
        p1 = fav["win"]
        narr = (f"では、1万回走らせた結果です。勝率1位は{fav['num']}番{fav['name']}で{p1 * 100:.1f}%。"
                f"2位は{by_rank[1]['name']}、{by_rank[1]['win'] * 100:.1f}%。3位は{by_rank[2]['name']}、"
                f"{by_rank[2]['win'] * 100:.1f}%。1位の馬でも勝つのは{round(1 / max(p1, 1e-9))}回に1回ほどです。")
        dur = say(narr, t0 + 0.4)
        rate_len = max(10.0, dur + 1.4)
        tau_hold = win_t + 1.0 + res_len
        for f in range(int(rate_len * FPS)):
            u = f / (rate_len * FPS)
            s = 20 + (ST - 120) * u
            cam = (P(s, -35, 16), P(s + 70, TW + 45, 9), 30)          # 内馬場からスタンドを見る
            add(tau_hold, cam, run=False)
        segment("rates", t0, now())
        plan["overlays"].append({"type": "rates", "t0": round(t0 + 0.4, 3), "t1": round(now() - 0.2, 3),
                                 "rows": [{"num": h["num"], "waku": h["waku"], "name": h["name"],
                                           "win": round(h["win"], 4)} for h in by_rank[:5]]})

        # ---- 勝率上位3頭の紹介（道中を走る姿のアップ）
        for k, h in enumerate(by_rank[:3]):
            i = names.index(h["name"])
            t0 = now()
            pts = [s[1] for s in h["strong"][:2]]
            body = "。".join(pts) if pts else "目立った弱点がなく、バランスのとれた成績"
            narr = f"勝率{k + 1}位、{h['name']}。{body}。"
            dur = say(narr, t0 + 0.3)
            flen = max(6.5, dur + 1.0)
            # カメラとその馬の間に他の馬が入らない時間帯と向き（外から／内から）を探す
            span = flen * 0.8

            def blockers(tt, sgn):
                nb = 0
                for q in range(6):
                    d, la = at(tt + span * q / 5)
                    for j in range(n):
                        if j != i and abs(d[j] - d[i]) < 7.5 and 0 < (la[j] - la[i]) * sgn < 12:
                            nb += 1
                return nb

            cands = [(blockers(8.0 + 0.5 * q, sgn), -abs(8.0 + 0.5 * q - (20 + 8 * k)), 8.0 + 0.5 * q, sgn)
                     for q in range(int((win_t - 16) / 0.5)) for sgn in (1, -1)]
            nb, _, tau, sgn = min(cands, key=lambda c: (c[0], -c[1]))
            print(f"  紹介 {h['name']}: 時刻 {tau:.1f}秒 {'外' if sgn > 0 else '内'}から（間の馬 {nb}）")
            for f in range(int(flen * FPS)):
                d, la = at(tau)
                s_h = start_s + d[i]
                u = f / (flen * FPS)
                side = la[i] + 9.0 * sgn
                cam = (P(s_h + 5.0 - 2.5 * u, side, 2.3), P(s_h + 0.4, la[i], 1.35), 46)
                add(tau, cam)
                tau += 0.8 / FPS
            segment(f"focus{k + 1}", t0, now())
            plan["overlays"].append({"type": "focus", "t0": round(t0 + 0.3, 3), "t1": round(now() - 0.2, 3),
                                     "rank": k + 1, "num": h["num"], "waku": h["waku"], "name": h["name"],
                                     "jockey": h["jockey"], "win": round(h["win"], 4), "style": h["style_name"],
                                     "record": f"JRA{surf_word} {h['starts']}戦{h['wins']}勝",
                                     "points": [f"{s[0]}：{s[1]}" for s in h["strong"][:2]]})

        # ---- エンディング（スタンドとゴール板を上空へ引いていく）
        t0 = now()
        dur = say("データはあくまで目安です。あなたの予想は、コメントで教えてください。", t0 + 0.3)
        end_len = max(6.0, dur + 1.5)
        for f in range(int(end_len * FPS)):
            u = ease(f / (end_len * FPS))
            cam = lerp(P(ST - 10, TW + 30, 6), P(ST - 140, TW + 260, 140), u)
            add(tau_hold, (cam, P(ST, TW / 2, 2 + 30 * u), 35 - 10 * u), run=False)
        segment("outro", t0, now())
        plan["overlays"].append({"type": "outro", "t0": round(t0 + 0.4, 3), "t1": round(now(), 3)})
    else:
        # ---- ショート: 締め（本編への誘導）
        t0 = now()
        dur = say("1万回走らせた勝率1位の馬は？答えは本編でチェック！", t0 + 0.3)
        end_len = max(4.5, dur + 1.0)
        tau = win_t + 1.0
        for f in range(int(end_len * FPS)):
            d, la = at(tau)
            c = start_s + sum(sorted(d, reverse=True)[:3]) / 3
            cam = (P(c - 18, TW + 16, 10), P(c, 6, 1.0), 40)
            add(tau, cam)
            tau += 0.7 / FPS
        segment("result", t0, now())
        plan["overlays"].append({"type": "end_card", "t0": round(t0 + 0.2, 3), "t1": round(now(), 3)})

    plan["total"] = round(now(), 3)
    plan["res"] = [1080, 1920] if SHORT else [1280, 720]
    plan["n_frames"] = len(frames)

    spec = {
        "course": {"lap": LAP, "straight": ST, "right": sr.RIGHT, "track_w": TW, "dist": dist, "start_s": start_s,
                   "name": venue, "surface": surf},
        "horses": [{"num": h["num"], "waku": h["waku"], "name": h["name"], "coat": (h["num"] * 5 + 3) % 6,
                    "silk": SILKS[(h["num"] * 7) % len(SILKS)], "cap": CAP[h["waku"] - 1]} for h in hs],
        "horse_model": "data/cg/horse_quaternius.blend",
        "horse_scale": 0.5, "model_yaw": math.pi / 2, "gallop_frames": 18, "gallop_stride": 7.2,
        "rider_bone": "Torso2", "rider_pos": [0.0, -0.25, 3.0],
        "label_font": "data/cg/BIZUDPGothic-Bold.ttf",
        "fps": FPS, "res": [1080, 1920] if SHORT else [1280, 720], "samples": 12,
        "sensor_fit": "HORIZONTAL" if SHORT else "AUTO", "lens_mult": 1.7 if SHORT else 1.0,
        "tag_scale": 1.6 if SHORT else 1.0,
        "frames": frames,
    }
    (out / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"フレーム数 {len(frames)}（{len(frames) / FPS:.1f}秒）")
    for sg in plan["segments"]:
        print(f"  {sg['name']}: {sg['t0']:.1f}〜{sg['t1']:.1f}秒")
    print(f"  音声 {len(plan['voice'])}本")


if __name__ == "__main__":
    main()
