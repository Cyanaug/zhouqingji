# -*- coding: utf-8 -*-
"""视觉编稿地基回归：真实纯函数与临时侧车，不操作作者方案。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_books import ROOT, SV, BO, run_node
sys.path.insert(0, str(ROOT))
from theater.tools import book_verify as BV


class BookDocumentTests(unittest.TestCase):
    def test_body_nodes_explicit_copy_and_current_schema(self):
        poem = {"id": "p", "title": "原诗", "content": "甲\n乙", "content_hash": "source-hash"}
        legacy = {"id": "book-legacy", "revision": 1, "title": "原书", "poem_ids": ["p"],
                  "versions": {"p": {"source_hash": "source-hash", "content": "旧改稿"}}}
        original = BO.from_legacy(legacy)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_bytes = json.dumps({"schema": 3, "books": [original]}, ensure_ascii=False).encode("utf-8")
            project_path = root / "books.json"
            project_path.write_bytes(old_bytes)
            with patch.object(SV, "load_corpus", return_value=[poem]), \
                 patch.object(SV, "BOOK_PROJECTS", project_path), \
                 patch.object(SV, "BACKUPS", root / "backups"):
                self.assertEqual(SV.load_book_projects()["schema"], 3)
                self.assertEqual(project_path.read_bytes(), old_bytes, "reading must not rewrite project data")
                copy_book = {**original, "id": "", "revision": 0, "title": "原书（编稿副本）",
                             "content_edit_copy": True, "versions": {},
                             "body_nodes": {"p": {"source_hash": "source-hash", "nodes": [
                                 {"id": "block-first1", "kind": "text", "text": "甲\n"},
                                 {"id": "block-second2", "kind": "text", "text": "乙"}]}}}
                saved = SV.update_book_projects({"book": copy_book})["book"]
                self.assertEqual(saved["body_nodes"]["p"]["nodes"], copy_book["body_nodes"]["p"]["nodes"])
                self.assertEqual(saved["versions"], {})
                disk = SV.load_book_projects()
                self.assertEqual(disk["schema"], 3)
                self.assertEqual(disk["books"][0], original, "original book row must remain unchanged")
                self.assertEqual(len(list((root / "backups").glob("*.json"))), 1)
                self.assertEqual(next((root / "backups").glob("*.json")).read_bytes(), old_bytes)
                unsupported = json.dumps({"schema": 2, "books": [legacy]}, ensure_ascii=False).encode("utf-8")
                project_path.write_bytes(unsupported)
                self.assertTrue(SV.load_book_projects().get("readonly"))
                with self.assertRaisesRegex(ValueError, "旧格式"):
                    SV.update_book_projects({"book": copy_book})
                self.assertEqual(project_path.read_bytes(), unsupported)
                project_path.write_bytes(json.dumps(disk, ensure_ascii=False).encode("utf-8"))
                copy_book["id"] = saved["id"]
                copy_book["revision"] = saved["revision"]
                for bad_nodes in (
                    [{"id": "block-first1", "kind": "text", "text": "甲"},
                     {"id": "block-first1", "kind": "text", "text": "乙"}],
                    [{"id": "block-third3", "kind": "image", "text": "甲"}],
                    [{"id": "block-third3", "kind": "text", "text": ""}],
                ):
                    bad = copy.deepcopy(copy_book)
                    bad["body_nodes"]["p"]["nodes"] = bad_nodes
                    before = project_path.read_bytes()
                    with self.assertRaises(ValueError):
                        SV.update_book_projects({"book": bad})
                    self.assertEqual(project_path.read_bytes(), before)
                conflict = copy.deepcopy(copy_book)
                conflict["versions"] = legacy["versions"]
                with self.assertRaises(ValueError):
                    SV.update_book_projects({"book": conflict})

    def test_body_nodes_drive_preview_and_manifest_without_mutating_source(self):
        source = (ROOT / "theater/src/webapp/app.js").read_text(encoding="utf-8")
        functions = source[source.index("const BOOK_BUNDLED_FONT"):source.index("function renderBookPreview")]
        run_node('''const assert=require("node:assert/strict");
let S={stanzas:{},version:"synthetic"};
function esc(value){return String(value);}
const poem={id:"p",title:"Poem",content:"甲\\n乙",content_hash:"hash"};
const maps={poem:new Map([["p",poem]])};
''' + functions + '''
const draft={title:"Copy",poem_ids:["p"],layout:{page_size:"A5"},versions:{},proof_breaks:{},
  content_edit_copy:true,body_nodes:{p:{source_hash:"hash",nodes:[
    {id:"block-first1",kind:"text",text:"甲\\n"},
    {id:"block-second2",kind:"text",text:"改乙"}]}}};
const before=JSON.stringify(draft);
assert.equal(bookPoemBaseText(poem,draft),"甲\\n改乙");
const preview=bookBuildPreview(draft);
assert.equal(preview.pages.filter(p=>p.kind==="poem").flatMap(p=>p.stanzas.flat().map(l=>l.text)).join(""),"甲改乙");
const manifest=bookPrintManifest(draft,preview);
assert.equal(manifest.contents[0].body_nodes_active,true);
assert.equal(manifest.contents[0].version_active,false);
assert.equal(manifest.contents[0].body_nodes_hash,bookTextHash("甲\\n改乙"));
assert.equal(JSON.stringify(draft),before);
draft.body_nodes.p.source_hash="stale";
assert.equal(bookPoemBaseText(poem,draft),"甲\\n乙");
assert.equal(bookPrintManifest(draft,bookBuildPreview(draft)).contents[0].body_nodes_stale,true);
''')

    def test_body_nodes_pass_whole_book_verifier(self):
        poem = {"id": "p", "title": "Test", "content": "甲\n乙", "content_hash": "hash"}
        config = {"id": "book-copy01", "title": "Copy", "poem_ids": ["p"],
                  "layout": {"page_size": "A5"}, "versions": {}, "proof_breaks": {},
                  "content_edit_copy": True,
                  "body_nodes": {"p": {"source_hash": "hash", "nodes": [
                      {"id": "block-first1", "kind": "text", "text": "甲\n"},
                      {"id": "block-second2", "kind": "text", "text": "改乙"}]}}}
        result = json.loads(run_node(BV.node_script(), json.dumps({
            "poems": [poem], "stanzas": {}, "configs": [config]
        }, ensure_ascii=False).encode("utf-8")).stdout.decode("utf-8"))[0]
        report = BV.verify_book(result, {"p": poem}, {}, config["body_nodes"])
        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(BV.effective_text(poem, {}, config["body_nodes"]), "甲改乙")

    def test_layout_correction_discards_detached_pages(self):
        source = (ROOT / "theater/src/webapp/app.js").read_text(encoding="utf-8")
        function = source[source.index("async function applyBookBlockCorrections"):source.index("// 出版版本")]
        run_node('''const assert=require("node:assert/strict");
let pending,measurements=0;
const poem={style:{}};
const sheet={isConnected:true,querySelector(){return poem;}};
const document={querySelectorAll(){return [sheet];}};
const bookWorkspace={blockShiftPx:new Map()};
function waitForBookLayoutReady(){return new Promise(resolve=>{pending=resolve;});}
function measureBookBlockShiftPx(offset){measurements++;return {[offset]:12};}
function toast(){}
''' + function + '''
(async()=>{
  const obsolete=applyBookBlockCorrections(5);
  sheet.isConnected=false;pending();await obsolete;
  assert.equal(measurements,0);
  assert.equal(bookWorkspace.blockShiftPx.size,0);
  sheet.isConnected=true;
  const current=applyBookBlockCorrections(7);pending();await current;
  assert.equal(measurements,1);
  assert.equal(bookWorkspace.blockShiftPx.get(7),12);
  assert.equal(poem.style.transform,"translateX(12px)");
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_unified_book_outline_and_insert_move(self):
        source = (ROOT / "theater/src/webapp/app.js").read_text(encoding="utf-8")
        insert_functions = source[source.index("function bookInsertedPageRow"):source.index("function openBookPageEditor")]
        outline_function = source[source.index("function bookSignaturesMarkup"):source.index("function removeBookPoem")]
        script = '''const assert = require("node:assert/strict");
const maps={poem:new Map([["p1",{id:"p1",title:"One"}],["p2",{id:"p2",title:"Two"}]])};
const esc=value=>String(value);
const yearOf=()=>"2026";
const poemSize=()=>3;
const bookInsertedPages=draft=>draft.pages;
const bookImageLayout=()=>({widthPct:60,align:"center",fit:"contain"});
const bookProofBreakRecord=()=>({active:false,stale:false});
const bookVersionRecord=()=>({active:false,stale:false});
const bookBodyNodesRecord=()=>({active:false,stale:false});
function bookCompileDocument(draft){
  const byId=new Map(draft.pages.map(page=>[page.id,page]));
  const insert=id=>({type:"insert",page:byId.get(id)});
  return {flow:[insert("front-a"),insert("front-b"),
    {type:"poem",poemId:"p1",poemIndex:0},insert("before-p2"),
    {type:"section",section:{id:"sec",title:"Part",before_poem_id:"p2"}},
    {type:"poem",poemId:"p2",poemIndex:1},insert("back")]};
}
''' + insert_functions + outline_function + '''
const draft={poem_ids:["p1","p2"],pages:[
  {id:"front-a",kind:"prose",title:"Preface A",body:"a",placement:"front"},
  {id:"back",kind:"prose",title:"Afterword",body:"z",placement:"back"},
  {id:"front-b",kind:"image",title:"Preface B",body:"",placement:"front"},
  {id:"before-p2",kind:"blank",title:"Pause",body:"",placement:"before:p2"},
]};
const outline=bookSignaturesMarkup(draft);
const labels=["<b>Preface A</b>","<b>Preface B</b>","<b>One</b>","<b>Pause</b>","<b>Part</b>","<b>Two</b>","<b>Afterword</b>"];
const locations=labels.map(label=>outline.indexOf(label));
assert.ok(locations.every(position=>position>=0));
assert.deepEqual(locations,[...locations].sort((a,b)=>a-b));
assert.equal((outline.match(/data-index=/g)||[]).length,2);
assert.match(outline,/data-page-move="front-a" data-dir="1"/);
assert.doesNotMatch(outline,/data-page-move="back"/);
const original=JSON.stringify(draft);
assert.equal(bookMoveInsertedPage(draft,"front-a",1),true);
assert.deepEqual(draft.pages.map(page=>page.id),["front-b","back","front-a","before-p2"]);
assert.equal(bookMoveInsertedPage(draft,"back",1),false);
assert.equal(bookMoveInsertedPage(draft,"front-a",2),false);
assert.notEqual(JSON.stringify(draft),original);
'''
        run_node(script)

    def test_client_history_undo_redo(self):
        source = (ROOT / "theater/src/webapp/app.js").read_text(encoding="utf-8")
        functions = source[source.index("function newBookDraft"):source.index("function bookDateKey")]
        script = '''const assert = require("node:assert/strict");
let S = {poems:[], book_projects:{books:[{id:"book-abcdef",revision:4,title:"Original",poem_ids:[]}]}};
const BOOK_NEW = "__new__";
const bookWorkspace = {activeId:null,drafts:new Map(),dirty:new Set(),histories:new Map(),
  savedSnapshots:new Map(),saveState:new Map()};
let renders = 0;
function renderBooks(){ renders += 1; }
''' + functions + '''
bookWorkspace.activeId="book-abcdef";
const draft=currentBookDraft();
assert.equal(draft.revision,4);
draft.title="Changed";
markBookDirty();
assert.equal(bookWorkspace.dirty.has("book-abcdef"),true);
assert.equal(bookWorkspace.histories.get("book-abcdef").past.length,1);
bookUndo();
assert.equal(currentBookDraft().title,"Original");
assert.equal(bookWorkspace.dirty.has("book-abcdef"),false);
bookRedo();
assert.equal(currentBookDraft().title,"Changed");
assert.equal(bookWorkspace.dirty.has("book-abcdef"),true);
assert.equal(renders,2);
'''
        run_node(script)

    def test_save_from_preview_preserves_in_flight_edits(self):
        source = (ROOT / "theater/src/webapp/app.js").read_text(encoding="utf-8")
        functions = source[source.index("function newBookDraft"):source.index("function bookDateKey")]
        script = '''const assert=require("node:assert/strict");
let S={poems:[],book_projects:{books:[{id:"book-abcdef",revision:4,title:"Original",subtitle:"",poem_ids:[]}]}};
const BOOK_NEW="__new__";
const bookWorkspace={activeId:null,drafts:new Map(),dirty:new Set(),histories:new Map(),
  savedSnapshots:new Map(),saveState:new Map(),saving:new Set()};
let renders=0, scrolled=0, pending, payload, messages=[];
const window={scrollY:120,scrollTo(x,y){scrolled=y;}};
const location={hash:"#/books/preview"};
const document={querySelectorAll(){return [];},querySelector(){return null;}};
function renderBooks(){renders+=100;}
function renderBookPreview(){renders+=1;}
function renderBookPrint(){renders+=10;}
function toast(message){messages.push(message);}
function post(path,body){payload=body;return new Promise(resolve=>{pending=resolve;});}
''' + functions + '''
(async()=>{
  bookWorkspace.activeId="book-abcdef";
  const draft=currentBookDraft();
  draft.title="First";markBookDirty();
  const first=saveCurrentBookDraft();
  assert.equal(bookWorkspace.saving.has("book-abcdef"),true);
  assert.equal(payload.book.title,"First");
  draft.subtitle="Later";markBookDirty();
  assert.equal(bookSaveStateText("book-abcdef"),"正在保存…");
  const duplicate=saveCurrentBookDraft();
  await duplicate;
  pending({book:{...payload.book,revision:5,created_at:"server-date",updated_at:"server-date",
    layout:{page_size:"A5"}},book_projects:{books:[]}});
  await first;
  assert.equal(renders,1);
  assert.equal(scrolled,120);
  assert.equal(currentBookDraft().subtitle,"Later");
  assert.equal(currentBookDraft().revision,5);
  assert.equal(currentBookDraft().updated_at,"server-date");
  assert.deepEqual(currentBookDraft().layout,{page_size:"A5"});
  assert.equal(bookWorkspace.dirty.has("book-abcdef"),true);
  assert.equal(bookWorkspace.saving.size,0);
  bookUndo();
  assert.equal(currentBookDraft().subtitle,"");
  assert.equal(currentBookDraft().revision,5);
  assert.equal(bookWorkspace.dirty.has("book-abcdef"),false);
  assert.ok(messages.some(message=>message.includes("仍待保存")));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        run_node(script)

    def test_save_new_book_conflict_and_navigation(self):
        source = (ROOT / "theater/src/webapp/app.js").read_text(encoding="utf-8")
        functions = source[source.index("function newBookDraft"):source.index("function bookDateKey")]
        run_node('''const assert=require("node:assert/strict");
let S={poems:[],book_projects:{books:[]}};
const BOOK_NEW="__new__";
const bookWorkspace={activeId:BOOK_NEW,drafts:new Map(),dirty:new Set(),histories:new Map(),
  savedSnapshots:new Map(),saveState:new Map(),saving:new Set()};
let renders=0,pending,rejectPending,payload;
const window={scrollY:0,scrollTo(){}};
const location={hash:"#/books/print"};
const document={querySelectorAll(){return [];},querySelector(){return null;}};
function renderBooks(){renders++;}
function renderBookPreview(){renders++;}
function renderBookPrint(){renders++;}
function toast(){}
function post(path,body){payload=body;return new Promise((resolve,reject)=>{pending=resolve;rejectPending=reject;});}
''' + functions + '''
(async()=>{
  currentBookDraft().title="New";markBookDirty();
  const first=saveCurrentBookDraft();
  assert.equal(payload.expected_revision,0);
  currentBookDraft().subtitle="Not sent";markBookDirty();
  location.hash="#/all";
  pending({book:{...payload.book,id:"book-abcdef",revision:1},book_projects:{books:[]}});
  await first;
  assert.equal(renders,0,"Late save must not overwrite another screen");
  assert.equal(bookWorkspace.activeId,"book-abcdef");
  assert.equal(currentBookDraft().subtitle,"Not sent");
  assert.equal(bookWorkspace.drafts.has(BOOK_NEW),false);
  assert.equal(bookWorkspace.dirty.has("book-abcdef"),true);
  location.hash="#/books/print";
  const conflict=saveCurrentBookDraft();
  assert.equal(payload.expected_revision,1);
  const error=new Error("Conflict");error.status=409;
  rejectPending(error);await conflict;
  assert.equal(bookWorkspace.saveState.get("book-abcdef"),"conflict");
  assert.equal(bookWorkspace.saving.size,0);
  assert.equal(currentBookDraft().subtitle,"Not sent");
  assert.equal(currentBookDraft().revision,1);
  const retry=saveCurrentBookDraft();
  pending({book:{...payload.book,revision:2},book_projects:{books:[]}});await retry;
  assert.equal(bookWorkspace.dirty.size,0);
  assert.equal(bookWorkspace.saveState.get("book-abcdef"),"saved");
  assert.equal(currentBookDraft().revision,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')

    def test_storage_extensions_title_and_deletion(self):
        poem = {"id": "p", "content": "abcd", "content_hash": "h"}
        ext = {"future": {"nested": [1, {"keep": True}]}, "_future": "keep"}
        raw = {
            "id": "book-abcdef", "title": "BOOK", "poem_ids": ["p"], **ext,
            "pages": [{"id": "page-a", "kind": "prose", "title": "INSERT",
                       "body": "text", "placement": "front", **ext}],
            "sections": [{"id": "section-abcdef", "title": "section", "before_poem_id": "p", **ext}],
            "versions": {"p": {"source_hash": "h", "content": "abcdef", **ext}},
            "proof_breaks": {"p": {"source_hash": "h", "positions": [5], **ext}},
            "front_matter": {"title_page": True, **ext}, "appendices": ext,
            "layout": {"page_size": "A5", **ext},
        }
        raw = BO.from_legacy(raw)
        untouched = copy.deepcopy(raw)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(SV, "load_corpus", return_value=[poem]), \
                 patch.object(SV, "BOOK_PROJECTS", root / "books.json"), \
                 patch.object(SV, "BACKUPS", root / "backups"):
                saved = SV.update_book_projects({"book": raw})["book"]
                self.assertEqual(saved["title"], "BOOK")
                self.assertEqual(raw, untouched)
                records = [saved, saved["inserts"]["page-a"], saved["sections"]["section-abcdef"],
                           saved["versions"]["p"], saved["proof_breaks"]["p"],
                           saved["layout"], saved["front_matter"], saved["appendices"]]
                for record in records:
                    self.assertEqual(record["future"], ext["future"])
                    self.assertEqual(record["_future"], "keep")
                self.assertEqual(SV.load_book_projects()["books"][0], saved)
                # Older UI omits extension keys: preserve them from the same stored ID.
                stripped = copy.deepcopy(saved)
                stripped["inserts"]["page-a"].pop("future")
                stripped["inserts"]["page-a"]["title"] = "CHANGED"
                saved = SV.update_book_projects({"book": stripped})["book"]
                self.assertEqual(saved["inserts"]["page-a"]["future"], ext["future"])
                self.assertEqual(saved["title"], "BOOK")
                # Deleting a whole known record must NOT resurrect its unknown fields.
                saved.update(order=[{"type": "poem", "id": "p"}], inserts={}, sections={},
                             versions={}, proof_breaks={})
                final = SV.update_book_projects({"book": saved})["book"]
                for key in ("inserts", "sections", "versions", "proof_breaks"):
                    self.assertFalse(final[key])
                final["order"].insert(0, {"type": "insert", "id": "blank"})
                final["inserts"]["blank"] = {"id": "blank", "kind": "blank", "title": "ignored"}
                self.assertEqual(SV.update_book_projects({"book": final})["book"]["title"], "BOOK")
                final["order"].insert(1, {"type": "section", "id": "section-generated"})
                final["sections"] = {"section-generated": {"id": "section-generated",
                                                         "title": "generated", **ext}}
                generated = SV.update_book_projects({"book": final})["book"]["sections"]["section-generated"]
                self.assertEqual(generated["id"], "section-generated")
                self.assertEqual(generated["future"], ext["future"])

    def test_effective_text_binding(self):
        with patch.object(SV, "load_corpus", return_value=[{"id": "p", "content": "abcd", "content_hash": "h"}]):
            raw = {"title": "B", "poem_ids": ["p"],
                   "versions": {"p": {"source_hash": "h", "content": "abcdef"}},
                   "proof_breaks": {"p": {"source_hash": "h", "positions": [5, 5]}}}
            saved = SV._clean_book_project(raw)
            self.assertEqual(saved["proof_breaks"]["p"]["positions"], [5, 5])
            raw["proof_breaks"]["p"]["positions"] = [7]
            with self.assertRaises(ValueError):
                SV._clean_book_project(raw)
            # Bound to an older effective text: keep stale record for author recovery.
            raw["proof_breaks"]["p"]["text_hash"] = SV._book_text_hash("abcdefg")
            self.assertEqual(SV._clean_book_project(raw)["proof_breaks"]["p"]["positions"], [7])

    def test_document_pagination_and_upload_contract(self):
        source = (ROOT / "theater/src/webapp/app.js").read_text(encoding="utf-8")
        functions = source[source.index("const BOOK_BUNDLED_FONT"):source.index("function renderBookPreview")]
        script = '''const assert = require("node:assert/strict");
let S = {stanzas:{}, version:"test"};
function esc(v) { return String(v); }
const poem = {id:"p", title:"P", content:"abcdef", content_hash:"h"};
const maps = {poem:new Map([["p",poem]])};
''' + functions + '''
for (const pageSize of ["A5","B5"]) {
  for (const sectionStart of ["next","recto"]) {
    const draft = {title:"B",poem_ids:["p"],layout:{page_size:pageSize,section_start:sectionStart},
      sections:[{id:"sec",title:"S",before_poem_id:"p"}],
      pages:Array.from({length:40},(_,i)=>({id:"i"+i,kind:"prose",title:"I"+i,body:"x",placement:"front",toc:true}))};
    const before = JSON.stringify(draft);
    const doc = bookCompileDocument(draft);
    assert.equal(doc.flow.length,42);
    assert.deepEqual(doc,bookCompileDocument(draft));
    const preview = bookBuildPreview(draft);
    const printed = preview.pages.filter(p=>p.kind==="toc").flatMap(p=>p.entries);
    assert.equal(printed.length,42);
    assert.deepEqual(printed,preview.tocItems);
    if(sectionStart==="recto") assert.equal(preview.pages.findIndex(p=>p.kind==="section")%2,0);
    assert.equal(JSON.stringify(draft),before);
    assert.equal(preview.jumps[0].label,"封面");
    assert.ok(preview.jumps.every(jump=>jump.label.trim().length>0));
  }
}
const seq = {poem_ids:["p"],pages:[
  {id:"back",kind:"blank",placement:"back"},
  {id:"front",kind:"prose",body:"x",placement:"front"},
  {id:"before",kind:"poem",body:"x",placement:"before:p"}],
  sections:[{id:"s",title:"S",before_poem_id:"p"}],tailpieces:{p:"0123456789abcdef"}};
assert.deepEqual(bookCompileDocument(seq).flow.map(n=>n.id),["insert:front","insert:before","section:s","poem:p","insert:back"]);
assert.equal(bookCompileDocument(seq).attachments[0].anchor.blockId,"poem:p");
assert.equal(bookCompileDocument(seq).attachments[0].role,"poem-end-ornament");
for(const bad of [
  {poem_ids:["missing"]},
  {poem_ids:["p"],pages:[{id:"f",kind:"future",body:"keep"}]},
  {poem_ids:["p"],pages:[{id:"f",kind:"prose",body:"keep",placement:"before:missing"}]},
  {poem_ids:["p"],pages:[{id:"f",kind:"image",placement:"front"}]},
  {poem_ids:["p","p"]}
]) {
  const preview=bookBuildPreview(bad);
  assert.ok(preview.diagnostics.length);
  assert.throws(()=>bookPrintManifest(bad,preview),/不能完整导出/);
}
const bound={versions:{p:{source_hash:"h",content:"abcdef"}},
  proof_breaks:{p:{source_hash:"h",text_hash:bookTextHash("abcdef"),positions:[5]}}};
assert.equal(bookProofText(poem,bound),"abcde\\nf");
bound.versions.p.content="xyz";
assert.equal(bookProofBreakRecord(bound,poem).stale,true);
assert.equal(bookProofText(poem,bound),"xyz");
// Full final page cannot acquire an unbudgeted tailpiece; removing it clears the blocker.
const full={id:"full",title:"F",content:Array(40).fill("x").join(String.fromCharCode(10)),content_hash:"f"};
maps.poem.set(full.id,full);
const withImage={poem_ids:["full"],tailpieces:{full:"0123456789abcdef"},layout:{page_size:"A5"}};
const overflow=bookBuildPreview(withImage);
assert.ok(overflow.diagnostics.some(d=>d.code==="tailpiece-overflow"));
assert.equal(overflow.pages.at(-1).tailpiecePlacement.fits,false);
assert.throws(()=>bookRequireCompleteLayout(overflow));
delete withImage.tailpieces.full;
assert.equal(bookBuildPreview(withImage).diagnostics.length,0);
const imageDraft={title:"Images",poem_ids:["p"],pages:[{id:"image-a",kind:"image",
  image_id:"0123456789abcdef",title:"Caption",placement:"front",toc:false,
  image_layout:{width_pct:60,align:"right",fit:"cover",focal_x:20,focal_y:80}}]};
const imagePreview=bookBuildPreview(imageDraft);
const imagePage=imagePreview.pages.find(page=>page.kind==="image");
assert.ok(imagePreview.jumps.some(jump=>jump.label.includes("图片页·Caption")));
const imageMarkup=bookPageMarkup(imagePage,imageDraft,imagePreview.pages.indexOf(imagePage),"A5");
assert.match(imageMarkup,/data-page-index=/);
assert.match(imageMarkup,/align-right/);
assert.match(imageMarkup,/--book-image-width:60%/);
assert.match(imageMarkup,/object-fit:cover/);
assert.match(imageMarkup,/object-position:20% 80%/);
const imageManifest=bookPrintManifest(imageDraft,imagePreview);
assert.deepEqual(imageManifest.book.pages[0].image_layout,
  {width_pct:60,align:"right",fit:"cover",focal_x:20,focal_y:80});
assert.equal(imageManifest.production_boundary.output_class,"reading-book-proof-source");
assert.equal(imageManifest.production_boundary.interior_only,false);
assert.equal(imageManifest.production_boundary.preview_cover_page,true);
assert.equal(imageManifest.production_boundary.pdf_verification,"pending");
assert.equal(imageManifest.production_boundary.press_ready,false);
assert.deepEqual(imageManifest.asset_graph.images,[{
  id:"image:image-a",image_id:"0123456789abcdef",role:"full-page",
  anchor:{block_id:"insert:image-a",edge:"self"},
  layout:{width_pct:60,align:"right",fit:"cover",focal_x:20,focal_y:80}
}]);
assert.deepEqual(bookOutputPreflight(imageDraft,imagePreview),{
  ready:true,problems:[],pageSize:"A5",pages:imagePreview.pages.length,poems:1,inserts:1,
  imagePlacements:1,uniqueImages:1
});
const blockedPreflight=bookOutputPreflight(withImage,overflow);
assert.equal(blockedPreflight.ready,false);
assert.ok(blockedPreflight.problems.some(item=>item.code==="tailpiece-overflow"));
const tailPage={poem,poemId:"p",isLastPoemPage:true,inserted:false,continuation:false,
  stanzas:[Array.from({length:14},()=>({text:"x"}))]};
const tailCfg=bookLayoutConfig({layout:{page_size:"A5"}},"A5");
const smallTail={tailpieces:{p:"0123456789abcdef"},tailpiece_layouts:{p:{size:"small"}}};
const largeTail={tailpieces:{p:"0123456789abcdef"},tailpiece_layouts:{p:{size:"large"}}};
assert.equal(bookTailpiecePlacement(tailPage,smallTail,tailCfg).fits,true);
assert.equal(bookTailpiecePlacement(tailPage,largeTail,tailCfg).fits,false);
const oldPages=[{id:"shared",kind:"image",title:"Old",image_id:"0123456789abcdef",
  image_layout:{width_pct:60},future:{keep:true}}];
const nextPage={id:"shared",kind:"prose",title:"New",body:"Body",placement:"front",toc:true};
const updatedPages=bookUpsertInsertedPage(oldPages,nextPage);
assert.equal(updatedPages[0].title,"New");
assert.deepEqual(updatedPages[0].future,{keep:true});
assert.equal("image_id" in updatedPages[0],false);
assert.equal("image_layout" in updatedPages[0],false);
assert.equal(oldPages[0].kind,"image");
assert.equal(bookUpsertInsertedPage([],nextPage).length,1);
(async()=>{
  let request;
  global.fetch=async(url,options)=>{ request={url,...options}; return {ok:true,json:async()=>({image_id:"0123456789abcdef"})}; };
  const image=await uploadBookImage({name:"image.png",size:3,arrayBuffer:async()=>new Uint8Array([1,2,3]).buffer});
  assert.equal(image.image_id,"0123456789abcdef");
  assert.equal(request.url,"/api/books/image");
  assert.equal(request.headers["Content-Type"],"application/json");
  assert.deepEqual(JSON.parse(request.body),{name:"image.png",data:"AQID"});
  global.fetch=async()=>({ok:false,status:400,json:async()=>({error:"bad image"})});
  await assert.rejects(()=>uploadBookImage({size:0,arrayBuffer:async()=>new ArrayBuffer(0)}),/bad image/);
  console.log(JSON.stringify({unicodeHash:bookTextHash("a😀字")}));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = json.loads(run_node(script).stdout.decode("utf-8"))
        self.assertEqual(result["unicodeHash"], SV._book_text_hash("a😀字"))


if __name__ == "__main__":
    unittest.main()
