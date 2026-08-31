# -*- coding: utf-8 -*-
"""手机只读入口、移动快照与单 HTML 导出的安全边界测试（零网络依赖）。"""
import json
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "theater" / "src"))
import server as S  # noqa: E402


def _state():
    return {
        "poems": [{"id": "zq-test", "title": "测试", "content": "含 </script> 的句子",
                   "author": "a", "genre": "现代诗", "visibility": "private",
                   "ai_read": True, "guid": "PRIVATE-GUID", "source": ["phone"],
                   "content_hash": "h", "created": "2026-01-01"}],
        "reads": [], "personas": [], "personas_defaults": [{"private": True}],
        "personas_sidecar": [{"private": True}], "curation": {}, "thread_meta": {},
        "votes": {}, "voter_votes": {}, "favs": {}, "stanzas": {}, "calibration": {},
        "persona_echo": {},
        "settings": {"site_title": "测试集", "site_subtitle": "掌中", "footer_text": "页脚",
                     "default_view": "all", "score_badge": "raw", "port": 8737,
                     "show_poetry_boards": False, "hidden_genre_boards": ["杂文"],
                     "mobile_port": 8738, "dispatch": {"default_model": "private-model"}},
        "version": "9.9",
    }


def test_snapshot_is_readonly_and_sanitized():
    old_state, old_wc = S.build_author_state, S.load_wordcloud
    try:
        S.build_author_state = _state
        S.load_wordcloud = lambda: {"poems": {"words": []}, "reasons": {"words": []}}
        snap = S.build_mobile_snapshot()
        lazy = S.build_mobile_snapshot(include_wordcloud=False)
    finally:
        S.build_author_state, S.load_wordcloud = old_state, old_wc
    assert snap["mobile"]["mode"] == "readonly"
    assert snap["poems"][0]["visibility"] == "private", "作者手机快照应保留私密作品"
    assert "guid" not in snap["poems"][0] and "source" not in snap["poems"][0]
    assert snap["personas_defaults"] == [] and snap["personas_sidecar"] == []
    assert "dispatch" not in snap["settings"] and "port" not in snap["settings"]
    assert snap["settings"]["show_poetry_boards"] is False
    assert snap["settings"]["hidden_genre_boards"] == ["杂文"]
    assert "wordcloud" not in lazy, "联网手机快照不应提前计算词云"
    print("[ok] 移动快照只读字段 / 私密作品保留 / 设备来源脱敏")
    return snap


def test_persona_echo_counts_only_visible_blind_direct_signals():
    reads = [
        {"read_id": "r-1", "context_mode": "blind", "reader": {"persona_id": "p-1"}},
        {"read_id": "r-2", "context_mode": "blind", "reader": {"persona_id": "p-1"}},
        {"read_id": "r-3", "context_mode": "thread", "reader": {"persona_id": "p-1"}},
        {"read_id": "r-4", "context_mode": "blind", "reader": {"persona_id": "p-2"}},
    ]
    personas = [
        {"persona_id": "p-1"},
        {"persona_id": "p-2"},
        {"persona_id": "old", "superseded_by": "p-1"},
    ]
    curation = {"r-1": {"author_marked": True}, "r-2": {"hidden": True}}
    votes = {
        "r-1": {"up": 3, "down": 1, "skip": 0, "best": 2, "pg_up": 9},
        "r-2": {"up": 8, "best": 4},
        "r-3": {"up": 6, "best": 3},
        "r-4": {"pg_up": 5},
    }
    echo = S.build_persona_echo(reads, personas, curation, votes)
    assert echo == {
        "p-1": {"comments": 1, "voted_comments": 1, "up": 3, "down": 1,
                "best": 2, "author_marks": 1},
        "p-2": {"comments": 1, "voted_comments": 0, "up": 0, "down": 0,
                "best": 0, "author_marks": 0},
    }
    print("[ok] 人设回声只数未折叠盲读的主动赞/踩/加精/作者藏印")


def test_author_mark_and_hidden_state_coexist():
    old_reads, old_curation = S.READS, S.CURATION
    with tempfile.TemporaryDirectory(prefix="zq-curation-") as td:
        root = Path(td)
        S.READS = root / "reads.jsonl"
        S.CURATION = root / "curation.json"
        S.READS.write_text(json.dumps({"read_id": "r-1"}, ensure_ascii=False) + "\n",
                           encoding="utf-8")
        try:
            assert S.curate({"read_id": "r-1", "author_marked": True})["author_marked"]
            S.curate({"read_id": "r-1", "hidden": True, "reason": "暂折"})
            both = S.load_curation()["r-1"]
            assert both["author_marked"] and both["hidden"]
            S.curate({"read_id": "r-1", "hidden": False})
            marked = S.load_curation()["r-1"]
            assert marked["author_marked"] and "hidden" not in marked
            assert S.curate({"read_id": "r-1", "author_marked": False}) == {}
            assert S.load_curation() == {}
        finally:
            S.READS, S.CURATION = old_reads, old_curation
    print("[ok] 作者藏印与折叠状态互不覆盖 / 均可撤回")


def test_word_context_is_on_demand_and_excludes_private_or_piggyback():
    old_corpus, old_reads, old_votes = S.CORPUS, S.READS, S.VOTES
    with tempfile.TemporaryDirectory(prefix="zq-word-context-") as td:
        root = Path(td)
        S.CORPUS, S.READS, S.VOTES = root / "corpus.json", root / "reads.jsonl", root / "votes.jsonl"
        S.CORPUS.write_text(json.dumps([
            {"id": "p-1", "title": "公开", "visibility": "public", "content": "夏天，夏天\n另一个夏天"},
            {"id": "p-2", "title": "私密", "visibility": "private", "content": "夏天"},
        ], ensure_ascii=False), encoding="utf-8")
        S.READS.write_text(json.dumps({"read_id": "r-1", "poem_id": "p-1"}) + "\n",
                           encoding="utf-8")
        S.VOTES.write_text("\n".join(json.dumps(v, ensure_ascii=False) for v in [
            {"vote_id": "v-1", "target_read_id": "r-1", "vote": "up", "reason": "夏天意象成立"},
            {"vote_id": "v-2", "target_read_id": "r-1", "vote": "up", "reason": "夏天顺势", "source": "piggyback"},
        ]) + "\n", encoding="utf-8")
        try:
            poems = S.load_word_context("poems", "夏天")
            reasons = S.load_word_context("reasons", "夏天")
            assert poems["documents"] == 1 and poems["hits"] == 2
            assert {row["title"] for row in poems["rows"]} == {"公开"}
            assert reasons["documents"] == 1 and reasons["hits"] == 1
            assert reasons["rows"][0]["text"] == "夏天意象成立"
        finally:
            S.CORPUS, S.READS, S.VOTES = old_corpus, old_reads, old_votes
    print("[ok] 词句索引按需查询 / 私密作品与顺势票排除")


def test_single_html_is_self_contained():
    snap = test_snapshot_is_readonly_and_sanitized()
    html = S.render_mobile_snapshot_html(snap).decode("utf-8")
    assert 'content="snapshot"' in html
    assert "window.__ZQ_SNAPSHOT__=" in html
    assert '<link rel="stylesheet" href="style.css">' not in html
    assert '<script src="app.js"></script>' not in html
    assert "\\u003c/script\\u003e" in html, "正文中的 </script> 必须转义"
    print("[ok] 单 HTML 自包含 / 脚本闭合转义")


def test_mobile_pair_token_survives_ios_browser_handoff():
    app = (S.WEBAPP / "app.js").read_text(encoding="utf-8")
    assert 'const token = paired || storageGet(MOBILE_TOKEN_KEY) || "";' in app
    assert 'params.delete("pair")' not in app
    assert 'history.replaceState(null, "", location.pathname' not in app
    print("[ok] iPad 扫码器转 Safari / 主屏幕时连接签不被预览页抹除")


def test_thread_filters_survive_detail_return():
    app = (S.WEBAPP / "app.js").read_text(encoding="utf-8")
    assert 'const threadIndexState = { query: "", poem: "", replies: "0", depth: "0", sort: "activity" };' in app
    assert "box.value = threadIndexState.query;" in app
    assert "Object.assign(threadIndexState" in app
    print("[ok] 跟帖索引筛选在详情返回后保留")


def test_qr_is_local_svg():
    svg = S.qr_svg("http://192.168.1.2:8738/?pair=test").decode("utf-8")
    assert svg.startswith("<svg") and "<path" in svg and "192.168.1.2" not in svg
    print("[ok] 二维码本机生成 SVG")


def test_private_pair_url_requires_current_token():
    port = _free_port()
    try:
        status = S.MOBILE_ACCESS.start(port)
        token = status["token"]
        assert S.MOBILE_ACCESS.valid_pair_url(
            f"https://computer.example.ts.net/?pair={token}")
        assert S.MOBILE_ACCESS.private_pair_url(
            "https://computer.example.ts.net") == (
                f"https://computer.example.ts.net?pair={token}")
        assert S.MOBILE_ACCESS.private_pair_url("https://example.com") is None
        assert not S.MOBILE_ACCESS.valid_pair_url(
            "https://computer.example.ts.net/?pair=wrong")
        assert not S.MOBILE_ACCESS.valid_pair_url(
            f"javascript:alert(1)?pair={token}")
    finally:
        S.MOBILE_ACCESS.stop()
    assert not S.MOBILE_ACCESS.valid_pair_url(
        f"https://computer.example.ts.net/?pair={token}")
    print("[ok] 私密 HTTPS 二维码仅接受本轮有效口令")


def test_ephemeral_token_rotates():
    access = S.MobileAccess()
    port = _free_port()
    try:
        first = access.start(port)["token"]
        access.stop()
        second = access.start(port)["token"]
        assert first != second
    finally:
        access.stop()
    print("[ok] 一次性入口停止重开后口令轮换")


def test_trusted_token_survives_restart_and_revokes():
    old_path = S.MOBILE_TRUST
    with tempfile.TemporaryDirectory(prefix="zq-mobile-trust-") as td:
        S.MOBILE_TRUST = Path(td) / "mobile_trust.json"
        port = _free_port()
        first = S.MobileAccess()
        second = S.MobileAccess()
        try:
            status = first.start(port, trusted=True)
            token = status["token"]
            assert status["trusted"] and S.MOBILE_TRUST.exists()
            assert "token" not in S.build_mobile_snapshot(include_wordcloud=False).get("mobile", {})
            first.stop()

            restored = second.restore_trusted()
            assert restored and restored["trusted"]
            assert restored["token"] == token, "可信入口重启后应沿用同一张连接签"
            second.stop(revoke=True)
            assert not S.MOBILE_TRUST.exists()
            assert S.MobileAccess().restore_trusted() is None

            S.MOBILE_TRUST.write_text(json.dumps({
                "schema": 1, "token": "x" * 32, "port": port,
                "expires_at": time.time() - 1,
            }), encoding="utf-8")
            assert S.MobileAccess().restore_trusted() is None
        finally:
            first.stop()
            second.stop(revoke=True)
            S.MOBILE_TRUST = old_path
    print("[ok] 可信入口跨重启复用 / 撤销与过期失效 / 口令不进快照")


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_mobile_server_rejects_writes_and_requires_token():
    port = _free_port()
    tiny = {"poems": [], "reads": [], "personas": [], "settings": {}, "version": "x",
            "mobile": {"content_hash": "abc", "generated_at": "now"}}
    old = S.build_mobile_snapshot
    S.build_mobile_snapshot = lambda include_wordcloud=True: tiny
    try:
        status = S.MOBILE_ACCESS.start(port)
        token = status["token"]
        url = f"http://127.0.0.1:{port}/api/mobile-state"
        try:
            urllib.request.urlopen(url, timeout=3)
            assert False, "无口令必须拒绝"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        req = urllib.request.Request(url, headers={"X-ZQ-Mobile-Token": token})
        data = json.loads(urllib.request.urlopen(req, timeout=3).read().decode("utf-8"))
        assert data["mobile"]["content_hash"] == "abc"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/?pair={token}", timeout=3) as res:
            html = res.read().decode("utf-8")
            assert f'manifest.webmanifest?pair={token}' in html
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/manifest.webmanifest?pair={token}", timeout=3) as res:
            manifest = json.loads(res.read())
            assert f"?pair={token}" in manifest["start_url"]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/manifest.webmanifest?pair=wrong", timeout=3) as res:
            manifest = json.loads(res.read())
            assert "pair=" not in manifest["start_url"], "无效签不能被写进安装启动地址"

        post = urllib.request.Request(url, data=b"{}", method="POST",
                                      headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(post, timeout=3)
            assert False, "手机入口不得接受 POST"
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
    finally:
        S.MOBILE_ACCESS.stop()
        S.build_mobile_snapshot = old
    print("[ok] 手机入口口令校验 / iPad 安装启动签 / 全部写入拒绝 / 临时启停")


if __name__ == "__main__":
    test_persona_echo_counts_only_visible_blind_direct_signals()
    test_author_mark_and_hidden_state_coexist()
    test_word_context_is_on_demand_and_excludes_private_or_piggyback()
    test_single_html_is_self_contained()
    test_mobile_pair_token_survives_ios_browser_handoff()
    test_thread_filters_survive_detail_return()
    test_qr_is_local_svg()
    test_private_pair_url_requires_current_token()
    test_ephemeral_token_rotates()
    test_trusted_token_survives_restart_and_revokes()
    test_mobile_server_rejects_writes_and_requires_token()
    print("ALL PASS")
