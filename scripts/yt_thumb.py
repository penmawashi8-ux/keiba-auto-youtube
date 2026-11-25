"""YouTube のサムネイル設定（ショート対応）。

2026年11月3日から thumbnails().set() でショートにもカスタムサムネイルを付けられるようになった。
ただし公式の注意書きどおり「動画の処理が終わってから（processingDetails.processingStatus が succeeded）」
呼ばないと、HTTP 200 が返ってもショートには反映されないことがある。
そこで、処理完了を待ってから設定する。
"""
import sys
import time

from googleapiclient.errors import HttpError


def wait_processed(youtube, video_id: str, timeout: float = 480, every: float = 15) -> str:
    """動画の処理完了を待つ。戻り値は最後に見た processingStatus（succeeded / failed / timeout など）"""
    t0 = time.time()
    status = "unknown"
    while time.time() - t0 < timeout:
        try:
            r = youtube.videos().list(part="processingDetails,status", id=video_id).execute()
            items = r.get("items", [])
            if items:
                pd = items[0].get("processingDetails", {})
                status = pd.get("processingStatus", "unknown")
                up = items[0].get("status", {}).get("uploadStatus", "")
                if status == "succeeded" or up == "processed":
                    print(f"  動画の処理完了（{int(time.time() - t0)}秒）: {video_id}")
                    return "succeeded"
                if status in ("failed", "terminated") or up in ("failed", "rejected"):
                    print(f"  [警告] 動画の処理が失敗: {status}/{up}", file=sys.stderr)
                    return status or up
        except HttpError as e:
            print(f"  [警告] 処理状況の取得失敗 HTTP {e.resp.status}", file=sys.stderr)
        time.sleep(every)
    print(f"  [警告] 処理完了を待ちきれず（{timeout:.0f}秒, 最後の状態 {status}）。そのまま設定します", file=sys.stderr)
    return "timeout"


def set_when_ready(youtube, video_id: str, media) -> None:
    """処理完了を待ってからサムネイルを設定し、反映されたかを確認して表示する。失敗時は HttpError を投げる"""
    wait_processed(youtube, video_id)
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    try:
        r = youtube.videos().list(part="snippet", id=video_id).execute()
        th = r["items"][0]["snippet"].get("thumbnails", {})
        print(f"  サムネイルの種類: {', '.join(sorted(th))}"
              + ("（ショート用 9:16 あり）" if "shortsAspectRatio" in th else ""))
    except Exception:  # noqa: BLE001
        pass
