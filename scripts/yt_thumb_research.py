"""サムネイル研究用: 競馬の予想・シミュレーション動画の再生数とサムネイルを集める。

入力: data/yt_research.json  {"queries": [...], "published_after": "2025-09-01T00:00:00Z", "per_query": 25}
出力: out/yt_research/videos.json, mine.json, thumbs/<videoId>.jpg

「登録者数のわりに再生された動画」（再生数 / 登録者数）が、サムネイルとタイトルで人を呼べた動画の目安。
認証は upload_custom_youtube.py と同じ OAuth（force-ssl スコープで読み取りもできる）。検索は1回100ユニット。
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from googleapiclient.discovery import build                      # noqa: E402
import upload_custom_youtube as up                                # noqa: E402

OUT = Path("out/yt_research")


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def main():
    req = json.loads(Path("data/yt_research.json").read_text(encoding="utf-8"))
    creds = up.load_all_credentials()
    yt = build("youtube", "v3", credentials=creds[-1], cache_discovery=False)   # アップロード用と別の枠を優先
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "thumbs").mkdir(exist_ok=True)

    found = {}
    for q in req["queries"]:
        r = yt.search().list(q=q, part="id", type="video", order="viewCount", regionCode="JP",
                             relevanceLanguage="ja", publishedAfter=req["published_after"],
                             maxResults=req.get("per_query", 25)).execute()
        for it in r.get("items", []):
            found.setdefault(it["id"]["videoId"], []).append(q)
        print(f"{q}: {len(r.get('items', []))}本")

    def videos(ids):
        out = []
        for c in chunks(list(ids), 50):
            r = yt.videos().list(id=",".join(c), part="snippet,statistics,contentDetails").execute()
            out += r.get("items", [])
        return out

    vids = videos(found)
    chans = {}
    for c in chunks(sorted({v["snippet"]["channelId"] for v in vids}), 50):
        r = yt.channels().list(id=",".join(c), part="statistics,snippet").execute()
        for ch in r.get("items", []):
            chans[ch["id"]] = ch

    def row(v, query=None):
        st, sn = v.get("statistics", {}), v["snippet"]
        ch = chans.get(sn["channelId"], {})
        subs = int(ch.get("statistics", {}).get("subscriberCount", 0) or 0)
        views = int(st.get("viewCount", 0) or 0)
        th = sn["thumbnails"]
        url = (th.get("maxres") or th.get("standard") or th.get("high") or th.get("medium") or th["default"])["url"]
        return {"id": v["id"], "title": sn["title"], "channel": sn["channelTitle"], "published": sn["publishedAt"],
                "views": views, "likes": int(st.get("likes", st.get("likeCount", 0)) or 0), "subs": subs,
                "ratio": round(views / max(subs, 100), 3), "duration": v["contentDetails"]["duration"],
                "thumb": url, "queries": query or []}

    rows = [row(v, found.get(v["id"])) for v in vids]
    # 自分のチャンネル
    mine = []
    try:
        me = yt.channels().list(mine=True, part="contentDetails,statistics,snippet").execute()["items"][0]
        chans[me["id"]] = me
        pl = me["contentDetails"]["relatedPlaylists"]["uploads"]
        ids, tok = [], None
        while len(ids) < 200:
            r = yt.playlistItems().list(playlistId=pl, part="contentDetails", maxResults=50, pageToken=tok).execute()
            ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
            tok = r.get("nextPageToken")
            if not tok:
                break
        mine = [row(v) for v in videos(ids)]
        print(f"自分のチャンネル: {me['snippet']['title']} 登録者 {me['statistics'].get('subscriberCount')} / 動画 {len(mine)}本")
    except Exception as e:     # noqa: BLE001
        print("自分のチャンネルの取得に失敗:", e)

    for r in rows + mine:
        p = OUT / "thumbs" / f"{r['id']}.jpg"
        if not p.exists():
            try:
                urllib.request.urlretrieve(r["thumb"], p)
            except Exception as e:     # noqa: BLE001
                print("thumb", r["id"], e)
    rows.sort(key=lambda r: -r["views"])
    (OUT / "videos.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "mine.json").write_text(json.dumps(sorted(mine, key=lambda r: -r["views"]), ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    print(f"動画 {len(rows)}本 / 自分 {len(mine)}本")


if __name__ == "__main__":
    main()
