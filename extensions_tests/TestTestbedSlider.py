# -*- coding: utf-8 -*-
"""灵敏度滑块：拖动过程中跟手，抬起不重复累加。

user 反馈：设置菜单里的灵敏度滑块在拖动时纹丝不动，松手后才跳一次，
看起来像界面卡住。根因是数值只在抬起时按整段位移算一次。

三个被测点：

1. **拖动中实时更新**：`_on_motion` 在左键按着且按点是滑块时，按
   「按下时的数值 + 相对位移」重算，并只在数值变化时重绘。
2. **不重复累加**：换算必须基于固定锚点（按下时的数值），拿当前值逐次叠加
   位移会让数值随事件次数膨胀。抬起时用同一锚点重算，与画面一致。
3. **夹紧与省重绘**：数值夹在 `[0, 100]`；数值没变时不触发 `draw()`。
"""

import unittest
from unittest import mock

from extensions_testbed import screens


class TestSliderValue(unittest.TestCase):
    """数值换算与重绘节流（不建真窗口，`draw` 用桩）。"""

    def setUp(self):
        """造一个画布与绘制都被替换掉的画面视图。"""
        patcher = mock.patch.object(screens.ScreenView, "draw")
        self.draw = patcher.start()
        self.addCleanup(patcher.stop)
        self.view = screens.ScreenView(mock.Mock(), 1280, 720, "zh", screen_id="settings_menu")
        self.view.slider_value = 50

    def test_drag_uses_anchor_value(self):
        """同一锚点与位移重复调用得到同一数值，不随事件次数累加。"""
        first = self.view.set_slider_from_drag(50, 60)
        second = self.view.set_slider_from_drag(50, 60)
        self.assertGreater(first, 50, "向右拖应增大数值")
        self.assertEqual(first, second, "实时更新必须基于固定锚点，不能逐次累加")

    def test_drag_clamps(self):
        """超大位移被夹到 0 / 100，手柄不会跑出轨道。"""
        self.assertEqual(self.view.set_slider_from_drag(50, 99999), 100)
        self.assertEqual(self.view.set_slider_from_drag(50, -99999), 0)

    def test_unchanged_value_does_not_redraw(self):
        """数值没变不重绘：拖动时鼠标每秒移动数百次，每次都画会卡顿。"""
        self.draw.reset_mock()
        self.view.set_slider_value(50)
        self.draw.assert_not_called()
        self.view.set_slider_value(51)
        self.assertEqual(self.draw.call_count, 1)

    def test_drag_slider_keeps_relative_semantics(self):
        """`drag_slider(dx)` 仍按「当前值 + 位移」生效（兼容既有调用）。"""
        self.view.slider_value = 50
        value = self.view.drag_slider(60)
        self.assertGreater(value, 50)
        again = self.view.drag_slider(60)
        self.assertGreater(again, value, "相对入口每次调用都应在当前值上继续累加")


class TestMotionUpdatesSlider(unittest.TestCase):
    """`_on_motion` 只在左键按着且锚点存在时更新滑块。"""

    def _app(self):
        """造一个只带计数、画面与滑块锚点的应用实例（不建窗口）。

        Returns:
            `TestbedApp` 实例（跳过构造）。
        """
        from extensions_testbed import app as app_module

        instance = object.__new__(app_module.TestbedApp)
        instance.counters = mock.Mock()
        instance.screen = mock.Mock(slider_value=50)
        instance._slider_drag = {"x": 100, "value": 50}
        instance._scrollbar_drag = None
        instance._grid_drag = None
        return instance

    def test_motion_while_pressed_updates_slider(self):
        """左键按着移动：按锚点与相对位移实时更新滑块。"""
        instance = self._app()
        event = mock.Mock(x=160, y=0, state=0x0100)
        instance._on_motion(event)
        instance.screen.set_slider_from_drag.assert_called_once_with(50, 60)

    def test_motion_without_button_ignores_slider(self):
        """没按左键（只是移动）不更新滑块。"""
        instance = self._app()
        event = mock.Mock(x=160, y=0, state=0)
        instance._on_motion(event)
        instance.screen.set_slider_from_drag.assert_not_called()

    def test_motion_without_anchor_is_noop(self):
        """锚点不存在（按点不在滑块上）时不更新。"""
        instance = self._app()
        instance._slider_drag = None
        event = mock.Mock(x=160, y=0, state=0x0100)
        instance._on_motion(event)
        instance.screen.set_slider_from_drag.assert_not_called()

    def test_gesture_uses_anchor_when_available(self):
        """抬起时用锚点重算最终值，而不是把整段位移再加一次。"""
        from extensions_testbed import gestures

        instance = self._app()
        instance.log = mock.Mock()
        instance.screen.hit_test.return_value = mock.Mock(kind="slider")
        gesture = gestures.GestureResult(
            kind="drag",
            dx=60,
            dy=0,
            distance=60.0,
            duration_ms=200.0,
            start=(100, 10),
            end=(160, 10),
        )
        instance._handle_gesture(gesture, slider_anchor={"x": 100, "value": 50})
        instance.screen.set_slider_from_drag.assert_called_once_with(50, 60)
        instance.screen.drag_slider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
