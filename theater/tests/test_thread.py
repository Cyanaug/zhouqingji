# -*- coding: utf-8 -*-
"""跟帖模式（thread）自包含测试（零依赖，直接跑：python theater/tests/test_thread.py）。

覆盖：祖先链组装 + token 预算封顶、自身楼层历史、persona_hash、ingest 对
thread_ref 的校验、void 级联标记、plan_thread.cmd_collect 的引用校验/
沉默分流/落盘/侧车元数据写入。全程把 R.CORPUS / R.READS / R.THREAD_META /
R.THREAD_SILENCES 指到临时文件，绝不碰真实 corpus/results。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "runners"))

import runner as R      # noqa: E402
import plan_thread as PT  # noqa: E402

TMP = Path(tempfile.gettempdir()) / "zqj_thread_test"
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


def test_ancestor_chain_and_history():
    poem = _setup_corpus()
    root = {"read_id": "r-1", "poem_id": poem["id"], "reader": {"persona_id": "a"},
            "context_mode": "blind", "thread_ref": None,
            "reaction": "短评", "long_form": "根楼长评" * 50,
            "content_hash": poem["content_hash"]}
    f2 = {"read_id": "r-2", "poem_id": poem["id"], "reader": {"persona_id": "b"},
          "context_mode": "thread", "thread_ref": "r-1",
          "reaction": "二楼回复" * 50, "long_form": None,
          "content_hash": poem["content_hash"]}
    f3 = {"read_id": "r-3", "poem_id": poem["id"], "reader": {"persona_id": "a"},
          "context_mode": "thread", "thread_ref": "r-2",
          "reaction": "三楼回复" * 50, "long_form": None,
          "content_hash": poem["content_hash"]}
    reads_by_id = {r["read_id"]: r for r in (root, f2, f3)}

    assert R.thread_root_id("r-3", reads_by_id) == "r-1"

    chain = R.ancestor_chain("r-3", reads_by_id, token_budget=100000)
    assert [r["read_id"] for r in chain] == ["r-1", "r-2", "r-3"]

    chain_tight = R.ancestor_chain("r-3", reads_by_id, token_budget=10)
    ids = [r["read_id"] for r in chain_tight]
    assert ids[0] == "r-1" and ids[-1] == "r-3", "根与 parent 预算再紧也要保留"
    assert "r-2" not in ids, "预算不够时先丢中间楼层，不是根/parent"

    hist_self = R.own_floor_history("r-1", "a", reads_by_id, exclude_read_id="r-3")
    assert hist_self == [], "排除自己当前这层后，a 在本帖没有别的楼层"
    hist_b = R.own_floor_history("r-1", "b", reads_by_id)
    assert [r["read_id"] for r in hist_b] == ["r-2"]
    print("[ok] ancestor_chain 预算封顶 / thread_root_id / own_floor_history")


def test_persona_sha1():
    h1 = R.persona_sha1({"persona": "甲"})
    h2 = R.persona_sha1({"persona": "甲"})
    h3 = R.persona_sha1({"persona": "乙"})
    assert h1 == h2 and h1 != h3
    print("[ok] persona_sha1 一致且随文本变化（比照 content_hash）")


def test_ingest_thread_requires_valid_thread_ref():
    poem = _setup_corpus()
    root = {"read_id": "r-100", "poem_id": poem["id"],
            "reader": {"persona_id": "a", "model": "m"},
            "context_mode": "blind", "thread_ref": None, "transport": "cc-subagent",
            "score": 8.0, "reaction": "短评", "long_form": "长评内容",
            "content_hash": poem["content_hash"], "ts": "2026-01-01T00:00:00"}
    _write_reads([root], "reads_a.jsonl")

    incoming = [{"poem_id": poem["id"], "reader": {"persona_id": "b", "model": "m"},
                 "context_mode": "thread", "thread_ref": "no-such-id",
                 "transport": "cc-subagent", "reaction": "回复",
                 "content_hash": poem["content_hash"]}]
    infile = TMP / "incoming.json"
    infile.write_text(json.dumps(incoming, ensure_ascii=False), encoding="utf-8")

    class _A:
        file = str(infile)

    try:
        R.cmd_ingest(_A)
        assert False, "thread_ref 指向不存在的楼层应该报错退出"
    except SystemExit:
        pass

    incoming[0]["thread_ref"] = "r-100"
    infile.write_text(json.dumps(incoming, ensure_ascii=False), encoding="utf-8")
    R.cmd_ingest(_A)
    reads = R.load_reads()
    thread_rec = [r for r in reads if r["context_mode"] == "thread"][0]
    assert thread_rec["score"] is None, "thread 记录不评分，score 落 null"
    assert thread_rec["thread_ref"] == "r-100"
    print("[ok] cmd_ingest 校验 thread_ref 存在性 + thread 记录 score=null")


def test_void_cascade():
    reads_by_id = {
        "r-1": {"read_id": "r-1", "context_mode": "blind", "thread_ref": None},
        "r-2": {"read_id": "r-2", "context_mode": "thread", "thread_ref": "r-1"},
        "r-3": {"read_id": "r-3", "context_mode": "thread", "thread_ref": "r-2"},
    }
    R.THREAD_DIR = TMP
    R.THREAD_META = TMP / "thread_meta_void.json"
    if R.THREAD_META.exists():
        R.THREAD_META.unlink()

    touched = R.void_floor("r-2", "人格崩坏", reads_by_id)
    assert set(touched) == {"r-2", "r-3"}
    meta = R.load_thread_meta()
    assert meta["r-2"]["void"] and meta["r-3"]["void"]
    assert meta["r-2"]["void_reason"] == "人格崩坏"
    assert "祖先 r-2 void 级联" in meta["r-3"]["void_reason"]
    print("[ok] void_floor 级联标记子孙楼层（隐藏不删除）")


def test_plan_thread_collect():
    poem = _setup_corpus()
    root = {"read_id": "r-200", "poem_id": poem["id"],
            "reader": {"persona_id": "op", "model": "m"},
            "context_mode": "blind", "thread_ref": None, "transport": "cc-subagent",
            "score": 8.0, "reaction": "短评",
            "long_form": "这首诗的核心意象是灯火，微弱却坚持。",
            "content_hash": poem["content_hash"], "ts": "2026-01-01T00:00:00"}
    _write_reads([root], "reads_b.jsonl")

    tdir, idir = TMP / "collect_tasks", TMP / "collect_inbox"
    for d in (tdir, idir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    base = {"poem_id": poem["id"], "title": poem["title"],
            "content_hash": poem["content_hash"], "root_id": "r-200",
            "parent_read_id": "r-200", "parent_text": root["long_form"],
            "depth": 1, "prompt": "..."}
    task_good = dict(base, persona_id="reader1", persona_hash="hash1",
                      reader={"persona_id": "reader1", "model": None,
                              "knows_诠释": False, "knows_date": False})
    task_silent = dict(base, persona_id="reader2", persona_hash="hash2",
                        reader={"persona_id": "reader2", "model": None,
                                "knows_诠释": False, "knows_date": False})
    task_bad = dict(base, persona_id="reader3", persona_hash="hash3",
                     reader={"persona_id": "reader3", "model": None,
                             "knows_诠释": False, "knows_date": False})

    (tdir / "task-001.json").write_text(json.dumps(task_good, ensure_ascii=False), encoding="utf-8")
    (tdir / "task-002.json").write_text(json.dumps(task_silent, ensure_ascii=False), encoding="utf-8")
    (tdir / "task-003.json").write_text(json.dumps(task_bad, ensure_ascii=False), encoding="utf-8")

    (idir / "task-001.response.json").write_text(json.dumps({
        "model": "test-model", "quote": "灯火，微弱却坚持",
        "restate": "灯火虽弱但没熄", "reaction": "我也觉得这个意象撑住了全诗",
        "long_form": None, "stance_changed": False, "stance_note": "没被说动",
    }, ensure_ascii=False), encoding="utf-8")
    (idir / "task-002.response.json").write_text(json.dumps({
        "model": "test-model", "silence": True,
        "reason": "楼主已经说尽了，我没有新点",
    }, ensure_ascii=False), encoding="utf-8")
    (idir / "task-003.response.json").write_text(json.dumps({
        "model": "test-model", "quote": "这句话根本不在原文里",
        "restate": "瞎编的", "reaction": "瞎回复", "long_form": None,
        "stance_changed": False, "stance_note": "x",
    }, ensure_ascii=False), encoding="utf-8")

    R.THREAD_SILENCES = TMP / "silences.jsonl"
    if R.THREAD_SILENCES.exists():
        R.THREAD_SILENCES.unlink()
    R.THREAD_DIR = TMP
    R.THREAD_META = TMP / "thread_meta_collect.json"
    if R.THREAD_META.exists():
        R.THREAD_META.unlink()

    class _A:
        tasks = str(tdir)
        inbox = str(idir)
        model = "fallback-model"
        transport = "cc-subagent"

    PT.cmd_collect(_A)

    reads = R.load_reads()
    thread_reads = [r for r in reads if r["context_mode"] == "thread"]
    assert len(thread_reads) == 1, "只有引用校验通过的那条应该落盘"
    assert thread_reads[0]["reader"]["persona_id"] == "reader1"
    assert thread_reads[0]["score"] is None

    meta = R.load_thread_meta()
    assert meta[thread_reads[0]["read_id"]]["persona_hash"] == "hash1"

    sil_lines = [json.loads(ln) for ln in
                 R.THREAD_SILENCES.read_text(encoding="utf-8").splitlines()]
    assert len(sil_lines) == 1 and sil_lines[0]["persona_id"] == "reader2"

    assert (idir / "rejected" / "task-003.response.json").exists(), \
        "引用不匹配应移入 rejected/，不落盘"
    assert not (idir / "task-003.response.json").exists()

    print("[ok] plan_thread.cmd_collect 引用校验/沉默分流/落盘/侧车元数据")


def test_invite_tiering_and_priority():
    """v1.5 分档邀请：一楼 prompt 无惯性段/stance 字段、深楼齐全且含诗正文；
    优先回应权=楼主+被回击者、parent 作者不自回；已回帖者默认去重、--allow-repeat 放开。"""
    poem = _setup_corpus()
    personas_all = [p["persona_id"] for p in R.load_personas()
                    if not p.get("superseded_by")]
    p1, p2, p3 = personas_all[0], personas_all[1], personas_all[2]
    root = {"read_id": "r-500", "poem_id": poem["id"],
            "reader": {"persona_id": p1, "model": "m", "knows_诠释": False,
                       "knows_date": False},
            "context_mode": "blind", "thread_ref": None, "transport": "cc-subagent",
            "score": 8.0, "reaction": "短评", "long_form": "根楼长评：灯火意象撑住了全诗。",
            "content_hash": poem["content_hash"], "ts": "2026-01-01T00:00:00"}
    f2 = dict(root, read_id="r-501", context_mode="thread", thread_ref="r-500",
              score=None, long_form=None, reaction="二楼：我不觉得撑住了",
              reader={"persona_id": p2, "model": "m", "knows_诠释": False,
                      "knows_date": False})
    f3 = dict(f2, read_id="r-502", thread_ref="r-501", reaction="三楼：同意二楼",
              reader={"persona_id": p3, "model": "m", "knows_诠释": False,
                      "knows_date": False})
    _write_reads([root, f2, f3], "reads_invite.jsonl")

    def run(parent, allow_repeat=False, sub="x"):
        out_dir = TMP / f"invite_{parent}_{sub}"
        if out_dir.exists():
            shutil.rmtree(out_dir)

        class _A:
            fraction = 1.0
            exclude = ""
            seed = 1
            out = str(out_dir)
        _A.parent = parent
        _A.allow_repeat = allow_repeat
        PT.cmd_invite(_A)
        return json.loads((out_dir / "batch.json").read_text(encoding="utf-8"))

    # 一楼（parent=root）：楼主不自回、已回过的 p2 去重、prompt 是广度档
    tasks = run("r-500")
    invited = {t["persona_id"] for t in tasks}
    assert p1 not in invited, "楼主=parent 作者，不回自己的楼"
    assert p2 not in invited, "p2 已回过 r-500，默认去重"
    prompt = tasks[0]["prompt"]
    assert "诗歌正文到此为止" in prompt, "一楼 prompt 必须带诗全文"
    assert "关于会不会被说服" not in prompt, "一楼没有旧立场，不拼惯性段"
    assert "stance_changed" not in prompt, "一楼回执不收 stance 字段"

    # 深楼（parent=r-501）：优先回应权=楼主 p1；p2 不自回；p3 已回过 r-501 去重
    tasks2 = run("r-501")
    invited2 = {t["persona_id"] for t in tasks2}
    assert p1 in invited2, "楼主有优先回应权"
    assert p2 not in invited2 and p3 not in invited2
    t_p1 = next(t for t in tasks2 if t["persona_id"] == p1)
    assert t_p1["depth"] == 2
    assert "关于会不会被说服" in t_p1["prompt"] and "stance_changed" in t_p1["prompt"]
    assert "诗歌正文到此为止" in t_p1["prompt"]

    # --allow-repeat：p3 可以再回 r-501
    tasks3 = run("r-501", allow_repeat=True, sub="rep")
    assert p3 in {t["persona_id"] for t in tasks3}
    print("[ok] invite 分档 prompt / 优先回应权 / 不自回 / 去重与 --allow-repeat")


if __name__ == "__main__":
    test_ancestor_chain_and_history()
    test_persona_sha1()
    test_ingest_thread_requires_valid_thread_ref()
    test_void_cascade()
    test_plan_thread_collect()
    test_invite_tiering_and_priority()
    print("ALL PASS")
