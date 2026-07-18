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
- vote 值域：`up` / `down` / `skip` / `best`（2026-07-18 扩展：`best`=加精，批量模式独有的相对判断，旧统计代码自动忽略它）。

## 1. 确认投票目标

```
# 对某几首诗的全部盲读评论（短评+长评）——默认批量装箱（≤8 条/箱、4000 字预算）
python plan_votes.py invite --poem-ids zq-0001,zq-0002 --fraction 0.3 \
    --out batches/votes-<批次名>

# 直接指定几条具体评论
python plan_votes.py invite --targets r-000123,r-000456 --fraction 0.5 \
    --out batches/votes-<批次名>
```

参数说明：
- `--fraction 0.3`：每条评论邀请 30% 的读者投票（不重复邀请已投过票的；已投过的条目会从该读者的票面上摘掉，而不是把人整箱排除）
- `--batch-size 8`（默认）：每箱最多 8 条评论；`--batch-chars 4000`（默认）：每箱正文字符预算——长评多的箱自动装得少。两个上限同时生效，不用手调
- 批量回执自带一条「加精」（best）：同箱横向比较里最扛得住的一条。逐条 up/down/skip 有正向偏置（实测八成 up），加精是相对判断、不受偏置影响，是白捡的区分度信号
- 任务生成后打印任务数，向用户确认再派发

## 2. 派发

`poem-reader` 子代理专为盲读设计，输出格式固定为 score/reaction，**不适用于点赞任务**。点赞模式的通道（按当时哪个便宜用哪个，不预设）：

### 方式 A：CC 轻量子代理 task-runner

用 Agent 工具，`subagent_type: "task-runner"`（只带 Read+Write，起步开销约为全工具子代理的 1/4；agent 定义新建/修改后要**新会话**才派得出去），模型按批次指定，并行 15–20 个为一波：

```
PROMPT 文件：<仓库根目录>/theater/runners/batches/<批次>/tasks/task-NNN.prompt.txt
RESPONSE 输出文件：<仓库根目录>/theater/runners/batches/<批次>/inbox/task-NNN.response.json
回执 model 字段填：<模型 ID>
```

### 方式 B：hy3 / CodeBuddy / agy 等外部 CLI

每个 task-NNN.prompt.txt 原样喂给外部工具（不要改写、不要摘要），回执格式见下。这些通道本来就没有 CC 子代理的固定开销，成本按各家额度自行折算。agy 注意：无结构化输出、退出码不可信，提示词里要求"只输出这个 JSON、不要任何别的话"，回来自行解析校验。

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
  ],
  "best": "r-xxxxxx"
}
```

- `vote` 只能是 `"up"` / `"down"` / `"skip"`
- 批量模式：`votes` 数组长度必须等于 prompt 里列出的评论数，`read_id` 原样照抄
- `best`（加精）：本箱里最扛得住的一条 read_id，或 null（仅当真分不出高下）；指向箱外的 best 会被 collect 忽略并告警
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
