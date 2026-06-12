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

## YouTubeサムネイル設定について

このチャンネルは **YouTube Shorts（縦1080×1920）** を投稿している。

### 現在の状況（2026年6月更新）

**公式 `thumbnails().set()` API が Shorts でも有効になった。**
以前は HTTP 200 を返すだけで無視されていたが、YouTube側の仕様変更により
コード変更なしでカスタムサムネイルが反映されるようになった。

**重要: サムネイル画像は動画と同じ縦解像度（1080×1920）のまま送ること。**
ffmpegのフレーム抽出時に `-s 1280x720` などで横長にリサイズすると、
潰れた横長画像がそのままShortsのサムネイルとして表示されてしまう（2026年6月に発生・修正済み）。

正しい抽出コマンド:
```
ffmpeg -y -ss 0.5 -i video.mp4 -vframes 1 -q:v 2 thumbnail.jpg
```
（`-s` によるリサイズ指定は禁止。動画ネイティブの縦横比を維持する）

### 過去の調査記録（参考）

API が無効だった時代に試して全滅した方法:

| 方法 | 当時の結果 |
|---|---|
| 公式 `thumbnails().set()` API | HTTP 200 を返すが無反応（現在は有効） |
| YouTube Studio 内部API `youtubei/v1/video_manager/metadata_update` | HTTP 500 backendError（SAPISIDクッキーが必要と推測） |
| Playwright + ブラウザ自動操作 | GitHub Actions ではクッキーのセッションが維持できず断念 |

内部API・Playwright への再挑戦は不要。公式APIで足りる。
