# window-restore-guard 实施计划

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | window-restore-guard |
| 创建日期 | 2026-11-04 |
| 更新日期 | 2026-11-04 |
| 生成器 | devflow-guard/new |

## 目标

完成后系统具备什么能力。对应设计文档：`design/window-restore-guard.md`。

启动时若窗口按 `window_maximized` 配置进入最大化，窗口不再被一次多余的 `SW_RESTORE` 降级，
从而不出现双标题栏与布局偏移；普通窗口启动、最小化后还原的行为与上游保持一致。

## 分批任务

| 批次 | 内容 | 涉及文件 | 验收标准 |
|---|---|---|---|
| 一 | 写能力实现：替换实例 `bring_to_front`，最大化分支跳过 `SW_RESTORE` | `extensions/capabilities/window_restore_guard/{__init__,guard}.py` | 最大化窗口调用后仍 `isMaximized() == True` 且客户区无内缩 |
| 二 | 登记能力清单并接钩子 | `extensions/capabilities/registry.py` | `on_show_main_window` 被派发时完成替换 |
| 三 | 补测试：判据层 + 契约层 | `extensions_tests/TestWindowRestoreGuard.py` | 全绿；最大化分支不调用上游实现 |
| 四 | 真实启动验证两种配置 | 临时配置目录（不入库） | 最大化：`client == window` 且单套按钮；普通窗口：行为同上游 |

## 文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `extensions/capabilities/window_restore_guard/__init__.py` | 新增 | 能力契约：空 `register` + `on_show_main_window` 钩子 |
| `extensions/capabilities/window_restore_guard/guard.py` | 新增 | 实现：`protect()` 替换实例方法，最大化分支自建置前逻辑 |
| `extensions/capabilities/registry.py` | 修改 | `CAPABILITIES` 追加一行（唯一登记点） |
| `extensions_tests/TestWindowRestoreGuard.py` | 新增 | 9 条用例：替换/幂等/缺目标/三分支/契约 |
| `extensions_docs/**` | 新增 | 本能力的探索、设计、计划、偏差、测试文档 |

## 验收标准

- 功能：`window_maximized: true` 启动后窗口为原生最大化，客户区等于窗口大小（无 16px 内缩），
  界面只有一套窗口按钮，布局与最大化状态一致。
- 测试：`extensions_tests/TestWindowRestoreGuard.py` 全绿；`extensions_tests/` 其余用例不回归。
- 落位：未改动框架拥有的文件；代码全部在 `extensions/`，测试在 `extensions_tests/`。

## 实施进度

开工后逐批更新，**只记结论不记过程**（过程进偏差记录）。

| 批次 | 状态 | 完成日期 | 备注 |
|---|---|---|---|
| 一 | 已完成 | 2026-11-04 | 见偏差记录 #1：改道判据从「非最小化」收紧为「最大化且未最小化」 |
| 二 | 已完成 | 2026-11-04 | 插在 `update_check_guard` 之后、`extension_i18n` 之前，与文档总览表顺序一致 |
| 三 | 已完成 | 2026-11-04 | 9 条用例全绿 |
| 四 | 已完成 | 2026-11-04 | 实测见 `testing/window-restore-guard-testing.md` |

## 计划外事项

- 无。实现过程中未发现需要单独立项的内容；DPI 变化与系统吸附加导致的同类错位（方案 B/C）
  按探索记录保持不做，若日后复现再立项。
