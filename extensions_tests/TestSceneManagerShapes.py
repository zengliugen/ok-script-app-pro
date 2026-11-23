# -*- coding: utf-8 -*-
"""特征形状与掩码的测试。

被测对象是 `extensions/capabilities/scene_manager/shapes.py` 与
`recognize.match_template` 的掩码分支。这两处共同决定「哪块像素参与匹配」，
判错的表现不是报错，而是**识别率下降**——圆形按钮的四角背景被算进相关性，
置信度被无关像素拉低，于是「时好时坏」，最难归因。

用合成图而不是真实截图：掩码的覆盖率有精确的理论值（圆 π/4、扇形按张角折算），
合成图能逐值断言，真实截图只能目测。

分五组：

1. **几何**：包围盒、旋转、半径按帧宽折算（圆不能被宽高比拉成椭圆）。
2. **掩码覆盖率**：圆 78.5%、扇形按张角、矩形 100%。
3. **命中测试**：各形状的 contains 与手柄位置。
4. **序列化**：只写该形状用得上的字段，往返一致。
5. **掩码对匹配的影响**：带掩码能排除干扰，且形状尺寸不匹配时退化为无掩码。

需要 numpy 与 cv2，缺失时按类跳过。
"""

import unittest

from extensions.capabilities.scene_manager import shapes

try:
    import cv2
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值/图像依赖
    cv2 = None
    np = None
    NUMPY_ERROR = str(exc)

FRAME_W = 1920
FRAME_H = 1080


class TestShapeGeometry(unittest.TestCase):
    """形状的包围盒与旋转。"""

    def test_rectangle_bbox_matches_input(self):
        """未旋转矩形的包围盒就是输入的两点。"""
        shape = shapes.Shape.rectangle(0.1, 0.2, 0.3, 0.5)
        x1, y1, x2, y2 = shape.pixel_bbox(FRAME_W, FRAME_H)
        self.assertEqual((x1, y1, x2, y2), (192, 216, 576, 540))

    def test_rectangle_reversed_drag_is_sorted(self):
        """反向拖拽（从右下往左上）得到的矩形与正向相同。"""
        forward = shapes.Shape.rectangle(0.1, 0.2, 0.3, 0.5)
        backward = shapes.Shape.rectangle(0.3, 0.5, 0.1, 0.2)
        self.assertEqual(forward.points, backward.points)

    def test_rotated_rectangle_bbox_grows(self):
        """旋转后包围盒必须变大——否则模板裁不全，旋转部分被切掉。"""
        plain = shapes.Shape.rectangle(0.1, 0.2, 0.3, 0.5)
        rotated = shapes.Shape.rectangle(0.1, 0.2, 0.3, 0.5, angle=45)
        w0 = plain.pixel_bbox(FRAME_W, FRAME_H)
        w1 = rotated.pixel_bbox(FRAME_W, FRAME_H)
        self.assertGreater((w1[2] - w1[0]) * (w1[3] - w1[1]), (w0[2] - w0[0]) * (w0[3] - w0[1]))

    def test_circle_radius_uses_frame_width(self):
        """圆的半径按帧宽折算，因此包围盒是正方形。

        这一条防的是「相对比例 x 按宽、y 按高」的错觉：1920×1080 下相对半径 0.1
        若在两轴各自折算，会得到 192×108 的椭圆，包围盒把圆上下切掉，掩码覆盖率
        从 78.5% 涨到 94.4%——看起来「匹配还行」，实际丢了圆弧。
        """
        circle = shapes.Shape(shapes.SHAPE_CIRCLE, [(0.5, 0.5)], radius=0.1)
        x1, y1, x2, y2 = circle.pixel_bbox(FRAME_W, FRAME_H)
        self.assertEqual(x2 - x1, y2 - y1, "圆的包围盒必须是正方形")
        self.assertEqual(x2 - x1, 384)

    def test_bbox_clamped_to_frame(self):
        """形状旋转后超出画面时，包围盒收敛到画面内，不越界。

        旋转会让原本贴边的矩形向外扩，这是包围盒越界的真实来源（直接画的矩形
        已在构造时校验过坐标范围）。
        """
        shape = shapes.Shape.rectangle(0.0, 0.0, 0.1, 0.1, angle=45)
        x1, y1, x2, y2 = shape.pixel_bbox(FRAME_W, FRAME_H)
        self.assertEqual(x1, 0, "旋转后左边界越过 0 时应收敛到 0")
        self.assertEqual(y1, 0, "旋转后上边界越过 0 时应收敛到 0")
        self.assertLessEqual(x2, FRAME_W)
        self.assertLessEqual(y2, FRAME_H)

    def test_region_matches_pixel_bbox(self):
        """`region()` 是包围盒的相对比例形式，两者必须自洽。"""
        shape = shapes.Shape.rectangle(0.1, 0.2, 0.3, 0.5)
        region = shape.region(FRAME_W, FRAME_H)
        x1, y1, x2, y2 = shape.pixel_bbox(FRAME_W, FRAME_H)
        self.assertAlmostEqual(region[0], x1 / FRAME_W, places=5)
        self.assertAlmostEqual(region[3], y2 / FRAME_H, places=5)


class TestShapeValidation(unittest.TestCase):
    """构造参数校验。"""

    def test_unknown_kind_rejected(self):
        """未知形状直接报错，不静默退化。"""
        with self.assertRaises(shapes.ShapeError):
            shapes.Shape("ellipse", [(0.1, 0.1)])

    def test_polygon_needs_three_points(self):
        """两个点构不成多边形。"""
        with self.assertRaises(shapes.ShapeError):
            shapes.Shape(shapes.SHAPE_POLYGON, [(0.1, 0.1), (0.2, 0.2)])

    def test_circle_needs_positive_radius(self):
        """半径为 0 的圆没有面积。"""
        with self.assertRaises(shapes.ShapeError):
            shapes.Shape(shapes.SHAPE_CIRCLE, [(0.5, 0.5)], radius=0.0)

    def test_sector_sweep_out_of_range(self):
        """扇角必须在 (0, 360]。"""
        with self.assertRaises(shapes.ShapeError):
            shapes.Shape(shapes.SHAPE_SECTOR, [(0.5, 0.5)], radius=0.1, sweep=0)
        with self.assertRaises(shapes.ShapeError):
            shapes.Shape(shapes.SHAPE_SECTOR, [(0.5, 0.5)], radius=0.1, sweep=400)

    def test_point_out_of_range_rejected(self):
        """坐标必须在 0–1。"""
        with self.assertRaises(shapes.ShapeError):
            shapes.Shape.rectangle(-0.1, 0.2, 0.3, 0.5)

    def test_degenerate_rectangle_rejected(self):
        """零宽或零高的矩形没有面积。"""
        with self.assertRaises(shapes.ShapeError):
            shapes.Shape.rectangle(0.3, 0.2, 0.3, 0.5)

    def test_angle_normalized(self):
        """角度归一到 [0, 360)，负值与超过 360 都要能接受。"""
        self.assertAlmostEqual(shapes.Shape.rectangle(0.1, 0.1, 0.2, 0.2, angle=-30).angle, 330.0)
        self.assertAlmostEqual(shapes.Shape.rectangle(0.1, 0.1, 0.2, 0.2, angle=390).angle, 30.0)


@unittest.skipIf(NUMPY_ERROR is not None, f"缺少数值/图像依赖：{NUMPY_ERROR}")
class TestShapeMask(unittest.TestCase):
    """掩码的形状与覆盖率。"""

    def test_rectangle_mask_fully_covered(self):
        """矩形掩码应 100% 为 255。"""
        shape = shapes.Shape.rectangle(0.1, 0.2, 0.3, 0.5)
        mask = shape.mask(FRAME_W, FRAME_H)
        self.assertEqual(float((mask > 0).mean()), 1.0)

    def test_circle_mask_coverage_is_pi_over_4(self):
        """圆掩码覆盖率应接近 π/4（78.54%）。

        这个数字是圆形的判据：若包围盒被宽高比拉成椭圆，覆盖率会明显偏离。
        """
        circle = shapes.Shape(shapes.SHAPE_CIRCLE, [(0.5, 0.5)], radius=0.1)
        mask = circle.mask(FRAME_W, FRAME_H)
        ratio = float((mask > 0).mean())
        self.assertAlmostEqual(ratio, 0.7854, delta=0.02)

    def test_sector_mask_coverage_scales_with_sweep(self):
        """扇形覆盖率按张角折算：90° 应约为圆的四分之一。"""
        sector = shapes.Shape(shapes.SHAPE_SECTOR, [(0.5, 0.5)], radius=0.1, angle=0, sweep=90)
        mask = sector.mask(FRAME_W, FRAME_H)
        ratio = float((mask > 0).mean())
        self.assertAlmostEqual(ratio, 0.7854 / 4, delta=0.02)

    def test_sector_direction_respected(self):
        """方向角决定扇形朝向：0° 覆盖右半、180° 覆盖左半，两者几乎不重叠。

        允许极少量重叠：cv2.ellipse 光栅化时边界像素会被两侧都填上（实测各 1 列），
        断言「重叠占比低于 1%」而不是「完全无重叠」。
        """
        right = shapes.Shape(shapes.SHAPE_SECTOR, [(0.5, 0.5)], radius=0.1, angle=0, sweep=90)
        left = shapes.Shape(shapes.SHAPE_SECTOR, [(0.5, 0.5)], radius=0.1, angle=180, sweep=90)
        mask_right = right.mask(FRAME_W, FRAME_H)
        mask_left = left.mask(FRAME_W, FRAME_H)
        overlap = int(((mask_right > 0) & (mask_left > 0)).sum())
        total = int((mask_right > 0).sum())
        self.assertGreater(total, 0)
        self.assertLess(overlap / total, 0.01, f"对向扇形重叠过多：{overlap}/{total}")
        # 方向差异必须体现在掩码上：右扇的重心偏右，左扇偏左。
        ys_r, xs_r = np.nonzero(mask_right)
        ys_l, xs_l = np.nonzero(mask_left)
        self.assertGreater(xs_r.mean(), xs_l.mean(), "0° 扇形的重心应更靠右")

    def test_polygon_mask_is_less_than_bbox(self):
        """三角形掩码应约为包围盒的一半以下。"""
        triangle = shapes.Shape(shapes.SHAPE_POLYGON, [(0.2, 0.2), (0.6, 0.2), (0.4, 0.6)])
        ratio = float((triangle.mask(FRAME_W, FRAME_H) > 0).mean())
        self.assertLess(ratio, 0.6)
        self.assertGreater(ratio, 0.2)

    def test_mask_is_single_channel(self):
        """掩码必须是单通道——三通道掩码传给 matchTemplate 会被直接拒绝。"""
        mask = shapes.Shape.rectangle(0.1, 0.1, 0.2, 0.2).mask(FRAME_W, FRAME_H)
        self.assertEqual(len(mask.shape), 2)


class TestShapeContains(unittest.TestCase):
    """画布命中测试。"""

    def test_circle_excludes_bbox_corner(self):
        """圆不包含其包围盒的角——这正是掩码要排除的部分。"""
        circle = shapes.Shape(shapes.SHAPE_CIRCLE, [(0.5, 0.5)], radius=0.1)
        self.assertTrue(circle.contains(0.5, 0.5, FRAME_W, FRAME_H))
        self.assertFalse(circle.contains(0.41, 0.41, FRAME_W, FRAME_H))

    def test_rectangle_contains_center(self):
        """矩形包含自己的中心。"""
        shape = shapes.Shape.rectangle(0.1, 0.2, 0.3, 0.5)
        self.assertTrue(shape.contains(0.2, 0.35, FRAME_W, FRAME_H))
        self.assertFalse(shape.contains(0.05, 0.35, FRAME_W, FRAME_H))

    def test_polygon_contains_center_only(self):
        """多边形包含内部点，不包含包围盒的空角。"""
        triangle = shapes.Shape(shapes.SHAPE_POLYGON, [(0.2, 0.2), (0.6, 0.2), (0.4, 0.6)])
        self.assertTrue(triangle.contains(0.4, 0.3, FRAME_W, FRAME_H))
        self.assertFalse(triangle.contains(0.22, 0.55, FRAME_W, FRAME_H))

    def test_circle_handles_are_center_and_radius(self):
        """圆的手柄是圆心与右侧半径点，不是包围盒四角。"""
        circle = shapes.Shape(shapes.SHAPE_CIRCLE, [(0.5, 0.5)], radius=0.1)
        handles = circle.handles(FRAME_W, FRAME_H)
        self.assertEqual(len(handles), 2)
        self.assertAlmostEqual(handles[0][0], 0.5, places=4)
        self.assertGreater(handles[1][0], handles[0][0])


class TestShapeSerialization(unittest.TestCase):
    """序列化只写该形状用得上的字段。"""

    def test_rectangle_omits_radius_and_sweep(self):
        """矩形不写半径与扇角——多余零值让「字段是否生效」不可判断。"""
        data = shapes.Shape.rectangle(0.1, 0.2, 0.3, 0.5).to_dict()
        self.assertNotIn("radius", data)
        self.assertNotIn("sweep", data)
        self.assertIn("angle", data)

    def test_circle_omits_angle(self):
        """圆不写角度：圆旋转后仍是自己。"""
        data = shapes.Shape(shapes.SHAPE_CIRCLE, [(0.5, 0.5)], radius=0.1).to_dict()
        self.assertNotIn("angle", data)
        self.assertIn("radius", data)

    def test_sector_keeps_angle_and_sweep(self):
        """扇形同时需要方向角与扇角。"""
        data = shapes.Shape(shapes.SHAPE_SECTOR, [(0.5, 0.5)], radius=0.1, angle=30, sweep=60).to_dict()
        self.assertEqual(data["angle"], 30.0)
        self.assertEqual(data["sweep"], 60.0)

    def test_roundtrip(self):
        """四种形状往返后字典一致。"""
        cases = [
            shapes.Shape.rectangle(0.1, 0.2, 0.3, 0.5, angle=15),
            shapes.Shape(shapes.SHAPE_CIRCLE, [(0.5, 0.5)], radius=0.1),
            shapes.Shape(shapes.SHAPE_SECTOR, [(0.4, 0.4)], radius=0.08, angle=45, sweep=120),
            shapes.Shape(shapes.SHAPE_POLYGON, [(0.2, 0.2), (0.5, 0.25), (0.45, 0.6)]),
        ]
        for shape in cases:
            with self.subTest(kind=shape.kind):
                data = shape.to_dict()
                self.assertEqual(shapes.Shape.from_dict(data).to_dict(), data)


@unittest.skipIf(NUMPY_ERROR is not None, f"缺少数值/图像依赖：{NUMPY_ERROR}")
class TestMaskImprovesMatching(unittest.TestCase):
    """掩码对匹配精度的实际影响。"""

    def _scene_with_circle_button(self, corner_text=False):
        """造一个画面：纯色背景 + 圆形按钮，可选在四角画干扰文字。"""
        image = np.full((300, 400, 3), 30, dtype=np.uint8)
        cv2.circle(image, (200, 150), 70, (60, 120, 220), -1)
        cv2.putText(image, "OK", (170, 165), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
        if corner_text:
            for dx, dy in ((-55, -55), (55, -55), (-55, 55), (55, 55)):
                cv2.putText(image, "X", (200 + dx - 8, 150 + dy + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
        return image

    def test_mask_rejects_corner_interference(self):
        """带掩码时，四角干扰对置信度的影响明显更小。

        这一条是异形掩码的存在理由：无掩码时四角内容参与相关性计算，同一张模板
        在「有干扰」画面上的得分明显低于「无干扰」画面；带圆掩码后两者应当接近。
        """
        from extensions.capabilities.scene_manager import recognize

        clean = self._scene_with_circle_button(corner_text=False)
        noisy = self._scene_with_circle_button(corner_text=True)
        x1, y1, x2, y2 = 130, 80, 270, 220
        template = clean[y1:y2, x1:x2].copy()

        mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
        cv2.circle(mask, (70, 70), 70, 255, -1)

        _hit_clean_plain, score_clean_plain = recognize.match_template(clean, template, (x1, y1, x2, y2), 0.5)
        _hit_noisy_plain, score_noisy_plain = recognize.match_template(noisy, template, (x1, y1, x2, y2), 0.5)
        _hit_clean_mask, score_clean_mask = recognize.match_template(clean, template, (x1, y1, x2, y2), 0.5, mask=mask)
        _hit_noisy_mask, score_noisy_mask = recognize.match_template(noisy, template, (x1, y1, x2, y2), 0.5, mask=mask)

        plain_gap = score_clean_plain - score_noisy_plain
        mask_gap = score_clean_mask - score_noisy_mask
        self.assertGreater(plain_gap, 0.0, "无掩码时干扰应拉低得分")
        self.assertLess(mask_gap, plain_gap, "带掩码后干扰的影响必须更小")

    def test_mask_size_mismatch_degrades_gracefully(self):
        """掩码尺寸与模板不符时退化为无掩码，不判失败。

        掩码是精度优化项：缺了只降低精度，若直接判未命中，一个读坏的掩码文件就会
        让整个特征永远失效，而表现是「这个特征从来匹配不到」。
        """
        from extensions.capabilities.scene_manager import recognize

        frame = np.zeros((200, 300, 3), dtype=np.uint8)
        frame[:, :, :] = 100
        frame[::4, :, :] = 200
        template = frame[50:150, 100:200].copy()
        bad_mask = np.full((10, 10), 255, dtype=np.uint8)

        hit, score = recognize.match_template(frame, template, (100, 50, 200, 150), 0.8, mask=bad_mask)
        self.assertTrue(hit, "掩码尺寸不符时应退化为无掩码并照常匹配")
        self.assertGreater(score, 0.9)

    def test_search_area_padded_around_template(self):
        """搜索区域向外扩，因此画面平移几像素仍能命中。

        模板按形状包围盒裁出，若搜索区域也是同一个包围盒，`matchTemplate` 的结果
        退化成 1×1：实测平移 1px 得分就从 1.0 掉到 0.003。
        """
        from extensions.capabilities.scene_manager import recognize

        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        frame[:, :, :] = 80
        frame[::3, :, :] = 210
        x1, y1, x2, y2 = 200, 150, 340, 260
        template = frame[y1:y2, x1:x2].copy()

        shifted = np.roll(frame, 3, axis=1)
        hit, score = recognize.match_template(shifted, template, (x1, y1, x2, y2), 0.8)
        self.assertTrue(hit, f"平移 3px 后应仍命中，实际得分 {score:.4f}")


if __name__ == "__main__":
    unittest.main()
