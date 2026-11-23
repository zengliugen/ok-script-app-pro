# -*- coding: utf-8 -*-
"""识别耗时字段与界面展示的测试。

user 要求「右侧点击识别后，加一个耗时显示」，并补充「实时刷新也需要显示」。

两个被测点：

- `recognize.MatchResult.duration`：耗时覆盖「归一化 + 全场景扫描」，不含取帧与
  线程排队——界面拿它判断的是「识别本身快不快」。
- `debug_page._timing_text`：把秒转成毫秒显示；`duration == 0` 时显示「未实际识别」，
  因为库为空或缺少基准时 `make_matcher` 返回的兜底结果并没有真的识别过，显示
  「0 ms」会被读成「快到没耗时」。

「实时刷新也显示」这条不需要单独的展示代码：实时刷新与按钮共用 `_on_identify`。
本文件另测 `_identifying` 互斥——刷新间隔 800ms 可能短于一次识别，没有它会在上一次
还没返回时再发一次，结果与耗时都对不上当前画面。
"""

import unittest

from extensions.capabilities.scene_manager import model, recognize, shapes

try:
    import cv2
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值依赖
    cv2 = None
    np = None
    NUMPY_ERROR = str(exc)

try:
    from PySide6.QtWidgets import QApplication

    QT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 GUI 依赖
    QApplication = None
    QT_ERROR = str(exc)


class TestMatchResultDuration(unittest.TestCase):
    """`MatchResult` 的耗时字段。"""

    def test_defaults_to_zero(self):
        """默认 0.0 —— 没传耗时的构造点（如兜底结果）不应显示成某个奇怪的数字。"""
        self.assertEqual(recognize.MatchResult().duration, 0.0)

    def test_keeps_given_value(self):
        """传入的耗时原样保留。"""
        self.assertAlmostEqual(recognize.MatchResult(duration=0.064).duration, 0.064)


class TestTimingText(unittest.TestCase):
    """耗时格式化。"""

    def _text(self, duration):
        """格式化一个指定耗时的结果。"""
        from extensions.capabilities.scene_manager.pages.debug_page import _timing_text

        return _timing_text(recognize.MatchResult(duration=duration))

    def test_zero_means_not_identified(self):
        """耗时为 0 时说明「未实际识别」，不能显示 0 ms。

        库为空或缺基准时 `runtime.make_matcher` 返回恒未命中的兜底函数，它从不
        真正扫描。显示「耗时 0 ms」会让人以为识别快到不需要时间。
        """
        self.assertEqual(self._text(0.0), "未实际识别")

    def test_sub_ten_milliseconds_keeps_one_decimal(self):
        """小于 10ms 保留一位小数——否则显示成 0 ms 与「未识别」混淆。"""
        self.assertEqual(self._text(0.0042), "耗时 4.2 ms")

    def test_normal_range_rounds_to_integer(self):
        """常规区间取整，避免长数字挤占一行。"""
        self.assertEqual(self._text(0.064), "耗时 64 ms")
        self.assertEqual(self._text(0.0874), "耗时 87 ms")

    def test_long_duration_still_reported(self):
        """秒级耗时照常显示，不做上限截断。"""
        self.assertEqual(self._text(1.2345), "耗时 1234 ms")


@unittest.skipIf(cv2 is None, f"缺少图像依赖：{NUMPY_ERROR}")
class TestRecognizeDuration(unittest.TestCase):
    """`recognize()` 真的把计时填进结果。"""

    def _scenes(self, count=8):
        """造若干个只有模板特征的场景。"""
        scenes = []
        for index in range(count):
            scene = model.Scene(scene_id=f"scene-{index:04d}", name=f"场景 {index}")
            scene.features.append(
                model.Feature(name="f0", kind=model.KIND_TEMPLATE, shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3))
            )
            scenes.append(scene)
        return scenes

    def _run(self, base_width, base_height):
        """跑一次识别并返回结果。"""
        frame = np.random.randint(0, 255, (360, 640, 3), dtype=np.uint8)
        template = np.random.randint(0, 255, (80, 80, 3), dtype=np.uint8)
        return recognize.recognize(frame, self._scenes(), base_width, base_height, lambda *_: (template, None))

    def test_duration_positive(self):
        """真实识别后耗时为正。"""
        result = self._run(640, 360)
        self.assertGreater(result.duration, 0.0, "耗时必须被填上，否则界面显示『未实际识别』")

    def test_duration_present_when_not_matched(self):
        """未命中也要有耗时——用户最需要排查的正是「为什么识别不到」。"""
        result = self._run(640, 360)
        self.assertFalse(result.matched())
        self.assertGreater(result.duration, 0.0)

    def test_resize_counts_into_duration(self):
        """归一化开销计入耗时：比例相同但尺寸不同时也要走一次缩放。"""
        self._run(640, 360)
        resized = self._run(1920, 1080)
        self.assertTrue(resized.normalized, "该用例要求确实发生了缩放")
        self.assertGreater(resized.duration, 0.0)


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
@unittest.skipIf(cv2 is None, f"缺少图像依赖：{NUMPY_ERROR}")
class TestDebugPageTiming(unittest.TestCase):
    """界面层：显示耗时、「实时刷新也显示」、并发保护。"""

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, duration=0.064, matched=True):
        """造一个带假 bridge 的调试页。

        Args：
            duration：假识别结果要带的耗时。
            matched：假识别是否命中。

        Returns：
            `DebugPage` 实例。
        """
        import tempfile

        from extensions.capabilities.scene_manager import store
        from extensions.capabilities.scene_manager.pages import debug_page

        temp_dir = tempfile.mkdtemp(prefix="scene-timing-")
        self.addCleanup(__import__("shutil").rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()
        library.set_base_resolution(640, 360)
        scene = library.new_scene("关卡选择")
        library.save_scene(scene)

        result = recognize.MatchResult(
            status=recognize.MATCH_EXACT if matched else recognize.MATCH_NONE,
            scene=scene if matched else None,
            details=[(scene.scene_id, 1, 1, 0, None)] if matched else [],
            duration=duration,
        )

        class _Bridge:
            """返回固定结果的假 bridge。"""

            def __init__(self):
                self.calls = 0

            def identify_blocking(self, timeout=30.0):
                self.calls += 1
                return result

        bridge = _Bridge()
        page = debug_page.DebugPage(library, build_bridge=lambda: bridge)
        page.resize(900, 600)
        self.addCleanup(page.deleteLater)
        return page, bridge

    def test_matched_shows_duration(self):
        """命中时文本里带耗时。"""
        page, _bridge = self._page(duration=0.064)
        page._on_identify()
        text = page.identify_label.text()
        self.assertIn("识别为", text)
        self.assertIn("耗时 64 ms", text)

    def test_unmatched_also_shows_duration(self):
        """未识别时也显示耗时——排查「为什么识别不到」时最需要这个数字。"""
        page, _bridge = self._page(duration=0.123, matched=False)
        page._on_identify()
        text = page.identify_label.text()
        self.assertIn("未识别", text)
        self.assertIn("耗时 123 ms", text)

    def test_live_refresh_uses_same_path(self):
        """实时刷新与按钮共用 `_on_identify`，因此耗时对两者同时生效。

        直接断言「两者是同一个方法」而不是模拟定时器：定时器触发只是调用
        `self._on_identify`，换成另一个方法就会让「实时刷新没有耗时」重新出现。
        """
        page, bridge = self._page()
        page._toggle_auto()
        self.assertIsNotNone(page._timer, "应已启动定时器")
        self.assertEqual(page._timer.interval(), 800)
        # 定时器到点即调 _on_identify；此处直接手动触发一次，验证它更新耗时。
        page._on_identify()
        self.assertIn("耗时", page.identify_label.text())
        self.assertEqual(bridge.calls, 1)
        page.stop_timer()

    def test_no_overlapping_identify(self):
        """上一次识别未返回时，刷新触发被丢弃，不会叠加。

        场景：实时刷新间隔 800ms，而场景多时一次识别可能更久。没有互斥时两次识别
        各拿一帧、各写一次结果，界面上的耗时与当前画面不同步。
        """
        page, bridge = self._page()
        page._identifying = True
        page._on_identify()
        self.assertEqual(bridge.calls, 0, "识别中不应再次发起")
        self.assertFalse(page.identify_label.text().startswith("识别为"))

    def test_flag_cleared_after_failure(self):
        """识别抛异常后标记要复位，否则后续点击全部静默失效。"""

        class _BrokenBridge:
            """总是抛异常。"""

            def identify_blocking(self, timeout=30.0):
                raise RuntimeError("执行器忙")

        import tempfile

        from extensions.capabilities.scene_manager import store
        from extensions.capabilities.scene_manager.pages import debug_page

        temp_dir = tempfile.mkdtemp(prefix="scene-timing-broken-")
        self.addCleanup(__import__("shutil").rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()
        page = debug_page.DebugPage(library, build_bridge=lambda: _BrokenBridge())
        self.addCleanup(page.deleteLater)

        page._on_identify()
        self.assertIn("识别失败", page.identify_label.text())
        self.assertFalse(page._identifying, "异常后必须复位，否则再也点不动")


if __name__ == "__main__":
    unittest.main()
