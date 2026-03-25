#!/usr/bin/env python3
"""デバッグ用: /tmp/youtube_upload.log の内容をGitHub Issueに投稿する。"""
import os, json, urllib.request, datetime, sys

log_path = "/tmp/youtube_upload.log"
try:
    with open(log_path) as f:
        log = f.read()[:3000]
except Exception:
    log = "(ログなし)"

title = "[DEBUG] YouTube Upload Log " + datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M")
body = "```\n" + log + "\n```"
payload = json.dumps({"title": title, "body": body}).encode()

repo = os.environ.get("GH_REPO", "")
token = os.environ.get("GH_TOKEN", "")
if not repo or not token:
    print("GH_REPO / GH_TOKEN が未設定")
    sys.exit(0)

req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/issues",
    data=payload,
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())
    print(f"Issue作成: {data.get('html_url')}")
