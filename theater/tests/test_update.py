# -*- coding: utf-8 -*-
"""ZIP 安装更新的安全边界测试（零网络、零第三方依赖）。"""
import io
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "src"))
import server as S  # noqa: E402


def _archive(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr("zhouqingji-main/" + name, content)
    return buf.getvalue()


def test_safe_archive_update():
    with tempfile.TemporaryDirectory(prefix="zqj-update-test-") as td:
        root = Path(td)
        (root / "theater/src").mkdir(parents=True)
        (root / "corpus").mkdir()
        (root / "results").mkdir()
        (root / "VERSION").write_text("1.5\n", encoding="utf-8")
        (root / "theater/src/server.py").write_text("old", encoding="utf-8")
        (root / "corpus/诗稿.json").write_text("PRIVATE", encoding="utf-8")
        (root / "results/reads.jsonl").write_text("PRIVATE", encoding="utf-8")

        data = _archive({
            "VERSION": "1.6\n",
            "README.md": "new docs",
            "theater/src/server.py": "new server",
            "corpus/诗稿.json": "PUBLIC SHOULD NEVER COPY",
            "results/reads.jsonl": "PUBLIC SHOULD NEVER COPY",
            "theater/runners/batches/task.json": "SHOULD NEVER COPY",
        })
        result = S.install_update_archive(data, root)
        assert result["changed"] == 3
        assert (root / "VERSION").read_text(encoding="utf-8") == "1.6\n"
        assert (root / "theater/src/server.py").read_text(encoding="utf-8") == "new server"
        assert (root / "corpus/诗稿.json").read_text(encoding="utf-8") == "PRIVATE"
        assert (root / "results/reads.jsonl").read_text(encoding="utf-8") == "PRIVATE"
        assert not (root / "theater/runners/batches/task.json").exists()
        backup = Path(result["backup"])
        assert (backup / "VERSION").read_text(encoding="utf-8") == "1.5\n"
        assert (backup / "theater/src/server.py").read_text(encoding="utf-8") == "old"
    print("[ok] ZIP 更新允许清单 / 私人数据隔离 / 覆盖前备份")


def test_archive_rejects_traversal():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("zhouqingji-main/VERSION", "1.6")
        zf.writestr("zhouqingji-main/theater/src/server.py", "ok")
        zf.writestr("zhouqingji-main/../escape.txt", "bad")
    try:
        S._validated_archive_files(buf.getvalue())
        assert False, "路径穿越 ZIP 必须被拒绝"
    except ValueError:
        pass
    print("[ok] ZIP 路径穿越拒绝")


def test_archive_version_must_match_expected_tag():
    data = _archive({
        "VERSION": "9.1\n",
        "theater/src/server.py": "new server",
    })
    try:
        S._validated_archive_files(data, expected_version="9.2")
        assert False, "归档版本与目标 tag 不一致时必须拒绝"
    except ValueError:
        pass
    assert S._validated_archive_files(data, expected_version="9.1")
    print("[ok] ZIP 版本与目标 tag 一致性校验")


def test_local_request_guards():
    port = 8737
    assert S._is_local_host_header("127.0.0.1:8737", port)
    assert S._is_local_host_header("localhost:8737", port)
    assert not S._is_local_host_header("example.test:8737", port)
    assert S._is_local_origin(None, port)
    assert S._is_local_origin("http://127.0.0.1:8737", port)
    assert S._is_local_origin("http://localhost:8737", port)
    assert not S._is_local_origin("https://example.test", port)
    print("[ok] 本地 Host / Origin 防护")


def test_official_remote_url_normalization():
    official = "https://github.com/Cyanaug/zhouqingji"
    assert S._normalized_git_url(official + ".git") == S._normalized_git_url(official)
    assert S._normalized_git_url("git@github.com:Cyanaug/zhouqingji.git") == \
        S._normalized_git_url(official)
    assert S._normalized_git_url("https://example.test/other/repo.git") != \
        S._normalized_git_url(official)
    print("[ok] Git 更新仅接受官方远端")


if __name__ == "__main__":
    test_safe_archive_update()
    test_archive_rejects_traversal()
    test_archive_version_must_match_expected_tag()
    test_local_request_guards()
    test_official_remote_url_normalization()
    print("ALL PASS")
