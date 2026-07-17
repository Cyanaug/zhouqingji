# -*- coding: utf-8 -*-
"""点赞模式：让读者对已有短评（无长评的盲读反应）投票 认同/不认同/跳过。

不是新的排名指标——数据完全独立于 reads.jsonl，落在 results/votes/votes.jsonl。
用途：给作者一个"这条短评有没有说到点子上"的信号，作为手动撤下低质短评、腾位置
给新读者（争取触发长评）的依据。要不要按票数自动降权，v0 不做，先看数据再说。

用法：
  python plan_votes.py invite --poem-ids zq-0001,zq-0002 [--fraction 0.3] [--seed 0] [--out DIR]
                                            # 对这些诗「无长评」的全部短评发起投票
  python plan_votes.py invite --targets r-000123,r-000456 [--fraction 0.3] [--out DIR]
                                            # 直接指定要投票的具体短评
  python plan_votes.py collect --tasks DIR/tasks --inbox DIR/inbox --model M
  python plan_votes.py tally --poem-id zq-0001
                                            # 打印这首诗下每条短评的赞/踩/跳过计数
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


def _short_comments_for(poem_ids, reads):
    """只挑「无长评」的盲读短评——长评走 thread 那条线，不在这里重复投票。"""
    want = set(poem_ids) if poem_ids else None
    out = []
    for r in reads:
        if r.get("context_mode") != "blind" or r.get("long_form"):
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
        targets = _short_comments_for(poem_ids, reads_by_id.values())
    if not targets:
        sys.exit("没有符合条件的短评可投票")

    poems = {p["id"]: p for p in R.load_json(R.CORPUS)}
    personas = {p["persona_id"]: p for p in R.load_personas() if not p.get("superseded_by")}
    stanzas = R.load_stanzas()
    voted = {(v["target_read_id"], v["voter"]["persona_id"])
             for v in R.load_comment_votes()}

    seed = args.seed if args.seed else int(time.time())
    random.seed(seed)

    tasks = []
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

    print(f"{len(tasks)} 个投票任务（{len(targets)} 条短评）→ {out}  (seed={seed})")


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
        vote = resp.get("vote")
        if vote not in ("up", "down", "skip"):
            missing.append(tf.stem + "（vote 值非法）"); continue
        new_votes.append({
            "poem_id": t["poem_id"],
            "target_read_id": t["target_read_id"],
            "voter": {"persona_id": t["voter"]["persona_id"],
                      "model": resp.get("model") or args.model},
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
    votes = R.load_comment_votes()
    reads_by_id = {r["read_id"]: r for r in R.load_reads()}
    by_target = defaultdict(list)
    for v in votes:
        if v["poem_id"] == args.poem_id:
            by_target[v["target_read_id"]].append(v)
    if not by_target:
        print("这首诗还没有投票记录")
        return
    for rid in by_target:
        r = reads_by_id.get(rid, {})
        tally = R.vote_tally(rid, votes)
        print(f"{rid}（{r.get('reader', {}).get('persona_id', '?')}）："
              f"👍{tally['up']} 👎{tally['down']} 跳过{tally['skip']}  "
              f"{(r.get('reaction') or '')[:40]}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("invite")
    i.add_argument("--poem-ids", dest="poem_ids", default="",
                    help="逗号分隔 poem_id：对这些诗「无长评」的短评发起投票")
    i.add_argument("--targets", default="",
                    help="逗号分隔 read_id：直接指定要投票的短评（优先于 --poem-ids）")
    i.add_argument("--fraction", type=float, default=0.3, help="每条短评邀请的读者比例")
    i.add_argument("--seed", type=int, default=0, help="0=用当前时间，其他值可复现")
    i.add_argument("--out", default="")

    c = sub.add_parser("collect")
    c.add_argument("--tasks", required=True)
    c.add_argument("--inbox", required=True)
    c.add_argument("--model", default="")

    t = sub.add_parser("tally")
    t.add_argument("--poem-id", dest="poem_id", required=True)

    args = ap.parse_args()
    {"invite": cmd_invite, "collect": cmd_collect, "tally": cmd_tally}[args.cmd](args)


if __name__ == "__main__":
    main()
