# -*- coding: utf-8 -*-
"""探索动作的分派与执行。

`TaskBridge.activate_area` 原先固定单击。现在按 `Area.explore_action` 分派到五种
动作。分派错了不会报错——只是「点了没反应」或「触发了别的功能」，探索据此写出的边
就是错的，而错边会让后续每次导航都点错地方。

因此这里用记录调用的假任务，逐种动作断言最终发到设备层的调用形态。
"""

import time
import unittest

from extensions.capabilities.scene_manager import model, runtime

try:
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值依赖
    np = None
    NUMPY_ERROR = str(exc)


class _RecordingTask:
    """记录 click / swipe 的调用参数。"""

    def __init__(self):
        """初始化调用记录。"""
        self.calls = []

    def click(self, x, y, **kwargs):
        """记录一次点击。"""
        self.calls.append(("click", x, y, kwargs))

    def swipe(self, from_x, from_y, to_x, to_y, **kwargs):
        """记录一次滑动。"""
        self.calls.append(("swipe", from_x, from_y, to_x, to_y))


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestActivateArea(unittest.TestCase):
    """按动作分派。"""

    def setUp(self):
        """造一个只有 task / 取帧 / 等待的 bridge。

        跳过 `__init__`：它要求真实任务具备 click 与 ocr，而这里只验证动作分派。
        """
        self.task = _RecordingTask()
        self.bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
        self.bridge.task = self.task
        # 1280x720 的画面，区域取正中，中心点 (640, 360)。
        self.bridge.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        self.bridge.wait = lambda seconds: self.task.calls.append(("wait", seconds))

    def _area(self, action, **params):
        """造一个矩形区域。"""
        return model.Area(
            name="a",
            rect=(0.4, 0.4, 0.6, 0.6),
            explore_action=action,
            explore_params=params,
        )

    def test_click(self):
        """单击：一次 click，无额外参数。"""
        self.bridge.activate_area(None, self._area(model.ACTION_CLICK))
        self.assertEqual(self.task.calls, [("click", 640, 360, {})])

    def test_double_click_two_clicks_with_interval(self):
        """双击：两次 click，中间等待 interval。"""
        self.bridge.activate_area(None, self._area(model.ACTION_DOUBLE_CLICK, interval=0.1))
        self.assertEqual(
            self.task.calls,
            [("click", 640, 360, {}), ("wait", 0.1), ("click", 640, 360, {})],
        )

    def test_long_press_uses_down_time(self):
        """长按：用 click 的 down_time 表达按住时长。

        比手动 mouse_down/mouse_up 可靠——后者在异常路径上可能留下一个没松开的按键。
        """
        self.bridge.activate_area(None, self._area(model.ACTION_LONG_PRESS, duration=2.5))
        self.assertEqual(self.task.calls, [("click", 640, 360, {"down_time": 2.5})])

    def test_right_click_uses_right_key(self):
        """右键：click 传 key='right'。"""
        self.bridge.activate_area(None, self._area(model.ACTION_RIGHT_CLICK))
        self.assertEqual(self.task.calls, [("click", 640, 360, {"key": "right"})])

    def test_swipe_up(self):
        """上滑：终点在起点正上方，距离按短边比例。"""
        self.bridge.activate_area(None, self._area(model.ACTION_SWIPE, direction="up", distance=0.25))
        # 短边 = 720，0.25 → 180
        self.assertEqual(self.task.calls, [("swipe", 640, 360, 640, 180)])

    def test_swipe_down(self):
        """下滑：终点在下方。"""
        self.bridge.activate_area(None, self._area(model.ACTION_SWIPE, direction="down", distance=0.25))
        self.assertEqual(self.task.calls, [("swipe", 640, 360, 640, 540)])

    def test_swipe_left_and_right(self):
        """左右滑：终点在水平方向。"""
        self.bridge.activate_area(None, self._area(model.ACTION_SWIPE, direction="left", distance=0.25))
        self.assertEqual(self.task.calls[-1], ("swipe", 640, 360, 460, 360))

        self.task.calls.clear()
        self.bridge.activate_area(None, self._area(model.ACTION_SWIPE, direction="right", distance=0.25))
        self.assertEqual(self.task.calls[-1], ("swipe", 640, 360, 820, 360))

    def test_swipe_end_clamped_to_frame(self):
        """滑动终点被夹在画面内，不会滑出边界。"""
        area = model.Area(
            name="a",
            rect=(0.0, 0.0, 0.1, 0.1),
            explore_action=model.ACTION_SWIPE,
            explore_params={"direction": "up", "distance": 0.9},
        )
        self.bridge.activate_area(None, area)
        _kind, _fx, _fy, _tx, ty = self.task.calls[-1]
        self.assertGreaterEqual(ty, 0, "终点不能为负")


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestClickPointOf(unittest.TestCase):
    """单击入口本身保持不变。"""

    def test_click_point_of_still_works(self):
        """直接调 `click_point_of` 仍是单击——外部调用方依赖它。"""
        task = _RecordingTask()
        bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
        bridge.task = task
        bridge.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)

        area = model.Area(name="a", rect=(0.4, 0.4, 0.6, 0.6), explore_action=model.ACTION_LONG_PRESS)
        bridge.click_point_of(area)
        self.assertEqual(task.calls, [("click", 640, 360, {})], "该方法不看 explore_action")


class TestBridgeWait(unittest.TestCase):
    """`TaskBridge.wait` 不得经过 `task.sleep`。

    用户实测：点调试页「点击并观察」后整个界面卡死。

    根因在 `TaskExecutor.sleep` 的循环结构——它只有一个正常出口，且位于「可以捕获」
    的分支里：

    ```python
    if not (self.blocked_by_pause() or ... or self.interaction is None
            or not self.interaction.should_capture()):
        to_sleep = self.pause_end_time - time.time()
        if to_sleep <= 0:
            return              # ← 唯一出口
    else:
        self._wait_for_activity(0.1)
    ```

    而 `should_capture()` 在 Pynput/PyDirect/ForegroundPostMessage 里返回
    `hwnd_window.is_foreground()`。用户点按钮时焦点在调试页，游戏窗口不在前台，
    判断恒为 False → 永远进不到 return → 每 0.1 秒空转一次，永不返回。

    因此 `wait` 必须用 `time.sleep`：这里的语义是「点击后等画面稳定」的固定时长，
    不需要「等用户切回前台」。
    """

    def test_wait_does_not_use_task_sleep(self):
        """`wait` 不调用 `task.sleep`——即使任务提供了该方法。"""
        slept = []

        class _Task:
            def sleep(self, seconds):
                slept.append(seconds)

        bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
        bridge.task = _Task()

        started = time.monotonic()
        bridge.wait(0.05)
        elapsed = time.monotonic() - started

        self.assertEqual(slept, [], "不得调用 task.sleep：它在窗口非前台时永不返回")
        self.assertGreaterEqual(elapsed, 0.04, "应当真的等待了指定时长")

    def test_wait_returns_zero_or_negative_immediately(self):
        """非正时长立即返回，不做任何等待。"""
        bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
        bridge.task = object()

        started = time.monotonic()
        bridge.wait(0)
        bridge.wait(-1)
        self.assertLess(time.monotonic() - started, 0.05)

    def test_wait_works_without_task_sleep(self):
        """任务没有 sleep 方法时同样可用（不因 getattr 失败而中断）。"""
        bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
        bridge.task = object()

        started = time.monotonic()
        bridge.wait(0.05)
        self.assertGreaterEqual(time.monotonic() - started, 0.04)


if __name__ == "__main__":
    unittest.main()
