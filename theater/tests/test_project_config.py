# -*- coding: utf-8 -*-
"""项目级多工具入口的轻量结构检查。"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    assert (ROOT / "AGENTS.md").exists()
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@AGENTS.md" in claude

    for path in sorted((ROOT / ".codex" / "agents").glob("*.toml")):
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        for key in ("name", "description", "developer_instructions"):
            assert isinstance(data.get(key), str) and data[key].strip(), f"{path}: missing {key}"

    skills = sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md"))
    assert skills, "Codex repository skills are missing"
    for path in skills:
        text = path.read_text(encoding="utf-8")
        match = re.match(r"---\n(.*?)\n---\n", text, re.S)
        assert match, f"{path}: invalid frontmatter"
        front = match.group(1)
        assert re.search(r"^name:\s*\S+", front, re.M)
        assert re.search(r"^description:\s*.+", front, re.M)
    print("[ok] AGENTS/CLAUDE entrypoints + Codex agents/skills")


if __name__ == "__main__":
    main()
