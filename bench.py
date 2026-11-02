"""試合をせずに、AIの返しを強いプレイヤーの実際の返しと見比べる。

flow_<名前>_bench.jsonl(extract_flow.py が作る。お手本には使っていない試合の場面)の各場面で、
think.py と同じ仕組み(同じプロンプト・同じ後処理・見直し)で AI に返しを書かせ、
「その場面で本人が実際に言ったこと」と並べた bench_results/日時.html を作る。

使い方:
    python bench.py sonnet              … 20場面で試す(Sonnet)
    python bench.py sonnet n=40         … 場面の数を変える
    python bench.py sonnet speak=opus   … think.py と同じ指定がそのまま使える
    python bench.py sonnet target=<別のプレイヤー名> … 別のプレイヤーの場面で試す
    python bench.py opus slim memo      … 考える係(Opus)が分析してから、話す係(Sonnet)が話す
    python bench.py sonnet slim scenes=scenes_意味不明.jsonl repeat=3 compare=low,medium
                                        … 同じ場面を、考える量 low と medium で3回ずつ試して並べる
    python bench.py sonnet slim scenes=scenes_盤面整理.jsonl repeat=3
                                        … 自分で作った場面(試合で気になった場面)を、それぞれ3回ずつ試す

料金: 1場面につき問い合わせ2回(話す・見直し)。Sonnetで20場面なら数十円程度。
"""
import html
import json
import random
import sys
import time
from pathlib import Path

args = [a for a in sys.argv[1:]]
N = next((int(a[2:]) for a in args if a.startswith("n=")), 20)
TARGET = next((a[7:] for a in args if a.startswith("target=")), "すぐれてない")
SCENES = next((a[7:] for a in args if a.startswith("scenes=")), None)   # 自分で作った場面のファイル(試合で気になった場面など)
REPEAT = next((int(a[7:]) for a in args if a.startswith("repeat=")), 1)  # 同じ場面を何回ずつ試すか(安定して言えるかを見る)
COMPARE = next((a[8:].split(",") for a in args if a.startswith("compare=")), [None])  # 考える量を比べる(例 compare=low,medium)
THINK_FILE = next((a[10:] for a in args if a.startswith("thinkfile=")), "think.py")   # thinkfile=think_free.py で別のファイルを試す
USE_MEMO = "memo" in args   # 考える係(主モデル)の分析を先に作って、話す係に渡す(本番の「考える係あり」と同じ形)
think_args = [a for a in args if not a.startswith(("n=", "target=", "scenes=", "repeat=", "compare=", "thinkfile=")) and a != "memo"]

ME = "ソネット"
OTHERS = {"〈A〉": "ミナト", "〈B〉": "ハク", "〈C〉": "ソラ", "〈D〉": "ルアー"}

# think.py を、試合のループに入る手前まで読み込む(関数と設定だけ使う)
sys.argv = [THINK_FILE] + think_args + ["noarchive", "noreflect", "solo"]
src = Path(THINK_FILE).read_text(encoding="utf-8")
cut = src.index("\nz = Zinro()\n")
T = {"__name__": "think_bench"}
exec(compile(src[:cut], THINK_FILE, "exec"), T)


def real(name_or_text):
    """名前を伏せたベンチ(extract_flow.py mask)のときだけ、仮の名前に置き換える。素のログはそのまま。"""
    if "〈" not in name_or_text:
        return name_or_text
    t = name_or_text.replace("〈自分〉", ME)
    for k, v in OTHERS.items():
        t = t.replace(k, v)
    return t


class FakeZ:
    """見直し(review_speech)が読む画面の代わり。"""
    def __init__(self, msgs, players):
        self._m, self._p = msgs, players

    def messages(self):
        return self._m

    def players(self):
        return self._p

    def time_left(self):
        return None


def scene_msgs(sc):
    role = sc["role"] if sc["role"] in ("村人", "人狼") else "村人"
    msgs = [{"from_user": "鯖", "to_user": "ALL", "text": "役職設定【人狼-1,村人-3,役欠け:あり】"},
            {"from_user": "鯖", "to_user": ME, "text": f"あなたは【{role}】です"},
            {"from_user": "鯖", "to_user": "ALL", "text": "ゲームを開始します"},
            {"from_user": "鯖", "to_user": "ALL", "text": "夜になりました…役職の人は能力を実行してください"},
            {"from_user": "鯖", "to_user": "ALL", "text": "初日犠牲者さんが死亡しました"},
            {"from_user": "鯖", "to_user": "ALL", "text": "恐ろしい夜が明け、朝になりました。"}]
    for who, text in sc["hist"]:
        msgs.append({"from_user": real(who), "to_user": "ALL", "text": real(text)})
    return msgs, role


def run_scene(sc, effort=None):
    global ME
    if effort:
        T["SPEAK_EFFORT"] = effort
    if sc.get("me") and sc["me"] != "〈自分〉":
        ME = sc["me"]   # 素のログ: AIは本人の名前のまま、その席に座る
    msgs, role = scene_msgs(sc)
    names = sorted({m["from_user"] for m in msgs if m["from_user"] != "鯖"} | {ME})
    while len(names) < 3:
        names.append([v for v in OTHERS.values() if v not in names][0])
    players = [{"name": n, "alive": True} for n in names]
    T["PHASE"]["name"] = sc["phase"]
    if "VOICE_EXCLUDE" in T:   # 本人の実際の返しは、声の近さの比較(think_free.py)の元データから外す
        T["VOICE_EXCLUDE"].clear()
        T["VOICE_EXCLUDE"].update(str(real(x)).strip().replace("\n", " ") for x in (sc.get("reply") or []))
    T["TARGETS"].clear()
    memo = None
    if USE_MEMO:
        system, ap = T["analyst_prompts"](ME, role, "昼", msgs, names)
        out, _ = T["llm"](system, ap, max_tokens=1400, temperature=0.4, think=True, effort=T["ANALYST_EFFORT"])
        memo = {"text": out[:2600], "time": time.time(), "read": 0}
    prompt = T["build_prompt"](ME, role, "昼", msgs, names, has_new=True, memo=memo)
    t0 = time.time()
    idea, st = T["think"](prompt, ME, True, model=T["speaker_model"](),
                          thinking=T["SLIM_THINK"] if T["SLIM"] else False)
    draft = str(idea.get("speech", ""))
    raw = draft
    if T.get("REVIEW_ON") and draft:
        raw = T["review_speech"](FakeZ(msgs, players), draft, ME, "昼", 40)
    final, _ = T["polish"](raw, msgs, ME, names, names, True, idea, None)
    if final and T["PERSONAS"][T["PERSONA"]].get("split_posts"):
        final = " ／ ".join(T["split_posts"](final))   # 試合では短い連投に分けて出すので、同じ形で見せる
    if memo:
        st["reading"] = (st.get("reading", "") + " / 考える係の見立て: "
                         +  " ".join(l for l in memo["text"].splitlines() if l.startswith(("【見立て】", "【違和感】")))[:200])
    return {"draft": draft, "reviewed": raw, "final": final, "reading": st.get("reading", ""),
            "sec": time.time() - t0, "effort": st.get("effort") or "", "tag": T["SCENE_TAG"][0] or ""}


def page(rows, title):
    css = """body{font-family:system-ui,'Hiragino Sans','Meiryo',sans-serif;max-width:900px;margin:24px auto;padding:0 16px;background:#fafafa;color:#222}
.s{background:#fff;border:1px solid #ddd;border-radius:8px;padding:14px 16px;margin:16px 0}
.ctx{font-size:13px;color:#555;white-space:pre-wrap;max-height:320px;overflow:auto;border-left:3px solid #ddd;padding-left:8px}
.h,.a{margin-top:10px;padding:8px 10px;border-radius:6px;white-space:pre-wrap}
.h{background:#eef6ee}.a{background:#eef1fb}.tag{font-size:12px;color:#888}
details{font-size:12px;color:#666;margin-top:6px}"""
    out = [f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title><style>{css}</style>",
           f"<h2>{html.escape(title)}</h2><p>緑=本人が実際に言ったこと / 青=AI。どちらが流れに合っていて、強く・自然に見えるかを比べる。"
           f"AIはその時点までの昼の流れだけを見て書いている。</p>"]
    for i, (sc, rs) in enumerate(rows, 1):
        ctx = "\n".join(f"{real(w)}: {real(t)}" for w, t in sc["hist"])   # 朝から全部(AIに見せたのと同じ)
        out.append(f"<div class='s'><div class='tag'>場面{i} [{sc['phase']}・{sc.get('me') if sc.get('me') and sc.get('me') != '〈自分〉' else ME}={sc['role']}] {html.escape(sc.get('note', ''))}</div>"
                   f"<div class='ctx'>{html.escape(ctx)}</div>"
                   f"<div class='h'><b>{html.escape(sc.get('label', '本人'))}</b>: {html.escape(real(' ／ '.join(sc['reply'])))}</div>")
        for r in rs:
            lab = "AI" + (f"(考える量 {r['effort']}" + (f"・札{r['tag']}" if r.get("tag") else "") + ")" if r.get("effort") else "")
            out.append(f"<div class='a'><b>{html.escape(lab)}</b>: {html.escape(r['final'] or '(何も言わない)')}</div>"
                       f"<details><summary>途中経過({r['sec']:.0f}秒)</summary>読み取り: {html.escape(r['reading'])}<br>"
                       f"下書き: {html.escape(r['draft'])}<br>見直し後: {html.escape(r['reviewed'])}</details>")
        out.append("</div>")
    return "\n".join(out)


def main():
    path = Path(SCENES or f"flow_{TARGET}_bench.jsonl")
    scenes = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if SCENES:
        picks = scenes[:N]     # 自分で作った場面は全部、順番どおりに
    else:
        random.seed(1)
        picks = random.sample(scenes, min(N, len(scenes)))
        order = {"序盤": 0, "中盤": 1, "終盤": 2}
        picks.sort(key=lambda s: order.get(s["phase"], 1))
    rows = []
    secs = {}
    for i, sc in enumerate(picks, 1):
        print(f"場面{i}/{len(picks)} [{sc['phase']}]...", flush=True)
        print(f"  {sc.get('label', '本人')}: {real(' ／ '.join(sc['reply']))[:80]}")
        rs = []
        for k in range(REPEAT):
            for eff in COMPARE:
                try:
                    r = run_scene(sc, eff)
                except Exception as e:
                    r = {"draft": "", "reviewed": "", "final": f"(エラー: {e})", "reading": "", "sec": 0,
                         "effort": eff or "", "tag": ""}
                lab = f"AI {r['effort']:<6}" if eff else "AI"
                print(f"  {lab}: {r['final'][:80]}{'…(続きはHTMLで)' if len(r['final']) > 80 else ''}  ({r['sec']:.0f}秒)")
                rs.append(r)
                secs.setdefault(r["effort"] or "-", []).append(r["sec"])
        rows.append((sc, rs))
    if len(COMPARE) > 1:
        print("\n平均の時間: " + " / ".join(f"{e} {sum(v) / len(v):.1f}秒" for e, v in secs.items()))
    Path("bench_results").mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    title = (f"ベンチ {stamp}(話す: {T['speaker_model']()}"
             + (f" / 考える: {T['CLAUDE_MODEL']}" if USE_MEMO else "")
             + f"{' / slim' if T['SLIM'] else ''}"
             + (f" / 考える量 {' vs '.join(COMPARE)}" if len(COMPARE) > 1 else f" / 考える量 {T['SPEAK_EFFORT']}")
             + f" / 人格: {T['PERSONA']} / {TARGET}の場面)")
    out = Path("bench_results") / f"{stamp}.html"
    out.write_text(page(rows, title), encoding="utf-8")
    print(f"\n結果: {out}(ブラウザで開く)")
    if T.get("cost_text") and T["cost_text"]():
        print(T["cost_text"]())


if __name__ == "__main__":
    main()
