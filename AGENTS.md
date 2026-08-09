# 昼青集：项目级协作说明

本仓库同时支持 Codex、Claude Code、Antigravity（AGY）与 CodeBuddy。项目规则以本文为共同
基线；各工具只在自己的原生配置目录里补充“如何执行”，不得改写数据语义。

## 零基础用户接待协议

用户不需要懂 Git、Python、JSON、命令行或本项目术语。用户第一次打开仓库，或只说
“带我开始”“怎么用”“你帮我看看”时，主代理必须先替用户完成只读检查，再像老师一样
说明现状和下一步；不要先把技术文档、命令或一串问题丢给用户。

1. 自动检查版本、Python 可用性、`corpus/诗稿.json` 是否存在及作品数、已有盲读/投票
   数据、应用能否启动，以及当前是 Git clone 还是 ZIP 安装。能从文件和环境查到的信息
   不反问用户。
2. 用日常中文给出结论，并提供 3–5 个具体选项，推荐项放第一。通常从“整理并导入作品”
   “打开读诗剧场”“跑一小轮盲读”“讨论一条长评”“检查评论质量”中按现状选择。
3. 用户选目标后，代理代为执行；只有涉及模型额度消耗、导入/改动 corpus、公开发布、
   推送或改写 Git 历史等有成本或难以撤销的动作，才先解释影响并确认。
4. 用户说“不知道”或“你决定”时，选择最小、最安全、可回滚且能产生可见结果的一步。
   不因为用户不懂技术而降低数据校验标准。
5. 每次完成后说明“做成了什么、有没有真实验证、接下来可选什么”。环境就绪不等于盲读、
   入库或更新已经成功，不得混为一谈。

如果用户明确要维护代码，再进入下面的工程文档；普通使用不要求用户先读完这些文件。

## 先读什么

1. `00_START_HERE.md`：项目入口与边界。
2. `01_corpus_schema.md`：作品数据的唯一字段定义。
3. `03_runner_and_coverage.md`：盲读记录、覆盖和入库规则。
4. 涉及跟帖、投票或 UI 时，再读 `05_run_modes.md`、`04_app_and_design.md`。
5. 接续工程前看 `PROGRESS.md`；历史设计理由在 `theater/NOTES.md`。

## 不可破坏的边界

- `corpus/诗稿.json` 是作者作品；不得自动润色、删改或重排正文。
- `results/reads/reads.jsonl` 与 `results/votes/votes.jsonl` 是 append-only 账本。
  只能通过 runner 的 collect/ingest 路径追加；纠错使用 sidecar void 标记。
- LLM 只产出独立读者反应，不负责跨作品排名。排名、校准、统计均由代码推导。
- 一份 task 对应一个独立模型上下文。不得让一个上下文批量扮演多位读者。
- 批次目录是可重建中间产物，不提交；公开 release 不得包含私人 corpus、results、
  批次回执、本机绝对路径、设备信息或其他身份线索。
- 写数据后运行 `python theater/runners/audit_data.py`；改 runner/server 后运行
  `powershell -ExecutionPolicy Bypass -File theater/check.ps1`。

## 多工具适配

- Codex：共享 skills 放在 `.agents/skills/`，角色定义在 `.codex/agents/`。
- Claude Code：入口是 `CLAUDE.md`，角色与 skills 保留在 `.claude/`。
- Antigravity：读取根目录 `AGENTS.md`，并原生识别 `.agents/skills/`；既可担任主代理，
  也可作为批量执行通道。
- CodeBuddy：本仓库不放 `CODEBUDDY.md`，因此由它兼容读取根目录 `AGENTS.md`，避免
  两份共同规则漂移；既可担任主代理，也可按 runner 协议作为批量执行通道。
- 作为批量执行通道时，每个 task 启动一个新上下文或进程，传入完整 prompt，优先使用
  当前工具的 JSON schema/JSON 输出能力，并记录真实底层模型 ID。
- 不在文档里写死并发数。按当前运行时可用槽位和额度分波；主代理必须保留能力做
  质检与 collect。大批量任务优先用可创建大量独立无状态进程的通道。
- transport 记录执行通道（如 `codex-subagent`、`cc-subagent`、`agy-subagent`、
  `codebuddy-cli`）；reader.model 记录真实模型 ID，不能填工具名。

## 实施习惯

- 先做只读审计，再修改；保留用户已有的未提交改动。
- 数据写入采用“完整校验后整批提交”；重试必须幂等，冲突必须显式拒收。
- 公开 release 使用允许清单同步，分别维护脱敏文档；禁止把私人开发仓整树复制过去。
- 未经作者确认，不扩大盲读/投票批次，不替作者做编辑性取舍。

## 正式发布

- 公开仓库的 `main` 是 ZIP 更新器使用的稳定发行通道，不在上面直接开发。
- 发布前先更新 `VERSION`，运行 `theater/check.ps1`，同步允许清单并完成当前文件树和
  可达历史的脱敏扫描；全部通过后才能推送公开 `main`。
- 为已推送的发布提交创建与 `VERSION` 完全一致的 annotated tag，例如 `v1.6`。
  tag 推送后由 `.github/workflows/release.yml` 再次检查并创建 GitHub Release。
- 已发布 tag 不移动、不复用。后续修复使用新版本号和新 tag；历史改写只用于发布前的
  隐私清理，不再作为普通发布步骤。
