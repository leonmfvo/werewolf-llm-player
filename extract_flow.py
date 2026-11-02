"""強いプレイヤーのログから「直前の流れ + その人の返し」の組を切り出す。

使い方:
    python extract_flow.py              … logs_strong/*.txt から すぐれてない の組を作る
    python extract_flow.py <別のプレイヤー名>      … 別のプレイヤーで作る

出力:
    flow_<名前>.txt        … AIに渡すお手本(4Aの昼の議論だけ。暴言・下ネタ・人格攻撃の入った組は除く)
    flow_<名前>_bench.txt  … お手本に使わなかった試合の場面(bench.py で AI の返しと比べる用)

ログは人狼Onlineの「ログ」ページをコピーしたもの(新しい発言が上)。
"""
import json
import random
import re
import sys
from pathlib import Path

_args = [a for a in sys.argv[1:] if a != "mask"]
TARGET = _args[0] if _args else "すぐれてない"
MASK = "mask" in sys.argv[1:]   # 既定は素のログのまま(名前を変えない)。python extract_flow.py mask で名前を〈A〉〈B〉に伏せる
SRC = Path("logs_strong")
CTX = 6            # 直前の流れとして見せる発言の数
MAX_TURN = 4       # 1回の返しとしてまとめる連投の数
BENCH_RATE = 0.25  # 試合のうちこの割合をベンチ用に取っておく(お手本には使わない)
N_PICK = 45        # AIに渡すお手本の数(多すぎると読むのが遅く・高くなる)
PER_GAME = 5       # 1試合から選ぶ数の上限(いろいろな流れを見せるため)
ALIASES = {"すぐれてない": ["すぐれてない"]}   # 発言の中での呼ばれ方
SEED = 7

LINE_RE = re.compile(r"^([^\s:：→][^:：→]{0,24}?)(?:→([^:：]{1,24}))?: ?(.*)$")
# 暴言・人格攻撃・下ネタ・個人の私生活いじりが入った組はお手本にしない(村のおきて)
BAD_RE = re.compile(r"ばーか|バカ(?!ケツ)|アホ|あほ(?!ざす)|ドン引き|死ね|しね|殺す|きも|キモ|きしょ|気持ち悪|気色悪|馬鹿|ばか|ボケ|カス|ゴミ|単細胞|餓鬼|頭おかし|黙れ|静かにしろ|"
                    r"無知は罪|読解力なさ|人間関係下手|リア友|蛆|汚部屋|臭い|ラブドール|性癖|同人|抜け|タイツ|裸|輪姦|ボトラー|ボトル|"
                    r"ふざけるな|ふざけんな|クソ|くそ|うざ|ガイジ|キチガイ|障害")


try:
    NAMES = json.loads(Path("log_names.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    NAMES = {}


def parse(path):
    """ログを古い順の [{"from","to","text"}] にする。複数行の発言はつなげる。"""
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith(" ログ-") or re.match(r"^名前\t状態", line):
            if line.startswith("名前"):
                break   # 下の参加者一覧はログではない
            continue
        m = LINE_RE.match(line)
        if m and not line.startswith("→"):
            rows.append({"from": m.group(1).strip(), "to": (m.group(2) or "ALL").strip(), "text": m.group(3)})
        elif rows:
            rows[-1]["text"] += "\n" + line   # 複数行の発言の続き
    rows.reverse()   # ログは新しい順なので、古い順にする
    # 名前が試合ごとに違う同一人物(save_log.py が log_names.json に残す)は、発言者・宛先の名前をTARGETにそろえる
    alias = NAMES.get(path.stem)
    if alias and alias.get("as") == TARGET and alias["name"] != TARGET:
        for r in rows:
            if r["from"] == alias["name"]:
                r["from"] = TARGET
            if r["to"] == alias["name"]:
                r["to"] = TARGET
    for r in rows:
        r["text"] = r["text"].strip()
    return rows


def games(rows):
    """4A(人狼1・村人3)の試合ごとに、(役職, 昼の発言リスト) を返す。"""
    out = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r["from"] == "鯖" and "ゲームを開始します" in r["text"]:
            setting = next((x["text"] for x in reversed(rows[max(0, i - 8):i]) if "役職設定【" in x["text"]), "")
            roles = {}
            j = i - 1
            while j >= 0 and j >= i - 8:
                m = re.match(r"あなたは【(.+?)】です", rows[j]["text"])
                if rows[j]["from"] == "鯖" and m:
                    roles[rows[j]["to"]] = m.group(1)
                j -= 1
            k = i + 1
            day, in_day, result = [], False, None
            while k < len(rows) and not (rows[k]["from"] == "鯖" and "ゲームを開始します" in rows[k]["text"]):
                t = rows[k]
                if t["from"] == "鯖" and "朝になりました" in t["text"]:
                    in_day = True
                elif t["from"] == "鯖" and ("投票時間" in t["text"] or "ゲームを中断" in t["text"]):
                    if "ゲームを中断" in t["text"]:
                        day = []
                    in_day = False
                elif t["from"] == "鯖" and "勝利です" in t["text"]:
                    result = "村" if "村人チーム" in t["text"] else "狼"
                    break
                elif in_day and t["to"] == "ALL" and t["from"] not in ("鯖", "人狼"):
                    day.append(t)
                k += 1
            if "人狼-1,村人-3" in setting and day and result:
                out.append({"roles": roles, "day": day, "result": result})
            i = k
        else:
            i += 1
    return out


def pairs(game, target):
    """target の発言ごとに (直前の流れ, まとめた返し, 位置) を作る。"""
    day = game["day"]
    out = []
    i = 0
    while i < len(day):
        if day[i]["from"] != target:
            i += 1
            continue
        j = i
        turn = []
        while j < len(day) and day[j]["from"] == target and len(turn) < MAX_TURN:
            turn.append(day[j]["text"])
            j += 1
        ctx = day[max(0, i - CTX):i]
        if ctx:   # 朝一の挨拶など、流れの無い発言は使わない
            pos = i / max(1, len(day))
            phase = "序盤" if pos < 0.3 else ("終盤" if pos > 0.75 else "中盤")
            out.append({"ctx": [(c["from"], c["text"]) for c in ctx], "reply": turn, "phase": phase,
                        "role": game["roles"].get(target, "?"),
                        "players": sorted({m["from"] for m in day} | set(game["roles"]) - {"初日犠牲者"}),
                        "hist": [(c["from"], c["text"]) for c in day[:i]]})   # ベンチ用: 朝からその時点までの昼の流れ全部
        i = j
    return out


def clean(p):
    texts = [t for _, t in p["ctx"]] + p["reply"]
    if any(BAD_RE.search(t) for t in texts):
        return False
    if all(re.fullmatch(r"[\sｗw草？?。、…!！]*", t) for t in p["reply"]):
        return False   # 「ｗ」「？」だけの返しは単体ではお手本にしない
    if sum(len(t) for t in p["reply"]) > 260:
        return False   # 長すぎる返しはチャットの上限を超える
    return True


def rename(p, target):
    if not MASK:
        return {"ctx": p["ctx"], "reply": p["reply"], "phase": p["phase"], "role": p["role"], "hist": p.get("hist", [])}
    """名前を伏せる: 自分=「私」役の target は〈自分〉、他は〈A〉〈B〉…(特定の人を真似ないように)。"""
    names = {}
    for who, _ in p["ctx"]:
        if who != target and who not in names:
            names[who] = "〈" + "ABCDEFG"[len(names)] + "〉"
    names[target] = "〈自分〉"
    for a in ALIASES.get(target, []):
        names.setdefault(a, "〈自分〉")
    for who in p.get("players", []):
        if who not in names:
            names[who] = "〈" + "ABCDEFG"[min(len(set(names.values())) - 1, 6)] + "〉"   # 流れに出ていない参加者も伏せる
    for who in list(names):
        if who not in ALIASES.get(target, []) and len(who) >= 5 and who[:3] not in names:
            names[who[:3]] = names[who]   # 「アレクサンダー」→「アレク」のような略し方も伏せる

    def sub(t):
        for n in sorted(names, key=len, reverse=True):
            t = t.replace(n, names[n])
        return t
    return {"ctx": [(names.get(w, w), sub(t)) for w, t in p["ctx"]], "reply": [sub(t) for t in p["reply"]],
            "phase": p["phase"], "role": p["role"],
            "hist": [(names.get(w, w), sub(t)) for w, t in p.get("hist", [])]}


REASON_RE = re.compile(r"から|だから|って事|ってこと|じゃないの|でしょ|視点|要素|黒|白|村|狼|理屈|考察|吊り|矛盾|比較|前提|どう思う")


def score(p):
    """お手本としての良さ。議論で理由を言っている返し・流れに直接返している返しを高くする。"""
    reply = " ".join(p["reply"])
    sc = 0.0
    if REASON_RE.search(reply):
        sc += 2
    if 15 <= len(reply) <= 180:
        sc += 1
    last = p["ctx"][-1][1]
    if any(w in reply for w in re.findall(r"[一-龥ァ-ヴー]{2,}", last)[:6]):
        sc += 1   # 直前の発言の言葉を受けて返している
    if p["phase"] == "序盤" and len(reply) <= 40:
        sc += 1   # 序盤の短い雑談の返しも少し混ぜる
    return sc + random.random() * 0.5


PLAY_RE = re.compile(r"[ｗw]|草|冗談|ネタ|><|！|～")
N_PLAY = 14        # そのうち、ネタ・冗談への返し(軽い返し)を入れる数。議論の返しばかりだと、ネタにも真面目に返すようになる


def select(teach_by_game):
    picked = []
    for g in teach_by_game:
        picked += sorted(g, key=score, reverse=True)[:PER_GAME]
    picked.sort(key=score, reverse=True)
    picked = picked[:N_PICK - N_PLAY]
    used = {id(p) for p in picked}
    play = [p for g in teach_by_game for p in g
            if id(p) not in used and PLAY_RE.search(" ".join(p["reply"])) and len(" ".join(p["reply"])) <= 80]
    random.shuffle(play)
    picked += play[:N_PLAY]
    order = {"序盤": 0, "中盤": 1, "終盤": 2}
    return sorted(picked, key=lambda p: order[p["phase"]])


def fmt(p):
    ctx = "\n".join(f"{w}: {t}" for w, t in p["ctx"])
    reply = " ／ ".join(p["reply"])
    me = "〈自分〉" if MASK else TARGET
    return f"[{p['phase']}・{p['role']}]\n{ctx}\n→ {me}: {reply}"


def main():
    random.seed(SEED)
    all_games = []
    seen = set()
    for f in sorted(SRC.glob("*.txt")):
        for g in games(parse(f)):
            key = tuple(m["text"] for m in g["day"][:5])
            if key in seen:
                continue   # 同じ試合が2つのログに入っていることがある
            seen.add(key)
            if TARGET in [m["from"] for m in g["day"]]:
                all_games.append(g)
    random.shuffle(all_games)
    n_bench = max(1, int(len(all_games) * BENCH_RATE))
    bench_games, teach_games = all_games[:n_bench], all_games[n_bench:]
    teach_by_game = [[rename(p, TARGET) for p in pairs(g, TARGET) if clean(p)] for g in teach_games]
    teach_all = [p for g in teach_by_game for p in g]
    teach = select(teach_by_game)
    Path(f"flow_{TARGET}_all.txt").write_text("\n\n".join(fmt(p) for p in teach_all) + "\n", encoding="utf-8")
    # 全部の組(お手本用の試合だけ)。think.py が発言のたびに、今の流れに似た場面をここから探して見せる
    Path(f"flow_{TARGET}_bank.jsonl").write_text(
        "\n".join(json.dumps({"ctx": p["ctx"], "reply": p["reply"], "phase": p["phase"], "role": p["role"]},
                             ensure_ascii=False) for p in teach_all) + "\n", encoding="utf-8")
    # 試合ごとの昼のログ(考え方の手引きづくり build_playbook.py 用。ベンチ用の試合は入れない)
    Path(f"games_{TARGET}.jsonl").write_text(
        "\n".join(json.dumps({"role": g["roles"].get(TARGET, "?"), "result": g["result"],
                              "day": [(m["from"], m["text"]) for m in g["day"]]}, ensure_ascii=False)
                  for g in teach_games) + "\n", encoding="utf-8")
    # ベンチは「他の人の発言を受けて本人が返した」場面だけにする(自分の連投の続きや「いえ」だけの返しは比べられない)
    bench = [rename(p, TARGET) for g in bench_games for p in pairs(g, TARGET)
             if clean(p) and p["ctx"][-1][0] != TARGET and len(" ".join(p["reply"])) >= 8]
    out = Path(f"flow_{TARGET}.txt")
    out.write_text(f"お手本の人: {'〈自分〉' if MASK else TARGET}\n\n" + "\n\n".join(fmt(p) for p in teach) + "\n", encoding="utf-8")
    Path(f"flow_{TARGET}_bench.jsonl").write_text(
        "\n".join(json.dumps(dict(p, me="〈自分〉" if MASK else TARGET), ensure_ascii=False) for p in bench) + "\n",
        encoding="utf-8")
    print(f"4Aの試合: {len(all_games)}(お手本用 {len(teach_games)} / ベンチ用 {len(bench_games)})")
    print(f"お手本の組: 全{len(teach_all)}組から{len(teach)}組を選択 → {out}(全部は flow_{TARGET}_all.txt)")
    print(f"ベンチの場面: {len(bench)} → flow_{TARGET}_bench.jsonl")
    print(f"似た場面を探す用: flow_{TARGET}_bank.jsonl / 手引きづくり用: games_{TARGET}.jsonl")


if __name__ == "__main__":
    main()
