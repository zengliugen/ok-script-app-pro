# 版本控制项目配置（SVN project-config）

> **本文件为占位骨架**：引擎提供 `vcs-svn-commit` 能力，但**本项目当前没有 SVN 工作副本**
> （`svn info` 在项目根返回 E155007，全仓无 `.svn` 目录），因此这里不填写具体取值。
> 各节保留**填写指引**，接入 SVN 时按实际约定补齐即可。
>
> 机制定义见引擎侧 `.codebuddy/skills/vcs/vcs-svn-commit/references/project-config.md`。
>
> **为什么留占位而不是删掉本文件**：索引 `svn_commit.project_config` 指向本路径，
> 删除会让 `core-init --status` 报索引悬空；保留则接入 SVN 时只需填内容，不必重建骨架。

## 0. 关联配置

| 配置项 | 逻辑名 | 项目侧路径 | 说明 |
|--------|--------|-----------|------|
| 提交摘要参数 | `svn_commit.config` | `.codebuddy.project/configs/skills/vcs/vcs-svn-commit/config.json` | 摘要格式 `pattern`（判定依据）、type 枚举及含义、长度上限、语言、禁用结尾字符 |
| Python 检查参数 | `python_lint.config` | `.codebuddy.project/configs/skills/python/python-lint/config.json` | ruff 规则集、行宽、docstring 风格、检查范围（H1 的唯一口径来源） |

`config.json` 当前为占位值，**校验脚本遇到占位会按配置缺失处理（退出码 3）**，不会把「未配置」
误判成「摘要格式写错」。接入 SVN 时先填 `format` 与 `pattern`（语义等价），再换掉 `types`。

## 1. Scope 规则（项目路径 → scope 映射）

<接入 SVN 时填写：项目路径模式 → scope 的映射表，路径按项目根相对书写。>

需要说明的边界：

- 目录级变更（`A` / `D` 整个目录）按哪个文件的路径取 scope；
- 同一笔涉及多个 scope 时的取舍规则；
- 无匹配路径时的兜底（省略 scope 还是归入某个通用值）。

> 参考：Git 侧同类映射见 `.codebuddy.project/configs/skills/vcs/vcs-git-commit/project-config.md` §1。
> **不要直接照搬**——SVN 工作副本通常只覆盖项目的一部分目录，映射范围需按实际边界裁剪。

## 2. 提交的发起权（参数）

| 参数 | 取值 | 说明 |
|------|------|------|
| `commit_initiation` | `<user_only / llm_allowed>` | 提交（`svn commit`）的发起权 |
| `sync_initiation` | `<user_only / llm_allowed>` | 远端同步（`svn update` / `svn switch`）的发起权 |

<接入 SVN 时确定取值。参考判据：`svn commit` 直连远端、无 amend、回退需反向提交，
误提交的影响面比 Git 本地提交大，通常取 `user_only`。>

## 3. 提交前审查门禁（参数）

<接入 SVN 时填写：`enabled` / `collect_cmd` / `file_extensions` / `include_patterns` /
`exclude_patterns` / `review_skill` / `rules_dir` / `report_dir` / `blocking_level`。>

注意：Git 侧的门禁收集脚本基于 `git status`，在 SVN 工作副本上无效，
`collect_cmd` 需另配或留空改由 LLM 自行筛选。

## 4. H1 钩子步骤

<接入 SVN 时填写：步骤表（enabled / name / description / commands / blocking）。>

字段语义见引擎侧 `references/project-config.md` §3。

## 5. 项目专有检查说明

<接入 SVN 时填写：工作副本边界（`svn info --show-item wc-root`）、临时文件目录、
编码约定等本项目特有的事项。>
