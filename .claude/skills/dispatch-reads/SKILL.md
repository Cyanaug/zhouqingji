---
name: dispatch-reads
description: 昼青集盲读批量派发。计算覆盖缺口 → plan 生成任务 → poem-reader 子代理并发盲读 → 质检 → collect 入库。当用户要"加厚覆盖"、"跑一轮盲读"、"派读者"时使用。
---

# 盲读派发 SOP

工作目录：仓库根目录下的 `theater/runners`。所有命令在此目录执行（下面路径都是相对这个目录写的）。

**开工前确认**：若用户没有明确给出（a）要读哪些诗 / 目标层数、（b）用什么模型，先算好缺口后**向用户报告缺口并确认范围与模型**，再动手派发。用户已明确给全的，直接执行。

## 0. 铁律（FROZEN，违反即事故）

- `reads.jsonl` 只允许 `runner.py collect` 追加，**永不手编、永不删行**。诗被改动时靠 `content_hash` 标记旧读为过时，不删除。
- task.json / response.json 的字段结构是 FROZEN 的，不得增删字段。
- 榜单永不让 LLM 排名，只从 reads.jsonl 事后推导。
- 一诗一子代理（作者定死，不用批量读换纯度）。

## 1. 算缺口

```
python runner.py coverage          # 只显示最薄的 15 首（默认截断！）
python runner.py coverage --full   # 全量，算真实缺口必须用这个
```

用 `--full` 输出统计每首的读数，确定目标层数（如 4 层）下还缺哪些 poem_id、各缺几读。

## 2. 生成批次

```
mkdir batches/<批次名> batches/<批次名>/tasks batches/<批次名>/inbox   # plan 不自动建目录
python runner.py plan --poem-ids "<逗号分隔的id>" --readers <每首几读> --out batches/<批次名>/batch.json
```

- **必须用 `--poem-ids` 显式指定**，不要用 `--poems N` 让它自选最薄——如果同时有多个会话/进程在并行派发，各自按 id 切片分工，避免抢同一批诗。
- 拆分 batch.json 为单任务文件：每个元素写成 `tasks/task-NNN.json`（3 位零填充，`ensure_ascii=False`）。
- **同时为每个任务写 prompt 侧车**：`tasks/task-NNN.prompt.txt`，内容 = 该任务 `prompt` 字段原文（多行纯文本）。原因：prompt 在 JSON 里是单行长字符串，Read 工具按行截断，长诗正文可能落在截断线外——读者看不到诗就打分，历史上出过因此污染多条读的事故。读者只读侧车；task.json 只给 collect 用。

## 3. 派发

- 用 Agent 工具，`subagent_type: "poem-reader"`，`model: "haiku"`（默认，成本最低；sonnet 约 3.5–4 倍价，仅特批时用）。
- 派发指令全文只有三行（`<仓库根目录>` 换成你实际的路径）：

```
PROMPT 文件：<仓库根目录>/theater/runners/batches/<批次>/tasks/task-NNN.prompt.txt
RESPONSE 输出文件：<仓库根目录>/theater/runners/batches/<批次>/inbox/task-NNN.response.json
回执 model 字段填：claude-haiku-4-5
```

- **波次并发**：一个回复里并行发 15–20 个 Agent 调用为一波，波与波之间紧接着发，吃提示缓存。不要一个一个串行发。
- 子代理完成靠 task-notification 自动通知，**不要轮询、不要 ScheduleWakeup**；等通知间隙可偶尔 `ls inbox/*.json | wc -l` 确认进度。
- ⚠ agent 定义（poem-reader.md）在会话启动时加载：改了定义要**新会话**才生效。

## 3b. 非 CC 派发方（用其他 AI 编程工具，如 CodeBuddy / 其他主管模型）

没有 `poem-reader` agent 可用时，用下面这份完整 prompt 模板派发（把 `PROMPT_FILE_PATH`/`RESPONSE_FILE_PATH` 换成实际路径）；其余步骤（`--poem-ids` 切片、质检、collect）不变，`collect` 时 `--model`/`--transport` 如实填对应模型与通道。

```
你是「诗歌盲读读者」。

必须遵守的读者底线：
1. 读懂并说出诗里的感受，是好读者。关于技艺的逆耳批评请保留——作者要听真话。
2. 「情绪低沉 = 诗差」是误读。把「传达得好不好」和「情绪暗不暗」严格分开。
3. 只读眼前这一首，不与别的诗比较排名。
4. 评分是你个人的真实反应（0–10）。7 分以上意味着真心喜欢；8 分以上留给读完还惦记、会想重读、会主动安利的——真到了这个程度不要因为"很少给高分"就压着不给。

Steps:
1. 读 PROMPT_FILE_PATH（纯文本侧车）—— 已包含诗全文、人设、一切所需
2. 完整性自检：「—— 现在，读这首诗 ——」之后必须能看到诗正文；看不到就不写回执，只回复「失败：PROMPT 文件不完整」。不要编造，不要读 诗稿.json、personas.json 或任何其他文件
3. 以人设读诗，产出 JSON 回执，写到 RESPONSE_FILE_PATH

Response format:
{
  "model": "<模型 ID>",
  "score": 7.0,
  "reaction": "两到三句话的真实短评，120 字以内。像跟帖，不像论文摘要。",
  "long_form": null
}

Rules:
- 用「」做中文引号
- long_form 为 null，除非真的有超出短评的话要说
- 只写 JSON 文件
```

## 4. 质检（collect 之前，必做）

- 数 inbox 回执数 == 任务数。子代理回复「失败：PROMPT 文件不完整」的，检查侧车是否漏生成，修好后重派该 task。
- 抽查 JSON 合法性；重点检查**空诗**：task 的 `content_hash` 为 `da39a3ee5e6b4b0d3255bfef95601890afd80709`（空串 SHA1）说明源诗 content 为空，其回执是编造的——移入 `inbox/quarantine/`，并向作者报告该 poem_id 的语料有问题。
- 不合格回执一律隔离，不修不补，让 collect 报"缺失"。

## 5. 入库与验证

```
python runner.py collect --tasks batches/<批次>/tasks --inbox batches/<批次>/inbox --model claude-haiku-4-5 --transport cc-subagent
python runner.py coverage --full   # 验证层数达标
```

collect 幂等，已收回执归档进 `inbox/ingested/`。

## 6. 汇报口径

向用户汇报时只给：批次名、成功/隔离/缺失数、总读数变化、覆盖分布变化、成本估算。不复述任何诗评内容。

## 7. 派发前必须先给用户确认清单，等批准再动手

批量派发会消耗真实额度。开工前把「读哪些诗、多少读者、用什么模型、大概多少条」列成清单报给用户，拿到明确批准再派发；不要自作主张扩大范围或换更贵的模型。选样如果是为了做统计/校准，要覆盖不同分数段的广度，不要只挑高分诗。
