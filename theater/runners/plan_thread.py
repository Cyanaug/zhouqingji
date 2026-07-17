# -*- coding: utf-8 -*-
"""跟帖模式（thread）派发：给一首已有长评的诗办一场读者跟帖讨论。

用法：
  python plan_thread.py invite --parent R [--fraction 0.5] [--exclude a,b] [--seed 0] [--out DIR]
                                            # 派发方指定接楼（v0：不做读者自选），
                                            # 生成一批「回复这一层楼」的任务
  python plan_thread.py collect --tasks DIR/tasks --inbox DIR/inbox --model M
                                            # 引用校验 + 沉默分流 + 落盘 + 侧车元数据
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

    root_id = R.thread_root_id(args.parent, reads)
    root = reads[root_id]
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
    exclude = {x for x in args.exclude.split(",") if x}
    # 楼主默认享有优先回护权利：永远在邀请名单里，不受随机抽样/exclude 影响
    candidates = [pid for pid in personas if pid != op_id and pid not in exclude]

    seed = args.seed if args.seed else int(time.time())
    random.seed(seed)
    random.shuffle(candidates)  # 派发顺序随机化——见 03「并列时随机打散」
    n = max(0, round(len(candidates) * args.fraction))
    invited = ([op_id] if op_id not in exclude else []) + candidates[:n]

    ancestors = R.ancestor_chain(args.parent, reads)
    parent_text = R.floor_text(parent)

    tasks = []
    for pid in invited:
        persona = personas[pid]
        history = R.own_floor_history(root_id, pid, reads)
        prompt = R.build_thread_prompt(poem, persona, ancestors, history, parent)
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

    print(f"{len(tasks)} 个跟帖任务（邀请 {len(invited)}/{len(personas)} 位读者，"
          f"楼主 {op_id} 优先在列）→ {out}  (seed={seed})")
    print(f"接楼目标：{args.parent}（祖先链 {len(ancestors)} 层，"
          f"派发方指定，v0 不做读者自选）")


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

        existing_before = {r["read_id"] for r in R.load_reads()}

        class _A:
            file = str(tmp)
        R.cmd_ingest(_A)

        new_reads = [r for r in R.load_reads() if r["read_id"] not in existing_before]
        meta_store = R.load_thread_meta()
        piggyback_votes = []
        for rec, m in zip(new_reads, metas):
            meta_store[rec["read_id"]] = {
                "persona_hash": m["persona_hash"],
                "depth": m["depth"],
                "stance_changed": m["stance_changed"],
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
                })
        R.save_thread_meta(meta_store)
        if piggyback_votes:
            n = R.append_comment_votes(piggyback_votes)
            print(f"{n} 条跟帖顺势投票 → {R.VOTES}")
        print(f"{len(new_reads)} 条跟帖落盘 reads.jsonl，侧车元数据写入 {R.THREAD_META}")


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
    i.add_argument("--seed", type=int, default=0, help="0=用当前时间，其他值可复现")
    i.add_argument("--out", default="")

    c = sub.add_parser("collect")
    c.add_argument("--tasks", required=True)
    c.add_argument("--inbox", required=True)
    c.add_argument("--model", default="")
    c.add_argument("--transport", default="cc-subagent")

    v = sub.add_parser("void")
    v.add_argument("--read-id", dest="read_id", required=True)
    v.add_argument("--reason", required=True)

    args = ap.parse_args()
    {"invite": cmd_invite, "collect": cmd_collect, "void": cmd_void}[args.cmd](args)


if __name__ == "__main__":
    main()
