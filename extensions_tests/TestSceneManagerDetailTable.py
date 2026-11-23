# -*- coding: utf-8 -*-
"""识别结果表的只读、相似度排序与相似度展示。

user 要求：结果列表改为不可编辑；按相似度排序；有相似度参数就显示出来。

三个被测点：

1. **相似度从哪来**。`evaluate_scene` 原本把每个特征的 `confidence` 算完就丢，
   `score` 只是排序用的合成值（命中固定 1.0、未命中 0.0）。现在把「已评估特征
   置信度的均值」作为 `detail` 第 6 项带出来。
2. **排序用哪一列**。按相似度降序，不再按扫描顺序（id 升序）——识别的第一诉求是
   「最像的是哪个」。
3. **为什么不能只读单元格**。控件级 `NoEditTriggers` 覆盖不到 F2 等入口，item 级
   去掉 `ItemIsEditable` 才是数据级保证。
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
    from PySide6.QtWidgets import QApplication, QTableWidget

    QT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 GUI 依赖
    QApplication = None
    QT_ERROR = str(exc)


class TestDetailSimilarity(unittest.TestCase):
    """`evaluate_scene` 带出真实置信度（纯逻辑，不需要图像依赖）。"""

    def _scene(self, count=2, mandatory=True):
        """造一个有 count 个同形状特征的场景。"""
        scene = model.Scene(scene_id="scene-0001", name="主页")
        for index in range(count):
            scene.features.append(
                model.Feature(
                    name=f"f{index}",
                    kind=model.KIND_TEMPLATE,
                    mandatory=mandatory,
                    shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3),
                )
            )
        return scene

    def _stub(self, confidences):
        """替换 `_evaluate_feature`，按顺序返回给定置信度。

        Args：
            confidences：`[(hit, confidence)]` 列表。
        """
        import itertools

        original = recognize._evaluate_feature
        pool = itertools.cycle(confidences) if len(confidences) == 1 else iter(confidences)
        recognize._evaluate_feature = lambda *_a, **_k: next(pool)
        self.addCleanup(setattr, recognize, "_evaluate_feature", original)

    def test_detail_has_eight_items(self):
        """明细是 8 元组：第 6 项相似度、第 7 项场景名、第 8 项可选特征总数。

        新字段一律**追加在末尾**：`detail[0..6]` 的位置是既有契约，多个调用点
        （含测试）按位置读取，插在中间会把 `missing` 与 `similarity` 的语义整体
        错位。

        第 8 项是可选特征的**总数**，与第 4 项（可选命中数）配对，供界面显示
        `X/Y`。缺了它界面只能显示一个孤立的 0，用户无法判断「一个都没命中」
        还是「这个场景根本没有可选特征」。
        """
        self._stub([(True, 0.9)])
        _hit, _score, detail = recognize.evaluate_scene(self._scene(), None, 640, 360, lambda *_: (None, None))
        self.assertEqual(len(detail), 8)
        self.assertAlmostEqual(detail[5], 0.9)
        self.assertEqual(detail[6], "主页")
        self.assertEqual(detail[7], 0, "该场景没有可选特征，总数应为 0")

    def test_similarity_is_mean_of_evaluated(self):
        """多个特征都命中时取置信度均值。"""
        self._stub([(True, 1.0), (True, 0.8)])
        _hit, _score, detail = recognize.evaluate_scene(self._scene(2), None, 640, 360, lambda *_: (None, None))
        self.assertAlmostEqual(detail[5], 0.9)

    def test_similarity_of_first_failure(self):
        """首个必需特征不中时，相似度就是那个特征的置信度（只评估了一个）。"""
        self._stub([(False, 0.35)])
        _hit, _score, detail = recognize.evaluate_scene(self._scene(), None, 640, 360, lambda *_: (None, None))
        self.assertAlmostEqual(detail[5], 0.35)
        self.assertEqual(detail[4], "f0")


class TestDetailsBySimilarity(unittest.TestCase):
    """`MatchResult.details_by_similarity` 的排序。"""

    def _result(self, details):
        """用给定明细造结果。"""
        return recognize.MatchResult(details=details)

    def test_sorted_descending(self):
        """按相似度降序排列。"""
        result = self._result(
            [
                ("scene-0001", 0, 1, 0, "f", 0.1),
                ("scene-0002", 1, 1, 0, None, 0.9),
                ("scene-0003", 1, 1, 0, None, 0.5),
            ]
        )
        order = [item[0] for item in result.details_by_similarity()]
        self.assertEqual(order, ["scene-0002", "scene-0003", "scene-0001"])

    def test_ties_keep_scan_order(self):
        """相似度相同时保持原扫描顺序，不随排序抖动。"""
        result = self._result(
            [
                ("scene-0003", 1, 1, 0, None, 0.5),
                ("scene-0001", 1, 1, 0, None, 0.5),
                ("scene-0002", 1, 1, 0, None, 0.5),
            ]
        )
        order = [item[0] for item in result.details_by_similarity()]
        self.assertEqual(order, ["scene-0003", "scene-0001", "scene-0002"])

    def test_prefers_diagnostics_over_details(self):
        """有全量诊断时优先用它排序。

        这是本功能的核心：`details` 受短路影响，每个场景的相似度平均了不同数量的
        特征，数值不可比。实测过一组会让排序**完全反转**的数据——场景 A 只有 1 个
        特征、相似度 0.617；场景 B 有 4 个特征、首个不中（短路只算它 → 0.009），
        但全量评估后是 0.752。按 `details` 排 B 会掉到最后，按 `diagnostics` 排
        B 才正确地排在 A 前面。
        """
        result = recognize.MatchResult(
            details=[
                ("scene-0001", 1, 1, 0, None, 0.617),
                ("scene-0002", 0, 4, 0, "f0", 0.009),
            ],
            diagnostics=[
                ("scene-0001", 1, 1, 1, None, 0.617),
                ("scene-0002", 3, 4, 3, "f0", 0.752),
            ],
        )
        order = [item[0] for item in result.details_by_similarity()]
        self.assertEqual(order, ["scene-0002", "scene-0001"], "全量诊断下 B 更相似，必须排前")
        # 只给 details 时（没有诊断），退回旧行为。
        alone = recognize.MatchResult(details=result.details)
        self.assertEqual([item[0] for item in alone.details_by_similarity()], ["scene-0001", "scene-0002"])

    def test_legacy_five_item_detail_supported(self):
        """旧格式（5 项、无相似度）不会被排序或渲染搞崩。

        `detail` 是元组协议，历史调用点可能只给 5 项。这类行按「无数据」排在最后，
        不按 0 处理——0 是「算出来全是 0 置信度」，与「没有这个数据」是两回事。
        """
        result = self._result(
            [
                ("scene-0001", 0, 1, 0, "f"),
                ("scene-0002", 1, 1, 0, None, 0.2),
            ]
        )
        order = [item[0] for item in result.details_by_similarity()]
        self.assertEqual(order, ["scene-0002", "scene-0001"])


class TestDiagnoseScene(unittest.TestCase):
    """`diagnose_scene` 全量评估（不做短路）。"""

    def _scene(self, count):
        """造 count 个同形状的必需特征。"""
        scene = model.Scene(scene_id="scene-0001", name="主页")
        for index in range(count):
            scene.features.append(
                model.Feature(
                    name=f"f{index}",
                    kind=model.KIND_TEMPLATE,
                    shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3),
                )
            )
        return scene

    def _stub(self, confidences):
        """按顺序返回给定判定结果。

        Args：
            confidences：`[(hit, confidence)]` 列表。
        """
        pool = iter(confidences)
        original = recognize._evaluate_feature
        recognize._evaluate_feature = lambda *_a, **_k: next(pool, (False, 0.0))
        self.addCleanup(setattr, recognize, "_evaluate_feature", original)

    def test_evaluates_every_feature(self):
        """全部特征都评估，不像 evaluate_scene 那样在首个失败处停。"""
        # 第二个特征失败，但第三个仍应被评估（相似度里能看到它的贡献）
        self._stub([(True, 1.0), (False, 0.0), (True, 1.0)])
        similarity, hits, total, mandatory_hit, missing, _optional, missing_all = recognize.diagnose_scene(
            self._scene(3), None, 640, 360, lambda *_: (None, None)
        )
        self.assertEqual(total, 3)
        self.assertEqual(hits, 2)
        self.assertAlmostEqual(similarity, 2 / 3, places=4)
        self.assertFalse(mandatory_hit)
        self.assertEqual(missing, "f1")
        self.assertEqual(missing_all, ["f1"], "全量列表此时只有一个失败项")

    def test_missing_all_lists_every_failure(self):
        """全部必需特征失败时，`missing_all` 必须列全，而不是只给第一个。

        **这是一个真实缺陷的回归测试**。原先只返回第一个失败项，界面上 3 个必需
        特征全失败的一行只能看到一个名字，用户会以为另外两个命中了——而那两个
        恰恰也是没通过的原因。

        同时锁定 `missing`（第一个）语义不变：运行期的短路诊断依赖它。
        """
        self._stub([(False, 0.0), (False, 0.0), (False, 0.0)])
        _sim, _hits, _total, mandatory_hit, missing, _optional, missing_all = recognize.diagnose_scene(
            self._scene(3), None, 640, 360, lambda *_: (None, None)
        )
        self.assertFalse(mandatory_hit)
        self.assertEqual(missing, "f0", "`missing` 保持「第一个失败」的语义")
        self.assertEqual(missing_all, ["f0", "f1", "f2"], "全量列表必须列全")

    def test_missing_all_excludes_optional(self):
        """可选特征没命中不进 `missing_all`（它不影响场景是否命中）。"""
        scene = model.Scene(scene_id="scene-0001", name="主页")
        scene.features.append(
            model.Feature(name="必需", kind=model.KIND_TEMPLATE, shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3))
        )
        scene.features.append(
            model.Feature(
                name="可选",
                kind=model.KIND_TEMPLATE,
                mandatory=False,
                shape=shapes.Shape.rectangle(0.4, 0.4, 0.6, 0.6),
            )
        )
        self._stub([(True, 0.9), (False, 0.0)])
        _sim, _hits, _total, mandatory_hit, missing, _optional, missing_all = recognize.diagnose_scene(
            scene, None, 640, 360, lambda *_: (None, None)
        )
        self.assertTrue(mandatory_hit)
        self.assertIsNone(missing)
        self.assertEqual(missing_all, [], "可选特征未命中不该出现在缺失列表里")

    def test_all_mandatory_hit(self):
        """必需特征全中时 `missing` 为 None。"""
        self._stub([(True, 0.9), (True, 0.9)])
        _sim, hits, total, mandatory_hit, missing, _optional, missing_all = recognize.diagnose_scene(
            self._scene(2), None, 640, 360, lambda *_: (None, None)
        )
        self.assertTrue(mandatory_hit)
        self.assertIsNone(missing)
        self.assertEqual(hits, total)
        self.assertEqual(missing_all, [], "全中时全量列表为空")


class TestSceneLabel(unittest.TestCase):
    """场景列文本：名称 + id。"""

    def _label(self, name, scene_id):
        """拼一个场景标签。"""
        from extensions.capabilities.scene_manager.pages.debug_page import _scene_label

        return _scene_label(name, scene_id)

    def test_name_and_id(self):
        """名称与 id 都显示——名称给人读，id 用来定位目录。"""
        self.assertEqual(self._label("靶场入口", "scene-0002"), "靶场入口（scene-0002）")

    def test_falls_back_to_id(self):
        """没有名称时只显示 id，不出现空括号。"""
        self.assertEqual(self._label("", "scene-0002"), "scene-0002")
        self.assertEqual(self._label(None, "scene-0002"), "scene-0002")

    def test_no_duplicate_when_name_equals_id(self):
        """名称等于 id 时不重复显示。

        新建场景默认就是这个状态（`Scene.__post_init__` 在 name 为空时填 scene_id），
        直接拼会显示成 `scene-0002（scene-0002）`。
        """
        self.assertEqual(self._label("scene-0002", "scene-0002"), "scene-0002")


class TestDebugPageScansAll(unittest.TestCase):
    """调试页必须评估**全部**场景，不能首中即停。

    用户实测：点「识别当前画面」后，命中第一个场景就不再往下算了，表格里只出现
    一个场景。根因是 `diagnose_blocking` 虽然对每个场景都算了全量相似度，但汇总
    用的 `recognize()` 仍走默认 `stop_on_first=True`——命中之后的场景压根不进
    `details`，表格自然只有一行。

    运行期识别保持 `stop_on_first=True`：那里每轮都要判断，不能为完整性牺牲速度。
    两者用不同 `MatchOptions`，本类锁的是调试页那一侧。
    """

    def test_debug_page_uses_full_scan(self):
        """`diagnose_blocking` 传 `stop_on_first=False`。"""
        import inspect

        from extensions.capabilities.scene_manager import runtime

        source = inspect.getsource(runtime.TaskBridge.diagnose_blocking)
        self.assertIn("stop_on_first=False", source, "调试页关闭首中即停")

    def test_runtime_matcher_keeps_short_circuit(self):
        """运行期 `make_matcher` 不改这个开关，保持首中即停。"""
        import inspect

        from extensions.capabilities.scene_manager import runtime

        source = inspect.getsource(runtime.TaskBridge.make_matcher)
        self.assertNotIn("stop_on_first", source, "运行期不应关掉短路")

    def test_full_scan_reports_every_scene(self):
        """全量模式下 details 覆盖全部场景，首中即停只有一条。"""
        import numpy as np

        scenes = []
        for index in range(1, 5):
            scene = model.Scene(scene_id=f"scene-{index:04d}", name=f"场景 {index}")
            scene.features.append(
                model.Feature(
                    name="f",
                    kind=model.KIND_TEMPLATE,
                    shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3),
                )
            )
            scenes.append(scene)

        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[::3] = 200
        template = frame[36:108, 64:192].copy()

        def loader(scene_id, _name):
            return (template, None) if scene_id == "scene-0001" else (np.zeros_like(template), None)

        short = recognize.recognize(frame, scenes, 640, 360, loader)
        self.assertEqual(len(short.details), 1, "首中即停只评估到命中项")

        full = recognize.recognize(frame, scenes, 640, 360, loader, options=recognize.MatchOptions(stop_on_first=False))
        self.assertEqual(len(full.details), 4, "全量模式必须覆盖全部场景")


class TestDetailCarriesName(unittest.TestCase):
    """明细元组里带场景名称。"""

    def test_evaluate_scene_detail_has_name(self):
        """`evaluate_scene` 的明细第 7 项是场景名。"""
        scene = model.Scene(scene_id="scene-0001", name="主界面")
        scene.features.append(
            model.Feature(name="f0", kind=model.KIND_TEMPLATE, shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3))
        )
        original = recognize._evaluate_feature
        recognize._evaluate_feature = lambda *_a, **_k: (True, 0.9)
        self.addCleanup(setattr, recognize, "_evaluate_feature", original)

        _hit, _score, detail = recognize.evaluate_scene(scene, None, 640, 360, lambda *_: (None, None))
        self.assertEqual(len(detail), 8)
        self.assertEqual(detail[0], "scene-0001")
        self.assertEqual(detail[6], "主界面")

    def test_recognize_result_details_carry_name(self):
        """`recognize` 产出的明细同样带名称（走的是同一个 evaluate_scene）。"""
        scene = model.Scene(scene_id="scene-0001", name="主界面")
        scene.features.append(
            model.Feature(name="f0", kind=model.KIND_TEMPLATE, shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3))
        )
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[::3] = 200
        template = frame[36:108, 64:192].copy()
        result = recognize.recognize(frame, [scene], 640, 360, lambda *_: (template, None))
        self.assertEqual(result.details[0][6], "主界面")


class TestPercentText(unittest.TestCase):
    """相似度文本。"""

    def _text(self, value):
        """格式化一个相似度。"""
        from extensions.capabilities.scene_manager.pages.debug_page import _percent_text

        return _percent_text(value)

    def test_rounds_to_percent(self):
        """0–1 转百分比并取整。"""
        self.assertEqual(self._text(1.0), "100%")
        self.assertEqual(self._text(0.617), "62%")
        self.assertEqual(self._text(0.009), "1%")

    def test_none_shows_dash(self):
        """没有相似度数据时显示破折号，不显示 0%。"""
        self.assertEqual(self._text(None), "—")


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
@unittest.skipIf(cv2 is None, f"缺少图像依赖：{NUMPY_ERROR}")
class TestMandatoryHitCount(unittest.TestCase):
    """必需命中数必须是**实际命中数**，不能由「没全中就是差一个」推算。

    **这是一个真实缺陷的回归测试**。`diagnose_blocking` 原先写：

        mandatory_hits = mandatory_total if mandatory_hit else mandatory_total - 1

    等于假设「没全中 ⇒ 恰好差一个」。但没全中可能**一个都没中**——截错窗口时
    两个特征都失败，真实命中 0/2，界面却显示 `1/2`，而同一行的「缺失特征」列
    还写着某个特征名。两列互相矛盾，读的人会误以为「有一个特征命中了」。
    """

    def _bridge(self, scene, frame):
        """造一个把执行器与取帧都短路掉的 TaskBridge。

        Args：
            scene：要评估的场景。
            frame：`capture_frame` 返回的帧。

        Returns：
            `runtime.TaskBridge` 实例（`run_on_executor` 改为同步执行）。
        """
        from extensions.capabilities.scene_manager import runtime

        class _Task:
            """任务桩：只提供 TaskBridge 构造时校验的方法。"""

            def click(self, *_args, **_kwargs):
                """不做任何事。"""

            def ocr(self, frame=None, **_kwargs):
                """始终识别不到文字（模拟截错窗口）。"""
                return []

        class _Library:
            """库桩：只提供 diagnose 需要的 scenes 与 meta。"""

            def __init__(self):
                self.meta = model.LibraryMeta(base_width=640, base_height=360)

            def load_library(self):
                """返回固定场景列表。"""
                return [scene], []

        bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
        bridge.task = _Task()
        bridge.library = _Library()
        bridge.loader = lambda *_args: (None, None)
        bridge._ocr = lambda *_args, **_kwargs: []
        bridge.last_graph_error = ""
        bridge.run_on_executor = lambda fn, timeout=30.0: fn()
        bridge.capture_frame = lambda: frame
        return bridge

    def test_zero_hits_not_reported_as_one(self):
        """两个必需特征全不中时，界面明细必须是 `0/2`。"""
        scene = model.Scene(scene_id="scene-0001", name="主页")
        for name in ("f0", "f1"):
            scene.features.append(
                model.Feature(name=name, kind=model.KIND_TEMPLATE, shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3))
            )
        original = recognize._evaluate_feature
        recognize._evaluate_feature = lambda *_a, **_k: (False, 0.0)
        self.addCleanup(setattr, recognize, "_evaluate_feature", original)

        result = self._bridge(scene, np.zeros((360, 640, 3), dtype=np.uint8)).diagnose_blocking()
        detail = result.diagnostics[0]
        self.assertEqual(detail[1], 0, f"必需命中数必须是 0，实测 {detail[1]}")
        self.assertEqual(detail[2], 2)
        self.assertEqual(detail[4], "f0、f1", "缺失特征必须列出全部失败项")

    def test_partial_hits_counted_exactly(self):
        """只中两个必需特征之一时，明细是 `1/2`（原公式在此恰好正确）。"""
        scene = model.Scene(scene_id="scene-0001", name="主页")
        for name in ("f0", "f1"):
            scene.features.append(
                model.Feature(name=name, kind=model.KIND_TEMPLATE, shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3))
            )
        pool = iter([(True, 0.9), (False, 0.0)])
        original = recognize._evaluate_feature
        recognize._evaluate_feature = lambda *_a, **_k: next(pool, (False, 0.0))
        self.addCleanup(setattr, recognize, "_evaluate_feature", original)

        result = self._bridge(scene, np.zeros((360, 640, 3), dtype=np.uint8)).diagnose_blocking()
        detail = result.diagnostics[0]
        self.assertEqual(detail[1], 1)
        self.assertEqual(detail[2], 2)
        self.assertEqual(detail[4], "f1")

    def test_optional_hits_do_not_inflate_mandatory(self):
        """可选特征命中不能算进必需命中（`hit_count` 含可选，必须减去）。"""
        scene = model.Scene(scene_id="scene-0001", name="主页")
        scene.features.append(
            model.Feature(name="required", kind=model.KIND_TEMPLATE, shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3))
        )
        scene.features.append(
            model.Feature(
                name="optional",
                kind=model.KIND_TEMPLATE,
                mandatory=False,
                shape=shapes.Shape.rectangle(0.4, 0.4, 0.6, 0.6),
            )
        )
        pool = iter([(False, 0.0), (True, 0.9)])
        original = recognize._evaluate_feature
        recognize._evaluate_feature = lambda *_a, **_k: next(pool, (False, 0.0))
        self.addCleanup(setattr, recognize, "_evaluate_feature", original)

        result = self._bridge(scene, np.zeros((360, 640, 3), dtype=np.uint8)).diagnose_blocking()
        detail = result.diagnostics[0]
        self.assertEqual(detail[1], 0, "必需命中必须是 0，不能把可选命中算进来")
        self.assertEqual(detail[3], 1, "可选命中数应为 1（第 4 项是命中数，总数在第 8 项）")
        self.assertEqual(detail[7], 1, "可选特征总数为 1")


class TestCaptureHint(unittest.TestCase):
    """调试页的「截图目标可能选错」提示，以及结果里的原始帧尺寸。

    背景（真实踩坑）：截图目标被选成输入法窗口（2560×1440），识别全部不命中，
    界面只显示「未识别」，排查方向全落在识别逻辑上——实际一个字都没截到。
    提示的价值是把这个可能直接摆出来。
    """

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, frame_size, matched):
        """造一个按指定帧尺寸与命中状态返回结果的调试页。

        Args：
            frame_size：结果要带的原始帧尺寸。
            matched：是否构造为「命中」。

        Returns：
            `DebugPage` 实例（已执行一次识别）。
        """
        import shutil
        import tempfile

        from extensions.capabilities.scene_manager import store
        from extensions.capabilities.scene_manager.pages import debug_page

        temp_dir = tempfile.mkdtemp(prefix="scene-hint-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()
        library.set_base_resolution(640, 360)
        scene = library.new_scene("主页")
        library.save_scene(scene)

        details = [
            ("scene-0001", 1 if matched else 0, 1, 0, None if matched else "f0", 0.9 if matched else 0.0, "主页", 0)
        ]
        result = recognize.MatchResult(
            status=recognize.MATCH_EXACT if matched else recognize.MATCH_NONE,
            scene=scene if matched else None,
            details=details,
            duration=0.05,
        )
        result.frame_size = frame_size

        class _Bridge:
            """返回固定结果。"""

            def identify_blocking(self, timeout=30.0):
                return result

            def diagnose_blocking(self, timeout=60.0):
                return result

        page = debug_page.DebugPage(library, build_bridge=lambda: _Bridge())
        page.resize(1000, 600)
        self.addCleanup(page.deleteLater)
        page._on_identify()
        return page

    def _toasts(self, page):
        """返回该页所在窗口的浮层列表。

        Args：
            page：`DebugPage`。

        Returns：
            `ok.ui.qt.kit.feedback.Toast` 列表。
        """
        from ok.ui.qt.kit.feedback import Toast

        return page.window().findChildren(Toast)

    def test_hint_absent_when_size_matches(self):
        """尺寸一致时不弹提示。"""
        page = self._page((640, 360), matched=False)
        self.assertEqual(self._toasts(page), [], "尺寸一致不该出现提示")

    def test_hint_absent_when_matched(self):
        """命中时即使尺寸不同也不提示。

        2560×1440 的游戏窗口配 1280×720 的库基准是合法配置，识别靠缩放正常工作。
        """
        page = self._page((1280, 720), matched=True)
        self.assertEqual(self._toasts(page), [], "命中时不该提示截图目标有问题")

    def test_hint_toast_when_missed_and_scaled(self):
        """未命中 + 尺寸不同时**弹浮层**，并写出两个尺寸。"""
        page = self._page((1280, 720), matched=False)
        toasts = self._toasts(page)
        self.assertEqual(len(toasts), 1, "未命中且尺寸不同必须弹浮层")
        toast = toasts[0]
        self.assertEqual(toast.tone, "warning", "这是「可能配错了」的警告")
        text = toast.message_label.text()
        self.assertIn("1280×720", text)
        self.assertIn("640×360", text)
        self.assertIn("截图目标", text)
        self.assertEqual(toast.title_label.text(), "截图目标可能选错")

    def test_hint_toast_when_ratio_differs(self):
        """宽高比不同时提示措辞更强（识别结果不可信）。"""
        page = self._page((1000, 700), matched=False)
        toasts = self._toasts(page)
        self.assertEqual(len(toasts), 1)
        self.assertIn("宽高比不同", toasts[0].message_label.text())

    def test_hint_toast_not_repeated_on_live_refresh(self):
        """实时刷新下同一条提示只弹一次，不堆叠。

        实时刷新每 800ms 跑一次识别。若每次都弹浮层，屏幕上会不断叠新窗口，
        把界面糊住——这比不提示更糟。
        """
        page = self._page((1280, 720), matched=False)
        first = self._toasts(page)
        self.assertEqual(len(first), 1)

        # 再识别两次（模拟实时刷新的后续 tick）
        page._on_identify()
        page._on_identify()

        self.assertEqual(len(self._toasts(page)), 1, "同一条提示不该重复弹")

    def test_hint_reappears_after_manual_click(self):
        """手动点「识别当前画面」时清除去重记录，可以再弹一次。

        用户主动点就是要一个当下的结论，哪怕与上次完全相同。
        """
        page = self._page((1280, 720), matched=False)
        self.assertEqual(len(self._toasts(page)), 1)

        page._on_identify_clicked()

        self.assertEqual(len(self._toasts(page)), 2, "手动点一次应再弹一条")

    def test_frame_size_is_reported_before_normalize(self):
        """`frame_size` 必须是**归一化之前**的尺寸，否则永远看不出截图目标错了。

        这是最容易实现错的地方：`diagnose_blocking` 里帧在归一化后尺寸就等于库
        基准，从那里读只能得到「和基准一样」，提示永远不会触发。
        """
        import numpy as np

        from extensions.capabilities.scene_manager import runtime

        class _Task:
            """任务桩。"""

            def click(self, *_args, **_kwargs):
                """不做任何事。"""

            def ocr(self, frame=None, **_kwargs):
                """识别不到文字。"""
                return []

        class _Library:
            """库桩。"""

            def __init__(self):
                self.meta = model.LibraryMeta(base_width=640, base_height=360)

            def load_library(self):
                """返回一个空场景（只为让流程走完）。"""
                return [model.Scene(scene_id="scene-0001", name="主页")], []

        bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
        bridge.task = _Task()
        bridge.library = _Library()
        bridge.loader = lambda *_args: (None, None)
        bridge._ocr = lambda *_args, **_kwargs: []
        bridge.last_graph_error = ""
        bridge.run_on_executor = lambda fn, timeout=30.0: fn()
        # 给一个与基准**不同**的帧：归一化后会变成 640×360，读那里就拿不到原始尺寸。
        bridge.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)

        result = bridge.diagnose_blocking()
        self.assertEqual(result.frame_size, (1280, 720), "必须报告归一化之前的原始尺寸")


class TestDetailTable(unittest.TestCase):
    """结果表：只读、按相似度排序、展示相似度。"""

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, details):
        """造一个返回给定明细的调试页。

        Args：
            details：`MatchResult.details` 的内容。

        Returns：
            `DebugPage` 实例。
        """
        import shutil
        import tempfile

        from extensions.capabilities.scene_manager import store
        from extensions.capabilities.scene_manager.pages import debug_page

        temp_dir = tempfile.mkdtemp(prefix="scene-table-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()
        library.set_base_resolution(640, 360)
        scene = library.new_scene("主页")
        library.save_scene(scene)

        result = recognize.MatchResult(
            status=recognize.MATCH_EXACT,
            scene=scene,
            details=details,
            duration=0.05,
        )

        class _Bridge:
            """返回固定结果。"""

            def identify_blocking(self, timeout=30.0):
                return result

        page = debug_page.DebugPage(library, build_bridge=lambda: _Bridge())
        page.resize(1000, 600)
        self.addCleanup(page.deleteLater)
        page._on_identify()
        return page

    def test_cells_not_editable(self):
        """所有单元格都不可编辑。

        控件级 `NoEditTriggers` 覆盖不到 F2、部分平台的右键菜单，因此 item 级也要
        去掉 `ItemIsEditable`；这里逐格断言，确保没有遗漏某一行某一列。
        """
        from PySide6.QtCore import Qt

        page = self._page([("scene-0001", 1, 1, 0, None, 0.9)])
        table = page.detail_table
        self.assertEqual(table.editTriggers(), QTableWidget.EditTrigger.NoEditTriggers)
        for row in range(table.rowCount()):
            for column in range(table.columnCount()):
                item = table.item(row, column)
                self.assertFalse(
                    bool(item.flags() & Qt.ItemFlag.ItemIsEditable),
                    f"第 {row} 行第 {column} 列不应可编辑",
                )

    def test_rows_sorted_by_similarity(self):
        """表格按相似度降序，行号与扫描顺序无关。"""
        page = self._page(
            [
                ("scene-0003", 0, 1, 0, "f", 0.05),
                ("scene-0001", 1, 1, 0, None, 0.98),
                ("scene-0002", 1, 1, 0, None, 0.55),
            ]
        )
        table = page.detail_table
        scenes = [table.item(row, 0).text() for row in range(table.rowCount())]
        self.assertEqual(scenes, ["scene-0001", "scene-0002", "scene-0003"])

    def test_scene_column_shows_name_and_id(self):
        """第一列同时显示名称与 id。"""
        page = self._page([("scene-0002", 1, 1, 1, None, 1.0, "靶场入口")])
        self.assertEqual(page.detail_table.item(0, 0).text(), "靶场入口（scene-0002）")

    def test_scene_column_falls_back_to_id(self):
        """明细不带名称（旧格式）时只显示 id，不出现空括号。"""
        page = self._page([("scene-0002", 1, 1, 1, None, 1.0)])
        self.assertEqual(page.detail_table.item(0, 0).text(), "scene-0002")

    def test_similarity_column_present(self):
        """第二列是相似度，显示成百分比。"""
        page = self._page([("scene-0001", 1, 1, 0, None, 0.617)])
        table = page.detail_table
        headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
        self.assertIn("Similarity", headers)
        self.assertEqual(table.item(0, 1).text(), "62%")

    def test_legacy_detail_rendered_as_dash(self):
        """旧格式明细在界面上显示破折号，而不是崩溃或 0%。"""
        page = self._page([("scene-0001", 1, 1, 0, None)])
        table = page.detail_table
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 1).text(), "—")

    def test_vertical_header_hidden(self):
        """行号表头隐藏：排序后行号与「第几个场景」无关，留着会误导。"""
        page = self._page([("scene-0001", 1, 1, 0, None, 0.9)])
        self.assertFalse(page.detail_table.verticalHeader().isVisible())

    def test_columns_are_five(self):
        """表格是 5 列：「必须总数」列已移除，命中数与总数合并成 `X/Y`。

        单列显示命中数时用户要横向对两次才能算出比例，而「必须总数」这一列
        本身没有独立信息（它只是分母）。合并后一列读完，也省出一列宽度。
        """
        page = self._page([("scene-0001", 1, 1, 0, None, 0.9, "主页", 0)])
        table = page.detail_table
        self.assertEqual(table.columnCount(), 5)

    def test_no_mandatory_total_column(self):
        """表头里不再有独立的「必须总数」列。"""
        page = self._page([("scene-0001", 1, 1, 0, None, 0.9, "主页", 0)])
        table = page.detail_table
        headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
        self.assertNotIn("Mandatory Total", headers)

    def test_mandatory_hits_shows_fraction(self):
        """必须命中列显示 `命中/总数`。"""
        page = self._page([("scene-0001", 1, 3, 0, "f2", 0.5, "主页", 0)])
        self.assertEqual(page.detail_table.item(0, 2).text(), "1/3")

    def test_optional_hits_shows_fraction(self):
        """可选命中列显示 `可选命中/可选总数`。

        这正是用户报的问题：原先前端只拿到「可选命中数」而没有总数，界面上
        一个孤立的数字无法判断含义——2 可能是「2/2 全中」，也可能是「2/5 只中
        两个」，排查方向完全不同。
        """
        page = self._page([("scene-0001", 2, 2, 1, None, 0.9, "主页", 3)])
        self.assertEqual(page.detail_table.item(0, 3).text(), "1/3")

    def test_optional_zero_total_still_shows_fraction(self):
        """没有可选特征时显示 `0/0`，不是破折号或留空。

        `0/0` 一眼能读出「这个场景没配可选特征」；留空或破折号会让它和
        「可选特征一个都没命中」混在一起，用户要回头翻配置才能分辨。
        """
        page = self._page([("scene-0001", 1, 1, 0, None, 0.9, "主页", 0)])
        self.assertEqual(page.detail_table.item(0, 3).text(), "0/0")

    def test_legacy_detail_without_optional_total(self):
        """旧格式明细（无第 8 项）仍能渲染，可选列退化为 `X/0` 而不是崩溃。"""
        page = self._page([("scene-0001", 1, 1, 0, None, 0.9, "主页")])
        table = page.detail_table
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 3).text(), "0/0")

    def test_missing_column_is_wide(self):
        """缺失特征列按固定宽度，足够显示完整特征名。

        原先按内容自适应只有约 90px，中文特征名被压成一两个字（「靶场…」），
        而这一列的全部价值就是指出**哪个**特征没命中。这里断言宽度至少是原先
        的四倍量级。
        """
        page = self._page([("scene-0001", 1, 2, 0, "靶场入口-开始按钮", 0.5, "主页", 0)])
        table = page.detail_table
        width = table.columnWidth(4)
        self.assertGreaterEqual(width, 320, f"缺失特征列过窄：{width}px")

    def test_missing_column_elides_with_tooltip(self):
        """特征多时单行省略，**完整名单放进 tooltip**。

        该格会列出全部未命中的必需特征（顿号连接），几个中文特征名加起来轻易超过
        360px。取舍是「单行省略」而不是折行：折行会让行高随内容涨到两三倍，几行就
        撑爆表格的固定高度，后面的场景要滚动才看得到——而这张表是「扫一眼谁最像」
        的用途，行高参差比截断更难扫。

        但被省略的后半截**不能丢**：tooltip 必须带完整名单，否则就是「列了全部却
        只看得到一部分」，比只列第一个更容易误导——用户会以为看到的就是全部。
        """
        long_missing = "、".join(f"靶场入口-第{i}号特征名称" for i in range(1, 8))
        page = self._page([("scene-0001", 0, 7, 0, long_missing, 0.0, "主页", 0)])
        table = page.detail_table
        self.assertFalse(table.wordWrap(), "缺失特征列应单行显示，不折行")

        single_line = table.fontMetrics().horizontalAdvance(long_missing)
        self.assertGreater(single_line, table.columnWidth(4), "测试数据不够长，未超过列宽")
        from extensions.capabilities.scene_manager.pages import debug_page

        self.assertEqual(
            table.rowHeight(0),
            debug_page._ROW_HEIGHT,
            "行高应恒定，不随内容长短变化",
        )

        item = table.item(0, 4)
        self.assertEqual(item.text(), long_missing, "单元格文本保留完整名单（只是显示时省略）")
        self.assertEqual(item.toolTip(), long_missing, "被省略的部分必须能在 tooltip 里看到")

    def test_empty_missing_cell_has_no_tooltip(self):
        """没有缺失特征时不设 tooltip（空的悬浮提示只会闪一下，没有信息）。"""
        page = self._page([("scene-0001", 1, 1, 0, None, 0.95, "主页", 0)])
        item = page.detail_table.item(0, 4)
        self.assertEqual(item.text(), "")
        self.assertEqual(item.toolTip(), "")

    def test_missing_cell_uses_danger_color(self):
        """缺失特征单元格用主题的 danger 色（比正文醒目）。

        判据是「与该行其他单元格颜色不同」——直接断言具体色值会把测试绑死在
        某个主题 token 上，换主题就红。
        """
        page = self._page([("scene-0001", 0, 2, 0, "靶场入口-文本", 0.0, "主页", 0)])
        table = page.detail_table
        missing_color = table.item(0, 4).foreground().color().name()
        scene_color = table.item(0, 0).foreground().color().name()
        self.assertNotEqual(missing_color, scene_color, "缺失特征应比其他列更醒目")

    def test_empty_missing_cell_keeps_default_color(self):
        """没有缺失特征时该格不留色（否则「空」也会被读成问题）。"""
        page = self._page([("scene-0001", 1, 1, 0, None, 0.95, "主页", 0)])
        table = page.detail_table
        self.assertEqual(table.item(0, 4).text(), "")
        self.assertEqual(
            table.item(0, 4).foreground().color().name(),
            table.item(0, 0).foreground().color().name(),
            "空单元格不该染成 danger 色",
        )


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class TestDebugPageLongText(unittest.TestCase):
    """完整跳转的长路径不能把页面最小宽度撑出窗口。

    用户报的现象：完整跳转成功后，同一行右侧的「开始」按钮看不见了。路径是一整条
    长字符串，`QLabel` 不换行时最小宽度等于整段文本宽度（实测数千像素），页面的
    最小宽度被撑大，按钮被推到可视区之外。结果标签必须开换行。
    """

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        """造一个只用于布局断言的调试页（不跑识别与跳转）。

        Returns：
            `DebugPage` 实例。
        """
        import shutil
        import tempfile

        from extensions.capabilities.scene_manager import store
        from extensions.capabilities.scene_manager.pages import debug_page

        temp_dir = tempfile.mkdtemp(prefix="scene-wide-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()
        library.set_base_resolution(640, 360)
        scene = library.new_scene("主页")
        library.save_scene(scene)
        page = debug_page.DebugPage(library, build_bridge=None)
        self.addCleanup(page.deleteLater)
        return page

    def test_goto_label_wraps_long_path(self):
        """验证完整跳转的结果标签换行，长路径不撑宽页面。"""
        page = self._page()
        path = "；".join(
            f"场景{index}（scene-{index:04d}） --关卡入口{index}--> 场景{index + 1}（scene-{index + 1:04d}）"
            for index in range(1, 12)
        )
        page._goto_result("到达 结算（scene-0006），用时 1.8s", level="success", detail=path)
        self.assertTrue(page.goto_label.wordWrap(), "长路径标签必须换行，否则会把页面撑宽")
        single_line = page.goto_label.fontMetrics().horizontalAdvance(page.goto_label.text())
        self.assertGreater(single_line, 1500, "测试数据不够长，未复现用户的长路径")
        self.assertLess(
            page.goto_label.minimumSizeHint().width(),
            600,
            "换行后最小宽度不应等于整段文本宽度",
        )

    def test_result_labels_all_wrap(self):
        """识别/试走/状态三条结果标签同样换行——错误文案也可能很长。"""
        page = self._page()
        for name in ("identify_label", "step_label", "goto_label", "status_label"):
            self.assertTrue(getattr(page, name).wordWrap(), f"{name} 应开换行")


if __name__ == "__main__":
    unittest.main()
