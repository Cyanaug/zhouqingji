# -*- coding: utf-8 -*-
"""GitHub Pages 安卓空壳必须精确允许、可复现且不含私人数据。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "release"))
import build_mobile_pages as MP  # noqa: E402
import sync_mobile_pages as SMP  # noqa: E402


def test_build_is_allowlisted_and_reproducible():
    with tempfile.TemporaryDirectory(prefix="zq-mobile-pages-") as td:
        first, second = Path(td) / "a", Path(td) / "b"
        one = MP.build_mobile_pages(first)
        two = MP.build_mobile_pages(second)
        expected = set(MP.SHELL_FILES) | {
            "index.html", ".nojekyll", ".gitattributes", "deployment-manifest.json"
        }
        assert {p.name for p in first.iterdir()} == expected
        assert one == two
        assert one["kind"] == "zhouqingji-mobile-pages-deployment"
        saved = json.loads((first / "deployment-manifest.json").read_text(encoding="utf-8"))
        assert saved == one
        assert (first / "index.html").read_bytes() == (first / "mobile.html").read_bytes()
        assert b"\r\n" not in (first / "app.js").read_bytes()
        assert b"\r\n" not in (first / "deployment-manifest.json").read_bytes()
        assert "eol=lf" in (first / ".gitattributes").read_text(encoding="utf-8")
    print("[ok] Pages 空壳精确允许清单 / 可复现哈希 / 根页别名")


def test_private_snapshot_is_rejected():
    with tempfile.TemporaryDirectory(prefix="zq-mobile-pages-private-") as td:
        fake = Path(td) / "webapp"
        fake.mkdir()
        for name in MP.SHELL_FILES:
            shutil.copyfile(MP.WEBAPP / name, fake / name)
        with (fake / "app.js").open("a", encoding="utf-8") as handle:
            handle.write("\nwindow.__ZQ_SNAPSHOT__ = {private: true};\n")
        try:
            MP.build_mobile_pages(Path(td) / "out", fake)
            assert False, "内嵌快照必须阻止 Pages 包生成"
        except ValueError as exc:
            assert "embedded snapshot" in str(exc)
    print("[ok] Pages 构建遇到内嵌私人快照立即中止")


def test_nonempty_target_is_rejected():
    with tempfile.TemporaryDirectory(prefix="zq-mobile-pages-target-") as td:
        out = Path(td) / "out"
        out.mkdir()
        (out / "unknown.txt").write_text("do not overwrite", encoding="utf-8")
        try:
            MP.build_mobile_pages(out)
            assert False, "非空目录不得被覆盖"
        except ValueError as exc:
            assert "不存在或为空" in str(exc)
    print("[ok] Pages 构建不覆盖未知目录")


def test_pages_sync_changes_only_generated_allowlist():
    with tempfile.TemporaryDirectory(prefix="zq-pages-build-") as build_td, \
            tempfile.TemporaryDirectory(prefix="zq-pages-target-") as target_td:
        built, target = Path(build_td), Path(target_td)
        MP.build_mobile_pages(built)
        expected = SMP.artifact_files(built)
        for rel in expected:
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((built / rel).read_bytes())
        (target / "app.js").write_text("stale", encoding="utf-8")
        plan = SMP.changed_files(built, target, expected)
        assert plan == ["app.js"]
        SMP.sync_files(built, target, plan)
        assert not SMP.changed_files(built, target, expected)
    print("[ok] gh-pages 刷新只写生成允许清单并逐字节复核")


def test_actions_deploys_only_generated_shell():
    workflow = (ROOT / ".github/workflows/mobile-pages.yml").read_text(encoding="utf-8")
    assert "branches: [main]" in workflow
    assert "python theater/tests/test_mobile_pages.py" in workflow
    assert "python theater/release/build_mobile_pages.py --output _mobile_site" in workflow
    assert "actions/upload-pages-artifact@v4" in workflow
    assert "path: _mobile_site" in workflow
    assert "actions/deploy-pages@v4" in workflow
    assert "pages: write" in workflow and "id-token: write" in workflow
    assert "path: ." not in workflow, "不得上传公开仓整树"
    print("[ok] Pages Actions 只部署经过隐私扫描的空壳目录")


if __name__ == "__main__":
    test_build_is_allowlisted_and_reproducible()
    test_private_snapshot_is_rejected()
    test_nonempty_target_is_rejected()
    test_pages_sync_changes_only_generated_allowlist()
    test_actions_deploys_only_generated_shell()
    print("ALL PASS")
