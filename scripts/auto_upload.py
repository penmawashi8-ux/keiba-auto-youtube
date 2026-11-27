"""G1 の CG 動画（本編・宣伝ショート）を YouTube に投稿する。本編を先に投稿し、ショートの説明文に本編のURLを入れる。

使い方: python scripts/auto_upload.py <prefix> <動画などのあるディレクトリ> [privacy]
  ディレクトリには <prefix>_cg.mp4 / <prefix>_cg_plan.json / <prefix>_cg_thumb.jpg /
  <prefix>_cg_short.mp4 / <prefix>_cg_short_thumb.jpg があること
出力: <ディレクトリ>/uploaded_<prefix>cg.txt, uploaded_<prefix>short.txt（投稿したURL）

タイトル・説明文は台本（plan.json）の勝率から作る。人気には触れない。
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import race_names as rn      # noqa: E402


def upload(video, title, desc, tags, thumb, privacy, url_out):
    subprocess.run([sys.executable, "scripts/upload_custom_youtube.py", "--file", str(video), "--title", title,
                    "--description", desc, "--tags", tags, "--thumbnail", str(thumb), "--privacy", privacy,
                    "--url-out", str(url_out)], check=True)
    return Path(url_out).read_text().strip()


def main():
    prefix, d = sys.argv[1], Path(sys.argv[2])
    privacy = sys.argv[3] if len(sys.argv) > 3 else "public"
    plan = json.loads((d / f"{prefix}_cg_plan.json").read_text(encoding="utf-8"))
    race = plan["race"]
    name, full, short = race["race_name"], rn.full_name(race["race_name"]), rn.short_name(race["race_name"])
    year = race["date"][:4]
    m, dd = int(race["date"][5:7]), int(race["date"][8:10])
    rnum = int(race["race_id"][-2:])
    hs = sorted(plan["horses"], key=lambda h: h["rank"])
    sw = "ダート" if race.get("surface") == "ダ" else "芝"
    rates = "\n".join(f"{h['rank']}位 {h['num']}番 {h['name']} {h['win'] * 100:.1f}%" for h in hs[:5])
    fav = hs[0]
    hashtag = "#" + short.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    tags = ",".join(dict.fromkeys(["競馬", "競馬予想", short, full, f"{short}{year}", "シミュレーション", "3DCG",
                                   fav["name"], race["venue"] + "競馬場", "G1"]))

    long_file = d / f"uploaded_{prefix}cg.txt"
    if long_file.exists():
        long_url = long_file.read_text().strip()
        print("本編は投稿済み:", long_url)
    else:
        title = f"【{short} {year}】1万回シミュレーションした結果｜3D CGでレース再現"
        desc = f"""{m}月{dd}日 {race['venue']}{rnum}R {full}（GI・{sw}{race['distance']}m）。
確定した枠順で、出走{len(hs)}頭を過去の成績から1万回走らせ、その代表的な1回を3D CGで再現しました。

■ 1万回走らせた勝率
{rates}

■ 計算で考えたこと
・各馬の過去の走りを、一緒に走った馬の実力と着差から点数にしています
・このコースの過去のレースから、枠と脚質の有利不利を測って反映
・1回ごとに「前が残る流れ／後ろが届く流れ」をランダムに決めています
・成績の少ない馬は、たまたまの好走・凡走に引っぱられないよう出走馬の平均に寄せています
・JRAでの出走が少ない馬（海外の馬など）は、JRAが公表する国際レーティングから力を見積もっています
・馬場は良を想定。調教や当日の状態は計算に入っていません

映像は1万回のうちの代表的な1回で、毎回この着順になるわけではありません。
馬券の購入を勧めるものではありません。

あなたの予想はどの馬ですか？コメントで教えてください。

■ 使用素材
馬の3Dモデル：Quaternius「Ultimate Animated Animal Pack」（CC0）
BGM：オリジナル

{hashtag} #競馬 #競馬予想 #シミュレーション #3DCG #{fav['name']}"""
        long_url = upload(d / f"{prefix}_cg.mp4", title, desc, tags, d / f"{prefix}_cg_thumb.jpg", privacy, long_file)
        print("本編:", long_url)

    short_file = d / f"uploaded_{prefix}short.txt"
    if short_file.exists():
        print("ショートは投稿済み:", short_file.read_text().strip())
        return
    title = f"【{short} {year}】1万回シミュレーションした結果を3D CGで再現 #Shorts"
    desc = f"""{full}（{race['venue']} {sw}{race['distance']}m）を、出走{len(hs)}頭の過去の成績から1万回シミュレーション。その代表的な1回を3D CGで再現しました。

▶ 1万回走らせた勝率・上位3頭の紹介は本編で
{long_url}

※データによるシミュレーションです。馬券の購入を勧めるものではありません
馬の3Dモデル：Quaternius（CC0）／BGM：オリジナル

#Shorts {hashtag} #競馬 #競馬予想 #シミュレーション"""
    url = upload(d / f"{prefix}_cg_short.mp4", title, desc, tags + ",Shorts", d / f"{prefix}_cg_short_thumb.jpg",
                 privacy, short_file)
    print("ショート:", url)


if __name__ == "__main__":
    main()
