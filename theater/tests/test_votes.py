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


def test_batch_ballot_trim_and_best_prompt():
    """批量装箱（v1.5）：逐投票人裁剪选票——自己写的从票面摘掉而不是整箱排除；
    prompt 里带加精（best）问题。"""
    poem = _setup_corpus()
    personas_all = [p["persona_id"] for p in R.load_personas()
                    if not p.get("superseded_by")]
    a1, a2 = personas_all[0], personas_all[1]
    reads = [
        {"read_id": "r-300", "poem_id": poem["id"], "context_mode": "blind",
         "long_form": None, "reaction": "短评甲", "reader": {"persona_id": a1, "model": "m"},
         "content_hash": poem["content_hash"]},
        {"read_id": "r-301", "poem_id": poem["id"], "context_mode": "blind",
         "long_form": None, "reaction": "短评乙", "reader": {"persona_id": a2, "model": "m"},
         "content_hash": poem["content_hash"]},
    ]
    _write_reads(reads, "reads_batch.jsonl")
    R.VOTES_DIR = TMP
    R.VOTES = TMP / "votes_batch.jsonl"
    if R.VOTES.exists():
        R.VOTES.unlink()

    out_dir = TMP / "invite_batch_out"
    if out_dir.exists():
        shutil.rmtree(out_dir)

    class _A:
        targets = "r-300,r-301"
        poem_ids = ""
        fraction = 1.0
        batch_size = 8
        batch_chars = 4000
        seed = 1
        out = str(out_dir)

    PV.cmd_invite(_A)
    tasks = json.loads((out_dir / "batch.json").read_text(encoding="utf-8"))
    by_voter = {t["voter"]["persona_id"]: t for t in tasks}
    assert a1 in by_voter and a2 in by_voter, "作者只是被摘掉自己那条，不该整箱出局"
    assert [x["read_id"] for x in by_voter[a1]["targets"]] == ["r-301"]
    assert [x["read_id"] for x in by_voter[a2]["targets"]] == ["r-300"]
    third = next(p for p in personas_all if p not in (a1, a2))
    assert {x["read_id"] for x in by_voter[third]["targets"]} == {"r-300", "r-301"}
    assert "加精" in by_voter[third]["prompt"] and '"best"' in by_voter[third]["prompt"]
    print("[ok] 批量装箱逐投票人裁剪选票 + prompt 含加精")


def test_collect_batch_best():
    """batch collect（v1.5）：best 落盘为 vote="best"；模型把说明抄进值里也能解析；
    指向箱外的 best 被忽略；旧统计自动忽略 best。"""
    poem = _setup_corpus()
    R.VOTES_DIR = TMP
    R.VOTES = TMP / "votes_best.jsonl"
    if R.VOTES.exists():
        R.VOTES.unlink()

    tdir, idir = TMP / "best_tasks", TMP / "best_inbox"
    for d in (tdir, idir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    task = {"poem_id": poem["id"],
            "targets": [{"read_id": "r-400"}, {"read_id": "r-401"}],
            "voter": {"persona_id": "reader1", "model": None}, "prompt": "..."}
    task2 = {"poem_id": poem["id"],
             "targets": [{"read_id": "r-402"}],
             "voter": {"persona_id": "reader2", "model": None}, "prompt": "..."}
    (tdir / "task-001.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    (tdir / "task-002.json").write_text(json.dumps(task2, ensure_ascii=False), encoding="utf-8")
    (idir / "task-001.response.json").write_text(json.dumps({
        "model": "test-model",
        "votes": [{"read_id": "r-400", "vote": "skip", "reason": ""},
                  {"read_id": "r-401", "vote": "up", "reason": "原句"}],
        "best": "r-401 —— 这一批里你最想顶上去…（模型把说明抄进来了）",
    }, ensure_ascii=False), encoding="utf-8")
    (idir / "task-002.response.json").write_text(json.dumps({
        "model": "test-model",
        "votes": [{"read_id": "r-402", "vote": "skip", "reason": ""}],
        "best": "r-999999",
    }, ensure_ascii=False), encoding="utf-8")

    class _A:
        tasks = str(tdir)
        inbox = str(idir)
        model = "fallback"

    PV.cmd_collect(_A)
    votes = R.load_comment_votes()
    best = [v for v in votes if v["vote"] == "best"]
    assert len(votes) == 4 and len(best) == 1, (len(votes), len(best))
    assert best[0]["target_read_id"] == "r-401", "脏值里应解析出开头的 read_id"
    assert R.vote_tally("r-401", votes) == {"up": 1, "down": 0, "skip": 0}, \
        "旧统计（vote_tally）必须自动忽略 best"
    print("[ok] batch collect：best 落盘 + 脏值解析 + 箱外 best 忽略 + 旧统计不受影响")


def test_invite_targets_skip_hidden():
    """显式 --targets 点名也要跳过已折叠(hidden)评论——折叠即作者判低质，
    不该再花票/加精。堵死原来 --targets 绕过折叠过滤的后门。"""
    poem = _setup_corpus()
    personas_all = [p["persona_id"] for p in R.load_personas() if not p.get("superseded_by")]
    a1, a2 = personas_all[0], personas_all[1]
    reads = [
        {"read_id": "r-500", "poem_id": poem["id"], "context_mode": "blind",
         "long_form": None, "reaction": "没折叠", "reader": {"persona_id": a1, "model": "m"},
         "content_hash": poem["content_hash"]},
        {"read_id": "r-501", "poem_id": poem["id"], "context_mode": "blind",
         "long_form": None, "reaction": "被折叠", "reader": {"persona_id": a2, "model": "m"},
         "content_hash": poem["content_hash"]},
    ]
    _write_reads(reads, "reads_hidden.jsonl")
    R.CURATION = TMP / "curation_hidden.json"
    R.CURATION.write_text(json.dumps({"r-501": {"hidden": True, "reason": "低质"}},
                                     ensure_ascii=False), encoding="utf-8")
    R.VOTES_DIR = TMP
    R.VOTES = TMP / "votes_hidden.jsonl"
    if R.VOTES.exists():
        R.VOTES.unlink()

    out_dir = TMP / "invite_hidden_out"
    if out_dir.exists():
        shutil.rmtree(out_dir)

    class _A:
        targets = "r-500,r-501"
        poem_ids = ""
        fraction = 1.0
        seed = 1
        out = str(out_dir)

    PV.cmd_invite(_A)
    tasks = json.loads((out_dir / "batch.json").read_text(encoding="utf-8"))
    all_target_rids = set()
    for t in tasks:
        for tg in t.get("targets", []):
            all_target_rids.add(tg["read_id"])
        if "target_read_id" in t:
            all_target_rids.add(t["target_read_id"])
    assert "r-501" not in all_target_rids, "折叠评论不该被 --targets 派去投票/加精"
    assert "r-500" in all_target_rids, "未折叠评论应正常派发"
    print("[ok] cmd_invite --targets 跳过折叠评论（不投票/加精）")


if __name__ == "__main__":
    test_append_and_tally()
    test_votable_reads_filter()
    test_invite_dedupe_and_exclude_author()
    test_invite_targets_skip_hidden()
    test_collect_valid_and_invalid()
    test_batch_ballot_trim_and_best_prompt()
    test_collect_batch_best()
    print("ALL PASS")
