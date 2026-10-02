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

try:
    from . import book_order
except ImportError:  # python theater/src/server.py
    import book_order

ROOT = Path(__file__).resolve().parents[2]
# 启动时固定当前后端代码身份；旧进程读取同一磁盘上的 VERSION 不能冒充新版本。
AUTHOR_BUILD_ID = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
CORPUS = ROOT / "corpus" / "诗稿.json"
BACKUPS = ROOT / "corpus" / ".backups"
READS = ROOT / "results" / "reads" / "reads.jsonl"
CURATION = ROOT / "results" / "curation.json"
THREAD_META = ROOT / "results" / "threads" / "meta.json"
VOTES = ROOT / "results" / "votes" / "votes.jsonl"
CALIBRATION = ROOT / "results" / "calibration" / "scores.json"
FAVS = ROOT / "corpus" / "作者偏爱.json"
STANZAS = ROOT / "corpus" / "分段.json"
BOOK_PROJECTS = ROOT / "corpus" / "诗集方案.json"
PERSONAS = ROOT / "theater" / "personas" / "personas.json"
PERSONAS_SIDECAR = ROOT / "corpus" / "personas.json"
WEBAPP = Path(__file__).resolve().parent / "webapp"
VERSION_FILE = ROOT / "VERSION"
PUBLIC_VERSION_URL = "https://raw.githubusercontent.com/Cyanaug/zhouqingji/main/VERSION"
PUBLIC_ARCHIVE_URL = "https://github.com/Cyanaug/zhouqingji/archive/refs/tags/v{version}.zip"
PUBLIC_REPO_URL = "https://github.com/Cyanaug/zhouqingji"
# 这个地址只承载公开的 PWA 程序外壳，不承载作者作品或评论。正式发布前由
# GitHub Pages 工作流部署；本地开发可用环境变量指向另一个 HTTPS 测试壳。
DEFAULT_MOBILE_PWA_URL = "https://cyanaug.github.io/zhouqingji/mobile.html"
# 官方空壳已独立部署并通过 marker/哈希验收；环境变量仍可让开发者指向测试壳。
MOBILE_PWA_URL = (os.environ.get("ZQ_MOBILE_PWA_URL") or DEFAULT_MOBILE_PWA_URL).strip()
UPDATE_MAX_DOWNLOAD = 50 * 1024 * 1024
UPDATE_MAX_EXPANDED = 120 * 1024 * 1024
UPDATE_MAX_FILES = 5000
API_MAX_BODY = 32 * 1024 * 1024

UPDATE_ROOT_FILES = {
    ".gitignore", "AGENTS.md", "CLAUDE.md", "LICENSE", "README.md", "VERSION",
    "00_START_HERE.md", "01_corpus_schema.md", "02_readers_and_casting.md",
    "03_runner_and_coverage.md", "04_app_and_design.md", "05_run_modes.md",
    "MOBILE_ACCESS.md", "PROGRESS.md",
}
UPDATE_PREFIXES = (
    ".agents/skills/", ".codex/agents/", ".claude/agents/", ".claude/skills/",
    ".github/workflows/", "theater/assets/", "theater/release/", "theater/src/",
    "theater/tests/", "theater/tools/", "theater/vendor/",
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
MOBILE_TRUST_RENEW_WINDOW = 7 * 24 * 60 * 60
MOBILE_SYNC_RECORD_INTERVAL = 5 * 60

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


BOOK_SORT_MODES = ("manual", "time_asc", "time_desc", "quality_desc")
BOOK_PAGE_SIZES = ("A5", "B5")
BOOK_SECTION_STARTS = ("recto", "next")
BOOK_DATE_POSITIONS = ("none", "under_title", "poem_end")
BOOK_INTERIOR_COLORS = ("warm", "mono")
BOOK_BLOCK_ALIGNS = ("left", "center")
BOOK_RUNNING_HEAD_CONTENTS = ("book", "section", "both")
BOOK_PAGE_KINDS = ("prose", "poem", "blank", "image")
BOOK_IMAGE_WIDTHS = (40, 60, 80, 100)
BOOK_IMAGE_ALIGNS = ("left", "center", "right")
BOOK_IMAGE_FITS = ("contain", "cover")
BOOK_TAILPIECE_PRESETS = {
    "small": {"width_pct": 20, "height_mm": 16, "gap_pt": 12},
    "standard": {"width_pct": 32, "height_mm": 24, "gap_pt": 18},
    "large": {"width_pct": 48, "height_mm": 36, "gap_pt": 24},
}
# 插图图片库：按内容哈希落盘，任何方案可复用；目录在 corpus 下，私有且不进 release。
BOOK_IMAGES_DIR = ROOT / "corpus" / "诗集图片"
BOOK_IMAGE_MAX_BYTES = 10 * 1024 * 1024
BOOK_IMAGE_MIMES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
BOOK_APPENDIX_KEYS = ("author_notes", "scores", "author_marks", "comments")
# 与 app.js 的 BOOK_TYPOGRAPHY_PROFILES 保持一致；新增 profile 时两处同步。
BOOK_TYPOGRAPHY_PROFILE_IDS = ("qinglang-song-105-18", "shulang-song-11-22")
BOOK_DEFAULT_PROFILE_ID = "qinglang-song-105-18"
BOOK_PROFILE_OVERRIDE_RANGES = {
    "topMm": (5, 40), "bottomMm": (5, 40), "innerMm": (5, 40), "outerMm": (5, 40),
    "bodyPt": (8, 14), "leadingPt": (12, 30),
}
BOOK_SECTION_ID_RE = re.compile(r"section-[a-z0-9-]{6,48}")


def _empty_book_projects():
    return {"schema": 3, "books": []}


# 方案侧车的结构版本。内部 schema 2 一次性备份迁移后，只保存单一编次。
# 非当前格式只读保护，绝不让不理解该文件的程序覆盖它。
BOOK_PROJECTS_SCHEMA = 3
BOOK_PROJECTS_BACKUP_KEEP = 200
BOOK_PROJECTS_LOCK = threading.RLock()
AUTHOR_API_LEVEL = 3


class BookRevisionConflict(ValueError):
    """诗集方案被另一个页面或进程先一步保存。"""

    def __init__(self, current_revision):
        super().__init__("磁盘中的方案已更新；本页面的旧草稿没有覆盖它")
        self.current_revision = current_revision


def load_book_projects(strict=False):
    """诗集工作台的作者侧车。普通方案只引用作品；显式分段编稿副本
    会另存出版正文，不改冻结 corpus schema。读页面时容错；写入时严格拒绝
    覆盖已损坏的方案文件，也拒绝降级覆盖由更新版本创建的文件。
    """
    if not BOOK_PROJECTS.exists():
        return _empty_book_projects()
    try:
        data = json.loads(BOOK_PROJECTS.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("books"), list):
            raise ValueError("books 必须是数组")
        schema = data.get("schema")
        if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
            raise ValueError("schema 缺失或无效")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        if strict:
            raise ValueError("诗集方案文件无法解析；为避免覆盖原文件，本次保存已中止") from exc
        data = _empty_book_projects()
        data["error"] = "诗集方案文件无法解析；请先恢复备份再继续编辑。"
        return data
    if schema != BOOK_PROJECTS_SCHEMA:
        # 不支持的旧/未来格式都原样带回并置只读；迁移只允许在已备份的离线操作中完成。
        direction = "旧格式，请先迁移方案文件" if schema < BOOK_PROJECTS_SCHEMA else "更新版本创建，请升级应用"
        error = (f"这份方案文件的格式与当前程序不同（schema {schema}，"
                 f"当前 {BOOK_PROJECTS_SCHEMA}）；{direction}后再编辑。")
        if strict:
            raise ValueError(error + " 为避免降级破坏数据，本次保存已中止")
        return {**data, "error": error, "readonly": True}
    return data


def _write_book_projects(data):
    BOOK_PROJECTS.parent.mkdir(parents=True, exist_ok=True)
    if BOOK_PROJECTS.exists():
        BACKUPS.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
        shutil.copy2(BOOK_PROJECTS, BACKUPS / f"诗集方案-{stamp}.json")
        # 备份封顶：只保留最近 N 份，长年使用也不会无限增长。
        backups = sorted(BACKUPS.glob("诗集方案-*.json"))
        for old in backups[:-BOOK_PROJECTS_BACKUP_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
    tmp = BOOK_PROJECTS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(BOOK_PROJECTS)


def _detect_image_type(blob: bytes):
    """只认文件魔数，不信任扩展名；返回 (ext, mime) 或 None。"""
    png_magic = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
    if blob[:8] == png_magic:
        return "png", "image/png"
    if blob[:3] == bytes((0xFF, 0xD8, 0xFF)):
        return "jpg", "image/jpeg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def save_book_image(payload):
    """插图上传：校验魔数与大小，按 sha256 前 16 位落盘（去重），返回图片 ID。
    目录在 corpus 下私有保存，不进 release 允许清单。"""
    data_b64 = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data_b64, str) or not data_b64:
        raise ValueError("缺少图片数据")
    try:
        blob = base64.b64decode(data_b64, validate=True)
    except Exception as exc:
        raise ValueError("图片数据不是有效的 base64") from exc
    if not blob:
        raise ValueError("图片内容为空")
    if len(blob) > BOOK_IMAGE_MAX_BYTES:
        raise ValueError("图片超过 10MB 上限")
    detected = _detect_image_type(blob)
    if not detected:
        raise ValueError("只支持 JPEG / PNG / WebP 图片")
    ext, mime = detected
    image_id = hashlib.sha256(blob).hexdigest()[:16]
    BOOK_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    target = BOOK_IMAGES_DIR / f"{image_id}.{ext}"
    if not target.exists():
        tmp = target.with_suffix(f".{ext}.tmp")
        tmp.write_bytes(blob)
        tmp.replace(target)
    name = str(payload.get("name") or "").strip()[:120]
    return {"image_id": image_id, "ext": ext, "mime": mime, "bytes": len(blob), "name": name}


def load_book_image(image_id):
    """按 ID 读取插图；ID 必须是 16 位十六进制，杜绝路径穿越。"""
    if not re.fullmatch(r"[0-9a-f]{16}", str(image_id or "")):
        raise ValueError("图片 ID 无效")
    if not BOOK_IMAGES_DIR.exists():
        raise ValueError("找不到这张图片")
    for entry in BOOK_IMAGES_DIR.glob(f"{image_id}.*"):
        mime = BOOK_IMAGE_MIMES.get(entry.suffix.lower())
        if mime:
            return entry, mime
    raise ValueError("找不到这张图片")


def list_book_images():
    """列出作者私有图片库中的可复用图片，不暴露文件名或本机路径。"""
    if not BOOK_IMAGES_DIR.exists():
        return []
    images = []
    for entry in BOOK_IMAGES_DIR.iterdir():
        if not entry.is_file() or not re.fullmatch(r"[0-9a-f]{16}", entry.stem):
            continue
        mime = BOOK_IMAGE_MIMES.get(entry.suffix.lower())
        if not mime:
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        images.append({
            "image_id": entry.stem,
            "mime": mime,
            "bytes": stat.st_size,
            "updated_at": int(stat.st_mtime),
        })
    images.sort(key=lambda item: (-item["updated_at"], item["image_id"]))
    return images


def _book_image_exists(image_id):
    return (BOOK_IMAGES_DIR.exists()
            and any(BOOK_IMAGES_DIR.glob(f"{image_id}.*")))


def _book_image_references(book):
    """把图片页和尾花投影为统一只读资源关系。"""
    references = []
    pages = ((book.get("inserts") or {}).values() if "order" in book
             else (book.get("pages") or []))
    for page in pages:
        if not isinstance(page, dict) or page.get("kind") != "image":
            continue
        image_id = str(page.get("image_id") or "").strip()
        if image_id:
            references.append({
                "image_id": image_id,
                "role": "full-page",
                "anchor": {"block_id": f"insert:{page.get('id') or ''}", "edge": "self"},
            })
    for poem_id, image_id in (book.get("tailpieces") or {}).items():
        image_id = str(image_id or "").strip()
        if image_id:
            references.append({
                "image_id": image_id,
                "role": "poem-end-ornament",
                "anchor": {"block_id": f"poem:{poem_id}", "edge": "end"},
            })
    return references


def clean_orphan_book_images(dry_run=False):
    """只统计当前方案未引用的候选图片；不代表可删除，不执行删除。"""
    # 未保存草稿、备份以及未来扩展中的引用尚不能完整追踪。只提供候选统计，
    # 显式调用也不得绕过保全边界；不把“当前方案未引用”等同于“可以删除”。
    if not dry_run:
        raise ValueError("图片清理暂只支持预览；备份与草稿引用尚未完整核验，不执行删除")
    if not BOOK_IMAGES_DIR.exists():
        return {"total": 0, "in_use": 0, "removed": 0, "freed_bytes": 0, "files": [], "dry_run": dry_run}
    data = load_book_projects(strict=True)
    in_use_ids = set()
    for book in data.get("books", []):
        in_use_ids.update(reference["image_id"] for reference in _book_image_references(book))
    all_files = [f for f in BOOK_IMAGES_DIR.iterdir() if f.is_file()]
    total_count = len(all_files)
    removed_files = []
    freed_bytes = 0
    for file in all_files:
        stem = file.stem.lower()
        if stem not in in_use_ids:
            size = file.stat().st_size
            removed_files.append({"name": file.name, "image_id": stem, "bytes": size})
            freed_bytes += size
    return {
        "total": total_count,
        "in_use": len(in_use_ids),
        "removed": len(removed_files),
        "freed_bytes": freed_bytes,
        "files": removed_files,
        "dry_run": dry_run,
    }



def _book_text_hash(text):
    """与前端 bookTextHash 相同的 UTF-16 FNV-1a 指纹，仅供修订失效判断。"""
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    value = 0x811c9dc5
    for at in range(0, len(encoded), 2):
        value = ((value ^ (encoded[at] | encoded[at + 1] << 8)) * 0x01000193) & 0xffffffff
    return f"{value:08x}"


def _book_extensions(clean, raw, previous=None, reserved=()):
    """只补未知字段；已知字段以校验结果为准，不复活删除的记录。"""
    known = set(clean) | set(reserved)
    result = {}
    for source in (previous, raw):
        if isinstance(source, dict):
            result.update({key: value for key, value in source.items() if key not in known})
    result.update(clean)
    return result


def _clean_book_project(raw, previous=None):
    if not isinstance(raw, dict):
        raise ValueError("诗集方案必须是对象")
    previous = previous or {}
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("诗集名不能为空")
    if len(title) > 120:
        raise ValueError("诗集名不能超过 120 字")

    book_id = str(raw.get("id") or "").strip()
    if not book_id:
        book_id = "book-" + secrets.token_hex(5)
    if not re.fullmatch(r"book-[a-z0-9-]{6,48}", book_id):
        raise ValueError("诗集方案 ID 无效")

    poem_ids = raw.get("poem_ids", [])
    if not isinstance(poem_ids, list) or not all(isinstance(x, str) for x in poem_ids):
        raise ValueError("poem_ids 必须是字符串数组")
    corpus_by_id = {p["id"]: p for p in load_corpus()}
    known = set(corpus_by_id)
    ordered, seen = [], set()
    for poem_id in poem_ids:
        if poem_id not in known:
            raise ValueError(f"找不到作品：{poem_id}")
        if poem_id not in seen:
            ordered.append(poem_id)
            seen.add(poem_id)

    sort_mode = raw.get("sort_mode", "manual")
    if sort_mode not in BOOK_SORT_MODES:
        raise ValueError("诗集排序方式无效")
    layout = raw.get("layout") or {}
    if not isinstance(layout, dict):
        raise ValueError("layout 必须是对象")
    page_size = layout.get("page_size", "A5")
    if page_size not in BOOK_PAGE_SIZES:
        raise ValueError("页面尺寸只能是 A5/B5")
    start_each_poem = layout.get("start_each_poem", True)
    if not isinstance(start_each_poem, bool):
        raise ValueError("start_each_poem 必须是布尔值")
    running_head = layout.get("running_head", True)
    folio = layout.get("folio", True)
    if not isinstance(running_head, bool) or not isinstance(folio, bool):
        raise ValueError("页眉与页码开关必须是布尔值")
    section_start = layout.get("section_start", "recto")
    if section_start not in BOOK_SECTION_STARTS:
        raise ValueError("分辑扉页只能右页起或紧接下一页")
    date_position = layout.get("date_position", "none")
    if date_position not in BOOK_DATE_POSITIONS:
        raise ValueError("写作时间位置无效")
    interior_color = layout.get("interior_color", "warm")
    if interior_color not in BOOK_INTERIOR_COLORS:
        raise ValueError("书芯颜色只能是暖红点色或黑白")
    block_align = layout.get("block_align", "left")
    if block_align not in BOOK_BLOCK_ALIGNS:
        raise ValueError("诗节对齐只能是左对齐或居中成块")
    running_head_content = layout.get("running_head_content", "book")
    if running_head_content not in BOOK_RUNNING_HEAD_CONTENTS:
        raise ValueError("页眉内容只能是书名、辑名或两者")
    show_numbering = layout.get("show_numbering", True)
    continue_hint = layout.get("continue_hint", False)
    if not isinstance(show_numbering, bool) or not isinstance(continue_hint, bool):
        raise ValueError("“第 N 首”编号与页底未完提示必须是布尔值")

    profile_id = layout.get("profile_id")
    if profile_id is not None:
        if not isinstance(profile_id, str) or profile_id not in BOOK_TYPOGRAPHY_PROFILE_IDS:
            raise ValueError("未知版式 profile")
    overrides_raw = layout.get("profile_overrides") or {}
    if not isinstance(overrides_raw, dict):
        raise ValueError("profile_overrides 必须是对象")
    clean_overrides = {}
    for key, value in overrides_raw.items():
        if key not in BOOK_PROFILE_OVERRIDE_RANGES:
            raise ValueError(f"不允许覆盖版式参数：{key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"版式参数 {key} 必须是数字")
        low, high = BOOK_PROFILE_OVERRIDE_RANGES[key]
        if not low <= value <= high:
            raise ValueError(f"版式参数 {key} 超出允许范围 {low}–{high}")
        clean_overrides[key] = float(value)

    # 前置页：只保存作者显式填写的书名页开关与出版说明文字；
    # 不编造出版社、ISBN、版次等任何出版信息。旧方案缺少该键时等价于关闭。
    front_matter = raw.get("front_matter") or {}
    if not isinstance(front_matter, dict):
        raise ValueError("front_matter 必须是对象")
    title_page = front_matter.get("title_page", False)
    if not isinstance(title_page, bool):
        raise ValueError("front_matter.title_page 必须是布尔值")
    colophon_raw = front_matter.get("colophon", "")
    if not isinstance(colophon_raw, str):
        raise ValueError("front_matter.colophon 必须是字符串")
    colophon = colophon_raw.strip()
    if len(colophon) > 2000:
        raise ValueError("出版说明最长 2000 字")
    # 题词/献词页按出版惯例只有正文、无标题，放在书名页之前；一页放不下就失去
    # “一页题词”的语义，因此限制比出版说明更紧。
    dedication_raw = front_matter.get("dedication", "")
    if not isinstance(dedication_raw, str):
        raise ValueError("front_matter.dedication 必须是字符串")
    dedication = dedication_raw.strip()
    if len(dedication) > 500:
        raise ValueError("题词最长 500 字")

    sections = raw.get("sections") or []
    if not isinstance(sections, list):
        raise ValueError("sections 必须是数组")
    if len(sections) > 100:
        raise ValueError("分辑不能超过 100 个")
    clean_sections, section_ids, section_anchors = [], set(), set()
    section_sources = {}
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("分辑必须是对象")
        section_id = str(section.get("id") or "").strip()
        if not section_id:
            section_id = "section-" + secrets.token_hex(5)
        if not BOOK_SECTION_ID_RE.fullmatch(section_id):
            raise ValueError("分辑 ID 无效")
        if section_id in section_ids:
            raise ValueError("分辑 ID 重复")
        # 分辑名用独立变量：不得覆盖外层书名 title（书名要在函数末尾返回）。
        section_title = str(section.get("title") or "").strip()
        if not section_title:
            raise ValueError("分辑名不能为空")
        if len(section_title) > 120:
            raise ValueError("分辑名不能超过 120 字")
        before_poem_id = str(section.get("before_poem_id") or "").strip()
        if before_poem_id not in seen:
            raise ValueError("分辑起点必须是已入集作品")
        if before_poem_id in section_anchors:
            raise ValueError("同一首作品前只能放一个分辑")
        clean_sections.append({
            "id": section_id,
            "title": section_title,
            "subtitle": str(section.get("subtitle") or "").strip()[:240],
            "before_poem_id": before_poem_id,
        })
        section_sources[section_id] = section
        section_ids.add(section_id)
        section_anchors.add(before_poem_id)
    poem_order = {poem_id: index for index, poem_id in enumerate(ordered)}
    clean_sections.sort(key=lambda section: poem_order[section["before_poem_id"]])

    # 诗集校样分行只保存 Unicode 字符流中的换行位置，不复制正文。source_hash
    # 与当前原文一致时同时核验位置上界；原文更新后的旧记录允许保留但渲染层会停用，
    # 这样作者保存方案时不会悄悄丢掉曾做过的校样。
    proof_breaks = raw.get("proof_breaks")
    if proof_breaks is None:
        proof_breaks = {}
    if not isinstance(proof_breaks, dict):
        raise ValueError("proof_breaks 必须是对象")
    if len(proof_breaks) > len(ordered):
        raise ValueError("校样分行只能用于已入集作品")
    clean_proof_breaks = {}
    for poem_id, record in proof_breaks.items():
        if poem_id not in seen:
            raise ValueError("校样分行只能用于已入集作品")
        if not isinstance(record, dict):
            raise ValueError("校样分行记录必须是对象")
        source_hash = record.get("source_hash")
        positions = record.get("positions")
        if not isinstance(source_hash, str) or not source_hash or len(source_hash) > 160:
            raise ValueError("校样分行缺少有效 source_hash")
        if (not isinstance(positions, list) or len(positions) > 10000
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or value < 0 or value > 200000 for value in positions)
                or positions != sorted(positions)):
            raise ValueError("校样分行位置必须是递增的非负整数数组")
        clean_proof_breaks[poem_id] = {
            "source_hash": source_hash,
            "positions": positions,
        }

    versions = raw.get("versions")
    if versions is None:
        versions = {}
    if not isinstance(versions, dict):
        raise ValueError("versions 必须是对象")
    if len(versions) > len(ordered):
        raise ValueError("出版版本数量超过入集作品数")
    clean_versions = {}
    for poem_id, record in versions.items():
        if poem_id not in seen:
            raise ValueError("出版版本只能用于已入集作品")
        if not isinstance(record, dict):
            raise ValueError("出版版本记录必须是对象")
        source_hash = record.get("source_hash")
        if not isinstance(source_hash, str) or not source_hash or len(source_hash) > 160:
            raise ValueError("出版版本缺少有效 source_hash")
        content = str(record.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
        if not content.strip():
            raise ValueError("出版版本正文不能为空；撤销改字请删除该记录")
        if len(content) > 20000:
            raise ValueError("出版版本正文过长（上限 20000 字）")
        clean_versions[poem_id] = {"source_hash": source_hash, "content": content}

    # Opt-in copy editing: each text node owns one stable identity. Its text is
    # concatenated verbatim; there is no second writable version for that poem.
    content_edit_copy = raw.get("content_edit_copy", False)
    if not isinstance(content_edit_copy, bool):
        raise ValueError("content_edit_copy 必须是布尔值")
    body_nodes = raw.get("body_nodes", {})
    if not isinstance(body_nodes, dict) or (body_nodes and not content_edit_copy):
        raise ValueError("分段正文只允许在显式编稿副本中保存")
    if len(body_nodes) > len(ordered):
        raise ValueError("分段正文只能用于已入集作品")
    clean_body_nodes = {}
    for poem_id, record in body_nodes.items():
        if poem_id not in seen or not isinstance(record, dict):
            raise ValueError("分段正文引用了无效作品")
        if poem_id in clean_versions or poem_id in clean_proof_breaks:
            raise ValueError("同一首诗不能同时保存分段正文与旧式改稿/分行")
        source_hash = record.get("source_hash")
        if not isinstance(source_hash, str) or not source_hash or len(source_hash) > 160:
            raise ValueError("分段正文缺少源稿指纹")
        nodes = record.get("nodes")
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 500:
            raise ValueError("分段正文节点数量无效")
        clean_nodes, node_ids = [], set()
        for node in nodes:
            if not isinstance(node, dict) or node.get("kind") != "text":
                raise ValueError("当前分段正文只支持文字节点")
            node_id, text = node.get("id"), node.get("text")
            if (not isinstance(node_id, str) or not re.fullmatch(r"block-[a-z0-9-]{6,48}", node_id)
                    or node_id in node_ids or not isinstance(text, str) or "\r" in text):
                raise ValueError("分段正文节点 ID 或文字无效")
            node_ids.add(node_id)
            clean_nodes.append({"id": node_id, "kind": "text", "text": text})
        text = "".join(node["text"] for node in clean_nodes)
        if not text.strip() or len(text) > 20000:
            raise ValueError("分段正文不能为空或超过 20000 字")
        clean_body_nodes[poem_id] = {"source_hash": source_hash, "nodes": clean_nodes}

    # 断点与前端一样作用于生效文本；过期记录保留但不应用。
    for poem_id, record in clean_proof_breaks.items():
        poem = corpus_by_id[poem_id]
        version = clean_versions.get(poem_id)
        content = (version["content"] if version and version["source_hash"] == poem.get("content_hash")
                   else str(poem.get("content") or ""))
        plain = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "")
        text_hash = proof_breaks[poem_id].get("text_hash")
        if text_hash is not None:
            if not isinstance(text_hash, str) or not re.fullmatch(r"[0-9a-f]{8}", text_hash):
                raise ValueError("校样分行 text_hash 无效")
            record["text_hash"] = text_hash
        if record["source_hash"] == poem.get("content_hash") and (
                text_hash is None or text_hash == _book_text_hash(plain)):
            if any(value > len(plain) for value in record["positions"]):
                raise ValueError("校样分行位置超出当前原文或生效出版文本")

    appendices = raw.get("appendices") or {}
    if not isinstance(appendices, dict):
        raise ValueError("appendices 必须是对象")
    clean_appendices = {}
    for key in BOOK_APPENDIX_KEYS:
        value = appendices.get(key, False)
        if not isinstance(value, bool):
            raise ValueError(f"appendices.{key} 必须是布尔值")
        clean_appendices[key] = value

    pages = raw.get("pages")
    if pages is None:
        pages = []
    if not isinstance(pages, list):
        raise ValueError("pages 必须是数组")
    if len(pages) > 200:
        raise ValueError("插入页过多（上限 200 页）")
    clean_pages = []
    page_ids = set()
    for item in pages:
        if not isinstance(item, dict):
            raise ValueError("插入页必须是对象")
        page_id = str(item.get("id") or "").strip()
        if not page_id or len(page_id) > 64 or page_id in page_ids:
            raise ValueError("插入页 id 无效或重复")
        kind = item.get("kind", "prose")
        if kind not in BOOK_PAGE_KINDS:
            raise ValueError("插入页款式只能是文字页、诗页、空白页或图片页")
        page_title = str(item.get("title") or "").strip()[:60]
        body = str(item.get("body") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(body) > 20000:
            raise ValueError("插入页正文过长（上限 20000 字）")
        placement = str(item.get("placement") or "front")
        if placement not in ("front", "back") and not (
                placement.startswith("before:") and placement[7:] in seen):
            raise ValueError("插入页位置无效：只能是 front、back 或已入集作品前")
        toc = item.get("toc", kind != "image")
        if not isinstance(toc, bool):
            raise ValueError("插入页进目录必须是布尔值")
        image_id = ""
        image_layout = None
        if kind == "image":
            # 图片页必须引用已上传的插图；图注进 title，正文不适用。
            image_id = str(item.get("image_id") or "")
            if not re.fullmatch(r"[0-9a-f]{16}", image_id) or not _book_image_exists(image_id):
                raise ValueError("图片页必须引用已上传的图片")
            layout_raw = item.get("image_layout") or {}
            if not isinstance(layout_raw, dict):
                raise ValueError("image_layout 必须是对象")
            width_pct = layout_raw.get("width_pct", 100)
            align = layout_raw.get("align", "center")
            fit = layout_raw.get("fit", "contain")
            focal_x = layout_raw.get("focal_x", 50)
            focal_y = layout_raw.get("focal_y", 50)
            if isinstance(width_pct, bool) or width_pct not in BOOK_IMAGE_WIDTHS:
                raise ValueError("图片宽度只能是 40/60/80/100%")
            if align not in BOOK_IMAGE_ALIGNS:
                raise ValueError("图片对齐方式无效")
            if fit not in BOOK_IMAGE_FITS:
                raise ValueError("图片适应方式无效")
            if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100
                   for value in (focal_x, focal_y)):
                raise ValueError("图片焦点必须在 0–100 之间")
            image_layout = {"width_pct": width_pct, "align": align, "fit": fit,
                            "focal_x": focal_x, "focal_y": focal_y}
            body = ""
        if kind == "blank":
            page_title, body, toc = "", "", False
        page_ids.add(page_id)
        entry = {"id": page_id, "kind": kind, "title": page_title,
                 "body": body, "placement": placement, "toc": toc}
        if kind == "image":
            entry["image_id"] = image_id
            entry["image_layout"] = image_layout
        clean_pages.append(entry)

    tailpieces = raw.get("tailpieces")
    if tailpieces is None:
        tailpieces = {}
    if not isinstance(tailpieces, dict):
        raise ValueError("tailpieces 必须是对象")
    clean_tailpieces = {}
    for poem_id, img_id in tailpieces.items():
        if poem_id not in seen:
            continue
        img_id_str = str(img_id or "").strip()
        if img_id_str:
            if not re.fullmatch(r"[0-9a-f]{16}", img_id_str) or not _book_image_exists(img_id_str):
                raise ValueError("尾花插图必须引用已上传的图片")
            clean_tailpieces[poem_id] = img_id_str

    tailpiece_layouts = raw.get("tailpiece_layouts") or {}
    if not isinstance(tailpiece_layouts, dict):
        raise ValueError("tailpiece_layouts 必须是对象")
    clean_tailpiece_layouts = {}
    for poem_id, layout_raw in tailpiece_layouts.items():
        if poem_id not in clean_tailpieces:
            continue
        if not isinstance(layout_raw, dict):
            raise ValueError("尾花版式必须是对象")
        size = layout_raw.get("size")
        if size is None:
            # 兼容 2026-09-22 尚未公开的宽度试验字段，不要求作者迁移。
            size = {20: "small", 32: "standard", 45: "large", 48: "large"}.get(
                layout_raw.get("width_pct"), "standard")
        align = layout_raw.get("align", "center")
        if size not in BOOK_TAILPIECE_PRESETS:
            raise ValueError("诗末装饰图大小只能是 small/standard/large")
        if align not in BOOK_IMAGE_ALIGNS:
            raise ValueError("诗末装饰图对齐方式无效")
        clean_tailpiece_layouts[poem_id] = {"size": size, "align": align}

    now = now_iso()
    result = {
        "id": book_id,
        "title": title,
        "subtitle": str(raw.get("subtitle") or "").strip()[:240],
        "author": str(raw.get("author") or "").strip()[:120],
        "poem_ids": ordered,
        "sections": clean_sections,
        "proof_breaks": clean_proof_breaks,
        "versions": clean_versions,
        "content_edit_copy": content_edit_copy,
        "body_nodes": clean_body_nodes,
        "pages": clean_pages,
        "tailpieces": clean_tailpieces,
        "tailpiece_layouts": clean_tailpiece_layouts,
        "front_matter": {"title_page": title_page, "colophon": colophon, "dedication": dedication},
        "sort_mode": sort_mode,
        "layout": {
            "page_size": page_size,
            "start_each_poem": start_each_poem,
            "running_head": running_head,
            "folio": folio,
            "section_start": section_start,
            "date_position": date_position,
            "interior_color": interior_color,
            "block_align": block_align,
            "running_head_content": running_head_content,
            "show_numbering": show_numbering,
            "continue_hint": continue_hint,
            "profile_id": profile_id or BOOK_DEFAULT_PROFILE_ID,
            "profile_overrides": clean_overrides,
        },
        # 附录开关先进 manifest，界面与渲染层后续逐项实现；默认全关，不挤诗正文。
        "appendices": clean_appendices,
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
        "archived_at": previous.get("archived_at"),
        # revision 由 update_book_projects 在 CAS 校验后递增；这里先将它
        # 声明为已知字段，避免未经校验的客户端值被未知键保留逻辑带回。
        "revision": 0,
    }
    # 扩展字段随同一稳定记录往返；数组/映射的成员删除仍由作者输入决定。
    for key in ("layout", "front_matter", "appendices"):
        result[key] = _book_extensions(result[key], raw.get(key), previous.get(key))
    for key in ("sections", "pages"):
        incoming = section_sources if key == "sections" else {item.get("id"): item for item in raw.get(key, []) or []}
        prior = {item.get("id"): item for item in previous.get(key, []) or []
                 if isinstance(item, dict)}
        result[key] = [_book_extensions(item, incoming.get(item["id"]), prior.get(item["id"]),
                                        reserved=("image_id", "imageId") if key == "pages" else ())
                       for item in result[key]]
    for key in ("versions", "proof_breaks"):
        incoming = raw.get(key) or {}
        prior = previous.get(key) or {}
        result[key] = {pid: _book_extensions(item, incoming.get(pid), prior.get(pid),
                                            reserved=("text_hash",) if key == "proof_breaks" else ())
                       for pid, item in result[key].items()}
    return _book_extensions(result, raw, previous)


def _clean_order_book_project(raw, previous=None):
    """Validate one canonical order without persisting legacy placement fields.

    The legacy projection borrows field checks only; returned data contains a
    single order. Existing schema-2 sidecars remain read-only until migrated.
    """
    known = {poem["id"] for poem in load_corpus()}
    book_order.validate(raw, known)
    prior_projection = (book_order.legacy_validation_projection(previous)
                        if isinstance(previous, dict) and "order" in previous else previous)
    projected = book_order.legacy_validation_projection(raw)
    cleaned = _clean_book_project(projected, prior_projection)
    result = book_order.from_validated_projection(raw["order"], cleaned)
    book_order.validate(result, known)
    return result


def _update_book_projects_locked(payload):
    """方案写入入口：save / archive / restore。archive 只打标记，不删记录。"""
    action = payload.get("action", "save")
    data = load_book_projects(strict=True)
    books = data["books"]
    if action == "save":
        raw = payload.get("book")
        raw_id = (raw or {}).get("id") if isinstance(raw, dict) else None
        previous = next((b for b in books if b.get("id") == raw_id), None)
        current_revision = previous.get("revision", 0) if previous else 0
        if (isinstance(current_revision, bool) or not isinstance(current_revision, int)
                or current_revision < 0):
            current_revision = 0
        expected_revision = payload.get("expected_revision")
        if expected_revision is not None:
            if (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
                    or expected_revision < 0):
                raise ValueError("expected_revision 必须是非负整数")
            if expected_revision != current_revision:
                raise BookRevisionConflict(current_revision)
        clean = _clean_order_book_project(raw, previous)
        clean["revision"] = current_revision + 1
        if previous:
            books[books.index(previous)] = clean
        else:
            if any(b.get("id") == clean["id"] for b in books):
                raise ValueError("诗集方案 ID 重复")
            books.append(clean)
        _write_book_projects(data)
        return {"book": clean, "book_projects": data}

    if action not in ("archive", "restore"):
        raise ValueError("诗集方案动作无效")
    book_id = str(payload.get("id") or "")
    book = next((b for b in books if b.get("id") == book_id), None)
    if book is None:
        raise ValueError("找不到诗集方案")
    current_revision = book.get("revision", 0)
    if isinstance(current_revision, bool) or not isinstance(current_revision, int) or current_revision < 0:
        current_revision = 0
    expected_revision = payload.get("expected_revision")
    if expected_revision is not None:
        if (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
                or expected_revision < 0):
            raise ValueError("expected_revision 必须是非负整数")
        if expected_revision != current_revision:
            raise BookRevisionConflict(current_revision)
    book["archived_at"] = now_iso() if action == "archive" else None
    book["updated_at"] = now_iso()
    book["revision"] = current_revision + 1
    _write_book_projects(data)
    return {"book": book, "book_projects": data}


def update_book_projects(payload):
    """将读取当前修订、CAS 校验和原子替换放在同一进程锁内。"""
    with BOOK_PROJECTS_LOCK:
        return _update_book_projects_locked(payload)


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
        "book_projects": load_book_projects(),
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


def _mobile_pwa_parts():
    """返回公开移动壳的规范 URL 与 origin；配置错误时宁可关闭该入口。"""
    try:
        parsed = urllib.parse.urlsplit(MOBILE_PWA_URL)
    except (TypeError, ValueError):
        return None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        return None
    path = parsed.path or "/mobile.html"
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return {"url": url, "origin": origin}


def _is_lan_mobile_endpoint(value, port=None):
    """公开 PWA 只可被配对到明确的 RFC1918 局域网 HTTP 入口。"""
    try:
        parsed = urllib.parse.urlsplit(value)
        ip = ipaddress.ip_address(parsed.hostname or "")
        parsed_port = parsed.port
    except (TypeError, ValueError):
        return False
    private = (ip in ipaddress.ip_network("10.0.0.0/8")
               or ip in ipaddress.ip_network("172.16.0.0/12")
               or ip in ipaddress.ip_network("192.168.0.0/16"))
    return (private and parsed.scheme == "http" and parsed.username is None
            and parsed.password is None and parsed.path in {"", "/"}
            and not parsed.query and not parsed.fragment
            and (port is None or parsed_port == port))


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

    def _pwa_origin(self):
        origin = self.headers.get("Origin", "")
        allowed = getattr(self.server, "mobile_pwa_origin", "")
        return origin if origin and allowed and hmac.compare_digest(origin, allowed) else ""

    def _cors_headers(self):
        origin = self._pwa_origin()
        if not origin:
            return {}
        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "X-ZQ-Mobile-Token, If-None-Match",
            "Access-Control-Expose-Headers": "ETag",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
            "Cross-Origin-Resource-Policy": "cross-origin",
        }
        # 兼容仍发送旧 PNA 预检头的 Chromium 版本；新 LNA 权限不会依赖它。
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            headers["Access-Control-Allow-Private-Network"] = "true"
        return headers

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
        merged = {**self._cors_headers(), **(headers or {})}
        self.send_header("Cross-Origin-Resource-Policy",
                         merged.pop("Cross-Origin-Resource-Policy", "same-origin"))
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        for key, value in merged.items():
            self.send_header(key, value)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _authorized(self, renew=False):
        supplied = self.headers.get("X-ZQ-Mobile-Token", "")
        access = getattr(self.server, "mobile_access", None)
        if access:
            return access.authorize(supplied, renew=renew)
        return bool(supplied) and hmac.compare_digest(supplied, self.server.mobile_token)

    def _query_pair(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query,
                                      keep_blank_values=True)
        values = query.get("pair") or []
        supplied = values[0] if len(values) == 1 else ""
        return supplied if supplied and hmac.compare_digest(
            supplied, self.server.mobile_token) else ""

    def do_OPTIONS(self):
        path = urllib.parse.urlsplit(self.path).path
        if path not in {"/api/mobile-state", "/api/wordcloud", "/api/word-context"}:
            return self._send(404, {"error": "not found"})
        if not self._pwa_origin():
            return self._send(403, {"error": "移动应用来源未获允许"})
        requested = {item.strip().lower() for item in
                     self.headers.get("Access-Control-Request-Headers", "").split(",")
                     if item.strip()}
        if not requested.issubset({"x-zq-mobile-token", "if-none-match"}):
            return self._send(403, {"error": "移动应用请求头未获允许"})
        return self._send(204, b"")

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/mobile-state":
            if not self._authorized(renew=True):
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
        self.last_sync_at = None

    @staticmethod
    def _load_trust(allow_expired=False):
        if not MOBILE_TRUST.exists():
            return None
        try:
            data = json.loads(MOBILE_TRUST.read_text(encoding="utf-8"))
            token = data.get("token")
            port = data.get("port")
            expires_at = float(data.get("expires_at", 0))
            last_sync_at = data.get("last_sync_at")
            if last_sync_at is not None:
                last_sync_at = float(last_sync_at)
            if (data.get("schema") != 1 or not isinstance(token, str) or len(token) < 24
                    or not isinstance(port, int) or not 1024 <= port <= 65535
                    or expires_at <= 0 or (expires_at <= time.time() and not allow_expired)):
                return None
            return {"token": token, "port": port, "expires_at": expires_at,
                    "last_sync_at": last_sync_at}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _save_trust(token, port, expires_at, last_sync_at=None):
        MOBILE_TRUST.parent.mkdir(parents=True, exist_ok=True)
        tmp = MOBILE_TRUST.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "schema": 1,
            "token": token,
            "port": port,
            "expires_at": expires_at,
            "last_sync_at": last_sync_at,
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
            # 自动恢复只接受仍有效的签；作者在桌面明确重新开启时，可以延续已经
            # 过期的同一张签，从而让已安装手机恢复同步而无需重新扫码。
            saved = _saved or (self._load_trust(allow_expired=True) if trusted else None)
            token = saved["token"] if saved else secrets.token_urlsafe(24)
            expires_at = saved["expires_at"] if saved else None
            if trusted and (not expires_at or expires_at <= time.time()):
                expires_at = time.time() + MOBILE_TRUST_SECONDS
            try:
                server = MobileHTTPServer(("0.0.0.0", port), MobileHandler)
            except OSError as exc:
                raise ValueError(f"端口 {port} 无法开启：{exc}") from exc
            server.mobile_token = token
            server.mobile_access = self
            parts = _mobile_pwa_parts()
            server.mobile_pwa_origin = parts["origin"] if parts else ""
            thread = threading.Thread(target=server.serve_forever,
                                      name="zhouqingji-mobile", daemon=True)
            self.server, self.thread, self.token, self.port = server, thread, token, port
            self.trusted, self.trust_expires_at = trusted, expires_at
            self.last_sync_at = saved.get("last_sync_at") if saved else None
            if trusted:
                self._save_trust(token, port, expires_at, self.last_sync_at)
            thread.start()
            return self.status()

    def stop(self, revoke=False):
        if not isinstance(revoke, bool):
            raise ValueError("撤销可信入口选项格式无效")
        with self._lock:
            server, thread = self.server, self.thread
            self.server = self.thread = self.token = self.port = None
            self.trusted, self.trust_expires_at, self.last_sync_at = False, None, None
            if revoke:
                self._delete_trust()
        if server:
            server.shutdown()
            server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        return self.status()

    def renew(self):
        """作者从桌面延长现有可信签；不换 token，手机无需重新扫码。"""
        with self._lock:
            if not self.server or not self.trusted or not self.token:
                raise ValueError("当前没有可续期的可信手机入口")
            self.trust_expires_at = time.time() + MOBILE_TRUST_SECONDS
            self._save_trust(self.token, self.port, self.trust_expires_at,
                             self.last_sync_at)
        return self.status()

    def authorize(self, supplied, renew=False):
        """校验正在运行的签，并在真实快照同步临近到期时低频续期。"""
        if not supplied:
            return False
        with self._lock:
            if (not self.server or not self.token
                    or not hmac.compare_digest(supplied, self.token)):
                return False
            now = time.time()
            if self.trusted and (not self.trust_expires_at
                                 or self.trust_expires_at <= now):
                return False
            if renew:
                previous_sync = self.last_sync_at or 0
                should_record = now - previous_sync >= MOBILE_SYNC_RECORD_INTERVAL
                should_extend = (self.trusted and self.trust_expires_at - now
                                 <= MOBILE_TRUST_RENEW_WINDOW)
                if should_record:
                    self.last_sync_at = now
                if should_extend:
                    self.trust_expires_at = now + MOBILE_TRUST_SECONDS
                if self.trusted and (should_record or should_extend):
                    self._save_trust(self.token, self.port, self.trust_expires_at,
                                     self.last_sync_at)
            return True

    def status(self):
        with self._lock:
            running, port, token = bool(self.server), self.port, self.token
            trusted, expires_at = self.trusted, self.trust_expires_at
            last_sync_at = self.last_sync_at
        expired = bool(trusted and expires_at and expires_at <= time.time())
        urls = []
        if running:
            urls = [f"http://{ip}:{port}/?pair={token}" for ip in _local_ipv4s()]
        pwa_urls = [self.pwa_pair_url(url) for url in urls]
        pwa_urls = [url for url in pwa_urls if url]
        pwa = _mobile_pwa_parts()
        return {"running": running, "port": port, "urls": urls,
                "pwa_urls": pwa_urls, "pwa_shell_url": pwa["url"] if pwa else None,
                "token": token if running else None, "trusted": trusted,
                "trust_expired": expired,
                "trust_expires_at": (time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                     time.localtime(expires_at))
                                     if trusted and expires_at else None),
                "last_sync_at": (time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                 time.localtime(last_sync_at))
                                 if last_sync_at else None)}

    def pwa_pair_url(self, local_url):
        """生成只在 URL fragment 携带口令的 Android 离线壳配对地址。"""
        with self._lock:
            token, port = (self.token, self.port) if self.server else (None, None)
        pwa = _mobile_pwa_parts()
        if not token or not pwa:
            return None
        try:
            parsed = urllib.parse.urlsplit(local_url)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            supplied = (query.get("pair") or [""])[0]
            endpoint = urllib.parse.urlunsplit(parsed._replace(
                path="/", query="", fragment=""))
        except (TypeError, ValueError):
            return None
        if (not supplied or not hmac.compare_digest(supplied, token)
                or not _is_lan_mobile_endpoint(endpoint, port)):
            return None
        fragment = urllib.parse.urlencode({"pair": token, "endpoint": endpoint})
        return pwa["url"] + "#" + fragment

    def valid_pair_url(self, value):
        """只为本轮有效配对地址生成二维码，兼容 Tailscale Serve 的 HTTPS 域名。"""
        with self._lock:
            token = self.token if self.server else None
        if not token or not isinstance(value, str) or len(value) > 2048:
            return False
        try:
            parsed = urllib.parse.urlsplit(value)
            pwa = _mobile_pwa_parts()
            if (pwa and urllib.parse.urlunsplit(parsed._replace(
                    query="", fragment="")) == pwa["url"]):
                fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)
                pair = fragment.get("pair") or []
                endpoint = fragment.get("endpoint") or []
                return (not parsed.query and len(pair) == 1 and len(endpoint) == 1
                        and hmac.compare_digest(pair[0], token)
                        and _is_lan_mobile_endpoint(endpoint[0], self.port))
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


def book_pdf_module():
    tool = ROOT / "theater" / "tools" / "book_pdf.py"
    spec = importlib.util.spec_from_file_location("zhouqingji_book_pdf", tool)
    if spec is None or spec.loader is None:
        raise ValueError("专业 PDF 工具不完整，请重新更新昼青集")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def book_pdf_dependency_status():
    """只读体检可选 PDF 环境；不导入诗稿、不安装依赖、不启动浏览器。"""
    return book_pdf_module().dependency_status()


def build_book_pdf_bytes(html, title="诗集"):
    """在系统临时目录原子生成并核验 PDF；不把私人 HTML/PDF 写入仓库。"""
    if not isinstance(html, str) or not html.strip():
        raise ValueError("缺少离线排版内容")
    encoded = html.encode("utf-8")
    if len(encoded) > 24 * 1024 * 1024:
        raise ValueError("诗集排版文件超过 24 MB，请改用离线 HTML 专业输出")
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(title or "诗集")).strip(" .")[:80] or "诗集"
    module = book_pdf_module()
    with tempfile.TemporaryDirectory(prefix="zq-book-pdf-") as folder:
        work = Path(folder)
        source = work / f"{safe_title}-离线排版.html"
        output = work / f"{safe_title}-专业阅读.pdf"
        source.write_bytes(encoded)
        qa = module.build_pdf(source, output, None, False, 300)
        return output.read_bytes(), qa


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
        if path == "/api/book-pdf/status":
            try:
                return self._send(200, book_pdf_dependency_status())
            except (OSError, ValueError, RuntimeError) as exc:
                return self._send(500, {"error": str(exc)})
        if path == "/api/mobile/qr":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            text = (query.get("text") or [""])[0]
            if not text and query.get("base"):
                text = MOBILE_ACCESS.private_pair_url(query["base"][0]) or ""
            if not MOBILE_ACCESS.valid_pair_url(text):
                return self._send(400, {"error": "二维码地址无效或手机入口已关闭"})
            return self._send(200, qr_svg(text), "image/svg+xml")
        if path == "/api/books/clean-images":
            return self._send(200, clean_orphan_book_images(dry_run=True))
        if path == "/api/books/images":
            return self._send(200, {"images": list_book_images()})
        if path == "/api/runtime":
            return self._send(200, {"app": "zhouqingji", "version": app_version(),
                                    "author_api_level": AUTHOR_API_LEVEL,
                                    "build_id": AUTHOR_BUILD_ID})
        if path == "/api/state":
            return self._send(200, build_author_state())
        if path.startswith("/api/books/image/"):
            try:
                entry, mime = load_book_image(path.rsplit("/", 1)[-1])
                return self._send(200, entry.read_bytes(), mime)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            except OSError as exc:
                return self._send(500, {"error": f"读取图片失败：{exc}"})
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
        if length > API_MAX_BODY:
            return self._send(413, {"error": "请求内容过大"})
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
            if self.path == "/api/mobile/renew":
                return self._send(200, MOBILE_ACCESS.renew())
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
            if self.path == "/api/book-projects":
                result = update_book_projects(payload)
                return self._send(200, {"ok": True, **result})
            if self.path == "/api/books/image":
                return self._send(200, save_book_image(payload))
            if self.path == "/api/books/clean-images":
                dry_run = bool(payload.get("dry_run", False))
                return self._send(200, clean_orphan_book_images(dry_run=dry_run))
            if self.path == "/api/book-pdf/build":
                pdf, qa = build_book_pdf_bytes(payload.get("html"), payload.get("title"))
                return self._send(200, pdf, "application/pdf", headers={
                    "X-ZQ-PDF-Pages": str(qa.get("pages", "")),
                    "X-ZQ-PDF-Verified": "true",
                })
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
        except BookRevisionConflict as e:
            return self._send(409, {"error": str(e), "code": "book_revision_conflict",
                                    "current_revision": e.current_revision})
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})


class AuthorHTTPServer(ThreadingHTTPServer):
    """桌面作者服务严格单实例，避免 Windows 把同端口请求分给新旧两个进程。"""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        # Windows 的 SO_REUSEADDR 允许后来进程抢占已监听端口，语义不同于 Unix。
        # 显式独占后，旧版仍运行时新版会立即失败并给出可理解提示。
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        return super().server_bind()


if __name__ == "__main__":
    st = load_settings()
    print(f"{st['site_title']}·{st['site_subtitle']}  →  http://localhost:{st['port']}")
    try:
        restored = MOBILE_ACCESS.restore_trusted()
        if restored:
            print(f"可信手机入口已恢复（有效至 {restored['trust_expires_at']}）")
    except (OSError, ValueError) as exc:
        print(f"可信手机入口未能自动恢复：{exc}")
    try:
        server = AuthorHTTPServer(("127.0.0.1", st["port"]), Handler)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or getattr(exc, "errno", None) in {48, 98, 10048}:
            print(f"无法启动：端口 {st['port']} 已被占用。请关闭旧的昼青集窗口后再打开。")
            raise SystemExit(2) from None
        raise
    server.serve_forever()
