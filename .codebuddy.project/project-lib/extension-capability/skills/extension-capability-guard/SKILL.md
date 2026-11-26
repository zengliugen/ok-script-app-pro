---
name: extension-capability-guard
description: 约束扩展能力的开发流程：管 extensions/ 按能力分包的落位体检（能力包契约、清单双向一致、命名三处对应、能力隔离、patch 断言、sys.path、__init__.py），并把文档流程转交引擎侧 devflow-guard（需求探索、生成骨架、登记总览表、改状态、归档、文档契约体检）。用户说"检查扩展文档"、"文档缺口"、"扩展能力体检"、"audit 一下扩展"、"代码落位"、"目录太乱"时使用。
---

# 扩展能力守卫

本项目把扩展能力当作一门独立的工程来做：能力怎么写（手段选择）、代码放哪（落位）、
过程怎么记录（文档体系）、由谁验收（本技能）。本技能只做两件规则做不到的事：
**守入口**与**机械体检代码落位**，不修改既有文档内容。

## 调用后的第一步：意图确认（强制）

**技能被唤起后不得直接执行 `audit`。** 先问清这次属于哪一类，再按去向走：

| 用户的意图 | 去向 |
|---|---|
| 只跑体检 / 查契约缺口 | 本技能 `audit`（文档契约段委托 `devflow-guard`） |
| 新增扩展能力 | **不在本技能**。转 `devflow-guard`：`explore` → `new --from-explore` → 实施 → 回来 `audit` |
| 改动现有能力 | 先判是否暴露新行为面：暴露 → 立新能力；不暴露 → 普通改动，完成后再 `audit` |
| 维护文档结构（状态 / 归档 / 决策记录） | 转 `devflow-guard`：`status` / `archive` / `adr` |

用户已说明意图的仍要确认一次——第一项放「按你刚才说的执行」，结论由用户确认。
**不确认文档域**：扩展能力文档固定落项目域，文档根由 devflow 配置声明（本仓库为
`extensions_docs`），不存在引擎域选择。完整判据见 `references/intake.md`——**执行前必读**。

## 职责分工

文档流程与文档契约体检**一律不在本技能内**，由引擎侧 `devflow` 包的 `devflow-guard` 提供
（探索、`new`、`adr`、`status`、`archive`、文档契约 `audit`）；本技能只做代码落位体检。
判定逻辑写两处会分叉，而分叉不报错，只会让体检结论失去可信度。

## 一个执行模式

通过意图确认后，只有一个执行模式：

```bash
DIR=.codebuddy.project/project-lib/extension-capability/skills/extension-capability-guard

python $DIR/scripts/extension_capability_guard.py audit
```

`audit` 分两段：**委托段**调 `devflow_guard.py audit` 并转发其输出与退出码（委托目标缺失时
整体退出码 3）；**代码落位段**执行本技能独有的九项检查。检查项、能力分包判据与
「脚本判断不了什么」见 `references/checks.md`。

## 三档分级与退出码

| 档位 | 含义 | 退出码 |
|---|---|---|
| 错误 | 确定的违规，需修复 | 2 |
| 警告 | 可能违规，需人工判断 | 1 |
| 提示 | 仅供参考 | 0 |
| 环境不可用 | 委托目标缺失、配置不可用或执行失败 | 3 |

**环境不可用优先于一切**：此时检查没真正跑完，报出的 0 或 1 都不代表代码合规。
**不设豁免机制**：缺口就该一直报，直到被修复。

## 不适用范围（do not use / out of scope）

| 请求 | 该去哪 |
|---|---|
| 生成文档骨架、登记总览表、改状态、归档 | `devflow-guard` 技能 |
| 决定用哪条扩展路径 | 规则 `extension-capability/methods.mdc` |
| 判断某个改动该不该改上游文件 / 登记上游改动 | `upstream-compat` 规则与 `upstream-compat-guard` 技能 |
| 修改文档内容、补写设计文档 | 人写。本技能不生成内容 |
| 执行测试 | `run_tests.ps1`——本技能不跑测试 |

**什么算「新增扩展能力」**：判定表与装配基础设施清单只在规则族维护一处，见
`.codebuddy.project/project-lib/extension-capability/rules/extension-capability/RULE.mdc`
的对应节；判断某文件该不该登记前先看那张表。

## 已知局限与触发评测

本技能**没有脚本单元测试**，判定逻辑靠实际运行验证——代价是逻辑错误可能静默通过，
应留意输出是否合理，而非只看退出码；已知验证记录见 `reports/output-risk-profile.md`。
改动 `description` 或 `evals/trigger_cases.json` 后必须按 `references/trigger-eval.md`
重跑语义评测，**不得为过测而删用例**。

## 相关规则

`extension-capability/RULE.mdc` 为核心底线与路由；子规则 `methods.mdc`（四条路径与红线）、
`layout.mdc`（落位与崩溃语义）、`docs.mdc`（文档体系）、`sync-conflict.mdc`（合并冲突处置）
按需加载。
