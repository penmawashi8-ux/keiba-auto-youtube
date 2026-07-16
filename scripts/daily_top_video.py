#!/usr/bin/env python3
"""その日投稿したニュースShortsのうち最も再生された動画を横向きに再生成して投稿する。

処理の流れ:
1. 当日(JST)の keiba_news ワークフロー実行の Artifact（news-videos-*）を収集し、
   upload_results.json から video_id と素材（idx / news_item）の対応を得る
2. YouTube Data API で各動画の再生数を取得し、最多再生の1本を選ぶ
3. Artifact に保存された脚本・音声・ASS字幕を使い、landscape_video.py の
   パイプラインでネイティブな横型動画を再生成する
   （縦動画の縮小合成ではないため、字幕は横画面に最適なサイズで表示される）
4. 通常動画（#Shortsなし）としてアップロードし、posted_daily_top_ids.txt に記録する
"""

import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import landscape_video  # 横型動画のネイティブ生成パイプラインを再利用
import upload_landscape_youtube as uploader  # 認証・アップロード・サムネイル処理を再利用

from googleapiclient.discovery import build

JST = datetime.timezone(datetime.timedelta(hours=9))
OUTPUT_DIR = "output"
POSTED_FILE = "posted_daily_top_ids.txt"
RESULT_FILE = "last_daily_top_result.txt"

NEWS_WORKFLOW = "keiba_news.yml"
ARTIFACT_PREFIX = "news-videos-"
GH_API = "https://api.github.com"

# 横型再生成に必要な素材ファイル（idx ごと）
SOURCE_FILES = ["script_{idx}.txt", "audio_{idx}.mp3", "subtitles_{idx}.ass"]
REQUIRED_FILES = ["script_{idx}.txt", "audio_{idx}.mp3"]  # ASS字幕は無くても生成可


# ---------------------------------------------------------------------------
# GitHub Actions Artifact の収集
# ---------------------------------------------------------------------------

def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "keiba-auto-youtube",
    }


def _gh_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_gh_headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download_artifact_zip(url: str) -> bytes:
    """Artifact の zip を取得する。

    GitHub API は署名付きURLへ 302 リダイレクトする。リダイレクト先に
    Authorization ヘッダを送ると認証エラーになるため、手動で追跡する。
    """
    req = urllib.request.Request(url, headers=_gh_headers())
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 307, 308):
            location = e.headers["Location"]
            with urllib.request.urlopen(location, timeout=300) as r2:
                return r2.read()
        raise


def collect_today_candidates(work_dir: str) -> list[dict]:
    """当日(JST)のニュース投稿の {video_id, title, idx, news_item, extract_dir} リストを返す。"""
    repo = os.environ["GITHUB_REPOSITORY"]
    day_start = datetime.datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)

    url = f"{GH_API}/repos/{repo}/actions/workflows/{NEWS_WORKFLOW}/runs?per_page=50"
    runs = _gh_json(url).get("workflow_runs", [])

    candidates: list[dict] = []
    for run in runs:
        created = datetime.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if created.astimezone(JST) < day_start:
            continue

        try:
            artifacts = _gh_json(run["artifacts_url"]).get("artifacts", [])
        except Exception as e:
            print(f"  [警告] run {run['id']} のartifact一覧取得失敗: {e}", file=sys.stderr)
            continue

        for art in artifacts:
            if not art["name"].startswith(ARTIFACT_PREFIX) or art.get("expired"):
                continue
            extract_dir = Path(work_dir) / f"run_{run['id']}"
            extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                data = _download_artifact_zip(art["archive_download_url"])
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    zf.extractall(extract_dir)
            except Exception as e:
                print(f"  [警告] artifact {art['name']} の取得失敗: {e}", file=sys.stderr)
                continue

            results_json = extract_dir / "upload_results.json"
            if not results_json.exists():
                continue
            try:
                results = json.loads(results_json.read_text(encoding="utf-8"))
            except Exception:
                continue
            for entry in results:
                video_id = entry.get("video_id", "")
                idx = entry.get("idx")
                if not video_id or idx is None:
                    continue
                missing = [t.format(idx=idx) for t in REQUIRED_FILES
                           if not (extract_dir / t.format(idx=idx)).exists()]
                if missing:
                    print(f"  [警告] {video_id} の素材不足: {missing}", file=sys.stderr)
                    continue
                candidates.append({
                    "video_id": video_id,
                    "title": entry.get("title", ""),
                    "idx": idx,
                    "news_item": entry.get("news_item") or {},
                    "extract_dir": str(extract_dir),
                })

    # 同一video_idの重複を除去
    seen: set[str] = set()
    unique = []
    for c in candidates:
        if c["video_id"] not in seen:
            seen.add(c["video_id"])
            unique.append(c)
    return unique


# ---------------------------------------------------------------------------
# 再生数の取得と1位選定
# ---------------------------------------------------------------------------

def fetch_view_counts(youtube, video_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        resp = youtube.videos().list(part="statistics", id=",".join(chunk)).execute()
        for item in resp.get("items", []):
            counts[item["id"]] = int(item.get("statistics", {}).get("viewCount", 0))
    return counts


def load_posted_ids() -> set[str]:
    path = Path(POSTED_FILE)
    if not path.exists():
        return set()
    return set(path.read_text(encoding="utf-8").splitlines())


def append_posted_id(video_id: str) -> None:
    with open(POSTED_FILE, "a", encoding="utf-8") as f:
        f.write(video_id + "\n")


# ---------------------------------------------------------------------------
# 横型動画のネイティブ再生成
# ---------------------------------------------------------------------------

def generate_landscape(top: dict) -> tuple[str, str]:
    """素材から横型動画を再生成して (動画パス, サムネイルパス) を返す。"""
    idx = top["idx"]
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    for tpl in SOURCE_FILES:
        name = tpl.format(idx=idx)
        src = Path(top["extract_dir"]) / name
        if src.exists():
            shutil.copy(src, Path(OUTPUT_DIR) / name)

    # リポジトリにコミット済みの古い縦型サムネイルが残っていると
    # landscape_video.generate_video が生成をスキップしてしまうため先に消す
    thumb_path = Path(OUTPUT_DIR) / f"thumbnail_{idx}.jpg"
    thumb_path.unlink(missing_ok=True)

    meta = dict(top["news_item"])
    meta.setdefault("title", top["title"])

    font = landscape_video.find_font()
    # 記事自身のog:imageを最優先の背景・サムネイル素材にする（関連性が段違い）
    article_img = landscape_video.fetch_article_image(
        meta.get("image_url", ""),
        f"{landscape_video.ASSETS_DIR}/article_{idx}.jpg",
    )
    bg_imgs = landscape_video.fetch_images(
        3 if article_img else 4, horse_names=meta.get("horses"))
    if article_img:
        bg_imgs = [article_img] + bg_imgs
    video_path = landscape_video.generate_video(idx, meta, font, bg_imgs)
    return video_path, str(thumb_path)


def extract_frame_thumbnail(video_path: str) -> str:
    """フォールバック: 横型動画の先頭フレームを抽出してサムネイルにする（リサイズ禁止）。"""
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    thumb_path = f"{OUTPUT_DIR}/thumbnail_daily_top.jpg"
    result = subprocess.run([
        "ffmpeg", "-y", "-ss", "0.5", "-i", video_path,
        "-vframes", "1", "-q:v", "2", thumb_path,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [警告] サムネイル抽出失敗: {result.stderr[-200:]}", file=sys.stderr)
    return thumb_path


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def build_title(article_title: str) -> str:
    now = datetime.datetime.now(JST)
    date_str = f"{now.month}月{now.day}日"
    return f"【本日の人気No.1】{date_str} {article_title}"[:100]


def build_description(article_title: str, short_video_id: str) -> str:
    return (
        f"本日投稿した競馬ニュースの中で最も再生された動画を横型でお届けします。\n"
        f"{article_title}\n\n"
        f"ショート版: https://www.youtube.com/shorts/{short_video_id}\n\n"
        f"#競馬 #競馬ニュース #JRA #keiba #競馬速報"
    )


def write_result(lines: list[str]) -> None:
    header = [f"date: {datetime.datetime.utcnow().isoformat()}Z", "type: daily_top_landscape"]
    Path(RESULT_FILE).write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    print("\n".join(header + lines))


def main() -> None:
    print("=== 1日の人気No.1動画 横型再投稿 ===")
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="artifacts_") as work_dir:
        candidates = collect_today_candidates(work_dir)
        if not candidates:
            print("本日の投稿動画が見つかりません。スキップします。")
            write_result(["result: no_candidates"])
            return

        posted = load_posted_ids()
        candidates = [c for c in candidates if c["video_id"] not in posted]
        if not candidates:
            print("未投稿の候補がありません（すべて再投稿済み）。スキップします。")
            write_result(["result: all_already_posted"])
            return

        print(f"候補: {len(candidates)} 本")

        all_creds, load_log = uploader.load_all_credentials()
        cred_idx = 0
        youtube = build("youtube", "v3", credentials=all_creds[cred_idx])

        counts = fetch_view_counts(youtube, [c["video_id"] for c in candidates])
        for c in candidates:
            c["views"] = counts.get(c["video_id"], 0)
            print(f"  {c['views']:>6} views  {c['video_id']}  {c['title'][:40]}")

        top = max(candidates, key=lambda c: c["views"])
        print(f"\n本日の1位: {top['title'][:50]} ({top['views']} views)")

        title = build_title(top["title"])
        description = build_description(top["title"], top["video_id"])
        landscape_path, gen_thumb = generate_landscape(top)

        extra_tags = ["競馬ニュース", "JRA", "競馬速報", "ニュース"]
        video_id = None
        while video_id is None:
            result = uploader.upload_video(youtube, title, description,
                                           landscape_path, extra_tags=extra_tags)
            if result == "CHANNEL_LIMIT":
                write_result(load_log + [f"CHANNEL_LIMIT title={title[:50]}"])
                sys.exit(1)
            elif result is None:
                cred_idx += 1
                if cred_idx < len(all_creds):
                    print(f"  プロジェクト {cred_idx + 1} に切り替えてリトライ...")
                    youtube = build("youtube", "v3", credentials=all_creds[cred_idx])
                else:
                    print("[警告] 全プロジェクトのAPIクォータが超過しました。", file=sys.stderr)
                    write_result(load_log + [f"QUOTA_EXCEEDED title={title[:50]}"])
                    sys.exit(1)
            else:
                video_id = result

        try:
            thumb = gen_thumb if Path(gen_thumb).exists() else extract_frame_thumbnail(landscape_path)
            uploader.upload_thumbnail(youtube, video_id, thumb)
        except Exception as e:
            print(f"  [警告] サムネイル処理失敗: {e}", file=sys.stderr)

        append_posted_id(top["video_id"])
        write_result(load_log + [
            f"OK project={cred_idx+1} video_id={video_id}",
            f"source_short={top['video_id']} views={top['views']}",
            f"title={title[:60]}",
        ])
        print("\n=== 完了 ===")


if __name__ == "__main__":
    main()
