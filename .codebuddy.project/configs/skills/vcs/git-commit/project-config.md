# 版本控制项目配置（project-config）

> 本文件是 git-commit 技能的**项目侧参数**：Scope 规则、发起权取值、审查门禁参数、H1 钩子步骤。
> 机制定义见引擎侧 `.codebuddy/skills/vcs/git-commit/references/project-config.md`。

## 0. 关联配置

| 配置项 | 逻辑名 | 项目侧路径 | 说明 |
|--------|--------|-----------|------|
| 提交摘要参数 | `git_commit.config` | `.codebuddy.project/configs/skills/vcs/git-commit/config.json` | type 枚举、subject/scope 长度上限、subject 语言 |

提交 type 枚举沿用通用集合（`feat`/`fix`/`docs`/`style`/`refactor`/`perf`/`test`/`chore`/`ci`/`revert`），
subject 上限 50 字符、scope 上限 40 字符，subject 语言为简体中文。

## 1. Scope 规则（项目路径 → scope 映射）

本项目为 Python 桌面应用项目（ok-script 框架），无程序集/包分层，scope 按**顶层目录归属**确定。
同一笔提交涉及多个 scope 时，取影响最大的一个写入 scope，其余在提交正文中说明。

| 变更路径模式 | scope | 说明 |
|-------------|-------|------|
| `src/tasks/**` | `tasks` | 任务实现（`MyBaseTask`、`MyOneTimeTask`、`MyTriggerTask` 等） |
| `src/ui/**` | `ui` | 自定义界面（`MyTab` 等基于 CustomTab 的页面） |
| `src/config.py`、`src/globals.py` | `core` | 应用配置与全局状态 |
| `src/**`（其余） | `src` | `src/` 下的其他模块 |
| `tests/**` | `test` | 单元测试 |
| `docs/**`、`mkdocs.yml` | `docs` | 文档与文档站点配置 |
| `i18n/**` | `i18n` | 多语言资源 |
| `.github/workflows/**` | `ci` | GitHub Actions 工作流 |
| `main.py`、`main_debug.py`、`web_main.py`、`web_main_debug.py` | `entry` | 应用入口脚本 |
| `pyproject.toml`、`requirements*.txt` | `deps` | 依赖声明与锁定文件 |
| `pyappify.yml`、`deploy.txt`、`icons/**`、`assets/**` | `packaging` | 打包、部署与随包资源 |
| `.codebuddy.project/**` | `codebuddy` | 引擎项目侧配置 |
| 其他（含仓库根零散文件） | 省略 scope | 无明确归属时不写 scope |

补充约定：

- 新增顶层目录时，应先在本表登记映射，再提交包含该目录的变更。
- `.codebuddy/` 是独立 submodule（引擎仓库），其变更**不属于本项目仓库的 scope 体系**，
  须在引擎仓库内提交，不套用本表。
- `.codebuddy.project/` 属项目仓库，按上表映射为 `codebuddy`。

## 2. 提交 / 推送的发起权（参数）

| 参数 | 取值 | 说明 |
|------|------|------|
| `commit_initiation` | `llm_allowed` | 允许 LLM 在合适时机（如完成一轮改动后）自行发起提交 |
| `push_initiation` | `user_only` | 推送必须由用户显式下达指令，LLM 不得自动推送 |

即便 `commit_initiation=llm_allowed`，以下硬约束仍全部保留：提交与推送严格分离（禁止
`git add && git commit && git push` 链式组合）、提交前敏感信息检查、`.gitignore` 交叉比对、
VCS 互斥锁、暂存区校验、计划外文件核对。

## 3. 提交前审查门禁（参数）

| 参数 | 取值 |
|------|------|
| `enabled` | `false` |
| `file_patterns` | `*.py` |
| `exclude_patterns` | `.venv/`、`__pycache__/`、`site/`、`build/`、`dist/` |
| `review_skill` | 空（当前无 Python 代码审查技能） |
| `rules_dir` | 空 |
| `report_dir` | 空 |
| `blocking_level` | `error` |

**关闭原因与后续启用条件**：本项目为 Python 项目，引擎侧的 `csharp-code-review` 仅面向 C#，
不适用；当前未安装任何 Python 代码审查技能，因此门禁关闭。后续接入 Python 审查技能后，
把 `enabled` 改为 `true` 并填写 `review_skill`/`rules_dir`/`report_dir` 即可恢复门禁。

关闭门禁**不豁免**阶段六的安全检查（敏感信息、`.gitignore` 交叉比对）与暂存区校验。

## 4. H1 钩子步骤

当前**不启用任何 H1 步骤**：表内为空，暂存前不执行额外动作，直接进入阶段六安全检查。

| enabled | name | description | blocking |
|---------|------|-------------|----------|
| false | — | — | — |

后续如需启用，按引擎侧字段语义（`enabled`/`name`/`description`/`blocking`）在本表追加行，
并按表内顺序执行。可选步骤示例：`python -m unittest`（`blocking=true`）、
`ruff format --check`（需先在项目内引入 ruff）。

## 5. 项目专有检查说明

- **运行时目录不入库**：`configs/`、`cache/`、`logs/`、`screenshots/`、`ok_tasks/`、
  `ok_templates/`、`site/`、`.venv/` 已被 `.gitignore` 忽略（`git check-ignore` 实测确认），
  提交前核对计划外文件时无需再将这些目录列入白名单。
- **依赖文件为生成物**：`requirements.txt` 与 `requirements-web.txt` 是 pip 编译锁定文件，
  由 `pyproject.toml` 的直接依赖生成。**修改依赖应改 `pyproject.toml` 后重新编译锁定文件**，
  不得直接手改锁定文件内容。
- **版本号由打包流程改写**：`src/config.py` 中的 `version` 固定为 `dev`，
  GitHub Actions 打包时自动改写，提交时不要手动改动。
- **文件编码**：全项目文本文件统一 UTF-8 无 BOM，换行遵循 `.gitattributes`/CI 的 `core.eol=lf` 约定。
