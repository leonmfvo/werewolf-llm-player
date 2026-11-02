"""本人(すぐれてない)の発言から、語尾・長さ・記号などの統計を数えて style_stats.json に書く。
使い方(think.py と同じフォルダで):  python build_style_stats.py
games_すぐれてない.jsonl を増やしたら、もう一度実行すると統計が更新される。
think_free.py は起動時にこのファイルを読んで、長さのくじ・癖の差のフィードバック・語尾の割合の調整に使う。"""
import json
import re
import sys
from pathlib import Path

WHO = "すぐれてない"
GAMES = Path("games_すぐれてない.jsonl")
OUT = Path("style_stats.json")

# (名前, 正規表現, 説明)。発言1つに含まれていれば1回と数える。
FEATURES = [
    ("ｗ", r"[ｗwW]", "文末や途中の「ｗ」"),
    ("？", r"[？?]", "疑問符を含む"),
    ("文末。", r"。$", "文末が「。」"),
    ("読点", r"、", "読点「、」を含む"),
    ("私", r"私", "「私」を含む"),
    ("です・ます", r"(です|ます)(。|$|ね|か|よ)", "丁寧語"),
    ("んだよな", r"んだよな", "「〜んだよな」"),
    ("よね", r"よね", "「〜よね」"),
    ("でしょ", r"でしょ", "「〜でしょ」"),
    ("って", r"って", "「って」を含む"),
    ("けど", r"けど", "「けど」を含む"),
    ("ので・から", r"(から|ので)", "理由の「から・ので」"),
    ("だろ", r"だろ", "「だろ」"),
    ("ないの", r"ないの[？?。]?$", "文末「〜ないの」"),
    ("ない？", r"ない[？?]$", "文末「〜ない？」"),
    ("けど止め", r"けど[。]?$", "「〜けど」で終わる"),
    ("なんか", r"なんか", "「なんか」"),
    ("草", r"草", "「草」"),
    ("気になる", r"気にな", "「気になる」系"),
    ("議論に切替", r"議論(しよう|しましょう|始め|に切り|に入|を始)|そろそろ|雑談(は|も)(この|もう|終)", "「そろそろ議論しよう」系の切り替え"),
    ("取り違えを認める", r"誤読誤読|読み外れ|読み違|読みず|間違えた|ごめん", "取り違え・ごめんと認める"),
    ("一般論で言い切る", r"っていうのは|というのは|ってのは|もの(だ|です|でしょ|じゃ)|もんだ|セオリー", "一般論で言い切る(〜っていうのは〜でしょ)"),
    ("理屈を評価して切る", r"おかしい|無理(が|あ)|恣意的|こじつけ|意味(わから|分から|不明)|謎|筋(が)?通(ら|せ)|曲解|破綻|くだらな|しょうもな", "相手の理屈を「おかしい・無理がある」と評価して切る"),
    ("常識の側に立つ", r"普通に|当たり前|当然|一般|そもそも|客観", "「普通に・当たり前・一般的に」で常識側に立つ"),
    ("文頭いや", r"^いや", "「いや」で切り返す"),
    ("丁寧語で突き放す", r"^(?!おは|こんにち|こんばん|よろしく)(?:.|\n)*(です|ます|ません)(よ|ね|か)?[。？?ｗ]*$", "丁寧語の文末(挨拶以外)"),
    ("ぼかし", r"と思うけど|気がする|かもしれな|かもね|かな[。？?]?$|っぽい", "ぼかし(と思うけど・気がする・かも)"),
    ("私が主語", r"私(は|の|視点|的)", "「私は」を主語にする"),
    ("柔らかい問い", r"の[？?]$", "「〜の？」と柔らかく聞く"),
    ("怪しい系", r"怪し|狼っぽ|村っぽ|白っぽ", "「怪しい・狼っぽい・村っぽい」(本人は黒目・白目と言う)"),
]
LEN_EDGES = [10, 20, 40, 60]   # 文字数の区切り(以下)。60超は最後の区分


def main():
    if not GAMES.exists():
        sys.exit(f"{GAMES} がありません(extract_flow.py で作るファイルです)")
    posts = []
    for line in GAMES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        g = json.loads(line)
        for who, text in g["day"]:
            if who == WHO and text.strip():
                posts.append((g.get("role", ""), text.strip()))
    if not posts:
        sys.exit("本人の発言が見つかりませんでした")
    texts = [t for _, t in posts]
    n = len(texts)
    lens = sorted(len(t) for t in texts)
    buckets, prev = [], 0
    for e in LEN_EDGES + [10 ** 6]:
        buckets.append(sum(prev < x <= e for x in lens) / n)
        prev = e
    feats = {}
    for name, pat, desc in FEATURES:
        rx = re.compile(pat)
        feats[name] = {"pattern": pat, "desc": desc, "rate": round(sum(bool(rx.search(t)) for t in texts) / n, 4)}
        for role in ("村人", "人狼"):
            sub = [t for r, t in posts if r == role]
            if sub:
                feats[name][role] = round(sum(bool(rx.search(t)) for t in sub) / len(sub), 4)
    # 場面(序盤・中盤・終盤)ごとの、1回の番(連投をまとめた)の長さの分布(flow_<名前>_bank.jsonl の本人の返しから)。AIは1回の発言で1つの番を作るので、こちらに合わせる
    by_phase = {}
    bank = Path(f"flow_{WHO}_bank.jsonl")
    if bank.exists():
        lens_p = {}
        for line in bank.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                lens_p.setdefault(r.get("phase", ""), []).append(sum(len(x) for x in r.get("reply", [])))   # 1回の番の合計(連投をまとめる)
        for ph, ls in lens_p.items():
            if len(ls) >= 30:
                bs, pv = [], 0
                for e in LEN_EDGES + [10 ** 6]:
                    bs.append(round(sum(pv < x <= e for x in ls) / len(ls), 4))
                    pv = e
                by_phase[ph] = bs
    out = {"who": WHO, "n": n, "len_buckets_phase": by_phase, "len_mean": round(sum(lens) / n, 1), "len_median": lens[n // 2],
           "len_edges": LEN_EDGES, "len_buckets": [round(b, 4) for b in buckets], "features": feats}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{WHO}の発言{n}件から {OUT} を作りました")
    print(f"長さ: 平均{out['len_mean']}字 / 中央値{out['len_median']}字 / 区分 {LEN_EDGES}以下・超: "
          + " ".join(f"{b:.0%}" for b in buckets))
    for ph, bs in by_phase.items():
        print(f"  {ph}の1回の番の長さ: " + " ".join(f"{x:.0%}" for x in bs))
    for name, f in feats.items():
        print(f"  {name:6s} {f['rate']:.1%}  {f['desc']}")


if __name__ == "__main__":
    main()
