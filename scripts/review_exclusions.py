#!/usr/bin/env python3
"""除外されたニュース記事を振り返るためのビューア。

debug/excluded_news.jsonl を読んで、「どのルールが何を弾いたか」を一覧する。
「これは除外すべきでなかった」記事を見つけたら、fetch_news.py のパターンを直す。

使い方:
  python scripts/review_exclusions.py                    # 直近7日をルール別に集計
  python scripts/review_exclusions.py --days 30          # 期間を変える
  python scripts/review_exclusions.py --days 0           # 全期間
  python scripts/review_exclusions.py --reason retrospective   # プレイバック除外だけ
  python scripts/review_exclusions.py --pattern 振り返   # 特定の一致語だけ
  python scripts/review_exclusions.py --list             # 集計せず時系列で全部出す
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "debug" / "excluded_news.jsonl"
JST = timezone(timedelta(hours=9))

REASON_LABELS = {
    "retrospective": "プレイバック（懐古）記事",
    "video_title": "動画タイトル",
    "deny_keyword": "対象外ジャンル/キャンペーン",
    "generic_title": "定型タイトル",
    "not_result": "予想・登録記事（結果ではない）",
}


def load_records(days: int) -> list[dict]:
    if not LOG_FILE.exists():
        print(f"除外ログがありません: {LOG_FILE}", file=sys.stderr)
        print("（ワークフローが1回でも走れば作成されます）", file=sys.stderr)
        return []

    cutoff = None
    if days > 0:
        cutoff = datetime.now(JST) - timedelta(days=days)

    records = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cutoff:
            try:
                ts = datetime.fromisoformat(rec.get("ts", ""))
            except ValueError:
                continue
            if ts < cutoff:
                continue
        records.append(rec)
    return records


def print_grouped(records: list[dict]) -> None:
    """ルール別 → 一致語別にまとめて表示する。"""
    by_reason: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_reason[rec.get("reason", "unknown")].append(rec)

    for reason, recs in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        label = REASON_LABELS.get(reason, reason)
        print(f"\n=== {label} [{reason}]  {len(recs)}件 ===")
        by_pattern: dict[str, list[dict]] = defaultdict(list)
        for rec in recs:
            by_pattern[rec.get("pattern", "")].append(rec)
        for pattern, prs in sorted(by_pattern.items(), key=lambda kv: -len(kv[1])):
            head = f"  ▼ 一致語「{pattern}」 {len(prs)}件" if pattern else f"  ▼ {len(prs)}件"
            print(head)
            for rec in prs:
                date = rec.get("ts", "")[:10]
                print(f"      {date}  {rec.get('title', '')[:70]}")
                if rec.get("url"):
                    print(f"                  {rec['url']}")


def print_list(records: list[dict]) -> None:
    """時系列でそのまま並べる。"""
    for rec in sorted(records, key=lambda r: r.get("ts", "")):
        print(
            f"{rec.get('ts', '')[:16]}  [{rec.get('reason', '')}]"
            f"{'（' + rec['pattern'] + '）' if rec.get('pattern') else ''}  "
            f"{rec.get('title', '')[:70]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="除外されたニュース記事を振り返る")
    parser.add_argument("--days", type=int, default=7,
                        help="何日前まで見るか（0で全期間、既定7）")
    parser.add_argument("--reason", help="除外理由で絞る（例: retrospective）")
    parser.add_argument("--pattern", help="一致語で絞る（部分一致）")
    parser.add_argument("--list", action="store_true", help="集計せず時系列で並べる")
    args = parser.parse_args()

    records = load_records(args.days)
    if args.reason:
        records = [r for r in records if r.get("reason") == args.reason]
    if args.pattern:
        records = [r for r in records if args.pattern in r.get("pattern", "")]

    period = "全期間" if args.days <= 0 else f"直近{args.days}日"
    if not records:
        print(f"{period}: 該当する除外記事はありません。")
        return 0

    print(f"{period}の除外記事: {len(records)}件")
    counts = Counter(r.get("reason", "unknown") for r in records)
    summary = "  ".join(
        f"{REASON_LABELS.get(k, k)}:{v}" for k, v in counts.most_common()
    )
    print(f"内訳: {summary}")

    if args.list:
        print_list(records)
    else:
        print_grouped(records)

    print("\n---")
    print("誤って除外されている記事があれば、scripts/fetch_news.py の")
    print("_DENY_RETROSPECTIVE_PATTERN / _DENY_KEYWORDS などを調整してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
