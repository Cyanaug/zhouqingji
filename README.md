# 昼青集 · 读诗剧场

> **硬边界**：`theater` 里的代码**读 `corpus`、写 `results`，永远不修改 `corpus` 内的作品内容**。清洗、删改、剪自注、改可见性，只由作者在 GUI 里做。

让许多 AI 读者（不同模型 × 不同读者背景）阅读你的作品，各自打分、写下反应；作者能看见「同一篇作品在许多双眼睛里的不同形状」。

## 最简单的开始方式

把这个文件夹交给 Codex、Claude Code、Antigravity 或 CodeBuddy，然后只说：**“我是第一次用，请检查项目并带我开始。”**

项目内的 agent 说明会要求它先检查你的安装与数据状态，用普通中文解释结果，再给出少量可选操作。你不需要先学命令行、Git、Python 或 JSON；涉及模型额度、导入作品、公开发布等有成本或难以撤销的操作时，agent 应先向你说明并确认。

## 三层结构

```
昼青集\
├─ corpus\      # 资产层：只进不毁、作者所有、可回滚
│  ├─ 诗稿.json      作品总集（唯一真源，作者可随时手改）
│  ├─ 昼青·诠释.md    读解档案，作者手工增删（可选，没有这个文件读者照样能正常读诗）
│  └─ raw\           原始设备导出留底（huawei\，将来 xiaomi\）
├─ theater\     # 机器层：本应用的代码，可重写可弃
│  ├─ src\           应用（本地服务器 + 网页前端）+ 入库脚本
│  ├─ runners\       跑批（盲读任务生成、覆盖账、结果落盘）
│  ├─ personas\      读者人设清单
│  └─ NOTES.md       实现方的设计决定与理由（供作者复核）
└─ results\     # 产出层：阅读记录，随时间累积、永不覆盖
   └─ reads\         reads.jsonl（append-only）
```

- **corpus** 是作者的：改一首诗、设私密、剪自注，都只动这里；`content_hash` 变了，旧评论自动标「读的是旧版」，不删。
- **theater** 是机器的：坏了可以整个删掉重写，corpus 与 results 毫发无损。
- **results** 是时间的：一条阅读记录 = 某读者读某诗的一次真实反应，永不覆盖、永不丢出处（model + transport 必记）。

## 怎么用

**第一次用、corpus 还是空的**：直接让 agent “帮我整理并导入这些作品”。它应先检查素材，再让你确认清理结果，最后写入 `corpus/诗稿.json`。如果你要自己维护数据，再看 `01_corpus_schema.md` 的字段格式。`corpus/`、`results/` 默认不存在，首次使用时由 agent 或你创建。

- **设备云笔记已经导出过 JSON**：参照 `theater/src/build_corpus_huawei.py`、`theater/src/merge_corpus_xiaomi.py` 的模式给自己的来源写一份转换脚本——这两个文件是历史脚本、设备专属，不能直接跑，照抄模式就好。
- **本地一堆 Word/txt/表格之类的文件，没导出过**：让支持项目 skill 的工具运行 `import-corpus`；Codex 读取 `.agents/skills/`，Claude Code 读取 `.claude/skills/`。其他工具也可参照同名文档执行。
- **作品还在别的云笔记里，尚未导出**：先使用该服务官方提供的导出功能；没有官方导出时，再让 agent 根据具体服务评估可行且符合服务条款的迁移方法。登录信息、令牌和原始导出不得提交到公开仓库。导出后仍需经过 `import-corpus` 的清理与确认，不会自动进入 corpus。

1. 启动应用：`python theater/src/server.py`，浏览器开 http://localhost:8737 —— corpus 为空也能正常打开，只是榜单/时间轴是空的。集名、页脚句、默认落地页、评分口径、端口、派发默认模型这些"可以换成你自己的"，都在顶栏「设置」里改（存 `corpus/settings.json` 侧车，清空某项即恢复默认；派发 agent 读的也是这一份）。想让 AI 也读散文/小说/剧本这类非诗文体：在「设置 · 阅读文体」里勾选、可附一两句你自己的评判要求——读者会带着"体裁转换"提示按该文体的判据读（诗永远在读者池，草稿永远不读）。读者人设也可持久化：随附的那批读者随更新走，你自己新增或改写的读者写进 `corpus/personas.json` 侧车（`git pull` 永不覆盖）。最顺手的路是在「读者」页里直接点——底部「你的读者」区「＋ 新建读者」，或点进任何一位读者「编辑 / 撤下 / 还原」；也可手改侧车文件（复制 `theater/personas/personas.sidecar.example.json` 起步；同 persona_id 只覆盖你改的字段，加 `"hidden": true` 可撤下某个随附读者）。
2. 推进一轮盲读（“加厚”覆盖）：对 Codex 或 Claude Code 说「跑一轮盲读」即可触发各自原生 `dispatch-reads` skill；AGY、CodeBuddy 等外部 CLI 也可按同一批次协议逐 task 独立执行。系统会按覆盖账补最薄的 (诗 × 读者) 组合。
3. 盲读之外还有两个轻模式，都不评分、永不进榜单/校准：**跟帖**（`dispatch-thread`，对长评逐轮接楼）与**点赞**（`dispatch-votes`，对评论投认同/不认同/跳过）。Codex 配置在 `.agents/skills/` + `.codex/agents/`，Claude 配置在 `.claude/`；两套都是正式支持层，不互相取代。
4. 进度看根目录 `PROGRESS.md`；实现方的设计决定看 `theater/NOTES.md`。

## 在手机上看

设置页可以临时开启只读手机入口：家中同一 Wi-Fi 直接扫码，不上传公网；可选用 Tailscale 私密 HTTPS 实现异地访问和安装 PWA；也可以导出一个完全离线的单 HTML 留影。手机自己的收藏、阅读足迹和私人随记不会被电脑下一次快照覆盖。

第一次使用请看 [MOBILE_ACCESS.md](MOBILE_ACCESS.md)，里面有三条路线的逐步教程、隐私权衡和常见问题。

## AI 工具入口

- `AGENTS.md` 是工具中立的项目共同规则，也是 Codex 的项目入口。
- `CLAUDE.md` 是 Claude Code 入口，并导入同一份共同规则。
- `.agents/skills/` / `.codex/agents/` 是 Codex 原生适配；`.claude/` 是 Claude Code 原生适配。
- Antigravity 与 CodeBuddy 都能读取根目录 `AGENTS.md`，可担任主代理；也可作为外部执行通道，按一 task 一独立上下文运行。并发数由当前运行时和额度决定，项目不写死。

版本与更新页同时支持 Git clone 和 ZIP 安装。Git 只做 `pull --ff-only`；ZIP 只从固定公开仓库更新发行允许清单，覆盖前备份，永不触碰 corpus、results、个人设置与批次回执。v1.7.1 起可选 30 天可信手机连接签，掌中页会显示更新差异，并把手机记录备份下载到手机本地。v1.7.2 起统计页有读者回声簿，逐条评论可并列看原始分与校准分，作者可落可撤回藏印。

## 文档

- **普通使用只需看**：本页；要在手机阅读时再看 `MOBILE_ACCESS.md`。
- **交给 AI 搭建/维护**：`AGENTS.md`、`CLAUDE.md` 与 `00_START_HERE.md`–`05_run_modes.md`，它们是运行这个“AI 帮用户使用项目”模式所需的规格，不是中间产物。
- **维护者记录**：`PROGRESS.md`、`theater/NOTES.md` 与 `theater/release/`。普通用户不必阅读；它们用于多工具接手、解释设计理由和防止发行漏项。

FROZEN 的部分（两张 schema、读者底线、榜单不得由 LLM 排名、content_hash 契约）不要改。
