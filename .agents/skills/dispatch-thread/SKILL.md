---
name: dispatch-thread
description: 昼青集跟帖模式派发。对已有长评开讨论或指定楼层接楼，逐轮深入；用户说“开跟帖”“讨论长评”“接楼”时使用。
---

# 跟帖派发

在 `theater/runners` 工作。先读 `AGENTS.md` 与 `05_run_modes.md`。跟帖只能接已有
`long_form` 的盲读或已有 thread 楼层；用户没指定目标时只提候选，不直接耗额度。

## 不可变规则

- 一份 task 一个独立上下文，禁止一个模型上下文代写多位读者。
- 楼层 `score=null`；引用必须逐字存在于 parent，失败则进入 rejected 并可重派。
- reads、thread meta、silence、piggyback vote 只能由 collect 写入。
- 主动票与 piggyback 是不同信号通道，不能混作同一种去重键。

## 流程

1. 选楼：`python plan_thread.py nextround [--root <root_id>] [--top 3]`。
2. 生成：

   ```text
   python plan_thread.py invite --parent <read_id> --fraction <比例> --out batches/thread-<批次>
   ```

3. 每个 prompt 用独立执行器：Codex 用 `task-runner`；Claude 用其同名项目 agent；
   AGY/CodeBuddy 每 task 新进程并启用结构化 JSON。按当前容量分波，不写死数量。
4. collect 前核对 JSON、真实 model、逐字 quote、回执时间分布和输出差异。全批高度同质
   或同秒落盘是需要隔离调查的信号，不是自动判罪规则。
5. 入库：

   ```text
   python plan_thread.py collect --tasks batches/<批次>/tasks --inbox batches/<批次>/inbox --model <真实模型ID> --transport <通道>
   python audit_data.py
   ```

有问题的楼层用 `plan_thread.py void` 级联隐藏，不删原始记录。汇报落盘、沉默、
reject 和顺势票数量。
