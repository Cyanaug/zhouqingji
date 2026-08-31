# -*- coding: utf-8 -*-
"""版本候选回执必须覆盖每个改动，并把私有项与公开项分开。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "theater" / "release" / "check_candidate.py"
CANDIDATE = ROOT / "theater" / "release" / "candidate.json"
sys.path.insert(0, str(CHECKER.parent))
import check_candidate as RC  # noqa: E402


def run_manifest(data):
    with tempfile.TemporaryDirectory(prefix="zq-release-receipt-") as td:
        manifest = Path(td) / "candidate.json"
        manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(CHECKER), "--manifest", str(manifest), "--require-verified"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_current_manifest_passes():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    proc = run_manifest(data)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    print("[ok] 当前候选回执覆盖全部允许清单变更")


def test_unaccounted_change_fails():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    if not RC.commit_exists(ROOT, data.get("private_base_commit", "")):
        print("[skip] 公开脱敏历史不含私有候选基准；未归类改动反向测试只在私有仓运行")
        return
    for receipt in data["receipts"]:
        receipt["files"] = [p for p in receipt.get("files", [])
                            if p != "theater/src/webapp/app.js"]
    proc = run_manifest(data)
    assert proc.returncode != 0
    assert "theater/src/webapp/app.js" in proc.stderr
    print("[ok] 任一未归类改动都会阻止发行")


def test_private_receipt_cannot_claim_public_release():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    private = next(r for r in data["receipts"] if r["status"] == "private")
    private["public_release"] = True
    proc = run_manifest(data)
    assert proc.returncode != 0
    assert "private 回执不能同时标记 public_release=true" in proc.stderr
    print("[ok] 私有回执不能误标为公开同步")


def test_staged_change_is_not_invisible():
    """多 AI 交接最危险的中间态：已 git add、尚未 commit，也必须被看见。"""
    with tempfile.TemporaryDirectory(prefix="zq-release-git-") as td:
        repo = Path(td)
        commands = [
            ["git", "init", "-q"],
            ["git", "config", "user.email", "release-test@users.noreply.github.com"],
            ["git", "config", "user.name", "Release Test"],
        ]
        for command in commands:
            subprocess.run(command, cwd=repo, check=True, capture_output=True)
        readme = repo / "README.md"
        readme.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                       text=True).strip()
        readme.write_text("staged but not committed\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        assert "README.md" in RC.candidate_changes(repo, base)
    print("[ok] 已暂存未提交的改动不会从回执审计消失")


if __name__ == "__main__":
    test_current_manifest_passes()
    test_unaccounted_change_fails()
    test_private_receipt_cannot_claim_public_release()
    test_staged_change_is_not_invisible()
    print("ALL PASS")
