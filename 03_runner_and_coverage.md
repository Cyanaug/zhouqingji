# 03 · 跑批与覆盖 Runner & Coverage

## 阅读记录 Schema —— FROZEN（结构冻结）

一条记录 = 某读者读某诗的一次反应。存在 `results/reads/`（建议按 `poem_id` 分子目录，或单一 append-only JSONL，你定实现，但字段冻结）。

```json
{
  "read_id": "r-000123",
  "poem_id": "zq-0001",
  "reader": {
    "persona_id": "classical-scholar",
    "model": "claude-...",
    "knows_诠释": false,
    "knows_date": false
  },
  "context_mode": "blind",           // blind | thread
  "thread_ref": null,                // thread 模式下指向被回复的 read_id
  "transport": "cc-subagent",        // 见下：这次是怎么跑的
  "score": 7.5,                      // 读者给的真实分（浅层信号）
  "reaction": "两三句短评，给字数上限", // 列表里展示的短跟帖
  "long_form": null,                 // 可选：长文深读，进"深读"专栏页
  "ts": "2026-..T..",
  "content_hash": "sha1(读时的content)" // 用于判断评论是否已过时（见 01 契约）
}
```

- `reaction` **设字数上限**（建议 2–3 句），保证列表能扫。
- 若读者产出了长文分析，放 `long_form`，前端最小化、点开进独立"深读"专栏页慢慢看，不挤在评论列表里。
- **每条必记 `transport` 与 `model`**——将来若用了质量拉胯的免费模型，作者能按字段一键过滤。**永不丢失出处，是这套东西的命根子。**

## 传输层 Transport（LATITUDE：实现你选）

"读者"是抽象（model + persona），"怎么把这次阅读跑出来"是**传输层**，可换、不影响数据模型：

- `api`：直连各家 API。
- `cc-subagent` / `agy-subagent`：用 CC/agy 的冗余额度派 subagent 代劳。**前期推荐**——便宜、顺便 dogfooding。
- `free-model`：免费模型（会拉低质量，按 `transport` 字段可事后过滤）。

换传输层只改这一层，schema 一行不动。

## 覆盖账 Coverage Ledger（让作者不必盯"谁被泛读少了"）

作者明确不想操心"某某诗是不是泛读少了、某人设少了"。所以**不靠作者记，靠一本覆盖账**：

- 覆盖账是对 `results/reads/` 的一个**计算视图**（别单独维护、免得漂移）：统计每首诗被各 `(model, persona_id)` 在 `blind` 下读过几次。
- 作者只说"跑一轮"，runner 就自动挑**覆盖最薄**的 (诗 × 读者) 组合去补，天然均衡。
- 提供两种触发：
  - **静默补齐**：作者完全不管，agent 自己按覆盖账跑。
  - **先解释再跑**：agent 报"这轮打算补这些冷门诗/人设"，作者点头再跑。（作者原话："他们解释给我听再做，无所谓。"）

**目标**：作者跟 agent 一句话就能推进一轮，不用逐条指挥。你把这套规则设好，让这件事变成"说一声"级别的操作。

## 成本纪律

- 主体是 **blind**，成本线性，可长期无限累积不涨——这是"重读积累"的稳定来源。
- **thread** 成本随楼层膨胀，只对精选少数诗定点开、给楼层上限。
- 免费/冗余额度优先跑 blind 的批量；贵的 API 留给少数精读或 thread。
