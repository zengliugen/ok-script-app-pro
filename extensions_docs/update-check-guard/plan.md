# update-check-guard 实施计划

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 生成器 | devflow-guard/new |
| 关联能力 | update-check-guard |
| 创建日期 | 2026-10-26 |
| 更新日期 | 2026-10-26 |

## 目标

启动器不存在时 About 页不展示「App update」卡片，且不再排定时器、不再产生
`FileNotFoundError`。对应设计：`design/update-check-guard.md`。

## 分批任务

| 批次 | 内容 | 涉及文件 | 验收标准 |
|---|---|---|---|
| 一 | 实现守卫 | `extensions/capabilities/update_check_guard/guard.py` | 启动器缺失时摘卡片、可用时不动；层级不符时崩溃并指名 |
| 二 | 定义项目全局单例 | `extensions/app_globals.py` | 继承上游 `Globals`，`on_show_main_window` 调守卫 |
| 三 | 替换 `my_app` 注册项 | `extensions/overrides.py` | 新增 `LIST_OVERRIDES` 并纳入缺键校验 |
| 四 | 补测试 | `extensions_tests/TestUpdateCheckGuard.py` | 三条判据 + 崩溃语义 + 继承关系 |

## 文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `extensions/capabilities/update_check_guard/guard.py` | 新增 | 摘卡片逻辑与容器定位 |
| `extensions/app_globals.py` | 新增 | `AppGlobals` 子类 |
| `extensions/overrides.py` | 修改 | 新增 `LIST_OVERRIDES`，缺键校验覆盖两张表 |
| `extensions_tests/TestUpdateCheckGuard.py` | 新增 | 7 个用例 |
| `configs/devices.json` | 修改 | 目标窗口改绑 Unity.exe |

## 验收标准

- 功能：源码方式启动后日志无 `PyAppify executable was not found`；About 页只剩版本卡片。
- 测试：`extensions_tests/TestUpdateCheckGuard.py` 全部通过。
- 约束：未修改上游文件（`src/config.py` 的接线行早已存在，本次未动），不涉及 `divergence.yaml`。

## 实施进度

| 批次 | 状态 | 完成日期 | 备注 |
|---|---|---|---|
| 一 | 已完成 | 2026-10-26 | — |
| 二 | 已完成 | 2026-10-26 | — |
| 三 | 已完成 | 2026-10-26 | `my_app` 由上游值改为项目子类 |
| 四 | 已完成 | 2026-10-26 | 7 个用例通过 |

## 计划外事项

无偏差：四个批次均按原计划完成，因此不产出 `update-check-guard-deviation.md`。
