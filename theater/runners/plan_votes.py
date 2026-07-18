# -*- coding: utf-8 -*-
"""点赞模式：让读者对已有的盲读评论（短评或长评均可）投票 认同/不认同/跳过。

不是新的排名指标——数据完全独立于 reads.jsonl，落在 results/votes/votes.jsonl。
用途：给作者一个"这条评论有没有说到点子上"的信号，短评上主要用来判断要不要手动
撤下低质短评、腾位置给新读者（争取触发长评）；长评上则是比开一整场跟帖更轻的
认同度信号。跟帖回复时也会对被回复的楼层顺势投一票（见 plan_thread.py），
落进同一份 votes.jsonl，这里只负责「主动发起」的那条路。
要不要按票数自动降权，v0 不做，先看数据再说。

用法：
  python plan_votes.py invite --poem-ids zq-0001,zq-0002 [--fraction 0.3] [--seed 0] [--out DIR]
                                            # 对这些诗的全部盲读评论（短评+长评）发起投票
  python plan_votes.py invite --targets r-000123,r-000456 [--fraction 0.3] [--out DIR]
                                            # 直接指定要投票的具体评论（含跟帖楼层也可）
  python plan_votes.py collect --tasks DIR/tasks --inbox DIR/inbox --model M
  python plan_votes.py tally --poem-id zq-0001
                                            # 打印这首诗下每条评论的赞/踩/跳过计数
"""
import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner as R

BATCHES = R.BATCHES


def _votable_reads_for(poem_ids, reads):
    """挑盲读评论（短评+长评都算）作为投票目标。
    排除：thread 楼层（顺势带票）、用户已折叠的评论（curation.json hidden=True）。"""
    want = set(poem_ids) if poem_ids else None
    hidden = R.hidden_read_ids()
    out = []
    for r in reads:
        if r.get("context_mode") != "blind":
            continue
        if r["read_id"] in hidden:
            continue
        if want is not None and r["poem_id"] not in want:
            continue
        out.append(r)
    return out


def cmd_invite(args):
    reads_by_id = {r["read_id"]: r for r in R.load_reads()}
    if args.targets:
        targets = [reads_by_id[t] for t in args.targets.split(",") if t in reads_by_id]
    else:
        poem_ids = [x for x in args.poem_ids.split(",") if x]
        targets = _votable_reads_for(poem_ids, reads_by_id.values())
    if not targets:
        sys.exit("没有符合条件的评论可投票")

    poems = {p["id"]: p for p in R.load_json(R.CORPUS)}
    personas = {p["persona_id"]: p for p in R.load_personas() if not p.get("superseded_by")}
    stanzas = R.load_stanzas()
    # 去重只认有效票：被作废（void）的票不算「已投过」，允许真读者重新投
    all_votes = R.valid_comment_votes()
    voted = {(v["target_read_id"], v["voter"]["persona_id"]) for v in all_votes}

    seed = args.seed if args.seed else int(time.time())
    random.seed(seed)

    batch_size = getattr(args, "batch_size", 1)
    tasks = []

    if batch_size <= 1:
        # 原有逻辑：每条评论独立一个任务
        for t in targets:
            poem = poems.get(t["poem_id"])
            if poem is None:
                continue
            author_id = t["reader"]["persona_id"]
            candidates = [pid for pid in personas if pid != author_id
                          and (t["read_id"], pid) not in voted]
            random.shuffle(candidates)
            n = max(1, round(len(candidates) * args.fraction)) if candidates else 0
            for pid in candidates[:n]:
                persona = personas[pid]
                prompt = R.build_vote_prompt(poem, persona, t, stanzas)
                tasks.append({
                    "poem_id": t["poem_id"],
                    "target_read_id": t["read_id"],
                    "voter": {"persona_id": pid, "model": None},
                    "prompt": prompt,
                })
    else:
        # 批量模式：同一首诗的评论分组后按 batch_size 切块，一个任务读 N 条
        by_poem = defaultdict(list)
        for t in targets:
            if t["poem_id"] in poems:
                by_poem[t["poem_id"]].append(t)
        for poem_id, ptargets in by_poem.items():
            poem = poems[poem_id]
            for i in range(0, len(ptargets), batch_size):
                chunk = ptargets[i:i + batch_size]
                chunk_rids = {c["read_id"] for c in chunk}
                excluded = {c["reader"]["persona_id"] for c in chunk}
                already = {pid for (rid, pid) in voted if rid in chunk_rids}
                candidates = [pid for pid in personas
                              if pid not in excluded and pid not in already]
                random.shuffle(candidates)
                n = max(1, round(len(candidates) * args.fraction)) if candidates else 0
                for pid in candidates[:n]:
                    persona = personas[pid]
                    prompt = R.build_batch_vote_prompt(poem, persona, chunk, stanzas)
                    tasks.append({
                        "poem_id": poem_id,
                        "targets": [{"read_id": c["read_id"]} for c in chunk],
                        "voter": {"persona_id": pid, "model": None},
                        "prompt": prompt,
                    })

    out = Path(args.out) if args.out else \
        BATCHES / f"votes-{time.strftime('%Y%m%d-%H%M%S')}"
    (out / "tasks").mkdir(parents=True, exist_ok=True)
    (out / "inbox").mkdir(parents=True, exist_ok=True)
    json.dump(tasks, open(out / "batch.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    for i, t2 in enumerate(tasks):
        n2 = f"{i + 1:03d}"
        json.dump(t2, open(out / f"tasks/task-{n2}.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        open(out / f"tasks/task-{n2}.prompt.txt", "w", encoding="utf-8").write(t2["prompt"])

    mode = f"批量 batch_size={batch_size}" if batch_size > 1 else "逐条"
    print(f"{len(tasks)} 个投票任务（{mode}，{len(targets)} 条评论）→ {out}  (seed={seed})")


def cmd_collect(args):
    tdir, inbox = Path(args.tasks), Path(args.inbox)
    done = inbox / "ingested"
    new_votes, missing = [], []
    for tf in sorted(tdir.glob("task-*.json")):
        rf = inbox / (tf.stem + ".response.json")
        if not rf.exists():
            missing.append(tf.stem); continue
        t = json.loads(tf.read_text(encoding="utf-8"))
        resp = json.loads(rf.read_text(encoding="utf-8"))
        model = resp.get("model") or args.model
        voter_id = t["voter"]["persona_id"]

        if "targets" in t:
            # 批量模式：response 里有 votes 数组
            valid_rids = {tgt["read_id"] for tgt in t["targets"]}
            votes_list = resp.get("votes")
            if not isinstance(votes_list, list):
                missing.append(tf.stem + "（batch: votes 字段非列表）"); continue
            bad = []
            for v in votes_list:
                rid = v.get("read_id", "")
                vote = v.get("vote")
                if rid not in valid_rids or vote not in ("up", "down", "skip"):
                    bad.append(rid or "?"); continue
                new_votes.append({
                    "poem_id": t["poem_id"],
                    "target_read_id": rid,
                    "voter": {"persona_id": voter_id, "model": model},
                    "vote": vote,
                    "reason": str(v.get("reason", "")),
                })
            if bad:
                print(f"  {tf.stem}: {len(bad)} 条无效（read_id 不对或 vote 非法）：{bad}",
                      file=sys.stderr)
        else:
            # 逐条模式（原有）
            vote = resp.get("vote")
            if vote not in ("up", "down", "skip"):
                missing.append(tf.stem + "（vote 值非法）"); continue
            new_votes.append({
                "poem_id": t["poem_id"],
                "target_read_id": t["target_read_id"],
                "voter": {"persona_id": voter_id, "model": model},
                "vote": vote,
                "reason": str(resp.get("reason", "")),
            })

        done.mkdir(exist_ok=True)
        rf.rename(done / rf.name)

    if missing:
        print(f"缺/无效 {len(missing)} 份回执：{', '.join(missing)}", file=sys.stderr)
    if new_votes:
        n = R.append_comment_votes(new_votes)
        print(f"{n} 条投票落盘 → {R.VOTES}")


def cmd_tally(args):
    votes = R.valid_comment_votes()
    reads_by_id = {r["read_id"]: r for r in R.load_reads()}
    by_target = defaultdict(list)
    for v in votes:
        if args.poem_id and v["poem_id"] != args.poem_id:
            continue
        by_target[v["target_read_id"]].append(v)
    if not by_target:
        print("这首诗还没有投票记录" if args.poem_id else "还没有任何投票记录")
        return
    # 主动票（点赞模式）在前——撤不撤评看这个；顺势票（跟帖带来）弱信号，括号里附注
    rows = []
    for rid in by_target:
        s = R.vote_tally_split(rid, votes)
        rows.append((rid, s))
    # 按主动票的净认同（up-down）升序：最该考虑撤下的排最前。
    # 只有主动票才排序（顺势票是弱信号）；无主动票的目标沉底、净值记 0。
    rows.sort(key=lambda x: (x[1]["direct"]["up"] - x[1]["direct"]["down"],
                             -(x[1]["direct"]["up"] + x[1]["direct"]["down"])))
    if args.worst:
        # 全集撤评总览：只留有主动票的、按最该撤在前，取前 N 条。
        # 已撤评（curation hidden）的评论从总览里剔除——它们已经处理过，
        # 留在榜上只会占位、把还没处理的挤下去。
        hidden = R.hidden_read_ids()
        rows = [r for r in rows
                if r[0] not in hidden
                and (r[1]["direct"]["up"] or r[1]["direct"]["down"]
                     or r[1]["direct"]["skip"])][:args.worst]
    for rid, s in rows:
        r = reads_by_id.get(rid, {})
        d, p = s["direct"], s["piggyback"]
        pid_poem = f"[{r.get('poem_id', '?')}] " if not args.poem_id else ""
        pig = f"  〔顺势 ▲{p['up']} ▼{p['down']}〕" if (p["up"] or p["down"]) else ""
        print(f"{pid_poem}{rid}（{r.get('reader', {}).get('persona_id', '?')}）："
              f"主动 ▲赞{d['up']} ▼踩{d['down']} 跳过{d['skip']}{pig}  "
              f"{(r.get('reaction') or '')[:40]}")


def cmd_void(args):
    ids = [x.strip() for x in args.vote_ids.split(",") if x.strip()]
    touched = R.void_votes(ids, args.reason)
    print(f"标记作废 {len(touched)} 张票 → {R.VOTES_VOID}（票据不删，统计/展示/去重均已排除）")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("invite")
    i.add_argument("--poem-ids", dest="poem_ids", default="",
                    help="逗号分隔 poem_id：对这些诗「无长评」的短评发起投票")
    i.add_argument("--targets", default="",
                    help="逗号分隔 read_id：直接指定要投票的短评（优先于 --poem-ids）")
    i.add_argument("--fraction", type=float, default=0.3, help="邀请的读者比例")
    i.add_argument("--batch-size", dest="batch_size", type=int, default=1,
                   help="批量模式：一个任务打包几条评论（默认 1=逐条，建议 3-5）")
    i.add_argument("--seed", type=int, default=0, help="0=用当前时间，其他值可复现")
    i.add_argument("--out", default="")

    c = sub.add_parser("collect")
    c.add_argument("--tasks", required=True)
    c.add_argument("--inbox", required=True)
    c.add_argument("--model", default="")

    t = sub.add_parser("tally")
    t.add_argument("--poem-id", dest="poem_id", default="",
                   help="只看这首诗；省略=全集")
    t.add_argument("--worst", type=int, default=0,
                   help="全集撤评总览：按主动净认同升序取前 N 条（最该撤的在前）")

    v = sub.add_parser("void")
    v.add_argument("--vote-ids", dest="vote_ids", required=True,
                   help="逗号分隔 vote_id，如 v-000137,v-000138")
    v.add_argument("--reason", required=True)

    args = ap.parse_args()
    {"invite": cmd_invite, "collect": cmd_collect, "tally": cmd_tally,
     "void": cmd_void}[args.cmd](args)


if __name__ == "__main__":
    main()
