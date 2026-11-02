"""見たログを手元に保存して、あとから探せるようにする(過去の発言を参照・照合するため)。

保存先: logs/archive/日付.jsonl(1行に1発言。他の人には送らない、手元だけのファイル)
試合が終わるたびに、その試合のまとめ(自分の名前・役職・勝敗・配役)も保存する。

使い方(think.py から):
    arc = Archive()
    arc.add(新しい発言のリスト, 自分の名前, シーン)
    arc.recall(最新の発言, 参加者の名前)   → AIに渡す文章(見つからなければ "")
    arc.games_text()                         → 最近の試合のまとめ
"""
import json
import re
import time
from pathlib import Path

ROOT = Path("logs") / "archive"
KEEP_DAYS = 90          # 読み込む過去の日数(古いファイルは消さない。読み込まないだけ)

# 「過去を振り返る」発言の目印
PAST_RE = re.compile(r"さっき|先ほど|前の試合|前回|前の村|前のゲーム|この前|以前|あの時|あのとき|昨日|"
                     r"覚えて|言ってた|言ってる|言ったよね|言ったじゃん|発言して|ログ|遡|さかのぼ|前に|最初に|"
                     r"何試合|何戦|前の|ヶ月前|か月前|日前|週間前|名前で参戦|参戦した")
QUOTE_RE = re.compile(r"[「『](.{4,80}?)[」』]")
WORD_RE = re.compile(r"[一-龥ァ-ヴーA-Za-z0-9]{2,}")
TERMS = ("占い", "霊能", "狩人", "猫又", "狂人", "人狼", "CO", "黒", "白", "吊", "噛", "護衛", "投票", "結果")
STOP = {"さっき", "前回", "試合", "発言", "ログ", "言って", "以前", "覚え", "人狼", "村人", "ゲーム", "名前", "参戦"}


class Archive:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.rows = []      # 発言 {t, g, me, scene, from, to, text}
        self.games = []     # 試合のまとめ {t, g, me, role, winner, roles}
        self.game_no = 0
        self._load()

    def _files(self):
        cutoff = time.time() - KEEP_DAYS * 86400
        return sorted(p for p in self.root.glob("*.jsonl") if p.stat().st_mtime >= cutoff)

    def _load(self):
        for p in self._files():
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                (self.games if r.get("kind") == "game" else self.rows).append(r)
        self.game_no = max([r.get("g", 0) for r in self.rows] + [r.get("g", 0) for r in self.games] + [0])

    def _write(self, rec):
        path = self.root / (time.strftime("%Y-%m-%d") + ".jsonl")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def add(self, msgs, me, scene):
        """新しく見た発言を保存する。試合の開始・終了も拾う。"""
        for m in msgs:
            if m["from_user"] == "鯖" and "ゲームを開始します" in m["text"]:
                self.game_no += 1
            rec = {"t": time.time(), "g": self.game_no, "me": me or "", "scene": scene or "",
                   "from": m["from_user"], "to": m["to_user"], "text": m["text"]}
            self.rows.append(rec)
            self._write(rec)

    def add_game(self, me, role, winner, roles):
        """試合のまとめを保存する(同じ試合は1回だけ)。"""
        if any(r.get("g") == self.game_no for r in self.games):
            return
        rec = {"kind": "game", "t": time.time(), "g": self.game_no, "me": me or "", "role": role or "",
               "winner": winner or "", "roles": roles or {}}
        self.games.append(rec)
        self._write(rec)

    # ---- 探す ----
    @staticmethod
    def _when(t):
        d = time.time() - t
        if d < 3600:
            return f"{int(d // 60)}分前"
        if d < 86400:
            return f"{int(d // 3600)}時間前"
        return time.strftime("%m/%d", time.localtime(t))

    def _line(self, r):
        to = "" if r["to"] == "ALL" else f"→{r['to']}"
        mine = "(自分)" if r["from"] == r.get("me") and r["from"] else ""
        return f"[第{r['g']}試合・{self._when(r['t'])}] {r['from']}{mine}{to}: {r['text'][:120]}"

    @staticmethod
    def _grams(t):
        t = re.sub(r"[\s、。！？!?「」『』ｗw…]", "", t)
        return {t[i:i + 2] for i in range(len(t) - 1)}

    def find_quote(self, q):
        """引用された文が保存したログにあるか。(発言, 完全一致か) を返す。
        そのままの文が無ければ、言葉の4割以上が重なる一番近い発言を返す(言い換えの確認用)。"""
        key = re.sub(r"\s", "", q)
        for r in reversed(self.rows):
            if key and key in re.sub(r"\s", "", r["text"]):
                return r, True
        gq = self._grams(q)
        best, score = None, 0.0
        for r in self.rows[-5000:]:
            if r["from"] in ("鯖", "人狼"):
                continue
            gr = self._grams(r["text"])
            if gq and gr:
                sc = len(gq & gr) / len(gq)
                if sc > score:
                    best, score = r, sc
        return (best, False) if score >= 0.4 else (None, False)

    def search(self, words, names, terms=(), limit=8, skip_last=0):
        """言葉と名前で発言を探す。新しい試合ほど・一致が多いほど上に。"""
        rows = self.rows[:len(self.rows) - skip_last] if skip_last else self.rows
        scored = []
        for i, r in enumerate(rows):
            if r["from"] in ("鯖", "人狼") and not any(n in r["text"] for n in names):
                continue
            s = (sum(2 for w in words if w in r["text"]) + sum(2 for n in names if n == r["from"])
                 + sum(1 for n in names if n in r["text"]) + sum(1 for w in terms if w in r["text"]))
            if s >= 2:
                scored.append((s, i, r))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [r for _, _, r in scored[:limit]]

    def recall(self, latest, players, recent_skip=40):
        """最新の発言が過去に触れていたら、保存したログから関係しそうな発言を探してAIに渡す文章にする。
        recent_skip: 直近の発言(今の画面に見えている分)は探さない。"""
        texts = [m["text"] for m in latest if m["from_user"] not in ("鯖", "人狼")]
        if not texts or not any(PAST_RE.search(t) or QUOTE_RE.search(t) for t in texts):
            return ""
        out = []
        # 引用の照合
        for t in texts:
            for q in QUOTE_RE.findall(t):
                r, exact = self.find_quote(q)
                if r and exact:
                    res = f"ログにある: {self._line(r)}"
                elif r:
                    res = f"そのままの文は無い。近い発言(言い換えの可能性): {self._line(r)}"
                else:
                    res = "保存したログには見つからない(記憶違い・作り話の可能性)"
                out.append(f"・引用「{q[:40]}」→ {res}")
        # 言葉・名前で検索
        known = set(players) | {r["from"] for r in self.rows[-2000:]} | {g["me"] for g in self.games}
        names = [n for n in known if n and len(n) >= 2 and any(n in t for t in texts)]
        words = [w for t in texts for w in WORD_RE.findall(t) if w not in STOP and w not in names][:12]
        terms = [w for w in TERMS if any(w in t for t in texts)]
        hits = self.search(words, names, terms=terms, skip_last=recent_skip)
        if hits:
            out.append("・関係しそうな過去の発言:")
            out += [f"  {self._line(r)}" for r in hits]
        if names:
            my_games = [g for g in self.games if g["me"] in names]
            for g in my_games[-3:]:
                out.append(f"・第{g['g']}試合({self._when(g['t'])})では自分は「{g['me']}」という名前で参加し、役職は{g['role'] or '不明'}、{g['winner'] or '勝敗不明'}")
        if not out:
            return ""
        return ("【保存してある過去ログの検索結果(自動。ここに無いことは「記録に無い」と言う。作らない)】\n"
                + "\n".join(out))

    def games_text(self, n=5):
        """最近の試合のまとめ。"""
        if not self.games:
            return ""
        rows = []
        for g in self.games[-n:]:
            roles = "・".join(f"{k}={v}" for k, v in list(g.get("roles", {}).items())[:12])
            rows.append(f"・第{g['g']}試合({self._when(g['t'])}): 自分の名前「{g['me']}」役職{g['role'] or '不明'} / "
                        f"{g['winner'] or '勝敗不明'}" + (f" / 配役 {roles}" if roles else ""))
        return "【最近の試合の記録(自動保存)】\n" + "\n".join(rows)
