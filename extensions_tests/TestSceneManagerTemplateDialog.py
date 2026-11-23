# -*- coding: utf-8 -*-
"""模板图对话框：参数区（全部常驻可编辑）与多边形顶点数。

## 为什么这层要单独测

参数区的核心约定是「**所有字段都显示，不按形状隐藏**，不适用时置灰」。这条
约定容易被无意改回「按形状显示隐藏」——那样做单看每一屏都很干净，但用户切
几次形状就记不清到底有哪些参数，而界面上没有任何痕迹说明「少掉的那行还在」。
"""

import unittest

import numpy as np

from extensions.capabilities.scene_manager import mask_shape

try:
    from PySide6.QtWidgets import QApplication

    from extensions.capabilities.scene_manager.pages.template_image import TemplateImageDialog

    QT_ERROR = None
except ModuleNotFoundError as exc:  # 缺界面依赖
    QApplication = None
    TemplateImageDialog = None
    QT_ERROR = str(exc)


def _app():
    """返回（必要时创建）QApplication 实例。"""
    return QApplication.instance() or QApplication([])


@unittest.skipIf(QApplication is None, f"缺少界面依赖：{QT_ERROR}")
class TestParameterPanel(unittest.TestCase):
    """参数区：全字段常驻、按形状置灰、写入生效。"""

    def setUp(self):
        """造一个宽扁模板与对话框。"""
        self.app = _app()
        template = np.zeros((47, 430, 3), dtype=np.uint8)
        template[:] = (48, 48, 48)
        self.dialog = TemplateImageDialog(template, color=(48, 48, 48), tolerance=4)

    def tearDown(self):
        """释放对话框。"""
        self.dialog.deleteLater()

    def test_all_fields_exist_for_every_shape(self):
        """切换所有形状时，每个参数控件都还存在（只隐藏，不销毁）。

        Note:
            控件必须一直存在：切换形状时若销毁重建，用户正在输入的框会失焦、
            光标位置丢失。隐藏只是不占布局空间。
        """
        controls = (
            self.dialog.angle_spin,
            self.dialog.sweep_spin,
            self.dialog.radius_spin,
            self.dialog.cx_spin,
            self.dialog.cy_spin,
            self.dialog.vertex_count,
        )
        for kind in (
            mask_shape.MASK_NONE,
            mask_shape.MASK_RECT,
            mask_shape.MASK_CIRCLE,
            mask_shape.MASK_SECTOR,
            mask_shape.MASK_POLYGON,
        ):
            self.dialog._on_shape_chosen(kind)
            for control in controls:
                self.assertIsNotNone(control, f"{kind} 下控件被销毁了")

    def test_fields_shown_only_when_relevant(self):
        """不适用于当前形状的参数**整组隐藏**，而不是置灰。

        **这是布局改造后的行为**。早期置灰，六组参数全摆着，用户要逐个读灰掉
        的项才知道哪些能用；隐藏后一眼只剩当前形状真正可调的项。
        """
        expected = {
            mask_shape.MASK_RECT: {"angle": True, "sweep": False, "radius": False, "vertex": False},
            mask_shape.MASK_CIRCLE: {"angle": False, "sweep": False, "radius": True, "vertex": False},
            mask_shape.MASK_SECTOR: {"angle": True, "sweep": True, "radius": True, "vertex": False},
            mask_shape.MASK_POLYGON: {"angle": False, "sweep": False, "radius": False, "vertex": True},
        }
        for kind, flags in expected.items():
            self.dialog._on_shape_chosen(kind)
            self.assertEqual(not self.dialog.angle_field.isHidden(), flags["angle"], f"{kind} angle")
            self.assertEqual(not self.dialog.sweep_field.isHidden(), flags["sweep"], f"{kind} sweep")
            self.assertEqual(not self.dialog.radius_field.isHidden(), flags["radius"], f"{kind} radius")
            self.assertEqual(not self.dialog.vertex_field.isHidden(), flags["vertex"], f"{kind} vertex")

    def test_center_fields_shown_for_circle_and_sector(self):
        """圆与扇形必须有圆心输入框——它们是唯一能把形状挪到指定位置的手段。"""
        for kind in (mask_shape.MASK_CIRCLE, mask_shape.MASK_SECTOR, mask_shape.MASK_POLYGON):
            self.dialog._on_shape_chosen(kind)
            self.assertFalse(self.dialog.cx_field.isHidden(), f"{kind} 缺圆心 X")
            self.assertFalse(self.dialog.cy_field.isHidden(), f"{kind} 缺圆心 Y")

    def test_none_hides_all_parameters(self):
        """`none` 没有几何，所有参数组都隐藏。"""
        self.dialog._on_shape_chosen(mask_shape.MASK_NONE)
        for holder in (
            self.dialog.angle_field,
            self.dialog.sweep_field,
            self.dialog.radius_field,
            self.dialog.cx_field,
            self.dialog.cy_field,
            self.dialog.vertex_field,
        ):
            self.assertTrue(holder.isHidden(), f"`none` 下 {holder} 应当隐藏")
        self.assertTrue(self.dialog.reset_params_button.isHidden(), "`none` 下不该有重置按钮")

    def test_radius_input_writes_percent(self):
        """半径输入框按「宽度百分比」写入模型。"""
        self.dialog._on_shape_chosen(mask_shape.MASK_CIRCLE)
        self.dialog.radius_spin.setValue(25)
        self.assertAlmostEqual(self.dialog.shape.radius, 0.25, places=4)

    def test_center_input_moves_shape(self):
        """圆心输入框能把形状挪到指定位置。"""
        self.dialog._on_shape_chosen(mask_shape.MASK_CIRCLE)
        self.dialog.cx_spin.setValue(30.0)
        self.dialog.cy_spin.setValue(70.0)
        cx, cy = self.dialog.shape.center()
        self.assertAlmostEqual(cx, 0.30, places=3)
        self.assertAlmostEqual(cy, 0.70, places=3)

    def test_sweep_input_writes_span(self):
        """张角输入框写入模型。"""
        self.dialog._on_shape_chosen(mask_shape.MASK_SECTOR)
        self.dialog.sweep_spin.setValue(200)
        self.assertAlmostEqual(self.dialog.shape.sweep, 200.0, places=3)

    def test_angle_input_writes_rotation_for_rect(self):
        """矩形的角度输入框写旋转角。"""
        self.dialog._on_shape_chosen(mask_shape.MASK_RECT)
        self.dialog.angle_spin.setValue(35)
        self.assertAlmostEqual(self.dialog.shape.angle, 35.0, places=3)

    def test_sync_does_not_loop(self):
        """刷新把模型值写回输入框时不能触发递归刷新。

        若忘了 `blockSignals`，`setValue` 会触发 `_on_params_changed` → `_refresh`
        → `setValue` 的无限递归——界面上表现为「一打开就卡死」。
        """
        self.dialog._on_shape_chosen(mask_shape.MASK_SECTOR)
        self.dialog.angle_spin.setValue(120)
        self.dialog.sweep_spin.setValue(45)
        # 反复刷新若干次，若递归会在这里栈溢出
        for _ in range(20):
            self.dialog._refresh()
        self.assertAlmostEqual(self.dialog.shape.angle, 120.0, places=3)


@unittest.skipIf(QApplication is None, f"缺少界面依赖：{QT_ERROR}")
class TestPolygonVertexControls(unittest.TestCase):
    """多边形顶点数控件。"""

    def setUp(self):
        """造对话框。"""
        self.app = _app()
        template = np.zeros((47, 430, 3), dtype=np.uint8)
        template[:] = (48, 48, 48)
        self.dialog = TemplateImageDialog(template)
        self.dialog._on_shape_chosen(mask_shape.MASK_POLYGON)

    def tearDown(self):
        """释放对话框。"""
        self.dialog.deleteLater()

    def test_vertex_spin_reflects_point_count(self):
        """顶点数输入框与模型点数一致。"""
        self.assertEqual(self.dialog.vertex_count.value(), len(self.dialog.shape.points))

    def test_changing_count_rebuilds(self):
        """改顶点数会重建轮廓。"""
        self.dialog.vertex_count.setValue(7)
        self.assertEqual(len(self.dialog.shape.points), 7)

    def test_reset_params_restores_rectangle(self):
        """重置参数把多边形恢复为默认轮廓（内缩矩形）。

        拖乱了之后的退路：对话框没有撤销栈，用户需要的不是「撤回一步」，
        而是「回到一个规整的形状重新开始」。
        """
        self.dialog.vertex_count.setValue(8)
        self.dialog.cx_spin.setValue(70.0)
        self.dialog._on_reset_params()
        xs = {round(point[0], 3) for point in self.dialog.shape.points}
        ys = {round(point[1], 3) for point in self.dialog.shape.points}
        self.assertEqual(len(xs), 2, "重置后应是轴对齐矩形")
        self.assertEqual(len(ys), 2)
        cx, cy = self.dialog.shape.center()
        self.assertAlmostEqual(cx, 0.5, places=3, msg="重置后应回到居中")
        self.assertAlmostEqual(cy, 0.5, places=3)


@unittest.skipIf(QApplication is None, f"缺少界面依赖：{QT_ERROR}")
class TestResetParameters(unittest.TestCase):
    """重置参数到默认值。"""

    def setUp(self):
        """造对话框。"""
        self.app = _app()
        template = np.zeros((47, 430, 3), dtype=np.uint8)
        template[:] = (48, 48, 48)
        self.dialog = TemplateImageDialog(template, color=(48, 48, 48), tolerance=6)

    def tearDown(self):
        """释放对话框。"""
        self.dialog.deleteLater()

    def test_reset_circle_restores_default_radius_and_center(self):
        """圆被改乱后，重置回到默认半径与居中位置。"""
        self.dialog._on_shape_chosen(mask_shape.MASK_CIRCLE)
        self.dialog.radius_spin.setValue(5)
        self.dialog.cx_spin.setValue(90.0)
        self.dialog._on_reset_params()
        expected = mask_shape.MaskShape.from_kind(mask_shape.MASK_CIRCLE, self.dialog._aspect())
        self.assertAlmostEqual(self.dialog.shape.radius, expected.radius, places=4)
        cx, cy = self.dialog.shape.center()
        self.assertAlmostEqual(cx, 0.5, places=3)
        self.assertAlmostEqual(cy, 0.5, places=3)

    def test_reset_sector_restores_angles(self):
        """扇形重置把起始角与张角都恢复默认。"""
        self.dialog._on_shape_chosen(mask_shape.MASK_SECTOR)
        self.dialog.angle_spin.setValue(200)
        self.dialog.sweep_spin.setValue(300)
        self.dialog._on_reset_params()
        self.assertAlmostEqual(self.dialog.shape.angle, 0.0, places=3)
        self.assertAlmostEqual(self.dialog.shape.sweep, 90.0, places=3)

    def test_reset_rect_clears_rotation(self):
        """矩形重置清掉旋转角。"""
        self.dialog._on_shape_chosen(mask_shape.MASK_RECT)
        self.dialog.angle_spin.setValue(45)
        self.dialog._on_reset_params()
        self.assertAlmostEqual(self.dialog.shape.angle, 0.0, places=3)

    def test_reset_keeps_shape_kind(self):
        """重置只恢复参数，不换形状种类。"""
        for kind in (mask_shape.MASK_RECT, mask_shape.MASK_CIRCLE, mask_shape.MASK_SECTOR, mask_shape.MASK_POLYGON):
            self.dialog._on_shape_chosen(kind)
            self.dialog._on_reset_params()
            self.assertEqual(self.dialog.shape.kind, kind, "重置不该改变形状种类")

    def test_reset_does_not_touch_color_filter(self):
        """重置形状参数**不清除背景色筛选**。

        这个按钮长在形状区块里，用户按它想的是「把形状调回默认」；顺手把颜色
        也清掉会让人措手不及——颜色是另一套配置，有自己的「不排除颜色」按钮。
        """
        self.dialog._on_shape_chosen(mask_shape.MASK_CIRCLE)
        self.dialog._on_reset_params()
        self.assertIsNotNone(self.dialog.color, "重置不应清除背景色")
        self.assertEqual(self.dialog.tolerance, 6, "重置不应改容差")

    def test_reset_noop_for_none(self):
        """`none` 没有几何，重置是空操作（不该抛异常）。"""
        self.dialog._on_shape_chosen(mask_shape.MASK_NONE)
        self.dialog._on_reset_params()
        self.assertEqual(self.dialog.shape.kind, mask_shape.MASK_NONE)


@unittest.skipIf(QApplication is None, f"缺少界面依赖：{QT_ERROR}")
class TestLayoutOrder(unittest.TestCase):
    """布局顺序：图像在上、背景色居中、掩码形状在下。"""

    def setUp(self):
        """造对话框（切到圆，保证半径与位置参数可见）。"""
        self.app = _app()
        template = np.zeros((47, 430, 3), dtype=np.uint8)
        template[:] = (48, 48, 48)
        self.dialog = TemplateImageDialog(template)
        # 切到圆：半径与中心参数都可见，才能用它们定位参数行。
        # 留在默认的 `none` 会让所有参数组隐藏，`mapTo` 全部返回 0。
        self.dialog._on_shape_chosen(mask_shape.MASK_CIRCLE)
        self.dialog.resize(900, 700)
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        """释放对话框。"""
        self.dialog.close()
        self.dialog.deleteLater()

    def test_canvas_is_above_color_row(self):
        """图像区在背景色操作行之上。"""
        canvas_y = self.dialog.canvas.mapTo(self.dialog, self.dialog.canvas.rect().topLeft()).y()
        color_y = self.dialog.color_edit.mapTo(self.dialog, self.dialog.color_edit.rect().topLeft()).y()
        self.assertLess(canvas_y, color_y, "图像区应当在背景色行上方")

    def test_color_row_is_above_parameter_row(self):
        """背景色操作行在参数行之上。"""
        color_y = self.dialog.color_edit.mapTo(self.dialog, self.dialog.color_edit.rect().topLeft()).y()
        param_y = self.dialog.radius_spin.mapTo(self.dialog, self.dialog.radius_spin.rect().topLeft()).y()
        self.assertLess(color_y, param_y, "背景色行应当在参数行上方")

    def test_parameter_row_is_above_shape_row(self):
        """参数行在掩码形状按钮之上。

        形状按钮是这块区域的标题与「切换对象」的入口，参数是它的属性。
        标题放最下会让人先看到属性、再看到「这些属性属于谁」。
        """
        param_y = self.dialog.radius_spin.mapTo(self.dialog, self.dialog.radius_spin.rect().topLeft()).y()
        shape_btn = self.dialog.shape_buttons[mask_shape.MASK_RECT]
        shape_y = shape_btn.mapTo(self.dialog, shape_btn.rect().topLeft()).y()
        self.assertLess(param_y, shape_y, "参数行应当在掩码形状行上方")

    def test_shape_row_is_bottommost(self):
        """掩码形状行在所有控件区块之下（只在确认按钮之上）。"""
        shape_btn = self.dialog.shape_buttons[mask_shape.MASK_RECT]
        shape_y = shape_btn.mapTo(self.dialog, shape_btn.rect().topLeft()).y()
        for name, widget in (
            ("画布", self.dialog.canvas),
            ("背景色", self.dialog.color_edit),
            ("参数", self.dialog.radius_spin),
        ):
            self.assertLess(
                widget.mapTo(self.dialog, widget.rect().topLeft()).y(),
                shape_y,
                f"{name}应当在掩码形状行之上",
            )

    def test_canvas_takes_most_height(self):
        """图像区吃掉大部分高度（铺满窗口）。

        拉伸因子只给画布：其余行都是固定内容的按钮行，窗口变大时只有画布该长高。
        给其它行也留拉伸，画布会在窗口变大时反而不长，用户会觉得「窗口大了图没大」。
        """
        canvas_h = self.dialog.canvas.height()
        total_h = self.dialog.height()
        self.assertGreater(canvas_h / total_h, 0.5, f"画布只占 {canvas_h / total_h:.0%}，没有铺满")

    def test_canvas_grows_with_window(self):
        """窗口变高时画布跟着变高。"""
        before = self.dialog.canvas.height()
        self.dialog.resize(900, 900)
        self.app.processEvents()
        after = self.dialog.canvas.height()
        self.assertGreater(after, before, "窗口变高后画布没有跟着长高")


if __name__ == "__main__":
    unittest.main()
