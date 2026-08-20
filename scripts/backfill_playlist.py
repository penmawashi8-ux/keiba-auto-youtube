#!/usr/bin/env python3
"""過去に投稿した横型ニュース動画を、まとめて再生リストに入れる。

対象は data/news_landscape_ids.txt に書いた動画ID。
すでに再生リストに入っているものは飛ばすので、何度実行しても
同じ動画が重複して並ぶことはない。

古い順に追加するので、再生リストは投稿順（＝日付順）に並ぶ。

使い方:
  python scripts/backfill_playlist.py                 # 追加する
  python scripts/backfill_playlist.py --dry-run       # 何が追加されるか見るだけ
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import playlist_utils
import upload_landscape_youtube as uploader

IDS_FILE = "data/news_landscape_ids.txt"


def read_ids(path: str) -> list[str]:
    ids, seen = [], set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        vid = line.split("#")[0].strip()
        if vid and vid not in seen:
            seen.add(vid)
            ids.append(vid)
    return ids


def _is_not_found(e: HttpError) -> bool:
    return getattr(getattr(e, "resp", None), "status", None) == 404


def existing_items(youtube, playlist_id: str) -> set[str]:
    """再生リストに入っている動画IDを集める（重複追加を避けるため）。

    作りたてだと YouTube 側の反映が追いつかず playlistNotFound(404) が
    返ることがある。少し待って何度か試し、それでも駄目なら
    「空の再生リスト」とみなす（実際、作りたてなら中身は無い）。
    """
    for attempt in range(5):
        out, token = set(), None
        try:
            while True:
                resp = youtube.playlistItems().list(
                    part="contentDetails", playlistId=playlist_id,
                    maxResults=50, pageToken=token).execute()
                for item in resp.get("items", []):
                    out.add(item["contentDetails"]["videoId"])
                token = resp.get("nextPageToken")
                if not token:
                    return out
        except HttpError as e:
            if not _is_not_found(e):
                raise
            wait = 3 * (attempt + 1)
            print(f"  再生リストがまだ見えません。{wait}秒待って再試行します"
                  f" ({attempt + 1}/5)")
            time.sleep(wait)
    print("  [情報] 反映待ちが解消しないので、空の再生リストとして扱います")
    return set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-file", default=IDS_FILE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ids = read_ids(args.ids_file)
    print(f"対象: {len(ids)} 本 ({args.ids_file})")

    all_creds, load_log = uploader.load_all_credentials()
    print("\n".join(load_log))
    youtube = build("youtube", "v3", credentials=all_creds[0])

    pid = playlist_utils.ensure_playlist(youtube)
    if not pid:
        print("[エラー] 再生リストを用意できませんでした。", file=sys.stderr)
        sys.exit(1)

    try:
        already = existing_items(youtube, pid)
    except HttpError as e:
        print(f"[エラー] 再生リストの中身を取得できません: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"再生リストの現在の本数: {len(already)}")

    todo = [v for v in ids if v not in already]
    skipped = len(ids) - len(todo)
    if skipped:
        print(f"すでに入っているので飛ばす: {skipped} 本")
    if not todo:
        print("追加するものはありません。")
        return

    if args.dry_run:
        print("--dry-run のため追加しません。追加予定:")
        for v in todo:
            print(f"  {v}")
        return

    ok = 0
    for vid in todo:
        if playlist_utils.add_to_playlist(youtube, vid):
            ok += 1
    print(f"\n=== 完了: {ok}/{len(todo)} 本を追加しました ===")
    if ok != len(todo):
        sys.exit(1)


if __name__ == "__main__":
    main()
