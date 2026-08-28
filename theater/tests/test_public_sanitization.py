# -*- coding: utf-8 -*-
"""公开发行树的隐私阻断检查；不扫描用户自己的 corpus/results/batches。"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {".md", ".py", ".js", ".json", ".toml", ".ps1", ".yml", ".yaml", ".txt"}
FORBIDDEN_TRACKED_PREFIXES = ("corpus/", "results/", "batches/")

EXACT_MODEL_ID = re.compile(
    r"\b(?:claude|gemini|deepseek|gpt|glm|qwen|grok|kimi|minimax)[-_ ]"
    r"(?:[a-z]+[-_ ])?v?\d+(?:[.\-_]\d+)*(?:[-_][a-z0-9]+)*\b",
    re.IGNORECASE,
)
PRIVATE_PATH = re.compile(r"C:\\Users\\|[DXYZ]:\\", re.IGNORECASE)
PRIVATE_EMAIL = re.compile(
    r"\b(?!git@)[A-Z0-9._%+-]+@(?!users\.noreply\.github\.com\b)"
    r"[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
SECRET_SHAPE = re.compile(
    r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----",
)

# 仓库所有者名只允许用于固定更新源及其测试；不得扩散到作者署名、
# 示例作品或其他产品文案。若未来迁移到组织账号，只需替换这两个位置。
REPOSITORY_OWNER = re.compile(r"\bCyanaug\b", re.IGNORECASE)
REPOSITORY_OWNER_ALLOWED = {
    "theater/src/server.py",
    "theater/tests/test_update.py",
}

# v1.6 已经公开，保留它作为兼容更新基线；从 v1.6.1 起，每个新增提交
# 都必须通过历史脱敏检查。更早的游离对象由 GitHub Support 单独清理。
LEGACY_PUBLIC_BASELINE = "6402bc78d02a685eba17838518dc9cfc1b4c62af"


def _tracked_files():
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT),
         "ls-files", "-z", "--cached", "--others",
         "--exclude-standard"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode("utf-8").split("\0") if p]


def _git(args):
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT), *args],
        capture_output=True,
        check=False,
    )


def test_public_tree_is_sanitized():
    tracked = _tracked_files()
    assert tracked is not None, "公开发行检查必须在 Git checkout 中运行"
    forbidden = [p for p in tracked if p.startswith(FORBIDDEN_TRACKED_PREFIXES)]
    assert not forbidden, f"公开仓跟踪了私人目录：{forbidden[:5]}"

    findings = []
    owner_paths = set()
    for rel in tracked:
        path = ROOT / rel
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if REPOSITORY_OWNER.search(text):
            owner_paths.add(rel)
        for label, pattern in (
            ("精确模型 ID", EXACT_MODEL_ID),
            ("私人绝对路径", PRIVATE_PATH),
            ("非 noreply 邮箱", PRIVATE_EMAIL),
            ("疑似凭据", SECRET_SHAPE),
        ):
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{rel}:{line} {label}")
    for rel in sorted(owner_paths - REPOSITORY_OWNER_ALLOWED):
        findings.append(f"{rel}: 仓库所有者标识出现在非更新文件")
    assert not findings, "公开发行脱敏检查失败：\n  " + "\n  ".join(findings)

    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for required in ("/corpus/", "/results/", "/batches/", ".env", "*.pem", "*.key"):
        assert required in ignore, f".gitignore 缺少：{required}"
    print("[ok] 公开树：私人目录 / 精确模型 ID / 路径 / 邮箱 / 凭据阻断")


def test_new_release_history_is_sanitized():
    """v1.6 之后的每个新增提交都必须干净；末端补删不能掩盖新快照。"""
    baseline_is_ancestor = (
        _git(["merge-base", "--is-ancestor", LEGACY_PUBLIC_BASELINE, "HEAD"])
        .returncode == 0
    )
    revision = f"{LEGACY_PUBLIC_BASELINE}..HEAD" if baseline_is_ancestor else "HEAD"
    revs = _git(["rev-list", revision])
    assert revs.returncode == 0, "读不到公开分支历史"
    commits = [x for x in revs.stdout.decode("ascii").splitlines() if x]
    findings = []

    for commit in commits:
        meta = _git([
            "show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", commit,
        ])
        assert meta.returncode == 0, f"读不到提交元数据：{commit[:12]}"
        metadata = meta.stdout.decode("utf-8", errors="replace")
        for label, pattern in (
            ("精确模型 ID", EXACT_MODEL_ID),
            ("非 noreply 邮箱", PRIVATE_EMAIL),
            ("仓库所有者身份", REPOSITORY_OWNER),
        ):
            if pattern.search(metadata):
                findings.append(f"{commit[:12]} 提交元数据含{label}")

        tree = _git(["ls-tree", "-r", "--name-only", "-z", commit])
        assert tree.returncode == 0, f"读不到提交树：{commit[:12]}"
        for rel in tree.stdout.decode("utf-8").split("\0"):
            if not rel or Path(rel).suffix.lower() not in TEXT_SUFFIXES:
                continue
            blob = _git(["show", f"{commit}:{rel}"])
            if blob.returncode != 0:
                continue
            text = blob.stdout.decode("utf-8", errors="replace")
            for label, pattern in (
                ("精确模型 ID", EXACT_MODEL_ID),
                ("私人绝对路径", PRIVATE_PATH),
                ("非 noreply 邮箱", PRIVATE_EMAIL),
                ("疑似凭据", SECRET_SHAPE),
            ):
                if pattern.search(text):
                    findings.append(f"{commit[:12]}:{rel} {label}")
            if (REPOSITORY_OWNER.search(text)
                    and rel not in REPOSITORY_OWNER_ALLOWED):
                findings.append(f"{commit[:12]}:{rel} 非必要仓库所有者标识")

    assert not findings, "公开可达历史脱敏检查失败：\n  " + "\n  ".join(findings)
    scope = "v1.6 后新增历史" if baseline_is_ancestor else "独立脱敏历史"
    print(f"[ok] {scope}：{len(commits)} 个提交全部脱敏")


if __name__ == "__main__":
    test_public_tree_is_sanitized()
    test_new_release_history_is_sanitized()
    print("ALL PASS")
