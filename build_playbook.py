"""強いプレイヤーのログから、AIに渡す2つのものを作る(1回だけ実行すればよい。ログを足したらもう一度)。

  1. 考え方の手引き playbook_<名前>.md
     Opus が全部の試合を読み、その人の「思想」(何を正しいと思っていて、どう考えて、どう返すか)を
     例つきでまとめる。できたファイルはメモ帳で開いて、違うところを直したり消したりしてよい。
  2. 場面の札 flow_<名前>_bank.jsonl に "tag"
     全部の「流れ → 返し」に、どんな場面か(冗談を詰められた・疑われている…)の札を Haiku で付ける。
     試合中は、今の流れと同じ札の場面を優先して見せる。

使い方(先に python extract_flow.py を実行しておく):
    python build_playbook.py              … 両方作る
    python build_playbook.py tags         … 札だけ
    python build_playbook.py playbook     … 手引きだけ
    python build_playbook.py target=<別のプレイヤー名>

料金の目安: 手引き(Opus)が数百円、札(Haiku)が数円。
試合のログはAnthropicに送られる(試合中と同じ)。できたファイルは手元だけで使い、外に出さない。
"""
import json
import re
import sys
import time
from pathlib import Path

import anthropic

ARGS = sys.argv[1:]
TARGET = next((a[7:] for a in ARGS if a.startswith("target=")), "すぐれてない")
DO_TAGS = "playbook" not in ARGS
DO_BOOK = "tags" not in ARGS
OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"
CHUNK_CHARS = 60000       # 1回に Opus に読ませる量(試合単位で区切る)

# 場面の札(think.py の SITUATIONS と同じ並び)
SITUATIONS = {
    "A": "自分の冗談・ネタ・軽口を、相手が本気で詰めてきた",
    "B": "自分が疑われている・自分の主張に反論されている",
    "C": "他の人の理屈や主張に穴・飛躍がある",
    "D": "雑談・ネタ・言葉遊びが流れている",
    "E": "自分が質問された・説明や意見を求められた",
    "F": "誰を吊るか・票先を決める流れ",
    "G": "他の2人が言い合っていて、自分は外から見ている",
    "H": "その他",
}

client = anthropic.Anthropic()
spent = {"in": 0, "out": 0, "opus_in": 0, "opus_out": 0}


def ask(model, prompt, max_tokens):
    for i in range(3):
        try:
            res = client.messages.create(model=model, max_tokens=max_tokens,
                                         messages=[{"role": "user", "content": prompt}])
            key = "opus_" if model == OPUS else ""
            spent[key + "in"] += res.usage.input_tokens
            spent[key + "out"] += res.usage.output_tokens
            return "".join(b.text for b in res.content if b.type == "text")
        except Exception as e:
            print(f"  ※エラー({i + 1}/3): {str(e)[:150]}")
            time.sleep(5)
    return ""


# ---------------- 1. 場面の札 ----------------
def tag_bank():
    path = Path(f"flow_{TARGET}_bank.jsonl")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    menu = "\n".join(f"{k}: {v}" for k, v in SITUATIONS.items())
    todo = [i for i, r in enumerate(rows) if "tag" not in r]
    print(f"場面の札: {len(todo)}組に付けます(全{len(rows)}組)")
    for s in range(0, len(todo), 20):
        batch = todo[s:s + 20]
        items = []
        for n, i in enumerate(batch):
            ctx = "\n".join(f"  {w}: {t[:100]}" for w, t in rows[i]["ctx"][-4:])
            items.append(f"[{n}]\n{ctx}\n  → {TARGET}: {' ／ '.join(rows[i]['reply'])[:120]}")
        prompt = (f"人狼ゲームのチャットの場面です。各場面で「{TARGET}」から見て、最後の流れがどんな状況か、"
                  f"次の札から1つ選んでください。\n{menu}\n\n" + "\n\n".join(items)
                  + "\n\n答えは1行に1つ「番号:札」の形だけで書く(例 0:B)。")
        out = ask(HAIKU, prompt, 300)
        for m in re.finditer(r"(\d+)\s*[:：]\s*([A-H])", out):
            n = int(m.group(1))
            if n < len(batch):
                rows[batch[n]]["tag"] = m.group(2)
        print(f"  {min(s + 20, len(todo))}/{len(todo)}")
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    counts = {}
    for r in rows:
        counts[r.get("tag", "?")] = counts.get(r.get("tag", "?"), 0) + 1
    print("札の数:", ", ".join(f"{k}({SITUATIONS.get(k, '未')[:8]}…)={v}" for k, v in sorted(counts.items())))


# ---------------- 2. 考え方の手引き ----------------
MAP_PROMPT = """以下は人狼ゲーム「4A」(人狼1・村人3・初日犠牲者。昼は1回だけで3人が残り、1回の吊りで勝負が決まる)の、
実際の試合の昼のログです。強いプレイヤー「{who}」がどう考えて、どう話しているかを読み取りたい。

{who}の発言だけでなく、誰の何に対してそう返したかの流れに注目して、次をまとめてください。
・{who}が正しいと信じている考え(人狼観・4A観・議論観)。何度も出てくる主張は特に
・疑う/白く見るときの考え方(何を見て、どう理屈をつなげるか)
・自分が疑われた・詰められたときの返し方
・相手の理屈を崩すときのやり方
・雑談・ネタ・冗談への乗り方、場の空気の作り方
・村のときと狼のときの違い
・話し方の癖(長さ、連投、語尾、皮肉、短い一言)
それぞれ、根拠になる{who}の発言を短く(40字以内)引用して付ける。暴言・人格攻撃・下品な言葉は引用しない。
推測で一般論を書かない。このログから読み取れることだけを書く。

{logs}"""

REDUCE_PROMPT = """以下は、人狼ゲーム4Aの強いプレイヤー「{who}」の試合ログを分けて読んだメモです。
これを1つの「{who}の考え方の手引き」にまとめてください。これはAIが{who}のように考えて話すために、毎回読むものです。

書き方:
・最初に「{who}の思想」: {who}が正しいと信じている核の考え(5〜8個)。それぞれ一言の理由つき
・次に場面ごとの考え方と返し方: 「疑われた・詰められたとき」「相手の理屈を崩すとき」「冗談・ネタのとき」「誰を吊るか決めるとき」「狼のとき」
  各項目に、短い実例(引用、40字以内)を1〜2個
・最後に「やらないこと」: {who}がしない言い方・動き
・全体で3000字以内。箇条書き。「〜する」の形で書く
・メモに無いことは足さない。暴言・人格攻撃・下品な言葉は書かない。特定の人の私生活に触れない

{notes}"""


def build_playbook():
    games = [json.loads(l) for l in Path(f"games_{TARGET}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    texts = []
    for n, g in enumerate(games, 1):
        day = "\n".join(f"{w}: {t}" for w, t in g["day"])
        texts.append(f"===== 試合{n}({TARGET}={g['role']}、{g['result']}陣営の勝ち) =====\n{day}")
    chunks, cur = [], ""
    for t in texts:
        if cur and len(cur) + len(t) > CHUNK_CHARS:
            chunks.append(cur)
            cur = ""
        cur += t + "\n\n"
    if cur:
        chunks.append(cur)
    print(f"考え方の手引き: {len(games)}試合を{len(chunks)}回に分けて Opus に読ませます")
    notes = []
    for i, c in enumerate(chunks, 1):
        print(f"  読んでいます {i}/{len(chunks)}...")
        notes.append(ask(OPUS, MAP_PROMPT.format(who=TARGET, logs=c), 12000))
    print("  まとめています...")
    book = ask(OPUS, REDUCE_PROMPT.format(who=TARGET, notes="\n\n---\n\n".join(notes)), 14000).strip()
    out = Path(f"playbook_{TARGET}.md")
    out.write_text(book + "\n", encoding="utf-8")
    Path(f"playbook_{TARGET}_notes.md").write_text("\n\n---\n\n".join(notes), encoding="utf-8")
    print(f"  できました: {out}({len(book)}字)。メモ帳で開いて、違うところは直してください")


if __name__ == "__main__":
    if DO_TAGS:
        tag_bank()
    if DO_BOOK:
        build_playbook()
    yen = (spent["in"] * 0.10 + spent["out"] * 0.50 + spent["opus_in"] * 4.0 + spent["opus_out"] * 20.0) / 1e6 * 150
    print(f"\n使ったトークン: Haiku 入力{spent['in']:,}・出力{spent['out']:,} / Opus 入力{spent['opus_in']:,}・出力{spent['opus_out']:,}"
          f"(約{yen:.0f}円)")
