---
name: upstream-compat-guard
description: 盘点本项目相对上游 ok-script-app 的代码差异、校验差异是否都已登记到 divergence.yaml、辅助生成登记条目。用户说"上游差异"、"检查有没有未登记的改动"、"盘点上游改动"、"登记这个改动"、"同步前检查"、"能不能跟进上游"时使用。也用于判断某个改动到底算不算上游差异（引擎仓库 .codebuddy 与 .codebuddy.project 的改动不属于上游）。
---

# 上游差异守护

本项目 fork 自上游 `ok-oldking/ok-script-app`，靠 `git merge upstream/master` 跟进更新。
上游文件改得越多，合并冲突越难解，最终会失去跟进能力。本技能守住这条线：
**盘点**改过哪些上游文件、**校验**是否都登记、**登记**某个改动。

## 先确认归属（最容易错的一步）

工作区里有三类路径，只有第三类需要登记：

| 路径 | 归属 | 要登记吗 |
|---|---|---|
| `.codebuddy/**` | **引擎仓库**（submodule，独立 Git 仓库） | **否** |
| `.codebuddy.project/**` | 项目仓库中的**项目自有目录** | **否** |
| `src/**`、`pyproject.toml` 等上游来源文件 | 项目仓库 | **是** |

改完引擎后跑本脚本"什么都没提示"，不代表引擎属于上游——那是「不归本技能管」。
判据：**这个文件在 `git merge upstream/master` 时会不会冲突**？不会就不登记。
完整判定顺序、脚本表现与自查方法见 `references/ownership-rules.md`。

## 怎么用

在项目根目录执行：

```bash
# 全量盘点（默认）：锚点 → HEAD 的全部差异，与登记清单比对
python .codebuddy.project/skills/project/upstream-compat-guard/scripts/upstream_compat_guard.py

# 只检查当前工作区尚未提交的变更
python .codebuddy.project/skills/project/upstream-compat-guard/scripts/upstream_compat_guard.py --changed

# 为某个上游文件生成登记条目骨架（人工补 reason 后写回）
python .codebuddy.project/skills/project/upstream-compat-guard/scripts/upstream_compat_guard.py --add src/config.py
```

`--add` 指向非上游路径（引擎、项目自有目录、锚点中不存在的新增文件）时会**拒绝**并说明该去哪，
退出码 4。四种输出标记（`[未登记]` / `[已失效]` / `[不一致]` / `[告警]`）的判定条件、
处置动作与全部退出码见 `references/output-interpretation.md`。

## 校验基准

以 `divergence.yaml` 的 `synced_upstream_commit` 为唯一基准，不取自远端分支——离线可用、
结果可复现，不会因某次 `git fetch` 让判定漂移。基准语义与配置定位见 `references/check-basis.md`。

## 不适用范围（do not use / out of scope）

以下请求**不要**调用本技能：

| 请求 | 该去哪 |
|---|---|
| 执行 `git fetch` / `git merge upstream/master` | 规则 `upstream-sync.mdc` 的人工流程 |
| 在引擎仓库**提交**改动 | 规则 `vcs-engine-commit`（只治引擎侧；含 submodule / 独立检出两种形态的提交顺序） |
| 判断「这个上游改动该不该改」 | 人读 diff 判断；本技能只发现与报告 |
| 直接改写 `divergence.yaml` 的 reason | 拒绝——自动推断的原因半年后毫无价值 |
| 校验上游行为是否仍正确 | 跑 `tests/` 下的上游基线测试（`run_tests.ps1` 的 `extensions_tests/` 部分与此无关）；本技能只保证「登记完整」 |

环境依赖与降级表现（缺 PyYAML、非 git 仓库、锚点失效）见 `references/boundaries.md`。

## 触发评测

`evals/` 下有 15 条正向 / 6 条反向 / 9 条近邻用例，基线为阈值 0.30、FP=0 / FN=0。
**改 `description` 后必须重跑** `trigger_eval.py`。命令与用例设计意图见
`references/trigger-eval.md`。

## 相关规则

- `rules/project/upstream-compat/RULE.mdc` —— 核心底线与路由
- `extension-capability/methods.mdc` —— 四条零改动路径与红线（属扩展能力族）
- `extension-capability/layout.mdc` —— `extensions/` 结构与覆写崩溃语义（属扩展能力族）
- `divergence-registry.mdc` —— 清单字段定义的权威来源
- `upstream-sync.mdc` —— merge 策略与冲突处置
- `rules/vcs/vcs-engine-commit/` —— 引擎仓库改动如何提交（两种部署形态）
