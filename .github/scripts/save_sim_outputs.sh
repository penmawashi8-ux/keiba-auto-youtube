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
  [ -f "$f" ] && cp "$f" "$wt/sim/"
done
git -C "$wt" add -f sim
git -C "$wt" commit -q -m "${title} (run ${GITHUB_RUN_ID}) [skip ci]" || echo "変更なし"
git -C "$wt" push -q origin research-output
git worktree remove --force "$wt"
