# project-ok-script-tasks 变更记录

> **本技能由仓库内 `.agents/skills/ok-script-tasks` 迁移而来，归本项目维护。**
> 来源内容为英文，本次迁移做了中文化与引擎规范对齐改造；上游更新需对照来源目录手工同步，
> 具体来源版本见下表。

## 记录规范

- 一个变更一节，**按时间倒序**，标题为 `## YYYY-MM-DD 主题`。
- 每节写明**为什么改**，而不只是改了什么——只记措辞调整的不写。
- 行为变更（判定逻辑、产出物、触发条件）必须记录；纯文档润色不单独成节。

## 来源版本

| 项 | 值 |
|---|---|
| 来源路径 | `.agents/skills/ok-script-tasks` |
| 来源 `SKILL.md` SHA-256 前缀 | `2902589a2676` |
| 来源最后提交 | `e2ad0510a56aa79287624c3268a6d63160af63d7`（2026-08-10 添加项目文档） |
| 迁移日期 | 2026-10-30 |

**如何跟进来源更新**：重算来源 `SKILL.md` 的 SHA-256 前缀，与本表不一致即说明来源已变。
此时对照来源正文逐节比对，把新增/修改的规则并入本目录，再更新本表。
**不要**直接用来源文件覆盖本目录——中文正文与 `references/` 的拆分是本地改造，覆盖会丢失。

## 2026-10-30 从 .agents/skills 迁移并按引擎规范改造

| 项 | 内容 |
|---|---|
| 改动 | 目录由 `.agents/skills/ok-script-tasks` 迁到 `.codebuddy.project/skills/project/project-ok-script/project-ok-script-tasks`，技能名加 `project-` 前缀 |
| 为什么改 | `.agents/skills` 是另一套技能约定（`agents/openai.yaml` 适配层），不参与本项目的引擎校验；项目侧技能必须 `project-` 前缀才能通过 `check_manifest.py` 项 10 |
| 正文 | 全文中文化；`SKILL.md` 从 55 行英文重写为「定位 / 核心契约 / 工作流 / 必备规则 / 双语 / 不适用范围 / 已知局限」结构，删去与本项目无关的通用套话 |
| 参考文档 | 原正文里的 API 细节拆到 `references/task-api.md`，模板拆到 `references/templates.md`，`SKILL.md` 只留路由 |
| 新增 | `agents/interface.yaml`（`manual` + 关键词触发）、`manifest.json`、`evals/`、`reports/`（风险画像与 skill-ir）、`README.md`、本文件 |
| 删除 | `agents/openai.yaml`——本项目用 `agents/interface.yaml` 承载平台适配元数据，两套并存会出现两份触发描述 |
| 边界 | 明确与 codegen / i18n / release 三个技能的产出物分工，避免同一件事写两处 |
| 验证 | 四个技能名均为 `project-ok-script-` 前缀；`interface.yaml` 无 `paths`、`mode: manual`；`manifest.json` 与 `skill-ir.json` 的 `name` 与目录名一致 |
