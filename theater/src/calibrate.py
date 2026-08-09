# -*- coding: utf-8 -*-
"""校准视图：把不同 rater（人设×模型）的原始分换算到统一参考量表。

设计要点（每一环都是对应领域的标准做法，不自创公式）：
- rater 单元 = (persona_id, 规范化后的 model)，**联合**建模——不假设人设效应
  和模型效应可加或可乘，二者的交互天然包含在单元自己的分布里。
- 单元内取经验分位（Hazen mid-rank，处理并列分），再做层级收缩（经验贝叶斯）：
  单元 → 模型 → 全局，样本越薄越靠上层先验；只有一个 rater 时退化为恒等映射。
- 等分位映射回冻结的参考分布（测验等值 equipercentile equating），
  分位截尾在 [0.5%, 99.5%]，单条幸运读数不能把诗弹上天。
- 诗级聚合用贝叶斯平均（IMDB 加权评分），读数少的诗向全局均值收缩。

有效性前提：各 rater 面对的诗池质量可比（派发大体随机）。本脚本每次运行
输出均衡诊断，某模型读到的诗池代理均分偏离全局超过阈值时给出警告——
届时分位换算会系统性偏袒/压制该模型。2026-07-17 起加入**偏差感知信任**：
诊断出的偏离直接衰减该模型自身分布的收缩权重（trust = max(0, 1-|dev|/D_TRUST)），
诗池失衡的模型（如只被派了好诗的低读数模型）自动滑向全局先验、近似恒等映射，
而不是把"读的全是好诗"误译成"这个 rater 手松"。警告仍保留，供检查派发逻辑。

用法：
  python calibrate.py            # 生成 results/calibration/report.md（参考分布不存在则先冻结）
  python calibrate.py --refreeze # 用当前全量读数重新冻结参考分布（升版本，慎用）
"""
import argparse
import bisect
import json
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus" / "诗稿.json"
READS = ROOT / "results" / "reads" / "reads.jsonl"
CURATION = ROOT / "results" / "curation.json"
OUT_DIR = ROOT / "results" / "calibration"
REFERENCE = OUT_DIR / "reference-v1.json"
REPORT = OUT_DIR / "report.md"
SCORES = OUT_DIR / "scores.json"

# 模型标签规范化：不改写 reads.jsonl 历史记录，只在读取时映射。
# 不明 gemini 碎片按约定归入 gemini-2.5-pro；haiku 两个写法合并；
# 2024 年代的 claude 标签（疑似派发方误报）归入最近的 sonnet。
MODEL_ALIASES = {
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-3.5-sonnet": "claude-sonnet-4-6",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-pro",
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-1.5-flash": "gemini-2.5-pro",
    "gemini-2.0": "gemini-2.5-pro",
    "gemini-2.0-pro": "gemini-2.5-pro",
    "gemini-2.0-pro-exp": "gemini-2.5-pro",
    "gemini-exp-1121": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-pro",
}

# —— 校准池按"评判标准"分界，不按作品当前文体 ——
# v1.3（2026-07-17）起，非诗文体经设置页勾选后可被读，读者 prompt 带「体裁转换」段，
# 评判尺度已不是诗歌底线，这些读数不进诗的校准池（前端对无校准分的作品自动回退原始均分）。
# 分界日之前的全部读数都产生于诗歌底线（含后来被作者改标为杂文/草稿的作品），保留——
# 它们是合法的 rater 松紧原料，剔除反而让校准漂移。
POETRY_GENRES = {"现代诗", "词", "歌词"}
STANDARD_SPLIT = "2026-07-17"

K_CELL = 10     # 单元层收缩常数：单元读数≈K_CELL 时，单元自身分位占一半权重
K_MODEL = 20    # 模型层收缩常数（对模型全体读数）
C_PRIOR = 5     # 诗级贝叶斯平均的先验读数条数
RHO = 0.1       # 同模型组内相关（实测 ICC≈0.089，见 bayes_avg 注释）
P_CLIP = (0.005, 0.995)   # 分位截尾（≈参考分布 P99 封顶）
BALANCE_WARN = 0.15       # 均衡诊断：诗池代理均分偏离全局超过此值则警告
D_TRUST = 0.6             # 偏差感知信任半径：|proxy_dev| 达此值时该模型自身分布权重归零


def canon_model(m):
    m = (m or "unknown").strip()
    return MODEL_ALIASES.get(m, m)


def load_reads():
    """与网页榜单同口径：只用 blind 读，剔除作者折叠（curation）的记录；
    另按 STANDARD_SPLIT 剔除体裁转换标准下产生的非诗读数（见顶部注释）。"""
    hidden = set()
    if CURATION.exists():
        cur = json.loads(CURATION.read_text(encoding="utf-8"))
        hidden = {k for k, v in cur.items() if v.get("hidden")}
    genres = {p["id"]: (p.get("genre") or "")
              for p in json.loads(CORPUS.read_text(encoding="utf-8"))}
    rows = []
    with READS.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("score") is None:
                continue
            if r.get("context_mode") != "blind" or r.get("read_id") in hidden:
                continue
            if genres.get(r["poem_id"]) not in POETRY_GENRES and \
                    (r.get("ts") or "") >= STANDARD_SPLIT:
                continue
            reader = r.get("reader") or {}
            rows.append({
                "read_id": r["read_id"],
                "poem_id": r["poem_id"],
                "persona": reader.get("persona_id") or "unknown",
                "model": canon_model(reader.get("model")),
                "score": float(r["score"]),
            })
    return rows


def hazen_percentile(sorted_scores, s):
    """mid-rank 经验分位：并列分取中位秩，避免最大值恰好落在 P100。"""
    lo = bisect.bisect_left(sorted_scores, s)
    hi = bisect.bisect_right(sorted_scores, s)
    return (lo + 0.5 * max(hi - lo, 1)) / len(sorted_scores)


def quantile(sorted_scores, p):
    """线性插值分位数。"""
    i = (len(sorted_scores) - 1) * p
    lo = int(i)
    hi = min(lo + 1, len(sorted_scores) - 1)
    return sorted_scores[lo] + (sorted_scores[hi] - sorted_scores[lo]) * (i - lo)


class Calibrator:
    def __init__(self, rows, reference_scores, trust=None):
        # trust: {model: 0..1}，来自 balance_check 的偏差感知信任。
        # 诗池代理偏离越大，该模型自身分布越不可信，收缩权重按比例衰减；
        # 未知模型（无跨模型重合诗、算不出代理）默认 1.0——它们本就读数极少，
        # K_CELL/K_MODEL 已把权重压得很低，不再叠罚。
        self.trust = trust or {}
        self.reference = sorted(reference_scores)
        self.by_cell = defaultdict(list)
        self.by_model = defaultdict(list)
        self.all_scores = []
        for r in rows:
            self.by_cell[(r["persona"], r["model"])].append(r["score"])
            self.by_model[r["model"]].append(r["score"])
            self.all_scores.append(r["score"])
        for d in (self.by_cell, self.by_model):
            for k in d:
                d[k].sort()
        self.all_scores.sort()

    def shrunk_percentile(self, persona, model, s):
        cell = self.by_cell.get((persona, model), [])
        mdl = self.by_model.get(model, [])
        p_global = hazen_percentile(self.all_scores, s)
        p_model = hazen_percentile(mdl, s) if mdl else p_global
        p_cell = hazen_percentile(cell, s) if cell else p_model
        t = self.trust.get(model, 1.0)
        w_cell = t * len(cell) / (len(cell) + K_CELL)
        w_model = t * len(mdl) / (len(mdl) + K_MODEL)
        p = w_cell * p_cell + (1 - w_cell) * (w_model * p_model + (1 - w_model) * p_global)
        return min(max(p, P_CLIP[0]), P_CLIP[1])

    def calibrate(self, persona, model, s):
        return quantile(self.reference, self.shrunk_percentile(persona, model, s))


def bayes_avg(reads, prior_mean):
    """组内相关加权的贝叶斯平均：reads = [(model, score)]。

    同一模型第 k 条读数的权重 = 1/(1+(k-1)·RHO)，即有效票数
    n_eff = k/(1+(k-1)·RHO)：同模型多人设横扫边际收益递减但不归零。
    RHO 取实测值：2026-07-13 在校准后读数上估得同模型组内相关
    ICC≈0.089（同诗 same-model 对 3579 / diff-model 对 6690）——
    校准移除松紧度后，同模型不同人设接近独立声音，故只做温和折扣。
    （曾试过每模型一票的两段式，split-half 显得更稳是收缩偏置的假象，
    交叉预测误差和 ICC 都不支持，已回退。）"""
    cnt = defaultdict(int)
    for m, _ in reads:
        cnt[m] += 1
    w_sum = 0.0
    s_sum = 0.0
    for m, s in reads:
        w = 1.0 / (1.0 + (cnt[m] - 1) * RHO)
        w_sum += w
        s_sum += w * s
    return (s_sum + C_PRIOR * prior_mean) / (w_sum + C_PRIOR)


def balance_check(rows):
    """各模型读到的诗池质量代理是否均衡。

    代理 = 诗被**其它模型**读出的均分（leave-one-rater-out）——若混入本模型
    自己的读数，严格/慷慨的 rater 会污染自家诗池的代理，把自身松紧误报成
    诗池偏差。只统计有其它模型读数的诗。"""
    poem_model_scores = defaultdict(lambda: defaultdict(list))
    for r in rows:
        poem_model_scores[r["poem_id"]][r["model"]].append(r["score"])
    out = []
    by_model = defaultdict(list)
    for r in rows:
        others = [s for m2, ss in poem_model_scores[r["poem_id"]].items()
                  if m2 != r["model"] for s in ss]
        if others:
            by_model[r["model"]].append(sum(others) / len(others))
    all_proxy = [x for xs in by_model.values() for x in xs]
    global_proxy = sum(all_proxy) / len(all_proxy)
    for m, xs in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        dev = sum(xs) / len(xs) - global_proxy
        out.append({"model": m, "reads": len(xs), "proxy_dev": round(dev, 3),
                    "trust": round(max(0.0, 1.0 - abs(dev) / D_TRUST), 3),
                    "warn": abs(dev) > BALANCE_WARN})
    return out


def generate(refreeze=False, top=20):
    """全量重算校准视图，落盘 report.md + scores.json。server.py 在
    scores.json 过期时自动调用；CLI 手动跑也走这里。"""
    rows = load_reads()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if refreeze or not REFERENCE.exists():
        ref = {"version": 1, "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "n": len(rows), "scores": sorted(r["score"] for r in rows),
               "note": "参考分布 = 冻结时点的全体读数合并分布（BASELINE 语义下生成）"}
        REFERENCE.write_text(json.dumps(ref, ensure_ascii=False), encoding="utf-8")
        print(f"reference frozen: n={ref['n']} -> {REFERENCE}")
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))

    # 均衡诊断先行：它的 proxy_dev 直接变成 Calibrator 的偏差感知信任
    balance = balance_check(rows)
    trust = {b["model"]: b["trust"] for b in balance}
    cal = Calibrator(rows, ref["scores"], trust)
    titles = {p["id"]: p["title"] for p in json.loads(CORPUS.read_text(encoding="utf-8"))}

    # 逐条校准，按诗聚合（raw/cal 都用两段式，名次变动才只反映校准本身）
    by_poem_raw = defaultdict(list)
    by_poem_cal = defaultdict(list)
    read_cal = {}   # read_id -> 校准后单读分（统计页"校准口径"用同一公式换原料重算）
    for r in rows:
        c = cal.calibrate(r["persona"], r["model"], r["score"])
        by_poem_raw[r["poem_id"]].append((r["model"], r["score"]))
        by_poem_cal[r["poem_id"]].append((r["model"], c))
        read_cal[r["read_id"]] = round(c, 2)

    g_raw = sum(r["score"] for r in rows) / len(rows)
    all_cal = [s for xs in by_poem_cal.values() for _, s in xs]
    g_cal = sum(all_cal) / len(all_cal)

    poems = []
    for pid in by_poem_raw:
        n = len(by_poem_raw[pid])
        n_models = len({m for m, _ in by_poem_raw[pid]})
        poems.append({
            "poem_id": pid, "title": titles.get(pid, pid), "n": n, "n_models": n_models,
            "raw_mean": round(sum(s for _, s in by_poem_raw[pid]) / n, 2),
            "raw_bayes": round(bayes_avg(by_poem_raw[pid], g_raw), 3),
            "cal_bayes": round(bayes_avg(by_poem_cal[pid], g_cal), 3),
        })
    # 展示分：诗级聚合分（多读平均后天然收窄）线性量表化回单读者打分的
    # 离散度（T-score 思路：均值不动、方差匹配参考分布），排序完全不变。
    # 这样"质 8.5"读起来就是"一位标准读者大概会给 8.5"，而不是收缩后的 7.6。
    ref_mean = sum(ref["scores"]) / len(ref["scores"])
    ref_sd = (sum((s - ref_mean) ** 2 for s in ref["scores"]) / len(ref["scores"])) ** 0.5
    cb = [p["cal_bayes"] for p in poems]
    cb_mean = sum(cb) / len(cb)
    cb_sd = (sum((x - cb_mean) ** 2 for x in cb) / len(cb)) ** 0.5 or 1.0
    stretch_k = min(ref_sd / cb_sd, 4.0)   # 封顶防语料极小时病态拉伸
    for p in poems:
        p["display"] = round(min(max(
            g_cal + (p["cal_bayes"] - g_cal) * stretch_k, 0.0), 10.0), 2)

    rank_raw = {p["poem_id"]: i for i, p in enumerate(
        sorted(poems, key=lambda x: -x["raw_bayes"]), 1)}
    poems.sort(key=lambda x: -x["cal_bayes"])
    for i, p in enumerate(poems, 1):
        p["rank"] = i
        p["rank_delta"] = rank_raw[p["poem_id"]] - i   # 正 = 校准后升

    lines = []
    lines.append("# 校准报告\n")
    lines.append(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M')}；读数 {len(rows)} 条，"
                 f"诗 {len(poems)} 首；参考分布 v{ref['version']}（冻结于 {ref['frozen_at']}，n={ref['n']}）")
    lines.append(f"- 参数：K_CELL={K_CELL} K_MODEL={K_MODEL} C_PRIOR={C_PRIOR} "
                 f"P_CLIP={P_CLIP} D_TRUST={D_TRUST} 全局均分 raw={g_raw:.3f} cal={g_cal:.3f}\n")

    lines.append("## 均衡诊断（分位换算的有效性前提）\n")
    lines.append("（信任 = max(0, 1-|偏离|/D_TRUST)，乘进该模型自身分布的收缩权重；"
                 "0 = 诗池失衡到自身分布完全不可信，校准退回全局先验）\n")
    lines.append("| 模型 | 读数 | 诗池代理偏离 | 信任 | 警告 |")
    lines.append("|---|---|---|---|---|")
    for b in balance:
        lines.append(f"| {b['model']} | {b['reads']} | {b['proxy_dev']:+.3f} | "
                     f"{b['trust']:.2f} | {'**YES**' if b['warn'] else '-'} |")

    lines.append("\n## 模型换算示例（原始分 → 校准分）\n")
    lines.append("| 模型 | N | 7.0→ | 7.5→ | 8.0→ |")
    lines.append("|---|---|---|---|---|")
    for m, xs in sorted(cal.by_model.items(), key=lambda kv: -len(kv[1])):
        ex = [cal.calibrate("__none__", m, s) for s in (7.0, 7.5, 8.0)]
        lines.append(f"| {m} | {len(xs)} | {ex[0]:.2f} | {ex[1]:.2f} | {ex[2]:.2f} |")

    lines.append(f"\n## 校准榜 Top {top}\n")
    lines.append(f"（展示分 = 校准贝叶斯经方差匹配拉伸，k={stretch_k:.2f}，排序不变）\n")
    lines.append("| 名次 | 诗 | 读数 | 模型数 | 原始均分 | 校准贝叶斯 | 展示分 | 名次变动 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for p in poems[:top]:
        lines.append(f"| {p['rank']} | 《{p['title']}》{p['poem_id']} | {p['n']} | {p['n_models']} "
                     f"| {p['raw_mean']} | {p['cal_bayes']} | {p['display']} "
                     f"| {p['rank_delta']:+d} |")

    movers = sorted((p for p in poems if p["n"] >= 5),
                    key=lambda x: -abs(x["rank_delta"]))[:10]
    lines.append("\n## 校准后名次变动最大（读数≥5）\n")
    lines.append("| 诗 | 读数 | 原始贝叶斯 | 校准贝叶斯 | 名次变动 |")
    lines.append("|---|---|---|---|---|")
    for p in movers:
        lines.append(f"| 《{p['title']}》{p['poem_id']} | {p['n']} | {p['raw_bayes']} "
                     f"| {p['cal_bayes']} | {p['rank_delta']:+d} |")

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 机器可读输出：供 server.py /api/state 下发给网页
    # display = 展示分（网页显示与排序都用它）；cal = 拉伸前的校准贝叶斯（审计用）
    SCORES.write_text(json.dumps({
        "meta": {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "reads": len(rows), "reference_version": ref["version"],
                 "rho": RHO, "c_prior": C_PRIOR, "stretch_k": round(stretch_k, 3),
                 "d_trust": D_TRUST,
                 # 规范别名表随数据下发：前端展示层与校准口径用同一套归并
                 "aliases": MODEL_ALIASES},
        "poems": {p["poem_id"]: {"display": p["display"], "cal": p["cal_bayes"],
                                 "n": p["n"], "n_models": p["n_models"]} for p in poems},
        "reads": read_cal,
    }, ensure_ascii=False), encoding="utf-8")

    warn_n = sum(1 for b in balance if b["warn"])
    print(f"report -> {REPORT}  scores -> {SCORES}  (poems={len(poems)}, balance warnings={warn_n})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refreeze", action="store_true",
                    help="用当前全量读数重新冻结参考分布（会让已展示的校准分整体漂移）")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    generate(refreeze=args.refreeze, top=args.top)


if __name__ == "__main__":
    main()
