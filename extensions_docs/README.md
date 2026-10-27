# 扩展能力开发文档

本目录存放**扩展能力**（`extensions/` 下的运行时代码）的设计、开发与测试过程文档，用于维护这些能力在长期演进中的可追溯性。

## 边界：本目录不管什么

| 内容 | 归属 | 说明 |
|---|---|---|
| 扩展代码怎么写、目录职责、崩溃语义 | `.codebuddy.project/rules/project/project-extension-capability/` | 强制约束，规则文件是唯一权威 |
| 面向使用者的扩展用法 | `docs/extension-guide.md` | 公开文档，讲稳定用法不讲论证过程 |
| 上游文件能不能改、改了怎么登记 | `.codebuddy.project/rules/project/project-upstream-compat/` | 服务于「上游兼容」，与扩展能力维护是两件事 |
| 扩展测试代码本身 | `extensions_tests/` | 可执行事实，本目录只引用不复制 |

**分工原则**：规则定约束，本目录记过程。同一结论不写两遍——约束进规则，判断与推理进本目录。

## 目录职责

每个子目录放什么、不放什么，见各目录下的 `README.md`。总览：

| 目录 | 阶段 | 承载内容 |
|---|---|---|
| `design/` | 做之前 | 方案推演、候选比较、取舍理由 |
| `plans/` | 做之中 | 实施计划，以及实施偏差记录 |
| `testing/` | 做之后 | 测试策略、验收结论、已知盲区 |
| `decisions/` | 贯穿 | 不可变的决策快照（ADR） |
| `templates/` | — | 上述四类文档的骨架，复制即用 |
| `archive/` | 收尾 | 已完成能力的整包归档 |

## 命名规范

- **文件名一律英文 kebab-case**，正文一律简体中文：`test-dir-naming.md` 而非 `测试目录命名.md`。
  理由是路径会出现在命令行、Git 日志、CI 输出与 Markdown 链接中，英文名无须转义。
- **能力相关文档用「能力名 + 后缀」配对**：
  - `plans/<能力>-plan.md` —— 实施计划
  - `plans/<能力>-deviation.md` —— 实施偏差记录
- **ADR 加四位序号前缀**：`decisions/0001-<slug>.md`，序号只增不复用、不回收。

## 文件头必备字段

每份文档开头（一级标题之后）放一张字段表，四行固定：

```markdown
| 字段 | 值 |
|---|---|
| 状态 | 草稿 / 评审中 / 已定 / 已实施 / 已废弃 |
| 关联能力 | 能力名，与能力总览表一致；跨能力决策写「全局」 |
| 创建日期 | YYYY-MM-DD |
| 更新日期 | YYYY-MM-DD |
```

「状态」字段的价值在于让**已废弃但仍存在**的文档可被检索识别，避免被误读为现行方案。
新建文档从 `templates/` 复制，模板已含该表。

## 新建扩展能力的流程

```bash
# 1. 生成骨架（方案设计开始前）
python .codebuddy.project/skills/project/project-extension-capability-guard/scripts/extension_capability_guard.py new <能力名>

# 2. 填 design（方案与取舍）→ 定稿后补 plans → 开发完成后补 testing

# 3. 提交前体检
python .codebuddy.project/skills/project/project-extension-capability-guard/scripts/extension_capability_guard.py audit
```

能力名必须是**英文 kebab-case**（如 `config-overrides`）——它同时是文件名、总览表「能力」列
与文件头「关联能力」字段的值。

**不是所有改动都算新增能力**：修 bug、调参数、内部重构、补测试都不需要走这套流程。
判据是「是否向用户或框架暴露了新的行为面」，详见 `rules/project/project-extension-capability/RULE.mdc`。

完整的约束与检查项见 `rules/project/project-extension-capability/docs.mdc`。

## 扩展能力总览

新增能力时在表末追加一行；某阶段未产出文档时该列留空，并在状态列如实填写。

| 能力 | 状态 | 设计 | 实施计划 | 实施偏差 | 测试 | 决策记录 |
|---|---|---|---|---|---|---|
| config 覆写 | 已实施 | 待补 | — | — | `extensions_tests/TestExtensions.py` | — |
| log-noise-filter | 已实施 | `design/log-noise-filter.md` | `plans/log-noise-filter-plan.md` | — | `testing/log-noise-filter-testing.md` | — |
| update-check-guard | 已实施 | `design/update-check-guard.md` | `plans/update-check-guard-plan.md` | — | `testing/update-check-guard-testing.md` | — |

> 「待补」是显式的缺口标记，不是占位符。补齐后改成实际文件名。
