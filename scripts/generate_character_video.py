#!/usr/bin/env python3
"""
generate_character_video.py - ウマコ キャラクタービデオ生成スクリプト

1. ウマコ画像生成（条件付き）→ assets/umako.jpg に保存
   - CHARACTER_VIDEO_INTERVAL 記事ごとに1回、または FORCE_CHARACTER_VIDEO=true のとき
   - 画像生成: Pollinations.ai（無料・キー不要）→ HuggingFace フォールバック
   - generate_video.py が読み込んでニュース動画右下にオーバーレイ表示する
2. 豆知識動画生成（同じ条件）→ output/character_video.mp4
   - Gemini で豆知識スクリプト生成 → edge_tts で音声 → ffmpeg で動画化
失敗時はフォールバックなし（ウマコなしで動画生成を続行する）。
"""

import asyncio
import glob
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.parse
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

ASSETS_DIR = "assets"
OUTPUT_DIR = "output"
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"
HF_MODEL_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
UMAKO_PATH = f"{ASSETS_DIR}/umako.jpg"
CHAR_VIDEO_PATH = f"{OUTPUT_DIR}/character_video.mp4"
CHAR_SCRIPT_PATH = f"{OUTPUT_DIR}/character_script.txt"
CHAR_AUDIO_PATH = f"{OUTPUT_DIR}/character_audio.mp3"
POSTED_IDS_FILE = "posted_ids.txt"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
FONT_SIZE = 60
LINE_SPACING = 10
SUBTITLE_CENTER_Y = 960
OUTLINE_WIDTH = 8

UMAKO_VOICE = "ja-JP-NanamiNeural"
UMAKO_RATE = "+25%"
UMAKO_PITCH = "+15Hz"

UMAKO_BASE = (
    "cute kawaii chibi anime horse girl mascot character, "
    "brown horse ears and flowing mane, red and yellow striped jockey helmet, "
    "big expressive sparkling eyes, simple clean pastel background, "
    "full body illustration, centered, high quality digital art, no text"
)

UMAKO_POSES = [
    "holding microphone, leaning forward enthusiastically, news reporter pose, excited smile",
    "pointing finger upward, explaining with confident cheerful expression, teaching pose",
    "sitting cross-legged, reading from clipboard, thoughtful face, glasses on nose",
    "waving both hands at camera, big cheerful grin, welcoming gesture",
    "hand on chin, curious thinking pose, tilted head, wondering expression",
    "thumbs up with one hand, other hand holding notepad, celebrating good news",
    "arms wide open, surprised excited reaction, eyes wide, mouth open in awe",
    "standing with arms crossed, confident nodding expression, professional pose",
]

TRIVIA_TOPICS = [
    "馬の睡眠と休息",
    "競馬の距離の種類（短距離・中距離・長距離）",
    "騎手の体重制限と減量騎手",
    "馬のひづめのケア",
    "スターティングゲートの仕組み",
    "馬の年齢の数え方（サラブレッドの生年月日）",
    "競馬場の芝とダートの違い",
    "騎手のムチの使い方ルール",
    "サラブレッドの起源と歴史",
    "競馬の賞金の仕組み",
    "調教師の役割と仕事",
    "馬の餌と栄養管理",
    "競馬の着差の測り方（ハナ差・クビ差・アタマ差）",
    "競馬場のコース設計の違い",
    "1番人気の勝率と複勝率",
    "競馬のオッズの決まり方",
    "馬の感情とコミュニケーション",
    "騎手のポジションと馬の走り方",
    "競馬のスタートを決める抽選",
    "馬の毛色の種類と名前",
]

GEMINI_MODELS = [
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.5-flash",
]


# ---------------------------------------------------------------------------
# 生成タイミング判定
# ---------------------------------------------------------------------------

def should_generate() -> bool:
    """ウマコ画像・豆知識動画を生成すべきか判定する。"""
    force = os.environ.get("FORCE_CHARACTER_VIDEO", "").lower() in ("true", "1", "yes")
    if force:
        print("[ウマコ] FORCE_CHARACTER_VIDEO=true のため強制生成します。")
        return True

    interval = int(os.environ.get("CHARACTER_VIDEO_INTERVAL", "10"))
    path = Path(POSTED_IDS_FILE)
    posted_count = 0
    if path.exists():
        ids = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        posted_count = len(ids)

    print(f"[ウマコ] 投稿済み記事数: {posted_count} / インターバル: {interval}")
    if posted_count > 0 and posted_count % interval == 0:
        print(f"[ウマコ] {interval}記事ごとの生成タイミングです。")
        return True

    print("[ウマコ] 今回はスキップします。")
    return False


# ---------------------------------------------------------------------------
# ウマコ画像生成
# ---------------------------------------------------------------------------

def generate_via_pollinations(prompt: str) -> bytes | None:
    """Pollinations.ai で画像を生成してバイト列を返す（無料・キー不要）。"""
    encoded = urllib.parse.quote(prompt)
    url = POLLINATIONS_URL.format(prompt=encoded) + "?width=512&height=512&model=flux&nologo=true"
    print(f"[ウマコ] Pollinations.ai にリクエスト中...")
    try:
        r = requests.get(url, timeout=120)
        print(f"[ウマコ] Pollinations HTTP {r.status_code} ({len(r.content)} bytes)")
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
        print(f"[ウマコ] Pollinations 失敗: {r.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"[ウマコ] Pollinations 例外: {e}", file=sys.stderr)
    return None


def generate_via_huggingface(prompt: str, hf_token: str) -> bytes | None:
    """HuggingFace Inference API (FLUX.1-schnell) で画像を生成してバイト列を返す。"""
    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {"inputs": prompt}
    for attempt in range(3):
        try:
            r = requests.post(HF_MODEL_URL, headers=headers, json=payload, timeout=120)
            print(f"[ウマコ] HF HTTP {r.status_code} ({len(r.content)} bytes)")
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
            elif r.status_code == 503:
                wait = 30 * (attempt + 1)
                print(f"[ウマコ] HF モデル読み込み中... {wait}秒待機")
                time.sleep(wait)
            else:
                print(f"[ウマコ] HF 失敗: {r.status_code} {r.text[:200]}", file=sys.stderr)
                break
        except Exception as e:
            print(f"[ウマコ] HF 例外: {e}", file=sys.stderr)
            break
    return None


def generate_umako_image(hf_token: str) -> bool:
    """ウマコ画像を生成して assets/umako.jpg に保存する。
    Pollinations.ai → HuggingFace の順にフォールバック。
    """
    pose = random.choice(UMAKO_POSES)
    prompt = f"{UMAKO_BASE}, {pose}"
    print(f"[ウマコ] 選択ポーズ: {pose}")

    content = generate_via_pollinations(prompt)

    if content is None and hf_token:
        print("[ウマコ] Pollinations 失敗 → HuggingFace にフォールバック")
        content = generate_via_huggingface(prompt, hf_token)

    if content is None:
        print("[ウマコ] 画像生成失敗。ウマコなしで動画生成を続行します。", file=sys.stderr)
        return False

    try:
        img = Image.open(io.BytesIO(content)).convert("RGB")
        Path(ASSETS_DIR).mkdir(exist_ok=True)
        img.save(UMAKO_PATH, "JPEG", quality=92)
        print(f"[ウマコ] 画像保存完了: {UMAKO_PATH} {img.size}")
        return True
    except Exception as e:
        print(f"[ウマコ] 画像保存失敗: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# 豆知識スクリプト生成（Gemini）
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str) -> str | None:
    api_keys = [
        k for k in [
            os.environ.get("GEMINI_API_KEY"),
            os.environ.get("GEMINI_API_KEY_2"),
            os.environ.get("GEMINI_API_KEY_3"),
        ] if k
    ]
    if not api_keys:
        print("[豆知識] GEMINI_API_KEY 未設定", file=sys.stderr)
        return None

    for api_key in api_keys:
        for model in GEMINI_MODELS:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={api_key}"
            )
            body = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.8, "maxOutputTokens": 800},
            }).encode("utf-8")
            try:
                import urllib.request
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    print(f"[豆知識] Gemini {model} 成功")
                    return text.strip()
            except Exception as e:
                print(f"[豆知識] {model}: {e}", file=sys.stderr)
    return None


def generate_trivia_script(topic: str) -> str:
    prompt = (
        f"あなたは競馬が大好きな馬と人間のハーフキャラクター「ウマコ」です。\n"
        f"今日の競馬豆知識テーマは「{topic}」です。\n"
        f"ウマコとして、視聴者に分かりやすく面白く競馬豆知識を紹介してください。\n"
        f"「みなさんこんにちは！ウマコです！」から始めて、豆知識を2〜3つ紹介し、\n"
        f"「今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！」で締めてください。\n"
        f"合計200〜350文字程度、句点（。）で区切った自然な日本語のナレーション原稿を書いてください。\n"
        f"記号や見出し、箇条書きは使わず、読み上げる文章のみを書いてください。"
    )
    script = _call_gemini(prompt)
    if script:
        script = re.sub(r"[#\*\-→•]", "", script)
        script = re.sub(r"\s{2,}", " ", script).strip()
        return script

    return (
        f"みなさんこんにちは！ウマコです！今日は「{topic}」についての豆知識をご紹介します。"
        f"競馬にはたくさんの豆知識が隠れています。"
        f"知れば知るほど競馬がもっと楽しくなりますよ！"
        f"今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    )


# ---------------------------------------------------------------------------
# TTS音声生成
# ---------------------------------------------------------------------------

async def _tts_async(text: str, output_path: str) -> None:
    import edge_tts
    tts = edge_tts.Communicate(text, voice=UMAKO_VOICE, rate=UMAKO_RATE, pitch=UMAKO_PITCH)
    await tts.save(output_path)


def generate_tts(text: str, output_path: str) -> bool:
    try:
        asyncio.run(_tts_async(text, output_path))
        if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            print(f"[ウマコTTS] 音声生成完了: {output_path}")
            return True
        print("[ウマコTTS] 音声ファイルが空です。", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[ウマコTTS] 音声生成失敗: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# 豆知識動画生成
# ---------------------------------------------------------------------------

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


def run_ffmpeg(cmd: list[str]) -> None:
    print(f"  $ {' '.join(cmd[:8])} ...")
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def get_audio_duration(audio_path: str) -> float:
    try:
        from mutagen.mp3 import MP3
        return MP3(audio_path).info.length
    except Exception:
        pass
    result = subprocess.run(["ffmpeg", "-i", audio_path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + s
    return 30.0


def build_character_video(script: str, audio_path: str, umako_img_path: str) -> bool:
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    font_path = find_japanese_font()
    try:
        font = ImageFont.truetype(font_path, FONT_SIZE) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    sentences = [s.strip() + "。" for s in script.split("。") if s.strip()]
    if not sentences:
        print("[ウマコ動画] セリフが空です。", file=sys.stderr)
        return False

    audio_duration = get_audio_duration(audio_path)
    total_chars = sum(len(s) for s in sentences)

    tmp_dir = tempfile.mkdtemp(prefix="umako_video_")
    try:
        umako_bg = Image.open(umako_img_path).convert("RGB")
        umako_bg = ImageOps.fit(umako_bg, (VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)

        clip_paths: list[str] = []
        for i, sentence in enumerate(sentences):
            duration = max(
                1.5,
                audio_duration * len(sentence) / total_chars if total_chars > 0
                else audio_duration / len(sentences),
            )

            bg = umako_bg.copy()
            overlay = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 150))
            img = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(img)

            max_chars = 14
            lines = textwrap.wrap(sentence, width=max_chars) or [sentence]
            line_height = FONT_SIZE + LINE_SPACING
            total_height = len(lines) * line_height
            start_y = SUBTITLE_CENTER_Y - total_height // 2

            for j, line in enumerate(lines):
                y = start_y + j * line_height
                try:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    text_w = bbox[2] - bbox[0]
                except Exception:
                    text_w = len(line) * (FONT_SIZE // 2)
                x = max((VIDEO_WIDTH - text_w) // 2, 20)
                try:
                    draw.text(
                        (x, y), line, font=font,
                        fill=(255, 235, 0),
                        stroke_width=OUTLINE_WIDTH,
                        stroke_fill=(0, 0, 0),
                    )
                except TypeError:
                    draw.text((x, y), line, font=font, fill=(255, 235, 0))

            frame_path = os.path.join(tmp_dir, f"frame_{i}.png")
            img.save(frame_path, "PNG")

            clip_path = os.path.join(tmp_dir, f"clip_{i}.mp4")
            run_ffmpeg([
                "ffmpeg", "-y", "-loop", "1", "-i", frame_path,
                "-t", f"{duration:.6f}",
                "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-r", str(FPS),
                clip_path,
            ])
            clip_paths.append(clip_path)
            print(f"  [ウマコ動画] フレーム{i}: {duration:.2f}秒")

        concat_txt = os.path.join(tmp_dir, "concat.txt")
        with open(concat_txt, "w") as f:
            for cp in clip_paths:
                f.write(f"file '{os.path.abspath(cp)}'\n")

        silent_mp4 = os.path.join(tmp_dir, "silent.mp4")
        run_ffmpeg([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_txt, "-c", "copy", silent_mp4,
        ])

        total_duration = sum(
            max(
                1.5,
                audio_duration * len(s) / total_chars if total_chars > 0
                else audio_duration / len(sentences),
            )
            for s in sentences
        )
        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", silent_mp4,
            "-i", audio_path,
            "-af", f"apad=whole_dur={total_duration:.3f}",
            "-c:v", "copy", "-c:a", "aac",
            CHAR_VIDEO_PATH,
        ])

        size_mb = Path(CHAR_VIDEO_PATH).stat().st_size / (1024 * 1024)
        print(f"[ウマコ動画] 生成完了: {CHAR_VIDEO_PATH} ({size_mb:.1f} MB)")
        return True

    except Exception as e:
        import traceback
        print(f"[ウマコ動画] 生成失敗: {e}", file=sys.stderr)
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== ウマコ画像・動画生成 ===")

    if not should_generate():
        sys.exit(0)

    hf_token = os.environ.get("HF_TOKEN", "").strip()

    # 1. ウマコ画像生成
    if not generate_umako_image(hf_token):
        print("[ウマコ] 画像生成失敗のため豆知識動画もスキップします。", file=sys.stderr)
        sys.exit(0)

    # 2. 豆知識動画生成
    print("[ウマコ] 豆知識動画の生成を開始します...")
    topic = random.choice(TRIVIA_TOPICS)
    print(f"[ウマコ] 豆知識テーマ: {topic}")

    script = generate_trivia_script(topic)
    print(f"[ウマコ] スクリプト ({len(script)}文字): {script[:80]}...")
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    Path(CHAR_SCRIPT_PATH).write_text(script, encoding="utf-8")
    Path(f"{OUTPUT_DIR}/character_topic.txt").write_text(topic, encoding="utf-8")

    if not generate_tts(script, CHAR_AUDIO_PATH):
        print("[ウマコ] TTS失敗。豆知識動画生成をスキップします。", file=sys.stderr)
        sys.exit(0)

    build_character_video(script, CHAR_AUDIO_PATH, UMAKO_PATH)
    sys.exit(0)


if __name__ == "__main__":
    main()
