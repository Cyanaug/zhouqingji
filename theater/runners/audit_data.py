# -*- coding: utf-8 -*-
"""昼青集账本只读审计。退出码 0=结构一致，1=发现会污染统计的错误。"""
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import runner as R


def _json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: JSON 无法读取：{exc}") from exc


def _jsonl(path):
    if not path.exists():
        return []
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{n}: JSONL 行损坏：{exc}") from exc
    return rows


def audit():
    errors, warnings = [], []
    try:
        corpus = _json(R.CORPUS, [])
        reads = _jsonl(R.READS)
        votes = _jsonl(R.VOTES)
        vote_void = _json(R.VOTES_VOID, {})
        thread_meta = _json(R.THREAD_META, {})
    except ValueError as exc:
        return [str(exc)], []

    if not R.CORPUS.exists():
        warnings.append("未安装私人 corpus；按空白公开发行版审计代码结构")

    poems = {}
    for i, poem in enumerate(corpus):
        pid = poem.get("id")
        if not pid or pid in poems:
            errors.append(f"corpus 第 {i + 1} 条 poem id 缺失或重复：{pid!r}")
            continue
        poems[pid] = poem
        expected = hashlib.sha1((poem.get("content") or "").encode("utf-8")).hexdigest()
        if poem.get("content_hash") != expected:
            errors.append(f"{pid}: content_hash 与正文不符")

    reads_by_id = {}
    stale = 0
    reaction_poems = defaultdict(set)
    for i, read in enumerate(reads):
        rid = read.get("read_id")
        if not rid or rid in reads_by_id:
            errors.append(f"reads 第 {i + 1} 条 read_id 缺失或重复：{rid!r}")
            continue
        reads_by_id[rid] = read
        poem = poems.get(read.get("poem_id"))
        if not poem:
            errors.append(f"{rid}: poem_id 不存在：{read.get('poem_id')!r}")
        elif read.get("content_hash") != poem.get("content_hash"):
            stale += 1
        mode = read.get("context_mode")
        if mode == "thread":
            if read.get("score") is not None:
                errors.append(f"{rid}: thread 楼层 score 必须为 null")
        else:
            score = read.get("score")
            if not isinstance(score, (int, float)) or not 0 <= score <= 10:
                errors.append(f"{rid}: score 非法：{score!r}")
        text = (read.get("reaction") or "").strip()
        if mode == "blind" and text:
            reaction_poems[text].add(read.get("poem_id"))

    for rid, read in reads_by_id.items():
        if read.get("context_mode") != "thread":
            continue
        parent = reads_by_id.get(read.get("thread_ref"))
        if not parent:
            errors.append(f"{rid}: thread_ref 断链：{read.get('thread_ref')!r}")
        elif parent.get("poem_id") != read.get("poem_id"):
            errors.append(f"{rid}: thread_ref 跨作品")

    reused = sum(1 for poem_ids in reaction_poems.values() if len(poem_ids) > 1)
    if reused:
        errors.append(f"发现 {reused} 段盲读 reaction 被复用于不同作品")

    vote_ids = set()
    for i, vote in enumerate(votes):
        vid = vote.get("vote_id")
        if not vid or vid in vote_ids:
            errors.append(f"votes 第 {i + 1} 条 vote_id 缺失或重复：{vid!r}")
        vote_ids.add(vid)
        target = reads_by_id.get(vote.get("target_read_id"))
        if not target:
            errors.append(f"{vid}: target_read_id 不存在：{vote.get('target_read_id')!r}")
        elif target.get("poem_id") != vote.get("poem_id"):
            errors.append(f"{vid}: poem_id 与目标评论不一致")
        if vote.get("vote") not in {"up", "down", "skip", "best"}:
            errors.append(f"{vid}: vote 值非法：{vote.get('vote')!r}")
        if not (vote.get("voter") or {}).get("persona_id"):
            errors.append(f"{vid}: voter.persona_id 缺失")

    unknown_void = set(vote_void) - vote_ids
    if unknown_void:
        errors.append(f"void.json 引用了 {len(unknown_void)} 个不存在的 vote_id")

    active_by_key = defaultdict(list)
    for vote in votes:
        if vote.get("vote_id") not in vote_void:
            active_by_key[R.vote_identity(vote)].append(vote)
    duplicate_groups = {k: vs for k, vs in active_by_key.items() if len(vs) > 1}
    if duplicate_groups:
        conflicts = sum(1 for vs in duplicate_groups.values()
                        if len({v.get("vote") for v in vs}) > 1)
        errors.append(f"有效票仍有 {len(duplicate_groups)} 组重复身份，其中 {conflicts} 组方向冲突")

    unknown_meta = set(thread_meta) - set(reads_by_id)
    if unknown_meta:
        errors.append(f"thread meta 引用了 {len(unknown_meta)} 个不存在的 read_id")

    warnings.append(
        f"统计：poems={len(corpus)} reads={len(reads)} votes={len(votes)} "
        f"valid_votes={len(votes) - len(vote_void)} stale_reads={stale}"
    )
    return errors, warnings


def main():
    errors, warnings = audit()
    for msg in warnings:
        print(f"[info] {msg}")
    for msg in errors:
        print(f"[error] {msg}", file=sys.stderr)
    if errors:
        print(f"FAIL: {len(errors)} 项错误", file=sys.stderr)
        return 1
    print("PASS: 数据账本一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
