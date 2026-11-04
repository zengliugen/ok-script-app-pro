# window-restore-guard 设计

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | window-restore-guard |
| 创建日期 | 2026-11-04 |
| 更新日期 | 2026-11-04 |
| 生成器 | devflow-guard/new |

## 背景

> 需求已确认，探索记录见 explore/maximize-restore-frameless-fix.md（含已排除方向与重新可行的前提）。

上游 `MainWindow.bring_to_front()` 在 Windows 分支无条件调用 `ShowWindow(SW_RESTORE)`。
启动时窗口若已按 `window_maximized` 配置进入原生最大化，这次调用会把它降级为普通窗口，
而 Qt 侧 `isMaximized()` 仍为 `True`，导致 kit 无边框窗口的 `WM_NCCALCSIZE` 处理按错误的
最大化状态内缩客户区——系统标题栏与 kit 标题栏同时可见，用户看到两组窗口按钮与整体偏移的布局。

## 要解决的问题

- 现象：仅当 `configs/_ok.json` 的 `window_maximized` 为 `true` 时，每次启动都出现双标题栏
  （两组最小化/最大化/关闭）与内容下移的布局偏差；普通窗口启动不出现。

- 不做的后果：这是启动第一屏的视觉错误，用户无法通过拖动或点最大化自愈，只能先还原再最大化。
  最大化后关闭是常见使用习惯，等于大部分用户每次启动都命中。

## 候选方案

至少两个候选，必须包含「什么都不做」。

| 方案 | 做法 | 代价 | 前提 |
|---|---|---|---|
| A 跳过启动期 `SW_RESTORE` | 在 `on_show_main_window` 把实例 `bring_to_front` 换成安全版：仅当 `IsIconic()` 为真时 `SW_RESTORE`，其余只做 `BringWindowToTop` + `SetForegroundWindow` | 需依赖上游方法名；上游改名后断言崩溃 | 上游 `bring_to_front` 是普通实例方法，可被实例属性遮蔽 |
| B A + 状态自愈守卫 | 额外监听 `windowStateChanged`，发现「非最大化但客户区被内缩」时补 `SWP_FRAMECHANGED` | 多一条常驻监听；覆盖范围超出本 bug | DPI 变化/系统吸附加等场景确实复现同类错位 |
| C 事后补偿 | 保留 `SW_RESTORE`，随后 `showMaximized()` + `SWP_FRAMECHANGED` 重算框架 | 启动时窗口先缩小再放大，可见闪烁 | 平台行为稳定可预期 |
| 不做 | 让用户自己先还原再最大化 | 启动第一屏即错 | 上游自行修复 `bring_to_front` |

**否决理由与重新可行前提**：

- B 被否决：用户明确选最小改动；其覆盖的触发条件（DPI 变化、吸附加）本次未复现，属另一条行为面。
  出现非启动路径的同类错位时重新立项。
- C 被否决：实测可自愈（见下「实测证据」第 4 行），但闪烁代价不必要——根治点是那次多余的
  `SW_RESTORE`，跳过它就不需要补偿。若发现跳过 `SW_RESTORE` 会导致置前失败且闪烁可接受，重评。
- 「不做」被否决：上游无修复计划，且这是启动即坏的视觉错误。

## 实现手段

按依赖深度从浅到深：显式扩展点 → 继承覆写 → 观察事件 → 侵入式替换。

- 选定哪一级：**事件订阅与钩子（路径 3）** 中的 `on_show_main_window`。项目已在
  `extensions/app_globals.py` 里把该生命周期事件派发给能力包，本能力只需实现同名钩子。

- 若上升过级别，逐级说明上一级为何不可行：

  | 上一级 | 为何不可行 |
  |---|---|
  | 路径 1 继承 + config 注册 | `bring_to_front` 是 `MainWindow` 的方法，而 `MainWindow` 由上游 `_create_main_window()` 直接构造，没有任何 config 键可以替换窗口类（`custom_tabs` / `my_app` / `onetime_tasks` 都换不了主窗口本身） |
  | 路径 2 子类化 `OK` 覆写 `do_show_main` | 可行但代价更大：要在 `extensions/` 里重建窗口构造与显示流程，且四个入口脚本各改一行、四处都要登记 `divergence.yaml`。本次只需在窗口显示前改一个实例方法属性，不值得动四个上游入口 |

  「实例属性遮蔽方法」本身不修改类，属路径 3 的范围内，不构成路径 4 的 patch。

## 实测证据

验证关键假设时跑过的操作与结果。**可复现是硬要求**——后人要能重跑一遍确认结论仍然成立。

```text
# 环境：Windows 10 19045 / 2560x1440 / DPI 96（缩放 100%）/ ok-script 3.0.0b3 / PySide6 (ok_pyside_minimal 6.12.0.post2)

# 1. 只创建 KitWindow，最大化后调用 SW_RESTORE（复现缺陷）
QApplication + KitWindow(1100x700) + setWindowState(WindowMaximized) + show()
  -> [shown]             isMax=True  zoomed=True  win=2576x1416 client=2560x1400
     ShowWindow(hwnd, SW_RESTORE)
  -> [after_SW_RESTORE]  isMax=False zoomed=False win=1100x700  client=1084x684   # 客户区被内缩 16px
  截图确认出现系统标题栏 + kit 标题栏两套按钮

# 2. 同样的窗口，跳过 SW_RESTORE（本次修复的做法）
  -> [after_fixed_bring] isMax=True  zoomed=True  win=2576x1416 client=2560x1400
  截图确认只有一套按钮、无黑边、内容对齐

# 3. 用户最小化后由任务栏还原（确认安全版仍能正确处理最小化）
  ShowWindow(SW_MINIMIZE) -> ShowWindow(SW_RESTORE)
  -> [after_minimize_restore] isMax=True zoomed=True win=2576x1416 client=2560x1400

# 4. 对照：SW_RESTORE 后用 SWP_FRAMECHANGED 重算框架可自愈（方案 C 的依据）
  -> [after_FRAMECHANGED] isMax=False zoomed=False win=1100x700 client=1100x700

# 5. 真实应用启动验证（temp 配置目录，window_maximized=true）
  打补丁前：win=(1306x749) client=(1290x733) zoomed=False，截图可见双标题栏与下移布局
  打补丁后：win=(2576x1416) client=(2560x1400) zoomed=True，截图铺满屏幕、单套按钮
```

实测同时确认 `MainWindow.bring_to_front` 有三处调用点，实例属性遮蔽可全部覆盖：
`MainWindow.showEvent` 的 `QTimer.singleShot(0, self.bring_to_front)`、
`OK.do_show_main`、`OK._apply_language`（语言切换重建窗口后）。

## 结论

选方案 A：在 `on_show_main_window` 钩子里用安全版替换主窗口实例的 `bring_to_front`。
它直击根因（启动期多余的一次 `SW_RESTORE`），不引入常驻监听、不产生可见闪烁、不改上游文件。

## 未解问题

- 跳过 `SW_RESTORE` 后 `SetForegroundWindow` 是否仍在所有前台锁场景下生效。
- 普通窗口（`window_maximized: false`）启动是否与本改动完全等价。
