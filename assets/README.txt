background.jpg をこのディレクトリに配置してください。

要件:
- ファイル名: background.jpg
- サイズ: 1080x1920px（縦型、YouTubeショート対応）
- フォーマット: JPEG

作成例（ffmpeg使用）:
  ffmpeg -f lavfi -i "color=c=0x1a5c2a:s=1080x1920:r=1" -frames:v 1 assets/background.jpg

詳細は README.md の「assets/background.jpg の準備」セクションを参照してください。
