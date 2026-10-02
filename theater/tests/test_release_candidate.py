# -*- coding: utf-8 -*-
"""版本候选回执必须覆盖每个改动，并把私有项与公开项分开。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "theater" / "release" / "check_candidate.py"
CANDIDATE = ROOT / "theater" / "release" / "candidate.json"
sys.path.insert(0, str(CHECKER.parent))
import check_candidate as RC  # noqa: E402
import sync_public_release as RS  # noqa: E402


def run_manifest(data, require_verified=True):
    with tempfile.TemporaryDirectory(prefix="zq-release-receipt-") as td:
        manifest = Path(td) / "candidate.json"
        manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        command = [sys.executable, str(CHECKER), "--manifest", str(manifest)]
        if require_verified:
            command.append("--require-verified")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            command,
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def test_current_manifest_passes():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    proc = run_manifest(data, require_verified=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    print("[ok] 当前开发候选回执覆盖全部允许清单变更")


def test_ready_change_cannot_release():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    public = next(r for r in data["receipts"] if r.get("public_release"))
    public["status"] = "ready"
    public.pop("verification_sha256", None)
    proc = run_manifest(data)
    assert proc.returncode != 0
    assert "公开发行项尚未 verified" in proc.stderr
    print("[ok] ready 开发回执不能越过正式发行门")


def test_stale_verified_fingerprint_fails():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    public = next(r for r in data["receipts"] if r.get("public_release"))
    public["status"] = "verified"
    public["verification_sha256"] = "0" * 64
    proc = run_manifest(data)
    assert proc.returncode != 0
    assert "验证指纹已过期" in proc.stderr
    print("[ok] 后续 AI 改过公开文件会自动作废旧 verified 回执")


def test_current_files_can_receive_fresh_fingerprint():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    # Simulate a future formal receipt in a temporary manifest; the real review
    # manifest on disk remains blocked by its independent release test below.
    data.pop("review_only", None)
    for public in (r for r in data["receipts"] if r.get("public_release")):
        public["status"] = "verified"
        public["verification_sha256"] = RC.receipt_fingerprint(ROOT, public)
    proc = run_manifest(data)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    print("[ok] 当前公开文件集合可生成并验证稳定指纹")


def test_unaccounted_change_fails():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    if not RC.commit_exists(ROOT, data.get("private_base_commit", "")):
        print("[skip] 公开脱敏历史不含私有候选基准；未归类改动反向测试只在私有仓运行")
        return
    for receipt in data["receipts"]:
        receipt["files"] = [p for p in receipt.get("files", [])
                            if p != "theater/src/webapp/app.js"]
    proc = run_manifest(data, require_verified=False)
    assert proc.returncode != 0
    assert "theater/src/webapp/app.js" in proc.stderr
    print("[ok] 任一未归类改动都会阻止发行")


def test_private_receipt_cannot_claim_public_release():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    private = next((r for r in data["receipts"] if r["status"] == "private"), None)
    if private is None:
        private = {"id": "synthetic-private", "status": "private", "files": []}
        data["receipts"].append(private)
    private["public_release"] = True
    proc = run_manifest(data, require_verified=False)
    assert proc.returncode != 0
    assert "private 回执不能同时标记 public_release=true" in proc.stderr
    print("[ok] 私有回执不能误标为公开同步")


def test_public_manifest_excludes_internal_work():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    for receipt in (r for r in data["receipts"] if r.get("public_release")):
        receipt["status"] = "verified"
        receipt["verification_sha256"] = RC.receipt_fingerprint(ROOT, receipt)
    rendered = RC.public_manifest_bytes(ROOT, data)
    public = json.loads(rendered)
    assert public["audience"] == "public"
    assert "private_base_commit" not in public and "development_track" not in public
    assert all(r["public_release"] and "notes" not in r and "tests" not in r
               for r in public["receipts"])
    assert b"private-ledgers-next" not in rendered and b"theater/NOTES.md" not in rendered
    proc = run_manifest(public)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    print("[ok] 公开回执只保留公开文件和指纹，不带内部日志与私有工作清单")


def test_internal_document_cannot_enter_public_receipt():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    public = next(r for r in data["receipts"] if r.get("public_release"))
    public["files"].append("PROGRESS.md")
    proc = run_manifest(data, require_verified=False)
    assert proc.returncode != 0 and "内部文件不得列入公开发行" in proc.stderr
    print("[ok] 内部日志不能被公开回执意外纳入")


def test_public_check_includes_sanitization():
    check = (ROOT / "theater/check.ps1").read_text(encoding="utf-8")
    assert "test_public_sanitization.py" in check
    assert "Public sanitization check failed" in check
    print("[ok] 公开仓完整检查会阻断当前树与历史的隐私问题")


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


def test_public_sync_state_catches_omission_and_private_copy():
    with tempfile.TemporaryDirectory(prefix="zq-release-private-") as private_td, \
            tempfile.TemporaryDirectory(prefix="zq-release-public-") as public_td:
        private, public = Path(private_td), Path(public_td)
        for root in (private, public):
            (root / "theater").mkdir()
        (private / "README.md").write_text("new public\n", encoding="utf-8")
        (public / "README.md").write_text("old public\n", encoding="utf-8")
        (private / "theater" / "NOTES.md").write_text("private notes\n", encoding="utf-8")
        (public / "theater" / "NOTES.md").write_text("private notes\n", encoding="utf-8")
        pending, leaked = RC.public_sync_state(
            private, public, {"README.md", "theater/NOTES.md"}, {"README.md"})
        assert pending == {"README.md"}
        assert leaked == {"theater/NOTES.md"}
    print("[ok] 正式闸门同时抓住公开漏同步与私有文件误同步")


def test_receipt_driven_sync_copies_nothing_else():
    with tempfile.TemporaryDirectory(prefix="zq-sync-source-") as source_td, \
            tempfile.TemporaryDirectory(prefix="zq-sync-target-") as target_td:
        source, target = Path(source_td), Path(target_td)
        (source / "theater").mkdir()
        (target / "theater").mkdir()
        (source / "README.md").write_text("public new\n", encoding="utf-8")
        (source / "theater" / "NOTES.md").write_text("private\n", encoding="utf-8")
        (target / "README.md").write_text("public old\n", encoding="utf-8")
        (target / "theater" / "NOTES.md").write_text("public-safe-old\n", encoding="utf-8")
        RS.sync_files(source, target, ["README.md"])
        assert (target / "README.md").read_text(encoding="utf-8") == "public new\n"
        assert (target / "theater" / "NOTES.md").read_text(encoding="utf-8") == "public-safe-old\n"
    print("[ok] 同步器只复制 verified 回执名单，不根据整树 diff 猜文件")


def test_explicit_internal_doc_retirement():
    with tempfile.TemporaryDirectory(prefix="zq-release-retire-") as td:
        root = Path(td)
        (root / "theater").mkdir()
        (root / "PROGRESS.md").write_text("old public summary", encoding="utf-8")
        (root / "theater" / "NOTES.md").write_text("old public notes", encoding="utf-8")
        (root / "theater" / "release" / "archive").mkdir(parents=True)
        (root / "theater" / "release" / "README.md").write_text(
            "old internal release guide", encoding="utf-8")
        (root / "theater" / "release" / "archive" / "v1.7.1.json").write_text(
            "old receipt", encoding="utf-8")
        (root / "README.md").write_text("keep", encoding="utf-8")
        removed = RS.retire_internal_documents(root, RC.PUBLIC_RETIRE_FILES)
        assert removed == ["PROGRESS.md", "theater/NOTES.md",
                           "theater/release/README.md",
                           "theater/release/archive/v1.7.1.json"]
        assert (root / "README.md").read_text(encoding="utf-8") == "keep"
    print("[ok] 只收起指定旧内部文档，不删除普通公开说明")


def test_release_directory_is_exact_allowlist():
    assert RC.allowed(PurePosixPath("theater/release/check_candidate.py"))
    assert RC.allowed(PurePosixPath("theater/release/sync_public_release.py"))
    assert not RC.allowed(PurePosixPath("theater/release/README.md"))
    assert not RC.allowed(PurePosixPath("theater/release/archive/v1.7.1.json"))
    assert not RC.allowed(PurePosixPath("theater/release/future-internal-log.md"))


def test_public_tree_rejects_unlisted_tracked_files():
    with tempfile.TemporaryDirectory(prefix="zq-public-tree-") as td:
        root = Path(td)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "README.md").write_text("user guide", encoding="utf-8")
        (root / "theater/release").mkdir(parents=True)
        internal = root / "theater/release/future-internal-log.md"
        internal.write_text("internal", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "README.md",
                        "theater/release/future-internal-log.md"], check=True)
        assert RC.unexpected_public_files(root) == ["theater/release/future-internal-log.md"]
        internal.unlink()
        assert RC.unexpected_public_files(root) == []
    print("[ok] 公开当前树阻断未列入允许清单的内部文件")


def test_public_onboarding_does_not_require_private_logs():
    for name in ("README.md", "00_START_HERE.md", "03_runner_and_coverage.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "PROGRESS.md" not in text, name
        assert "theater/NOTES.md" not in text, name
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "公开发行包不附这两份内部记录" in agents
    print("[ok] 公开入门文档不依赖私有进度与设计日志")


def test_fingerprint_ignores_checkout_line_endings_but_not_binary_changes():
    with tempfile.TemporaryDirectory(prefix="zq-release-eol-") as td:
        source, target = Path(td) / "source", Path(td) / "target"
        source.mkdir()
        target.mkdir()
        (source / "app.js").write_bytes(b"const a = 1;\r\nconst b = 2;\r\n")
        (target / "app.js").write_bytes(b"const a = 1;\nconst b = 2;\n")
        receipt = {"files": ["app.js"]}
        assert RC.receipt_fingerprint(source, receipt) == RC.receipt_fingerprint(target, receipt)
        assert not RC.differs_from_public(source, target, "app.js")
        (target / "app.js").write_bytes(b"const a = 3;\nconst b = 2;\n")
        assert RC.receipt_fingerprint(source, receipt) != RC.receipt_fingerprint(target, receipt)
        (source / "font.otf").write_bytes(b"OTTO\r\n")
        (target / "font.otf").write_bytes(b"OTTO\n")
        assert RC.differs_from_public(source, target, "font.otf")
        rel = RC.PUBLIC_MANIFEST
        (source / rel).parent.mkdir(parents=True)
        (target / rel).parent.mkdir(parents=True)
        data = {"schema": 2, "target_version": "1.8.1", "receipts": [
            {"id": "eol", "title": "EOL test", "status": "verified", "public_release": True,
             "files": [rel]}]}
        (source / rel).write_text(json.dumps(data), encoding="utf-8")
        public = RC.public_manifest_bytes(source, data)
        (target / rel).write_bytes(public.replace(b"\n", b"\r\n"))
        assert not RC.differs_from_public(source, target, rel)
    print("[ok] Git 检出换行不使文本指纹失效，真实文字与二进制改动仍会失效")


def test_public_ignore_is_generated_and_fingerprint_survives_sync():
    with tempfile.TemporaryDirectory(prefix="zq-public-ignore-") as td:
        source, target = Path(td) / "source", Path(td) / "target"
        source.mkdir()
        target.mkdir()
        original = b"# development\nnode_modules/\n"
        (source / ".gitignore").write_bytes(original)
        RS.sync_files(source, target, [".gitignore"])
        published = (target / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert all(rule in published for rule in ("/corpus/", "/results/", "/batches/"))
        assert (source / ".gitignore").read_bytes() == original
        receipt = {"files": [".gitignore"]}
        assert RC.receipt_fingerprint(source, receipt) == RC.receipt_fingerprint(target, receipt)
        assert not RC.differs_from_public(source, target, ".gitignore")
        assert RC.public_gitignore_bytes(target) == (target / ".gitignore").read_bytes()
    print("[ok] 公开忽略规则保护私人目录，生成幂等且验证指纹一致")


def test_review_manifest_cannot_pass_release_gate():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    for receipt in data["receipts"]:
        if receipt.get("public_release"):
            receipt["status"] = "ready"
    public = json.loads(RC.public_manifest_bytes(ROOT, data, review=True))
    assert public["review_only"] is True
    assert all(r["status"] == "ready" for r in public["receipts"])
    assert "private_base_commit" not in public
    # Even manually turning every row green must not turn a review copy into a release.
    for receipt in public["receipts"]:
        receipt["status"] = "verified"
    proc = run_manifest(public)
    assert proc.returncode != 0
    assert "本地验收副本" in proc.stderr
    print("[ok] 验收清单不伪造 verified，正式闸门拒绝 review_only")


def test_sync_writes_sanitized_public_manifest():
    data = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    for receipt in (r for r in data["receipts"] if r.get("public_release")):
        receipt["status"] = "verified"
        receipt["verification_sha256"] = RC.receipt_fingerprint(ROOT, receipt)
    with tempfile.TemporaryDirectory(prefix="zq-public-manifest-") as td:
        source, target = Path(td) / "private", Path(td) / "public"
        (source / "theater/release").mkdir(parents=True)
        target.mkdir()
        private_manifest = source / RC.PUBLIC_MANIFEST
        private_manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        RS.sync_files(source, target, [RC.PUBLIC_MANIFEST])
        published = (target / RC.PUBLIC_MANIFEST).read_bytes()
        assert published == RC.public_manifest_bytes(source, data)
        assert not RC.differs_from_public(source, target, RC.PUBLIC_MANIFEST)
        assert b"private-ledgers-next" not in published
    print("[ok] 同步时实际生成脱敏公开回执，不复制私有候选原文")


if __name__ == "__main__":
    test_current_manifest_passes()
    test_ready_change_cannot_release()
    test_stale_verified_fingerprint_fails()
    test_current_files_can_receive_fresh_fingerprint()
    test_unaccounted_change_fails()
    test_private_receipt_cannot_claim_public_release()
    test_public_manifest_excludes_internal_work()
    test_internal_document_cannot_enter_public_receipt()
    test_public_check_includes_sanitization()
    test_staged_change_is_not_invisible()
    test_public_sync_state_catches_omission_and_private_copy()
    test_receipt_driven_sync_copies_nothing_else()
    test_explicit_internal_doc_retirement()
    test_release_directory_is_exact_allowlist()
    test_public_tree_rejects_unlisted_tracked_files()
    test_public_onboarding_does_not_require_private_logs()
    test_sync_writes_sanitized_public_manifest()
    test_public_ignore_is_generated_and_fingerprint_survives_sync()
    test_review_manifest_cannot_pass_release_gate()
    test_fingerprint_ignores_checkout_line_endings_but_not_binary_changes()
    print("ALL PASS")
