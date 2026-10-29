# 扩展能力守卫

## 解决什么问题

扩展能力的**代码**能说明「做了什么」，但说明不了「为什么这么做、放弃了什么、在什么前提下会失效」。
这些判断一旦丢失，后来者只能重新推演——而重新推演往往会得出相反结论，于是同一件事被反复推翻。

本技能把代码一侧的落位约束变成机械体检；文档一侧的流程与契约由引擎侧 `devflow-guard` 承担。

## 职责边界（为什么本技能不再生成文档）

| 事项 | 归属 | 理由 |
|---|---|---|
| 需求探索、生成骨架、登记总览表、改状态、归档 | `devflow-guard` | 引擎已全部覆盖，且其 `new` 强制要求已确认的探索记录，比手工更严 |
| 文档契约体检（11 项） | `devflow-guard` | 判定逻辑只应有一处事实来源 |
| **代码落位体检** | **本技能** | 需要读 `extensions/` 源码结构，引擎不查 |

两处各写一份文档判定会分叉，而分叉不会报错——只会让「体检通过」这句话失去意义。

## 怎么用

```bash
S=.codebuddy.project/skills/project/project-extension-capability/project-extension-capability-guard/scripts/extension_capability_guard.py

python $S audit    # 先委托 devflow-guard 体检文档契约，再体检代码落位，合并结论
```

新建能力不在这里：

```bash
D=.codebuddy/lib/devflow/skills/devflow-guard/scripts/devflow_guard.py

python $D explore <主题名>
python $D new <能力名> --from-explore <主题名>
python $D status <能力名> <状态>
python $D archive <能力名>
```

## `audit` 报出来的东西怎么读

输出分两段，最后给合并结论。

| 段 | 检查内容 |
|---|---|
| 文档契约（委托） | 文件头字段、生成器取值、总览表契约、表↔磁盘对账、ADR 编号与取代链、计划与偏差配对、归档一致性、探索记录状态与采纳指针 |
| 代码落位（本技能） | patch 的 `apply()` 有 raise 断言、`sys.path` 污染、每层 `__init__.py`、代码变更未登记提示 |

| 档位 | 你该做什么 |
|---|---|
| 错误（退出码 2）| 必须修 |
| 警告（退出码 1）| 逐条判断 |
| 提示（退出码 0）| 看一眼即可 |
| 环境不可用（退出码 3）| 先修环境：多为未安装 devflow 包或 devflow 配置不可用。**此时其余结论都不可采信** |

**不设豁免机制**：缺口一直报，直到修掉。这条是刻意的——豁免清单一旦存在就会无限扩张，
最后没人再看输出。

## 边界（不做什么）

| 不做 | 理由 | 该谁做 |
|---|---|---|
| 生成文档骨架、登记总览表 | 引擎已有实现，重复即分叉 | `devflow-guard` |
| 判断用哪条扩展路径 | 需要读框架能力，脚本判断不了 | 规则 `project-extension-capability/methods.mdc` |
| 修改文档内容 | 设计与措辞是人的判断 | 人写 |
| 检查上游文件改动 | 属另一族 | 技能 `project-upstream-compat-guard` |
| 跑测试 | 职责分离 | `run_tests.ps1` |

## 已知局限

**没有脚本单元测试**。代码落位检查的判定逻辑靠实际运行验证，这意味着逻辑错误可能**静默通过**
（表现是「零缺口」，而非报错）。使用时不应只看退出码，还要看输出内容是否合理。

已验证过的场景（手工构造）：缺 `__init__.py` 目录、patch 无断言、`sys.path` 污染、
委托目标缺失时退出码 3。

## 文档索引

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 触发条件、职责分工、检查项清单、边界 |
| `scripts/extension_capability_guard.py` | 脚本本体（单命令 `audit`：委托文档体检 + 四项代码检查）|
| `evals/` | 触发评测用例与语义配置 |
| `reports/output-risk-profile.md` | 输出风险与自查清单 |
| `agents/interface.yaml` | 平台适配元数据 |
| `manifest.json` | 生命周期与打包元数据 |
