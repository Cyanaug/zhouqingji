# -*- coding: utf-8 -*-
"""跟帖模式（thread）派发：给一首已有长评的诗办一场读者跟帖讨论。

用法：
  python plan_thread.py invite --parent R [--fraction 0.5] [--exclude a,b] [--seed 0] [--out DIR]
                                            # 派发方指定接楼（v0：不做读者自选），
                                            # 生成一批「回复这一层楼」的任务
  python plan_thread.py collect --tasks DIR/tasks --inbox DIR/inbox --model M
                                            # 引用校验 + 沉默分流 + 落盘 + 侧车元数据
  python plan_thread.py nextround [--root R] [--top 3]
                                            # 从已有楼层挑最值得往下接的几层，打印候选与
                                            # 现成 invite 命令（只提案不派发，深度档建议
                                            # fraction 0.25 + 更强通道，不预设具体模型）
  python plan_thread.py void --read-id R --reason "..."
                                            # 事后标记楼层（及其子孙楼层）为 void，不删除

设计依据：theater/NOTES.md「2026-07-17 · 跟帖模式（thread）设计定稿」。
- 只对已有长评（长评作者=楼主）开楼，v0 由派发方（人）指定接楼目标，不做读者自选。
- 可见范围 = 祖先链 ∪ 自己在本帖已发过的楼层（runner.ancestor_chain / own_floor_history）。
- 楼主默认享有优先回护权利：invite 时楼主人设永远在邀请名单里，不受随机抽样影响。
- 沉默是完整产出：response 里 silence=true 时不落 reads.jsonl，落 THREAD_SILENCES。
- 引用校验：response["quote"] 必须能在 parent 楼层原文里逐字找到，找不到 = 静默重roll
  （此时还没有任何记录落盘，不涉及 append-only/void，跟事后 void 是两回事）。
"""
import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner as R

BATCHES = R.BATCHES


def _reads_by_id():
    return {r["read_id"]: r for r in R.load_reads()}


def cmd_invite(args):
    reads = _reads_by_id()
    parent = reads.get(args.parent)
    if parent is None:
        sys.exit(f"找不到 read_id：{args.parent}")

    hidden = R.hidden_read_ids()
    if args.parent in hidden:
        sys.exit(f"目标楼层 {args.parent} 已被折叠（hidden），不开跟帖。")

    root_id = R.thread_root_id(args.parent, reads)
    root = reads[root_id]
    if root_id != args.parent and root_id in hidden:
        sys.exit(f"根楼 {root_id} 已被折叠（hidden），不开跟帖。")
    if not (root.get("long_form") or "").strip():
        sys.exit(f"根楼 {root_id} 没有长评（long_form 为空），不能作为开楼帖——"
                  f"v0 规定开楼必须绑定已有长评，不是随便一条盲读短评。")

    poems = {p["id"]: p for p in R.load_json(R.CORPUS)}
    poem = poems.get(root["poem_id"])
    if poem is None:
        sys.exit(f"诗 {root['poem_id']} 不存在")

    personas = {p["persona_id"]: p for p in R.load_personas()
                if not p.get("superseded_by")}
    op_id = root["reader"]["persona_id"]
    parent_author = parent["reader"]["persona_id"]
    exclude = {x for x in args.exclude.split(",") if x}

    # 已经回过这层楼的人不重复邀请——重复回帖会重复带顺势票（2026-07-18 修）。
    # 想让某人再说一轮，显式 --allow-repeat。
    replied = set() if args.allow_repeat else {
        r["reader"]["persona_id"] for r in reads.values()
        if r.get("context_mode") == "thread" and r.get("thread_ref") == args.parent}

    # 优先回应权：楼主（回护自己的开楼观点）+ 被 parent 回击的那层楼的作者
    # （别人接了你的楼，你有权应答）。parent 自己的作者不邀——不回自己的楼。
    grand = reads.get(parent.get("thread_ref")) \
        if parent.get("context_mode") == "thread" else None
    grand_author = grand["reader"]["persona_id"] if grand else None
    priority = [pid for pid in dict.fromkeys([op_id, grand_author])
                if pid and pid != parent_author and pid in personas
                and pid not in exclude and pid not in replied]

    candidates = [pid for pid in personas
                  if pid != parent_author and pid not in priority
                  and pid not in exclude and pid not in replied]

    seed = args.seed if args.seed else int(time.time())
    random.seed(seed)
    random.shuffle(candidates)  # 派发顺序随机化——见 03「并列时随机打散」
    n = max(0, round(len(candidates) * args.fraction))
    invited = priority + candidates[:n]

    ancestors = R.ancestor_chain(args.parent, reads)
    parent_text = R.floor_text(parent)
    stanzas = R.load_stanzas()

    tasks = []
    for pid in invited:
        persona = personas[pid]
        history = R.own_floor_history(root_id, pid, reads)
        prompt = R.build_thread_prompt(poem, persona, ancestors, history, parent,
                                       stanzas)
        tasks.append({
            "poem_id": root["poem_id"],
            "title": poem["title"],
            "persona_id": pid,
            "reader": {
                "persona_id": pid,
                "model": None,
                "knows_诠释": persona["knows_诠释"],
                "knows_date": persona["knows_date"],
            },
            "content_hash": poem["content_hash"],
            "root_id": root_id,
            "parent_read_id": args.parent,
            "parent_text": parent_text,
            "persona_hash": R.persona_sha1(persona),
            "depth": len(ancestors),
            "prompt": prompt,
        })

    out = Path(args.out) if args.out else \
        BATCHES / f"thread-{root_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    (out / "tasks").mkdir(parents=True, exist_ok=True)
    (out / "inbox").mkdir(parents=True, exist_ok=True)
    json.dump(tasks, open(out / "batch.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    for i, t in enumerate(tasks):
        n2 = f"{i + 1:03d}"
        json.dump(t, open(out / f"tasks/task-{n2}.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        open(out / f"tasks/task-{n2}.prompt.txt", "w", encoding="utf-8").write(t["prompt"])

    depth = len(ancestors)
    print(f"{len(tasks)} 个跟帖任务（邀请 {len(invited)}/{len(personas)} 位读者，"
          f"优先回应权：{'、'.join(priority) if priority else '无'}）→ {out}  (seed={seed})")
    print(f"接楼目标：{args.parent}（祖先链 {depth} 层，派发方指定，v0 不做读者自选）")
    if replied:
        print(f"已回过这层楼、本轮不重复邀请：{'、'.join(sorted(replied))}"
              f"（要重邀用 --allow-repeat）")
    # 分档只谈"贵贱"，不点名模型——各人手头最便宜的通道不一样（用户 2026-07-18 定）。
    if depth <= 1:
        print("档位：广度档（一楼铺场）——用你手头最便宜的通道跑就行。")
    else:
        print(f"档位：深度档（第 {depth} 级）——楼层少、上下文长、要接得住论点，"
              f"建议用你手头更强的通道，别心疼这几个任务。")


def cmd_collect(args):
    tdir, inbox = Path(args.tasks), Path(args.inbox)
    rejected = inbox / "rejected"
    done = inbox / "ingested"
    ingested_lines, silences, rejects, missing = [], [], [], []

    for tf in sorted(tdir.glob("task-*.json")):
        rf = inbox / (tf.stem + ".response.json")
        if not rf.exists():
            missing.append(tf.stem); continue
        t = json.loads(tf.read_text(encoding="utf-8"))
        resp = json.loads(rf.read_text(encoding="utf-8"))

        if resp.get("silence"):
            silences.append({
                "parent": t["parent_read_id"],
                "root_id": t["root_id"],
                "persona_id": t["persona_id"],
                "persona_hash": t["persona_hash"],
                "reason": str(resp.get("reason", "")),
                "model": resp.get("model") or args.model,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
            done.mkdir(exist_ok=True)
            rf.rename(done / rf.name)
            continue

        quote = (resp.get("quote") or "").strip()
        parent_norm = " ".join(t["parent_text"].split())
        quote_norm = " ".join(quote.split())
        if not quote or quote_norm not in parent_norm:
            rejected.mkdir(parents=True, exist_ok=True)
            rf.rename(rejected / rf.name)
            rejects.append(tf.stem)
            continue

        reaction = (resp.get("reaction") or "").strip()
        if not reaction:
            rejected.mkdir(parents=True, exist_ok=True)
            rf.rename(rejected / rf.name)
            rejects.append(tf.stem)
            continue

        ingested_lines.append({
            "poem_id": t["poem_id"],
            "reader": {
                "persona_id": t["persona_id"],
                "model": resp.get("model") or args.model,
                "knows_诠释": t["reader"]["knows_诠释"],
                "knows_date": t["reader"]["knows_date"],
            },
            "context_mode": "thread",
            "thread_ref": t["parent_read_id"],
            "transport": args.transport,
            "reaction": reaction,
            "long_form": resp.get("long_form"),
            "content_hash": t["content_hash"],
            "_meta": {
                "persona_hash": t["persona_hash"],
                "depth": t["depth"],
                "stance_changed": resp.get("stance_changed"),
                "parent_read_id": t["parent_read_id"],
                "vote": resp.get("vote"),
            },
        })
        done.mkdir(exist_ok=True)
        rf.rename(done / rf.name)

    if missing:
        print(f"缺 {len(missing)} 份回执：{', '.join(missing)}", file=sys.stderr)
    if rejects:
        print(f"引用校验未通过，静默重roll {len(rejects)} 条（已移入 "
              f"inbox/rejected/，原 task 未消耗，可重新派发同一任务）："
              f"{', '.join(rejects)}", file=sys.stderr)

    for s in silences:
        R.append_thread_silence(s)
    if silences:
        print(f"{len(silences)} 条沉默事件 → {R.THREAD_SILENCES}")

    if ingested_lines:
        metas = [rec.pop("_meta") for rec in ingested_lines]
        tmp = inbox / "_merged.json"
        tmp.write_text(json.dumps(ingested_lines, ensure_ascii=False, indent=1),
                       encoding="utf-8")

        class _A:
            file = str(tmp)
        # cmd_ingest 现在 all-or-nothing 并返回新记录（顺序=输入顺序），
        # 不再用"前后差集"对齐侧车——差集法在 ingest 半途退出时会留孤儿楼层。
        new_reads = R.cmd_ingest(_A)
        meta_store = R.load_thread_meta()
        piggyback_votes = []
        for rec, m in zip(new_reads, metas):
            meta_store[rec["read_id"]] = {
                "persona_hash": m["persona_hash"],
                "depth": m["depth"],
                "stance_changed": m["stance_changed"],
                "parent_vote": m.get("vote"),   # up/down/null：对 parent 楼层的顺势投票方向
                "void": False,
                "void_reason": None,
            }
            # 顺势点赞：回复者已经引用+转述过被回复的楼层，判断早就形成了，
            # 让这层楼额外收到一票 up/down（2026-07-17 用户提议）——不额外派发。
            if m.get("vote") in ("up", "down"):
                piggyback_votes.append({
                    "poem_id": rec["poem_id"],
                    "target_read_id": m["parent_read_id"],
                    "voter": {"persona_id": rec["reader"]["persona_id"],
                              "model": rec["reader"]["model"]},
                    "vote": m["vote"],
                    "reason": "",
                    # 顺势票 vs 点赞模式主动票要分得开：回复者几乎总认同自己选来回复的楼层
                    # （实测 754up/22down≈97% up），信号弱。作者据票撤评时只该看主动票，
                    # 展示层可合并显示，但去重/裁撤统计要能把这批摘出去。
                    "source": "piggyback",
                })
        R.save_thread_meta(meta_store)
        if piggyback_votes:
            n = R.append_comment_votes(piggyback_votes)
            print(f"{n} 条跟帖顺势投票 → {R.VOTES}")
        print(f"{len(new_reads)} 条跟帖落盘 reads.jsonl，侧车元数据写入 {R.THREAD_META}")


def cmd_nextround(args):
    """从已有楼层里挑最值得往下接的几层，打印候选清单和现成的 invite 命令。
    只提案、不派发：深楼一直跑不起来，不是读者不想聊，是每轮都要人工翻楼挑目标
    （2026-07-18 机制审查：892 层里 886 层是一楼）。这里把挑楼的功夫自动化，
    批准与派发照旧走人，额度红线不变。"""
    reads = _reads_by_id()
    hidden = R.hidden_read_ids()
    meta = R.load_thread_meta()

    down_recv = Counter()
    for v in R.valid_comment_votes():
        if v.get("source") == "piggyback" and v.get("vote") == "down":
            down_recv[v["target_read_id"]] += 1

    children = Counter()
    for r in reads.values():
        if r.get("context_mode") == "thread" and r.get("thread_ref"):
            children[r["thread_ref"]] += 1

    cands = []
    for r in reads.values():
        if r.get("context_mode") != "thread":
            continue
        rid = r["read_id"]
        m = meta.get(rid, {})
        if m.get("void") or rid in hidden:
            continue
        root_id = R.thread_root_id(rid, reads)
        if args.root and root_id != args.root:
            continue
        op_id = reads[root_id]["reader"]["persona_id"]
        score, why = 0.0, []
        if down_recv[rid]:
            score += 3 * down_recv[rid]
            why.append(f"吃了 {down_recv[rid]} 张顺势▼——有争议，值得围观")
        if m.get("stance_changed"):
            score += 2
            why.append("作者自陈立场被撼动——说服弧线的活口")
        if r["reader"]["persona_id"] == op_id:
            score += 2
            why.append("楼主亲自下场的回应")
        if not children[rid]:
            score += 1
            why.append("还没人接")
        score += min(len(r.get("reaction") or "") / 200, 1.5)
        cands.append((score, rid, root_id, m.get("depth") or 1, r, why))

    cands.sort(key=lambda x: (-x[0], x[1]))
    top = cands[:args.top]
    if not top:
        print("没有可接的候选楼层")
        return
    print(f"最值得往下接的 {len(top)} 层（共 {len(cands)} 层候选）：")
    for score, rid, root_id, depth, r, why in top:
        print(f"\n[{score:.1f}] {rid}（{r['reader']['persona_id']}，root {root_id}，"
              f"接上去是第 {depth + 1} 级）")
        for w in why:
            print(f"    · {w}")
        print(f"    「{(r.get('reaction') or '')[:60]}」")
        print(f"    python plan_thread.py invite --parent {rid} --fraction 0.25 "
              f"--out batches/thread-{root_id}-f{rid[2:]}")
    print("\n深度档建议：fraction 低一点（0.25 上下）、用你手头更强的通道——"
          "楼层少而精，优先回应权（楼主+被回击者）已由 invite 自动保障。")


def cmd_void(args):
    reads = _reads_by_id()
    if args.read_id not in reads:
        sys.exit(f"找不到 read_id：{args.read_id}")
    touched = R.void_floor(args.read_id, args.reason, reads)
    print(f"标记 void：{', '.join(touched)}（隐藏不删除，参考 curation.json 先例）")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("invite")
    i.add_argument("--parent", required=True, help="要接的楼层 read_id（派发方指定，v0 不做读者自选）")
    i.add_argument("--fraction", type=float, default=0.5,
                   help="随机邀请比例（楼主永远额外在列，不受此影响）")
    i.add_argument("--exclude", default="", help="逗号分隔 persona_id，排除在邀请之外")
    i.add_argument("--allow-repeat", dest="allow_repeat", action="store_true",
                   help="允许重邀已回过这层楼的人（默认去重，防顺势票重复计）")
    i.add_argument("--seed", type=int, default=0, help="0=用当前时间，其他值可复现")
    i.add_argument("--out", default="")

    c = sub.add_parser("collect")
    c.add_argument("--tasks", required=True)
    c.add_argument("--inbox", required=True)
    c.add_argument("--model", default="")
    c.add_argument("--transport", default="cc-subagent")

    nr = sub.add_parser("nextround")
    nr.add_argument("--root", default="", help="只在这个根楼的讨论里挑；省略=全部")
    nr.add_argument("--top", type=int, default=3, help="给出前 N 个候选（默认 3）")

    v = sub.add_parser("void")
    v.add_argument("--read-id", dest="read_id", required=True)
    v.add_argument("--reason", required=True)

    args = ap.parse_args()
    {"invite": cmd_invite, "collect": cmd_collect, "nextround": cmd_nextround,
     "void": cmd_void}[args.cmd](args)


if __name__ == "__main__":
    main()
