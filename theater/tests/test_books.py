# -*- coding: utf-8 -*-
"""诗集工作台侧车测试：只存 ID，原子保存、归档可恢复、损坏文件不覆盖。"""
import json
import hashlib
import shutil
import subprocess
import sys
import tempfile
from functools import wraps
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "src"))

import server as SV  # noqa: E402
import book_order as BO  # noqa: E402


_REAL_UPDATE_BOOK_PROJECTS = SV.update_book_projects


def _update_legacy_fixture(payload):
    """Only for older synthetic fixtures; production saves canonical rows only."""
    if payload.get("action", "save") != "save" or not isinstance(payload.get("book"), dict):
        return _REAL_UPDATE_BOOK_PROJECTS(payload)
    raw = payload["book"]
    if "order" in raw and "poem_ids" not in raw and "pages" not in raw:
        return _REAL_UPDATE_BOOK_PROJECTS(payload)
    cleaned_fixture = SV._clean_book_project(raw)
    canonical = BO.from_legacy(cleaned_fixture)
    result = _REAL_UPDATE_BOOK_PROJECTS({**payload, "book": canonical})
    return {**result, "book": BO.legacy_validation_projection(result["book"])}


def legacy_fixture_test(function):
    """Keep older fixture shorthand local to the storage tests using it."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with patch.object(SV, "update_book_projects", _update_legacy_fixture):
            return function(*args, **kwargs)
    return wrapped


def run_node(script: str, stdin_payload: bytes | None = None, encoding: str = "utf-8"):
    """node -e 的命令行在 Windows 有 32K 上限，嵌入 app.js 片段必须走临时文件。"""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js 不可用")
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        script_path = Path(handle.name)
    try:
        proc = subprocess.run([node, str(script_path)],
                              input=stdin_payload, capture_output=True, check=False)
        if proc.returncode:
            tail = proc.stderr.decode("utf-8", "replace")[-800:]
            raise RuntimeError("node 脚本失败：" + tail)
        return proc
    finally:
        script_path.unlink(missing_ok=True)


def test_book_line_semantics():
    """直接执行 app.js 中的纯排印函数：不复制一份 Python 实现来做假测试。"""
    node = shutil.which("node")
    if not node:
        print("[skip] Node.js 不可用，跳过诗行语义执行测试")
        return
    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("const BOOK_BUNDLED_FONT")
    end = app_js.index("function renderBookPreview", start)
    functions = app_js[start:end]
    content = "\n\u3000\u3000首行\n  次行很长很长很长很长很长很长\n\n\t第三行  \n"
    script = f'''"use strict";
let S = {{stanzas: {{}}, version: "test"}};
function esc(value) {{ return String(value); }}
{functions}
const poem = {{id: "p", content: {json.dumps(content, ensure_ascii=False)}}};
const preserved = bookStanzas(poem);
const parts = bookLineParts("\u3000  \t诗");
const units = bookLineUnits("\u3000" + "长".repeat(22), 22);
    const longPages = bookPaginatePoem({{id: "long", title: "长行", content: "长".repeat(600)}}, "A5");
    const longConfig = bookLayoutConfig({{layout: {{page_size: "A5"}}}}, "A5");
const twoPoems = [
  {{id: "a", title: "甲", content: "风", genre: "现代诗", created: "2024-01", content_hash: "hash-a"}},
  {{id: "b", title: "乙", content: "雨", genre: "现代诗", created: "2024-02", content_hash: "hash-b"}},
];
const centerPoem = {{id: "c", title: "短行", content: "风\\n雨打芭蕉叶", genre: "现代诗", content_hash: "h-c"}};
const maps = {{poem: new Map([...twoPoems, centerPoem].map(poem => [poem.id, poem]))}};
const sectionDraft = {{
  title: "试集", poem_ids: ["a", "b"], layout: {{page_size: "A5"}},
  sections: [{{id: "section-one000", title: "第二辑", subtitle: "入夜", before_poem_id: "b"}}],
}};
const sectionPreview = bookBuildPreview(sectionDraft);
const manifest = bookPrintManifest(sectionDraft, sectionPreview);
const sectionNextDraft = {{...sectionDraft, layout: {{page_size: "A5", section_start: "next"}}}};
const sectionNextPreview = bookBuildPreview(sectionNextDraft);
const dateUnderDraft = {{title: "日期", poem_ids: ["a"], layout: {{page_size: "A5", date_position: "under_title"}}}};
const dateUnderPreview = bookBuildPreview(dateUnderDraft);
const dateUnderMarkup = bookPageMarkup(dateUnderPreview.pages.find(page => page.kind === "poem"), dateUnderDraft,
  dateUnderPreview.pages.findIndex(page => page.kind === "poem"), "A5");
const dateEndDraft = {{title: "日期", poem_ids: ["a"], layout: {{page_size: "A5", date_position: "poem_end"}}}};
const dateEndPreview = bookBuildPreview(dateEndDraft);
const dateEndMarkup = bookPageMarkup(dateEndPreview.pages.find(page => page.kind === "poem"), dateEndDraft,
  dateEndPreview.pages.findIndex(page => page.kind === "poem"), "A5");
const proofPoem = {{id: "proof", title: "词", content: "春风又绿江南岸", content_hash: "hash-proof"}};
const proofDraft = {{proof_breaks: {{proof: {{source_hash: "hash-proof", positions: [2, 4]}}}}}};
const proofText = bookProofText(proofPoem, proofDraft);
const proofStanzas = bookStanzas(proofPoem, proofDraft);
const staleText = bookProofText(proofPoem, {{proof_breaks: {{proof: {{source_hash: "old", positions: [2]}}}}}});
const duplicateBreaks = bookProofText(proofPoem, {{proof_breaks: {{proof: {{source_hash: "hash-proof", positions: [2, 2]}}}}}});
const sectionProofDraft = {{...sectionDraft, proof_breaks: {{a: {{source_hash: "hash-a", positions: [0]}}}}}};
const sectionProofManifest = bookPrintManifest(sectionProofDraft, bookBuildPreview(sectionProofDraft));
S.stanzas = {{p: [0]}};
const explicit = bookStanzas(poem);
const latinWrapped = bookWrapLine("Let the world change like a kaleidoscope today and yesterday forever", 28);
const hardWrapped = bookWrapLine("a".repeat(60), 28);
const tightWrapped = bookWrapLine("长".repeat(29), 28);
const mergedTail = bookWrapLine("长".repeat(56), 28);
const tightLine = bookLineMarkup(mergedTail[1]);
const centerPreview = bookBuildPreview({{poem_ids: ["c"], layout: {{page_size: "A5", block_align: "center"}}}});
const centerIndex = centerPreview.pages.findIndex(page => page.kind === "poem");
const centerMarkup = bookPageMarkup(centerPreview.pages[centerIndex], {{poem_ids: ["c"], layout: {{page_size: "A5", block_align: "center"}}}}, centerIndex, "A5");
const leftMarkup = bookPageMarkup(centerPreview.pages[centerIndex], {{poem_ids: ["c"], layout: {{page_size: "A5", block_align: "left"}}}}, centerIndex, "A5");
const fitsWrapped = bookWrapLine("长".repeat(28), 28);
const datedPages = bookPaginatePoem({{id: "dated", title: "日期", content: Array.from({{length: 25}}, () => "行").join("\\n"), created: "2024-01", content_hash: "h-d"}},
  "A5", undefined, {{layout: {{page_size: "A5", date_position: "poem_end"}}}});
const dateChecks = {{iso: bookPoemDateText({{date_written: "2021-08-07T14:41:43.735000+08:00"}}), month: bookPoemDateText({{date_written: "2021-08"}}), custom: bookPoemDateText({{date_written: "1987年春"}})}};
console.log(JSON.stringify({{preserved, parts, units, explicit, longPages, longConfig, latinWrapped, hardWrapped, tightWrapped, mergedTail, tightLine, centerMarkup, leftMarkup, fitsWrapped, datedPages, dateChecks, sectionPreview, manifest,
  sectionNextPreview, dateUnderMarkup, dateEndMarkup,
  proofText, proofStanzas, staleText, duplicateBreaks, sectionProofManifest}}));
'''
    proc = run_node(script)
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    result = json.loads(proc.stdout)
    assert result["preserved"] == [
        ["\u3000\u3000首行", "  次行很长很长很长很长很长很长"],
        ["\t第三行"],
    ]
    assert result["parts"] == {"text": "诗", "indentEm": 4}
    assert result["units"] == 1, "仅超宽一个字的行整行微缩保留，不再制造孤字续行"
    assert result["explicit"] == [
        ["\u3000\u3000首行"],
        ["  次行很长很长很长很长很长很长", "\t第三行"],
    ]
    assert len(result["longPages"]) == 2, "按新容量 310 全角宽应恰好拆两页"
    assert result["longConfig"]["charsPerLine"] == 27 and result["longConfig"]["firstLines"] == 21
    total_visual = sum(len(block) for block in result["longPages"][0]["stanzas"]) \
        + sum(len(block) for block in result["longPages"][1]["stanzas"])
    assert total_visual == 23, "600 全角宽＝首行 27 + 续行 26×22（含 1 字缩进与 .05em 字距），共 23 个视觉行"
    assert all(len(block) <= result["longConfig"]["firstLines"] for block in result["longPages"][0]["stanzas"])
    assert all(len(block) <= result["longConfig"]["continuationLines"] for block in result["longPages"][1]["stanzas"])
    visual_lines = [line for page in result["longPages"]
                    for block in page["stanzas"] for line in block]
    assert "".join(line["text"] for line in visual_lines) == "长" * 600
    assert visual_lines[0]["machineContinuation"] is False
    assert all(line["machineContinuation"] for line in visual_lines[1:])
    latin_wrapped = result["latinWrapped"]
    assert len(latin_wrapped) >= 2, "33.5 全角宽在每行 28 下必须折行"
    assert " ".join(line["text"].strip() for line in latin_wrapped) \
        == "Let the world change like a kaleidoscope today and yesterday forever", "英文折行不得增删字符"
    for line in latin_wrapped:
        for word in line["text"].split():
            assert word in ("Let", "the", "world", "change", "like", "a", "kaleidoscope",
                            "today", "and", "yesterday", "forever"), f"单词被拦腰切断：{word}"
    hard_wrapped = result["hardWrapped"]
    assert "".join(line["text"] for line in hard_wrapped) == "a" * 60, "超长无断点串硬切后仍须守恒"
    assert all(len(line["text"]) <= 56 for line in hard_wrapped), "硬切片段不得超行宽（半角 0.5 宽）"
    assert result["dateChecks"] == {"iso": "2021-08-07", "month": "2021-08", "custom": "1987年春"}, \
        "时间戳截到年月日，作者自写日期逐字保留"
    tight = result["tightWrapped"]
    assert len(tight) == 1 and tight[0]["tight"] is True, "仅超宽不足一个字的行必须整行保留"
    assert "".join(line["text"] for line in tight) == "长" * 29, "微缩行不得增删字符"
    merged = result["mergedTail"]
    assert len(merged) == 2, "56 全角宽应折 28+28 两行（续行缩进 1 字），而不是孤字挂行"
    assert "".join(line["text"] for line in merged) == "长" * 56, "孤字合并不得增删字符"
    assert merged[1]["tight"] is True and merged[1]["machineContinuation"] is True, \
        "并回的末行必须带微缩标记"
    assert 'class="machine-continuation tight"' in result["tightLine"], "微缩行必须带 tight 类由渲染层收字距"
    center = result["centerMarkup"]
    assert "book-align-center" in center and "--poem-block-shift:11.82em" in center, \
        "居中成块按真实版心宽与该页最长行（5 全角宽）取中平移"
    left = result["leftMarkup"]
    assert "book-align-center" not in left and "--poem-block-shift:0em" in left, "左对齐模式不平移"
    fits = result["fitsWrapped"]
    assert len(fits) == 1 and not fits[0].get("tight"), "恰好排满的行不得误加微缩"
    dated = result["datedPages"]
    assert len(dated) == 2 and sum(len(st) for st in dated[0]["stanzas"]) == 21 \
        and sum(len(st) for st in dated[1]["stanzas"]) == 4, \
        "诗末日期只在本诗末页预留两行，其余页排满"
    assert result["proofText"] == "春风\n又绿\n江南岸"
    assert result["proofStanzas"] == [["春风", "又绿", "江南岸"]]
    assert result["staleText"] == "春风又绿江南岸", "原文变化后旧分行必须停用"
    assert result["duplicateBreaks"] == "春风\n\n又绿江南岸", "同一位置的两个断点表示空行"
    assert result["sectionProofManifest"]["contents"][0]["proof_line_breaks"] == [0]
    assert result["sectionProofManifest"]["contents"][0]["proof_line_breaks_stale"] is False
    section_preview = result["sectionPreview"]
    assert [page["kind"] for page in section_preview["pages"]] == [
        "cover", "toc", "poem", "blank", "section", "poem"]
    assert [(item["kind"], item["title"], item["folio"])
            for item in section_preview["tocItems"]] == [
        ("poem", "甲", 1), ("section", "第二辑", 3), ("poem", "乙", 4)]
    assert section_preview["poemPages"] == 4
    assert [page["kind"] for page in result["sectionNextPreview"]["pages"]] == [
        "cover", "toc", "poem", "section", "poem"]
    assert 'class="book-poem-date under-title">2024-01</time>' in result["dateUnderMarkup"]
    assert 'class="book-poem-date poem-end">2024-01</time>' in result["dateEndMarkup"]
    manifest = result["manifest"]
    assert manifest["kind"] == "zhouqingji-book-manifest" and manifest["app_version"] == "test"
    assert manifest["book"]["layout"]["section_start"] == "recto"
    assert manifest["book"]["layout"]["date_position"] == "none"
    assert manifest["book"]["layout"]["interior_color"] == "warm"
    assert manifest["pagination"] == {
        "total_pages": 6, "body_pages": 4, "front_matter_pages": 0, "toc_pages": 1,
        "front_blank_pages": 0, "section_pages": 1, "essay_pages": 0, "blank_pages": 1,
    }
    assert [(item["id"], item["content_hash"], item["start_folio"], item["section_id"])
            for item in manifest["contents"]] == [
        ("a", "hash-a", 1, None), ("b", "hash-b", 4, "section-one000")]
    assert all("content" not in item for item in manifest["contents"]), "排印清单不复制正文"
    print("[ok] 作者行首缩进、诗节与长行续排估算保真")


@legacy_fixture_test
def test_book_projects_roundtrip():
    old_projects, old_corpus, old_backups = SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS
    with tempfile.TemporaryDirectory(prefix="zq-books-") as td:
        root = Path(td)
        SV.BOOK_PROJECTS = root / "诗集方案.json"
        SV.CORPUS = root / "诗稿.json"
        SV.BACKUPS = root / ".backups"
        poems = [
            {"id": "zq-0001", "title": "一", "content": "风", "author": "a", "content_hash": "h1"},
            {"id": "zq-0002", "title": "二", "content": "雨", "author": "a", "content_hash": "h2"},
        ]
        SV.CORPUS.write_text(json.dumps(poems, ensure_ascii=False), encoding="utf-8")
        try:
            assert SV.load_book_projects() == {"schema": 3, "books": []}
            result = SV.update_book_projects({"action": "save", "book": {
                "title": "集一", "subtitle": "副题", "author": "作者",
                "poem_ids": ["zq-0002", "zq-0001", "zq-0002"],
                "sections": [{
                    "id": "section-opening", "title": "第一辑", "subtitle": "向晚",
                    "before_poem_id": "zq-0002",
                }],
                "sort_mode": "manual",
                "layout": {"page_size": "A5", "start_each_poem": True},
            }})
            book = result["book"]
            stored = SV.load_book_projects()["books"][0]
            assert "order" in stored and "poem_ids" not in stored and "pages" not in stored
            assert book["title"] == "集一", "书名不得被分辑名覆盖"
            assert book["id"].startswith("book-")
            assert book["poem_ids"] == ["zq-0002", "zq-0001"], "去重但保留作者顺序"
            assert book["sections"] == [{
                "id": "section-opening", "title": "第一辑", "subtitle": "向晚",
                "before_poem_id": "zq-0002",
            }]
            assert book["appendices"] == {
                "author_notes": False, "scores": False,
                "author_marks": False, "comments": False,
            }
            assert book["proof_breaks"] == {}
            raw = SV.BOOK_PROJECTS.read_text(encoding="utf-8")
            assert "\"风\"" not in raw and "\"雨\"" not in raw, "侧车不得复制正文"

            result = SV.update_book_projects({"action": "save", "book": {
                **book, "title": "集一·修订", "poem_ids": ["zq-0001"], "sections": [],
            }})
            assert len(result["book_projects"]["books"]) == 1
            assert result["book"]["created_at"] == book["created_at"]
            assert list(SV.BACKUPS.glob("诗集方案-*.json")), "更新前应留备份"

            result = SV.update_book_projects({"action": "save", "book": {
                key: book[key] for key in
                ("id", "title", "author", "poem_ids", "sort_mode", "layout")
            } | {"title": "旧版方案", "poem_ids": ["zq-0002"]}})
            assert result["book"]["sections"] == [], "旧方案缺少 sections 键应等价于空数组"
            assert result["book"]["front_matter"] == {
                "title_page": False, "colophon": "", "dedication": ""}, "旧方案缺少 front_matter 应等价于关闭"

            result = SV.update_book_projects({"action": "save", "book": {
                **book, "title": "带前置页",
                "front_matter": {"title_page": True, "colophon": "  自印试集。\n仅赠友人。  "},
            }})
            assert result["book"]["front_matter"] == {
                "title_page": True, "colophon": "自印试集。\n仅赠友人。",
                "dedication": ""}, "出版说明应去首尾空白但保留换行"

            result = SV.update_book_projects({"action": "save", "book": {
                **book, "title": "带题词",
                "front_matter": {"title_page": True, "colophon": "",
                                 "dedication": "  献给所有走夜路的人。\n以及点灯的人。  "},
            }})
            assert result["book"]["front_matter"] == {
                "title_page": True, "colophon": "",
                "dedication": "献给所有走夜路的人。\n以及点灯的人。"}, "题词应去首尾空白但保留换行"

            result = SV.update_book_projects({"action": "save", "book": {
                **book, "title": "带校样分行",
                "proof_breaks": {"zq-0002": {"source_hash": "h2", "positions": [0, 1]}},
            }})
            assert result["book"]["proof_breaks"] == {
                "zq-0002": {"source_hash": "h2", "positions": [0, 1]}}

            result = SV.update_book_projects({"action": "save", "book": {
                **book, "title": "带插入页",
                "pages": [
                    {"id": "page-preface", "kind": "prose", "title": "序",
                     "body": "  第一段。\n续行并入同段。\n\n第二段。  ",
                     "placement": "front", "toc": True},
                    {"id": "page-blank", "kind": "blank", "title": "x", "body": "y",
                     "placement": "back", "toc": True},
                ],
            }})
            assert result["book"]["pages"] == [
                {"id": "page-preface", "kind": "prose", "title": "序",
                 "body": "第一段。\n续行并入同段。\n\n第二段。", "placement": "front", "toc": True},
                {"id": "page-blank", "kind": "blank", "title": "", "body": "",
                 "placement": "back", "toc": False},
            ], "插入页应归一化：正文换行统一并去首尾、空白页清空字段"
            result = SV.update_book_projects({"action": "save", "book": {
                key: book[key] for key in ("id", "title", "poem_ids", "layout")
            } | {"title": "旧版方案"}})
            assert result["book"]["pages"] == [], "旧方案缺少 pages 键应等价于无插页"


            result = SV.update_book_projects({"action": "save", "book": {
                **result["book"],
                "proof_breaks": {"zq-0002": {"source_hash": "old-hash", "positions": [1]}},
            }})
            assert result["book"]["proof_breaks"]["zq-0002"]["source_hash"] == "old-hash", \
                "原文变化后的旧校样应保留，由前端明确停用"

            result = SV.update_book_projects({"action": "save", "book": {
                **book, "title": "带出版版本",
                "versions": {"zq-0001": {"source_hash": "h1",
                                         "content": "风（出版定稿）\r\n雨落无声。  "}},
            }})
            assert result["book"]["versions"] == {
                "zq-0001": {"source_hash": "h1", "content": "风（出版定稿）\n雨落无声。"},
            }, "出版版本应归一化换行并逐字保留改字"
            result = SV.update_book_projects({"action": "save", "book": {
                key: book[key] for key in ("id", "title", "poem_ids", "layout")
            } | {"title": "旧版方案"}})
            assert result["book"]["versions"] == {}, "旧方案缺少 versions 键应等价于无版本"

            result = SV.update_book_projects({"action": "save", "book": {
                **book, "title": "带版式覆盖",
                "layout": {"page_size": "A5", "profile_id": "qinglang-song-105-18",
                           "profile_overrides": {"bodyPt": 12, "innerMm": 25}},
            }})
            assert result["book"]["layout"] == {
                "page_size": "A5", "start_each_poem": True,
                "running_head": True, "folio": True,
                "section_start": "recto", "date_position": "none",
                "interior_color": "warm", "block_align": "left",
                "running_head_content": "book", "show_numbering": True,
                "continue_hint": False,
                "profile_id": "qinglang-song-105-18",
                "profile_overrides": {"bodyPt": 12.0, "innerMm": 25.0},
            }, "版式 profile 与覆盖参数应归一化存储"

            result = SV.update_book_projects({"action": "archive", "id": book["id"]})
            assert result["book"]["archived_at"]
            result = SV.update_book_projects({"action": "restore", "id": book["id"]})
            assert result["book"]["archived_at"] is None
        finally:
            SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS = old_projects, old_corpus, old_backups
    print("[ok] 诗集方案新建/更新/备份/归档/恢复")


@legacy_fixture_test
def test_book_projects_reject_bad_data():
    old_projects, old_corpus, old_backups = SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS
    with tempfile.TemporaryDirectory(prefix="zq-books-bad-") as td:
        root = Path(td)
        SV.BOOK_PROJECTS = root / "诗集方案.json"
        SV.CORPUS = root / "诗稿.json"
        SV.BACKUPS = root / ".backups"
        SV.CORPUS.write_text('[{"id":"zq-0001","content":"x","content_hash":"h1"}]', encoding="utf-8")
        try:
            bad_cases = [
                ({"title": "", "poem_ids": []}, "不能为空"),
                ({"title": "x", "poem_ids": "zq-0001"}, "字符串数组"),
                ({"title": "x", "poem_ids": ["zq-missing"]}, "找不到作品"),
                ({"title": "x", "poem_ids": [], "sort_mode": "random"}, "排序方式"),
                ({"title": "x", "poem_ids": [], "layout": {"page_size": "Letter"}}, "A5/B5"),
                ({"title": "x", "poem_ids": [], "layout": {"section_start": "left"}}, "分辑扉页"),
                ({"title": "x", "poem_ids": [], "layout": {"date_position": "top"}}, "写作时间"),
                ({"title": "x", "poem_ids": [], "layout": {"interior_color": "rgb"}}, "书芯颜色"),
                ({"title": "x", "poem_ids": [], "layout": {"block_align": "middle"}}, "诗节对齐"),
                ({"title": "x", "poem_ids": [], "layout": {"running_head_content": "poem"}}, "页眉内容"),
                ({"title": "x", "poem_ids": [], "layout": {"show_numbering": "no"}}, "布尔值"),
                ({"title": "x", "poem_ids": [], "layout": {"continue_hint": "yes"}}, "布尔值"),
                ({"title": "x", "poem_ids": ["zq-0001"], "pages": "bad"}, "pages"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "pages": [{"id": "p", "kind": "video"}]}, "款式"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "pages": [{"id": "p", "placement": "before:zq-missing"}]}, "位置无效"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "pages": [{"id": "", "body": ""}]}, "插入页 id"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "pages": [{"id": "p", "toc": "yes"}]}, "布尔值"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "pages": [{"id": "p", "body": "字" * 20001}]}, "过长"),
                ({"title": "x", "poem_ids": ["zq-0001"], "versions": "bad"}, "versions"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "versions": {"zq-missing": {"source_hash": "h", "content": "字"}}}, "已入集"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "versions": {"zq-0001": {"content": "字"}}}, "source_hash"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "versions": {"zq-0001": {"source_hash": "h", "content": "  "}}}, "不能为空"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "versions": {"zq-0001": {"source_hash": "h", "content": "字" * 20001}}}, "过长"),
                ({"title": "x", "poem_ids": [], "layout": {"page_size": "A5", "folio": "yes"}}, "页眉与页码"),
                ({"title": "x", "poem_ids": ["zq-0001"], "sections": "bad"}, "sections"),
                ({"title": "x", "poem_ids": ["zq-0001"], "sections": [
                    {"title": "第一辑", "before_poem_id": "zq-missing"}]}, "起点"),
                ({"title": "x", "poem_ids": ["zq-0001"], "sections": [
                    {"title": "第一辑", "before_poem_id": "zq-0001"},
                    {"title": "第二辑", "before_poem_id": "zq-0001"}]}, "一个分辑"),
                ({"title": "x", "poem_ids": ["zq-0001"], "front_matter": "bad"}, "front_matter"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "front_matter": {"title_page": "yes"}}, "布尔值"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "front_matter": {"colophon": 123}}, "字符串"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "front_matter": {"colophon": "长" * 2001}}, "2000"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "front_matter": {"dedication": 123}}, "字符串"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "front_matter": {"dedication": "长" * 501}}, "500"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "layout": {"page_size": "A5", "profile_id": "fake-profile"}}, "未知版式"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "layout": {"page_size": "A5", "profile_overrides": {"fontName": "x"}}}, "不允许覆盖"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "layout": {"page_size": "A5", "profile_overrides": {"bodyPt": "大"}}}, "必须是数字"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "layout": {"page_size": "A5", "profile_overrides": {"topMm": 3}}}, "超出允许范围"),
                ({"title": "x", "poem_ids": ["zq-0001"], "proof_breaks": []}, "proof_breaks"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "proof_breaks": {"zq-missing": {"source_hash": "h", "positions": [0]}}}, "已入集"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "proof_breaks": {"zq-0001": {"source_hash": "h1", "positions": [2]}}}, "超出当前原文"),
                ({"title": "x", "poem_ids": ["zq-0001"],
                  "proof_breaks": {"zq-0001": {"source_hash": "h1", "positions": [1, 0]}}}, "递增"),
            ]
            for book, expected in bad_cases:
                try:
                    SV.update_book_projects({"action": "save", "book": book})
                    assert False, expected
                except ValueError as exc:
                    assert expected in str(exc)

            SV.BOOK_PROJECTS.write_text("{broken", encoding="utf-8")
            assert SV.load_book_projects().get("error"), "读页面时应给可见错误"
            before = SV.BOOK_PROJECTS.read_bytes()
            try:
                SV.update_book_projects({"action": "save", "book": {"title": "x", "poem_ids": []}})
                assert False, "损坏文件不得被空方案覆盖"
            except ValueError as exc:
                assert "避免覆盖" in str(exc)
            assert SV.BOOK_PROJECTS.read_bytes() == before
        finally:
            SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS = old_projects, old_corpus, old_backups
    print("[ok] 诗集方案严格校验，损坏文件不覆盖")


@legacy_fixture_test
def test_book_projects_storage_integrity():
    """存储地基：未来版本文件只读保护（绝不降级覆盖）、未知键往返保留、
    备份封顶、原子写入无残留临时文件。"""
    old_projects, old_corpus, old_backups = SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS
    with tempfile.TemporaryDirectory(prefix="zq-books-store-") as td:
        root = Path(td)
        SV.BOOK_PROJECTS = root / "诗集方案.json"
        SV.CORPUS = root / "诗稿.json"
        SV.BACKUPS = root / ".backups"
        SV.CORPUS.write_text("[]", encoding="utf-8")
        try:
            # 未来版本创建的文件：读得到（带说明），写入必须拒绝，文件一字不动。
            future = json.dumps({"schema": 4, "books": [{"id": "b-future", "title": "未来书"}]},
                                ensure_ascii=False)
            SV.BOOK_PROJECTS.write_text(future, encoding="utf-8")
            loaded = SV.load_book_projects()
            assert loaded.get("readonly") is True and "更新版本" in loaded.get("error", ""), \
                "高版本文件必须给出升级提示而不是当作损坏"
            assert loaded["books"][0]["title"] == "未来书", "高版本数据必须原样可见"
            before = SV.BOOK_PROJECTS.read_bytes()
            try:
                SV.update_book_projects({"action": "save",
                                         "book": {"title": "x", "poem_ids": []}})
                assert False, "高版本文件不得被旧代码覆盖"
            except ValueError as exc:
                assert "更新版本" in str(exc) and "保存已中止" in str(exc)
            assert SV.BOOK_PROJECTS.read_bytes() == before, "拒绝写入后文件必须逐字节不变"

            # 当前方案：未知顶层键与未知 layout 键必须在往返中保留。
            SV.BOOK_PROJECTS.write_text(json.dumps({"schema": 3, "books": []}, ensure_ascii=False),
                                        encoding="utf-8")
            result = SV.update_book_projects({"action": "save", "book": {
                "title": "带未来键", "poem_ids": [],
                "future_book_key": {"note": "v9 字段"},
                "layout": {"page_size": "A5", "futureLayoutKey": 7},
            }})
            book = result["book"]
            assert book.get("future_book_key") == {"note": "v9 字段"}, "未知顶层键必须原样保留"
            assert book["layout"].get("futureLayoutKey") == 7, "未知 layout 键必须原样保留"
            reread = SV.load_book_projects()
            assert reread["books"][0].get("future_book_key"), "落盘后再读仍保留"

            # 备份封顶 + 原子写入无残留。
            SV.BACKUPS.mkdir(parents=True, exist_ok=True)
            for index in range(205):
                (SV.BACKUPS / f"诗集方案-{index:012d}.json").write_text("{}", encoding="utf-8")
            SV.update_book_projects({"action": "save", "book": {"title": "再次保存", "poem_ids": []}})
            backups = list(SV.BACKUPS.glob("诗集方案-*.json"))
            assert len(backups) <= 200, "备份必须封顶，不随使用无限增长"
            assert not (root / "诗集方案.json.tmp").exists(), "原子写入不得留下临时文件"
        finally:
            SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS = old_projects, old_corpus, old_backups
    print("[ok] 存储地基：高版本只读保护、未知键保留、备份封顶、原子写")


@legacy_fixture_test
def test_book_project_revision_conflict():
    """新客户端带 expected_revision 保存：旧标签页不得覆盖新版本。"""
    old_projects, old_corpus, old_backups = SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS
    with tempfile.TemporaryDirectory(prefix="zq-books-cas-") as td:
        root = Path(td)
        SV.BOOK_PROJECTS = root / "诗集方案.json"
        SV.CORPUS = root / "诗稿.json"
        SV.BACKUPS = root / ".backups"
        SV.CORPUS.write_text('[{"id":"zq-0001","content":"x","content_hash":"h1"}]',
                             encoding="utf-8")
        try:
            first = SV.update_book_projects({"action": "save", "expected_revision": 0,
                                             "book": {"title": "CAS", "poem_ids": ["zq-0001"]}})["book"]
            assert first["revision"] == 1
            stale = json.loads(json.dumps(first, ensure_ascii=False))
            newer = SV.update_book_projects({"action": "save", "expected_revision": 1,
                                             "book": {**first, "title": "新版"}})["book"]
            assert newer["revision"] == 2 and newer["title"] == "新版"
            before = SV.BOOK_PROJECTS.read_bytes()
            try:
                SV.update_book_projects({"action": "save", "expected_revision": 1,
                                         "book": {**stale, "title": "旧页覆盖"}})
                assert False, "旧页面必须发生修订冲突"
            except SV.BookRevisionConflict as exc:
                assert exc.current_revision == 2 and "没有覆盖" in str(exc)
            assert SV.BOOK_PROJECTS.read_bytes() == before, "冲突后磁盘方案必须逐字节不变"
            archived = SV.update_book_projects({"action": "archive", "id": newer["id"],
                                                "expected_revision": 2})["book"]
            assert archived["revision"] == 3 and archived["archived_at"]
        finally:
            SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS = old_projects, old_corpus, old_backups
    print("[ok] 诗集方案 revision/CAS：旧页面拒绝覆盖新版本")




@legacy_fixture_test
def test_book_images():
    """插图图片库：魔数校验、按内容去重落盘、读取与拒收、图片页引用已上传插图。"""
    import base64
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    jpg = b"\xff\xd8\xff" + b"\x00" * 64
    old_dir = SV.BOOK_IMAGES_DIR
    SV.BOOK_IMAGES_DIR = Path(tempfile.mkdtemp(prefix="zq-book-img-"))
    try:
        first = SV.save_book_image({"name": "a.png", "data": base64.b64encode(png).decode()})
        assert len(first["image_id"]) == 16 and first["mime"] == "image/png"
        second = SV.save_book_image({"name": "b.png", "data": base64.b64encode(png).decode()})
        assert second["image_id"] == first["image_id"], "相同内容必须去重为同一张图"
        third = SV.save_book_image({"name": "c.jpg", "data": base64.b64encode(jpg).decode()})
        assert len(list(SV.BOOK_IMAGES_DIR.glob("*.png"))) == 1, "去重后不得重复落盘"
        (SV.BOOK_IMAGES_DIR / "原始私人文件名.png").write_bytes(png)
        library = SV.list_book_images()
        assert {item["image_id"] for item in library} == {first["image_id"], third["image_id"]}
        assert all(set(item) == {"image_id", "mime", "bytes", "updated_at"} for item in library), \
            "图片库接口不得暴露原始文件名或本机路径"
        (SV.BOOK_IMAGES_DIR / "原始私人文件名.png").unlink()

        entry, mime = SV.load_book_image(first["image_id"])
        assert entry.read_bytes() == png and mime == "image/png"
        for bad_id in ("../etc", "zzzz", "0123456789ABCDEF", ""):
            try:
                SV.load_book_image(bad_id)
                assert False, bad_id
            except ValueError:
                pass
        for bad_payload, expected in (
            ({"data": base64.b64encode(b"not an image").decode()}, "只支持"),
            ({"data": base64.b64encode(b"\x00" * (11 * 1024 * 1024)).decode()}, "上限"),
            ({"data": "not-base64!!!"}, "base64"),
            ({}, "缺少"),
        ):
            try:
                SV.save_book_image(bad_payload)
                assert False, expected
            except ValueError as exc:
                assert expected in str(exc)

        old_projects, old_corpus, old_backups = SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS
        with tempfile.TemporaryDirectory(prefix="zq-books-img-") as td:
            root = Path(td)
            SV.BOOK_PROJECTS = root / "诗集方案.json"
            SV.CORPUS = root / "诗稿.json"
            SV.BACKUPS = root / ".backups"
            SV.CORPUS.write_text(json.dumps([{"id": "p", "title": "诗", "content": "一行",
                                               "content_hash": "h"}], ensure_ascii=False), encoding="utf-8")
            SV.BOOK_PROJECTS.write_text(json.dumps({"schema": 3, "books": []}, ensure_ascii=False),
                                        encoding="utf-8")
            try:
                result = SV.update_book_projects({"action": "save", "book": {
                    "title": "带插图", "poem_ids": ["p"],
                    "pages": [{"id": "pg-pic", "kind": "image", "title": "题图",
                               "image_id": first["image_id"], "placement": "front", "toc": False,
                               "image_layout": {"width_pct": 60, "align": "right", "fit": "cover",
                                                "focal_x": 20, "focal_y": 80}}],
                    "tailpieces": {"p": first["image_id"]},
                    "tailpiece_layouts": {"p": {"size": "large", "align": "left"}},
                }})
                assert result["book"]["pages"] == [{
                    "id": "pg-pic", "kind": "image", "title": "题图", "body": "",
                    "placement": "front", "toc": False, "image_id": first["image_id"],
                    "image_layout": {"width_pct": 60, "align": "right", "fit": "cover",
                                     "focal_x": 20, "focal_y": 80},
                }], "图片页应归一化存储并引用已上传插图"
                assert result["book"]["tailpiece_layouts"] == {
                    "p": {"size": "large", "align": "left"}
                }, "尾花大小与位置应严格归一化"
                refs = SV._book_image_references(result["book"])
                assert {(ref["role"], ref["image_id"]) for ref in refs} == {
                    ("full-page", first["image_id"]),
                    ("poem-end-ornament", first["image_id"]),
                }, "服务端应把不同图片字段投影为统一资源关系"
                assert {ref["anchor"]["edge"] for ref in refs} == {"self", "end"}
                bad_tailpiece = dict(result["book"])
                bad_tailpiece["tailpiece_layouts"] = {"p": {"size": "giant", "align": "left"}}
                try:
                    SV.update_book_projects({"action": "save", "book": bad_tailpiece})
                    assert False, "非法尾花宽度必须拒收"
                except ValueError as exc:
                    assert "small/standard/large" in str(exc)
                try:
                    SV.update_book_projects({"action": "save", "book": {
                        "title": "坏图片布局", "poem_ids": [],
                        "pages": [{"id": "pg-layout", "kind": "image",
                                   "image_id": first["image_id"],
                                   "image_layout": {"width_pct": 55}}],
                    }})
                    assert False, "非允许宽度必须拒收"
                except ValueError as exc:
                    assert "40/60/80/100" in str(exc)
                try:
                    SV.update_book_projects({"action": "save", "book": {
                        "title": "坏插图", "poem_ids": [],
                        "pages": [{"id": "pg-bad", "kind": "image",
                                   "image_id": "0" * 16, "placement": "front"}],
                    }})
                    assert False, "未上传的图片必须被拒收"
                except ValueError as exc:
                    assert "已上传" in str(exc)
                # 测试孤儿图片识别与清理
                dry_res = SV.clean_orphan_book_images(dry_run=True)
                assert dry_res["in_use"] == 1 and dry_res["removed"] == 1, "c.jpg 应识别为未被引用的孤儿图片"
                before_images = {f.name: f.read_bytes() for f in SV.BOOK_IMAGES_DIR.iterdir()}
                try:
                    SV.clean_orphan_book_images(dry_run=False)
                    assert False, "引用未完整核验前不得删除"
                except ValueError as exc:
                    assert "不执行删除" in str(exc)
                assert before_images == {f.name: f.read_bytes() for f in SV.BOOK_IMAGES_DIR.iterdir()}
                SV.BOOK_PROJECTS.write_text("{broken", encoding="utf-8")
                try:
                    SV.clean_orphan_book_images(dry_run=True)
                    assert False, "损坏方案不得产生可清理结论"
                except ValueError:
                    pass
                assert before_images == {f.name: f.read_bytes() for f in SV.BOOK_IMAGES_DIR.iterdir()}
                assert any(f.name.startswith(first["image_id"]) for f in SV.BOOK_IMAGES_DIR.iterdir()), "在用 png 必须保留"
            finally:
                SV.BOOK_PROJECTS, SV.CORPUS, SV.BACKUPS = old_projects, old_corpus, old_backups
    finally:
        shutil.rmtree(SV.BOOK_IMAGES_DIR, ignore_errors=True)
        SV.BOOK_IMAGES_DIR = old_dir
    print("[ok] 插图图片库：魔数校验、内容去重、路径安全、私密复用列表、图片页引用")


def test_book_projects_stay_author_side():
    """选稿是作者工作台状态：不进 Git，不进只读手机快照。"""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "corpus/诗集方案.json" in ignore
    old_builder = SV.build_author_state
    SV.build_author_state = lambda: {
        "poems": [], "reads": [], "personas": [], "personas_defaults": [],
        "personas_sidecar": [], "curation": {}, "thread_meta": {},
        "votes": {}, "voter_votes": {}, "persona_echo": {}, "favs": {},
        "stanzas": {}, "calibration": {}, "settings": {}, "version": "test",
        "book_projects": {"schema": 1, "books": [{"id": "book-private"}]},
    }
    try:
        mobile = SV.build_mobile_snapshot(include_wordcloud=False)
        assert "book_projects" not in mobile, "未出版选稿不应送到手机快照"
    finally:
        SV.build_author_state = old_builder

    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    index = (ROOT / "theater" / "src" / "webapp" / "index.html").read_text(encoding="utf-8")
    assert 'seg[0] === "books"' in app_js and 'post("/api/book-projects"' in app_js
    assert "poolScroll" in app_js and "currentPool.scrollTop = poolScroll" in app_js
    assert "bookPreviewText" in app_js and "book-poem-proof" in app_js
    assert 'seg[1] === "preview"' in app_js and "renderBookPreview" in app_js
    assert "bookBuildPreview" in app_js and "bookPaginatePoem" in app_js
    assert 'id="book-preview-open"' in app_js and "book-page-folio" in app_js
    assert 'seg[1] === "print"' in app_js and "renderBookPrint" in app_js
    assert "BOOK_PUBLICATION_PROFILE" in app_js and "window.print()" in app_js
    assert "翻页校样与输出" in app_js and "进入输出排版" in app_js
    assert 'data-book-view="page"' in app_js and 'data-book-view="spreads"' in app_js
    assert "fitBookPrintView" in app_js and "屏幕比例不影响成品" in app_js
    assert "收起方案" in app_js and "已收起" in app_js
    assert "bookContentLines" in app_js and "bookLineParts" in app_js
    assert "bookWrapLine" in app_js and "BOOK_FORBID_LINE_START" in app_js
    assert "bookLineMarkup" in app_js and "--poem-indent" in app_js
    assert "bookSectionMap" in app_js and "openBookSectionEditor" in app_js
    assert 'page.kind === "section"' in app_js and 'page.kind === "blank"' in app_js
    assert "bookPrintManifest" in app_js and 'id="book-print-manifest"' in app_js
    assert "bookFrontMatter" in app_js and "front_matter" in app_js
    assert 'id="book-front-dedication"' in app_js and "题词 / 献词" in app_js
    css = (ROOT / "theater" / "src" / "webapp" / "style.css").read_text(encoding="utf-8")
    assert "book-title-sheet" in css and "book-colophon-sheet" in css
    assert "p.tight" in css and "letter-spacing: -.025em" in css
    assert "book-dedication-sheet" in css and "book-dedication-page" in css
    assert ".book-front-dedication-label" in css and ".book-tone-mono .book-dedication-page div" in css
    assert "width: 148mm; height: 210mm" in css and "width: 176mm; height: 250mm" in css
    assert "font-size: var(--bm-body-pt, 10.5pt); line-height: var(--bm-leading-pt, 18pt)" in css
    assert "--bm-top: 18mm" in css and "--bm-inner: 25mm" in css and "@media print" in css
    assert "text-indent: -1em" in css and "line-break: strict" in css
    assert "margin-top: var(--bm-leading-pt" in css and "letter-spacing: .05em" in css
    assert "letter-spacing: .18em" in css and ".book-typeset-body.book-align-center > header h2" in css
    assert "book-align-center" in css and "book-print-fullscreen" in app_js
    assert "minmax(0, 1fr) repeat(6, auto)" in css and ":fullscreen .book-print-toolbar" in css
    assert ".book-fullscreen-bar" in css and "book-fullscreen-exit" in app_js
    assert 'draggable="true"' in app_js and ".drag-over" in css
    assert "data-move" not in app_js, "上下移按钮应已被拖拽取代"
    assert 'data-folio="${entry.folio}"' in app_js
    assert "white-space: pre-wrap" in css and "word-break: normal" in css
    assert "book-section-page" in css and "book-section-marker" in css
    assert ".book-proof-launch" in css and ".book-print-document.view-spreads" in css
    assert "--book-screen-zoom" in css and "grid-column: 1 / -1" in css
    assert ".book-tone-mono .book-sheet:not(.book-cover-sheet)" in css
    assert 'href="#/books" data-author-only' in index
    print("[ok] 诗集选稿留在作者端，排印保留缩进并区分机器续行")


def test_book_bundled_font():
    font_dir = ROOT / "theater" / "src" / "webapp" / "fonts"
    font = font_dir / "ZQBookSong-Regular.otf"
    license_file = font_dir / "OFL-1.1.txt"
    payload = font.read_bytes()
    assert payload[:4] == b"OTTO" and len(payload) == 11_742_564, "随包 OTF 文件损坏或被替换"
    assert hashlib.sha256(payload).hexdigest() == \
        "e6a906c9f2f472d7b55b23dd80774818d00ae54c5766b682f502c4010822d29d"
    license_text = license_file.read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert "Reserved Font" in license_text and "Name 'Source'" in license_text
    print("[ok] 随包思源宋体文件、来源哈希与 OFL 许可证完整")


def test_book_real_corpus_stress():
    """真实语料分页压力测试：字符守恒、页容量、目录页码、右页起辑、机器续行。

    语料是作者资产，公开仓没有 corpus/诗稿.json 时跳过；测试本身不复制任何正文。
    """
    corpus_path = ROOT / "corpus" / "诗稿.json"
    node = shutil.which("node")
    if not node or not corpus_path.exists():
        print("[skip] Node 或真实语料不可用，跳过真实语料压力测试")
        return
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    corpus_ids = sorted(p["id"] for p in corpus)
    edge_poems = [
        {"id": "stress-empty", "title": "空", "content": "", "content_hash": "h-empty"},
        {"id": "stress-one", "title": "一行", "content": "长" * 310, "content_hash": "h-one"},
        {"id": "stress-lead", "title": "缩进",
         "content": "\u3000\u3000首行\n\t制表符行\n  半角空格行\n\u00a0nbsp行",
         "content_hash": "h-lead"},
    ]
    configs = []
    for layout in ("A5", "B5"):
        configs.append({"name": f"full-{layout}", "layout": layout, "poem_ids": corpus_ids})
        configs.append({"name": f"edge-{layout}", "layout": layout,
                        "poem_ids": ["stress-empty", "stress-one", "stress-lead"]})
        configs.append({"name": f"blank-forced-{layout}", "layout": layout,
                        "poem_ids": corpus_ids[:8],
                        "sections": [{"id": f"sec-{i:02d}", "title": f"第{i}辑",
                                      "subtitle": "", "before_poem_id": pid}
                                     for i, pid in enumerate(corpus_ids[:8])]})
    configs.append({"name": "legacy-no-sections-key-A5", "layout": "A5",
                    "poem_ids": corpus_ids[:10]})
    payload = {
        "configs": configs,
        "poems": [{k: p.get(k) for k in ("id", "title", "content", "content_hash")}
                  for p in corpus] + edge_poems,
    }
    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("const BOOK_BUNDLED_FONT")
    end = app_js.index("function renderBookPreview", start)
    script = f'''"use strict";
const IN = JSON.parse(require("fs").readFileSync(0, "utf8"));
let S = {{stanzas: {{}}, version: "stress"}};
const maps = {{poem: new Map(IN.poems.map(p => [p.id, p]))}};
function esc(value) {{ return String(value); }}
{app_js[start:end]}
const results = [];
for (const cfg of IN.configs) {{
  const draft = {{title: "压测", poem_ids: cfg.poem_ids, layout: {{page_size: cfg.layout}}}};
  if (cfg.sections) draft.sections = cfg.sections;
  const layoutCfg = bookLayoutConfig(draft, cfg.layout);
  const perPoem = {{}};
  for (const pid of cfg.poem_ids) {{
    const poem = maps.poem.get(pid);
    const wrapped = bookStanzas(poem).map(st =>
      st.map(line => bookWrapLine(line, layoutCfg.charsPerLine)));
    const visual = wrapped.flat(2);
    perPoem[pid] = {{
      joined: visual.map(w => w.text).join(""),
      noContinueCount: visual.filter(w => !w.machineContinuation).length,
      pages: bookPaginatePoem(poem, cfg.layout).map(pg => ({{
        units: pg.stanzas.reduce((n, st) => n + st.length, 0) + Math.max(0, pg.stanzas.length - 1),
        isFirst: !pg.continuation,
      }})),
    }};
  }}
  const preview = bookBuildPreview(draft);
  const assembled = {{}};
  for (const page of preview.pages) {{
    if (page.kind !== "poem") continue;
    if (!assembled[page.poemId]) assembled[page.poemId] = [];
    assembled[page.poemId].push(...page.stanzas.flat().map(line => line.text));
  }}
  results.push({{
    name: cfg.name, layout: cfg.layout,
    config: {{charsPerLine: layoutCfg.charsPerLine, firstLines: layoutCfg.firstLines,
             continuationLines: layoutCfg.continuationLines}},
    pageKinds: preview.pages.map(pg => pg.kind),
    entries: preview.entries.map(e => ({{poemId: e.poemId, folio: e.folio}})),
    tocItems: preview.tocItems, assembled,
    perPoem,
  }});
}}
console.log(JSON.stringify(results));
'''
    proc = run_node(script, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    results = json.loads(proc.stdout.decode("utf-8"))
    poem_by_id = {p["id"]: p for p in payload["poems"]}

    def norm_text(poem):
        strip = " \t\u3000\u00a0"
        lines = str(poem.get("content") or "").replace("\r\n", "\n").split("\n")
        kept = [l.rstrip() for l in lines]
        while kept and not kept[0].strip():
            kept.pop(0)
        while kept and not kept[-1].strip():
            kept.pop()
        return "".join(l.lstrip(strip) for l in kept)

    checked = 0
    for res in results:
        name, layout = res["name"], res["layout"]
        assert res["pageKinds"][0] == "cover", name
        assert set(res["assembled"]) == set(res["perPoem"]), \
            f"{name}: 组装页丢失或混入作品"
        toc_pages = res["pageKinds"].count("toc")
        kinds = res["pageKinds"][1 + toc_pages:]
        for idx, kind in enumerate(kinds):
            if kind == "section":
                assert idx % 2 == 0, f"{name}: 分辑扉页未落在右页 idx={idx}"
            if kind == "blank":
                assert idx % 2 == 1, f"{name}: 空白页不在 verso 位 idx={idx}"
        folio_kind = {i + 1: kind for i, kind in enumerate(kinds)}
        for entry in res["entries"]:
            assert folio_kind.get(entry["folio"]) == "poem", \
                f"{name}: 目录页码错位 {entry['poemId']} folio={entry['folio']}"
        for item in res["tocItems"]:
            if item["kind"] == "section":
                assert folio_kind.get(item["folio"]) == "section", \
                    f"{name}: 分辑页码错位 {item['title']}"
        for pid, info in res["perPoem"].items():
            poem = poem_by_id[pid]
            assert info["joined"] == norm_text(poem), f"{name}: 字符守恒失败 {pid}"
            assert "".join(res["assembled"][pid]) == norm_text(poem), \
                f"{name}: 完整书页组装后字符守恒失败 {pid}"
            expected_false = sum(1 for l in str(poem.get("content") or "").split("\n")
                                 if l.strip())
            assert info["noContinueCount"] == expected_false, \
                f"{name}: 续行标记异常 {pid}"
            for i, pg in enumerate(info["pages"]):
                cap = res["config"]["firstLines"] if pg["isFirst"] else res["config"]["continuationLines"]
                assert pg["units"] <= cap, f"{name}: 页容量溢出 {pid} 第{i}页"
            checked += 1
    assert checked >= len(corpus_ids), "压测必须覆盖全部真实作品"
    print(f"[ok] 真实语料压力测试：{len(corpus_ids)} 首 × {len(results)} 配置，组装页字符守恒等不变量成立")


def test_book_front_matter():
    """前置页骨架：书名页/出版说明页进入页序但不计 folio，分辑右页 parity 重算，
    旧方案（无 front_matter 键）输出与基线完全一致。"""
    node = shutil.which("node")
    if not node:
        print("[skip] Node.js 不可用，跳过前置页测试")
        return
    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("const BOOK_BUNDLED_FONT")
    end = app_js.index("function renderBookPreview", start)
    functions = app_js[start:end]
    script = f'''"use strict";
let S = {{stanzas: {{}}, version: "test"}};
function esc(value) {{ return String(value); }}
{functions}
const twoPoems = [
  {{id: "a", title: "甲", content: "风", genre: "现代诗", created: "2024-01", content_hash: "hash-a"}},
  {{id: "b", title: "乙", content: "雨", genre: "现代诗", created: "2024-02", content_hash: "hash-b"}},
];
const maps = {{poem: new Map(twoPoems.map(poem => [poem.id, poem]))}};
const base = {{
  title: "试集", subtitle: "副题", author: "作者", poem_ids: ["a", "b"],
  layout: {{page_size: "A5"}},
  sections: [{{id: "section-one000", title: "第二辑", subtitle: "入夜", before_poem_id: "b"}}],
}};
const legacy = bookBuildPreview(base);
const withTitle = bookBuildPreview({{...base, front_matter: {{title_page: true, colophon: ""}}}});
const withBoth = bookBuildPreview({{...base, front_matter: {{title_page: true, colophon: "  自印试集，仅赠友人。\\n第二行  "}}}});
const colophonOnly = bookBuildPreview({{...base, front_matter: {{colophon: "说明"}}}});
const wsOnly = bookBuildPreview({{...base, front_matter: {{title_page: true, colophon: "   "}}}});
const dedicationOnly = bookBuildPreview({{...base, front_matter: {{dedication: "献给夜间赶路的人"}}}});
const withAll = bookBuildPreview({{...base, front_matter: {{title_page: true, colophon: "自印试集。", dedication: "献给夜间赶路的人"}}}});
const wsDedication = bookBuildPreview({{...base, front_matter: {{dedication: "   "}}}});
const dedicationMarkup = bookPageMarkup(withAll.pages.find(page => page.kind === "dedication"), withAll,
  withAll.pages.findIndex(page => page.kind === "dedication"), "A5");
const manifest = bookPrintManifest({{...base, front_matter: {{title_page: true, colophon: "自印试集，仅赠友人。\\n第二行", dedication: "献给夜间赶路的人"}}}}, withAll);
const marksOn = bookPageMarkup(legacy.pages[2], base, 2, "A5");
const marksOffDraft = {{...base, layout: {{...base.layout, running_head: false, folio: false}}}};
const marksOff = bookPageMarkup(legacy.pages[2], marksOffDraft, 2, "A5");
const standalone = bookStandaloneHtml({{...base, front_matter: {{title_page: true,
  colophon: "自印试集，仅赠友人。\\n第二行", dedication: "献给夜间赶路的人"}}}}, withAll, "/* embedded-book-css */");
const embeddedCss = bookEmbedBundledFontCss(
  '@font-face{{src:url("fonts/ZQBookSong-Regular.otf") format("opentype")}}', "QUJD");
const encoded = bookArrayBufferBase64(Uint8Array.from([65, 66, 67]).buffer);
console.log(JSON.stringify({{legacy, withTitle, withBoth, colophonOnly, wsOnly, dedicationOnly, withAll, wsDedication,
  dedicationMarkup, manifest, marksOn, marksOff, standalone, embeddedCss, encoded}}));
'''
    proc = run_node(script)
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    result = json.loads(proc.stdout)
    assert [p["kind"] for p in result["legacy"]["pages"]] == [
        "cover", "toc", "poem", "blank", "section", "poem"], "无前置页时基线不得漂移"
    assert [p["kind"] for p in result["withTitle"]["pages"]] == [
        "cover", "blank", "title", "toc", "poem", "blank", "section", "poem"]
    assert [p["kind"] for p in result["withBoth"]["pages"]] == [
        "cover", "blank", "title", "colophon", "toc", "poem", "section", "poem"]
    assert [p["kind"] for p in result["colophonOnly"]["pages"]] == [
        "cover", "blank", "colophon", "toc", "poem", "blank", "section", "poem"]
    assert [p["kind"] for p in result["wsOnly"]["pages"]] == [
        "cover", "blank", "title", "toc", "poem", "blank", "section", "poem"], "纯空白出版说明不得成页"
    assert [p["kind"] for p in result["dedicationOnly"]["pages"]] == [
        "cover", "blank", "dedication", "toc", "poem", "blank", "section", "poem"]
    assert [p["kind"] for p in result["withAll"]["pages"]] == [
        "cover", "blank", "title", "colophon", "dedication", "toc",
        "poem", "blank", "section", "poem"], "CMOS 惯例：书名页在先、出版说明在背、题词右页起排"
    assert [p["kind"] for p in result["wsDedication"]["pages"]] == [
        "cover", "toc", "poem", "blank", "section", "poem"], "纯空白题词不得成页"
    assert "book-dedication-sheet" in result["dedicationMarkup"]
    assert 'data-page-kind="dedication"' in result["dedicationMarkup"]
    assert ">献给夜间赶路的人</div>" in result["dedicationMarkup"]
    for name in ("legacy", "withTitle", "withBoth", "colophonOnly", "wsOnly",
                 "dedicationOnly", "withAll", "wsDedication"):
        preview = result[name]
        kinds = preview["pages"]
        assert kinds[0]["kind"] == "cover" and kinds[1]["kind"] != "poem"
        if preview["frontMatterPages"]:
            assert kinds[1].get("zone") == "inside-cover", f"{name}: 前置页前缺封二空白"
            first_front_index = next(i for i, page in enumerate(kinds)
                                     if page["kind"] in ("dedication", "title", "colophon"))
            assert first_front_index % 2 == 0, f"{name}: 第一张前置页必须落在右页"
            dedication_indices = [i for i, page in enumerate(kinds)
                                  if page["kind"] in ("dedication", "title")]
            assert all(index % 2 == 0 for index in dedication_indices), \
                f"{name}: 题词页与书名页都必须落在右页"
        else:
            assert preview["frontBlankPages"] == 0
        section_index = [i for i, p in enumerate(kinds) if p["kind"] == "section"][0]
        assert section_index % 2 == 0, f"{name}: 分辑扉页必须落在右页"
        first_poem = [p for p in preview["entries"] if p["poemId"] == "a"][0]
        assert first_poem["folio"] == 1, f"{name}: 正文 folio 必须仍从 1 开始"
        assert preview["frontMatterPages"] in (0, 1, 2, 3)
    assert result["withBoth"]["frontMatterPages"] == 2
    assert result["withAll"]["frontMatterPages"] == 3
    assert result["withAll"]["frontBlankPages"] == 1
    manifest = result["manifest"]
    assert manifest["book"]["front_matter"] == {
        "title_page": True, "colophon": "自印试集，仅赠友人。\n第二行",
        "dedication": "献给夜间赶路的人"}
    assert manifest["book"]["layout"]["running_head"] is True
    assert manifest["book"]["layout"]["folio"] is True
    assert "book-page-running" in result["marksOn"] and "book-page-folio" in result["marksOn"]
    assert "book-page-running" not in result["marksOff"] and "book-page-folio" not in result["marksOff"]
    assert manifest["pagination"]["front_matter_pages"] == 3
    assert manifest["pagination"]["front_blank_pages"] == 1
    assert manifest["publication_profile"]["bundled_font"] == {
        "family": "ZQ Book Song", "version": "2.003.1",
        "sha256": "e6a906c9f2f472d7b55b23dd80774818d00ae54c5766b682f502c4010822d29d",
        "mime": "font/otf", "source_file": "ZQBookSong-Regular.otf"}
    standalone = result["standalone"]
    assert standalone.startswith("<!doctype html>") and "/* embedded-book-css */" in standalone
    assert "<link" not in standalone and "book-print-manifest" in standalone
    assert standalone.count("data-page-kind=") == len(result["withAll"]["pages"])
    assert "@page { size: 148mm 210mm; margin: 0; }" in standalone
    assert "离线排版快照" in standalone and "打印时使用 100% 缩放" in standalone
    book_json = standalone.split('id="book-print-manifest">', 1)[1].split("</script>", 1)[0]
    assert book_json
    embedded_manifest = json.loads(book_json)
    assert embedded_manifest["pagination"] == manifest["pagination"]
    assert all("content" not in item for item in embedded_manifest["contents"])
    assert 'data:font/otf;base64,QUJD' in result["embeddedCss"]
    assert "fonts/ZQBookSong-Regular.otf" not in result["embeddedCss"]
    assert result["encoded"] == "QUJD", "字体二进制必须可稳定转为 data URI"
    print("[ok] 封面与书芯分离，前置页右页起排且不计 folio，旧方案基线不漂移")


def test_book_page_furniture_options():
    """页眉辑名 / “原汁原味”去编号 / 页底未完提示：旧方案缺省行为不漂移，
    开关分别作用于页眉左栏、诗页眉与目录序号、跨页非末页的页脚小字，并进排印清单。"""
    node = shutil.which("node")
    if not node:
        print("[skip] Node.js 不可用，跳过书页标记选项测试")
        return
    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("const BOOK_BUNDLED_FONT")
    end = app_js.index("function renderBookPreview", start)
    functions = app_js[start:end]
    script = f'''"use strict";
let S = {{stanzas: {{}}, version: "test"}};
function esc(value) {{ return String(value); }}
{functions}
const poems = [
  {{id: "a", title: "甲", content: Array.from({{length: 40}}, () => "行").join("\\n"), genre: "现代诗", created: "2024-01", content_hash: "ha"}},
  {{id: "b", title: "乙", content: "短行", genre: "现代诗", created: "2024-02", content_hash: "hb"}},
  {{id: "c", title: "丙", content: "又一行\\n再一行", genre: "现代诗", created: "2024-03", content_hash: "hc"}},
];
const maps = {{poem: new Map(poems.map(poem => [poem.id, poem]))}};
const base = {{
  title: "试集", author: "作者", poem_ids: ["a", "b", "c"],
  layout: {{page_size: "A5"}},
  sections: [{{id: "section-night000", title: "夜辑", subtitle: "", before_poem_id: "b"}}],
}};
const renderAll = (draft, preview) =>
  preview.pages.map((page, index) => bookPageMarkup(page, draft, index, "A5")).join("\\n");
const legacy = bookBuildPreview(base);
const defaultMarkup = renderAll(base, legacy);
const sectionDraft = {{...base, layout: {{...base.layout, running_head_content: "section"}}}};
const sectionMarkup = renderAll(sectionDraft, bookBuildPreview(sectionDraft));
const bothDraft = {{...base, layout: {{...base.layout, running_head_content: "both"}}}};
const bothMarkup = renderAll(bothDraft, bookBuildPreview(bothDraft));
const noNumberDraft = {{...base, layout: {{...base.layout, show_numbering: false}}}};
const noNumberMarkup = renderAll(noNumberDraft, bookBuildPreview(noNumberDraft));
const hintDraft = {{...base, layout: {{...base.layout, continue_hint: true}}}};
const hintPreview = bookBuildPreview(hintDraft);
const hintMarkup = renderAll(hintDraft, hintPreview);
const manifest = bookPrintManifest(hintDraft, hintPreview);
console.log(JSON.stringify({{legacy, defaultMarkup, sectionMarkup, bothMarkup,
  noNumberMarkup, hintPreview, hintMarkup, manifest}}));
'''
    proc = run_node(script)
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    result = json.loads(proc.stdout)
    default_markup = result["defaultMarkup"]
    assert "<span>01</span>" in default_markup and "第 01 首" in default_markup, \
        "默认方案必须保留“第 N 首”编号"
    assert "book-page-continue" not in default_markup and "（未完）" not in default_markup, \
        "未开开关时旧方案页脚不得出现未完提示"
    assert ">夜辑</span>" not in default_markup, "默认页眉模式左栏仍是书名/诗题交替"
    section_markup = result["sectionMarkup"]
    assert ">夜辑</span>" in section_markup, "辑名模式页眉左栏应显示所属辑名"
    assert "试集" in section_markup, "无所属辑的页面仍回退书名"
    anchor_after = [page for page in result["legacy"]["pages"]
                    if page["kind"] == "poem" and page["title"] == "丙"]
    assert anchor_after and all(page["sectionTitle"] == "夜辑" for page in anchor_after), \
        "分辑锚点之后的诗同样属于该辑，页眉辑名必须覆盖"
    assert "试集 · 夜辑</span>" in result["bothMarkup"], "both 模式应叠显书名与辑名"
    no_number = result["noNumberMarkup"]
    assert "第 0" not in no_number and "<span>01</span>" not in no_number, \
        "原汁原味模式不得出现编号"
    assert "<span></span>" in no_number and "<span>辑</span>" in no_number, \
        "目录序号位保留占位、辑标记不消失"
    assert "<h2>甲</h2>" in no_number, "去编号后诗题仍保留"
    preview = result["hintPreview"]
    open_pages = [page for page in preview["pages"]
                  if page["kind"] == "poem" and not page["isLastPoemPage"]]
    assert open_pages, "40 行长诗必须跨页，否则用例失真"
    hint_markup = result["hintMarkup"]
    assert hint_markup.count("book-page-continue") == len(open_pages), \
        "未完提示必须恰好落在跨页诗的非末页页脚"
    assert "（未完）" in hint_markup
    manifest = result["manifest"]
    assert manifest["book"]["layout"]["continue_hint"] is True
    assert manifest["book"]["layout"]["show_numbering"] is True
    assert manifest["book"]["layout"]["running_head_content"] == "book"
    print("[ok] 页眉辑名/去编号/页底未完三项开关默认不漂移且生效")


def test_book_inserted_pages():
    """长文插入页：序（front）/ 空白页（锚点）/ 诗页（back）三类挂载，
    字符守恒、目录条目与 folio 重算、插入诗页不参与编号且标题不重复渲染。"""
    node = shutil.which("node")
    if not node:
        print("[skip] Node.js 不可用，跳过长文插入页测试")
        return
    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("const BOOK_BUNDLED_FONT")
    end = app_js.index("function renderBookPreview", start)
    functions = app_js[start:end]
    script = f'''"use strict";
let S = {{stanzas: {{}}, version: "test"}};
function esc(value) {{ return String(value); }}
{functions}
const poems = [
  {{id: "a", title: "甲", content: Array.from({{length: 40}}, () => "行").join("\\n"), genre: "现代诗", created: "2024-01", content_hash: "ha"}},
  {{id: "b", title: "乙", content: "短行", genre: "现代诗", created: "2024-02", content_hash: "hb"}},
];
const maps = {{poem: new Map(poems.map(poem => [poem.id, poem]))}};
const base = {{
  title: "试集", author: "作者", poem_ids: ["a", "b"],
  layout: {{page_size: "A5"}},
  sections: [{{id: "section-night000", title: "夜辑", subtitle: "", before_poem_id: "b"}}],
  pages: [
    {{id: "pg-pre", kind: "prose", title: "序", placement: "front", toc: true, body: "字".repeat(1200)}},
    {{id: "pg-mid", kind: "blank", title: "", body: "", placement: "before:b", toc: true}},
    {{id: "pg-tail", kind: "poem", title: "书末小诗", placement: "back", toc: true,
      body: Array.from({{length: 40}}, (_, i) => `行${{i}}`).join("\\n")}},
    {{id: "pg-pic", kind: "image", title: "题图", image_id: "0123456789abcdef",
      placement: "back", toc: false}},
  ],
}};
const preview = bookBuildPreview(base);
const prosePages = preview.pages.filter(page => page.kind === "prose");
const proseText = prosePages.flatMap(page => page.chunks.map(chunk => chunk.text)).join("");
const tailMarkup = preview.pages.slice(-3).map((page, at) =>
  bookPageMarkup(page, base, preview.pages.length - 3 + at, "A5")).join("\\n");
const allMarkup = preview.pages.map((page, index) =>
  bookPageMarkup(page, base, index, "A5")).join("\\n");
const manifest = bookPrintManifest(base, preview);
const standalone = bookStandaloneHtml(base, preview, "/* embedded-book-css */", [],
  {{ "0123456789abcdef": "data:image/png;base64,QUJD" }});
console.log(JSON.stringify({{preview, prosePages, proseText, tailMarkup, allMarkup, manifest, standalone}}));
'''
    proc = run_node(script)
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    result = json.loads(proc.stdout)
    preview = result["preview"]
    kinds = [page["kind"] for page in preview["pages"]]
    assert kinds[0] == "cover" and kinds[1] == "toc", "封面与目录仍在前"
    prose_pages = result["prosePages"]
    assert len(prose_pages) >= 2, "1200 字序文必须跨页，否则用例失真"
    assert kinds[2] == "prose" and kinds[2:2 + len(prose_pages)] == ["prose"] * len(prose_pages), \
        "front 序文必须紧跟目录之后"
    assert prose_pages[0]["continuation"] is False and prose_pages[0]["title"] == "序"
    assert all(page["continuation"] for page in prose_pages[1:])
    assert result["proseText"] == "字" * 1200, "散文分页必须逐字守恒"
    assert prose_pages[1]["chunks"][0]["indent"] is False, "跨页续段不再缩进"
    first_poem = preview["entries"][0]
    assert first_poem["folio"] == len(prose_pages) + 1, "插入页计入正文页码，第一首诗顺延"
    section_at = kinds.index("section")
    assert kinds[section_at - 1] == "blank", "锚点空白页落在第一首诗与分辑扉页之间"
    toc_kinds = [item["kind"] for item in preview["tocItems"]]
    assert toc_kinds == ["essay", "poem", "section", "poem", "essay"], "目录顺序应为 序/甲/辑/乙/书末小诗"
    assert preview["tocItems"][0]["folio"] == 1 and preview["tocItems"][0]["title"] == "序"
    assert preview["tocItems"][-1]["title"] == "书末小诗"
    poem_tail = [page for page in preview["pages"] if page["kind"] == "poem"][-2:]
    assert all(page["inserted"] for page in poem_tail), "back 诗页排在全书末尾"
    assert poem_tail[0]["isLastPoemPage"] is False and poem_tail[-1]["isLastPoemPage"] is True, \
        "插入诗页的未完标记也只在非末页"
    assert kinds[-1] == "image" and preview["pages"][-1]["imageId"] == "0123456789abcdef", \
        "图片页排在最后且携带图片 ID"
    assert 'src="/api/books/image/0123456789abcdef"' in result["tailMarkup"], \
        "屏幕渲染用服务地址取图"
    assert 'data:image/png;base64,QUJD' in result["standalone"], \
        "离线 HTML 必须内嵌图片 data URI"
    assert "/api/books/image/" not in result["standalone"], "离线 HTML 不得残留服务地址"
    assert "第 0" not in result["tailMarkup"], "插入诗页与图片页永不参与“第 N 首”编号"
    assert "<h2>书末小诗</h2>" in result["tailMarkup"]
    assert result["allMarkup"].count("<h2>序</h2>") == 1, "散文标题只渲染在首页"
    assert "book-prose-sheet" in result["allMarkup"]
    manifest = result["manifest"]
    assert len(manifest["book"]["pages"]) == 4
    assert all("body" not in item for item in manifest["book"]["pages"]), "插入页正文不进清单"
    assert manifest["book"]["pages"][-1]["image_id"] == "0123456789abcdef", \
        "图片页在清单中只记 ID 不记二进制"
    assert manifest["pagination"]["essay_pages"] == len(prose_pages)
    print("[ok] 长文插入页：三类挂载、守恒、目录重算、清单只含元数据")


def test_book_published_versions():
    """出版改字最小版：版本激活时分页以版本文本为准；哈希过期自动回退真源；
    校样分行断点作用于生效文本；清单记录源哈希（content_hash）与版本哈希。"""
    node = shutil.which("node")
    if not node:
        print("[skip] Node.js 不可用，跳过出版版本测试")
        return
    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("const BOOK_BUNDLED_FONT")
    end = app_js.index("function renderBookPreview", start)
    functions = app_js[start:end]
    script = f'''"use strict";
let S = {{stanzas: {{}}, version: "test"}};
function esc(value) {{ return String(value); }}
{functions}
const poems = [
  {{id: "a", title: "甲", content: "原稿一行\\n原稿二行", genre: "现代诗", created: "2024-01", content_hash: "ha"}},
];
const maps = {{poem: new Map(poems.map(poem => [poem.id, poem]))}};
const base = {{title: "试集", author: "作者", poem_ids: ["a"], layout: {{page_size: "A5"}}}};
const VERSION_TEXT = "改后一行\\n改后二行";
const verDraft = {{...base, versions: {{a: {{source_hash: "ha", content: VERSION_TEXT}}}}}};
const verPreview = bookBuildPreview(verDraft);
const verText = verPreview.pages.filter(page => page.kind === "poem")
  .flatMap(page => page.stanzas.flat().map(line => line.text)).join("");
const staleDraft = {{...base, versions: {{a: {{source_hash: "OLD", content: "旧版文本"}}}}}};
const staleText = bookBuildPreview(staleDraft).pages.filter(page => page.kind === "poem")
  .flatMap(page => page.stanzas.flat().map(line => line.text)).join("");
const bothDraft = {{...base, versions: {{a: {{source_hash: "ha", content: "改后一行改后二行"}}}},
  proof_breaks: {{a: {{source_hash: "ha", positions: [4]}}}}}};
const proofApplied = bookProofText(poems[0], bothDraft);
const staleProof = bookProofText(poems[0], staleDraft);
const manifest = bookPrintManifest(verDraft, verPreview);
const staleManifest = bookPrintManifest(staleDraft, bookBuildPreview(staleDraft));
console.log(JSON.stringify({{verText, staleText, proofApplied, staleProof, manifest, staleManifest,
  hashSame: bookTextHash("abc") === bookTextHash("abc"),
  hashDiff: bookTextHash("abc") !== bookTextHash("abd"),
  versionHash: bookTextHash(VERSION_TEXT),
  clsRevert: bookClassifyPoemEdit("原稿一行\\n原稿二行", "原稿一行\\n原稿二行\\n"),
  clsBreaks: bookClassifyPoemEdit("原稿一行\\n原稿二行", "原稿\\n一行原稿二行"),
  clsVersion: bookClassifyPoemEdit("原稿一行\\n原稿二行", "改后一行\\n改后二行"),
  clsInvalid: bookClassifyPoemEdit("原稿一行\\n原稿二行", "   ")}}));
'''
    proc = run_node(script)
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    result = json.loads(proc.stdout)
    assert result["verText"] == "改后一行改后二行", "版本激活时分页必须用版本文本"
    assert result["staleText"] == "原稿一行原稿二行", "哈希过期必须回退真源文本"
    assert result["proofApplied"] == "改后一行\n改后二行", "分行断点应作用于生效文本（版本）"
    assert result["staleProof"] == "原稿一行\n原稿二行", "版本停用时分行回退真源文本"
    entry = result["manifest"]["contents"][0]
    assert entry["version_active"] is True and entry["version_stale"] is False
    assert entry["version_hash"] == result["versionHash"]
    assert entry["content_hash"] == "ha", "清单同时保留源哈希"
    stale_entry = result["staleManifest"]["contents"][0]
    assert stale_entry["version_active"] is False and stale_entry["version_stale"] is True
    assert stale_entry["version_hash"] is None
    assert result["hashSame"] and result["hashDiff"], "版本指纹必须稳定且可区分"
    assert result["clsRevert"]["kind"] == "revert", "与真源全同（含空白差异）应清除记录"
    assert result["clsBreaks"] == {"kind": "breaks", "positions": [2]}, \
        "只动换行必须归为校样分行并记录位置"
    assert result["clsVersion"] == {"kind": "version", "content": "改后一行\n改后二行"}, \
        "动了字必须归为出版版本"
    assert result["clsInvalid"]["kind"] == "invalid", "纯空白文本必须拒绝"
    print("[ok] 出版改字：写时复制生效、过期回退真源、清单双哈希")


def test_book_typography_profile():
    """版式 profile：单一数据源、未知 ID 回退默认、覆盖参数按版心比例缩放估算。"""
    node = shutil.which("node")
    if not node:
        print("[skip] Node.js 不可用，跳过版式 profile 测试")
        return
    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("const BOOK_BUNDLED_FONT")
    end = app_js.index("function renderBookPreview", start)
    functions = app_js[start:end]
    script = f'''"use strict";
let S = {{stanzas: {{}}, version: "test"}};
const maps = {{poem: new Map([
  ["a", {{id: "a", title: "甲", content: "风\\n雨", genre: "现代诗", content_hash: "hash-a"}}],
  ["long", {{id: "long", title: "长", content: Array.from({{length: 40}}, () => "abcdefghij").join("\\n"), genre: "现代诗", content_hash: "hash-l"}}],
])}};
function esc(value) {{ return String(value); }}
{functions}
const base = {{title: "试集", poem_ids: ["a"], layout: {{page_size: "A5"}}}};
const baselineConfig = bookLayoutConfig(base, "A5");
const qinglangConfig = bookLayoutConfig({{...base, layout: {{page_size: "A5", profile_id: "qinglang-song-105-18"}}}}, "A5");
const shulangConfig = bookLayoutConfig({{...base, layout: {{page_size: "A5", profile_id: "shulang-song-11-22"}}}}, "A5");
const unknown = {{...base, layout: {{page_size: "A5", profile_id: "nonexistent-px"}}}};
const magicKey = {{...base, layout: {{page_size: "A5", profile_id: "__proto__"}}}};
const baselinePreview = bookBuildPreview(base);
const unknownPreview = bookBuildPreview(unknown);
const overridden = {{...base, poem_ids: ["a", "long"], layout: {{page_size: "A5",
  profile_id: "qinglang-song-105-18",
  profile_overrides: {{bodyPt: 12, leadingPt: 20, innerMm: 25}}}}}};
const overConfig = bookLayoutConfig(overridden, "A5");
const overPreview = bookBuildPreview(overridden);
const manifest = bookPrintManifest(overridden, overPreview);
const style = bookTypographyStyle(overridden, "A5");
console.log(JSON.stringify({{baselineConfig, qinglangConfig, shulangConfig, baselinePreview,
  profileIds: Object.keys(BOOK_TYPOGRAPHY_PROFILES), defaultProfileId: BOOK_DEFAULT_PROFILE_ID,
  overrideKeys: BOOK_PROFILE_OVERRIDE_KEYS,
  unknownConfig: bookLayoutConfig(unknown, "A5"), unknownPreview,
  magicKeyConfig: bookLayoutConfig(magicKey, "A5"), overConfig, overPreview, manifest, style}}));
'''
    proc = run_node(script)
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    result = json.loads(proc.stdout)
    assert result["profileIds"] == list(SV.BOOK_TYPOGRAPHY_PROFILE_IDS), \
        "前端版式 profile ID 与服务端允许清单漂移"
    assert result["defaultProfileId"] == SV.BOOK_DEFAULT_PROFILE_ID, \
        "前端与服务端默认版式 profile 漂移"
    assert set(result["overrideKeys"]) == set(SV.BOOK_PROFILE_OVERRIDE_RANGES), \
        "前端版式覆盖键与服务端校验范围漂移"
    baseline = dict(result["baselineConfig"]); baseline.pop("widthEm")
    assert baseline == {
        "profileId": "qinglang-song-105-18", "topMm": 18, "bottomMm": 20,
        "innerMm": 23, "outerMm": 18, "bodyPt": 10.5, "leadingPt": 18,
        "overrides": {}, "firstLines": 21, "continuationLines": 22,
        "charsPerLine": 27, "tocPerPage": 16,
    }, "默认 profile（清朗宋体）必须逐值等于既有参数"
    assert abs(result["baselineConfig"]["widthEm"] - 107 / (10.5 * 0.3527777)) < 1e-6, \
        "版心宽度（font-em）供居中成块取中使用"
    shulang = dict(result["shulangConfig"]); shulang.pop("widthEm")
    assert shulang == {
        "profileId": "shulang-song-11-22", "topMm": 18, "bottomMm": 20,
        "innerMm": 23, "outerMm": 18, "bodyPt": 11, "leadingPt": 22,
        "overrides": {}, "firstLines": 18, "continuationLines": 19,
        "charsPerLine": 26, "tocPerPage": 16,
    }, "疏朗宋体参数不得随默认版式切换漂移"
    assert result["unknownConfig"] == result["baselineConfig"], "未知 profile 必须回退默认"
    assert result["unknownPreview"]["pages"] == result["baselinePreview"]["pages"], \
        "未知 profile 的分页输出必须与基线一致"
    assert result["magicKeyConfig"] == result["baselineConfig"], \
        "原型链魔法键 profile_id 必须回退默认而不是崩溃"
    over = dict(result["overConfig"]); over.pop("widthEm")
    assert over["charsPerLine"] == 23, "版心不变、字号 12pt 时行宽估算应收窄"
    assert over["firstLines"] == 18 and over["continuationLines"] == 19, "行距 20pt 时行数估算应减少"
    assert over["innerMm"] == 25 and over["bodyPt"] == 12 and over["leadingPt"] == 20
    assert over["overrides"] == {"bodyPt": 12, "leadingPt": 20, "innerMm": 25}
    # 覆盖参数下分页不变量仍然成立：字符守恒 + 页容量
    pages = result["overPreview"]["pages"]
    visual = "".join(
        line["text"] for page in pages if page["kind"] == "poem"
        for block in page["stanzas"] for line in block)
    assert visual == "风雨" + "abcdefghij" * 40, "覆盖参数下字符守恒失败"
    poem_pages = [p for p in pages if p["kind"] == "poem" and p["poemId"] == "long"]
    for i, page in enumerate(poem_pages):
        units = sum(len(st) for st in page["stanzas"]) + max(0, len(page["stanzas"]) - 1)
        cap = over["firstLines"] if not page["continuation"] else over["continuationLines"]
        assert units <= cap, f"覆盖参数下页容量溢出 第{i}页 {units}>{cap}"
    manifest = result["manifest"]
    assert manifest["publication_profile"]["id"] == "qinglang-song-105-18"
    assert manifest["publication_profile"]["body_pt"] == 12
    assert manifest["publication_profile"]["overrides"] == {"bodyPt": 12, "leadingPt": 20, "innerMm": 25}
    assert manifest["book"]["layout"]["profile_id"] == "qinglang-song-105-18"
    style = result["style"]
    assert "--bm-body-pt:12pt" in style and "--bm-inner:25mm" in style and "--bm-leading-pt:20pt" in style
    print("[ok] 版式 profile 单一数据源、未知回退、覆盖估算与清单记录")


def test_book_pdf_environment_status_is_read_only():
    status = SV.book_pdf_dependency_status()
    assert set(status) == {"node", "vivliostyle", "pypdf", "fonttools", "browsers", "ready"}
    assert isinstance(status["ready"], bool)
    assert isinstance(status["node"]["ready"], bool)
    assert isinstance(status["vivliostyle"]["ready"], bool)
    assert isinstance(status["pypdf"]["ready"], bool)
    assert isinstance(status["fonttools"]["ready"], bool)
    assert isinstance(status["browsers"], list)
    print("[ok] 专业 PDF 环境体检只返回能力状态")


def test_book_pdf_build_uses_temporary_private_files():
    original = SV.book_pdf_module

    class FakePdfModule:
        @staticmethod
        def build_pdf(source, output, executable, overwrite, timeout):
            assert source.read_text(encoding="utf-8") == "<!doctype html><p>风</p>"
            assert source.parent != ROOT and output.parent == source.parent
            assert executable is None and overwrite is False and timeout == 300
            output.write_bytes(b"%PDF-test")
            return {"pages": 1}

    try:
        SV.book_pdf_module = lambda: FakePdfModule
        pdf, qa = SV.build_book_pdf_bytes("<!doctype html><p>风</p>", '试/集:*?')
        assert pdf == b"%PDF-test" and qa == {"pages": 1}
        try:
            SV.build_book_pdf_bytes("", "空")
            assert False, "空 HTML 必须拒绝"
        except ValueError:
            pass
    finally:
        SV.book_pdf_module = original
    print("[ok] 一键专业 PDF 只使用临时文件并返回已核验成品")


def test_book_verify_tool():
    """整本校验工具：正常方案通过；引用缺失作品与字符守恒破坏必须暴露（退出码 2）。"""
    sys.path.insert(0, str(ROOT / "theater" / "tools"))
    import book_verify  # noqa: E402
    node = shutil.which("node")
    if not node:
        print("[skip] Node.js 不可用，跳过整本校验工具测试")
        return
    with tempfile.TemporaryDirectory(prefix="zq-book-verify-") as td:
        root = Path(td)
        corpus = root / "诗稿.json"
        projects = root / "诗集方案.json"
        corpus.write_text(json.dumps([
            {"id": "zq-a", "title": "甲", "content": "风\n雨", "content_hash": "ha"},
            {"id": "zq-b", "title": "乙", "content": "长" * 60, "content_hash": "hb"},
        ], ensure_ascii=False), encoding="utf-8")
        projects.write_text(json.dumps({"schema": 2, "books": [{
            "id": "book-t", "title": "验证集", "poem_ids": ["zq-a", "zq-b"],
            "front_matter": {"title_page": True, "colophon": "", "dedication": "献给夜行人"},
            "layout": {"page_size": "A5"},
            "versions": {"zq-a": {"source_hash": "ha", "content": "风（出版定稿）\n雨"}},
        }]}, ensure_ascii=False), encoding="utf-8")
        argv = ["--corpus", str(corpus), "--projects", str(projects)]
        assert book_verify.main(argv) == 0
        report_path = root / "report.json"
        book_verify.main([*argv, "--report", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["all_ok"] and report["books_checked"] == 1
        body = report["reports"][0]
        assert body["pages"]["front_matter"] == 2 and body["first_folio_of_first_poem"] == 1

        projects.write_text(json.dumps({"schema": 2, "books": [{
            "id": "book-bad", "title": "坏方案", "poem_ids": ["zq-missing"],
            "layout": {"page_size": "A5"},
        }]}, ensure_ascii=False), encoding="utf-8")
        assert book_verify.main([*argv, "--report", str(report_path)]) == 2, "引用缺失作品必须失败"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert not report["all_ok"] and any("zq-missing" in issue for issue in report["reports"][0]["issues"])
        corpus.write_text(json.dumps([{
            "id": "zq-tail", "title": "末页", "content": "\n".join(["x"] * 14),
            "content_hash": "ht",
        }], ensure_ascii=False), encoding="utf-8")
        projects.write_text(json.dumps({"schema": 2, "books": [{
            "id": "book-large", "title": "装饰图容量", "poem_ids": ["zq-tail"],
            "tailpieces": {"zq-tail": "0123456789abcdef"},
            "tailpiece_layouts": {"zq-tail": {"size": "large"}},
        }]}, ensure_ascii=False), encoding="utf-8")
        assert book_verify.main([*argv, "--report", str(report_path)]) == 2
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert any("末页余白不足" in issue for issue in report["reports"][0]["issues"])
    print("[ok] 整本校验工具：正常方案通过、坏方案显式失败且报告不复制正文")


def test_book_tailpieces():
    """短诗末页尾花：仅在诗的末页渲染、未设尾花时不出现、跨页首页无尾花、清单记录。"""
    node = shutil.which("node")
    if not node:
        print("[skip] Node.js 不可用，跳过尾花测试")
        return
    app_js = (ROOT / "theater" / "src" / "webapp" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("const BOOK_BUNDLED_FONT")
    end = app_js.index("function renderBookPreview", start)
    functions = app_js[start:end]
    script = f'''"use strict";
let S = {{stanzas: {{}}, version: "test"}};
function esc(value) {{ return String(value); }}
const maps = {{
  poem: new Map([
    ["short", {{id: "short", title: "绝句", content: "窗含西岭千秋雪\\n门泊东吴万里船", created: "2026-09-01", content_hash: "sh1"}}],
    ["long", {{id: "long", title: "长诗", content: Array.from({{length: 26}}, (_, i) => "行" + i).join("\\n"), created: "2026-09-01", content_hash: "ln1"}}],
  ]),
}};
{functions}
const draft = {{
  title: "尾花测试",
  poem_ids: ["short", "long"],
  layout: {{page_size: "A5"}},
  tailpieces: {{
    "short": "0123456789abcdef",
    "long": "fedcba9876543210",
  }},
  tailpiece_layouts: {{
    "short": {{size: "large", align: "left"}},
    "long": {{size: "small", align: "right"}},
  }},
}};
const preview = bookBuildPreview(draft);
const shortPages = preview.pages.filter(p => p.poemId === "short");
const longPages = preview.pages.filter(p => p.poemId === "long");
const shortMarkup = shortPages.map((p, i) => bookPageMarkup(p, draft, i, "A5")).join("\\n");
const longMarkup0 = bookPageMarkup(longPages[0], draft, 0, "A5");
const longMarkupEnd = bookPageMarkup(longPages[longPages.length - 1], draft, longPages.length - 1, "A5");
const manifest = bookPrintManifest(draft, preview);
console.log(JSON.stringify({{
  shortPagesCount: shortPages.length,
  longPagesCount: longPages.length,
  shortMarkup,
  longMarkup0,
  longMarkupEnd,
  manifestTailpieces: manifest.book.tailpieces,
  manifestTailpieceLayouts: manifest.book.tailpiece_layouts,
}}));
'''
    proc = run_node(script)
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    result = json.loads(proc.stdout)
    assert result["shortPagesCount"] == 1
    assert result["longPagesCount"] >= 2
    assert "book-poem-tailpiece" in result["shortMarkup"]
    assert 'src="/api/books/image/0123456789abcdef"' in result["shortMarkup"]
    assert "align-left" in result["shortMarkup"] and "--tailpiece-width:48%" in result["shortMarkup"]
    assert "--tailpiece-height:36mm" in result["shortMarkup"] and "--tailpiece-gap:24pt" in result["shortMarkup"]
    assert "book-poem-tailpiece" not in result["longMarkup0"], "跨页长诗第 1 页绝不得出现尾花"
    assert "book-poem-tailpiece" in result["longMarkupEnd"], "跨页长诗末页必须渲染尾花"
    assert result["manifestTailpieces"] == {
        "short": "0123456789abcdef",
        "long": "fedcba9876543210",
    }, "排印清单应包含尾花元数据"
    assert result["manifestTailpieceLayouts"]["short"] == {"size": "large", "align": "left"}
    print("[ok] 短诗末页余白尾花：末页渲染、跨页首页无尾花、清单记录")


if __name__ == "__main__":
    test_book_line_semantics()
    test_book_projects_roundtrip()
    test_book_projects_reject_bad_data()
    test_book_projects_storage_integrity()
    test_book_project_revision_conflict()
    test_book_images()
    test_book_real_corpus_stress()
    test_book_front_matter()
    test_book_page_furniture_options()
    test_book_inserted_pages()
    test_book_published_versions()
    test_book_verify_tool()
    test_book_typography_profile()
    test_book_pdf_environment_status_is_read_only()
    test_book_pdf_build_uses_temporary_private_files()
    test_book_projects_stay_author_side()
    test_book_bundled_font()
    test_book_tailpieces()
    print("ALL PASS")
