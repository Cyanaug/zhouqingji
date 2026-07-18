# -*- coding: utf-8 -*-
"""跑批 runner：覆盖账（03 定义的计算视图）、盲读任务计划、阅读记录落盘。

用法：
  python runner.py coverage                 # 打印覆盖账摘要
  python runner.py plan --poems 6 --readers 4 [--out FILE]
                                            # 挑覆盖最薄的组合生成一批盲读任务
  python runner.py ingest --file FILE       # 校验一批读稿结果并 append 到 reads.jsonl

约定：
- 阅读记录 schema 冻结（见 03），本文件只做落盘与校验，不做任何"排名"。
- reads.jsonl 为 append-only；本代码读 corpus、写 results，绝不改 corpus。
"""
import argparse
import hashlib
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus" / "诗稿.json"
INTERP = ROOT / "corpus" / "昼青·诠释.md"
PERSONAS = ROOT / "theater" / "personas" / "personas.json"
PERSONAS_SIDECAR = ROOT / "corpus" / "personas.json"
READS = ROOT / "results" / "reads" / "reads.jsonl"
BATCHES = ROOT / "theater" / "runners" / "batches"
THREAD_DIR = ROOT / "results" / "threads"
THREAD_META = THREAD_DIR / "meta.json"          # 侧车：{read_id: {persona_hash, depth, stance_changed, void, void_reason}}
THREAD_SILENCES = THREAD_DIR / "silences.jsonl"  # 侧车：append-only 沉默事件日志
THREAD_TOKEN_BUDGET = 6000  # 祖先链原文字符预算（无分词器，字符数近似；具体数字待 2 首诗试点后校准）
VOTES_DIR = ROOT / "results" / "votes"
VOTES = VOTES_DIR / "votes.jsonl"  # 侧车：读者对既有短评的 认同/不认同/跳过，独立于 reads.jsonl
VOTES_VOID = VOTES_DIR / "void.json"  # 侧车：{vote_id: {reason, ts}}——票据 append-only，作废走标记（同 thread void 先例）
CURATION = ROOT / "results" / "curation.json"


def hidden_read_ids():
    """返回用户已折叠（hidden=True）的 read_id 集合，供派发脚本过滤。"""
    if not CURATION.exists():
        return set()
    cur = json.loads(CURATION.read_text(encoding="utf-8"))
    return {rid for rid, v in cur.items() if v.get("hidden")}

REQUIRED_FIELDS = ["poem_id", "reader", "context_mode", "transport",
                   "score", "reaction", "content_hash"]

BASELINE = """你是「昼青集·读诗剧场」的一位读者。你面前只有一首诗。请认真读它，给出你真实的反应。

必须遵守的读者底线：
1. 读懂并说出诗里的感受，是好读者。关于技艺（意象、结构、语言、节奏）的逆耳批评请保留——作者要听真话，不要捧场客。
2. 但「情绪低沉 = 诗差」是误读。有些诗很消沉；低沉是被读懂的对象，不是被扣分的理由。把「这首诗传达得好不好」和「这首诗的情绪暗不暗」严格分开。
3. 你只读眼前这一首，不与任何别的诗比较排名，不猜测作者其他作品。
4. 评分是你个人的真实反应（0–10，可带一位小数），不是客观裁决。7 分以上意味着你真心喜欢、觉得写得好；8 分以上留给那种读完还惦记、会想再读一遍、或者会主动讲给别人听的——如果一首诗真的到了这个程度，不要因为「很少给高分」就压着不给。不要礼貌性给分。

你的产出必须是一个 JSON 对象（不要包裹在代码块外再加文字）：
{
  "score": 7.5,
  "reaction": "两到三句话的真实短评，120 字以内。像跟帖，不像论文摘要。",
  "long_form": null
}
只有当这首诗真的让你有超出短评的话要说时，才把 long_form 写成一篇几百字的深读（否则保持 null）。"""


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def load_reads():
    if not READS.exists():
        return []
    out = []
    for line in READS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


STANZAS = ROOT / "corpus" / "分段.json"
SETTINGS = ROOT / "corpus" / "settings.json"

POETRY_GENRES = ("现代诗", "词", "歌词")

GENRE_SWITCH = """—— 体裁转换 ——
上面的读者底线是为读诗写的；你现在拿到的不是诗，是一篇{genre}。
四条底线原样适用：说真话、情绪暗不等于写得差、只读眼前这一篇、评分是你的真实反应。
只是把诗的判据（断行、意象密度、节奏这些）换成{genre}应有的判据去感受。
仍然用欣赏的眼光，按你自己的性格与偏好来读，不必比读诗时更苛刻，也不必更宽容。"""


def load_settings_file():
    """corpus/settings.json 侧车（GUI 设置页写入）。runner 只关心
    read_genres（勾选进读者池的非诗文体）与 genre_notes（作者补充的评判要求）。"""
    if SETTINGS.exists():
        try:
            d = json.loads(SETTINGS.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            pass
    return {}


def load_personas():
    """默认人设（theater/personas/personas.json，git 跟踪、随更新可覆盖）
    ＋ 读者侧车（corpus/personas.json，已 gitignore、pull 永不覆盖）合并。
    按 persona_id：侧车同 id 部分覆盖字段、新 id 追加、hidden=true 撤下某默认。
    无侧车文件时行为与旧版一致。与 server.load_personas 同一套口径。"""
    base = load_json(PERSONAS)
    order = [p["persona_id"] for p in base]
    merged = {p["persona_id"]: p for p in base}
    if PERSONAS_SIDECAR.exists():
        try:
            side = json.loads(PERSONAS_SIDECAR.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            side = []
        if isinstance(side, list):
            for p in side:
                pid = (p or {}).get("persona_id")
                if not pid:
                    continue
                if pid in merged:
                    merged[pid] = {**merged[pid], **p}
                else:
                    merged[pid] = p
                    order.append(pid)
    return [merged[pid] for pid in order if not merged[pid].get("hidden")]


def pool():
    """读者池：public 且（ai_read，或文体被设置页勾选进 read_genres）。
    诗类（POETRY_GENRES）走 ai_read 老规则；其他文体默认在池外，作者勾选才读。"""
    extra = set(load_settings_file().get("read_genres") or [])
    return [p for p in load_json(CORPUS)
            if p["visibility"] == "public"
            and (p["ai_read"] or p.get("genre") in extra)]


def load_stanzas():
    if STANZAS.exists():
        return json.loads(STANZAS.read_text(encoding="utf-8"))
    return {}


def poem_text(poem, stanzas):
    """读者读到的正文：有作者手工分段（侧车）时应用之——
    分段是恢复导出丢失的信息而非修订，content 与 content_hash 不动。"""
    breaks = (stanzas or {}).get(poem["id"])
    if not breaks:
        return poem["content"]
    lines = [l for l in poem["content"].split("\n") if l.strip()]
    bset = set(breaks)
    out = []
    for i, ln in enumerate(lines):
        out.append(ln)
        if i in bset and i < len(lines) - 1:
            out.append("")
    return "\n".join(out)


def build_prompt(poem, persona, stanzas=None, with_note=False, genre_notes=None):
    if persona.get("superseded_by"):
        sys.exit(f"{persona['persona_id']} 已被 {persona['superseded_by']} 取代，"
                  f"不应再派发新任务（2026-07-13 poetry-editor 误派发事故后加的硬闸门）。"
                  f"如果确实要读旧版历史用途，直接改这行代码绕过，不要静默继续。")
    genre = (poem.get("genre") or "").strip() or "文章"
    is_poetry = genre in POETRY_GENRES
    parts = [BASELINE]
    if not is_poetry:
        # 体裁转换：底线与人设一个字不动，只声明"这不是诗"并换判据——
        # 同一批读者以自己的性格读散文/小说，而不是拿诗的标准压别的文体。
        parts += ["", GENRE_SWITCH.format(genre=genre)]
        note = ((genre_notes or {}).get(genre) or "").strip()
        if note:
            parts += ["", f"作者对{genre}的补充评判要求：{note}"]
    parts += ["", "—— 你是谁 ——", persona["persona"]]
    if persona.get("knows_诠释") and INTERP.exists():
        parts += ["", "—— 你读过作者的自述档案《昼青·诠释》（背景，不是标准答案）——",
                  INTERP.read_text(encoding="utf-8")]
    head = "—— 现在，读这首诗 ——" if is_poetry else f"—— 现在，读这篇{genre} ——"
    parts += ["", head, f"《{poem['title']}》"]
    if persona.get("knows_date"):
        when = poem.get("date_written") or poem.get("created", "")[:7]
        parts += [f"（写于 {when}）"]
    if persona.get("reads_background") and poem.get("background"):
        parts += [f"（背景小注：{poem['background']}）"]
    tail = "—— 诗歌正文到此为止 ——" if is_poetry else "—— 正文到此为止 ——"
    parts += ["", poem_text(poem, stanzas), "", tail]
    if with_note and (poem.get("note") or "").strip():
        parts += ["", "—— 作者眉批 ——",
                  "你拿到的不是净本，是作者的手稿批注本：下面的眉批是这份文档的一部分，"
                  "与正文一起读。它是作者的私语，不是标准答案——正文与眉批之间的落差，"
                  "也在你的所见之内。",
                  "", poem["note"].strip()]
    return "\n".join(parts)


def persona_sha1(persona):
    """比照 content_hash 的做法：记一份人格文本的 sha1，人格改了就知道读的是哪版。"""
    return hashlib.sha1(persona["persona"].encode("utf-8")).hexdigest()


def load_thread_meta():
    if THREAD_META.exists():
        return json.loads(THREAD_META.read_text(encoding="utf-8"))
    return {}


def save_thread_meta(meta):
    THREAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = THREAD_META.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(THREAD_META)


def append_thread_silence(rec):
    THREAD_DIR.mkdir(parents=True, exist_ok=True)
    with THREAD_SILENCES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def void_floor(read_id, reason, reads_by_id):
    """标记该楼层及其所有子孙楼层为 void——隐藏不删除，参考 curation.json 先例。
    格式对了但内容垮了（人格崩坏/跑题）事后才发现时用；机械引用校验不通过走的是
    「静默重roll」（此时还没有任何记录落盘），跟这里是两回事。"""
    children = {}
    for r in reads_by_id.values():
        if r.get("context_mode") == "thread" and r.get("thread_ref"):
            children.setdefault(r["thread_ref"], []).append(r["read_id"])
    meta = load_thread_meta()
    stack, touched, first = [read_id], [], True
    while stack:
        rid = stack.pop()
        entry = meta.setdefault(rid, {})
        entry["void"] = True
        entry["void_reason"] = reason if first else f"祖先 {read_id} void 级联"
        touched.append(rid)
        stack.extend(children.get(rid, []))
        first = False
    save_thread_meta(meta)
    return touched


def thread_root_id(read_id, reads_by_id):
    """一路回溯 thread_ref 到根——根是 context_mode=blind 的长评楼。"""
    seen, rid = set(), read_id
    while True:
        r = reads_by_id.get(rid)
        if r is None:
            sys.exit(f"thread_ref 断链：{rid} 不存在")
        if r.get("context_mode") != "thread" or not r.get("thread_ref"):
            return rid
        if rid in seen:
            sys.exit(f"thread_ref 成环：{rid}")
        seen.add(rid)
        rid = r["thread_ref"]


def floor_text(r):
    return r.get("long_form") or r.get("reaction") or ""


def ancestor_chain(parent_read_id, reads_by_id, token_budget=THREAD_TOKEN_BUDGET):
    """从 parent 一路回溯到根，返回 [root, ..., parent]（时间顺序）。
    可见范围 = 祖先链，不是全楼也不是随便开的滑动窗口（见 NOTES 2026-07-17 设计定稿）。
    深度不靠层数硬封顶（会杀死第 5 轮之后才会发生的说服弧线），改按祖先链原文的
    token 预算封顶；不足预算时从最老的中间楼层开始丢，根（锚住话题）和 parent
    （回复对象本身）永远保留。不做摘要压缩——具体预算数字待 2 首诗试点后校准。"""
    chain, seen, rid = [], set(), parent_read_id
    while True:
        r = reads_by_id.get(rid)
        if r is None:
            sys.exit(f"thread_ref 断链：{rid} 不存在")
        chain.append(r)
        if r.get("context_mode") != "thread" or not r.get("thread_ref"):
            break
        if rid in seen:
            sys.exit(f"thread_ref 成环：{rid}")
        seen.add(rid)
        rid = r["thread_ref"]
    chain.reverse()  # root ... parent
    root, rest = chain[0], chain[1:]
    if not rest:
        return [root]
    parent, older = rest[-1], rest[:-1]
    budget = token_budget - len(floor_text(root)) - len(floor_text(parent))
    kept_older = []
    for r in reversed(older):  # 从离 parent 最近的开始往回保留
        t = floor_text(r)
        if budget - len(t) < 0:
            break
        kept_older.append(r)
        budget -= len(t)
    kept_older.reverse()
    return [root] + kept_older + [parent]


def own_floor_history(root_id, persona_id, reads_by_id, exclude_read_id=None):
    """该人设在本帖已经发过的楼层（时间顺序）——外加祖先链，是读者的完整可见范围。
    防的是同一读者在不同分支里自我打脸（早期方案没考虑到的技术漏洞）。"""
    out = []
    for r in reads_by_id.values():
        if r.get("context_mode") != "thread" or r["read_id"] == exclude_read_id:
            continue
        if r["reader"]["persona_id"] != persona_id:
            continue
        if thread_root_id(r["thread_ref"], reads_by_id) != root_id:
            continue
        out.append(r)
    out.sort(key=lambda r: r.get("ts") or "")
    return out


THREAD_BASELINE = """你是「昼青集·读诗剧场」的一位读者，现在你在参加一场关于某首诗的跟帖讨论——不是盲读。这场讨论从一篇已有的长评开始，其他读者也在里面发言、互相回应。

读者底线不变：
1. 读懂并说出真实感受，技艺上的逆耳批评照说不误。
2. 情绪低沉 ≠ 写得差，把"传达得好不好"和"情绪暗不暗"分开。
3. 这场讨论只对这一首诗、这一串楼层，不牵涉其他诗。
4. 这里不打分——你的任务是真实地参与讨论，不是给出裁决。"""

SILENCE_BLOCK = """—— 要不要接话，你自己判断 ——
多数读者读完一层楼不会有话接——这是正常状态，不是失职。开口前先问自己一句：这层楼里有没有一个具体的、楼上还没人说过的点，你能一句话说清楚？
- 有：接下去按下面的格式回复。
- 没有：不要硬挤内容，直接沉默（见下面的 JSON 格式）。沉默和精彩的回复是同样被认可的输出，不是"允许但没人会选"的摆设——按你的真实判断来，不要因为怕被认为"不够投入"而硬凑一段。"""

POSITION_INERTIA_BLOCK = """—— 关于会不会被说服 ——
你有没有被这一层楼说服，只看你自己在意的东西有没有真的被击中——不预设"应该"被说服还是"应该"守住立场。
- 如果你的判断变了：说清楚对方哪一句具体的话，推翻了你原来站的哪一个具体理由。
- 如果你的判断没变：不要绕开对方说得最有力的那一点去挑软柿子——正面复述对方讲得最强的那句话，再说清楚它为什么撼动不了你。绕开最强的点，等于是被说服了却不肯承认。
不要为了"显得不容易被说服"而刻意唱反调——那和刻意迎合一样，都是表演。"""

QUOTE_BLOCK = """—— 回复格式（内部草稿，不进最终稿）——
先在心里/草稿里过三段，最后只把第三段交出去：
【接住的原句】逐字引用对方楼层里真正扛分量的一句话（不是开场寒暄）。
【我的转述】用你自己的话转述这句话，标准是"对方看了会觉得——对，这就是我的意思"。
【回应】你的真实回应，第一句必须直接接着上面的转述说下去——删掉转述后，如果这段回应还能独立成立、看不出在接谁的话，说明没有真的在回复，需要重写。
只有【回应】部分会被存档展示，前两段是给你自己核对用的脚手架，也会被机器拿去核对引用是否属实——不要在最终交出的 reaction 里保留"接住的原句/我的转述/回应"这些标签，让它读起来像你自然说的话。"""

THREAD_RESPONSE_FORMAT = """你的产出必须是一个 JSON 对象。

如果沉默：
{"silence": true, "reason": "一句话，指出具体是哪一点已经被说尽了或者与你无关——不是"没什么可说"这种空话"}

如果回复：
{
  "quote": "【接住的原句】的原文，逐字，用于核对",
  "restate": "【我的转述】",
  "reaction": "【回应】——只有这部分会被存档展示，不要带任何标签，两三句到几句话，像跟帖不像论文",
  "long_form": null,
  "stance_changed": true 或 false,
  "stance_note": "一句话交代你的立场机制（见上）",
  "vote": "up 或 down —— 你认不认同你正在回复的这层楼本身说的（不是认不认同发帖人这个人）"
}"""


def build_thread_prompt(poem, persona, ancestor_floors, own_history, parent_floor):
    """ancestor_floors 是 ancestor_chain() 的返回值（[root, ..., parent]，已含 parent）。
    own_history 是 own_floor_history() 的返回值。"""
    parts = [THREAD_BASELINE, "", "—— 你是谁 ——", persona["persona"]]
    if persona.get("thread_priors"):
        # 立场惯性的真实来源：只在跟帖 prompt 里拼，盲读的 build_prompt 不读这个字段，
        # 避免"什么论证说服不了你"这类跟帖专属的价值倾向渗进盲读打分（2026-07-17 用户指出）。
        parts += ["", "—— 你在讨论时的脾气 ——", persona["thread_priors"]]
    parts += ["", "—— 这首诗（讨论背景）——", f"《{poem['title']}》"]
    root = ancestor_floors[0]
    parts += ["", "—— 楼主长评（开楼帖）——", floor_text(root)]
    if len(ancestor_floors) > 1:
        parts += ["", "—— 沿途楼层（从楼主往下，一路到你要回复的这层）——"]
        for r in ancestor_floors[1:]:
            parts += [f"[{r['read_id']} · {r['reader']['persona_id']}]", floor_text(r), ""]
    if own_history:
        parts += ["", "—— 你自己在这一串讨论里已经发过的话（防止你在别的分支里说法前后矛盾）——"]
        for r in own_history:
            parts += [f"[{r['read_id']}]", floor_text(r), ""]
    parts += ["", f"—— 现在，请回复这一层 [{parent_floor['read_id']} · "
                  f"{parent_floor['reader']['persona_id']}] ——", floor_text(parent_floor)]
    parts += ["", SILENCE_BLOCK, "", POSITION_INERTIA_BLOCK, "", QUOTE_BLOCK,
              "", THREAD_RESPONSE_FORMAT]
    return "\n".join(parts)


def load_comment_votes():
    if not VOTES.exists():
        return []
    out = []
    for line in VOTES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def next_vote_id(existing):
    mx = 0
    for v in existing:
        vid = v.get("vote_id", "")
        if vid.startswith("v-"):
            try:
                mx = max(mx, int(vid[2:]))
            except ValueError:
                pass
    return mx


def append_comment_votes(new_votes):
    """append-only，同 reads.jsonl 的精神；vote_id/ts 落盘时补。"""
    existing = load_comment_votes()
    counter = next_vote_id(existing)
    lines = []
    for v in new_votes:
        counter += 1
        rec = dict(v)
        rec["vote_id"] = f"v-{counter:06d}"
        rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        lines.append(json.dumps(rec, ensure_ascii=False))
    if lines:
        VOTES_DIR.mkdir(parents=True, exist_ok=True)
        with VOTES.open("a", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
    return len(lines)


def load_vote_void():
    if VOTES_VOID.exists():
        return json.loads(VOTES_VOID.read_text(encoding="utf-8"))
    return {}


def void_votes(vote_ids, reason):
    """标记若干张票作废——票据文件 append-only 永不删行，作废走侧车标记。
    用途：事后发现某批票是单上下文代写等不可信产出时，把它们从一切统计里摘出去。"""
    existing = {v.get("vote_id") for v in load_comment_votes()}
    void = load_vote_void()
    touched = []
    for vid in vote_ids:
        if vid not in existing:
            print(f"跳过：{vid} 不存在", file=sys.stderr)
            continue
        void[vid] = {"reason": reason, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        touched.append(vid)
    VOTES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = VOTES_VOID.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(void, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(VOTES_VOID)
    return touched


def valid_comment_votes():
    """votes.jsonl 里去掉已作废票的视图——一切统计/展示都应从这里拿。"""
    void = load_vote_void()
    return [v for v in load_comment_votes() if v.get("vote_id") not in void]


def vote_tally(read_id, votes=None):
    votes = votes if votes is not None else valid_comment_votes()
    tally = {"up": 0, "down": 0, "skip": 0}
    for v in votes:
        if v.get("target_read_id") == read_id and v.get("vote") in tally:
            tally[v["vote"]] += 1
    return tally


def vote_tally_split(read_id, votes=None):
    """把一条评论的票拆成「主动票」（点赞模式发起）和「顺势票」（跟帖带上来的）。
    作者据票决定撤不撤评时只该看主动票——顺势票几乎恒为 up，是弱信号（见 plan_thread）。"""
    votes = votes if votes is not None else valid_comment_votes()
    direct = {"up": 0, "down": 0, "skip": 0}
    piggy = {"up": 0, "down": 0, "skip": 0}
    for v in votes:
        if v.get("target_read_id") != read_id or v.get("vote") not in direct:
            continue
        bucket = piggy if v.get("source") == "piggyback" else direct
        bucket[v["vote"]] += 1
    return {"direct": direct, "piggyback": piggy}


VOTE_BASELINE = """你是「昼青集·读诗剧场」的一位读者。这次不是去读一首新诗写反应，而是判断别人写的评论——它的论证，有没有真的撑起它给这首诗的分。

一个贯穿始终的试金石：**把这条评论原样搬到另一首诗下面，还成不成立？** 越是"换首诗照样能说"的话，越空；越是"只有对着这首诗才说得出"的话，越实。

判断分两步走，别一上来就三选一：

第一步 · 有没有硬伤？——分数与理由脱节、说的对不上诗本身、通篇套话。有，就是 **down**，在 reason 里点名是哪一句露了馅。没有，进第二步。

第二步 · 没硬伤的评论，默认落 **skip**；只有当它挣到 up 时才往上抬。挣到 up 的唯一凭据：评论里有一句「只有对着这首诗才写得出」、你愿意逐字引用来替这个分辩护的话——把那句原话抄进 reason。抄不出、或者那句话换首诗也照样成立，就老实留在 skip。

所以 skip 不是弃权，是最诚实的静息档：绝大多数四平八稳、说得没错但换首诗照样能讲的评论，本就该落这里。真正咬住这一首的 up 是少数，一眼看穿的硬伤 down 也是少数——一整批里 up/down 加起来还多过 skip，多半是你没沉住气。不要因为"总得表个态"就把该 skip 的抬成 up，也不要因为"读着不顺眼"就把没硬伤的压成 down。

你自己的口味不是标准：「我不会给这个分，但他的理由具体且对得上这首诗」不构成 down；「我同意这个分，但理由换首诗照样成立」不构成 up。只看这一条评论和这一首诗，不牵涉别的。"""

VOTE_RESPONSE_FORMAT = """你的产出必须是一个 JSON 对象：
{
  "vote": "up 或 down 或 skip",
  "reason": "up：逐字抄下你当作依据的那句原话（抄不出=不是 up）；down：点名换首诗照样成立、或对不上诗的那句；skip：可留空"
}"""


def _score_label(score):
    if score is None:
        return ""
    return f"，打了 {score:.1f} 分"


def build_vote_prompt(poem, persona, target_read, stanzas=None):
    parts = [VOTE_BASELINE, "", "—— 你是谁 ——", persona["persona"]]
    parts += ["", "—— 这首诗 ——", f"《{poem['title']}》", "", poem_text(poem, stanzas), ""]
    is_long = bool((target_read.get("long_form") or "").strip())
    label = "长评" if is_long else "短评"
    body = target_read.get("long_form") if is_long else target_read.get("reaction")
    score_str = _score_label(target_read.get("score"))
    parts += [f"—— 这条{label}（{target_read['reader']['persona_id']} 写的{score_str}）——", body or ""]
    parts += ["", VOTE_RESPONSE_FORMAT]
    return "\n".join(parts)


BATCH_VOTE_RESPONSE_FORMAT = """你的产出必须是一个 JSON 对象，votes 数组长度必须等于上方评论数，顺序一一对应：
{
  "votes": [
    {"read_id": "r-xxxxxx", "vote": "up",   "reason": "抄下你愿意背书的那句原话"},
    {"read_id": "r-yyyyyy", "vote": "down",  "reason": "点名露馅的那句"},
    {"read_id": "r-zzzzzz", "vote": "skip",  "reason": ""}
  ]
}
两步漏斗见上（默认落 skip，up/down 都要挣）：先看硬伤——分数与理由脱节/对不上诗/套话才降 down；没硬伤先落 skip；只有抄得出「只对这首诗成立」的原句，才把它抬成 up。
逐条独立判断，不要给整批一个统一态度——这一批里 skip 通常该是多数，出现清一色 up 或 up/down 压过 skip 几乎一定是没沉住气；read_id 必须原样照抄，不能省略或改写。"""


def build_batch_vote_prompt(poem, persona, target_reads, stanzas=None):
    """批量投票：一次读 N 条评论，逐一判断——减少任务数，同时能做横向比较。"""
    parts = [VOTE_BASELINE, "", "—— 你是谁 ——", persona["persona"]]
    parts += ["", "—— 这首诗 ——", f"《{poem['title']}》", "", poem_text(poem, stanzas), ""]
    parts += [f"—— 需要你判断的 {len(target_reads)} 条评论（逐一给出判断）——"]
    for i, tr in enumerate(target_reads, 1):
        is_long = bool((tr.get("long_form") or "").strip())
        label = "长评" if is_long else "短评"
        body = (tr.get("long_form") if is_long else tr.get("reaction")) or ""
        score_str = _score_label(tr.get("score"))
        parts += [f"\n【{i}】{label}（read_id: {tr['read_id']}{score_str}）", body]
    parts += ["", BATCH_VOTE_RESPONSE_FORMAT]
    return "\n".join(parts)


def cmd_coverage(args):
    poems = pool()
    reads = [r for r in load_reads() if r.get("context_mode") == "blind"]
    per_poem = Counter(r["poem_id"] for r in reads)
    per_pair = Counter((r["poem_id"], r["reader"]["persona_id"]) for r in reads)
    total = sum(per_poem.values())
    zero = [p["id"] for p in poems if per_poem[p["id"]] == 0]
    print(f"读者池 {len(poems)} 首；盲读记录共 {total} 条；"
          f"未被读过的 {len(zero)} 首")
    thin = sorted(poems, key=lambda p: (per_poem[p["id"]], p["id"]))[:15]
    print("覆盖最薄的 15 首：")
    for p in thin:
        print(f"  {p['id']} 《{p['title'][:20]}》 已读 {per_poem[p['id']]} 次")
    if args.full:
        for p in sorted(poems, key=lambda p: p["id"]):
            print(p["id"], per_poem[p["id"]],
                  json.dumps({k: v for (pid, k), v in per_pair.items()
                              if pid == p["id"]}, ensure_ascii=False))


def cmd_plan(args):
    poems = pool()
    personas = [p for p in load_personas() if not p.get("superseded_by")]
    stanzas = load_stanzas()
    reads = [r for r in load_reads() if r.get("context_mode") == "blind"]
    per_poem = Counter(r["poem_id"] for r in reads)
    per_pair = Counter((r["poem_id"], r["reader"]["persona_id"]) for r in reads)

    if args.poem_ids:
        want = set(args.poem_ids.split(","))
        chosen_poems = [p for p in poems if p["id"] in want]
    else:
        chosen_poems = sorted(
            poems, key=lambda p: (per_poem[p["id"]], random.random()))[:args.poems]

    with_note = getattr(args, "with_note", False)
    if with_note:
        skipped = [p["id"] for p in chosen_poems if not (p.get("note") or "").strip()]
        if skipped:
            print(f"跳过 {len(skipped)} 首无自注的诗（批注本任务要求有 note）："
                  f"{', '.join(skipped)}", file=sys.stderr)
        chosen_poems = [p for p in chosen_poems if (p.get("note") or "").strip()]

    genre_notes = load_settings_file().get("genre_notes") or {}
    tasks = []
    for poem in chosen_poems:
        ps = sorted(personas,
                    key=lambda x: (per_pair[(poem["id"], x["persona_id"])],
                                   random.random()))[:args.readers]
        for persona in ps:
            t = {
                "poem_id": poem["id"],
                "title": poem["title"],
                "persona_id": persona["persona_id"],
                "reader": {
                    "persona_id": persona["persona_id"],
                    "model": None,
                    "knows_诠释": persona["knows_诠释"],
                    "knows_date": persona["knows_date"],
                },
                "content_hash": poem["content_hash"],
                "prompt": build_prompt(poem, persona, stanzas, with_note=with_note,
                                       genre_notes=genre_notes),
            }
            tasks.append(t)

    BATCHES.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else \
        BATCHES / f"batch-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(tasks, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    kind = "批注本" if with_note else "盲读"
    print(f"{len(tasks)} 个{kind}任务（{len(chosen_poems)} 首 × ≤{args.readers} 读者）→ {out}")
    nonp = Counter(p.get("genre") or "未分类" for p in chosen_poems
                   if (p.get("genre") or "") not in POETRY_GENRES)
    if nonp:
        print("本批含非诗文体：" + "，".join(f"{g} ×{n}" for g, n in sorted(nonp.items()))
              + "（prompt 已带体裁转换段）")
    if with_note:
        print("注意：批注本批次 collect 时必须带 --context-mode annotated，否则会混进盲读统计")


def cmd_collect(args):
    """信箱流程：subagent 把各自的读稿结果写到 inbox/task-NN.response.json，
    这里与任务元数据合并成冻结 schema 记录并落盘——中间没有任何人手/大模型转录。
    response 文件格式：{"model": "claude-...", "score": 7.0, "reaction": "...", "long_form": null}
    """
    tdir = Path(args.tasks)
    inbox = Path(args.inbox)
    merged, missing, processed = [], [], []
    for tf in sorted(tdir.glob("task-*.json")):
        rf = inbox / (tf.stem + ".response.json")
        if not rf.exists():
            missing.append(tf.stem); continue
        processed.append(rf)
        t = json.loads(tf.read_text(encoding="utf-8"))
        r = json.loads(rf.read_text(encoding="utf-8"))
        reader = dict(t["reader"])
        reader["model"] = r.get("model") or args.model
        merged.append({
            "poem_id": t["poem_id"],
            "reader": reader,
            "context_mode": getattr(args, "context_mode", "blind") or "blind",
            "thread_ref": None,
            "transport": args.transport,
            "score": r.get("score"),
            "reaction": r.get("reaction"),
            "long_form": r.get("long_form"),
            "content_hash": t["content_hash"],
        })
    if missing:
        print(f"缺 {len(missing)} 份回执：{', '.join(missing)}", file=sys.stderr)
    if merged:
        # 展示本批回执的 model 分布，供派发方核对出处：应是真实底层模型 ID，
        # 不是派发工具/平台名（如 codebuddy）。reads.jsonl 只追加不删，入库前是最后一道人眼关。
        dist = {}
        for m in merged:
            k = m["reader"]["model"]
            dist[k] = dist.get(k, 0) + 1
        print("本批回执 model 分布：" + "，".join(
            f"{k} ×{v}" for k, v in sorted(dist.items())))
    tmp = inbox / "_merged.json"
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    class _A:  # 复用 ingest 的校验与落盘
        file = str(tmp)
    cmd_ingest(_A)

    # 幂等：已入库的回执移入 inbox/ingested/，重复 collect 不会二次落盘
    done = inbox / "ingested"
    done.mkdir(exist_ok=True)
    for rf in processed:
        rf.rename(done / rf.name)


def next_read_id(existing):
    mx = 0
    for r in existing:
        rid = r.get("read_id", "")
        if rid.startswith("r-"):
            try:
                mx = max(mx, int(rid[2:]))
            except ValueError:
                pass
    return mx


def cmd_ingest(args):
    """输入：一个 JSON 数组文件，每项含冻结 schema 的必填字段（read_id/ts 可缺，落盘时补）。
    thread 模式（context_mode=="thread"）不评分：score 不在必填之列、落盘为 null；
    改为必须给出真实存在的 thread_ref（跟帖不评分，天然不进 calibrate.py 的统计）。"""
    incoming = load_json(args.file)
    if isinstance(incoming, dict):
        incoming = [incoming]
    existing = load_reads()
    counter = next_read_id(existing)
    valid_poems = {p["id"]: p for p in load_json(CORPUS)}
    existing_ids = {r["read_id"] for r in existing}

    lines, errors = [], []
    for i, r in enumerate(incoming):
        is_thread = r.get("context_mode") == "thread"
        required = [f for f in REQUIRED_FIELDS if not (is_thread and f == "score")]
        missing = [f for f in required if f not in r or r[f] is None]
        if missing:
            errors.append(f"#{i} 缺字段 {missing}"); continue
        if r["poem_id"] not in valid_poems:
            errors.append(f"#{i} poem_id 不存在: {r['poem_id']}"); continue
        if is_thread:
            tref = r.get("thread_ref")
            if not tref:
                errors.append(f"#{i} thread 模式必须有 thread_ref"); continue
            if tref not in existing_ids:
                errors.append(f"#{i} thread_ref 指向的楼层不存在: {tref}"); continue
        elif not isinstance(r["score"], (int, float)) or not 0 <= r["score"] <= 10:
            errors.append(f"#{i} score 非法: {r['score']!r}"); continue
        if not r["reader"].get("model"):
            errors.append(f"#{i} reader.model 缺失（出处是命根子）"); continue
        counter += 1
        rec = {
            "read_id": f"r-{counter:06d}",
            "poem_id": r["poem_id"],
            "reader": {
                "persona_id": r["reader"]["persona_id"],
                "model": r["reader"]["model"],
                "knows_诠释": r["reader"].get("knows_诠释", False),
                "knows_date": r["reader"].get("knows_date", False),
            },
            "context_mode": r["context_mode"],
            "thread_ref": r.get("thread_ref"),
            "transport": r["transport"],
            "score": None if is_thread else round(float(r["score"]), 1),
            "reaction": r["reaction"],
            "long_form": r.get("long_form"),
            "ts": r.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "content_hash": r["content_hash"],
        }
        lines.append(json.dumps(rec, ensure_ascii=False))
        existing_ids.add(rec["read_id"])  # 同批次可续链（root+多层一起 ingest 时）

    if lines:
        READS.parent.mkdir(parents=True, exist_ok=True)
        with READS.open("a", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
    print(f"落盘 {len(lines)} 条 → {READS}")
    if errors:
        print(f"拒收 {len(errors)} 条：", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("coverage"); c.add_argument("--full", action="store_true")
    p = sub.add_parser("plan")
    p.add_argument("--poems", type=int, default=6)
    p.add_argument("--readers", type=int, default=4)
    p.add_argument("--poem-ids", dest="poem_ids", default="")
    p.add_argument("--out", default="")
    p.add_argument("--with-note", dest="with_note", action="store_true",
                   help="批注本场：prompt 附作者眉批（note），collect 须配 --context-mode annotated")
    g = sub.add_parser("ingest"); g.add_argument("--file", required=True)
    k = sub.add_parser("collect")
    k.add_argument("--tasks", required=True)
    k.add_argument("--inbox", required=True)
    k.add_argument("--model", default="")
    k.add_argument("--transport", default="cc-subagent")
    k.add_argument("--context-mode", dest="context_mode", default="blind",
                   choices=["blind", "annotated"])
    args = ap.parse_args()
    {"coverage": cmd_coverage, "plan": cmd_plan, "ingest": cmd_ingest,
     "collect": cmd_collect}[args.cmd](args)


if __name__ == "__main__":
    main()
