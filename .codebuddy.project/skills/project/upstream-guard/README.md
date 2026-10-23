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
- 校验通过不等于行为正确，回归靠 `run_tests.ps1`。

## 文档索引

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 触发描述、用法、输出标记解读、边界 |
| `scripts/upstream_guard.py` | 校验脚本本体 |
| `agents/interface.yaml` | 可移植接口元数据 |
| `reports/` | 技能工程审计报告（由 yao-meta-skill 生成） |

规则侧权威定义见 `.codebuddy.project/rules/project/upstream-compat/`，
其中 `divergence-registry.mdc` 是清单字段的唯一来源。
