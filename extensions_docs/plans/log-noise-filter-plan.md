# log-noise-filter 实施计划

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 生成器 | project-extension-capability-guard/new |
| 关联能力 | log-noise-filter |
| 创建日期 | 2026-10-26 |
| 更新日期 | 2026-10-26 |

## 目标

启动后同类「get_exe_by_hwnd process.cmdline() error」日志不再每 0.2 秒刷完整 traceback，
而是降级为可读的单行摘要并按冷却窗口折叠。对应设计：`design/log-noise-filter.md`。

## 分批任务

| 批次 | 内容 | 涉及文件 | 验收标准 |
|---|---|---|---|
| 一 | 实现过滤器与安装入口 | `extensions/core/log_noise_filter.py` | `NoiseFilter` 可独立构造；`install()` 幂等 |
| 二 | 接入 bootstrap 装载链 | `extensions/bootstrap.py` | `bootstrap.apply()` 后 ok logger 上恰有一个过滤器 |
| 三 | 补测试 | `extensions_tests/TestLogNoiseFilter.py` | 折叠、放行、计数守恒、幂等四类行为均有用例 |
| 四 | 修正本机设备绑定 | `configs/devices.json` | 不再指向输入法窗口 |

## 文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `extensions/core/log_noise_filter.py` | 新增 | 过滤器实现与 `install()` |
| `extensions/bootstrap.py` | 修改 | `_register_capabilities` 中调用 `log_noise_filter.install()` |
| `extensions_tests/TestLogNoiseFilter.py` | 新增 | 11 个用例 |
| `configs/devices.json` | 修改 | 目标窗口改绑 `Unity.exe` / hwnd `1184012` |

## 验收标准

- 功能：连续三条同类噪音只输出一条 WARNING 摘要；非噪音 ERROR 原样保留。
- 测试：`extensions_tests/TestLogNoiseFilter.py` 全部通过。
- 约束：未修改任何上游文件（`src/`、`main*.py`、依赖清单均未改动），因此不涉及 `divergence.yaml` 登记。

## 实施进度

| 批次 | 状态 | 完成日期 | 备注 |
|---|---|---|---|
| 一 | 已完成 | 2026-10-26 | — |
| 二 | 已完成 | 2026-10-26 | — |
| 三 | 已完成 | 2026-10-26 | 11 个用例通过 |
| 四 | 已完成 | 2026-10-26 | 最终改绑 Unity.exe；需重启应用生效 |

## 计划外事项

无偏差：四个批次均按原计划完成，未出现「实际做法 ≠ 计划」的情形，因此不产出
`log-noise-filter-deviation.md`。日志过滤器与设备配置修正是两个独立事项，后者属本地配置调整，
未纳入能力文档。
