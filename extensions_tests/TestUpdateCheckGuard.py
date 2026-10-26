# -*- coding: utf-8 -*-
"""更新检查守卫测试。

被测对象是 `extensions/core/update_check_guard.py` 与 `extensions/core/app_globals.py`。
卡片摘除涉及 Qt 控件生命周期，用轻量替身验证「什么条件下摘、摘的是哪个对象」，
不启动真实 GUI——GUI 装配本身由上游保证，这里只关心本项目的判据与副作用。
"""

import unittest

from extensions.core import update_check_guard
from extensions.overrides import LIST_OVERRIDES

try:
    from src.config import config

    IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 ok / PySide6 等运行依赖
    config = None
    IMPORT_ERROR = str(exc)


class _FakeWidget:
    """记录父子关系变更的控件替身。"""

    def __init__(self, name: str = "widget") -> None:
        """初始化替身。

        Args：
            name：调试用名字。
        """
        self.name = name
        self._parent = None
        self.deleted = False

    def parent(self):
        """返回父控件。"""
        return self._parent

    def setParent(self, parent) -> None:
        """设置父控件。"""
        self._parent = parent

    def deleteLater(self) -> None:
        """记录已请求销毁。"""
        self.deleted = True


class _FakeCard(_FakeWidget):
    """带标题的卡片容器替身，等价于上游 Card。"""

    def __init__(self) -> None:
        """初始化并声明标题标签属性。"""
        super().__init__("card")
        self.titleLabel = object()


class _FakeAboutTab:
    """About 页替身，只保留守卫需要的最小接口。"""

    def __init__(self, update_card) -> None:
        """初始化。

        Args：
            update_card：卡片控件，None 表示上游没有创建更新卡片。
        """
        self.update_card = update_card
        self.removed = []

    def removeWidget(self, widget) -> None:
        """记录被摘除的控件。"""
        self.removed.append(widget)


def _make_window(launcher_available: bool):
    """构造一棵等价的最小控件树。

    Args：
        launcher_available：假启动器探测结果。

    Returns：
        (main_window 替身, about_tab, 卡片容器)。
    """
    container = _FakeCard()
    inner = _FakeWidget("frame")
    update_card = _FakeWidget("update_card")
    update_card._parent = inner
    inner._parent = container
    about_tab = _FakeAboutTab(update_card)
    window = _FakeWidget("main_window")
    window.about_tab = about_tab
    window.launcher_available = launcher_available
    return window, about_tab, container


class TestUpdateCheckGuard(unittest.TestCase):
    """验证摘除判据与副作用。"""

    def test_card_removed_when_launcher_missing(self) -> None:
        """启动器不存在时摘掉卡片，并清空 about_tab.update_card。"""
        window, about_tab, container = _make_window(False)
        removed = update_check_guard.apply(window, launcher_probe=lambda: None)
        self.assertTrue(removed)
        self.assertEqual([container], about_tab.removed, "应摘除包裹卡片的容器而非内部 frame")
        self.assertTrue(container.deleted, "容器应被请求销毁")
        self.assertIsNone(about_tab.update_card, "置空后上游不再排更新检查定时器")

    def test_card_kept_when_launcher_available(self) -> None:
        """启动器可用时不动界面，检查失败仍由上游照常报错。"""
        window, about_tab, _ = _make_window(True)
        removed = update_check_guard.apply(window, launcher_probe=lambda: "C:/x/Launcher.exe")
        self.assertFalse(removed)
        self.assertEqual([], about_tab.removed)
        self.assertIsNotNone(about_tab.update_card)

    def test_no_op_when_update_card_absent(self) -> None:
        """上游未创建更新卡片时直接返回，不抛错。"""
        about_tab = _FakeAboutTab(None)
        window = _FakeWidget("main_window")
        window.about_tab = about_tab
        self.assertFalse(update_check_guard.apply(window, launcher_probe=lambda: None))
        self.assertEqual([], about_tab.removed)

    def test_raises_when_container_structure_changed(self) -> None:
        """容器层级不符合预期时崩溃并指名缺失项，不静默跳过。"""
        update_card = _FakeWidget("update_card")
        about_tab = _FakeAboutTab(update_card)
        window = _FakeWidget("main_window")
        window.about_tab = about_tab
        with self.assertRaises(RuntimeError) as ctx:
            update_check_guard.apply(window, launcher_probe=lambda: None)
        self.assertIn("Card", str(ctx.exception))


@unittest.skipIf(config is None, f"缺少运行依赖，跳过：{IMPORT_ERROR}")
class TestMyAppOverride(unittest.TestCase):
    """验证 my_app 已换成项目子类。"""

    def test_my_app_points_to_project_globals(self) -> None:
        """my_app 指向 AppGlobals，而不是上游 Globals。"""
        self.assertEqual(["extensions.core.app_globals", "AppGlobals"], config["my_app"])

    def test_app_globals_inherits_upstream(self) -> None:
        """AppGlobals 继承上游 Globals，保证上游行为不被丢弃。"""
        from extensions.core.app_globals import AppGlobals
        from src.globals import Globals as UpstreamGlobals

        self.assertTrue(issubclass(AppGlobals, UpstreamGlobals))

    def test_list_override_registered(self) -> None:
        """列表覆写表已登记 my_app，供缺键校验使用。"""
        self.assertIn("my_app", LIST_OVERRIDES)


if __name__ == "__main__":
    unittest.main()
