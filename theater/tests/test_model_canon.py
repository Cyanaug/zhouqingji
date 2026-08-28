# -*- coding: utf-8 -*-
"""模型标签规范化与 thinking/档位后缀剥离测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "src"))

from calibrate import canon_model  # noqa: E402


def test_canon_model():
    # 思考档位 / thinking 后缀剥离
    assert canon_model("vendor-family-9-high") == "vendor-family-9"
    assert canon_model("vendor-family-9-medium") == "vendor-family-9"
    assert canon_model("vendor-family-9-low") == "vendor-family-9"
    assert canon_model("vendor-family-9-minimal") == "vendor-family-9"
    assert canon_model("vendor-family-9-thinking") == "vendor-family-9"
    assert canon_model("vendor-family-9-extended") == "vendor-family-9"

    # 标准基名不变
    assert canon_model("vendor-family-9") == "vendor-family-9"
    assert canon_model("unversioned-model") == "unversioned-model"

    print("[ok] canon_model 思考后缀剥离（无内置精确模型别名）")


if __name__ == "__main__":
    test_canon_model()
