# -*- coding: utf-8 -*-
"""场景管理的识别测试（批次三）。

被测对象是 `extensions/capabilities/scene_manager/recognize.py`。识别是全部导航与
探索的入口，判错的表现是「从错误的起点寻路」或「走到了未验证的场景」，比报错更糟。

用**合成图**而不是真实截图：识别的判据是「必需特征是否命中」，用几何图形能精确
控制每个特征的命中与否，而真实截图的像素差异会让「为什么不命中」无法归因。
真实截图上的表现记入 `testing.md` 的验收记录，属人工验证范围。

分五组：

1. **单特征**：模板匹配与 OCR 文本匹配的命中判定。
2. **单场景**：必需全中才命中、可选特征只加分、短路确实生效。
3. **多场景**：精确命中、相似场景不互相误识别、缓存优先、疑似候选。
4. **遮罩相交**：特征框与遮罩矩形的重叠检测（裁出的模板是纯色块）。
5. **文本相似度**：LCS 口径。

需要 numpy 与 cv2，缺失时按类跳过。
"""

import unittest

from extensions.capabilities.scene_manager import model, normalize, recognize, shapes

try:
    import cv2
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值/图像依赖
    cv2 = None
    np = None
    NUMPY_ERROR = str(exc)


def _solid(width, height, color, channels=3):
    """造一张纯色图。纯色模板会被识别层拒绝（零方差），因此不用于模板。"""
    if channels == 2:
        return np.full((height, width), color, dtype=np.uint8)
    return np.full((height, width, 3), color, dtype=np.uint8)


def _textured(width, height, base=0, step=4):
    """造一张有条纹纹理的图，作为可控的模板与画面特征。

    纯色模板在 TM_CCOEFF_NORMED 下相关系数退化为 1.0（见 recognize.match_template
    的说明），因此测试里凡是当模板用的图都必须有纹理。
    """
    image = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        if (y // step) % 2 == 0:
            image[y, :, :] = 255
        else:
            image[y, :, :] = base
    return image


def _stripe_horizontal(width, height, step=4):
    """造垂直方向变化、水平方向一致的条纹图（与 _textured 转向 90 度）。"""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    for x in range(width):
        if (x // step) % 2 == 0:
            image[:, x, :] = 200
        else:
            image[:, x, :] = 30
    return image


def _stamp(frame, rect, color):
    """在帧的指定像素矩形内涂色，模拟画面里的一个特征。"""
    x1, y1, x2, y2 = rect
    frame[y1:y2, x1:x2] = color


def _feature(name, region, mandatory=True, threshold=0.8, kind=model.KIND_TEMPLATE, text="", shape=None):
    """造一个特征定义。

    Args：
        name：特征名。
        region：矩形区域的相对比例 `(x1,y1,x2,y2)`，未给 `shape` 时据此造矩形。
        mandatory：是否必需。
        threshold：阈值。
        kind：识别手段。
        text：OCR 期望文本。
        shape：显式形状；给定后忽略 `region`，用于圆/扇形/多边形用例。
    """
    payload = {
        "name": name,
        "kind": kind,
        "mandatory": mandatory,
        "shape": shape if shape is not None else shapes.Shape.rectangle(*region),
        "threshold": threshold,
    }
    if kind == model.KIND_OCR:
        payload["text"] = text
    return model.Feature(**payload)


def _scene(scene_id, features):
    """造一个场景定义。"""
    return model.Scene(scene_id=scene_id, name=scene_id, features=features)


class _TemplateLoader:
    """按 (场景 id, 特征名) 返回预置模板，并记录被请求过的特征。"""

    def __init__(self, templates, masks=None):
        """保存模板字典。

        Args：
            templates：`{(scene_id, feature_name): ndarray}` 字典。
            masks：`{(scene_id, feature_name): ndarray}` 掩码字典，可选。
        """
        self.templates = templates
        self.masks = masks or {}
        self.requested = []

    def __call__(self, scene_id, feature_name):
        """返回模板与掩码。

        Args：
            scene_id：场景 id。
            feature_name：特征名。

        Returns：
            `(模板, 掩码)` 二元组；模板未预置时返回 `(None, None)`（识别层按未命中
            处理）。掩码字典可选，未预置时为 None（识别层退化为不带掩码）。
        """
        self.requested.append((scene_id, feature_name))
        template = self.templates.get((scene_id, feature_name))
        mask = self.masks.get((scene_id, feature_name))
        return template, mask


class TestSimilarity(unittest.TestCase):
    """文本相似度（LCS 口径）。"""

    def test_exact_match(self):
        """完全相同得 1.0。"""
        self.assertEqual(recognize._similarity("确认", "确认"), 1.0)

    def test_contained_match(self):
        """期望文本被更长文本包含时得 1.0（OCR 常把邻近文字一起识别出来）。"""
        self.assertEqual(recognize._similarity("确认", "请点击确认按钮"), 1.0)

    def test_partial_match(self):
        """部分匹配按占比给分。"""
        self.assertAlmostEqual(recognize._similarity("abc", "abx"), 2 / 3, places=6)

    def test_no_match_and_empty(self):
        """完全不匹配与空串都得 0.0。"""
        self.assertEqual(recognize._similarity("abc", "xyz"), 0.0)
        self.assertEqual(recognize._similarity("", "abc"), 0.0)
        self.assertEqual(recognize._similarity("abc", ""), 0.0)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestMatchPrimitives(unittest.TestCase):
    """单特征匹配。"""

    def setUp(self):
        """造一张 200x200 的黑底帧与一块白色模板。"""
        self.frame = _solid(200, 200, 0)
        self.template = _textured(20, 20)
        self.frame[60:80, 50:70] = self.template

    def test_template_hit_inside_region(self):
        """模板出现在搜索区域内时命中，置信度接近 1。"""
        hit, confidence = recognize.match_template(self.frame, self.template, (40, 50, 90, 100), 0.8)
        self.assertTrue(hit)
        self.assertGreater(confidence, 0.9)

    def test_template_miss_when_outside_region(self):
        """模板在区域外时未命中（区域限定必须真正起作用）。"""
        hit, _confidence = recognize.match_template(self.frame, self.template, (150, 150, 190, 190), 0.8)
        self.assertFalse(hit)

    def test_template_larger_than_region_is_miss_not_error(self):
        """模板比搜索区域大时判未命中，不抛异常。"""
        big = _textured(100, 100)
        hit, confidence = recognize.match_template(self.frame, big, (45, 55, 75, 85), 0.8)
        self.assertFalse(hit)
        self.assertEqual(confidence, 0.0)

    def test_solid_template_rejected(self):
        """纯色模板被拒绝，否则 TM_CCOEFF_NORMED 会让它永远命中。

        实测：纯黑与纯白模板在完全不含它们的画面上 matchTemplate 的 max() 都是
        1.0（零方差导致相关系数退化）。若不拦，用户框一块纯色区域当特征，该场景
        就会永远命中，表现是「总是识别成这个场景」。
        """
        solid_white = _solid(20, 20, 255)
        solid_black = _solid(20, 20, 0)
        for template in (solid_white, solid_black):
            with self.subTest(color=template[0, 0, 0]):
                self.assertFalse(recognize.template_has_texture(template))
                hit, confidence = recognize.match_template(self.frame, template, (40, 50, 90, 100), 0.8)
                self.assertFalse(hit)
                self.assertEqual(confidence, 0.0)

    def test_textured_template_accepted(self):
        """有纹理的模板通过检查。"""
        self.assertTrue(recognize.template_has_texture(self.template))
        self.assertFalse(recognize.template_has_texture(None))

    def test_solid_region_confidence_is_zero_not_nan(self):
        """搜索区是纯色时置信度必须归零，不能是 `nan`。

        **这是一个真实缺陷的回归测试**。`TM_CCOEFF_NORMED` 计算相关系数，搜索区
        方差为 0 时分母为 0，OpenCV 返回 `nan`。先前直接把 `nan` 传出去，导致
        调用方 `_mean` 求出的相似度整体变成 `nan`，调试页显示 `nan%`——排查时
        看不出「其实是画面里没有内容」（真实成因是截图目标选成了输入法窗口）。

        归零的语义是「没有任何相似度证据」，与「算出 0」在判定上等价，但能参与
        求均值、能显示，也就能看出「这一帧根本没内容」。
        """
        solid_frame = _solid(200, 200, 245)
        hit, confidence = recognize.match_template(solid_frame, self.template, (40, 50, 90, 100), 0.8)
        self.assertFalse(hit)
        self.assertEqual(confidence, 0.0)
        self.assertTrue(np.isfinite(confidence), "置信度不能是 nan/inf，否则整行相似度会变成 nan")

    def test_solid_region_with_mask_confidence_is_zero_not_nan(self):
        """带掩码时纯色搜索区同样归零（掩码路径与无掩码路径都会出 `nan`）。"""
        solid_frame = _solid(200, 200, 245)
        mask = np.full(self.template.shape[:2], 255, dtype=np.uint8)
        hit, confidence = recognize.match_template(solid_frame, self.template, (40, 50, 90, 100), 0.8, mask=mask)
        self.assertFalse(hit)
        self.assertEqual(confidence, 0.0)

    def test_template_with_none_inputs(self):
        """空帧或空模板判未命中。"""
        self.assertFalse(recognize.match_template(None, self.template, (0, 0, 50, 50), 0.8)[0])
        self.assertFalse(recognize.match_template(self.frame, None, (0, 0, 50, 50), 0.8)[0])

    def test_text_hit_and_miss(self):
        """OCR 文本匹配：相似度达阈值即命中。"""

        def ocr(_frame, _rect):
            return ["请点击确认按钮"]

        hit, confidence = recognize.match_text(self.frame, "确认", (0, 0, 100, 100), 0.5, ocr=ocr)
        self.assertTrue(hit)
        self.assertGreaterEqual(confidence, 0.5)

        hit, _ = recognize.match_text(self.frame, "取消", (0, 0, 100, 100), 0.9, ocr=ocr)
        self.assertFalse(hit)

    def test_text_without_ocr_engine_is_miss(self):
        """没有 OCR 引擎时该特征判未命中，不得静默判命中。"""
        hit, confidence = recognize.match_text(self.frame, "确认", (0, 0, 100, 100), 0.5, ocr=None)
        self.assertFalse(hit)
        self.assertEqual(confidence, 0.0)

    def test_text_ocr_exception_is_miss(self):
        """OCR 引擎抛异常时判未命中，不让整个识别失败。"""

        def broken_ocr(_frame, _rect):
            raise RuntimeError("引擎崩了")

        hit, _ = recognize.match_text(self.frame, "确认", (0, 0, 100, 100), 0.5, ocr=broken_ocr)
        self.assertFalse(hit)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestEvaluateScene(unittest.TestCase):
    """单场景判定。"""

    def setUp(self):
        """造 200x200 黑帧，白块在 (50,60)-(70,80)。"""
        self.frame = _solid(200, 200, 0)
        self.white = _textured(20, 20)
        self.black = _stripe_horizontal(20, 20)
        self.frame[60:80, 50:70] = self.white
        self.base_w = 200
        self.base_h = 200

    def test_requires_all_mandatory_hits(self):
        """必需特征全中才命中，缺一即不命中。"""
        scene = _scene(
            "scene-0001",
            [
                _feature("f1", (0.2, 0.25, 0.4, 0.45)),
                _feature("f2", (0.2, 0.25, 0.4, 0.45)),
            ],
        )
        loader = _TemplateLoader(
            {
                ("scene-0001", "f1"): self.white,
                ("scene-0001", "f2"): self.white,
            }
        )
        hit, score, detail = recognize.evaluate_scene(scene, self.frame, self.base_w, self.base_h, loader)
        self.assertTrue(hit)
        self.assertEqual(detail[1], 2)
        self.assertEqual(detail[2], 2)

        loader_missing = _TemplateLoader(
            {
                ("scene-0001", "f1"): self.white,
                ("scene-0001", "f2"): self.black,
            }
        )
        hit, _score, detail = recognize.evaluate_scene(scene, self.frame, self.base_w, self.base_h, loader_missing)
        self.assertFalse(hit)
        self.assertEqual(detail[4], "f2")

    def test_optional_features_only_add_score(self):
        """可选特征不命中不影响命中结论，只降低得分。"""
        scene = _scene(
            "scene-0001",
            [
                _feature("must", (0.2, 0.25, 0.4, 0.45)),
                _feature("opt", (0.2, 0.25, 0.4, 0.45), mandatory=False),
            ],
        )
        loader = _TemplateLoader(
            {
                ("scene-0001", "must"): self.white,
                ("scene-0001", "opt"): self.black,
            }
        )
        hit, score, _detail = recognize.evaluate_scene(scene, self.frame, self.base_w, self.base_h, loader)
        self.assertTrue(hit)
        self.assertEqual(score, 1.0)

        loader_all = _TemplateLoader(
            {
                ("scene-0001", "must"): self.white,
                ("scene-0001", "opt"): self.white,
            }
        )
        hit, score, _detail = recognize.evaluate_scene(scene, self.frame, self.base_w, self.base_h, loader_all)
        self.assertTrue(hit)
        self.assertEqual(score, 2.0)

    def test_short_circuit_stops_after_first_miss(self):
        """必需特征按区分度排序后，首个不中就停止，不再请求后续模板。"""
        # f_later 的区分度高（命中率低），先跑且不中，因此 f_never 不应被请求
        scene = _scene(
            "scene-0001",
            [
                _feature("f_later", (0.75, 0.75, 0.95, 0.95)),
                _feature("f_never", (0.2, 0.25, 0.4, 0.45)),
            ],
        )
        scene.find_feature("f_later").hit_count = 1
        scene.find_feature("f_later").attempt_count = 10
        scene.find_feature("f_never").hit_count = 9
        scene.find_feature("f_never").attempt_count = 10
        loader = _TemplateLoader(
            {
                ("scene-0001", "f_later"): self.white,
                ("scene-0001", "f_never"): self.white,
            }
        )
        hit, _score, detail = recognize.evaluate_scene(scene, self.frame, self.base_w, self.base_h, loader)
        self.assertFalse(hit)
        self.assertEqual(detail[4], "f_later")
        self.assertEqual(loader.requested, [("scene-0001", "f_later")])

    def test_missing_template_counts_as_miss(self):
        """模板文件缺失时该特征按未命中处理，不让识别崩掉。"""
        scene = _scene("scene-0001", [_feature("gone", (0.2, 0.25, 0.4, 0.45))])
        loader = _TemplateLoader({})
        hit, _score, detail = recognize.evaluate_scene(scene, self.frame, self.base_w, self.base_h, loader)
        self.assertFalse(hit)
        self.assertEqual(detail[4], "gone")


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestRecognizeMultiple(unittest.TestCase):
    """多场景识别。"""

    def setUp(self):
        """造两个互不相同的场景：A 的左上有白块，B 的右下有白块。"""
        self.frame = _solid(200, 200, 0)
        self.white = _textured(40, 40)
        self.black = _stripe_horizontal(40, 40)
        self.frame[10:50, 10:50] = self.white
        self.base_w = 200
        self.base_h = 200
        self.scene_a = _scene("scene-0001", [_feature("a", (0.0, 0.0, 0.3, 0.3))])
        self.scene_b = _scene("scene-0002", [_feature("b", (0.7, 0.7, 1.0, 1.0))])
        self.loader = _TemplateLoader(
            {
                ("scene-0001", "a"): self.white,
                ("scene-0002", "b"): self.white,
            }
        )

    def test_exact_match_picks_right_scene(self):
        """只有 A 的特征命中，识别结果为 A。"""
        result = recognize.recognize(self.frame, [self.scene_a, self.scene_b], self.base_w, self.base_h, self.loader)
        self.assertTrue(result.matched())
        self.assertEqual(result.scene_id(), "scene-0001")

    def test_similar_scenes_not_confused(self):
        """两个场景必需特征区域不同、模板不同，不会互相误识别。"""
        loader = _TemplateLoader(
            {
                ("scene-0001", "a"): self.white,
                ("scene-0002", "b"): self.black,
            }
        )
        result = recognize.recognize(self.frame, [self.scene_a, self.scene_b], self.base_w, self.base_h, loader)
        self.assertEqual(result.scene_id(), "scene-0001")

        loader_reverse = _TemplateLoader(
            {
                ("scene-0001", "a"): self.black,
                ("scene-0002", "b"): self.black,
            }
        )
        result = recognize.recognize(self.frame, [self.scene_a, self.scene_b], self.base_w, self.base_h, loader_reverse)
        self.assertFalse(result.matched())

    def test_cached_scene_tried_first(self):
        """缓存的上次场景先被尝试（模板请求顺序可证）。"""
        loader = _TemplateLoader(
            {
                ("scene-0001", "a"): self.white,
                ("scene-0002", "b"): self.white,
            }
        )
        recognize.recognize(
            self.frame,
            [self.scene_a, self.scene_b],
            self.base_w,
            self.base_h,
            loader,
            cached_scene_id="scene-0002",
        )
        self.assertEqual(loader.requested[0][0], "scene-0002")

    def test_suspicious_candidates_when_no_match(self):
        """全不命中但有场景部分命中时，返回疑似候选而非空。"""
        scene_partial = _scene(
            "scene-0003",
            [
                _feature("p1", (0.0, 0.0, 0.3, 0.3)),
                _feature("p2", (0.7, 0.7, 1.0, 1.0)),
            ],
        )
        loader = _TemplateLoader(
            {
                ("scene-0003", "p1"): self.white,
                ("scene-0003", "p2"): self.black,
            }
        )
        result = recognize.recognize(self.frame, [scene_partial], self.base_w, self.base_h, loader)
        self.assertFalse(result.matched())
        self.assertEqual(result.status, recognize.MATCH_SUSPICIOUS)
        scene, progress = result.best_suspicion()
        self.assertEqual(scene.scene_id, "scene-0003")
        self.assertAlmostEqual(progress, 0.5)

    def test_no_suspicion_when_no_mandatory_hit(self):
        """必需特征一个都没中时不给疑似候选（与随机无异）。"""
        loader = _TemplateLoader({("scene-0001", "a"): self.black})
        result = recognize.recognize(self.frame, [self.scene_a], self.base_w, self.base_h, loader)
        self.assertEqual(result.status, recognize.MATCH_NONE)
        self.assertEqual(result.candidates, [])

    def test_empty_library_raises(self):
        """空库无法识别，明确报错。"""
        with self.assertRaises(recognize.RecognizeError):
            recognize.recognize(self.frame, [], self.base_w, self.base_h, self.loader)

    def test_none_frame_raises(self):
        """空帧报错。"""
        with self.assertRaises(recognize.RecognizeError):
            recognize.recognize(None, [self.scene_a], self.base_w, self.base_h, self.loader)

    def test_ratio_mismatch_raises(self):
        """宽高比与基准不一致时明确报错，不硬缩放。"""
        odd_frame = _solid(200, 100, 0)
        with self.assertRaises(normalize.NormalizeError):
            recognize.recognize(odd_frame, [self.scene_a], self.base_w, self.base_h, self.loader)

    def test_frame_is_normalized_before_matching(self):
        """大帧先缩放到基准分辨率再匹配，模板不需要缩放。

        大帧由基准帧放大而来，因此缩回基准尺寸后应与原帧基本一致。直接造一张
        「里面有相似纹理」的大帧不行——纹理缩放后的相位与模板不同，匹配会失败，
        那样测的是纹理而不是归一化。
        """
        big_frame = cv2.resize(self.frame, (400, 400), interpolation=cv2.INTER_NEAREST)
        result = recognize.recognize(big_frame, [self.scene_a], self.base_w, self.base_h, self.loader)
        self.assertTrue(result.matched())
        self.assertTrue(result.normalized)

    def test_detail_records_every_scene(self):
        """明细覆盖所有被扫描的场景，供界面展示与排查。"""
        result = recognize.recognize(
            self.frame,
            [self.scene_a, self.scene_b],
            self.base_w,
            self.base_h,
            self.loader,
            options=recognize.MatchOptions(stop_on_first=False),
        )
        self.assertEqual(len(result.details), 2)
        self.assertIsNotNone(result.detail_for("scene-0002"))


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestMaskOverlap(unittest.TestCase):
    """特征框与遮罩矩形的重叠检测。"""

    def test_overlap_detected(self):
        """特征区域与遮罩重叠时报出交集与占比。"""
        scene = _scene("scene-0001", [_feature("f", (0.0, 0.0, 1.0, 1.0))])
        warnings = recognize.mask_overlap_warnings(scene, (150, 150, 200, 200), 200, 200)
        self.assertEqual(len(warnings), 1)
        name, intersection, ratio = warnings[0]
        self.assertEqual(name, "f")
        self.assertEqual(intersection, (150, 150, 200, 200))
        self.assertAlmostEqual(ratio, 0.0625)

    def test_no_overlap_returns_empty(self):
        """不相交时无警告。"""
        scene = _scene("scene-0001", [_feature("f", (0.0, 0.0, 0.3, 0.3))])
        self.assertEqual(recognize.mask_overlap_warnings(scene, (150, 150, 200, 200), 200, 200), [])

    def test_mask_disabled_returns_empty(self):
        """遮罩关闭（矩形为 None）时无警告。"""
        scene = _scene("scene-0001", [_feature("f", (0.0, 0.0, 1.0, 1.0))])
        self.assertEqual(recognize.mask_overlap_warnings(scene, None, 200, 200), [])

    def test_warnings_sorted_by_ratio(self):
        """多个特征重叠时按交集占比降序，最严重的在前。

        遮罩取右下角 40x40：`half` 区域 60x60 中 40x40 落在遮罩内（占比 0.444），
        `quarter` 区域 60x60 中只有 20x20 落在遮罩内（占比 0.111）。
        """
        scene = _scene(
            "scene-0001",
            [
                _feature("quarter", (0.6, 0.6, 0.9, 0.9)),
                _feature("half", (0.7, 0.7, 1.0, 1.0)),
            ],
        )
        warnings = recognize.mask_overlap_warnings(scene, (160, 160, 200, 200), 200, 200)
        self.assertEqual([item[0] for item in warnings], ["half", "quarter"])
        self.assertAlmostEqual(warnings[0][2], 1600 / 3600, places=6)
        self.assertAlmostEqual(warnings[1][2], 400 / 3600, places=6)


if __name__ == "__main__":
    unittest.main()
