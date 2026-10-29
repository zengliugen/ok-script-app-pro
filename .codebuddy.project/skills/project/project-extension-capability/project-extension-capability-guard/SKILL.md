---
name: project-extension-capability-guard
description: 约束扩展能力的开发流程：管 extensions/ 按能力分包的落位体检（能力包契约、清单双向一致、命名三处对应、能力隔离、patch 断言、sys.path、__init__.py），并把文档流程转交引擎侧 devflow-guard（需求探索、生成骨架、登记总览表、改状态、归档、文档契约体检）。用户说"检查扩展文档"、"文档缺口"、"扩展能力体检"、"audit 一下扩展"、"代码落位"、"目录太乱"时使用。
---

# 扩展能力守卫

本项目把扩展能力当作一门独立的工程来做：能力怎么写（手段选择）、代码放哪（落位）、
过程怎么记录（文档体系）、由谁验收（本技能）。规则约束见
`rules/project/project-extension-capability/`。

本技能只做两件规则做不到的事：**守入口**与**机械体检代码落位**。它不修改既有文档内容——
判断与措辞由人负责。

## 职责分工（先读这一节）

文档流程与文档契约体检**一律不在本技能内**，由引擎侧 `devflow` 包的 `devflow-guard` 提供：

| 事项 | 由谁做 | 命令 |
|---|---|---|
| 需求探索记录 | `devflow-guard` | `explore <主题名>` |
| 生成能力文档骨架并登记 | `devflow-guard` | `new <能力名> --from-explore <主题名>` |
| 决策记录 | `devflow-guard` | `adr <slug>` |
| 状态同步（表 + 四份文档头） | `devflow-guard` | `status <能力名> <状态>` |
| 能力归档 | `devflow-guard` | `archive <能力名>` |
| 文档契约体检（11 项） | `devflow-guard` | `audit` |
| **代码落位体检** | **本技能** | `audit` |

**为什么不各写一份**：判定逻辑写两处，改一处漏一处就会出现「一边报错一边放行」，
而这类分叉不会报错，只会让体检结论失去可信度。

## 一个模式

```bash
DIR=.codebuddy.project/skills/project/project-extension-capability/project-extension-capability-guard

python $DIR/scripts/extension_capability_guard.py audit
```

`audit` 分两段输出，最后给合并结论：

1. **委托段**：调 `devflow_guard.py audit`，原样转发其输出与退出码。委托目标不存在
   （未安装 devflow 包）时该段报错误，整体退出码 3。
2. **代码落位段**：本技能独有的七项检查，读 `extensions/` 下的 Python 源码。

### 代码落位检查什么

| 检查项 | 判据 | 为什么脚本能判 |
|---|---|---|
| patch 断言 | `patches/*.py` 的 `apply()` 内必须有 `raise` | ast 解析函数体，不靠文本匹配（注释里的 raise 不算） |
| sys.path 污染 | `extensions/` 内不得 `sys.path.insert/append` 自己 | 文本匹配 + 同行含 extensions |
| 包结构 | `extensions/` 每层目录必须有 `__init__.py` | 目录遍历 |
| **能力分包** | 顶层条目在白名单内；`capabilities/` 下的目录与 `registry.py` 的 `CAPABILITIES` **双向一致**；每个能力包有 `register` 函数 | ast 读清单字面量（不 import，避免被 import 失败连带失效）+ 目录遍历 |
| **能力命名** | 能力包名按 `-`↔`_` 换算后能对上文档总览表「能力」列 | 从 `project.index.json` 找 devflow 配置，读文档根的 `README.md` 首列 |
| **能力隔离** | 能力包内不得 `from/import extensions.capabilities.<其他能力>` | 按行正则，截断到能力包一级 |
| 登记同步提示 | `extensions/` 有改动但总览表未变 | `git status` 比对，仅为提示 |

**能力分包的六项判据**（前四项报错误，末两项报错误/提示）：

1. `extensions/` 顶层只允许 `__init__.py`、`bootstrap.py`、`overrides.py`、`app_globals.py`、
   `capabilities/`、`patches/`；
2. 清单条目必须在 `extensions.capabilities.` 下且目录真实存在；
3. `capabilities/` 下的目录必须在清单里登记，否则**永远不会被装载**；
4. 每个能力包的 `__init__.py` 必须定义 `register` 函数（**缺契约静默不生效**）；
5. 能力包名换算成 kebab-case 后未在总览表登记 → **提示**（新建能力先写代码后补文档是正常顺序）；
6. 能力之间互相 import → 错误。

**不查「用哪条扩展路径」「能力归属是否合理」**——那要读懂框架能力，脚本判断不了，走评审。

### 三档分级与退出码

| 档位 | 含义 | 退出码 |
|---|---|---|
| 错误 | 确定的违规，需修复 | 2 |
| 警告 | 可能违规，需人工判断 | 1 |
| 提示 | 仅供参考 | 0（有输出但通过） |
| 环境不可用 | 委托目标缺失、配置不可用或执行失败 | 3 |

**环境不可用优先于一切**：此时检查没真正跑完，报出的 0 或 1 都不代表代码合规。

**不设豁免机制**：缺口就该一直报，直到被修复。已知缺口的可见性比输出的整洁更重要。

## 不适用范围（do not use / out of scope）

| 请求 | 该去哪 |
|---|---|
| 生成文档骨架、登记总览表、改状态、归档 | `devflow-guard` 技能——本技能已不含这些命令 |
| 决定用哪条扩展路径 | 规则 `project-extension-capability/methods.mdc`——需要读框架能力，脚本判断不了 |
| 判断某个改动该不该改上游文件 | 规则 `project-upstream-compat`——本技能不管上游 |
| 登记上游改动、校验差异清单 | 技能 `project-upstream-compat-guard` |
| 修改文档内容、补写设计文档 | 人写。本技能不生成内容 |
| 执行测试 | `run_tests.ps1`——本技能不跑测试 |

## 什么算「新增扩展能力」

完整判据只在规则族维护一处，本技能不复制：见
`rules/project/project-extension-capability/RULE.mdc` 的「什么算「新增扩展能力」」节。

该处除了判定表，还有一张**装配基础设施清单**（`bootstrap.py` / `overrides.py` / `patches/`）——
这类文件改变用户可见行为但**不是能力**，不进总览、不写过程文档。判断某文件该不该登记前先看那张表。

## 已知局限

本技能**没有脚本单元测试**，判定逻辑靠实际运行验证，已知验证记录见 `reports/output-risk-profile.md`。
这是显式取舍：代价是**逻辑错误可能静默通过**，应留意输出是否合理，而非只看退出码。

## 触发评测

改动 `description` 或 `evals/trigger_cases.json` 后必须重跑语义评测，跑法与用例分类见
`references/trigger-eval.md`。**不得为过测而删用例**。

## 相关规则

`rules/project/project-extension-capability/RULE.mdc` 为核心底线与路由；其子规则
`methods.mdc`（四条路径与红线）、`layout.mdc`（落位与崩溃语义）、`docs.mdc`（文档体系）、
`sync-conflict.mdc`（合并冲突处置）按需加载。
