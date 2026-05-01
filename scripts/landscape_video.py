#!/usr/bin/env python3
"""横向き（1280×720）予想解説動画を生成する。

generate_audio.py が生成した ASS 字幕ファイル（output/subtitles_N.ass）を
ffmpeg の ass フィルターで適用することで音声と字幕を正確に同期させる。
単一の ffmpeg プロセスで完結させることで生成を高速化する。
"""
import glob
import json
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

NEWS_JSON  = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
BGM_DIR    = f"{ASSETS_DIR}/bgm"
W, H       = 1280, 720
FPS        = 30
OPEN_DUR   = 3.0   # オープニングカード表示秒数
CARD_DUR   = 2.5   # チャプタータイトルカード表示秒数
BGM_VOL    = 0.12


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


def audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 60.0


_SSL_CTX = ssl.create_default_context()
_WP_UA   = "keiba-auto-youtube/1.0"
_WP_SKIP = {".svg", ".ogv", ".ogg", ".webm", ".gif"}


def _fetch_wikipedia_image(horse_name: str, out_path: str) -> bool:
    """Wikipedia(ja→en)から馬の画像を取得してJPEGに変換する。"""
    for lang in ("ja", "en"):
        encoded = urllib.parse.quote(horse_name)
        api_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
        try:
            req = urllib.request.Request(
                api_url, headers={"User-Agent": _WP_UA, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as r:
                data = json.loads(r.read())
        except Exception:
            continue
        src = (data.get("originalimage") or data.get("thumbnail") or {}).get("source", "")
        if not src or Path(src.split("?")[0]).suffix.lower() in _WP_SKIP:
            continue
        ext = Path(src.split("?")[0]).suffix.lower() or ".jpg"
        try:
            req2 = urllib.request.Request(src, headers={"User-Agent": _WP_UA})
            with urllib.request.urlopen(req2, timeout=30, context=_SSL_CTX) as r:
                raw = r.read()
        except Exception:
            continue
        tmp = out_path + ".raw" + ext
        Path(tmp).write_bytes(raw)
        res = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp, "-frames:v", "1", "-q:v", "2", out_path],
            capture_output=True,
        )
        Path(tmp).unlink(missing_ok=True)
        if res.returncode == 0:
            return True
    return False


def fetch_images(count: int = 4, horse_names: list[str] | None = None) -> list[str]:
    """馬名が指定されればWikipedia、次いでPixabayから競馬写真を取得。
    失敗時は geq グラデーションフォールバック。"""
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    paths: list[str] = []

    # Wikipedia から馬専用画像を取得
    if horse_names:
        for i, name in enumerate(horse_names[:count]):
            out = f"{ASSETS_DIR}/landscape_{i}.jpg"
            if _fetch_wikipedia_image(name, out):
                paths.append(out)
                print(f"  Wikipedia画像: {name}")
        if len(paths) >= count:
            return paths[:count]

    queries = ["horse racing track", "horse racing", "jockey horse race", "thoroughbred racing"]
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "")

    import requests
    for i in range(len(paths), count):
        out = f"{ASSETS_DIR}/landscape_{i}.jpg"
        if Path(out).exists() and Path(out).stat().st_size > 1000:
            paths.append(out)
            continue
        if pixabay_key:
            try:
                r = requests.get("https://pixabay.com/api/", params={
                    "key": pixabay_key, "q": queries[i % len(queries)],
                    "image_type": "photo", "orientation": "horizontal",
                    "min_width": 1280, "min_height": 720, "per_page": 20, "safesearch": "true",
                }, timeout=30)
                hits = r.json().get("hits", [])
                if hits:
                    url = random.choice(hits).get("webformatURL", "")
                    if url:
                        img = requests.get(url, timeout=30)
                        if img.status_code == 200:
                            tmp = out + ".tmp"
                            Path(tmp).write_bytes(img.content)
                            res = subprocess.run(
                                ["ffmpeg", "-y", "-i", tmp, "-frames:v", "1", "-q:v", "2", out],
                                capture_output=True,
                            )
                            Path(tmp).unlink(missing_ok=True)
                            if res.returncode == 0:
                                paths.append(out)
                                continue
            except Exception as e:
                print(f"  [警告] Pixabay失敗: {e}", file=sys.stderr)

        # geq グラデーションフォールバック
        colors = [
            ("clip(8+148*pow(Y/H,1.6),8,156)", "clip(4*pow(Y/H,2),0,4)",   "clip(4*pow(Y/H,2),0,4)"),
            ("clip(4*pow(1-Y/H,2),0,4)",        "clip(4*pow(1-Y/H,2),0,4)", "clip(10+105*pow(1-Y/H,1.5),10,115)"),
            ("clip(8+100*pow(1-Y/H,1.4),8,108)","clip(6+68*pow(1-Y/H,1.6),6,74)", "clip(2,0,2)"),
            ("clip(5*pow(Y/H,2),0,5)",           "clip(8+80*pow(Y/H,1.5),8,88)",   "clip(8+90*pow(1-Y/H,1.5),8,98)"),
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


def _ass_time_to_s(t: str) -> float:
    """ASS時刻文字列（H:MM:SS.cc）を秒数に変換する。"""
    h, m, rest = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def ass_to_drawtext_filters(ass_path: str, font: str | None, tmp_dir: str) -> list[str]:
    """ASSファイルのDialogueイベントをdrawtextフィルターに変換する。

    ass フィルターは libass + fontconfig でフォントを名前検索するため、
    GitHub Actions 環境で CJK フォントが見つからず字幕が描画されないことがある。
    drawtext は fontfile= でファイルパスを直接指定できるため安定して動作する。
    """
    content = Path(ass_path).read_text(encoding="utf-8")
    fp = _esc(font) if font else ""
    filters: list[str] = []

    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        # Format: Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        text = parts[9].replace("\\N", "\n").strip()
        if not text:
            continue
        try:
            t1 = _ass_time_to_s(parts[1])
            t2 = _ass_time_to_s(parts[2])
        except Exception:
            continue
        if t2 <= t1 + 0.01:
            continue

        tf = f"{tmp_dir}/s{len(filters)}.txt"
        Path(tf).write_text(text, encoding="utf-8")

        base = f"drawtext=textfile='{_esc(tf)}'"
        if fp:
            base += f":fontfile='{fp}'"
        base += (
            f":fontsize=36:fontcolor=0xFFFFFF"
            f":x=(w-text_w)/2:y=h-text_h-40"
            f":box=1:boxcolor=0x000000@0.65:boxborderw=10"
            f":borderw=2:bordercolor=0x000000"
            f":enable='between(t,{t1:.3f},{t2:.3f})'"
        )
        filters.append(base)

    return filters


def parse_chapters(script: str) -> list[tuple[str, int]]:
    """スクリプトから(章タイトル, 累積文字位置)のリストを返す。"""
    chapters: list[tuple[str, int]] = []
    pos = 0
    for block in script.split("\n\n"):
        stripped = block.strip()
        if stripped.startswith("【") and "】" in stripped:
            header = stripped.split("\n")[0].strip()
            chapters.append((header, pos))
        pos += len(block) + 2
    return chapters


def wrap_text(text: str, max_chars: int) -> str:
    lines, para = [], text
    while len(para) > max_chars:
        lines.append(para[:max_chars])
        para = para[max_chars:]
    if para:
        lines.append(para)
    return "\n".join(lines)


def _esc(path: str) -> str:
    """ffmpeg textfile パスのシングルクォートエスケープ。"""
    return path.replace("'", "\\'")


def generate_thumbnail(meta: dict, font: str | None, bg_img: str | None,
                       thumb_path: str, tmp_dir: str) -> None:
    """サムネイル画像を生成する。THUMBNAIL_HOOKで注目を引くデザインに。"""
    hook      = meta.get("thumbnail_hook", "")
    race_name = meta.get("race_name", "")
    grade     = meta.get("grade", "")
    fp = _esc(font or "")

    if bg_img and Path(bg_img).exists():
        inputs = ["-loop", "1", "-i", bg_img]
        v_init = [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}"]
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c=#0D1B2A:s={W}x{H}:r=1"]
        v_init = []

    filters: list[str] = v_init + ["eq=brightness=-0.45:contrast=1.05"]

    if font:
        if grade:
            gf = f"{tmp_dir}/tg.txt"
            Path(gf).write_text(grade, encoding="utf-8")
            filters.append(
                f"drawtext=textfile='{_esc(gf)}':fontfile='{fp}':fontsize=42:fontcolor=0xFFFFFF"
                f":x=40:y=40:box=1:boxcolor=0xCC0000@0.95:boxborderw=18"
            )

        main_text = hook or race_name
        main_fs   = 72 if hook else 68
        main_col  = "0xFFFF00" if hook else "0xFFD700"
        main_border_col = "0xFF6600" if hook else "0x000000"
        if main_text:
            mf = f"{tmp_dir}/tm.txt"
            Path(mf).write_text(wrap_text(main_text, 14), encoding="utf-8")
            filters.append(
                f"drawtext=textfile='{_esc(mf)}':fontfile='{fp}':fontsize={main_fs}:fontcolor={main_col}"
                f":x=(w-text_w)/2:y=(h-text_h)/2-20"
                f":box=1:boxcolor=0x000000@0.88:boxborderw=30"
                f":borderw=5:bordercolor={main_border_col}"
            )

        # Race name at bottom (only when hook is the main text)
        if hook and race_name:
            nf = f"{tmp_dir}/tn.txt"
            Path(nf).write_text(wrap_text(race_name, 16), encoding="utf-8")
            filters.append(
                f"drawtext=textfile='{_esc(nf)}':fontfile='{fp}':fontsize=40:fontcolor=0xFFFFFF"
                f":x=(w-text_w)/2:y=h-text_h-50"
                f":box=1:boxcolor=0x000000@0.80:boxborderw=16"
            )

    fc = "[0:v]" + ",".join(filters) + "[vout]"
    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc, "-map", "[vout]",
        "-frames:v", "1", "-q:v", "2", thumb_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        size_kb = Path(thumb_path).stat().st_size // 1024
        print(f"  サムネイル: {thumb_path} ({size_kb} KB)")
    else:
        print(f"  [警告] サムネイル生成失敗:\n{result.stderr[-300:]}", file=sys.stderr)


def generate_video(idx: int, meta: dict, font: str | None, bg_imgs: list[str]) -> str:
    """1本分の動画を生成して出力パスを返す。"""
    race_meta = {
        "race_name":      meta.get("race_name", meta.get("title", "")),
        "grade":          meta.get("grade", ""),
        "date":           meta.get("date", ""),
        "venue":          meta.get("venue", ""),
        "distance":       meta.get("distance", ""),
        "thumbnail_hook": meta.get("thumbnail_hook", ""),
    }

    script_path = Path(f"{OUTPUT_DIR}/script_{idx}.txt")
    audio_path  = f"{OUTPUT_DIR}/audio_{idx}.mp3"
    ass_path    = f"{OUTPUT_DIR}/subtitles_{idx}.ass"
    output_path = f"{OUTPUT_DIR}/landscape_video_{idx}.mp4"
    thumb_path  = f"{OUTPUT_DIR}/thumbnail_{idx}.jpg"

    if not script_path.exists():
        raise FileNotFoundError(f"{script_path} が見つかりません。")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"{audio_path} が見つかりません。")

    script      = script_path.read_text(encoding="utf-8").strip()
    aud_dur     = audio_duration(audio_path)
    total_dur   = aud_dur
    total_chars = max(len(script), 1)
    chapters    = parse_chapters(script)

    def ch_t(char_pos: int) -> float:
        return (char_pos / total_chars) * aud_dur

    tmp_dir = tempfile.mkdtemp(prefix=f"ls_{idx}_")
    try:
        fp = _esc(font or "")
        valid_imgs = [p for p in bg_imgs if p and Path(p).exists()]
        N = min(len(valid_imgs), 4)

        img_scale = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{H},eq=brightness=-0.06")

        if N >= 2:
            # 複数画像をconcatしてシーンチェンジ
            seg_dur = total_dur / N
            bg_inputs = []
            for i, img in enumerate(valid_imgs[:N]):
                dur = seg_dur if i < N - 1 else (total_dur - (N - 1) * seg_dur + 1.0)
                bg_inputs += ["-r", str(FPS), "-loop", "1", "-t", f"{dur:.3f}", "-i", img]
            pre_filters = [f"[{i}:v]{img_scale}[vi{i}]" for i in range(N)]
            concat_in = "".join(f"[vi{i}]" for i in range(N))
            pre_filters.append(f"{concat_in}concat=n={N}:v=1:a=0[bgout]")
            vid_start = "[bgout]"
            vid_init  = []
            audio_idx = N
            print(f"  背景: {N}枚concat ({seg_dur:.1f}s×{N})")
        elif N == 1:
            bg_inputs = ["-loop", "1", "-i", valid_imgs[0]]
            pre_filters = []
            vid_start = "[0:v]"
            vid_init  = [f"scale={W}:{H}:force_original_aspect_ratio=increase",
                         f"crop={W}:{H}", "eq=brightness=-0.06"]
            audio_idx = 1
        else:
            bg_inputs = ["-f", "lavfi", "-i", f"color=c=#0D1B2A:s={W}x{H}:r={FPS}"]
            pre_filters = []
            vid_start = "[0:v]"
            vid_init  = []
            audio_idx = 1

        video_filters: list[str] = list(vid_init)

        if font:
            # --- Opening card (t=0 to OPEN_DUR) ---
            grade    = race_meta["grade"]
            rn_text  = race_meta["race_name"]
            date_str = " ".join(filter(None, [
                race_meta["date"], race_meta["venue"], race_meta["distance"]
            ]))

            if grade:
                gf = f"{tmp_dir}/g.txt"
                Path(gf).write_text(grade, encoding="utf-8")
                video_filters.append(
                    f"drawtext=textfile='{_esc(gf)}':fontfile='{fp}':fontsize=48:fontcolor=0xFFFFFF"
                    f":x=30:y=30:box=1:boxcolor=0xCC0000@0.95:boxborderw=18"
                    f":enable='between(t,0,{OPEN_DUR})'"
                )
            if rn_text:
                nf = f"{tmp_dir}/n.txt"
                Path(nf).write_text(wrap_text(rn_text, 12), encoding="utf-8")
                video_filters.append(
                    f"drawtext=textfile='{_esc(nf)}':fontfile='{fp}':fontsize=80:fontcolor=0xFFD700"
                    f":x=(w-text_w)/2:y=(h-text_h)/2-30"
                    f":box=1:boxcolor=0x000000@0.82:boxborderw=32"
                    f":borderw=3:bordercolor=0x000000"
                    f":enable='between(t,0,{OPEN_DUR})'"
                )
            if date_str:
                df = f"{tmp_dir}/d.txt"
                Path(df).write_text(date_str, encoding="utf-8")
                video_filters.append(
                    f"drawtext=textfile='{_esc(df)}':fontfile='{fp}':fontsize=36:fontcolor=0xFFFFFF"
                    f":x=(w-text_w)/2:y=h/2+60"
                    f":borderw=2:bordercolor=0x000000"
                    f":enable='between(t,0,{OPEN_DUR})'"
                )

            # --- Chapter title cards ---
            for ci, (ch_title, ch_pos) in enumerate(chapters):
                t1 = ch_t(ch_pos)
                t2 = min(t1 + CARD_DUR, total_dur)
                if t2 <= t1 + 0.1:
                    continue
                cf = f"{tmp_dir}/c{ci}.txt"
                Path(cf).write_text(wrap_text(ch_title, 12), encoding="utf-8")
                video_filters.append(
                    f"drawtext=textfile='{_esc(cf)}':fontfile='{fp}':fontsize=52:fontcolor=0xFFD700"
                    f":x=(w-text_w)/2:y=(h-text_h)/2"
                    f":box=1:boxcolor=0x000000@0.85:boxborderw=40"
                    f":borderw=4:bordercolor=0x000000"
                    f":enable='between(t,{t1:.2f},{t2:.2f})'"
                )

        # --- ASS字幕 → drawtext フィルターに変換して適用 ---
        # ass フィルターは libass+fontconfig でフォントを名前検索するため
        # GitHub Actions 環境で CJK フォントが見つからず字幕未表示になる。
        # drawtext は fontfile= で直接指定できるため確実に動作する。
        has_ass = Path(ass_path).exists() and Path(ass_path).stat().st_size > 100
        if has_ass:
            sub_filters = ass_to_drawtext_filters(ass_path, font, tmp_dir)
            video_filters.extend(sub_filters)
            print(f"  字幕 drawtext: {len(sub_filters)} セグメント")
        else:
            print("  [警告] ASS字幕ファイルなし。字幕なしで生成します。", file=sys.stderr)

        if not video_filters:
            video_filters.append(f"scale={W}:{H}")

        vid_chain = vid_start + ",".join(video_filters) + "[vout]"
        fc_video_parts = list(pre_filters) + [vid_chain]

        # --- BGM ---
        bgm_files = sorted(glob.glob(f"{BGM_DIR}/*.mp3") + glob.glob(f"{BGM_DIR}/*.m4a"))
        bgm_file  = random.choice(bgm_files) if bgm_files else None

        if bgm_file:
            print(f"  BGM: {Path(bgm_file).name}")
            audio_chain = (
                f"[{audio_idx}:a]apad=whole_dur={total_dur:.3f}[narr];"
                f"[narr][{audio_idx+1}:a]amix=inputs=2:duration=first:weights=1 {BGM_VOL}[aout]"
            )
            fc = ";".join(fc_video_parts) + ";" + audio_chain
            cmd = (["ffmpeg", "-y"] + bg_inputs +
                   ["-i", audio_path, "-stream_loop", "-1", "-i", bgm_file,
                    "-filter_complex", fc,
                    "-map", "[vout]", "-map", "[aout]"])
        else:
            audio_chain = f"[{audio_idx}:a]apad=whole_dur={total_dur:.3f}[aout]"
            fc = ";".join(fc_video_parts) + ";" + audio_chain
            cmd = (["ffmpeg", "-y"] + bg_inputs +
                   ["-i", audio_path,
                    "-filter_complex", fc,
                    "-map", "[vout]", "-map", "[aout]"])

        cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(total_dur + 0.5),
            output_path,
        ]

        print(f"  動画生成中... (音声長: {total_dur:.1f}s, チャプター: {len(chapters)}個)")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"  [エラー] ffmpeg失敗:\n{result.stderr[-800:]}", file=sys.stderr)
            raise RuntimeError(f"ffmpeg失敗 returncode={result.returncode}")

        size_mb = Path(output_path).stat().st_size / (1024 * 1024)
        print(f"✅ {output_path} ({size_mb:.1f} MB)")

        bg_img = valid_imgs[0] if valid_imgs else None
        if Path(thumb_path).exists():
            print(f"  サムネイル既存のためスキップ: {thumb_path}")
        else:
            generate_thumbnail(race_meta, font, bg_img, thumb_path, tmp_dir)
        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    news_items = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("news.json が空です。スキップします。")
        sys.exit(0)

    print(f"動画生成対象: {len(news_items)} 件")
    font = find_font()
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    success = 0
    for idx, meta in enumerate(news_items):
        race_name   = meta.get("race_name", f"レース{idx}")
        horse_names = meta.get("horses")
        print(f"\n=== [{idx}] {race_name} ===")
        bg_imgs = fetch_images(4, horse_names=horse_names)
        if not bg_imgs:
            print("[警告] 背景画像取得失敗。ソリッドカラーを使用します。", file=sys.stderr)
        try:
            generate_video(idx, meta, font, bg_imgs)
            success += 1
        except Exception as e:
            print(f"[エラー] {race_name} の動画生成失敗: {e}", file=sys.stderr)

    if success == 0:
        print("[エラー] 全レースの動画生成に失敗しました。", file=sys.stderr)
        sys.exit(1)
    print(f"\n✅ {success}/{len(news_items)} 本の動画を生成しました。")


if __name__ == "__main__":
    main()
