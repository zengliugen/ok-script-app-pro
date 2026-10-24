# 版本控制项目配置（project-config）

> 本文件是 git-commit 技能的**项目侧参数**：Scope 规则、发起权取值、审查门禁参数、H1 钩子步骤。
> 机制定义见引擎侧 `.codebuddy/skills/vcs/git-commit/references/project-config.md`。

## 0. 关联配置

| 配置项 | 逻辑名 | 项目侧路径 | 说明 |
|--------|--------|-----------|------|
| 提交摘要参数 | `git_commit.config` | `.codebuddy.project/configs/skills/vcs/git-commit/config.json` | type 枚举、subject/scope 长度上限、subject 语言 |
| Python 检查参数 | `python_lint.config` | `.codebuddy.project/configs/skills/python/python-lint/config.json` | ruff 规则集、行宽、docstring 风格、检查范围（H1 与清单收集的唯一口径来源） |
| Python 覆盖率参数 | `python_coverage.config` | `.codebuddy.project/configs/skills/python/python-test-coverage/config.json` | 测试框架、测试根、覆盖率分母（不在门禁链路上，供覆盖率技能使用） |

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
| `collect_cmd` | `[".codebuddy.project/envs/python/Scripts/python.exe", ".codebuddy/lib/python/skills/python-lint/scripts/collect_targets.py"]` |
| `enabled` | `false` |
| `file_extensions` | 空（由 `collect_cmd` 接管） |
| `include_patterns` | 空（由 `collect_cmd` 接管） |
| `exclude_patterns` | 空（由 `collect_cmd` 接管） |
| `review_skill` | `python-code-review` |
| `rules_dir` | `.codebuddy/rules/python/python-code/` |
| `report_dir` | `.codebuddy.project/caches/skills/python/python-code-review/check_logs/` |
| `blocking_level` | `error` |

**清单来源决定哪些参数生效**（引擎 §2.3）：`collect_cmd` 非空时，清单完全由该命令产出，
它自行处理未跟踪文件、扩展名与排除规则，上表三个过滤参数（`file_extensions` /
`include_patterns` / `exclude_patterns`）**不再参与**；`collect_cmd` 为空时才按
「扩展名 → 白名单 → 黑名单」由 LLM 自行筛选。

本项目**采用命令产出**，因此检查范围的口径只在
`.codebuddy.project/configs/skills/python/python-lint/config.json`（索引 `python_lint.config`）
**一处维护**，门禁侧三个参数置空——避免同一套范围在两处重复维护而漂移。

> 当前白名单为 `extensions/**/*.py` 与 `extensions_tests/**/*.py`，**不带 `**/` 前缀**。
> 带前缀会误匹配 `src/extensions/`、`src/tasks/extensions/` 这类任意层级的同名目录（已实测）。
> 收集脚本会把 `git status` 的仓库相对路径换算为项目根相对路径后再匹配，无需为抵销仓库前缀补 `**/`。

**关闭原因**：`enabled` 仍为 `false`——审查维度需要 Agent 参与（语义判断），
当前 Python 审查能力已具备（技能与 Agent 均在），但尚未在真实提交上验证稳定性。
启用方式：把 `enabled` 改为 `true` 即可，其余参数已就位。

关闭门禁**不豁免**阶段六的安全检查（敏感信息、`.gitignore` 交叉比对）与暂存区校验。

## 4. H1 钩子步骤

当前启用 1 个 H1 步骤：Python 静态检查（格式 + 规则），提交前阻断违规。

| enabled | name | description | commands | blocking |
|---------|------|-------------|----------|----------|
| true | Python 静态检查 | 按 `python_lint.config` 的规则集与检查范围，对**本次变更**的 Python 文件依次执行：① 自动修 import 排序（仅记录）② ruff format 格式检查（阻塞）③ ruff check 规则检查（阻塞）。范围由收集脚本按白名单过滤；工具环境缺失时退出 3，提示先跑 `python-env` 的 `ensure`，不自动安装 | `[".codebuddy.project/envs/python/Scripts/python.exe", ".codebuddy/lib/python/skills/python-lint/scripts/run_ruff.py", "check", "--changed", "--blocking"]` | true |

**为何只写一条命令**：三档分级（`I` 自动修、`F`/`D` 阻塞、`E501` 忽略）收在
`run_ruff.py` 的 `--blocking` 内。单条命令的退出码表达不了三档；若拆成三条门禁命令，
「先修 import 再检查」的执行顺序会成为隐式契约，且参数要在门禁配置里再写一份。

**退出码语义**：`0` 通过；`1` 有违规（含格式漂移）；`3` 环境或配置不可用。
`3` 与 `1` 分离，便于区分「环境没装好」与「代码有问题」。

**该步骤会改写工作区文件**（第 ① 步的 `--fix`）。H1 位于暂存前，修复结果随正常暂存流程
进入提交，无需「修复后重新 `git add` 并重跑 `verify_staged.py`」。

**前置条件**：首次使用前须运行一次 `python .codebuddy/lib/python/skills/python-env/scripts/env.py ensure`
创建工具环境（约 3.6 秒、28MB、可离线）。环境缺失时本步骤退出 3，不会静默跳过。

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
