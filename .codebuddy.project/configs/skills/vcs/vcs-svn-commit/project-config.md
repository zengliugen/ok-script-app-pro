# 版本控制项目配置（SVN project-config）

> 本文件是 vcs-svn-commit 技能的**项目侧参数**：Scope 规则、发起权取值、审查门禁参数、H1 钩子步骤。
> 机制定义见引擎侧 `.codebuddy/skills/vcs/vcs-svn-commit/references/project-config.md`。
>
> **与 Git 侧的关系**：本项目主仓库是 Git 仓库，SVN 只用于与外部协作方同步的子工作副本。
> 因此这里取值与 `.codebuddy.project/configs/skills/vcs/vcs-git-commit/project-config.md` 保持一致（同一份代码、同一套检查口径），
> 但**两份文件各自独立**：调整 SVN 侧取值不影响 Git 侧，反之亦然。

## 0. 关联配置

| 配置项 | 逻辑名 | 项目侧路径 | 说明 |
|--------|--------|-----------|------|
| 提交摘要参数 | `svn_commit.config` | `.codebuddy.project/configs/skills/vcs/vcs-svn-commit/config.json` | **摘要格式 `pattern`（判定依据）**、type 枚举及含义、长度上限、语言、禁用结尾字符 |
| Python 检查参数 | `python_lint.config` | `.codebuddy.project/configs/skills/python/python-lint/config.json` | ruff 规则集、行宽、docstring 风格、检查范围（H1 的唯一口径来源） |

**摘要格式与 Git 侧不同**：Git 用 `<type>(<scope>): <subject>`，SVN 用 `[<scope>] <type>: <subject>`（scope 可省略）——
沿用团队在 Subversion 客户端里既有的写法。要改成与 Git 一致，只改 `config.json` 的 `format` 与 `pattern`，
不动引擎文件。

type 枚举沿用以 `name`+`description` 对象形式声明（与 Git 侧同一批 type，含义一致），数组顺序即决策优先级；
subject 上限 50 字符、scope 上限 40 字符，subject 语言为简体中文，禁用结尾字符为句号与分号。

## 1. Scope 规则（项目路径 → scope 映射）

按**项目根相对路径**取 scope，规则与 Git 侧同一张表（本项目为 Python 桌面应用，无程序集/包分层）：

| 变更路径模式 | scope | 说明 |
|-------------|-------|------|
| `src/tasks/**` | `tasks` | 任务实现 |
| `src/ui/**` | `ui` | 自定义界面 |
| `src/config.py`、`src/globals.py` | `core` | 应用配置与全局状态 |
| `src/**`（其余） | `src` | `src/` 下的其他模块 |
| `tests/**`、`extensions_tests/**` | `test` | 测试 |
| `extensions/**` | `extensions` | 项目自有运行时代码 |
| `extensions_docs/**`、`docs/**`、`mkdocs.yml` | `docs` | 文档与文档站点配置 |
| `i18n/**` | `i18n` | 多语言资源 |
| `.github/workflows/**` | `ci` | GitHub Actions 工作流 |
| `main*.py`、`web_main*.py` | `entry` | 应用入口脚本 |
| `pyproject.toml`、`requirements*.txt` | `deps` | 依赖声明与锁定文件 |
| `pyappify.yml`、`deploy.txt`、`icons/**`、`assets/**` | `packaging` | 打包、部署与随包资源 |
| `.codebuddy.project/**` | `codebuddy` | 引擎项目侧配置 |
| 其他 | 省略 scope | 无明确归属时不写 scope |

**SVN 特有补充**：目录级变更（`A` / `D` 整个目录）按其第一个文件的路径取 scope；
同一笔涉及多个 scope 时取影响最大的一个写入 scope，其余在正文说明。

## 2. 提交的发起权（参数）

| 参数 | 取值 | 说明 |
|------|------|------|
| `commit_initiation` | `user_only` | SVN 提交直连远端、且本项目的 SVN 工作副本用于与外部协作方同步，误提交影响他人，必须由用户显式发起 |
| `sync_initiation` | `user_only` | 提交后的 `svn update` / `svn switch` 必须由用户显式下达指令 |

**为什么 SVN 侧比 Git 侧严**：Git 侧的 `commit_initiation` 为 `llm_allowed`（本地提交可回退），
但 SVN 的 `svn commit` 一经执行即写入共享远端，且 SVN 无 amend、回退需要反向提交，
因此这里取 `user_only`。

即便 `commit_initiation=llm_allowed`，以下硬约束仍全部保留：提交与远端同步严格分离、
提交前敏感信息检查、`svn:ignore` 交叉比对、VCS 互斥锁、目标清单前后校验、计划外文件核对。

## 3. 提交前审查门禁（参数）

| 参数 | 取值 |
|------|------|
| `collect_cmd` | 空（SVN 侧无对应的收集脚本，改由 LLM 自行筛选） |
| `enabled` | `false` |
| `file_extensions` | `["py"]` |
| `include_patterns` | `[]`（空即黑名单模式） |
| `exclude_patterns` | `["**/.codebuddy/**", "**/.codebuddy.project/**"]` |
| `review_skill` | `python-code-review` |
| `rules_dir` | `.codebuddy/rules/python/python-code-review/` |
| `report_dir` | `.codebuddy.project/caches/skills/python/python-code-review/check_logs/` |
| `blocking_level` | `error` |

**为什么 `collect_cmd` 留空**：Git 侧的收集脚本基于 `git status`，在 SVN 工作副本上无效。
留空后门禁按「扩展名 → 白名单 → 黑名单」由 LLM 自行筛选，口径是
`svn status` 的已版本化改动 + 未版本化新文件（不含未纳入本笔的项）。

**与 Git 侧一样保持 `enabled=false`**：审查维度需要 Agent 参与，尚未在真实 SVN 提交上验证稳定性。
启用方式：把 `enabled` 改为 `true`。关闭门禁**不豁免**阶段六的安全检查与目标清单校验。

## 4. H1 钩子步骤

当前启用 1 个 H1 步骤，与 Git 侧同一条命令（同一份代码、同一套 lint 口径）：

| enabled | name | description | commands | blocking |
|---------|------|-------------|----------|----------|
| true | Python 静态检查 | 按 `python_lint.config` 的规则集与检查范围，对**本次变更**的 Python 文件依次执行：① 自动修 import 排序（仅记录）② ruff format 格式检查（阻塞）③ ruff check 规则检查（阻塞）。工具环境缺失时退出 3，提示先跑 `python-env` 的 `ensure`，不自动安装 | `[".codebuddy.project/envs/python/Scripts/python.exe", ".codebuddy/lib/python/skills/python-lint/scripts/run_ruff.py", "check", "--changed", "--blocking"]` | true |

**`--changed` 在 SVN 工作副本上的行为**：该开关内部调用 `git status` 取清单，
在 SVN 工作副本上会取不到文件。因此在 SVN 场景下改用显式 `--target` 传本笔文件：

```bash
.codebuddy.project/envs/python/Scripts/python.exe .codebuddy/lib/python/skills/python-lint/scripts/run_ruff.py \
  check --blocking --target <本笔 Python 文件…>
```

**退出码语义**：`0` 通过；`1` 有违规（含格式漂移）；`3` 环境或配置不可用。
**该步骤会改写工作区文件**（第 ① 步的 `--fix`），因此它必须位于**目标清单成型之后、清单校验之前**：
修复结果随后的 `svn commit` 一并提交，无需「修复后重建清单」。

字段语义（引擎只定义执行规则，不内置任何步骤）：

- `description`：步骤**做什么**的完整说明。引擎不解释、不补齐。
- `commands`：可执行命令清单，**逐条执行并以退出码判定**（0 通过，非 0 按 `blocking` 处理）。
- `blocking`：`true` 失败即中止并询问用户；`false` 失败仅告警继续。

## 5. 项目专有检查说明

- **SVN 工作副本范围**：本项目的 SVN 工作副本只覆盖与外部协作方同步的目录，
  其边界以 `svn info --show-item wc-root` 为准；不在边界内的改动不属于 SVN 提交范围。
- **临时文件目录**：提交消息文件（UTF-8）与目标清单文件（GBK）一律放系统临时目录，
  不得放工作副本内——否则会污染变更集。
- **编码约定**：与项目其余部分一致，文本文件统一 UTF-8 无 BOM；
  仅 `svn commit --targets` 的清单文件例外（须 GBK，缘由见提交技能的 `references/commit-spec.md`）。
