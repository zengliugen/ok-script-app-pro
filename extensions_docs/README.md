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

## 谁在维护本目录

文档流程由引擎侧 `devflow` 包的 `devflow-guard` 技能执行，本项目不另写一份：

```bash
D=.codebuddy/lib/devflow/skills/devflow-guard/scripts/devflow_guard.py

python $D explore <主题名>                        # 需求探索（动手前，可反复改写）
python $D new <能力名> --from-explore <主题名>     # 生成四份骨架并登记总览表
python $D adr <slug>                             # 决策记录，编号自动推算
python $D status <能力名> <状态>                  # 同步总览表与四份文档头（共五处）
python $D archive <能力名>                        # 归档并回写总览表路径

# 提交前体检：文档契约 + 代码落位，合并结论
python .codebuddy.project/skills/project/project-extension-capability-guard/scripts/extension_capability_guard.py audit
```

判定逻辑只写一处——文档契约由引擎侧脚本判、代码落位由本项目脚本判。两处各写一份会分叉，
而分叉不会报错，只会让「体检通过」失去意义。

**骨架不在本目录**：五份骨架由引擎包内置，`new` / `adr` 在落盘那一刻现场实例化。
本目录曾有一份 `templates/` 副本，因零消费者、且与源模板的差异无法机械判定而撤除。

## 目录职责

每个子目录放什么、不放什么，见各目录下的 `README.md`。总览：

| 目录 | 阶段 | 承载内容 |
|---|---|---|
| `explore/` | 动手前 | 需求确认记录；确认后 `new --from-explore` 才接受引用 |
| `design/` | 做之前 | 方案推演、候选比较、取舍理由 |
| `plans/` | 做之中 | 实施计划，以及实施偏差记录 |
| `testing/` | 做之后 | 测试策略、验收结论、已知盲区 |
| `decisions/` | 贯穿 | 不可变的决策快照（ADR） |
| `archive/` | 收尾 | 已完成能力的整包归档 |

## 命名规范

- **文件名一律英文 kebab-case**，正文一律简体中文：`test-dir-naming.md` 而非 `测试目录命名.md`。
  理由是路径会出现在命令行、Git 日志、CI 输出与 Markdown 链接中，英文名无须转义。
- **能力相关文档用「能力名 + 后缀」配对**：
  - `plans/<能力>-plan.md` —— 实施计划
  - `plans/<能力>-deviation.md` —— 实施偏差记录
- **ADR 加四位序号前缀**：`decisions/0001-<slug>.md`，序号只增不复用、不回收。用 `adr` 创建，
  不要手工定号。

## 文件头必备字段

每份能力文档开头（一级标题之后）放一张字段表，**五行固定**：

```markdown
| 字段 | 值 |
|---|---|
| 状态 | 草稿 / 评审中 / 已定 / 已实施 / 已废弃 |
| 关联能力 | 能力名，与能力总览表一致；跨能力决策写「全局」 |
| 创建日期 | YYYY-MM-DD |
| 更新日期 | YYYY-MM-DD |
| 生成器 | devflow-guard/new |
```

**生成器字段的用途与局限**：它证明该文档的骨架由技能写入，合法值只有 `devflow-guard/new`，
其它取值一律报错。但它**不能证明内容经过推演**——标记只存在于写文件那一刻，文档之后被整体重写也不会被察觉。
要真正证明「生成时走过技能」需要 git 历史基线，本项目暂不做该检查（与 ADR 正文不可变性同属一类取舍）。
因此 **体检全绿不等于流程合规**，内容是否可采纳仍需评审。

「状态」字段的价值在于让**已废弃但仍存在**的文档可被检索识别，避免被误读为现行方案。
新建文档经 `new` / `adr` 生成，不要手工拼——手工写的文档缺生成器合法值，体检会判错误。

## 新建扩展能力的流程

```bash
D=.codebuddy/lib/devflow/skills/devflow-guard/scripts/devflow_guard.py

# 1. 需求探索（确认前可反复改写）
python $D explore <主题名>
#    填写「要解决的问题」「已确认的决定」「未定项」「结论」，把状态推进一档

# 2. 生成四份骨架并登记（--from-explore 必填）
python $D new <能力名> --from-explore <主题名>

# 3. 填 design（方案与取舍）→ 定稿后补 plans → 开发完成后补 testing

# 4. 提交前体检
python .codebuddy.project/skills/project/project-extension-capability-guard/scripts/extension_capability_guard.py audit
```

能力名必须是**英文 kebab-case**（如 `config-overrides`）——它同时是文件名、总览表「能力」列
与文件头「关联能力」字段的值。

**不是所有改动都算新增能力**：修 bug、调参数、内部重构、补测试都不需要走这套流程。
判据是「是否向用户或框架暴露了新的行为面」，详见 `rules/project/project-extension-capability/RULE.mdc`。
该节另附一张**装配基础设施清单**（`bootstrap.py` / `overrides.py` / `patches/` 目录）——这类文件改变用户可见行为但**不是能力**，不进本表。

完整的约束与检查项见 `rules/project/project-extension-capability/docs.mdc`。

## 扩展能力总览

新增能力时在表末追加一行；某阶段未产出文档时该列留空，并在状态列如实填写。

| 能力 | 状态 | 设计 | 实施计划 | 实施偏差 | 测试 | 决策记录 |
|---|---|---|---|---|---|---|
| log-noise-filter | 已实施 | design/log-noise-filter.md | plans/log-noise-filter-plan.md | plans/log-noise-filter-deviation.md | testing/log-noise-filter-testing.md | — |
| update-check-guard | 已实施 | design/update-check-guard.md | plans/update-check-guard-plan.md | plans/update-check-guard-deviation.md | testing/update-check-guard-testing.md | — |

> 「待补」是显式的缺口标记，不是占位符。补齐后改成实际文件名。
