#!/usr/bin/env python3
"""
天皇賞(春)2026 予想動画用の news.json と output/script_0.txt を生成する。

横向き(1280×720)動画用。landscape_video.py → upload_landscape_youtube.py で処理する。
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"

# ──────────────────────────────────────────────────────────────────────────────
# 天皇賞(春)2026 レース情報
# landscape_video.py が race_name / grade / date / venue / distance / thumbnail_hook を参照する
# ──────────────────────────────────────────────────────────────────────────────
NEWS_ENTRY = {
    "id": "tennosho_haru_2026_prediction",
    "title": "【天皇賞(春)2026枠順確定予想】PR122クロワデュノールより強い馬がいる！3200mデータで本命アドマイヤテラ断言",
    "url": "https://www.jra.go.jp/keiba/g1/tennosho_spring/syutsuba.html",
    "summary": (
        "2026年5月3日(日)京都競馬場・芝3200m 天皇賞(春)G1の枠順が確定。"
        "出走メンバー最高プレレーティング122を持つクロワデュノール(4枠7番・北村友一騎手)が話題の中心だが、"
        "3200m初挑戦という致命的な不安がある。"
        "本命はアドマイヤテラ(2枠3番・武豊騎手)。PR115で友道康夫調教師との黄金コンビ、"
        "好枠2枠3番からの先行策で長距離の末脚を温存できる。"
        "対抗はヘデントール(7枠12番・C.ルメール騎手)。PR118で長距離路線での実績を誇り、"
        "ルメール騎手の天皇賞での勝負強さも見逃せない。"
        "3着はタガノデュード(6枠11番・古川吉洋騎手)。PR117で長距離への安定感が光る。"
    ),
    "image_url": "",
    "published_date": "2026-05-02T20:00:00+09:00",
    "race_name": "天皇賞（春）",
    "grade": "G1",
    "date": "5月3日（日）",
    "venue": "京都",
    "distance": "芝3200m",
    "thumbnail_hook": "PR122より強い馬がいる",
    "horses": ["アドマイヤテラ", "クロワデュノール", "ヘデントール", "タガノデュード"],
}

# ──────────────────────────────────────────────────────────────────────────────
# 予想ナレーション脚本
# 句点(。)ごとにシーンが切り替わる想定。最後はCTAで締める。
# ──────────────────────────────────────────────────────────────────────────────
PREDICTION_SCRIPT = (
    "天皇賞春2026、15頭の枠順が確定した。"
    "最長距離グランプリ、京都3200メートルの戦いが始まる。"
    "最大の注目はPR122、出走メンバー最高評価のクロワデュノールだ。"
    "4枠7番に入り、北村友一騎手との好枠コンビは魅力的に映る。"
    "しかしデータが冷徹な真実を告げている。3200メートルは初挑戦。"
    "天皇賞春の過去10年で、同距離の重賞未経験馬が1番人気で来たケースは極めて少ない。"
    "思い切って消しの一手だ。"
    "本命はアドマイヤテラ、2枠3番・武豊騎手。"
    "PR115で友道康夫調教師との黄金コンビ、好枠から先行して末脚を温存する競馬は京都の長い直線で最高に生きる。"
    "武豊騎手はこの舞台で幾度も歴史を刻んできた。長距離への適性も申し分ない。"
    "対抗はヘデントール、7枠12番・ルメール騎手。"
    "PR118は出走馬中トップクラス。長距離路線で積んだ経験値はクロワデュノールが持っていないものだ。"
    "ルメール騎手の天皇賞での高い勝率も強力な後押しになる。"
    "3着はタガノデュード、6枠11番・古川吉洋騎手。PR117で長距離での安定感は本物。"
    "中団から末脚を伸ばすレーススタイルは京都の直線と相性がいい。"
    "本命アドマイヤテラ、対抗ヘデントール、3着タガノデュードで春天を制する。"
    "みんなはどの馬を本命にした？コメントで教えてくれ！"
)


def _esc(p: str) -> str:
    return p.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def generate_horror_thumbnail() -> None:
    """ホラー系テキストサムネイルを生成する（1280×720、文字のみ）。
    output/thumbnail_0.jpg に保存する。landscape_video.py が上書きしないよう先に生成する。
    """
    out_path = f"{OUTPUT_DIR}/thumbnail_0.jpg"
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    W, H = 1280, 720
    font_path = Path("assets/fonts/KaiseiTokumin-ExtraBold.ttf")
    fallback_fonts = [
        Path("assets/fonts/DelaGothicOne-Regular.ttf"),
        Path("assets/fonts/MPLUS1p-Black.ttf"),
    ]
    if not font_path.exists():
        for fb in fallback_fonts:
            if fb.exists():
                font_path = fb
                break

    tmp_dir = tempfile.mkdtemp(prefix="tennosho_thumb_")
    try:
        # テキストファイル（日本語エスケープ問題を回避するため textfile= を使用）
        tf_title = f"{tmp_dir}/title.txt"
        tf_ai    = f"{tmp_dir}/ai.txt"
        tf_g1    = f"{tmp_dir}/g1.txt"
        tf_date  = f"{tmp_dir}/date.txt"
        Path(tf_title).write_text("天皇賞（春）", encoding="utf-8")
        Path(tf_ai).write_text("AI 予 想", encoding="utf-8")
        Path(tf_g1).write_text("G1", encoding="utf-8")
        Path(tf_date).write_text("2026年5月3日  京都  芝3200m", encoding="utf-8")

        fp = _esc(str(font_path)) if font_path.exists() else ""

        def dt(textfile: str, fs: int, color: str, x: str, y: str,
               bw: int = 0, bc: str = "0x000000", shadow: int = 0) -> str:
            base = (
                f"drawtext=textfile='{_esc(textfile)}'"
                + (f":fontfile='{fp}'" if fp else "")
                + f":fontsize={fs}:fontcolor={color}"
                f":x={x}:y={y}"
            )
            if bw:
                base += f":borderw={bw}:bordercolor={bc}"
            if shadow:
                base += f":shadowx={shadow}:shadowy={shadow}:shadowcolor=0x660000@0.9"
            return base

        # 暗い背景グラデーション（地面から黒→深紅）
        bg_filter = (
            "geq="
            "r='clip(8+60*pow(Y/H,2),0,68)':"
            "g='clip(0,0,2)':"
            "b='clip(0,0,2)'"
        )

        tf_sep = f"{tmp_dir}/sep.txt"
        Path(tf_sep).write_text("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", encoding="utf-8")

        filters = [
            bg_filter,
            # G1バッジ（boxオプションで赤背景を実現）
            f"drawtext=textfile='{_esc(tf_g1)}'"
            + (f":fontfile='{fp}'" if fp else "")
            + ":fontsize=42:fontcolor=0xFFFFFF"
            + ":x=60:y=40"
            + ":box=1:boxcolor=0x880000@0.95:boxborderw=22",
            # メインタイトル「天皇賞（春）」― 血のような深紅
            dt(tf_title, 148, "0xCC0000", "(w-text_w)/2", "140",
               bw=5, bc="0x000000@0.8", shadow=6),
            # 「AI 予想」― 白
            dt(tf_ai, 88, "0xFFFFFF", "(w-text_w)/2", "360",
               bw=3, bc="0x440000@0.9"),
            # 罫線（drawtext の box で代用）
            f"drawtext=textfile='{_esc(tf_sep)}'"
            + (f":fontfile='{fp}'" if fp else "")
            + ":fontsize=18:fontcolor=0xAA0000@0.9"
            + ":x=(w-text_w)/2:y=496",
            # 日付・会場
            dt(tf_date, 34, "0x999999", "(w-text_w)/2", "524",
               bw=2, bc="0x000000"),
        ]

        fc = "[0:v]" + ",".join(filters) + "[vout]"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=#000000:s={W}x{H}:r=1",
            "-filter_complex", fc,
            "-map", "[vout]",
            "-frames:v", "1", "-q:v", "2", out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            size_kb = Path(out_path).stat().st_size // 1024
            print(f"✅ ホラーサムネイル生成: {out_path} ({size_kb} KB)")
        else:
            print(f"[警告] サムネイル生成失敗:\n{result.stderr[-400:]}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    Path(NEWS_JSON).write_text(
        json.dumps([NEWS_ENTRY], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ {NEWS_JSON} を生成しました。")

    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)
    script_path = output_dir / "script_0.txt"
    script_path.write_text(PREDICTION_SCRIPT, encoding="utf-8")
    print(f"✅ {script_path} を生成しました。")
    print(f"   文字数: {len(PREDICTION_SCRIPT)} 字")
    print(f"   プレビュー: {PREDICTION_SCRIPT[:80]}...")

    generate_horror_thumbnail()


if __name__ == "__main__":
    main()
