# -*- coding: utf-8 -*-
"""场景管理的任务与装配契约测试（批次七）。

被测对象是 `extensions/capabilities/scene_manager/__init__.py` 与 `tasks.py` 的
装配契约。装配失败的形态是**静默不生效**：代码都在、单元测试都过，只是任务与
页签从来没被注册——用户看到的是「这个功能好像没做」。

因此这里只测「能不能被正确地注册与实例化」，不测导航逻辑（那在
`TestSceneManagerNavigate.py`）。需要 `ok` 运行时的用例按类跳过。
"""

import unittest
from unittest import mock

from extensions.capabilities import scene_manager
from extensions.capabilities.scene_manager import explore, model, runtime
from extensions.capabilities.scene_manager import tasks as tasks_module

try:
    from ok import BaseTask
    from ok import og as ok_og

    OK_ERROR = None
except ModuleNotFoundError as exc:  # 缺框架依赖
    BaseTask = None
    ok_og = None
    OK_ERROR = str(exc)


class TestRegister(unittest.TestCase):
    """装配期注册。"""

    def _config(self, onetime_tasks=None, custom_tabs=None):
        """造一个最小 config 字典。"""
        config = {"onetime_tasks": list(onetime_tasks or [])}
        if custom_tabs is not None:
            config["custom_tabs"] = list(custom_tabs)
        return config

    def test_registers_two_tasks_and_one_tab(self):
        """注册两个任务与一个页签。"""
        config = self._config()
        scene_manager.register(config)
        tasks = [item for item in config["onetime_tasks"] if "scene_manager" in item[0]]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(sorted(item[1] for item in tasks), ["SceneExploreTask", "SceneGotoTask"])
        tabs = [item for item in config["custom_tabs"] if "scene_manager" in item[0]]
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0], [scene_manager.TAB_MODULE, scene_manager.TAB_CLASS])

    def test_register_is_idempotent(self):
        """重复注册不产生重复条目（页签重复会导致界面出现两个相同页）。"""
        config = self._config()
        scene_manager.register(config)
        scene_manager.register(config)
        tasks = [item for item in config["onetime_tasks"] if "scene_manager" in item[0]]
        self.assertEqual(len(tasks), 2)
        tabs = [item for item in config["custom_tabs"] if "scene_manager" in item[0]]
        self.assertEqual(len(tabs), 1)

    def test_custom_tabs_created_when_absent(self):
        """上游没有 custom_tabs 键时新建：框架用 get 读取，缺键合法。"""
        config = self._config()
        self.assertNotIn("custom_tabs", config)
        scene_manager.register(config)
        self.assertIn("custom_tabs", config)

    def test_missing_required_key_raises(self):
        """缺少 onetime_tasks 时崩溃并指名：静默不生效比崩溃难查得多。"""
        with self.assertRaises(RuntimeError) as context:
            scene_manager.register({})
        self.assertIn("onetime_tasks", str(context.exception))

    def test_existing_entries_preserved(self):
        """已有任务与页签不被破坏。"""
        config = self._config(
            onetime_tasks=[["src.tasks.MyOneTimeTask", "MyOneTimeTask"]],
            custom_tabs=[["extensions.capabilities.screenshot_mask.tab", "ScreenshotMaskTab"]],
        )
        scene_manager.register(config)
        self.assertEqual(config["onetime_tasks"][0], ["src.tasks.MyOneTimeTask", "MyOneTimeTask"])
        self.assertEqual(
            config["custom_tabs"][0],
            ["extensions.capabilities.screenshot_mask.tab", "ScreenshotMaskTab"],
        )

    def test_task_specs_are_declared(self):
        """任务清单在装配模块内静态声明，且与 tasks 模块的类名一致。"""
        self.assertEqual(len(scene_manager.TASK_SPECS), 2)
        for module_path, class_name in scene_manager.TASK_SPECS:
            self.assertEqual(module_path, "extensions.capabilities.scene_manager.tasks")
            self.assertIn(class_name, ("SceneGotoTask", "SceneExploreTask"))


@unittest.skipIf(BaseTask is None, f"缺少框架依赖：{OK_ERROR}")
class TestTaskClasses(unittest.TestCase):
    """任务类契约。"""

    def test_task_classes_subclass_base_task(self):
        """两个任务都是 BaseTask 子类。"""
        from extensions.capabilities.scene_manager import tasks

        self.assertTrue(issubclass(tasks.SceneGotoTask, BaseTask))
        self.assertTrue(issubclass(tasks.SceneExploreTask, BaseTask))

    def test_task_names_match_constants(self):
        """任务名与常量一致（任务名同时是配置文件名，改名会让用户配置失联）。"""
        from extensions.capabilities.scene_manager import tasks

        self.assertEqual(tasks.GOTO_TASK_NAME, "Scene Goto")
        self.assertEqual(tasks.EXPLORE_TASK_NAME, "Scene Explore")

    def test_build_tasks_matches_register_specs(self):
        """`build_tasks()` 与装配模块的 TASK_SPECS 一致。"""
        from extensions.capabilities.scene_manager import tasks

        built = [tuple(item) for item in tasks.build_tasks()]
        self.assertEqual(built, list(scene_manager.TASK_SPECS))


class TestRuntimeHelpers(unittest.TestCase):
    """运行期辅助函数（不依赖 ok 的部分）。"""

    def test_library_root_is_under_assets(self):
        """场景库默认落 assets/scenes。"""
        import os

        path = runtime.library_root("X:/demo")
        self.assertTrue(path.endswith(os.path.join("assets", "scenes")))

    def test_frame_size(self):
        """帧尺寸读取对空帧返回 (0, 0)。"""
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("缺少 numpy")
        self.assertEqual(runtime.frame_size(None), (0, 0))
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertEqual(runtime.frame_size(frame), (1920, 1080))

    def test_rect_pixels_raises_value_error_on_degenerate(self):
        """退化矩形转成 ValueError（供界面直接展示）。"""
        with self.assertRaises(ValueError):
            runtime.rect_pixels((0.5, 0.5, 0.5, 0.9), 100, 100)

    def _fake_bridge(self, frame):
        """造一个只实现取帧与点击的桥接对象。

        直接调 `TaskBridge` 会去拿设备管理器；这里只验证「坐标怎么换算、点了哪里」
        这条路径，因此用假对象绕开框架装配。

        Args:
            frame: `capture_frame` 要返回的帧。

        Returns:
            `(bridge, clicks)`，`clicks` 累积每次点击的坐标。
        """
        clicks = []

        class FakeTask:
            """只实现 click。"""

            def __init__(self):
                """空实现。"""

            def click(self, x, y):
                """记录坐标。"""
                clicks.append((x, y))

        bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
        bridge.task = FakeTask()
        bridge.capture_frame = lambda: frame
        return bridge, clicks

    def test_activate_area_clicks_rect_center(self):
        """`activate_area` 的缺省动作是单击区域中心。"""
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("缺少 numpy")
        from extensions.capabilities.scene_manager import model

        area = model.Area(name="a", rect=(0.25, 0.4, 0.75, 0.6))
        bridge, clicks = self._fake_bridge(np.zeros((100, 200, 3), dtype=np.uint8))
        bridge.activate_area(None, area)
        self.assertEqual(len(clicks), 1)
        # 帧 200x100，矩形 x 50..150 / y 40..60 → 中心 (100, 50)
        self.assertEqual(clicks[0], (100, 50))

    def test_area_center_does_not_click(self):
        """`area_center` 只算坐标、不点击——外部拿坐标后自己做任意动作。"""
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("缺少 numpy")
        from extensions.capabilities.scene_manager import model

        area = model.Area(name="a", rect=(0.1, 0.2, 0.3, 0.6))
        bridge, clicks = self._fake_bridge(np.zeros((100, 200, 3), dtype=np.uint8))
        point = bridge.area_center(area)
        # 帧 200x100，矩形 x 20..60 / y 20..60 → 中心 (40, 40)
        self.assertEqual(point, (40, 40))
        self.assertEqual(clicks, [])


class TestCaptureFrame(unittest.TestCase):
    """取帧入口收口设备调用，但**不施遮罩**。

    遮罩是输出脱敏：框架自己在 `to_pil_image`（把图变成文件）时才施加
    `screenshot_processor`，运行期识别一律跑在原始帧上。本能力沿用同一口径，
    只在写 `preview.png` 与展示预览图时调 `apply_global_mask`。

    这些用例固化「取帧返回原始像素」这一条——若有人把遮罩塞进取帧层，识别就会
    在涂黑的帧上跑，遮罩区内的特征永远匹配不到，而那不会报错。
    """

    def _fake_device(self, frame):
        """造一个只提供 get_frame 的假设备管理器。

        Args:
            frame: `get_frame` 要返回的帧。

        Returns:
            替身 `og` 命名空间。
        """
        from types import SimpleNamespace

        class FakeCapture:
            """只实现 get_frame。"""

            def __init__(self):
                """空实现。"""

            def get_frame(self, busy_wait=0):
                """返回预置帧。"""
                del busy_wait
                return frame

        return SimpleNamespace(device_manager=SimpleNamespace(capture_method=FakeCapture()))

    def test_capture_frame_returns_raw_pixels(self):
        """取帧返回原始像素，即使遮罩配置启用也不涂黑。

        这是与框架一致的语义：识别跑在原始帧上。涂黑只发生在落盘时。
        """
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("缺少 numpy")
        import ok

        original = ok.og
        raw = np.full((200, 300, 3), 200, dtype=np.uint8)
        fake = self._fake_device(raw)
        fake.global_config = _FakeGlobalConfig(
            {
                "Enable Mask": True,
                "Mask Anchor": "bottom_right",
                "Mask Width": 50.0,
                "Mask Height": 50.0,
                "Mask Offset X": 0.0,
                "Mask Offset Y": 0.0,
            }
        )
        try:
            ok.og = fake
            frame = runtime.capture_frame()
            self.assertEqual(int(frame[180, 280, 0]), 200, "取帧不应涂黑，遮罩只在落盘时加")
        finally:
            ok.og = original

    def test_apply_global_mask_blacks_out_area(self):
        """`apply_global_mask` 独立可用，且不修改传入的帧。"""
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("缺少 numpy")
        import ok

        original = ok.og
        raw = np.full((200, 300, 3), 200, dtype=np.uint8)
        from types import SimpleNamespace

        values = {
            "Enable Mask": True,
            "Mask Anchor": "bottom_right",
            "Mask Width": 50.0,
            "Mask Height": 50.0,
            "Mask Offset X": 0.0,
            "Mask Offset Y": 0.0,
        }
        try:
            ok.og = SimpleNamespace(global_config=_FakeGlobalConfig(values))
            masked = runtime.apply_global_mask(raw)
            self.assertEqual(int(masked[180, 280, 0]), 0, "遮罩区必须为 0")
            self.assertEqual(int(masked[10, 10, 0]), 200, "遮罩区外保持原像素")
            self.assertEqual(int(raw[180, 280, 0]), 200, "不得原地修改传入帧")
            self.assertIsNot(masked, raw, "必须返回新对象")
        finally:
            ok.og = original

    def test_capture_frame_returns_none_when_no_frame(self):
        """取不到帧时返回 None，不抛异常。"""
        import ok

        original = ok.og
        fake = self._fake_device(None)
        try:
            ok.og = fake
            self.assertIsNone(runtime.capture_frame())
        finally:
            ok.og = original

    def test_capture_frame_reports_missing_device(self):
        """设备管理器缺失时抛 RuntimeError 并指明缺什么。"""
        from types import SimpleNamespace

        import ok

        original = ok.og
        try:
            ok.og = SimpleNamespace(device_manager=None)
            with self.assertRaises(RuntimeError) as ctx:
                runtime.capture_frame()
            self.assertIn("device_manager", str(ctx.exception))
        finally:
            ok.og = original

    def test_bridge_capture_frame_delegates_to_module_entry(self):
        """`TaskBridge.capture_frame` 转调模块级入口，把设备调用收敛到一处。"""
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("缺少 numpy")

        sentinel = np.zeros((10, 10, 3), dtype=np.uint8)
        bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
        original = runtime.capture_frame
        try:
            runtime.capture_frame = lambda: sentinel
            self.assertIs(bridge.capture_frame(), sentinel)
        finally:
            runtime.capture_frame = original


class _FakeGlobalConfig:
    """伪造 `og.global_config`，只实现 `get_config`。"""

    def __init__(self, values):
        """保存配置字典。

        Args:
            values: 要返回的配置。
        """
        self.values = values

    def get_config(self, name):
        """按选项名返回配置。

        Args:
            name: 选项名，本替身忽略它。

        Returns:
            预置的配置字典。
        """
        del name
        return self.values


class TestConfigPageTexture(unittest.TestCase):
    """配置页的零方差模板提示。

    识别层已拒绝零方差模板（判未命中），但用户需要知道「为什么我建的特征不起作用」。
    两处阈值必须同源，否则会出现「界面说能用、识别时恒不命中」。
    """

    def test_pure_color_has_no_texture(self):
        """纯色块判为无纹理。"""
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("缺少 numpy")
        from extensions.capabilities.scene_manager.pages import scene_editor

        self.assertFalse(scene_editor._has_texture(np.full((20, 20, 3), 128, np.uint8)))

    def test_textured_image_has_texture(self):
        """有明暗变化的块判为有纹理。"""
        try:
            import numpy as np
        except ModuleNotFoundError:
            self.skipTest("缺少 numpy")
        from extensions.capabilities.scene_manager.pages import scene_editor

        image = np.zeros((20, 20, 3), np.uint8)
        image[::2, :, :] = 255
        self.assertTrue(scene_editor._has_texture(image))

    def test_empty_image_has_no_texture(self):
        """空图或 None 判为无纹理。"""
        from extensions.capabilities.scene_manager.pages import scene_editor

        self.assertFalse(scene_editor._has_texture(None))

    def test_threshold_matches_recognize_layer(self):
        """阈值与识别层同源：两处都是 1.0。"""
        import inspect

        from extensions.capabilities.scene_manager import recognize
        from extensions.capabilities.scene_manager.pages import scene_editor

        page_default = inspect.signature(scene_editor._has_texture).parameters["min_std"].default
        recognize_default = inspect.signature(recognize.template_has_texture).parameters["min_std"].default
        self.assertEqual(page_default, recognize_default)


class TestMaskRect(unittest.TestCase):
    """遮罩矩形计算（与截图遮罩能力的几何口径一致）。

    `scene_manager` 不导入遮罩能力模块（能力之间不得互相 import），因此自己实现
    同一套几何。这里逐项比对结果，保证两处口径不会漂移。
    """

    def _values(self, **overrides):
        """造一份遮罩配置。"""
        values = {
            "Enable Mask": True,
            "Mask Anchor": "bottom_right",
            "Mask Width": 13.0,
            "Mask Height": 2.5,
            "Mask Offset X": 0.0,
            "Mask Offset Y": 0.0,
        }
        values.update(overrides)
        return values

    def test_default_bottom_right(self):
        """默认右下角 13% x 2.5%：换算用截断，与上游 int(0.13 * width) 同源。"""
        rect = runtime.resolve_mask_rect(self._values(), 1920, 1080)
        self.assertEqual(rect, (1920 - 249, 1080 - 27, 1920, 1080))

    def test_top_left(self):
        """左上锚点从原点开始。"""
        rect = runtime.resolve_mask_rect(self._values(**{"Mask Anchor": "top_left"}), 1920, 1080)
        self.assertEqual(rect, (0, 0, 249, 27))

    def test_offset_moves_inward(self):
        """偏移向画面内部移动。"""
        rect = runtime.resolve_mask_rect(self._values(**{"Mask Offset X": 10.0, "Mask Offset Y": 1.0}), 1920, 1080)
        # 贴右边：x2 减去偏移；贴下边：y2 减去偏移
        self.assertEqual(rect[2], 1920 - int(10.0 * 1920 / 100))
        self.assertEqual(rect[3], 1080 - int(1.0 * 1080 / 100))

    def test_invalid_cases_return_none(self):
        """未知锚点、零尺寸、非法分辨率都返回 None。"""
        self.assertIsNone(runtime.resolve_mask_rect(self._values(**{"Mask Anchor": "center"}), 1920, 1080))
        self.assertIsNone(runtime.resolve_mask_rect(self._values(**{"Mask Width": 0.0}), 1920, 1080))
        self.assertIsNone(runtime.resolve_mask_rect(self._values(), 0, 1080))

    def test_matches_screenshot_mask_geometry(self):
        """与截图遮罩能力的几何层逐值一致。

        这条用例是防漂移的关键：两处各写一份几何，一旦口径不同（例如一处
        round、一处 int），交集检测就会与实际遮罩差几像素。遮罩能力存在时才跑。
        """
        try:
            from extensions.capabilities.screenshot_mask import geometry
        except ModuleNotFoundError:
            self.skipTest("截图遮罩能力不可用")
        values = self._values()
        for size in ((1920, 1080), (2560, 1440), (1280, 720)):
            with self.subTest(size=size):
                self.assertEqual(
                    runtime.resolve_mask_rect(values, *size),
                    geometry.resolve_rect(values, *size),
                )


class TestDraftLibrarySeparation(unittest.TestCase):
    """探索草稿写**编辑区**，识别读**产物区**，两个库不能混。

    **这是「探索跑完草稿页还是没有数据」的根因**。草稿页读编辑区、`promote_draft`
    也在同一个库内搬正；而探索原先写产物区，两份库都在正常读写、**没有任何报错**，
    只有「草稿页永远空着」这一个症状。

    反过来也不行：让识别读编辑区，运行期的产物库就白导出了。
    """

    def _bridge(self):
        """造一个库分离的 TaskBridge。

        Returns:
            `(bridge, output_library, edit_library)` 三元组。
        """
        import os
        import shutil
        import tempfile

        from extensions.capabilities.scene_manager import runtime, store

        temp_dir = tempfile.mkdtemp(prefix="draft-lib-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        output = store.SceneLibrary(os.path.join(temp_dir, "assets"))
        output.ensure_layout()
        output.set_base_resolution(640, 360)
        edit = store.SceneLibrary(os.path.join(temp_dir, "edit"))
        edit.ensure_layout()
        edit.set_base_resolution(640, 360)

        class _Task:
            """任务桩。"""

            def click(self, *_args, **_kwargs):
                """不做任何事。"""

            def ocr(self, frame=None, **_kwargs):
                """识别不到文字。"""
                return []

        bridge = runtime.TaskBridge(_Task(), output, draft_library=edit)
        return bridge, output, edit

    def test_draft_goes_to_edit_library(self):
        """草稿落进编辑区，产物区目录里看不到它。"""
        from extensions.capabilities.scene_manager import model

        bridge, output, edit = self._bridge()
        draft = model.Scene(scene_id="draft-0001", name="探索发现")
        bridge.save_draft(draft, None, None)

        self.assertIsNotNone(edit.load_scene("draft-0001"), "草稿应落在编辑区（草稿页读这里）")
        self.assertIsNone(output.load_scene("draft-0001"), "产物区不该出现草稿")

    def test_drafts_visible_to_drafts_page_source(self):
        """`load_drafts()` 能读到刚落的草稿——草稿页正是用它。"""
        from extensions.capabilities.scene_manager import model

        bridge, _output, edit = self._bridge()
        bridge.save_draft(model.Scene(scene_id="draft-0002", name="发现"), None, None)

        drafts, failures = edit.load_drafts()
        self.assertEqual(failures, [])
        self.assertEqual([draft.scene_id for draft in drafts], ["draft-0002"])

    def test_explore_scenes_merge_formal_and_drafts(self):
        """探索的识别集含「产物区正式场景 + 编辑区草稿」。

        只读一边的话：认不出正式场景则探索一开局就「当前画面无法识别」；
        认不出草稿则点进去就再也回不来。
        """
        from extensions.capabilities.scene_manager import model, shapes

        bridge, output, edit = self._bridge()
        formal = model.Scene(scene_id="scene-0001", name="正式")
        formal.features.append(model.Feature(name="f", shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3)))
        output.save_scene(formal)
        edit.save_scene(model.Scene(scene_id="draft-0001", name="草稿"), as_draft=True)

        merged = bridge._explore_scenes([formal])
        ids = [scene.scene_id for scene in merged]
        self.assertIn("scene-0001", ids)
        self.assertIn("draft-0001", ids)

    def test_explored_edge_saved_to_edit_library(self):
        """探索边落进编辑区（流转页读的就是这份），不写产物区。

        写产物区时探索报告显示「采集 N 条边」，流转页表格却是空的——确认按钮
        永远提示「请先选中一条边」，而两份库都在正常读写、没有任何报错。
        """
        from extensions.capabilities.scene_manager import model, shapes

        bridge, output, edit = self._bridge()
        scene = model.Scene(scene_id="scene-0001", name="正式")
        scene.features.append(model.Feature(name="f", shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3)))
        scene.areas.append(model.Area(name="开始", rect=(0.1, 0.1, 0.2, 0.2)))
        output.save_scene(scene)
        edit.save_scene(scene)

        bridge.save_edge("scene-0001", "开始", "scene-0002", 1)

        edit.load_routes()
        output.load_routes()
        route = edit.routes.get("scene-0001", "开始")
        self.assertIsNotNone(route, "编辑区应有这条边（流转页从这里读）")
        self.assertEqual(route.source, model.SOURCE_EXPLORED)
        self.assertIsNone(output.routes.get("scene-0001", "开始"), "产物区不该被探索直接写入")

    def test_merged_routes_puts_edit_over_product(self):
        """探索读的路由表是两个库的合并，编辑区优先。

        只读产物区会把「刚应用、还没导出」的边当成不存在，探索会重复探测并在
        缺边的图上找路；只读编辑区又看不到上一次导出的历史结果。
        """
        from extensions.capabilities.scene_manager import model

        bridge, output, edit = self._bridge()
        output.set_route("scene-0001", "开始", "scene-0002", source=model.SOURCE_CONFIRMED)
        edit.set_route("scene-0001", "开始", "scene-0003", source=model.SOURCE_MANUAL)

        merged = bridge._merged_routes()

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged.target_of("scene-0001", "开始"), "scene-0003", "同键时编辑区覆盖产物区")


class TestExploreRestart(unittest.TestCase):
    """探索过程中的目标重启与启动失败重试。

    重启的用途是**重置目标程序状态**、让探索接着当前进度走，而不是把整个任务
    推倒重跑。判定必须严格：中途只在卡于草稿/未识别画面时重启；启动失败与异常
    重跑一轮；正常完成、达到上限、用户取消都不重启；开关关闭时不注入 hook。
    """

    def _task(self, results, meta):
        """造一个只带探索所需依赖的任务实例。

        Args：
            results：`explorer.run` 依次返回的结果或抛出的异常。
            meta：`model.LibraryMeta` 实例。

        Returns：
            `(task, bridge, explorer, reported)` 四元组。
        """
        task = object.__new__(tasks_module.SceneExploreTask)
        task.config = {
            "Max New Scenes": 30,
            "Max Seconds": 600,
            "Max Clicks Per Scene": 20,
        }
        task.log_info = lambda *args, **kwargs: None
        task.log_warning = lambda *args, **kwargs: None
        task.log_error = lambda *args, **kwargs: None
        task.info_set = lambda *args, **kwargs: None
        task.progress = {
            "visited": 0,
            "new_scenes": 0,
            "edges": 0,
            "elapsed": 0.0,
            "scene": "",
            "event": "",
            "running": False,
        }
        task.cancel_requested = False
        task._seen_scenes = set()
        task.edit_library = mock.Mock()
        reported = []
        task._report = lambda result: reported.append(result)

        bridge = mock.Mock()
        bridge.draft_library.load_meta.return_value = meta
        bridge.should_stop.return_value = False
        explorer = mock.Mock()
        explorer.run.side_effect = results
        bridge.build_explorer.return_value = explorer
        # 目标路径只在第一次重启前解析一次：给一个稳定的假目标，避免测试
        # 依赖本机真实窗口（那会让「首选设备」随前台窗口变化）。
        status_patch = mock.patch.object(
            tasks_module.runtime,
            "target_status",
            return_value={
                "device": "windows",
                "path": "C:/fake/game.exe",
                "connected": True,
                "supported": True,
                "reason": "",
            },
        )
        status_patch.start()
        self.addCleanup(status_patch.stop)
        bound_patch = mock.patch.object(tasks_module.runtime, "bound_target_path", return_value="")
        bound_patch.start()
        self.addCleanup(bound_patch.stop)
        return task, bridge, explorer, reported

    def test_no_start_restarts_and_retries(self):
        """no_start_scene 会重启一次并重跑，最终结果带重启次数。"""
        meta = model.LibraryMeta(restart_on_incomplete=True, max_restarts=1, restart_wait=1)
        first = explore.ExploreResult(stop_reason=explore.STOP_NO_START)
        second = explore.ExploreResult(stop_reason=explore.STOP_COMPLETED)
        task, bridge, explorer, reported = self._task([first, second], meta)
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
        ):
            result = task.explore()

        self.assertIs(result, second)
        self.assertEqual(result.restarts, 1)
        self.assertEqual(restart.call_count, 1)
        self.assertEqual(explorer.run.call_count, 2)
        self.assertIs(reported[-1], second)

    def test_incomplete_does_not_rerun_whole_exploration(self):
        """收尾 incomplete 不重跑整轮：中途恢复由 restart hook 负责，重跑只会重复劳动。"""
        meta = model.LibraryMeta(restart_on_incomplete=True, max_restarts=1, restart_wait=1)
        first = explore.ExploreResult(stop_reason=explore.STOP_INCOMPLETE)
        task, bridge, explorer, reported = self._task([first], meta)
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
        ):
            result = task.explore()

        self.assertIs(result, first)
        self.assertEqual(result.restarts, 0)
        self.assertEqual(restart.call_count, 0)
        self.assertEqual(explorer.run.call_count, 1)
        self.assertIs(reported[-1], first)

    def test_pins_bound_target_over_selected_device(self):
        """重启优先锁定「当前绑定窗口」的 exe，而不是可能已回落的选中设备。"""
        meta = model.LibraryMeta(restart_on_incomplete=True, max_restarts=1, restart_wait=1)
        first = explore.ExploreResult(stop_reason=explore.STOP_NO_START)
        second = explore.ExploreResult(stop_reason=explore.STOP_COMPLETED)
        task, bridge, _explorer, _reported = self._task([first, second], meta)
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "bound_target_path", return_value="C:/bound.exe"),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
        ):
            task.explore()
        restart.assert_called_once()
        self.assertEqual(restart.call_args.args[2], "C:/bound.exe")

    def test_restart_hook_respects_budget(self):
        """开关打开时注入 hook；hook 重启一次后用尽额度，第二次返回 False。"""
        meta = model.LibraryMeta(restart_on_incomplete=True, max_restarts=1, restart_wait=1)
        task, bridge, _explorer, _reported = self._task(
            [explore.ExploreResult(stop_reason=explore.STOP_COMPLETED)], meta
        )
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
        ):
            task.explore()
            hook = bridge.build_explorer.call_args.kwargs["restart_target"]
            self.assertIsNotNone(hook, "开关打开时应注入重启 hook")
            self.assertTrue(hook())
            self.assertFalse(hook(), "额度用尽后不应再次重启")
        self.assertEqual(restart.call_count, 1)

    def test_restart_hook_absent_when_disabled(self):
        """开关关闭时不注入 hook，探索器按原逻辑跳过卡住的场景。"""
        meta = model.LibraryMeta(restart_on_incomplete=False, max_restarts=2)
        task, bridge, _explorer, _reported = self._task(
            [explore.ExploreResult(stop_reason=explore.STOP_COMPLETED)], meta
        )
        with mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge):
            task.explore()
        self.assertIsNone(bridge.build_explorer.call_args.kwargs["restart_target"])

    def test_restart_hook_skips_when_cancelled(self):
        """已请求中断时 hook 直接返回 False，不再重启目标。"""
        meta = model.LibraryMeta(restart_on_incomplete=True, max_restarts=2)
        task, bridge, _explorer, _reported = self._task(
            [explore.ExploreResult(stop_reason=explore.STOP_COMPLETED)], meta
        )
        bridge.should_stop.return_value = True
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
        ):
            task.explore()
            hook = bridge.build_explorer.call_args.kwargs["restart_target"]
            self.assertFalse(hook())
        self.assertEqual(restart.call_count, 0)

    def test_exception_after_cancel_returns_cancelled(self):
        """异常发生前用户已点中断：按取消收尾，不重启也不把异常抛给执行器。"""
        meta = model.LibraryMeta(restart_on_incomplete=True, max_restarts=2)
        task, bridge, _explorer, reported = self._task([RuntimeError("取帧失败")], meta)
        bridge.should_stop.return_value = True
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
        ):
            result = task.explore()
        self.assertEqual(result.stop_reason, explore.STOP_CANCELLED)
        self.assertEqual(restart.call_count, 0)
        self.assertIs(reported[-1], result)

    def test_completed_does_not_restart(self):
        """正常完成不触发重启。"""
        meta = model.LibraryMeta(restart_on_incomplete=True, max_restarts=1)
        task, bridge, _explorer, _reported = self._task(
            [explore.ExploreResult(stop_reason=explore.STOP_COMPLETED)], meta
        )
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
        ):
            result = task.explore()

        self.assertEqual(result.stop_reason, explore.STOP_COMPLETED)
        self.assertEqual(restart.call_count, 0)
        self.assertEqual(result.restarts, 0)

    def test_limit_reasons_do_not_restart(self):
        """达到上限与用户取消是正常截止，不重启。"""
        for reason in (
            explore.STOP_MAX_SCENES,
            explore.STOP_MAX_SECONDS,
            explore.STOP_MAX_CLICKS_PER_SCENE,
            explore.STOP_CANCELLED,
        ):
            with self.subTest(reason=reason):
                meta = model.LibraryMeta(restart_on_incomplete=True, max_restarts=1)
                task, bridge, _explorer, _reported = self._task([explore.ExploreResult(stop_reason=reason)], meta)
                with (
                    mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
                    mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
                ):
                    result = task.explore()
                self.assertEqual(result.stop_reason, reason)
                self.assertEqual(restart.call_count, 0)

    def test_restart_disabled_keeps_old_behavior(self):
        """开关关闭时 no_start 不重启，结果与改动前一致。"""
        meta = model.LibraryMeta(restart_on_incomplete=False, max_restarts=2)
        first = explore.ExploreResult(stop_reason=explore.STOP_NO_START)
        task, bridge, explorer, _reported = self._task([first], meta)
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
        ):
            result = task.explore()

        self.assertIs(result, first)
        self.assertEqual(result.restarts, 0)
        self.assertEqual(restart.call_count, 0)
        self.assertEqual(explorer.run.call_count, 1)

    def test_exception_restarts_when_enabled(self):
        """取帧/点击异常按未完成处理：开启重启时重试一次。"""
        meta = model.LibraryMeta(restart_on_incomplete=True, max_restarts=1, restart_wait=1)
        second = explore.ExploreResult(stop_reason=explore.STOP_COMPLETED)
        task, bridge, explorer, _reported = self._task([RuntimeError("取帧失败"), second], meta)
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
        ):
            result = task.explore()

        self.assertIs(result, second)
        self.assertEqual(result.restarts, 1)
        self.assertEqual(restart.call_count, 1)
        self.assertEqual(explorer.run.call_count, 2)

    def test_exception_raises_when_disabled(self):
        """开关关闭时异常照旧抛给执行器。"""
        meta = model.LibraryMeta(restart_on_incomplete=False)
        task, bridge, _explorer, _reported = self._task([RuntimeError("取帧失败")], meta)
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")) as restart,
            self.assertRaises(RuntimeError),
        ):
            task.explore()
        self.assertEqual(restart.call_count, 0)

    def test_auto_launch_called_once(self):
        """自动启动只在前一轮尝试一次，重试不重复拉起。"""
        meta = model.LibraryMeta(auto_launch=True, restart_on_incomplete=True, max_restarts=1)
        first = explore.ExploreResult(stop_reason=explore.STOP_NO_START)
        second = explore.ExploreResult(stop_reason=explore.STOP_COMPLETED)
        task, bridge, _explorer, _reported = self._task([first, second], meta)
        with (
            mock.patch.object(tasks_module.runtime, "TaskBridge", return_value=bridge),
            mock.patch.object(tasks_module.runtime, "launch_target", return_value=(True, "")) as launch,
            mock.patch.object(tasks_module.runtime, "restart_target", return_value=(True, "")),
        ):
            task.explore()
        self.assertEqual(launch.call_count, 1)


@unittest.skipIf(ok_og is None, f"缺少框架依赖：{OK_ERROR}")
class TestTargetRestart(unittest.TestCase):
    """目标探测与重启只调用框架公开接口，不自己拼杀进程/启动命令。"""

    def test_target_status_rejects_adb(self):
        """ADB 设备不支持自动重启，原因必须可读。"""
        manager = mock.Mock()
        manager.get_preferred_device.return_value = {"device": "adb", "connected": True}
        manager.get_exe_path.return_value = "C:/emulator.exe"
        with mock.patch.object(ok_og, "device_manager", manager):
            status = runtime.target_status()
        self.assertFalse(status["supported"])
        self.assertIn("Windows", status["reason"])

    def test_target_status_accepts_windows_with_path(self):
        """Windows 目标且有 exe 路径时 supported 为 True。"""
        manager = mock.Mock()
        manager.get_preferred_device.return_value = {"device": "windows", "connected": False}
        manager.get_exe_path.return_value = "C:/game.exe"
        with mock.patch.object(ok_og, "device_manager", manager):
            status = runtime.target_status()
        self.assertTrue(status["supported"])
        self.assertEqual(status["path"], "C:/game.exe")

    def test_restart_target_uses_framework_calls(self):
        """重启按「停窗口 → 兜底杀进程 → 启动 → 锁定目标窗口 → 等取帧」执行。"""
        manager = mock.Mock()
        manager.get_preferred_device.return_value = {"device": "windows", "connected": False, "imei": "pc_game"}
        manager.get_exe_path.return_value = "C:/game.exe"
        # 窗口列表里同时有别的窗口（微信）与目标：必须按 exe 选中目标，
        # 不能沿用「第一个 connected 窗口」。
        manager.device_dict = {
            "pc_wechat": {
                "imei": "pc_wechat",
                "device": "windows",
                "real_hwnd": 111,
                "full_path": "C:/Program Files/Tencent/Weixin.exe",
                "exe": "Weixin.exe",
                "width": 144,
                "height": 20,
            },
            "pc_game": {
                "imei": "pc_game",
                "device": "windows",
                "real_hwnd": 222,
                "full_path": "C:/game.exe",
                "exe": "game.exe",
                "width": 1280,
                "height": 720,
            },
        }
        manager.hwnd_window.hwnd = 222
        manager.hwnd_window.exe_full_path = "C:/game.exe"
        with (
            mock.patch.object(ok_og, "device_manager", manager),
            mock.patch.object(runtime, "_wait_process_gone", return_value=True),
            mock.patch.object(runtime, "_sleep_cancellable", return_value=True),
            mock.patch.object(runtime, "_target_arguments", return_value=None),
            mock.patch.object(runtime, "_target_start_method", return_value="start"),
            mock.patch.object(runtime.os.path, "exists", return_value=True),
            mock.patch("ok.util.process.execute", return_value=True) as execute,
            mock.patch("ok.util.process.kill_exe") as kill,
            mock.patch.object(runtime, "capture_frame", return_value=object()),
        ):
            ok, reason = runtime.restart_target(1.0)
        self.assertTrue(ok, reason)
        manager.stop_hwnd.assert_called_once()
        kill.assert_called_once_with(abs_path="C:/game.exe")
        execute.assert_called_once()
        manager.update_pc_device.assert_called()
        manager.set_preferred_device.assert_called_once_with(imei="pc_game")
        manager.do_start.assert_called()

    def test_bind_rejects_preferred_fallback(self):
        """选中项没切到候选窗口时不继续：否则 do_start 会绑到别的应用。"""
        manager = mock.Mock()
        manager.device_dict = {
            "pc_game": {
                "imei": "pc_game",
                "device": "windows",
                "real_hwnd": 222,
                "full_path": "C:/game.exe",
                "exe": "game.exe",
                "width": 1280,
                "height": 720,
            }
        }
        manager.get_preferred_device.return_value = {
            "imei": "pc_other",
            "device": "windows",
            "full_path": "C:/other.exe",
        }
        ok, reason = runtime._bind_target_window(manager, "C:/game.exe", 1.0)
        self.assertFalse(ok)
        self.assertIn("丢失", reason)
        manager.do_start.assert_not_called()

    def test_restart_skips_stop_hwnd_when_bound_to_other_app(self):
        """绑定窗口不是目标时不调 stop_hwnd：避免把上一次错误绑定的应用杀掉。"""
        manager = mock.Mock()
        manager.hwnd_window.exe_full_path = "C:/other.exe"
        with (
            mock.patch.object(ok_og, "device_manager", manager),
            mock.patch.object(runtime, "_wait_process_gone", return_value=True),
            mock.patch.object(runtime, "_sleep_cancellable", return_value=True),
            mock.patch.object(runtime, "_target_arguments", return_value=None),
            mock.patch.object(runtime, "_target_start_method", return_value="start"),
            mock.patch.object(runtime, "_bind_target_window", return_value=(False, "未找到目标窗口")),
            mock.patch.object(runtime.os.path, "exists", return_value=True),
            mock.patch("ok.util.process.execute", return_value=True),
            mock.patch("ok.util.process.kill_exe") as kill,
        ):
            ok, _reason = runtime.restart_target(0.1, target_path="C:/game.exe")
        self.assertFalse(ok)
        manager.stop_hwnd.assert_not_called()
        kill.assert_called_once_with(abs_path="C:/game.exe")

    def test_restart_target_stops_immediately_when_cancelled(self):
        """已点中断时连 kill 都不做：中断要立刻生效。"""
        manager = mock.Mock()
        with (
            mock.patch.object(ok_og, "device_manager", manager),
            mock.patch("ok.util.process.kill_exe") as kill,
        ):
            ok, reason = runtime.restart_target(1.0, should_stop=lambda: True, target_path="C:/game.exe")
        self.assertFalse(ok)
        self.assertEqual(reason, "已取消")
        kill.assert_not_called()
        manager.stop_hwnd.assert_not_called()

    def test_restart_target_prefers_pinned_path(self):
        """传了锁定路径时不再读取「当前选中设备」，避免目标被 kill 后杀错进程。"""
        manager = mock.Mock()
        manager.device_dict = {}
        with (
            mock.patch.object(ok_og, "device_manager", manager),
            mock.patch.object(runtime, "target_status") as status,
            mock.patch.object(runtime, "_wait_process_gone", return_value=True),
            mock.patch.object(runtime, "_sleep_cancellable", return_value=True),
            mock.patch.object(runtime, "_target_arguments", return_value=None),
            mock.patch.object(runtime, "_target_start_method", return_value="start"),
            mock.patch.object(runtime, "_bind_target_window", return_value=(False, "未找到目标窗口")),
            mock.patch.object(runtime.os.path, "exists", return_value=True),
            mock.patch("ok.util.process.execute", return_value=True),
            mock.patch("ok.util.process.kill_exe") as kill,
        ):
            ok, _reason = runtime.restart_target(0.1, target_path="C:/pinned.exe")
        self.assertFalse(ok, "找不到目标窗口时应返回失败")
        status.assert_not_called()
        kill.assert_called_once_with(abs_path="C:/pinned.exe")


class TestExploreProgress(unittest.TestCase):
    """探索进度快照与中断标记。"""

    def _task(self):
        """造一个只带进度字段的任务实例。"""
        task = object.__new__(tasks_module.SceneExploreTask)
        task.progress = {
            "visited": 0,
            "new_scenes": 0,
            "edges": 0,
            "elapsed": 0.0,
            "scene": "",
            "event": "",
            "running": False,
        }
        task.cancel_requested = False
        task._seen_scenes = set()
        task.log_info = lambda *args, **kwargs: None
        return task

    def test_events_accumulate_progress(self):
        """访问、新场景与边分别累加，耗时取最大值。"""
        task = self._task()
        task._on_explore_event(explore.ExploreEvent("probe", "scene-0001", "点击 区域1", 1.5))
        task._on_explore_event(explore.ExploreEvent("new_scene", "scene-0002", "发现草稿 draft-0001", 3.0))
        task._on_explore_event(explore.ExploreEvent("edge", "scene-0002", "区域1 → scene-0003", 4.0))
        task._on_explore_event(explore.ExploreEvent("warn", "scene-0001", "无法回退", 3.5))
        self.assertEqual(task.progress["visited"], 2, "按出现过的场景去重")
        self.assertEqual(task.progress["new_scenes"], 1)
        self.assertEqual(task.progress["edges"], 1)
        self.assertAlmostEqual(task.progress["elapsed"], 4.0)
        self.assertEqual(task.progress["event"], "无法回退")

    def test_cancel_requested_stops_bridge(self):
        """界面置 cancel_requested 后，桥接的 should_stop 立即为 True。"""
        task = self._task()
        bridge = object.__new__(runtime.TaskBridge)
        bridge.task = task
        with mock.patch.object(
            tasks_module.SceneExploreTask,
            "executor",
            new_callable=mock.PropertyMock,
        ) as executor:
            executor.return_value = mock.Mock(exit_event=None)
            self.assertFalse(bridge.should_stop())
            task.cancel_requested = True
            self.assertTrue(bridge.should_stop())


if __name__ == "__main__":
    unittest.main()
