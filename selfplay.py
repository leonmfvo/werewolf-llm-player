"""AI同士の人狼(手元のパソコンの中だけで動く模擬の村)。ブラウザもサイトも使わない。

使い方(think.py と同じフォルダで):
  python selfplay.py                         … 4A(4人+初日犠牲者)を1試合。主役 Sonnet・seishun / 相手 Haiku
  python selfplay.py games=3                 … 3試合続けて
  python selfplay.py main=opus persona=seimitsu strong=1
        main=     主役のモデル (haiku / sonnet / opus / fable)
        others=   相手のモデル (既定 haiku)
        strong=1  相手のうち1人を Sonnet にする(強めの相手を混ぜる)
        solo=1    主役も考える係なし(1回で考えて話す)にする。考える係ありと比べたいとき
        think=1   試合中の画面に主役の考え(考える係の分析など)も出す(既定は出さない)
        persona=  主役の人格 (plain / seimitsu / rewrite / kyouben / seishun / logical ...)
        players=  初日犠牲者を除いた人数(既定4。4Aにするなら 3 = 人狼1・村人2+初日犠牲者)
        human=名前 あなたも1席に入る(コンソールに打って参加する)。例: python selfplay.py players=3 human=あなた day=300
                  打った文はそのまま発言。/vote 名前 で投票、/kill 名前 で(人狼のとき)噛み、/who で状況を見る
                  入力は別の小さな窓で行う(日本語変換のEnterで送信されないように、送信は Ctrl+Enter か「送信」ボタン)
        gui=0     窓を使わずコンソールで打つ / enter=1  窓で Enter だけで送信する(変換のEnterで送られることがある)
        day=      昼の秒数(既定570) / vote= 投票の秒数(既定40) / night= 夜の秒数(既定50) / first_night= 初日の夜(噛み時間)の秒数(既定3。初日犠牲者が噛まれるだけなので待たない)
        prewait=5 単純な版・素のLLM版で、だれも話さなくなってから何秒待って考え始めるか(既定5。考えたらすぐ投稿する。prewait=0 で前の「考えてから打つ時間を待つ」方式)
        keys=8    主役が1秒8打鍵で打つ時間だけ待ってから投稿する(既定8。keys=0 で待たない)
        allraw=1  相手役のAIも全員、素のLLMで動かす(主役と同じ条件で対戦させる。モデルは others= で選ぶ)
        raw=1     主役を素のLLMで動かす(ゲームの説明とログだけ。指示・人格・台帳なし)
        simple=1  主役を単純な版で動かす(1回の呼び出しで話す。比べる用)
        quiet=0   相手役のAIも普通の量で話す(既定は控えめ: 1発言15〜45字・25秒以上あける)
        votetalk=1 投票中も話せるようにする(既定は話せない。人狼Onlineで「話すのをやめてください」の村と同じ)
                  ※夜は30秒以上にする(人狼AIは夜になって20秒待ってから噛むため)

ログ: selfplay_logs/日時_第N試合/ に保存
  game.html     … ブラウザで開くと読みやすいログ(主役AIの考えも表示できる)
  game.txt      … 文字だけのログ
  player_名前.txt … 各AIの頭の中(考えた内容・分析メモ)
  selfplay_logs/summary.csv … 全試合の一覧(勝敗・役職・料金)
主役の振り返りは lessons_pending_ai.txt に書かれる(python lessons.py ai で採用するか決める)。
"""
import csv
import html
import random
import re
import sys
import threading
import time
import traceback
import types
from pathlib import Path

# ---- 設定(起動時の指定で変えられる) ----
OPT = {"games": "1", "main": "sonnet", "others": "haiku", "strong": "0", "persona": "seishun",
       "solo": "0", "think": "0", "players": "4", "day": "570", "vote": "40", "votetalk": "0", "night": "50", "first_night": "3", "post": "30", "thinkfile": "think.py", "slim": "1", "human": "", "gui": "1", "enter": "0", "quiet": "1", "simple": "0", "raw": "0", "keys": "8", "prewait": "", "allraw": "0"}
for a in sys.argv[1:]:
    if "=" in a:
        k, v = a.split("=", 1)
        if k in OPT:
            OPT[k] = {"seisyun": "seishun", "seisyunn": "seishun"}.get(v, v)
MAIN_NAME = "ルアー"
OTHER_NAMES = ["ハク", "ミナト", "ソラ", "ツムギ", "レン", "カイ", "ユズ"]
OTHER_PERSONAS = ["logical", "seishun", "sirius", "aggressive"]
VICTIM = "初日犠牲者"
LOG_ROOT = Path("selfplay_logs")
PRICE = {"haiku": (0.10, 0.50), "sonnet": (2.00, 10.00), "opus": (4.00, 20.00), "fable": (10.00, 50.00)}


def role_list(n):
    """人数に合わせた配役。4〜5人は人狼1・村人。6人以上は占い・霊能・狩人を足す。"""
    if n <= 5:
        return ["人狼"] + ["村人"] * (n - 1)
    base = ["人狼", "人狼", "占い師", "霊能者", "狩人"]
    return (base + ["村人"] * n)[:n]


class Village:
    """進行役(GM)。昼・投票・夜を時間で進め、勝敗を判定する。全員が同じ村を見る。"""

    def __init__(self, names, roles, sec):
        self.lock = threading.RLock()
        self.names = names                       # 初日犠牲者を含む
        self.roles = roles                       # {名前: 役職}
        self.alive = {n: True for n in names}
        self.sec = sec
        self.log = []                            # {t, from_user, to_user, text, vis(None=全員 / set)}
        self.scene = "開始前"
        self.scene_end = time.time() + 5
        self.day = 0
        self.night_no = 0
        self.votes, self.bite, self.guard, self.divine = {}, None, None, {}
        self.last_guard = {}
        self.winner = None
        self.closed = False
        self.t0 = time.time()

    # ---- ログ ----
    def post(self, frm, to, text, vis=None):
        with self.lock:
            self.log.append({"t": time.time(), "from_user": frm, "to_user": to, "text": text, "vis": vis})
        human = OPT.get("human")
        if vis is None or (human and human in vis and frm != human):
            el = time.time() - self.t0
            print(f"  [{int(el // 60):02d}:{int(el % 60):02d}] {frm}{'' if to == 'ALL' else '→' + to}: {text}")

    def gm(self, text, to="ALL", vis=None):
        self.post("鯖", to, text, vis)

    def view(self, name):
        with self.lock:
            return [{"from_user": m["from_user"], "to_user": m["to_user"], "text": m["text"]}
                    for m in self.log if m["vis"] is None or name in m["vis"]]

    def wolves(self):
        return {n for n, r in self.roles.items() if r == "人狼"}

    def dead(self):
        return {n for n, a in self.alive.items() if not a}

    # ---- 進行 ----
    def set_scene(self, scene, seconds):
        with self.lock:
            self.scene = scene
            self.scene_end = time.time() + seconds

    def wait_scene(self):
        while time.time() < self.scene_end:
            time.sleep(0.2)

    def kill(self, n):
        self.alive[n] = False
        self.gm(f"{n}さんが死亡しました")

    def check_win(self):
        alive = [n for n in self.names if self.alive[n]]
        w = sum(1 for n in alive if self.roles[n] == "人狼")
        h = len(alive) - w
        if w == 0:
            self.winner = "村人チーム"
            self.gm("人狼が全滅しました!【村人チーム】の勝利です!")
        elif w >= h:
            self.winner = "人狼チーム"
            self.gm("人狼が村人の数より多くなりました!【人狼チーム】の勝利です!")
        return self.winner

    def run(self):
        n_real = len(self.names)
        counts = {}
        for r in sorted(self.roles.values(), key=lambda r: (r != "人狼", r == "村人")):
            counts[r] = counts.get(r, 0) + 1
        self.gm("役職設定【" + ",".join(f"{r}-{c}" for r, c in counts.items()) + ",役欠け:なし】")
        wolves = sorted(self.wolves())
        for n in self.names:
            if n == VICTIM:
                continue
            r = self.roles[n]
            note = f"あなたは【{r}】です" + (f"　仲間は【{','.join(w for w in wolves if w != n)}】です"
                                           if r == "人狼" and len(wolves) > 1 else "")
            self.gm(note, to=n, vis={n})
        self.wait_scene()
        self.gm("ゲームを開始します")
        while True:
            # ---- 夜 ----
            self.night_no += 1
            first = self.night_no == 1
            self.votes, self.bite, self.guard, self.divine = {}, None, None, {}
            self.gm("夜になりました…役職の人は能力を実行してください")
            self.set_scene("夜", self.sec["first_night"] if first else self.sec["night"])
            self.wait_scene()
            victim = VICTIM if first else self.bite
            if victim is None:
                cands = [n for n in self.names if self.alive[n] and self.roles[n] != "人狼"]
                victim = random.choice(cands)
            # ---- 朝 ----
            self.day += 1
            self.set_scene("昼", self.sec["day"])
            self.gm("恐ろしい夜が明け、朝になりました。")
            if not first and self.guard == victim:
                self.gm("狩人が人狼から守った!")
            else:
                self.kill(victim)
            for seer, t in self.divine.items():
                if self.alive.get(seer):
                    self.gm(f"{t}さんは人狼{'です' if self.roles[t] == '人狼' else 'ではありません'}", to=seer, vis={seer})
            if self.check_win():
                break
            self.wait_scene()
            # ---- 投票 ----
            self.set_scene("投票", self.sec["vote"])
            self.gm("投票時間になりました。投票中も話せます。" if OPT.get("votetalk") == "1"
                    else "投票時間になりました。話すのをやめてください。")
            if OPT.get("human") and self.alive.get(OPT["human"]):
                print(f"  (投票は /vote 名前 。候補: {', '.join(n for n in self.names if self.alive[n] and n != OPT['human'])})")
            self.wait_scene()
            voters = [n for n in self.names if self.alive[n] and n != VICTIM]
            cands_all = [n for n in self.names if self.alive[n]]
            tally = {}
            lines = ["【投票結果】"]
            for v in voters:
                t = self.votes.get(v) or random.choice([c for c in cands_all if c != v])
                self.votes[v] = t
                tally[t] = tally.get(t, 0) + 1
            for v in voters:
                lines.append(f"{v}({tally.get(v, 0)}票)の投票先→{self.votes[v]}")
            top = max(tally.values())
            out = random.choice([n for n, c in tally.items() if c == top])
            lines.append(f"投票の結果【{out}】さんが処刑されます")
            self.gm("\n".join(lines))
            self.kill(out)
            for med in [n for n in self.names if self.roles[n] == "霊能者" and self.alive[n]]:
                self.gm(f"処刑された{out}さんは人狼{'でした' if self.roles[out] == '人狼' else 'ではありません'}",
                        to=med, vis={med})
            if self.check_win():
                break
        # ---- 終了・感想戦 ----
        with self.lock:
            for m in self.log:            # 試合後は全員の役職通知が見えるようにする
                if m["from_user"] == "鯖" and "あなたは【" in m["text"]:
                    m["vis"] = None
        self.gm("【配役】" + " / ".join(f"{n}:{self.roles[n]}" for n in self.names if n != VICTIM))
        self.gm("ログはこちらから→ログ")
        self.set_scene("終了", self.sec["post"])
        print(f"  (感想戦 {self.sec['post']}秒。Ctrl+C で残りの試合をやめて終了)")
        self.wait_scene()
        self.closed = True


class ZView:
    """1人のAIから見た村。think.py が使う人狼Online(zinro.py)と同じ操作を用意する。"""

    def __init__(self, village, name):
        self.v = village
        self.name = name
        self._seen = 0
        self._stopped = False

    def messages(self):
        if self.v.closed and not self._stopped:
            self._stopped = True
            raise KeyboardInterrupt     # 試合が終わったら think.py の終了処理(振り返りなど)へ
        return self.v.view(self.name)

    def new_messages(self):
        m = self.messages()
        new = m[self._seen:]
        self._seen = len(m)
        return new

    def skip_history(self):
        self._seen = len(self.v.view(self.name))

    def scene(self):
        return self.v.scene

    def my_name(self):
        return self.name

    def my_role(self):
        return self.v.roles.get(self.name) if self.v.scene != "開始前" else None

    @staticmethod
    def find_role(msgs, name=None):
        for m in reversed(msgs or []):
            if name and m["to_user"] != name:
                continue
            r = re.search(r"あなたは【(.+?)】", m["text"])
            if r:
                return r.group(1)
        return None

    def players(self):
        return [{"name": n, "alive": self.v.alive[n]} for n in self.v.names]

    def is_alive(self, name):
        return self.v.alive.get(name, True)

    def time_left(self):
        return max(0, int(self.v.scene_end - time.time()))

    def action_choices(self, kind):
        v, me = self.v, self.name
        if not v.alive.get(me) or v.closed:
            return []
        others = [n for n in v.names if v.alive[n] and n != me]
        if kind == "vote" and v.scene == "投票":
            return [n for n in others]
        if kind == "night" and v.scene == "夜":
            role = v.roles.get(me)
            if role == "人狼":
                return [VICTIM] if v.night_no == 1 else [n for n in others if v.roles[n] != "人狼"]
            if role == "占い師":
                return [n for n in others if n != VICTIM]
            if role == "狩人" and v.night_no > 1:
                return [n for n in others if n != v.last_guard.get(me)]
        return []

    def act(self, kind, target):
        v, me = self.v, self.name
        if target not in self.action_choices(kind):
            return False
        if kind == "vote":
            v.votes[me] = target
            v.post(me, "GM", f"投票@{me}→{target}", vis={me})
        else:
            role = v.roles.get(me)
            if role == "人狼":
                v.bite = target
                v.post("人狼", "GM", f"実行@{me}→{target}", vis=v.wolves())
                v.gm(f"{target}さんを噛みます", to="人狼", vis=v.wolves())
            elif role == "占い師":
                v.divine[me] = target
                v.post(me, "GM", f"占い@{me}→{target}", vis={me})
            elif role == "狩人":
                v.guard = target
                v.last_guard[me] = target
                v.post(me, "GM", f"護衛@{me}→{target}", vis={me})
        return True

    def say(self, text):
        v, me = self.v, self.name
        text = text.replace("\n", " ").strip()[:255]
        if not text or v.closed:
            return
        if v.scene in ("開始前", "終了"):
            v.post(me, "ALL", text)
        elif not v.alive.get(me):
            v.post(me, "霊界", text, vis=v.dead())
        elif v.scene == "夜":
            if v.roles.get(me) == "人狼":
                v.post(me, "人狼", text, vis=v.wolves())
                v.post("人狼", "ALL", "アォ" + "-" * random.randint(8, 20) + "ン",
                       vis=set(v.names) - v.wolves())
            else:
                v.post(me, me, text, vis={me})
        elif v.scene == "投票" and OPT.get("votetalk") != "1":
            return False   # 投票中は話せない村(既定)
        else:
            v.post(me, "ALL", text)
        return True

    def close(self):
        pass


# ---- 人間のプレイヤー(コンソールに打って参加する) ----
def human_loop(village, name):
    view = ZView(village, name)
    print(f"\n  ★ あなたは {name} です。打った文がそのまま発言になります。"
          "/vote 名前 で投票、/kill 名前 で噛み(人狼のとき)、/who で状況表示")
    while not village.closed:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        reply = human_command(village, view, name, line)
        if reply:
            print("  " + reply)


def human_command(village, view, name, line):
    """人間の入力1行を処理する。表示する返事の文字(無ければ空)を返す。"""
    line = line.strip()
    if not line or village.closed:
        return ""
    if line.startswith(("/vote ", "/kill ")):
        kind = "vote" if line.startswith("/vote ") else "night"
        target = line.split(" ", 1)[1].strip()
        ok = view.act(kind, target)
        return (f"({'受け付けました' if ok else '今はできません'}: {line}"
                f"{'' if ok else ' / 候補: ' + ', '.join(view.action_choices(kind))})")
    if line == "/who":
        return (f"(場面: {village.scene} / 残り{view.time_left()}秒 / あなたの役職: {village.roles.get(name)} / "
                f"生存: {', '.join(n for n in village.names if village.alive[n])})")
    if view.say(line) is False:
        return "(投票中は発言できません。/vote 名前 で投票してください)"
    return ""


def human_gui(village, name, gm):
    """人間用の窓(ログと入力欄)。変換中のEnterで送られないよう、送信は Ctrl+Enter か「送信」ボタン。
    メインの処理の代わりに動き、試合が終わったら閉じる。"""
    import tkinter as tk
    from tkinter import scrolledtext
    view = ZView(village, name)
    root = tk.Tk()
    root.title(f"人狼 4A — あなたは {name}")
    root.geometry("720x560")
    log = scrolledtext.ScrolledText(root, wrap="word", font=("Meiryo", 11), state="disabled")
    log.pack(fill="both", expand=True, padx=6, pady=6)
    bar = tk.Frame(root)
    bar.pack(fill="x", padx=6, pady=(0, 6))
    box = tk.Text(bar, height=3, wrap="word", font=("Meiryo", 11))
    box.pack(side="left", fill="x", expand=True)
    status = tk.Label(root, anchor="w", font=("Meiryo", 9))
    status.pack(fill="x", padx=6)
    seen = [0]

    def add(text):
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.configure(state="disabled")
        log.see("end")

    def send(event=None):
        line = box.get("1.0", "end").strip().replace("\n", " ")
        box.delete("1.0", "end")
        if line:
            reply = human_command(village, view, name, line)
            if reply:
                add("  " + reply)
        return "break"

    tk.Button(bar, text="送信\n(Ctrl+Enter)", command=send).pack(side="right", padx=(6, 0))
    box.bind("<Control-Return>", send)
    if OPT.get("enter") == "1":
        box.bind("<Return>", send)
    add(f"★ あなたは {name} です。下の欄に打って Ctrl+Enter(または「送信」)で発言。Enter は改行(変換の確定に使える)。")
    add("  /vote 名前 で投票、/kill 名前 で噛み(人狼のとき)、/who で状況表示")

    def tick():
        msgs = village.view(name)
        for m in msgs[seen[0]:]:
            if m["to_user"] == "GM":
                continue
            who = m["from_user"] + ("" if m["to_user"] == "ALL" else "→" + m["to_user"])
            add(f"{who}: {m['text']}")
            if m["from_user"] == "鯖" and "投票時間" in m["text"] and village.alive.get(name):
                add("  (投票は /vote 名前 。候補: " + ", ".join(n for n in village.names if village.alive[n] and n != name) + ")")
        seen[0] = len(msgs)
        status.configure(text=f"場面: {village.scene} / 残り {view.time_left()} 秒 / 役職: {village.roles.get(name)}")
        if gm.is_alive() or not village.closed:
            root.after(500, tick)
        else:
            root.after(3000, root.destroy)

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.after(300, tick)
    box.focus_set()
    root.mainloop()


# ---- AIプレイヤー(think.py をそのまま1人ずつ動かす) ----
SRC = Path(OPT["thinkfile"]).read_text(encoding="utf-8")
for a, b in [("ARGS = [a.lower() for a in sys.argv[1:]]", "ARGS = list(__ARGS)"),
             ("from zinro import Zinro\n", "Zinro = __ZINRO\n")]:
    assert a in SRC, f"think.py の形が想定と違います: {a}"
    SRC = SRC.replace(a, b, 1)
try:
    import ollama  # noqa: F401
except ImportError:
    sys.modules["ollama"] = types.ModuleType("ollama")
CODE = compile(SRC, OPT["thinkfile"], "exec")


class Player:
    def __init__(self, village, name, args, folder, show_console=False):
        self.name = name
        self.args = args
        self.lines = []          # (時刻, 文字)
        self.file = (folder / f"player_{name}.txt").open("w", encoding="utf-8")
        self.show = show_console
        self.g = None
        self.error = None
        view_cls = type("ZV", (ZView,), {"__init__": lambda s: ZView.__init__(s, village, name)})
        self.g = {"__name__": "ai_" + name, "__ARGS": args, "__ZINRO": view_cls, "print": self._print}
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _print(self, *a, sep=" ", end="\n", **k):
        text = sep.join(str(x) for x in a)
        self.lines.append((time.time(), text))
        try:
            self.file.write(time.strftime("%H:%M:%S ") + text + ("\n" if end else ""))
            self.file.flush()
        except ValueError:
            pass        # 試合後にファイルを閉じた後の書き込みは捨てる
        if re.search(r"エラーで止まり|\[振り返り\] 反省の候補", text) or (
                self.show and re.search(r"\[考える係\]|\[振り返り\]|※|エラー", text)):
            print(f"      ({self.name}) {text.strip()[:150]}")

    def _run(self):
        try:
            exec(CODE, self.g)
        except SystemExit:
            pass
        except Exception:
            self.error = traceback.format_exc()
            self._print("※エラーで止まりました:\n" + self.error)
            print(f"  ※{self.name} のAIがエラーで止まりました(player_{self.name}.txt を見てください)")

    def usd(self):
        """このAIが使った料金(think.py の集計から)。"""
        try:
            t = self.g["cost_text"]()
            r = re.search(r"約\$([\d.]+)", t or "")
            return float(r.group(1)) if r else 0.0
        except Exception:
            return 0.0


# ---- ログを読みやすくする ----
COLORS = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d"]
THOUGHT_RE = re.compile(r"\[考える係\]|▶ 発言案|\[見立て\]|▶ .+ を選びました|考え中|※|\[振り返り\]|反省")


def write_logs(folder, village, players, info):
    names = [n for n in village.names if n != VICTIM]
    color = {n: COLORS[i % len(COLORS)] for i, n in enumerate(names)}
    t0 = village.t0

    def ts(t):
        e = t - t0
        return f"{int(e // 60):02d}:{int(e % 60):02d}"

    # 文字のログ
    with (folder / "game.txt").open("w", encoding="utf-8") as f:
        f.write(info["title"] + "\n" + info["sub"] + "\n\n")
        for m in village.log:
            who = m["from_user"] + ("" if m["to_user"] == "ALL" else "→" + m["to_user"])
            vis = "" if m["vis"] is None else "  (非公開)"
            f.write(f"[{ts(m['t'])}] {who}: {m['text']}{vis}\n")

    # HTMLのログ(主役の考えを差し込める)
    items = [(m["t"], "msg", m) for m in village.log]
    for p in players:
        for t, line in p.lines:
            if THOUGHT_RE.search(line) and line.strip():
                items.append((t, "think", {"who": p.name, "text": line.strip()}))
    items.sort(key=lambda x: x[0])
    rows = []
    for t, kind, m in items:
        if kind == "msg":
            frm, to, text = m["from_user"], m["to_user"], m["text"]
            cls = "gm" if frm == "鯖" else ("wolf" if to == "人狼" or frm == "人狼" else "")
            if m["vis"] is not None and frm != "鯖":
                cls += " private"
            label = html.escape(frm) + ("" if to == "ALL" else " → " + html.escape(to))
            style = f' style="color:{color[frm]}"' if frm in color else ""
            rows.append(f'<div class="row {cls}"><span class="t">{ts(t)}</span>'
                        f'<span class="who"{style}>{label}</span>'
                        f'<span class="txt">{html.escape(text).replace(chr(10), "<br>")}</span></div>')
        else:
            main = " main" if m["who"] == MAIN_NAME else ""
            rows.append(f'<div class="row think{main}" data-who="{html.escape(m["who"])}"><span class="t">{ts(t)}</span>'
                        f'<span class="who">💭 {html.escape(m["who"])}</span>'
                        f'<span class="txt">{html.escape(m["text"][:600])}</span></div>')
    role_rows = "".join(f"<tr><td style='color:{color[n]}'>{html.escape(n)}</td><td>{html.escape(village.roles[n])}</td>"
                        f"<td>{html.escape(info['models'].get(n, ''))}</td></tr>" for n in names)
    toggles = "".join(f'<label><input type="checkbox" data-who="{html.escape(n)}" ""> '
                      f'{html.escape(n)}の考え</label>' for n in names)
    page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(info['title'])}</title>
<style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mute:#78716c;--line:#e7e5e4;--gm:#f5f5f4;--wolf:#fef2f2;--think:#f0f9ff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mute:#a8a29e;--line:#292524;--gm:#262626;--wolf:#3b1d1d;--think:#0c2233}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Hiragino Sans","Yu Gothic",sans-serif}}
main{{max-width:900px;margin:0 auto;padding:16px}}
h1{{font-size:20px;margin:8px 0}} .sub{{color:var(--mute);margin-bottom:12px}}
table{{border-collapse:collapse;margin:8px 0 16px}} td{{border:1px solid var(--line);padding:4px 10px}}
.bar{{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:12px;font-size:14px}}
.row{{display:grid;grid-template-columns:48px 150px 1fr;gap:8px;padding:4px 6px;border-bottom:1px solid var(--line)}}
.t{{color:var(--mute);font-variant-numeric:tabular-nums}} .who{{font-weight:600;overflow-wrap:anywhere}}
.gm{{background:var(--gm);color:var(--mute)}} .wolf{{background:var(--wolf)}} .private .txt{{font-style:italic}}
.think{{background:var(--think);font-size:13px;color:var(--mute);display:none}} .think.show{{display:grid}}
@media (max-width:600px){{.row{{grid-template-columns:40px 1fr}} .row .txt{{grid-column:1/-1}}}}
</style></head><body><main>
<h1>{html.escape(info['title'])}</h1><div class="sub">{html.escape(info['sub'])}</div>
<table><tr><td>名前</td><td>役職</td><td>AI</td></tr>{role_rows}</table>
<div class="bar">{toggles}</div>
{''.join(rows)}
</main><script>
function upd(){{document.querySelectorAll('.bar input').forEach(function(c){{
document.querySelectorAll('.think[data-who="'+c.dataset.who+'"]').forEach(function(r){{r.classList.toggle('show',c.checked)}})}})}}
document.querySelectorAll('.bar input').forEach(function(c){{c.addEventListener('change',upd)}});upd();
</script></body></html>"""
    (folder / "game.html").write_text(page, encoding="utf-8")


def play_one(no):
    n = int(OPT["players"])
    human = OPT.get("human") or ""
    names = [MAIN_NAME] + ([human] if human else []) + [x for x in OTHER_NAMES if x != human][:n - 1 - (1 if human else 0)]
    random.shuffle(names)
    roles_ = role_list(n)
    random.shuffle(roles_)
    roles = dict(zip(names, roles_))
    roles[VICTIM] = "村人"
    names = names + [VICTIM]
    sec = {k: int(OPT[k]) for k in ("day", "vote", "night", "first_night", "post")}
    village = Village(names, roles, sec)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder = LOG_ROOT / f"{stamp}_第{no}試合"
    folder.mkdir(parents=True, exist_ok=True)

    players, models = [], {}
    stopped = False
    strong_left = int(OPT["strong"])
    others = [x for x in names if x not in (MAIN_NAME, VICTIM, human)]
    for i, nm in enumerate([MAIN_NAME] + others):
        if nm == MAIN_NAME:
            args = [OPT["main"], OPT["persona"], "noarchive", "selfplay"] + (["slim"] if OPT["slim"] == "1" else []) + (["solo"] if OPT["solo"] == "1" else []) + (["simple"] if OPT["simple"] == "1" else []) + (["raw"] if OPT["raw"] == "1" else []) + ([f"keys={OPT['keys']}"] if OPT["keys"] not in ("", "0") else []) + ([f"prewait={OPT['prewait']}"] if OPT["prewait"] else [])
        else:
            model = OPT["others"]
            if strong_left > 0:
                model, strong_left = "sonnet", strong_left - 1
            persona = OTHER_PERSONAS[(i - 1) % len(OTHER_PERSONAS)]
            args = [model, persona, "noarchive", "solo", "selfplay", "noreflect"] + (["quiet"] if OPT["quiet"] == "1" else [])
            if OPT["allraw"] == "1":   # 相手役も主役と同じ素のLLM(raw_prompt.txt も同じものを読む)
                args = [model, persona, "noarchive", "solo", "selfplay", "noreflect", "raw"] + (
                    [f"keys={OPT['keys']}"] if OPT["keys"] not in ("", "0") else []) + (
                    [f"prewait={OPT['prewait']}"] if OPT["prewait"] else [])
        models[nm] = f"{args[0]} / {args[1]}"
        players.append(Player(village, nm, args, folder, show_console=(nm == MAIN_NAME and OPT["think"] == "1")))
    print(f"\n===== 第{no}試合 =====")
    for p in players:
        print(f"  {p.name}: {'?' if human else roles[p.name]}  ({models[p.name]}{' ・主役' if p.name == MAIN_NAME else ''})")
    if human:
        models[human] = "人間"
        print(f"  {human}: {roles[human]}  (あなた)")
        if OPT.get("gui") != "1":
            threading.Thread(target=human_loop, args=(village, human), daemon=True).start()
    for p in players:
        p.thread.start()
    time.sleep(3)                      # 全員が起動して画面を読み始めるのを待つ
    gm = threading.Thread(target=village.run, daemon=True)
    gm.start()
    try:
        if human and OPT.get("gui") == "1":
            try:
                human_gui(village, human, gm)     # 窓を閉じても試合は続く(その場合は投票などがランダムになる)
            except Exception as e:
                print(f"  ※入力用の窓を開けませんでした({e})。コンソールで打ってください")
                threading.Thread(target=human_loop, args=(village, human), daemon=True).start()
        gm.join()
        if any(p.thread.is_alive() for p in players):
            print("\n  (主役が試合を振り返って反省の候補を書いています。最大2分ほど待ちます。止めるなら Ctrl+C)")
        for p in players:              # 主役の振り返りの書き込みなどを待つ
            p.thread.join(150)
    except KeyboardInterrupt:
        village.closed = True
        stopped = True
        print("\n中断しました。AIたちを止めて、ここまでのログを保存します(数秒かかります)")
        for p in players:
            try:
                p.thread.join(15)
            except KeyboardInterrupt:
                break
    cost = sum(p.usd() for p in players)
    main_cost = sum(p.usd() for p in players if p.name == MAIN_NAME)
    main_role = roles[MAIN_NAME]
    won = village.winner and ((main_role == "人狼") == (village.winner == "人狼チーム"))
    info = {"title": f"AI戦 第{no}試合({time.strftime('%Y-%m-%d %H:%M')})",
            "sub": f"{village.winner or '中断'} / 主役 {MAIN_NAME}={main_role}({'勝ち' if won else '負け'}) / "
                   f"料金 約${cost:.2f}(主役 ${main_cost:.2f})",
            "models": models}
    write_logs(folder, village, players, info)
    for p in players:
        p.file.close()
    summary = LOG_ROOT / "summary.csv"
    new_file = not summary.exists()
    with summary.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["日時", "フォルダ", "人数", "勝者", "主役の役職", "主役の勝敗", "主役モデル", "主役人格",
                        "相手モデル", "料金USD", "主役料金USD", "エラー"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M"), folder.name, n, village.winner or "中断", main_role,
                    "勝ち" if won else "負け", OPT["main"], OPT["persona"],
                    " / ".join(f"{k}:{v}" for k, v in models.items() if k != MAIN_NAME),
                    f"{cost:.3f}", f"{main_cost:.3f}",
                    " / ".join(p.name for p in players if p.error)])
    print(f"\n結果: {info['sub']}")
    # 昼の議論での発言の割合(試合の長さに左右されない比べ方。本人は発言数37%・文字数39%が中央値)
    day_talk, in_day = [], False
    for m in village.log:
        if m["from_user"] == "鯖":
            if "朝になりました" in m["text"]:
                in_day = True
            elif "処刑されます" in m["text"] or "【投票結果】" in m["text"]:
                in_day = False
            continue
        if in_day and m["vis"] is None and m["from_user"] != VICTIM:
            day_talk.append(m)
    if day_talk:
        tot_n, tot_c = len(day_talk), sum(len(m["text"]) for m in day_talk)
        print("昼の発言の割合: " + " / ".join(
            f"{n} 発言{sum(1 for m in day_talk if m['from_user'] == n) / tot_n:.0%}・文字{sum(len(m['text']) for m in day_talk if m['from_user'] == n) / max(1, tot_c):.0%}"
            for n in village.names if n != VICTIM) + "(本人の中央値: 発言37%・文字39%)")
        # 主役の「番」(続けて打った発言をまとめたもの)の長さ。本人は60字超えの番が約15〜20%、その中央値97字
        turns, prev = [], None
        for m in day_talk:
            if m["from_user"] == MAIN_NAME:
                if prev == MAIN_NAME:
                    turns[-1] += len(m["text"])
                else:
                    turns.append(len(m["text"]))
            prev = m["from_user"]
        if turns:
            longs = sorted(t for t in turns if t > 60)
            print(f"{MAIN_NAME}の長い番: 60字超え {len(longs)}/{len(turns)}回({len(longs) / len(turns):.0%})"
                  + (f"・長さ {', '.join(map(str, longs))}字" if longs else "") + "(本人: 約15〜20%・中央値97字・最長187字)")
    print(f"ログ: {folder / 'game.html'}(ブラウザで開く)")
    return village.winner is not None and not stopped


def main():
    LOG_ROOT.mkdir(exist_ok=True)
    print(f"AI戦: {OPT['games']}試合 / 主役 {OPT['main']}・{OPT['persona']} / 相手 {OPT['others']}"
          f"{'(うち' + OPT['strong'] + '人 sonnet)' if OPT['strong'] != '0' else ''} / {OPT['players']}人村")
    for no in range(1, int(OPT["games"]) + 1):
        if not play_one(no):
            break
    print("\n終わりました。一覧は selfplay_logs/summary.csv、主役の反省の候補は lessons_pending_ai.txt にあります"
          "(python lessons.py ai で採用するか決められます)")


if __name__ == "__main__":
    main()
