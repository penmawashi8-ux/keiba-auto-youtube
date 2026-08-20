#!/usr/bin/env python3
"""投稿した動画を再生リストに自動で入れるための共通処理。

再生リストIDをファイルに持たせると、消えたり作り直したりしたときに
ずれるので持たない。毎回タイトルで自分の再生リストを探し、無ければ
その場で作る。API消費は1日あたり数十ユニット（上限10000）で収まる。

認証は upload_youtube.py と同じものをそのまま使える。既存のスコープに
youtube.force-ssl が入っており、再生リストの作成・追加もこれで足りる
（トークンの取り直しは不要）。

再生リストへの追加は「おまけ」なので、失敗しても例外は投げない。
動画自体は上がっているのに、ワークフローが落ちるほうが困る。
"""

import sys

from googleapiclient.errors import HttpError

# 横型ニュース2本（21時のアクセスランキング／22時の人気動画の再投稿）は
# どちらも「その日の競馬ニュース」なので、1つの再生リストにまとめる。
# 本数がまとまっているほうが連続再生されやすい。
NEWS_LANDSCAPE = {
    "title": "競馬ニュース（毎日更新）",
    "description": (
        "毎日の競馬ニュースをまとめた横型動画の再生リストです。\n"
        "その日の注目ニュースと、いちばん見られた話題を毎日追加しています。"
    ),
    "privacy": "public",
}


def _find_playlist(youtube, title: str) -> str | None:
    """自分の再生リストからタイトル一致のものを探す。"""
    token = None
    while True:
        resp = youtube.playlists().list(
            part="snippet", mine=True, maxResults=50, pageToken=token).execute()
        for item in resp.get("items", []):
            if item["snippet"]["title"] == title:
                return item["id"]
        token = resp.get("nextPageToken")
        if not token:
            return None


def ensure_playlist(youtube, spec: dict = NEWS_LANDSCAPE) -> str | None:
    """再生リストのIDを返す。無ければ作る。失敗したら None。"""
    try:
        pid = _find_playlist(youtube, spec["title"])
        if pid:
            return pid
        resp = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {"title": spec["title"],
                            "description": spec.get("description", ""),
                            "defaultLanguage": "ja"},
                "status": {"privacyStatus": spec.get("privacy", "public")},
            },
        ).execute()
        pid = resp["id"]
        print(f"  再生リストを作成しました: {spec['title']} ({pid})")
        return pid
    except HttpError as e:
        print(f"  [警告] 再生リストを用意できませんでした: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [警告] 再生リストを用意できませんでした: {e}", file=sys.stderr)
        return None


def add_to_playlist(youtube, video_id: str, spec: dict = NEWS_LANDSCAPE) -> bool:
    """動画を再生リストの末尾に追加する。失敗しても False を返すだけ。"""
    pid = ensure_playlist(youtube, spec)
    if not pid:
        return False
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": {
                "playlistId": pid,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }},
        ).execute()
        print(f"  再生リストに追加しました: {spec['title']} ← {video_id}")
        return True
    except HttpError as e:
        print(f"  [警告] 再生リストへの追加に失敗: {video_id}: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  [警告] 再生リストへの追加に失敗: {video_id}: {e}", file=sys.stderr)
        return False
