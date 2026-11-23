# -*- coding: utf-8 -*-
"""场景管理的数据模型与场景库读写测试（批次一）。

被测对象是 `extensions/capabilities/scene_manager/model.py` 与 `store.py`。这两层
**不依赖 ok 与 PySide6**，因此在任何环境都能完整运行——它们承载全部持久化契约，
一旦出错表现为「保存后重开某项没了」，不会报错。

分三组：

1. **模型组**：字段校验、相对比例规整、必需/可选特征排序、边来源语义。
2. **读写组**：JSON 往返一致、损坏文件跳过、预览图缺失降级、草稿区隔离。
3. **导入导出组**：zip 往返一致、清单校验。

图片相关用例需要 cv2；缺失时按类跳过，并在跳过原因里写明缺的是哪个依赖。
"""

import os
import shutil
import tempfile
import unittest

from extensions.capabilities.scene_manager import model, routes, shapes, store

try:
    import cv2
    import numpy as np

    CV2_ERROR = None
except ModuleNotFoundError as exc:  # 缺图像依赖
    cv2 = None
    np = None
    CV2_ERROR = str(exc)


def _feature(name="f1", **kwargs):
    """构造一个合法特征，供各用例复用。"""
    payload = {
        "name": name,
        "kind": model.KIND_TEMPLATE,
        "mandatory": True,
        "shape": shapes.Shape.rectangle(0.1, 0.1, 0.5, 0.4),
        "threshold": 0.85,
    }
    payload.update(kwargs)
    if payload.get("kind") == model.KIND_OCR and not payload.get("text"):
        payload["text"] = "确认"
    return model.Feature(**payload)


def _area(name="a1", **kwargs):
    """构造一个合法区域，供各用例复用。

    区域**不再承载跳转信息**（目标场景、等待、边来源都在路由表里），因此
    这里只填形状与探索相关字段。传旧字段会直接 TypeError——这正是我们要的：
    测试也要跟着数据模型走，否则改完模型测试还能通过，就说明测试没测到真东西。
    """
    payload = {
        "name": name,
        "rect": (0.2, 0.3, 0.4, 0.5),
        "explore_probe": True,
    }
    payload.update(kwargs)
    return model.Area(**payload)


def _routes(entries):
    """按 `[(from, area, to, source)]` 造一张路由表。

    Args：
        entries：四元组列表；`source` 省略时用 `manual`。

    Returns：
        `routes.RouteTable` 实例。
    """
    table = routes.RouteTable()
    for entry in entries:
        from_scene, area_name, to_scene = entry[:3]
        source = entry[3] if len(entry) > 3 else model.SOURCE_MANUAL
        table.put(routes.Route(from_scene, area_name, to_scene, source=source))
    return table


def _scene(scene_id="scene-0001", **kwargs):
    """构造一个合法场景，供各用例复用。"""
    payload = {
        "scene_id": scene_id,
        "name": "主界面",
        "features": [_feature()],
        "areas": [_area()],
    }
    payload.update(kwargs)
    return model.Scene(**payload)


class TestSceneManagerModel(unittest.TestCase):
    """模型层：字段校验与语义。"""

    def test_rect_is_rounded_and_validated(self):
        """相对比例被规整到固定小数位，越界与零面积被拒绝。"""
        self.assertEqual(
            model.normalize_rect((0.12345678, 0.2, 0.9, 0.95)),
            (0.123457, 0.2, 0.9, 0.95),
        )
        for bad in [(0.0, 0.0, 0.0, 0.5), (0.5, 0.0, 0.5, 1.0), (-0.1, 0, 0.5, 0.5), (0, 0, 1.5, 1), (0.1, 0.2, 0.3)]:
            with self.subTest(bad=bad):
                with self.assertRaises(model.SceneModelError):
                    model.normalize_rect(bad)

    def test_scene_id_pattern_rejects_arbitrary_names(self):
        """场景 id 必须是 scene-四位数，避免用显示名当主键。"""
        with self.assertRaises(model.SceneModelError):
            model.Scene(scene_id="主界面")
        with self.assertRaises(model.SceneModelError):
            model.Scene(scene_id="scene-1")
        self.assertEqual(model.Scene(scene_id="scene-0001").scene_id, "scene-0001")
        self.assertEqual(model.Scene(scene_id="scene-12345").scene_id, "scene-12345")

    def test_name_defaults_to_id(self):
        """显示名留空时用 id 占位，保证界面总有可显示文本。"""
        self.assertEqual(model.Scene(scene_id="scene-0007").name, "scene-0007")

    def test_duplicate_names_rejected(self):
        """同一场景内特征名与区域名必须唯一，否则模板文件会互相覆盖。"""
        with self.assertRaises(model.SceneModelError):
            _scene(features=[_feature("same"), _feature("same")])
        with self.assertRaises(model.SceneModelError):
            _scene(areas=[_area("same"), _area("same")])

    def test_ocr_feature_requires_text(self):
        """OCR 特征没有期望文本就无法判定，构造时报错。"""
        with self.assertRaises(model.SceneModelError):
            model.Feature(name="t", kind=model.KIND_OCR, text="")

    def test_template_feature_gets_default_template_path(self):
        """模板特征的模板文件路径由特征名推导，不需要用户填。"""
        self.assertEqual(_feature("btn").template_file, "templates/btn.png")

    def test_threshold_range_enforced(self):
        """阈值必须落在 (0, 1]，0 会被框架当成「用默认值」，语义冲突。"""
        with self.assertRaises(model.SceneModelError):
            _feature(threshold=0.0)
        with self.assertRaises(model.SceneModelError):
            _feature(threshold=1.5)

    def test_mandatory_features_sorted_by_name(self):
        """必需特征按名称排序，保证每次识别顺序一致；可选特征不参与。"""
        beta = _feature("beta")
        alpha = _feature("alpha")
        optional = _feature("gamma", mandatory=False)
        scene = _scene(features=[beta, optional, alpha])
        self.assertEqual(
            [item.name for item in scene.mandatory_features()],
            ["alpha", "beta"],
        )

    def test_area_carries_no_routing_fields(self):
        """区域**不得**带任何跳转字段。

        这是本次结构调整的核心约束：场景只描述「这个画面长什么样」，跳转关系
        存在库根的 `routes.json`。把目标场景/等待/边来源留在区域上，同一份事实
        就有两处落点，改一处漏一处就分叉——而分叉的表现是「流转图上有边，点下去
        到不了」，不报错。
        """
        area = _area("btn")
        for field in ("target_scene", "after_wait", "edge_source", "observations"):
            self.assertFalse(hasattr(area, field), f"区域不该再有 {field} 字段")
        payload = area.to_dict()
        for field in ("target_scene", "after_wait", "edge_source", "observations"):
            self.assertNotIn(field, payload, f"序列化里不该再有 {field}")

    def test_route_routable_respects_source(self):
        """只有 manual 与 confirmed 的路由参与寻路，explored 必须被排除。"""
        table = _routes(
            [
                ("scene-0001", "m", "scene-0002", model.SOURCE_MANUAL),
                ("scene-0001", "e", "scene-0002", model.SOURCE_EXPLORED),
                ("scene-0001", "c", "scene-0002", model.SOURCE_CONFIRMED),
            ]
        )
        self.assertTrue(table.get("scene-0001", "m").routable())
        self.assertFalse(table.get("scene-0001", "e").routable())
        self.assertTrue(table.get("scene-0001", "c").routable())
        self.assertEqual(len(table.routable_routes()), 2)

    def test_area_has_no_action_field(self):
        """区域不记录动作类型。

        区域后续可能承担单击、双击、长按、拖拽、滚动等多种操作，具体做什么由使用方
        决定。一旦模型里出现 `action` 字段，场景管理就被迫枚举动作种类，新增一种
        动作就要改场景库结构——这里把「不带动作字段」固化成断言，防止后来者顺手加上。
        """
        area = _area("btn")
        self.assertFalse(hasattr(area, "action"))
        self.assertNotIn("action", area.to_dict())

    def test_area_probe_flag_roundtrips(self):
        """探索探测标记能被序列化并还原。"""
        area = _area("btn", explore_probe=True)
        restored = model.Area.from_dict(area.to_dict())
        self.assertTrue(restored.explore_probe)
        self.assertFalse(_area("other", explore_probe=False).explore_probe)

    def test_explore_return_areas(self):
        """探索返回标记可以标多个，按场景内顺序返回。"""
        scene = _scene(areas=[_area("go"), _area("back", explore_return=True), _area("close", explore_return=True)])
        self.assertEqual([item.name for item in scene.explore_return_areas()], ["back", "close"])
        self.assertEqual(_scene(areas=[_area("go")]).explore_return_areas(), [])

    def test_legacy_return_area_migrates_on_read(self):
        """旧库的场景级 `return_area` 读进来就变成区域的探索返回标记。"""
        payload = _scene(areas=[_area("back"), _area("go")]).to_dict()
        payload["return_area"] = "back"
        restored = model.Scene.from_dict(payload)
        self.assertTrue(restored.find_area("back").explore_return)
        self.assertFalse(restored.find_area("go").explore_return)
        self.assertNotIn("return_area", restored.to_dict(), "新写出的场景不再带旧字段")

    def test_scene_roundtrip_in_memory(self):
        """场景 → 字典 → 场景，逐字段相等。"""
        original = _scene(
            features=[_feature("tpl"), _feature("txt", kind=model.KIND_OCR, text="开始")],
            areas=[_area("btn", explore_return=True)],
        )
        restored = model.Scene.from_dict(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())
        self.assertEqual(restored.scene_id, original.scene_id)
        self.assertEqual(len(restored.features), 2)
        self.assertTrue(restored.find_area("btn").explore_return)

    def test_future_schema_version_rejected(self):
        """库结构版本高于本程序时明确拒绝，而不是按旧结构误读。"""
        payload = _scene().to_dict()
        payload["schema_version"] = model.SCHEMA_VERSION + 1
        with self.assertRaises(model.SceneModelError):
            model.Scene.from_dict(payload)

    def test_library_meta_defaults_and_ratio(self):
        """库元数据默认无分辨率；记录后能算出宽高比。"""
        meta = model.LibraryMeta()
        self.assertFalse(meta.has_resolution())
        self.assertEqual(meta.aspect_ratio(), 0.0)
        meta = model.LibraryMeta(base_width=1920, base_height=1080)
        self.assertTrue(meta.has_resolution())
        self.assertAlmostEqual(meta.aspect_ratio(), 16 / 9, places=6)

    def test_library_meta_search_order_roundtrip(self):
        """探索顺序随库保存，未知值退回广度优先。

        它只影响展开顺序，取值坏了不值得让整库读不出来；退回默认即可。
        """
        meta = model.LibraryMeta(search_order=model.SEARCH_DFS)
        self.assertEqual(meta.to_dict()["search_order"], model.SEARCH_DFS)
        restored = model.LibraryMeta.from_dict(meta.to_dict())
        self.assertEqual(restored.search_order, model.SEARCH_DFS)
        self.assertEqual(model.LibraryMeta(search_order="unknown").search_order, model.SEARCH_BFS)

    def test_library_meta_auto_save_roundtrip(self):
        """自动保存偏好随库元数据保存；旧库缺字段时默认关闭。"""
        meta = model.LibraryMeta(auto_save=True)
        self.assertTrue(meta.to_dict()["auto_save"])
        self.assertTrue(model.LibraryMeta.from_dict(meta.to_dict()).auto_save)
        self.assertFalse(model.LibraryMeta.from_dict({}).auto_save)

    def test_library_meta_name_prefix_roundtrip(self):
        """名称带场景前缀偏好随库元数据保存；旧库缺字段时默认关闭。"""
        meta = model.LibraryMeta(name_with_scene_prefix=True)
        self.assertTrue(meta.to_dict()["name_with_scene_prefix"])
        self.assertTrue(model.LibraryMeta.from_dict(meta.to_dict()).name_with_scene_prefix)
        self.assertFalse(model.LibraryMeta.from_dict({}).name_with_scene_prefix)

    def test_library_meta_restart_fields_roundtrip(self):
        """目标启动与重启设置随库保存；旧库缺字段时全部取默认值。"""
        meta = model.LibraryMeta(
            auto_launch=True,
            restart_on_incomplete=True,
            max_restarts=2,
            restart_wait=12.5,
        )
        data = meta.to_dict()
        self.assertTrue(data["auto_launch"])
        self.assertTrue(data["restart_on_incomplete"])
        self.assertEqual(data["max_restarts"], 2)
        restored = model.LibraryMeta.from_dict(data)
        self.assertTrue(restored.auto_launch)
        self.assertTrue(restored.restart_on_incomplete)
        self.assertEqual(restored.max_restarts, 2)
        self.assertAlmostEqual(restored.restart_wait, 12.5)
        defaults = model.LibraryMeta.from_dict({})
        self.assertFalse(defaults.auto_launch)
        self.assertFalse(defaults.restart_on_incomplete)
        self.assertEqual(defaults.max_restarts, 1)
        self.assertAlmostEqual(defaults.restart_wait, 8.0)

    def test_library_meta_restart_fields_are_clamped(self):
        """越界值被夹到安全范围：重启次数 0–3，等待 1–60 秒。"""
        meta = model.LibraryMeta(max_restarts=9, restart_wait=0.1)
        self.assertEqual(meta.max_restarts, 3)
        self.assertAlmostEqual(meta.restart_wait, 1.0)
        meta = model.LibraryMeta(max_restarts=-2, restart_wait=120.0)
        self.assertEqual(meta.max_restarts, 0)
        self.assertAlmostEqual(meta.restart_wait, 60.0)


class TestExploreAction(unittest.TestCase):
    """区域探索动作与参数。

    探索原先固定单击。碰到滑动列表、需要长按蓄力的按钮、右键菜单时，单击要么画面
    不变化（被判「无效区域」，连两次就永久跳过），要么触发另一个功能（写出的边是
    错的）。两种失败都不报错，所以动作必须可配。
    """

    def test_default_is_click(self):
        """旧库没有该字段时默认单击——正是它原本的行为。"""
        area = model.Area.from_dict({"name": "a", "rect": [0.1, 0.1, 0.2, 0.2]})
        self.assertEqual(area.explore_action, model.ACTION_CLICK)
        self.assertEqual(area.explore_params, {})

    def test_every_action_accepted(self):
        """五种动作都能构造。"""
        for action in model.EXPLORE_ACTIONS:
            area = _area(explore_action=action)
            self.assertEqual(area.explore_action, action)

    def test_unknown_action_rejected(self):
        """未知动作明确报错，不静默当成单击。"""
        with self.assertRaises(model.SceneModelError):
            _area(explore_action="fly")

    def test_default_params_filled(self):
        """不传参数时按动作补默认值。"""
        self.assertEqual(_area(explore_action=model.ACTION_LONG_PRESS).explore_params, {"duration": 1.0})
        self.assertEqual(
            _area(explore_action=model.ACTION_SWIPE).explore_params,
            {"distance": 0.3, "direction": model.SWIPE_UP},
        )
        self.assertEqual(_area(explore_action=model.ACTION_DOUBLE_CLICK).explore_params, {"interval": 0.08})

    def test_actions_without_params(self):
        """单击与右键没有参数。"""
        for action in (model.ACTION_CLICK, model.ACTION_RIGHT_CLICK):
            self.assertEqual(_area(explore_action=action).explore_params, {})

    def test_param_range_enforced(self):
        """数值参数超出声明范围时报错。"""
        with self.assertRaises(model.SceneModelError):
            _area(explore_action=model.ACTION_LONG_PRESS, explore_params={"duration": 99})
        with self.assertRaises(model.SceneModelError):
            _area(explore_action=model.ACTION_SWIPE, explore_params={"distance": 2.0})

    def test_param_must_be_number(self):
        """数值参数拿到非数字时报错。"""
        with self.assertRaises(model.SceneModelError):
            _area(explore_action=model.ACTION_LONG_PRESS, explore_params={"duration": "很久"})

    def test_direction_validated(self):
        """滑动方向只接受四个值。"""
        area = _area(explore_action=model.ACTION_SWIPE, explore_params={"direction": model.SWIPE_LEFT})
        self.assertEqual(area.explore_params["direction"], model.SWIPE_LEFT)
        with self.assertRaises(model.SceneModelError):
            _area(explore_action=model.ACTION_SWIPE, explore_params={"direction": "斜着"})

    def test_stale_params_dropped_on_action_switch(self):
        """切换动作时不属于该动作的旧参数被丢弃。

        长按的 `duration` 若留在滑动区域上，会跟着落盘，下次读出来看着像「滑动
        也有个 duration」，而实际不生效。
        """
        area = _area(
            explore_action=model.ACTION_SWIPE,
            explore_params={"direction": "left", "distance": 0.5, "duration": 2.0},
        )
        self.assertNotIn("duration", area.explore_params)
        self.assertEqual(area.explore_params, {"direction": "left", "distance": 0.5})

    def test_serialization_roundtrip(self):
        """动作与参数能原样存取。"""
        area = _area(
            explore_action=model.ACTION_SWIPE,
            explore_params={"direction": model.SWIPE_DOWN, "distance": 0.45},
        )
        restored = model.Area.from_dict(area.to_dict())
        self.assertEqual(restored.explore_action, model.ACTION_SWIPE)
        self.assertEqual(restored.explore_params, area.explore_params)
        self.assertEqual(restored.to_dict(), area.to_dict())

    def test_param_spec_helps_ui(self):
        """参数规格能被界面直接用来生成控件。"""
        numeric, enum = model.action_param_spec(model.ACTION_SWIPE)
        self.assertIn("distance", numeric)
        self.assertIn("direction", enum)
        empty_numeric, empty_enum = model.action_param_spec(model.ACTION_CLICK)
        self.assertEqual((empty_numeric, empty_enum), ({}, {}))


class TestSceneManagerStore(unittest.TestCase):
    """场景库读写。"""

    def setUp(self):
        """为每个用例建一个独立临时库目录。"""
        self.temp_dir = tempfile.mkdtemp(prefix="scene-store-")
        self.root = os.path.join(self.temp_dir, "scenes")
        self.library = store.SceneLibrary(self.root)

    def tearDown(self):
        """删除临时目录。"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ensure_layout_creates_skeleton(self):
        """首次初始化建出库文件、README 与草稿区，重复调用幂等。"""
        self.assertTrue(self.library.ensure_layout())
        self.assertTrue(os.path.isfile(self.library.library_path))
        self.assertTrue(os.path.isfile(os.path.join(self.root, store.README_FILE)))
        self.assertTrue(os.path.isdir(self.library.drafts_root))
        self.assertFalse(self.library.ensure_layout())

    def test_roundtrip_full_scene(self):
        """存 → 读 → 再存，JSON 逐字段相等（含嵌套特征与区域）。"""
        self.library.ensure_layout()
        scene = _scene(
            features=[
                _feature("tpl", shape=shapes.Shape.rectangle(0.05, 0.06, 0.35, 0.2), threshold=0.9),
                _feature("txt", kind=model.KIND_OCR, text="确定", threshold=0.6),
            ],
            areas=[
                _area("go", rect=(0.7, 0.8, 0.9, 0.95)),
                _area("close", rect=(0.9, 0.05, 0.99, 0.12)),
            ],
        )
        self.library.save_scene(scene)
        loaded = self.library.load_scene("scene-0001")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.to_dict(), scene.to_dict())

    def test_next_ids_increment(self):
        """新 id 取现有最大值加一，删除后不复用。"""
        self.library.ensure_layout()
        self.assertEqual(self.library.next_scene_id(), "scene-0001")
        self.library.save_scene(_scene("scene-0001"))
        self.library.save_scene(_scene("scene-0005"))
        self.assertEqual(self.library.next_scene_id(), "scene-0006")
        self.library.delete_scene("scene-0005")
        self.assertEqual(self.library.next_scene_id(), "scene-0006")

    def test_load_skips_missing_dir(self):
        """目录被手工删掉后，列出与加载都不抛异常。"""
        self.library.ensure_layout()
        self.library.save_scene(_scene("scene-0001"))
        shutil.rmtree(self.library.scene_dir("scene-0001"))
        self.assertEqual(self.library.list_scene_ids(), [])
        self.assertIsNone(self.library.load_scene("scene-0001"))
        scenes, failures = self.library.load_library()
        self.assertEqual(scenes, [])
        self.assertEqual(failures, [])

    def test_load_library_skips_broken_json(self):
        """一个损坏的场景只记入 failures，其余场景照常加载。"""
        self.library.ensure_layout()
        self.library.save_scene(_scene("scene-0001"))
        broken = self.library.scene_dir("scene-0002")
        os.makedirs(broken, exist_ok=True)
        with open(os.path.join(broken, store.SCENE_FILE), "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        scenes, failures = self.library.load_library()
        self.assertEqual([item.scene_id for item in scenes], ["scene-0001"])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0], "scene-0002")

    def test_drafts_not_loaded_as_scenes(self):
        """草稿区与正式区严格隔离：load_library 看不到草稿。"""
        self.library.ensure_layout()
        self.library.save_scene(_scene("scene-0001"))
        draft_id = self.library.next_draft_id()
        self.assertEqual(draft_id, "draft-0001")
        self.library.save_scene(_scene(draft_id), as_draft=True)
        self.assertEqual(len(self.library.load_library()[0]), 1)
        self.assertEqual(len(self.library.load_drafts()[0]), 1)
        self.assertEqual([item.scene_id for item in self.library.load_drafts()[0]], ["draft-0001"])

    def test_save_rejects_id_prefix_mismatch(self):
        """正式区不接受 draft- 前缀，草稿区不接受 scene- 前缀。"""
        self.library.ensure_layout()
        with self.assertRaises(store.SceneStoreError):
            self.library.save_scene(_scene("draft-0001"))
        with self.assertRaises(store.SceneStoreError):
            self.library.save_scene(_scene("scene-0001"), as_draft=True)

    def test_promote_draft_renumbers_and_drops_draft_edges(self):
        """草稿转正换正式 id、边升为 confirmed、指向草稿的边被改写并计数。"""
        self.library.ensure_layout()
        self.library.set_base_resolution(640, 360)
        # 先备一个正式场景，让草稿的出边有个合法目标。
        self.library.save_scene(_scene("scene-0001", name="已有"))
        draft = _scene(
            "draft-0001",
            name="探索发现",
            areas=[_area("to_draft"), _area("to_scene")],
        )
        self.library.save_scene(draft, as_draft=True)
        self.library.ensure_routes()
        self.library.routes.put(routes.Route("draft-0001", "to_draft", "draft-0002", source=model.SOURCE_EXPLORED))
        self.library.routes.put(routes.Route("draft-0001", "to_scene", "scene-0001", source=model.SOURCE_EXPLORED))
        # 还要有一条「正式场景指向草稿」的入边，转正后必须被改写而不是悬空。
        self.library.routes.put(routes.Route("scene-0001", "to_new", "draft-0001", source=model.SOURCE_EXPLORED))
        self.library.save_routes()

        promoted, dropped = self.library.promote_draft("draft-0001")
        self.assertEqual(promoted.scene_id, "scene-0002")
        self.assertEqual(promoted.name, "探索发现")
        self.assertEqual(dropped, 1, "指向其它草稿的边应被清空并计数")
        self.assertEqual(self.library.list_draft_ids(), [])
        self.assertEqual(self.library.list_scene_ids(), ["scene-0001", "scene-0002"])

        self.library.load_routes()
        table = self.library.routes
        self.assertIsNone(table.get("draft-0001", "to_draft"), "指向草稿的边必须清掉")
        self.assertEqual(
            table.get("scene-0002", "to_scene").source,
            model.SOURCE_CONFIRMED,
            "草稿的出边应升为 confirmed",
        )
        self.assertEqual(
            table.get("scene-0001", "to_new").target_scene,
            "scene-0002",
            "**指向草稿的入边必须改写到新 id**，否则就是悬空边",
        )

    def test_base_resolution_persisted(self):
        """基准分辨率落盘后能被重新读出。"""
        self.library.set_base_resolution(1920, 1080)
        reloaded = store.SceneLibrary(self.root)
        meta = reloaded.load_meta()
        self.assertEqual((meta.base_width, meta.base_height), (1920, 1080))
        with self.assertRaises(store.SceneStoreError):
            self.library.set_base_resolution(0, 1080)

    def test_export_import_roundtrip(self):
        """整库导出再导入到空库，场景数据逐字段一致。"""
        self.library.ensure_layout()
        self.library.set_base_resolution(1920, 1080)
        self.library.save_scene(_scene("scene-0001"))
        self.library.save_scene(_scene("scene-0002", name="设置"))
        self.library.save_scene(_scene("draft-0001"), as_draft=True)

        zip_path = os.path.join(self.temp_dir, "lib.zip")
        entries = self.library.export_library(zip_path)
        self.assertGreater(entries, 1)

        other_root = os.path.join(self.temp_dir, "other")
        other = store.SceneLibrary(other_root)
        count = other.import_library(zip_path)
        self.assertEqual(count, 2)
        self.assertEqual(other.list_scene_ids(), ["scene-0001", "scene-0002"])
        self.assertEqual(other.list_draft_ids(), [])
        self.assertEqual(
            other.load_scene("scene-0002").to_dict(),
            self.library.load_scene("scene-0002").to_dict(),
        )
        self.assertEqual(other.load_meta().base_width, 1920)

    def test_export_includes_drafts_on_request(self):
        """显式要求时草稿也进导出包；默认不带。"""
        self.library.ensure_layout()
        self.library.save_scene(_scene("draft-0001"), as_draft=True)
        plain = os.path.join(self.temp_dir, "plain.zip")
        with_drafts = os.path.join(self.temp_dir, "drafts.zip")
        self.library.export_library(plain)
        self.library.export_library(with_drafts, include_drafts=True)

        target_plain = store.SceneLibrary(os.path.join(self.temp_dir, "p"))
        target_drafts = store.SceneLibrary(os.path.join(self.temp_dir, "d"))
        target_plain.import_library(plain)
        target_drafts.import_library(with_drafts)
        self.assertEqual(target_plain.list_draft_ids(), [])
        self.assertEqual(target_drafts.list_draft_ids(), ["draft-0001"])

    def test_import_rejects_package_without_manifest(self):
        """缺少清单的 zip 不是有效导出包，明确拒绝而不是导入一半。"""
        import zipfile

        bad_zip = os.path.join(self.temp_dir, "bad.zip")
        with zipfile.ZipFile(bad_zip, "w") as archive:
            archive.writestr("scene-0001/scene.json", "{}")
        with self.assertRaises(store.SceneStoreError):
            self.library.import_library(bad_zip)


class TestSceneDefaultName(unittest.TestCase):
    """默认场景名：序号从 id 派生。

    历史缺陷：名称按「现有 场景N 的最大 N+1」从 1 数起，id 由库计数器分配。库里
    一旦有用户自定义名的场景，两套就会分叉——实测 `scene-0004` 被叫成「场景 1」，
    而库里已经有 `scene-0002`「靶场入口」、`scene-0003`「关卡选择」。
    """

    def test_name_follows_id_sequence(self):
        """`scene-0005` → `场景 5`。"""
        self.assertEqual(store.scene_default_name("scene-0001"), "场景 1")
        self.assertEqual(store.scene_default_name("scene-0005"), "场景 5")
        self.assertEqual(store.scene_default_name("scene-0042"), "场景 42")

    def test_draft_id_also_works(self):
        """草稿 id 同样能生成名称，序号取数字部分。"""
        self.assertEqual(store.scene_default_name("draft-0007"), "场景 7")

    def test_invalid_id_falls_back_to_id(self):
        """Id 不合格式时退回 id 本身，不返回空串。"""
        self.assertEqual(store.scene_default_name("weird"), "weird")
        self.assertEqual(store.scene_default_name(""), "")


class TestIdSequence(unittest.TestCase):
    """`model.id_sequence` 取 id 里的数字。"""

    def test_scene_and_draft(self):
        """正式与草稿 id 都能取出序号。"""
        self.assertEqual(model.id_sequence("scene-0001"), 1)
        self.assertEqual(model.id_sequence("draft-0012"), 12)

    def test_invalid_returns_zero(self):
        """不合格式返回 0，不抛异常——调用方据此判断能否生成名称。"""
        for value in ("bad", "", None, "scene-x", "scene-"):
            self.assertEqual(model.id_sequence(value), 0, f"{value!r} 应返回 0")


class TestStoreNewSceneName(unittest.TestCase):
    """`new_scene` 的命名。"""

    def setUp(self):
        """建临时库并把 id 计数器推到 3。"""
        self.temp_dir = tempfile.mkdtemp(prefix="scene-name-")
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.library = store.SceneLibrary(os.path.join(self.temp_dir, "scenes"))
        self.library.ensure_layout()
        for index in range(1, 3):
            scene = self.library.new_scene(f"自定义 {index}")
            self.library.save_scene(scene)

    def test_name_matches_id(self):
        """名称序号与 id 序号一致，即使已有场景都是自定义名。"""
        scene = self.library.new_scene()
        self.assertEqual(scene.scene_id, "scene-0003")
        self.assertEqual(scene.name, "场景 3")

    def test_explicit_name_kept(self):
        """显式传名时不套用默认规则。"""
        scene = self.library.new_scene("靶场入口")
        self.assertEqual(scene.name, "靶场入口")


@unittest.skipIf(cv2 is None, f"缺少图像依赖：{CV2_ERROR}")
class TestSceneManagerStoreAssets(unittest.TestCase):
    """场景目录里的图片资产：预览图与模板图。"""

    def setUp(self):
        """建临时库与一张测试图。"""
        self.temp_dir = tempfile.mkdtemp(prefix="scene-assets-")
        self.library = store.SceneLibrary(os.path.join(self.temp_dir, "scenes"))
        self.library.ensure_layout()
        self.image = np.zeros((120, 200, 3), dtype=np.uint8)
        self.image[10:60, 20:120] = (255, 255, 255)
        # 导出验证用的目标目录（产物区替身）
        self.target_root = os.path.join(self.temp_dir, "assets")

    def tearDown(self):
        """删除临时目录。"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_preview_roundtrip(self):
        """预览图写入后可原样读回。"""
        self.library.save_scene(_scene("scene-0001"))
        self.library.save_base_image("scene-0001", self.image)
        loaded = self.library.load_base_image("scene-0001")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.shape, self.image.shape)
        self.assertTrue(np.array_equal(loaded, self.image))

    def test_missing_preview_returns_placeholder(self):
        """预览图缺失时返回 None（界面显示占位），不抛异常。"""
        self.library.save_scene(_scene("scene-0001"))
        self.assertIsNone(self.library.load_base_image("scene-0001"))
        self.assertIsNone(self.library.load_base_image("scene-9999"))

    def test_template_roundtrip(self):
        """模板图按特征名落盘到 templates/，可原样读回。"""
        self.library.save_scene(_scene("scene-0001"))
        self.library.save_template("scene-0001", "btn", self.image)
        expected = os.path.join(self.library.scene_dir("scene-0001"), store.TEMPLATE_DIR, "btn.png")
        self.assertTrue(os.path.isfile(expected))
        loaded = self.library.load_template("scene-0001", "btn")
        self.assertTrue(np.array_equal(loaded, self.image))

    def test_chinese_template_name_roundtrip(self):
        """中文特征名必须能正常落盘与读回。

        **这是一个真实缺陷的回归测试**（用户实测「识别方式为模板匹配当前无法
        配置模板图」）。根因是 `cv2.imwrite` 在 Windows 上按 ANSI 代码页解释
        路径：中文文件名会被写到另一个乱码名的文件上，目标路径不存在，而它
        仍返回 True。于是 `templates/` 里是乱码文件、`scene.json` 记的是正确
        名字，两者对不上——导出时判定「特征缺少模板图」而跳过整个场景。

        只用 ASCII 名（如 `btn`）的测试发现不了这个问题，所以这里必须用中文。
        """
        name = "靶场入口-色卡"
        self.library.save_scene(_scene("scene-0001"))
        self.library.save_template("scene-0001", name, self.image)

        expected = os.path.join(self.library.scene_dir("scene-0001"), store.TEMPLATE_DIR, f"{name}.png")
        self.assertTrue(os.path.isfile(expected), f"文件未写到预期路径：{expected}")
        # 目录里不该出现额外文件（乱码名会多出一个）
        folder = os.path.join(self.library.scene_dir("scene-0001"), store.TEMPLATE_DIR)
        self.assertEqual(sorted(os.listdir(folder)), [f"{name}.png"])

        loaded = self.library.load_template("scene-0001", name)
        self.assertIsNotNone(loaded, "中文名模板读不回来")
        self.assertTrue(np.array_equal(loaded, self.image))

    def test_chinese_asset_name_passes_export_check(self):
        """中文特征名的模板齐全时，该场景不应被导出跳过。

        这条覆盖用户遇到的完整现象：「已同步 N 个场景，以下场景因资产不全被
        跳过：特征 xxx 缺少模板图」。
        """
        scene = _scene("scene-0001")
        scene.features = [
            model.Feature(
                name="靶场入口-色卡",
                kind=model.KIND_TEMPLATE,
                mandatory=True,
                shape=shapes.Shape.rectangle(0.0, 0.0, 0.5, 0.5),
                threshold=0.8,
            )
        ]
        self.library.save_scene(scene)
        self.library.save_base_image("scene-0001", self.image)
        self.library.save_template("scene-0001", "靶场入口-色卡", self.image)
        self.library.save_mask("scene-0001", "靶场入口-色卡", scene.features[0].shape.mask(800, 600))

        exported, skipped = self.library.export_to(self.target_root)
        self.assertEqual(skipped, [], f"不该被跳过，实际：{skipped}")
        self.assertEqual(exported, ["scene-0001"])

    def test_missing_template_returns_none(self):
        """模板图缺失时返回 None，识别层据此把该特征按未命中处理。"""
        self.library.save_scene(_scene("scene-0001"))
        self.assertIsNone(self.library.load_template("scene-0001", "absent"))

    def test_delete_scene_removes_assets(self):
        """删场景连带删掉预览图与模板图，不留孤儿文件。"""
        self.library.save_scene(_scene("scene-0001"))
        self.library.save_base_image("scene-0001", self.image)
        self.library.save_template("scene-0001", "btn", self.image)
        folder = self.library.scene_dir("scene-0001")
        self.assertTrue(self.library.delete_scene("scene-0001"))
        self.assertFalse(os.path.exists(folder))
        self.assertFalse(self.library.delete_scene("scene-0001"))


@unittest.skipIf(cv2 is None, f"缺少图像依赖：{CV2_ERROR}")
class TestSceneManagerExport(unittest.TestCase):
    """编辑区 → 产物区导出。"""

    def setUp(self):
        """建临时编辑区与产物区目录。"""
        self.temp_dir = tempfile.mkdtemp(prefix="scene-export-")
        self.edit = store.SceneLibrary(os.path.join(self.temp_dir, "ok_scenes"))
        self.edit.ensure_layout()
        self.edit.set_base_resolution(640, 360)
        self.target = os.path.join(self.temp_dir, "assets_scenes")
        self.image = np.full((360, 640, 3), 128, dtype=np.uint8)
        self.mask = np.full((180, 320), 255, dtype=np.uint8)

    def tearDown(self):
        """清理临时目录。"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_scene(self, with_mask=True):
        """造一个资产齐全的场景。"""
        scene = self.edit.new_scene("主页")
        scene.features.append(_feature("btn", shape=shapes.Shape.rectangle(0.1, 0.1, 0.6, 0.6)))
        self.edit.save_scene(scene)
        self.edit.save_base_image(scene.scene_id, self.image)
        self.edit.save_template(scene.scene_id, "btn", np.full((180, 320, 3), 200, dtype=np.uint8))
        if with_mask:
            self.edit.save_mask(scene.scene_id, "btn", self.mask)
        return scene

    def test_export_copies_required_assets(self):
        """导出复制 scene.json、preview.png、模板与掩码。"""
        scene = self._make_scene()
        exported, skipped = self.edit.export_to(self.target)

        self.assertEqual(exported, [scene.scene_id])
        self.assertEqual(skipped, [])
        folder = os.path.join(self.target, scene.scene_id)
        for name in (store.SCENE_FILE, store.BASE_FILE):
            self.assertTrue(os.path.isfile(os.path.join(folder, name)), name)
        templates = os.path.join(folder, store.TEMPLATE_DIR)
        self.assertTrue(os.path.isfile(os.path.join(templates, "btn.png")))
        self.assertTrue(os.path.isfile(os.path.join(templates, "btn_mask.png")))

    def test_export_never_writes_raw_frame(self):
        """导出不产生未遮罩的原始帧——整帧截图含游戏 UID，任何目录都不该留它。"""
        scene = self._make_scene()
        self.edit.export_to(self.target)
        for folder in (self.edit.scene_dir(scene.scene_id), os.path.join(self.target, scene.scene_id)):
            self.assertFalse(
                os.path.exists(os.path.join(folder, "raw.png")),
                f"{folder} 不应存在 raw.png",
            )

    def test_export_writes_library_meta(self):
        """导出顺带写 library.json，产物区才能独立加载。"""
        self._make_scene()
        self.edit.export_to(self.target)
        meta = store.SceneLibrary(self.target).load_meta()
        self.assertEqual((meta.base_width, meta.base_height), (640, 360))

    def test_export_skips_scene_without_mask(self):
        """缺掩码的场景被跳过并给出原因，而不是静默导出半成品。

        静默导出后运行期匹配会退化成整矩形——精度悄悄下降，且没有任何提示。
        """
        scene = self._make_scene(with_mask=False)
        exported, skipped = self.edit.export_to(self.target)
        self.assertEqual(exported, [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][0], scene.scene_id)
        self.assertIn("掩码", skipped[0][1])

    def test_export_excludes_drafts(self):
        """草稿不进产物区——未经人工确认的探索产物不该参与运行期识别。"""
        self._make_scene()
        draft = self.edit.new_scene("草稿")
        draft.scene_id = self.edit.next_draft_id()
        self.edit.save_scene(draft, as_draft=True)
        exported, _skipped = self.edit.export_to(self.target)
        self.assertFalse(any(item.startswith("draft-") for item in exported))

    def test_reexport_removes_stale_templates(self):
        """重新导出会清掉旧模板，不让残留文件进产物区。

        采用整体替换而不是逐文件覆盖：编辑时删掉的特征会在旧产物里留下孤儿 PNG，
        它不被任何特征引用，却会随包分发出去。
        """
        scene = self._make_scene()
        self.edit.export_to(self.target)
        stale = os.path.join(self.target, scene.scene_id, store.TEMPLATE_DIR, "removed.png")
        with open(stale, "wb") as handle:
            handle.write(b"stale")

        self.edit.export_to(self.target)
        self.assertFalse(os.path.exists(stale), "残留模板必须在重新导出时被清掉")

    def test_exported_library_loads_independently(self):
        """产物区能被独立加载，形状参数原样还原。"""
        self._make_scene()
        self.edit.export_to(self.target)

        out = store.SceneLibrary(self.target)
        out.load_meta()
        scenes, failures = out.load_library()
        self.assertEqual(failures, [])
        self.assertEqual(len(scenes), 1)
        feature = scenes[0].features[0]
        self.assertEqual(feature.shape.kind, shapes.SHAPE_RECT)
        region = feature.region(640, 360)
        self.assertAlmostEqual(region[0], 0.1, places=3)
        self.assertAlmostEqual(region[2], 0.6, places=3)


@unittest.skipIf(cv2 is None, f"缺少图像依赖：{CV2_ERROR}")
class TestSceneManagerMaskStorage(unittest.TestCase):
    """掩码与原始帧的落盘与读回。"""

    def setUp(self):
        """建临时库。"""
        self.temp_dir = tempfile.mkdtemp(prefix="scene-mask-")
        self.library = store.SceneLibrary(os.path.join(self.temp_dir, "scenes"))
        self.library.ensure_layout()
        self.library.set_base_resolution(640, 360)

    def tearDown(self):
        """清理临时目录。"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mask_roundtrip_is_single_channel(self):
        """掩码读回必须是单通道——三通道掩码传给 matchTemplate 会被拒。"""
        self.library.save_scene(_scene("scene-0001"))
        mask = np.zeros((180, 320), dtype=np.uint8)
        mask[40:140, 60:260] = 255
        self.library.save_mask("scene-0001", "btn", mask)
        loaded = self.library.load_mask("scene-0001", "btn")
        self.assertEqual(len(loaded.shape), 2)
        self.assertTrue(np.array_equal(loaded, mask))

    def test_missing_mask_returns_none(self):
        """掩码缺失时返回 None，识别层退化为不带掩码。"""
        self.library.save_scene(_scene("scene-0001"))
        self.assertIsNone(self.library.load_mask("scene-0001", "absent"))


class TestSceneManagerLegacyRejected(unittest.TestCase):
    """旧格式（region 矩形）不再兼容。"""

    def test_legacy_feature_dict_rejected(self):
        """缺 shape 字段的旧特征直接报错，不按全画面矩形兜底。

        兜底的话模板会按整帧裁剪：3840x2160 下一个特征就 23.7MB，启动即卡住，
        而表现是「打开很慢」而非「格式不对」。
        """
        with self.assertRaises(model.SceneModelError) as ctx:
            model.Feature.from_dict({"name": "old", "region": [0.1, 0.1, 0.2, 0.2]})
        self.assertIn("shape", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
