#!/usr/bin/env python3
"""投稿済みの動画（ショート含む）にサムネイルを付け直す。
使い方: python scripts/set_thumb_existing.py data/thumb_set/<名前>.json
  JSON: {"items": [{"video": "動画ID または youtu.be のURL", "thumbnail": "リポジトリ内の画像パス"}]}
"""
import io
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaIoBaseUpload  # noqa: E402

import yt_thumb  # noqa: E402
from upload_custom_youtube import load_all_credentials  # noqa: E402


def frame_from_youtube(vid: str, at: float) -> str | None:
    """yt-dlp で動画を取得し、at 秒のフレームを元の縦横比のまま抜き出す（-s でのリサイズはしない）"""
    out = Path("out_thumb")
    out.mkdir(exist_ok=True)
    mp4 = out / f"{vid}.mp4"
    r = subprocess.run(["yt-dlp", "-q", "-f", "bv*[ext=mp4]/b[ext=mp4]/bv*/b", "-o", str(mp4),
                        f"https://www.youtube.com/shorts/{vid}"], capture_output=True, text=True)
    if r.returncode != 0 or not mp4.exists():
        print(f"NG {vid}: 動画を取得できず {r.stderr[-300:]}", file=sys.stderr)
        return None
    jpg = out / f"{vid}.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(mp4), "-vframes", "1", "-q:v", "2",
                    str(jpg)], check=False)
    if not jpg.exists():
        print(f"NG {vid}: フレームを抜けず", file=sys.stderr)
        return None
    print(f"  フレーム抽出: {jpg}")
    return str(jpg)


def main():
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    youtube = build("youtube", "v3", credentials=load_all_credentials()[0])
    ng = 0
    for it in cfg["items"]:
        vid = it["video"].rstrip("/").split("/")[-1].split("=")[-1]
        if cfg.get("check_only"):                     # 付け直さず、今のサムネイルの状態だけ見る
            th = youtube.videos().list(part="snippet", id=vid).execute()["items"][0]["snippet"]["thumbnails"]
            print(f"{vid}: {', '.join(sorted(th))}" + (f"  9:16 → {th['shortsAspectRatio']}" if "shortsAspectRatio" in th else "  （9:16 なし）"))
            continue
        thumb = it.get("thumbnail")
        if not thumb:                                 # 画像が残っていない動画: 投稿済みの動画を落としてフレームを抜く
            thumb = frame_from_youtube(vid, float(it.get("frame_at", 0.5)))
            if not thumb:
                ng += 1
                continue
        media = MediaIoBaseUpload(io.BytesIO(Path(thumb).read_bytes()), mimetype="image/jpeg", resumable=False)
        try:
            yt_thumb.set_when_ready(youtube, vid, media)
            print(f"OK {vid} <- {thumb}")
        except Exception as e:  # noqa: BLE001
            ng += 1
            print(f"NG {vid}: {e}", file=sys.stderr)
    sys.exit(1 if ng else 0)


if __name__ == "__main__":
    main()
