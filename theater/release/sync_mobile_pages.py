# -*- coding: utf-8 -*-
"""Refresh a clean gh-pages checkout from the canonical private source tree.

The generated checkout remains a disposable artifact: this tool never commits or
pushes, and refuses unknown files, the wrong branch, or the wrong remote.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from build_mobile_pages import build_mobile_pages

OFFICIAL_REMOTE = "https://github.com/Cyanaug/zhouqingji.git"


def git_output(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or "Git 状态读取失败")
    return proc.stdout.strip()


def artifact_files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts}


def changed_files(source: Path, target: Path, expected: set[str]) -> list[str]:
    return sorted(rel for rel in expected
                  if not (target / rel).is_file()
                  or (source / rel).read_bytes() != (target / rel).read_bytes())


def sync_files(source: Path, target: Path, files: list[str]) -> None:
    for rel in files:
        src, dst = source / rel, target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".zq-pages.tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)


def validate_checkout(root: Path, expected: set[str]) -> None:
    if not (root / ".git").exists():
        raise ValueError("目标不是 Git 工作区")
    if git_output(root, "branch", "--show-current") != "gh-pages":
        raise ValueError("目标必须位于 gh-pages 分支")
    if git_output(root, "remote", "get-url", "origin").rstrip("/") != OFFICIAL_REMOTE.rstrip("/"):
        raise ValueError("目标不是官方 Cyanaug/zhouqingji 远端")
    if git_output(root, "status", "--porcelain"):
        raise ValueError("gh-pages 工作区存在未提交改动")
    unknown = artifact_files(root) - expected
    if unknown:
        raise ValueError("gh-pages 出现允许清单外文件：" + "、".join(sorted(unknown)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从唯一源码刷新 gh-pages 安卓空壳（不提交、不推送）")
    parser.add_argument("--pages-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true",
                        help="实际写入；省略时只显示计划")
    args = parser.parse_args()
    pages_root = args.pages_root.resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="zq-mobile-pages-") as td:
            built = Path(td)
            build_mobile_pages(built)
            expected = artifact_files(built)
            validate_checkout(pages_root, expected)
            plan = changed_files(built, pages_root, expected)
            print(f"安卓空壳同步计划：{len(plan)} 个文件")
            for rel in plan:
                print("  " + rel)
            if not args.apply:
                print("只读预览完成；确认后加 --apply。")
                return
            sync_files(built, pages_root, plan)
            if changed_files(built, pages_root, expected):
                raise ValueError("写入后哈希复核失败")
            print("空壳已刷新；尚未 commit 或 push。")
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
