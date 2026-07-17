# -*- coding: utf-8 -*-
"""侧车合并/写入的自包含测试（零依赖，直接跑：python theater/tests/test_sidecars.py）。

覆盖 persona 外挂的后端：load_personas 合并语义 + set_personas 写 API。
全程把 PERSONAS_SIDECAR 指到临时文件，绝不碰真实 corpus/。
前端非诗榜（app.js）由 node --check + 浏览器人工验证，不在此列。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "src"))
sys.path.insert(0, str(ROOT / "theater" / "runners"))

import server as SV       # noqa: E402
import runner as RN       # noqa: E402

TMP = Path(tempfile.gettempdir())


def test_load_personas_merge():
    """runner/server 同一套合并口径：无侧车向后兼容 + 覆盖/新增/隐藏。"""
    base = json.loads(SV.PERSONAS.read_text(encoding="utf-8"))
    for mod in (SV, RN):
        mod.PERSONAS_SIDECAR = TMP / "sidecar_absent_xyz.json"
        got = mod.load_personas()
        assert got == base, f"{mod.__name__}: 无侧车应字节级等于随附默认"

    d0, d1 = base[0]["persona_id"], base[1]["persona_id"]
    side = [
        {"persona_id": d0, "persona": "★改写★"},               # 部分覆盖
        {"persona_id": "reader-x", "name": "自建", "persona": "外挂",
         "knows_诠释": False, "knows_date": False},              # 新增
        {"persona_id": d1, "hidden": True},                     # 隐藏
    ]
    sc = TMP / "sidecar_merge.json"
    sc.write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
    for mod in (SV, RN):
        mod.PERSONAS_SIDECAR = sc
        m = {p["persona_id"]: p for p in mod.load_personas()}
        ids = [p["persona_id"] for p in mod.load_personas()]
        assert m[d0]["persona"] == "★改写★", f"{mod.__name__}: 同 id 覆盖"
        assert m[d0]["name"] == base[0]["name"], f"{mod.__name__}: 部分覆盖保留未给字段"
        assert "reader-x" in m and ids[-1] == "reader-x", f"{mod.__name__}: 新增追加末位"
        assert d1 not in m, f"{mod.__name__}: hidden 撤下"
    sc.unlink()
    print("[ok] load_personas 合并（无侧车/覆盖/新增/隐藏）")


def test_set_personas():
    """写 API：upsert / 新人设必填 / 去重 / 类型 / 空则删。"""
    sc = TMP / "sidecar_write.json"
    if sc.exists():
        sc.unlink()
    SV.PERSONAS_SIDECAR = sc
    base = json.loads(SV.PERSONAS.read_text(encoding="utf-8"))
    d0 = base[0]["persona_id"]

    SV.set_personas({"personas": [
        {"persona_id": d0, "persona": "★改写★"},
        {"persona_id": "reader-x", "name": "自建X", "persona": "外挂人设"},
    ]})
    assert sc.exists()
    m = {p["persona_id"]: p for p in SV.load_personas()}
    assert m[d0]["persona"] == "★改写★" and m[d0]["name"] == base[0]["name"]
    assert m["reader-x"]["knows_诠释"] is False, "新人设 knows_* 缺省补 False"

    for bad, kw in [
        ({"personas": [{"persona_id": "bad"}]}, "必须含 name 与 persona"),
        ({"personas": [{"persona_id": d0}, {"persona_id": d0}]}, "重复"),
        ({"personas": [{"persona_id": d0, "hidden": "yes"}]}, "布尔"),
        ({"personas": "nope"}, "数组"),
    ]:
        try:
            SV.set_personas(bad)
            assert False, f"应报错：{kw}"
        except ValueError as e:
            assert kw in str(e), f"错误信息应含「{kw}」，实得：{e}"

    SV.set_personas({"personas": []})
    assert not sc.exists(), "空列表应删侧车"
    assert SV.load_personas() == base, "删侧车后回随附默认"
    print("[ok] set_personas 写 API（upsert/必填/去重/类型/空则删）")


if __name__ == "__main__":
    test_load_personas_merge()
    test_set_personas()
    print("ALL PASS")
