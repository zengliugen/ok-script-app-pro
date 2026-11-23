# -*- coding: utf-8 -*-
"""两个「编辑模板图」入口：画布右键与特征列表右键。

## 为什么要有两个入口

坐标框上右键是用户最自然的入口——他正看着那个框，想调的就是框里裁出来的模板。
要求他先去右侧列表里找到同名项再右键，等于把「我已经知道是哪个」的信息丢掉重找。

但两个入口必须给出**同一套菜单项与启用状态**：各写一份的话，图上能点、列表里
不能点这类不一致迟早出现，而用户无法判断是 bug 还是设计。

## 启用规则的判据

只有**模板匹配**特征有模板图。OCR 特征没有可处理的对象，菜单项保留但置灰——
直接隐藏会让用户以为「这个特征不支持编辑模板」，置灰能传达「不是这个类型」。
"""

import shutil
import tempfile
import unittest

from extensions.capabilities.scene_manager import model, shapes, store

try:
    from PySide6.QtWidgets import QApplication, QMenu

    QT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 GUI 依赖
    QApplication = None
    QMenu = None
    QT_ERROR = str(exc)


def _app():
    """取或建 QApplication（无 GUI 环境下构造控件需要它）。

    Returns:
        QApplication 实例。
    """
    return QApplication.instance() or QApplication([])


class _MenuRecorder(QMenu):
    """记录菜单项而不真正弹出的 QMenu 替身。

    Note:
        `exec` 是 C++ 绑定方法，无法在实例上覆盖；必须替换**类**。而编辑器模块
        在顶部 `from PySide6.QtWidgets import QMenu` 已经拿到了类对象，因此
        除了 `QtWidgets.QMenu` 之外还要替换 `scene_editor.QMenu`——只改前者时
        侧栏那条路径仍用旧类，测出来的菜单项是空的。
    """

    captured = []

    def exec(self, *args, **kwargs):
        """记录 actions 并返回 None。

        Args:
            *args: 忽略。
            **kwargs: 忽略。

        Returns:
            None（等价于用户关闭了菜单）。
        """
        del args, kwargs
        type(self).captured = [
            (action.text(), action.isEnabled()) for action in self.actions() if not action.isSeparator()
        ]
        return None


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class TestTemplateImageMenu(unittest.TestCase):
    """两个入口的菜单项。"""

    def setUp(self):
        """建临时库、一个模板特征与一个 OCR 特征，并打开编辑器。"""
        self.app = _app()
        self.temp_dir = tempfile.mkdtemp(prefix="scene-menu-")
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.library = store.SceneLibrary(self.temp_dir)
        self.library.ensure_layout()
        self.library.set_base_resolution(640, 360)

        scene = self.library.new_scene("测试场景")
        scene.features = [
            model.Feature(
                name="文字特征",
                kind=model.KIND_OCR,
                text="开始",
                shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.2),
            ),
            model.Feature(
                name="模板特征",
                kind=model.KIND_TEMPLATE,
                shape=shapes.Shape.rectangle(0.1, 0.3, 0.3, 0.5),
            ),
        ]
        scene.areas = [
            model.Area(name="区域", rect=(0.5, 0.5, 0.7, 0.7)),
        ]
        self.library.save_scene(scene)

        from extensions.capabilities.scene_manager.pages import scene_editor

        self.editor_module = scene_editor
        self._original_module_menu = scene_editor.QMenu
        self._original_qt_menu = QMenu
        import PySide6.QtWidgets as qt_widgets

        self._qt_widgets = qt_widgets
        self._original_qt_attr = qt_widgets.QMenu
        qt_widgets.QMenu = _MenuRecorder
        scene_editor.QMenu = _MenuRecorder
        self.addCleanup(self._restore_menu)

        self.dialog = scene_editor.SceneEditorDialog(self.library, scene.scene_id)
        self.addCleanup(self.dialog.deleteLater)

    def _restore_menu(self):
        """还原被替换的 QMenu，避免污染其他用例。"""
        self._qt_widgets.QMenu = self._original_qt_attr
        self.editor_module.QMenu = self._original_module_menu

    def _canvas_items(self, key):
        """取画布右键菜单项。

        Args:
            key: `("feature"|"area", name)`。

        Returns:
            `[(文本, 是否启用)]` 列表。
        """
        self.dialog._on_canvas_context("menu", key)
        return list(_MenuRecorder.captured)

    def _list_items(self, row):
        """取特征列表右键菜单项。

        Args:
            row: 特征下标。

        Returns:
            `[(文本, 是否启用)]` 列表。
        """
        rect = self.dialog.feature_list.visualItemRect(self.dialog.feature_list.item(row))
        self.dialog._on_feature_menu(rect.center())
        return list(_MenuRecorder.captured)

    # --- 画布入口 ---------------------------------------------------------

    def test_canvas_template_feature_has_template_action(self):
        """画布上右键模板特征：有「编辑模板图」且可点。"""
        items = self._canvas_items(("feature", "模板特征"))
        texts = [text for text, _enabled in items]
        self.assertIn("Edit Template Image", texts)
        enabled = dict(items)["Edit Template Image"]
        self.assertTrue(enabled, "模板特征上该项必须可点")

    def test_canvas_ocr_feature_disables_template_action(self):
        """画布上右键 OCR 特征：菜单项在但置灰。

        置灰而不是隐藏：用户点不到任何东西时会怀疑「是不是我没找到」，
        灰掉的项能明确告诉他「这个类型不支持」。
        """
        items = self._canvas_items(("feature", "文字特征"))
        self.assertIn("Edit Template Image", [text for text, _enabled in items])
        self.assertFalse(dict(items)["Edit Template Image"], "OCR 特征上该项必须置灰")

    def test_canvas_area_has_no_template_action(self):
        """互动区域没有模板图，菜单里不出现该项。"""
        items = self._canvas_items(("area", "区域"))
        self.assertNotIn("Edit Template Image", [text for text, _enabled in items])

    def test_canvas_menu_keeps_existing_actions(self):
        """原有三项（复制/编辑属性/删除）仍在，加功能不能挤掉它们。"""
        items = self._canvas_items(("feature", "模板特征"))
        texts = [text for text, _enabled in items]
        for expected in ("Copy", "Edit Properties", "Delete"):
            self.assertIn(expected, texts)

    def test_canvas_edit_uses_consistent_label(self):
        """画布菜单的「编辑」与侧栏一致，都叫「编辑属性」。

        两处叫法不同（「编辑」vs「编辑属性」）会让用户以为是两个功能。
        """
        items = self._canvas_items(("feature", "模板特征"))
        self.assertIn("Edit Properties", [text for text, _enabled in items])

    # --- 列表入口 ---------------------------------------------------------

    def test_list_template_feature_has_template_action(self):
        """特征列表右键模板特征：有「编辑模板图」且可点。"""
        items = self._list_items(1)
        self.assertIn("Edit Template Image", [text for text, _enabled in items])
        self.assertTrue(dict(items)["Edit Template Image"])

    def test_list_ocr_feature_disables_template_action(self):
        """特征列表右键 OCR 特征：该项置灰。"""
        items = self._list_items(0)
        self.assertIn("Edit Template Image", [text for text, _enabled in items])
        self.assertFalse(dict(items)["Edit Template Image"])

    # --- 两个入口一致 -----------------------------------------------------

    def test_both_entries_offer_same_template_action(self):
        """同一特征在两个入口上的「编辑模板图」启用状态一致。

        不一致时用户会以为「图上能调、列表里不能调」是功能差异，
        而实际是其中一个入口漏了判断。
        """
        canvas = dict(self._canvas_items(("feature", "模板特征")))
        listed = dict(self._list_items(1))
        self.assertEqual(
            canvas.get("Edit Template Image"),
            listed.get("Edit Template Image"),
        )
        self.assertTrue(canvas.get("Edit Template Image"))


if __name__ == "__main__":
    unittest.main()
