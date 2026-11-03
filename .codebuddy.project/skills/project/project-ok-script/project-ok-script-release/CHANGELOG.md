# project-ok-script-release 变更记录

> **本技能由仓库内 `.agents/skills/deploy` 迁移并改造而来，归本项目维护。**
> 来源内容为英文，且其中的提交流程与本项目的提交强制链路冲突，本次做了实质性改造；
> 上游更新需对照来源目录手工同步，具体来源版本见下表。

## 记录规范

- 一个变更一节，**按时间倒序**，标题为 `## YYYY-MM-DD 主题`。
- 每节写明**为什么改**，而不只是改了什么——只记措辞调整的不写。
- 行为变更（步骤归属、写操作范围、退出码）必须记录；纯文档润色不单独成节。

## 来源版本

| 项 | 值 |
|---|---|
| 来源路径 | `.agents/skills/deploy` |
| 来源 `SKILL.md` SHA-256 前缀 | `6bc9d369acc1` |
| 来源 `scripts/next_tag.py` | 123 行，本次重写为中文注释版本，版本判定逻辑保持一致 |
| 来源最后提交 | `e2ad0510a56aa79287624c3268a6d63160af63d7`（2026-08-10 添加项目文档） |
| 迁移日期 | 2026-10-30 |

**如何跟进来源更新**：重算来源 `SKILL.md` 的 SHA-256 前缀，与本表不一致即说明来源已变。
重点比对**版本号规则**是否变化——那是本技能唯一不宜偏离来源的部分；
提交流程的差异属本地必要改造，不要随来源回退。

## 2026-10-30 从 .agents/skills/deploy 迁移并改造为发布编排

| 项 | 内容 |
|---|---|
| 改名 | `deploy` → `project-ok-script-release`，目录迁到 `.codebuddy.project/skills/project/project-ok-script/` |
| 为什么改名 | `deploy` 是个无归属的通用词，看不出属于哪个项目、哪个领域；加 `project-ok-script-` 前缀既满足引擎项 10 的命名校验，也表明它是本项目的发布编排 |
| **实质改造一** | 来源步骤 5 直接执行 `git add` + `git commit`。本项目 `vcs-router.entry` 规则要求**所有提交必须经 `vcs-router` 链路**（含摘要格式校验、敏感信息检查、暂存区校验、VCS 互斥锁）。若保留来源写法，就会出现第二套提交流程，两套必然分叉，而分叉不报错。本次改为**提交交给 `vcs-router`**，本技能只负责顺序与版本号 |
| **实质改造二** | 推送保留在本技能。理由：`vcs-router` 的路由范围是 commit 与 merge，`tag` 与 `push` 都不在其内；而不推 tag 就不构成发布。因此推送由本技能执行，但**发起权仍受项目配置约束**（`push_initiation` 默认 `user_only`），没有用户显式指令就不推 |
| 路径修正 | 来源正文里的脚本路径是 `.agent\skills\deploy\scripts\next_tag.py`（少一个 s，且是另一套目录约定），本次改为项目侧真实路径 |
| 正文 | 全文中文化；新增「定位与边界」节把动作归属列成表；版本规则、工作流、硬约束按本项目口径重写 |
| 脚本 | `next_tag.py` 重写：全部函数加中文注释；错误消息中文化；`--tag` / `--remote` 语义与来源一致；版本判定逻辑（含「稳定版关闭同基线预发布」）逐条保留 |
| 新增 | `references/release-checklist.md`（发布前清单、tag 与 CI 触发关系、失败处置）、`security/permission_policy.json`（允许与禁止的 git 子命令）、`agents/interface.yaml`、`manifest.json`、`evals/`、`reports/`、`README.md`、本文件 |
| 删除 | `agents/openai.yaml`——与 `agents/interface.yaml` 两套适配描述会互相漂移 |
| 验证 | 脚本用四组 tag 组合实测：`release` 出 `v3.3.55`、同线 `beta` 出 `v3.3.55-beta.3`、`v3.3.55` 已发正式版后 `alpha` 出 `v3.3.56-alpha.1`、无预发布时 `beta` 出 `v3.3.55-beta.1`，与规则一致 |
