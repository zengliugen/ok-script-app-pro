# -*- coding: utf-8 -*-
"""替换主窗口的置前逻辑，避免启动时把已最大化的窗口降级。

上游 `MainWindow.bring_to_front()` 在 Windows 分支**无条件**调用
`win32gui.ShowWindow(hwnd, SW_RESTORE)`。启动时窗口若已按 `window_maximized` 配置进入
原生最大化，这次调用会把它降级成普通窗口，而 Qt 侧 `isMaximized()` 仍是 `True`：

- 原生侧：窗口变回带 `WS_CAPTION` 的普通窗口，系统标题栏可见；
- Qt 侧：`ok.ui.qt.kit.window.KitWindow` 的 `WM_NCCALCSIZE` 处理按 `self.isMaximized()`
  给客户区加上边框内缩。

两侧状态错位后，系统标题栏与 kit 标题栏同时可见——用户看到两组最小化/最大化/关闭按钮，
内容整体下移 16px。本模块在最大化窗口上跳过那次 `SW_RESTORE`，只保留置前所需的调用。

为什么只对「最大化且未最小化」的窗口改道：这两个分支之外的情形（普通窗口、最小化后还原）
`SW_RESTORE` 本来就是正确的，直接交回上游实现，避免复制它的全部逻辑——上游若在该方法里
新增行为，那些分支会自动跟进，只有最大化分支是刻意保留的本地实现。
"""

import os

from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

# 打上本能力标记后重复 protect 直接返回，保证幂等。
_PROTECTED_FLAG = "_window_restore_guard_protected"
# 被替换掉的原实现引用，便于排查与回退。
_ORIGINAL_ATTR = "_window_restore_guard_original_bring_to_front"


def protect(main_window: object) -> bool:
    """把 `main_window.bring_to_front` 换成不会降级最大化窗口的版本。

    Args：
        main_window：上游 `MainWindow` 实例。

    Returns：
        True 表示本次完成替换，False 表示此前已替换过（幂等）。

    Raises：
        RuntimeError：目标方法不存在或已在构造函数之外被替换，说明上游已重构。
    """
    original = getattr(main_window, "bring_to_front", None)
    if original is None or not callable(original):
        raise RuntimeError(
            "窗口还原守卫的目标缺失：MainWindow 上不存在可调用的 bring_to_front，"
            "请检查 ok-script 升级后更新 extensions/capabilities/window_restore_guard/guard.py"
        )
    if getattr(main_window, _PROTECTED_FLAG, False):
        return False

    setattr(main_window, _ORIGINAL_ATTR, original)

    def bring_to_front():
        """置前主窗口，仅在有必要时才发出系统还原请求。"""
        if _needs_native_restore_skipped(main_window):
            _raise_maximized_window(main_window)
        else:
            original()

    main_window.bring_to_front = bring_to_front
    setattr(main_window, _PROTECTED_FLAG, True)
    return True


def _needs_native_restore_skipped(main_window: object) -> bool:
    """判断当前窗口是否需要走「跳过系统还原」的置前分支。

    Args：
        main_window：`MainWindow` 实例。

    Returns：
        True 表示窗口已最大化且未被最小化，此时 `SW_RESTORE` 会把它降级。
    """
    if os.name != "nt":
        return False
    return bool(main_window.isMaximized()) and not bool(main_window.isMinimized())


def _raise_maximized_window(main_window: object) -> None:
    """对已最大化的窗口执行置前，不发 `SW_RESTORE`。

    与上游实现相比只省略了 `ShowWindow(SW_RESTORE)`；Qt 侧的 `show` / `raise_` /
    `activateWindow` 与 Win32 侧的 `BringWindowToTop` / `SetForegroundWindow` 全部保留。

    Args：
        main_window：`MainWindow` 实例。
    """
    main_window.show()
    main_window.raise_()
    main_window.activateWindow()
    handle = main_window.windowHandle()
    if handle is not None:
        handle.requestActivate()
    try:
        import win32gui

        hwnd = int(main_window.winId())
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:  # 与上游一致：置前失败只记录，不阻断启动
        logger.debug(f"bring_to_front native activation failed: {exc}")
