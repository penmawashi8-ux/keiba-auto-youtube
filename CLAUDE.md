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

## YouTubeサムネイル設定について（調査済み・断念）

このチャンネルは **YouTube Shorts（縦1080×1920）** を投稿している。
Shorts はサムネイルをプログラムから設定できない。以下は試みて全滅した方法の記録。

### 試みた方法と結果

| 方法 | 結果 | 理由 |
|---|---|---|
| 公式 `thumbnails().set()` API | HTTP 200 を返すが実際には無反応 | Shorts は API で設定しても無視される |
| YouTube Studio 内部API `youtubei/v1/video_manager/metadata_update` | HTTP 500 backendError | OAuth Bearer トークンだけでは不十分（ブラウザのSAPISIDクッキーが必要と推測） |
| Playwright + ブラウザ自動操作 | クッキーが毎回期限切れ | GitHub Actions 環境ではセッションが維持できない |

### 内部APIで試したリクエスト形式（全て HTTP 500 または 400）

- `clientName: "YOUTUBE_STUDIO"` → HTTP 400（無効なクライアント名）
- `clientName: "WEB_CREATOR"` + `user.delegationContext` → HTTP 400
- `clientName: "WEB_CREATOR"` + `user.onBehalfOfUser` + `encryptedVideoId` + `thumbnailDetails.stillImageTime` → HTTP 500
- `clientName: "WEB_CREATOR"` + `user.onBehalfOfUser` + `encryptedVideoId` + `thumbnail.stillImageTime` → HTTP 500
- `clientName: "WEB_CREATOR"` + `user.onBehalfOfUser` + `videoId` + `thumbnailDetails.stillImageTime` → HTTP 400

### 現在の対応

動画の冒頭 1.5 秒をタイトルカードフレームとして設計し、YouTube の自動フレーム選択に任せている。
サムネイル関連のコードやワークフローステップは削除済み。再挑戦は不要。
