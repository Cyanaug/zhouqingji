---
name: dispatch-votes
description: 昼青集点赞模式派发。让读者对已有盲读评论（短评或长评）投票认同/不认同/跳过，收集"这条评论有没有说到点子上"的信号。当用户要"跑点赞"、"投票"、"看哪条评论质量差"时使用。
---

# 点赞模式派发 SOP

工作目录：`theater/runners`。所有命令在此目录执行。

**开工前确认**：若用户没有指明（a）对哪些诗/哪些具体评论、（b）fraction 和 batch-size，先向用户报清单并等批准，再动手。批量派发消耗真实额度，不得自作主张扩大范围。

## 0. 铁律（FROZEN，违反即事故）

- `results/votes/votes.jsonl` 只允许 `plan_votes.py collect` 追加，永不手编、永不删行。
- 点赞数据与 `reads.jsonl` 完全独立，不影响盲读分数和校准。
- 跟帖楼层（context_mode=thread）不走本模式——它们靠回帖时顺势带票，不重复邀请。
- **一 task 一独立上下文**：batch-size 打包的是"一个投票人读多条评论"，不是"一个上下文扮演多个投票人"。每份回执必须由只见过自己那份 prompt 的独立模型上下文产出；严禁同一个上下文代写多位读者的投票（那是一只手投全场的票，信号就是假的）。

## 1. 确认投票目标

```
# 对某几首诗的全部盲读评论（短评+长评）
python plan_votes.py invite --poem-ids zq-0001,zq-0002 --fraction 0.3 --batch-size 4 \
    --out batches/votes-<批次名>

# 直接指定几条具体评论
python plan_votes.py invite --targets r-000123,r-000456 --fraction 0.5 \
    --out batches/votes-<批次名>
```

参数说明：
- `--fraction 0.3`：每条评论邀请 30% 的读者投票（不重复邀请已投过票的）
- `--batch-size 4`：批量模式，一个任务打包 4 条评论（推荐 3–5，减少任务数且能横向比较）；默认 1=逐条
- 任务生成后打印任务数，向用户确认再派发

## 2. 派发

`poem-reader` 子代理专为盲读设计，输出格式固定为 score/reaction，**不适用于点赞任务**。点赞模式用以下两种方式之一：

### 方式 A：CC 通用子代理（推荐）

用 Agent 工具，`subagent_type: "claude"`，`model: "haiku"`，每个任务一个 agent，并行 15–20 个为一波：

```
读 <仓库根目录>/theater/runners/batches/<批次>/tasks/task-NNN.prompt.txt 的全部内容，
按其中指示产出 JSON，写入 <仓库根目录>/theater/runners/batches/<批次>/inbox/task-NNN.response.json。
model 字段填 claude-haiku-4-5。
```

### 方式 B：hy3 / CodeBuddy 等外部工具

每个 task-NNN.prompt.txt 原样喂给 hy3，回执格式见下。

**逐条模式（batch-size=1）回执格式**：
```json
{
  "model": "实际底层模型ID（非工具名）",
  "vote": "up",
  "reason": "可选，down 时请说清哪里不认同"
}
```

**批量模式（batch-size>1）回执格式**：
```json
{
  "model": "实际底层模型ID（非工具名）",
  "votes": [
    {"read_id": "r-xxxxxx", "vote": "up",   "reason": ""},
    {"read_id": "r-yyyyyy", "vote": "down",  "reason": "没读出层次来"},
    {"read_id": "r-zzzzzz", "vote": "skip",  "reason": ""}
  ]
}
```

- `vote` 只能是 `"up"` / `"down"` / `"skip"`
- 批量模式：`votes` 数组长度必须等于 prompt 里列出的评论数，`read_id` 原样照抄
- `model` 填底层真实模型 ID，不是工具名（hy3/codebuddy 是工具名）

## 3. 质检（collect 之前）

- 回执数 == 任务数，缺少的检查是否派发漏掉
- 抽查 `model` 字段：必须是真实模型 ID；出现工具名的隔离查清再收
- 批量回执：检查 `votes` 数组长度是否匹配
- **落盘时间形态**：多份回执同一秒落盘 = 单上下文批量代写的红旗，隔离整批查明产出方式后再收
- **区分度预警**：up 占比超过九成的批次（如 137↑/1↓）说明投票框架没有起到区分作用，向用户报告并讨论是否收紧 prompt，而不是照常入库当信号用

## 4. 落盘 + 查看结果

```
python plan_votes.py collect --tasks batches/<批次>/tasks \
    --inbox batches/<批次>/inbox --model <模型ID>

python plan_votes.py tally --poem-id zq-0001
```

collect 会打印落盘数和无效数；tally 输出每条评论的 👍/👎/跳过 计数。

## 5. 汇报口径

向用户汇报：批次名、落盘数、无效数、tally 摘要（哪几条评论 down 票多）。不复述具体评论内容，由用户自己去 webapp 查看详情决定是否折叠低质评论。
