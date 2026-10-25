# 本仓库对上游的修改说明

> 本页是**修改面的权威说明**。仓库根目录的 `UPSTREAM_CHANGES.md` 是摘要入口，只列轮廓并指向本页；
> 新增或调整修改项时改这一份即可。

本仓库 fork 自 [ok-oldking/ok-script-app](https://github.com/ok-oldking/ok-script-app)，通过
`git merge upstream/master` 跟进上游更新。本文件说明**相对于上游，本项目改了什么、为什么改、
怎样防止改动继续扩大**。

配套的机器可校验清单在 `.codebuddy.project/configs/upstream/divergence.yaml`，本文件是它的
人类可读版本。两者不一致时以清单为准，并修正本文件。

## 一句话原则

**能扩展就不修改。** 新增能力一律写在 `extensions/`（上游不存在的目录），只有「不改就做不到」
的文件才动，且必须登记。

判据只有一条：**这个文件在 `git merge upstream/master` 时会不会产生冲突**？会才需要登记。

## 当前修改面

相对上游锚点 `f134d07`，共 **22 个文件被修改、0 个被删除**，全部为 `M`（修改）状态。
按性质分三类：

| 类别 | 数量 | 含义 |
|---|---|---|
| 接线点 | 1 | 扩展生效的唯一入口，改动只有一行 |
| 构建与发布必需 | 8 | 不改就无法以本项目名义构建/发布 |
| 文档与站点 | 13 | 项目名、仓库地址、链接对齐 |

### 一、接线点（1 个）

唯一真正侵入代码逻辑的文件。

| 文件 | 改动 |
|---|---|
| `src/config.py` | 末尾追加一行 `from extensions import bootstrap; bootstrap.apply(config)` |

这一行是项目扩展生效的**唯一接线点**，位于 `config` 字典构造完成之后、`ok.OK(config)` 之前——
再晚就没有机会了（`OK.__init__` 原地消费该 dict，不做深合并）。

**该文件相对上游只允许这一行差异。** 任何其他 `config` 改动都必须迁到
`extensions/overrides.py`，否则会持续侵蚀这一底线。

### 二、构建与发布必需（8 个）

不改就无法以「ok-script-app-pro」的身份安装与发布。

| 文件 | 改动原因 |
|---|---|
| `pyproject.toml` | 项目名改为 `ok-script-app-pro`，打包与依赖安装均依赖该名称 |
| `pyappify.yml` | 打包配置指向本项目仓库与产物名，新增 Web profile |
| `.github/workflows/build.yml` | Release 需 `contents: write`；作者改为本项目；下载链接与产物名改本项目；测试步骤复用 `run_tests.ps1` |
| `.github/workflows/docs.yml` | 文档站部署配置对齐本项目仓库 |
| `.github/workflows/mirrorchyan_release_note.yml` | Mirror酱渠道配置对齐本项目 |
| `.github/workflows/mirrorchyan_uploading.yml` | Mirror酱上传配置对齐本项目，改为按仓库变量开关 |
| `run_tests.ps1` | 上游只收集 `tests/*.py` 一层；本项目有 `tests/` 与 `extensions_tests/` 两个测试根，改为遍历多根递归收集 |
| `.gitignore` | 补充忽略运行时缓存目录 |

### 三、文档与站点（13 个）

纯文案对齐：项目名、仓库地址、链接、导航条目。不涉及代码行为。

- `mkdocs.yml`、`README.md`、`README_en.md`
- `docs/` 下 5 个中文页 + `docs/en/` 下 5 个英文页

改动内容统一为：把上游的项目名与仓库链接替换为本项目，并在导航中加入扩展开发指南条目。

## 没有修改的部分

以下内容**保持上游原样**，是本项目刻意为之：

| 内容 | 说明 |
|---|---|
| `src/tasks/`、`src/ui/`、`src/globals.py` | 上游代码区，不新增文件、不修改。项目新增代码写 `extensions/` |
| `tests/TestMain.py` | 上游行为基线测试，**不得修改**，用于判断上游行为是否被破坏 |
| `docs/tasks.md` | 上游任务开发文档，未改动 |
| `.codebuddy/` | 独立 Git 仓库（submodule），不属于上游，改动走引擎仓库 |
| `.codebuddy.project/` | 本项目自有配置，上游不存在 |
| `extensions/`、`extensions_tests/` | 本项目自有代码与测试，上游不存在 |

## 扩展代码为什么在顶层 `extensions/`

早期设计是 `src/extensions/`，现改为顶层。原因不是审美，是避免真问题：

`src/` 是命名空间包（无 `__init__.py`）。若扩展目录下再套一层 `src`（形如 `extensions/src/`），
且其父目录进入 `sys.path`，两个 `src` 会被**静默合并**成同一个包：

```
$ PYTHONPATH="extensions" python -c "import src; print(list(src.__path__))"
['...\src', '...\extensions\src']        # 合并，不报错
```

此时同名模块谁生效取决于路径顺序，且没有任何提示。`extensions/` 用常规包（每层
`__init__.py`）后 `__path__` 唯一，不存在该风险。

**禁止**把 `extensions/` 加入 `sys.path`——它是包，只能以 `extensions.xxx` 形式引用。

## 上游更新时会发生什么

| 情况 | 处置 |
|---|---|
| 改动文件有登记条目 | 预期内冲突。按条目 `reason` 判断，若该改动现在可由 `extensions/` 承接，**借机迁移掉**并删除条目 |
| 改动文件无登记条目 | 意外冲突。先停下查明原因：可能是漏登记，也可能是上游动了我们依赖的结构 |
| 冲突暴露出「这个改动本该在 `extensions/` 里」 | **迁移而非合并**。迁移后文件回归上游原样，条目删除 |

这是唯一能持续减少冲突面的做法：每次同步都让修改面变小，而不是把冲突「解决」成更多本地改动。

## 如何校验与登记

全量盘点（锚点到 HEAD 的全部差异，与清单比对）：

```bash
python .codebuddy.project/skills/project/upstream-compat-guard/scripts/upstream_compat_guard.py
```

只查工作区未提交的改动：

```bash
python .codebuddy.project/skills/project/upstream-compat-guard/scripts/upstream_compat_guard.py --changed
```

为某个上游文件生成登记条目骨架（人工补 `reason` 后写回）：

```bash
python .codebuddy.project/skills/project/upstream-compat-guard/scripts/upstream_compat_guard.py --add src/config.py
```

`--add` 指向非上游路径（`extensions/`、`.codebuddy.project/`、锚点中不存在的新增文件）时会
**拒绝**并说明该去哪，退出码 4。

退出码：`0` 通过 / `1` 有问题 / `2` git 失败 / `3` 配置缺失 / `4` 目标不属上游来源。

## 登记纪律

- **改动与登记必须同一次提交**。事后补登记等于没有登记。
- `reason` 写**动机**（为什么不能不改），不写「改了什么」——半年后读它的人需要知道原因。
- 条目失效（文件已恢复上游原样）时**删除条目**，不要留作历史记录。需要历史请查 git。
- 上游同步后必须更新 `synced_upstream_commit` 与 `updated_at`。

## 相关文档

| 文档 | 内容 |
|---|---|
| `.codebuddy.project/configs/upstream/divergence.yaml` | 差异登记清单（机器可校验，权威来源） |
| `extensions_docs/README.md` 的能力总览表 | 已实现能力清单（防重复造轮子） |
| `.codebuddy.project/rules/project/extension-capability/` | 规则正文：扩展手段、代码落位、文档体系 |
| [扩展开发指南](extension-guide.md) | 四条扩展路径与目录约定 |
| `.codebuddy.project/skills/project/upstream-compat-guard/` | 校验技能：盘点和登记的实现 |
