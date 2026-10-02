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
    assert 'const token = paired || portable?.token || storageGet(MOBILE_TOKEN_KEY) || "";' in app
    assert 'params.delete("pair")' not in app
    assert 'history.replaceState(null, "", location.pathname + location.search + "#/settings")' in app
    print("[ok] 旧局域网签保留 / 安卓 PWA 配对片段落盘后才清理")


def test_android_shell_is_static_and_offline_capable():
    html = (S.WEBAPP / "mobile.html").read_text(encoding="utf-8")
    manifest = json.loads((S.WEBAPP / "mobile.webmanifest").read_text(encoding="utf-8"))
    marker = json.loads((S.WEBAPP / "mobile-shell.json").read_text(encoding="utf-8"))
    worker = (S.WEBAPP / "mobile-sw.js").read_text(encoding="utf-8")
    assert 'content="portable"' in html and "mobile.webmanifest" in html
    assert "window.__ZQ_SNAPSHOT__" not in html, "公开安卓壳不得内嵌私人快照"
    assert manifest["display"] == "standalone" and "mobile.html" in manifest["start_url"]
    assert marker == {"schema": 1, "kind": "zhouqingji-mobile-shell", "compatibility": 1}
    assert "mobile.html" in worker and "app.js" in worker and "style.css" in worker
    assert "api/mobile-state" not in worker, "Service Worker 只缓存程序壳，不缓存跨源私人接口"
    print("[ok] 安卓公开空壳 / 可安装 manifest / 离线程序缓存")


def test_mobile_trust_ui_has_explicit_running_actions():
    app = (S.WEBAPP / "app.js").read_text(encoding="utf-8")
    assert 'id="mobile-renew" hidden' in app
    assert 'id="mobile-trust-choice"' in app
    assert 'post("/api/mobile/renew", {})' in app
    assert 'document.getElementById("mobile-start").hidden = running' in app
    assert "最近同步" in app and "不需要重新扫码" in app
    print("[ok] 手机设置页按状态显示开启/续期/撤销，不再留下无解释的禁用按钮")


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


def test_android_pair_url_uses_fragment_and_private_lan_only():
    port = _free_port()
    old_pwa = S.MOBILE_PWA_URL
    S.MOBILE_PWA_URL = S.DEFAULT_MOBILE_PWA_URL
    try:
        status = S.MOBILE_ACCESS.start(port)
        token = status["token"]
        local = f"http://192.168.4.20:{port}/?pair={token}"
        pair = S.MOBILE_ACCESS.pwa_pair_url(local)
        assert pair and pair.startswith(S.MOBILE_PWA_URL + "#")
        parsed = S.urllib.parse.urlsplit(pair)
        assert not parsed.query and token not in parsed.path
        fragment = S.urllib.parse.parse_qs(parsed.fragment)
        assert fragment["pair"] == [token]
        assert fragment["endpoint"] == [f"http://192.168.4.20:{port}/"]
        assert S.MOBILE_ACCESS.valid_pair_url(pair)
        assert S.MOBILE_ACCESS.pwa_pair_url(
            f"http://8.8.8.8:{port}/?pair={token}") is None
        assert not S.MOBILE_ACCESS.valid_pair_url(
            S.MOBILE_PWA_URL + f"#pair={token}&endpoint=http://8.8.8.8:{port}")
    finally:
        S.MOBILE_ACCESS.stop()
        S.MOBILE_PWA_URL = old_pwa
    print("[ok] 安卓配对口令只进 fragment / 仅接受 RFC1918 局域网端点")


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
            resumed = S.MobileAccess()
            resumed_status = resumed.start(port, trusted=True)
            assert resumed_status["token"] == "x" * 32
            assert not resumed_status["trust_expired"]
            resumed.stop(revoke=True)
        finally:
            first.stop()
            second.stop(revoke=True)
            S.MOBILE_TRUST = old_path
    print("[ok] 可信入口跨重启复用 / 撤销与过期失效 / 口令不进快照")


def test_trusted_token_sliding_renewal_and_live_expiry():
    old_path = S.MOBILE_TRUST
    with tempfile.TemporaryDirectory(prefix="zq-mobile-renew-") as td:
        S.MOBILE_TRUST = Path(td) / "mobile_trust.json"
        access = S.MobileAccess()
        port = _free_port()
        try:
            status = access.start(port, trusted=True)
            token = status["token"]
            access.trust_expires_at = time.time() + 2
            old_expiry = access.trust_expires_at
            assert access.authorize(token, renew=True)
            assert access.token == token, "续期不能更换已安装手机保存的连接签"
            assert access.trust_expires_at > old_expiry + S.MOBILE_TRUST_RENEW_WINDOW
            assert access.status()["last_sync_at"]

            access.trust_expires_at = time.time() - 1
            assert not access.authorize(token), "运行中的可信签到期后也必须即时拒绝"
            renewed = access.renew()
            assert renewed["token"] == token and not renewed["trust_expired"]
            assert access.authorize(token)
        finally:
            access.stop(revoke=True)
            S.MOBILE_TRUST = old_path
    print("[ok] 可信签临近到期自动续期 / 运行中到期即时生效 / 手动续期不换签")


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
    old, old_pwa = S.build_mobile_snapshot, S.MOBILE_PWA_URL
    S.build_mobile_snapshot = lambda include_wordcloud=True: tiny
    S.MOBILE_PWA_URL = S.DEFAULT_MOBILE_PWA_URL
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

        origin = S._mobile_pwa_parts()["origin"]
        preflight = urllib.request.Request(url, method="OPTIONS", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-ZQ-Mobile-Token, If-None-Match",
            "Access-Control-Request-Private-Network": "true",
        })
        with urllib.request.urlopen(preflight, timeout=3) as res:
            assert res.status == 204
            assert res.headers["Access-Control-Allow-Origin"] == origin
            assert res.headers["Access-Control-Allow-Private-Network"] == "true"
        cors_get = urllib.request.Request(url, headers={
            "Origin": origin, "X-ZQ-Mobile-Token": token,
        })
        with urllib.request.urlopen(cors_get, timeout=3) as res:
            assert res.headers["Access-Control-Allow-Origin"] == origin
            assert res.headers["Cross-Origin-Resource-Policy"] == "cross-origin"

        evil = urllib.request.Request(url, method="OPTIONS", headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-ZQ-Mobile-Token",
        })
        try:
            urllib.request.urlopen(evil, timeout=3)
            assert False, "非配置来源的跨源预检必须拒绝"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403

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
        S.MOBILE_PWA_URL = old_pwa
    print("[ok] 手机入口口令 / 精确 CORS 来源 / iPad 兼容签 / 全部写入拒绝")


if __name__ == "__main__":
    test_persona_echo_counts_only_visible_blind_direct_signals()
    test_author_mark_and_hidden_state_coexist()
    test_word_context_is_on_demand_and_excludes_private_or_piggyback()
    test_single_html_is_self_contained()
    test_mobile_pair_token_survives_ios_browser_handoff()
    test_android_shell_is_static_and_offline_capable()
    test_mobile_trust_ui_has_explicit_running_actions()
    test_thread_filters_survive_detail_return()
    test_qr_is_local_svg()
    test_private_pair_url_requires_current_token()
    test_android_pair_url_uses_fragment_and_private_lan_only()
    test_ephemeral_token_rotates()
    test_trusted_token_survives_restart_and_revokes()
    test_trusted_token_sliding_renewal_and_live_expiry()
    test_mobile_server_rejects_writes_and_requires_token()
    print("ALL PASS")
