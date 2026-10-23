# 上游差异守护（upstream-guard）

## 解决什么问题

本项目 fork 自 `ok-oldking/ok-script-app`，新增能力时若直接改上游文件，每次上游更新都会在
`git merge` 时产生冲突。改动越深，冲突越难解，最终会失去跟进上游的能力。

本技能把「改过哪些上游文件」从模糊记忆变成可机械校验的清单，让冲突面始终可见、可控。

## 怎么用

```bash
python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py            # 全量盘点
python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py --changed  # 只查工作区
python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py --add <路径> # 生成条目
```

建议时机：改动上游文件后、提交前、以及每次上游同步的前后。

## 边界

- 不做 `git fetch` / `git merge`，同步动作不走技能脚本。
- 只报告差异，不判断「改动是否合理」——那需要读 diff。
- 不自动推断改动原因，`--add` 只给骨架。
- 校验通过不等于行为正确；上游行为回归靠 `tests/` 基线测试，扩展测试（`extensions_tests/`）不提供此证据。

标记判定细节、基准语义与环境依赖见 `references/` 下三份文档。

## 文档索引

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 触发描述、用法、不适用范围、触发评测入口 |
| `references/output-interpretation.md` | 四种输出标记的判定条件、处置动作、退出码 |
| `references/check-basis.md` | 为什么用本地锚点当基准、配置定位、字段权威来源 |
| `references/boundaries.md` | 不做的事、环境依赖、已知判定盲区、何时该怀疑结论 |
| `references/ownership-rules.md` | 归属判定：引擎 submodule 与项目自有目录为何不登记、脚本如何表现 |
| `evals/trigger_cases.json` | 触发用例：12 正向 / 6 反向 / 8 近邻 |
| `evals/semantic_config.json` | 语义概念与权重配置 |
| `security/permission_policy.json` | subprocess 权限审批（git 只读命令） |
| `scripts/upstream_guard.py` | 校验脚本本体 |
| `agents/interface.yaml` | 可移植接口元数据 |
| `reports/skill-ir.json` | 规范化 Skill IR（`manifest.skill_ir_source` 指向，必须入库） |
| `reports/output-risk-profile.md` | 输出风险档案（手写，入库） |

规则侧权威定义见 `.codebuddy.project/rules/project/upstream-compat/`，
其中 `divergence-registry.mdc` 是清单字段的唯一来源。
