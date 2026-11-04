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
python .codebuddy.project/skills/project/project-extension-capability/project-extension-capability-guard/scripts/extension_capability_guard.py audit
```

判定逻辑只写一处——文档契约由引擎侧脚本判、代码落位由本项目脚本判。两处各写一份会分叉，
而分叉不会报错，只会让「体检通过」失去意义。

**骨架不在本目录**：五份骨架由引擎包内置，`new` / `adr` 在落盘那一刻现场实例化。
本目录曾有一份 `templates/` 副本，因零消费者、且与源模板的差异无法机械判定而撤除。

## 目录职责

```
extensions_docs/
├── README.md              # 本文件：目录说明 + 能力总览表
├── <能力名>/              # 一个能力一个目录，内部平铺四份文档
│   ├── design.md          # 做之前：方案推演、候选比较、取舍理由
│   ├── plan.md            # 做之中：实施计划
│   ├── deviation.md       # 做之中：实施偏差记录
│   └── testing.md         # 做之后：测试策略、验收结论、已知盲区
├── explore/               # 动手前：需求确认，可反复改写
├── decisions/             # 贯穿：不可变的决策快照（ADR）
└── archive/<能力名>/      # 收尾：已完成能力的整包归档
```

**为什么按能力分目录**：一个能力的四份文档放在同一目录，看全貌只需 `ls <能力名>/`。
`archive/` 一直是这个形态，活跃区与归档区同构后，归档退化为一次目录搬迁，不需要改名。

**`explore/` 与 `decisions/` 留在文档根**：探索记录按**主题**命名，一份可产出多个能力
（如 `explore/maximize-restore-frameless-fix.md` 产出的是 `window-restore-guard`）；
ADR 编号必须全局连续，且有「关联能力=全局」的跨能力决策。

每个保留目录放什么、不放什么，见其自身 `README.md`。能力目录没有 README——
四份文档的文件头已说明各自用途。

## 命名规范

- **文件名一律英文 kebab-case**，正文一律简体中文：`test-dir-naming.md` 而非 `测试目录命名.md`。
  理由是路径会出现在命令行、Git 日志、CI 输出与 Markdown 链接中，英文名无须转义。
- **能力名同时是目录名**：`<能力名>/{design,plan,deviation,testing}.md`。文件名固定，
  能力名只出现在目录上；这要求能力名不得与保留目录（`explore`/`decisions`/`archive`）同名，
  `new` 会拦住。
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

# 3. 填 <能力名>/design.md（方案与取舍）→ 定稿后补 plan.md → 开发完成后补 testing.md

# 4. 提交前体检
python .codebuddy.project/skills/project/project-extension-capability/project-extension-capability-guard/scripts/extension_capability_guard.py audit
```

能力名必须是**英文 kebab-case**（如 `config-overrides`）——它同时是能力目录名、总览表「能力」列
与文件头「关联能力」字段的值，也是代码侧的落位依据：

| 位置 | 形态 | 示例 |
|---|---|---|
| 代码目录 | `extensions/capabilities/<snake_case>/` | `config_overrides/` |
| 能力清单 | `extensions/capabilities/registry.py` 的 `CAPABILITIES` | `extensions.capabilities.config_overrides` |
| 文档目录 | `extensions_docs/<kebab-case>/` | `config-overrides/` |
| 文档总览表 | 本文件「能力」列 | `config-overrides` |

三者由 `audit` 双向核对：目录与清单必须互相登记，包名换算成 kebab-case 后必须能在总览表找到
（未登记报**提示**，因为先写代码后补文档是正常顺序）。

**不是所有改动都算新增能力**：修 bug、调参数、内部重构、补测试都不需要走这套流程。
判据是「是否向用户或框架暴露了新的行为面」，详见 `rules/project/project-extension-capability/RULE.mdc`。
该节另附一张**装配基础设施清单**（`bootstrap.py` / `overrides.py` / `app_globals.py` /
`capabilities/registry.py` / `patches/` 目录）——这类文件改变用户可见行为但**不是能力**，不进本表。

完整的约束与检查项见 `rules/project/project-extension-capability/docs.mdc`。

## 扩展能力总览

新增能力时在表末追加一行；某阶段未产出文档时该列留空，并在状态列如实填写。

| 能力 | 状态 | 设计 | 实施计划 | 实施偏差 | 测试 | 决策记录 |
|---|---|---|---|---|---|---|
| log-noise-filter | 已实施 | log-noise-filter/design.md | log-noise-filter/plan.md | log-noise-filter/deviation.md | log-noise-filter/testing.md | — |
| update-check-guard | 已实施 | update-check-guard/design.md | update-check-guard/plan.md | update-check-guard/deviation.md | update-check-guard/testing.md | — |
| extension-i18n | 已实施 | extension-i18n/design.md | extension-i18n/plan.md | extension-i18n/deviation.md | extension-i18n/testing.md | — |
| window-restore-guard | 已实施 | window-restore-guard/design.md | window-restore-guard/plan.md | window-restore-guard/deviation.md | window-restore-guard/testing.md | — |
| screenshot-mask | 已实施 | screenshot-mask/design.md | screenshot-mask/plan.md | screenshot-mask/deviation.md | screenshot-mask/testing.md | — |

> 「待补」是显式的缺口标记，不是占位符。补齐后改成实际文件名。
