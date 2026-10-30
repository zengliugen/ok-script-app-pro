# project-ok-script-i18n 变更记录

> **本技能由仓库内 `.agents/skills/ok-script-i18n` 迁移而来，归本项目维护。**
> 来源内容为英文，本次迁移做了中文化与引擎规范改造；上游更新需对照来源目录手工同步，
> 具体来源版本见下表。

## 记录规范

- 一个变更一节，**按时间倒序**，标题为 `## YYYY-MM-DD 主题`。
- 每节写明**为什么改**，而不只是改了什么——只记措辞调整的不写。
- 行为变更（脚本判定、退出码、文件产出）必须记录；纯文档润色不单独成节。

## 来源版本

| 项 | 值 |
|---|---|
| 来源路径 | `.agents/skills/ok-script-i18n` |
| 来源 `SKILL.md` SHA-256 前缀 | `6902c15d4c14` |
| 来源 `scripts/task_i18n_helper.py` | 167 行，本次重写为中文注释版本，逻辑保持一致 |
| 来源最后提交 | `e2ad0510a56aa79287624c3268a6d63160af63d7`（2026-08-10 添加项目文档） |
| 迁移日期 | 2026-10-30 |

**如何跟进来源更新**：重算来源 `SKILL.md` 的 SHA-256 前缀，与本表不一致即说明来源已变。
对照来源正文与脚本逻辑逐项比对后并入本目录，再更新本表。**不要**直接用来源文件覆盖——
中文注释、退出码约定与新增的 3 号错误处理都是本地改造。

## 2026-10-30 从 .agents/skills 迁移并按引擎规范改造

| 项 | 内容 |
|---|---|
| 改动 | 目录由 `.agents/skills/ok-script-i18n` 迁到 `.codebuddy.project/skills/project/project-ok-script/project-ok-script-i18n`，技能名加 `project-` 前缀 |
| 为什么改 | `.agents/skills` 不参与本项目引擎校验；项目侧技能必须 `project-` 前缀才能通过 `check_manifest.py` 项 10 |
| 正文 | 全文中文化；脚本路径改为项目侧真实路径（来源里写的是 `.agent\skills\...`，与实际目录不符） |
| 脚本 | `task_i18n_helper.py` 重写：全部函数与关键行加中文注释；`polib` 改为延迟导入，缺失时退出码 3 并打印安装命令；文件/目录不存在与解析失败也统一退出 3；`check` 发现重复返回 1 |
| **行为变更** | 来源版本在两个子命令函数内部直接 `import polib`，缺失时会抛未捕获的 `ImportError`（非零退出但无提示）。本次改为显式报错 + 可操作提示——**翻译改了却不生效比直接报错更难发现** |
| 参考文档 | 新增 `references/catalog-rules.md`：运行时读 `.mo` 的依据、`msgid` 精确匹配的失效形态、语言回退规则、`polib` 依赖、失效现象对照表 |
| 新增 | `agents/interface.yaml`（`manual` + 关键词触发）、`manifest.json`、`evals/`、`reports/`、`README.md`、本文件 |
| 删除 | `agents/openai.yaml`——与 `agents/interface.yaml` 两套适配描述会互相漂移 |
| 依赖现状 | 本项目 `.venv` 当前**未安装** `polib`，`compile` 会返回 3；首次编译前需按提示安装 |
