# -*- coding: utf-8 -*-
"""场景编辑弹窗与配置页预览的测试。

两个被测对象：

- `pages/canvas.py`：多矩形画布的坐标换算、缩放锚点、手柄调整、预览模式只读；
- `pages/scene_editor.py` + `pages/config_page.py`：弹窗编辑与外部只读预览。

重点不是渲染像素，而是「数据与界面必须一致」：CanvasItem 的矩形来自场景数据、
拖动后写回场景数据、保存后落盘文件与内存一致。
"""

import os
import shutil
import tempfile
import unittest

from extensions.capabilities.scene_manager import model, shapes, store

try:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtWidgets import QApplication, QWidget

    from extensions.capabilities.scene_manager.pages import canvas as canvas_module
    from extensions.capabilities.scene_manager.pages import config_page, scene_editor

    QT_ERROR = None
except ModuleNotFoundError as exc:
    QApplication = None
    canvas_module = None
    config_page = None
    scene_editor = None
    QT_ERROR = str(exc)

try:
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:
    np = None
    NUMPY_ERROR = str(exc)


def _striped(width, height):
    """造一张有条纹的图，避免零方差模板提示干扰断言。"""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[::4, :, :] = 255
    return image


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestSceneCanvas(unittest.TestCase):
    """画布的坐标与交互。"""

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """造一张 800x600 的底图并设成固定控件尺寸。"""
        from PySide6.QtGui import QImage

        self.canvas = canvas_module.SceneCanvas()
        self.canvas.resize(800, 600)
        image = QImage(800, 600, QImage.Format.Format_RGB888)
        image.fill(0x404040)
        self.canvas.set_image(image)

    def tearDown(self):
        """销毁控件。"""
        self.canvas.deleteLater()

    def test_fit_scale_on_load(self):
        """载图后自动取景为整幅进视口（800x600 图与控件同尺寸时为 1.0）。"""
        self.assertAlmostEqual(self.canvas.zoom_scale(), 1.0, places=3)

    def test_background_follows_theme(self):
        """画布底色跟随主题 `sidebar`，与标注编辑器同色，不再写死深灰。"""
        from ok.ui.qt.kit import theme

        expected = theme.color(canvas_module.BACKGROUND_COLOR_NAME)
        actual = canvas_module.background_color()
        self.assertEqual(actual.name(), expected.name())
        # 深灰是改前写死的值，回归时会被这条拦下。
        self.assertNotEqual(actual.name(), "#2e3036")

    def test_frame_to_view_center(self):
        """帧中心应映射到控件中心附近。"""
        point = self.canvas._frame_to_view((400, 300))
        self.assertAlmostEqual(point.x(), 400, delta=2)
        self.assertAlmostEqual(point.y(), 300, delta=2)

    def test_view_to_frame_roundtrip(self):
        """帧 → 控件 → 帧 往返误差小于 1 像素。"""
        for original in ((0, 0), (123, 456), (799, 599)):
            point = self.canvas._frame_to_view(original)
            back = self.canvas._view_to_frame(point)
            self.assertAlmostEqual(back[0], original[0], delta=1)
            self.assertAlmostEqual(back[1], original[1], delta=1)

    def test_zoom_at_keeps_anchor(self):
        """以锚点缩放时，锚点下的帧坐标不变。"""
        anchor = QPointF(200, 150)
        before = self.canvas._view_to_frame(anchor)
        self.canvas._zoom_at(2.0, anchor)
        after = self.canvas._view_to_frame(anchor)
        self.assertAlmostEqual(before[0], after[0], delta=1)
        self.assertAlmostEqual(before[1], after[1], delta=1)

    def test_zoom_limits(self):
        """缩放被夹在上下限内。"""
        self.canvas._zoom_at(1000.0, QPointF(0, 0))
        self.assertLessEqual(self.canvas.zoom_scale(), canvas_module.MAX_SCALE)
        self.canvas._zoom_at(0.00001, QPointF(0, 0))
        self.assertGreaterEqual(self.canvas.zoom_scale(), self.canvas.fit_scale() - 1e-9)

    def test_zoom_reset_is_one_to_one(self):
        """1:1 后比例为 1.0。"""
        self.canvas._zoom_at(2.0, QPointF(0, 0))
        self.canvas.zoom_reset()
        self.assertAlmostEqual(self.canvas.zoom_scale(), 1.0, places=6)

    def test_moved_rect_stays_inside_frame(self):
        """整体移动时矩形被夹在画面内。"""
        moved = self.canvas._moved_rect((0, 0, 100, 100), -50, -50)
        self.assertEqual(moved[:2], (0.0, 0.0))
        moved = self.canvas._moved_rect((0, 0, 100, 100), 10000, 10000)
        self.assertAlmostEqual(moved[2], 800)
        self.assertAlmostEqual(moved[3], 600)

    def test_resized_rect_handles(self):
        """四角手柄各自只动对应的两条边。"""
        rect = (100, 100, 300, 300)
        self.assertEqual(self.canvas._resized_rect(rect, "nw", 10, 20), (110, 120, 300, 300))
        self.assertEqual(self.canvas._resized_rect(rect, "se", 10, 20), (100, 100, 310, 320))
        self.assertEqual(self.canvas._resized_rect(rect, "n", 0, 10), (100, 110, 300, 300))
        self.assertEqual(self.canvas._resized_rect(rect, "e", 10, 0), (100, 100, 310, 300))

    def test_resize_flip_is_normalized(self):
        """拖过对边时自动纠正左右/上下翻转。"""
        flipped = self.canvas._resized_rect((100, 100, 300, 300), "w", 500, 0)
        self.assertLess(flipped[0], flipped[2])
        self.assertLess(flipped[1], flipped[3])

    def test_resize_never_degenerates(self):
        """矩形不会退化成零面积（否则相对比例换算会除零）。"""
        rect = self.canvas._resized_rect((100, 100, 300, 300), "e", -500, 0)
        self.assertGreaterEqual(rect[2] - rect[0], 1)
        self.assertGreaterEqual(rect[3] - rect[1], 1)

    def test_preview_only_blocks_selection(self):
        """预览模式下不命中任何矩形，选中恒为 None。"""
        self.canvas.set_preview_only(True)
        self.canvas.set_items([canvas_module.CanvasItem(("feature", "a"), (10, 10, 100, 100))])
        self.assertIsNone(self.canvas._hit_item(QPoint(50, 50)))
        self.assertIsNone(self.canvas.selected_key())

    def test_editable_mode_hits_item(self):
        """非预览模式能命中矩形。"""
        self.canvas.set_items([canvas_module.CanvasItem(("feature", "a"), (10, 10, 100, 100))])
        hit = self.canvas._hit_item(QPoint(50, 50))
        self.assertIsNotNone(hit)
        self.assertEqual(hit.key, ("feature", "a"))

    def test_non_editable_item_has_no_handles(self):
        """只读项不出手柄，避免误以为可拖。"""
        self.canvas.set_preview_only(False)
        item = canvas_module.CanvasItem(("mask", "mask"), (10, 10, 100, 100), editable=False)
        self.canvas.set_items([item])
        self.assertIsNone(self.canvas._hit_handle(QPoint(10, 10), item))

    def test_set_selected_emits_only_on_change(self):
        """重复设置同一选中项不重复发信号。"""
        self.canvas.set_items([canvas_module.CanvasItem(("feature", "a"), (10, 10, 100, 100))])
        received = []
        self.canvas.selection_changed.connect(received.append)
        self.canvas.set_selected(("feature", "a"))
        self.canvas.set_selected(("feature", "a"))
        self.assertEqual(len(received), 1)

    def _double_click(self, point):
        """在给定控件坐标上造一次左键双击事件。

        Args：
            point：`QPoint` 控件坐标。

        Returns：
            发出的 key 列表（每次双击一项）。
        """
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        received = []
        self.canvas.double_clicked.connect(received.append)
        position = QPointF(point)
        # 用带 scenePos / globalPos 的重载：只传 localPos 的版本已被 Qt 标记废弃，
        # 调用时会刷 DeprecationWarning 淹没测试输出。
        event = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            position,
            position,
            position,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.canvas.mouseDoubleClickEvent(event)
        return received

    def test_small_image_is_centered_without_padding(self):
        """图像小于视口时强制居中，不留可拖动的空白。

        改前允许任一侧留 60px 空白，缩到适配时四周凭空多出可拖动范围，用户会
        以为「图像没居中」。与标注编辑器一致：小于视口就居中。
        """
        # 底图 800x600，控件放大到 1600x1200 后缩到适配 → 图像远小于视口
        self.canvas.resize(1600, 1200)
        self.canvas.zoom_fit()
        scaled_w = 800 * self.canvas.zoom_scale()
        scaled_h = 600 * self.canvas.zoom_scale()

        offset = self.canvas._offset
        self.assertAlmostEqual(offset.x(), (1600 - scaled_w) / 2, delta=1)
        self.assertAlmostEqual(offset.y(), (1200 - scaled_h) / 2, delta=1)

    def test_large_image_cannot_be_dragged_past_edge(self):
        """图像大于视口时偏移夹到边界，两侧不留黑边。"""
        # 控件缩到 400x300，配合 scale=1 让 800x600 的图超出视口
        self.canvas.resize(400, 300)
        self.canvas._scale = 1.0

        # 往右下猛拖：左上不应出现空白
        clamped = self.canvas._clamp_offset(QPointF(500, 500), 1.0)
        self.assertLessEqual(clamped.x(), 0)
        self.assertLessEqual(clamped.y(), 0)

        # 往左上猛拖：右下不应出现空白
        clamped = self.canvas._clamp_offset(QPointF(-500, -500), 1.0)
        self.assertGreaterEqual(clamped.x(), 400 - 800)
        self.assertGreaterEqual(clamped.y(), 300 - 600)

    def test_zoom_cannot_go_below_fit(self):
        """缩放下限夹到「适配视口」，不会把图像缩成小点。"""
        self.canvas.resize(400, 300)
        fit = self.canvas.fit_scale()

        self.canvas._zoom_at(fit * 0.1, QPointF(200, 150))
        self.assertGreaterEqual(self.canvas.zoom_scale(), fit - 1e-9)

    def test_hover_state_tracks_hit(self):
        """悬停在框上时记录该框，移开清空。"""
        self.canvas.set_items([canvas_module.CanvasItem(("feature", "a"), (10, 10, 200, 150))])
        self.canvas.set_mode(canvas_module.MODE_SELECT)
        self.canvas._scale = 1.0
        self.canvas._offset = QPointF(0, 0)

        self.canvas._hover_at(QPoint(100, 80))
        self.assertEqual(self.canvas._hovered, ("feature", "a"))

        self.canvas._hover_at(QPoint(600, 500))
        self.assertIsNone(self.canvas._hovered)

    def test_handle_hit_on_unselected_item(self):
        """手柄命中不要求该项已选中，与标注编辑器一致。

        改前必须先单击选中才能拖手柄，多一步；框密集时用户也分不清该先点哪一下。
        """
        self.canvas.set_items([canvas_module.CanvasItem(("feature", "a"), (100, 100, 300, 250))])
        self.canvas._scale = 1.0
        self.canvas._offset = QPointF(0, 0)
        self.assertIsNone(self.canvas.selected_key(), "前提：未选中")

        item, handle = self.canvas._hit_any_handle(QPoint(100, 100))
        self.assertIsNotNone(item, "未选中时也应命中手柄")
        self.assertEqual(item.key, ("feature", "a"))
        self.assertEqual(handle, canvas_module.HANDLE_TL)

    def test_handle_hit_prioritized_over_body(self):
        """手柄优先于框体：框贴在一起时拖手柄更常见。"""
        self.canvas.set_items(
            [
                canvas_module.CanvasItem(("feature", "a"), (100, 100, 300, 250)),
                canvas_module.CanvasItem(("feature", "b"), (300, 100, 500, 250)),
            ]
        )
        self.canvas._scale = 1.0
        self.canvas._offset = QPointF(0, 0)

        item, handle = self.canvas._hit_any_handle(QPoint(300, 175))
        self.assertIsNotNone(item, "两框交界处应命中手柄")
        self.assertIn(
            handle,
            (canvas_module.HANDLE_LEFT, canvas_module.HANDLE_RIGHT),
            f"交界处应是左右边手柄，实际 {handle}",
        )

    def test_double_click_emits_hit_key(self):
        """双击命中的框时发出该框的 key，并把它设为选中。"""
        self.canvas.set_items([canvas_module.CanvasItem(("feature", "a"), (10, 10, 100, 100))])
        received = self._double_click(QPoint(50, 50))
        self.assertEqual(received, [("feature", "a")])
        self.assertEqual(self.canvas.selected_key(), ("feature", "a"))

    def test_double_click_on_blank_emits_none(self):
        """双击空白处发出 None——预览页正是靠这个语义「双击画面任意处」。"""
        self.canvas.set_items([canvas_module.CanvasItem(("feature", "a"), (10, 10, 100, 100))])
        received = self._double_click(QPoint(600, 500))
        self.assertEqual(received, [None])

    def test_double_click_in_preview_mode_always_none(self):
        """预览模式下即使点在框上也发 None（框只读，没有「选中某个框」的语义）。"""
        self.canvas.set_preview_only(True)
        self.canvas.set_items([canvas_module.CanvasItem(("feature", "a"), (10, 10, 100, 100))])
        received = self._double_click(QPoint(50, 50))
        self.assertEqual(received, [None])

    def test_double_click_ignored_in_draw_mode(self):
        """画新框的模式下双击不发信号，避免刚画完一框又弹窗。"""
        self.canvas.set_mode(canvas_module.MODE_DRAW_FEATURE)
        self.canvas.set_items([canvas_module.CanvasItem(("feature", "a"), (10, 10, 100, 100))])
        received = self._double_click(QPoint(50, 50))
        self.assertEqual(received, [])


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestPropertyDialogDynamic(unittest.TestCase):
    """属性对话框的动态参数区。

    用户实测缺陷：新建区域时动作默认单击、单击没有参数，于是切到「长按」也看不到
    「按住时长」输入框。

    根因是参数在**打开对话框时**按当时动作静态生成了一次，之后下拉变化不重建。改法
    是把参数区做成动态区，随控制器取值重建，同时用高度下限保证布局不跳。
    """

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, action=model.ACTION_CLICK):
        """造一个带动态参数区的对话框。

        Args：
            action：初始动作。

        Returns：
            `PropertyDialog` 实例。
        """
        fields = [
            ("name", "Name", "x", None),
            *scene_editor._explore_action_field(action),
            ("after_wait", "Wait", "1.0", None),
        ]
        dialog = scene_editor.PropertyDialog(
            "T", fields, dynamic_params=("explore_action", scene_editor._explore_param_fields)
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _switch(self, dialog, action):
        """切到指定动作并处理事件。"""
        combo = dialog._fields["explore_action"][0]
        combo.setCurrentIndex(model.EXPLORE_ACTIONS.index(action))
        self.app.processEvents()

    def test_toggle_field_returns_bool(self):
        """`TOGGLE_FIELD` 渲染成开关，取值是布尔而不是下拉选项。"""
        from ok.ui.qt.kit import Switch

        dialog = scene_editor.PropertyDialog(
            "T",
            [
                ("name", "Name", "x", None),
                ("flag", "Flag", False, scene_editor.TOGGLE_FIELD),
            ],
        )
        self.addCleanup(dialog.deleteLater)
        editor = dialog._fields["flag"][0]
        self.assertIsInstance(editor, Switch)
        self.assertFalse(dialog.values()["flag"])
        editor.setChecked(True)
        self.assertTrue(dialog.values()["flag"])

    def test_params_appear_after_switching_action(self):
        """切到长按后出现「按住时长」输入框——这条正是用户报的缺陷。"""
        dialog = self._dialog(model.ACTION_CLICK)
        self.assertEqual(list(dialog._dynamic_fields), [], "单击没有参数")

        self._switch(dialog, model.ACTION_LONG_PRESS)
        self.assertIn("param_duration", dialog._dynamic_fields)
        self.assertEqual(dialog.values()["param_duration"], "1")

    def test_swipe_has_two_params(self):
        """滑动有两个参数：距离与方向。"""
        dialog = self._dialog()
        self._switch(dialog, model.ACTION_SWIPE)
        self.assertEqual(sorted(dialog._dynamic_fields), ["param_direction", "param_distance"])
        values = dialog.values()
        self.assertEqual(values["param_distance"], "0.3")
        self.assertEqual(values["param_direction"], model.SWIPE_UP)

    def test_switching_back_keeps_filled_value(self):
        """记住用户填过的值：切走再切回，2.5 秒不该丢。"""
        dialog = self._dialog()
        self._switch(dialog, model.ACTION_LONG_PRESS)
        dialog._dynamic_fields["param_duration"][0].setText("2.5")

        self._switch(dialog, model.ACTION_SWIPE)
        self._switch(dialog, model.ACTION_LONG_PRESS)
        self.assertEqual(dialog.values()["param_duration"], "2.5")

    def test_set_dynamic_values_used_as_initial(self):
        """灌入的初值优先于模型默认值。

        不灌初值的话，编辑一个填过 3.5 秒的长按区域会显示默认的 1，用户会以为值
        被改了。
        """
        dialog = self._dialog(model.ACTION_LONG_PRESS)
        dialog.set_dynamic_values({"param_duration": 3.5})
        self.app.processEvents()
        self.assertEqual(dialog.values()["param_duration"], "3.5")

    def test_no_placeholder_row_when_action_has_no_params(self):
        """无参数时**不插任何行**，也不显示提示文字。

        用户明确要求「没有参数的时候直接不显示那个提示」。早先为固定高度插过一行
        「该动作没有额外参数」占位，用户不要这个。

        现在靠「整个对话框固定为最大自然高度」来稳住按钮位置，多余空间落在按钮
        上方——所以去掉占位行不会引起跳动。
        """
        dialog = self._dialog(model.ACTION_LONG_PRESS)
        rows_with_params = dialog._form.rowCount()

        self._switch(dialog, model.ACTION_CLICK)
        self.assertEqual(dialog._dynamic_rows, [], "单击不应插任何动态行")
        self.assertEqual(dialog._dynamic_fields, {})
        self.assertEqual(dialog._form.rowCount(), rows_with_params - 1, "少一行参数，不留占位")

    def test_measure_picks_tallest_not_first(self):
        """测量取的是**最大**自然高度，不是第一个取值的。

        直接调 `_measure_and_freeze` 并在调用前后不补 `processEvents`——这条守的是
        「测量本身算对」，而 `test_height_frozen_at_tallest_variant` 走的是完整
        `show()` 流程，用例自己的 `processEvents` 会把布局提前稳定，掩盖测量阶段的
        缺陷（实测去掉 `_measure_and_freeze` 里的 `processEvents` 后那条仍通过）。
        """
        dialog = self._dialog(model.ACTION_SWIPE)
        dialog.show()
        for _ in range(12):
            self.app.processEvents()

        fixed = dialog.height()
        # 单击（无参数）的自然高度一定小于滑动（两行参数）。若测量只取了第一个
        # 取值的高度，这里会得到单击的高度，比滑动矮两行。
        self._switch(dialog, model.ACTION_CLICK)
        for _ in range(4):
            self.app.processEvents()
        self.assertGreater(fixed, dialog.sizeHint().height(), "固定高度应大于无参数时的自然高度")

    def test_height_frozen_at_tallest_variant(self):
        """对话框高度固定为所有取值里的最大自然高度，按钮位置不随动作变化。

        三条要求同时成立：标签共列（参数行插进主表单）、无参数不留占位、按钮不跳。
        靠的是在首次显示后逐值量高、取最大、`setFixedHeight`。
        """
        from PySide6.QtWidgets import QDialogButtonBox

        dialog = self._dialog(model.ACTION_CLICK)
        dialog.show()
        for _ in range(12):
            self.app.processEvents()

        box = dialog.findChild(QDialogButtonBox)
        heights = []
        positions = []
        for action in model.EXPLORE_ACTIONS:
            self._switch(dialog, action)
            for _ in range(4):
                self.app.processEvents()
            heights.append(dialog.height())
            positions.append(box.mapTo(dialog, box.rect().topLeft()).y())

        self.assertEqual(len(set(heights)), 1, f"高度必须固定，实测 {heights}")
        self.assertEqual(len(set(positions)), 1, f"按钮位置必须固定，实测 {positions}")
        self.assertEqual(
            dialog.minimumHeight(),
            dialog.maximumHeight(),
            "固定高度意味着 min == max",
        )

    def test_dynamic_rows_follow_controller_row(self):
        """动态行紧跟在控制器行之后，且与主表单共用同一个 QFormLayout。

        另建容器 + 独立 QFormLayout 时，标签列宽由它自己算，与主表单对不齐——
        实测「双击间隔（秒）」的标签比「探索动作」更靠左。插进同一个 QFormLayout
        才共享列宽。
        """
        dialog = self._dialog(model.ACTION_SWIPE)
        controller_row = dialog._row_of["explore_action"]
        self.assertEqual(dialog._dynamic_rows, [controller_row + 1, controller_row + 2])

    def test_labels_share_one_column(self):
        """所有标签（含动态行）的右边界对齐在同一列。

        这是「共用 QFormLayout」的直接可观测结果：右对齐标签宽度不同，但右边界相同。
        """
        dialog = self._dialog(model.ACTION_DOUBLE_CLICK)
        dialog.show()
        for _ in range(6):
            self.app.processEvents()

        from PySide6.QtWidgets import QFormLayout

        right_edges = []
        for row in range(dialog._form.rowCount()):
            item = dialog._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is None or item.widget() is None:
                continue
            label = item.widget()
            left = label.mapTo(dialog, label.rect().topLeft()).x()
            right_edges.append(left + label.width())
        self.assertEqual(len(set(right_edges)), 1, f"标签右边界必须一致，实测 {right_edges}")

    def test_params_follow_controller_position(self):
        """参数区插在控制器下方，不排到表单末尾。

        排在末尾时参数会显示在「操作后等待」下面，看着像那条的子项。
        """
        dialog = self._dialog(model.ACTION_LONG_PRESS)
        rows = dialog._row_of
        self.assertLess(rows["explore_action"], rows["after_wait"], "控制器应在等待之前")
        # 动态区插在控制器行之后，因此表单行数比字段数多（插了一行容器）。
        self.assertGreater(dialog._form.rowCount(), len(rows))

    def test_unknown_controller_raises(self):
        """控制器不存在时构造即报错，不静默不生效。"""
        with self.assertRaises(ValueError):
            scene_editor.PropertyDialog(
                "T",
                [("name", "Name", "x", None)],
                dynamic_params=("nope", scene_editor._explore_param_fields),
            )

    def test_text_controller_raises(self):
        """控制器是文本框时报错——文本没有可枚举的取值集合。"""
        with self.assertRaises(ValueError):
            scene_editor.PropertyDialog(
                "T",
                [("name", "Name", "x", None)],
                dynamic_params=("name", scene_editor._explore_param_fields),
            )


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestFeatureKindFields(unittest.TestCase):
    """识别特征按识别方式显示字段。

    用户要求「只显示对应相关的选项，如识别方式为模板匹配的时候不显示期望文本」。

    这不是「少一个字段」的洁癖：模板匹配的判定路径 `match_template(...)` 根本不读
    `feature.text`，保存时也直接写 `text if kind == KIND_OCR else ""`。摆一个填了
    不生效的输入框，用户会反复试而结果永远不变。
    """

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def test_template_has_no_extra_fields(self):
        """模板匹配没有额外字段。"""
        self.assertEqual(scene_editor._feature_kind_fields(model.KIND_TEMPLATE), [])

    def test_ocr_shows_expected_text(self):
        """OCR 显示「期望文本」，且带当前值。"""
        fields = scene_editor._feature_kind_fields(model.KIND_OCR, "靶场入口")
        self.assertEqual(len(fields), 1)
        key, _label, value, options = fields[0]
        self.assertEqual(key, "text")
        self.assertEqual(value, "靶场入口")
        self.assertIsNone(options, "是文本输入，不是下拉")

    def test_dialog_hides_text_under_template(self):
        """对话框在模板匹配下不显示「期望文本」行。"""
        dialog = self._dialog(model.KIND_TEMPLATE)
        labels = self._labels(dialog)
        self.assertNotIn("Expected text (OCR)", labels)
        self.assertNotIn("text", dialog.values())

    def test_dialog_shows_text_under_ocr(self):
        """对话框在 OCR 下显示「期望文本」行。"""
        dialog = self._dialog(model.KIND_OCR, "靶场入口")
        labels = self._labels(dialog)
        self.assertIn("Expected text (OCR)", labels)
        self.assertEqual(dialog.values()["text"], "靶场入口")

    def test_switching_hides_text_and_drops_value(self):
        """切到模板匹配后「期望文本」行消失，值也不再返回。

        值不返回很关键：隐藏字段的编辑器里可能留着 OCR 填过的内容，若 `values()`
        照常返回，调用方就会把只有 OCR 认的参数带进模板匹配特征。
        """
        dialog = self._dialog(model.KIND_OCR, "靶场入口")
        self.assertIn("text", dialog.values())

        combo = dialog._fields["kind"][0]
        combo.setCurrentIndex(model.KIND_TEMPLATE and model.FEATURE_KINDS.index(model.KIND_TEMPLATE))
        self.app.processEvents()

        self.assertNotIn("Expected text (OCR)", self._labels(dialog))
        self.assertNotIn("text", dialog.values())

    def test_text_field_follows_threshold(self):
        """「期望文本」排在「阈值」之后，不紧贴「识别方式」。

        两者合起来构成匹配参数；插在「必需」之前会让它和阈值脱节。
        """
        from PySide6.QtWidgets import QFormLayout

        dialog = self._dialog(model.KIND_OCR, "x")
        order = []
        for row in range(dialog._form.rowCount()):
            item = dialog._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is not None and item.widget() is not None:
                order.append(item.widget().text())
        self.assertLess(order.index("Threshold (0-1)"), order.index("Expected text (OCR)"))

    def test_height_fixed_across_kinds(self):
        """两种识别方式的对话框高度一致，切下拉不跳。"""
        from PySide6.QtWidgets import QDialogButtonBox

        dialog = self._dialog(model.KIND_TEMPLATE)
        dialog.show()
        for _ in range(12):
            self.app.processEvents()

        box = dialog.findChild(QDialogButtonBox)
        combo = dialog._fields["kind"][0]
        sizes = []
        for kind in model.FEATURE_KINDS:
            combo.setCurrentIndex(model.FEATURE_KINDS.index(kind))
            for _ in range(4):
                self.app.processEvents()
            sizes.append((dialog.height(), box.mapTo(dialog, box.rect().topLeft()).y()))
        self.assertEqual(len(set(sizes)), 1, f"高度与按钮位置必须固定，实测 {sizes}")

    def test_input_geometry_stable_across_kinds(self):
        """切换识别方式时，输入框的左边界与宽度不变。

        高度固定解决不了这条：标签列宽由该列**最长标签**决定，「期望文本（OCR 用）」
        一出现就把整列撑宽，其余所有输入框被推着右移并变窄——实测阈值框 x 92→140、
        宽 337→289，视觉上就是用户说的「文本与输入框宽度变了」。

        解法是给标签列定宽（取所有取值里最宽的标签），并显式右对齐。
        """
        dialog = self._dialog(model.KIND_TEMPLATE)
        dialog.show()
        for _ in range(12):
            self.app.processEvents()

        combo = dialog._fields["kind"][0]
        geometries = []
        for kind in model.FEATURE_KINDS:
            combo.setCurrentIndex(model.FEATURE_KINDS.index(kind))
            for _ in range(4):
                self.app.processEvents()
            editor = dialog._fields["threshold"][0]
            left = editor.mapTo(dialog, editor.rect().topLeft()).x()
            geometries.append((left, editor.width()))
        self.assertEqual(len(set(geometries)), 1, f"输入框位置与宽度必须固定，实测 {geometries}")

    def test_label_column_width_fixed(self):
        """所有标签被设为同一固定宽度，且该宽度由最宽标签决定。

        直接断言「列宽已锁定」这条事实，而不是只靠几何巧合：本用例的测试桩标签比
        生产文案短，两列宽度差可能被边距吸收，几何不变也可能通过——那样就在测
        「碰巧没变」而不是「已锁定」。
        """
        dialog = self._dialog(model.KIND_OCR, "靶场入口")
        dialog.show()
        for _ in range(12):
            self.app.processEvents()

        from PySide6.QtWidgets import QFormLayout

        widths = set()
        for row in range(dialog._form.rowCount()):
            item = dialog._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is None or item.widget() is None:
                continue
            widths.add(item.widget().width())
        self.assertEqual(len(widths), 1, f"所有标签宽度必须一致，实测 {widths}")
        self.assertGreater(dialog._label_width, 0, "应记录固定列宽，供后续动态行沿用")

    def test_labels_are_right_aligned(self):
        """标签列定宽后仍然右对齐。

        `QFormLayout.setLabelAlignment` 只对「未定宽」的标签生效；一旦 `setFixedWidth`，
        标签会按自己的默认对齐（左）渲染，表现为「所有标签挤到最左边」，与原先观感
        完全不同。
        """
        dialog = self._dialog(model.KIND_TEMPLATE)
        dialog.show()
        for _ in range(12):
            self.app.processEvents()

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QFormLayout

        for row in range(dialog._form.rowCount()):
            item = dialog._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is None or item.widget() is None:
                continue
            flags = item.widget().alignment()
            self.assertTrue(flags & Qt.AlignmentFlag.AlignRight, f"第 {row} 行标签未右对齐")

    def _dialog(self, kind, text=""):
        """造一个与「新建识别特征」同构的对话框。

        Args：
            kind：初始识别方式。
            text：OCR 期望文本初值。

        Returns：
            `PropertyDialog` 实例。
        """
        fields = [
            ("name", "Name", "f1", None),
            (
                "kind",
                "Recognition Method",
                kind,
                [("Template Match", model.KIND_TEMPLATE), ("Text (OCR)", model.KIND_OCR)],
            ),
            ("mandatory", "Required", True, [("Required", True), ("Optional", False)]),
            ("threshold", "Threshold (0-1)", "0.80", None),
        ]
        dialog = scene_editor.PropertyDialog(
            "T", fields, dynamic_params=("kind", scene_editor._feature_kind_fields, "threshold")
        )
        dialog.set_dynamic_values({"text": text} if text else {})
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _labels(self, dialog):
        """返回对话框当前显示的字段标签列表。"""
        from PySide6.QtWidgets import QFormLayout

        labels = []
        for row in range(dialog._form.rowCount()):
            item = dialog._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is not None and item.widget() is not None:
                labels.append(item.widget().text())
        return labels


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestSceneEditorDialog(unittest.TestCase):
    """弹窗编辑。"""

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """建临时库与一个已存在的场景。"""
        self.temp_dir = tempfile.mkdtemp(prefix="scene-editor-")
        self.library = store.SceneLibrary(os.path.join(self.temp_dir, "scenes"))
        self.library.ensure_layout()
        self.library.set_base_resolution(800, 600)
        self.scene = self.library.new_scene("已有场景")
        self.scene.features.append(
            model.Feature(name="feature-1", shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3), threshold=0.8)
        )
        self.scene.areas.append(model.Area(name="area-1", rect=(0.5, 0.5, 0.7, 0.7), explore_probe=False))
        self.library.save_scene(self.scene)
        self.library.save_base_image(self.scene.scene_id, _striped(800, 600))
        template = _striped(80, 80)
        self.library.save_template(self.scene.scene_id, "feature-1", template)

    def tearDown(self):
        """清理临时目录。"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_dialog(self, scene_id=None):
        """造一个弹窗。

        弹窗不再接收取帧函数：抓图与导入都在外部配置页完成，弹窗只负责编辑已有
        场景。素材（preview.png）在打开前就已经落好盘。

        Args：
            scene_id：场景 id；None 用默认场景。

        Returns：
            `SceneEditorDialog` 实例。
        """
        dialog = scene_editor.SceneEditorDialog(
            self.library,
            scene_id=self.scene.scene_id if scene_id is None else scene_id,
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_edit_existing_loads_data(self):
        """打开已有场景时载入特征与区域。"""
        dialog = self._make_dialog()
        self.assertEqual(len(dialog.scene.features), 1)
        self.assertEqual(dialog.feature_list.count(), 1)
        self.assertEqual(dialog.area_list.count(), 1)
        self.assertEqual(dialog.name_edit.text(), "已有场景")

    def test_list_item_shows_name_and_summary(self):
        """列表每一项两行：名称 + 属性摘要。

        右侧不再用表格，改由列表项承载信息。摘要里必须能看到「识别方式 / 是否必需 /
        位置」这类改完想核对的内容，否则用户只能靠双击逐个打开对话框确认。
        """
        dialog = self._make_dialog()
        feature_text = dialog.feature_list.item(0).text()
        self.assertIn("feature-1", feature_text)
        self.assertIn("Template", feature_text)
        self.assertIn("Required", feature_text)
        self.assertIn("\n", feature_text)

        area_text = dialog.area_list.item(0).text()
        self.assertIn("area-1", area_text)
        # 摘要只含位置与探索开关：目标场景与等待属流转图，不在这里。
        self.assertIn("50,50", area_text, "摘要应含相对坐标")
        self.assertNotIn("s", area_text.split("\n")[-1], "区域摘要不该再出现等待秒数")

    def test_side_header_shows_count(self):
        """分区标题显示元素个数；没有元素时留空而不是显示 0。"""
        dialog = self._make_dialog()
        header_label = dialog._feature_header.property("sideCountLabel")
        self.assertEqual(header_label.text(), "1")
        dialog.scene.features.clear()
        dialog._refresh_lists()
        self.assertEqual(header_label.text(), "")

    def test_canvas_marks_both_kinds(self):
        """画布上同时标出特征与区域，且颜色不同。"""
        dialog = self._make_dialog()
        items = list(dialog.canvas._items.values())
        colors = {item.color for item in items}
        self.assertIn(scene_editor.COLOR_FEATURE_MANDATORY, colors)
        self.assertIn(scene_editor.COLOR_AREA, colors)
        self.assertNotEqual(scene_editor.COLOR_FEATURE_MANDATORY, scene_editor.COLOR_AREA)

    def test_editor_canvas_is_editable(self):
        """弹窗里的画布可编辑（与外部只读预览相反）。"""
        dialog = self._make_dialog()
        self.assertFalse(dialog.canvas.is_preview_only())
        dialog.canvas.set_items([canvas_module.CanvasItem(("feature", "feature-1"), (80, 60, 240, 180))])
        hit = dialog.canvas._hit_item(QPoint(100, 100))
        self.assertIsNotNone(hit)

    def test_resize_recomputes_fit_scale(self):
        """控件尺寸变化时重算适配比例，图像缩进视口而不是按原尺寸铺出去。

        `set_image` 常在控件还没完成布局时被调用（弹窗构造期），那一刻宽高是默认
        值，算出的比例可能是 1.0；之后控件真正变大却不再更新，图像就按原始尺寸
        绘制、四周露出大片空白。实测 1320×860 弹窗里画布 988×749、图像 1920×1080，
        修复前 scale 停在 1.0。
        """
        dialog = self._make_dialog()
        dialog.resize(1320, 860)
        dialog.show()
        for _ in range(6):
            self.app.processEvents()

        canvas = dialog.canvas
        expected = canvas.fit_scale()
        # 关键不是「必须缩小」而是「必须等于当前视口的适配值」——测试用的
        # 800×600 小图放进更大的画布时适配值本就大于 1。修复前这个值会停在
        # set_image 那一刻算出的旧值（1.0 或更早的默认尺寸算出的值）。
        self.assertAlmostEqual(canvas.zoom_scale(), expected, places=6)
        # 缩放后的图像必须完整落在画布内
        self.assertLessEqual(canvas._image.width() * canvas.zoom_scale(), canvas.width() + 1)
        self.assertLessEqual(canvas._image.height() * canvas.zoom_scale(), canvas.height() + 1)

    def test_hover_box_uses_size_all_cursor(self):
        """悬停在框体上光标是四向箭头，与标注编辑器一致。

        标注编辑器的 hover 分支对框体用 `SizeAllCursor`（提示「可移动」），
        只在删除模式下用 `PointingHandCursor`。
        """
        dialog = self._make_dialog()
        dialog.canvas.set_items([canvas_module.CanvasItem(("feature", "f"), (100, 100, 400, 300))])
        dialog.canvas.set_mode(canvas_module.MODE_SELECT)
        dialog.canvas._scale = 1.0
        dialog.canvas._offset = QPointF(0, 0)
        dialog.canvas._update_cursor(QPoint(250, 200))
        self.assertEqual(
            dialog.canvas.cursor().shape(),
            Qt.CursorShape.SizeAllCursor,
        )

    def test_fresh_dialog_is_not_dirty(self):
        """刚打开时无改动：保存按钮灰、切换不弹确认。

        这是用户报的核心问题——什么都没动，点左右切换也弹「未保存的改动将丢失」。
        """
        dialog = self._make_dialog()
        self.assertFalse(dialog._dirty(), "刚打开不应标记为有改动")
        self.assertFalse(dialog.save_button.isEnabled(), "无改动时保存按钮应禁用")

    def test_switch_without_change_does_not_prompt(self):
        """无改动时切换场景不弹确认框。"""
        second = self.library.new_scene("第二个")
        self.library.save_scene(second)
        self.library.save_base_image(second.scene_id, _striped(800, 600))

        dialog = self._make_dialog()
        called = []

        import ok.ui.qt.kit as kit

        original = kit.confirm
        kit.confirm = lambda *args, **kwargs: called.append(1) or True
        try:
            dialog.next_scene()
        finally:
            kit.confirm = original

        self.assertEqual(called, [], "无改动时不该弹确认")
        self.assertEqual(dialog.scene.scene_id, second.scene_id, "应直接切换")

    def test_name_edit_marks_dirty(self):
        """改名字后标记为有改动，保存按钮变可用。"""
        dialog = self._make_dialog()
        dialog.name_edit.setText("新名字")
        dialog._on_name_edited("新名字")
        self.assertTrue(dialog._dirty())
        self.assertTrue(dialog.save_button.isEnabled())

    def test_rect_drag_marks_dirty(self):
        """拖动框后标记为有改动。"""
        dialog = self._make_dialog()
        meta = dialog.library.meta
        pixel = dialog.scene.features[0].pixel_rect(meta.base_width, meta.base_height)
        dialog._on_rect_changed(("feature", "feature-1"), pixel)
        self.assertTrue(dialog._dirty())

    def test_removing_feature_marks_dirty(self):
        """删除特征后标记为有改动。

        走 `_on_canvas_delete`（右键菜单的删除路径），它直接改 `scene.features`
        并置标记。判断「是否真删掉」与「是否标记脏」两件事都要成立——只标记不删
        或者只删不标记，表现都是「保存按钮亮了但内容没变」或反之。
        """
        dialog = self._make_dialog()
        import ok.ui.qt.kit as kit

        original = kit.confirm
        kit.confirm = lambda *args, **kwargs: True
        try:
            dialog._on_canvas_delete(("feature", "feature-1"))
        finally:
            kit.confirm = original
        self.assertTrue(dialog._dirty())
        self.assertIsNone(dialog.scene.find_feature("feature-1"))

    # --- Delete 键删除 ------------------------------------------------------

    def _press(self, widget, key):
        """向控件发送一次按键事件。

        Args：
            widget：目标控件。
            key：`Qt.Key` 值。

        Returns：
            无。
        """
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

        self.app.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
        for _ in range(4):
            self.app.processEvents()

    def _confirm_yes(self):
        """把确认框替换成「一律确认」，并返回记录调用次数的列表。

        Returns：
            列表，每次确认框被调用时追加一项。
        """
        import ok.ui.qt.kit as kit

        called = []
        original = kit.confirm
        kit.confirm = lambda *args, **kwargs: called.append(args) or True
        self.addCleanup(setattr, kit, "confirm", original)
        return called

    def _select_feature_row(self, dialog, row=0):
        """在特征列表里选中一行，并等画布同步。

        Args：
            dialog：`SceneEditorDialog`。
            row：行号。
        """
        dialog.show()
        for _ in range(6):
            self.app.processEvents()
        dialog.feature_list.setCurrentRow(row)
        for _ in range(4):
            self.app.processEvents()

    def test_delete_key_removes_selected_feature(self):
        """列表选中特征后按 Delete，特征被删除。

        **用户报的正是这条**：选中特征后按 Delete 没反应，只能走右键菜单。
        """
        from PySide6.QtCore import Qt

        dialog = self._make_dialog()
        self._confirm_yes()
        self._select_feature_row(dialog, 0)
        self.assertIsNotNone(dialog.canvas.selected_key(), "前置条件：画布应已选中该项")

        self._press(dialog.feature_list, Qt.Key.Key_Delete)

        self.assertIsNone(dialog.scene.find_feature("feature-1"), "Delete 应删掉选中特征")
        self.assertTrue(dialog._dirty(), "删除后应标记为有改动")

    def test_delete_key_removes_selected_area(self):
        """区域列表选中后按 Delete，区域被删除。"""
        from PySide6.QtCore import Qt

        dialog = self._make_dialog()
        self._confirm_yes()
        dialog.show()
        for _ in range(6):
            self.app.processEvents()
        dialog.area_list.setCurrentRow(0)
        for _ in range(4):
            self.app.processEvents()

        self._press(dialog.area_list, Qt.Key.Key_Delete)

        self.assertEqual(len(dialog.scene.areas), 0, "Delete 应删掉选中区域")

    def test_delete_key_asks_for_confirmation(self):
        """Delete 与右键菜单走同一条路径，必须弹确认框。"""
        from PySide6.QtCore import Qt

        dialog = self._make_dialog()
        called = self._confirm_yes()
        self._select_feature_row(dialog, 0)

        self._press(dialog.feature_list, Qt.Key.Key_Delete)

        self.assertEqual(len(called), 1, "必须经过确认框（与右键菜单同一路径）")

    def test_delete_key_cancelled_keeps_feature(self):
        """确认框取消时特征保留。"""
        import ok.ui.qt.kit as kit
        from PySide6.QtCore import Qt

        dialog = self._make_dialog()
        original = kit.confirm
        kit.confirm = lambda *args, **kwargs: False
        self.addCleanup(setattr, kit, "confirm", original)
        self._select_feature_row(dialog, 0)

        self._press(dialog.feature_list, Qt.Key.Key_Delete)

        self.assertIsNotNone(dialog.scene.find_feature("feature-1"), "取消后不该删除")

    def test_delete_key_in_line_edit_does_not_delete_feature(self):
        """**在名称输入框里按 Delete 不能删特征**。

        这是最容易写错的地方：用 `QShortcut` 装窗口级快捷键会把输入框里的
        Delete 一起吃掉——用户在改名字时删一个字符，整个特征就没了。

        `keyPressEvent` 天然避开这一点：`QLineEdit` 自己消费 Delete（删光标前
        的字符），事件不再冒泡到对话框。
        """
        from PySide6.QtCore import Qt

        dialog = self._make_dialog()
        self._confirm_yes()
        self._select_feature_row(dialog, 0)
        dialog.name_edit.setText("abc")
        dialog.name_edit.setFocus()
        dialog.name_edit.end(False)
        for _ in range(4):
            self.app.processEvents()

        self._press(dialog.name_edit, Qt.Key.Key_Delete)

        self.assertIsNotNone(dialog.scene.find_feature("feature-1"), "输入框里的 Delete 不能删特征")

    def test_delete_uses_key_press_not_shortcut(self):
        """删除必须走 `keyPressEvent`，**不能用窗口级 `QShortcut`**。

        补这条是因为上一条测试抓不到 `QShortcut` 的写法：`QShortcut` 依赖真实的
        键盘事件队列与 `ShortcutOverride` 协商，`sendEvent` 造出的 KeyPress 不会
        触发它——那种实现下上一条照样绿，而真实使用中在名称框按 Delete 会连特征
        一起删掉。

        因此直接检查实现事实：`keyPressEvent` 处理 Delete，且**没有真的构造**
        `QShortcut`（只看代码行，不看注释与文档字符串——本模块的布局示意图里
        就写着这个词）。
        """
        import inspect

        source = inspect.getsource(scene_editor.SceneEditorDialog.keyPressEvent)
        self.assertIn("Key_Delete", source, "keyPressEvent 必须处理 Delete")

        module_lines = inspect.getsource(scene_editor).splitlines()
        code_lines = [line for line in module_lines if not line.lstrip().startswith("#")]
        constructing = [line for line in code_lines if "QShortcut(" in line]
        self.assertEqual(constructing, [], f"不得用窗口级快捷键（会吃掉输入框的 Delete）：{constructing}")

    def test_delete_key_without_selection_is_noop(self):
        """没有选中项时按 Delete 不做任何事，也不抛异常。"""
        from PySide6.QtCore import Qt

        dialog = self._make_dialog()
        called = self._confirm_yes()
        dialog.show()
        for _ in range(6):
            self.app.processEvents()
        dialog.canvas.set_selected(None)
        for _ in range(4):
            self.app.processEvents()

        self._press(dialog.feature_list, Qt.Key.Key_Delete)

        self.assertIsNotNone(dialog.scene.find_feature("feature-1"), "无选中不该删")
        self.assertEqual(called, [], "无选中不该弹确认框")

    def test_switch_clears_dirty(self):
        """切换场景后脏标记清零，新场景的保存按钮是灰的。"""
        second = self.library.new_scene("第二个")
        self.library.save_scene(second)
        self.library.save_base_image(second.scene_id, _striped(800, 600))

        dialog = self._make_dialog()
        dialog.name_edit.setText("改过")
        dialog._on_name_edited("改过")
        self.assertTrue(dialog._dirty())

        import ok.ui.qt.kit as kit

        original = kit.confirm
        kit.confirm = lambda *args, **kwargs: True
        try:
            dialog.next_scene()
        finally:
            kit.confirm = original

        self.assertFalse(dialog._dirty(), "切换后应清零")
        self.assertFalse(dialog.save_button.isEnabled())

    def test_nav_buttons_reflect_position(self):
        """左右按钮按当前下标启用，位置标签显示 `序号/总数`。

        单场景时两个按钮都禁用——边界处禁用比「点了没反应」明确。
        """
        dialog = self._make_dialog()
        self.assertEqual(dialog.position_label.text(), "1/1")
        self.assertFalse(dialog.prev_button.isEnabled())
        self.assertFalse(dialog.next_button.isEnabled())

    def test_next_scene_switches_content(self):
        """切到下一个场景时载入它的数据与底图。"""
        second = self.library.new_scene("第二个")
        second.features.append(model.Feature(name="f2", shape=shapes.Shape.rectangle(0.2, 0.2, 0.5, 0.4)))
        self.library.save_scene(second)
        self.library.save_base_image(second.scene_id, _striped(800, 600))

        dialog = self._make_dialog()
        self.assertEqual(len(dialog.scene_ids), 2)
        self.assertEqual(dialog.position_label.text(), "1/2")

        # 切之前确认放弃改动
        import ok.ui.qt.kit as kit

        original = kit.confirm
        kit.confirm = lambda *args, **kwargs: True
        try:
            dialog.next_scene()
        finally:
            kit.confirm = original

        self.assertEqual(dialog.scene.scene_id, second.scene_id)
        self.assertEqual(dialog.name_edit.text(), "第二个")
        self.assertEqual(dialog.position_label.text(), "2/2")
        self.assertFalse(dialog.next_button.isEnabled(), "已在末尾")
        self.assertTrue(dialog.prev_button.isEnabled())

    def test_prev_scene_goes_back(self):
        """切到下一个后能切回上一个。"""
        second = self.library.new_scene("第二个")
        self.library.save_scene(second)
        self.library.save_base_image(second.scene_id, _striped(800, 600))

        dialog = self._make_dialog()
        import ok.ui.qt.kit as kit

        original = kit.confirm
        kit.confirm = lambda *args, **kwargs: True
        try:
            dialog.next_scene()
            dialog.prev_scene()
        finally:
            kit.confirm = original

        self.assertEqual(dialog.scene.scene_id, self.scene.scene_id)
        self.assertEqual(dialog.position_label.text(), "1/2")

    def test_switch_cancel_keeps_current_scene(self):
        """确认框取消时不切换场景，改动保留。"""
        second = self.library.new_scene("第二个")
        self.library.save_scene(second)
        self.library.save_base_image(second.scene_id, _striped(800, 600))

        dialog = self._make_dialog()
        # 模拟用户编辑：setText 不触发 textEdited（那是程序设值），必须显式调
        # _on_name_edited 才等价于「用户真的改了名字」。
        dialog.name_edit.setText("改过名")
        dialog._on_name_edited("改过名")

        import ok.ui.qt.kit as kit

        original = kit.confirm
        kit.confirm = lambda *args, **kwargs: False
        try:
            dialog.next_scene()
        finally:
            kit.confirm = original

        self.assertEqual(dialog.scene.scene_id, self.scene.scene_id, "取消后应停在原场景")
        self.assertEqual(dialog.name_edit.text(), "改过名", "改动不能丢")

    def test_switch_resets_view(self):
        """切换场景后画布重置为适应视口。

        换了底图，旧缩放与位置不再对应同一画面——保留反而会让人对着偏移的视图找内容。
        """
        second = self.library.new_scene("第二个")
        self.library.save_scene(second)
        self.library.save_base_image(second.scene_id, _striped(800, 600))

        dialog = self._make_dialog()
        dialog.resize(1320, 860)
        dialog.show()
        for _ in range(6):
            self.app.processEvents()
        dialog.canvas._zoom_at(dialog.canvas.zoom_scale() * 3, QPointF(100, 100))

        import ok.ui.qt.kit as kit

        original = kit.confirm
        kit.confirm = lambda *args, **kwargs: True
        try:
            dialog.next_scene()
        finally:
            kit.confirm = original

        self.assertAlmostEqual(dialog.canvas.zoom_scale(), dialog.canvas.fit_scale(), places=6)

    def test_refresh_keeps_zoom_and_pan(self):
        """改框后画布保留用户当前的缩放与平移。

        本方法在每次新增/修改一个框后都会被调用。若每次都重置视图，用户刚用滚轮
        调到合适的缩放与位置就白调了——实测表现为「点确认后画面突然缩回适应窗口」。
        底图内容没变（同一张 preview.png）时没有任何理由重置视图。
        """
        dialog = self._make_dialog()
        fit_scale = dialog.canvas.zoom_scale()
        dialog.canvas._zoom_at(fit_scale * 2.5, dialog.canvas.rect().center())
        dialog.canvas._offset = QPointF(120, -80)
        user_scale = dialog.canvas.zoom_scale()
        user_offset = QPointF(dialog.canvas._offset)

        dialog._on_rect_changed(("feature", "feature-1"), (200, 120, 600, 400))

        self.assertAlmostEqual(dialog.canvas.zoom_scale(), user_scale, places=6)
        self.assertAlmostEqual(dialog.canvas._offset.x(), user_offset.x(), delta=1)
        self.assertAlmostEqual(dialog.canvas._offset.y(), user_offset.y(), delta=1)

    def test_refresh_with_reset_view_fits_image(self):
        """换底图（抓图/导入）时视图重置为「整幅进视口」。

        与上一条相反：底图变了，旧缩放与位置不再对应同一画面，保留反而会让人
        对着一个偏移的视图找内容。
        """
        dialog = self._make_dialog()
        dialog.resize(1320, 860)
        dialog.show()
        for _ in range(6):
            self.app.processEvents()
        expected_fit = dialog.canvas.fit_scale()

        dialog.canvas._zoom_at(expected_fit * 3, dialog.canvas.rect().center())
        self.assertGreater(dialog.canvas.zoom_scale(), expected_fit)

        dialog._refresh_canvas(reset_view=True)
        self.assertAlmostEqual(dialog.canvas.zoom_scale(), expected_fit, places=6)

    def test_drag_writes_back_to_scene(self):
        """画布拖动后矩形写回形状。

        写回的是形状本身（包围盒即形状的 bbox），因此断言走 `feature.region(width,
        height)` —— 它由形状派生，能反映真实生效的搜索区域。
        """
        dialog = self._make_dialog()
        meta = dialog.library.meta
        dialog._on_rect_changed(("feature", "feature-1"), (160, 120, 320, 240))
        feature = dialog.scene.find_feature("feature-1")
        region = feature.region(meta.base_width, meta.base_height)
        self.assertAlmostEqual(region[0], 0.2, places=3)
        self.assertAlmostEqual(region[1], 0.2, places=3)

    def _accept_property_dialog(self, values):
        """替换属性对话框，使其自动确认并返回给定值。

        Args：
            values：`{key: value}` 字典，模拟用户输入。

        Returns：
            计数器字典，含 `count` 与 `title`，供断言「确实弹过对话框」。
        """
        counter = {"count": 0, "title": ""}
        original = scene_editor.PropertyDialog

        class FakeDialog:
            """假属性对话框：直接确认。"""

            def __init__(self, title, fields, parent=None, dynamic_params=None):
                """记录标题。"""
                counter["count"] += 1
                counter["title"] = title

            def exec(self):
                """总是确认。"""
                return True

            def values(self):
                """返回预置值。"""
                return dict(values)

            def set_dynamic_values(self, _values):
                """空实现，接口对齐——假对话框没有动态区。"""

            def deleteLater(self):
                """空实现。"""

        scene_editor.PropertyDialog = FakeDialog
        self.addCleanup(setattr, scene_editor, "PropertyDialog", original)
        return counter

    def test_next_name_prefix_includes_scene_name(self):
        """新建特征/区域的默认名前缀带场景名：`<场景名>-特征N` / `<场景名>-区域N`。

        光看 `特征1` 分不清是哪个场景的——右侧列表与属性对话框只显示名称，用户在
        多个场景间切换时很容易配错。场景名作前缀让归属一眼可见。
        """
        self.assertEqual(scene_editor._name_prefix(self.scene, "特征"), "已有场景-特征")
        self.assertEqual(scene_editor._name_prefix(self.scene, "区域"), "已有场景-区域")

    def test_next_name_numbering_skips_used(self):
        """编号从 1 起、跳过已占用的，编号紧贴前缀不加分隔符。"""
        prefix = scene_editor._name_prefix(self.scene, "特征")
        self.assertEqual(scene_editor._next_name([], prefix), f"{prefix}1")
        self.assertEqual(scene_editor._next_name([f"{prefix}1"], prefix), f"{prefix}2")
        self.assertEqual(
            scene_editor._next_name([f"{prefix}1", f"{prefix}2"], prefix),
            f"{prefix}3",
        )

    def test_next_name_prefix_falls_back_to_id(self):
        """场景名为空时用 id 兜底，仍保留归属信息。

        `Scene.__post_init__` 本会把空名填成 id，但导入的旧数据可能真的有空名。
        此时用 id 兜底比只用「特征」更好：`特征1` 看不出属于哪个场景，
        `scene-0009-特征1` 至少能定位到目录。
        """
        blank = model.Scene(scene_id="scene-0009", name="")
        blank.name = ""  # 绕过 __post_init__ 的兜底，模拟旧数据
        self.assertEqual(scene_editor._name_prefix(blank, "特征"), "scene-0009-特征")

    def test_draw_feature_default_name_has_no_prefix(self):
        """框选特征时默认名不带场景前缀（开关默认关闭），首次为 `特征1`。"""
        dialog = self._make_dialog()
        captured = {}
        original = scene_editor.PropertyDialog

        class FakeDialog:
            """记录传进来的默认名。"""

            def __init__(self, title, fields, parent=None, dynamic_params=None):
                captured["name"] = fields[0][2]

            def exec(self):
                return False

            def values(self):
                return {}

            def set_dynamic_values(self, _values):
                pass

            def deleteLater(self):
                pass

        scene_editor.PropertyDialog = FakeDialog
        self.addCleanup(setattr, scene_editor, "PropertyDialog", original)

        dialog.mode_strip.set_current(scene_editor.MODE_FEATURE)
        dialog._on_rect_created(80, 60, 240, 180)
        self.assertEqual(captured["name"], "特征1")

    def test_draw_area_default_name_has_no_prefix(self):
        """框选区域时默认名不带场景前缀（开关默认关闭），首次为 `区域1`。"""
        dialog = self._make_dialog()
        captured = {}
        original = scene_editor.PropertyDialog

        class FakeDialog:
            """记录传进来的默认名。"""

            def __init__(self, title, fields, parent=None, dynamic_params=None):
                captured["name"] = fields[0][2]

            def exec(self):
                return False

            def values(self):
                return {}

            def set_dynamic_values(self, _values):
                pass

            def deleteLater(self):
                pass

        scene_editor.PropertyDialog = FakeDialog
        self.addCleanup(setattr, scene_editor, "PropertyDialog", original)

        dialog.mode_strip.set_current(scene_editor.MODE_AREA)
        dialog._on_rect_created(80, 60, 240, 180)
        self.assertEqual(captured["name"], "区域1")

    def test_name_prefix_prefers_typed_name(self):
        """`typed_name` 优先于 `scene.name`：用户先改名再画框时用新名字。

        改名要保存才落盘，此时 `scene.name` 还是旧名；若取它作前缀，会生成
        `旧名字-特征1`，与界面上显示的当前名称对不上。
        """
        self.assertEqual(scene_editor._name_prefix(self.scene, "特征", "新名字"), "新名字-特征")
        self.assertEqual(scene_editor._name_prefix(self.scene, "区域", "  新名字  "), "新名字-区域")

    def test_name_prefix_ignores_blank_typed_name(self):
        """输入框为空或只有空白时回落到已保存的场景名，不生成 `-特征1`。"""
        self.assertEqual(scene_editor._name_prefix(self.scene, "特征", ""), "已有场景-特征")
        self.assertEqual(scene_editor._name_prefix(self.scene, "特征", "   "), "已有场景-特征")
        self.assertEqual(scene_editor._name_prefix(self.scene, "特征", None), "已有场景-特征")

    def test_draw_feature_uses_typed_name_before_saving(self):
        """输入框改名后不保存直接框选，默认名用输入框里的新名字。"""
        dialog = self._make_dialog()
        captured = {}
        original = scene_editor.PropertyDialog

        class FakeDialog:
            """记录传进来的默认名。"""

            def __init__(self, title, fields, parent=None, dynamic_params=None):
                captured["name"] = fields[0][2]

            def exec(self):
                return False

            def values(self):
                return {}

            def set_dynamic_values(self, _values):
                pass

            def deleteLater(self):
                pass

        scene_editor.PropertyDialog = FakeDialog
        self.addCleanup(setattr, scene_editor, "PropertyDialog", original)

        dialog.name_edit.setText("改名后")
        dialog.name_prefix_switch.setChecked(True)
        dialog.mode_strip.set_current(scene_editor.MODE_FEATURE)
        dialog._on_rect_created(80, 60, 240, 180)
        self.assertEqual(captured["name"], "改名后-特征1")

    def test_draw_area_uses_typed_name_before_saving(self):
        """输入框改名后不保存直接框选区域，默认名用输入框里的新名字。"""
        dialog = self._make_dialog()
        captured = {}
        original = scene_editor.PropertyDialog

        class FakeDialog:
            """记录传进来的默认名。"""

            def __init__(self, title, fields, parent=None, dynamic_params=None):
                captured["name"] = fields[0][2]

            def exec(self):
                return False

            def values(self):
                return {}

            def set_dynamic_values(self, _values):
                pass

            def deleteLater(self):
                pass

        scene_editor.PropertyDialog = FakeDialog
        self.addCleanup(setattr, scene_editor, "PropertyDialog", original)

        dialog.name_edit.setText("改名后")
        dialog.name_prefix_switch.setChecked(True)
        dialog.mode_strip.set_current(scene_editor.MODE_AREA)
        dialog._on_rect_created(80, 60, 240, 180)
        self.assertEqual(captured["name"], "改名后-区域1")

    def test_name_prefix_switch_defaults_off(self):
        """开关默认关闭：新建库没有该偏好时保持关闭。"""
        dialog = self._make_dialog()
        self.assertFalse(dialog.name_prefix_switch.isChecked())

    def test_name_prefix_switch_adds_scene_prefix(self):
        """打开开关后默认名带场景前缀：`<场景名>-特征N` / `<场景名>-区域N`。"""
        dialog = self._make_dialog()
        dialog.name_prefix_switch.setChecked(True)
        self.assertEqual(dialog._default_prefix("特征"), "已有场景-特征")
        self.assertEqual(dialog._default_prefix("区域"), "已有场景-区域")
        dialog.name_prefix_switch.setChecked(False)
        self.assertEqual(dialog._default_prefix("特征"), "特征")

    def test_name_prefix_switch_persists(self):
        """开关状态写进库元数据，重开弹窗后恢复上次的选择。

        它是编辑器偏好，跟着库走；不持久化的话每次打开都要重新拨一次。
        """
        dialog = self._make_dialog()
        dialog.name_prefix_switch.setChecked(True)
        self.assertTrue(self.library.load_meta().name_with_scene_prefix)

        reopened = self._make_dialog()
        self.assertTrue(reopened.name_prefix_switch.isChecked())

        reopened.name_prefix_switch.setChecked(False)
        self.assertFalse(self.library.load_meta().name_with_scene_prefix)
        again = self._make_dialog()
        self.assertFalse(again.name_prefix_switch.isChecked())

    def test_draw_feature_opens_property_dialog_then_adds(self):
        """框选特征后先弹属性对话框，确认才新增。

        顺序很重要：名称是必填项，先建后改会让画布上出现一个叫 `feature-1` 的
        框，而用户很可能忘了改。
        """
        dialog = self._make_dialog()
        counter = self._accept_property_dialog(
            {
                "name": "my_btn",
                "kind": model.KIND_TEMPLATE,
                "mandatory": True,
                "threshold": "0.85",
                "text": "",
            }
        )
        dialog.mode_strip.set_current(scene_editor.MODE_FEATURE)
        dialog._on_rect_created(80, 60, 240, 180)
        self.assertEqual(counter["count"], 1)
        self.assertEqual(counter["title"], "New Recognition Feature")
        self.assertEqual(len(dialog.scene.features), 2)
        self.assertEqual(dialog.scene.features[-1].name, "my_btn")
        self.assertAlmostEqual(dialog.scene.features[-1].threshold, 0.85)
        self.assertEqual(dialog.canvas.selected_key()[0], "feature")
        self.assertEqual(dialog._current_mode(), scene_editor.MODE_SELECT)

    def test_create_area_saves_action_and_params(self):
        """新建区域时保存探索动作与参数。

        探索动作若没被写进区域，探索仍按单击探测——对滑动列表或长按按钮会得出
        错误结论，而错误不报错。
        """
        dialog = self._make_dialog()
        self._accept_property_dialog(
            {
                "name": "swipe_area",
                "target": "",
                "explore_probe": True,
                "explore_action": model.ACTION_SWIPE,
                "param_distance": "0.45",
                "param_direction": model.SWIPE_LEFT,
                "after_wait": "1.0",
            }
        )
        dialog.mode_strip.set_current(scene_editor.MODE_AREA)
        dialog._on_rect_created(80, 60, 240, 180)

        created = dialog.scene.areas[-1]
        self.assertEqual(created.explore_action, model.ACTION_SWIPE)
        self.assertEqual(created.explore_params, {"distance": 0.45, "direction": model.SWIPE_LEFT})

    def test_create_area_rejects_bad_param(self):
        """参数非法时不建区域，状态栏给出原因。"""
        dialog = self._make_dialog()
        self._accept_property_dialog(
            {
                "name": "bad",
                "target": "",
                "explore_probe": True,
                "explore_action": model.ACTION_LONG_PRESS,
                "param_duration": "99",
                "after_wait": "1.0",
            }
        )
        before = len(dialog.scene.areas)
        dialog.mode_strip.set_current(scene_editor.MODE_AREA)
        dialog._on_rect_created(80, 60, 240, 180)

        self.assertEqual(len(dialog.scene.areas), before, "非法参数不应建区域")
        self.assertIn("动作参数无效", dialog.status_label.text())

    def test_edit_area_updates_action(self):
        """编辑区域时动作与参数能改。"""
        dialog = self._make_dialog()
        area = dialog.scene.areas[0]
        area.explore_action = model.ACTION_CLICK
        self._accept_property_dialog(
            {
                "name": area.name,
                "target": "",
                "explore_probe": True,
                "explore_action": model.ACTION_LONG_PRESS,
                "param_duration": "1.5",
                "after_wait": "1.0",
            }
        )
        dialog._edit_area_at(0)
        self.assertEqual(area.explore_action, model.ACTION_LONG_PRESS)
        self.assertEqual(area.explore_params, {"duration": 1.5})

    def test_area_dialog_lists_all_actions(self):
        """区域对话框的动作下拉列出全部五种动作。"""
        fields = scene_editor._explore_action_field(model.ACTION_CLICK)
        action_field = next(item for item in fields if item[0] == "explore_action")
        values = [value for _label, value in action_field[3]]
        self.assertEqual(values, list(model.EXPLORE_ACTIONS))

    def test_param_fields_follow_action(self):
        """参数字段随动作变化——这是用户报的缺陷。

        原先参数在打开对话框时按「当时的动作」静态生成一次：新建区域默认单击、单击
        没有参数，于是切到长按也看不到「按住时长」输入框。
        """
        self.assertEqual(scene_editor._explore_param_fields(model.ACTION_CLICK), [])
        self.assertEqual(scene_editor._explore_param_fields(model.ACTION_RIGHT_CLICK), [])

        long_press = scene_editor._explore_param_fields(model.ACTION_LONG_PRESS)
        self.assertEqual([item[0] for item in long_press], ["param_duration"])

        swipe = scene_editor._explore_param_fields(model.ACTION_SWIPE)
        self.assertEqual([item[0] for item in swipe], ["param_distance", "param_direction"])

        double_click = scene_editor._explore_param_fields(model.ACTION_DOUBLE_CLICK)
        self.assertEqual([item[0] for item in double_click], ["param_interval"])

    def test_draw_area_opens_property_dialog_then_adds(self):
        """框选区域后先弹属性对话框，确认才新增。"""
        dialog = self._make_dialog()
        counter = self._accept_property_dialog(
            {
                "name": "my_area",
                "explore_probe": True,
            }
        )
        dialog.mode_strip.set_current(scene_editor.MODE_AREA)
        dialog._on_rect_created(80, 60, 240, 180)
        self.assertEqual(counter["count"], 1)
        self.assertEqual(counter["title"], "New Interaction Region")
        self.assertEqual(len(dialog.scene.areas), 2)
        self.assertEqual(dialog.scene.areas[-1].name, "my_area")
        self.assertTrue(dialog.scene.areas[-1].explore_probe)
        # 区域不再有「目标场景 / 操作后等待」——那些属流转图，由探索产出后在
        # 流转页维护。这里断言它**不存在**，防止后来者顺手加回来。
        self.assertFalse(hasattr(dialog.scene.areas[-1], "after_wait"))
        self.assertFalse(hasattr(dialog.scene.areas[-1], "target_scene"))

    def _capture_area_fields(self, edit_row=None):
        """截获区域对话框的字段声明，供断言字段形态。

        Args：
            edit_row：为 None 时走「新建区域」（`_on_rect_created`），
                否则走「编辑区域」（`_edit_area_at(edit_row)`）。

        Returns：
            `{key: options}` 字典，options 即字段声明第四项。
        """
        dialog = self._make_dialog()
        captured = {}
        original = scene_editor.PropertyDialog

        class FakeDialog:
            """只记录字段声明、不真的显示。"""

            def __init__(self, _title, fields, parent=None, dynamic_params=None):
                captured.update({key: options for key, _label, _value, options in fields})

            def exec(self):
                return False

            def values(self):
                return {}

            def set_dynamic_values(self, _values):
                pass

            def deleteLater(self):
                pass

        scene_editor.PropertyDialog = FakeDialog
        self.addCleanup(setattr, scene_editor, "PropertyDialog", original)

        if edit_row is None:
            dialog.mode_strip.set_current(scene_editor.MODE_AREA)
            dialog._on_rect_created(80, 60, 240, 180)
        else:
            dialog._edit_area_at(edit_row)
        return captured

    def test_create_area_dialog_exposes_both_switches(self):
        """新建区域时必须能看到「允许探索探测」与「探索返回区域」两个开关。

        返回区域原先只在编辑弹窗里有，新建时看不到——用户建完才发现没地方标，
        只能再双击进一次编辑弹窗。两个开关都用开关形态而不是「是/否」下拉。
        """
        fields = self._capture_area_fields()
        self.assertEqual(fields["explore_probe"], scene_editor.TOGGLE_FIELD)
        self.assertEqual(fields["explore_return"], scene_editor.TOGGLE_FIELD)

    def test_edit_area_dialog_explore_probe_is_switch(self):
        """编辑区域的「允许探索探测」与返回区域同形，都是开关。"""
        fields = self._capture_area_fields(edit_row=0)
        self.assertEqual(fields["explore_probe"], scene_editor.TOGGLE_FIELD)
        self.assertEqual(fields["explore_return"], scene_editor.TOGGLE_FIELD)

    def test_create_area_marks_explore_return(self):
        """新建区域时打开「探索返回区域」，落在区域数据上。"""
        dialog = self._make_dialog()
        self._accept_property_dialog({"name": "back_area", "explore_probe": False, "explore_return": True})
        dialog.mode_strip.set_current(scene_editor.MODE_AREA)
        dialog._on_rect_created(80, 60, 240, 180)
        self.assertTrue(dialog.scene.areas[-1].explore_return)
        self.assertFalse(dialog.scene.areas[-1].explore_probe)

    def _capture_feature_fields(self, edit_row=None):
        """截获特征对话框的字段声明，供断言字段形态。

        Args：
            edit_row：为 None 时走「新建特征」（`_on_rect_created`），
                否则走「编辑特征」（`_edit_feature_at(edit_row)`）。

        Returns：
            `{key: options}` 字典，options 即字段声明第四项。
        """
        dialog = self._make_dialog()
        captured = {}
        original = scene_editor.PropertyDialog

        class FakeDialog:
            """只记录字段声明、不真的显示。"""

            def __init__(self, _title, fields, parent=None, dynamic_params=None):
                captured.update({key: options for key, _label, _value, options in fields})

            def exec(self):
                return False

            def values(self):
                return {}

            def set_dynamic_values(self, _values):
                pass

            def deleteLater(self):
                pass

        scene_editor.PropertyDialog = FakeDialog
        self.addCleanup(setattr, scene_editor, "PropertyDialog", original)

        if edit_row is None:
            dialog.mode_strip.set_current(scene_editor.MODE_FEATURE)
            dialog._on_rect_created(80, 60, 240, 180)
        else:
            dialog._edit_feature_at(edit_row)
        return captured

    def test_feature_dialog_mandatory_is_switch(self):
        """识别特征的「必须」在新建与编辑弹窗里都是开关，不是是/否下拉。"""
        create_fields = self._capture_feature_fields()
        self.assertEqual(create_fields["mandatory"], scene_editor.TOGGLE_FIELD)
        edit_fields = self._capture_feature_fields(edit_row=0)
        self.assertEqual(edit_fields["mandatory"], scene_editor.TOGGLE_FIELD)

    def test_create_feature_marks_optional(self):
        """新建特征时关掉「必须」，落在特征数据上。"""
        dialog = self._make_dialog()
        self._accept_property_dialog(
            {
                "name": "optional_one",
                "kind": model.KIND_TEMPLATE,
                "mandatory": False,
                "threshold": "0.8",
                "text": "",
            }
        )
        dialog.mode_strip.set_current(scene_editor.MODE_FEATURE)
        dialog._on_rect_created(80, 60, 240, 180)
        self.assertFalse(dialog.scene.features[-1].mandatory)

    def test_cancel_property_dialog_adds_nothing(self):
        """属性对话框取消时不新增。"""
        dialog = self._make_dialog()
        original = scene_editor.PropertyDialog

        class CancelDialog:
            """总是取消的属性对话框。"""

            def __init__(self, *args, **kwargs):
                """忽略参数。"""

            def exec(self):
                """取消。"""
                return False

            def values(self):
                """无值。"""
                return {}

            def deleteLater(self):
                """空实现。"""

        scene_editor.PropertyDialog = CancelDialog
        self.addCleanup(setattr, scene_editor, "PropertyDialog", original)

        dialog.mode_strip.set_current(scene_editor.MODE_FEATURE)
        dialog._on_rect_created(80, 60, 240, 180)
        self.assertEqual(len(dialog.scene.features), 1)

    def test_double_click_feature_opens_property_dialog(self):
        """双击特征行打开属性对话框，改完写回。"""
        dialog = self._make_dialog()
        counter = self._accept_property_dialog(
            {
                "name": "renamed",
                "kind": model.KIND_TEMPLATE,
                "mandatory": False,
                "threshold": "0.6",
                "text": "",
            }
        )
        dialog._edit_feature_at(0)
        self.assertEqual(counter["count"], 1)
        self.assertEqual(counter["title"], "Edit Recognition Feature")
        self.assertEqual(dialog.scene.features[0].name, "renamed")
        self.assertFalse(dialog.scene.features[0].mandatory)
        self.assertAlmostEqual(dialog.scene.features[0].threshold, 0.6)

    def test_double_click_area_opens_property_dialog(self):
        """双击区域行打开属性对话框，改完写回。"""
        dialog = self._make_dialog()
        counter = self._accept_property_dialog(
            {
                "name": "area-1",
                "explore_probe": True,
            }
        )
        dialog._edit_area_at(0)
        self.assertEqual(counter["count"], 1)
        self.assertEqual(counter["title"], "Edit Interaction Region")
        self.assertTrue(dialog.scene.areas[0].explore_probe)

    def test_area_dialog_marks_explore_return(self):
        """区域属性对话框可以标「探索返回区域」，且与探测开关独立。"""
        dialog = self._make_dialog()
        self._accept_property_dialog({"name": "area-1", "explore_probe": False, "explore_return": True})
        dialog._edit_area_at(0)
        self.assertTrue(dialog.scene.areas[0].explore_return)
        self.assertFalse(dialog.scene.areas[0].explore_probe, "标返回不需要同时打开探测")

    def test_double_click_canvas_feature_opens_property_dialog(self):
        """双击画布上的特征框，直接打开该特征的属性对话框。"""
        dialog = self._make_dialog()
        counter = self._accept_property_dialog(
            {
                "name": "feature-1",
                "kind": model.KIND_TEMPLATE,
                "mandatory": True,
                "threshold": "0.70",
                "text": "",
            }
        )
        dialog._on_canvas_double_clicked(("feature", "feature-1"))
        self.assertEqual(counter["count"], 1)
        self.assertEqual(counter["title"], "Edit Recognition Feature")
        self.assertAlmostEqual(dialog.scene.features[0].threshold, 0.7)

    def test_double_click_canvas_area_opens_property_dialog(self):
        """双击画布上的交互区域，直接打开该区域的属性对话框。"""
        dialog = self._make_dialog()
        counter = self._accept_property_dialog(
            {
                "name": "area-1",
                "explore_probe": True,
            }
        )
        dialog._on_canvas_double_clicked(("area", "area-1"))
        self.assertEqual(counter["count"], 1)
        self.assertEqual(counter["title"], "Edit Interaction Region")
        self.assertTrue(dialog.scene.areas[0].explore_probe)

    def test_double_click_canvas_blank_does_nothing(self):
        """双击空白不弹窗——空白处双击多半是无意。"""
        dialog = self._make_dialog()
        counter = self._accept_property_dialog({})
        dialog._on_canvas_double_clicked(None)
        self.assertEqual(counter["count"], 0)

    def test_area_list_shows_probe_state(self):
        """区域列表的摘要只在开启探测时写出 Probe，关闭时不写。"""
        dialog = self._make_dialog()
        self.assertNotIn("Probe", dialog.area_list.item(0).text())
        dialog.scene.areas[0].explore_probe = True
        dialog._refresh_lists()
        self.assertIn("Probe", dialog.area_list.item(0).text())

    def test_list_items_are_readonly(self):
        """列表项不可编辑：所有改数据的入口都收敛到属性对话框与画布拖拽。

        列表可编辑时会形成「列表能改一半、对话框能改全部」的两套口径，字段一多
        就分叉，而分叉不会报错。
        """
        from PySide6.QtCore import Qt

        dialog = self._make_dialog()
        for list_widget in (dialog.feature_list, dialog.area_list):
            for row in range(list_widget.count()):
                flags = list_widget.item(row).flags()
                self.assertFalse(bool(flags & Qt.ItemFlag.ItemIsEditable))

    def test_list_item_height_fits_two_lines(self):
        """列表项高度必须容得下两行文字。

        踩过两次同一现象：右侧列表只剩「名称…」，第二行静默消失。两个成因方向相反，
        所以这条用例要同时盯住：

        - 什么都不设：`QListWidget` 不会为文本里的换行符撑高项（实测 Qt 6.12 下无论
          是否开 `wordWrap`，`visualItemRect` 恒为单行 27px）；
        - 设一个常量：装了主题后 item padding 需要更多高度，常量比自然需求小，反而
          把原本够用的高度压小（上一版写死 38px，偏小 10px）。
        """
        dialog = self._make_dialog()
        for list_widget in (dialog.feature_list, dialog.area_list):
            for row in range(list_widget.count()):
                item = list_widget.item(row)
                self.assertIn("\n", item.text())
                height = list_widget.visualItemRect(item).height()
                needed = scene_editor._two_line_height(list_widget)
                self.assertGreaterEqual(height, needed, f"项高 {height}px 小于两行所需 {needed}px")

    def test_list_item_height_with_and_without_theme(self):
        """有主题与无主题下，项高都不小于两行需求。

        主题会通过 `::item` 的 padding 抬高自然高度；项高取「自然高度」与「两行需求」
        的较大值，因此两种环境都必须成立。只测其中一种会漏掉另一侧的回归——上一版
        只在无主题的测试里通过，真实界面（有主题）反而被压掉第二行。
        """
        from ok.ui.qt.kit import theme

        for use_theme in (False, True):
            dialog = self._make_dialog()
            if use_theme:
                theme.install(dialog)
            dialog.resize(1320, 860)
            dialog.show()
            for _ in range(6):
                self.app.processEvents()
            for list_widget in (dialog.feature_list, dialog.area_list):
                height = list_widget.visualItemRect(list_widget.item(0)).height()
                needed = scene_editor._two_line_height(list_widget)
                self.assertGreaterEqual(height, needed, f"theme={use_theme} 项高 {height}px 小于 {needed}px")
            dialog.deleteLater()

    def test_list_item_size_hint_has_real_width(self):
        """项的 `sizeHint` 必须带真实宽度，不能是 0。

        **这是一个真实缺陷的回归测试**。宽度传 0 时 delegate 认为「没有可用
        宽度」，不换行、直接单行省略——长摘要被截成 `… BG 48,48,48…`，而项高
        按多行算好了，下方留一片空白。这个组合最容易误导：看着像「宽度不够」，
        实际是「没告诉它有多宽」。

        只断言高度抓不到它（高度本来就是对的），必须断言宽度。
        """
        dialog = self._make_dialog()
        for list_widget in (dialog.feature_list, dialog.area_list):
            viewport_width = list_widget.viewport().width()
            self.assertGreater(viewport_width, 0, "测试环境异常：视口宽度为 0")
            for row in range(list_widget.count()):
                item = list_widget.item(row)
                hint = item.sizeHint()
                self.assertGreater(hint.width(), 0, "sizeHint 宽度为 0，文字会被省略而不是折行")
                self.assertLessEqual(
                    hint.width(),
                    viewport_width + 4,
                    f"sizeHint 宽 {hint.width()} 超过视口 {viewport_width}",
                )

    def test_long_summary_wraps_instead_of_eliding(self):
        """长摘要必须折行显示完整，不能被省略号截断。

        判据是「按**真实文字区宽度**折行所需高度 + 真实内边距」：若项高不足，
        delegate 会降级成「压成两行 + 省略号」，用户永远看不到后半截内容。

        Note:
            宽度与内边距都必须取自 :func:`scene_editor._item_text_box`（向样式
            查询），不能写常数。上一版写死 `视口−20` 与 `+12`，而主题 QSS 的
            实际值是「视口−16」与 14px——**每项少 2px**，三行摘要只能排下两行，
            连第一行的名称都被省略。用常数断言的用例会与实现一起错，抓不到它。
        """
        from PySide6.QtCore import Qt

        dialog = self._make_dialog()
        dialog.resize(1320, 860)
        dialog.show()
        for _ in range(6):
            self.app.processEvents()
        list_widget = dialog.feature_list
        metrics = list_widget.fontMetrics()
        viewport = list_widget.viewport().width()
        self.assertGreater(viewport, 0, "测试环境异常：视口宽度为 0")
        # 造一条必然超出单行宽度的摘要（与真实最长摘要同构）。不能写死一段文本：
        # 不同字体的字宽差异足以让同一段文字在 CI 上比视口窄（实测 255px vs
        # 276px），断言会变成「测试数据不够长」而不是验证折行。按实际字体度量补足。
        base = "Template · Required · 0,69→34,76 · BG 48,48,48±4"
        long_summary = base
        for _ in range(10):
            if metrics.horizontalAdvance(long_summary) > viewport:
                break
            long_summary += " · " + base
        list_widget.clear()
        dialog._append_item(list_widget, "靶场入口-色卡", long_summary)
        dialog._fit_list_heights()
        for _ in range(4):
            self.app.processEvents()

        item = list_widget.item(0)
        single_line_width = metrics.horizontalAdvance(long_summary)
        self.assertGreater(
            single_line_width,
            viewport,
            f"测试数据不够长：摘要 {single_line_width}px 未超过视口 {viewport}px",
        )
        text_width, padding = scene_editor._item_text_box(list_widget)
        wrapped = metrics.boundingRect(
            0, 0, max(1, text_width), 10000, int(Qt.TextFlag.TextWordWrap), item.text()
        ).height()
        self.assertGreaterEqual(
            item.sizeHint().height(),
            wrapped + padding,
            f"项高 {item.sizeHint().height()} 小于「折行文字 {wrapped} + 内边距 {padding}」，文字会被省略",
        )
        dialog.close()

    def test_long_summary_renders_without_elide(self):
        """长摘要的真实渲染结果里不得出现省略号（三行不被压成两行）。

        **这是「差 2px 就看不全」的终局防线**。上一版内边距按 12px 估、实际是
        14px，项高比需要少 2px——那一版的数值断言用的正是同一个错误常数，全部
        通过；只有比对实际渲染出的**行数**才能发现「三行被压成两行 + `…`」。

        Note:
            判据是「行段数」而不是逐像素相等：项高变化会让 delegate 垂直居中，
            文字整体位移，像素比对永远不相等。而行数在高度不足时必然从 3 降为 2
            （末行加省略号），这个差异跨字体也稳定。
        """
        from ok.ui.qt.kit import theme
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage

        dialog = self._make_dialog()
        theme.install(dialog)
        dialog.resize(1320, 860)
        dialog.show()
        for _ in range(8):
            self.app.processEvents()

        list_widget = dialog.feature_list
        list_widget.clear()
        dialog._append_item(list_widget, "靶场入口-色卡", "Template · Required · 0,0→0,0 · BG 48,48,48±4")
        width = list_widget.viewport().width()
        item = list_widget.item(0)
        dialog._fit_list_heights()
        for _ in range(4):
            self.app.processEvents()
        computed = item.sizeHint().height()
        self.assertGreater(computed, 0, "测试环境异常：项高为 0")

        def count_text_rows(height):
            """按指定项高渲染，统计文字行段数。

            Args：
                height：项高（像素）。

            Returns：
                连续有墨迹的行段个数（一行文字算一段）。
            """
            item.setSizeHint(QSize(width, height))
            for _ in range(4):
                self.app.processEvents()
            image = list_widget.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
            background = image.pixel(1, height - 1)
            ink_rows = [y for y in range(height) if sum(1 for x in range(width) if image.pixel(x, y) != background) > 3]
            rows = 0
            previous = None
            for y in ink_rows:
                if previous is None or y - previous > 1:
                    rows += 1
                previous = y
            return rows

        # 给足高度必然完整显示，用它确定「应有的行数」（名称 1 行 + 摘要 2 行）。
        expected = count_text_rows(computed + 60)
        self.assertGreaterEqual(expected, 3, "测试数据不够长：给足高度也只排出 2 行")
        self.assertEqual(
            count_text_rows(computed),
            expected,
            f"项高 {computed}px 下只排出 {count_text_rows(computed)} 行（应 {expected} 行），文字被省略号截断",
        )
        dialog.close()

    def test_list_viewport_tracks_width(self):
        """列表视口必须装上宽度监听。

        **这是「窗口拉宽后文字被裁 / 下方多空白」的防线**。侧栏宽度固定
        （`_SIDE_WIDTH`），缩对话框不会让列表变窄；真正会变窄的情形（拖动分栏、
        字号变化、主题改 padding）都不会触发对话框的 `resizeEvent`。所以必须
        直接监听列表自己的视口。

        这里查「监听是否装上」而不是断高：`setFixedWidth` 会连带触发对话框重排，
        走 `resizeEvent` 兜底路径也能算对高，那条断言无法区分两个触发源。
        """
        dialog = self._make_dialog()
        for list_widget in (dialog.feature_list, dialog.area_list):
            viewport = list_widget.viewport()
            self.assertTrue(
                dialog._width_tracking_targets[viewport],
                "列表视口未装上宽度监听",
            )
        dialog.close()

    def test_width_tracking_targets_covers_both_lists(self):
        """两个列表的视口都在监听列表里，且是同一批对象。"""
        dialog = self._make_dialog()
        targets = dialog._width_tracking_targets
        self.assertIn(dialog.feature_list.viewport(), targets)
        self.assertIn(dialog.area_list.viewport(), targets)
        self.assertEqual(len(targets), 2)
        dialog.close()

    def test_side_width_is_fixed(self):
        """右侧信息栏固定宽度，不随窗口拉伸抢画面。"""
        dialog = self._make_dialog()
        self.assertEqual(dialog.feature_list.parentWidget().width(), scene_editor._SIDE_WIDTH)

    def test_save_writes_preview_and_template(self):
        """保存后预览图与模板都落盘。"""
        dialog = self._make_dialog()
        dialog.accept_original = dialog.accept
        dialog.accept = lambda: None  # 避免真的关窗
        dialog._on_save()
        self.assertIsNotNone(self.library.load_base_image(self.scene.scene_id))
        self.assertIsNotNone(self.library.load_template(self.scene.scene_id, "feature-1"))

    # --- 应用按钮 -----------------------------------------------------------

    def test_apply_button_is_left_of_save(self):
        """「应用」在「保存」左侧。

        位置不是审美问题：用户的操作顺序是「改 → 应用 → 继续改 → 最后保存并
        关闭」，把「保存并关闭」放在最右符合终点在后的阅读习惯。
        """
        dialog = self._make_dialog()
        dialog.show()
        for _ in range(6):
            self.app.processEvents()
        apply_x = dialog.apply_button.mapTo(dialog, dialog.apply_button.rect().topLeft()).x()
        save_x = dialog.save_button.mapTo(dialog, dialog.save_button.rect().topLeft()).x()
        self.assertLess(apply_x, save_x, f"「应用」应在「保存」左侧，实测 apply={apply_x} save={save_x}")

    def test_apply_writes_without_closing(self):
        """「应用」会写盘，但**不关闭弹窗**。

        核心差异就在这里：改完想接着在画布上调形状时，不必「保存 → 关窗 →
        重新打开」。
        """
        dialog = self._make_dialog()
        closed = []
        dialog.accept = lambda: closed.append(1)
        # 必须走 `name_edit`：保存时名字取的是输入框内容（`_write_scene` 里
        # `name = self.name_edit.text().strip()`），直接改 `scene.name` 会被覆盖。
        dialog.name_edit.setText("改过的名字")
        dialog._on_name_edited("改过的名字")

        dialog._on_apply()

        self.assertEqual(closed, [], "「应用」不该关窗")
        scenes, _failures = self.library.load_library()
        self.assertEqual(scenes[0].name, "改过的名字", "「应用」必须真的写盘")

    def test_apply_clears_dirty_and_disables_buttons(self):
        """「应用」后清脏标记并禁用两个按钮。

        不清脏的话，用户点完「应用」看到保存按钮还亮着、切换场景还弹「未保存
        的改动将丢失」——会以为刚才那次没生效。
        """
        dialog = self._make_dialog()
        dialog.accept = lambda: None
        dialog._mark_modified()
        self.assertTrue(dialog.apply_button.isEnabled())

        dialog._on_apply()

        self.assertFalse(dialog._dirty(), "应用后应清脏")
        self.assertFalse(dialog.apply_button.isEnabled(), "应用后按钮应变灰")
        self.assertFalse(dialog.save_button.isEnabled(), "保存按钮同步变灰")

    def test_apply_and_save_write_the_same_content(self):
        """两个按钮写出的内容一致（共用 `_write_scene`）。

        两条入口各写一份的话，模板与掩码的生成规则迟早分叉，而那种分叉不会
        报错——只会表现为「点这个存的是对的、点那个存的是旧的」。
        """
        applier = self._make_dialog()
        applier.accept = lambda: None
        applier._on_apply()
        applied = self.library.load_scene(self.scene.scene_id)

        saver = self._make_dialog()
        saver.accept = lambda: None
        saver._on_save()
        saved = self.library.load_scene(self.scene.scene_id)

        self.assertEqual(applied.to_dict(), saved.to_dict(), "两个按钮的产物必须完全一致")

    def test_auto_save_preference_persists(self):
        """开关状态写进库元数据，重开窗口保持上次的选择。"""
        dialog = self._make_dialog()
        dialog.auto_save_switch.setChecked(True)
        self.assertTrue(self.library.load_meta().auto_save, "切换后应立即落盘")

        reopened = self._make_dialog()
        self.assertTrue(reopened.auto_save_switch.isChecked(), "重开窗口应恢复上次状态")

    def test_auto_save_writes_existing_scene(self):
        """打开自动保存后，改动在防抖计时结束后写盘。"""
        dialog = self._make_dialog()
        dialog.auto_save_switch.setChecked(True)
        dialog.name_edit.setText("自动保存的名字")
        dialog._mark_modified()
        self.assertTrue(dialog._auto_save_timer.isActive(), "改动后应排入防抖计时")

        dialog._auto_save_timer.stop()
        dialog._auto_save()

        reloaded = self.library.load_scene(self.scene.scene_id)
        self.assertEqual(reloaded.name, "自动保存的名字")
        self.assertFalse(dialog._dirty(), "写盘后应清脏")

    def test_auto_save_off_does_not_write(self):
        """开关关闭时只改内存，不写盘。"""
        dialog = self._make_dialog()
        dialog.name_edit.setText("不该写盘的名字")
        dialog._mark_modified()
        self.assertFalse(dialog._auto_save_timer.isActive(), "未打开开关不该排保存")

        dialog._auto_save()

        reloaded = self.library.load_scene(self.scene.scene_id)
        self.assertEqual(reloaded.name, "已有场景")

    def test_auto_save_skips_new_scene(self):
        """新建场景先手动保存一次，自动保存不替它分配 id。"""
        dialog = self._make_dialog(scene_id="")
        dialog.auto_save_switch.setChecked(True)
        dialog._mark_modified()
        dialog._auto_save_timer.stop()

        dialog._auto_save()

        self.assertEqual(self.library.list_scene_ids(), [self.scene.scene_id], "不该凭空多出一个场景")

    def test_auto_save_flushes_before_switching(self):
        """自动保存开着时，切场景前先落盘，不弹确认。"""
        second = self.library.new_scene("第二个场景")
        second.features.append(
            model.Feature(name="feature-2", shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3), threshold=0.8)
        )
        self.library.save_scene(second)
        self.library.save_base_image(second.scene_id, _striped(800, 600))

        dialog = self._make_dialog()
        dialog.auto_save_switch.setChecked(True)
        dialog.name_edit.setText("切走前保存")
        dialog._mark_modified()
        dialog.next_scene()

        reloaded = self.library.load_scene(self.scene.scene_id)
        self.assertEqual(reloaded.name, "切走前保存")
        self.assertEqual(dialog.scene.scene_id, second.scene_id, "应切到下一个场景")

    def test_apply_rejects_without_closing(self):
        """前置条件不满足时「应用」不写盘、不关窗，只提示。"""
        dialog = self._make_dialog()
        closed = []
        dialog.accept = lambda: closed.append(1)
        dialog.scene.features = []
        dialog._mark_modified()

        dialog._on_apply()

        self.assertEqual(closed, [], "校验失败时不该关窗")
        self.assertTrue(dialog._dirty(), "失败时不该清脏")

    def test_apply_button_disabled_when_clean(self):
        """没有改动时「应用」是灰的（与保存同步）。

        让它常亮的话，用户会在没改任何东西时点它，得到「已应用」的虚假反馈。
        """
        dialog = self._make_dialog()
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog._mark_modified()
        self.assertTrue(dialog.apply_button.isEnabled())

    def test_save_rejects_scene_without_features(self):
        """没有识别特征的场景不允许保存（否则永远不会被识别）。"""
        dialog = self._make_dialog()
        dialog.scene.features = []
        dialog._on_save()
        self.assertIn("至少需要一个识别特征", dialog.status_label.text())

    def test_save_rejects_scene_without_image(self):
        """没有基准图不允许保存。"""
        dialog = self._make_dialog()
        dialog._display_frame = None
        dialog._on_save()
        self.assertIn("请先抓取画面", dialog.status_label.text())

    def test_new_scene_gets_id_on_save(self):
        """新建场景在保存时才分配正式 id。"""
        dialog = self._make_dialog(scene_id="")
        self.assertIsNone(dialog.saved_scene)
        dialog.name_edit.setText("全新场景")
        dialog.scene.features.append(model.Feature(name="f", shape=shapes.Shape.rectangle(0.1, 0.1, 0.2, 0.2)))
        dialog._raw_frame = _striped(800, 600)
        dialog._display_frame = _striped(800, 600)
        dialog.accept = lambda: None
        dialog._on_save()
        self.assertIsNotNone(dialog.saved_scene)
        self.assertTrue(dialog.saved_scene.scene_id.startswith("scene-"))
        reloaded = self.library.load_scene(dialog.saved_scene.scene_id)
        self.assertEqual(reloaded.name, "全新场景")

    def test_target_options_exclude_self(self):
        """目标场景选项不含自己（自环无意义）。"""
        options = scene_editor._target_options(self.library, self.scene.scene_id)
        values = [value for _text, value in options]
        self.assertNotIn(self.scene.scene_id, values)
        self.assertEqual(values[0], "")

    def test_mask_not_drawn_as_item(self):
        """遮罩不作为画布元素（不画框），只通过 set_mask_rect 涂黑。"""
        dialog = self._make_dialog()
        self.assertNotIn(("mask", "mask"), list(dialog.canvas._items.keys()))
        self.assertTrue(hasattr(dialog.canvas, "set_mask_rect"))

    def test_stale_templates_removed_on_save(self):
        """改名后旧模板文件被清理，目录里不留历史残留。

        掩码的行为是「没有筛选就不落盘」：该特征既没配掩码形状也没配背景色，
        `build_mask` 返回 None，保存时删掉旧掩码文件。这与写一张全 255 的掩码
        语义相同，但后者会让「这个特征有没有掩码」看不出来，而前者一眼可辨。
        """
        dialog = self._make_dialog()
        self._accept_property_dialog(
            {
                "name": "renamed",
                "kind": model.KIND_TEMPLATE,
                "mandatory": True,
                "threshold": "0.80",
                "text": "",
            }
        )
        dialog._edit_feature_at(0)
        dialog._raw_frame = _striped(800, 600)
        dialog._display_frame = _striped(800, 600)
        dialog.accept = lambda: None
        dialog._on_save()
        folder = os.path.join(self.library.scene_dir(self.scene.scene_id), "templates")
        names = set(os.listdir(folder)) if os.path.isdir(folder) else set()
        self.assertIn("renamed.png", names)
        self.assertNotIn("renamed_mask.png", names, "无筛选时不该落盘掩码")
        self.assertNotIn("feature-1.png", names)
        self.assertNotIn("feature-1_mask.png", names)

    def test_mask_written_when_filter_configured(self):
        """配了掩码形状或背景色时，掩码必须落盘。

        这是修 bug 的回归测试：此前保存只写 `feature.shape.mask(...)`（纯几何，
        且是**特征定位框**的形状而非模板掩码形状），颜色剔除完全没写进去——
        落盘的 mask 与实际参与匹配的像素不一致，排查时看 mask 图会得出
        「颜色剔除没生效」的错误结论。
        """
        import cv2

        dialog = self._make_dialog()
        feature = dialog.scene.features[0]
        feature.kind = model.KIND_TEMPLATE
        # 背景色要用图里真实存在的颜色：`_striped` 的背景是 0、条纹是 255。
        # 拿一个图里没有的颜色去筛，掩码自然是全 255，测出来的「没排除」
        # 与被测假设（颜色有没有写进掩码）无关。
        feature.bg_color = (0, 0, 0)
        feature.bg_tolerance = 4
        dialog._raw_frame = _striped(800, 600)
        dialog._display_frame = _striped(800, 600)
        dialog.accept = lambda: None
        dialog._on_save()

        path = self.library.mask_path(self.scene.scene_id, feature.name)
        self.assertTrue(os.path.isfile(path), "配了背景色就必须写掩码")
        mask = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        self.assertIsNotNone(mask)
        # 掩码必须真的排除了像素，而不是一张全 255
        self.assertLess(float((mask > 0).mean()), 1.0, "掩码没有任何排除，说明颜色没写进去")


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestConfigPageCards(unittest.TestCase):
    """配置页的卡片网格与四个动作。

    布局对齐上游模板页签：标题 + 计数 + 搜索框 + 四个按钮，下面是卡片流。
    卡片只显示缩略图与场景名；`删除`/`编辑` 随选中态显隐。
    """

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """建临时库与两个场景。"""
        self.temp_dir = tempfile.mkdtemp(prefix="scene-config-cards-")
        self.library = store.SceneLibrary(os.path.join(self.temp_dir, "scenes"))
        self.library.ensure_layout()
        self.library.set_base_resolution(800, 600)
        self.scene = self.library.new_scene("主页界面")
        self.scene.features.append(
            model.Feature(name="f1", shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3), threshold=0.8)
        )
        self.library.save_scene(self.scene)
        self.library.save_base_image(self.scene.scene_id, _striped(800, 600))

        self.second = self.library.new_scene("设置页")
        self.library.save_scene(self.second)
        self.library.save_base_image(self.second.scene_id, _striped(800, 600))

        self.changed = []
        self.page = config_page.ConfigPage(self.library, on_scene_changed=lambda: self.changed.append(1))
        self.page.resize(900, 700)
        # 必须 show()：`isVisible()` 要求控件自身与所有祖先都可见，未 show 的控件
        # 即使 `setVisible(True)` 也返回 False——那样断言测的是「窗口没开」而不是
        # 「按钮该不该出现」。
        self.page.show()
        self.page.reload_scenes()
        self.app.processEvents()

    def tearDown(self):
        """销毁控件并清理临时目录。"""
        self.page.deleteLater()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_card_per_scene(self):
        """每个场景一张卡片。"""
        self.assertEqual(len(self.page._cards), 2)
        self.assertIn(self.scene.scene_id, self.page._cards)
        self.assertIn(self.second.scene_id, self.page._cards)

    def test_card_shows_name_and_id(self):
        """卡片名称行显示「场景名 + 灰色 id」，id 不占副文本行。

        卡片是「找到那个场景」的入口，不是信息面板，所以不显示特征数/区域数；
        但 id 是定位场景目录必需的信息，与名称放同一行。
        """
        card = self.page._cards[self.scene.scene_id]
        text = card.name_label.text()
        self.assertIn("主页界面", text)
        self.assertIn(self.scene.scene_id, text)
        self.assertIn("<span", text, "id 应是小一号的灰色富文本片段")
        # 名称与 id 之间的两个不断行空格：防止只剩 id 单独掉到下一行。
        self.assertIn("&nbsp;&nbsp;", text)
        self.assertFalse(card.features_label.isVisible(), "id 走名称行，副文本行仍应隐藏")

    def test_card_name_is_html_escaped(self):
        """场景名含尖括号或 & 时按字面显示，不被当成 HTML 标签解析。

        名称是用户输入。不转义时 `<b>` 会让后面整段变粗体甚至整段消失，而界面上
        看不出是「名字里有个尖括号」还是「渲染坏了」。
        """
        weird = self.library.new_scene("a<b>c & d")
        self.library.save_scene(weird)
        self.library.save_base_image(weird.scene_id, _striped(800, 600))
        self.page.reload_scenes()
        card = self.page._cards[weird.scene_id]
        text = card.name_label.text()
        self.assertIn("a&lt;b&gt;c &amp; d", text)
        self.assertNotIn("a<b>c", text)

    def test_card_carries_scene_id(self):
        """卡片的 scene_id 是场景 id，不是图片路径。"""
        card = self.page._cards[self.scene.scene_id]
        self.assertEqual(card.scene_id, self.scene.scene_id)
        self.assertNotIn("preview.png", card.scene_id)
        self.assertTrue(hasattr(card, "clicked"), "必须沿用父类的 clicked 信号")

    def test_buttons_hidden_without_selection(self):
        """未选中时「删除」「编辑」不出现。"""
        self.assertFalse(self.page.delete_button.isVisible())
        self.assertFalse(self.page.edit_button.isVisible())

    def test_buttons_visible_after_selection(self):
        """选中卡片后「删除」「编辑」出现。"""
        self.page._on_card_clicked(self.scene.scene_id)
        self.assertTrue(self.page.delete_button.isVisible())
        self.assertTrue(self.page.edit_button.isVisible())

    def test_click_same_card_toggles_off(self):
        """再次点击同一张卡片取消选中。"""
        self.page._on_card_clicked(self.scene.scene_id)
        self.page._on_card_clicked(self.scene.scene_id)
        self.assertEqual(self.page.selected_scene_id, "")
        self.assertFalse(self.page.edit_button.isVisible())

    def test_click_other_card_switches_selection(self):
        """点击另一张卡片切换选中，且只有一张卡片高亮。"""
        self.page._on_card_clicked(self.scene.scene_id)
        self.page._on_card_clicked(self.second.scene_id)
        self.assertEqual(self.page.selected_scene_id, self.second.scene_id)
        selected = [cid for cid, card in self.page._cards.items() if card.selected]
        self.assertEqual(selected, [self.second.scene_id])

    def test_search_filters_cards(self):
        """搜索按场景名过滤卡片。"""
        self.page.search_box.setText("设置")
        self.page._apply_filter()
        visible = self.page.flow_widget._items
        self.assertEqual(len(visible), 1)
        self.assertIs(visible[0], self.page._cards[self.second.scene_id])

    def test_search_empty_shows_all(self):
        """清空搜索后恢复全部卡片。"""
        self.page.search_box.setText("设置")
        self.page._apply_filter()
        self.page.search_box.setText("")
        self.page._apply_filter()
        self.assertEqual(len(self.page.flow_widget._items), 2)

    def test_double_click_opens_editor(self):
        """双击卡片打开该场景的编辑弹窗。"""
        opened = []
        original = config_page.SceneEditorDialog

        class FakeDialog:
            """假弹窗：只记录被打开过。"""

            def __init__(self, library, scene_id="", capture_frame=None, parent=None):
                """记录场景 id。"""
                opened.append(scene_id)

            def exec(self):
                """不确认，保持页面状态不变。"""
                return False

            def deleteLater(self):
                """空实现。"""

        config_page.SceneEditorDialog = FakeDialog
        self.addCleanup(setattr, config_page, "SceneEditorDialog", original)

        self.page._on_card_double_clicked(self.scene.scene_id)
        self.assertEqual(opened, [self.scene.scene_id])
        self.assertEqual(self.page.selected_scene_id, self.scene.scene_id)

    def test_edit_button_opens_selected_scene(self):
        """「编辑」按钮打开当前选中的场景。"""
        opened = []
        original = config_page.SceneEditorDialog

        class FakeDialog:
            """假弹窗：只记录被打开过。"""

            def __init__(self, library, scene_id="", capture_frame=None, parent=None):
                """记录。"""
                opened.append(scene_id)

            def exec(self):
                """不确认。"""
                return False

            def deleteLater(self):
                """空实现。"""

        config_page.SceneEditorDialog = FakeDialog
        self.addCleanup(setattr, config_page, "SceneEditorDialog", original)

        self.page._on_card_clicked(self.second.scene_id)
        self.page._on_edit_scene()
        self.assertEqual(opened, [self.second.scene_id])

    def test_delete_removes_from_edit_library_only(self):
        """删除只作用于编辑区，产物区不动。

        产物区等「保存」同步：保留它作为唯一导出动作，产物区的内容才有唯一来源。
        """
        target = os.path.join(self.temp_dir, "assets_scenes")
        # 用只有 preview 的场景验证「产物区不动」：带特征但缺模板的场景会在导出时
        # 被跳过（那是另一条用例），拿它来断言「产物区有这个目录」会得到假阴性。
        exported, skipped = self.library.export_to(target)
        self.assertIn(self.second.scene_id, exported, "导出应先成功，否则断言无意义")

        # 直接替换 kit 的 confirm，避免弹真实对话框。
        import ok.ui.qt.kit as kit

        original_confirm = kit.confirm
        kit.confirm = lambda *args, **kwargs: True
        try:
            self.page._on_card_clicked(self.scene.scene_id)
            self.page._on_delete_scene()
        finally:
            kit.confirm = original_confirm

        self.assertIsNone(self.library.load_scene(self.scene.scene_id), "编辑区应已删除")
        self.assertTrue(
            os.path.isdir(os.path.join(target, self.second.scene_id)),
            "产物区必须保持原样，等「保存」才同步",
        )
        self.assertEqual(self.page.selected_scene_id, "")

    def test_delete_cancel_keeps_scene(self):
        """确认框取消时不删场景。"""
        import ok.ui.qt.kit as kit

        original_confirm = kit.confirm
        kit.confirm = lambda *args, **kwargs: False
        try:
            self.page._on_card_clicked(self.scene.scene_id)
            self.page._on_delete_scene()
        finally:
            kit.confirm = original_confirm

        self.assertIsNotNone(self.library.load_scene(self.scene.scene_id))

    def test_new_scene_name_follows_id(self):
        """新建场景的名称序号跟随 id，不另起计数器。

        历史缺陷：名称按「现有 场景N 的最大 N+1」从 1 数起，而 id 由库计数器分配。
        库里一旦有用户自定义名的场景（如 `scene-0002` 叫「靶场入口」），两套就对不上——
        实测 `scene-0004` 被叫成「场景 1」。
        """
        first = self.library.new_scene()
        self.library.save_scene(first)
        self.assertEqual(first.name, f"场景 {int(first.scene_id.split('-')[1])}")

        # 再建一个，序号继续跟着 id 走，不受首个名称影响。
        second = self.library.new_scene()
        self.library.save_scene(second)
        self.assertEqual(second.name, f"场景 {int(second.scene_id.split('-')[1])}")
        self.assertNotEqual(first.name, second.name, "两个场景不能同名")

    def test_explicit_name_is_kept(self):
        """显式传名时不覆盖——用户自定义名不该被默认命名规则改掉。"""
        scene = self.library.new_scene("靶场入口")
        self.assertEqual(scene.name, "靶场入口")
        self.assertNotEqual(scene.name, f"场景 {int(scene.scene_id.split('-')[1])}")

    def test_capture_creates_card_without_dialog(self):
        """截图后自动建卡片并选中，不弹编辑窗。

        与模板页签的「截图」一致：先抓后配。弹窗是「我要配这个场景」的明确动作，
        与「先把画面抓下来」不是同一件事。
        """
        opened = []
        original_dialog = config_page.SceneEditorDialog

        class FakeDialog:
            """假弹窗：只记录被打开过。"""

            def __init__(self, *args, **kwargs):
                """记录。"""
                opened.append(args)

            def exec(self):
                """不确认。"""
                return False

            def deleteLater(self):
                """空实现。"""

        config_page.SceneEditorDialog = FakeDialog
        self.addCleanup(setattr, config_page, "SceneEditorDialog", original_dialog)

        self.page._capture_frame = lambda: _striped(800, 600)
        self.page._do_capture()

        self.assertEqual(opened, [], "新建场景不应弹编辑窗")
        self.assertEqual(len(self.page._cards), 3)
        self.assertTrue(self.page.selected_scene_id)
        created = self.library.load_scene(self.page.selected_scene_id)
        # 名称的序号从 id 派生：库里已有 scene-0002/0003，新建的是 scene-0004。
        self.assertEqual(created.name, f"场景 {int(created.scene_id.split('-')[1])}")

    def test_import_creates_card_without_dialog(self):
        """导入图片后自动建卡片并选中，不弹编辑窗。

        与截图同一条路径：先有画面、再配置。
        """
        opened = []
        original_dialog = config_page.SceneEditorDialog

        class FakeDialog:
            """假弹窗：只记录被打开过。"""

            def __init__(self, *args, **kwargs):
                """记录。"""
                opened.append(args)

            def exec(self):
                """不确认。"""
                return False

            def deleteLater(self):
                """空实现。"""

        config_page.SceneEditorDialog = FakeDialog
        self.addCleanup(setattr, config_page, "SceneEditorDialog", original_dialog)

        # 绕过文件对话框，直接走落盘路径
        self.page._create_scene_from_frame(_striped(800, 600))
        self.assertEqual(opened, [], "导入不应弹编辑窗")
        self.assertEqual(len(self.page._cards), 3)
        self.assertTrue(self.page.selected_scene_id)

    def test_capture_disabled_without_capture_function(self):
        """没有取帧函数时「截图」禁用。"""
        self.page._capture_frame = None
        self.page._update_buttons()
        self.assertFalse(self.page.capture_button.isEnabled())

    def test_empty_state_visible_without_scenes(self):
        """库里没有场景时显示空态，隐藏卡片区。"""
        empty_dir = tempfile.mkdtemp(prefix="scene-empty-")
        self.addCleanup(shutil.rmtree, empty_dir, True)
        empty = store.SceneLibrary(os.path.join(empty_dir, "scenes"))
        empty.ensure_layout()
        empty.set_base_resolution(800, 600)
        page = config_page.ConfigPage(empty)
        self.addCleanup(page.deleteLater)
        page.resize(900, 700)
        page.show()
        page.reload_scenes()
        self.app.processEvents()

        self.assertTrue(page.empty_widget.isVisible())
        self.assertFalse(page.scroll_area.isVisible())
        self.assertEqual(page.count_label.text(), "")

    def test_no_canvas_or_combo_left(self):
        """旧的画布与下拉框已移除。"""
        self.assertFalse(hasattr(self.page, "canvas"))
        self.assertFalse(hasattr(self.page, "scene_combo"))
        self.assertFalse(hasattr(self.page, "feature_table"))


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestConfigPageResolutionGuard(unittest.TestCase):
    """配置页的三道分辨率判断。

    被测缺陷：基准分辨率只在「库里还没有」时写入，之后无论画面变成多少都不再更新。
    换分辨率后再截图，新场景的预览图与库基准不一致，而它的相对坐标仍按基准写入——
    画布上看起来正常（存与读用同一套错口径，互相抵消），但运行期识别时帧被缩放到
    基准，特征框整体错位。表现是「这个场景从来识别不到」，不报错。

    三道判断各测一支：空库立基准、已有场景拒绝不一致的截图、保存前全面检查。
    """

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """建一个 800×600 基准、含一个场景的临时库。"""
        self.temp_dir = tempfile.mkdtemp(prefix="scene-guard-")
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.library = store.SceneLibrary(os.path.join(self.temp_dir, "scenes"))
        self.library.ensure_layout()
        self.library.set_base_resolution(800, 600)

        scene = self.library.new_scene("主页")
        self.library.save_scene(scene)
        self.library.save_base_image(scene.scene_id, _striped(800, 600))
        self.scene = scene

        self.captured = {"count": 0, "dialogs": []}
        self.page = config_page.ConfigPage(self.library, capture_frame=self._fake_capture)
        self.page.resize(900, 700)
        self.page.show()
        self.page.reload_scenes()
        self.app.processEvents()
        self.addCleanup(self.page.deleteLater)

    def _fake_capture(self):
        """返回一张与基准一致的帧，并记录调用次数。"""
        self.captured["count"] += 1
        return _striped(800, 600)

    def _patch_dialog(self, accept):
        """替换 kit 的 Dialog，记录标题与正文，并按 `accept` 返回。

        **必须补丁 `ok.ui.qt.kit.Dialog` 而不是 `kit.dialogs.Dialog`**：两处调用点
        都在函数内写 `from ok.ui.qt.kit import Dialog`，解析的是包模块的属性。
        补丁错层时真实对话框会弹出来并阻塞在 `exec()`——测试不是失败而是挂住，
        表现为「没输出、也不结束」。

        Args：
            accept：`exec()` 的返回值。

        Returns：
            记录列表，元素为 `(title, message)`。
        """
        import ok.ui.qt.kit as kit

        records = []
        original = kit.Dialog

        class FakeDialog:
            """假对话框：只记录内容。"""

            def __init__(self, parent, title, message=None, **kwargs):
                """记录标题与正文。"""
                records.append((title, message or ""))

            def set_cancel_visible(self, _visible):
                """空实现，接口对齐。"""

            def exec(self):
                """按预置结果返回。"""
                return accept

            def deleteLater(self):
                """空实现。"""

        kit.Dialog = FakeDialog
        self.addCleanup(setattr, kit, "Dialog", original)
        return records

    def test_empty_library_sets_base_from_first_frame(self):
        """空库第一次截图时，用该帧立基准。"""
        empty_dir = os.path.join(self.temp_dir, "fresh")
        empty = store.SceneLibrary(empty_dir)
        empty.ensure_layout()
        page = config_page.ConfigPage(empty)
        self.addCleanup(page.deleteLater)
        page.resize(900, 700)
        page.reload_scenes()

        created = page._create_scene_from_frame(_striped(1024, 768))
        self.assertTrue(created, "空库应允许第一帧")
        self.assertEqual((empty.meta.base_width, empty.meta.base_height), (1024, 768))

    def test_mismatched_frame_blocked(self):
        """已有场景时，宽高比不同的帧被拒绝，且不落盘。"""
        records = self._patch_dialog(accept=False)
        before = len(self.page._cards)

        created = self.page._create_scene_from_frame(_striped(640, 400))

        self.assertFalse(created, "不一致的帧不应建场景")
        self.assertEqual(len(self.page._cards), before, "卡片数不能变")
        self.assertEqual(len(records), 1, "必须弹一次说明")
        title, message = records[0]
        self.assertIn("Resolution", title)
        self.assertIn("宽高比", message)
        self.assertIn("640×400", message)

    def test_matching_frame_allowed(self):
        """与基准一致的帧正常建场景，不弹窗。"""
        records = self._patch_dialog(accept=False)
        created = self.page._create_scene_from_frame(_striped(800, 600))
        self.assertTrue(created)
        self.assertEqual(records, [], "一致时不该打扰用户")

    def test_scaled_frame_allowed_with_warning(self):
        """比例相同、尺寸不同的帧放行，并在状态栏提示会被缩放。"""
        records = self._patch_dialog(accept=False)
        created = self.page._create_scene_from_frame(_striped(1600, 1200))
        self.assertTrue(created, "比例一致时应放行")
        self.assertEqual(records, [], "软提示不该弹窗")
        self.assertIn("缩放", self.page.status_label.text())

    def test_capture_blocked_does_not_create_card(self):
        """走「截图」按钮时同样被拦，卡片数不变。"""
        records = self._patch_dialog(accept=False)
        before = len(self.page._cards)
        self.page._capture_frame = lambda: _striped(640, 400)

        self.page._do_capture()

        self.assertEqual(len(self.page._cards), before)
        self.assertEqual(len(records), 1)

    def test_rebase_only_when_library_empty(self):
        """库里有场景时不允许改基准，提示写清原因。"""
        self.page._rebase_resolution()
        self.assertEqual((self.library.meta.base_width, self.library.meta.base_height), (800, 600))
        # 无翻译环境时 `tr()` 回退到英文源串，因此按源串断言。
        self.assertIn("base resolution", self.page.status_label.text())

    def test_save_blocked_on_ratio_mismatch(self):
        """保存前检查到宽高比不一致时中止导出。

        这条要连 `tab._check_resolutions` 一起验证：配置页只管截图准入，历史数据
        （比如改过基准之前拍的场景）只有在保存这一刻才会被全面核对。
        """
        from extensions.capabilities.scene_manager import tab as tab_module

        # 造一个与基准比例不同的场景，绕过配置页的准入（模拟历史数据）。
        bad = self.library.new_scene("历史错比例")
        self.library.save_scene(bad)
        self.library.save_base_image(bad.scene_id, _striped(640, 400))

        owner = tab_module.SceneManagerTab.__new__(tab_module.SceneManagerTab)
        owner.edit_library = self.library
        owner.library = self.library
        owner.logger = __import__("logging").getLogger("test")
        owner.tr = lambda text: text
        owner.config_page = self.page

        records = self._patch_dialog(accept=True)
        self.assertFalse(owner._check_resolutions(), "比例不一致必须中止保存")
        self.assertEqual(len(records), 1)
        self.assertIn("历史错比例", records[0][1])

    def test_save_passes_when_all_consistent(self):
        """全部一致时保存前检查放行，不弹窗。"""
        from extensions.capabilities.scene_manager import tab as tab_module

        owner = tab_module.SceneManagerTab.__new__(tab_module.SceneManagerTab)
        owner.edit_library = self.library
        owner.logger = __import__("logging").getLogger("test")
        owner.tr = lambda text: text
        owner.config_page = self.page

        records = self._patch_dialog(accept=True)
        self.assertTrue(owner._check_resolutions())
        self.assertEqual(records, [])


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestSceneManagerTab(unittest.TestCase):
    """主页签的子页签切换。

    被测缺陷：`current_changed` 只连了「刷新数据」，从未把 `QStackedWidget` 切到
    对应控件，因此点任何子页签都停在第一页（场景配置），而**没有任何报错**——
    页签高亮变了、数据也 reload 了，只有内容是错的。

    这类缺陷只能靠「点一下再断言当前控件」发现，所以这里逐个页签真点。
    """

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """用临时目录顶替两个场景库，避免测试写进仓库。"""
        from unittest import mock

        from extensions.capabilities.scene_manager import runtime

        self.temp_dir = tempfile.mkdtemp(prefix="scene-tab-")
        self.addCleanup(shutil.rmtree, self.temp_dir, True)

        def _library(name):
            library = store.SceneLibrary(os.path.join(self.temp_dir, name))
            library.ensure_layout()
            return library

        patches = [
            mock.patch.object(runtime, "open_edit_library", lambda *_a, **_k: _library("edit")),
            mock.patch.object(runtime, "open_library", lambda *_a, **_k: _library("assets")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        from extensions.capabilities.scene_manager.tab import (
            KEY_CONFIG,
            KEY_DRAFTS,
            KEY_EXPLORE,
            KEY_FLOW,
            SceneManagerTab,
        )

        self.KEY_CONFIG = KEY_CONFIG
        self.KEY_FLOW = KEY_FLOW
        self.KEY_EXPLORE = KEY_EXPLORE
        self.KEY_DRAFTS = KEY_DRAFTS
        self.tab = SceneManagerTab()
        self.tab.resize(1000, 700)
        self.tab.show()
        self.app.processEvents()
        self.addCleanup(self.tab.deleteLater)

    def _click(self, key):
        """点一个页签并处理事件。"""
        self.tab.strip.item(key).click()
        for _ in range(3):
            self.app.processEvents()

    def test_initial_page_is_config(self):
        """初始显示场景配置页。"""
        self.assertEqual(self.tab.strip.current_key(), self.KEY_CONFIG)
        self.assertIs(self.tab.stack.currentWidget(), self.tab.config_page)

    def test_click_flow_switches_content(self):
        """点「场景路由」后堆栈切到路由页。"""
        self._click(self.KEY_FLOW)
        self.assertIs(self.tab.stack.currentWidget(), self.tab.flow_page)

    def test_click_explore_switches_content(self):
        """点「探索」后堆栈切到探索页——它与场景路由已经拆开。"""
        self._click(self.KEY_EXPLORE)
        self.assertIs(self.tab.stack.currentWidget(), self.tab.explore_page)

    def test_click_drafts_switches_content(self):
        """点「草稿确认」后堆栈切到草稿页。"""
        self._click(self.KEY_DRAFTS)
        self.assertIs(self.tab.stack.currentWidget(), self.tab.drafts_page)

    def test_click_back_to_config(self):
        """从后面的页签点回「场景配置」能切回来。"""
        self._click(self.KEY_DRAFTS)
        self._click(self.KEY_CONFIG)
        self.assertIs(self.tab.stack.currentWidget(), self.tab.config_page)

    def test_interrupt_uses_running_task_instance(self):
        """中断直接作用于启动时保存的实例，不依赖 get_task 反查。"""
        from unittest import mock

        task = mock.Mock()
        self.tab._explore_task = task
        self.tab.get_task = mock.Mock(side_effect=AssertionError("不应反查任务"))
        self.tab._interrupt_explore()
        self.assertTrue(task.cancel_requested)

    def test_interrupt_falls_back_to_get_task(self):
        """引用没挂上时（热重载前后）仍能按类反查并中断。"""
        from unittest import mock

        task = mock.Mock()
        self.tab._explore_task = None
        self.tab.get_task = mock.Mock(return_value=task)
        self.tab._interrupt_explore()
        self.assertTrue(task.cancel_requested)

    def test_interrupt_reports_when_task_missing(self):
        """连反查都拿不到任务时给出可读状态，而不是静默无反应。"""
        from unittest import mock

        self.tab._explore_task = None
        self.tab.get_task = mock.Mock(return_value=None)
        self.tab._interrupt_explore()
        self.assertIn("中断失败", self.tab.explore_page.status_label.text())

    def test_debug_bridge_reads_edit_library(self):
        """调试桥接读编辑区：刚连的边不会在调试页报「没有可用路径」。"""
        from unittest import mock

        task = mock.Mock()
        task.click = lambda *args, **kwargs: None
        task.ocr = lambda *args, **kwargs: None
        self.tab.executor = mock.Mock()
        self.tab.get_task = mock.Mock(return_value=task)
        bridge = self.tab._build_bridge()
        self.assertIs(bridge.library, self.tab.edit_library)
        self.assertIs(bridge.draft_library, self.tab.edit_library)

    def test_export_import_buttons_live_on_tab_strip(self):
        """页签靠左，导入导出固定在页签条最右两项。"""
        layout = self.tab.strip.layout()
        count = layout.count()
        self.assertIs(layout.itemAt(count - 2).widget(), self.tab.export_button)
        self.assertIs(layout.itemAt(count - 1).widget(), self.tab.import_button)
        first_tab = layout.indexOf(self.tab.strip.item(self.KEY_CONFIG))
        self.assertLess(first_tab, layout.indexOf(self.tab.export_button), "页签应在按钮左侧")
        self.assertTrue(self.tab.export_button.isVisibleTo(self.tab))
        self.assertTrue(self.tab.import_button.isVisibleTo(self.tab))

    def test_switch_to_explore_reloads(self):
        """切到探索页会重新读库，避免仍显示路由页保存前的旧边。"""
        from unittest import mock

        with mock.patch.object(self.tab.explore_page, "reload") as reload_mock:
            self._click(self.KEY_EXPLORE)
        reload_mock.assert_called_once()

    def test_page_map_covers_every_strip_key(self):
        """每个页签都有对应的控件，且堆栈里没有多余的页。

        这条是切页生效的前提：`_add_page` 漏记一次，点击就会静默无反应。
        """
        self.assertEqual(sorted(self.tab._pages), sorted(self.tab.strip.keys()))
        self.assertEqual(self.tab.stack.count(), len(self.tab._pages))
        for key, page in self.tab._pages.items():
            self.assertIs(self.tab.stack.widget(self.tab.stack.indexOf(page)), page, f"{key} 未进堆栈")

    # --- 开始探索 -----------------------------------------------------------

    def _fake_executor(self, paused):
        """造一个只提供 `paused` 的执行器桩。

        Args：
            paused：执行器是否处于暂停态。

        Returns：
            桩对象。
        """

        class _Executor:
            """只暴露本用例关心的状态。"""

            def __init__(self, is_paused):
                self.paused = is_paused

        return _Executor(paused)

    def _fake_task(self):
        """造一个记录 `enable()` 调用的探索任务桩。

        Returns：
            `(task, calls)`，`calls` 在 `enable()` 被调用时追加。
        """
        calls = []

        class _Task:
            """探索任务桩。"""

            def enable(self):
                calls.append(1)

        return _Task(), calls

    def test_explore_blocked_when_executor_paused(self):
        """执行器暂停时点「开始探索」，**不入队**，并告知要先点「启动」。

        **这是用户报的缺陷**：点「开始探索」没反应。实因是 `task.enable()` 只
        入队，真正执行要等执行器从暂停态醒来（用户在主界面点「启动」）。未运行
        时入队的结果是——日志里只有 `queued onetime_task`，之后没有任何执行记录，
        界面上只有一行小字，用户只会觉得按钮坏了。

        Note:
            文案必须是**「启动」**，不是「开始」：主界面那个按钮的中文文案是
            `RunBar` 的 `Start {name}` → 「启动{name}」（截图里是
            「启动ok-script-app-pro」）。写「开始」会让用户找不到对应按钮。
        """
        task, calls = self._fake_task()
        self.tab.executor = self._fake_executor(paused=True)
        self.tab.get_task = lambda _cls: task

        self.tab._start_explore()

        self.assertEqual(calls, [], "执行器暂停时不该入队（入了也不会跑）")
        text = self.tab.explore_page.status_label.text()
        self.assertIn("启动", text, f"提示必须指向主界面那个名为「启动」的按钮，实测：{text!r}")
        self.assertNotIn("「开始」", text, f"按钮实际叫「启动」，不该写「开始」，实测：{text!r}")

    def test_explore_blocked_shows_toast(self):
        """执行器暂停时**弹出浮层提示**，不是只写状态栏。

        用户实测：状态栏那行小字在页面底部，点完按钮视线还在按钮上，根本不会
        注意到。浮层出现在窗口顶部居中，并带警告图标。
        """
        from ok.ui.qt.kit.feedback import Toast

        task, _calls = self._fake_task()
        self.tab.executor = self._fake_executor(paused=True)
        self.tab.get_task = lambda _cls: task
        self.tab.show()
        for _ in range(4):
            self.app.processEvents()

        self.tab._start_explore()
        for _ in range(6):
            self.app.processEvents()

        toasts = self.tab.window().findChildren(Toast)
        self.assertEqual(len(toasts), 1, "必须弹出一条浮层")
        toast = toasts[0]
        self.assertEqual(toast.tone, "warning", "这是「当前不能做」的警告，不是普通信息")
        self.assertIn("启动", toast.message_label.text())
        self.assertIn("启动", toast.title_label.text())

    def test_explore_enqueues_when_running(self):
        """执行器在运行时正常入队。"""
        task, calls = self._fake_task()
        self.tab.executor = self._fake_executor(paused=False)
        self.tab.get_task = lambda _cls: task

        self.tab._start_explore()

        self.assertEqual(calls, [1], "运行态应把任务入队")
        self.assertIn("已入队", self.tab.explore_page.status_label.text())

    def test_explore_reports_missing_task(self):
        """取不到探索任务时给出提示，不静默失败。"""
        self.tab.executor = self._fake_executor(paused=False)
        self.tab.get_task = lambda _cls: None

        self.tab._start_explore()

        self.assertIn("未注册", self.tab.explore_page.status_label.text())

    def test_explore_reports_no_executor(self):
        """执行器缺失时给出提示。"""
        self.tab.executor = None

        self.tab._start_explore()

        self.assertIn("执行器未就绪", self.tab.explore_page.status_label.text())

    def test_explore_button_wired(self):
        """「开始探索」按钮确实接到页签的探索回调。

        用户点按钮没反应时，先要排除「信号没接上」这种低级原因——`connect` 少写
        一行不会有任何报错，按钮照常发光发热。

        Note:
            改的是 `explore_page._on_explore`（按钮经 `_on_start_explore` 转调它），
            不是页签上的 `_start_explore`：后者在构造时已被绑给 `_on_explore`，
            事后改它不影响已建立的连接。
        """
        seen = []
        self.tab.explore_page._on_explore = lambda: seen.append(1)

        self.tab.explore_page.explore_button.click()
        self.app.processEvents()

        self.assertEqual(seen, [1], "按钮必须经 `_on_start_explore` 触发探索回调")

    # --- 滚动边界 -----------------------------------------------------------

    def _pump(self, times=5):
        """处理若干轮事件，让布局与滚动条范围稳定下来。"""
        for _ in range(times):
            self.app.processEvents()

    def _fill_cards(self, count):
        """往卡片网格里塞指定数量的占位卡片。

        Returns：
            生成的卡片列表。

        Note:
            用最小的卡片桩而不是真实 `SceneImageCard`：本用例只关心高度与
            滚动范围，真实卡片要读图、算名称行高，慢且与本行为无关。
            `FlowWidget` 依赖 `set_card_width` 与 `natural_height` 两个接口。
        """

        class _Card(QWidget):
            """固定高度的卡片桩。"""

            natural_height = 120

            def set_card_width(self, width):
                """按目标宽度设置尺寸。"""
                self.resize(width, self.natural_height)

        page = self.tab.config_page
        page.scroll_area.setVisible(True)
        page.empty_widget.setVisible(False)
        cards = [_Card() for _ in range(count)]
        page.flow_widget.set_items(cards)
        self._pump()
        return cards

    def test_config_page_never_scrolls_as_a_whole(self):
        """配置页的外层页签没有滚动余量，页面高度恒等于视口高度。

        **这是用户报的缺陷**：滚动区应该是场景卡片列表，而不是整个配置页。
        只要外层还留着滚动余量，滚轮滚动的就是整页——标题行、搜索框与状态行
        会被一起滚出视口，而卡片列表自己反而不滚。
        """
        self._click(self.KEY_CONFIG)
        self._pump()

        self.assertEqual(
            self.tab.view.maximumHeight(),
            self.tab.viewport().height(),
            "外层 view 的最大高度必须钉在视口高度",
        )
        self.assertEqual(self.tab.verticalScrollBar().maximum(), 0, "外层页签不应出现滚动范围")

    def test_only_card_list_scrolls_when_cards_overflow(self):
        """卡片多到放不下时，滚动只发生在卡片列表内部，页面本身不动。"""
        self._click(self.KEY_CONFIG)
        self._fill_cards(40)
        page = self.tab.config_page

        self.assertGreater(
            page.scroll_area.verticalScrollBar().maximum(),
            0,
            "卡片超出可视高度时，卡片列表自身必须可滚动",
        )
        self.assertEqual(self.tab.verticalScrollBar().maximum(), 0, "卡片再多也不该让整页滚动")

        # 滚到列表底部，页面的标题/搜索框位置不能变——这是「只有列表在滚」的
        # 可观测判据。只断言外层滚动范围为 0 不能排除「内层滚动带动外层布局」。
        before = page.search_box.mapTo(page, QPoint(0, 0))
        bar = page.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._pump()
        after = page.search_box.mapTo(page, QPoint(0, 0))
        self.assertEqual(before, after, "滚动卡片列表时页面固定部分不能移动")

    def test_empty_state_hint_wraps(self):
        """配置页空态提示换行，避免撑大整页最小宽度。"""
        self.assertTrue(self.tab.config_page.empty_hint_label.wordWrap())

    def test_drafts_hint_wraps(self):
        """草稿页说明换行，避免撑大共享堆栈的最小宽度。

        `QStackedWidget` 的最小宽度取所有子页的最大值；草稿页那行说明不换行
        时会把整组页签推得比窗口还宽，配置页的卡片列表右侧因此被裁掉。
        """
        self.assertTrue(self.tab.drafts_page.hint_label.wordWrap())


if __name__ == "__main__":
    unittest.main()
