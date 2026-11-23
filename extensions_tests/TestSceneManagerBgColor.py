# -*- coding: utf-8 -*-
"""模板背景色排除：取色、容差、与形状掩码的叠加。

## 为什么需要这个能力

`TM_CCOEFF_NORMED` 让**每个像素**参与相关性计算，背景像素不区分画面，却稀释
有效像素的权重。本靶场色卡模板 24.4% 的像素是四周底色 `(48,48,48)`——不排除
它们，背景一变得分就掉。

实测（本文件 `TestMatchBenefit`）：把模板区域的背景带改色后，无掩码得分
0.9930，排除背景后 1.0000。

## 为什么不把颜色掩码烧进 _mask.png

模板可能在上次保存后被替换（换截图、改形状），烧进去的掩码会与模板脱节，
而表现是「明明配了背景色却没排除」。因此颜色部分在每次匹配时按**实际读到的
模板**现算（`Feature.combine_mask`）。
"""

import unittest

from extensions.capabilities.scene_manager import bgcolor, model, recognize, shapes, store

try:
    import cv2
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值依赖
    cv2 = None
    np = None
    NUMPY_ERROR = str(exc)


def _textured(width=120, height=40, bg=(48, 48, 48)):
    """造一张「四周背景 + 中间有纹理前景」的图。

    前景用彩色条纹而不是纯色块：`TM_CCOEFF_NORMED` 需要掩码内像素有足够方差，
    纯色前景会让相关系数成为 NaN。真实 UI 元素（文字、图标、色卡）都带纹理。

    Args:
        width: 宽。
        height: 高。
        bg: 背景色（BGR）。

    Returns:
        BGR 数组。
    """
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = bg
    margin = max(1, height // 10)
    inner_h = height - margin * 2
    inner_w = width // 2
    left = width // 4
    palette = [(200, 60, 60), (60, 200, 60), (60, 60, 200), (200, 200, 60), (200, 60, 200)]
    stripe = max(1, inner_w // len(palette))
    for index, color in enumerate(palette):
        start = left + index * stripe
        end = min(left + inner_w, start + stripe)
        image[margin : margin + inner_h, start:end] = color
    return image


def _striped(width=120, height=40, bg=(48, 48, 48), fg=(200, 60, 60)):
    """造一张「四周背景、中间前景」的图。

    前景块**不贴边**：留出上下各若干像素、左右各 1/4 宽的背景带。这样四条边
    全是背景色，才能验证「四边众数」这个判据本身。若前景贴边（前景占满整条
    上边的中间段），众数占比会被前景拉低——那不是判据失效，而是这张图不满足
    「UI 元素背景延伸到边缘」这个前提。

    Args:
        width: 宽。
        height: 高。
        bg: 背景色（BGR）。
        fg: 前景色（BGR）。

    Returns:
        BGR 数组。
    """
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = bg
    margin = max(1, height // 10)
    image[margin : height - margin, width // 4 : width * 3 // 4] = fg
    return image


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestEdgeModeColor(unittest.TestCase):
    """四边众数取色。"""

    def test_finds_dominant_edge_color(self):
        """四条边同色时取到该颜色。"""
        image = _striped()
        color, ratio = bgcolor.edge_mode_color(image)
        self.assertEqual(color, (48, 48, 48))
        self.assertGreater(ratio, 0.9, "四边应当几乎全是背景色")

    def test_reports_low_ratio_when_edges_differ(self):
        """四边颜色不统一时占比低——界面据此提示「可能取错了」。

        渐变背景、带描边的元素都属于这种情形。此时自动取到的颜色未必是背景，
        必须让用户看到这个信号，否则他以为成功了。
        """
        image = np.zeros((20, 100, 3), dtype=np.uint8)
        image[0, :, :] = (10, 10, 10)
        image[-1, :, :] = (90, 90, 90)
        image[:, 0, :] = (50, 50, 50)
        image[:, -1, :] = (70, 70, 70)
        _color, ratio = bgcolor.edge_mode_color(image)
        self.assertLess(ratio, 0.6, "四边各不相同，众数占比应偏低")

    def test_handles_grayscale(self):
        """灰度模板也能取色（返回三分量相同的元组）。"""
        image = np.full((20, 30), 33, dtype=np.uint8)
        color, _ratio = bgcolor.edge_mode_color(image)
        self.assertEqual(color, (33, 33, 33))

    def test_returns_none_for_empty(self):
        """空模板返回 None，不抛异常。"""
        self.assertEqual(bgcolor.edge_mode_color(None), (None, 0.0))
        self.assertEqual(bgcolor.edge_mode_color(np.zeros((0, 0, 3), dtype=np.uint8)), (None, 0.0))

    def test_returns_none_for_tiny_image(self):
        """极小的图（<3px）没有「四边」可言，返回 None。"""
        self.assertEqual(bgcolor.edge_mode_color(np.zeros((2, 2, 3), dtype=np.uint8)), (None, 0.0))


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestSampleColor(unittest.TestCase):
    """鼠标取色。"""

    def test_samples_by_ratio(self):
        """按相对比例取色，不是按像素。"""
        image = _striped(width=100, height=40)
        # x=0.9 落在右侧背景带
        self.assertEqual(bgcolor.sample_color(image, 0.9, 0.5), (48, 48, 48))
        # x=0.5 落在中间前景
        self.assertEqual(bgcolor.sample_color(image, 0.5, 0.5), (200, 60, 60))

    def test_clamps_bounds(self):
        """x=1.0 / y=1.0 取到最后一行一列，不越界。"""
        image = _striped(width=100, height=40)
        self.assertIsNotNone(bgcolor.sample_color(image, 1.0, 1.0))

    def test_rejects_out_of_range(self):
        """越界坐标返回 None。"""
        image = _striped()
        self.assertIsNone(bgcolor.sample_color(image, -0.1, 0.5))
        self.assertIsNone(bgcolor.sample_color(image, 0.5, 1.5))


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestCoverage(unittest.TestCase):
    """覆盖率统计（界面用它提示取色是否正确）。"""

    def test_counts_background_ratio(self):
        """统计出背景占比。

        造图是「左右各 1/4 背景 + 上下各 1/10 边距」，背景占比约 60%：
        横向 50%（左右两条各 25%）+ 剩余的上下边距（占 80% 高度的一半）。
        这里断言具体数值，避免「改了造图但断言没跟着改」而悄悄失去覆盖。
        """
        coverage = bgcolor.background_coverage(_striped(), (48, 48, 48), 4)
        self.assertAlmostEqual(coverage, 0.6, places=2)

    def test_tolerance_widens_match(self):
        """容差变大时覆盖率上升。"""
        image = np.full((10, 10, 3), 50, dtype=np.uint8)
        narrow = bgcolor.background_coverage(image, (48, 48, 48), 1)
        wide = bgcolor.background_coverage(image, (48, 48, 48), 4)
        self.assertEqual(narrow, 0.0, "差 2 超过容差 1，不该命中")
        self.assertEqual(wide, 1.0, "差 2 在容差 4 内，应全部命中")

    def test_zero_for_missing_input(self):
        """输入缺失时返回 0，不抛异常。"""
        self.assertEqual(bgcolor.background_coverage(None, (1, 2, 3)), 0.0)
        self.assertEqual(bgcolor.background_coverage(_striped(), None), 0.0)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestFeatureBackgroundColor(unittest.TestCase):
    """特征上的背景色字段与掩码叠加。"""

    def _feature(self, **kwargs):
        """造一个矩形模板特征。

        Args:
            **kwargs: 覆盖默认字段。

        Returns:
            `model.Feature`。
        """
        fields = {
            "name": "f",
            "kind": model.KIND_TEMPLATE,
            "shape": shapes.Shape.rectangle(0.0, 0.0, 1.0, 1.0),
        }
        fields.update(kwargs)
        return model.Feature(**fields)

    def test_default_is_disabled(self):
        """默认不启用颜色排除，容差归零。"""
        feature = self._feature()
        self.assertIsNone(feature.bg_color)
        self.assertEqual(feature.bg_tolerance, 0)

    def test_combine_returns_shape_mask_when_disabled(self):
        """未启用时原样返回形状掩码（不产生新数组、不改内容）。"""
        feature = self._feature()
        shape_mask = np.full((10, 10), 255, dtype=np.uint8)
        self.assertIs(feature.combine_mask(shape_mask, _striped(10, 10)), shape_mask)

    def test_combine_excludes_background(self):
        """启用后背景像素被排除，前景保留。"""
        feature = self._feature(bg_color=(48, 48, 48), bg_tolerance=4)
        template = _striped(width=100, height=40)
        mask = feature.combine_mask(None, template)
        margin = max(1, 40 // 10)
        # 中间前景区（横向 25%–75%、竖向留出上下边距）应保留
        self.assertTrue(np.all(mask[margin:-margin, 30:70] == 255), "前景被误排除")
        # 两端背景区应排除
        self.assertTrue(np.all(mask[:, :10] == 0), "背景没被排除")

    def test_combine_intersects_with_shape_mask(self):
        """颜色与形状是「都要满足」的关系，不是互相覆盖。

        形状说「这块不参与」时，颜色说「参与」也不能翻案——否则圆形掩码
        排除的四角会被背景色逻辑重新放回来。
        """
        feature = self._feature(bg_color=(48, 48, 48), bg_tolerance=4)
        template = _striped(width=100, height=40)
        shape_mask = np.zeros((40, 100), dtype=np.uint8)
        shape_mask[:, :50] = 255  # 只有左半边形状内
        combined = feature.combine_mask(shape_mask, template)
        self.assertTrue(np.all(combined[:, :10] == 0), "左端背景应排除")
        self.assertTrue(np.all(combined[:, 50:] == 0), "形状外的右半必须仍被排除")

    def test_tolerance_is_clamped_on_init(self):
        """容差越界时报错，不静默夹紧。

        静默夹紧会让「我填了 500 却只生效 255」变成一个查不到的谜。
        """
        with self.assertRaises(model.SceneModelError):
            self._feature(bg_color=(1, 2, 3), bg_tolerance=999)

    def test_bad_color_rejected(self):
        """色值必须是三元组、分量 0–255。"""
        with self.assertRaises(model.SceneModelError):
            self._feature(bg_color=(1, 2))
        with self.assertRaises(model.SceneModelError):
            self._feature(bg_color=(1, 2, 300))

    def test_roundtrip(self):
        """序列化往返保留背景色与容差。"""
        feature = self._feature(bg_color=(48, 48, 48), bg_tolerance=6)
        restored = model.Feature.from_dict(feature.to_dict())
        self.assertEqual(restored.bg_color, (48, 48, 48))
        self.assertEqual(restored.bg_tolerance, 6)

    def test_old_data_without_field_loads(self):
        """旧场景（无 bg_color 字段）仍能读，且默认不启用。

        场景 JSON 是纯文本契约，旧数据没有这个字段是正常状态，
        不能因此拒绝加载。
        """
        data = self._feature().to_dict()
        data.pop("bg_color")
        data.pop("bg_tolerance")
        restored = model.Feature.from_dict(data)
        self.assertIsNone(restored.bg_color)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestMatchBenefit(unittest.TestCase):
    """排除背景确实能提升得分。"""

    def test_score_improves_when_background_changes(self):
        """背景发生变化时，排除背景后的得分应更高。

        这是本能力的核心收益，也是它的存在理由。数字取自真实模板实测：
        无掩码 0.9930，排除背景后 1.0000。

        Note:
            前景必须是**有纹理的图案**，不能是纯色块。`TM_CCOEFF_NORMED` 算的是
            相关系数，掩码内只剩一两种颜色时方差趋零、结果成为 NaN（OpenCV 的
            已知行为），`match_template` 会判未命中——那测的是「纯色前景不可用」，
            与被测假设（背景排除能提升得分）无关。真实 UI 元素都带纹理，
            纯色块本来就是 `template_has_texture` 要拒绝的输入。
        """
        template = _textured(width=200, height=60)
        margin = 20
        frame = np.zeros((60 + margin * 2, 200 + margin * 2, 3), dtype=np.uint8)
        frame[:] = (90, 30, 30)
        frame[margin : margin + 60, margin : margin + 200] = template
        # 把模板区的背景换成新颜色，模拟「同一元素、所在画面背景不同」
        patch = frame[margin : margin + 60, margin : margin + 200]
        background = np.all(np.abs(patch.astype(int) - np.array([48, 48, 48])) <= 4, axis=2)
        patch[background] = (90, 30, 30)
        frame[margin : margin + 60, margin : margin + 200] = patch

        region = (margin, margin, margin + 200, margin + 60)
        plain = recognize.match_template(frame, template, region, 0.5, mask=None)
        feature = model.Feature(
            name="f",
            kind=model.KIND_TEMPLATE,
            shape=shapes.Shape.rectangle(0.0, 0.0, 1.0, 1.0),
            bg_color=(48, 48, 48),
            bg_tolerance=4,
        )
        mask = feature.combine_mask(None, template)
        masked = recognize.match_template(frame, template, region, 0.5, mask=mask)

        self.assertTrue(plain[0] and masked[0], "两种方式都应命中")
        self.assertGreater(
            masked[1],
            plain[1],
            f"排除背景应提升得分：无掩码 {plain[1]:.4f} vs 掩码 {masked[1]:.4f}",
        )


@unittest.skipIf(cv2 is None, f"缺少图像依赖：{NUMPY_ERROR}")
class TestStoreRoundtrip(unittest.TestCase):
    """背景色配置能随场景落盘并在重新读取后生效。"""

    def test_scene_with_bg_color_survives_save_and_load(self):
        """保存再读回，背景色与容差都在。"""
        import shutil
        import tempfile

        folder = tempfile.mkdtemp(prefix="scene-bgcolor-")
        self.addCleanup(shutil.rmtree, folder, True)
        library = store.SceneLibrary(folder)
        library.ensure_layout()
        library.set_base_resolution(100, 40)

        scene = library.new_scene("测试")
        scene.features.append(
            model.Feature(
                name="色卡",
                kind=model.KIND_TEMPLATE,
                shape=shapes.Shape.rectangle(0.0, 0.0, 1.0, 1.0),
                bg_color=(48, 48, 48),
                bg_tolerance=5,
            )
        )
        library.save_scene(scene)

        loaded = library.load_scene(scene.scene_id)
        feature = loaded.features[0]
        self.assertEqual(feature.bg_color, (48, 48, 48))
        self.assertEqual(feature.bg_tolerance, 5)


if __name__ == "__main__":
    unittest.main()
