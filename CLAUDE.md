# プロジェクトルール

## ライブラリ制約

### Pillow (PIL) は完全廃止
- 新規コードで `from PIL import ...` / `import PIL` を使わないこと
- 背景画像・フレーム画像の生成は **ffmpeg の lavfi/geq フィルター** または外部API（Pixabay, HuggingFace）で行う
- 既存の generate_video.py 内の Pillow 使用箇所も、触る機会があれば順次 ffmpeg 化する
