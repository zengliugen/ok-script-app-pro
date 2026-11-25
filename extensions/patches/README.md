# patch 目录

存放对框架的 monkey patch，**一个目标一个文件**。

## 准入条件

patch 是最后手段。新增 patch 前必须确认规则 `project-extension-capability/methods.mdc` 中路径 1、2、3 都不可行，
并在差异登记条目里写明各自失败的原因。

## 文件约定

每个 patch 模块必须提供 `apply()` 函数，且满足：

1. 开头断言目标符号存在，缺失即抛 `RuntimeError` 并指名。
2. 幂等：重复执行无副作用。
3. 保留原实现引用，便于排查与恢复。

## 当前状态

| 文件 | 目标 | 为什么要 patch |
|---|---|---|
| `main_window_tab_section.py` | `ok.ui.qt.MainWindow.add_page` | `custom_tabs` 的装配循环把分区硬编码为 `SECTION_GAME`，config 无可注册的分组字段；`MainWindow` 由框架内部构造，无法子类化替换；`add_page` 不派发钩子。四条路径只剩 patch 可行，详见模块 docstring |

`main_window_tab_section.py` 在上游原生支持（模块出现 `_tab_section`）时自动让位，
`ok-script` 升级后若 `add_page` 改名会在启动时抛错并指名，不会静默退回「全挂游戏分组」。
