# -*- coding: utf-8 -*-
"""整本压力校样：对作者实际保存的诗集方案跑程序化校验，输出结构报告。

与 theater/tests/test_books.py 的真实语料压测共享同一套不变量，但对象是作者当前
保存的方案（含分辑、校样分行、前置页与版式覆盖），而不是合成配置：

- 组装页字符守恒（含校样分行生效时）；
- 目录页码与实际正文页一一对应；
- 分辑扉页与前置页（题词/书名页）落在右页，空白页只出现在 verso；
- 每页排版单元不超过该开本与版式 profile 的容量；
- 校样分行原文哈希过期时明确列出“分行需重做”。

工具只读 corpus 与方案侧车，不写任何数据；报告不复制诗正文，只含作品 ID。
不修改 corpus、results 或已导出的 HTML/PDF。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODE_FUNCTIONS_SPAN = ("const BOOK_BUNDLED_FONT", "function renderBookPreview")


def node_script() -> str:
    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    start = app_js.index(NODE_FUNCTIONS_SPAN[0])
    end = app_js.index(NODE_FUNCTIONS_SPAN[1], start)
    return "\n".join([
        '"use strict";',
        'const IN = JSON.parse(require("fs").readFileSync(0, "utf8"));',
        f'let S = {{stanzas: IN.stanzas || {{}}, version: "book-verify"}};',
        "function esc(value) { return String(value); }",
        "const maps = {poem: new Map(IN.poems.map(p => [p.id, p]))};",
        app_js[start:end],
        "const results = [];",
        """for (const cfg of IN.configs) {
  const draft = {
    ...cfg,
    title: cfg.title || "校验",
    layout: cfg.layout || {page_size: "A5"},
    versions: cfg.versions || {},
  };
  if (!("order" in cfg)) draft.poem_ids = cfg.poem_ids || [];
  const poemIds = bookPoemIds(draft);
  const pageSize = draft.layout.page_size || "A5";
  const config = bookLayoutConfig(draft, pageSize);
  const preview = bookBuildPreview(draft);
  const assembled = {};
  const perPoem = {};
  for (const pid of poemIds) {
    const poem = maps.poem.get(pid);
    if (!poem) continue; // 缺失引用由 Python 端显式报告，node 端不崩
    const ver = draft.versions[pid];
    const verActive = bookVersionRecord(draft, poem).active;
    const effective = { ...poem, content: bookPoemBaseText(poem, draft) };
    const wrapped = bookStanzas(effective, draft).map(st =>
      st.map(line => bookWrapLine(line, config.charsPerLine)));
    const visual = wrapped.flat(2);
    perPoem[pid] = {
      joined: visual.map(w => w.text).join(""),
      staleBreak: bookProofBreakRecord(draft, poem).stale,
      staleVersion: Boolean(ver && ver.source_hash && poem.content_hash
        && ver.source_hash !== poem.content_hash),
      versionActive: verActive,
      pages: bookPaginatePoem(effective, pageSize, config, draft).map(pg => ({
        units: pg.stanzas.reduce((n, st) => n + st.length, 0) + Math.max(0, pg.stanzas.length - 1),
        isFirst: !pg.continuation,
      })),
    };
  }
  for (const page of preview.pages) {
    if (page.kind !== "poem") continue;
    if (!assembled[page.poemId]) assembled[page.poemId] = [];
    assembled[page.poemId].push(...page.stanzas.flat().map(line => line.text));
  }
  results.push({
    id: cfg.id, title: cfg.title, pageSize,
    config: {charsPerLine: config.charsPerLine, firstLines: config.firstLines,
             continuationLines: config.continuationLines},
    sectionStart: bookSectionStart(draft),
    pageKinds: preview.pages.map(pg => pg.kind),
    insertedFlags: preview.pages.map(pg => pg.inserted === true),
    entries: preview.entries.map(e => ({poemId: e.poemId, folio: e.folio})),
    tocItems: preview.tocItems,
    printedTocItems: preview.pages.filter(page => page.kind === "toc").flatMap(page => page.entries),
    diagnostics: preview.diagnostics,
    frontMatterPages: preview.frontMatterPages,
    frontBlankPages: preview.frontBlankPages,
    poemPages: preview.poemPages,
    poemIds,
    assembled, perPoem,
  });
}
console.log(JSON.stringify(results));""",
    ])


def _norm_content(content: str) -> str:
    strip = " \t\u3000\u00a0"
    lines = str(content or "").replace("\r\n", "\n").split("\n")
    kept = [line.rstrip() for line in lines]
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return "".join(line.lstrip(strip) for line in kept)


def norm_text(poem: dict) -> str:
    return _norm_content(str(poem.get("content") or ""))


def effective_text(poem: dict, versions: dict | None, body_nodes: dict | None = None) -> str:
    """出版版本激活时的“生效文本”；否则真源。停用（哈希过期）回退真源。"""
    body = (body_nodes or {}).get(poem.get("id"))
    if (isinstance(body, dict) and body.get("source_hash") == poem.get("content_hash")
            and isinstance(body.get("nodes"), list)):
        return _norm_content("".join(node.get("text", "") for node in body["nodes"]
                                     if isinstance(node, dict)))
    record = (versions or {}).get(poem.get("id"))
    if (isinstance(record, dict) and isinstance(record.get("content"), str)
            and record["content"].strip()
            and (not record.get("source_hash")
                 or record.get("source_hash") == poem.get("content_hash"))):
        return _norm_content(record["content"])
    return norm_text(poem)


def verify_book(result: dict, poem_by_id: dict, versions: dict | None = None,
                body_nodes: dict | None = None) -> dict:
    issues, stale_breaks = [], []
    kinds = result["pageKinds"]
    issues.extend(item["message"] for item in result.get("diagnostics", []) if item.get("blocking"))
    if result.get("printedTocItems") != result.get("tocItems"):
        issues.append("实际目录与完整目录条目不一致")
    name = result.get("title") or result.get("id") or "未命名"
    if not kinds or kinds[0] != "cover":
        issues.append("第一页不是封面")
    if set(result["assembled"]) != set(result["perPoem"]):
        issues.append("组装页与入集作品不一致（丢诗或串诗）")
    toc_pages = kinds.count("toc")
    front_len = result["frontBlankPages"] + result["frontMatterPages"]
    body = kinds[1 + front_len + toc_pages:]
    body_inserted = (result.get("insertedFlags") or [False] * len(kinds))[1 + front_len + toc_pages:]
    require_recto = result.get("sectionStart", "recto") == "recto"
    for offset, kind in enumerate(body):
        physical_index = 1 + front_len + toc_pages + offset
        if kind == "section" and require_recto and physical_index % 2 != 0:
            issues.append(f"分辑扉页未落在右页（正文第 {offset + 1} 物理页）")
        # 作者手插的空白页属于内容，不参与“补白空白页必须在 verso”的自动规则。
        if kind == "blank" and not body_inserted[offset] and physical_index % 2 != 1:
            issues.append(f"空白页出现在非 verso 位（正文第 {offset + 1} 物理页）")
    front_kinds = kinds[1:1 + front_len]
    for offset, kind in enumerate(front_kinds):
        if kind in ("dedication", "title") and (1 + offset) % 2 != 0:
            issues.append(f"前置页 {kind} 未落在右页")
    folio_kind = {i + 1: kind for i, kind in enumerate(body)}
    poem_first_page = {entry["poemId"]: entry["folio"] for entry in result["entries"]}
    for item in result["tocItems"]:
        if item["kind"] == "poem" and folio_kind.get(item["folio"]) != "poem":
            issues.append(f"目录页码错位：{item['poemId']} → {item['folio']}")
        if item["kind"] == "section" and folio_kind.get(item["folio"]) != "section":
            issues.append(f"分辑目录页码错位：{item['title']}")
    first_pages = set()
    for pid in result.get("poemIds", []):
        if pid not in poem_by_id:
            issues.append(f"方案引用了语料中不存在的作品：{pid}")
    for pid, info in result["perPoem"].items():
        poem = poem_by_id.get(pid)
        if poem is None:
            issues.append(f"方案引用了语料中不存在的作品：{pid}")
            continue
        expected = effective_text(poem, versions, body_nodes)
        if info["joined"] != expected:
            issues.append(f"字符守恒失败：{pid}")
        if "".join(result["assembled"].get(pid, [])) != expected:
            issues.append(f"组装后字符守恒失败：{pid}")
        if info["staleBreak"]:
            stale_breaks.append(pid)
        if info.get("staleVersion"):
            stale_breaks.append(f"{pid}（出版版本）")
        for i, page in enumerate(info["pages"]):
            cap = result["config"]["firstLines"] if page["isFirst"] else result["config"]["continuationLines"]
            if page["units"] > cap:
                issues.append(f"页容量溢出：{pid} 第 {i + 1} 页 {page['units']}>{cap}")
    return {
        "book": result.get("id"), "title": name, "page_size": result["pageSize"],
        "poems": len(result["perPoem"]),
        "pages": {
            "total": len(kinds), "body": len(body), "toc": toc_pages,
            "front_matter": result["frontMatterPages"], "front_blank": result["frontBlankPages"],
            "essay_pages": kinds.count("prose"),
            "poem_folios": result["poemPages"],
        },
        "stale_proof_breaks": stale_breaks,
        "first_folio_of_first_poem": min(poem_first_page.values()) if poem_first_page else None,
        "ok": not issues,
        "issues": issues,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="对已保存诗集方案跑整本程序化校验（只读）")
    parser.add_argument("--corpus", type=Path, default=ROOT / "corpus" / "诗稿.json")
    parser.add_argument("--stanzas", type=Path, default=ROOT / "corpus" / "分段.json",
                        help="作者分段侧车；缺省时与应用一致地留空")
    parser.add_argument("--projects", type=Path, default=ROOT / "corpus" / "诗集方案.json")
    parser.add_argument("--all", action="store_true", help="额外校验一组合成极端配置（全语料等）")
    parser.add_argument("--report", type=Path, help="另存 JSON 报告到该路径（默认只打印）")
    args = parser.parse_args(argv)
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    started = time.time()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    stanzas = json.loads(args.stanzas.read_text(encoding="utf-8")) if args.stanzas.exists() else {}
    poem_by_id = {p["id"]: p for p in corpus}
    projects_doc = json.loads(args.projects.read_text(encoding="utf-8")) if args.projects.exists() \
        else {"schema": 3, "books": []}
    books = projects_doc.get("books", [])
    # 原样传入保存方案，避免校验器逐项抄字段时漏掉新的版式设置。
    configs = [dict(book) for book in books]
    if args.all:
        configs.append({"id": "verify-full-corpus", "title": "全语料合成书",
                        "poem_ids": sorted(poem_by_id),
                        "layout": {"page_size": "A5"}})
        configs.append({"id": "verify-edge", "title": "极端边缘配置",
                        "poem_ids": sorted(poem_by_id)[:8],
                        "sections": [{"id": f"verify-sec-{i:02d}", "title": f"第{i}辑", "subtitle": "",
                                      "before_poem_id": pid}
                                     for i, pid in enumerate(sorted(poem_by_id)[:8])],
                        "front_matter": {"title_page": True, "colophon": "verify",
                                         "dedication": "verify"},
                        "layout": {"page_size": "B5", "date_position": "poem_end",
                                   "section_start": "next"}})
    # node -e 的引号在 Windows 命令行转义里不可靠，落临时文件执行更稳。
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as fh:
        fh.write(node_script())
        script_path = Path(fh.name)
    try:
        node = subprocess.run(["node", str(script_path)],
                              input=json.dumps({"poems": corpus, "stanzas": stanzas,
                                                "configs": configs},
                                               ensure_ascii=False).encode("utf-8"),
                              capture_output=True, check=True)
    finally:
        script_path.unlink(missing_ok=True)
    results = json.loads(node.stdout.decode("utf-8"))
    versions_by_id = {config["id"]: config.get("versions", {}) for config in configs}
    bodies_by_id = {config["id"]: config.get("body_nodes", {}) for config in configs}
    reports = [verify_book(result, poem_by_id, versions_by_id.get(result.get("id")),
                           bodies_by_id.get(result.get("id")))
               for result in results]
    summary = {
        "kind": "zhouqingji-book-verify",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_seconds": round(time.time() - started, 2),
        "corpus_poems": len(corpus),
        "books_checked": len(reports),
        "all_ok": all(report["ok"] for report in reports),
        "reports": reports,
    }
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if summary["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
