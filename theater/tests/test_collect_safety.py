# -*- coding: utf-8 -*-
"""回执解析与盲读 collect 的无损、整批、幂等边界（不碰真实数据）。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "runners"))
import runner as R  # noqa: E402

TMP = Path(tempfile.gettempdir()) / "zqj_collect_safety_test"


def _reset():
    if TMP.exists():
        shutil.rmtree(TMP)
    (TMP / "tasks").mkdir(parents=True)
    (TMP / "inbox").mkdir(parents=True)
    corpus = TMP / "corpus.json"
    corpus.write_text(json.dumps([{
        "id": "zq-test", "title": "测试诗", "content": "一行\n二行",
        "content_hash": "deadbeef", "visibility": "private", "ai_read": True,
        "genre": "现代诗",
    }], ensure_ascii=False), encoding="utf-8")
    R.CORPUS = corpus
    R.READS = TMP / "reads.jsonl"
    R.DATA_LOCK = TMP / ".write.lock"


def _task(n, persona):
    path = TMP / "tasks" / f"task-{n:03d}.json"
    path.write_text(json.dumps({
        "poem_id": "zq-test", "content_hash": "deadbeef",
        "reader": {"persona_id": persona, "model": None,
                   "knows_诠释": False, "knows_date": False},
        "prompt": "只用于测试",
    }, ensure_ascii=False), encoding="utf-8")


class Args:
    tasks = str(TMP / "tasks")
    inbox = str(TMP / "inbox")
    model = "test-model"
    transport = "test-local"
    context_mode = "blind"


def test_parser_accepts_transport_wrappers_but_not_damaged_content():
    _reset()
    p = TMP / "response.json"
    p.write_text('```json\n{"score": 7, "reaction": "好"}\n```', encoding="utf-8")
    assert R.parse_response_file(p)["score"] == 7
    p.write_bytes(json.dumps({"score": 8, "reaction": "中文"},
                             ensure_ascii=False).encode("gb18030"))
    assert R.parse_response_file(p)["reaction"] == "中文"
    p.write_text('{"score": 9, "reaction": "截断"', encoding="utf-8")
    try:
        R.parse_response_file(p)
        assert False, "截断 JSON 不能靠猜测修复"
    except ValueError as exc:
        assert "JSON 解析失败" in str(exc)
    print("[ok] 围栏/GB18030 无损兼容；截断内容明确拒收")


def test_collect_is_atomic_and_duplicate_safe():
    _reset()
    _task(1, "reader-a")
    _task(2, "reader-b")
    good = {"model": "model-a", "score": 7.5, "reaction": "有效回执", "long_form": None}
    (TMP / "inbox" / "task-001.response.json").write_text(
        json.dumps(good, ensure_ascii=False), encoding="utf-8")
    bad_path = TMP / "inbox" / "task-002.response.json"
    bad_path.write_text('{"model":"model-b","score":8,"reaction":"截断"', encoding="utf-8")
    try:
        R.cmd_collect(Args)
        assert False, "一份坏回执应中止整批"
    except SystemExit as exc:
        assert exc.code == 1
    assert not R.READS.exists(), "整批中止时不得写入一条读数"
    assert (TMP / "inbox" / "task-001.response.json").exists()
    assert bad_path.exists(), "坏回执必须留在 inbox 供修复"

    bad_path.write_bytes(json.dumps({
        "model": "model-b", "score": 8, "reaction": "修复后的回执", "long_form": None,
    }, ensure_ascii=False).encode("gb18030"))
    R.cmd_collect(Args)
    assert len(R.load_reads()) == 2
    archived = TMP / "inbox" / "ingested" / "task-001.response.json"
    duplicate = TMP / "inbox" / "task-001.response.json"
    duplicate.write_bytes(archived.read_bytes())
    R.cmd_collect(Args)
    assert len(R.load_reads()) == 2, "完全相同的已归档回执不能重复入库"
    assert not duplicate.exists()

    duplicate.write_text(json.dumps({**good, "reaction": "同名但不同内容"},
                                    ensure_ascii=False), encoding="utf-8")
    try:
        R.cmd_collect(Args)
        assert False, "同名冲突回执必须中止"
    except SystemExit as exc:
        assert exc.code == 1
    assert len(R.load_reads()) == 2
    assert duplicate.exists(), "冲突回执必须原地保留供人工判断"
    print("[ok] 坏回执整批零写入；归档重复跳过；同名冲突保留并中止")


if __name__ == "__main__":
    test_parser_accepts_transport_wrappers_but_not_damaged_content()
    test_collect_is_atomic_and_duplicate_safe()
    print("ALL PASS")
