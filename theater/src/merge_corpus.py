# -*- coding: utf-8 -*-
"""参考样例（历史脚本，非通用工具）：把小米笔记同步产物增量并入 corpus/诗稿.json。

这是作者从小米笔记（经 mi-note-export 同步 + 自定义预处理）并入第二批语料时
写的一次性脚本，`NEW_RAW`/`SKIP_GUIDS` 都是那一次的具体参数，不是长期运行的
工具。其他设备/来源请照这个"读现有诗稿.json → 取最大 zq-ID → 追加"的模式
各自写一份，不要直接改这份跑自己的数据。

只做追加：读现有 诗稿.json，取其最大 zq-ID，新条目按 created 时间排序后接着编号，
写回前先在 corpus/.backups/ 落一份带时间戳的备份。绝不修改/重排已有条目。
"""
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "corpus" / "诗稿.json"
BACKUP_DIR = ROOT / "corpus" / ".backups"

# 本次要并入的批次；已确认与既有条目内容完全重复的 guid 会被跳过，不生成新 zq-ID
NEW_RAW = ROOT / "corpus" / "raw" / "xiaomi" / "preprocessed-20260712.json"
SKIP_GUIDS = {
    "45222276772676640",  # 十一月末、エルマ计划 - 与 zq-0154 内容完全一致，作者确认跳过
}


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _loose(s: str) -> str:
    return s.replace("\xa0", " ").replace("　", " ").strip()


def normalize_content(title: str, content: str) -> str:
    lines = content.split("\n")
    if lines and _loose(lines[0]) == _loose(title):
        rest = "\n".join(lines[1:])
        return rest.lstrip("\n")
    return content


def main():
    existing = json.loads(OUT.read_text(encoding="utf-8"))
    new_entries = json.loads(NEW_RAW.read_text(encoding="utf-8"))
    new_entries = [e for e in new_entries if e["guid"] not in SKIP_GUIDS]

    max_id = max(int(e["id"].split("-")[1]) for e in existing)
    new_entries.sort(key=lambda e: e["created"])

    added = []
    for i, e in enumerate(new_entries, start=max_id + 1):
        content = normalize_content(e["title"], e["content"])
        added.append({
            "id": f"zq-{i:04d}",
            "author": "cyan",
            "title": e["title"],
            "genre": e["genre"],
            "content": content,
            "note": e.get("note", ""),
            "date_written": None,
            "created": e["created"],
            "modified": e["modified"],
            "visibility": "public",
            "ai_read": e["genre"] != "杂文",
            "background": "",
            "source": [e["source"]],
            "guid": e["guid"],
            "content_hash": sha1(content),
        })

    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy(OUT, BACKUP_DIR / f"诗稿-{ts}.json")

    merged = existing + added
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    by_genre = {}
    for c in added:
        by_genre[c["genre"]] = by_genre.get(c["genre"], 0) + 1
    print(f"原有 {len(existing)} 首，新增 {len(added)} 首，合计 {len(merged)} 首")
    print("新增按体裁：", json.dumps(by_genre, ensure_ascii=False))
    print("跳过重复：", len(SKIP_GUIDS))
    print(f"新增 ID 区间：zq-{max_id+1:04d} ~ zq-{max_id+len(added):04d}")


if __name__ == "__main__":
    if not OUT.exists():
        sys.exit("诗稿.json 不存在，请先跑 build_corpus.py 做首次全量生成。")
    main()
