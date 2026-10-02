# -*- coding: utf-8 -*-
"""专业阅读 PDF 管线的输入验证与 PDF 预检。"""
import json
import hashlib
from pathlib import Path
import sys
import tempfile

try:
    from pypdf import PdfWriter
except ImportError:
    PdfWriter = None

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "tools"))

import book_pdf


def sample_manifest(pages=2):
    font = b"OTTO"
    return {
        "schema": 1,
        "kind": "zhouqingji-book-manifest",
        "book": {"title": "测试诗集"},
        "publication_profile": {
            "page": {"widthMm": 148, "heightMm": 210},
            "bundled_font": {"sha256": hashlib.sha256(font).hexdigest()},
        },
        "pagination": {"total_pages": pages},
    }


def sample_html(manifest=None, font=True, external=False):
    manifest = manifest or sample_manifest()
    font_css = "data:font/otf;base64,T1RUTw==" if font else "fonts/local.otf"
    external_link = '<img src="https://example.com/a.png">' if external else ""
    return ("<!doctype html><meta charset=utf-8><style>@font-face{src:url(\"" + font_css + "\")}</style>"
            + external_link + '<script type="application/json" id="book-print-manifest">'
            + json.dumps(manifest, ensure_ascii=False) + "</script>")


def test_html_contract(tmp: Path):
    path = tmp / "book.html"
    path.write_text(sample_html(), encoding="utf-8")
    manifest, html = book_pdf.load_book_html(path)
    assert manifest["book"]["title"] == "测试诗集" and "data:font/otf" in html
    page_html = sample_html().replace("<script", '<section data-page-kind="poem"><h2>纸上青光</h2><p>第一行</p></section><script')
    assert book_pdf.book_text_fragments(page_html) == ["纸上青光", "第一行"]
    for name, payload, message in (
        ("no-font.html", sample_html(font=False), "没有内嵌随包字体"),
        ("external.html", sample_html(external=True), "仍有外部资源"),
        ("wrong.html", sample_html({"kind": "other"}), "不是昼青集"),
    ):
        candidate = tmp / name
        candidate.write_text(payload, encoding="utf-8")
        try:
            book_pdf.load_book_html(candidate)
            raise AssertionError(f"{name} 应被拒绝")
        except ValueError as exc:
            assert message in str(exc)
    wrong_hash = sample_manifest()
    wrong_hash["publication_profile"]["bundled_font"]["sha256"] = "0" * 64
    candidate = tmp / "wrong-font.html"
    candidate.write_text(sample_html(wrong_hash), encoding="utf-8")
    try:
        book_pdf.load_book_html(candidate)
        raise AssertionError("字体哈希漂移必须拒绝")
    except ValueError as exc:
        assert "字体与排印清单" in str(exc)


def test_pdf_page_contract(tmp: Path):
    assert book_pdf._font_is_embedded({"/Subtype": "/Type3", "/CharProcs": {"/glyph": {}}})
    assert not book_pdf._font_is_embedded({"/Subtype": "/Type3", "/CharProcs": {}})
    if PdfWriter is None:
        print("[skip] pypdf 未安装，跳过实际 PDF 拒收测试")
        return
    pdf = tmp / "blank.pdf"
    writer = PdfWriter()
    width, height = 148 / book_pdf.MM_PER_POINT, 210 / book_pdf.MM_PER_POINT
    writer.add_blank_page(width=width, height=height)
    writer.add_blank_page(width=width, height=height)
    with pdf.open("wb") as stream:
        writer.write(stream)
    try:
        book_pdf.verify_pdf(pdf, sample_manifest())
        raise AssertionError("无字体、无可提取文字的空 PDF 不能通过专业预检")
    except ValueError as exc:
        assert "字体" in str(exc) or "可提取文字" in str(exc)
    try:
        book_pdf.verify_pdf(pdf, sample_manifest(pages=3))
        raise AssertionError("页数与 manifest 不同必须拒绝")
    except ValueError as exc:
        assert "页数" in str(exc)


def test_font_alias_and_cmap_repair():
    import importlib.util
    if importlib.util.find_spec("fontTools") is None:
        print("[skip] fonttools 未安装，跳过字体别名映射表执行测试")
        return
    font = ROOT / "theater/src/webapp/fonts/SourceHanSerifCN-Regular.otf"
    aliases = book_pdf.font_alias_map(font.read_bytes(), "青文首页色一而")
    assert aliases[0x2ED8] == ord("青")
    assert aliases[0x2F42] == ord("文")
    assert aliases[0x2FB8] == ord("首")
    source = b"1 beginbfchar\n<AD> <2ED8>\nendbfchar"
    repaired, count = book_pdf._rewrite_tounicode(source, aliases)
    assert b"<9752>" in repaired and count == 1


def test_safe_defaults(tmp: Path):
    source = tmp / "我的诗-离线排版.html"
    assert book_pdf.default_output(source).name == "我的诗-专业阅读.pdf"
    source.write_text(sample_html(), encoding="utf-8")
    existing = tmp / "already.pdf"
    existing.write_bytes(b"private")
    try:
        book_pdf.build_pdf(source, existing, "missing", False, 300)
        raise AssertionError("不得默认覆盖已有成品")
    except FileExistsError:
        pass
    fake_cli = tmp / "relative-cli.cmd"
    fake_cli.write_text("@echo off\r\n", encoding="ascii")
    old_cwd = Path.cwd()
    try:
        # 显式相对 .cmd 必须先绝对化，否则 Windows 会把路径首段当命令。
        import os
        os.chdir(tmp.parent)
        resolved = book_pdf.find_vivliostyle(str(Path(tmp.name) / fake_cli.name))
        assert Path(resolved).is_absolute() and Path(resolved) == fake_cli.resolve()
    finally:
        os.chdir(old_cwd)
    status = book_pdf.dependency_status()
    assert {"node", "vivliostyle", "pypdf", "fonttools", "browsers", "ready"} <= set(status)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="zqj_book_pdf_test_") as folder:
        tmp = Path(folder)
        test_html_contract(tmp)
        test_pdf_page_contract(tmp)
        test_font_alias_and_cmap_repair()
        test_safe_defaults(tmp)
    print("[ok] 离线 HTML 输入闭包、manifest 与外链边界")
    print("[ok] PDF 页数/物理尺寸/嵌字/可提取文字预检边界")
    print("[ok] 默认输出命名与不覆盖已有成品")
    print("[ok] Type3 ToUnicode 部首别名修复为作者原始汉字")
    print("ALL PASS")
