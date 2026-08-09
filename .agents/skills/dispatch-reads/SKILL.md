---
name: dispatch-reads
description: 昼青集盲读批量派发。计算覆盖缺口，生成独立任务，按可用执行器派发，质检并 collect 入库。用户说“加厚覆盖”“跑一轮盲读”“派读者”时使用。
---

# 盲读派发

在仓库根目录下的 `theater/runners` 工作。先读根目录 `AGENTS.md` 和
`03_runner_and_coverage.md`。用户未指定作品范围、目标覆盖或模型时，先算缺口并
报告待确认项；批量任务消耗真实额度，不自行扩大范围。

## 不可变规则

- 一首诗的一份 task 必须由一个独立模型上下文完成。
- `reads.jsonl` 只能通过 collect 追加，永不手编或删行。
- task/response schema 不改；榜单只能从账本推导。
- `reader.model` 填真实模型 ID，`transport` 填执行通道。

## 流程

1. `python runner.py coverage --full` 计算真实缺口。
2. 用明确的 `--poem-ids` 生成批次：

   ```text
   python runner.py plan --poem-ids "zq-0001,zq-0002" --readers 2 --out batches/<批次>/batch.json
   ```

3. 为每个任务生成 `tasks/task-NNN.json` 与完整的
   `tasks/task-NNN.prompt.txt`；prompt 侧车必须包含结尾标记
   `—— 诗歌正文到此为止 ——`，读者只读侧车。
4. 选择执行器：
   - Codex 小批量：每个 task 派一个 `poem-reader` 子代理；按当前可用槽位分波，
     不写死并发数，并给主代理保留质检能力。
   - AGY / CodeBuddy 大批量：每个 task 启动一个全新无状态进程；优先启用当前 CLI
     的 JSON schema/JSON 输出，禁用不需要的工具，绝不复用会话。
   - Claude Code：使用 `.claude/skills/dispatch-reads` 与 `poem-reader`。
5. collect 前检查任务/回执数、JSON、model、正文结尾标记和空内容哈希。异常回执移入
   quarantine，不手修后混入。
6. 入库并审计：

   ```text
   python runner.py collect --tasks batches/<批次>/tasks --inbox batches/<批次>/inbox --model <真实模型ID> --transport <通道>
   python audit_data.py
   python runner.py coverage --full
   ```

汇报批次、成功/隔离/缺失数和覆盖变化，不在主对话复述诗评。
