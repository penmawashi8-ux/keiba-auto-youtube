#!/usr/bin/env python3
"""
スプリンターズステークス2026 最終予想動画（横型 1280×720・重馬場想定）用の
news.json・output/script_0.txt・デザイン背景画像を生成する。

takarazuka_kinen_prediction.yml と同様に
generate_audio.py → landscape_video.py → upload_landscape_youtube.py で処理する。

予想印（9月27日(日) 中山・芝1200m / 土曜の大雨で稍重～重を想定）:
  ◎ 11 ルガル
  ○  5 サウンドモリアーナ
  ▲ 15 フリッカージャブ
  △  3 ママコチャ / 4 ジューンブレア / 8 エーティーマクフィ / 14 ウインカーネリアン
  ☆  1 レッドモンレーヴ（3着穴）
  消し寄り: スターアニス / 16 ピューロマジック
"""

import glob
import json
import subprocess
import tempfile
from pathlib import Path

NEWS_JSON = "news.json"
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"

# ──────────────────────────────────────────────────────────────────────────────
# YouTube説明文（upload_landscape_youtube.py が youtube_description として使用）
# ──────────────────────────────────────────────────────────────────────────────
YOUTUBE_DESCRIPTION = """\
土曜の大雨で馬場は稍重～重へ。スプリンターズステークス2026の最終予想です。
本命は11番ルガル。印・展開・買い目、そして当日の馬場で予想をどう変えるかまで約4分でまとめました。

【レース情報】
スプリンターズステークス（GⅠ）
2026年9月27日(日) 中山競馬場 芝1200m

【チャプター】
{chapters}

【予想印】
◎ 11 ルガル
　2024年の勝ち馬。ハイペースを好位で粘り切った、今年と同じ型のレースを勝っている
○ 5 サウンドモリアーナ
　キーンランドCでGⅠ馬を撃破。3枠5番の好枠も、道悪適性は未知数
▲ 15 フリッカージャブ
　重馬場の前哨戦で大きく飛躍。雨は追い風、割引は8枠15番の外枠だけ
△ 3 ママコチャ／4 ジューンブレア／8 エーティーマクフィ／14 ウインカーネリアン
☆ 1 レッドモンレーヴ（3着穴）
　近5年の3着は差し・追い込みが中心。高松宮記念2着の末脚に期待
消し寄り：スターアニス／16 ピューロマジック

【想定する展開】
6 ワールズエンド、12 ブラックチャリス、14 ウインカーネリアン、16 ピューロマジックと先行型が揃い、外の2頭が主張して前半は速くなる想定。
それでも近年はハイペースでも好位の馬が勝っており、稍重～重なら2018年のように前・内が残りやすい。
軸は「速い流れを好位で追走して止まらない馬」。

【買い目例】
馬連：11 → 5・15・3・4・8・14（6点）
三連複フォーメーション：11 → 5・15 → 1・3・4・5・8・14・15（11点）

【当日の最終チェック】
① 馬場が良～稍重まで回復 → 本命をサウンドモリアーナへ、スターアニスを押さえに戻す
② 午前の芝で外差しばかり届く → 内が荒れているサイン。フリッカージャブを本命へ、3着候補に差し馬を追加
③ 内の先行馬が残っている → この予想のままでOK

みなさんの本命もぜひコメントで教えてください！
チャンネル登録・高評価もよろしくお願いします。

※予想は個人の見解です。馬券の購入は自己責任でお願いします。

#スプリンターズステークス #スプリンターズS #競馬予想 #ルガル #サウンドモリアーナ #フリッカージャブ #重馬場 #中山競馬場
"""

# ──────────────────────────────────────────────────────────────────────────────
# スプリンターズS 2026 レース情報
# ──────────────────────────────────────────────────────────────────────────────
NEWS_ENTRY = {
    "id": "sprinters_stakes_2026_final_prediction",
    "title": (
        "【スプリンターズS2026最終予想】重馬場想定なら本命は11番ルガル！"
        "対抗サウンドモリアーナ、雨で浮上する単穴と3着穴も公開"
    ),
    "url": "https://www.jra.go.jp/keiba/g1/sprinters.html",
    "summary": (
        "2026年9月27日(日)中山競馬場・芝1200m スプリンターズステークスGⅠの最終予想。"
        "土曜の大雨で稍重～重馬場を想定。"
        "本命は2024年の覇者11番ルガル、対抗は5番サウンドモリアーナ、"
        "単穴は重馬場の前哨戦で飛躍した15番フリッカージャブ。"
        "3着穴に高松宮記念2着の1番レッドモンレーヴ。"
    ),
    "image_url": "",
    "published_date": "2026-09-26T20:00:00+09:00",
    # landscape_video.py / upload_landscape_youtube.py が参照するフィールド
    "race_name": "スプリンターズS2026",
    "grade": "G1",
    "date": "2026年9月27日(日)",
    "venue": "中山競馬場",
    "distance": "芝1200m",
    "thumbnail_hook": "重馬場なら◎ルガル！",
    # "horses" は意図的に未設定: Wikipedia画像のランダム取得を避け、
    # generate_designed_backgrounds() のデザイン背景を確実に使用する
    # アップロード時にテンプレートの代わりに使用するタイトル・説明文
    "youtube_title": (
        "【スプリンターズS 2026 最終予想】雨で重馬場なら本命はルガル！"
        "穴馬・買い目・当日の最終チェックまで公開【競馬予想】"
    ),
    "youtube_description": YOUTUBE_DESCRIPTION,
    "extra_tags": [
        "スプリンターズステークス", "スプリンターズS", "スプリンターズS2026",
        "ルガル", "サウンドモリアーナ", "フリッカージャブ", "ママコチャ",
        "ジューンブレア", "エーティーマクフィ", "ウインカーネリアン",
        "レッドモンレーヴ", "ピューロマジック", "スターアニス", "中山競馬場",
        "重馬場", "競馬予想", "G1予想", "最終予想", "買い目",
    ],
}

# ──────────────────────────────────────────────────────────────────────────────
# 予想ナレーション脚本
# 【見出し】で始まる空行区切りブロックがチャプターとなり、
# landscape_video.py がチャプタータイトルカードを表示する。
# ──────────────────────────────────────────────────────────────────────────────
PREDICTION_SCRIPT = (
    "【最終予想・重馬場想定】\n"
    "9月27日日曜、中山競馬場・芝1200メートルで行われるスプリンターズステークス。"
    "秋のジーワン開幕戦、電撃の6ハロン決戦の最終予想をお届けする。"
    "今回のポイントは土曜の大雨。馬場は稍重から重まで悪化すると想定して印を打った。"
    "\n\n"
    "【想定する展開】\n"
    "逃げ・先行型は6番ワールズエンド、12番ブラックチャリス、14番ウインカーネリアン、16番ピューロマジックと揃った。"
    "外の2頭が主張するため、前半のペースは速くなるだろう。"
    "ただ、このレースは近年、ハイペースでも好位の馬が勝っている。"
    "さらに稍重から重になれば、2018年のように前と内が残りやすい。"
    "そこで今回は、速い流れを好位で追走して止まらない馬を軸にする。"
    "\n\n"
    "【◎ルガル】\n"
    "本命は11番ルガル。"
    "2024年、ピューロマジックが作ったハイペースを好位で粘り切って勝った馬だ。"
    "まさに今年と同じ型のレースの勝ち馬と言える。"
    "スタートが安定してから復調しており、タフな条件もむしろ向く。"
    "重馬場なら、この馬の粘り強さが最大限に生きるはずだ。"
    "\n\n"
    "【○サウンドモリアーナ】\n"
    "対抗は5番サウンドモリアーナ。"
    "前走のキーンランドカップでジーワン馬を破った勢いは本物。"
    "3枠5番という好枠も引いた。"
    "ただ、道悪の適性はまだ未知数。その分、本命からは一段下げた。"
    "\n\n"
    "【▲フリッカージャブ】\n"
    "単穴は15番フリッカージャブ。"
    "重馬場の前哨戦で大きく飛躍した馬で、今回の雨はまさに追い風だ。"
    "割引材料は8枠15番の外枠だけ。能力と馬場適性なら、頭まで十分にある。"
    "\n\n"
    "【△押さえ・☆3着穴】\n"
    "押さえは4頭。内枠で好位を取れる3番ママコチャと4番ジューンブレア。"
    "道悪で妙味が出る8番エーティーマクフィ。"
    "そして昨年の覇者で、外からでも番手を取れる14番ウインカーネリアン。"
    "さらに3着穴として1番レッドモンレーヴを推したい。"
    "近5年の3着は差しと追い込みばかり。高松宮記念2着の末脚を、3着候補として押さえておく。"
    "\n\n"
    "【消し寄りの人気馬】\n"
    "逆に評価を下げるのは2頭。"
    "スターアニスは初めての1200メートル、初めての古馬相手、そして道悪と、不安材料が重なった。"
    "16番ピューロマジックは大外枠。ハナを主張するために脚を使わされる分、最後は苦しくなるとみる。"
    "\n\n"
    "【買い目】\n"
    "買い目の例を紹介する。"
    "馬連は11番から、5番、15番、3番、4番、8番、14番への6点。"
    "三連複はフォーメーションで、1頭目が11番、2頭目が5番と15番、"
    "3頭目が1番、3番、4番、5番、8番、14番、15番の11点だ。"
    "\n\n"
    "【当日の最終チェック】\n"
    "最後に、当日チェックしてほしいポイントが3つある。"
    "1つ目。馬場発表が良から稍重まで回復していたら、本命をサウンドモリアーナに上げ、スターアニスを押さえに戻す。"
    "2つ目。午前の芝のレースで外からの差しばかり届いているなら、内が荒れているサイン。"
    "フリッカージャブを本命に上げ、3着候補に差し馬を増やそう。"
    "3つ目。内の先行馬が残っているなら、この予想のままでオーケーだ。"
    "最後は当日の馬場を優先して、スプリンターズステークスを楽しんでほしい。"
    "みんなの本命はどの馬？コメントで教えてくれ！"
)


def _find_font() -> str | None:
    for path in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]:
        if Path(path).exists():
            return path
    hits = glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    return hits[0] if hits else None


# JRA枠カラー (R, G, B) 1白 2黒 3赤 4青 5黄 6緑 7橙 8桃
_WAKU_COLORS = [
    (255, 255, 255), (40, 40, 40), (230, 0, 18), (0, 104, 183),
    (255, 241, 0), (0, 166, 86), (243, 152, 0), (229, 151, 178),
]


def _waku_bar_expr(channel: int) -> str:
    """8枠カラーバー用のgeq式（1280px幅を160pxずつ8分割）を返す。"""
    expr = str(_WAKU_COLORS[7][channel])
    for i in range(6, -1, -1):
        expr = f"if(lt(X,{(i + 1) * 160}),{_WAKU_COLORS[i][channel]},{expr})"
    return expr


def generate_designed_backgrounds() -> None:
    """横型動画用の背景画像4枚を ffmpeg のみで生成する（Pillow不使用）。
    多色斜めグラデーション＋ビネット＋ノイズ質感＋JRA8枠カラーバー＋
    「スプリンターズS」透かしの装飾デザイン。assets/landscape_N.jpg に保存し、
    landscape_video.py がそのまま背景として使用する。
    """
    Path(ASSETS_DIR).mkdir(exist_ok=True)
    font = _find_font()

    # (ファイル名, 3色グラデーション[左上, 中間, 右下])
    palettes = [
        # 0: スチールブルー（雨の中山・重馬場）
        ("landscape_0.jpg", ("0x05080F", "0x2A4A6B", "0x0A1420")),
        # 1: ダークターフ（水を含んだ芝）
        ("landscape_1.jpg", ("0x03100A", "0x1E5A3A", "0x061A10")),
        # 2: クリムゾン（電撃の6ハロン）
        ("landscape_2.jpg", ("0x16060A", "0x8A1E2E", "0x200A10")),
        # 3: ゴールドブロンズ（秋のスプリント王）
        ("landscape_3.jpg", ("0x181006", "0x8A6A1E", "0x281C0A")),
    ]

    bar_r, bar_g, bar_b = (_waku_bar_expr(c) for c in range(3))

    with tempfile.TemporaryDirectory(prefix="sprinters_bg_") as tmp_dir:
        wm_file = Path(tmp_dir) / "wm.txt"
        wm_file.write_text("スプリンターズS", encoding="utf-8")

        for filename, (c0, c1, c2) in palettes:
            out_path = f"{ASSETS_DIR}/{filename}"

            base_filters = [
                "vignette=angle=PI/4.5",
                "noise=alls=7:allf=t",
            ]
            if font:
                fp = str(font).replace("'", "\\'")
                wf = str(wm_file).replace("'", "\\'")
                # 大きな「スプリンターズS」透かし（半透明・中央奥）
                base_filters.append(
                    f"drawtext=textfile='{wf}':fontfile='{fp}'"
                    f":fontsize=150:fontcolor=0xFFFFFF@0.07"
                    f":x=(w-text_w)/2:y=(h-text_h)/2-20"
                )
                # 英字キャプション（下部・上品なアクセント）
                base_filters.append(
                    "drawtext=text='SPRINTERS STAKES 2026  NAKAYAMA 1200m'"
                    f":fontfile='{fp}':fontsize=24:fontcolor=0xFFFFFF@0.40"
                    ":x=(w-text_w)/2:y=h-58"
                )

            fc = (
                f"[0:v]{','.join(base_filters)}[base];"
                f"[1:v]geq=r='{bar_r}':g='{bar_g}':b='{bar_b}'[bar1];"
                f"[2:v]geq=r='{bar_r}':g='{bar_g}':b='{bar_b}'[bar2];"
                f"[base][bar1]overlay=0:0[t1];"
                f"[t1][bar2]overlay=0:714[vout]"
            )

            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", (
                        f"gradients=s=1280x720:c0={c0}:c1={c1}:c2={c2}"
                        f":x0=0:y0=40:x1=1280:y1=680:nb_colors=3"
                    ),
                    "-f", "lavfi", "-i", "color=black:s=1280x6",
                    "-f", "lavfi", "-i", "color=black:s=1280x6",
                    "-filter_complex", fc,
                    "-map", "[vout]",
                    "-frames:v", "1",
                    "-q:v", "3",
                    out_path,
                ],
                check=True,
                capture_output=True,
            )
            print(f"背景画像を生成: {out_path}")


def main() -> None:
    generate_designed_backgrounds()

    Path(NEWS_JSON).write_text(
        json.dumps([NEWS_ENTRY], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"{NEWS_JSON} を生成しました。")

    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)
    script_path = output_dir / "script_0.txt"
    script_path.write_text(PREDICTION_SCRIPT, encoding="utf-8")
    print(f"{script_path} を生成しました。")
    print(f"   文字数: {len(PREDICTION_SCRIPT)} 字")
    print(f"   プレビュー: {PREDICTION_SCRIPT[:80]}...")


if __name__ == "__main__":
    main()
