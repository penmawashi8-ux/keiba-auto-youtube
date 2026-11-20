#!/usr/bin/env bash
# 夢の対決シミュレーションの出力を research-output ブランチの sim/ に追加・上書き保存する。
# 使い方: save_sim_outputs.sh "コミットの見出し" ファイル...
# 作業ツリーを汚さないよう、別ディレクトリの worktree で research-output を操作する。
set -euo pipefail
title="$1"; shift
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
wt=$(mktemp -d)
git fetch -q origin research-output
git worktree add -q -B research-output "$wt" FETCH_HEAD
mkdir -p "$wt/sim"
for f in "$@"; do
  [ -f "$f" ] || continue
  b=$(basename "$f")
  # GitHub は 100MB を超えるファイルを受け付けないので、20MB を超える JSON は gzip で保存する
  # （復元は restore_sim_file.sh が .gz を優先して展開する）
  if [ "${b##*.}" = json ] && [ "$(wc -c < "$f")" -gt 20000000 ]; then
    gzip -9 -c "$f" > "$wt/sim/$b.gz"
    git -C "$wt" rm -q --cached --ignore-unmatch "sim/$b"; rm -f "$wt/sim/$b"
  else
    cp "$f" "$wt/sim/"
  fi
done
git -C "$wt" add -f sim
git -C "$wt" commit -q -m "${title} (run ${GITHUB_RUN_ID}) [skip ci]" || echo "変更なし"
# 別のワークフローが同時に保存していると push が拒否されるので、取り込み直して再試行する
ok=0
for i in 1 2 3 4; do
  git -C "$wt" push -q origin research-output && { ok=1; break; }
  sleep $((i * 5))
  git -C "$wt" pull -q --rebase origin research-output
done
git worktree remove --force "$wt"
[ "$ok" = 1 ] || { echo "research-output への保存に失敗"; exit 1; }
