# -*- coding: utf-8 -*-
"""场景管理统一浮层提示入口（`notify.py`）的测试。

**这个模块是所有提示的唯一出口**，它的分级映射错了，全场景管理的提示颜色就
会集体失准——而「红色的失败提示变成蓝色」这种错误不会有任何报错。
"""

import unittest

try:
    from PySide6.QtWidgets import QApplication, QWidget

    QT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 GUI 依赖
    QApplication = None
    QWidget = None
    QT_ERROR = str(exc)

from extensions.capabilities.scene_manager import notify


class TestToneMapping(unittest.TestCase):
    """级别到框架 tone 的映射。"""

    def test_levels_map_to_framework_tones(self):
        """四个业务级别各对应一个框架 tone。

        映射错的后果：`error` 落到 neutral 会变成灰色，用户根本看不出「没做成」。
        """
        self.assertEqual(notify._tone("info"), "accent")
        self.assertEqual(notify._tone("success"), "success")
        self.assertEqual(notify._tone("warning"), "warning")
        self.assertEqual(notify._tone("error"), "danger")

    def test_error_uses_danger_not_warning(self):
        """错误与警告必须区分开——两者的处置方式不同。"""
        self.assertNotEqual(notify._tone("error"), notify._tone("warning"))

    def test_unknown_level_raises(self):
        """拼错的级别要报错，不静默回退。

        静默回退意味着提示以错误颜色出现，而「看起来没那么严重」的失败提示比
        没有提示更糟——用户会以为操作成功了。
        """
        with self.assertRaises(ValueError):
            notify._tone("errror")


class TestDurations(unittest.TestCase):
    """默认停留时长。"""

    def test_warning_and_error_stay_longer(self):
        """警告与错误停留更久：它们需要读完再行动。"""
        self.assertGreater(notify._DEFAULT_DURATION["warning"], notify._DEFAULT_DURATION["success"])
        self.assertGreater(notify._DEFAULT_DURATION["error"], notify._DEFAULT_DURATION["info"])

    def test_every_level_has_duration(self):
        """每个级别都有默认时长，调用方可以省略这个参数。"""
        for level in ("info", "success", "warning", "error"):
            self.assertIn(level, notify._DEFAULT_DURATION)


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class TestNotify(unittest.TestCase):
    """真的弹出浮层。"""

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def _host(self):
        """造一个已显示的空窗口。

        Returns:
            `QWidget` 实例（已 `show()`，否则浮层无法定位）。
        """
        from ok.ui.qt.kit import theme

        host = QWidget()
        theme.install(host)
        host.resize(800, 500)
        host.show()
        for _ in range(4):
            self.app.processEvents()
        self.addCleanup(host.deleteLater)
        return host

    def _toasts(self, host):
        """取该窗口上的浮层。"""
        from ok.ui.qt.kit.feedback import Toast

        for _ in range(4):
            self.app.processEvents()
        return host.findChildren(Toast)

    def test_notify_shows_one_toast(self):
        """`notify` 弹出一条浮层，文案与标题如实带出。"""
        host = self._host()
        notify.notify_warning(host, "探测会真实操作游戏", "需要先启动应用")
        toasts = self._toasts(host)
        self.assertEqual(len(toasts), 1)
        self.assertEqual(toasts[0].tone, "warning")
        self.assertEqual(toasts[0].title_label.text(), "需要先启动应用")
        self.assertIn("探测会真实操作游戏", toasts[0].message_label.text())

    def test_notify_returns_toast(self):
        """返回浮层实例，调用方可以做后续断言或手动关闭。"""
        host = self._host()
        toast = notify.notify_info(host, "已同步 3 个场景")
        self.assertIsNotNone(toast)
        self.assertEqual(toast.tone, "accent")

    def test_notify_without_title(self):
        """省略标题时标题为空，正文照常显示。"""
        host = self._host()
        toast = notify.notify_success(host, "试走成功")
        self.assertEqual(toast.title_label.text(), "")
        self.assertIn("试走成功", toast.message_label.text())

    def test_notify_none_parent_is_safe(self):
        """父控件为 None 时安静返回 None，不抛异常。

        提示是辅助手段，不能因为「没有可挂的窗口」把正常业务带崩。
        """
        self.assertIsNone(notify.notify(None, "x"))

    def test_notify_does_not_leak_between_windows(self):
        """浮层挂在传入控件的窗口上，不会跑到别的窗口。"""
        first = self._host()
        second = self._host()
        notify.notify_info(first, "只属于第一个窗口")
        self.assertEqual(len(self._toasts(first)), 1)
        self.assertEqual(len(self._toasts(second)), 0)


if __name__ == "__main__":
    unittest.main()
