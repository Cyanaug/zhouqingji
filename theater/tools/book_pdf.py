# -*- coding: utf-8 -*-
"""把昼青集导出的离线排版 HTML 交给可选 Vivliostyle CLI，并验证 PDF。"""
from __future__ import annotations

import argparse
import base64
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from io import BytesIO
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any


MM_PER_POINT = 25.4 / 72
SIZE_TOLERANCE_MM = 0.6


class _ManifestParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._capturing = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "script" and values.get("id") == "book-print-manifest":
            self._capturing = True

    def handle_endtag(self, tag: str) -> None:
        if self._capturing and tag.lower() == "script":
            self._capturing = False

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    @property
    def payload(self) -> str:
        return "".join(self._parts)


class _PageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self.fragments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._depth:
            self._depth += 1
        elif "data-page-kind" in dict(attrs):
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", "", data)
        if self._depth and value:
            self.fragments.append(value)


def book_text_fragments(html: str) -> list[str]:
    parser = _PageTextParser()
    parser.feed(html)
    # 去重但保留 DOM 顺序；短页码也保留，确保页眉/folio 没有静默消失。
    return list(dict.fromkeys(parser.fragments))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_book_html(path: Path) -> tuple[dict[str, Any], str]:
    try:
        html = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法读取 UTF-8 排版 HTML：{exc}") from exc
    parser = _ManifestParser()
    parser.feed(html)
    try:
        manifest = json.loads(parser.payload)
    except json.JSONDecodeError as exc:
        raise ValueError("找不到完整的 book-print-manifest；请从昼青集重新导出离线排版 HTML") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != "zhouqingji-book-manifest":
        raise ValueError("输入不是昼青集诗集排版文件")
    page = manifest.get("publication_profile", {}).get("page", {})
    pagination = manifest.get("pagination", {})
    try:
        width = float(page["widthMm"])
        height = float(page["heightMm"])
        total_pages = int(pagination["total_pages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("排印清单缺少有效的页面尺寸或总页数") from exc
    if not (80 <= width <= 400 and 80 <= height <= 500 and total_pages > 0):
        raise ValueError("排印清单中的页面尺寸或总页数超出合理范围")
    match = re.search(r'data:font/otf;base64,([A-Za-z0-9+/=]+)', html)
    if not match:
        raise ValueError("排版 HTML 没有内嵌随包字体；请用当前版本重新导出")
    bundled = manifest.get("publication_profile", {}).get("bundled_font") or {}
    expected_font_hash = str(bundled.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_font_hash):
        raise ValueError("排印清单缺少随包字体 SHA-256；请用当前版本重新导出")
    try:
        embedded_font = base64.b64decode(match.group(1), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("排版 HTML 的内嵌字体不是有效 base64") from exc
    if hashlib.sha256(embedded_font).hexdigest() != expected_font_hash:
        raise ValueError("排版 HTML 的内嵌字体与排印清单 SHA-256 不一致")
    if re.search(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', html, flags=re.I):
        raise ValueError("排版 HTML 仍有外部资源，不满足离线专业输出边界")
    return manifest, html


def embedded_font_bytes(html: str) -> bytes:
    match = re.search(r'data:font/otf;base64,([A-Za-z0-9+/=]+)', html)
    if not match:
        raise ValueError("排版 HTML 没有内嵌随包字体")
    return base64.b64decode(match.group(1), validate=True)


def _compatibility_codepoint(codepoint: int) -> bool:
    return (0x2E80 <= codepoint <= 0x2EFF or 0x2F00 <= codepoint <= 0x2FDF
            or 0xF900 <= codepoint <= 0xFAFF or 0x2F800 <= codepoint <= 0x2FA1F)


def _unified_han(codepoint: int) -> bool:
    return (0x3400 <= codepoint <= 0x4DBF or 0x4E00 <= codepoint <= 0x9FFF
            or 0x20000 <= codepoint <= 0x323AF)


def font_alias_map(font_payload: bytes, preferred_text: str) -> dict[int, int]:
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise RuntimeError("缺少字体映射验证器 fonttools；请运行：python -m pip install fonttools") from exc
    font = TTFont(BytesIO(font_payload), lazy=True)
    cmap = font.getBestCmap() or {}
    by_glyph: dict[str, list[int]] = {}
    for codepoint, glyph in cmap.items():
        by_glyph.setdefault(glyph, []).append(codepoint)
    preferred = {ord(char) for char in preferred_text}
    aliases: dict[int, int] = {}
    for codepoint, glyph in cmap.items():
        if not _compatibility_codepoint(codepoint):
            continue
        candidates = [value for value in by_glyph[glyph] if _unified_han(value)]
        selected = [value for value in candidates if value in preferred]
        if len(selected) == 1:
            aliases[codepoint] = selected[0]
        elif len(candidates) == 1:
            aliases[codepoint] = candidates[0]
    font.close()
    return aliases


def _rewrite_tounicode(data: bytes, aliases: dict[int, int]) -> tuple[bytes, int]:
    changed = 0

    def replace(match: re.Match[bytes]) -> bytes:
        nonlocal changed
        raw = bytes.fromhex(match.group(1).decode("ascii"))
        if len(raw) not in (2, 4):
            return match.group(0)
        try:
            value = raw.decode("utf-16-be")
        except UnicodeDecodeError:
            return match.group(0)
        if len(value) != 1 or ord(value) not in aliases:
            return match.group(0)
        changed += 1
        encoded = chr(aliases[ord(value)]).encode("utf-16-be").hex().upper().encode("ascii")
        return b"<" + encoded + b">"

    return re.sub(rb"<([0-9A-Fa-f]{4,8})>", replace, data), changed


def repair_pdf_unicode(path: Path, font_payload: bytes, fragments: list[str]) -> int:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:
        raise RuntimeError("缺少 PDF 验证器 pypdf；请运行：python -m pip install pypdf") from exc
    aliases = font_alias_map(font_payload, "".join(fragments))
    if not aliases:
        return 0
    reader = PdfReader(str(path))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    changed = 0
    seen: set[int] = set()
    for page in writer.pages:
        resources = page.get("/Resources") or {}
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        fonts = resources.get("/Font") or {}
        fonts = fonts.get_object() if hasattr(fonts, "get_object") else fonts
        for ref in fonts.values():
            font = ref.get_object()
            marker = getattr(ref, "idnum", id(font))
            if marker in seen:
                continue
            seen.add(marker)
            stream = font.get("/ToUnicode")
            if not stream:
                continue
            stream = stream.get_object()
            rewritten, count = _rewrite_tounicode(stream.get_data(), aliases)
            if count:
                stream.set_data(rewritten)
                changed += count
    if changed:
        repaired = path.with_name(path.stem + ".unicode.pdf")
        with repaired.open("wb") as output:
            writer.write(output)
        repaired.replace(path)
    return changed


def _font_descriptor(font: Any) -> Any | None:
    font = font.get_object()
    descriptor = font.get("/FontDescriptor")
    if descriptor:
        return descriptor.get_object()
    descendants = font.get("/DescendantFonts") or []
    if descendants:
        descriptor = descendants[0].get_object().get("/FontDescriptor")
        return descriptor.get_object() if descriptor else None
    return None


def _font_is_embedded(font: Any) -> bool:
    font = font.get_object() if hasattr(font, "get_object") else font
    # Type3 字体的轮廓直接保存在 /CharProcs，不使用 FontDescriptor/FontFile。
    if str(font.get("/Subtype")) == "/Type3":
        char_procs = font.get("/CharProcs")
        if hasattr(char_procs, "get_object"):
            char_procs = char_procs.get_object()
        return bool(char_procs)
    descriptor = _font_descriptor(font)
    return bool(descriptor and any(descriptor.get(key) for key in ("/FontFile", "/FontFile2", "/FontFile3")))


def verify_pdf(path: Path, manifest: dict[str, Any], expected_fragments: list[str] | None = None) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 PDF 验证器 pypdf；请先运行：python -m pip install pypdf") from exc
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ValueError(f"PDF 无法重新打开：{exc}") from exc
    expected_pages = int(manifest["pagination"]["total_pages"])
    if len(reader.pages) != expected_pages:
        raise ValueError(f"PDF 页数 {len(reader.pages)} 与排印清单 {expected_pages} 不一致")
    expected = manifest["publication_profile"]["page"]
    target_width, target_height = float(expected["widthMm"]), float(expected["heightMm"])
    sizes: list[list[float]] = []
    fonts: dict[str, bool] = {}
    extracted_chars = 0
    extracted_pages: list[str] = []
    for index, page in enumerate(reader.pages, 1):
        width = float(page.mediabox.width) * MM_PER_POINT
        height = float(page.mediabox.height) * MM_PER_POINT
        sizes.append([round(width, 3), round(height, 3)])
        if abs(width - target_width) > SIZE_TOLERANCE_MM or abs(height - target_height) > SIZE_TOLERANCE_MM:
            raise ValueError(
                f"第 {index} 页尺寸 {width:.2f}x{height:.2f} mm，预期 {target_width:g}x{target_height:g} mm")
        extracted = page.extract_text() or ""
        extracted_chars += len(extracted.strip())
        extracted_pages.append(extracted)
        resources = page.get("/Resources") or {}
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        font_resources = resources.get("/Font") or {}
        if hasattr(font_resources, "get_object"):
            font_resources = font_resources.get_object()
        for ref in font_resources.values():
            font = ref.get_object()
            name = str(font.get("/BaseFont") or font.get("/Subtype") or "unknown")
            embedded = _font_is_embedded(font)
            fonts[name] = fonts.get(name, True) and embedded
    if not fonts:
        raise ValueError("PDF 中没有检测到字体资源")
    missing = sorted(name for name, embedded in fonts.items() if not embedded)
    if missing:
        raise ValueError("PDF 存在未嵌入字体：" + ", ".join(missing))
    if extracted_chars == 0:
        raise ValueError("PDF 没有可提取文字；可能被错误栅格化或字体映射损坏")
    fragments = expected_fragments or []
    normalized_pdf = re.sub(r"\s+", "", "\n".join(extracted_pages))
    missing_fragments = [value for value in fragments if re.sub(r"\s+", "", value) not in normalized_pdf]
    if missing_fragments:
        sample = " / ".join(missing_fragments[:3])
        raise ValueError(f"PDF 文字层缺少排版页文字（示例：{sample}）")
    return {
        "pages": len(reader.pages),
        "page_sizes_mm": [list(size) for size in sorted({tuple(size) for size in sizes})],
        "fonts": [{"name": name, "embedded": embedded} for name, embedded in sorted(fonts.items())],
        "extracted_characters": extracted_chars,
        "source_text_fragments": len(fragments),
        "source_text_complete": bool(fragments),
    }


def _local_vivliostyle() -> str | None:
    """项目本地安装（npm install @vivliostyle/cli，无 -g）优先于全局，避免占用系统盘。"""
    root = Path(__file__).resolve().parents[2]
    for base in (root / "node_modules" / ".bin", root / "theater" / "node_modules" / ".bin"):
        for name in ("vivliostyle.cmd", "vivliostyle", "vs.cmd", "vs"):
            candidate = base / name
            if candidate.is_file():
                return str(candidate)
    return None


def find_vivliostyle(explicit: str | None) -> str:
    if explicit:
        found = shutil.which(explicit)
        if not found and Path(explicit).is_file():
            found = str(Path(explicit).resolve())
    else:
        found = _local_vivliostyle() or next(
            (shutil.which(name) for name in ("vivliostyle", "vivliostyle.cmd", "vs")
             if shutil.which(name)), None)
    if not found:
        raise RuntimeError(
            "未找到 Vivliostyle CLI。普通阅读不受影响；要制作专业 PDF，请先安装 Node.js 22.12+，"
            "再在昼青集目录运行 npm install @vivliostyle/cli（装在项目里）或 "
            "npm install -g @vivliostyle/cli（装成全局）")
    return str(Path(found).resolve())


def build_pdf(input_html: Path, output_pdf: Path, executable: str | None,
              overwrite: bool, timeout: int, browser: str | None = None) -> dict[str, Any]:
    manifest, html = load_book_html(input_html)
    if output_pdf.exists() and not overwrite:
        raise FileExistsError(f"输出已存在：{output_pdf}（确认后加 --overwrite）")
    command = find_vivliostyle(executable)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    page = manifest["publication_profile"]["page"]
    size = f'{float(page["widthMm"]):g}mm,{float(page["heightMm"]):g}mm'
    try:
        version_proc = subprocess.run([command, "--version"], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=30)
        renderer_version = (version_proc.stdout or version_proc.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        renderer_version = "unknown"
    # 先渲染和验证临时文件；即使构建失败，也不破坏已有成品。
    # Vivliostyle 11.2 起拒绝把输出写进输入文件所在目录（视为覆盖原稿），
    # 因此在系统临时目录构建，成功后再落位到作者指定的输出路径。
    with tempfile.TemporaryDirectory(prefix=".zq-book-pdf-") as folder:
        staged_pdf = Path(folder) / output_pdf.name
        args = [command, "build", str(input_html), "--output", str(staged_pdf), "--size", size,
                "--single-doc", "--no-vite-config-file", "--timeout", str(timeout)]
        if browser:
            browser_path = Path(browser).resolve(strict=True)
            args.extend(["--executable-browser", str(browser_path)])
        # Vivliostyle 11.2 起把进程工作目录当工作区根，输出在工作区树外或跨盘时
        # 会被“覆盖原稿”守卫拒绝；暂存目录在系统临时目录里，cwd 取其父目录即可。
        proc = subprocess.run(args, text=True, encoding="utf-8", errors="replace",
                              capture_output=True, cwd=str(Path(folder).parent))
        if proc.returncode:
            # 附上渲染器自己的报错尾部，避免只有退出码时无从下手。
            tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-6:]).strip()
            detail = f"：{tail[-600:]}" if tail else ""
            raise RuntimeError(f"Vivliostyle 构建失败（退出码 {proc.returncode}）{detail}")
        if not staged_pdf.is_file() or staged_pdf.stat().st_size < 1024:
            raise ValueError("渲染器没有产生有效 PDF")
        with staged_pdf.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError("渲染器没有产生有效 PDF")
        fragments = book_text_fragments(html)
        if not fragments:
            raise ValueError("排版 HTML 页面中没有可核验文字")
        font_payload = embedded_font_bytes(html)
        unicode_repairs = repair_pdf_unicode(staged_pdf, font_payload, fragments)
        qa = verify_pdf(staged_pdf, manifest, fragments)
        qa["unicode_mappings_repaired"] = unicode_repairs
        shutil.move(str(staged_pdf), str(output_pdf))
    receipt = {
        "schema": 1,
        "kind": "zhouqingji-book-pdf-verification",
        "input": {"name": input_html.name, "sha256": sha256_file(input_html)},
        "output": {"name": output_pdf.name, "sha256": sha256_file(output_pdf),
                   "bytes": output_pdf.stat().st_size},
        "manifest": manifest,
        "verification": qa,
        "renderer": {"command": Path(command).name, "version": renderer_version, "page_size": size},
    }
    receipt_path = output_pdf.with_suffix(".pdf.qa.json")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"pdf": str(output_pdf), "receipt": str(receipt_path), **qa}


def default_output(input_html: Path) -> Path:
    suffix = "-离线排版"
    stem = input_html.stem[:-len(suffix)] if input_html.stem.endswith(suffix) else input_html.stem
    return input_html.with_name(stem + "-专业阅读.pdf")


def dependency_status() -> dict[str, Any]:
    node = shutil.which("node")
    node_version = None
    if node:
        try:
            proc = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
            node_version = proc.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            pass
    version_match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", node_version or "")
    node_compatible = bool(version_match and tuple(map(int, version_match.groups())) >= (22, 12, 0))
    cli = _local_vivliostyle() or next(
        (shutil.which(name) for name in ("vivliostyle", "vivliostyle.cmd", "vs")
         if shutil.which(name)), None)
    browser_candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]
    browsers = [str(path) for path in browser_candidates if path.is_file()]
    return {
        "node": {"ready": node_compatible, "installed": bool(node), "path": node, "version": node_version,
                 "minimum": "22.12.0"},
        "vivliostyle": {"ready": bool(cli), "path": cli},
        "pypdf": {"ready": importlib.util.find_spec("pypdf") is not None},
        "fonttools": {"ready": importlib.util.find_spec("fontTools") is not None},
        "browsers": browsers,
        "ready": bool(node_compatible and cli and importlib.util.find_spec("pypdf")
                      and importlib.util.find_spec("fontTools")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="昼青集诗集：离线 HTML -> 可验证的专业阅读 PDF")
    parser.add_argument("input", nargs="?", type=Path, help="从诗集完整排版页导出的离线 HTML")
    parser.add_argument("-o", "--output", type=Path, help="输出 PDF；默认与 HTML 同目录")
    parser.add_argument("--check-html", action="store_true", help="只检查输入与内嵌排印清单，不生成 PDF")
    parser.add_argument("--vivliostyle", help="Vivliostyle 可执行文件名或路径")
    parser.add_argument("--browser", help="可选：现有 Chrome/Edge 可执行文件路径，避免 CLI 另下载浏览器")
    parser.add_argument("--overwrite", action="store_true", help="明确允许覆盖已有 PDF 与验证回执")
    parser.add_argument("--timeout", type=int, default=300, help="渲染超时秒数，默认 300")
    parser.add_argument("--doctor", action="store_true", help="只检查专业 PDF 环境，不读取作品或生成文件")
    args = parser.parse_args(argv)
    try:
        if args.doctor:
            print(json.dumps({"ok": True, **dependency_status()}, ensure_ascii=False, indent=2))
            return 0
        if args.input is None:
            raise ValueError("请指定离线排版 HTML，或使用 --doctor 检查环境")
        input_html = args.input.resolve(strict=True)
        manifest, _ = load_book_html(input_html)
        if args.check_html:
            page = manifest["publication_profile"]["page"]
            result = {"ok": True, "title": manifest.get("book", {}).get("title"),
                      "pages": manifest["pagination"]["total_pages"],
                      "page_size_mm": [page["widthMm"], page["heightMm"]],
                      "sha256": sha256_file(input_html)}
        else:
            if not 30 <= args.timeout <= 1800:
                raise ValueError("--timeout 必须在 30 到 1800 秒之间")
            output = (args.output or default_output(input_html)).resolve()
            if output.suffix.lower() != ".pdf":
                raise ValueError("输出文件必须以 .pdf 结尾")
            result = {"ok": True, **build_pdf(input_html, output, args.vivliostyle,
                                                args.overwrite, args.timeout, args.browser)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
