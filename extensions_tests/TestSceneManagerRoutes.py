# -*- coding: utf-8 -*-
"""流转路由表的测试。

被测对象是 `extensions/capabilities/scene_manager/routes.py` 与
`store.SceneLibrary` 里围绕它的读写、迁移、清理。

**为什么这一组必须单独存在**：路由与场景分家之后，两者之间只剩「`(场景 id,
区域名)` 这个键」一条纽带。键对不上时的表现全是静默的——

- 区域改名不同步 → 边无故消失；
- 草稿转正不改写引用 → 建图抛「悬空边」，界面却提示「请检查是否被手工改动」；
- 删除场景不清入边 → 整个流转图打不开；
- 旧库不迁移 → 升级后所有手工配的边凭空消失。

四种都不会报错、都不会自己恢复。因此逐个锁死。
"""

import os
import shutil
import tempfile
import unittest

from extensions.capabilities.scene_manager import graph, model, routes, shapes, store


def _route(from_scene, area, to_scene, source=model.SOURCE_MANUAL, after_wait=1.0, observations=1):
    """造一条路由。

    Args：
        from_scene：来源场景 id。
        area：区域名。
        to_scene：目标场景 id。
        source：来源档位。
        after_wait：等待秒数。
        observations：观测次数。

    Returns：
        `routes.Route` 实例。
    """
    return routes.Route(
        from_scene,
        area,
        to_scene,
        after_wait=after_wait,
        source=source,
        observations=observations,
    )


class TestRouteTable(unittest.TestCase):
    """路由表的内存模型。"""

    def test_put_and_get(self):
        """写入后能按 `(场景, 区域)` 取回。"""
        table = routes.RouteTable([_route("scene-0001", "go", "scene-0002")])
        route = table.get("scene-0001", "go")
        self.assertIsNotNone(route)
        self.assertEqual(route.target_scene, "scene-0002")
        self.assertEqual(len(table), 1)

    def test_duplicate_key_overwrites(self):
        """同键后写覆盖先写——一个区域只能通向一个场景。"""
        table = routes.RouteTable(
            [
                _route("scene-0001", "go", "scene-0002"),
                _route("scene-0001", "go", "scene-0003"),
            ]
        )
        self.assertEqual(len(table), 1)
        self.assertEqual(table.target_of("scene-0001", "go"), "scene-0003")

    def test_target_of_missing_returns_empty(self):
        """查不存在的边返回空串而不是 None。"""
        table = routes.RouteTable()
        self.assertEqual(table.target_of("scene-0001", "nope"), "")

    def test_after_wait_of_missing_returns_default(self):
        """没有边时等待取 1.0（历史默认值）。"""
        table = routes.RouteTable()
        self.assertEqual(table.after_wait_of("scene-0001", "nope"), 1.0)

    def test_routable_filter(self):
        """只有 manual 与 confirmed 可寻路。"""
        table = routes.RouteTable(
            [
                _route("scene-0001", "m", "scene-0002", model.SOURCE_MANUAL),
                _route("scene-0001", "e", "scene-0002", model.SOURCE_EXPLORED),
                _route("scene-0001", "c", "scene-0002", model.SOURCE_CONFIRMED),
            ]
        )
        names = [route.area_name for route in table.routable_routes()]
        self.assertEqual(names, ["c", "m"])

    def test_set_target_creates_and_updates(self):
        """设置目标时新建，`after_wait` 为空则沿用旧值。"""
        table = routes.RouteTable()
        table.set_target("scene-0001", "go", "scene-0002", after_wait=2.0)
        self.assertEqual(table.get("scene-0001", "go").after_wait, 2.0)
        table.set_target("scene-0001", "go", "scene-0003")
        route = table.get("scene-0001", "go")
        self.assertEqual(route.target_scene, "scene-0003")
        self.assertEqual(route.after_wait, 2.0, "省略时不该把等待重置")

    def test_set_empty_target_removes(self):
        """目标为空即删除——「这个区域不构成边」。"""
        table = routes.RouteTable([_route("scene-0001", "go", "scene-0002")])
        table.set_target("scene-0001", "go", "")
        self.assertEqual(len(table), 0)

    def test_rename_area_migrates_key(self):
        """区域改名后路由键跟着改，取值不受影响。"""
        table = routes.RouteTable([_route("scene-0001", "old", "scene-0002")])
        self.assertTrue(table.rename_area("scene-0001", "old", "new"))
        self.assertEqual(table.target_of("scene-0001", "new"), "scene-0002")
        self.assertEqual(table.target_of("scene-0001", "old"), "")

    def test_remove_scene_clears_in_and_out(self):
        """删除场景时入边与出边一起清掉。"""
        table = routes.RouteTable(
            [
                _route("scene-0001", "to_b", "scene-0002"),
                _route("scene-0002", "to_c", "scene-0003"),
                _route("scene-0003", "to_b2", "scene-0002"),
            ]
        )
        removed = table.remove_scene("scene-0002")
        self.assertEqual(removed, 3)
        self.assertEqual(len(table), 0)

    def test_confirm_scene_edges_includes_incoming(self):
        """确认场景时出边与入边一起升档，`manual` 不动。"""
        table = routes.RouteTable(
            [
                _route("scene-0001", "to_b", "scene-0002", model.SOURCE_EXPLORED),
                _route("scene-0002", "to_c", "scene-0003", model.SOURCE_EXPLORED),
                _route("scene-0002", "manual", "scene-0003", model.SOURCE_MANUAL),
            ]
        )
        touched = table.confirm_scene_edges("scene-0002", include_incoming=True)
        self.assertEqual(touched, 2)
        self.assertEqual(table.get("scene-0001", "to_b").source, model.SOURCE_CONFIRMED)
        self.assertEqual(table.get("scene-0002", "to_c").source, model.SOURCE_CONFIRMED)
        self.assertEqual(
            table.get("scene-0002", "manual").source,
            model.SOURCE_MANUAL,
            "人工配的边不该被标成探索确认",
        )

    def test_invalid_source_rejected(self):
        """来源档位非法时直接报错，不静默接受。"""
        with self.assertRaises(routes.RoutesError):
            _route("scene-0001", "go", "scene-0002", source="guess")

    def test_empty_area_or_target_rejected(self):
        """区域名与目标场景都不能为空。"""
        with self.assertRaises(routes.RoutesError):
            _route("scene-0001", "", "scene-0002")
        with self.assertRaises(routes.RoutesError):
            _route("scene-0001", "go", "")


class TestRouteSerialization(unittest.TestCase):
    """序列化与磁盘往返。"""

    def test_roundtrip(self):
        """存 → 读 → 存，逐字段相等。"""
        table = routes.RouteTable([_route("scene-0001", "go", "scene-0002", model.SOURCE_EXPLORED, 2.5, 3)])
        restored = routes.RouteTable.from_dict(table.to_dict())
        self.assertEqual(restored.to_dict(), table.to_dict())

    def test_missing_file_is_empty_table(self):
        """文件不存在等价于空表，不是错误。"""
        temp_dir = tempfile.mkdtemp(prefix="routes-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        table = routes.RouteTable().load(os.path.join(temp_dir, "nope.json"))
        self.assertTrue(table.is_empty())

    def test_bad_entry_skipped_not_fatal(self):
        """单条坏数据跳过，其余仍可用。"""
        payload = {
            "schema_version": 1,
            "routes": [
                {"from": "scene-0001", "area": "go", "to": "scene-0002"},
                {"from": "", "area": "bad", "to": "scene-0002"},
            ],
        }
        table = routes.RouteTable.from_dict(payload)
        self.assertEqual(len(table), 1)
        self.assertEqual(table.target_of("scene-0001", "go"), "scene-0002")

    def test_v1_payload_without_version_is_accepted(self):
        """没有 `schema_version` 的旧表也能读。"""
        table = routes.RouteTable.from_dict({"routes": [{"from": "a", "area": "x", "to": "b"}]})
        self.assertEqual(table.target_of("a", "x"), "b")

    def test_save_uses_utf8_without_bom(self):
        """落盘是 UTF-8 无 BOM、LF 换行、中文原样。"""
        temp_dir = tempfile.mkdtemp(prefix="routes-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        path = os.path.join(temp_dir, "routes.json")
        routes.RouteTable([_route("scene-0001", "开始按钮", "scene-0002")]).save(path)
        raw = open(path, "rb").read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "不能带 BOM")
        self.assertNotIn(b"\r\n", raw, "换行必须是 LF")
        self.assertIn("开始按钮".encode(), raw, "中文不能被转义")


class TestLibraryRoutes(unittest.TestCase):
    """库级的路由读写与清理。"""

    def setUp(self):
        """建一个临时库。"""
        self.root = tempfile.mkdtemp(prefix="scene-routes-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.library = store.SceneLibrary(os.path.join(self.root, "scenes"))
        self.library.ensure_layout()
        self.library.set_base_resolution(640, 360)

    def _scene(self, scene_id, areas=()):
        """建一个场景并落盘。

        Args：
            scene_id：场景 id。
            areas：区域名列表。

        Returns：
            `model.Scene` 实例。
        """
        scene = model.Scene(scene_id=scene_id, name=scene_id)
        for name in areas:
            scene.areas.append(model.Area(name=name, rect=(0.1, 0.1, 0.2, 0.2)))
        self.library.save_scene(scene, as_draft=store.SceneLibrary.is_draft_id(scene_id))
        return scene

    def test_ensure_layout_creates_routes_file(self):
        """建库时就落一张空表，让「空」与「没有这个概念」可区分。"""
        self.assertTrue(os.path.isfile(self.library.routes_path))

    def test_set_and_reload_route(self):
        """写入后重新从磁盘读仍能取到。"""
        self._scene("scene-0001", ["go"])
        self.library.set_route("scene-0001", "go", "scene-0002", after_wait=1.5)
        reloaded = store.SceneLibrary(self.library.root)
        reloaded.load_routes()
        self.assertEqual(reloaded.route_target("scene-0001", "go"), "scene-0002")
        self.assertEqual(reloaded.route_after_wait("scene-0001", "go"), 1.5)

    def test_confirm_route_makes_it_routable(self):
        """确认后来源变为 confirmed，进入可寻路集合。"""
        self._scene("scene-0001", ["go"])
        self.library.set_route("scene-0001", "go", "scene-0002", source=model.SOURCE_EXPLORED)
        self.assertFalse(self.library.routes.get("scene-0001", "go").routable())
        self.assertTrue(self.library.confirm_route("scene-0001", "go"))
        self.assertTrue(self.library.routes.get("scene-0001", "go").routable())

    def test_delete_scene_clears_its_routes(self):
        """删场景时路由一起清，避免留下悬空边让建图报错。"""
        self._scene("scene-0001", ["go"])
        self._scene("scene-0002", [])
        self.library.set_route("scene-0001", "go", "scene-0002")
        self.library.delete_scene("scene-0002")
        self.library.load_routes()
        self.assertEqual(len(self.library.routes), 0)

    def test_legacy_edges_migrated_on_load(self):
        """旧库挂在区域上的边在首次加载时搬进路由表。

        **这是升级路径的关键**：`Area.from_dict` 已经不读那些字段，不迁移的话
        用户升级后会看到所有手工配的边凭空消失，且没有任何报错。
        """
        import json

        folder = os.path.join(self.library.root, "scene-0001")
        os.makedirs(os.path.join(folder, "templates"), exist_ok=True)
        legacy = {
            "schema_version": 1,
            "id": "scene-0001",
            "name": "旧场景",
            "features": [],
            "areas": [
                {
                    "name": "go",
                    "rect": [0.4, 0.4, 0.6, 0.6],
                    "target_scene": "scene-0002",
                    "after_wait": 2.5,
                    "edge_source": "manual",
                    "observations": 3,
                }
            ],
            "return_area": "",
        }
        with open(os.path.join(folder, "scene.json"), "w", encoding="utf-8") as handle:
            json.dump(legacy, handle, ensure_ascii=False)

        scenes, failures = self.library.load_library()
        self.assertEqual(failures, [])
        route = self.library.routes.get("scene-0001", "go")
        self.assertIsNotNone(route, "旧边必须被迁移")
        self.assertEqual(route.target_scene, "scene-0002")
        self.assertEqual(route.after_wait, 2.5)
        self.assertEqual(route.observations, 3)
        self.assertFalse(hasattr(scenes[0].areas[0], "target_scene"))

    def test_migration_does_not_override_existing_route(self):
        """迁移不覆盖路由表里已有的条目——表是新的权威来源。"""
        import json

        self._scene("scene-0001", ["go"])
        self.library.set_route("scene-0001", "go", "scene-0003")
        path = os.path.join(self.library.scene_dir("scene-0001"), "scene.json")
        data = json.load(open(path, encoding="utf-8"))
        data["areas"][0]["target_scene"] = "scene-0002"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)

        self.library._routes_ready = False
        self.library.load_library()
        self.assertEqual(
            self.library.routes.target_of("scene-0001", "go"),
            "scene-0003",
            "已有路由不该被旧字段覆盖",
        )


class TestPromoteMigration(unittest.TestCase):
    """草稿转正时路由必须整体迁移。"""

    def setUp(self):
        """建库。"""
        self.root = tempfile.mkdtemp(prefix="promote-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.library = store.SceneLibrary(os.path.join(self.root, "scenes"))
        self.library.ensure_layout()
        self.library.set_base_resolution(640, 360)

    def test_dangling_edge_removed_after_promote(self):
        """转正后不再有指向草稿 id 的边——那会让建图直接抛错。"""
        parent = model.Scene(scene_id="scene-0001", name="父")
        parent.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        self.library.save_scene(parent)

        draft = model.Scene(scene_id="draft-0001", name="发现")
        draft.features.append(model.Feature(name="auto", shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3)))
        self.library.save_scene(draft, as_draft=True)
        self.library.set_route("scene-0001", "go", "draft-0001", source=model.SOURCE_EXPLORED)

        promoted, _dropped = self.library.promote_draft("draft-0001")
        self.library.load_routes()

        scenes, failures = self.library.load_library()
        self.assertEqual(failures, [])
        built = graph.SceneGraph.build(scenes, self.library.routes)
        self.assertTrue(built.graph.has_edge("scene-0001", promoted.scene_id))
        self.assertIsNone(
            self.library.routes.get("scene-0001", "go").target_scene == "draft-0001" or None,
            "目标必须已改写成新 id",
        )

    def test_promote_confirms_incoming_edge(self):
        """转正同时确认入边：确认「画面是对的」等于确认「这条路能到」。"""
        parent = model.Scene(scene_id="scene-0001", name="父")
        parent.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        self.library.save_scene(parent)
        self.library.save_scene(model.Scene(scene_id="draft-0001", name="发现"), as_draft=True)
        self.library.set_route("scene-0001", "go", "draft-0001", source=model.SOURCE_EXPLORED)

        self.library.promote_draft("draft-0001")
        self.library.load_routes()
        route = self.library.routes.get("scene-0001", "go")
        self.assertEqual(
            route.source,
            model.SOURCE_CONFIRMED,
            "转正后入边应可直接寻路，不必再确认一次",
        )

    def test_draft_to_draft_edge_dropped(self):
        """指向其它草稿的边在转正时清空并计数。"""
        draft = model.Scene(scene_id="draft-0001", name="A")
        draft.areas.append(model.Area(name="next", rect=(0.1, 0.1, 0.2, 0.2)))
        self.library.save_scene(draft, as_draft=True)
        self.library.set_route("draft-0001", "next", "draft-0002", source=model.SOURCE_EXPLORED)

        _promoted, dropped = self.library.promote_draft("draft-0001")
        self.assertEqual(dropped, 1)


class TestExportRoutes(unittest.TestCase):
    """导出时只带库内场景之间的路由。"""

    def test_export_keeps_only_internal_routes(self):
        """指向未导出场景的路由不写进产物区，否则运行期建图会报悬空边。"""
        root = tempfile.mkdtemp(prefix="export-")
        self.addCleanup(shutil.rmtree, root, True)
        library = store.SceneLibrary(os.path.join(root, "edit"))
        library.ensure_layout()
        library.set_base_resolution(640, 360)

        for scene_id in ("scene-0001", "scene-0002"):
            scene = model.Scene(scene_id=scene_id, name=scene_id)
            scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
            scene.features.append(model.Feature(name="tpl", shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3)))
            library.save_scene(scene)
            for feature in scene.features:
                library.save_template(scene_id, feature.name, _solid())
                library.save_mask(scene_id, feature.name, _mask())
            library.save_base_image(scene_id, _frame())

        library.set_route("scene-0001", "go", "scene-0002")
        library.set_route("scene-0002", "go", "scene-9999")

        target = os.path.join(root, "assets")
        exported, skipped = library.export_to(target)
        self.assertEqual(skipped, [])
        self.assertEqual(len(exported), 2)

        exported_table = routes.RouteTable().load(os.path.join(target, routes.ROUTES_FILE))
        self.assertEqual(len(exported_table), 1)
        self.assertEqual(exported_table.target_of("scene-0001", "go"), "scene-0002")


def _frame():
    """造一张纯色帧。

    Returns：
        单通道 `numpy` 数组。
    """
    import numpy as np

    return np.zeros((360, 640, 3), dtype="uint8")


def _solid():
    """造一个小模板图。

    Returns：
        `numpy` 数组。
    """
    import numpy as np

    return np.full((20, 20, 3), 128, dtype="uint8")


def _mask():
    """造一张全通掩码。

    Returns：
        单通道 `numpy` 数组。
    """
    import numpy as np

    return np.full((20, 20), 255, dtype="uint8")


if __name__ == "__main__":
    unittest.main()
