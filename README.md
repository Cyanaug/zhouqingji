# 昼青集 · 读诗剧场

> **硬边界**：`theater` 里的代码**读 `corpus`、写 `results`，永远不修改 `corpus` 内的作品内容**。清洗、删改、剪自注、改可见性，只由作者在 GUI 里做。

让许多 AI 读者（不同模型 × 不同读者背景）阅读 cyan 的诗，各自打分、写下反应；作者能看见「一首诗在很多眼睛里的形状」——兑现《夜路》结尾那句「由世间所有的所见将它命名」。

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

**第一次用、corpus 还是空的**：先看 `01_corpus_schema.md` 里 `诗稿.json` 的字段格式，把你自己的诗整理成同样结构的 JSON 数组存到 `corpus/诗稿.json`（`corpus/`、`results/` 默认不存在，需要你自己建）。手写几条起步即可；也可以参照 `theater/src/build_corpus_huawei.py`、`theater/src/merge_corpus_xiaomi.py` 的模式给自己的诗歌来源写一份转换脚本——这两个文件是历史脚本、设备专属，不能直接跑，照抄模式就好（记得把里面的 `author` 换成你自己的笔名）。

1. 启动应用：`python theater/src/server.py`，浏览器开 http://localhost:8737 —— corpus 为空也能正常打开，只是榜单/时间轴是空的。集名、页脚句、默认落地页、评分口径、端口、派发默认模型这些"可以换成你自己的"，都在顶栏「设置」里改（存 `corpus/settings.json` 侧车，清空某项即恢复默认；派发 agent 读的也是这一份）。想让 AI 也读散文/小说/剧本这类非诗文体：在「设置 · 阅读文体」里勾选、可附一两句你自己的评判要求——读者会带着"体裁转换"提示按该文体的判据读（诗永远在读者池，草稿永远不读）。读者人设也可持久化：随附的那批读者随更新走，你自己新增或改写的读者写进 `corpus/personas.json` 侧车（`git pull` 永不覆盖，复制 `theater/personas/personas.sidecar.example.json` 起步；同 persona_id 只覆盖你改的字段，加 `"hidden": true` 可撤下某个随附读者）。
2. 推进一轮盲读（"加厚"覆盖）：按 `.claude/skills/dispatch-reads/SKILL.md` 的流程走（用 Claude Code 就说一声「跑一轮」；用其他 AI 编程工具参考技能文档里附的通用 prompt 模板），会按覆盖账自动补最薄的 (诗 × 读者) 组合。
3. 进度看根目录 `PROGRESS.md`；实现方的设计决定看 `theater/NOTES.md`。

## 文档

`00_START_HERE.md`–`04_app_and_design.md` 是这个项目最初的架构规格书，写给负责搭建/维护它的 AI agent 看的技术设计文档，不是新手教程——想直接上手用，从上面「怎么用」开始就够了。FROZEN 的部分（两张 schema、读者底线、榜单不得由 LLM 排名、content_hash 契约）不要改。
