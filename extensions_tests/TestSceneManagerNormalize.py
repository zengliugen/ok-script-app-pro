# -*- coding: utf-8 -*-
"""场景管理的分辨率归一化与坐标换算测试（批次二）。

被测对象是 `extensions/capabilities/scene_manager/normalize.py`。它决定「同一套场景库
能不能在任意分辨率下继续用」，且全部是纯数学，因此在无 GUI、无设备的环境逐值验证。

分四组：

1. **比例判定**：宽高比一致才允许换算，不一致明确报错而非硬算。
2. **坐标换算**：相对比例与像素的双向换算、截断取整口径、往返误差。
3. **相交检测**：特征框与遮罩矩形的交集，用于发现「裁出的模板是纯色块」。
4. **帧稳定判据**：复用框架阈值的稳定性判定方向正确。

帧相关用例需要 numpy（框架既有依赖），缺它时按类跳过。
"""

import unittest

from extensions.capabilities.scene_manager import normalize

try:
    import cv2
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值依赖
    cv2 = None
    np = None
    NUMPY_ERROR = str(exc)


class TestNormalizeRatio(unittest.TestCase):
    """宽高比判定。"""

    def test_same_ratio_variants_allowed(self):
        """同为 16:9 的不同分辨率之间允许换算。"""
        pairs = [
            ((1920, 1080), (3840, 2160)),
            ((1920, 1080), (2560, 1440)),
            ((1920, 1080), (1280, 720)),
            ((1920, 1080), (1600, 900)),
        ]
        for current, base in pairs:
            with self.subTest(current=current, base=base):
                self.assertFalse(
                    normalize.ratio_differs(*current, *base),
                )
                self.assertAlmostEqual(
                    normalize.scale_factor(*current, *base),
                    base[0] / current[0],
                    places=6,
                )

    def test_different_ratio_rejected(self):
        """4:3 与 16:9 之间必须报错，不允许硬算。"""
        self.assertTrue(normalize.ratio_differs(1024, 768, 1920, 1080))
        with self.assertRaises(normalize.NormalizeError):
            normalize.scale_factor(1024, 768, 1920, 1080)

    def test_tolerance_matches_framework(self):
        """容差为 1%，与框架 check_frame_and_resolution 一致。"""
        self.assertEqual(normalize.RATIO_TOLERANCE, 0.01)
        # 1920x1081 的宽高比与 16:9 相差约 0.09%，在容差内
        self.assertFalse(normalize.ratio_differs(1920, 1081, 1920, 1080))
        # 1920x1090 相差约 0.9%，仍在 1% 容差内
        self.assertFalse(normalize.ratio_differs(1920, 1090, 1920, 1080))
        # 1920x1150 相差约 6%，超出容差
        self.assertTrue(normalize.ratio_differs(1920, 1150, 1920, 1080))

    def test_non_positive_resolution_rejected(self):
        """零或负分辨率直接报错，不静默返回 0 缩放。"""
        with self.assertRaises(normalize.NormalizeError):
            normalize.ratio_differs(0, 1080, 1920, 1080)
        with self.assertRaises(normalize.NormalizeError):
            normalize.scale_factor(1920, 1080, -1, 1080)

    def test_resize_cost_hint(self):
        """提示缩放方向，供界面告知用户「正在缩放」。"""
        self.assertEqual(normalize.resize_cost_hint(1920, 1080, 1920, 1080), (False, "none"))
        self.assertEqual(normalize.resize_cost_hint(3840, 2160, 1920, 1080), (True, "down"))
        self.assertEqual(normalize.resize_cost_hint(1280, 720, 1920, 1080), (True, "up"))


class TestNormalizeRect(unittest.TestCase):
    """相对比例与像素的换算。"""

    def test_rect_is_resolution_independent(self):
        """同一相对矩形在两种分辨率下得到成比例的像素矩形。"""
        rect = (0.25, 0.5, 0.75, 0.875)
        small = normalize.rect_to_pixels(rect, 1920, 1080)
        large = normalize.rect_to_pixels(rect, 3840, 2160)
        self.assertEqual(small, (480, 540, 1440, 945))
        self.assertEqual(large, (960, 1080, 2880, 1890))
        for index in range(4):
            self.assertEqual(large[index], small[index] * 2)

    def test_truncation_not_rounding(self):
        """取整用截断，与遮罩几何口径一致，避免同一矩形在两处差 1 像素。"""
        # 0.13 * 1920 = 249.6，截断得 249，四舍五入会是 250
        pixels = normalize.rect_to_pixels((0.13, 0.0, 0.9, 1.0), 1920, 1080)
        self.assertEqual(pixels[0], 249)

    def test_rect_clamped_to_frame(self):
        """越界矩形被裁到帧边界内。"""
        pixels = normalize.rect_to_pixels((0.0, 0.0, 1.0, 1.0), 100, 50)
        self.assertEqual(pixels, (0, 0, 100, 50))

    def test_degenerate_rect_rejected(self):
        """零面积矩形报错，不返回空矩形让调用方自己发现。"""
        with self.assertRaises(normalize.NormalizeError):
            normalize.rect_to_pixels((0.5, 0.5, 0.5, 0.9), 100, 100)
        with self.assertRaises(normalize.NormalizeError):
            normalize.rect_to_pixels((0.1, 0.1, 0.3, 0.3), 0, 100)

    def test_rect_center(self):
        """中心点取几何中心，向下取整（与点击坐标的整数要求一致）。"""
        center = normalize.rect_center((0.2, 0.2, 0.6, 0.6), 100, 100)
        self.assertEqual(center, (40, 40))
        center = normalize.rect_center((0.0, 0.0, 0.05, 0.05), 100, 100)
        self.assertEqual(center, (2, 2))

    def test_click_point_is_resolution_independent(self):
        """同一相对区域在两种分辨率下的点击点成比例。"""
        rect = (0.4, 0.4, 0.6, 0.6)
        low = normalize.click_point(rect, 1280, 720)
        high = normalize.click_point(rect, 2560, 1440)
        self.assertEqual(high, (low[0] * 2, low[1] * 2))

    def test_pixels_to_rect_roundtrip_within_one_pixel(self):
        """像素 → 比例 → 像素的往返误差不超过 1 像素。"""
        width, height = 1920, 1080
        samples = [
            (0, 0, 100, 100),
            (480, 540, 1440, 945),
            (1919, 1079, 1920, 1080),
            (13, 7, 777, 333),
        ]
        for original in samples:
            with self.subTest(rect=original):
                ratio = normalize.pixels_to_rect(*original, width, height)
                restored = normalize.rect_to_pixels(ratio, width, height)
                for index in range(4):
                    self.assertLessEqual(abs(restored[index] - original[index]), 1)

    def test_pixels_to_rect_rejects_degenerate_and_out_of_range(self):
        """退化矩形与非法分辨率报错。"""
        with self.assertRaises(normalize.NormalizeError):
            normalize.pixels_to_rect(100, 100, 100, 200, 1920, 1080)
        with self.assertRaises(normalize.NormalizeError):
            normalize.pixels_to_rect(0, 0, 100, 100, 0, 1080)

    def test_resolve_region_pixels_uses_base_resolution(self):
        """模板裁剪区域按基准分辨率换算，与当前帧无关。"""
        rect = (0.1, 0.1, 0.3, 0.3)
        self.assertEqual(normalize.resolve_region_pixels(rect, 1920, 1080), (192, 108, 576, 324))


class TestNormalizeIntersection(unittest.TestCase):
    """特征框与遮罩矩形的相交检测。"""

    def test_overlap_detected_with_intersection(self):
        """相交时返回 True 与精确交集。"""
        overlaps, intersection = normalize.rects_intersect((0, 0, 100, 100), (50, 50, 150, 150))
        self.assertTrue(overlaps)
        self.assertEqual(intersection, (50, 50, 100, 100))

    def test_touching_edges_not_counted_as_overlap(self):
        """仅边界相接不算相交（右下开区间，相接面积为 0）。"""
        overlaps, intersection = normalize.rects_intersect((0, 0, 100, 100), (100, 0, 200, 100))
        self.assertFalse(overlaps)
        self.assertIsNone(intersection)

    def test_disjoint_rects(self):
        """完全不相交返回 False。"""
        overlaps, _ = normalize.rects_intersect((0, 0, 10, 10), (20, 20, 30, 30))
        self.assertFalse(overlaps)

    def test_contained_rect_overlap_ratio(self):
        """内框被完全包含时，占比等于内框面积除以第一个矩形面积。"""
        # 第一个 100x100 = 10000，交集是内框 10x10 = 100，占比 0.01
        self.assertAlmostEqual(normalize.intersection_ratio((0, 0, 100, 100), (10, 10, 20, 20)), 0.01)

    def test_self_overlap_ratio_is_one(self):
        """与自身求交时占比为 1.0。"""
        self.assertAlmostEqual(normalize.intersection_ratio((0, 0, 100, 100), (0, 0, 100, 100)), 1.0)

    def test_partial_overlap_ratio(self):
        """部分重叠时占比等于重叠面积除以第一个矩形面积。"""
        # 第一个 100x100，交集 50x50 = 2500，占比 0.25
        self.assertAlmostEqual(normalize.intersection_ratio((0, 0, 100, 100), (50, 50, 150, 150)), 0.25)

    def test_zero_area_first_rect(self):
        """分母为零时返回 0.0 而不是抛除零异常。"""
        self.assertEqual(normalize.intersection_ratio((0, 0, 0, 100), (0, 0, 10, 10)), 0.0)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestNormalizeFrame(unittest.TestCase):
    """帧缩放与稳定判定。"""

    def test_normalize_scales_to_base(self):
        """当前帧被缩放到基准分辨率。"""
        frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
        result = normalize.normalize_frame(frame, 1920, 1080)
        self.assertEqual(result.shape[:2], (1080, 1920))

    def test_normalize_returns_same_object_when_size_matches(self):
        """尺寸已一致时返回原帧对象，不做无谓的全帧拷贝。"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertIs(normalize.normalize_frame(frame, 1920, 1080), frame)

    def test_normalize_rejects_ratio_mismatch(self):
        """宽高比不一致时报错，不强行缩放成另一个比例的图。"""
        frame = np.zeros((768, 1024, 3), dtype=np.uint8)
        with self.assertRaises(normalize.NormalizeError):
            normalize.normalize_frame(frame, 1920, 1080)

    def test_normalize_rejects_empty_frame(self):
        """空帧报错。"""
        with self.assertRaises(normalize.NormalizeError):
            normalize.normalize_frame(None, 1920, 1080)

    def test_frames_stable_same_frame(self):
        """完全相同的两帧判为稳定。"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertTrue(normalize.frames_stable(frame, frame.copy()))

    def test_frames_stable_detects_change(self):
        """改动一块区域后判为不稳定。"""
        before = np.zeros((1080, 1920, 3), dtype=np.uint8)
        after = before.copy()
        after[100:400, 100:400] = 255
        self.assertFalse(normalize.frames_stable(before, after))

    def test_frames_stable_handles_none_and_shape_mismatch(self):
        """空帧与形状不一致时视为不稳定，不抛异常。"""
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        self.assertFalse(normalize.frames_stable(None, frame))
        self.assertFalse(normalize.frames_stable(frame, None))
        self.assertFalse(normalize.frames_stable(frame, np.zeros((200, 200, 3), dtype=np.uint8)))

    def test_frames_stable_matches_framework_thresholds(self):
        """阈值与框架 patches_changed 一致：平均差 3.0、变化占比 0.1、单像素 8。"""
        import inspect

        signature = inspect.signature(normalize.frames_stable)
        self.assertEqual(signature.parameters["mean_threshold"].default, 3.0)
        self.assertEqual(signature.parameters["changed_ratio"].default, 0.1)
        self.assertEqual(signature.parameters["pixel_threshold"].default, 8)


if __name__ == "__main__":
    unittest.main()
