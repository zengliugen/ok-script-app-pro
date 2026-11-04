# -*- coding: utf-8 -*-
"""窗口还原守卫测试。

被测对象是 `extensions/capabilities/window_restore_guard/guard.py`。分两层：

1. **决策层**：验证「什么情况下改道、什么情况下交回上游」。用替身窗口，不真实显示窗口——
   缺陷的成因是窗口状态与系统调用的错位，与界面无关，替身足以固定判据。
2. **契约层**：验证真实 `main_window` 上确实存在 `bring_to_front`，也就是守卫的替换目标
   仍然存在。替身层再仔细，也只能证明「代码符合我们对上游的假设」；契约层才能证明
   「假设本身仍然成立」。

为什么要测「最大化分支不调上游实现」：上游实现在 Windows 上无条件发 `SW_RESTORE`，
最大化窗口一旦走到它就会降级，而这正是本能力要消除的现象。这条判据不写测试固定下来，
下次有人简化成「直接调用上游」时不会有任何信号。
"""

import unittest
from unittest import mock

from extensions.capabilities.window_restore_guard import guard

try:
    from ok.ui.qt.MainWindow import MainWindow

    IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 ok / PySide6 等运行依赖
    MainWindow = None
    IMPORT_ERROR = str(exc)


class _FakeWindow:
    """只保留守卫会用到的接口的窗口替身。

    刻意**不提供** `ShowWindow`，也不实现任何 Qt 方法之外的便利方法：守卫若误用真实
    `QWidget` 没有的接口，替身会立刻抛 AttributeError，而不是让测试静默通过。
    """

    def __init__(self, maximized: bool, minimized: bool = False) -> None:
        """按给定状态构造替身。

        Args：
            maximized：`isMaximized()` 的返回值。
            minimized：`isMinimized()` 的返回值。
        """
        self._maximized = maximized
        self._minimized = minimized
        self.calls = []
        self.original_calls = 0

    def isMaximized(self) -> bool:
        """返回是否最大化。"""
        return self._maximized

    def isMinimized(self) -> bool:
        """返回是否最小化。"""
        return self._minimized

    def bring_to_front(self) -> None:
        """上游实现的替身：记录被调用次数。"""
        self.original_calls += 1

    def show(self) -> None:
        """记录 show 调用。"""
        self.calls.append("show")

    def raise_(self) -> None:
        """记录 raise_ 调用。"""
        self.calls.append("raise_")

    def activateWindow(self) -> None:
        """记录 activateWindow 调用。"""
        self.calls.append("activateWindow")

    def windowHandle(self):
        """返回 None：替身不模拟原生窗口句柄。"""
        return None

    def winId(self) -> int:
        """返回无效句柄：替身不模拟真实窗口。"""
        return 0


class TestWindowRestoreGuard(unittest.TestCase):
    """守卫的替换行为与幂等性。"""

    def test_replaces_bring_to_front(self):
        """替换后实例属性指向新函数，且原实现被保留下来可回退。"""
        window = _FakeWindow(maximized=True)
        original = window.bring_to_front

        self.assertTrue(guard.protect(window))

        self.assertIsNot(window.bring_to_front, original)
        kept = window._window_restore_guard_original_bring_to_front
        # 每次取属性都会新建绑定方法对象，只能比较底层函数与绑定对象。
        self.assertEqual(kept.__func__, original.__func__)
        self.assertIs(kept.__self__, window)

    def test_protect_is_idempotent(self):
        """重复调用不叠加包装，第二次返回 False。"""
        window = _FakeWindow(maximized=True)
        guard.protect(window)
        replaced = window.bring_to_front

        self.assertFalse(guard.protect(window))
        self.assertIs(window.bring_to_front, replaced)

    def test_missing_target_raises(self):
        """目标方法不存在时立即崩溃并指名，不静默跳过。"""

        class _NoTarget:
            """没有 bring_to_front 的窗口替身。"""

        with self.assertRaises(RuntimeError) as ctx:
            guard.protect(_NoTarget())
        self.assertIn("bring_to_front", str(ctx.exception))

    def test_non_callable_target_raises(self):
        """目标存在但不可调用时同样视为上游已重构。"""

        class _BadTarget:
            """bring_to_front 不是函数的窗口替身。"""

            bring_to_front = None

        with self.assertRaises(RuntimeError):
            guard.protect(_BadTarget())


class TestMaximizedWindowBranch(unittest.TestCase):
    """最大化窗口：跳过上游实现，改走不发 `SW_RESTORE` 的置前。"""

    def test_maximized_window_bypasses_original(self):
        """最大化且未最小化时，不得调用上游实现（它会发 SW_RESTORE）。"""
        window = _FakeWindow(maximized=True)
        guard.protect(window)

        with mock.patch.object(guard, "_raise_maximized_window") as raised:
            window.bring_to_front()

        self.assertEqual(window.original_calls, 0)
        raised.assert_called_once_with(window)

    def test_minimized_window_defers_to_original(self):
        """最小化后的窗口需要 SW_RESTORE，必须交回上游实现。"""
        window = _FakeWindow(maximized=True, minimized=True)
        guard.protect(window)

        with mock.patch.object(guard, "_raise_maximized_window") as raised:
            window.bring_to_front()

        self.assertEqual(window.original_calls, 1)
        raised.assert_not_called()

    def test_normal_window_defers_to_original(self):
        """普通窗口本就该被还原/置前，交回上游实现才能跟进上游改动。"""
        window = _FakeWindow(maximized=False)
        guard.protect(window)

        with mock.patch.object(guard, "_raise_maximized_window") as raised:
            window.bring_to_front()

        self.assertEqual(window.original_calls, 1)
        raised.assert_not_called()

    def test_maximized_branch_keeps_foreground_calls(self):
        """改道分支仍执行 Qt 侧置前动作，不能只省略系统调用却忘了置前。"""
        window = _FakeWindow(maximized=True)
        guard.protect(window)

        window.bring_to_front()

        self.assertEqual(window.calls, ["show", "raise_", "activateWindow"])


@unittest.skipIf(MainWindow is None, f"缺少运行依赖，跳过契约测试：{IMPORT_ERROR}")
class TestMainWindowContract(unittest.TestCase):
    """真实 `MainWindow` 上替换目标仍然存在。"""

    def test_main_window_provides_bring_to_front(self):
        """上游若无条件删掉/重命名该方法，本能力应即刻失效可见，而非静默不生效。"""
        self.assertTrue(callable(getattr(MainWindow, "bring_to_front", None)))


if __name__ == "__main__":
    unittest.main()
