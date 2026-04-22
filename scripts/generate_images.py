#!/usr/bin/env python3
"""
generate_images.py - 競馬関連背景画像を取得する。

# ============================================================
# IMPORTANT: Pillow (PIL) は絶対に使用禁止。
# 画像の保存・変換はすべて ffmpeg で行うこと。
# from PIL import ... / import PIL と書いたら即削除。
# ============================================================

優先順位:
  1. Pixabay API（無料・実写競馬写真）
  2. HuggingFace Inference API（HF_TOKEN が設定されている場合）
  3. どちらも失敗 → エラー終了（フォールバックなし）

AI画像が1枚も取得できなかった場合は sys.exit(1) で失敗にする。
generate_video.py も同様に、画像がなければ失敗する。
"""

import io
import json
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

NEWS_JSON = "news.json"
ASSETS_DIR = Path("assets")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

PIXABAY_API_URL = "https://pixabay.com/api/"
HF_MODEL_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

PIXABAY_QUERIES = [
    # レース・アクション
    "horse racing", "horse race action", "thoroughbred racing",
    "jockey horse race", "horse racing finish line", "horse racing track",
    "horse galloping race", "horse racing winner", "horse racing crowd",
    "horse sprint race", "horse racing start gate", "horse racing hurdle",
    "horse racing pack", "horse racing photo finish", "horse race neck neck",
    # ポートレート・クローズアップ
    "thoroughbred horse", "racehorse portrait", "horse face close up",
    "horse eye closeup", "horse mane flowing", "horse profile outdoor",
    "horse running field", "horse silhouette sunset", "racehorse muscular",
    "horse dramatic sky", "horse power gallop",
    # パドック・厩舎・調教
    "horse paddock", "horse stable", "horse grooming jockey",
    "horse morning training", "horse workout track", "horse trainer",
    "horse tack saddle", "horse parade ring", "horse walking handler",
    # 競馬場・施設
    "horse racetrack grandstand", "racetrack aerial view",
    "horse racing venue", "turf course horse", "horse racing stadium",
    "horse racing fans", "horse racing trophy", "jockey celebration",
    # 光・雰囲気
    "horse racing sunset", "horse racing sunrise", "horse racing fog",
    "horse racing rain", "horse racing night lights", "horse silhouette dusk",
    "horse racing golden hour", "horse race dramatic clouds",
    # 一般的な馬の美しさ
    "horse nature landscape", "wild horse running", "horse field green",
    "horse farm countryside", "horse outdoor freedom", "horse misty morning",
    "horse jumping fence", "equestrian sport",
]

DEFAULT_PROMPTS = [
    # レースアクション
    "cinematic photo of horses racing at full gallop on sunlit racecourse, dust and motion, dramatic lighting, high quality",
    "cinematic photo of jockey riding thoroughbred horse in race, motion blur, intense competition, high quality",
    "cinematic photo of horse race finish line photo finish, crowd cheering, dramatic moment, high quality",
    "cinematic photo of horses breaking from starting gate, explosive power, dramatic action, high quality",
    "cinematic photo of horse racing pack thundering down the stretch, vibrant colors, high quality",
    "cinematic photo of winning horse crossing finish line, jockey celebrating, triumphant, high quality",
    "cinematic photo of racehorses and jockeys in tight pack on turn, dirt flying, high quality",
    "cinematic photo of racehorse head-on racing toward camera, power and speed blur, high quality",
    "cinematic photo of horse racing at dusk, long shadows, golden light on track, high quality",
    "cinematic photo of horse race aerial view, colorful jockey silks, dynamic composition, high quality",
    "cinematic photo of lone racehorse leading by lengths, jockey pumping fist, high quality",
    "cinematic photo of horse racing in rain, water spray, dramatic wet atmosphere, high quality",
    "cinematic photo of horses neck and neck in final furlong, intense duel, high quality",
    "cinematic photo of horse racing jockey in colorful silks on tight turn, high quality",
    "cinematic photo of racehorses thundering past finish post, winner emerges, exhilaration, high quality",
    "cinematic photo of racehorses from trackside ground level, speed blur, close perspective, high quality",
    "cinematic photo of horse racing from behind the pack, rhythm of hooves, high quality",
    "cinematic photo of race start moment, horses lunging forward, explosive energy, high quality",
    "cinematic photo of horse racing wide angle full field, grandstand backdrop, high quality",
    "cinematic photo of racehorses with motion-frozen hooves midair, peak action, high quality",
    # ポートレート
    "cinematic photo of majestic thoroughbred horse portrait, soulful eyes, dramatic lighting, high quality",
    "cinematic photo of racehorse in paddock, muscular build, gleaming coat, professional photography, high quality",
    "cinematic photo of horse face close-up, powerful noble expression, shallow depth of field, high quality",
    "cinematic photo of thoroughbred horse profile, racetrack background, golden hour light, high quality",
    "cinematic photo of horse mane flowing in wind, running free, dramatic cloud sky, high quality",
    "cinematic photo of racehorse eye close-up, reflection of racetrack, intense focus, high quality",
    "cinematic photo of horse silhouette against dramatic sunset sky, powerful noble stance, high quality",
    "cinematic photo of racehorse sweating after race, steaming coat, champion spirit, high quality",
    "cinematic photo of racehorse in winner's enclosure with flowers, champion moment, high quality",
    "cinematic photo of horse breathing hard post-race, dramatic close-up, warrior, high quality",
    "cinematic photo of thoroughbred horse running in open field, freedom and power, high quality",
    "cinematic photo of horse nose crossing finish line, ultimate drama, champion, high quality",
    "cinematic photo of jockey standing in stirrups over finish line, triumph fist pump, high quality",
    "cinematic photo of two thoroughbred horses together in paddock, majestic comparison, high quality",
    "cinematic photo of young racehorse on green paddock, morning dew light, peaceful, high quality",
    # 調教・パドック
    "cinematic photo of horse morning training on racetrack, sunrise, misty atmosphere, high quality",
    "cinematic photo of jockey training thoroughbred at dawn, golden rim light, racetrack, high quality",
    "cinematic photo of racehorse galloping in training session, trainer timing with stopwatch, high quality",
    "cinematic photo of horse workout track early morning, fog rolling in, atmospheric, high quality",
    "cinematic photo of horse and jockey canter to post, anticipation, pre-race calm, high quality",
    "cinematic photo of racehorse doing speed work on training track, power and focus, high quality",
    "cinematic photo of horse in stable box, warm amber lighting, peaceful animal at rest, high quality",
    "cinematic photo of racehorse being brushed in stable, grooming ritual, bond with handler, high quality",
    "cinematic photo of horses walking in paddock ring, crowd watching, pre-race ritual, high quality",
    "cinematic photo of jockey weighing in after race, tradition of racing, backstage moment, high quality",
    "cinematic photo of trainer giving jockey final instructions, intense focus, pre-race drama, high quality",
    "cinematic photo of horse being led to starting gate, final moments of calm, high quality",
    "cinematic photo of racehorse swimming pool training, aquatic exercise, athletic recovery, high quality",
    "cinematic photo of horses on horse walker, post-race cooldown, training facility, high quality",
    "cinematic photo of horse tack saddle and bridle preparation, race day ritual, high quality",
    # 競馬場・施設
    "cinematic photo of racetrack grandstand full of excited crowd on big race day, high quality",
    "cinematic photo of horses parading past grandstand, race day pageantry and tradition, high quality",
    "cinematic photo of horse racing winners circle ceremony, trophy presentation, crowd, high quality",
    "cinematic photo of racetrack from above, perfect green turf oval, white rails, high quality",
    "cinematic photo of starting gate mechanism, dramatic metal structure, race day tension, high quality",
    "cinematic photo of racecourse turf green and lush, morning dew, pristine condition, high quality",
    "cinematic photo of racing crowd fans cheering finish, emotion and excitement, high quality",
    "cinematic photo of racetrack empty at dawn, peaceful before the storm, misty morning, high quality",
    "cinematic photo of horse racing grandstand lit at dusk, golden hour transition, high quality",
    "cinematic photo of race day atmosphere, binoculars and racing form, tradition, high quality",
    # 光・雰囲気
    "cinematic photo of horse racing silhouette against dramatic storm clouds, epic power, high quality",
    "cinematic photo of racehorses in morning fog workout, ethereal misty atmosphere, high quality",
    "cinematic photo of horses racing with sun directly behind, backlit rim lighting halo, high quality",
    "cinematic photo of horse racing with rainbow in background, lucky symbol, vibrant, high quality",
    "cinematic photo of racetrack at magic hour, purple and orange sky, last race of day, high quality",
    "cinematic photo of horse racing motion blur artistic, impression of pure speed, high quality",
    "cinematic photo of horse racing dust cloud kicked up, earth power drama, high quality",
    "cinematic photo of racehorse reflected in puddle on track, artistic mirror image, high quality",
    "cinematic photo of racehorses in golden hour backlight, silhouettes and rim glow, high quality",
    "cinematic photo of horse racing abstract freeze frame, crystalline moment of power, high quality",
    "cinematic photo of racehorses in misty autumn forest track, seasonal beauty, high quality",
    "cinematic photo of horse racing under dramatic cumulus clouds, natural drama, high quality",
    "cinematic photo of horse galloping on beach at sunrise, freedom and power, high quality",
    "cinematic photo of racehorse nose to nose rival duel, ultimate close finish, high quality",
    "cinematic photo of horse racing black and white high contrast, classic sport drama, high quality",
    # ナイトレース
    "cinematic photo of night horse racing under floodlights, dramatic artificial light, high quality",
    "cinematic photo of horse racing twilight track, purple sky, floodlights activating, high quality",
    "cinematic photo of racehorse under spotlight in winners circle at night, champion, high quality",
    "cinematic photo of racetrack lit up at night, crowd in stadium lights, electric, high quality",
    "cinematic photo of horse racing under lights, speed and drama, night race energy, high quality",
    "cinematic photo of racecourse grandstand illuminated at dusk, golden-purple sky, high quality",
    "cinematic photo of nighttime track with horse silhouettes against bright floodlights, high quality",
    "cinematic photo of racing horse with light trails long exposure, artistic night shot, high quality",
    "cinematic photo of horse racing fire torches track boundary, festival atmosphere, high quality",
    "cinematic photo of jockeys under floodlights post-race, colorful silks glistening, high quality",
    # 日本・アジア競馬
    "cinematic photo of horse racing Japanese racetrack, cherry blossoms spring atmosphere, high quality",
    "cinematic photo of Japan Derby race crowd Tokyo racecourse, massive audience excitement, high quality",
    "cinematic photo of Japanese jockey in silks, Tokyo racecourse background, high quality",
    "cinematic photo of horse racing Japan scenic mountains background, beautiful racecourse, high quality",
    "cinematic photo of Japanese racehorse training Hokkaido farm, cold morning mist, high quality",
    "cinematic photo of Japanese racetrack autumn red leaves, scenic horse race, high quality",
    "cinematic photo of JRA Tokyo racecourse landscape, horse parade grandeur, high quality",
    "cinematic photo of Japan horse racing winter scene snow distant, Arima atmosphere, high quality",
    "cinematic photo of horse racing Japan green turf summer, vibrant energy, high quality",
    "cinematic photo of Japan racetrack wide shot with city skyline background, modern racing, high quality",
]


def get_prompts_from_gemini(api_keys: list[str], news_items: list[dict], n: int = 4) -> list[str]:
    """Geminiテキストモデルで画像プロンプトを生成（全キー失敗時はデフォルト使用）"""
    item = news_items[0] if news_items else {}
    title = item.get("title", "")
    body = item.get("body", item.get("summary", ""))[:300]
    prompt_text = (
        f"以下の競馬ニュースの内容に合った、AI画像生成用の英語プロンプトを{n}つ作成してください。"
        "競馬場・馬・騎手・レースの雰囲気が伝わるシーンを描写してください。"
        "各プロンプトは「cinematic photo of [描写], horse racing, dramatic lighting, high quality」"
        "の形式で50語以内。JSON配列で返してください。\n\n"
        f"タイトル: {title}\n本文: {body}"
    )
    url = f"{GEMINI_API_BASE}/gemini-2.5-flash:generateContent"
    for api_key in api_keys:
        key_label = f"***{api_key[-4:]}"
        try:
            r = requests.post(
                url,
                json={"contents": [{"parts": [{"text": prompt_text}]}]},
                params={"key": api_key},
                timeout=30,
            )
            if r.status_code == 429:
                print(f"  [警告] key={key_label} 429 クォータ超過。20秒待機後に次のキーへ。", flush=True)
                time.sleep(20)
                continue
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = text.replace("```json", "").replace("```", "").strip()
            prompts = json.loads(text)
            if isinstance(prompts, list) and len(prompts) >= n:
                result = []
                for it in prompts:
                    if isinstance(it, str):
                        result.append(it)
                    elif isinstance(it, dict):
                        val = next((it[k] for k in ("prompt", "text", "description", "content") if k in it), None)
                        if val is None and it:
                            val = str(list(it.values())[0])
                        if val:
                            result.append(str(val))
                    else:
                        result.append(str(it))
                if len(result) >= n:
                    print(f"  Geminiプロンプト生成成功 (key={key_label}): {len(result)}件", flush=True)
                    return result[:n]
        except Exception as e:
            safe = str(e).replace(api_key, "***") if api_key else str(e)
            print(f"  [警告] key={key_label} プロンプト生成失敗: {safe}", flush=True)
    print("  [警告] 全キーでプロンプト生成失敗。デフォルトプロンプトを使用します。", flush=True)
    return random.sample(DEFAULT_PROMPTS, min(n, len(DEFAULT_PROMPTS)))


def save_image_bytes(content: bytes, filepath: str) -> bool:
    """
    画像バイト列をffmpegでJPEGに変換して保存する。
    Pillow は絶対に使用しない。ffmpeg のみで変換・バリデーションを行う。
    """
    if len(content) < 1000:
        print(f"    [エラー] 画像データが小さすぎます ({len(content)} bytes)", flush=True)
        return False

    tmp_path = filepath + ".download"
    try:
        Path(tmp_path).write_bytes(content)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_path, "-frames:v", "1", "-q:v", "2", filepath],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and Path(filepath).exists() and Path(filepath).stat().st_size > 1000:
            return True
        print(f"    [エラー] ffmpegで画像変換失敗", flush=True)
        return False
    except Exception as e:
        print(f"    [エラー] 画像保存失敗: {e}", flush=True)
        return False
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def generate_via_pixabay(api_key: str, query: str, filepath: str) -> bool:
    """Pixabay API で競馬写真を取得して保存する。"""
    EXCLUDE_TAGS = {"zebra", "donkey", "mule", "pony", "ass"}
    page = random.randint(1, 5)
    params = {
        "key": api_key,
        "q": query,
        "image_type": "photo",
        "category": "animals",
        "min_width": 640,
        "per_page": 100,
        "page": page,
        "order": "popular",
        "safesearch": "true",
    }
    try:
        r = requests.get(PIXABAY_API_URL, params=params, timeout=30)
        print(f"    [Pixabay] status={r.status_code} query='{query}' page={page}", flush=True)
        if r.status_code != 200:
            print(f"    エラー: {r.status_code} {r.text[:200]}", flush=True)
            return False
        hits = r.json().get("hits", [])
        if not hits:
            print(f"    [Pixabay] 該当画像なし: {query}", flush=True)
            return False
        hits = [
            h for h in hits
            if not EXCLUDE_TAGS & {t.strip().lower() for t in h.get("tags", "").split(",")}
        ]
        if not hits:
            print(f"    [Pixabay] フィルター後に該当画像なし: {query}", flush=True)
            return False
        hit = random.choice(hits)
        img_url = hit.get("webformatURL") or hit.get("largeImageURL")
        if not img_url:
            print(f"    [Pixabay] 画像URL取得失敗", flush=True)
            return False
        img_r = requests.get(img_url, timeout=30)
        if img_r.status_code == 200 and len(img_r.content) > 1000:
            if save_image_bytes(img_r.content, filepath):
                size_kb = len(img_r.content) // 1024
                print(f"    Pixabay成功: {filepath} ({size_kb}KB)", flush=True)
                return True
        else:
            print(f"    [Pixabay] 画像DL失敗: status={img_r.status_code}", flush=True)
    except Exception as e:
        print(f"    例外: {type(e).__name__}: {e}", flush=True)
    return False


def generate_via_huggingface(hf_tokens: list[str], prompt: str, filepath: str) -> bool:
    """HuggingFace Inference API (FLUX.1-schnell) で画像生成"""
    payload = {"inputs": prompt}
    for token_idx, hf_token in enumerate(hf_tokens):
        token_label = f"token[{token_idx + 1}/{len(hf_tokens)}]"
        headers = {"Authorization": f"Bearer {hf_token}"}
        for attempt in range(3):
            try:
                r = requests.post(HF_MODEL_URL, headers=headers, json=payload, timeout=120)
                print(f"    [HF] {token_label} status={r.status_code}", flush=True)
                if r.status_code == 200 and len(r.content) > 1000:
                    if save_image_bytes(r.content, filepath):
                        size_kb = len(r.content) // 1024
                        print(f"    HF成功: {filepath} ({size_kb}KB)", flush=True)
                        return True
                elif r.status_code == 402:
                    print(f"    [HF] {token_label} クレジット枯渇(402)。次のトークンへ。", flush=True)
                    break
                elif r.status_code == 403:
                    print(f"    [HF] {token_label} 権限不足(403)。次のトークンへ。", flush=True)
                    break
                elif r.status_code == 503:
                    wait = 30 * (attempt + 1)
                    print(f"    モデル読み込み中... {wait}秒待機", flush=True)
                    time.sleep(wait)
                else:
                    print(f"    エラー: {r.status_code} {r.text[:200]}", flush=True)
                    break
            except Exception as e:
                print(f"    例外: {type(e).__name__}: {e}", flush=True)
                break
    return False


def main() -> None:
    print("=== AI画像生成開始 ===", flush=True)
    ASSETS_DIR.mkdir(exist_ok=True)

    gemini_keys = [
        k for k in [
            os.environ.get("GEMINI_API_KEY", ""),
            os.environ.get("GEMINI_API_KEY_2", ""),
            os.environ.get("GEMINI_API_KEY_3", ""),
        ] if k
    ]
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "")
    hf_tokens = [
        k for k in [
            os.environ.get("HF_TOKEN", ""),
            os.environ.get("HF_TOKEN_2", ""),
            os.environ.get("HF_TOKEN_3", ""),
        ] if k
    ]

    print(f"Gemini APIキー: {len(gemini_keys)} 件ロード", flush=True)
    print(f"Pixabay: {'あり' if pixabay_key else 'なし（PIXABAY_API_KEY未設定）'}", flush=True)
    print(f"HuggingFace: {len(hf_tokens)} トークンロード", flush=True)

    if not pixabay_key and not hf_tokens:
        print("[エラー] PIXABAY_API_KEY も HF_TOKEN も未設定。AI画像を取得できません。", flush=True)
        sys.exit(1)

    try:
        news_items = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    except Exception:
        news_items = []

    IMAGES_PER_VIDEO = 3
    n_videos = max(1, len(news_items))
    n_images = n_videos * IMAGES_PER_VIDEO
    print(f"  ニュース{n_videos}件 × {IMAGES_PER_VIDEO}枚 = {n_images}枚を生成します", flush=True)

    print("  プロンプト生成中...", flush=True)
    prompts = get_prompts_from_gemini(gemini_keys, news_items, n=n_images) if gemini_keys else random.sample(DEFAULT_PROMPTS, min(n_images, len(DEFAULT_PROMPTS)))
    for i, p in enumerate(prompts, 1):
        print(f"    [{i}] {p[:80]}", flush=True)

    def generate_one(args):
        i, prompt = args
        out_path = str(ASSETS_DIR / f"ai_{i}.jpg")
        query = random.choice(PIXABAY_QUERIES)
        print(f"\n  [{i}/{n_images}] 画像取得中...", flush=True)

        if pixabay_key:
            print(f"  [{i}] → Pixabay を試行 (query='{query}')", flush=True)
            if generate_via_pixabay(pixabay_key, query, out_path):
                return i, True
            print(f"  [{i}] → Pixabay失敗。", flush=True)

        if hf_tokens:
            print(f"  [{i}] → HuggingFace にフォールバック", flush=True)
            if generate_via_huggingface(hf_tokens, prompt, out_path):
                return i, True

        print(f"  [{i}] → 全手段で画像取得失敗", flush=True)
        return i, False

    failed = []
    with ThreadPoolExecutor(max_workers=min(n_images, 6)) as executor:
        futures = {executor.submit(generate_one, (i, p)): i for i, p in enumerate(prompts, 1)}
        for future in as_completed(futures):
            i, ok = future.result()
            if not ok:
                failed.append(i)

    ai_files = sorted(ASSETS_DIR.glob("ai_*.jpg"))
    print(f"\n=== 結果: {n_images - len(failed)}/{n_images} 枚生成 ===", flush=True)
    print(f"  生成ファイル: {[f.name for f in ai_files]}", flush=True)

    if not ai_files:
        print("[エラー] AI画像が1枚も取得できませんでした。動画生成を中止します。", flush=True)
        sys.exit(1)

    if failed:
        print(f"[警告] {len(failed)}枚の取得失敗。取得できた{len(ai_files)}枚で動画を生成します。", flush=True)


if __name__ == "__main__":
    main()
