# -*- coding: utf-8 -*-
"""Sync only verified public receipt files into a clean public checkout.

This does not commit, push, or infer files from a raw diff. Only verified
receipt files are copied, with an explicit one-time retirement of old internal
documents from the public tree. Published Git history is not rewritten.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import check_candidate as RC

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("candidate.json")


def public_receipt_files(data: dict, require_verified: bool = True) -> set[str]:
    files: set[str] = set()
    for receipt in data.get("receipts") or []:
        if not receipt.get("public_release"):
            continue
        if require_verified and receipt.get("status") != "verified":
            raise ValueError(f"{receipt.get('id')}: 尚未 verified，拒绝同步")
        files.update(receipt.get("files") or [])
    return files


def changed_files(source: Path, target: Path, files: set[str]) -> list[str]:
    return sorted(rel for rel in files if RC.differs_from_public(source, target, rel))


def sync_files(source: Path, target: Path, files: list[str], *, review=False) -> None:
    """Atomically replace explicit public files, sanitizing the public receipt."""
    for rel in files:
        src, dst = source / rel, target / rel
        if not src.is_file():
            raise ValueError(f"公开回执文件不存在：{rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".zq-release.tmp")
        if rel == RC.PUBLIC_MANIFEST:
            data = json.loads(src.read_text(encoding="utf-8"))
            tmp.write_bytes(RC.public_manifest_bytes(source, data, review=review))
        elif rel == ".gitignore":
            tmp.write_bytes(RC.public_gitignore_bytes(source))
        else:
            shutil.copyfile(src, tmp)
        os.replace(tmp, dst)


def retire_internal_documents(target: Path, names: set[str]) -> list[str]:
    """Remove only named, tracked-in-the-old-release documents after preflight."""
    removed = []
    root = target.resolve()
    for rel in sorted(names):
        path = target / rel
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f"内部文档目标无效，拒绝移除：{rel}")
        path.unlink()
        removed.append(rel)
    return removed


def git_status(root: Path) -> str:
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root),
         "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or "无法读取公开仓状态")
    return proc.stdout.strip()


def validate_receipts(manifest: Path, *, review=False) -> None:
    command = [sys.executable, str(RC.__file__), "--manifest", str(manifest)]
    if not review:
        command.append("--require-verified")
    proc = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if proc.returncode:
        raise ValueError((proc.stdout + proc.stderr).strip())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按 verified 发行回执同步公开仓（不提交、不推送）")
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--apply", action="store_true",
                        help="实际复制；省略时只显示计划")
    parser.add_argument("--review-output", type=Path,
                        help="克隆公开仓到全新目录并生成本地验收副本；不改公开仓、不标记 verified")
    args = parser.parse_args()
    public_root = args.public_root.resolve()
    manifest = args.manifest.resolve()
    if args.review_output and args.apply:
        raise SystemExit("验收副本与正式 --apply 不能一起使用")
    if not (public_root / ".git").exists() or not (public_root / "VERSION").is_file():
        raise SystemExit("目标不是有效的公开 Git 仓库")
    if git_status(public_root):
        raise SystemExit("公开仓存在未提交改动；为防覆盖，拒绝同步")
    try:
        if args.review_output:
            output = args.review_output.resolve()
            if public_root == ROOT or output.is_relative_to(public_root):
                raise ValueError("验收副本必须基于独立公开仓，且输出目录不能放进公开仓")
            if output.exists():
                raise ValueError("验收输出目录必须不存在，拒绝覆盖")
            validate_receipts(manifest, review=True)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            files = public_receipt_files(data, require_verified=False)
            if any(not RC.allowed(Path(rel)) for rel in files):
                raise ValueError("验收文件包含公开允许清单外路径")
            clone = subprocess.run(["git", "clone", "--local", "--no-hardlinks", "--single-branch",
                                    "--no-tags", str(public_root), str(output)],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
            if clone.returncode:
                raise ValueError(clone.stderr.strip() or "无法建立验收副本")
            # The local clone is never a deployment checkout. Remove its push destination.
            subprocess.run(["git", "-C", str(output), "remote", "remove", "origin"], check=True)
            sync_files(ROOT, output, sorted(files), review=True)
            removed = retire_internal_documents(output, RC.PUBLIC_RETIRE_FILES)
            print(f"本地验收副本已生成：{len(files)} 个公开文件，收起 {len(removed)} 份内部文档。")
            print("清单保留原 ready 状态并标记 review_only；未提交、未推送，正式仓未改动。")
            return
        validate_receipts(manifest)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        files = public_receipt_files(data)
        changed = RC.candidate_changes(ROOT, data["private_base_commit"])
        pending, leaked = RC.public_sync_state(ROOT, public_root, changed, files)
        if leaked:
            raise ValueError("公开仓已出现私有/延期文件的当前副本：" + "、".join(sorted(leaked)))
        plan = changed_files(ROOT, public_root, files)
        retire = sorted(rel for rel in RC.PUBLIC_RETIRE_FILES if (public_root / rel).is_file())
        print(f"公开同步计划：{len(plan)} 个文件，收起 {len(retire)} 份内部文档")
        for rel in plan:
            print("  " + rel)
        for rel in retire:
            print("  收起 " + rel)
        if not args.apply:
            print("只读预览完成；确认后加 --apply。")
            return
        sync_files(ROOT, public_root, plan)
        removed = retire_internal_documents(public_root, set(retire))
        pending_after, leaked_after = RC.public_sync_state(ROOT, public_root, changed, files)
        if pending_after or leaked_after or any((public_root / rel).exists() for rel in RC.PUBLIC_RETIRE_FILES):
            raise ValueError("同步后复核未通过")
        print(f"同步完成，收起 {len(removed)} 份旧内部文档；Git 历史仍可恢复。尚未 commit 或 push。")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
