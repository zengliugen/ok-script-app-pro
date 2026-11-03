# project-ok-script-codegen 变更记录

> **本技能由仓库内 `.agents/skills/ok-script-codegen` 迁移而来，归本项目维护。**
> 来源内容为英文，本次迁移做了中文化与引擎规范对齐改造；上游更新需对照来源目录手工同步，
> 具体来源版本见下表。

## 记录规范

- 一个变更一节，**按时间倒序**，标题为 `## YYYY-MM-DD 主题`。
- 每节写明**为什么改**，而不只是改了什么——只记措辞调整的不写。
- 行为变更（规则增删、产出物形态、触发条件）必须记录；纯文档润色不单独成节。

## 来源版本

| 项 | 值 |
|---|---|
| 来源路径 | `.agents/skills/ok-script-codegen` |
| 来源 `SKILL.md` SHA-256 前缀 | `94cfe5454259` |
| 来源最后提交 | `e2ad0510a56aa79287624c3268a6d63160af63d7`（2026-08-10 添加项目文档） |
| 迁移日期 | 2026-10-30 |

**如何跟进来源更新**：重算来源 `SKILL.md` 的 SHA-256 前缀，与本表不一致即说明来源已变。
对照来源正文逐节比对后并入本目录，再更新本表。**不要**直接用来源文件覆盖本目录——
中文正文与 `references/` 的拆分是本地改造。

## 2026-10-30 从 .agents/skills 迁移并按引擎规范改造

| 项 | 内容 |
|---|---|
| 改动 | 目录由 `.agents/skills/ok-script-codegen` 迁到 `.codebuddy.project/skills/project/project-ok-script/project-ok-script-codegen`，技能名加 `project-` 前缀 |
| 为什么改 | `.agents/skills` 不参与本项目引擎校验；项目侧技能必须 `project-` 前缀才能通过 `check_manifest.py` 项 10 |
| 正文 | 全文中文化；规则按「响应方式 / 生成规则 / 注释要求 / 帧刷新 / OCR / 模板 / 截图推断 / 错误处理 / 自检」重排，原英文小标题逐条对齐不丢条目 |
| 参考文档 | 新增 `references/frame-and-wait.md`：把散在规则里的帧语义集中成一份带源码依据与失效对照的说明，`SKILL.md` 只留结论 |
| 新增 | `agents/interface.yaml`（`manual` + 关键词触发）、`manifest.json`、`evals/`、`reports/`、`README.md`、本文件 |
| 删除 | `agents/openai.yaml`——与 `agents/interface.yaml` 的两套适配描述会互相漂移 |
| 边界 | 明确「只产出 `run(self)` 方法体」，任务骨架与翻译分别交给 tasks / i18n 技能 |
| 事实核对 | `sleep` 清帧的说法经查 `ok/task/TaskExecutor.py:299`（`sleep` 开头调 `reset_scene`）确认，非凭印象 |
