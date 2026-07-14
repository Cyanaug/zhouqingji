# 昼青集 · 读诗剧场

> **硬边界**：`theater` 里的代码**读 `corpus`、写 `results`，永远不修改 `corpus` 内的作品内容**。清洗、删改、剪自注、改可见性，只由作者在 GUI 里做。

让许多 AI 读者（不同模型 × 不同读者背景）阅读 cyan 的诗，各自打分、写下反应；作者能看见「一首诗在很多眼睛里的形状」——兑现《夜路》结尾那句「由世间所有的所见将它命名」。

## 三层结构

```
昼青集\
├─ corpus\      # 资产层：只进不毁、作者所有、可回滚
│  ├─ 诗稿.json      作品总集（唯一真源，作者可随时手改）
│  ├─ 昼青·诠释.md    读解档案，作者手工增删
│  └─ raw\           原始设备导出留底（按来源分子目录，如 device-a\、device-b\）
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

1. 启动应用：`python theater\src\server.py`，浏览器开 http://localhost:8737
2. 推进一轮盲读：跟 agent（CC）说一声「跑一轮」即可，它按覆盖账自动补最薄的 (诗 × 读者) 组合。
3. 进度看根目录 `PROGRESS.md`；实现方的设计决定看 `theater\NOTES.md`。

## 文档

`00_START_HERE.md` 总入口；`01`–`04` 分别是作品 schema、读者与选角、跑批与覆盖、应用与设计。FROZEN 的部分（两张 schema、读者底线、榜单不得由 LLM 排名、content_hash 契约）不要改。
