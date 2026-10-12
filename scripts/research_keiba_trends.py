#!/usr/bin/env python3
"""競馬ジャンルで再生数が伸びている横動画（通常動画）を YouTube Data API で調査する。

既存の OAuth 認証情報（upload_landscape_youtube.py と同じ GOOGLE_* シークレット）を使い、
キーワードごとに直近N日に公開された動画を再生数順で検索 → 統計情報を取得して
CSV と Markdown レポートを research/ に出力する。

- Shorts を除外するため videoDuration=medium(4〜20分) / long(20分超) のみ検索する
- クォータ: search.list 100ユニット × (キーワード数 × 2) + videos/channels.list 少々
  （アップロード用クォータを圧迫しないよう、後ろのプロジェクトの認証情報から使う）

使い方: python scripts/research_keiba_trends.py [--days 30]
"""

import argparse
import collections
import csv
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import upload_landscape_youtube as uploader  # 認証情報の読み込みを再利用

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

JST = datetime.timezone(datetime.timedelta(hours=9))
OUT_DIR = Path("research")

KEYWORDS = [
    "競馬",
    "競馬 予想",
    "競馬 解説",
    "競馬 ゆっくり解説",
    "名馬",
    "競馬 歴史",
    "競馬 データ",
    "競馬 ランキング",
    "競走馬 引退",
    "ウマ娘 史実",
    "菊花賞",
    "天皇賞秋",
]
DURATIONS = ["medium", "long"]

# タイトルから企画ジャンルを推定するための簡易ルール（上から順に判定）
GENRE_RULES = [
    ("名馬/歴史", r"名馬|伝説|歴史|列伝|史上|最強|物語|生涯|引退|死去|ウマ娘"),
    ("馬券勝負/エンタメ", r"万円|全額|爆益|一撃|的中|回収|3連単|三連単"),
    ("予想", r"予想|本命|買い目|狙い|穴馬|追い切り|調教|最終結論"),
    ("データ/ランキング", r"データ|ランキング|TOP|ベスト|統計|傾向|過去10年"),
    ("レース映像/実況", r"実況|レース映像|全レース|パドック|返し馬"),
    ("ニュース/話題", r"速報|ニュース|炎上|物議|騒動|発表|会見|コメント"),
    ("ゆっくり/解説", r"ゆっくり|解説|とは|なぜ|理由"),
]


def classify(title: str) -> str:
    for name, pattern in GENRE_RULES:
        if re.search(pattern, title, re.IGNORECASE):
            return name
    return "その他"


def parse_duration(iso: str) -> int:
    """ISO8601 の PT#H#M#S を秒に変換する。"""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


class Api:
    """クォータ超過時に次のプロジェクトの認証情報へ切り替える薄いラッパー。"""

    def __init__(self):
        creds, _ = uploader.load_all_credentials()
        # 先頭プロジェクトは定時アップロードで使うので後ろから使う
        self.clients = [build("youtube", "v3", credentials=c) for c in reversed(creds)]
        self.idx = 0
        self.units = 0

    def call(self, fn, cost: int):
        while self.idx < len(self.clients):
            try:
                res = fn(self.clients[self.idx]).execute()
                self.units += cost
                return res
            except HttpError as e:
                if uploader.is_quota_exceeded(e):
                    print(f"[警告] クォータ超過 → 次の認証情報へ切り替え ({self.idx})", file=sys.stderr)
                    self.idx += 1
                    continue
                raise
        raise RuntimeError("全プロジェクトのクォータを使い切りました")


def chunks(items, n=50):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc)
    published_after = (now - datetime.timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    api = Api()

    # 1) キーワード検索で候補動画を集める
    hits: dict[str, set[str]] = collections.defaultdict(set)
    for kw in KEYWORDS:
        for dur in DURATIONS:
            res = api.call(lambda yt: yt.search().list(
                part="id", q=kw, type="video", order="viewCount", maxResults=50,
                regionCode="JP", relevanceLanguage="ja", videoDuration=dur,
                publishedAfter=published_after,
            ), 100)
            for item in res.get("items", []):
                hits[item["id"]["videoId"]].add(kw)
            print(f"search: {kw} / {dur} → {len(res.get('items', []))}件")

    # 2) 急上昇（スポーツカテゴリ）からも競馬関連を拾う
    trending_ids = set()
    try:
        res = api.call(lambda yt: yt.videos().list(
            part="id,snippet", chart="mostPopular", regionCode="JP",
            videoCategoryId="17", maxResults=50), 1)
        for item in res.get("items", []):
            if re.search(r"競馬|馬券|G1|ＧⅠ|重賞|騎手|ダービー|ウマ", item["snippet"]["title"]):
                trending_ids.add(item["id"])
                hits[item["id"]].add("(急上昇)")
    except HttpError as e:
        print(f"[警告] 急上昇の取得に失敗: {e}", file=sys.stderr)

    # 3) 動画の統計情報
    videos = []
    for batch in chunks(list(hits)):
        res = api.call(lambda yt: yt.videos().list(
            part="snippet,statistics,contentDetails", id=",".join(batch)), 1)
        for v in res.get("items", []):
            sec = parse_duration(v["contentDetails"].get("duration", ""))
            if sec <= 180:  # Shorts は除外
                continue
            pub = datetime.datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
            age_days = max((now - pub).total_seconds() / 86400, 0.5)
            views = int(v["statistics"].get("viewCount", 0))
            videos.append({
                "video_id": v["id"],
                "title": v["snippet"]["title"],
                "channel_id": v["snippet"]["channelId"],
                "channel": v["snippet"]["channelTitle"],
                "published_jst": pub.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                "age_days": round(age_days, 1),
                "duration_min": round(sec / 60, 1),
                "views": views,
                "views_per_day": round(views / age_days),
                "likes": int(v["statistics"].get("likeCount", 0)),
                "comments": int(v["statistics"].get("commentCount", 0)),
                "genre": classify(v["snippet"]["title"]),
                "keywords": " / ".join(sorted(hits[v["id"]])),
                "url": f"https://www.youtube.com/watch?v={v['id']}",
            })

    # 4) チャンネル登録者数（登録者比の再生数＝企画の強さの指標）
    subs = {}
    for batch in chunks(sorted({v["channel_id"] for v in videos})):
        res = api.call(lambda yt: yt.channels().list(part="statistics", id=",".join(batch)), 1)
        for c in res.get("items", []):
            s = c["statistics"]
            subs[c["id"]] = None if s.get("hiddenSubscriberCount") else int(s.get("subscriberCount", 0))
    for v in videos:
        n = subs.get(v["channel_id"])
        v["subscribers"] = n if n is not None else ""
        v["views_per_sub"] = round(v["views"] / n, 2) if n else ""

    videos.sort(key=lambda v: v["views"], reverse=True)
    write_outputs(videos, args.days, now, api.units)


def fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def video_table(rows) -> list[str]:
    lines = ["| # | 再生数 | 再生/日 | 登録者比 | 長さ | ジャンル | タイトル | チャンネル |",
             "|---|---:|---:|---:|---:|---|---|---|"]
    for i, v in enumerate(rows, 1):
        title = v["title"].replace("|", "｜")
        lines.append(f"| {i} | {fmt(v['views'])} | {fmt(v['views_per_day'])} | {v['views_per_sub']} | "
                     f"{v['duration_min']}分 | {v['genre']} | [{title}]({v['url']}) | {v['channel']} |")
    return lines


def write_outputs(videos, days, now, units) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    stamp = now.astimezone(JST).strftime("%Y%m%d")
    csv_path = OUT_DIR / f"keiba_trends_{stamp}.csv"
    md_path = OUT_DIR / f"keiba_trends_{stamp}.md"

    fields = ["video_id", "title", "channel", "channel_id", "subscribers", "published_jst", "age_days",
              "duration_min", "views", "views_per_day", "views_per_sub", "likes", "comments",
              "genre", "keywords", "url"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(videos)

    md = [f"# 競馬 横動画トレンド調査（{now.astimezone(JST):%Y-%m-%d}）", "",
          f"- 対象: 直近{days}日に公開された4分超の動画（Shorts除外）",
          f"- 検索キーワード: {', '.join(KEYWORDS)}",
          f"- 収集動画数: {len(videos)} / 使用クォータ概算: {units}ユニット", ""]

    md += ["## ジャンル別集計", "", "| ジャンル | 本数 | 合計再生 | 中央値再生 | 平均再生/日 |", "|---|---:|---:|---:|---:|"]
    by_genre = collections.defaultdict(list)
    for v in videos:
        by_genre[v["genre"]].append(v)
    for g, vs in sorted(by_genre.items(), key=lambda kv: -sum(x["views"] for x in kv[1])):
        views = sorted(x["views"] for x in vs)
        md.append(f"| {g} | {len(vs)} | {fmt(sum(views))} | {fmt(views[len(views)//2])} | "
                  f"{fmt(round(sum(x['views_per_day'] for x in vs) / len(vs)))} |")

    md += ["", "## 長さ別集計", "", "| 長さ | 本数 | 中央値再生 |", "|---|---:|---:|"]
    buckets = [("3〜8分", 3, 8), ("8〜15分", 8, 15), ("15〜30分", 15, 30), ("30〜60分", 30, 60), ("60分超", 60, 10**6)]
    for label, lo, hi in buckets:
        vs = sorted(x["views"] for x in videos if lo < x["duration_min"] <= hi)
        if vs:
            md.append(f"| {label} | {len(vs)} | {fmt(vs[len(vs)//2])} |")

    md += ["", "## 再生数 TOP50", ""] + video_table(videos[:50])
    md += ["", "## 1日あたり再生数 TOP30（勢い）", ""] + video_table(
        sorted(videos, key=lambda v: v["views_per_day"], reverse=True)[:30])
    small = [v for v in videos if isinstance(v["subscribers"], int) and v["subscribers"] < 50000
             and v["views_per_sub"] != ""]
    md += ["", "## 小規模チャンネル（登録5万未満）で登録者比が高い動画 TOP30（企画の強さ）", ""] + video_table(
        sorted(small, key=lambda v: v["views_per_sub"], reverse=True)[:30])

    md += ["", "## チャンネル別集計 TOP30", "", "| チャンネル | 登録者 | 本数 | 合計再生 |", "|---|---:|---:|---:|"]
    by_ch = collections.defaultdict(list)
    for v in videos:
        by_ch[v["channel"]].append(v)
    for ch, vs in sorted(by_ch.items(), key=lambda kv: -sum(x["views"] for x in kv[1]))[:30]:
        md.append(f"| {ch} | {fmt(vs[0]['subscribers'])} | {len(vs)} | {fmt(sum(x['views'] for x in vs))} |")

    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"保存: {csv_path} / {md_path}")


if __name__ == "__main__":
    main()
