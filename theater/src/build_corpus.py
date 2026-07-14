# -*- coding: utf-8 -*-
"""参考样例（历史脚本，非通用工具）：把华为备忘录导出合并为 corpus/诗稿.json（01 冻结 schema）。

这是作者自己用华为手机备忘录导出时写的一次性入库脚本，字段名和文件路径都
写死对应华为导出的形状，不是"任意设备 → 诗稿.json"的通用转换器。其他设备
（如小米，见 merge_corpus.py）请照着这个模式各自写一份等价脚本，不要指望
改这一份就能通用。

只在 诗稿.json 不存在时全量生成；已存在则拒绝运行（corpus 只进不毁，
后续并入新设备批次时写增量逻辑，不覆盖，见 merge_corpus.py）。
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "corpus" / "raw"
OUT = ROOT / "corpus" / "诗稿.json"

RAW_FILES = [
    RAW / "huawei" / "poems_modern.json",
    RAW / "huawei" / "poems_ci.json",
    RAW / "huawei" / "prose_backup.json",
]


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _loose(s: str) -> str:
    """比较用：备忘录导出会把空格变成不间断空格(\xa0)，比较前归一。"""
    return s.replace("\xa0", " ").replace("　", " ").strip()


def normalize_content(title: str, content: str) -> str:
    """备忘录导出的正文首行重复了标题，入库时剥掉这一行（仅当宽松相等）。"""
    lines = content.split("\n")
    if lines and _loose(lines[0]) == _loose(title):
        rest = "\n".join(lines[1:])
        return rest.lstrip("\n")
    return content


def main():
    if OUT.exists():
        sys.exit("诗稿.json 已存在，拒绝覆盖。并入新批次请写增量脚本。")

    entries = []
    for f in RAW_FILES:
        entries.extend(json.loads(f.read_text(encoding="utf-8")))

    entries.sort(key=lambda e: e["created"])  # 按创建时间发号，id 稳定可读

    corpus = []
    for i, e in enumerate(entries, 1):
        content = normalize_content(e["title"], e["content"])
        corpus.append({
            "id": f"zq-{i:04d}",
            "author": "cyan",
            "title": e["title"],
            "genre": e["genre"],
            "content": content,
            "note": "",
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

    OUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    by_genre = {}
    for c in corpus:
        by_genre[c["genre"]] = by_genre.get(c["genre"], 0) + 1
    print(f"共 {len(corpus)} 条 → {OUT}")
    print("按体裁：", json.dumps(by_genre, ensure_ascii=False))
    print("进读者池：", sum(1 for c in corpus
                        if c["visibility"] == "public" and c["ai_read"]))


if __name__ == "__main__":
    main()
