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
SCHEMAS = {1, 2}
# 回执文件本身要在验证后写入指纹，因此只把它的路径、而非它自己的内容纳入指纹；
# 其余公开文件的路径和字节全部参与计算。
FINGERPRINT_CONTENT_EXCLUSIONS = {"theater/release/candidate.json"}
PUBLIC_MANIFEST = "theater/release/candidate.json"
# These are internal work records, not user documentation. Existing public copies
# are retired from the current tree during the next release; published tags remain immutable.
PUBLIC_RETIRE_FILES = {
    "PROGRESS.md", "theater/NOTES.md",
    "theater/release/README.md",
    "theater/release/archive/v1.7.1.json",
    "theater/release/roadmap-v1.8-author-threads.md",
}
PRIVATE_ONLY_PREFIXES = ("theater/handoffs/", "theater/release/roadmap-",
                         "theater/release/archive/")

ROOT_FILES = {
    ".gitignore", "AGENTS.md", "CLAUDE.md", "LICENSE", "README.md", "VERSION",
    "00_START_HERE.md", "01_corpus_schema.md", "02_readers_and_casting.md",
    "03_runner_and_coverage.md", "04_app_and_design.md", "05_run_modes.md",
    "MOBILE_ACCESS.md",
}
PREFIXES = (
    ".agents/skills/", ".codex/agents/", ".claude/agents/", ".claude/skills/",
    ".github/workflows/", "theater/assets/", "theater/src/",
    "theater/tests/", "theater/tools/", "theater/vendor/",
)
THEATER_FILES = {
    "theater/BOOK_PDF.md", "theater/check.ps1", "theater/open-theater.ps1",
    "theater/personas/personas.json", "theater/personas/personas.sidecar.example.json",
}
PUBLIC_RELEASE_FILES = {
    "theater/release/build_mobile_pages.py",
    "theater/release/candidate.json",
    "theater/release/check_candidate.py",
    "theater/release/sync_mobile_pages.py",
    "theater/release/sync_public_release.py",
}


def allowed(path):
    posix = path.as_posix()
    if posix in PUBLIC_RETIRE_FILES or posix.startswith(PRIVATE_ONLY_PREFIXES):
        return False
    if posix.startswith("theater/release/"):
        return posix in PUBLIC_RELEASE_FILES
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


def unexpected_public_files(root):
    """Inspect the current tracked tree; deleted old files are not current leaks."""
    proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                          capture_output=True)
    if proc.returncode:
        raise ValueError("无法读取公开仓的跟踪文件名单")
    names = (name.decode("utf-8", errors="replace") for name in proc.stdout.split(b"\0")
             if name)
    return sorted(name for name in names if (root / name).exists()
                  and not allowed(PurePosixPath(name)))


def canonical_file_bytes(path):
    """Git may check out text as CRLF on Windows and LF on other systems."""
    data = path.read_bytes()
    if path.suffix.lower() in {".md", ".py", ".js", ".json", ".toml", ".ps1", ".yml",
                               ".yaml", ".txt", ".html", ".css", ".webmanifest", ".svg"} or path.name in {".gitignore", "VERSION", "LICENSE"}:
        return data.replace(b"\r\n", b"\n")
    return data


def digest(path):
    return hashlib.sha256(canonical_file_bytes(path)).digest()


def public_gitignore_bytes(root):
    """Protect user data in public installs without ignoring private development data."""
    lines = (root / ".gitignore").read_text(encoding="utf-8-sig").splitlines()
    for rule in ("/corpus/", "/results/", "/batches/", "/FORK_REPORT.md", "/SANITIZATION_REPORT.md"):
        if rule not in lines:
            lines.append(rule)
    return ("\n".join(lines) + "\n").encode("utf-8")


def receipt_fingerprint(root, receipt):
    """Return a stable digest for the exact public file set covered by a receipt."""
    checksum = hashlib.sha256()
    for value in sorted(receipt.get("files") or []):
        checksum.update(value.encode("utf-8"))
        checksum.update(b"\0")
        if value not in FINGERPRINT_CONTENT_EXCLUSIONS:
            path = root / PurePosixPath(value)
            checksum.update(public_gitignore_bytes(root) if value == ".gitignore" and path.is_file()
                            else canonical_file_bytes(path) if path.is_file() else b"<missing>")
        checksum.update(b"\0")
    return checksum.hexdigest()


def public_manifest_bytes(private_root, data, *, review=False):
    """Publish only the verified public inventory, never private notes/receipts."""
    receipts = []
    for receipt in data.get("receipts") or []:
        if not receipt.get("public_release"):
            continue
        if receipt.get("status") != "verified" and not review:
            raise ValueError("公开回执尚未 verified，不能生成公开清单")
        files = sorted(set(receipt.get("files") or []))
        if any(not allowed(PurePosixPath(name)) for name in files):
            raise ValueError("公开回执含内部文件，不能生成公开清单")
        public_receipt = {
            "id": receipt["id"], "title": receipt["title"],
            "status": receipt["status"], "public_release": True,
            "files": files,
        }
        public_receipt["verification_sha256"] = receipt_fingerprint(private_root, public_receipt)
        receipts.append(public_receipt)
    public = {"schema": 2, "audience": "public", "target_version": data["target_version"],
              "receipts": receipts}
    if review:
        public["review_only"] = True
    return (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def candidate_changes(root, base_commit):
    """Tracked changes since the declared base plus current untracked files."""
    commands = [
        ["git", "diff", "--name-only", f"{base_commit}..HEAD"],
        ["git", "diff", "--cached", "--name-only"],
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
    if rel == ".gitignore":
        return not private.is_file() or not public.is_file() or public_gitignore_bytes(private_root) != canonical_file_bytes(public)
    if rel == PUBLIC_MANIFEST:
        if not private.is_file() or not public.is_file():
            return True
        data = json.loads(private.read_text(encoding="utf-8"))
        if all(r.get("status") == "verified" for r in data.get("receipts") or []
               if r.get("public_release")):
            return public_manifest_bytes(private_root, data) != public.read_bytes()
        return True
    return not private.exists() or not public.exists() or digest(private) != digest(public)


def public_sync_state(private_root, public_root, changed, covered):
    """Return missing public receipt files and suspicious exact private copies."""
    pending = {rel for rel in changed & covered
               if differs_from_public(private_root, public_root, rel)}
    leaked_private = set()
    for rel in changed - covered:
        private, public = private_root / rel, public_root / rel
        if private.is_file() and public.is_file() and digest(private) == digest(public):
            leaked_private.add(rel)
    return pending, leaked_private


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--public-root", type=Path)
    ap.add_argument("--require-verified", action="store_true")
    args = ap.parse_args()

    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors = []
    if args.require_verified and data.get("review_only"):
        errors.append("这是本地验收副本，不得作为正式发行清单")
    version = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
    schema = data.get("schema")
    if schema not in SCHEMAS:
        errors.append(f"candidate.json schema 必须是 {sorted(SCHEMAS)} 之一")
    if data.get("target_version") != version:
        errors.append(f"候选版本 {data.get('target_version')} 与 VERSION {version} 不一致")
    audience = data.get("audience", "private")
    if audience not in ("private", "public"):
        errors.append("候选清单 audience 无效")
    base_commit = data.get("private_base_commit")
    if audience == "private" and (not isinstance(base_commit, str) or not base_commit):
        errors.append("candidate.json 缺少 private_base_commit")

    ids, accounted, covered = set(), set(), set()
    for index, receipt in enumerate(data.get("receipts") or []):
        rid = receipt.get("id")
        if not rid or rid in ids:
            errors.append(f"第 {index + 1} 张回执 id 缺失或重复：{rid!r}")
        ids.add(rid)
        status = receipt.get("status")
        if status not in STATUSES:
            errors.append(f"{rid}: status 无效：{status!r}")
        if status == "private" and receipt.get("public_release"):
            errors.append(f"{rid}: private 回执不能同时标记 public_release=true")
        if audience == "public" and not receipt.get("public_release"):
            errors.append(f"{rid}: 公开清单不能包含私有回执")
        if args.require_verified and receipt.get("public_release") and status != "verified":
            errors.append(f"{rid}: 公开发行项尚未 verified")
        for value in receipt.get("files") or []:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or value != path.as_posix():
                errors.append(f"{rid}: 非法相对路径：{value!r}")
                continue
            if receipt.get("public_release"):
                if not allowed(path):
                    errors.append(f"{rid}: 内部文件不得列入公开发行：{value}")
                covered.add(value)
            accounted.add(value)
            if status != "deferred" and not (ROOT / path).exists():
                errors.append(f"{rid}: 文件不存在：{value}")
        if schema == 2 and receipt.get("public_release") and status == "verified":
            expected = receipt.get("verification_sha256")
            actual = receipt_fingerprint(ROOT, receipt)
            if not isinstance(expected, str) or len(expected) != 64:
                errors.append(f"{rid}: verified 回执缺少有效 verification_sha256")
            elif expected.lower() != actual:
                errors.append(f"{rid}: 验证指纹已过期；公开文件在验证后发生了变化")

    changed = set()
    try:
        if audience == "private" and base_commit and commit_exists(ROOT, base_commit):
            changed = candidate_changes(ROOT, base_commit)
        elif audience == "private" and args.public_root:
            errors.append(f"私有基准提交不存在：{base_commit}")
    except ValueError as exc:
        errors.append(str(exc))
    unaccounted = sorted(changed - accounted)
    if unaccounted:
        errors.append("以下候选变更没有任何回执（公开/私有/延期均未声明）：\n  - " +
                      "\n  - ".join(unaccounted))
    pending_sync, leaked_private = set(), set()
    if args.public_root:
        public_root = args.public_root.resolve()
        if not (public_root / "VERSION").exists():
            errors.append("--public-root 不是有效的公开仓目录")
        else:
            pending_sync, leaked_private = public_sync_state(
                ROOT, public_root, changed, covered)
            if args.require_verified and pending_sync:
                errors.append("以下已验证公开文件尚未同步：\n  - " +
                              "\n  - ".join(sorted(pending_sync)))
            if args.require_verified:
                try:
                    unexpected = unexpected_public_files(public_root)
                    if unexpected:
                        errors.append("以下文件不在公开允许清单中：\n  - " +
                                      "\n  - ".join(unexpected))
                except ValueError as exc:
                    errors.append(str(exc))
            if leaked_private:
                errors.append("以下私有/延期文件疑似被原样复制进公开仓：\n  - " +
                              "\n  - ".join(sorted(leaked_private)))

    if (audience == "public" and args.require_verified
            and args.manifest.resolve() == DEFAULT_MANIFEST.resolve()):
        try:
            unexpected = unexpected_public_files(ROOT)
            if unexpected:
                errors.append("以下文件不在公开允许清单中：\n  - " +
                              "\n  - ".join(unexpected))
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        print("候选回执检查失败：", file=sys.stderr)
        for error in errors:
            print("- " + error, file=sys.stderr)
        raise SystemExit(1)
    private_or_deferred = accounted - covered
    print(f"候选回执通过：{len(ids)} 张；候选变更 {len(changed)} 个；"
          f"公开文件 {len(covered)} 个；私有/延期文件 {len(private_or_deferred)} 个；"
          f"待同步 {len(pending_sync)} 个")


if __name__ == "__main__":
    main()
