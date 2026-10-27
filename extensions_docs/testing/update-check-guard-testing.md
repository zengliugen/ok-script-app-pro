# update-check-guard 测试策略与验收记录

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 生成器 | project-extension-capability-guard/new |
| 关联能力 | update-check-guard |
| 创建日期 | 2026-10-26 |
| 更新日期 | 2026-10-26 |

## 测试策略

| 被测行为 | 为什么必须覆盖 | 对应用例 |
|---|---|---|
| 启动器缺失时摘卡片 | 这是能力本体；且必须摘外层 Card 而非内部 frame | `TestUpdateCheckGuard.py::TestUpdateCheckGuard.test_card_removed_when_launcher_missing` |
| 摘除后置空 `about_tab.update_card` | 置空才是关闭定时器的开关，不置空等于没摘 | 同上 |
| 启动器可用时不动界面 | 误摘会让发行版用户失去更新入口，代价最高 | `test_card_kept_when_launcher_available` |
| 卡片不存在时无操作 | 上游没建卡片时不得抛错 | `test_no_op_when_update_card_absent` |
| 控件层级变化时崩溃 | 静默跳过会让守卫悄悄失效，噪音回归 | `test_raises_when_container_structure_changed` |
| `my_app` 已换成项目子类 | 守卫靠这个注册项才被调用 | `TestMyAppOverride.test_my_app_points_to_project_globals` |
| `AppGlobals` 继承上游 | 换注册项不得丢弃上游行为 | `test_app_globals_inherits_upstream` |
| `LIST_OVERRIDES` 已登记 | 缺键校验依赖该表，漏登记会让上游改名无声通过 | `test_list_override_registered` |

**刻意不测的行为**：

| 行为 | 不测的原因 |
|---|---|
| 真实 GUI 下的卡片消失 | 需要完整 Qt 装配与显示，断言成本远高于收益；控件替换语义已由替身覆盖 |
| 启动器存在时的真实版本拉取 | 依赖外部服务，且属上游行为 |

## 验收记录

| 日期 | 测试根 | 用例数 | 结论 | 环境 |
|---|---|---|---|---|
| 2026-10-26 | `extensions_tests/` | 7（本能力）+ 18（既有） | 通过 | Python 3.12 / Windows 10 19045 |
| 2026-10-26 | 实机启动 | — | 通过：`PyAppify executable was not found` 0 次，`schedule pyappify update check` 0 次 | 同上，21:47:39—22:12:09 |

> 上表两行口径不同：单元用例证明判据与副作用正确，实机启动证明守卫在真实装配链上被调用。

## 已知盲区

| 盲区 | 影响面 | 缓解手段 |
|---|---|---|
| 依赖 `UpdateCard.parent().parent()` 的层级 | 上游重构 Card 装配后守卫启动期崩溃 | 崩溃信息指名了要改的文件；崩溃比静默失效更早暴露 |
| 只处理「完全没有启动器」 | 启动器在但不可执行等异常仍按上游逻辑报错 | 这是刻意保留——那类失败需要用户看见 |
| 未覆盖 `about_tab` 缺失的崩溃分支 | 该属性由上游 `MainWindow` 必建，无法在替身上稳定构造缺失场景 | 代码已改为抛 `RuntimeError` 并指名，行为由代码结构保证 |
