# -*- coding: utf-8 -*-
"""关卡图标网格：固定顺序 + 20 个关卡 + 平滑竖向滚动。

三个被测点：

1. **顺序固定**：按 1~20 从左到右、从上到下排列，不再随机洗牌。
2. **数量与行列**：20 个格子、3 列 7 行；首行三个 y 相同、x 递增，第 4 个换行。
3. **平滑滚动**：按像素滚动（不是整行翻页），裁剪交给视口子画布；滚轮/滚动条/
   列表内拖拽三条入口共用同一套像素偏移。
"""

import unittest
from unittest import mock

from extensions_testbed import layout, screens, yolo_scene

# 视口尺寸与位置（1280×720 下的实际像素）：与 `GRID_RECT` 一致
_VIEW_W, _VIEW_H = 400, 256
_ORIGIN = (100, 184)


def _scale(rect):
    """把基准矩形按 1280×720 原样换算（测试固定尺寸）。"""
    return layout.scale_rect(rect, 1280, 720)


def _grid(seed=7, viewport=_VIEW_W, viewport_h=_VIEW_H):
    """造一个画布被替换成 Mock 的网格（视口尺寸默认与关卡列表一致）。"""
    return yolo_scene.IconGrid(mock.Mock(), viewport, viewport_h, _scale, seed=seed, origin=_ORIGIN)


class TestGridLayout(unittest.TestCase):
    """格子位置与图标顺序（不建窗口）。"""

    def test_icon_count_and_labels(self):
        """20 个关卡，编号 1~20。"""
        self.assertEqual(yolo_scene.ICON_COUNT, 20)
        self.assertEqual(len(yolo_scene.CELL_RECTS), 20)
        self.assertEqual(_grid().labels, tuple(str(i) for i in range(1, 21)))

    def test_order_is_fixed(self):
        """顺序固定：不同随机种子的两次「进入」排列一致。"""
        self.assertEqual(_grid(seed=1).perm, _grid(seed=2).perm)
        self.assertEqual(_grid(seed=1).perm, tuple(range(20)))

    def test_row_major_positions(self):
        """按行优先：同一行 y 相同、x 递增；第 4 个换到下一行第一列。"""
        rects = yolo_scene.CELL_RECTS
        self.assertEqual(rects[0][1], rects[1][1], "同一行 y 应相同")
        self.assertEqual(rects[1][1], rects[2][1])
        self.assertLess(rects[0][0], rects[1][0])
        self.assertLess(rects[1][0], rects[2][0])
        self.assertEqual(rects[3][0], rects[0][0], "换行后回到第一列")
        self.assertGreater(rects[3][1], rects[0][1], "换行后 y 增大")


class TestSmoothScroll(unittest.TestCase):
    """像素滚动：连续、夹紧、只画与视口相交的行。"""

    def setUp(self):
        """造一个画布被替换成 Mock 的网格。"""
        self.canvas = mock.Mock()
        self.grid = _grid()

    def test_visible_rows_cover_viewport(self):
        """初始只画与视口相交的行（含底部露出的半行），其余不画。"""
        self.grid.draw()
        self.assertLess(len(self.grid._items), yolo_scene.ICON_COUNT)
        self.assertIn(0, self.grid._items)
        self.assertNotIn(yolo_scene.ICON_COUNT - 1, self.grid._items, "最后一行在视口外")

    def test_scroll_is_pixel_based(self):
        """滚动按像素生效：滚半行不换整行，行号取整只用于展示。"""
        self.grid.draw()
        step = self.grid.row_step()
        self.assertTrue(self.grid.scroll_by(step / 2))
        self.assertAlmostEqual(self.grid.scroll_px, step / 2, places=3)
        self.assertEqual(self.grid.scroll_row, 0, "半行时四舍五入仍是第 0 行")

    def test_scroll_clamps_at_ends(self):
        """滚到顶/底后夹紧，且不再重绘。"""
        self.grid.draw()
        self.assertTrue(self.grid.scroll_to_px(99999))
        self.assertAlmostEqual(self.grid.scroll_px, self.grid.max_scroll_px(), places=3)
        self.assertFalse(self.grid.scroll_to_px(99999), "已在底部不应再滚动")
        self.assertTrue(self.grid.scroll_to_px(-10))
        self.assertEqual(self.grid.scroll_px, 0.0)

    def test_scroll_to_row_maps_to_pixels(self):
        """按行跳转换算成像素偏移。"""
        self.grid.scroll_to_row(1)
        self.assertAlmostEqual(self.grid.scroll_px, self.grid.row_step(), delta=0.5)


class TestScrollbarDrag(unittest.TestCase):
    """滚动条：几何、比例跳转、鼠标拖拽（视口内局部坐标）。"""

    def _view(self, grid):
        """造一个挂上给定网格的关卡列表画面。"""
        view = screens.ScreenView(mock.Mock(), 1280, 720, "zh", screen_id="chapter_select")
        view._icon_grid = grid
        return view

    def test_scrollbar_geometry(self):
        """轨道贴视口右缘内部，滑块高度按视口/内容比例。"""
        grid = _grid()
        x1, y1, x2, y2 = grid.scrollbar_rect()
        self.assertEqual((x1, y1, x2, y2), (_VIEW_W - 10, 0, _VIEW_W - 4, _VIEW_H))
        thumb = grid.thumb_rect()
        self.assertEqual(thumb[1], 0, "顶部时滑块贴轨道上边")
        self.assertEqual(thumb[3] - thumb[1], round(_VIEW_H * _VIEW_H / grid._content_height()))

    def test_scroll_to_ratio_maps_ends(self):
        """比例 0/1 对应顶部/底部。"""
        grid = _grid()
        grid.scroll_to_ratio(1.0)
        self.assertAlmostEqual(grid.scroll_px, grid.max_scroll_px(), places=3)
        grid.scroll_to_ratio(0.0)
        self.assertEqual(grid.scroll_px, 0.0)

    def test_drag_thumb_moves_pixels(self):
        """按住滑块向下拖：滚动偏移连续增加。"""
        grid = _grid()
        grid.draw()
        view = self._view(grid)
        # 视口右下：局部 (393, 16) → 客户区 (493, 200)
        anchor = view.begin_scrollbar_drag(493, 200)
        self.assertEqual(anchor, {"offset": 16}, "按在滑块上保持原偏移")
        self.assertTrue(view.drag_scrollbar_to(220, anchor["offset"]))
        self.assertGreater(grid.scroll_px, 0)

    def test_track_click_jumps(self):
        """点在轨道（滑块之外）直接跳到该位置。"""
        grid = _grid()
        grid.draw()
        view = self._view(grid)
        self.assertIsNotNone(view.begin_scrollbar_drag(493, 430))
        self.assertGreater(grid.scroll_px, 0)

    def test_popup_blocks_scrollbar(self):
        """弹窗遮挡时按滚动条无效。"""
        grid = _grid()
        view = self._view(grid)
        view.popup = "popup_notice"
        self.assertIsNone(view.begin_scrollbar_drag(493, 200))
        self.assertFalse(view.drag_scrollbar_to(230, 0))

    def test_grid_drag_is_smooth(self):
        """在列表区域内按住拖动：按像素连续滚动（向上拖看后面的行）。"""
        grid = _grid()
        grid.draw()
        view = self._view(grid)
        anchor = view.begin_grid_drag(200, 300)
        self.assertIsNotNone(anchor)
        self.assertTrue(view.drag_grid_to(220, anchor["y"], anchor["px"]))
        self.assertAlmostEqual(grid.scroll_px, 80, delta=1, msg="向上拖 80px 应滚 80px")

    def test_grid_drag_outside_or_disabled(self):
        """不在列表区域、内容装得下或有弹窗时不进入拖拽。"""
        grid = _grid()
        view = self._view(grid)
        self.assertIsNone(view.begin_grid_drag(50, 300))
        self.assertIsNone(view.begin_grid_drag(200, 100))
        view.popup = "popup_notice"
        self.assertIsNone(view.begin_grid_drag(200, 300))

    def test_grid_drag_disabled_when_not_scrollable(self):
        """内容装得下时不进入拖拽（没有可滚动范围）。"""
        grid = mock.Mock()
        grid.max_scroll_px.return_value = 0
        view = self._view(grid)
        self.assertIsNone(view.begin_grid_drag(200, 300))


class TestScrollbarWiring(unittest.TestCase):
    """应用层：按下建立锚点、移动拖动、抬起写日志。"""

    def _app(self):
        """造一个只带计数、画面与日志的应用实例（不建窗口）。"""
        from extensions_testbed import app as app_module

        instance = object.__new__(app_module.TestbedApp)
        instance.claimer = mock.Mock()
        instance.counters = mock.Mock()
        instance.log = mock.Mock()
        instance.screen = mock.Mock()
        instance.screen.hit_test.return_value = None
        instance.screen.begin_scrollbar_drag.return_value = {"offset": 10}
        instance.screen.icon_scroll_row = 1
        instance._slider_drag = None
        instance._scrollbar_drag = None
        instance._grid_drag = None
        return instance

    def test_press_starts_scrollbar_drag(self):
        """左键按在滚动条上建立拖动锚点。"""
        from extensions_testbed import app as app_module

        instance = self._app()
        event = mock.Mock(x=493, y=200, num=1)
        with mock.patch.object(app_module.TestbedApp, "_draw_input"):
            instance._on_button_down(event)
        instance.screen.begin_scrollbar_drag.assert_called_once_with(493, 200)
        self.assertEqual(instance._scrollbar_drag, {"offset": 10})

    def test_press_in_grid_starts_grid_drag(self):
        """在列表区域按下建立像素锚点（与滚动条互不干扰）。"""
        from extensions_testbed import app as app_module

        instance = self._app()
        instance.screen.begin_scrollbar_drag.return_value = None
        instance.screen.begin_grid_drag.return_value = {"y": 300, "px": 0.0}
        event = mock.Mock(x=200, y=300, num=1)
        with mock.patch.object(app_module.TestbedApp, "_draw_input"):
            instance._on_button_down(event)
        self.assertEqual(instance._grid_drag, {"y": 300, "px": 0.0})

    def test_motion_drags_grid_by_pixels(self):
        """按着左键在列表内移动：按像素锚点滚动。"""
        instance = self._app()
        instance._grid_drag = {"y": 300, "px": 0.0}
        event = mock.Mock(x=200, y=250, state=0x0100)
        instance._on_motion(event)
        instance.screen.drag_grid_to.assert_called_once_with(250, 300, 0.0)

    def test_release_logs_drags(self):
        """抬起写 scrollbar_drag / grid_drag 日志并清空锚点。"""
        from extensions_testbed import app as app_module

        instance = self._app()
        instance._scrollbar_drag = {"offset": 10}
        instance._grid_drag = {"y": 300, "px": 0.0}
        instance._press_origin = None
        event = mock.Mock(x=493, y=230)
        with mock.patch.object(app_module.TestbedApp, "_draw_input"):
            instance._on_button_up(event)
        self.assertIsNone(instance._scrollbar_drag)
        self.assertIsNone(instance._grid_drag)
        events = [call.kwargs.get("event") for call in instance.log.write.call_args_list]
        self.assertIn("scrollbar_drag", events)
        self.assertIn("grid_drag", events)


if __name__ == "__main__":
    unittest.main()
