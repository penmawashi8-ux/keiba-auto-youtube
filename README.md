# keiba-auto-youtube

競馬ニュースを自動収集し、AIナレーション付きYouTubeショート動画を自動投稿するパイプラインです。
GitHub Actions で毎日 09:00 JST に自動実行されます。すべて**無料サービスのみ**使用します。

---

## 全体の仕組み

```
RSSフィード（netkeiba / スポニチ）
      ↓ fetch_news.py
  news.json（最新3件）
      ↓ generate_script.py（Gemini API）
  script.txt（60秒ナレーション脚本）
      ↓ generate_audio.py（Google Cloud TTS）
  output/audio.mp3
      ↓ generate_video.py（ffmpeg）
  output/video.mp4（1080×1920 縦型）
      ↓ upload_youtube.py（YouTube Data API v3）
  YouTube Shorts 公開投稿
      ↓
  posted_ids.txt を git commit & push（重複防止）
```

---

## 必要なアカウントと取得するもの

### 1. GitHubアカウント
このリポジトリを fork またはクローンして使用します。

### 2. Googleアカウント（YouTube兼用）
すべてのGoogle サービスを同一アカウントで使用できます。

---

### 3. Gemini APIキー取得（Google AI Studio）

1. [Google AI Studio](https://aistudio.google.com/) にアクセス
2. 右上の「Get API key」をクリック
3. 「Create API key」→ プロジェクトを選択して生成
4. 表示されたキーをコピー → GitHub Secrets に `GEMINI_API_KEY` として登録

**無料枠**: gemini-1.5-flash は1分あたり15リクエスト、1日1500リクエストまで無料

---

### 4. Google Cloud TTS 有効化とサービスアカウント作成

#### 4-1. プロジェクト作成・API有効化

1. [Google Cloud Console](https://console.cloud.google.com/) にアクセス
2. 新しいプロジェクトを作成（例: `keiba-youtube`）
3. 左メニュー「APIとサービス」→「ライブラリ」
4. 「Cloud Text-to-Speech API」を検索して「有効にする」

**無料枠**: WaveNet音声は月100万文字まで無料（標準音声は月400万文字）

#### 4-2. サービスアカウント作成

1. 「APIとサービス」→「認証情報」→「認証情報を作成」→「サービスアカウント」
2. 名前を入力（例: `keiba-tts`）→「作成して続行」
3. ロール: 「Cloud Text-to-Speech 管理者」を選択 → 「完了」
4. 作成したサービスアカウントをクリック →「キー」タブ →「鍵を追加」→「新しい鍵を作成」→「JSON」
5. ダウンロードされたJSONファイルを**1行のテキスト**に変換:

```bash
# Mac/Linux の場合
cat your-key.json | tr -d '\n'

# または Python で
python3 -c "import json; f=open('your-key.json'); print(json.dumps(json.load(f)))"
```

6. 出力された1行テキストを GitHub Secrets に `GOOGLE_APPLICATION_CREDENTIALS_JSON` として登録

---

### 5. YouTube Data API 有効化と OAuth2 設定

#### 5-1. YouTube Data API v3 有効化

1. Google Cloud Console の同じプロジェクトで
2. 「APIとサービス」→「ライブラリ」→「YouTube Data API v3」→「有効にする」

**無料枠**: 1日10,000ユニット（動画アップロードは1,600ユニット/本）

#### 5-2. OAuth2 クライアントID作成

1. 「APIとサービス」→「認証情報」→「認証情報を作成」→「OAuthクライアントID」
2. アプリの種類: 「デスクトップアプリ」を選択
3. 名前を入力（例: `keiba-youtube-uploader`）→「作成」
4. **クライアントID** と **クライアントシークレット** をコピー

#### 5-3. OAuth 同意画面の設定

1. 「OAuth同意画面」→「外部」を選択 → 「作成」
2. アプリ名・メールアドレスを入力 → 「保存して次へ」
3. スコープ: 「スコープを追加または削除」→ `youtube.upload` を追加
4. テストユーザー: 自分のGoogleアカウントのメールアドレスを追加

---

## GitHubリポジトリのSecretsに登録する5つの値

リポジトリの「Settings」→「Secrets and variables」→「Actions」→「New repository secret」

| Secret名 | 値の取得元 |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | GCPサービスアカウントJSONの1行化テキスト |
| `YOUTUBE_CLIENT_ID` | GCP OAuthクライアントID |
| `YOUTUBE_CLIENT_SECRET` | GCP OAuthクライアントシークレット |
| `YOUTUBE_REFRESH_TOKEN` | `get_token.py` 実行で取得（次節参照） |

---

## get_token.py の実行方法（refresh_token取得）

### 前提
- ローカルにPythonがインストールされていること
- `pip install google-auth-oauthlib` が完了していること

### 手順

1. `get_token.py` を開いて、取得したクライアント情報を設定:

```python
CLIENT_ID = "your-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "your-client-secret"
```

2. スクリプトを実行:

```bash
python get_token.py
```

3. ブラウザが自動で開くので、動画をアップロードしたいYouTubeチャンネルのGoogleアカウントでログイン
4. 「keiba-youtube-uploader がYouTubeへのアクセスを求めています」→「許可」をクリック
5. ターミナルに `YOUTUBE_REFRESH_TOKEN` が表示されるので GitHub Secrets に登録

> **注意**: `youtube_token.json` は機密情報を含むため、`.gitignore` に追加してリポジトリにコミットしないでください。

---

## 手動実行テストの方法

GitHub リポジトリの「Actions」タブ →「Keiba News YouTube Auto Post」→「Run workflow」ボタンをクリック

ローカルでテストする場合（各スクリプトを順番に実行）:

```bash
# 環境変数を設定
export GEMINI_API_KEY="your-key"
export GOOGLE_APPLICATION_CREDENTIALS_JSON='{"type":"service_account",...}'
export YOUTUBE_CLIENT_ID="your-client-id"
export YOUTUBE_CLIENT_SECRET="your-secret"
export YOUTUBE_REFRESH_TOKEN="your-refresh-token"

# 順番に実行
python scripts/fetch_news.py
python scripts/generate_script.py
python scripts/generate_audio.py
python scripts/generate_video.py
python scripts/upload_youtube.py
```

---

## assets/background.jpg の準備

`assets/background.jpg` は **1080×1920px（縦型）** の画像を用意してください。

作成方法の例:

```bash
# ffmpegで競馬場のグリーンをイメージした背景を生成
ffmpeg -f lavfi -i "color=c=0x1a5c2a:s=1080x1920:r=1" \
  -frames:v 1 assets/background.jpg

# または ImageMagick を使用
convert -size 1080x1920 gradient:"#1a5c2a-#0d2e15" \
  -gravity Center -pointsize 100 -fill white \
  -annotate 0 "keiba" assets/background.jpg
```

フリー素材サイト（Unsplash、Pixabay）から競馬場・芝・馬の画像をダウンロードして
1080×1920 にリサイズする方法もあります。

---

## カスタマイズ方法

### 声の変更（generate_audio.py）

`scripts/generate_audio.py` の `TTS_VOICE_NAME` を変更します:

| 声名 | 特徴 |
|---|---|
| `ja-JP-Wavenet-A` | 女性（明るい） |
| `ja-JP-Wavenet-B` | 男性（デフォルト、実況向き） |
| `ja-JP-Wavenet-C` | 男性（落ち着いた） |
| `ja-JP-Wavenet-D` | 女性（落ち着いた） |
| `ja-JP-Neural2-B` | 男性（より自然な発音） |
| `ja-JP-Neural2-D` | 女性（より自然な発音） |

### RSSフィードの変更（fetch_news.py）

`scripts/fetch_news.py` の `RSS_FEEDS` リストを編集します:

```python
RSS_FEEDS = [
    "https://news.netkeiba.com/?pid=news_rss",
    "https://www.sponichi.co.jp/gamble/rss/atom/index.rdf",
    # 追加したいRSSフィードのURLをここに追加
]
```

### 動画フォーマットの変更（generate_video.py）

- `VIDEO_WIDTH` / `VIDEO_HEIGHT`: 解像度（デフォルト: 1080×1920 縦型）
- `FONT_SIZE`: フォントサイズ（デフォルト: 52）
- `TEXT_Y_RATIO`: テキスト縦位置（0.0〜1.0、デフォルト: 0.75）
- `TTS_SPEAKING_RATE`: 読み上げ速度（0.25〜4.0、デフォルト: 1.1）

---

## ファイル構成

```
/
├── .github/
│   └── workflows/
│       └── keiba_news.yml      # GitHub Actions ワークフロー
├── scripts/
│   ├── fetch_news.py           # RSSニュース取得
│   ├── generate_script.py      # Gemini APIで脚本生成
│   ├── generate_audio.py       # Google TTSで音声生成
│   ├── generate_video.py       # ffmpegで動画生成
│   └── upload_youtube.py       # YouTube APIでアップロード
├── assets/
│   └── background.jpg          # 背景画像（要準備）
├── output/                     # 生成ファイル（自動作成）
│   ├── audio.mp3
│   └── video.mp4
├── get_token.py                # OAuth2 refresh_token取得ツール
├── news.json                   # 取得したニュース（自動生成）
├── script.txt                  # 生成した脚本（自動生成）
├── posted_ids.txt              # 投稿済みID管理（gitで管理）
└── requirements.txt
```

---

## トラブルシューティング

### GOOGLE_APPLICATION_CREDENTIALS_JSON のJSONが不正と言われる

JSONをコピーする際に改行が入っていないか確認してください:

```bash
python3 -c "
import json
s = open('your-key.json').read()
print(json.dumps(json.loads(s)))
"
```

### YouTube アップロードが quotaExceeded エラーになる

YouTube Data API の1日の無料クォータ（10,000ユニット）を超えています。
翌日になるとリセットされます。

### ffmpeg で日本語テキストが表示されない

GitHub Actions のステップで `fonts-noto-cjk` が正しくインストールされているか確認してください。
ワークフローログで `apt-get install fonts-noto-cjk` のステップを確認してください。
