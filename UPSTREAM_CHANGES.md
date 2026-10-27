# 本仓库对上游的修改说明

本仓库 fork 自 [ok-oldking/ok-script-app](https://github.com/ok-oldking/ok-script-app)，通过
`git merge upstream/master` 跟进上游更新。本文件是修改面的**摘要入口**，完整说明见
[docs/upstream-changes.md](docs/upstream-changes.md)。

机器可校验的权威清单是 `.codebuddy.project/configs/upstream/divergence.yaml`。

## 一句话原则

**能扩展就不修改。** 新增能力一律写在 `extensions/`（上游不存在的目录），只有「不改就做不到」
的文件才动，且必须登记到清单。

判据只有一条：**这个文件在 `git merge upstream/master` 时会不会产生冲突**？会才需要登记。

## 修改面总览

相对上游锚点 `f134d07`，共 **22 个文件被修改、0 个被删除**，全部为 `M`（修改）状态。

| 类别 | 数量 | 代表文件 | 性质 |
|---|---|---|---|
| 接线点 | 1 | `src/config.py` | 唯一侵入代码逻辑处，只有一行 |
| 构建与发布必需 | 8 | `pyproject.toml`、`pyappify.yml`、`run_tests.ps1`、4 个 workflow、`.gitignore` | 不改就无法以本项目名义构建/发布 |
| 文档与站点 | 13 | `README.md`、`README_en.md`、`mkdocs.yml`、`docs/**` | 项目名与仓库地址对齐，无代码行为 |

### 唯一的接线点

`src/config.py` 末尾追加一行：

```python
from extensions import bootstrap; bootstrap.apply(config)
```

位于 `config` 字典构造完成之后、`ok.OK(config)` 之前——再晚就没机会（`OK.__init__` 原地消费
该 dict，不做深合并）。**该文件相对上游只允许这一行差异**，其余 config 改动必须进
`extensions/overrides.py`。

### 保持上游原样的部分

| 内容 | 说明 |
|---|---|
| `src/tasks/`、`src/ui/`、`src/globals.py` | 上游代码区，不新增、不修改 |
| `tests/TestMain.py` | 上游行为基线测试，**不得修改** |
| `.codebuddy/` | 独立 Git 仓库（submodule），改动走引擎仓库 |
| `.codebuddy.project/`、`extensions/`、`extensions_tests/` | 本项目自有，上游不存在 |
| `extensions_docs/` | 本项目自有的扩展能力开发文档，上游不存在；不参与运行时与打包 |

## 为什么扩展代码在顶层 `extensions/`

不是审美，是避免真问题：`src/` 是命名空间包，若扩展目录下再套一层 `src`，且父目录进入
`sys.path`，两个 `src` 会被**静默合并**，同名模块谁生效取决于路径顺序且无任何提示。
`extensions/` 用常规包（每层 `__init__.py`）后 `__path__` 唯一，不存在该风险。
**禁止**把 `extensions/` 加入 `sys.path`。

## 校验

```bash
# 全量：锚点到 HEAD 的全部差异，与清单比对
python .codebuddy.project/skills/project/project-upstream-compat-guard/scripts/upstream_compat_guard.py

# 只查工作区未提交改动
python .codebuddy.project/skills/project/project-upstream-compat-guard/scripts/upstream_compat_guard.py --changed
```

退出码：`0` 通过 / `1` 有问题 / `2` git 失败 / `3` 配置缺失 / `4` 目标不属上游来源。

## 登记纪律

- **改动与登记同一次提交**，事后补登记等于没登记。
- `reason` 写动机（为什么不能不改），不写「改了什么」。
- 条目失效时**删除**，不留作历史记录——需要历史查 git。
- 上游同步后更新 `synced_upstream_commit` 与 `updated_at`。

## 延伸阅读

| 文档 | 内容 |
|---|---|
| [完整修改说明](docs/upstream-changes.md) | 逐文件列出改动原因、同步时的冲突处置 |
| [扩展开发指南](docs/extension-guide.md) | 四条扩展路径与目录约定 |
| `.codebuddy.project/configs/upstream/divergence.yaml` | 差异登记清单（机器可校验） |
| `.codebuddy.project/rules/project/project-extension-capability/`、`project-upstream-compat/`（各自另有 `*-guard.entry/` 入口） | 规则正文：扩展能力 / 上游兼容 |
