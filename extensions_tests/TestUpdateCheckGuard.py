# -*- coding: utf-8 -*-
"""更新检查守卫测试。

被测对象是 `extensions/capabilities/update_check_guard/guard.py` 与 `extensions/app_globals.py`。
分两层：

1. **替身层**（`_FakeAboutTab` 等）：验证「什么条件下摘、摘的是哪个对象」。替身必须与真实
   结构一致，不得提供真实类没有的方法——否则会掩盖接口漂移（见下文契约测试的说明）。
2. **契约层**（`TestAboutTabContract`）：用**真实** `AboutTab` 跑一遍守卫。替身层再仔细，
   也只能证明「代码符合我们对上游的假设」；契约层才能证明「假设本身仍然成立」。
"""

import os
import unittest

from extensions.capabilities.update_check_guard import guard as update_check_guard
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


class _FakeLayout:
    """记录被摘除控件的布局替身，等价于 QVBoxLayout。"""

    def __init__(self) -> None:
        """初始化空的摘除记录。"""
        self.removed = []

    def removeWidget(self, widget) -> None:
        """记录从布局中摘除的控件。"""
        self.removed.append(widget)


class _FakeAboutTab:
    """About 页替身，只保留守卫需要的最小接口。

    注意：**不提供 `removeWidget`**。`AboutTab` 是 QWidget 子类，`removeWidget` 属于
    QLayout；替身若自带该方法，守卫误用 `about_tab.removeWidget(...)` 时测试仍会通过，
    启动时才会以 AttributeError 崩溃（本项目实际踩过）。替身必须与真实结构一致：
    区块挂在 `vBoxLayout` 上，并由 `sections` 列表索引。
    """

    def __init__(self, update_card) -> None:
        """初始化。

        Args：
            update_card：卡片控件，None 表示上游没有创建更新卡片。
        """
        self.update_card = update_card
        self.vBoxLayout = _FakeLayout()
        self.sections = []

    @property
    def removed(self) -> list:
        """返回从布局中摘除的控件列表，供断言使用。"""
        return self.vBoxLayout.removed


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
        """启动器不存在时摘掉整个区块，并清空 about_tab.update_card。"""
        window, about_tab, container = _make_window(False)
        removed = update_check_guard.apply(window, launcher_probe=lambda: None)
        self.assertTrue(removed)
        self.assertEqual([container], about_tab.removed, "应摘除包裹卡片的整块而非内部 frame")
        self.assertNotIn(container, about_tab.sections, "sections 索引也须同步移除")
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
        self.assertIn("AboutSection", str(ctx.exception))

    def test_raises_when_layout_missing(self) -> None:
        """About 页缺少 vBoxLayout 时崩溃并指名缺失项。"""
        window, about_tab, _ = _make_window(False)
        del about_tab.vBoxLayout
        with self.assertRaises(RuntimeError) as ctx:
            update_check_guard.apply(window, launcher_probe=lambda: None)
        self.assertIn("vBoxLayout", str(ctx.exception))

    def test_does_not_rely_on_about_tab_remove_widget(self) -> None:
        """守卫不得调用 about_tab.removeWidget——AboutTab 上并没有该方法。

        回归用例：真实 AboutTab 是 QWidget 子类，removeWidget 属于 QLayout。此前守卫
        误调 about_tab.removeWidget，而测试替身自带该方法，导致单元测试全绿、启动即报
        AttributeError: 'AboutTab' object has no attribute 'removeWidget'。
        """
        window, about_tab, container = _make_window(False)
        self.assertFalse(hasattr(about_tab, "removeWidget"), "替身不该提供该方法")
        self.assertTrue(update_check_guard.apply(window, launcher_probe=lambda: None))
        self.assertEqual([container], about_tab.removed, "应通过 vBoxLayout 摘除区块")


def _real_about_tab_available() -> tuple[bool, str]:
    """探测能否构造真实 `AboutTab`（需要 Qt 平台插件与上游 GUI 依赖）。

    使用离屏平台，不弹窗口、不依赖桌面会话。

    Returns：
        (可用, 不可用原因)。可用时原因为空串。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # noqa: BLE001 - 缺 Qt 绑定属于环境问题，跳过而非失败
        return False, f"缺少 PySide6：{exc}"
    try:
        if QApplication.instance() is None:
            QApplication([])
        import pyappify  # noqa: F401
        from ok.ui.qt.about.AboutTab import AboutTab  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - 上游结构变化由契约测试自身暴露，此处只做可用性探测
        return False, f"缺少上游 GUI 依赖：{exc}"
    return True, ""


_REAL_TAB_OK, _REAL_TAB_REASON = _real_about_tab_available()


@unittest.skipIf(not _REAL_TAB_OK, f"无法构造真实 AboutTab，跳过契约测试：{_REAL_TAB_REASON}")
class TestAboutTabContract(unittest.TestCase):
    """用真实 `AboutTab` 验证守卫与上游接口的契约。

    为什么必须有这一层：替身层只能证明「代码符合我们对上游的假设」，无法发现假设本身
    已经过期。本项目实际发生过——守卫调用 `about_tab.removeWidget()`，而测试替身恰好
    实现了这个方法，单元测试全绿、覆盖率 100%，启动时才以 AttributeError 崩溃。
    契约测试直接拿上游真实控件跑，替身漂移无处藏身。
    """

    def _build(self):
        """构造真实 About 页包装成的 MainWindow 替身。

        Returns：
            (window 替身, about_tab, update_card 所在的 AboutSection)。
        """
        import pyappify
        from ok.ui.qt.about.AboutTab import AboutTab

        about_tab = AboutTab({"version": "1.0.0", "links": {}}, pyappify_module=pyappify)
        self.assertIsNotNone(
            about_tab.update_card,
            "上游未创建更新卡片，契约测试失去验证对象（检查 pyappify.get_version_list 是否可用）",
        )
        section = about_tab.update_card.parent().parent()
        window = _FakeWidget("main_window")
        window.about_tab = about_tab
        return window, about_tab, section

    def test_real_about_tab_has_no_remove_widget(self) -> None:
        """真实 AboutTab 不提供 removeWidget，守卫不得依赖它。"""
        _, about_tab, _ = self._build()
        self.assertFalse(
            hasattr(about_tab, "removeWidget"),
            "上游若新增了 removeWidget，契约已变化，需重新评估守卫实现",
        )

    def test_real_about_tab_exposes_layout_and_sections(self) -> None:
        """真实 AboutTab 暴露 vBoxLayout 与 sections，这是守卫依赖的两个成员。"""
        _, about_tab, section = self._build()
        self.assertTrue(hasattr(about_tab, "vBoxLayout"), "缺少 vBoxLayout，守卫无法摘除区块")
        self.assertIsInstance(about_tab.sections, list, "sections 应为列表")
        self.assertIn(section, about_tab.sections, "更新卡片所在的区块应在 sections 中")

    def test_apply_removes_section_on_real_about_tab(self) -> None:
        """在真实 AboutTab 上执行摘除，区块从布局与 sections 中消失、update_card 置空。"""
        window, about_tab, section = self._build()
        self.assertTrue(update_check_guard.apply(window, launcher_probe=lambda: None))
        self.assertNotIn(section, about_tab.sections, "区块应从 sections 索引移除")
        self.assertIsNone(about_tab.update_card, "update_card 应置空以停止定时器")
        self.assertIsNone(section.parent(), "区块应已脱离父控件")

    def test_apply_keeps_section_when_launcher_available(self) -> None:
        """启动器可用时真实控件不被改动。"""
        window, about_tab, section = self._build()
        self.assertFalse(update_check_guard.apply(window, launcher_probe=lambda: "C:/x/Launcher.exe"))
        self.assertIn(section, about_tab.sections)
        self.assertIsNotNone(about_tab.update_card)

    def test_find_card_container_matches_real_structure(self) -> None:
        """`_find_card_container` 的层级假设在真实控件上成立。"""
        _, about_tab, section = self._build()
        found = update_check_guard._find_card_container(about_tab.update_card)
        self.assertIs(found, section, "应定位到包裹 UpdateCard 的 AboutSection")
        self.assertTrue(hasattr(found, "titleLabel"), "区块应带标题标签")


@unittest.skipIf(config is None, f"缺少运行依赖，跳过：{IMPORT_ERROR}")
class TestMyAppOverride(unittest.TestCase):
    """验证 my_app 已换成项目子类。"""

    def test_my_app_points_to_project_globals(self) -> None:
        """my_app 指向 AppGlobals，而不是上游 Globals。"""
        self.assertEqual(["extensions.app_globals", "AppGlobals"], config["my_app"])

    def test_app_globals_inherits_upstream(self) -> None:
        """AppGlobals 继承上游 Globals，保证上游行为不被丢弃。"""
        from extensions.app_globals import AppGlobals
        from src.globals import Globals as UpstreamGlobals

        self.assertTrue(issubclass(AppGlobals, UpstreamGlobals))

    def test_list_override_registered(self) -> None:
        """列表覆写表已登记 my_app，供缺键校验使用。"""
        self.assertIn("my_app", LIST_OVERRIDES)


if __name__ == "__main__":
    unittest.main()
