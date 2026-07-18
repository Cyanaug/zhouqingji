# -*- coding: utf-8 -*-
"""昼青集·读诗剧场 本地服务器（纯标准库，零依赖）。

职责边界（README 硬边界的机器侧执行）：
- 读 corpus，读 results；
- 写 corpus 仅限作者在 GUI 里明确触发的动作（切可见性/剪自注/背景小注/诠释升格），
  且每次写前把 诗稿.json 备份到 corpus/.backups/（只进不毁、可回滚）；
- 绝不由代码自动改动任何作品内容。

启动：python theater/src/server.py  →  http://localhost:8737
"""
import hashlib
import json
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus" / "诗稿.json"
INTERP = ROOT / "corpus" / "昼青·诠释.md"
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

# 作者偏好（corpus/settings.json 侧车）：缺文件/缺字段一律回退这里的默认值。
# GUI 设置页与派发 agent 读写同一份文件——所有"可以换成你自己的"都收口在这里。
DEFAULT_SETTINGS = {
    "site_title": "昼青集",
    "site_subtitle": "读诗剧场",
    "footer_text": "由世间所有的所见将它命名。",
    "default_view": "boards",    # boards | readers | timeline | stats | all
    "score_badge": "cal",        # cal = 质分优先；raw = 只看原始均分
    "read_genres": [],           # 诗（现代诗/词/歌词）永远在读者池；其他文体勾选才读
    "genre_notes": {},           # 文体 → 作者补充的评判要求（附进读者 prompt）
    "port": 8737,                # 重启后生效
    "dispatch": {                # 派发 agent 的默认偏好
        "default_model": "claude-haiku-4-5",
        "default_transport": "cc-subagent",
        "target_depth": 4,
    },
}
VIEW_CHOICES = ("boards", "readers", "timeline", "stats", "all")
SETTINGS = ROOT / "corpus" / "settings.json"

MIME = {".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
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


_calib_lock = threading.Lock()


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
    if "port" in payload:
        v = payload["port"]
        if v in (None, ""):
            cur.pop("port", None)
        elif not isinstance(v, int) or not 1024 <= v <= 65535:
            raise ValueError("port 需为 1024–65535 的整数")
        else:
            cur["port"] = v
    if "dispatch" in payload:
        dp = payload["dispatch"]
        if not isinstance(dp, dict):
            raise ValueError("dispatch 必须是对象")
        cd = cur.get("dispatch", {})
        for k in ("default_model", "default_transport"):
            if k in dp:
                if dp[k] is not None and not isinstance(dp[k], str):
                    raise ValueError(f"{k} 必须是字符串")
                put(cd, k, dp[k])
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
    """作者折叠/恢复某条阅读记录（侧车文件，绝不改 reads.jsonl）。"""
    read_id = payload.get("read_id")
    if not read_id or read_id not in {r["read_id"] for r in load_reads()}:
        raise ValueError("找不到该阅读记录")
    cur = load_curation()
    if payload.get("hidden"):
        cur[read_id] = {"hidden": True,
                        "reason": str(payload.get("reason", "")),
                        "ts": now_iso()}
    else:
        cur.pop(read_id, None)
    CURATION.parent.mkdir(parents=True, exist_ok=True)
    CURATION.write_text(json.dumps(cur, ensure_ascii=False, indent=1),
                        encoding="utf-8")


def promote_interpretation(payload):
    """诠释升格：作者亲手把一篇深读追加进 昼青·诠释.md。"""
    read_id = payload.get("read_id")
    reads = {r["read_id"]: r for r in load_reads()}
    r = reads.get(read_id)
    if not r or not r.get("long_form"):
        raise ValueError("找不到该深读")
    poems = {p["id"]: p for p in load_corpus()}
    poem = poems.get(r["poem_id"], {})
    block = (f"\n\n---\n\n## 升格深读 · 《{poem.get('title','?')}》"
             f"（{r['reader']['persona_id']} · {r['reader']['model']} · {r['ts'][:10]}）\n\n"
             f"{r['long_form']}\n")
    with INTERP.open("a", encoding="utf-8") as f:
        f.write(block)


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


def update_check():
    """git fetch 后比对本地与上游——只读，不动工作树。返回落后提交数与远端版本。"""
    rc, _, _ = _git(["rev-parse", "--is-inside-work-tree"], timeout=10)
    if rc != 0:
        return {"ok": False, "error": "这里不是 git 仓库，无法检查更新（直接下载的压缩包没有更新能力）。",
                "local_version": app_version()}
    rc, upstream, _ = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], timeout=10)
    if rc != 0:
        return {"ok": False, "error": "没有配置远端上游分支，无法检查更新。",
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
            "local_version": app_version(), "remote_version": remote_ver}


def update_pull():
    """git pull --ff-only，前置洁净检查：有本地未提交改动或历史分叉一律拒绝，绝不冲突毁数据。"""
    rc, _, _ = _git(["rev-parse", "--is-inside-work-tree"], timeout=10)
    if rc != 0:
        return {"ok": False, "error": "这里不是 git 仓库，无法拉取更新。"}
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 安静

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else \
            json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/state":
            return self._send(200, {
                "poems": load_corpus(),
                "reads": load_reads(),
                "personas": load_personas(),
                # GUI 编辑人设要区分随附/自建/改写，并整份读改侧车
                "personas_defaults": json.loads(PERSONAS.read_text(encoding="utf-8")),
                "personas_sidecar": load_personas_sidecar(),
                "curation": load_curation(),
                "thread_meta": load_thread_meta(),
                "votes": load_vote_tally(),
                "voter_votes": load_voter_votes(),
                "favs": load_favs(),
                "stanzas": load_stanzas(),
                "calibration": load_calibration(),
                "settings": load_settings(),
                "version": app_version(),
            })
        # 静态文件
        if path == "/":
            path = "/index.html"
        f = (WEBAPP / path.lstrip("/")).resolve()
        if WEBAPP.resolve() in f.parents and f.is_file():
            return self._send(200, f.read_bytes(),
                              MIME.get(f.suffix, "application/octet-stream"))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "bad json"})

        try:
            if self.path == "/api/promote":
                promote_interpretation(payload)
                return self._send(200, {"ok": True})
            if self.path == "/api/settings":
                set_settings(payload)
                return self._send(200, {"ok": True, "settings": load_settings()})
            if self.path == "/api/update/check":
                return self._send(200, update_check())
            if self.path == "/api/update/pull":
                return self._send(200, update_pull())
            if self.path == "/api/personas":
                set_personas(payload)
                return self._send(200, {"ok": True, "personas": load_personas()})
            if self.path == "/api/curate":
                curate(payload)
                return self._send(200, {"ok": True})
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
    ThreadingHTTPServer(("127.0.0.1", st["port"]), Handler).serve_forever()
