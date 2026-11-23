# -*- coding: utf-8 -*-
"""掩码形状画布：拖拽分派、绘制点与越界渲染。

## 为什么单独测画布层

`MaskShape.grab_at` 是纯几何判定，测试它只能证明「判定函数对」。真正的风险在
画布这一层：像素坐标与相对比例的换算、容差按两轴分别换算、光标与绘制点是否
与判定一致。这些错了不会报错，只会表现为「明明在边上却拖不动」或
「圆外面浮着一个孤立白点」。
"""

import unittest

import numpy as np

from extensions.capabilities.scene_manager import mask_shape

try:
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    from extensions.capabilities.scene_manager.pages.mask_canvas import MaskShapeCanvas

    QT_ERROR = None
except ModuleNotFoundError as exc:  # 缺界面依赖
    QApplication = None
    MaskShapeCanvas = None
    QT_ERROR = str(exc)


def _app():
    """返回（必要时创建）QApplication 实例。"""
    return QApplication.instance() or QApplication([])


@unittest.skipIf(QApplication is None, f"缺少界面依赖：{QT_ERROR}")
class TestCanvasDispatch(unittest.TestCase):
    """画布把控件坐标翻译成拖拽模式。"""

    def setUp(self):
        """造一个 400x300 的模板并适配视图。"""
        self.app = _app()
        template = np.zeros((300, 400, 3), dtype=np.uint8)
        template[:] = (48, 48, 48)
        self.canvas = MaskShapeCanvas()
        self.canvas.resize(800, 600)
        self.canvas.set_template(template)
        self.canvas.zoom_fit()

    def tearDown(self):
        """释放画布。"""
        self.canvas.deleteLater()

    def _grab(self, rx, ry):
        """按相对比例问画布判定。"""
        return self.canvas._grab_at(self.canvas._ratio_to_view(rx, ry))

    def test_rect_regions(self):
        """矩形的内部/中心/边/角/外部各自对应正确的模式。"""
        self.canvas.set_shape(mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.25, 0.2), (0.75, 0.8)]))
        self.assertEqual(self._grab(0.5, 0.5)[0], mask_shape.GRAB_ROTATE)
        self.assertEqual(self._grab(0.4, 0.35)[0], mask_shape.GRAB_MOVE)
        self.assertEqual(self._grab(0.75, 0.5), (mask_shape.GRAB_RESIZE, mask_shape.RECT_RIGHT))
        self.assertEqual(self._grab(0.25, 0.2), (mask_shape.GRAB_RESIZE, mask_shape.RECT_TL))
        self.assertEqual(self._grab(0.05, 0.05)[0], mask_shape.GRAB_NONE)

    def test_circle_has_no_draw_handles(self):
        """圆不画控制点。

        圆周上画一个半径点，在宽扁模板上它会离圆很远（半径按宽度算），看起来
        像浮在形状外面的孤立白点。
        """
        self.canvas.set_shape(mask_shape.MaskShape(mask_shape.MASK_CIRCLE, [(0.5, 0.5)], radius=0.3))
        self.assertEqual(self.canvas.shape().draw_handles(), [])

    def test_circle_arc_resizes(self):
        """圆的圆周是缩放，圆心是移动。"""
        self.canvas.set_shape(mask_shape.MaskShape(mask_shape.MASK_CIRCLE, [(0.5, 0.5)], radius=0.3))
        self.assertEqual(self._grab(0.8, 0.5), (mask_shape.GRAB_RESIZE, "radius"))
        self.assertEqual(self._grab(0.5, 0.5)[0], mask_shape.GRAB_MOVE)

    def test_sector_edge_before_arc(self):
        """扇形的两条半径边先于圆弧被判定。

        半径边与圆弧在端点相交。先判圆弧会把「拖起始边」误判成「改半径」，
        而用户看到的是边在动、弧却没变。

        Note:
            点必须按**等比空间**构造（比例 y 要除以 `aspect`）。模板是
            400×300，直接写 `(0.5, 0.9)` 会落在弧外——测出来的「没命中」
            与被测假设无关。
        """
        self.canvas.set_shape(mask_shape.MaskShape(mask_shape.MASK_SECTOR, [(0.5, 0.5)], angle=0, sweep=90, radius=0.4))
        aspect = self.canvas.aspect()
        self.assertEqual(self._grab(0.9, 0.5), (mask_shape.GRAB_SWEEP, "start"))
        arc_y = 0.5 + 0.4 / aspect
        self.assertEqual(self._grab(0.5, arc_y), (mask_shape.GRAB_SWEEP, "end"))
        diag = 0.4 * 0.7071
        self.assertEqual(
            self._grab(0.5 + diag, 0.5 + diag / aspect),
            (mask_shape.GRAB_RESIZE, "radius"),
        )

    def test_polygon_draws_vertices(self):
        """多边形画全部顶点，且顶点命中是缩放。"""
        self.canvas.set_shape(mask_shape.MaskShape.diamond())
        self.assertEqual(len(self.canvas.shape().draw_handles()), 4)
        self.assertEqual(self._grab(0.5, 0.0), (mask_shape.GRAB_RESIZE, 0))

    def test_move_cursor_is_cross(self):
        """移动光标是十字，不是 `SizeAllCursor`。

        `SizeAll` 是 Windows 的「移动窗口」四向箭头，在图形编辑器里意图不明确；
        十字是绘图类工具的通用「可拖动」提示，与「在形状内按住拖动整体移动」
        这个语义更贴。
        """
        self.canvas.set_shape(mask_shape.MaskShape(mask_shape.MASK_CIRCLE, [(0.5, 0.5)], radius=0.3))
        mode, detail = self._grab(0.5, 0.5)
        self.assertEqual(mode, mask_shape.GRAB_MOVE)
        self.assertEqual(
            MaskShapeCanvas._cursor_for(mode, detail).name,
            "CrossCursor",
        )

    def test_circle_center_reports_move(self):
        """圆的圆心是移动，不是旋转。

        **原缺陷**：在圆心附近拖动会被判成「旋转」，而圆旋转后画面毫无变化，
        用户看到的就是「圆拖不动」。
        """
        self.canvas.set_shape(mask_shape.MaskShape(mask_shape.MASK_CIRCLE, [(0.5, 0.5)], radius=0.3))
        self.assertEqual(self._grab(0.5, 0.5), (mask_shape.GRAB_MOVE, None))

    def test_vertex_menu_always_carries_popup_point(self):
        """顶点菜单的两个分支都必须带弹出位置。

        **原缺陷**：「加点」分支传了 `None`，`QMenu.exec(None)` 直接抛
        `TypeError`——用户右键一下就一条异常，而堆栈全在 Qt 内部，
        看不出是自己漏传了参数。
        """
        emitted = []
        self.canvas.vertex_menu_requested.connect(lambda action, index, coords, point: emitted.append((action, point)))
        self.canvas.set_shape(mask_shape.MaskShape.inset_polygon(0.4))
        shape = self.canvas.shape()

        # 顶点上右键 → remove
        vx, vy = shape.points[0]
        self.canvas._request_vertex_menu(self.canvas._ratio_to_view(vx, vy))
        # 边中点右键 → add
        mx = (shape.points[0][0] + shape.points[1][0]) / 2
        my = (shape.points[0][1] + shape.points[1][1]) / 2
        self.canvas._request_vertex_menu(self.canvas._ratio_to_view(mx, my))

        self.assertEqual(len(emitted), 2, f"应当发两个信号，实际 {emitted}")
        self.assertEqual(emitted[0][0], "remove")
        self.assertEqual(emitted[1][0], "add")
        for action, point in emitted:
            self.assertIsNotNone(point, f"{action} 分支漏了弹出位置")

    def test_none_has_no_draw_handles(self):
        """`none` 不画点也不响应拖拽。"""
        self.canvas.set_shape(mask_shape.MaskShape.none())
        self.assertEqual(self.canvas.shape().draw_handles(), [])
        self.assertEqual(self._grab(0.5, 0.5)[0], mask_shape.GRAB_NONE)


@unittest.skipIf(QApplication is None, f"缺少界面依赖：{QT_ERROR}")
class TestCanvasOutOfBox(unittest.TestCase):
    """越界编辑：拖出包围盒不报错、不崩渲染。"""

    def setUp(self):
        """造模板与画布。"""
        self.app = _app()
        template = np.zeros((47, 430, 3), dtype=np.uint8)
        template[:] = (48, 48, 48)
        self.canvas = MaskShapeCanvas()
        self.canvas.resize(600, 300)
        self.canvas.set_template(template)
        self.canvas.zoom_fit()

    def tearDown(self):
        """释放画布。"""
        self.canvas.deleteLater()

    def test_drag_right_edge_outside(self):
        """拖右边到画面外：坐标保留，不被夹回。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.25, 0.2), (0.75, 0.8)])
        self.canvas.set_shape(shape)
        self.canvas._grab_mode = mask_shape.GRAB_RESIZE
        self.canvas._grab_detail = mask_shape.RECT_RIGHT
        self.canvas._grab_shape = shape
        self.canvas._apply_grab(self.canvas._ratio_to_view(1.6, 0.5))
        self.assertGreater(self.canvas.shape().points[1][0], 1.0)

    def test_render_does_not_crash_for_oversized_shape(self):
        """大幅越界的形状仍能渲染（只画落在视口内的部分）。"""
        self.canvas.set_shape(mask_shape.MaskShape(mask_shape.MASK_RECT, [(-1.5, -1.0), (2.5, 2.0)], angle=37))
        pixmap = QPixmap(self.canvas.size())
        self.canvas.render(pixmap)
        self.assertFalse(pixmap.isNull())

    def test_mask_keeps_only_inside_pixels(self):
        """圆心在盒外时，掩码只保留落在模板内的部分。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_CIRCLE, [(0.0, 0.5)], radius=0.5)
        mask = shape.mask(100, 100)
        coverage = float((mask > 0).mean())
        self.assertGreater(coverage, 0.0)
        self.assertLess(coverage, 0.5, "圆心在左边界上，只应有右半圆")


if __name__ == "__main__":
    unittest.main()
