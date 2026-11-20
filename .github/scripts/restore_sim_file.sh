#!/usr/bin/env bash
# research-output の sim/<名前> を sim/ に復元する（大きいファイルは <名前>.gz で保存されているので展開する）。
# 使い方: restore_sim_file.sh 名前...   （事前に git fetch origin research-output で FETCH_HEAD を作っておく）
# 見つからないファイルは作らない（終了コードは常に 0）。
mkdir -p sim
for f in "$@"; do
  if git cat-file -e "FETCH_HEAD:sim/$f.gz" 2>/dev/null; then
    git show "FETCH_HEAD:sim/$f.gz" | gunzip > "sim/$f"
  elif git cat-file -e "FETCH_HEAD:sim/$f" 2>/dev/null; then
    git show "FETCH_HEAD:sim/$f" > "sim/$f"
  else
    rm -f "sim/$f"; echo "キャッシュなし $f"; continue
  fi
  echo "復元 $f: $(wc -c < "sim/$f") bytes"
done
exit 0
