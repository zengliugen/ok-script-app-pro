# -*- coding: utf-8 -*-
"""点击是否真的产生效果：前后帧比对与界面提示。

用户反馈「点了后按钮还是未被触发」。这类失败**不报错**：后端把输入发出去了，
只是目标程序没响应，界面照样显示「点击成功」。最典型的成因是交互方式与目标
程序不匹配——投递类方式依赖目标处理窗口消息，而工具包自绘的程序（如 Tkinter）
通常不响应投递来的鼠标消息。

因此点击前后各取一帧比对，把「没变化」显式化，并在界面上给出可操作的提示。
"""

import unittest

from extensions.capabilities.scene_manager import recognize, runtime
from extensions.capabilities.scene_manager.pages import debug_page

try:
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值依赖
    np = None
    NUMPY_ERROR = str(exc)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestFramesDiffer(unittest.TestCase):
    """`frames_differ` 的判定边界。"""

    def test_identical_frames_report_no_change(self):
        """完全相同的两帧判为没变化。"""
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertFalse(runtime.frames_differ(frame, frame.copy()))

    def test_small_change_above_threshold_is_detected(self):
        """小面积但明显的变化要能识别出来。

        真实生效的点击往往只改几个小区域（按钮高亮、画面切换），阈值定得过大
        会把正常生效误判成「没反应」，反而给用户错误提示。
        """
        before = np.zeros((720, 1280, 3), dtype=np.uint8)
        after = before.copy()
        # 约 240x240 的区域翻转成白色：占比约 6%，远高于阈值。
        after[100:340, 100:340] = 255
        self.assertTrue(runtime.frames_differ(before, after))

    def test_tiny_change_below_threshold_is_ignored(self):
        """极小的像素抖动不算变化（抗锯齿、压缩噪声）。"""
        before = np.zeros((720, 1280, 3), dtype=np.uint8)
        after = before.copy()
        after[0:2, 0:2] = 255  # 4 个像素 / 921600，约 0.0004%
        self.assertFalse(runtime.frames_differ(before, after))

    def test_subtle_gradient_below_pixel_threshold_is_ignored(self):
        """整帧小幅亮度漂移不算变化。

        `FRAME_CHANGE_PIXEL` 用来吸收这类漂移：每个通道只差几个灰阶时，
        人眼与「点击生效」无关，但按像素数会算成「整帧都变了」。
        """
        before = np.full((720, 1280, 3), 100, dtype=np.uint8)
        after = np.full((720, 1280, 3), 103, dtype=np.uint8)
        self.assertFalse(runtime.frames_differ(before, after))

    def test_size_change_counts_as_change(self):
        """画面尺寸变化必然算变化（例如窗口被调整）。"""
        before = np.zeros((720, 1280, 3), dtype=np.uint8)
        after = np.zeros((360, 640, 3), dtype=np.uint8)
        self.assertTrue(runtime.frames_differ(before, after))

    def test_missing_frame_is_treated_as_changed(self):
        """取不到帧时按「有变化」处理。

        没有证据不能断言「点击无效」——那会把「取帧偶发失败」误报成
        「点击方式不适配」，而后者会引导用户去改配置，方向就错了。
        """
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertTrue(runtime.frames_differ(None, frame))
        self.assertTrue(runtime.frames_differ(frame, None))
        self.assertTrue(runtime.frames_differ(None, None))

    def test_broken_frame_object_is_treated_as_changed(self):
        """帧对象异常时也按「有变化」处理，不抛异常打断主流程。"""
        before = np.zeros((4, 4, 3), dtype=np.uint8)
        self.assertTrue(runtime.frames_differ(before, "不是数组"))


class TestMatchResultFrameChanged(unittest.TestCase):
    """`MatchResult.frame_changed` 的默认值与赋值。"""

    def test_default_is_none(self):
        """识别类调用不涉及点击，默认 None——界面据此不显示提示。

        用 False 当默认会让「识别当前画面」也弹出「点击后画面没有变化」。
        """
        self.assertIsNone(recognize.MatchResult().frame_changed)

    def test_assignable(self):
        """可被赋值为 booleans。"""
        result = recognize.MatchResult()
        result.frame_changed = False
        self.assertFalse(result.frame_changed)


class TestBackendIsBackground(unittest.TestCase):
    """调试页的后台后端判定（决定提示文案指向哪种原因）。"""

    class _Interaction:
        pass

    def _bridge_with(self, class_name):
        """造一个只暴露 task.executor.interaction 的 bridge 替身。

        Args:
            class_name: 交互后端的类型名。

        Returns：
            无参工厂函数。
        """
        backend = type(class_name, (object,), {})()
        task = type(
            "Task",
            (object,),
            {
                "executor": type("Executor", (object,), {"interaction": backend})(),
            },
        )()

        class _Bridge:
            task = None

        bridge = _Bridge()
        bridge.task = task
        return lambda: bridge

    def test_post_message_is_background(self):
        """`PostMessage` 判为后台投递。"""
        build = self._bridge_with("PostMessageInteraction")
        self.assertTrue(debug_page._backend_is_background(build))

    def test_genshin_is_background(self):
        """`Genshin` 判为后台投递。"""
        build = self._bridge_with("GenshinInteraction")
        self.assertTrue(debug_page._backend_is_background(build))

    def test_foreground_post_message_is_not_background(self):
        """`ForegroundPostMessage` 名字带 PostMessage，但不是后台方式。

        它重写了 `clickable()` 为前台判断，提示文案不该往「改用 Pynput」上引
        ——它本来就是需要前台的。
        """
        build = self._bridge_with("ForegroundPostMessageInteraction")
        self.assertFalse(debug_page._backend_is_background(build))

    def test_pynput_is_not_background(self):
        """`Pynput` 判为非后台。"""
        build = self._bridge_with("PynputInteraction")
        self.assertFalse(debug_page._backend_is_background(build))

    def test_missing_factory_is_not_background(self):
        """没有 bridge 工厂时按非后台处理。"""
        self.assertFalse(debug_page._backend_is_background(None))

    def test_broken_factory_is_not_background(self):
        """工厂抛异常时按非后台处理，不把提示整段丢掉。"""

        def boom():
            raise RuntimeError("装配失败")

        self.assertFalse(debug_page._backend_is_background(boom))


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestNoEffectHint(unittest.TestCase):
    """提示文案要指向正确的原因。"""

    def _page(self, class_name):
        """造一个只带 bridge 工厂的页面替身。

        Args:
            class_name: 交互后端类型名。

        Returns：
            `DebugPage` 实例（跳过构造）。
        """
        backend = type(class_name, (object,), {})()
        task = type(
            "Task",
            (object,),
            {
                "executor": type("Executor", (object,), {"interaction": backend})(),
            },
        )()

        class _Bridge:
            task = None

        bridge = _Bridge()
        bridge.task = task
        page = debug_page.DebugPage.__new__(debug_page.DebugPage)
        page._build_bridge = lambda: bridge
        return page

    def test_background_backend_hint_mentions_other_modes(self):
        """后台投递方式下，提示要指向「改用 Pynput / PyDirect」。"""
        hint = self._page("PostMessageInteraction")._no_effect_hint()
        self.assertIn("后台投递", hint)
        self.assertIn("Pynput", hint)
        self.assertIn("PyDirect", hint)

    def test_foreground_backend_hint_mentions_occlusion(self):
        """前台方式下，提示要指向「窗口被遮挡」。"""
        hint = self._page("PynputInteraction")._no_effect_hint()
        self.assertIn("遮挡", hint)
        self.assertNotIn("Pynput", hint, "已经是前台方式，不该再让人改它")


if __name__ == "__main__":
    unittest.main()
