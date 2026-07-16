#!/usr/bin/env python3
"""人気ニュース（news.json）を横型動画（1280×720・通常動画）として生成・投稿する。

generate_audio.py が output/script_N.txt / audio_N.mp3 / subtitles_N.ass を
生成済みであることを前提に、landscape_video.py のパイプラインでネイティブな
横型動画を生成し、#Shortsなしの通常動画としてアップロードする。

投稿済みIDは既存の posted_ids.txt に追記し、縦型ニュースパイプライン
（keiba_news.yml）との重複投稿を防ぐ。
"""

import datetime
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import landscape_video  # 横型動画のネイティブ生成パイプラインを再利用
import upload_landscape_youtube as uploader  # 認証・アップロード・サムネイル処理を再利用

from googleapiclient.discovery import build

JST = datetime.timezone(datetime.timedelta(hours=9))
NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
POSTED_IDS_FILE = "posted_ids.txt"
RESULT_FILE = "last_upload_result.txt"


def append_posted_id(entry_id: str) -> None:
    path = Path(POSTED_IDS_FILE)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if entry_id in existing.splitlines():
        return
    with path.open("a", encoding="utf-8") as f:
        f.write(entry_id + "\n")
    print(f"投稿済みIDを {POSTED_IDS_FILE} に追記: {entry_id}")


def build_title(item: dict) -> str:
    """記事見出しを先頭に置き、末尾にシリーズタグを付ける。

    旧形式「【話題】7月16日 〜」は先頭12文字が毎回同じで、フィードの
    省略表示では肝心の見出しが読めなかった。検索キーワードにもなる
    見出し本文を前方に置き、日付は概要欄へ移す。
    """
    base = item["title"].strip()
    views_str = landscape_video.format_views(int(item.get("views") or 0))
    tag = f"【{views_str}・話題の競馬ニュース】" if views_str else "【話題の競馬ニュース】"
    limit = 100 - len(tag)
    if len(base) > limit:
        base = base[: limit - 1] + "…"
    return base + tag


def build_description(item: dict) -> str:
    views = item.get("views", 0)
    now = datetime.datetime.now(JST)
    lead = "いま最も読まれている競馬ニュースをお届けします。"
    if views:
        lead = "いま最も読まれている競馬ニュースをお届けします（netkeibaアクセスランキングより）。"
    return (
        f"{lead}\n"
        f"{item['title']}\n"
        f"配信日: {now.year}年{now.month}月{now.day}日\n\n"
        f"#競馬 #競馬ニュース #JRA #keiba #競馬速報"
    )


def extract_frame_thumbnail(video_path: str, idx: int) -> str:
    """フォールバック: 横型動画の先頭フレームを抽出してサムネイルにする（リサイズ禁止）。"""
    thumb_path = f"{OUTPUT_DIR}/thumbnail_{idx}.jpg"
    result = subprocess.run([
        "ffmpeg", "-y", "-ss", "0.5", "-i", video_path,
        "-vframes", "1", "-q:v", "2", thumb_path,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [警告] サムネイル抽出失敗: {result.stderr[-200:]}", file=sys.stderr)
    return thumb_path


def write_result(lines: list[str]) -> None:
    header = [f"date: {datetime.datetime.utcnow().isoformat()}Z", "type: popular_news_landscape"]
    Path(RESULT_FILE).write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    print("\n".join(header + lines))


def main() -> None:
    print("=== 人気ニュース 横型動画投稿 ===")
    if not Path(NEWS_JSON).exists():
        print(f"[エラー] {NEWS_JSON} が見つかりません。", file=sys.stderr)
        sys.exit(1)
    news_items: list[dict] = json.loads(Path(NEWS_JSON).read_text(encoding="utf-8"))
    if not news_items:
        print("news.json が空です。スキップします。")
        return

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    all_creds, load_log = uploader.load_all_credentials()
    cred_idx = 0
    youtube = build("youtube", "v3", credentials=all_creds[cred_idx])

    font = landscape_video.find_font()
    result_lines: list[str] = []

    for idx, item in enumerate(news_items):
        if not Path(f"{OUTPUT_DIR}/script_{idx}.txt").exists():
            print(f"[{idx}] 脚本なし（生成スキップ記事）。スキップします。")
            continue

        # リポジトリにコミット済みの古いサムネイルが残っていると
        # landscape_video.generate_video が生成をスキップしてしまうため先に消す
        Path(f"{OUTPUT_DIR}/thumbnail_{idx}.jpg").unlink(missing_ok=True)

        print(f"\n--- [{idx}] {item['title'][:50]} (views={item.get('views', 0):,}) ---")
        # 記事自身のog:imageを最優先の背景・サムネイル素材にする（関連性が段違い）
        article_img = landscape_video.fetch_article_image(
            item.get("image_url", ""),
            f"{landscape_video.ASSETS_DIR}/article_{idx}.jpg",
        )
        # タイトル・本文から馬名を推定してWikipediaの馬写真も狙う
        # （strict=True なので競走馬ページ以外は採用されない）
        horses = item.get("horses") or landscape_video.extract_horse_names(
            f"{item['title']} {item.get('summary', '')[:300]}"
        )
        stock_count = 3 if article_img else 4
        bg_imgs = landscape_video.fetch_images(
            stock_count, horse_names=horses,
            strict_horses=not item.get("horses"),
            # 記事画像がある場合はダーク背景で埋めず、記事画像の再利用を優先
            fill_fallback=not article_img,
        )
        if article_img:
            bg_imgs = [article_img] + bg_imgs
            while len(bg_imgs) < 3:
                # 素材不足時は記事画像を再利用（セグメントごとにパン方向が変わる）
                bg_imgs.append(article_img)
        video_path = landscape_video.generate_video(idx, item, font, bg_imgs)

        title = build_title(item)
        description = build_description(item)
        extra_tags = ["競馬ニュース", "JRA", "競馬速報", "ニュース"]

        video_id = None
        while video_id is None:
            result = uploader.upload_video(youtube, title, description,
                                           video_path, extra_tags=extra_tags)
            if result == "CHANNEL_LIMIT":
                write_result(load_log + result_lines + [f"CHANNEL_LIMIT title={title[:50]}"])
                sys.exit(1)
            elif result is None:
                cred_idx += 1
                if cred_idx < len(all_creds):
                    print(f"  プロジェクト {cred_idx + 1} に切り替えてリトライ...")
                    youtube = build("youtube", "v3", credentials=all_creds[cred_idx])
                else:
                    print("[警告] 全プロジェクトのAPIクォータが超過しました。", file=sys.stderr)
                    write_result(load_log + result_lines + [f"QUOTA_EXCEEDED title={title[:50]}"])
                    sys.exit(1)
            else:
                video_id = result

        try:
            gen_thumb = f"{OUTPUT_DIR}/thumbnail_{idx}.jpg"
            thumb = gen_thumb if Path(gen_thumb).exists() else extract_frame_thumbnail(video_path, idx)
            uploader.upload_thumbnail(youtube, video_id, thumb)
        except Exception as e:
            print(f"  [警告] サムネイル処理失敗: {e}", file=sys.stderr)

        append_posted_id(item["id"])
        result_lines.append(
            f"OK project={cred_idx+1} video_id={video_id} views={item.get('views', 0)} title={title[:60]}"
        )

    if not result_lines:
        result_lines.append("result: no_uploads")
    write_result(load_log + result_lines)
    print("\n=== 完了 ===")


if __name__ == "__main__":
    main()
