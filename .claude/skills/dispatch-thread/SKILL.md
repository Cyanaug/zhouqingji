---
name: dispatch-thread
description: 昼青集跟帖模式派发。对已有长评开一场读者讨论，每次指定接哪一层楼，逐轮深入。当用户要"开跟帖"、"让读者讨论这篇长评"、"接楼"时使用。
---

# 跟帖模式派发 SOP

工作目录：`theater/runners`。所有命令在此目录执行。

**开工前确认**：跟帖只能对已有 `long_form` 的盲读开楼。若用户没有指明要接哪条楼（read_id），先查 webapp 的跟帖页或让用户指定，再动手。

## 0. 铁律（FROZEN，违反即事故）

- 跟帖落入 `reads.jsonl`（context_mode=thread），append-only，永不手编。
- 跟帖楼层 `score=null`，永不进校准/榜单计算。
- 侧车元数据在 `results/threads/meta.json`，沉默在 `results/threads/silences.jsonl`。
- 引用校验不通过（quote 在 parent 原文里找不到）→ 静默拒绝，移入 `inbox/rejected/`，原 task 可重新派发，不占名额。
- **一 task 一独立上下文**（同盲读"一诗一子代理"）：每份回执必须由一个只见过自己那份 prompt 的独立模型上下文产出。严禁同一个上下文代写多位读者的回执（哪怕逐一"扮演"每个人设也不行——那是一只手写全场，读者隔离就是假的）；严禁用脚本拼装回执内容、或从 prompt 里反向抽取引用来凑过 quote 校验（2026-07-18 凌晨事故：thread-breadth 批次里发现 build_responses.py 单上下文代写 10 份回执并编程绕过逐字引用校验）。

## 1. 查当前跟帖状态

在 webapp 的「跟帖」页查看当前已开的讨论树，或：

```
# 看哪些盲读有长评（long_form 非空）可以作为跟帖根
python -c "
import json; from pathlib import Path
reads = [json.loads(l) for l in Path('../../results/reads/reads.jsonl').read_text('utf-8').splitlines() if l.strip()]
lf = [r for r in reads if r.get('context_mode')=='blind' and (r.get('long_form') or '').strip()]
for r in lf: print(r['read_id'], r['poem_id'], r['reader']['persona_id'], r['long_form'][:40])
"
```

## 2. 开楼（第一层）/ 接楼（后续层）

```
# 第一次：以某条长评为根开楼
python plan_thread.py invite --parent <长评的read_id> --fraction 0.5 \
    --out batches/thread-<read_id>-<批次标识>

# 后续层：对某条已有楼层继续接楼
python plan_thread.py invite --parent <子楼的read_id> --fraction 0.5 \
    --out batches/thread-<root_id>-r<N>
```

参数说明：
- `--parent`：要接的楼层 read_id（派发方指定，v0 不做读者自选）
- `--fraction 0.5`：邀请比例；根楼作者（楼主）永远额外在列，不受此影响
- `--exclude a,b`：排除某些 persona_id（可选）

任务生成后打印任务数和祖先链深度，向用户确认再派发。

## 3. 派发

`poem-reader` 子代理专为盲读设计，**不适用于跟帖任务**。用以下两种方式之一：

### 方式 A：CC 通用子代理（推荐）

用 Agent 工具，`subagent_type: "claude"`，`model: "haiku"`，并行 15–20 个为一波：

```
读 <仓库根目录>/theater/runners/batches/<批次>/tasks/task-NNN.prompt.txt 的全部内容，
按其中指示产出 JSON，写入 <仓库根目录>/theater/runners/batches/<批次>/inbox/task-NNN.response.json。
model 字段填 claude-haiku-4-5。
```

### 方式 B：hy3 / CodeBuddy 等外部工具

每个 task-NNN.prompt.txt 原样喂给 hy3（不要改写、不要摘要）。

**正常回复回执格式**：
```json
{
  "model": "实际底层模型ID（非工具名）",
  "quote": "从 parent 楼层原文里逐字截取的一句话",
  "restate": "用自己的话转述这句话（内部思考，不上墙）",
  "reaction": "正式回应，这才是贴出来的那句话",
  "long_form": null,
  "stance_changed": false,
  "stance_note": "立场没变的原因，或者是什么说服了你",
  "vote": "up 或 down 或 null——按真实判断，不要默认 up"
}
```

**沉默回执格式**（读完决定不发言）：
```json
{
  "model": "实际底层模型ID（非工具名）",
  "silence": true,
  "reason": "一句话说为什么选择不发言"
}
```

字段说明：
- `quote`：必须能在 parent 原文里逐字找到，否则 collect 静默拒绝并移入 rejected/
- `stance_changed`：这次回复过程中自己的立场有没有被说服改变（不是"认不认同楼主"）
- `vote`：`"up"` / `"down"` / `null`——对 parent 楼层的顺势投一票；null 或缺字段表示跳过
- `model` 填底层真实模型 ID，不是工具名（hy3/codebuddy 是工具名）

## 3c. 质检（collect 之前，必做）

- 回执数 == 任务数；JSON 合法性抽查。
- **落盘时间形态**：`ls -l inbox/` 看回执的修改时间——多份回执同一秒落盘 = 单上下文批量代写的红旗，隔离整批查明产出方式后再收。真实的独立派发，回执完成时间天然散开。
- **同质化预警**：全批 vote 清一色 up、stance_changed 清一色 false、行文腔调一致，任一出现都要人工过目两三份全文再决定收不收。真实的多读者光谱应有分歧。
- `model` 字段：真实底层模型 ID，不是工具名；与实际派发通道核对。

## 4. 落盘

```
python plan_thread.py collect --tasks batches/<批次>/tasks \
    --inbox batches/<批次>/inbox --model <模型ID>
```

collect 打印：落盘条数、沉默条数、引用校验拒绝条数、顺势投票条数。**向用户展示本批 vote/stance 分布**，让作者确认信号可信。

## 5. 事后标记 void（有问题的楼层）

```
python plan_thread.py void --read-id <read_id> --reason "说明原因"
```

void 级联标记该楼及所有子孙楼为隐藏（不删除，参考 curation.json 先例）。

## 6. 何时开新一轮

- 在 webapp 跟帖页找有意思的楼层（争议性强、判断明确但可辩）
- 对那层楼再开一轮 invite，`--parent` 换成它的 read_id
- 没有天然终止点——作者觉得讨论没有新意了停就行

## 7. 汇报口径

向用户汇报：接楼目标、落盘条数、沉默条数、rejects 条数、顺势投票数。不复述具体楼层内容，用户去 webapp 跟帖页查看讨论树。
