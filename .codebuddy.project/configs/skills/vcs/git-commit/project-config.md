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
| `tests/**` | `test` | 单元测试（上游基线） |
| `extensions/**` | `extensions` | 项目自有运行时代码（扩展能力） |
| `extensions_tests/**` | `test` | 项目扩展测试 |
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
| `file_extensions` | `.py` |
| `include_patterns` | 空（黑名单模式：仅按 `exclude_patterns` 排除，新目录自动纳入） |
| `exclude_patterns` | `.venv/`、`__pycache__/`、`site/`、`build/`、`dist/` |
| `review_skill` | 空（当前无 Python 代码审查技能） |
| `rules_dir` | 空 |
| `report_dir` | 空 |
| `collect_cmd` | 空（未配置时由 LLM 按 `git diff` + `git ls-files --others` 自行收集清单） |
| `blocking_level` | `error` |

过滤顺序为「扩展名 → 白名单（可选）→ 黑名单」。`file_extensions` 是门禁的固有边界，始终生效；
`include_patterns` 非空即切到**白名单模式（默认拒绝）**，此时新增代码目录必须同步补充白名单，否则静默漏审；
留空则退回**黑名单模式（默认放行）**。本项目选择黑名单模式，避免新增目录漏审的维护成本。

**关闭原因与后续启用条件**：本项目为 Python 项目，引擎侧的 `csharp-code-review` 仅面向 C#，
不适用；当前未安装任何 Python 代码审查技能，因此门禁关闭。后续接入 Python 审查技能后，
把 `enabled` 改为 `true` 并填写 `review_skill`/`rules_dir`/`report_dir` 即可恢复门禁。

关闭门禁**不豁免**阶段六的安全检查（敏感信息、`.gitignore` 交叉比对）与暂存区校验。

## 4. H1 钩子步骤

当前**不启用任何 H1 步骤**：表内为空，暂存前不执行额外动作，直接进入阶段六安全检查。

| enabled | name | description | commands | blocking |
|---------|------|-------------|----------|----------|
| false | — | — | — | — |

字段语义（引擎只定义执行规则，不内置任何步骤）：

- `description`：步骤**做什么**的完整说明。引擎不解释、不补齐；调用哪个技能、检查什么，全由这里给出。
- `commands`：可执行命令清单，**逐条执行并以退出码判定**（0 通过，非 0 按 `blocking` 处理）。
  留空表示该步骤为纯人工核对项，由 LLM 按 `description` 核对后判定。
- `blocking`：`true` 失败即中止并询问用户；`false` 失败仅告警继续。

按表内顺序逐行处理，`enabled=false` 跳过。后续如需启用，在本表追加行即可。
可选步骤示例：`python -m unittest`（`commands` 填该命令，`blocking=true`）、
`ruff format --check`（需先在项目内引入 ruff）。

**为何 H1 位于暂存前**：任何会改写工作区文件的步骤（如格式化修复）置于暂存前时，
修复结果随正常暂存流程进入提交，无需「修复后重新 `git add` 并重跑 `verify_staged.py`」；
且 H1 落在 VCS 锁窗口之外，不占用互斥锁。

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
