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
    "競馬の距離の種類",
    "騎手の体重制限と減量騎手",
    "馬のひづめのケア",
    "スターティングゲートの仕組み",
    "馬の年齢の数え方",
    "競馬場の芝とダートの違い",
    "騎手のムチの使い方ルール",
    "サラブレッドの起源と歴史",
    "競馬の賞金の仕組み",
    "調教師の役割と仕事",
    "馬の餌と栄養管理",
    "競馬の着差の測り方",
    "競馬場のコース設計の違い",
    "1番人気の勝率と複勝率",
    "競馬のオッズの決まり方",
    "馬の感情とコミュニケーション",
    "騎手のポジションと馬の走り方",
    "競馬のスタートを決める抽選",
    "馬の毛色の種類と名前",
]

TRIVIA_FALLBACK: dict[str, str] = {
    "馬の睡眠と休息": (
        "みなさんこんにちは！ウマコです！今日は馬の睡眠についての豆知識をご紹介します。"
        "実は馬は立ったまま眠ることができるんです。後ろ脚の関節をロックする仕組みがあって、倒れずに熟睡できます。"
        "でも深い眠りのときは横になることもありますよ。一日の睡眠時間はなんと2〜3時間程度。とっても短いんです。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "競馬の距離の種類": (
        "みなさんこんにちは！ウマコです！今日は競馬の距離についての豆知識をご紹介します。"
        "競馬のレースは距離によって短距離・マイル・中距離・長距離に分かれます。"
        "1200メートル以下が短距離、1400〜1600メートルがマイル、1700〜2100メートルが中距離、それ以上が長距離です。"
        "距離が変わると求められる能力も大きく変わるので、馬によって得意な距離が違うんですよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "騎手の体重制限と減量騎手": (
        "みなさんこんにちは！ウマコです！今日は騎手の体重についての豆知識をご紹介します。"
        "競馬では馬が背負う重さ（斤量）が決まっていて、騎手はその斤量に合わせる必要があります。"
        "通常の斤量は55〜57キロ程度。騎手本体は50キロ以下が理想とされています。"
        "デビューから5年未満の若い騎手は減量騎手と呼ばれ、斤量が2〜3キロ軽くなる特典があるんですよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "馬のひづめのケア": (
        "みなさんこんにちは！ウマコです！今日は馬のひづめについての豆知識をご紹介します。"
        "馬のひづめは人間の爪と同じ素材でできていて、約6週間で少しずつ伸びます。"
        "競走馬には鉄製の蹄鉄が装着されていて、装蹄師という専門家が定期的に交換します。"
        "ひづめは馬の健康のバロメーター。ひづめが悪いと走りにも大きく影響するんです。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "スターティングゲートの仕組み": (
        "みなさんこんにちは！ウマコです！今日はスターティングゲートの豆知識をご紹介します。"
        "スターティングゲートは全馬が同時に公平にスタートできるように作られた装置です。"
        "前扉と後扉が同時に開く仕組みで、スタート係の係員が各馬を丁寧に誘導して入れます。"
        "実はゲートが苦手な馬もいて、ゲート練習が必要な馬はレースに出られないこともあるんですよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "馬の年齢の数え方": (
        "みなさんこんにちは！ウマコです！今日は馬の年齢の豆知識をご紹介します。"
        "サラブレッドは誕生日に関係なく、毎年1月1日に一斉に年齢が上がります。"
        "12月31日生まれでも翌日の1月1日には2歳になる計算です。これをまとめ年齢といいます。"
        "そのため早生まれ（1〜3月生まれ）の馬は有利とされていて、繁殖シーズンも2〜3月が人気なんです。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "競馬場の芝とダートの違い": (
        "みなさんこんにちは！ウマコです！今日は芝とダートの違いについての豆知識をご紹介します。"
        "芝コースは天然の草の上を走るコース。クッション性があり馬の脚への負担が少ないのが特徴です。"
        "ダートコースは砂や土のコース。雨が降っても影響を受けにくく、力強い走りが求められます。"
        "日本の重賞レースのほとんどは芝コースで行われますが、アメリカや中東ではダートが主流ですよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "騎手のムチの使い方ルール": (
        "みなさんこんにちは！ウマコです！今日は騎手のムチについての豆知識をご紹介します。"
        "競馬ではムチの使用に厳しいルールがあります。JRAのルールでは1レースで使える回数に制限があります。"
        "また馬の肩より後ろの特定の部位にしか使ってはいけません。違反すると制裁を受けることもあります。"
        "ムチは馬を傷つけるためではなく、集中させて走る気を引き出すために使うものなんですよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "サラブレッドの起源と歴史": (
        "みなさんこんにちは！ウマコです！今日はサラブレッドの歴史についての豆知識をご紹介します。"
        "世界中の現役サラブレッドは、3頭のアラブ馬を祖先に持つといわれています。"
        "ゴドルフィンアラビアン、ダーレーアラビアン、バイアリーターク。この3頭から全員が繋がっているんです。"
        "17世紀にイギリスで品種改良が始まり、今では世界で最もスピードが速い馬として君臨しています。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "競馬の賞金の仕組み": (
        "みなさんこんにちは！ウマコです！今日は競馬の賞金についての豆知識をご紹介します。"
        "競馬の賞金はレースのグレードによって大きく異なります。G1レースの1着賞金は1億〜3億円にもなります。"
        "賞金は1着だけでなく、2〜5着にも支払われます。ただし騎手と馬主と調教師で分配されるんですよ。"
        "馬主が受け取るのは賞金の約80パーセント。騎手は5〜10パーセントほどが相場です。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "調教師の役割と仕事": (
        "みなさんこんにちは！ウマコです！今日は調教師についての豆知識をご紹介します。"
        "調教師は競走馬のトレーナーです。毎朝早起きして馬の状態を確認し、走る練習を指揮します。"
        "どのレースに出走するか判断したり、騎手を選んだりするのも調教師の大切な仕事です。"
        "JRAの調教師になるには試験に合格する必要があり、その倍率はとても高いんですよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "馬の餌と栄養管理": (
        "みなさんこんにちは！ウマコです！今日は馬の食事についての豆知識をご紹介します。"
        "競走馬は1日に乾草を5〜8キロ、配合飼料を3〜5キロも食べます。人間よりずっと大食いですね。"
        "主食は牧草やオーツ麦などの穀物。ビタミンやミネラルも細かく管理されています。"
        "レース前日や当日は消化不良を防ぐため食事量を減らす調整も行われますよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "競馬の着差の測り方": (
        "みなさんこんにちは！ウマコです！今日は着差の測り方についての豆知識をご紹介します。"
        "競馬の着差は馬の鼻先から次の馬の鼻先までの距離で表します。"
        "ハナ差はほんのわずかな差。クビ差は馬の首の長さ分。アタマ差はその中間くらいです。"
        "1馬身は約2.4メートルで、これ以上開くと2馬身、3馬身と数えていきます。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "競馬場のコース設計の違い": (
        "みなさんこんにちは！ウマコです！今日は競馬場のコース設計についての豆知識をご紹介します。"
        "日本の競馬場はそれぞれコースの形や広さが違います。東京競馬場は広くて直線が長い約526メートル。"
        "一方の小倉競馬場は直線が短く小回りのコース。得意な馬のタイプが変わってきます。"
        "坂があるコースとないコースでも展開が大きく変わるので、コース適性の読みがカギになりますよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "1番人気の勝率と複勝率": (
        "みなさんこんにちは！ウマコです！今日は1番人気の勝率についての豆知識をご紹介します。"
        "競馬で1番人気の馬が勝つ確率は約33パーセントといわれています。3回に1回は来る計算です。"
        "複勝（3着以内）になる確率は約60パーセントとさらに高くなります。"
        "ただし1番人気ばかり買い続けると長期的には損をします。競馬はやっぱり奥が深いですね。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "競馬のオッズの決まり方": (
        "みなさんこんにちは！ウマコです！今日はオッズの仕組みについての豆知識をご紹介します。"
        "競馬のオッズはファンが購入した馬券の総額をもとに自動的に計算されます。"
        "たくさん買われている馬ほどオッズが低くなり、あまり注目されていない馬のオッズは高くなります。"
        "馬券の売上から約25パーセントが控除されてから払戻し額が計算されるんですよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "馬の感情とコミュニケーション": (
        "みなさんこんにちは！ウマコです！今日は馬の感情についての豆知識をご紹介します。"
        "馬はとても感情豊かな動物です。耳の向きや尻尾の動きで気持ちが分かります。"
        "耳が前を向いていると興味や注意、後ろに倒れていると怒りや不安のサインです。"
        "騎手や調教師と信頼関係を築くと馬の走りにも大きく影響するんですよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "騎手のポジションと馬の走り方": (
        "みなさんこんにちは！ウマコです！今日は騎手のポジションについての豆知識をご紹介します。"
        "競馬の騎手は鐙（あぶみ）に立つような姿勢で乗ります。これをモンキー乗りといいます。"
        "重心を低くして空気抵抗を減らし、馬の動きに合わせて体を前後に動かすことで馬が走りやすくなります。"
        "実はこのフォームが普及したのは20世紀初頭のこと。それまでは直立した姿勢で乗っていたんですよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
    "馬の毛色の種類と名前": (
        "みなさんこんにちは！ウマコです！今日は馬の毛色についての豆知識をご紹介します。"
        "競走馬の毛色には様々な種類があります。赤みがかった茶色が「鹿毛」、黒に近い濃い茶色が「黒鹿毛」。"
        "灰色や白っぽい色は「芦毛」といって、年を取るほど白くなっていく不思議な毛色です。"
        "毛色は馬の個性の一つ。ファンに愛される馬の特徴にもなっていますよ。"
        "今日の豆知識はここまで！また次回お会いしましょう！ウマコでした！"
    ),
}

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
        # スクリプトが短すぎる or 締めの言葉がない場合はフォールバック使用
        if len(script) >= 150 and "ウマコでした" in script:
            return script
        print(f"[豆知識] Geminiスクリプトが不完全({len(script)}文字)。フォールバックを使用します。")

    fallback = TRIVIA_FALLBACK.get(topic)
    if fallback:
        return fallback
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
