"""Pure book-order model and a validation bridge for pre-release plans.

No file I/O occurs here. ``legacy_validation_projection`` deliberately cannot
encode every new position; it exists only to reuse the current server's field
validators while the editor and persistent format are switched together.
"""

from __future__ import annotations

from copy import deepcopy


KINDS = frozenset({"poem", "section", "insert"})
FORBIDDEN_IDS = frozenset({"__proto__", "constructor", "prototype"})
POEM_METADATA = ("proof_breaks", "versions", "body_nodes", "tailpieces",
                 "tailpiece_layouts")


def validate(model: dict, known_poems: set[str] | None = None) -> None:
    """Reject duplicated ordering data, orphans and empty sections."""
    if not isinstance(model, dict):
        raise ValueError("诗集编次必须是对象")
    if "poem_ids" in model or "pages" in model:
        raise ValueError("新编次不能同时保存旧作品或插页顺序")
    order = model.get("order")
    sections = model.get("sections")
    inserts = model.get("inserts")
    if not isinstance(order, list) or not isinstance(sections, dict) or not isinstance(inserts, dict):
        raise ValueError("诗集编次、分辑或插入内容格式无效")
    if len(sections) > 100 or len(inserts) > 200:
        raise ValueError("分辑或插入内容过多")
    seen = set()
    used_sections, used_inserts = set(), set()
    section_needs_poem = False
    for block in order:
        if not isinstance(block, dict) or set(block) != {"type", "id"}:
            raise ValueError("编次节点格式无效")
        kind, ident = block["type"], block["id"]
        if kind not in KINDS or not isinstance(ident, str) or not ident or ident in FORBIDDEN_IDS:
            raise ValueError("编次节点类型或标识无效")
        key = (kind, ident)
        if key in seen:
            raise ValueError("编次节点重复")
        seen.add(key)
        if kind == "poem":
            if known_poems is not None and ident not in known_poems:
                raise ValueError("编次引用了不存在的作品")
            section_needs_poem = False
            continue
        content_map = sections if kind == "section" else inserts
        content = content_map.get(ident)
        if not isinstance(content, dict) or content.get("id") != ident:
            raise ValueError("编次引用的内容不存在或标识不一致")
        if kind == "section":
            if "before_poem_id" in content:
                raise ValueError("分辑不能另存第二份位置")
            if section_needs_poem:
                raise ValueError("相邻分辑之间没有作品")
            section_needs_poem = True
            used_sections.add(ident)
        else:
            if "placement" in content:
                raise ValueError("插入内容不能另存第二份位置")
            used_inserts.add(ident)
    if section_needs_poem:
        raise ValueError("末尾分辑没有作品")
    if used_sections != set(sections) or used_inserts != set(inserts):
        raise ValueError("分辑或插入内容有未编入的孤儿记录")
    poems = {block["id"] for block in order if block["type"] == "poem"}
    for key in POEM_METADATA:
        records = model.get(key)
        if isinstance(records, dict) and not set(records).issubset(poems):
            raise ValueError("作品出版记录引用了未入集作品")


def from_legacy(book: dict) -> dict:
    """Read-only conversion; the original row and its other fields stay intact."""
    if not isinstance(book, dict):
        raise ValueError("旧方案必须是对象")
    poems, sections, pages = (book.get("poem_ids", []), book.get("sections", []),
                              book.get("pages", []))
    if not all(isinstance(part, list) for part in (poems, sections, pages)):
        raise ValueError("旧方案内容集合格式无效")
    if not all(isinstance(pid, str) for pid in poems) or len(set(poems)) != len(poems):
        raise ValueError("旧方案作品标识重复或无效")
    poem_set = set(poems)
    sections_at, section_content = {}, {}
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("旧方案分辑格式无效")
        ident, anchor = section.get("id"), section.get("before_poem_id")
        if (not isinstance(ident, str) or not ident or ident in section_content
                or anchor not in poem_set or anchor in sections_at):
            raise ValueError("旧方案分辑标识或位置无效")
        sections_at[anchor] = ident
        section_content[ident] = deepcopy({k: v for k, v in section.items()
                                            if k != "before_poem_id"})
    pages_at, insert_content = {}, {}
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("旧方案插入内容格式无效")
        ident, placement = page.get("id"), page.get("placement", "front")
        if (not isinstance(ident, str) or not ident or ident in insert_content
                or not isinstance(placement, str)
                or not (placement in ("front", "back") or
                        (placement.startswith("before:") and placement[7:] in poem_set))):
            raise ValueError("旧方案插入内容标识或位置无效")
        pages_at.setdefault(placement, []).append(ident)
        insert_content[ident] = deepcopy({k: v for k, v in page.items()
                                          if k != "placement"})

    order = []

    def add_pages(placement: str) -> None:
        order.extend({"type": "insert", "id": ident} for ident in pages_at.get(placement, []))

    add_pages("front")
    for poem_id in poems:
        add_pages(f"before:{poem_id}")
        if poem_id in sections_at:
            order.append({"type": "section", "id": sections_at[poem_id]})
        order.append({"type": "poem", "id": poem_id})
    add_pages("back")
    model = deepcopy(book)
    for key in ("poem_ids", "sections", "pages"):
        model.pop(key, None)
    model.update(order=order, sections=section_content, inserts=insert_content)
    validate(model)
    return model


def legacy_validation_projection(model: dict) -> dict:
    """Borrow old field checks; never use this projection to render or persist."""
    validate(model)
    order = model["order"]
    next_poem = None
    following = [None] * len(order)
    for index in range(len(order) - 1, -1, -1):
        if order[index]["type"] == "poem":
            next_poem = order[index]["id"]
        following[index] = next_poem
    legacy = deepcopy(model)
    for key in ("order", "sections", "inserts"):
        legacy.pop(key, None)
    legacy["poem_ids"] = [b["id"] for b in order if b["type"] == "poem"]
    legacy["sections"] = []
    legacy["pages"] = []
    first_main = next((index for index, block in enumerate(order)
                       if block["type"] != "insert"), len(order))
    for index, block in enumerate(order):
        if block["type"] == "section":
            legacy["sections"].append({**deepcopy(model["sections"][block["id"]]),
                                       "before_poem_id": following[index]})
        elif block["type"] == "insert":
            placement = ("front" if index < first_main else
                         (f"before:{following[index]}" if following[index] else "back"))
            legacy["pages"].append({**deepcopy(model["inserts"][block["id"]]),
                                    "placement": placement})
    return legacy


def from_validated_projection(order: list[dict], cleaned: dict) -> dict:
    """Restore the true order after old field validators normalize content."""
    sections = {item["id"]: {k: deepcopy(v) for k, v in item.items()
                              if k != "before_poem_id"} for item in cleaned["sections"]}
    inserts = {item["id"]: {k: deepcopy(v) for k, v in item.items()
                             if k != "placement"} for item in cleaned["pages"]}
    result = deepcopy(cleaned)
    for key in ("poem_ids", "sections", "pages"):
        result.pop(key, None)
    result.update(order=deepcopy(order), sections=sections, inserts=inserts)
    validate(result)
    return result


def migrate_collection(data: dict, validate_legacy=None) -> dict:
    """Build a schema-3 copy in memory; callers own backup and file replacement.

    No existing book is normalized or timestamped here. Either every row passes
    validation, or an exception is raised with the source object untouched.
    """
    if not isinstance(data, dict) or data.get("schema") != 2 or not isinstance(data.get("books"), list):
        raise ValueError("只能从完整的 schema 2 诗集方案预演迁移")
    output = deepcopy(data)
    migrated, ids = [], set()
    for book in data["books"]:
        model = from_legacy(book)
        ident = model.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError("诗集方案 ID 缺失或重复")
        ids.add(ident)
        if validate_legacy is not None:
            validate_legacy(legacy_validation_projection(model), book)
        migrated.append(model)
    output["schema"] = 3
    output["books"] = migrated
    return output
