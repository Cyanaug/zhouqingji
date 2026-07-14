# -*- coding: utf-8 -*-
"""全量加厚：给读者池每首诗随机补 N 层盲读（可按 id 排除若干首）。

用法:
  python plan_thicken.py --layers 1 [--exclude zq-0280,zq-0132,zq-0038] [--seed 0] [--out DIR]

产出: <DIR>/batch.json + tasks/task-NN.json + tasks/task-NN.prompt.txt（自包含，侧车防截断）
人设选择: 每首按 (诗×人设) 最薄覆盖 + 随机 tiebreak 取 N 个
          —— 即「随机人设」且优先补还没读过的配对；被取代人设自动剔除。
依赖 runner.py 的 pool / build_prompt / load_stanzas（schema 不变，仅加此入口）。
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layers", type=int, default=1, help="每首补几层盲读")
    ap.add_argument("--exclude", default="", help="逗号分隔的 poem_id，排除在本轮外")
    ap.add_argument("--seed", type=int, default=0,
                    help="随机种子；0=每次用当前时间（不可复现），其他值可复现")
    ap.add_argument("--out", default="", help="批次目录，缺省自动命名")
    args = ap.parse_args()

    seed = args.seed if args.seed else int(time.time())
    random.seed(seed)

    exclude = {x for x in args.exclude.split(",") if x}
    poems = [p for p in R.pool() if p["id"] not in exclude]
    personas = [p for p in R.load_json(R.PERSONAS) if not p.get("superseded_by")]
    stanzas = R.load_stanzas()
    reads = [r for r in R.load_reads() if r.get("context_mode") == "blind"]
    per_pair = Counter((r["poem_id"], r["reader"]["persona_id"]) for r in reads)

    tasks = []
    for p in poems:
        ps = sorted(personas,
                    key=lambda x: (per_pair[(p["id"], x["persona_id"])],
                                   random.random()))[:args.layers]
        for persona in ps:
            tasks.append({
                "poem_id": p["id"],
                "title": p["title"],
                "persona_id": persona["persona_id"],
                "reader": {
                    "persona_id": persona["persona_id"],
                    "model": None,
                    "knows_诠释": persona["knows_诠释"],
                    "knows_date": persona["knows_date"],
                },
                "content_hash": p["content_hash"],
                "prompt": R.build_prompt(p, persona, stanzas),
            })

    out = Path(args.out) if args.out else \
        BATCHES / f"thicken-all{args.layers}-{time.strftime('%Y%m%d-%H%M%S')}"
    (out / "tasks").mkdir(parents=True, exist_ok=True)
    (out / "inbox").mkdir(parents=True, exist_ok=True)
    json.dump(tasks, open(out / "batch.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    for i, t in enumerate(tasks):
        n = f"{i + 1:03d}"
        json.dump(t, open(out / f"tasks/task-{n}.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        open(out / f"tasks/task-{n}.prompt.txt", "w", encoding="utf-8").write(t["prompt"])

    print(f"{len(tasks)} 个盲读任务（{len(poems)} 首 × {args.layers} 层）"
          f" -> {out}  (seed={seed}, 排除 {len(exclude)} 首)")


if __name__ == "__main__":
    main()
