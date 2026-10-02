# -*- coding: utf-8 -*-
"""Build a deterministic, privacy-minimized GitHub Pages mobile shell.

This tool never performs network access or pushes Git.  It only assembles the exact
public files that may be committed to the independent ``gh-pages`` branch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEBAPP = ROOT / "theater" / "src" / "webapp"

SHELL_FILES = (
    "mobile.html",
    "mobile.webmanifest",
    "mobile-sw.js",
    "mobile-shell.json",
    "app.js",
    "style.css",
    "favicon.png",
    "apple-touch-icon.png",
    "icon-192.png",
    "icon-512.png",
)
TEXT_SUFFIXES = {".html", ".webmanifest", ".js", ".json", ".css"}
PRIVATE_PATTERNS = {
    "embedded snapshot": re.compile(r"window\.__ZQ_SNAPSHOT__\s*="),
    "Windows absolute path": re.compile(r"(?:[A-Za-z]:\\|C:/Users/|X:/)"),
    "literal private IPv4": re.compile(
        r"(?:192\.168|10(?:\.\d{1,3}){0}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}"
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_empty_target(target: Path) -> None:
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"输出目录必须不存在或为空：{target}")
    target.mkdir(parents=True, exist_ok=True)


def _privacy_scan(paths: list[Path]) -> None:
    problems = []
    for path in paths:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in PRIVATE_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{path.name}: {label}")
    if problems:
        raise ValueError("Pages 空壳隐私扫描失败：" + "；".join(problems))


def build_mobile_pages(target: Path, source: Path = WEBAPP) -> dict:
    target, source = Path(target), Path(source)
    _ensure_empty_target(target)
    copied = []
    for name in SHELL_FILES:
        src = source / name
        if not src.is_file():
            raise ValueError(f"缺少移动空壳文件：{name}")
        dst = target / name
        if src.suffix.lower() in TEXT_SUFFIXES:
            # GitHub Pages 从 Git blob 提供 LF。构建阶段就统一换行，确保部署清单
            # 的字节哈希能和线上文件直接比较，不再产生 Windows CRLF 假差异。
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(src, dst)
        copied.append(dst)
    shutil.copyfile(target / "mobile.html", target / "index.html")
    copied.append(target / "index.html")
    (target / ".nojekyll").touch()
    copied.append(target / ".nojekyll")
    attributes = target / ".gitattributes"
    attributes.write_text("* text=auto eol=lf\n*.png -text\n", encoding="utf-8", newline="\n")
    copied.append(attributes)
    _privacy_scan(copied)

    public_files = sorted(copied, key=lambda path: path.name)
    manifest = {
        "schema": 1,
        "kind": "zhouqingji-mobile-pages-deployment",
        "total_bytes": sum(path.stat().st_size for path in public_files),
        "files": {path.name: sha256(path) for path in public_files},
    }
    manifest_path = target / "deployment-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="组装不含私人数据的安卓 Pages 空壳")
    parser.add_argument("--output", type=Path, required=True,
                        help="必须不存在或为空的输出目录")
    args = parser.parse_args()
    result = build_mobile_pages(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
