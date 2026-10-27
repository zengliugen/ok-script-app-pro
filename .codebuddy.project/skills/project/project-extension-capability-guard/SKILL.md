---
name: project-extension-capability-guard
description: 约束扩展能力的开发流程：新建扩展能力时生成设计/计划/测试三份文档骨架并登记能力总览表，体检文档契约与代码落位并按三档报缺口。用户说"新建扩展能力"、"加个新任务"、"扩展文档"、"检查扩展文档"、"文档缺口"、"扩展能力体检"、"audit 一下扩展"、"这个能力登记了吗"时使用。也用于判断某个改动算不算新增扩展能力。
---

# 扩展能力守卫

本项目把扩展能力当作一门独立的工程来做：能力怎么写（手段选择）、代码放哪（落位）、
过程怎么记录（文档体系）、由谁验收（本技能）。规则约束见 `rules/project/project-extension-capability/`。

本技能只做两件规则做不到的事：**生成骨架**与**机械体检**。它不修改既有文档内容——
判断与措辞由人负责。

## 两个模式

```bash
DIR=.codebuddy.project/skills/project/project-extension-capability-guard

python $DIR/scripts/extension_capability_guard.py new <能力名>   # 生成骨架并登记总览表
python $DIR/scripts/extension_capability_guard.py audit           # 体检文档契约与代码落位
```

### `new` 做什么

从 `extensions_docs/templates/` 复制三份骨架，填充文件头（状态=草稿、关联能力=能力名、日期=今天、
生成器标记），并在 `extensions_docs/README.md` 的能力总览表末尾追加一行。

**三道运行时保护**，任一不满足即中止且不写任何文件：

| 保护 | 触发条件 |
|---|---|
| 能力名格式 | 非英文 kebab-case（如输入中文名）即报错并给出示例 |
| 目标文件已存在 | 任一目标文件存在即中止，列出全部冲突文件 |
| 表头契约 | 总览表不可解析即中止；插入点定位失败则回滚已生成的文件 |

**不提供 `--force`**：覆盖会毁掉已写的内容，而 design 文档往往是唯一副本。

### `audit` 检查什么

九项，全部机械可判定：

| 组 | 检查项 |
|---|---|
| 文档契约 | 文件头四字段齐全、状态值合法 |
| 文档契约 | 总览表↔磁盘双向对账（表引用的文件必须存在；磁盘上的能力文档必须在表里有行）|
| 文档契约 | 表头七列契约（不符即报错，不静默降级）|
| 文档契约 | ADR 编号连续、无重复、文件名合规 |
| 文档契约 | plan/deviation 配对完整（状态为已定/已实施时必须有偏差文档或显式写「无偏差」）|
| 文档契约 | 草稿文档带生成器标记（证明走了技能）|
| 代码落位 | patch 模块的 `apply()` 内有 raise 断言（用 ast 解析，不靠文本匹配）|
| 代码落位 | `extensions/` 内不得把 extensions 加入 `sys.path` |
| 代码落位 | `extensions/` 每层目录有 `__init__.py` |

另有一项**提示**级信号：`extensions/` 有未提交改动但总览表未变——可能是新能力未登记，也可能只是改了个 bug。

### 三档分级与退出码

| 档位 | 含义 | 退出码 |
|---|---|---|
| 错误 | 确定的违规，需修复 | 2 |
| 警告 | 可能违规，需人工判断 | 1 |
| 提示 | 仅供参考 | 0（有输出但通过）|

**不设豁免机制**：缺口就该一直报，直到被修复。已知缺口的可见性比输出的整洁更重要。

## 不适用范围（do not use / out of scope）

| 请求 | 该去哪 |
|---|---|
| 决定用哪条扩展路径 | 规则 `project-extension-capability/methods.mdc`——需要读框架能力，脚本判断不了 |
| 判断某个改动该不该改上游文件 | 规则 `project-upstream-compat`——本技能不管上游 |
| 登记上游改动、校验差异清单 | 技能 `project-upstream-compat-guard` |
| 修改文档内容、补写设计文档 | 人写。本技能只生成空骨架，不生成内容 |
| 执行测试 | `run_tests.ps1`——本技能不跑测试 |

## 什么算「新增扩展能力」

判据是**是否向用户或框架暴露了新的行为面**：

| 算 | 不算 |
|---|---|
| 新增任务、Tab、Web 页面 | 修 bug |
| 新增 patch 目标 | 调整既有能力参数或文案 |
| 新增 config 覆写项（改变用户可见行为）| 内部重构、拆分模块 |
| 新增全局单例或后台触发器 | 补测试、改注释 |

边界模糊时按「是否需要记录取舍理由」判断：有取舍就写，没有就不写。

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
