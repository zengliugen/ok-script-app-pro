---
name: upstream-guard
description: 盘点本项目相对上游 ok-script-app 的代码差异、校验差异是否都已登记到 divergence.yaml、辅助生成登记条目。用户说"上游差异"、"检查有没有未登记的改动"、"盘点上游改动"、"登记这个改动"、"同步前检查"、"能不能跟进上游"时使用。
---

# 上游差异守护

## 解决什么问题

本项目 fork 自上游 `ok-oldking/ok-script-app`，靠 `git merge upstream/master` 跟进更新。
上游文件被改动得越多，合并冲突越难解，最终会失去跟进能力。本技能守住这条线：

1. **盘点**——以登记的上游锚点为基准，列出本项目改过/删过哪些上游文件。
2. **校验**——这些改动是否都已在 `divergence.yaml` 中有条目，条目是否已失效。
3. **登记**——为某个改动生成登记条目骨架，避免手写 YAML 写歪。

## 怎么用

在项目根目录执行：

```bash
# 全量盘点（默认）：锚点 → HEAD 的全部差异，与登记清单比对
python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py

# 只检查当前工作区尚未提交的变更
python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py --changed

# 为某个上游文件生成登记条目骨架（人工补 reason 后写回）
python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py --add src/config.py
```

### 输出解读

| 标记 | 含义 | 处置 |
|---|---|---|
| `[未登记]` | 改动了上游文件但清单里没有条目 | 补登记，与改动同一次提交 |
| `[已失效]` | 清单有条目但文件已无差异 | 删除该条目（需要历史请查 git） |
| `[不一致]` | 实际状态（M/D）与登记的 kind 不符 | 修正条目 kind |
| `[告警]` | 新增文件落在上游专属目录 | 按扩展优先原则移到 `src/extensions/` |

退出码：0 通过 / 1 有问题 / 2 git 失败 / 3 配置缺失。

## 校验基准是本地锚点，不是远端分支

`divergence.yaml` 里的 `synced_upstream_commit` 是唯一基准。原因：离线可用、结果可复现，
不会因为某次 `git fetch` 而让「本地有无未登记改动」这个判定漂移。

「上游自锚点以来有哪些新提交」属于同步范畴，由规则的 `upstream-sync.mdc` 处理。

## 配置来源

两个清单的路径经 `.codebuddy/project.index.json` 的逻辑名定位：

| 逻辑名 | 用途 |
|---|---|
| `upstream.divergence` | 上游差异登记清单 |
| `upstream.capabilities` | 本项目已实现能力清单 |

配置缺失或索引损坏时脚本直接报错退出（退出码 3），**不回退内置默认值**——
路径写死在脚本里会让技能在别的项目失效。

## 边界（诚实的部分）

- **不做同步**。不执行 `git fetch` / `git merge`，那属于 `upstream-sync.mdc` 的流程，
  且高风险 git 操作不应藏在技能脚本里。
- **只对比锚点与 HEAD 的树差异**，不分析改动的语义。判断「这个改动该不该由 `src/extensions/`
  承接」需要人（或模型）读 diff，脚本只负责发现与报告。
- **不推断改动原因**。`--add` 只生成骨架，`reason` 必须人工填写——自动推断出的原因
  半年后读起来毫无价值。
- **不替代测试**。校验通过只说明「登记完整」，不说明「上游行为未被破坏」，后者靠运行测试。

## 相关规则

- `rules/project/upstream-compat/RULE.mdc` —— 核心底线与路由
- `extension-methods.mdc` —— 四条零改动路径与红线
- `extension-layout.mdc` —— `src/extensions/` 结构与覆写崩溃语义
- `divergence-registry.mdc` —— 清单字段定义的权威来源
- `upstream-sync.mdc` —— merge 策略与冲突处置
