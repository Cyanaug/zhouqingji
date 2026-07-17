# -*- coding: utf-8 -*-
"""点赞模式（点赞/点踩/跳过）自包含测试（零依赖，直接跑：python theater/tests/test_votes.py）。

覆盖：append_comment_votes/vote_tally 读写、_short_comments_for 过滤（只挑无长评的
盲读短评）、cmd_invite 的去重（不重复邀请已投过的 voter、排除评论作者本人）、
cmd_collect 的落盘与非法 vote 值拒收。全程把 R.CORPUS/R.READS/R.VOTES 指到临时
文件，绝不碰真实 corpus/results。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "runners"))

import runner as R       # noqa: E402
import plan_votes as PV  # noqa: E402

TMP = Path(tempfile.gettempdir()) / "zqj_votes_test"
TMP.mkdir(exist_ok=True)


def _setup_corpus():
    poem = {"id": "zq-test", "title": "测试诗", "content": "一行\n二行",
            "content_hash": "deadbeef", "visibility": "public", "ai_read": True,
            "genre": "现代诗"}
    p = TMP / "corpus.json"
    p.write_text(json.dumps([poem], ensure_ascii=False), encoding="utf-8")
    R.CORPUS = p
    return poem


def _write_reads(records, name="reads.jsonl"):
    p = TMP / name
    with p.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    R.READS = p
    return p


def test_append_and_tally():
    R.VOTES_DIR = TMP
    R.VOTES = TMP / "votes_a.jsonl"
    if R.VOTES.exists():
        R.VOTES.unlink()

    n = R.append_comment_votes([
        {"poem_id": "zq-test", "target_read_id": "r-1",
         "voter": {"persona_id": "a", "model": "m"}, "vote": "up", "reason": ""},
        {"poem_id": "zq-test", "target_read_id": "r-1",
         "voter": {"persona_id": "b", "model": "m"}, "vote": "down", "reason": "没读懂"},
        {"poem_id": "zq-test", "target_read_id": "r-1",
         "voter": {"persona_id": "c", "model": "m"}, "vote": "skip", "reason": ""},
    ])
    assert n == 3
    votes = R.load_comment_votes()
    assert [v["vote_id"] for v in votes] == ["v-000001", "v-000002", "v-000003"]
    tally = R.vote_tally("r-1", votes)
    assert tally == {"up": 1, "down": 1, "skip": 1}
    print("[ok] append_comment_votes / vote_tally")


def test_votable_reads_filter():
    reads = [
        {"read_id": "r-1", "poem_id": "zq-a", "context_mode": "blind",
         "long_form": None, "reader": {"persona_id": "x"}},
        {"read_id": "r-2", "poem_id": "zq-a", "context_mode": "blind",
         "long_form": "一篇长评", "reader": {"persona_id": "y"}},
        {"read_id": "r-3", "poem_id": "zq-b", "context_mode": "blind",
         "long_form": None, "reader": {"persona_id": "z"}},
        {"read_id": "r-4", "poem_id": "zq-a", "context_mode": "thread",
         "long_form": None, "reader": {"persona_id": "w"}},
    ]
    out_all = PV._votable_reads_for([], reads)
    assert [r["read_id"] for r in out_all] == ["r-1", "r-2", "r-3"], \
        "短评和长评都算票选目标，只排除 thread 楼层（那边走顺势投票）"
    out_a = PV._votable_reads_for(["zq-a"], reads)
    assert [r["read_id"] for r in out_a] == ["r-1", "r-2"]
    print("[ok] _votable_reads_for 过滤（短评+长评都算，排除 thread，可选 poem 过滤）")


def test_invite_dedupe_and_exclude_author():
    poem = _setup_corpus()
    target = {"read_id": "r-100", "poem_id": poem["id"],
              "context_mode": "blind", "long_form": None, "reaction": "还行",
              "reader": {"persona_id": "midnight-peer", "model": "m"},
              "content_hash": poem["content_hash"]}
    _write_reads([target], "reads_invite.jsonl")

    R.VOTES_DIR = TMP
    R.VOTES = TMP / "votes_invite.jsonl"
    if R.VOTES.exists():
        R.VOTES.unlink()
    personas_all = [p["persona_id"] for p in R.load_personas() if not p.get("superseded_by")]
    other = next(p for p in personas_all if p != "midnight-peer")
    R.append_comment_votes([{"poem_id": poem["id"], "target_read_id": "r-100",
                              "voter": {"persona_id": other, "model": "m"},
                              "vote": "up", "reason": ""}])

    out_dir = TMP / "invite_out"
    if out_dir.exists():
        shutil.rmtree(out_dir)

    class _A:
        targets = "r-100"
        poem_ids = ""
        fraction = 1.0
        seed = 1
        out = str(out_dir)

    PV.cmd_invite(_A)
    tasks = json.loads((out_dir / "batch.json").read_text(encoding="utf-8"))
    voter_ids = {t["voter"]["persona_id"] for t in tasks}
    assert "midnight-peer" not in voter_ids, "评论作者本人不应该被邀请投票"
    assert other not in voter_ids, "已经投过票的人不应该被重复邀请"
    assert len(voter_ids) == len(personas_all) - 2
    print("[ok] cmd_invite 排除作者本人 + 去重已投票者")


def test_collect_valid_and_invalid():
    poem = _setup_corpus()
    R.VOTES_DIR = TMP
    R.VOTES = TMP / "votes_collect.jsonl"
    if R.VOTES.exists():
        R.VOTES.unlink()

    tdir, idir = TMP / "vote_tasks", TMP / "vote_inbox"
    for d in (tdir, idir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    task_ok = {"poem_id": poem["id"], "target_read_id": "r-200",
               "voter": {"persona_id": "reader1", "model": None}, "prompt": "..."}
    task_bad = {"poem_id": poem["id"], "target_read_id": "r-201",
                "voter": {"persona_id": "reader2", "model": None}, "prompt": "..."}
    (tdir / "task-001.json").write_text(json.dumps(task_ok, ensure_ascii=False), encoding="utf-8")
    (tdir / "task-002.json").write_text(json.dumps(task_bad, ensure_ascii=False), encoding="utf-8")
    (idir / "task-001.response.json").write_text(
        json.dumps({"model": "test-model", "vote": "up", "reason": "说到点子上了"},
                   ensure_ascii=False), encoding="utf-8")
    (idir / "task-002.response.json").write_text(
        json.dumps({"model": "test-model", "vote": "maybe"}, ensure_ascii=False),
        encoding="utf-8")

    class _A:
        tasks = str(tdir)
        inbox = str(idir)
        model = "fallback"

    PV.cmd_collect(_A)
    votes = R.load_comment_votes()
    assert len(votes) == 1 and votes[0]["target_read_id"] == "r-200"
    assert (idir / "task-001.response.json").exists() is False
    assert (idir / "ingested" / "task-001.response.json").exists()
    assert (idir / "task-002.response.json").exists(), "非法 vote 值不应被移动/落盘"
    print("[ok] cmd_collect 合法落盘 + 非法 vote 值拒收")


if __name__ == "__main__":
    test_append_and_tally()
    test_votable_reads_filter()
    test_invite_dedupe_and_exclude_author()
    test_collect_valid_and_invalid()
    print("ALL PASS")
