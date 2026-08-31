# -*- coding: utf-8 -*-
"""词云展示层的完整词频回归。"""
import collections
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
import wordcloud_data as W  # noqa: E402


def test_cloud_keeps_compact_nodes_and_full_ranking():
    freq = collections.Counter({f"词{i:03d}": 200 - i for i in range(W.TOPN + 8)})
    docs = [set(freq)]
    result = W._build(freq, docs, [], 1, 20, 8, {"poems": 1})
    assert len(result["words"]) == W.TOPN
    assert len(result["ranking"]) == W.TOPN + 8
    assert result["ranking"][0] == {"w": "词000", "c": 200}
    assert result["ranking"][-1]["w"] == f"词{W.TOPN + 7:03d}"
    assert all("p" not in item and "ex" not in item for item in result["ranking"])
    assert result["coverage"][0]["c"] == 1
    print("[ok] 词云节点保持 120 个 / 完整词频与文档覆盖独立下发")


def test_coverage_counts_a_word_once_per_document():
    freq = collections.Counter({"夏天": 5, "月亮": 2})
    docs = [{"夏天", "月亮"}, {"夏天"}]
    result = W._build(freq, docs, [], 1, 20, 8, {"poems": 2})
    assert result["ranking"][0] == {"w": "夏天", "c": 5}
    assert result["coverage"][0] == {"w": "夏天", "c": 2}
    assert result["coverage"][1] == {"w": "月亮", "c": 1}
    print("[ok] 作品覆盖口径同一首作品内重复只算一次")


if __name__ == "__main__":
    test_cloud_keeps_compact_nodes_and_full_ranking()
    test_coverage_counts_a_word_once_per_document()
    print("ALL PASS")
