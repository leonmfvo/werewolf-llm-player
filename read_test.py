"""読解テスト: 実際の試合ログ(本人とAIの対戦)から作った問題で、AIがログを正しく読めているかを測る。

使い方(think_free.py と同じフォルダで):
    python read_test.py                          … Sonnet で全問(ログを読んで直接答える)
    python read_test.py models=haiku,sonnet,opus … モデルを並べて比べる
    python read_test.py modes=direct,memo        … 読み方を並べて比べる
        direct : ログと質問だけで答える(モデルの素の読解力)
        memo   : 先に think_free.py の「読む係」に読解メモを書かせ、ログ+メモで答える(今の仕組みの読解力)
        memoonly: メモだけで答える(話す係に渡るメモに、必要なことが書けているか)
    python read_test.py repeat=3                 … 同じ問題を3回ずつ(選択肢の並びは毎回変える)
    python read_test.py effort=low               … 考える量(既定 medium)
    python read_test.py only=Q03,Q15             … 一部の問題だけ

結果は read_test_results/日時.html に出る(問題ごとの正誤と、AIの答えの理由)。
問題は read_test.jsonl(正解は人がログを読んで決めたもの。choices の最初が正解で、出すときに並べ替える)。
"""
import html
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

args = sys.argv[1:]
MODELS = next((a[7:].split(",") for a in args if a.startswith("models=")), ["sonnet"])
MODES = next((a[6:].split(",") for a in args if a.startswith("modes=")), ["direct"])
REPEAT = next((int(a[7:]) for a in args if a.startswith("repeat=")), 1)
EFFORT = next((a[7:] for a in args if a.startswith("effort=")), "medium")
ONLY = next((a[5:].split(",") for a in args if a.startswith("only=")), None)
THINK_FILE = next((a[10:] for a in args if a.startswith("thinkfile=")), "think_free.py")
IDS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "fable": "claude-fable-5-1"}

# think_free.py を、試合のループに入る手前まで読み込む(llm と読む係をそのまま使う)
sys.argv = [THINK_FILE, "sonnet", "slim", "noarchive", "noreflect", "solo"]
src = Path(THINK_FILE).read_text(encoding="utf-8")
T = {"__name__": "read_test"}
exec(compile(src[:src.index("\nz = Zinro()\n")], THINK_FILE, "exec"), T)

QS = [json.loads(l) for l in Path("read_test.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
if ONLY:
    QS = [q for q in QS if q["id"] in ONLY]

SYSTEM = ("あなたは日本語のチャットを正確に読む人です。人狼ゲーム(4A: 人狼1・村人3・初日犠牲者。昼に残っているのは3人)の"
          "昼の議論のログを読み、質問に選択肢の記号で答える。ログの#番号は発言の順番。「>名前」「名前→」は、その人あての発言という意味。")
LETTERS = "ABCD"
LOCK = threading.Lock()


def log_text(q):
    return "【ログ(古い順・最後が最新)】\n" + "\n".join(q["log"])


def get_memo(q, model_id):
    """読む係(think_free.py の read_memo)の読解メモ。プロンプトは本番と同じ形(ログ→お手本の…)に合わせる。"""
    prompt = ("【今日のログ(朝から全部・古い順・最後が最新)】\n" + "\n".join(q["log"])
              + f"\n\nあなた={q['me']} / 役職=村人\n\nお手本の")
    with LOCK:
        T["CLAUDE_MODEL"] = model_id
    return T["read_memo"](prompt, q["me"])


def ask(q, mode, model_id, rep):
    rnd = random.Random(f"{q['id']}-{rep}")
    choices = list(q["choices"])
    rnd.shuffle(choices)
    gold = LETTERS[choices.index(q["answer"])]
    memo = ""
    if mode in ("memo", "memoonly"):
        memo = get_memo(q, model_id)
    body = "" if mode == "memoonly" else log_text(q) + "\n\n"
    if memo:
        body += f"【読解メモ({q['me']}の席から、読む係が書いたもの)】\n{memo}\n\n"
    body += (f"【質問】{q['question']}\n" + "\n".join(f"{LETTERS[i]}. {c}" for i, c in enumerate(choices))
             + "\n\n1行目に「答え: 記号」(A〜Dのどれか1つ)、2行目に理由を一言で書く。")
    out, _ = T["llm"](SYSTEM, body, max_tokens=300, temperature=0.0, think=True, effort=EFFORT, model=model_id)
    m = re.search(r"答え\s*[:：]\s*([A-DＡ-Ｄ])", out or "")
    got = m.group(1).translate(str.maketrans("ＡＢＣＤ", "ABCD")) if m else "?"
    reason = re.sub(r"^.*答え\s*[:：]\s*[A-DＡ-Ｄ]\s*", "", (out or "").strip(), count=1, flags=re.S).strip()[:200]
    return {"id": q["id"], "type": q["type"], "mode": mode, "model": model_id, "rep": rep, "gold": gold, "got": got,
            "ok": got == gold, "choices": choices, "reason": reason, "memo": memo}


def page(results, title):
    css = ("body{font-family:system-ui,'Hiragino Sans','Meiryo',sans-serif;max-width:960px;margin:24px auto;padding:0 16px;"
           "background:#fafafa;color:#222}table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #ccc;padding:4px 8px;"
           "font-size:14px}.q{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 14px;margin:14px 0}"
           ".log{font-size:12px;color:#555;white-space:pre-wrap;max-height:220px;overflow:auto;border-left:3px solid #ddd;padding-left:8px}"
           ".ok{color:#1a7f37}.ng{color:#c62828;font-weight:bold}details{font-size:12px;color:#555}")
    keys = sorted({(r["model"], r["mode"]) for r in results})
    h = [f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title><style>{css}</style><h2>{html.escape(title)}</h2>"]
    h.append("<table><tr><th>モデル</th><th>読み方</th><th>正答率</th></tr>")
    for k in keys:
        rs = [r for r in results if (r["model"], r["mode"]) == k]
        h.append(f"<tr><td>{k[0]}</td><td>{k[1]}</td><td>{sum(r['ok'] for r in rs)}/{len(rs)} ({sum(r['ok'] for r in rs) / len(rs):.0%})</td></tr>")
    h.append("</table><table><tr><th>種類</th>" + "".join(f"<th>{k[0]}<br>{k[1]}</th>" for k in keys) + "</tr>")
    for ty in sorted({r["type"] for r in results}):
        h.append(f"<tr><td>{html.escape(ty)}</td>")
        for k in keys:
            rs = [r for r in results if (r["model"], r["mode"]) == k and r["type"] == ty]
            h.append(f"<td>{sum(r['ok'] for r in rs)}/{len(rs)}</td>")
        h.append("</tr>")
    h.append("</table>")
    for q in QS:
        rs = [r for r in results if r["id"] == q["id"]]
        if not rs:
            continue
        h.append(f"<div class='q'><b>{q['id']} [{html.escape(q['type'])}]</b> {html.escape(q['question'])}"
                 f"<div class='log'>{html.escape(chr(10).join(q['log'][-10:]))}</div><p>正解: {html.escape(q['answer'])}</p>")
        for r in rs:
            mark = "<span class='ok'>○</span>" if r["ok"] else "<span class='ng'>×</span>"
            pick = r["choices"][LETTERS.index(r["got"])] if r["got"] in LETTERS[:len(r["choices"])] else "(答えなし)"
            h.append(f"<div>{mark} {r['model']} / {r['mode']} (#{r['rep'] + 1}): {html.escape(pick)}"
                     f"<details><summary>理由</summary>{html.escape(r['reason'])}"
                     + (f"<br><br>読解メモ:<br>{html.escape(r['memo']).replace(chr(10), '<br>')}" if r["memo"] else "")
                     + "</details></div>")
        h.append("</div>")
    return "".join(h)


def main():
    stamp = time.strftime("%Y%m%d_%H%M%S")
    results = []
    jobs = [(q, mode, IDS.get(m, m), rep) for m in MODELS for mode in MODES for q in QS for rep in range(REPEAT)]
    print(f"{len(QS)}問 × モデル{len(MODELS)} × 読み方{len(MODES)} × {REPEAT}回 = {len(jobs)}回 問い合わせます")
    with ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(lambda j: ask(*j), jobs):
            results.append(r)
            print(f"  {'○' if r['ok'] else '×'} {r['id']} {r['model']} {r['mode']}: 答え{r['got']} 正解{r['gold']}  {r['reason'][:50]}")
    print("\n==== 正答率 ====")
    for m in MODELS:
        for mode in MODES:
            rs = [r for r in results if r["model"] == IDS.get(m, m) and r["mode"] == mode]
            if rs:
                print(f"  {m:7s} {mode:9s} {sum(r['ok'] for r in rs)}/{len(rs)} ({sum(r['ok'] for r in rs) / len(rs):.0%})")
    wrong = sorted({r["id"] for r in results if not r["ok"]})
    if wrong:
        print("  間違えた問題: " + " ".join(wrong))
    out = Path("read_test_results")
    out.mkdir(exist_ok=True)
    path = out / f"{stamp}.html"
    path.write_text(page(results, f"読解テスト {stamp}(考える量 {EFFORT})"), encoding="utf-8")
    print(f"\n結果: {path}(ブラウザで開く)")
    if T.get("cost_text") and T["cost_text"]():
        print(T["cost_text"]() + "(モデルを並べたときは目安)")


if __name__ == "__main__":
    main()
