# -*- coding: utf-8 -*-
"""词云数据计算（服务端实时算，供 server.py 的 /api/wordcloud 调用）。

两套：
  compute_poem_cloud(poems)   —— 源=公开诗正文；共现=两词同现一首诗；例句=含该词的凝练诗行。
  compute_reason_cloud(votes) —— 源=非作废、非顺势票的投票理由 reason；每条 reason 当一短文档。

产出同一 schema，直接喂前端词云引擎：
  {"meta": {...}, "words": [{"w": 词, "c": 频次, "ex": 例句, "p": [[伙伴词, 共现次], ...]}, ...]}

分词用 vendored jieba（theater/vendor/jieba，MIT，纯 Python，仅保留 cut 所需的
dict.txt + finalseg）。首次调用惰性加载词典（~0.5s，之后走 jieba 自身缓存）。
语料/票据没变时由 server.py 按 mtime 缓存整份结果，不重复计算。"""
import os
import re
import sys
import warnings
import collections

# vendored jieba：theater/vendor 相对本文件 = ../../vendor
_VENDOR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vendor"))
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
with warnings.catch_warnings():
    # jieba 0.42.1 内有两处 Py3.12 弃用的正则转义（\. \d）——只是 warning、不影响功能，
    # 不去改 vendored 源码（便于日后原样更新 jieba），在导入处静音即可。
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba  # noqa: E402  （置于 sys.path 注入之后）

TOPN = 120        # 参与词云的词数
PARTNERS = 5      # 每词保留的最强共现伙伴数

# 中文虚词/填充词停用表（诗、评通用基础层）
_CN_STOP_BASE = (
    '的 了 在 是 我 你 他 她 它 们 和 与 也 就 都 而 及 或 一个 这 那 之 于 '
    '着 过 又 没 不 要 会 能 可 把 被 让 给 但 却 还 只 很 太 更 最 已 '
    '将 从 向 往 里 中 上 下 前 后 内 外 时 时候 因为 所以 如果 虽然 '
    '这个 那个 这样 那样 什么 怎么 为了 一样 一种 一些 这些 那些 自己 '
    '我们 你们 他们 她们 它们 起来 出来 下去 一直 已经 还是 不是 就是 '
    '这里 那里 现在 然后 那么 这么 一切 每个 有些 那种 一个个 '
    '没有 即便 知道 依旧 可能 可以 无法 好像 只是 是否 不知 不到 还有 '
    '于是 似乎 的话 不能 最后 告诉 全部 不会 一点 一些 有点 一下 而已 '
    '之后 之前 之间 尽管 竟然 果然 究竟 其实 也许 大概 是的 不过 或许 '
    '总是 曾经 从来 永远 依然 仿佛 犹如 若是 假如 哪怕 无论 只要 只有 '
    '这种 一场 一首 如此 不要 已然 所有 东西 成为 不可 一定 到来 过去 '
    '明明 不再 这样 那样 一种 一个 那个 这个 一切 不得 不用 不管 一样'
)
CN_STOP = set(_CN_STOP_BASE.split())

# 评论区额外停用：主观口头禅 + 投票机制/方法论泄漏词 + 位置指代填充
CN_STOP_REASON = CN_STOP | set(
    ('这首 这句 那句 一句 这诗 首诗 有点 有些 觉得 感觉 认为 应该 '
     '不太 比较 尤其 特别 非常 真的 确实 部分 地方 '
     '背书 换首 换掉 换成 对不上 咬住 咬合 加精 这批 一批 相比 换首诗 '
     '本身 前面 后面 具体 真正 独有 诗里 本诗 整首 诗中 全诗 通篇 原诗 '
     '此诗 该诗 这首诗 作者 现代 成立 存在 需要 显得 有种 有一种 更像').split()
)

EN_STOP = set(
    ('the a an and or of to in on at is are was were be been being it its '
     'this that these those you your yours i me my we our they them he she '
     'his her him for with as but not no so if then than too very can will '
     'would could should have has had do does did just from into out up down '
     'all each some any mine ours over under about like when where what how '
     'who whom which there here now').split()
)

_CJK = re.compile(r'[一-鿿]+')
_EN = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")


def _words_of(text, cn_stop, want_en=True):
    """一段文本 → 词列表（中文 jieba 切、滤停用与单字；英文可选、滤停用与短词）。"""
    ws = []
    if want_en:
        for w in _EN.findall(text):
            wl = w.lower().strip("'-")
            if len(wl) >= 3 and wl not in EN_STOP:
                ws.append(wl)
    for seg in _CJK.findall(text):
        for w in jieba.cut(seg):
            if len(w) >= 2 and w not in cn_stop:
                ws.append(w)
    return ws


def _cooccur(docsets, topset):
    """在 top 词之间数同文档共现。docsets: [set(词), ...]。返回 {词: [(伙伴, 次), ...]}。"""
    co = collections.Counter()
    for s in docsets:
        present = [w for w in s if w in topset]
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                a, b = sorted((present[i], present[j]))
                co[(a, b)] += 1
    partners = collections.defaultdict(list)
    for (a, b), c in co.items():
        partners[a].append((b, c))
        partners[b].append((a, c))
    return {w: sorted(partners[w], key=lambda x: -x[1])[:PARTNERS] for w in topset}


def _clean_line(ln):
    return re.sub(r'\s+', ' ', ln).strip(' 　,，。.、；;：:!！?？—-')


def _build(freq, docsets, examples_src, ex_len_lo, ex_len_hi, ex_ideal, meta):
    """共同收尾：取 top、算共现、挑例句、组 schema。
    examples_src: 可迭代的候选例句串（诗行 / reason）。"""
    top = [w for w, _ in freq.most_common(TOPN)]
    topset = set(top)
    partners = _cooccur(docsets, topset)

    best = {}  # 词 -> (score, 例句)  score 越小越好（越接近理想长度）
    if top:
        pat = re.compile('|'.join(map(re.escape, top)))
        for line in examples_src:
            L = len(line)
            if L < ex_len_lo or L > ex_len_hi:
                continue
            score = abs(L - ex_ideal)
            for w in set(pat.findall(line)):
                cur = best.get(w)
                if cur is None or score < cur[0]:
                    best[w] = (score, line)

    words = [{
        'w': w,
        'c': freq[w],
        'ex': best.get(w, (0, ''))[1],
        'p': [[pw, pc] for pw, pc in partners.get(w, [])],
    } for w in top]
    meta = dict(meta, vocab=len(freq), topn=len(top))
    return {'meta': meta, 'words': words}


def compute_poem_cloud(poems):
    """poems: 已筛好的公开诗 dict 列表（需含 content）。"""
    freq = collections.Counter()
    docsets = []
    lines = []
    for p in poems:
        content = p.get('content') or ''
        ws = _words_of(content, CN_STOP, want_en=True)
        freq.update(ws)
        docsets.append(set(ws))
        for raw in re.split(r'[\n\r]+', content):
            ln = _clean_line(raw)
            if 4 <= len(ln) <= 30:
                lines.append(ln)
    return _build(freq, docsets, lines, 4, 30, 14, {'poems': len(poems)})


def compute_reason_cloud(votes):
    """votes: 可迭代的投票 dict（已排作废票即可；此处再排顺势票、取非空 reason）。"""
    reasons = []
    for v in votes:
        if v.get('source') == 'piggyback':
            continue
        r = (v.get('reason') or '').strip()
        if r:
            reasons.append(r)
    freq = collections.Counter()
    docsets = []
    for r in reasons:
        ws = _words_of(r, CN_STOP_REASON, want_en=False)
        freq.update(ws)
        docsets.append(set(ws))
    return _build(freq, docsets, reasons, 6, 40, 16, {'reasons': len(reasons)})
