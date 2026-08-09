---
name: dispatch-votes
description: 昼青集点赞模式派发。让独立读者对盲读评论投 up/down/skip 并可选 best；用户说“跑点赞”“投票”“检查评论质量”时使用。
---

# 点赞派发

在 `theater/runners` 工作。先读 `AGENTS.md` 与 `05_run_modes.md`。用户未指定目标、
fraction 或 batch-size 时先报告候选与预计任务数，不擅自扩大批次。

## 不可变规则

- 一份 task 是一个投票人的一个独立上下文；batch-size 只表示该人一张票面里的评论数。
- `votes.jsonl` 只能由 collect 追加；void 用 sidecar，永不删票。
- 主动票和跟帖 piggyback 是两条信号；投票幂等身份不含 model，换模型重跑不会多一票。
- 同一身份同值重试会跳过；同一身份方向冲突会整批拒收，必须人工查明。

## 流程

1. 生成任务：

   ```text
   python plan_votes.py invite --poem-ids zq-0001,zq-0002 --fraction 0.3 --out batches/votes-<批次>
   python plan_votes.py invite --targets r-000123,r-000456 --fraction 0.5 --out batches/votes-<批次>
   ```

2. Codex 用 `task-runner` 分波派发；Claude 使用其项目 agent；AGY/CodeBuddy 每个
   task 启动独立进程并启用结构化 JSON。记录真实 model 与 transport。
3. collect 前核对票面长度、read_id、vote 值域、model 和输出独立性。极端单一方向
   只表示需要人工检查 prompt/批次，不自动作废。
4. 入库和审计：

   ```text
   python plan_votes.py collect --tasks batches/<批次>/tasks --inbox batches/<批次>/inbox --model <真实模型ID>
   python plan_votes.py tally --poem-id zq-0001
   python audit_data.py
   ```

汇报落盘、幂等跳过、冲突/无效和 tally 摘要；不复述评论全文。
