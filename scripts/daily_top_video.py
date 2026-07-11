#!/usr/bin/env python3
"""その日投稿したニュースShortsのうち最も再生された動画を横向きに再編集して投稿する。

処理の流れ:
1. 当日(JST)の keiba_news ワークフロー実行の Artifact（news-videos-*）を収集し、
   upload_results.json から video_id と動画ファイルの対応を得る
2. YouTube Data API で各動画の再生数を取得し、最多再生の1本を選ぶ
3. ffmpeg で縦動画(1080x1920)を横型(1920x1080)に再編集する
   （ぼかし背景 + 中央に元動画 + 上部にタイトル帯。drawbox は使わず drawtext の box= で実現）
4. 通常動画（#Shortsなし）としてアップロードし、posted_daily_top_ids.txt に記録する
"""

import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import upload_landscape_youtube as uploader  # 認証・アップロード・サムネイル処理を再利用
from landscape_video import find_font, wrap_text, _esc

from googleapiclient.discovery import build

JST = datetime.timezone(datetime.timedelta(hours=9))
OUTPUT_DIR = "output"
POSTED_FILE = "posted_daily_top_ids.txt"
RESULT_FILE = "last_daily_top_result.txt"
W, H = 1920, 1080
FG_H = 930  # 中央に置く元動画の高さ（上部にタイトル帯ぶんの余白を残す）

NEWS_WORKFLOW = "keiba_news.yml"
ARTIFACT_PREFIX = "news-videos-"
GH_API = "https://api.github.com"


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
    """当日(JST)のニュース投稿の {video_id, title, video_path} リストを返す。"""
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
                video_file = entry.get("video_file", "")
                video_path = extract_dir / video_file if video_file else None
                if video_id and video_path and video_path.exists():
                    candidates.append({
                        "video_id": video_id,
                        "title": entry.get("title", ""),
                        "video_path": str(video_path),
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
# 横型への再編集
# ---------------------------------------------------------------------------

def convert_to_landscape(src: str, title: str, out_path: str) -> None:
    """縦動画をぼかし背景つきの横型(1920x1080)に再編集する。"""
    font = find_font()
    tmp_dir = tempfile.mkdtemp(prefix="daily_top_")

    fc_parts = [
        "[0:v]split=2[bg][fg]",
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur=20:2,eq=brightness=-0.12[bgb]",
        f"[fg]scale=-2:{FG_H}[fgs]",
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2+40[base]",
    ]

    last = "[base]"
    if font and title:
        tf = f"{tmp_dir}/title.txt"
        # 最大2行（24文字×2）に収めて動画本体への被りを最小限にする
        short = title if len(title) <= 48 else title[:47] + "…"
        Path(tf).write_text(wrap_text(short, 24), encoding="utf-8")
        fc_parts.append(
            f"{last}drawtext=textfile='{_esc(tf)}':fontfile='{_esc(font)}'"
            f":fontsize=52:fontcolor=0xFFD700"
            f":x=(w-text_w)/2:y=24"
            f":box=1:boxcolor=0x000000@0.72:boxborderw=18"
            f":borderw=2:bordercolor=0x000000[vout]"
        )
        last = "[vout]"
    else:
        fc_parts[-1] = fc_parts[-1].replace("[base]", "[vout]")
        last = "[vout]"

    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-filter_complex", ";".join(fc_parts),
        "-map", last, "-map", "0:a?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "192k",
        out_path,
    ]
    print("  横型再編集中...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        print(f"[エラー] ffmpeg失敗:\n{result.stderr[-800:]}", file=sys.stderr)
        raise RuntimeError("横型変換に失敗しました")
    size_mb = Path(out_path).stat().st_size / (1024 * 1024)
    print(f"✅ {out_path} ({size_mb:.1f} MB)")


def generate_thumbnail(video_path: str) -> str:
    """横型動画の先頭フレームを抽出してサムネイルにする（リサイズ禁止）。"""
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
        landscape_path = f"{OUTPUT_DIR}/daily_top_landscape.mp4"
        convert_to_landscape(top["video_path"], top["title"], landscape_path)

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
            uploader.upload_thumbnail(youtube, video_id, generate_thumbnail(landscape_path))
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
