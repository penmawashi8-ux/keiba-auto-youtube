#!/usr/bin/env python3
"""横向き（1920×1080）予想解説動画を生成する。

縦動画（generate_video.py）との構成の違い:
  - ローワーサードバー: 画面下部210pxを暗くしてテキストを表示（TV報道スタイル）
  - セクションヘッダーカード: 【...】行を全画面中央に金色で大きく表示
  - オープニングカード: レース名・グレード・日程を表示
  - アニメーション無し（シンプル・落ち着いた演出）
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

NEWS_JSON    = "news.json"
OUTPUT_DIR   = "output"
ASSETS_DIR   = "assets"
BGM_DIR      = f"{ASSETS_DIR}/bgm"
W, H         = 1920, 1080
FPS          = 30
BAR_H        = 210          # ローワーサードバー高さ
BAR_Y        = H - BAR_H   # = 870
HDR_FS       = 72           # ヘッダーフォントサイズ
BODY_FS      = 46           # ボディフォントサイズ
HDR_DUR      = 2.5          # ヘッダーカード最低秒数
OPEN_DUR     = 3.0          # オープニングカード秒数
END_DUR      = 5.0          # エンディング秒数
MIN_DUR      = 1.5
BGM_VOL      = 0.15
LINE_HDR     = 14
LINE_BODY    = 24


# ---------------------------------------------------------------------------
def find_font() -> str | None:
    for p in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]:
        if Path(p).exists():
            return p
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    return hits[0] if hits else None


def wrap(text: str, max_chars: int) -> str:
    lines, para = [], text
    while len(para) > max_chars:
        lines.append(para[:max_chars])
        para = para[max_chars:]
    if para:
        lines.append(para)
    return "\n".join(lines)


def audio_duration(path: str) -> float:
    try:
        from mutagen.mp3 import MP3
        return MP3(path).info.length
    except Exception:
        pass
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                        path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 10.0


def parse_segments(script: str) -> list[dict]:
    """スクリプトを header / body セグメントに分割する。"""
    segs = []
    for para in script.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        lines = [l.strip() for l in para.splitlines() if l.strip()]
        if not lines:
            continue
        if lines[0].startswith("【") and "】" in lines[0]:
            segs.append({"type": "header", "text": lines[0]})
            body_lines = lines[1:]
        else:
            body_lines = lines
        for line in body_lines:
            for sent in re.split(r"[。\n]", line):
                sent = sent.strip()
                if sent:
                    segs.append({"type": "body", "text": sent})
    return segs


# ---------------------------------------------------------------------------
def fetch_images(count: int = 4) -> list[str]:
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    queries  = ["horse racing track", "horse racing", "jockey horse race", "thoroughbred racing"]
    hf_prompts = [
        "cinematic landscape photo horses racing on beautiful racecourse dramatic lighting",
        "wide angle horse racing track action shot golden hour",
    ]
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "")
    hf_tokens   = [t for t in [os.environ.get("HF_TOKEN",""), os.environ.get("HF_TOKEN_2",""), os.environ.get("HF_TOKEN_3","")] if t]
    import requests
    paths = []

    for i in range(count):
        out = f"{ASSETS_DIR}/landscape_{i}.jpg"
        # 1. Pixabay
        if pixabay_key:
            try:
                r = requests.get("https://pixabay.com/api/", params={
                    "key": pixabay_key, "q": queries[i % len(queries)],
                    "image_type": "photo", "orientation": "horizontal",
                    "min_width": 1280, "min_height": 720, "per_page": 20, "safesearch": "true",
                }, timeout=30)
                hits = r.json().get("hits", [])
                if hits:
                    import random
                    url = random.choice(hits).get("webformatURL", "")
                    if url:
                        img = requests.get(url, timeout=30)
                        if img.status_code == 200:
                            tmp = out + ".tmp"
                            Path(tmp).write_bytes(img.content)
                            res = subprocess.run(["ffmpeg", "-y", "-i", tmp, "-frames:v", "1", "-q:v", "2", out], capture_output=True)
                            Path(tmp).unlink(missing_ok=True)
                            if res.returncode == 0:
                                paths.append(out)
                                continue
            except Exception as e:
                print(f"  [警告] Pixabay失敗: {e}", file=sys.stderr)
        # 2. HuggingFace
        if hf_tokens:
            for tok in hf_tokens:
                try:
                    import requests as _r
                    r2 = _r.post(
                        "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
                        headers={"Authorization": f"Bearer {tok}"},
                        json={"inputs": hf_prompts[i % len(hf_prompts)]},
                        timeout=120,
                    )
                    if r2.status_code == 200:
                        tmp = out + ".tmp"
                        Path(tmp).write_bytes(r2.content)
                        res = subprocess.run(["ffmpeg", "-y", "-i", tmp, "-frames:v", "1", "-q:v", "2", out], capture_output=True)
                        Path(tmp).unlink(missing_ok=True)
                        if res.returncode == 0:
                            paths.append(out)
                            break
                    if r2.status_code in (402, 403):
                        break
                except Exception:
                    pass
            if Path(out).exists() and Path(out).stat().st_size > 1000:
                continue
        # 3. geqパターンフォールバック
        colors = [
            ("clip(8+148*pow(Y/H,1.6),8,156)", "clip(4*pow(Y/H,2),0,4)",  "clip(4*pow(Y/H,2),0,4)"),
            ("clip(4*pow(1-Y/H,2),0,4)",        "clip(4*pow(1-Y/H,2),0,4)", "clip(10+105*pow(1-Y/H,1.5),10,115)"),
            ("clip(8+100*pow(1-Y/H,1.4),8,108)", "clip(6+68*pow(1-Y/H,1.6),6,74)", "clip(2,0,2)"),
            ("clip(5*pow(Y/H,2),0,5)",            "clip(8+80*pow(Y/H,1.5),8,88)",  "clip(8+90*pow(1-Y/H,1.5),8,98)"),
        ]
        r_e, g_e, b_e = colors[i % len(colors)]
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=1",
            "-vf", f"geq=r='{r_e}':g='{g_e}':b='{b_e}'",
            "-frames:v", "1", "-q:v", "3", out,
        ], capture_output=True)
        if Path(out).exists():
            paths.append(out)

    return paths


# ---------------------------------------------------------------------------
def bg_chain(bg_img: str | None) -> tuple[list, str]:
    """ffmpegコマンドの入力引数とfilter_complexのベースチェーンを返す。"""
    if bg_img and Path(bg_img).exists():
        inputs = ["-loop", "1", "-i", bg_img]
        chain  = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},eq=brightness=-0.06,vignette=PI/6"
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c=#0D1B2A:s={W}x{H}:r={FPS}"]
        chain  = "[0:v]vignette=PI/4"
    return inputs, chain


def make_clip(idx: int, bg_img: str | None, seg: dict, duration: float,
              font: str | None, tmp: str, race_meta: dict) -> str:
    out  = f"{tmp}/clip_{idx:04d}.mp4"
    dur  = max(duration, 0.5)
    fp   = (font or "").replace("'", "\\'")
    inputs, chain = bg_chain(bg_img)

    stype = seg.get("type", "body")
    text  = seg.get("text", "")

    if stype == "opening":
        # オープニングカード: 全体を暗く→グレードバッジ→レース名→日程
        chain += ",geq=r='r(X,Y)*0.50':g='g(X,Y)*0.50':b='b(X,Y)*0.50'"
        if font:
            # グレードバッジ
            grade_f = f"{tmp}/grade.txt"
            Path(grade_f).write_text(race_meta.get("grade", ""), encoding="utf-8")
            chain += (f",drawtext=textfile='{grade_f.replace(chr(39), chr(92)+chr(39))}':"
                      f"fontfile='{fp}':fontsize=64:fontcolor=0xFFFFFF:"
                      f"x=(w-text_w)/2:y=80:"
                      f"box=1:boxcolor=0xCC0000@0.95:boxborderw=24")
            # レース名
            name_f = f"{tmp}/race_name.txt"
            Path(name_f).write_text(wrap(race_meta.get("race_name", ""), LINE_HDR), encoding="utf-8")
            chain += (f",drawtext=textfile='{name_f.replace(chr(39), chr(92)+chr(39))}':"
                      f"fontfile='{fp}':fontsize=96:fontcolor=0xFFD700:"
                      f"x=(w-text_w)/2:y=(h-text_h)/2-60:"
                      f"box=1:boxcolor=0x000000@0.80:boxborderw=40:"
                      f"borderw=4:bordercolor=0x000000")
            # 日程・会場
            date_str = " ".join(filter(None, [race_meta.get("date",""), race_meta.get("venue",""), race_meta.get("distance","")]))
            if date_str:
                dv_f = f"{tmp}/date_venue.txt"
                Path(dv_f).write_text(date_str, encoding="utf-8")
                chain += (f",drawtext=textfile='{dv_f.replace(chr(39), chr(92)+chr(39))}':"
                          f"fontfile='{fp}':fontsize=48:fontcolor=0xFFFFFF:"
                          f"x=(w-text_w)/2:y=(h+96)/2+40:"
                          f"borderw=3:bordercolor=0x000000")

    elif stype == "header":
        # セクションヘッダーカード: 全体を暗く→中央に金色テキスト
        chain += ",geq=r='r(X,Y)*0.45':g='g(X,Y)*0.45':b='b(X,Y)*0.45'"
        if font:
            tf = f"{tmp}/hdr_{idx:04d}.txt"
            Path(tf).write_text(wrap(text, LINE_HDR), encoding="utf-8")
            chain += (f",drawtext=textfile='{tf.replace(chr(39), chr(92)+chr(39))}':"
                      f"fontfile='{fp}':fontsize={HDR_FS}:fontcolor=0xFFD700:"
                      f"x=(w-text_w)/2:y=(h-text_h)/2:"
                      f"box=1:boxcolor=0x000000@0.78:boxborderw=48:"
                      f"borderw=5:bordercolor=0x000000")

    elif stype == "ending":
        # エンディング: 全体を暗く→CTAテキスト
        chain += ",geq=r='r(X,Y)*0.40':g='g(X,Y)*0.40':b='b(X,Y)*0.40'"
        if font:
            ef = f"{tmp}/ending.txt"
            Path(ef).write_text("チャンネル登録＆通知ON\nで最新予想をGET！", encoding="utf-8")
            chain += (f",drawtext=textfile='{ef.replace(chr(39), chr(92)+chr(39))}':"
                      f"fontfile='{fp}':fontsize=72:fontcolor=0xFFD700:"
                      f"x=(w-text_w)/2:y=(h-text_h)/2:"
                      f"box=1:boxcolor=0x000000@0.80:boxborderw=40:"
                      f"borderw=4:bordercolor=0x000000:"
                      f"line_spacing=20")

    else:
        # ボディ: ローワーサードバー（geqで下部を暗く）＋テキスト
        chain += (f",geq=r='if(gt(Y,{BAR_Y}),r(X,Y)*0.18,r(X,Y))'"
                  f":g='if(gt(Y,{BAR_Y}),g(X,Y)*0.18,g(X,Y))'"
                  f":b='if(gt(Y,{BAR_Y}),b(X,Y)*0.18,b(X,Y))'")
        if font:
            tf = f"{tmp}/body_{idx:04d}.txt"
            Path(tf).write_text(wrap(text, LINE_BODY), encoding="utf-8")
            chain += (f",drawtext=textfile='{tf.replace(chr(39), chr(92)+chr(39))}':"
                      f"fontfile='{fp}':fontsize={BODY_FS}:fontcolor=0xFFFFFF:"
                      f"x=(w-text_w)/2:y={BAR_Y}+({BAR_H}-text_h)/2:"
                      f"borderw=3:bordercolor=0x000000:"
                      f"line_spacing=14")

    chain += "[vout]"
    cmd = (["ffmpeg", "-y"] + inputs +
           ["-filter_complex", chain, "-map", "[vout]", "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
            "-t", str(dur), out])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [警告] clip_{idx:04d} 失敗:\n{r.stderr[-400:]}", file=sys.stderr)
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=#0D1B2A:s={W}x{H}:r={FPS}:d={dur}",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out,
        ], check=True, capture_output=True)
    return out


# ---------------------------------------------------------------------------
def generate_video(idx: int, meta: dict, font: str, bg_imgs: list) -> str:
    """1本分の動画を生成して出力パスを返す。"""
    race_meta = {
        "race_name": meta.get("race_name", meta.get("title", "")),
        "grade":     meta.get("grade", ""),
        "date":      meta.get("date", ""),
        "venue":     meta.get("venue", ""),
        "distance":  meta.get("distance", ""),
    }

    script_path = Path(f"{OUTPUT_DIR}/script_{idx}.txt")
    audio_path  = f"{OUTPUT_DIR}/audio_{idx}.mp3"
    output_path = f"{OUTPUT_DIR}/landscape_video_{idx}.mp4"

    if not script_path.exists():
        raise FileNotFoundError(f"{script_path} が見つかりません。")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"{audio_path} が見つかりません。")

    script   = script_path.read_text(encoding="utf-8").strip()
    segments = parse_segments(script)

    aud_dur    = audio_duration(audio_path)
    total_body = sum(len(s["text"]) for s in segments)
    body_audio = max(aud_dur - OPEN_DUR - END_DUR, aud_dur * 0.8)

    def seg_dur(seg: dict) -> float:
        chars = len(seg["text"])
        prop  = body_audio * chars / total_body if total_body else MIN_DUR
        return max(HDR_DUR if seg["type"] == "header" else MIN_DUR, prop)

    tmp_dir = tempfile.mkdtemp(prefix=f"landscape_{idx}_")
    try:
        clips = []
        clips.append(make_clip(0, bg_imgs[0], {"type":"opening"}, OPEN_DUR, font, tmp_dir, race_meta))
        for i, seg in enumerate(segments, start=1):
            bg = bg_imgs[i % len(bg_imgs)]
            clips.append(make_clip(i, bg, seg, seg_dur(seg), font, tmp_dir, race_meta))
            print(f"  [{i}/{len(segments)}] {seg['type']} 「{seg['text'][:30]}」 {seg_dur(seg):.1f}s")
        clips.append(make_clip(len(segments)+1, bg_imgs[-1], {"type":"ending"}, END_DUR, font, tmp_dir, race_meta))

        concat_txt = f"{tmp_dir}/concat.txt"
        with open(concat_txt, "w") as f:
            for c in clips:
                f.write(f"file '{c}'\n")
        silent = f"{tmp_dir}/silent.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", silent,
        ], check=True, capture_output=True)

        bgm_files = sorted(glob.glob(f"{BGM_DIR}/*.mp3") + glob.glob(f"{BGM_DIR}/*.m4a"))
        total_dur = OPEN_DUR + sum(seg_dur(s) for s in segments) + END_DUR
        cmd = ["ffmpeg", "-y", "-i", silent, "-i", audio_path]
        if bgm_files:
            import random
            bgm = random.choice(bgm_files)
            print(f"  BGM: {Path(bgm).name}")
            cmd += ["-stream_loop", "-1", "-i", bgm,
                    "-filter_complex",
                    f"[1:a]apad=whole_dur={total_dur:.3f}[narr];[narr][2:a]amix=inputs=2:duration=first:weights=1 {BGM_VOL}[aout]",
                    "-map", "0:v", "-map", "[aout]"]
        else:
            cmd += ["-af", f"apad=whole_dur={total_dur:.3f}", "-map", "0:v", "-map", "1:a"]
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", output_path]
        subprocess.run(cmd, check=True, capture_output=True)

        size_mb = Path(output_path).stat().st_size / (1024*1024)
        print(f"✅ {output_path} ({size_mb:.1f} MB)")
        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    news_items = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("news.json が空です。スキップします。")
        sys.exit(0)

    print(f"動画生成対象: {len(news_items)} 件")

    font    = find_font()
    bg_imgs = fetch_images(4)
    if not bg_imgs:
        print("[エラー] 背景画像の取得に失敗しました。", file=sys.stderr)
        sys.exit(1)

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    # ffmpegはCPUバウンドのためワーカー数は2（GitHub Actions: 2 vCPU）
    max_workers = min(2, len(news_items))
    print(f"並列ワーカー数: {max_workers}")

    def _gen(args: tuple) -> int:
        idx, meta = args
        race_name = meta.get("race_name", f"レース{idx}")
        print(f"\n=== [{idx}] {race_name} ===")
        generate_video(idx, meta, font, bg_imgs)
        return idx

    success = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_gen, (idx, meta)): idx
            for idx, meta in enumerate(news_items)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                future.result()
                success += 1
            except Exception as e:
                race_name = news_items[idx].get("race_name", f"レース{idx}")
                print(f"[エラー] {race_name} の動画生成失敗: {e}", file=sys.stderr)

    if success == 0:
        print("[エラー] 全レースの動画生成に失敗しました。", file=sys.stderr)
        sys.exit(1)
    print(f"\n✅ {success}/{len(news_items)} 本の動画を生成しました。")


if __name__ == "__main__":
    main()
