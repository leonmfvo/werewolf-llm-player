"""盤面の集計(大人数村向け)。ログ全体から、CO・占い/霊能結果・処刑・噛み・GJ・指定・護衛指示・進行の合意を拾う。
AIは直近の発言しか読まないので、流れてしまったCOや結果をここでまとめてAIに渡す。
推理はしない。ログに書いてある事実だけを集める。"""
import re

ROLE_WORDS = [("占い", "占い師"), ("占", "占い師"), ("霊能", "霊能者"), ("霊", "霊能者"),
              ("狩人", "狩人"), ("狩", "狩人"), ("騎士", "狩人"), ("猫又", "猫又"), ("猫", "猫又"),
              ("共有", "共有者"), ("共", "共有者")]
# 【占いCO】【霊能者CO】【猫co】【占いＣＯ】【占いCO A白】 のように、【】の中に役職とCOがあるもの
CO_RE = re.compile(r"【([^】]*?)(占い|占|霊能|霊|狩人|狩|騎士|猫又|猫|共有|共)(師|者)?\s*[CcＣｃ][OoＯｏ]([^】]*)】")
WHITE = ("白", "○", "◯", "村人", "人狼ではない")
BLACK = ("黒", "●", "人狼")
SYSTEM = ("鯖", "人狼", "GM", "初日犠牲者")


def match_names(text, names):
    """text に出てくるプレイヤー名を、出てきた順に返す。略称(「たろう」→「XYZたろう」のような短縮)にも対応。"""
    found = []
    for n in sorted(names, key=len, reverse=True):
        i = text.find(n)
        if i >= 0 and not any(n in f for _, f in found):
            found.append((i, n))
    if not found:
        # 略称: 名前の一部(3文字以上、ひらがな・カタカナ名なら2文字以上)が入っているか
        for n in names:
            best = None
            for L in range(len(n) - 1, 1, -1):
                if L < 3 and re.search(r"[A-Za-z0-9]", n):
                    break
                for s in range(0, len(n) - L + 1):
                    piece = n[s:s + L]
                    if piece in text and not re.fullmatch(r"[白黒占霊狩猫結果COco]+", piece):
                        best = (text.find(piece), n)
                        break
                if best:
                    break
            if best:
                found.append(best)
    return [n for _, n in sorted(found)]


def _color(text):
    w = min((text.find(c) for c in WHITE if c in text), default=-1)
    b = min((text.find(c) for c in BLACK if c in text), default=-1)
    if w < 0 and b < 0:
        return None
    if w < 0:
        return True
    if b < 0:
        return False
    return b < w   # 先に出てきた方


def _results_in(text, names, owner):
    """「A白」「B黒」「C○」のような結果を {名前: True(黒)/False(白)} にする。"""
    out = {}
    # 名前ごとに、その直後(10文字以内)の白黒を見る
    for n in match_names(text, [x for x in names if x != owner]):
        i = text.find(n)
        if i < 0:
            # 略称で見つかった場合
            for L in range(len(n) - 1, 1, -1):
                for s in range(0, len(n) - L + 1):
                    if n[s:s + L] in text:
                        i = text.find(n[s:s + L])
                        break
                if i >= 0:
                    break
        tail = text[i:i + len(n) + 10]
        c = _color(tail)
        if c is not None:
            out[n] = c
    return out


def parse(msgs, me, names):
    """ログ(古い順、今の試合の分)から盤面を集計する。names は参加者全員(死者を含む)の名前。"""
    b = {"co": {}, "co_order": [], "results": {}, "executed": [], "bitten": [], "gj_days": [],
         "day": 0, "designate": None, "guard_order": None, "rolla": None, "hunter_called": [],
         "co_called": {}, "leaders": set(),
         "my_guards": [], "today_start": 0}
    day = 0
    night = True    # 試合は夜から始まる
    deaths = []     # (日, 名前, 夜に死んだか)
    for idx, m in enumerate(msgs):
        f, to, t = m["from_user"], m["to_user"], m["text"]
        if f == "鯖":
            if "夜になりました" in t:
                night = True
            if "朝になりました" in t:
                day += 1
                night = False
                b["today_start"] = idx
                b["designate"] = b["guard_order"] = b["rolla"] = None
                b["hunter_called"] = []
                b["co_called"] = {}
            if "狩人が人狼から守った" in t:
                b["gj_days"].append(day + 1 if night else day)
            r = re.search(r"投票の結果【(.+?)】さんが処刑されます", t)
            if r:
                b["executed"].append((day, r.group(1)))
            r = re.match(r"(.+?)さんが死亡しました", t)
            if r:
                deaths.append((day + 1 if night else day, r.group(1), night))
            continue
        if to == "GM":
            r = re.search(r"実行@" + re.escape(me) + r"→(.+)$", t) if f == me else None
            if r:
                b["my_guards"].append((day, r.group(1).strip()))
            continue
        if to != "ALL" or f not in names:
            continue
        # 他人の発言の引用(「名前: …」で始まる行)は除いて読む
        own = "\n".join(l for l in t.splitlines() if not re.match(r"^\s*\S{1,20}\s*[:：]\s", l)) or ""
        # CO
        for r in CO_RE.finditer(own):
            role = dict(ROLE_WORDS)[r.group(2)]
            if f not in b["co"]:
                b["co"][f] = role
                b["co_order"].append((day, f, role))
        role = b["co"].get(f)
        # 結果(占い師・霊能者COの人の、【】付きの発言)
        if role in ("占い師", "霊能者") and "【" in own:
            res = _results_in(own, names, f)
            if not res and role == "霊能者" and b["executed"]:
                c = _color(own)
                if c is not None and re.search(r"【\s*[白黒●○]\s*】", own):
                    res = {b["executed"][-1][1]: c}
            for p, c in res.items():
                b["results"].setdefault(f, {})[p] = c
        # 指定・護衛指示・進行の合意(今日の分だけ)
        if f != me:
            r = (re.search(r"指定\s*[→:：>＞]\s*([^\s】、。]+)", own)
                 or re.search(r"([^\s、。「」【】]{2,12}?)(?:さん)?\s*指定(?:で|します|です|！|!|\s|$)", own))
            if r:
                nm = match_names(r.group(1), [n for n in names if n != f])
                if nm:
                    b["designate"] = (nm[0], f)
            r = (re.search(r"([^\s、。「」【】]{2,12}?)(?:さん)?\s*(?:を)?\s*護衛\s*(?:固定|して|しろ|お願い|ください|指示|で)", own)
                 or re.search(r"護衛(?:先)?\s*[→:：は]\s*([^\s、。]+)", own))
            if r:
                nm = match_names(r.group(1), [n for n in names if n not in (me, f)])
                if nm:
                    b["guard_order"] = (nm[-1], f)
            if re.search(r"霊ロラ|霊能ロラ", own):
                b["rolla"] = "霊能者"
            elif re.search(r"占いロラ|占ロラ", own):
                b["rolla"] = "占い師"
            # 指定役(進行役): 「指定役は俺」「進行やります」と名乗った人、【指定→】を出した人、猫又・共有CO
            if re.search(r"(指定役|進行役|進行)(は)?(俺|私|自分|僕|やります|します|でいい|もらいます)", own) or "【指定" in own:
                b["leaders"].add(f)
            # COの要請(「灰に狩がいたら露出して」「猫又出て」など)
            ask = re.search(r"(露出|(CO|ＣＯ|co)\s*(して|お願い|しろ|ください|頼む)|出て(きて|ください|ほしい|欲しい|くれ)|出ろ|名乗って)", own)
            if ask and not CO_RE.search(own):
                for word, role in (("狩", "狩人"), ("騎士", "狩人"), ("猫", "猫又")):
                    if word in own:
                        lst = b["co_called"].setdefault(role, [])
                        if f not in lst:
                            lst.append(f)
                if "狩" in own and f not in b["hunter_called"]:
                    b["hunter_called"].append(f)   # 狩人の露出・COを求めた人
    b["leaders"] |= {p for p, r in b["co"].items() if r in ("猫又", "共有者")}
    executed = {n for _, n in b["executed"]}
    b["bitten"] = [(d, n) for d, n, at_night in deaths if at_night and n not in executed]
    b["day"] = day
    return b


def summary(b, me, alive, all_names):
    """AIに渡す盤面表の文章。"""
    dead_how = {n: f"{d}日目に処刑" for d, n in b["executed"]}
    dead_how.update({n: f"{d}日目の朝に噛まれた" for d, n in b["bitten"]})
    def st(n):
        return "生存" if n in alive else dead_how.get(n, "死亡")
    lines = ["【盤面表(Pythonがログ全体から集計した事実。COや結果はこれを正とする)】"]
    by_role = {}
    for _, p, role in b["co_order"]:
        by_role.setdefault(role, []).append(p)
    if by_role:
        lines.append("CO: " + " / ".join(f"{r}: " + ", ".join(f"{p}({st(p)})" for p in ps)
                                         for r, ps in by_role.items()))
        nums = {r: len(ps) for r, ps in by_role.items()}
        if "占い師" in nums or "霊能者" in nums:
            lines.append(f"CO状況: {nums.get('占い師', 0)}-{nums.get('霊能者', 0)}(占い-霊能)")
    else:
        lines.append("CO: まだ誰もCOしていない")
    for seer, res in b["results"].items():
        kind = "占い" if b["co"].get(seer) == "占い師" else "霊能"
        lines.append(f"{seer}の{kind}結果: " + ", ".join(f"{p}{'●黒' if c else '○白'}" for p, c in res.items()))
    if b["executed"]:
        lines.append("処刑: " + ", ".join(f"{d}日目 {n}" for d, n in b["executed"]))
    if b["bitten"]:
        lines.append("噛み: " + ", ".join(f"{d}日目朝 {n}" for d, n in b["bitten"]))
    if b["gj_days"]:
        lines.append("GJ(誰も噛まれなかった朝): " + ", ".join(f"{d}日目" for d in b["gj_days"]))
    has_info = set(b["co"]) | {p for r in b["results"].values() for p in r}
    gray = [n for n in alive if n not in has_info and n != me]
    lines.append("灰(COも結果もない生存者、あなたを除く): " + (", ".join(gray) if gray else "なし"))
    if b["designate"]:
        lines.append(f"今日の指定: {b['designate'][0]}({b['designate'][1]}が指定)")
    if b["rolla"]:
        lines.append(f"今日の進行で出ている案: {b['rolla']}ロラ")
    if b["guard_order"]:
        lines.append(f"護衛指示: {b['guard_order'][0]}を護衛({b['guard_order'][1]}の指示)")
    return "\n".join(lines)


def hunter_diary(b):
    """狩日記(毎晩の護衛先)。"""
    return " ".join(f"{'初日' if d == 0 else str(d) + '日目'}夜{p}" for d, p in b["my_guards"] if p != "初日犠牲者")
