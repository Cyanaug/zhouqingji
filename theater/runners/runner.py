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
READS = ROOT / "results" / "reads" / "reads.jsonl"
BATCHES = ROOT / "theater" / "runners" / "batches"

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
    personas = [p for p in load_json(PERSONAS) if not p.get("superseded_by")]
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
    """输入：一个 JSON 数组文件，每项含冻结 schema 的必填字段（read_id/ts 可缺，落盘时补）。"""
    incoming = load_json(args.file)
    if isinstance(incoming, dict):
        incoming = [incoming]
    existing = load_reads()
    counter = next_read_id(existing)
    valid_poems = {p["id"]: p for p in load_json(CORPUS)}

    lines, errors = [], []
    for i, r in enumerate(incoming):
        missing = [f for f in REQUIRED_FIELDS if f not in r or r[f] is None]
        if missing:
            errors.append(f"#{i} 缺字段 {missing}"); continue
        if r["poem_id"] not in valid_poems:
            errors.append(f"#{i} poem_id 不存在: {r['poem_id']}"); continue
        if not isinstance(r["score"], (int, float)) or not 0 <= r["score"] <= 10:
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
            "score": round(float(r["score"]), 1),
            "reaction": r["reaction"],
            "long_form": r.get("long_form"),
            "ts": r.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "content_hash": r["content_hash"],
        }
        lines.append(json.dumps(rec, ensure_ascii=False))

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
