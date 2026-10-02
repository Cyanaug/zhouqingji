# -*- coding: utf-8 -*-
"""Pre-release book-order bridge; synthetic writes only, real plans read-only."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from theater.src import book_order as BO  # noqa: E402
from theater.src import server as SV  # noqa: E402
from theater.tools import book_verify as BV  # noqa: E402
from test_books import run_node  # noqa: E402


class BookOrderTests(unittest.TestCase):
    def test_legacy_conversion_preserves_all_other_fields_and_source(self):
        source = {
            "title": "synthetic", "layout": {"page_size": "A5"},
            "poem_ids": ["p1", "p2"],
            "sections": [{"id": "section-one001", "title": "part", "before_poem_id": "p2"}],
            "pages": [
                {"id": "intro", "kind": "prose", "placement": "front", "body": "a"},
                {"id": "between", "kind": "prose", "placement": "before:p2", "body": "b"},
                {"id": "outro", "kind": "prose", "placement": "back", "body": "c"},
            ],
            "body_nodes": {"p2": {"source_hash": "h", "nodes": []}},
        }
        before = copy.deepcopy(source)
        model = BO.from_legacy(source)
        self.assertEqual(source, before)
        self.assertEqual([(b["type"], b["id"]) for b in model["order"]], [
            ("insert", "intro"), ("poem", "p1"), ("insert", "between"),
            ("section", "section-one001"), ("poem", "p2"), ("insert", "outro")])
        self.assertEqual(model["layout"], source["layout"])
        self.assertEqual(model["body_nodes"], source["body_nodes"])
        self.assertNotIn("poem_ids", model)
        self.assertNotIn("before_poem_id", model["sections"]["section-one001"])
        self.assertNotIn("placement", model["inserts"]["between"])
        self.assertEqual(json.loads(json.dumps(model, ensure_ascii=False)), model)

    def test_normalized_arbitrary_order_keeps_single_truth(self):
        source = {
            "title": "synthetic", "poem_ids": ["p1", "p2"],
            "sections": [{"id": "section-one001", "title": "part", "before_poem_id": "p2"}],
            "pages": [{"id": "page-abc", "kind": "prose", "title": "note",
                       "body": "  text  ", "placement": "before:p2"}],
        }
        model = BO.from_legacy(source)
        # A page after the section, before its first poem, is not expressible in
        # the old placement system. Projection is validation-only; order survives.
        page = model["order"].pop(1)
        model["order"].insert(2, page)
        BO.validate(model, {"p1", "p2"})
        projected = BO.legacy_validation_projection(model)
        poems = [{"id": "p1", "title": "one", "content": "甲", "content_hash": "h1"},
                 {"id": "p2", "title": "two", "content": "乙", "content_hash": "h2"}]
        with patch.object(SV, "load_corpus", return_value=poems):
            cleaned = SV._clean_book_project(projected)
            direct = SV._clean_order_book_project(model)
        normalized = BO.from_validated_projection(model["order"], cleaned)
        self.assertEqual(direct["order"], normalized["order"])
        self.assertEqual(direct["inserts"], normalized["inserts"])
        self.assertEqual(normalized["order"], model["order"])
        self.assertEqual(normalized["inserts"]["page-abc"]["body"], "text")
        self.assertNotIn("placement", normalized["inserts"]["page-abc"])
        self.assertNotIn("poem_ids", normalized)
        self.assertEqual(BO.legacy_validation_projection(normalized)["poem_ids"], ["p1", "p2"])

    def test_canonical_server_cleaner_preserves_revision_boundary_and_rejects_unknown_poem(self):
        source = {"id": "book-aaaaaa", "title": "synthetic", "poem_ids": ["p1"],
                  "sections": [], "pages": [], "custom_note": "retain"}
        model = BO.from_legacy(source)
        model["created_at"] = "original"
        corpus = [{"id": "p1", "title": "one", "content": "甲", "content_hash": "h1"}]
        with patch.object(SV, "load_corpus", return_value=corpus):
            cleaned = SV._clean_order_book_project(model, previous=model)
            self.assertEqual(cleaned["order"], model["order"])
            self.assertEqual(cleaned["created_at"], "original")
            self.assertEqual(cleaned["custom_note"], "retain")
            self.assertNotIn("poem_ids", cleaned)
            self.assertNotIn("pages", cleaned)
            invalid = copy.deepcopy(model)
            invalid["order"][0]["id"] = "missing"
            with self.assertRaisesRegex(ValueError, "不存在的作品"):
                SV._clean_order_book_project(invalid)

    def test_save_route_persists_only_canonical_order_and_keeps_cas(self):
        corpus = [{"id": "p1", "title": "one", "content": "甲", "content_hash": "h1"}]
        draft = BO.from_legacy({"title": "synthetic", "poem_ids": ["p1"],
                                "sections": [], "pages": []})
        with tempfile.TemporaryDirectory() as folder:
            project_path = Path(folder) / "books.json"
            with patch.object(SV, "load_corpus", return_value=corpus), \
                 patch.object(SV, "BOOK_PROJECTS", project_path), \
                 patch.object(SV, "BACKUPS", Path(folder) / "backups"):
                saved = SV.update_book_projects({"action": "save", "book": draft,
                                                 "expected_revision": 0})["book"]
                self.assertEqual(saved["revision"], 1)
                disk = json.loads(project_path.read_text(encoding="utf-8"))
                self.assertEqual(disk["schema"], 3)
                self.assertEqual(disk["books"][0]["order"], draft["order"])
                self.assertNotIn("poem_ids", disk["books"][0])
                self.assertNotIn("pages", disk["books"][0])
                before = project_path.read_bytes()
                with self.assertRaises(SV.BookRevisionConflict):
                    SV.update_book_projects({"action": "save", "book": saved,
                                             "expected_revision": 0})
                self.assertEqual(project_path.read_bytes(), before)

    def test_whole_book_verifier_accepts_canonical_plan(self):
        poem = {"id": "p", "title": "Poem", "content": "甲\n乙", "content_hash": "h"}
        plan = BO.from_legacy({"id": "book-aaaaaa", "title": "Synthetic",
                               "poem_ids": ["p"], "sections": [], "pages": []})
        result = json.loads(run_node(BV.node_script(), json.dumps({
            "poems": [poem], "stanzas": {}, "configs": [plan],
        }, ensure_ascii=False).encode("utf-8")).stdout.decode("utf-8"))[0]
        report = BV.verify_book(result, {"p": poem})
        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(report["poems"], 1)

    def test_rejects_second_order_and_orphan(self):
        model = BO.from_legacy({"poem_ids": ["p"], "sections": [], "pages": []})
        bad = copy.deepcopy(model)
        bad["order"].append({"type": "poem", "id": "p"})
        with self.assertRaisesRegex(ValueError, "重复"):
            BO.validate(bad)
        bad = copy.deepcopy(model)
        bad["inserts"]["x"] = {"id": "x", "body": "text"}
        with self.assertRaisesRegex(ValueError, "孤儿"):
            BO.validate(bad)
        bad = copy.deepcopy(model)
        bad["order"].insert(0, {"type": "section", "id": "s"})
        bad["sections"]["s"] = {"id": "s", "title": "part", "before_poem_id": "p"}
        with self.assertRaisesRegex(ValueError, "第二份位置"):
            BO.validate(bad)
        bad = copy.deepcopy(model)
        bad["poem_ids"] = ["p"]
        with self.assertRaisesRegex(ValueError, "同时保存"):
            BO.validate(bad)

    def test_malformed_legacy_ids_fail_with_useful_error(self):
        with self.assertRaisesRegex(ValueError, "作品标识重复或无效"):
            BO.from_legacy({"poem_ids": [["not", "an", "id"]]})

    def test_collection_migration_is_atomic_and_preserves_metadata(self):
        first = {"id": "book-aaaaaa", "revision": 7, "created_at": "original",
                 "poem_ids": ["p"], "sections": [], "pages": []}
        source = {"schema": 2, "books": [first], "custom": {"keep": True}}
        before = copy.deepcopy(source)
        result = BO.migrate_collection(source)
        self.assertEqual(source, before)
        self.assertEqual(result["schema"], 3)
        self.assertEqual(result["custom"], source["custom"])
        self.assertEqual(result["books"][0]["revision"], 7)
        self.assertEqual(result["books"][0]["created_at"], "original")
        self.assertEqual(result["books"][0]["order"], [{"type": "poem", "id": "p"}])
        with self.assertRaisesRegex(ValueError, "ID 缺失或重复"):
            BO.migrate_collection({"schema": 2, "books": [first, first]})
        self.assertEqual(source, before)

    def test_browser_compiler_uses_true_order_not_validation_projection(self):
        source = (ROOT / "theater/src/webapp/app.js").read_text(encoding="utf-8")
        functions = source[source.index("const BOOK_BUNDLED_FONT"):
                           source.index("function renderBookPreview")]
        run_node('''const assert=require("node:assert/strict");
let S={stanzas:{},version:"synthetic"};
function esc(value){return String(value);}
const maps={poem:new Map([
  ["p1",{id:"p1",title:"One",content:"甲",content_hash:"h1"}],
  ["p2",{id:"p2",title:"Two",content:"乙",content_hash:"h2"}],
])};
''' + functions + '''
const draft={title:"Synthetic",layout:{page_size:"A5"},
  order:[{type:"poem",id:"p1"},{type:"section",id:"s"},
    {type:"insert",id:"i"},{type:"poem",id:"p2"}],
  sections:{s:{id:"s",title:"Part"}},
  inserts:{i:{id:"i",kind:"prose",title:"Aside",body:"hello"}}};
const original=JSON.stringify(draft);
const compiled=bookCompileDocument(draft);
assert.deepEqual(compiled.flow.map(node=>node.id),
  ["poem:p1","section:s","insert:i","poem:p2"]);
assert.equal(compiled.diagnostics.length,0);
assert.equal(compiled.flow.at(-1).sectionTitle,"Part");
const preview=bookBuildPreview(draft);
assert.equal(bookOutputPreflight(draft,preview).inserts,1);
const manifest=bookPrintManifest(draft,preview);
assert.deepEqual(manifest.book.order,draft.order);
assert.equal(manifest.book.sections[0].before_poem_id,undefined);
assert.equal(manifest.book.pages[0].placement,undefined);
assert.deepEqual(manifest.contents.map(entry=>entry.id),["p1","p2"]);
assert.equal(manifest.asset_graph.adapter_version,2);
const html=bookStandaloneHtml(draft,preview,"");
assert.ok(html.includes('id="book-print-manifest"'));
assert.ok(html.includes('"type": "section"'));
const section=preview.pages.findIndex(page=>page.kind==="section");
const insert=preview.pages.findIndex(page=>page.kind==="prose");
const poem=preview.pages.findIndex(page=>page.kind==="poem"&&page.poemId==="p2");
assert.ok(section>=0&&section<insert&&insert<poem);
assert.equal(JSON.stringify(draft),original,"compile/preview must not mutate source");
assert.equal(bookCompileDocument({...draft,pages:[]}).diagnostics[0].blocking,true);
const moved=JSON.parse(original);
bookApplyOrderCommand(moved,{action:"move",index:2,to:3});
assert.deepEqual(moved.order.map(block=>block.id),["p1","s","p2","i"]);
const beforeRejected=JSON.stringify(moved);
assert.throws(()=>bookApplyOrderCommand(moved,{action:"move",index:1,to:3}),
  /末尾分辑没有作品/);
assert.equal(JSON.stringify(moved),beforeRejected,"rejected move must be atomic");
bookApplyOrderCommand(moved,{action:"insert",index:3,
  block:{type:"insert",id:"i2"},content:{id:"i2",kind:"prose",body:"new"}});
assert.deepEqual(moved.order.map(block=>block.id),["p1","s","p2","i2","i"]);
bookApplyOrderCommand(moved,{action:"remove",index:2});
assert.deepEqual(moved.order.map(block=>block.id),["p1","i2","i"]);
assert.deepEqual(moved.sections,{},"empty section cascades with its last poem");
assert.equal(moved.inserts.i2.body,"new");
bookApplyOrderCommand(moved,{action:"update",block:{type:"insert",id:"i2"},
  content:{id:"i2",kind:"prose",body:"revised"},to:2});
assert.deepEqual(moved.order.map(block=>block.id),["p1","i","i2"]);
assert.equal(moved.inserts.i2.body,"revised");
const beforeBadUpdate=JSON.stringify(moved);
assert.throws(()=>bookApplyOrderCommand(moved,{action:"update",block:{type:"insert",id:"i2"},
  content:{id:"i2",kind:"prose",body:"bad",placement:"front"}}),
  /第二份位置/);
assert.equal(JSON.stringify(moved),beforeBadUpdate);
const placement=JSON.parse(original);
bookUpsertPageInDraft(placement,{id:"i2",kind:"prose",body:"fresh",placement:"before:p2"});
assert.deepEqual(placement.order.map(block=>block.id),["p1","s","i","i2","p2"]);
assert.equal(placement.inserts.i2.placement,undefined);
bookUpsertPageInDraft(placement,{id:"front-note",kind:"prose",body:"opening",placement:"front"});
bookUpsertPageInDraft(placement,{id:"after-note",kind:"prose",body:"closing",placement:"back"});
const displayPages=bookInsertedPages(placement);
assert.equal(displayPages.find(page=>page.id==="front-note").placement,"front");
assert.equal(displayPages.find(page=>page.id==="i2").placement,"before:p2");
assert.equal(displayPages.find(page=>page.id==="after-note").placement,"back");
const displayFlow=bookCompileDocument(placement).flow;
assert.equal(displayFlow.find(node=>node.id==="insert:front-note").page.placement,"front");
assert.equal(displayFlow.find(node=>node.id==="insert:after-note").page.placement,"back");
bookUpsertPageInDraft(placement,{id:"i2",kind:"prose",body:"edited",placement:"before:p2"},
  "before:p2");
assert.deepEqual(placement.order.map(block=>block.id),
  ["front-note","p1","s","i","i2","p2","after-note"],
  "editing an unchanged placement keeps its true position");
const beforeBulk=JSON.stringify(placement);
assert.throws(()=>bookApplyOrderCommand(placement,{action:"insert-poems",ids:["p3","p1"]}));
assert.equal(JSON.stringify(placement),beforeBulk,"batch insert must be atomic");
''')

    def test_new_draft_and_clone_follow_container_schema_without_mixing_models(self):
        source = (ROOT / "theater/src/webapp/app.js").read_text(encoding="utf-8")
        functions = source[source.index("function newBookDraft()"):
                           source.index("function bookSnapshot(draft)")]
        run_node('''const assert=require("node:assert/strict");
let S={poems:[],book_projects:{schema:2}};
''' + functions + '''
const old=newBookDraft();
assert.deepEqual(old.poem_ids,[]);
assert.deepEqual(old.sections,[]);
assert.deepEqual(old.pages,[]);
assert.equal(old.order,undefined);
S.book_projects.schema=3;
const fresh=newBookDraft();
assert.deepEqual(fresh.order,[]);
assert.deepEqual(fresh.sections,{});
assert.deepEqual(fresh.inserts,{});
assert.equal(fresh.poem_ids,undefined);
assert.equal(fresh.pages,undefined);
fresh.order=[{type:"section",id:"s"},{type:"poem",id:"p"}];
fresh.sections.s={id:"s",title:"Part"};
const cloned=cloneBook(fresh);
assert.deepEqual(cloned.sections,fresh.sections);
assert.equal(cloned.pages,undefined);
assert.equal(cloned.poem_ids,undefined);
''')

    def test_private_plans_are_read_only(self):
        path = ROOT / "corpus" / "诗集方案.json"
        if not path.is_file():
            self.skipTest("public package has no author plans")
        before = path.read_bytes()
        projects = json.loads(before)
        if projects.get("schema") not in (2, 3):
            self.skipTest("unsupported plan schema")
        for book in projects.get("books", []):
            model = BO.from_legacy(book) if projects["schema"] == 2 else book
            projected = BO.legacy_validation_projection(model)
            cleaned = SV._clean_book_project(projected, previous=projected if projects["schema"] == 3 else book)
            normalized = BO.from_validated_projection(model["order"], cleaned)
            direct = SV._clean_order_book_project(model, previous=model)
            self.assertEqual(direct["order"], normalized["order"])
            self.assertEqual(normalized["order"], model["order"])
            self.assertEqual(len(projected["poem_ids"]),
                             sum(node["type"] == "poem" for node in model["order"]))
        if projects["schema"] == 2:
            candidate = BO.migrate_collection(projects, lambda projected, previous:
                                              SV._clean_book_project(projected, previous=previous))
            self.assertEqual(candidate["schema"], 3)
            self.assertEqual(len(candidate["books"]), len(projects["books"]))
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
