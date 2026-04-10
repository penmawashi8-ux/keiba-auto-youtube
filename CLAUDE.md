# プロジェクトルール

## ライブラリ制約

### Pillow (PIL) は完全使用禁止
- **新規・既存コードを問わず** `from PIL import ...` / `import PIL` を一切使わないこと
- 過去のコードにPillowが残っていた場合も、触る機会があれば即座にffmpegに置き換えること
- 背景画像・フレーム画像の生成は **ffmpeg の lavfi/geq フィルター、drawtext、またはdrawbox** で行う
- サムネイルは **ffmpegのフレーム抽出**（`ffmpeg -ss 0.5 -i video.mp4 -vframes 1 thumbnail.jpg`）で生成する
- numpy も Pillow と一緒に使われていたため、同様に使用禁止

### ffmpeg で動画処理を行う際の注意
- `drawbox` はGitHub Actions環境で失敗するケースがあるため使用禁止
- テキスト背景パネルは `drawtext` の `box=1:boxcolor=...:boxborderw=N` オプションで実現する
- 日本語テキストは `text=` ではなく `textfile=` を使ってファイル経由で渡す（エスケープ問題を回避）
- ASS字幕の色は `&HAABBGGRR` 形式（ABGRの順）

### TTS (edge-tts) ボイス
- ニュースシリーズ: `ja-JP-KeitaNeural`（男性）
- 名馬列伝シリーズ: `ja-JP-NanamiNeural`（女性）
- `ja-JP-NaokiNeural` は存在しないため使用禁止
