# -*- coding: utf-8 -*-
"""分辨率一致性判断的测试。

被测对象是 `extensions/capabilities/scene_manager/resolution.py` 与
`store.SceneLibrary.resolution_problems`。它们决定「什么时候禁止截图、什么时候
禁止保存」，判断错了的两种后果都是静默的：

- 该拦没拦 → 不同分辨率拍进来的场景，特征框到运行期整体错位，表现为「识别不到」；
- 不该拦却拦了 → 换个分辨率重拍同一画面被拒，用户只能删库重建。

因此这里逐条固化三类分支：宽高比不同（硬错误）、比例相同但尺寸不同（放行）、
完全一致（放行）。
"""

import os
import shutil
import tempfile
import unittest

from extensions.capabilities.scene_manager import resolution, store

try:
    import cv2
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值依赖
    cv2 = None
    np = None
    NUMPY_ERROR = str(exc)


class TestFrameProblem(unittest.TestCase):
    """单帧准入判断（纯函数，不依赖图像库）。"""

    def test_same_size_passes(self):
        """尺寸完全一致时不报问题。"""
        self.assertIsNone(resolution.frame_problem((1280, 720), 1280, 720))

    def test_different_ratio_blocked(self):
        """宽高比不同时报错，并给出两侧的分辨率。"""
        problem = resolution.frame_problem((1280, 720), 1918, 997)
        self.assertIsNotNone(problem)
        self.assertIn("宽高比", problem)
        self.assertIn("1280×720", problem)
        self.assertIn("1918×997", problem)

    def test_same_ratio_different_size_passes(self):
        """比例相同、尺寸不同时放行（识别会等比缩放到基准）。"""
        self.assertIsNone(resolution.frame_problem((2560, 1440), 1280, 720))

    def test_zero_size_blocked(self):
        """帧尺寸非正时报错。"""
        self.assertIsNotNone(resolution.frame_problem((0, 0), 1280, 720))

    def test_missing_base_blocked(self):
        """库没有基准时给出可操作的提示，而不是静默放行。"""
        problem = resolution.frame_problem((1280, 720), 0, 0)
        self.assertIsNotNone(problem)
        self.assertIn("基准分辨率", problem)

    def test_tolerance_matches_normalize(self):
        """容差与识别链路同源：1920×1081 与 1920×1080 只差 0.09%，应放行。"""
        self.assertIsNone(resolution.frame_problem((1920, 1081), 1920, 1080))


class TestFrameWarning(unittest.TestCase):
    """「比例一致但尺寸不同」的提醒。"""

    def test_no_warning_when_identical(self):
        """尺寸一致时没有提醒。"""
        self.assertIsNone(resolution.frame_warning((1280, 720), 1280, 720))

    def test_warning_when_scaled(self):
        """尺寸不同但比例一致时给提醒，说明会被缩放。"""
        warning = resolution.frame_warning((2560, 1440), 1280, 720)
        self.assertIsNotNone(warning)
        self.assertIn("缩放", warning)

    def test_no_warning_when_hard_error(self):
        """存在硬错误时不再给「可正常识别」的软提醒，避免自相矛盾。"""
        self.assertIsNone(resolution.frame_warning((1280, 720), 1918, 997))


class TestCaptureWarning(unittest.TestCase):
    """识别后的「截图目标可能选错」提示。

    与 `frame_warning` 的区别：那个用于采集前的准入提醒，这个用于识别后的诊断；
    关键差异是**必须看识别是否命中**。
    """

    def test_no_warning_when_identical(self):
        """尺寸一致时没有提示（未命中也不提，尺寸不是原因）。"""
        self.assertIsNone(resolution.capture_warning((1280, 720), 1280, 720, matched=False))

    def test_no_warning_when_matched_even_if_scaled(self):
        """命中时即使尺寸不同也不提。

        **这是防「提示贬值」的关键**：2560×1440 的游戏窗口配 1280×720 的库基准
        宽高比完全相同，识别靠缩放就能正确工作，是合法且常见的配置。若只要尺寸
        不同就提示，这类用户每次识别都会看到一条无用提示。
        """
        self.assertIsNone(resolution.capture_warning((2560, 1440), 1280, 720, matched=True))

    def test_warns_when_missed_and_scaled(self):
        """未命中 + 尺寸不同（比例一致）时提示截图目标可能错了。

        这正是用户踩过的情形：截图目标被选成输入法窗口（2560×1440），与库基准
        比例完全相同——光看比例无论如何分辨不出来，只有尺寸能提供线索。
        """
        warning = resolution.capture_warning((2560, 1440), 1280, 720, matched=False)
        self.assertIsNotNone(warning)
        self.assertIn("2560×1440", warning)
        self.assertIn("1280×720", warning)
        self.assertIn("截图目标", warning)

    def test_warns_on_ratio_mismatch(self):
        """宽高比不同时提示「识别结果不可信」，措辞比等比缩放更强。"""
        warning = resolution.capture_warning((1918, 997), 1280, 720, matched=False)
        self.assertIsNotNone(warning)
        self.assertIn("宽高比不同", warning)
        self.assertIn("截图目标", warning)

    def test_warns_when_no_frame(self):
        """没取到帧时给出「检查设备连接」的提示。"""
        warning = resolution.capture_warning(None, 1280, 720, matched=False)
        self.assertIsNotNone(warning)
        self.assertIn("没有取到画面", warning)

    def test_no_warning_without_base_resolution(self):
        """库还没有基准分辨率时不提（无从比较，提了只会误导）。"""
        self.assertIsNone(resolution.capture_warning((2560, 1440), 0, 0, matched=False))


class TestFormatting(unittest.TestCase):
    """显示格式。"""

    def test_describe_size(self):
        """尺寸格式化为「宽×高」。"""
        self.assertEqual(resolution.describe_size(1280, 720), "1280×720")
        self.assertEqual(resolution.describe_size(0, 720), "未知")

    def test_aspect_text_has_four_decimals(self):
        """带比例时保留四位小数。"""
        self.assertEqual(resolution.aspect_text(1280, 720), "1280×720（1.7778）")
        self.assertEqual(resolution.aspect_text(0, 0), "未知")


@unittest.skipIf(cv2 is None, f"缺少图像依赖：{NUMPY_ERROR}")
class TestResolutionProblems(unittest.TestCase):
    """库级全面检查（需要真实 PNG 才能读尺寸）。"""

    def setUp(self):
        """建一个 800×600 基准的临时库。"""
        self.temp_dir = tempfile.mkdtemp(prefix="scene-resolution-")
        self.library = store.SceneLibrary(os.path.join(self.temp_dir, "scenes"))
        self.library.ensure_layout()
        self.library.set_base_resolution(800, 600)

    def tearDown(self):
        """清理临时目录。"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _add_scene(self, name, width, height):
        """加一个带指定尺寸预览图的场景。"""
        scene = self.library.new_scene(name)
        self.library.save_scene(scene)
        image = np.full((height, width, 3), 128, dtype=np.uint8)
        self.library.save_base_image(scene.scene_id, image)
        return scene

    def test_base_image_size_reads_header(self):
        """只读文件头也能拿到正确尺寸。"""
        scene = self._add_scene("主页", 800, 600)
        self.assertEqual(self.library.base_image_size(scene.scene_id), (800, 600))

    def test_base_image_size_missing_returns_none(self):
        """预览图不存在时返回 None，不抛异常。"""
        self.assertIsNone(self.library.base_image_size("scene-9999"))

    def test_image_size_rejects_non_png(self):
        """非 PNG 文件返回 None，不按 PNG 布局读出垃圾宽高。"""
        path = os.path.join(self.temp_dir, "fake.png")
        with open(path, "wb") as handle:
            handle.write(b"not a png at all, just text")
        self.assertIsNone(store.image_size(path))

    def test_all_consistent_no_problems(self):
        """全部一致时没有问题项。"""
        self._add_scene("主页", 800, 600)
        width, height, problems = self.library.resolution_problems()
        self.assertEqual((width, height), (800, 600))
        self.assertEqual(problems, [])

    def test_ratio_mismatch_reported(self):
        """宽高比不同时被查出，原因写明。"""
        self._add_scene("主页", 800, 600)
        self._add_scene("错比例", 640, 400)
        _width, _height, problems = self.library.resolution_problems()
        self.assertEqual(len(problems), 1)
        scene_id, name, actual, reason = problems[0]
        self.assertEqual(name, "错比例")
        self.assertEqual(actual, (640, 400))
        self.assertIn("宽高比", reason)

    def test_size_only_mismatch_marked_soft(self):
        """比例相同、尺寸不同时也列出，但原因标明「可正常识别」。"""
        self._add_scene("主页", 800, 600)
        self._add_scene("同比例", 1600, 1200)
        _width, _height, problems = self.library.resolution_problems()
        self.assertEqual(len(problems), 1)
        _sid, _name, actual, reason = problems[0]
        self.assertEqual(actual, (1600, 1200))
        self.assertIn("比例一致", reason)

    def test_missing_preview_reported(self):
        """预览图缺失也要报，否则导出时才发现（那时已经晚了）。"""
        scene = self.library.new_scene("缺图")
        self.library.save_scene(scene)
        _width, _height, problems = self.library.resolution_problems()
        self.assertEqual(len(problems), 1)
        self.assertIsNone(problems[0][2])

    def test_empty_library_has_no_problems(self):
        """空库没有任何问题项。"""
        _width, _height, problems = self.library.resolution_problems()
        self.assertEqual(problems, [])

    def test_no_base_resolution_returns_empty(self):
        """未记录基准时返回 `(0, 0)` 与空列表，由调用方决定怎么提示。"""
        other = store.SceneLibrary(os.path.join(self.temp_dir, "fresh"))
        other.ensure_layout()
        width, height, problems = other.resolution_problems()
        self.assertEqual((width, height), (0, 0))
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
