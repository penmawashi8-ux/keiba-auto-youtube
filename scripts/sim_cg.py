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

FPS = 24
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


def main() -> None:
    prefix, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    inp = json.loads((SIM / f"{prefix}_cg_input.json").read_text(encoding="utf-8"))
    race = inp["race"]
    dist, venue = race["distance"], race["venue"]
    sr.set_course(sr.course_key(venue, dist))
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
    D, LAT = sr.formation(finish, styles, dist, taus)

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

    # ---- オープニング: コース全景 → スタート地点
    t0 = now()
    narr = (f"{race['race_name']}。{venue}の芝{dist}メートルを、出走{n}頭の過去の成績から"
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
        sp = 1.6 if rem > 520 else 1.0 + 0.6 * ease((rem - 340) / 180)
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
            cam = (P(sc - 55, TW + 46, 42), P(sc + 18, 8, 0), 34)     # 3〜4コーナー: 上空から
            shot = "heli"
        elif not in_straight:
            cam = (P(sc + 6, TW + 34, 13), P(sc, 9, 1.0), 52)         # 向こう正面: 外から並走
            shot = "side"
        elif rem > 230:
            ll = sum(la[i] for i in order[:4]) / 4
            cam = (P(lead_s + 42, ll, 3.4), P(lead_s - 5, ll, 1.3), 70)   # 直線: 先頭の前から（移動車）
            shot = "headon"
        else:
            cam = (P(lead_s - 4, TW + 26, 9), P(lead_s - 5, 7, 1.1), 62)   # ゴール前: スタンド側から並走
            shot = "finish"
        if not shot_log or shot_log[-1][0] != shot:
            shot_log.append((shot, now()))
        add(tau, cam)
        tau += sp / FPS
    race_t1 = now()
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
        tau = 14.0 + k * 9.0
        for f in range(int(flen * FPS)):
            d, la = at(tau)
            s_h = start_s + d[i]
            u = f / (flen * FPS)
            side = la[i] + 9.5
            cam = (P(s_h + 5.0 - 2.5 * u, side, 2.0), P(s_h + 0.4, la[i], 1.35), 46)
            add(tau, cam)
            tau += 0.8 / FPS
        segment(f"focus{k + 1}", t0, now())
        plan["overlays"].append({"type": "focus", "t0": round(t0 + 0.3, 3), "t1": round(now() - 0.2, 3),
                                 "rank": k + 1, "num": h["num"], "waku": h["waku"], "name": h["name"],
                                 "jockey": h["jockey"], "win": round(h["win"], 4), "style": h["style_name"],
                                 "record": f"JRA芝 {h['starts']}戦{h['wins']}勝",
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
    plan["total"] = round(now(), 3)
    plan["n_frames"] = len(frames)

    spec = {
        "course": {"lap": LAP, "straight": ST, "right": sr.RIGHT, "track_w": TW, "dist": dist, "start_s": start_s,
                   "name": venue},
        "horses": [{"num": h["num"], "waku": h["waku"], "name": h["name"], "coat": (h["num"] * 5 + 3) % 6,
                    "silk": SILKS[(h["num"] * 7) % len(SILKS)], "cap": CAP[h["waku"] - 1]} for h in hs],
        "horse_model": "data/cg/horse_quaternius.blend",
        "horse_scale": 0.5, "model_yaw": math.pi / 2, "gallop_frames": 18, "gallop_stride": 7.2,
        "rider_bone": "Torso2", "rider_pos": [0.0, -0.25, 3.0],
        "fps": FPS, "res": [1280, 720], "samples": 12,
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
