#!/usr/bin/env python3
"""
スプリンターズS2026 最終予想のスライド形式動画（横型 1280×720）を生成する。

紺背景＋細い枠線、金色の見出し、白の箇条書き、フッター「SPRINTERS STAKES 2026 • NN」
というシンプルなスライドを1枚ずつ表示し、スライドごとのナレーション（edge-tts）を乗せる。
画面上の字幕は出さない。BGMは make_prediction_bgm.py のオリジナル曲を敷き、声に合わせて自動で音量を下げる。

前提: scripts/create_sprinters_prediction.py で news.json を生成済み（アップロード用メタ）。
出力: output/landscape_video_0.mp4 / output/thumbnail_0.jpg
      （upload_landscape_youtube.py がそのまま使用する）

Pillow / numpy / drawbox は使わず、ffmpeg の color・pad・drawtext(textfile=) のみで描画する。
"""

import asyncio
import glob
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import edge_tts

sys.path.insert(0, str(Path(__file__).parent))
from make_prediction_bgm import make_bgm  # noqa: E402
from reading_utils import apply_readings  # noqa: E402

OUTPUT_DIR = Path("output")
VIDEO_PATH = OUTPUT_DIR / "landscape_video_0.mp4"
THUMB_PATH = OUTPUT_DIR / "thumbnail_0.jpg"

VOICE = os.environ.get("TTS_VOICE", "ja-JP-KeitaNeural")
RATE = os.environ.get("TTS_RATE", "+10%")

W, H = 1280, 720
FPS = 30
BG = "0x121829"
FRAME = "0xB4BED2"
TITLE_COLOR = "0xFFCB47"
BODY_COLOR = "0xFFFFFF"
FOOTER_COLOR = "0x8A94A8"
FOOTER_LABEL = "SPRINTERS STAKES 2026"

LEAD_IN = 0.6   # スライド表示からナレーション開始までの間
TAIL = 0.9      # ナレーション終了から次のスライドまでの間
END_TAIL = 3.0  # 最終スライドの余韻（BGMのフェードアウト用）
BGM_VOLUME = float(os.environ.get("BGM_VOLUME", "0.26"))

# (見出し, 箇条書き, ナレーション)
SLIDES: list[tuple[str, list[str], str]] = [
    (
        "最終予想・重馬場想定",
        [
            "9月27日（日） 中山・芝1200m",
            "スプリンターズステークス",
            "土曜の大雨を想定して予想！",
        ],
        "9月27日日曜、中山競馬場・芝1200メートルで行われるスプリンターズステークス。"
        "秋のジーワン開幕戦、電撃の6ハロン決戦の最終予想をお届けする。"
        "今回のポイントは土曜の大雨。馬場は稍重から重まで悪化すると想定して印を打った。",
    ),
    (
        "想定する展開",
        [
            "6 ワールズエンド　 12 ブラックチャリス",
            "14 ウインカーネリアン　 16 ピューロマジック",
            "外の2頭が主張 → 前半は速くなる想定",
        ],
        "逃げ・先行型は6番ワールズエンド、12番ブラックチャリス、"
        "14番ウインカーネリアン、16番ピューロマジックと揃った。"
        "外の2頭が主張するため、前半のペースは速くなるだろう。",
    ),
    (
        "展開のポイント",
        [
            "ハイペースでも好位勢が残る近年の傾向",
            "稍重～重なら「前＋内」が有利と想定",
            "速い流れを好位で追走できる馬を軸に",
        ],
        "ただ、このレースは近年、ハイペースでも好位の馬が勝っている。"
        "さらに稍重から重になれば、2018年のように前と内が残りやすい。"
        "そこで今回は、速い流れを好位で追走して止まらない馬を軸にする。",
    ),
    (
        "◎ 11 ルガル",
        [
            "2024年の勝ち馬",
            "ハイペースを好位で粘り切った実績",
            "スタート安定＋復調気配",
            "タフな馬場はむしろ歓迎",
            "重馬場なら粘り強さが最大限に生きる！",
        ],
        "本命は11番ルガル。"
        "2024年、ピューロマジックが作ったハイペースを好位で粘り切って勝った馬だ。"
        "まさに今年と同じ型のレースの勝ち馬と言える。"
        "スタートが安定してから復調しており、タフな条件もむしろ向く。"
        "重馬場なら、この馬の粘り強さが最大限に生きるはずだ。",
    ),
    (
        "○ 5 サウンドモリアーナ",
        [
            "前走キーンランドカップでGⅠ馬を撃破",
            "3枠5番の好枠",
            "勢いは本物",
            "ただし道悪適性は未知数 → 本命から一段下げ",
        ],
        "対抗は5番サウンドモリアーナ。"
        "前走のキーンランドカップでジーワン馬を破った勢いは本物。"
        "3枠5番という好枠も引いた。"
        "ただ、道悪の適性はまだ未知数。その分、本命からは一段下げた。",
    ),
    (
        "▲ 15 フリッカージャブ",
        [
            "重馬場の前哨戦で大きく飛躍",
            "今回の雨は追い風",
            "能力＋馬場適性なら頭まで十分",
            "割引材料は8枠15番の外枠",
        ],
        "単穴は15番フリッカージャブ。"
        "重馬場の前哨戦で大きく飛躍した馬で、今回の雨はまさに追い風だ。"
        "割引材料は8枠15番の外枠だけ。能力と馬場適性なら、頭まで十分にある。",
    ),
    (
        "△ 押さえ",
        [
            "3 ママコチャ　／　4 ジューンブレア",
            "8 エーティーマクフィ　／　14 ウインカーネリアン",
            "内枠で好位を取れる馬＋道悪妙味",
        ],
        "押さえは4頭。内枠で好位を取れる3番ママコチャと4番ジューンブレア。"
        "道悪で妙味が出る8番エーティーマクフィ。"
        "そして昨年の覇者で、外からでも番手を取れる14番ウインカーネリアン。",
    ),
    (
        "☆ 3着穴 1 レッドモンレーヴ",
        [
            "近5年の3着は差し・追い込みが中心",
            "高松宮記念2着の末脚",
            "3着候補として押さえる",
        ],
        "さらに3着穴として1番レッドモンレーヴを推したい。"
        "近5年の3着は差しと追い込みばかり。"
        "高松宮記念2着の末脚を、3着候補として押さえておく。",
    ),
    (
        "消し寄りの人気馬",
        [
            "スターアニス",
            "初の1200m・初の古馬相手・道悪と不安材料が重なる",
            "16 ピューロマジック",
            "大外枠からハナを主張 → 最後は苦しくなる想定",
        ],
        "逆に評価を下げるのは2頭。"
        "スターアニスは初めての1200メートル、初めての古馬相手、そして道悪と、不安材料が重なった。"
        "16番ピューロマジックは大外枠。ハナを主張するために脚を使わされる分、最後は苦しくなるとみる。",
    ),
    (
        "買い目",
        [
            "馬連：11 → 5・15・3・4・8・14（6点）",
            "三連複フォーメーション（11点）",
            "　1頭目：11",
            "　2頭目：5・15",
            "　3頭目：1・3・4・5・8・14・15",
        ],
        "買い目の例を紹介する。"
        "馬連は11番から、5番、15番、3番、4番、8番、14番への6点。"
        "三連複はフォーメーションで、1頭目が11番、2頭目が5番と15番、"
        "3頭目が1番、3番、4番、5番、8番、14番、15番の11点だ。",
    ),
    (
        "当日の最終チェック",
        [
            "① 良～稍重まで回復",
            "　→ 本命を5サウンドモリアーナへ、スターアニスを押さえに",
            "② 外差しばかり届く",
            "　→ 内が荒れたサイン。フリッカージャブを本命へ",
            "③ 内の先行馬が残る → この予想のままでOK",
        ],
        "最後に、当日チェックしてほしいポイントが3つある。"
        "1つ目。馬場発表が良から稍重まで回復していたら、本命をサウンドモリアーナに上げ、スターアニスを押さえに戻す。"
        "2つ目。午前の芝のレースで外からの差しばかり届いているなら、内が荒れているサイン。"
        "フリッカージャブを本命に上げ、3着候補に差し馬を増やそう。"
        "3つ目。内の先行馬が残っているなら、この予想のままでオーケーだ。",
    ),
    (
        "最終結論",
        [
            "◎ 11 ルガル",
            "○ 5 サウンドモリアーナ",
            "▲ 15 フリッカージャブ",
            "△ 3・4・8・14　 ☆ 1 レッドモンレーヴ",
            "最後は当日の馬場を最優先！",
        ],
        "まとめよう。本命は11番ルガル。対抗は5番サウンドモリアーナ。単穴は15番フリッカージャブ。"
        "押さえは3番、4番、8番、14番。3着穴に1番レッドモンレーヴ。"
        "最後は当日の馬場を優先して判断してほしい。",
    ),
    (
        "みんなの本命は？",
        [
            "スプリンターズステークスを楽しもう！",
            "本命馬をコメントで教えてください！",
        ],
        "スプリンターズステークスを楽しもう。"
        "みんなの本命はどの馬？コメントで教えてくれ！",
    ),
]


def find_font() -> str:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    hits = sorted(glob.glob("/usr/share/fonts/**/*CJK*Bold*.tt[co]", recursive=True)) \
        or sorted(glob.glob("/usr/share/fonts/**/*CJK*.tt[co]", recursive=True))
    if not hits:
        sys.exit("[エラー] 日本語フォント（Noto Sans CJK）が見つかりません。")
    return hits[0]


def _esc(path: str | Path) -> str:
    return str(path).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


async def _tts(text: str, path: Path) -> None:
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(path))


def synthesize(text: str, path: Path) -> None:
    text = apply_readings(text)
    last_err = None
    for attempt in range(1, 4):
        try:
            asyncio.run(_tts(text, path))
            if path.exists() and path.stat().st_size > 0:
                return
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"  TTS失敗 (attempt {attempt}/3): {e}", file=sys.stderr)
    raise RuntimeError(f"TTS生成に失敗しました: {last_err}")


def render_slide_png(idx: int, title: str, lines: list[str], font: str,
                     tmp: Path, out_png: Path) -> None:
    """スライド1枚を静止画で描画する（枠線は pad の重ねがけで表現）。"""
    fp = _esc(font)
    inner_w, inner_h = 1202, 644
    filters = [
        # 2px の枠線 → 外側余白
        f"pad={inner_w + 4}:{inner_h + 4}:2:2:color={FRAME}",
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={BG}",
    ]

    tf = tmp / f"s{idx:02d}_title.txt"
    tf.write_text(title, encoding="utf-8")
    filters.append(
        f"drawtext=textfile='{_esc(tf)}':fontfile='{fp}':fontsize=42"
        f":fontcolor={TITLE_COLOR}:x=74:y=76"
    )

    body_y, line_h = 166, 56
    body_fs = 30 if len(lines) <= 4 else 29
    for li, line in enumerate(lines):
        lf = tmp / f"s{idx:02d}_l{li}.txt"
        lf.write_text(line, encoding="utf-8")
        filters.append(
            f"drawtext=textfile='{_esc(lf)}':fontfile='{fp}':fontsize={body_fs}"
            f":fontcolor={BODY_COLOR}:x=84:y={body_y + li * line_h}"
        )

    ff = tmp / f"s{idx:02d}_footer.txt"
    ff.write_text(f"{FOOTER_LABEL}  •  {idx:02d}", encoding="utf-8")
    filters.append(
        f"drawtext=textfile='{_esc(ff)}':fontfile='{fp}':fontsize=22"
        f":fontcolor={FOOTER_COLOR}:x=74:y=651"
    )

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c={BG}:s={inner_w}x{inner_h}",
         "-vf", ",".join(filters), "-frames:v", "1", str(out_png)],
        check=True,
    )


def render_segment(png: Path, audio: Path, out: Path, tail: float = TAIL) -> float:
    """静止スライド＋ナレーションの1セグメントを書き出す。長さ(秒)を返す。"""
    dur = LEAD_IN + audio_duration(audio) + tail
    delay_ms = int(LEAD_IN * 1000)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-loop", "1", "-framerate", str(FPS), "-i", str(png),
         "-i", str(audio),
         "-filter_complex",
         f"[1:a]adelay={delay_ms}:all=1,aresample=44100,"
         f"aformat=channel_layouts=stereo,apad[a]",
         "-map", "0:v", "-map", "[a]",
         "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
         "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
         str(out)],
        check=True,
    )
    return dur


def mix_bgm(video: Path, bgm: Path, total: float, out: Path) -> None:
    """BGMをループさせてナレーションの下に敷く。声が出ている間は自動で音量を下げる。"""
    fade_out_start = max(0.0, total - END_TAIL + 0.5)
    fc = (
        f"[1:a]volume={BGM_VOLUME},afade=t=in:d=2,"
        f"afade=t=out:st={fade_out_start:.2f}:d={END_TAIL - 0.5:.2f},"
        f"aformat=sample_rates=44100:channel_layouts=stereo[bgm];"
        f"[0:a]asplit=2[voice][key];"
        f"[bgm][key]sidechaincompress=threshold=0.03:ratio=2:attack=30:release=600[duck];"
        f"[voice][duck]amix=inputs=2:duration=first:normalize=0,"
        f"alimiter=limit=0.95[a]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(video), "-stream_loop", "-1", "-i", str(bgm),
         "-filter_complex", fc,
         "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)],
        check=True,
    )


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    font = find_font()
    print(f"フォント: {font}")
    print(f"TTS: voice={VOICE} rate={RATE}")

    with tempfile.TemporaryDirectory(prefix="sprinters_slides_") as tmp_dir:
        tmp = Path(tmp_dir)
        segments: list[Path] = []
        total = 0.0
        for i, (title, lines, narration) in enumerate(SLIDES, start=1):
            png = tmp / f"slide_{i:02d}.png"
            mp3 = tmp / f"slide_{i:02d}.mp3"
            seg = tmp / f"seg_{i:02d}.mp4"
            render_slide_png(i, title, lines, font, tmp, png)
            synthesize(narration, mp3)
            tail = END_TAIL if i == len(SLIDES) else TAIL
            dur = render_segment(png, mp3, seg, tail)
            total += dur
            segments.append(seg)
            print(f"  [{i:02d}] {title}  {dur:.1f}s")

        concat_list = tmp / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{s}'\n" for s in segments), encoding="utf-8"
        )
        narration_video = tmp / "narration.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(narration_video)],
            check=True,
        )
        mix_bgm(narration_video, make_bgm(tmp / "bgm.wav"), total, VIDEO_PATH)

    # サムネイルは動画からのフレーム抽出（リサイズしない）
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", str(VIDEO_PATH),
         "-vframes", "1", "-q:v", "2", str(THUMB_PATH)],
        check=True,
    )

    size_mb = VIDEO_PATH.stat().st_size / 1024 / 1024
    print(f"✅ {VIDEO_PATH} ({size_mb:.1f} MB, {total:.1f}s, {len(SLIDES)}枚)")
    print(f"✅ {THUMB_PATH}")


if __name__ == "__main__":
    main()
