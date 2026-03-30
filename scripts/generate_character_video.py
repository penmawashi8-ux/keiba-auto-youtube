#!/usr/bin/env python3
"""
キャラクター動画生成スクリプト
「ウマコ」（馬と人のハーフキャラクター）が競馬豆知識を語る動画を生成する。
通常の競馬ニュース動画とビジュアル・声・スタイルを全て変えることで、
コンテンツの多様性を確保しAI自動投稿に見えにくくする。

出力: output/character_video.mp4, output/character_script.txt

実行条件（どちらかを満たせば生成）:
  - 環境変数 FORCE_CHARACTER_VIDEO=1
  - posted_ids.txt の行数 % CHARACTER_VIDEO_INTERVAL == 0（デフォルト10）
"""

import asyncio
import glob
import io
import os
import random
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
BGM_DIR = f"{ASSETS_DIR}/bgm"

HF_MODEL_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
# ウマコキャラクター生成プロンプト
CHARACTER_IMAGE_PROMPT = (
    "cute kawaii chibi anime style horse girl mascot character, "
    "brown horse ears and flowing mane, red and yellow striped jockey helmet, "
    "big sparkly expressive eyes, warm smile, cream and brown color scheme, "
    "bright yellow orange gradient background, full body cartoon illustration, "
    "vertical portrait composition, centered, high quality digital art, "
    "no text, no watermark"
)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30

# 豆知識・雑学スクリプト集（固定コンテンツ）
CHARACTER_SCRIPTS = [
    "サラブレッドの最高時速、知ってる？なんと70キロ以上！人間の世界最速記録が時速44キロだから、馬の速さは本当にケタ違いだよ！",
    "騎手ってものすごく体重管理が厳しくて、ほとんどの人は50キロ前後を維持してるんだって。試合前に数キロ落とす人もいるんだよ。ストイックすぎる！",
    "競馬のG1って何？って思ってる人へ。グレード制度の最高峰で、JRAには年間25レースあるよ。馬にとって夢の大舞台なんだ！",
    "競走馬の年齢は全馬1月1日に一斉に1歳増えるんだ！だから1月生まれと12月生まれが同じ「2歳」として戦うことがあるんだよ。生まれ月ってけっこう大事なんだね。",
    "馬の目って顔の真横についてるから、ほぼ360度見えるんだって！後ろもほぼ丸見えだから、こっそり近づいてもバレバレなのだ！",
    "競馬場のターフ（芝コース）は専用の芝を何層にも重ねて作られていて、管理がすごく大変なんだよ。馬もスタッフも芝を大切にしてるんだね。",
    "サラブレッドの名前には規則がいっぱいあって、18文字以内・他の馬と重複NG・難しすぎる漢字もNG、などなど審査が厳しいんだ。名前つけるのも一苦労だよ！",
    "馬って立ったまま眠れるの知ってた？足の関節をロックできるから倒れずにウトウトできるんだって。横になって深く寝ることもあるけどね。",
    "JRAの馬券の年間売上は約3兆円以上！日本のスポーツ産業の中でもずば抜けた規模なんだよ。競馬ファンってすごく多いんだね。",
    "競走馬が引退したあと、乗馬クラブや牧場で第二の人生を送る馬もたくさんいるよ。繁殖に使われて子孫がまた競馬界に戻ってくることも多いんだ！",
    "ダービーって世界中にあるの知ってた？英国ダービーが起源で、日本ダービー・ケンタッキーダービーなど各国にあるんだよ。競馬のお祭りって感じだよね！",
    "競馬のスタートゲートは1960年代に普及したんだって。それまでは人が旗を持ってスタートを決めてたんだよ。今と全然違うね！",
]


def should_generate() -> bool:
    """キャラクター動画を生成すべきかどうかを判定する。"""
    # 強制モード
    force = os.environ.get("FORCE_CHARACTER_VIDEO", "").lower()
    if force in ("1", "true", "yes"):
        print("FORCE_CHARACTER_VIDEO=true → キャラクター動画を強制生成します。")
        return True

    # コマンドライン引数 --test
    if "--test" in sys.argv:
        print("--test フラグ → キャラクター動画を生成します。")
        return True

    # カウンターベースのトリガー
    interval = int(os.environ.get("CHARACTER_VIDEO_INTERVAL", "10"))
    posted_path = Path("posted_ids.txt")
    count = len(posted_path.read_text(encoding="utf-8").splitlines()) if posted_path.exists() else 0
    print(f"投稿済み件数: {count}, インターバル: {interval}")

    if count > 0 and count % interval == 0:
        print(f"投稿済み件数が {interval} の倍数 → キャラクター動画を生成します。")
        return True

    remaining = interval - (count % interval)
    print(f"スキップ: キャラクター動画はあと {remaining} 本後に生成予定。")
    return False


def generate_character_image_hf(hf_token: str) -> Image.Image | None:
    """HuggingFace FLUX.1-schnell でウマコのキャラクター画像を AI 生成する。
    失敗時は None を返す（呼び出し元で Pillow フォールバック）。"""
    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {"inputs": CHARACTER_IMAGE_PROMPT}  # generate_images.py と同じ形式（parametersなし）
    print("  [AI] HuggingFace FLUX でウマコキャラクター画像を生成中...")
    for attempt in range(3):
        try:
            r = requests.post(HF_MODEL_URL, headers=headers, json=payload, timeout=120)
            print(f"  [HF] status={r.status_code} ({len(r.content)} bytes)")
            if r.status_code == 200 and len(r.content) > 5000:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                # 1080x1920 にリサイズ（上部を優先するよう center-top でクロップ）
                img = ImageOps.fit(img, (VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS,
                                   centering=(0.5, 0.3))
                print(f"  [AI] ウマコ画像生成成功: {img.size}")
                return img
            elif r.status_code == 503:
                wait = 30 * (attempt + 1)
                print(f"  [HF] モデル読み込み中... {wait}秒待機")
                time.sleep(wait)
            else:
                print(f"  [HF] エラー: {r.status_code} {r.text[:200]}")
                break
        except Exception as e:
            print(f"  [HF] 例外: {type(e).__name__}: {e}")
            break
    print("  [AI] HF 生成失敗 → Pillow フォールバックを使用します。")
    return None


def find_japanese_font() -> str | None:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    return hits[0] if hits else None


def draw_character_bg(ai_image: Image.Image | None = None) -> Image.Image:
    """ウマコのキャラクターフレームを生成する。
    ai_image が渡された場合はそれを背景に使い、バナーだけ Pillow で追加する。
    ai_image が None の場合は Pillow で全て描画する（フォールバック）。
    """
    W, H = VIDEO_WIDTH, VIDEO_HEIGHT
    font_path = find_japanese_font()

    def load_font(size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()

    if ai_image is not None:
        # ===== AI生成画像を背景に使うパス =====
        img = ai_image.copy()
        draw = ImageDraw.Draw(img)

        # バナー部分だけ半透明暗幕（視認性確保）
        overlay = Image.new("RGBA", (W, 200), (0, 0, 0, 160))
        img = img.convert("RGBA")
        img.paste(overlay, (0, 40), overlay)
        img = img.convert("RGB")
        draw = ImageDraw.Draw(img)

        # ========== タイトルバナー（上部） ==========
        banner_font = load_font(62)
        banner_text = "ウマコの競馬豆知識"
        draw.rounded_rectangle([40, 58, W - 40, 185], radius=22, fill=(180, 50, 20))
        try:
            bb = draw.textbbox((0, 0), banner_text, font=banner_font)
            tw = bb[2] - bb[0]
        except Exception:
            tw = len(banner_text) * 37
        draw.text(((W - tw) // 2, 90), banner_text,
                  font=banner_font, fill=(255, 255, 255),
                  stroke_width=3, stroke_fill=(100, 20, 0))
        return img

    # ===== Pillow フォールバック（AI生成なし） =====
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # 背景: 黄色〜オレンジのグラデーション（通常の濃紺と真逆）
    for y in range(H):
        r = 255
        g = max(0, int(220 - 80 * y / H))
        b = max(0, int(60 - 60 * y / H))
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # ランダムな丸模様（ポップな雰囲気）
    rng = random.Random(42)
    for _ in range(60):
        dx = rng.randint(0, W)
        dy = rng.randint(0, H)
        dr = rng.randint(8, 28)
        draw.ellipse([dx, dy, dx + dr, dy + dr], fill=(255, 255, 120))

    # ========== タイトルバナー（上部） ==========
    banner_font = load_font(58)
    banner_text = "ウマコの競馬豆知識"
    draw.rounded_rectangle([40, 60, W - 40, 180], radius=22, fill=(180, 50, 20))
    try:
        bb = draw.textbbox((0, 0), banner_text, font=banner_font)
        tw = bb[2] - bb[0]
    except Exception:
        tw = len(banner_text) * 35
    draw.text(((W - tw) // 2, 90), banner_text,
              font=banner_font, fill=(255, 255, 255),
              stroke_width=3, stroke_fill=(100, 20, 0))

    # ========== キャラクター（馬顔）描画 ==========
    BROWN = (160, 100, 40)
    DARK_BROWN = (80, 45, 10)
    CREAM = (220, 170, 90)
    WHITE = (255, 255, 255)
    BLACK = (20, 20, 20)
    PINK = (255, 160, 160)
    HELMET_RED = (200, 40, 40)
    HELMET_GOLD = (255, 220, 0)

    cx = W // 2
    face_cy = 780
    face_r = 235

    # --------- 体（胴体楕円） ---------
    draw.ellipse([cx - 200, face_cy + 210, cx + 200, face_cy + 560], fill=BROWN)

    # --------- 前足（2本） ---------
    for lx in [cx - 90, cx + 30]:
        draw.rounded_rectangle([lx, face_cy + 490, lx + 60, face_cy + 700],
                                radius=14, fill=BROWN)
        draw.ellipse([lx - 6, face_cy + 690, lx + 66, face_cy + 730], fill=DARK_BROWN)

    # --------- 首 ---------
    draw.ellipse([cx - 80, face_cy - 60, cx + 80, face_cy + 270], fill=BROWN)

    # --------- 顔（影→本体） ---------
    draw.ellipse([cx - face_r + 12, face_cy - face_r + 12,
                  cx + face_r + 12, face_cy + face_r + 12], fill=DARK_BROWN)
    draw.ellipse([cx - face_r, face_cy - face_r,
                  cx + face_r, face_cy + face_r], fill=BROWN)

    # --------- 耳 ---------
    draw.polygon([(cx - 155, face_cy - 185),
                  (cx - 235, face_cy - 340),
                  (cx - 78, face_cy - 198)], fill=BROWN)
    draw.polygon([(cx - 147, face_cy - 193),
                  (cx - 213, face_cy - 314),
                  (cx - 86, face_cy - 204)], fill=PINK)
    draw.polygon([(cx + 155, face_cy - 185),
                  (cx + 235, face_cy - 340),
                  (cx + 78, face_cy - 198)], fill=BROWN)
    draw.polygon([(cx + 147, face_cy - 193),
                  (cx + 213, face_cy - 314),
                  (cx + 86, face_cy - 204)], fill=PINK)

    # --------- たてがみ ---------
    for i, mx in enumerate(range(cx - 175, cx + 200, 44)):
        mh = 58 + (i % 3) * 24
        draw.ellipse([mx, face_cy - face_r - mh, mx + 44, face_cy - face_r + 12], fill=DARK_BROWN)

    # --------- 口元（クリーム色の鼻口部） ---------
    draw.ellipse([cx - 138, face_cy + 55, cx + 138, face_cy + face_r + 45], fill=CREAM)

    # --------- 目（左右） ---------
    EYE_Y = face_cy - 48
    for ex in [cx - 92, cx + 92]:
        draw.ellipse([ex - 50, EYE_Y - 34, ex + 50, EYE_Y + 34], fill=WHITE)
        draw.ellipse([ex - 25, EYE_Y - 25, ex + 25, EYE_Y + 25], fill=(80, 40, 10))
        draw.ellipse([ex - 15, EYE_Y - 15, ex + 15, EYE_Y + 15], fill=BLACK)
        draw.ellipse([ex + 4, EYE_Y - 11, ex + 13, EYE_Y - 2], fill=WHITE)

    # --------- まつ毛 ---------
    for i in range(4):
        draw.line([(cx - 122 + i * 20, EYE_Y - 31),
                   (cx - 127 + i * 18, EYE_Y - 55)],
                  fill=DARK_BROWN, width=4)
        draw.line([(cx + 62 + i * 20, EYE_Y - 31),
                   (cx + 67 + i * 18, EYE_Y - 55)],
                  fill=DARK_BROWN, width=4)

    # --------- 鼻の穴 ---------
    draw.ellipse([cx - 43, face_cy + 132, cx - 13, face_cy + 160], fill=DARK_BROWN)
    draw.ellipse([cx + 13, face_cy + 132, cx + 43, face_cy + 160], fill=DARK_BROWN)

    # --------- 笑顔 ---------
    draw.arc([cx - 62, face_cy + 58, cx + 62, face_cy + 150],
             start=20, end=160, fill=DARK_BROWN, width=7)

    # --------- 騎手ヘルメット ---------
    helm_top = face_cy - face_r - 28
    helm_bot = face_cy - face_r + 105
    draw.ellipse([cx - face_r + 22, helm_top, cx + face_r - 22, helm_bot + 50], fill=HELMET_RED)
    draw.rectangle([cx - face_r - 4, helm_bot + 12,
                    cx + face_r + 4, helm_bot + 46], fill=HELMET_RED)
    for sx in range(cx - 196, cx + 220, 50):
        draw.rectangle([sx, helm_top + 12, sx + 24, helm_bot + 50], fill=HELMET_GOLD)
    draw.ellipse([cx - face_r + 22, helm_top, cx + face_r - 22, helm_bot + 50],
                 outline=DARK_BROWN, width=3)

    return img


async def generate_character_audio(script: str, audio_path: str) -> None:
    """キャラクター用音声（女性声・少し速め）を生成する。"""
    import edge_tts
    communicate = edge_tts.Communicate(script, "ja-JP-NanamiNeural", rate="+15%")
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
    size_kb = Path(audio_path).stat().st_size // 1024
    print(f"  キャラクター音声生成完了: {audio_path} ({size_kb} KB)")


def get_audio_duration(audio_path: str) -> float:
    try:
        from mutagen.mp3 import MP3
        return MP3(audio_path).info.length
    except Exception:
        pass
    import re
    result = subprocess.run(["ffmpeg", "-i", audio_path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + s
    return 20.0


def run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [ffmpeg ERROR] {result.stderr[-300:]}", file=sys.stderr)
        raise RuntimeError(f"ffmpeg失敗: {cmd[:5]}")


def build_character_video(script: str, audio_path: str, output_path: str,
                          ai_image: Image.Image | None = None) -> None:
    """キャラクター動画をffmpegで合成する。"""
    font_path = find_japanese_font()

    def load_font(size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()

    audio_duration = get_audio_duration(audio_path)
    raw_sentences = [s.strip() for s in script.split("。") if s.strip()]
    sentences = [s + "。" for s in raw_sentences] or [script]
    total_chars = sum(len(s) for s in sentences)
    durations = [max(1.5, audio_duration * len(s) / total_chars) for s in sentences]

    base_img = draw_character_bg(ai_image)
    subtitle_font = load_font(50)

    INTRO_DURATION = 0.8  # キャラクター登場フレーム（無音部分）

    tmp_dir = tempfile.mkdtemp(prefix="keiba_char_")
    try:
        clip_paths: list[str] = []

        # ---- イントロフレーム（キャラクターのみ・字幕なし） ----
        intro_path = os.path.join(tmp_dir, "frame_intro.png")
        base_img.save(intro_path, "PNG")
        intro_clip = os.path.join(tmp_dir, "clip_intro.mp4")
        run_ffmpeg([
            "ffmpeg", "-y", "-loop", "1", "-i", intro_path,
            "-t", str(INTRO_DURATION),
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            intro_clip,
        ])
        clip_paths.append(intro_clip)

        # ---- 各セリフフレーム ----
        MAX_CHARS_PER_LINE = 17
        for idx, (sentence, duration) in enumerate(zip(sentences, durations)):
            frame_img = base_img.copy()
            fdraw = ImageDraw.Draw(frame_img)

            lines = textwrap.wrap(sentence, width=MAX_CHARS_PER_LINE) or [sentence]
            line_h = 60
            box_h = len(lines) * line_h + 48
            box_top = VIDEO_HEIGHT - box_h - 110

            # 吹き出し背景
            fdraw.rounded_rectangle([55, box_top, VIDEO_WIDTH - 55, VIDEO_HEIGHT - 85],
                                     radius=28, fill=(255, 255, 255))
            fdraw.rounded_rectangle([55, box_top, VIDEO_WIDTH - 55, VIDEO_HEIGHT - 85],
                                     radius=28, outline=(180, 100, 20), width=6)
            # 吹き出しの三角（キャラ方向）
            fdraw.polygon([(VIDEO_WIDTH // 2 - 22, box_top),
                            (VIDEO_WIDTH // 2 + 22, box_top),
                            (VIDEO_WIDTH // 2, box_top - 38)],
                           fill=(255, 255, 255))

            # テキスト描画
            for j, line in enumerate(lines):
                try:
                    bb = fdraw.textbbox((0, 0), line, font=subtitle_font)
                    tw = bb[2] - bb[0]
                except Exception:
                    tw = len(line) * 30
                tx = max((VIDEO_WIDTH - tw) // 2, 60)
                ty = box_top + 24 + j * line_h
                fdraw.text((tx, ty), line, font=subtitle_font,
                            fill=(60, 30, 10),
                            stroke_width=2, stroke_fill=(200, 150, 80))

            frame_path = os.path.join(tmp_dir, f"frame_{idx}.png")
            frame_img.save(frame_path, "PNG")
            clip_path = os.path.join(tmp_dir, f"clip_{idx}.mp4")
            run_ffmpeg([
                "ffmpeg", "-y", "-loop", "1", "-i", frame_path,
                "-t", f"{duration:.3f}",
                "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-r", str(FPS),
                clip_path,
            ])
            clip_paths.append(clip_path)

        # ---- エンディングフレーム「またね！」 ----
        ending_img = base_img.copy()
        edraw = ImageDraw.Draw(ending_img)
        bye_font = load_font(96)
        bye_text = "またね！"
        try:
            bb = edraw.textbbox((0, 0), bye_text, font=bye_font)
            tw = bb[2] - bb[0]
        except Exception:
            tw = len(bye_text) * 58
        edraw.text(((VIDEO_WIDTH - tw) // 2, VIDEO_HEIGHT - 300), bye_text,
                    font=bye_font, fill=(200, 40, 20),
                    stroke_width=6, stroke_fill=(255, 220, 0))
        ending_path = os.path.join(tmp_dir, "frame_ending.png")
        ending_img.save(ending_path, "PNG")
        ending_clip = os.path.join(tmp_dir, "clip_ending.mp4")
        ENDING_DURATION = 2.5
        run_ffmpeg([
            "ffmpeg", "-y", "-loop", "1", "-i", ending_path,
            "-t", str(ENDING_DURATION),
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            ending_clip,
        ])
        clip_paths.append(ending_clip)

        # ---- クリップ結合 ----
        concat_txt = os.path.join(tmp_dir, "concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            for cp in clip_paths:
                f.write(f"file '{os.path.abspath(cp)}'\n")

        silent_mp4 = os.path.join(tmp_dir, "silent.mp4")
        run_ffmpeg([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_txt, "-c", "copy", silent_mp4,
        ])

        # ---- 音声合成（BGMあれば追加） ----
        total_duration = INTRO_DURATION + sum(durations) + ENDING_DURATION
        narr_delay_ms = int(INTRO_DURATION * 1000)
        bgm_files = sorted(glob.glob(f"{BGM_DIR}/*.mp3") + glob.glob(f"{BGM_DIR}/*.m4a"))
        bgm_path = random.choice(bgm_files) if bgm_files else None

        if bgm_path:
            print(f"  BGM使用: {Path(bgm_path).name}")
            run_ffmpeg([
                "ffmpeg", "-y",
                "-i", silent_mp4,
                "-i", audio_path,
                "-stream_loop", "-1", "-i", bgm_path,
                "-filter_complex",
                (f"[1:a]adelay={narr_delay_ms}|{narr_delay_ms},"
                 f"apad=whole_dur={total_duration:.3f}[narr];"
                 f"[narr][2:a]amix=inputs=2:duration=first:weights=1 0.12[aout]"),
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac",
                output_path,
            ])
        else:
            run_ffmpeg([
                "ffmpeg", "-y",
                "-i", silent_mp4,
                "-i", audio_path,
                "-af", (f"adelay={narr_delay_ms}|{narr_delay_ms},"
                        f"apad=whole_dur={total_duration:.3f}"),
                "-c:v", "copy", "-c:a", "aac",
                output_path,
            ])

        size_mb = Path(output_path).stat().st_size / (1024 * 1024)
        print(f"  キャラクター動画生成完了: {output_path} ({size_mb:.1f} MB)")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    print("=== キャラクター動画生成 ===")

    if not should_generate():
        print("キャラクター動画の生成をスキップします。")
        sys.exit(0)

    script = random.choice(CHARACTER_SCRIPTS)
    print(f"選択スクリプト: {script}")

    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    audio_path = f"{OUTPUT_DIR}/character_audio.mp3"
    output_path = f"{OUTPUT_DIR}/character_video.mp4"
    script_path = f"{OUTPUT_DIR}/character_script.txt"

    # HuggingFace FLUX でキャラクター画像を AI 生成（トークン未設定時は Pillow フォールバック）
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    ai_image = generate_character_image_hf(hf_token) if hf_token else None
    if not hf_token:
        print("  HF_TOKEN 未設定 → Pillow フォールバックを使用します。")

    asyncio.run(generate_character_audio(script, audio_path))
    build_character_video(script, audio_path, output_path, ai_image=ai_image)
    Path(script_path).write_text(script, encoding="utf-8")
    print(f"\nキャラクター動画を生成しました: {output_path}")


if __name__ == "__main__":
    main()
