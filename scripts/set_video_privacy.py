#!/usr/bin/env python3
"""アップロード済みの動画の公開設定を変更する。

非公開で上げたものを後から公開に切り替えるときに使う。上げ直すと
同じ動画が二重に並んでしまうので、既存の動画を更新する。

認証は upload_custom_youtube.py と同じ OAuth2 refresh_token 方式。
動画IDは、その動画をアップロードしたのと同じチャンネルのものであること。

使い方:
  python scripts/set_video_privacy.py --ids SZgU2oYTi0s --privacy public
  python scripts/set_video_privacy.py --ids-file dist/gen25/uploaded_ids.txt --privacy public
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from upload_custom_youtube import load_all_credentials


def set_privacy(youtube, video_id: str, privacy: str) -> bool:
    """privacyStatus だけを差し替える。他のメタデータは触らない。

    videos().update は指定した part をまるごと置き換えるので、
    先に現在の status を読んでから、必要な項目だけ書き換えて送る。
    """
    resp = youtube.videos().list(part="status", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        print(f"[エラー] 動画が見つかりません: {video_id}", file=sys.stderr)
        return False

    status = items[0]["status"]
    before = status.get("privacyStatus")
    if before == privacy:
        print(f"  {video_id}: すでに {privacy} です")
        return True

    status["privacyStatus"] = privacy
    # 読み取り専用フィールドは送り返せない
    for key in ("uploadStatus", "failureReason", "rejectionReason",
                "publishAt", "madeForKids"):
        status.pop(key, None)

    youtube.videos().update(
        part="status", body={"id": video_id, "status": status}).execute()
    print(f"  {video_id}: {before} → {privacy}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="カンマ区切りの動画ID")
    ap.add_argument("--ids-file", default="",
                    help="動画IDを1行ずつ書いたファイル（# 以降はコメント）")
    ap.add_argument("--privacy", required=True,
                    choices=["public", "unlisted", "private"])
    args = ap.parse_args()

    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    if args.ids_file:
        for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line:
                ids.append(line)
    if not ids:
        print("[エラー] 動画IDが指定されていません。", file=sys.stderr)
        sys.exit(1)

    creds_list = load_all_credentials()
    print(f"公開設定を {args.privacy} にします: {', '.join(ids)}")

    failed = []
    for vid in ids:
        # 動画を持っているチャンネルの認証情報でないと更新できないので、
        # 見つかるまで順に試す。
        for creds in creds_list:
            youtube = build("youtube", "v3", credentials=creds)
            try:
                if set_privacy(youtube, vid, args.privacy):
                    break
            except HttpError as e:
                print(f"  [警告] {vid}: {e}", file=sys.stderr)
        else:
            failed.append(vid)

    if failed:
        print(f"[エラー] 変更できませんでした: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)
    print("完了")


if __name__ == "__main__":
    main()
