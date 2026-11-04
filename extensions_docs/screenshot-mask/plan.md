# screenshot-mask 实施计划

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | screenshot-mask |
| 创建日期 | 2026-11-04 |
| 更新日期 | 2026-11-04 |
| 生成器 | devflow-guard/new |

## 目标

完成后系统具备什么能力。对应设计文档：`design/screenshot-mask.md`。

截图时可用黑色矩形遮挡画面中的 UID，位置与尺寸可配置、可关闭；侧边栏有可视化页签，
可直接在画面上拖动遮罩与 8 个手柄调整，点「保存配置」才落盘。

## 分批任务

| 批次 | 内容 | 涉及文件 | 验收标准 |
|---|---|---|---|
| 一 | 几何层：8 锚点、百分比宽高与偏移、边界裁剪、矩形反解 | `geometry.py` | 默认值与上游写死行为逐像素一致；任意矩形反解后能精确还原 |
| 二 | 配置与处理器：配置项契约、旧中文键迁移、异常兜底 | `processor.py` | 开关关闭不涂黑；异常返回原帧；旧配置数值不丢 |
| 三 | 页签：工具条、预览画布、8 手柄拖拽、缩放、编辑与保存分离 | `tab.py` | 拖动不写配置；保存后能还原同一矩形；关闭开关隐藏编辑框 |
| 三·补 | 工具栏补「重置为默认值」：回到上游的右下角 13% × 2.5%，不动开关 | `tab.py`、`i18n/` | 重置后几何与上游逐像素一致；已在默认值时按钮不可用 |
| 四 | 装配与本地化：注册清单、`ok_ext` 译文 | `__init__.py`、`registry.py`、`i18n/` | 真实启动路径能实例化页签；`.po` 与 `.mo` 同步 |
| 五 | 测试：几何、处理器、界面三层 | `extensions_tests/TestScreenshotMask.py` | 全部用例通过 |

## 文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `extensions/capabilities/screenshot_mask/__init__.py` | 新增 | 装配契约：迁移 → 登记配置项 → 换处理器 → 登记页签 |
| `extensions/capabilities/screenshot_mask/geometry.py` | 新增 | 纯几何换算，不依赖 Qt |
| `extensions/capabilities/screenshot_mask/processor.py` | 新增 | `screenshot_processor` 工厂、配置项构造、旧键迁移 |
| `extensions/capabilities/screenshot_mask/tab.py` | 新增 | `PreviewCanvas` 与 `ScreenshotMaskTab` |
| `extensions/capabilities/screenshot_mask/i18n/{zh_CN,en_US}/LC_MESSAGES/ok_ext.{po,mo}` | 新增 | 13 条界面文案 |
| `extensions/capabilities/registry.py` | 修改 | `CAPABILITIES` 追加一行 |
| `extensions_tests/TestScreenshotMask.py` | 新增 | 63 个用例 |
| `extensions_docs/explore/screenshot-mask.md` | 新增 | 需求探索记录 |
| `extensions_docs/design/screenshot-mask.md` | 新增 | 设计文档 |
| `extensions_docs/plans/screenshot-mask-plan.md` | 新增 | 本文件 |
| `extensions_docs/plans/screenshot-mask-deviation.md` | 新增 | 实施偏差记录 |
| `extensions_docs/testing/screenshot-mask-testing.md` | 新增 | 测试策略与验收结论 |

## 验收标准

- 功能：默认配置（开启、右下角、13% × 2.5%、偏移 0）与上游 `make_bottom_right_black`
  逐像素一致；关闭开关后截图无遮罩；页签拖动符合「拖到哪存到哪」。
- 测试：`extensions_tests/TestScreenshotMask.py` 全绿；`tests/TestMain.py` 不受影响。
- 落位：未改动框架拥有的文件——处理器与页签分别走 `screenshot_processor` 与
  `custom_tabs` 两个 config 通道，`src/config.py` 与上游其他文件零改动。

## 实施进度

开工后逐批更新，**只记结论不记过程**（过程进偏差记录）。

| 批次 | 状态 | 完成日期 | 备注 |
|---|---|---|---|
| 一 | 已完成 | 2026-11-04 | 反解改为「8 锚点全试 + 偏移最小者胜」 |
| 二 | 已完成 | 2026-11-04 | 迁移在 `Config` 构造前执行 |
| 三 | 已完成 | 2026-11-04 | 见偏差记录第 1、2 条 |
| 三·补 | 已完成 | 2026-11-04 | 见偏差记录第 4 条 |
| 四 | 已完成 | 2026-11-04 | 见偏差记录第 3 条 |
| 五 | 已完成 | 2026-11-04 | 63 个用例 |

## 计划外事项

- **`extensions_tests/TestScreenshotMask.py` 需显式销毁页签**：上游 `enable_touch_scrolling`
  给每个页签挂了全局事件过滤器，旧页签被 GC 后过滤器仍可能被调用，导致下个用例在构造阶段
  抛出 `_TouchScrollClickGuard` 递归错误。已在 `tearDown` 里 close + deleteLater +
  `processEvents`。这是测试基础设施问题，不单独立项。
