#!/usr/bin/env python3
"""script.txtを読み込みGoogle Cloud TTSで音声ファイルを生成する。"""

import json
import os
import sys
from pathlib import Path

from google.cloud import texttospeech

SCRIPT_TXT = "script.txt"
OUTPUT_DIR = "output"
OUTPUT_AUDIO = f"{OUTPUT_DIR}/audio.mp3"

# 音声設定
TTS_LANGUAGE = "ja-JP"
TTS_VOICE_NAME = "ja-JP-Wavenet-B"
TTS_SPEAKING_RATE = 1.1  # 実況風に少し速め


def setup_credentials() -> None:
    """GOOGLE_APPLICATION_CREDENTIALS_JSON 環境変数からキーファイルを生成する。"""
    creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not creds_json:
        print("[エラー] 環境変数 GOOGLE_APPLICATION_CREDENTIALS_JSON が設定されていません。", file=sys.stderr)
        sys.exit(1)

    creds_path = Path("/tmp/gcp_credentials.json")
    try:
        # JSON文字列の検証
        json.loads(creds_json)
        creds_path.write_text(creds_json, encoding="utf-8")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds_path)
        print(f"認証情報を {creds_path} に書き込みました。")
    except json.JSONDecodeError as e:
        print(f"[エラー] GOOGLE_APPLICATION_CREDENTIALS_JSON が不正なJSONです: {e}", file=sys.stderr)
        sys.exit(1)


def generate_audio(script: str) -> None:
    client = texttospeech.TextToSpeechClient()

    synthesis_input = texttospeech.SynthesisInput(text=script)

    voice = texttospeech.VoiceSelectionParams(
        language_code=TTS_LANGUAGE,
        name=TTS_VOICE_NAME,
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=TTS_SPEAKING_RATE,
    )

    print(f"Google Cloud TTS API に音声合成リクエスト送信中（声: {TTS_VOICE_NAME}）...")
    try:
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
    except Exception as e:
        print(f"[エラー] TTS API 呼び出し失敗: {e}", file=sys.stderr)
        sys.exit(1)

    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    Path(OUTPUT_AUDIO).write_bytes(response.audio_content)
    size_kb = len(response.audio_content) / 1024
    print(f"音声ファイルを {OUTPUT_AUDIO} に保存しました（{size_kb:.1f} KB）。")


def main() -> None:
    print("=== 音声生成開始 ===")

    setup_credentials()

    script_path = Path(SCRIPT_TXT)
    if not script_path.exists():
        print(f"[エラー] {SCRIPT_TXT} が見つかりません。generate_script.py を先に実行してください。", file=sys.stderr)
        sys.exit(1)

    script = script_path.read_text(encoding="utf-8").strip()
    if not script:
        print(f"[エラー] {SCRIPT_TXT} が空です。", file=sys.stderr)
        sys.exit(1)

    print(f"脚本文字数: {len(script)} 文字")
    generate_audio(script)


if __name__ == "__main__":
    main()
