# -*- coding: utf-8 -*-
"""Validate release receipts and optionally compare a private/public checkout."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("candidate.json")
STATUSES = {"ready", "verified", "deferred", "private"}

ROOT_FILES = {
    ".gitignore", "AGENTS.md", "CLAUDE.md", "LICENSE", "README.md", "VERSION",
    "00_START_HERE.md", "01_corpus_schema.md", "02_readers_and_casting.md",
    "03_runner_and_coverage.md", "04_app_and_design.md", "05_run_modes.md",
    "MOBILE_ACCESS.md", "PROGRESS.md",
}
PREFIXES = (
    ".agents/skills/", ".codex/agents/", ".claude/agents/", ".claude/skills/",
    ".github/workflows/", "theater/assets/", "theater/release/", "theater/src/",
    "theater/tests/", "theater/vendor/",
)
THEATER_FILES = {
    "theater/NOTES.md", "theater/check.ps1", "theater/open-theater.ps1",
    "theater/personas/personas.json", "theater/personas/personas.sidecar.example.json",
}


def allowed(path):
    posix = path.as_posix()
    if posix in ROOT_FILES or posix in THEATER_FILES:
        return True
    if posix.startswith("theater/runners/"):
        return len(path.parts) == 3 and path.suffix == ".py"
    if posix.startswith("theater/personas/"):
        return posix.endswith(".example.json")
    return any(posix.startswith(prefix) for prefix in PREFIXES)


def files_under(root):
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts
            and "__pycache__" not in p.relative_to(root).parts and p.suffix != ".pyc"
            and allowed(PurePosixPath(p.relative_to(root).as_posix()))}


def digest(path):
    return hashlib.sha256(path.read_bytes()).digest()


def candidate_changes(root, base_commit):
    """Tracked changes since the declared base plus current untracked files."""
    commands = [
        ["git", "diff", "--name-only", f"{base_commit}..HEAD"],
        ["git", "diff", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    names = set()
    for command in commands:
        proc = subprocess.run(command, cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode:
            raise ValueError(proc.stderr.strip() or "无法读取 Git 变更")
        names.update(line.strip().replace("\\", "/") for line in proc.stdout.splitlines()
                     if line.strip())
    return {name for name in names if allowed(PurePosixPath(name))}


def commit_exists(root, commit):
    proc = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode == 0


def differs_from_public(private_root, public_root, rel):
    private, public = private_root / rel, public_root / rel
    return not private.exists() or not public.exists() or digest(private) != digest(public)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--public-root", type=Path)
    ap.add_argument("--require-verified", action="store_true")
    args = ap.parse_args()

    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors = []
    version = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
    if data.get("schema") != 1:
        errors.append("candidate.json schema 必须为 1")
    if data.get("target_version") != version:
        errors.append(f"候选版本 {data.get('target_version')} 与 VERSION {version} 不一致")
    base_commit = data.get("private_base_commit")
    if not isinstance(base_commit, str) or not base_commit:
        errors.append("candidate.json 缺少 private_base_commit")

    ids, covered = set(), set()
    for index, receipt in enumerate(data.get("receipts") or []):
        rid = receipt.get("id")
        if not rid or rid in ids:
            errors.append(f"第 {index + 1} 张回执 id 缺失或重复：{rid!r}")
        ids.add(rid)
        status = receipt.get("status")
        if status not in STATUSES:
            errors.append(f"{rid}: status 无效：{status!r}")
        if args.require_verified and receipt.get("public_release") and status != "verified":
            errors.append(f"{rid}: 公开发行项尚未 verified")
        for value in receipt.get("files") or []:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or value != path.as_posix():
                errors.append(f"{rid}: 非法相对路径：{value!r}")
                continue
            if receipt.get("public_release"):
                covered.add(value)
            if status != "deferred" and not (ROOT / path).exists():
                errors.append(f"{rid}: 文件不存在：{value}")

    changed = set()
    try:
        if base_commit and commit_exists(ROOT, base_commit):
            changed = candidate_changes(ROOT, base_commit)
        elif args.public_root:
            errors.append(f"私有基准提交不存在：{base_commit}")
    except ValueError as exc:
        errors.append(str(exc))
    pending_sync = set()
    if args.public_root:
        public_root = args.public_root.resolve()
        if not (public_root / "VERSION").exists():
            errors.append("--public-root 不是有效的公开仓目录")
        else:
            pending_sync = {rel for rel in changed
                            if differs_from_public(ROOT, public_root, rel)}
            uncovered = sorted(pending_sync - covered)
            if uncovered:
                errors.append("以下待同步候选变更没有公开回执：\n  - " + "\n  - ".join(uncovered))

    if errors:
        print("候选回执检查失败：", file=sys.stderr)
        for error in errors:
            print("- " + error, file=sys.stderr)
        raise SystemExit(1)
    print(f"候选回执通过：{len(ids)} 张；登记发行文件 {len(covered)} 个；"
          f"候选变更 {len(changed)} 个；待同步 {len(pending_sync)} 个")


if __name__ == "__main__":
    main()
