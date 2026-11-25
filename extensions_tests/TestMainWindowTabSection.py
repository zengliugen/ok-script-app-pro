# -*- coding: utf-8 -*-
"""页签声明式分组补丁测试。

分两层，结论不混用：

1. **纯函数层**：分组选择与 `add_page` 包装，不依赖 Qt。它证明「声明合法就采用、
   未声明或非法就沿用原值」这条判据本身正确。
2. **集成层**：对真实 `ok.ui.qt.MainWindow` 调用 `apply()`，用假 self 走完
   `add_page` 的真实实现。它证明补丁确实装在框架类上、参数透传与幂等成立。
   需要 PySide6，缺依赖时整类跳过。

为什么不直接构造 `MainWindow`：它的构造会创建整窗、读配置、拉设备，测试环境
不具备这些前提。`add_page` 只用到 `self._pages` / `self.stack` / `self.navigation`
三个属性，用假 self 调用即可覆盖补丁改写的全部分支。
"""

import types
import unittest

from extensions.patches import main_window_tab_section as patch_module

VALID_SECTIONS = frozenset({"run", "game", "develop"})

try:
    from ok.ui.qt import MainWindow as main_window_module

    QT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 PySide6 等运行依赖
    main_window_module = None
    QT_ERROR = str(exc)


class TestSectionFor(unittest.TestCase):
    """分组选择的判据：声明优先，未知值回退。"""

    def test_declared_section_wins(self):
        """页面声明合法分组时采用声明值。"""
        widget = types.SimpleNamespace(section="develop")
        self.assertEqual(patch_module.section_for(widget, "game", VALID_SECTIONS), "develop")

    def test_missing_section_keeps_incoming(self):
        """页面没有 section 属性时沿用调用方传入的分组。"""
        self.assertEqual(patch_module.section_for(object(), "game", VALID_SECTIONS), "game")

    def test_unknown_section_falls_back_to_incoming(self):
        """声明了拼错的分组时回退到传入值，而不是把页面挂进不存在的分组。"""
        widget = types.SimpleNamespace(section="develo")
        self.assertEqual(patch_module.section_for(widget, "game", VALID_SECTIONS), "game")

    def test_none_incoming_stays_none(self):
        """未声明且调用方也没传分组时保持 None，不凭空造一个分组。"""
        self.assertIsNone(patch_module.section_for(object(), None, VALID_SECTIONS))


class TestMakeAddPage(unittest.TestCase):
    """包装后的 add_page 只改 section，其余参数原样透传。"""

    def test_only_section_is_rewritten(self):
        """声明分组覆盖传入值，self 与其余参数逐个保持原对象。"""
        calls = []

        def original(self, widget, icon, text, section=None, bottom=False):
            calls.append(
                {"self": self, "widget": widget, "icon": icon, "text": text, "section": section, "bottom": bottom}
            )
            return "page_key"

        wrapped = patch_module.make_add_page(original, VALID_SECTIONS)
        holder = object()
        widget = types.SimpleNamespace(section="develop")

        result = wrapped(holder, widget, "image", "标题", "game", bottom=True)

        self.assertEqual(result, "page_key")
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertIs(call["self"], holder)
        self.assertIs(call["widget"], widget)
        self.assertEqual(call["icon"], "image")
        self.assertEqual(call["text"], "标题")
        self.assertEqual(call["section"], "develop")
        self.assertTrue(call["bottom"])

    def test_missing_section_keeps_caller_value(self):
        """没有声明分组的页面维持调用方传入的分组。"""
        calls = []

        def original(self, widget, icon, text, section=None, bottom=False):
            calls.append(section)

        wrapped = patch_module.make_add_page(original, VALID_SECTIONS)
        wrapped(object(), types.SimpleNamespace(), "image", "标题", "run")

        self.assertEqual(calls, ["run"])


class _FakeWidget:
    """替身页面：只提供 add_page 用到的 objectName 与 section。"""

    def __init__(self, section=None, name="page"):
        self.section = section
        self._name = name

    def objectName(self):
        """返回固定的控件名。"""
        return self._name


class _FakeStack:
    """替身页面栈：记录挂载的控件。"""

    def __init__(self):
        self.widgets = []

    def addWidget(self, widget):
        """记录被挂载的控件。"""
        self.widgets.append(widget)


class _FakeNavigation:
    """替身导航栏：记录每个导航项的分组。"""

    def __init__(self):
        self.calls = []

    def add_item(self, key, text, icon, section, bottom=False):
        """记录一次导航项挂载。"""
        self.calls.append((key, text, section, bottom))


class _FakeWindow:
    """替身窗口：add_page 只用到这三个属性。"""

    def __init__(self):
        self._pages = {}
        self.stack = _FakeStack()
        self.navigation = _FakeNavigation()


@unittest.skipIf(main_window_module is None, f"缺少 PySide6，跳过真实框架集成：{QT_ERROR}")
class TestApplyOnRealMainWindow(unittest.TestCase):
    """把补丁装到真实 `MainWindow` 上验证。"""

    def setUp(self):
        """记录类属性原值，供每个用例后恢复。"""
        self.cls = main_window_module.MainWindow
        self.saved = {
            "add_page": self.cls.__dict__.get("add_page"),
            patch_module._PATCHED_FLAG: self.cls.__dict__.get(patch_module._PATCHED_FLAG),
            patch_module._ORIGINAL_ATTR: self.cls.__dict__.get(patch_module._ORIGINAL_ATTR),
        }

    def tearDown(self):
        """把类属性恢复到用例执行前的状态。"""
        for name, value in self.saved.items():
            if value is None:
                if name in self.cls.__dict__:
                    delattr(self.cls, name)
            else:
                setattr(self.cls, name, value)

    def test_apply_wraps_add_page(self):
        """上游无原生支持时，apply 替换 add_page 并保留原实现引用。"""
        if callable(getattr(main_window_module, "_tab_section", None)):
            self.skipTest("上游已原生支持 _tab_section，包装分支不适用")

        patch_module.apply()

        self.assertTrue(getattr(self.cls, patch_module._PATCHED_FLAG, False))
        self.assertIsNot(self.cls.__dict__.get("add_page"), self.saved["add_page"])
        self.assertIs(self.cls.__dict__.get(patch_module._ORIGINAL_ATTR), self.saved["add_page"])

    def test_wrapped_add_page_uses_declared_section(self):
        """走真实 add_page 时，声明 develop 的页面被挂到 develop 分组。"""
        if callable(getattr(main_window_module, "_tab_section", None)):
            self.skipTest("上游已原生支持 _tab_section，包装分支不适用")

        patch_module.apply()
        page = _FakeWidget("develop")
        window = _FakeWindow()

        self.cls.add_page(window, page, "image", "标题", main_window_module.SECTION_GAME)

        self.assertEqual(window.navigation.calls, [("page", "标题", "develop", False)])

    def test_apply_is_idempotent(self):
        """重复 apply 不会二次包装。"""
        if callable(getattr(main_window_module, "_tab_section", None)):
            self.skipTest("上游已原生支持 _tab_section，包装分支不适用")

        patch_module.apply()
        wrapped = self.cls.__dict__.get("add_page")
        patch_module.apply()

        self.assertIs(self.cls.__dict__.get("add_page"), wrapped)

    def test_apply_yields_to_upstream_support(self):
        """上游出现 _tab_section 时不包装，直接让位。"""
        main_window_module._tab_section = lambda tab: "develop"
        try:
            patch_module.apply()
            self.assertIs(self.cls.__dict__.get("add_page"), self.saved["add_page"])
            self.assertTrue(getattr(self.cls, patch_module._PATCHED_FLAG, False))
        finally:
            del main_window_module._tab_section


if __name__ == "__main__":
    unittest.main()
