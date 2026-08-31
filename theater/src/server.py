# -*- coding: utf-8 -*-
"""昼青集·读诗剧场 本地服务器（标准库为主；词云分词用仓库内 vendored jieba，随仓库分发、无需 pip）。

职责边界（README 硬边界的机器侧执行）：
- 读 corpus，读 results；
- 写 corpus 仅限作者在 GUI 里明确触发的作品动作（切可见性/剪自注/背景小注等），
  且每次写前把 诗稿.json 备份到 corpus/.backups/（只进不毁、可回滚）；
- 绝不由代码自动改动任何作品内容。

启动：python theater/src/server.py  →  http://localhost:8737
"""
import base64
import hashlib
import hmac
import importlib.util
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus" / "诗稿.json"
BACKUPS = ROOT / "corpus" / ".backups"
READS = ROOT / "results" / "reads" / "reads.jsonl"
CURATION = ROOT / "results" / "curation.json"
THREAD_META = ROOT / "results" / "threads" / "meta.json"
VOTES = ROOT / "results" / "votes" / "votes.jsonl"
CALIBRATION = ROOT / "results" / "calibration" / "scores.json"
FAVS = ROOT / "corpus" / "作者偏爱.json"
STANZAS = ROOT / "corpus" / "分段.json"
PERSONAS = ROOT / "theater" / "personas" / "personas.json"
PERSONAS_SIDECAR = ROOT / "corpus" / "personas.json"
WEBAPP = Path(__file__).resolve().parent / "webapp"
VERSION_FILE = ROOT / "VERSION"
PUBLIC_VERSION_URL = "https://raw.githubusercontent.com/Cyanaug/zhouqingji/main/VERSION"
PUBLIC_ARCHIVE_URL = "https://github.com/Cyanaug/zhouqingji/archive/refs/tags/v{version}.zip"
PUBLIC_REPO_URL = "https://github.com/Cyanaug/zhouqingji"
UPDATE_MAX_DOWNLOAD = 50 * 1024 * 1024
UPDATE_MAX_EXPANDED = 120 * 1024 * 1024
UPDATE_MAX_FILES = 5000

UPDATE_ROOT_FILES = {
    ".gitignore", "AGENTS.md", "CLAUDE.md", "LICENSE", "README.md", "VERSION",
    "00_START_HERE.md", "01_corpus_schema.md", "02_readers_and_casting.md",
    "03_runner_and_coverage.md", "04_app_and_design.md", "05_run_modes.md",
    "MOBILE_ACCESS.md", "PROGRESS.md",
}
UPDATE_PREFIXES = (
    ".agents/skills/", ".codex/agents/", ".claude/agents/", ".claude/skills/",
    ".github/workflows/", "theater/assets/", "theater/release/", "theater/src/",
    "theater/tests/", "theater/vendor/",
)
UPDATE_THEATER_FILES = {
    "theater/NOTES.md", "theater/check.ps1", "theater/open-theater.ps1",
    "theater/personas/personas.json", "theater/personas/personas.sidecar.example.json",
}

# 作者偏好（corpus/settings.json 侧车）：缺文件/缺字段一律回退这里的默认值。
# GUI 设置页与派发 agent 读写同一份文件——所有"可以换成你自己的"都收口在这里。
DEFAULT_SETTINGS = {
    "site_title": "昼青集",
    "site_subtitle": "读诗剧场",
    "footer_text": "由世间所有的所见将它命名。",
    "default_view": "boards",    # boards | readers | timeline | stats | all
    "score_badge": "cal",        # cal = 质分优先；raw = 只看原始均分
    "show_poetry_boards": True,   # 榜单首页是否显示诗词/长诗/短诗三个诗类专榜
    "hidden_genre_boards": [],    # 不在榜单首页显示的非诗文体榜（数据与直达页不删除）
    "read_genres": [],           # 诗（现代诗/词/歌词）永远在读者池；其他文体勾选才读
    "genre_notes": {},           # 文体 → 作者补充的评判要求（附进读者 prompt）
    "port": 8737,                # 重启后生效
    "mobile_port": 8738,         # 手机临时访问端口；只读服务，运行中可开关
    "dispatch": {                # 派发 agent 的默认偏好
        "default_model": "",    # 不替用户预设供应商；首次派发时明确选择
        "default_transport": "auto",
        "target_depth": None,    # 留空时按当前覆盖账计算“最薄层 + 1”，不把历史数字钉死
    },
}
VIEW_CHOICES = ("boards", "readers", "timeline", "stats", "all")
SETTINGS = ROOT / "corpus" / "settings.json"
MOBILE_TRUST = ROOT / "corpus" / "mobile_trust.json"
MOBILE_TRUST_SECONDS = 30 * 24 * 60 * 60

MIME = {".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".webmanifest": "application/manifest+json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
        ".json": "application/json; charset=utf-8"}


def sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def load_corpus():
    if not CORPUS.exists():
        return []
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def save_corpus(corpus):
    """作者动作专用：先备份再原子替换。"""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    if CORPUS.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(CORPUS, BACKUPS / f"诗稿-{stamp}.json")
    tmp = CORPUS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(corpus, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(CORPUS)


def load_reads():
    if not READS.exists():
        return []
    out = []
    for line in READS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S") + time.strftime("%z")


# ---------- 作者动作（唯一允许写 corpus 的路径） ----------

def act_set_visibility(poem, payload, _corpus):
    v = payload.get("value")
    if v not in ("public", "private"):
        raise ValueError("visibility 只能是 public/private")
    poem["visibility"] = v


def act_set_background(poem, payload, _corpus):
    poem["background"] = str(payload.get("value", ""))


def act_set_date_written(poem, payload, _corpus):
    v = payload.get("value") or None
    poem["date_written"] = v


def act_cut_note(poem, payload, _corpus):
    """把 content 里被划取的一段剪入 note；content_hash 随之更新。"""
    text = payload.get("text", "")
    if not text.strip():
        raise ValueError("未选中任何文本")
    if text not in poem["content"]:
        raise ValueError("选中的文本与正文不一致（可能跨越了折行渲染），请重试")
    before, _, after = poem["content"].partition(text)
    poem["content"] = (before.rstrip() + "\n\n" + after.lstrip()).strip()
    poem["note"] = (poem["note"] + "\n\n" + text.strip()).strip()
    poem["content_hash"] = sha1(poem["content"])
    poem["modified"] = now_iso()


def act_set_title(poem, payload, _corpus):
    """改标题：不动 content_hash（只算正文），已有阅读记录不会因此标为旧版。"""
    v = str(payload.get("value", "")).strip()
    if not v:
        raise ValueError("标题不能为空")
    poem["title"] = v
    poem["modified"] = now_iso()


def act_edit(poem, payload, _corpus):
    """统一编辑：标题 + 正文一个入口。
    正文变更沿用 cut_note 的契约：更新 content_hash 与 modified，已有阅读
    记录按 hash 自动标"旧版"（保留不删）；仅改标题不动 hash（同 set_title）。
    正文变更时丢弃该诗的分段侧车——空行已随正文一并可编辑，旧的行号分段
    对不上新正文，留着反而会错位覆盖显示。"""
    title = str(payload.get("title", poem["title"])).strip()
    if not title:
        raise ValueError("标题不能为空")
    changed = title != poem["title"]
    poem["title"] = title
    if "content" in payload:
        content = str(payload["content"]).replace("\r\n", "\n").strip("\n")
        if not content.strip():
            raise ValueError("正文不能为空")
        if content != poem["content"]:
            poem["content"] = content
            poem["content_hash"] = sha1(content)
            changed = True
            st = load_stanzas()
            if poem["id"] in st:
                st.pop(poem["id"])
                STANZAS.parent.mkdir(parents=True, exist_ok=True)
                STANZAS.write_text(json.dumps(st, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    if changed:
        poem["modified"] = now_iso()


POETRY_GENRES = ("现代诗", "词", "歌词")


def act_set_genre(poem, payload, _corpus):
    """改文体；非诗文体一律默认退出读者池（ai_read 联动）——否则自定义文体
    （剧本之类）会带着诗歌标准被读。作者可在设置页 read_genres 勾选让某文体
    重新入池，届时 runner 的读者 prompt 自动带体裁转换段。"""
    v = str(payload.get("value", "")).strip()
    if not v:
        raise ValueError("文体不能为空")
    poem["genre"] = v
    poem["ai_read"] = v in POETRY_GENRES


ACTIONS = {"set_visibility": act_set_visibility,
           "edit": act_edit,
           "set_title": act_set_title,
           "set_background": act_set_background,
           "set_date_written": act_set_date_written,
           "set_genre": act_set_genre,
           "cut_note": act_cut_note}


def load_curation():
    if CURATION.exists():
        return json.loads(CURATION.read_text(encoding="utf-8"))
    return {}


def load_thread_meta():
    """跟帖侧车（runner.py/plan_thread.py 写）：persona_hash/链深/立场变化/void。
    纯只读展示用，不进任何榜单/校准逻辑。"""
    if THREAD_META.exists():
        return json.loads(THREAD_META.read_text(encoding="utf-8"))
    return {}


def _vote_void_ids():
    """作废票标记（results/votes/void.json，plan_votes.py void 写）：统计与展示一律排除。"""
    f = VOTES.parent / "void.json"
    if f.exists():
        return set(json.loads(f.read_text(encoding="utf-8")))
    return set()


def _iter_votes():
    if not VOTES.exists():
        return
    void = _vote_void_ids()
    for line in VOTES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        v = json.loads(line)
        if v.get("vote_id") in void:
            continue
        yield v


def load_vote_tally():
    """点赞模式（plan_votes.py 写）的只读聚合视图：
    {read_id: {up, down, skip, best, pg_up, pg_down}}。up/down/skip 只数「主动票」——作者
    据此判断要不要手删短评；pg_* 是跟帖顺势票，几乎恒为 up 的弱信号，分开列，不混入撤评判断。
    best 是「加精」——批量投票时"这几条里最扛得住的一条"的相对判断；绝对判断有正向
    偏置（实测 up 占八成），加精不受它影响，是真正有区分度的正向信号。不是排名指标，纯展示。"""
    tally = {}
    for v in _iter_votes():
        t = tally.setdefault(v["target_read_id"],
                             {"up": 0, "down": 0, "skip": 0, "best": 0,
                              "pg_up": 0, "pg_down": 0})
        vote = v.get("vote")
        if v.get("source") == "piggyback":
            if vote == "up":
                t["pg_up"] += 1
            elif vote == "down":
                t["pg_down"] += 1
        elif vote in ("up", "down", "skip", "best"):
            t[vote] += 1
    return tally


def load_voter_votes():
    """每一张个人票的方向索引：{target_read_id: {persona_id: "up"/"down"/"skip"}}。
    供跟帖页面查询「这个楼层的作者对 parent 投了什么票」。
    加精（best）不是方向票，不入此索引——否则会覆盖同一人对同一目标的 up/down。"""
    idx = {}
    for v in _iter_votes():
        pid = v.get("voter", {}).get("persona_id")
        vote = v.get("vote")
        if pid and vote in ("up", "down", "skip"):
            idx.setdefault(v["target_read_id"], {})[pid] = vote
    return idx


def build_persona_echo(reads, personas, curation, vote_tally):
    """按评论作者聚合收到的主动赞与加精，供统计页只读展示。

    这里只数未折叠的盲读评语；顺势票 pg_* 不混进来。原始票仍以 votes.jsonl 为
    唯一真源，本函数只是可重建视图，不参与作品排名、校准或自动撤评。
    """
    rows = {
        p["persona_id"]: {"comments": 0, "voted_comments": 0,
                          "up": 0, "down": 0, "best": 0, "author_marks": 0}
        for p in personas if not p.get("superseded_by")
    }
    for read in reads:
        if read.get("context_mode") != "blind":
            continue
        read_id = read.get("read_id")
        if (curation.get(read_id) or {}).get("hidden"):
            continue
        persona_id = (read.get("reader") or {}).get("persona_id")
        if persona_id not in rows:
            continue
        row = rows[persona_id]
        row["comments"] += 1
        tally = vote_tally.get(read_id) or {}
        row["up"] += int(tally.get("up") or 0)
        row["down"] += int(tally.get("down") or 0)
        row["best"] += int(tally.get("best") or 0)
        row["author_marks"] += int(bool((curation.get(read_id) or {}).get("author_marked")))
        if any(int(tally.get(k) or 0) for k in ("up", "down", "skip", "best")):
            row["voted_comments"] += 1
    return rows


_calib_lock = threading.Lock()

_wc_lock = threading.Lock()
_WC_CACHE = {"key": None, "data": None}   # 词云按语料/票据 mtime 缓存，见 load_wordcloud


def load_calibration():
    """校准分（calibrate.py 生成的只读视图）。scores.json 比 reads.jsonl 或
    curation.json 旧时自动重算——作者无需手动跑任何脚本；重算失败只打警告
    并回退旧文件/空 dict（前端遇空自动退回原始均分），绝不拖垮页面。"""
    try:
        deps = [p.stat().st_mtime for p in (READS, CURATION) if p.exists()]
        stale = (not CALIBRATION.exists()) or \
            (deps and CALIBRATION.stat().st_mtime < max(deps))
        if stale:
            with _calib_lock:
                import importlib
                import calibrate
                importlib.reload(calibrate)  # 服务器长驻：强制用磁盘上最新的校准代码，
                calibrate.generate()         # 否则改完 calibrate.py 不重启会拿旧模块重算

    except Exception as e:
        print(f"[calibration] 自动重算失败，沿用旧数据：{e}")
    if CALIBRATION.exists():
        return json.loads(CALIBRATION.read_text(encoding="utf-8"))
    return {}


def load_wordcloud():
    """词云数据（诗正文 + 读者反应）。跟着当前语料/票据实时算，按二者 mtime 缓存：
    诗稿或投票没变就直接吃缓存，变了才在锁内重算一次（诗~0.7s、评~1.5s）。分词用
    仓库内 vendored jieba（theater/vendor，MIT、纯 Python，随仓库分发，无需 pip 安装）。
    首次调用惰性加载词典。任何失败（如 vendor 缺失）只打警告、回退上次结果或空，绝不拖垮页面。"""
    key = (CORPUS.stat().st_mtime if CORPUS.exists() else 0,
           VOTES.stat().st_mtime if VOTES.exists() else 0)
    if _WC_CACHE["key"] == key and _WC_CACHE["data"] is not None:
        return _WC_CACHE["data"]
    with _wc_lock:
        if _WC_CACHE["key"] == key and _WC_CACHE["data"] is not None:
            return _WC_CACHE["data"]
        try:
            import wordcloud_data as wc
            poems = [p for p in load_corpus() if p.get("visibility") == "public"]
            data = {"poems": wc.compute_poem_cloud(poems),
                    "reasons": wc.compute_reason_cloud(_iter_votes())}
            _WC_CACHE["key"] = key
            _WC_CACHE["data"] = data
            return data
        except Exception as e:
            print(f"[wordcloud] 计算失败，回退：{e}")
            if _WC_CACHE["data"] is not None:
                return _WC_CACHE["data"]
            return {"poems": {"meta": {}, "words": [], "ranking": [], "coverage": []},
                    "reasons": {"meta": {}, "words": [], "ranking": [], "coverage": []}}


def load_word_context(mode, word, limit=100):
    """按需查一个词出现在哪些诗句/投票理由中；不进入启动快照。"""
    if mode not in {"poems", "reasons"}:
        raise ValueError("词句索引模式无效")
    if not isinstance(word, str):
        raise ValueError("词不能为空")
    word = word.strip()
    if not word or len(word) > 32 or any(ord(ch) < 32 for ch in word):
        raise ValueError("词不能为空且不能超过 32 个字符")
    needle = word.casefold()
    rows, documents, hits = [], set(), 0

    if mode == "poems":
        for poem in load_corpus():
            if poem.get("visibility") != "public":
                continue
            matched = []
            for raw in re.split(r"[\r\n]+", poem.get("content") or ""):
                line = re.sub(r"\s+", " ", raw).strip()
                if line and needle in line.casefold() and line not in matched:
                    matched.append(line)
            if not matched:
                continue
            documents.add(poem["id"])
            hits += len(matched)
            for line in matched:
                if len(rows) < limit:
                    rows.append({"poem_id": poem["id"], "title": poem.get("title") or poem["id"],
                                 "text": line[:240]})
    else:
        read_poems = {r["read_id"]: r.get("poem_id") for r in load_reads()}
        poems = {p["id"]: p.get("title") or p["id"] for p in load_corpus()}
        for vote in _iter_votes():
            if vote.get("source") == "piggyback":
                continue
            reason = re.sub(r"\s+", " ", (vote.get("reason") or "")).strip()
            if not reason or needle not in reason.casefold():
                continue
            vote_id = vote.get("vote_id") or f"row-{hits}"
            documents.add(vote_id)
            hits += 1
            if len(rows) < limit:
                poem_id = read_poems.get(vote.get("target_read_id"))
                rows.append({"poem_id": poem_id, "title": poems.get(poem_id, "读者反应"),
                             "read_id": vote.get("target_read_id"), "text": reason[:320]})

    return {"mode": mode, "word": word, "documents": len(documents), "hits": hits,
            "rows": rows, "truncated": hits > len(rows)}


def load_favs():
    if FAVS.exists():
        return json.loads(FAVS.read_text(encoding="utf-8"))
    return {}


def set_favorite(payload):
    """作者「我觉得好」标记（侧车文件，不动冻结的诗稿 schema）。"""
    pid = payload.get("poem_id")
    if pid not in {p["id"] for p in load_corpus()}:
        raise ValueError("找不到这首诗")
    favs = load_favs()
    if payload.get("value"):
        favs[pid] = {"ts": now_iso()}
    else:
        favs.pop(pid, None)
    FAVS.parent.mkdir(parents=True, exist_ok=True)
    FAVS.write_text(json.dumps(favs, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def load_stanzas():
    if STANZAS.exists():
        return json.loads(STANZAS.read_text(encoding="utf-8"))
    return {}


def set_stanzas(payload):
    """作者手工分段（侧车文件）。分段是恢复导出丢失的信息而非修订：
    不动 content、不改 content_hash，已有阅读记录不会因此变旧版。"""
    pid = payload.get("poem_id")
    poem = next((p for p in load_corpus() if p["id"] == pid), None)
    if poem is None:
        raise ValueError("找不到这首诗")
    breaks = payload.get("breaks")
    if not isinstance(breaks, list) or not all(isinstance(b, int) for b in breaks):
        raise ValueError("breaks 必须是整数数组")
    n = sum(1 for l in poem["content"].split("\n") if l.strip())
    breaks = sorted({b for b in breaks if 0 <= b < n - 1})
    st = load_stanzas()
    if breaks:
        st[pid] = breaks
    else:
        st.pop(pid, None)
    STANZAS.parent.mkdir(parents=True, exist_ok=True)
    STANZAS.write_text(json.dumps(st, ensure_ascii=False, indent=1),
                       encoding="utf-8")


def load_settings_file():
    """settings.json 的原始内容（只含作者显式设置过的项）。"""
    if SETTINGS.exists():
        try:
            d = json.loads(SETTINGS.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            print("[settings] settings.json 解析失败，按全默认处理")
    return {}


def load_personas():
    """默认人设（theater/personas/personas.json，git 跟踪、随更新可覆盖）
    ＋ 读者侧车（corpus/personas.json，已 gitignore、pull 永不覆盖）合并。
    按 persona_id：侧车同 id 部分覆盖字段、新 id 追加、hidden=true 撤下某默认。
    没有侧车文件时，返回与旧行为完全一致。"""
    base = json.loads(PERSONAS.read_text(encoding="utf-8"))
    order = [p["persona_id"] for p in base]
    merged = {p["persona_id"]: p for p in base}
    if PERSONAS_SIDECAR.exists():
        try:
            side = json.loads(PERSONAS_SIDECAR.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("[personas] corpus/personas.json 解析失败，忽略侧车")
            side = []
        if isinstance(side, list):
            for p in side:
                pid = (p or {}).get("persona_id")
                if not pid:
                    continue
                if pid in merged:
                    merged[pid] = {**merged[pid], **p}   # 部分覆盖：只改给出的字段
                else:
                    merged[pid] = p
                    order.append(pid)
    return [merged[pid] for pid in order if not merged[pid].get("hidden")]


def load_personas_sidecar():
    """侧车原文（GUI 编辑用：/api/personas 是整份替换，前端必须先拿到全份）。
    缺文件/坏 JSON/非数组一律回空列表——与 load_personas 的容错口径一致。"""
    if not PERSONAS_SIDECAR.exists():
        return []
    try:
        side = json.loads(PERSONAS_SIDECAR.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return side if isinstance(side, list) else []


def load_settings():
    """默认值 + 作者设置的合并视图（下发给前端与 agent 的口径）。"""
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    user = load_settings_file()
    disp = user.get("dispatch")
    merged.update({k: v for k, v in user.items()
                   if k in DEFAULT_SETTINGS and k != "dispatch"})
    if isinstance(disp, dict):
        merged["dispatch"].update({k: v for k, v in disp.items()
                                   if k in DEFAULT_SETTINGS["dispatch"]})
    return merged


def set_settings(payload):
    """作者偏好（侧车文件，不碰任何冻结 schema）。只收白名单字段；
    空字符串/None = 恢复该项默认（从文件里删掉，而不是把默认值固化进文件）。"""
    cur = load_settings_file()

    def put(d, key, val):
        if val is None or (isinstance(val, str) and not val.strip()):
            d.pop(key, None)
        else:
            d[key] = val.strip() if isinstance(val, str) else val

    for k in ("site_title", "site_subtitle", "footer_text"):
        if k in payload:
            if payload[k] is not None and not isinstance(payload[k], str):
                raise ValueError(f"{k} 必须是字符串")
            put(cur, k, payload[k])
    if "default_view" in payload:
        v = payload["default_view"]
        if v and v not in VIEW_CHOICES:
            raise ValueError(f"default_view 只能是 {'/'.join(VIEW_CHOICES)}")
        put(cur, "default_view", v)
    if "score_badge" in payload:
        v = payload["score_badge"]
        if v and v not in ("cal", "raw"):
            raise ValueError("score_badge 只能是 cal/raw")
        put(cur, "score_badge", v)
    if "show_poetry_boards" in payload:
        v = payload["show_poetry_boards"]
        if not isinstance(v, bool):
            raise ValueError("show_poetry_boards 必须是布尔值")
        if v == DEFAULT_SETTINGS["show_poetry_boards"]:
            cur.pop("show_poetry_boards", None)
        else:
            cur["show_poetry_boards"] = v
    if "hidden_genre_boards" in payload:
        v = payload["hidden_genre_boards"] or []
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError("hidden_genre_boards 必须是字符串数组")
        v = sorted({x.strip() for x in v if x.strip()})
        if v:
            cur["hidden_genre_boards"] = v
        else:
            cur.pop("hidden_genre_boards", None)
    if "read_genres" in payload:
        v = payload["read_genres"] or []
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError("read_genres 必须是字符串数组")
        v = sorted({x.strip() for x in v if x.strip()})
        if v:
            cur["read_genres"] = v
        else:
            cur.pop("read_genres", None)
    if "genre_notes" in payload:
        v = payload["genre_notes"] or {}
        if not isinstance(v, dict) or not all(
                isinstance(k, str) and isinstance(x, str) for k, x in v.items()):
            raise ValueError("genre_notes 必须是 {文体: 要求} 对象")
        v = {k.strip(): x.strip() for k, x in v.items() if k.strip() and x.strip()}
        if v:
            cur["genre_notes"] = v
        else:
            cur.pop("genre_notes", None)
    for port_key in ("port", "mobile_port"):
        if port_key in payload:
            v = payload[port_key]
            if v in (None, ""):
                cur.pop(port_key, None)
            elif not isinstance(v, int) or not 1024 <= v <= 65535:
                raise ValueError(f"{port_key} 需为 1024–65535 的整数")
            else:
                cur[port_key] = v
    if "dispatch" in payload:
        dp = payload["dispatch"]
        if not isinstance(dp, dict):
            raise ValueError("dispatch 必须是对象")
        cd = cur.get("dispatch", {})
        for k in ("default_model", "default_transport"):
            if k in dp:
                if dp[k] is not None and not isinstance(dp[k], str):
                    raise ValueError(f"{k} 必须是字符串")
                value = dp[k].strip() if isinstance(dp[k], str) else dp[k]
                if value in (None, "", DEFAULT_SETTINGS["dispatch"][k]):
                    cd.pop(k, None)
                else:
                    cd[k] = value
        if "target_depth" in dp:
            v = dp["target_depth"]
            if v in (None, ""):
                cd.pop("target_depth", None)
            elif not isinstance(v, int) or not 1 <= v <= 99:
                raise ValueError("target_depth 需为 1–99 的整数")
            else:
                cd["target_depth"] = v
        if cd:
            cur["dispatch"] = cd
        else:
            cur.pop("dispatch", None)

    if not cur:
        if SETTINGS.exists():
            SETTINGS.unlink()
        return
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(SETTINGS)


PERSONA_STR_FIELDS = ("name", "generation", "native_lang", "orientation",
                      "persona", "superseded_by")
PERSONA_BOOL_FIELDS = ("knows_诠释", "knows_date", "reads_background", "hidden")


def set_personas(payload):
    """读者自建/覆盖人设（侧车 corpus/personas.json，绝不动随附的 personas.json）。
    payload = {"personas": [ {persona_id, ...}, ... ]}，整份替换侧车；空列表 → 删文件。
    随附 id 的条目可只给要改的字段（合并时部分覆盖，其余保留随附值）；
    全新 id 必须含 name 与 persona（否则无法展示/派发），knows_* 缺省补 False。"""
    items = payload.get("personas")
    if not isinstance(items, list):
        raise ValueError("personas 必须是数组")
    default_ids = {p["persona_id"]
                   for p in json.loads(PERSONAS.read_text(encoding="utf-8"))}
    clean, seen = [], set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("每个人设必须是对象")
        pid = (raw.get("persona_id") or "").strip()
        if not pid:
            raise ValueError("persona_id 不能为空")
        if pid in seen:
            raise ValueError(f"persona_id 重复：{pid}")
        seen.add(pid)
        e = {"persona_id": pid}
        for k in PERSONA_STR_FIELDS:
            if raw.get(k) is not None:
                if not isinstance(raw[k], str):
                    raise ValueError(f"{k} 必须是字符串")
                if raw[k].strip():
                    e[k] = raw[k].strip()
        for k in PERSONA_BOOL_FIELDS:
            if raw.get(k) is not None:
                if not isinstance(raw[k], bool):
                    raise ValueError(f"{k} 必须是布尔值")
                e[k] = raw[k]
        if pid not in default_ids:
            if not e.get("name") or not e.get("persona"):
                raise ValueError(f"新人设 {pid} 必须含 name 与 persona")
            for k in ("knows_诠释", "knows_date", "reads_background"):
                e.setdefault(k, False)
        clean.append(e)
    if not clean:
        if PERSONAS_SIDECAR.exists():
            PERSONAS_SIDECAR.unlink()
        return
    PERSONAS_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    tmp = PERSONAS_SIDECAR.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PERSONAS_SIDECAR)


def curate(payload):
    """作者折叠/恢复或给评论落藏印；都只写侧车，不改 reads.jsonl。"""
    read_id = payload.get("read_id")
    if not read_id or read_id not in {r["read_id"] for r in load_reads()}:
        raise ValueError("找不到该阅读记录")
    cur = load_curation()
    entry = dict(cur.get(read_id) or {})
    if "hidden" in payload:
        if payload.get("hidden"):
            entry.update({"hidden": True,
                          "reason": str(payload.get("reason", "")),
                          "ts": now_iso()})
        else:
            for key in ("hidden", "reason", "ts"):
                entry.pop(key, None)
    if "author_marked" in payload:
        if payload.get("author_marked"):
            entry.update({"author_marked": True, "author_marked_ts": now_iso()})
        else:
            for key in ("author_marked", "author_marked_ts"):
                entry.pop(key, None)
    if entry:
        cur[read_id] = entry
    else:
        cur.pop(read_id, None)
    CURATION.parent.mkdir(parents=True, exist_ok=True)
    CURATION.write_text(json.dumps(cur, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return cur.get(read_id) or {}


# ---------- 版本 & 更新（对 git-clone 了本仓的读者：显示版本 / 检查 / 一键快进拉取）----------

def app_version():
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "?"
    except OSError:
        return "?"


def _git(args, timeout=30):
    """在 ROOT 跑 git，返回 (rc, stdout, stderr)。git 缺失/超时时 rc=-1。"""
    try:
        p = subprocess.run(["git", "-C", str(ROOT), *args],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError:
        return -1, "", "git 未安装"
    except subprocess.TimeoutExpired:
        return -1, "", "git 超时"


def _own_git_repo():
    """ROOT 自身是 git 根才算 clone；避免误把上级仓库当成本项目上游。"""
    rc, top, _ = _git(["rev-parse", "--show-toplevel"], timeout=10)
    if rc != 0:
        return False
    try:
        return Path(top).resolve() == ROOT.resolve()
    except OSError:
        return False


def _normalized_git_url(url):
    """把官方仓库常见 HTTPS/SSH 写法归一，供更新前做来源校验。"""
    value = (url or "").strip().replace("\\", "/")
    lower = value.lower()
    if lower.startswith("git@github.com:"):
        value = "https://github.com/" + value.split(":", 1)[1]
    elif lower.startswith("ssh://git@github.com/"):
        value = "https://github.com/" + value[len("ssh://git@github.com/"):]
    return value.rstrip("/").removesuffix(".git").lower()


def _official_upstream():
    """返回官方上游名；拒绝让一键更新跟随被改写的远端。"""
    rc, upstream, _ = _git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], timeout=10)
    if rc != 0 or "/" not in upstream:
        return None, "没有配置远端上游分支，无法检查更新。"
    remote = upstream.split("/", 1)[0]
    rc, url, _ = _git(["remote", "get-url", remote], timeout=10)
    if rc != 0 or _normalized_git_url(url) != _normalized_git_url(PUBLIC_REPO_URL):
        return None, "当前上游不是昼青集官方公开仓库；为避免拉取未知代码，已中止。"
    return upstream, None


def _download_url(url, max_bytes=UPDATE_MAX_DOWNLOAD, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "zhouqingji-updater/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            length = resp.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError("更新包超过安全大小上限")
            chunks, total = [], 0
            while True:
                chunk = resp.read(min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("更新包超过安全大小上限")
                chunks.append(chunk)
            return b"".join(chunks)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"下载失败：{exc}") from exc


def _updatable_path(rel):
    """ZIP 更新允许清单。用户 corpus/results/settings/batches 永不在名单内。"""
    posix = rel.as_posix()
    if posix in UPDATE_ROOT_FILES or posix in UPDATE_THEATER_FILES:
        return True
    if posix.startswith("theater/runners/"):
        return len(rel.parts) == 3 and rel.suffix == ".py"
    if posix.startswith("theater/personas/"):
        return posix.endswith(".example.json")
    return any(posix.startswith(prefix) for prefix in UPDATE_PREFIXES)


def _validated_archive_files(data, expected_version=None):
    """返回 {仓内相对路径: bytes}；拒绝越界、链接和异常膨胀包。"""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("下载内容不是有效 ZIP") from exc
    infos = [i for i in archive.infolist() if not i.is_dir()]
    if not infos or len(infos) > UPDATE_MAX_FILES:
        raise ValueError("更新包文件数量异常")
    if sum(i.file_size for i in infos) > UPDATE_MAX_EXPANDED:
        raise ValueError("更新包解压后超过安全大小上限")

    roots, files = set(), {}
    for info in infos:
        if "\\" in info.filename:
            raise ValueError(f"更新包路径格式异常：{info.filename}")
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
            raise ValueError(f"更新包含越界路径：{info.filename}")
        # Unix mode 0120000 是符号链接；Windows ZIP 通常 mode=0。
        if ((info.external_attr >> 16) & 0o170000) == 0o120000:
            raise ValueError(f"更新包含符号链接：{info.filename}")
        roots.add(path.parts[0])
        rel = PurePosixPath(*path.parts[1:])
        if _updatable_path(rel):
            if rel in files:
                raise ValueError(f"更新包包含重复路径：{rel}")
            files[rel] = archive.read(info)
    if len(roots) != 1:
        raise ValueError("更新包必须只有一个项目根目录")
    required = {PurePosixPath("VERSION"), PurePosixPath("theater/src/server.py")}
    if not required.issubset(files):
        raise ValueError("更新包缺少 VERSION 或 server.py，已中止")
    if expected_version is not None:
        try:
            archive_version = files[PurePosixPath("VERSION")].decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("更新包 VERSION 不是有效 UTF-8") from exc
        if archive_version != expected_version:
            raise ValueError(
                f"更新包版本 {archive_version or '?'} 与预期 {expected_version} 不一致")
    return files


def install_update_archive(data, root=ROOT, expected_version=None):
    """把已下载公开发行包事务式安装到 ZIP 版；返回更新与备份信息。"""
    files = _validated_archive_files(data, expected_version=expected_version)
    root = Path(root).resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = root / ".update-backups" / stamp
    changed, created = [], []
    with tempfile.TemporaryDirectory(prefix=".update-stage-", dir=root) as td:
        stage = Path(td)
        for rel, content in files.items():
            dest = stage.joinpath(*rel.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

        ordered = sorted(files, key=lambda p: (p.name == "VERSION", p.as_posix()))
        try:
            for rel in ordered:
                dest = root.joinpath(*rel.parts)
                staged = stage.joinpath(*rel.parts)
                if dest.exists() and dest.read_bytes() == files[rel]:
                    continue
                if dest.exists():
                    backup = backup_root.joinpath(*rel.parts)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest, backup)
                else:
                    created.append(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, dest)
                changed.append((rel, dest))
        except Exception:
            for rel, dest in reversed(changed):
                backup = backup_root.joinpath(*rel.parts)
                if backup.exists():
                    os.replace(backup, dest)
                elif dest in created and dest.exists():
                    dest.unlink()
            raise
    return {"changed": len(changed),
            "backup": str(backup_root) if backup_root.exists() else None}


def _version_key(value):
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (value or "").strip())
    return tuple(int(x) for x in match.group(1).split(".")) if match else None


def update_check():
    """clone 走 git；ZIP 安装从固定公开仓库读取 VERSION。"""
    if not _own_git_repo():
        try:
            remote_ver = _download_url(PUBLIC_VERSION_URL, max_bytes=1024,
                                       timeout=15).decode("utf-8").strip()
        except (RuntimeError, ValueError, UnicodeDecodeError) as exc:
            return {"ok": False, "error": str(exc), "local_version": app_version(),
                    "install_type": "archive"}
        if not remote_ver or len(remote_ver) > 40 or _version_key(remote_ver) is None:
            return {"ok": False, "error": "远端 VERSION 内容异常",
                    "local_version": app_version(), "install_type": "archive"}
        local_ver = app_version()
        remote_key, local_key = _version_key(remote_ver), _version_key(local_ver)
        newer = remote_key > local_key if remote_key and local_key else remote_ver != local_ver
        return {"ok": True, "behind": int(newer),
                "local_version": local_ver, "remote_version": remote_ver,
                "install_type": "archive"}
    upstream, upstream_error = _official_upstream()
    if upstream_error:
        return {"ok": False, "error": upstream_error,
                "local_version": app_version()}
    rc, _, err = _git(["fetch", "--quiet"], timeout=60)
    if rc != 0:
        return {"ok": False, "error": f"连接远端失败：{err or '网络或权限问题'}",
                "local_version": app_version()}
    rc, behind, _ = _git(["rev-list", "--count", f"HEAD..{upstream}"], timeout=15)
    behind = int(behind) if behind.isdigit() else 0
    rc, remote_ver, _ = _git(["show", f"{upstream}:VERSION"], timeout=15)
    remote_ver = remote_ver.strip() if rc == 0 else "?"
    return {"ok": True, "behind": behind, "upstream": upstream,
            "local_version": app_version(), "remote_version": remote_ver,
            "install_type": "git"}


def update_pull():
    """clone 快进拉取；ZIP 安装校验允许清单、备份后原子替换。"""
    if not _own_git_repo():
        check = update_check()
        if not check.get("ok"):
            return check
        if not check.get("behind"):
            return {"ok": True, "message": "已是最新版本。",
                    "new_version": app_version(), "restart_needed": False,
                    "install_type": "archive"}
        remote_ver = check["remote_version"]
        try:
            archive_url = PUBLIC_ARCHIVE_URL.format(version=remote_ver)
            result = install_update_archive(
                _download_url(archive_url), ROOT, expected_version=remote_ver)
        except (RuntimeError, ValueError, OSError, zipfile.BadZipFile) as exc:
            return {"ok": False, "error": f"ZIP 更新已中止：{exc}"}
        return {"ok": True, "message": f"已更新 {result['changed']} 个发行文件。",
                "new_version": app_version(), "restart_needed": True,
                "install_type": "archive", "backup": result["backup"]}
    _, upstream_error = _official_upstream()
    if upstream_error:
        return {"ok": False, "error": upstream_error}
    rc, dirty, _ = _git(["status", "--porcelain"], timeout=15)
    if rc != 0:
        return {"ok": False, "error": "读不到 git 状态，已中止。"}
    if dirty:
        n = len(dirty.splitlines())
        return {"ok": False, "dirty": True,
                "error": f"检测到 {n} 处本地未提交改动，为避免冲突已中止。"
                         f"请先提交或搁置（git stash）本地改动，再拉取更新。"}
    rc, out, err = _git(["pull", "--ff-only"], timeout=120)
    if rc != 0:
        return {"ok": False,
                "error": f"拉取失败（多半是本地历史与远端分叉，需手动处理）：{err or out}"}
    return {"ok": True, "message": out or "已更新到最新。",
            "new_version": app_version(), "restart_needed": True}


def build_author_state():
    """桌面作者模式的完整状态。集中在一处，避免导出/手机视图各抄一份。"""
    reads = load_reads()
    personas = load_personas()
    curation = load_curation()
    votes = load_vote_tally()
    return {
        "poems": load_corpus(),
        "reads": reads,
        "personas": personas,
        "personas_defaults": json.loads(PERSONAS.read_text(encoding="utf-8")),
        "personas_sidecar": load_personas_sidecar(),
        "curation": curation,
        "thread_meta": load_thread_meta(),
        "votes": votes,
        "voter_votes": load_voter_votes(),
        "persona_echo": build_persona_echo(reads, personas, curation, votes),
        "favs": load_favs(),
        "stanzas": load_stanzas(),
        "calibration": load_calibration(),
        "settings": load_settings(),
        "version": app_version(),
    }


def build_mobile_snapshot(include_wordcloud=True):
    """生成只读移动快照；不落盘、不维护第二份真源。

    快照保留作者在手机阅读所需的正文、自注、评论和派生统计，但移除设备 GUID、
    原始来源清单、人设编辑底稿、派发设置与本地端口。手机自己的偏爱/进度/随记由
    浏览器独立保存，不进入这份从电脑生成的状态，因此下次刷新不会覆盖它们。
    """
    state = build_author_state()
    poems = [{k: v for k, v in poem.items() if k not in {"guid", "source"}}
             for poem in state["poems"]]
    settings = state.get("settings") or {}
    mobile = {
        "poems": poems,
        "reads": state["reads"],
        "personas": state["personas"],
        "personas_defaults": [],
        "personas_sidecar": [],
        "curation": state["curation"],
        "thread_meta": state["thread_meta"],
        "votes": state["votes"],
        "voter_votes": state["voter_votes"],
        "persona_echo": state["persona_echo"],
        "favs": state["favs"],
        "stanzas": state["stanzas"],
        "calibration": state["calibration"],
        "settings": {k: settings.get(k) for k in (
            "site_title", "site_subtitle", "footer_text", "default_view", "score_badge",
            "show_poetry_boards", "hidden_genre_boards")},
        "version": state["version"],
    }
    if include_wordcloud:
        try:
            mobile["wordcloud"] = load_wordcloud()
        except Exception as exc:
            print(f"[mobile] 词云快照生成失败，已略过：{exc}")
            mobile["wordcloud"] = None
    canonical = json.dumps(mobile, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    mobile["mobile"] = {
        "schema": 1,
        "mode": "readonly",
        "generated_at": now_iso(),
        "content_hash": hashlib.sha256(canonical).hexdigest(),
        "poems": len(mobile["poems"]),
        "reads": len(mobile["reads"]),
    }
    return mobile


def render_mobile_snapshot_html(snapshot=None):
    """把同一套前端与一份移动快照嵌成单 HTML；全程不引用外网资源。"""
    snapshot = snapshot or build_mobile_snapshot(include_wordcloud=True)
    index = (WEBAPP / "index.html").read_text(encoding="utf-8")
    css = (WEBAPP / "style.css").read_text(encoding="utf-8")
    app_js = (WEBAPP / "app.js").read_text(encoding="utf-8")
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    index = index.replace('content="author"', 'content="snapshot"')
    index = index.replace('<link rel="manifest" href="manifest.webmanifest">', "")
    index = index.replace('<link rel="stylesheet" href="style.css">', f"<style>\n{css}\n</style>")
    inline = f"<script>window.__ZQ_SNAPSHOT__={payload};</script>\n<script>\n{app_js}\n</script>"
    index = index.replace('<script src="app.js"></script>', inline)
    icon = WEBAPP / "favicon.png"
    if icon.exists():
        uri = "data:image/png;base64," + base64.b64encode(icon.read_bytes()).decode("ascii")
        index = index.replace('href="favicon.png"', f'href="{uri}"')
        index = index.replace('href="apple-touch-icon.png"', f'href="{uri}"')
    index = re.sub(r'<link rel="icon" href="favicon\.ico"[^>]*>\s*', "", index)
    return index.encode("utf-8")


def _local_ipv4s():
    """列出可给手机打开的本机 IPv4；包含局域网与可选的 Tailscale 地址。"""
    found = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(item[4][0])
    except OSError:
        pass
    try:
        proc = subprocess.run(["tailscale", "ip", "-4"], cwd=ROOT,
                              capture_output=True, text=True, timeout=5,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode == 0:
            found.update(x.strip() for x in proc.stdout.splitlines() if x.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    out = []
    for value in found:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            continue
        if ip.version == 4 and not ip.is_loopback and not ip.is_link_local and not ip.is_multicast:
            out.append(value)
    return sorted(out, key=lambda x: tuple(int(p) for p in x.split(".")))


_QR_MODULE = None


def qr_svg(text, border=4):
    """用随发行包附带的 MIT 实现本机生成二维码；访问口令不会发送给第三方。"""
    global _QR_MODULE
    if _QR_MODULE is None:
        path = ROOT / "theater" / "vendor" / "qrcodegen.py"
        spec = importlib.util.spec_from_file_location("zhouqingji_qrcodegen", path)
        if not spec or not spec.loader:
            raise RuntimeError("二维码组件无法加载")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _QR_MODULE = module
    qr = _QR_MODULE.QrCode.encode_text(text, _QR_MODULE.QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    parts = []
    for y in range(size):
        for x in range(size):
            if qr.get_module(x, y):
                parts.append(f"M{x + border},{y + border}h1v1h-1z")
    dim = size + border * 2
    path_data = "".join(parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fcf9f2"/>'
            f'<path d="{path_data}" fill="#24544c"/></svg>').encode("utf-8")


class MobileHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class MobileHandler(BaseHTTPRequestHandler):
    """手机观看专用服务：静态壳可见，数据需随机口令；所有 POST 一律拒绝。"""

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8",
              cache="no-store", headers=None):
        data = body if isinstance(body, bytes) else \
            json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _authorized(self):
        supplied = self.headers.get("X-ZQ-Mobile-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, self.server.mobile_token)

    def _query_pair(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query,
                                      keep_blank_values=True)
        values = query.get("pair") or []
        supplied = values[0] if len(values) == 1 else ""
        return supplied if supplied and hmac.compare_digest(
            supplied, self.server.mobile_token) else ""

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/mobile-state":
            if not self._authorized():
                return self._send(401, {"error": "手机访问口令无效，请从电脑重新扫码。"})
            snapshot = build_mobile_snapshot(include_wordcloud=False)
            etag = '"' + snapshot["mobile"]["content_hash"] + '"'
            if self.headers.get("If-None-Match") == etag:
                return self._send(304, b"", headers={"ETag": etag})
            return self._send(200, snapshot, headers={"ETag": etag})
        if path == "/api/wordcloud":
            if not self._authorized():
                return self._send(401, {"error": "手机访问口令无效。"})
            return self._send(200, load_wordcloud())
        if path == "/api/word-context":
            if not self._authorized():
                return self._send(401, {"error": "手机访问口令无效。"})
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                data = load_word_context((query.get("mode") or [""])[0],
                                         (query.get("word") or [""])[0])
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, data)
        if path == "/manifest.webmanifest":
            manifest = json.loads((WEBAPP / "manifest.webmanifest").read_text(encoding="utf-8"))
            pair = self._query_pair()
            if pair:
                # iPad“添加到主屏幕”可能把 Web App 与 Safari 存储隔离；安装启动地址
                # 自带当前连接签，才不依赖 Safari 预览页的 localStorage。
                manifest["start_url"] = f"./?pair={urllib.parse.quote(pair)}#/settings"
            return self._send(200, manifest, "application/manifest+json; charset=utf-8",
                              cache="no-store")
        if path == "/":
            html = (WEBAPP / "index.html").read_text(encoding="utf-8")
            html = html.replace('content="author"', 'content="mobile"')
            pair = self._query_pair()
            if pair:
                href = "manifest.webmanifest?pair=" + urllib.parse.quote(pair)
                html = html.replace('href="manifest.webmanifest"', f'href="{href}"')
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        f = (WEBAPP / path.lstrip("/")).resolve()
        if WEBAPP.resolve() in f.parents and f.is_file():
            extra = {"Service-Worker-Allowed": "/"} if f.name == "sw.js" else None
            return self._send(200, f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"),
                              cache="no-cache", headers=extra)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        return self._send(405, {"error": "手机观看入口只读，不接受写入。"},
                          headers={"Allow": "GET"})


class MobileAccess:
    """手机只读入口；默认一次性，也可由作者明确保存为 30 天可信入口。"""

    def __init__(self):
        self._lock = threading.RLock()
        self.server = None
        self.thread = None
        self.token = None
        self.port = None
        self.trusted = False
        self.trust_expires_at = None

    @staticmethod
    def _load_trust():
        if not MOBILE_TRUST.exists():
            return None
        try:
            data = json.loads(MOBILE_TRUST.read_text(encoding="utf-8"))
            token = data.get("token")
            port = data.get("port")
            expires_at = float(data.get("expires_at", 0))
            if (data.get("schema") != 1 or not isinstance(token, str) or len(token) < 24
                    or not isinstance(port, int) or not 1024 <= port <= 65535
                    or expires_at <= time.time()):
                return None
            return {"token": token, "port": port, "expires_at": expires_at}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _save_trust(token, port, expires_at):
        MOBILE_TRUST.parent.mkdir(parents=True, exist_ok=True)
        tmp = MOBILE_TRUST.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "schema": 1,
            "token": token,
            "port": port,
            "expires_at": expires_at,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(MOBILE_TRUST)

    @staticmethod
    def _delete_trust():
        try:
            MOBILE_TRUST.unlink()
        except FileNotFoundError:
            pass

    def restore_trusted(self):
        """应用启动时恢复尚未过期的可信入口；失败不妨碍桌面应用启动。"""
        saved = self._load_trust()
        if not saved:
            return None
        return self.start(saved["port"], trusted=True, _saved=saved)

    def start(self, port, trusted=False, _saved=None):
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError("手机访问端口需为 1024–65535 的整数")
        if not isinstance(trusted, bool):
            raise ValueError("可信入口选项格式无效")
        with self._lock:
            if self.server and self.port == port and self.trusted == trusted:
                return self.status()
            if self.server:
                self.stop()
            saved = _saved or (self._load_trust() if trusted else None)
            token = saved["token"] if saved else secrets.token_urlsafe(24)
            expires_at = saved["expires_at"] if saved else (
                time.time() + MOBILE_TRUST_SECONDS if trusted else None)
            try:
                server = MobileHTTPServer(("0.0.0.0", port), MobileHandler)
            except OSError as exc:
                raise ValueError(f"端口 {port} 无法开启：{exc}") from exc
            server.mobile_token = token
            thread = threading.Thread(target=server.serve_forever,
                                      name="zhouqingji-mobile", daemon=True)
            self.server, self.thread, self.token, self.port = server, thread, token, port
            self.trusted, self.trust_expires_at = trusted, expires_at
            if trusted:
                self._save_trust(token, port, expires_at)
            thread.start()
            return self.status()

    def stop(self, revoke=False):
        if not isinstance(revoke, bool):
            raise ValueError("撤销可信入口选项格式无效")
        with self._lock:
            server, thread = self.server, self.thread
            self.server = self.thread = self.token = self.port = None
            self.trusted, self.trust_expires_at = False, None
            if revoke:
                self._delete_trust()
        if server:
            server.shutdown()
            server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        return self.status()

    def status(self):
        with self._lock:
            running, port, token = bool(self.server), self.port, self.token
            trusted, expires_at = self.trusted, self.trust_expires_at
        urls = []
        if running:
            urls = [f"http://{ip}:{port}/?pair={token}" for ip in _local_ipv4s()]
        return {"running": running, "port": port, "urls": urls,
                "token": token if running else None, "trusted": trusted,
                "trust_expires_at": (time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                     time.localtime(expires_at))
                                     if trusted and expires_at else None)}

    def valid_pair_url(self, value):
        """只为本轮有效配对地址生成二维码，兼容 Tailscale Serve 的 HTTPS 域名。"""
        with self._lock:
            token = self.token if self.server else None
        if not token or not isinstance(value, str) or len(value) > 2048:
            return False
        try:
            parsed = urllib.parse.urlsplit(value)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                    and parsed.username is None and parsed.password is None
                    and len(query.get("pair", [])) == 1
                    and hmac.compare_digest(query["pair"][0], token))
        except (TypeError, ValueError):
            return False

    def private_pair_url(self, base):
        """把 Tailscale Serve 的公开基址和本轮口令在服务端合成，避免嵌套查询歧义。"""
        with self._lock:
            token = self.token if self.server else None
        if not token or not isinstance(base, str) or len(base) > 2048:
            return None
        try:
            parsed = urllib.parse.urlsplit(base)
            if (parsed.scheme != "https" or not parsed.hostname
                    or not parsed.hostname.lower().endswith(".ts.net")
                    or parsed.username is not None or parsed.password is not None):
                return None
            query = [(k, v) for k, v in urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True) if k != "pair"]
            query.append(("pair", token))
            return urllib.parse.urlunsplit(parsed._replace(
                query=urllib.parse.urlencode(query), fragment=""))
        except (TypeError, ValueError):
            return None


MOBILE_ACCESS = MobileAccess()


def _is_local_host_header(value, port):
    """拒绝 DNS rebinding：Host 必须明确指向当前回环服务。"""
    host = (value or "").strip().lower()
    return host in {f"127.0.0.1:{port}", f"localhost:{port}"}


def _is_local_origin(value, port):
    """有 Origin 时只接受当前回环源；无 Origin 留给本地 CLI。"""
    if not value:
        return True
    try:
        parsed = urllib.parse.urlsplit(value)
        return (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.port == port and parsed.username is None
                and parsed.password is None)
    except ValueError:
        return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 安静

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else \
            json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _local_request_allowed(self):
        port = int(self.server.server_address[1])
        return (_is_local_host_header(self.headers.get("Host"), port)
                and _is_local_origin(self.headers.get("Origin"), port))

    def do_GET(self):
        if not self._local_request_allowed():
            return self._send(403, {"error": "forbidden origin"})
        path = self.path.split("?")[0]
        if path == "/api/wordcloud":
            return self._send(200, load_wordcloud())
        if path == "/api/word-context":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                data = load_word_context((query.get("mode") or [""])[0],
                                         (query.get("word") or [""])[0])
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, data)
        if path == "/api/mobile/status":
            return self._send(200, MOBILE_ACCESS.status())
        if path == "/api/mobile/qr":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            text = (query.get("text") or [""])[0]
            if not text and query.get("base"):
                text = MOBILE_ACCESS.private_pair_url(query["base"][0]) or ""
            if not MOBILE_ACCESS.valid_pair_url(text):
                return self._send(400, {"error": "二维码地址无效或手机入口已关闭"})
            return self._send(200, qr_svg(text), "image/svg+xml")
        if path == "/api/state":
            return self._send(200, build_author_state())
        # 静态文件
        if path == "/":
            path = "/index.html"
        f = (WEBAPP / path.lstrip("/")).resolve()
        if WEBAPP.resolve() in f.parents and f.is_file():
            return self._send(200, f.read_bytes(),
                              MIME.get(f.suffix, "application/octet-stream"))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._local_request_allowed():
            return self._send(403, {"error": "forbidden origin"})
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "bad json"})

        try:
            if self.path == "/api/settings":
                set_settings(payload)
                return self._send(200, {"ok": True, "settings": load_settings()})
            if self.path == "/api/update/check":
                return self._send(200, update_check())
            if self.path == "/api/update/pull":
                return self._send(200, update_pull())
            if self.path == "/api/mobile/start":
                port = payload.get("port", load_settings().get("mobile_port", 8738))
                trusted = payload.get("trusted", False)
                return self._send(200, MOBILE_ACCESS.start(port, trusted=trusted))
            if self.path == "/api/mobile/stop":
                return self._send(200, MOBILE_ACCESS.stop(
                    revoke=payload.get("revoke", False)))
            if self.path == "/api/mobile/export":
                html = render_mobile_snapshot_html()
                stamp = time.strftime("%Y%m%d-%H%M")
                return self._send(200, html, "text/html; charset=utf-8", headers={
                    "Content-Disposition": f'attachment; filename="zhouqingji-mobile-{stamp}.html"'})
            if self.path == "/api/personas":
                set_personas(payload)
                return self._send(200, {"ok": True, "personas": load_personas()})
            if self.path == "/api/curate":
                entry = curate(payload)
                return self._send(200, {"ok": True, "curation": entry})
            if self.path == "/api/favorite":
                set_favorite(payload)
                return self._send(200, {"ok": True})
            if self.path == "/api/stanzas":
                set_stanzas(payload)
                return self._send(200, {"ok": True})
            if self.path == "/api/action":
                action = payload.get("action")
                if action not in ACTIONS:
                    return self._send(400, {"error": f"未知动作 {action}"})
                corpus = load_corpus()
                poem = next((p for p in corpus if p["id"] == payload.get("id")), None)
                if poem is None:
                    return self._send(404, {"error": "poem not found"})
                ACTIONS[action](poem, payload, corpus)
                save_corpus(corpus)
                return self._send(200, {"ok": True, "poem": poem})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    st = load_settings()
    print(f"{st['site_title']}·{st['site_subtitle']}  →  http://localhost:{st['port']}")
    try:
        restored = MOBILE_ACCESS.restore_trusted()
        if restored:
            print(f"可信手机入口已恢复（有效至 {restored['trust_expires_at']}）")
    except (OSError, ValueError) as exc:
        print(f"可信手机入口未能自动恢复：{exc}")
    ThreadingHTTPServer(("127.0.0.1", st["port"]), Handler).serve_forever()
