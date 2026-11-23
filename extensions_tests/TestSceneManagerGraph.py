# -*- coding: utf-8 -*-
"""场景管理的流转图与寻路测试（批次四）。

被测对象是 `extensions/capabilities/scene_manager/graph.py`。寻路错的表现是
「点了不该点的区域」或「绕远路」，而两者都不会报错。

分五组：

1. **建图**：节点、边、孤立场景、悬空边报错。
2. **边来源过滤**：`explored` 边不进图，`manual` 与 `confirmed` 进图。
3. **寻路**：直达、两跳、不可达、起止相同、场景不存在。
4. **回退**：优先返回区域，其次入边反推，起点无路可退。
5. **布局**：固定种子保证布局稳定、单节点与空图不崩。
"""

import unittest

from extensions.capabilities.scene_manager import graph as scene_graph
from extensions.capabilities.scene_manager import model, routes


def _area(name):
    """造一个交互区域。

    **区域不再带目标场景**：跳转关系在路由表里（见 `_routes`）。这里保留
    `_area(name)` 的单参数形态，让调用处只关心「这块地方叫什么」。
    """
    return model.Area(name=name, rect=(0.1, 0.1, 0.2, 0.2))


def _scene(scene_id, areas=None, return_area="", name=""):
    """造一个场景。

    `return_area` 是旧参数名，等价于把同名区域标成 `explore_return`——保留它
    让用例读起来仍是「这个场景的返回区域是 X」，不必到处改调用点。
    """
    areas = list(areas or [])
    for area in areas:
        if return_area and area.name == return_area:
            area.explore_return = True
    return model.Scene(
        scene_id=scene_id,
        name=name or scene_id,
        areas=areas,
    )


def _routes(entries):
    """按 `[(from, area, to, source, after_wait)]` 造路由表。

    Args：
        entries：五元组列表，后两项可省略（默认 `manual`、1.0）。

    Returns：
        `routes.RouteTable` 实例。
    """
    table = routes.RouteTable()
    for entry in entries:
        from_scene, area_name, to_scene = entry[:3]
        source = entry[3] if len(entry) > 3 else model.SOURCE_MANUAL
        after_wait = entry[4] if len(entry) > 4 else 1.0
        table.put(routes.Route(from_scene, area_name, to_scene, after_wait=after_wait, source=source))
    return table


def _chain():
    """造一条 A→B→C 的三点链路，外加 A→C 的直达边。"""
    scenes = [
        _scene("scene-0001", [_area("to_b"), _area("to_c")]),
        _scene("scene-0002", [_area("to_c")]),
        _scene("scene-0003", []),
    ]
    table = _routes(
        [
            ("scene-0001", "to_b", "scene-0002"),
            ("scene-0001", "to_c", "scene-0003"),
            ("scene-0002", "to_c", "scene-0003"),
        ]
    )
    return scenes, table


class TestBuild(unittest.TestCase):
    """建图。"""

    def test_three_node_path(self):
        """A→B→C 建出三个节点两条边。"""
        graph = scene_graph.SceneGraph.build(*_chain())
        self.assertEqual(graph.scene_count(), 3)
        self.assertEqual(graph.edge_count(), 3)
        self.assertEqual(
            graph.outgoing("scene-0001"),
            [("to_b", "scene-0002", 1.0), ("to_c", "scene-0003", 1.0)],
        )

    def test_isolated_scene_still_a_node(self):
        """没有任何边的场景仍是节点：界面上必须显示它。

        孤立指「既无入边也无出边」。有入边但无出边的场景不算孤立——它是路径终点，
        界面上会通过边被看到。
        """
        scenes = [
            _scene("scene-0001", []),
            _scene("scene-0002", [_area("x")]),
            _scene("scene-0003", []),
        ]
        graph = scene_graph.SceneGraph.build(scenes, _routes([("scene-0002", "x", "scene-0001")]))
        self.assertEqual(graph.scene_count(), 3)
        # scene-0001 有入边，不算孤立；scene-0003 无边才是孤立
        self.assertEqual(graph.isolated_scenes(), ["scene-0003"])

    def test_explored_edges_excluded_from_routing(self):
        """Explored 边不进图：未确认的探索边不得参与寻路。"""
        scenes = [_scene("scene-0001", [_area("manual_edge"), _area("explored_edge")])]
        scenes += [_scene("scene-0002", []), _scene("scene-0003", [])]
        table = _routes(
            [
                ("scene-0001", "manual_edge", "scene-0002", model.SOURCE_MANUAL),
                ("scene-0001", "explored_edge", "scene-0003", model.SOURCE_EXPLORED),
            ]
        )
        graph = scene_graph.SceneGraph.build(scenes, table)
        self.assertEqual(graph.edge_count(), 1)
        self.assertTrue(graph.graph.has_edge("scene-0001", "scene-0002"))
        self.assertFalse(graph.graph.has_edge("scene-0001", "scene-0003"))

    def test_pending_edges_visible_when_requested(self):
        """传 `include_pending=True` 时待确认边也画出来，但仍不进寻路。"""
        scenes = [_scene("scene-0001", [_area("e")]), _scene("scene-0002", [])]
        table = _routes([("scene-0001", "e", "scene-0002", model.SOURCE_EXPLORED)])
        drawn = scene_graph.SceneGraph.build(scenes, table, include_pending=True)
        self.assertEqual(drawn.edge_count(), 1, "画图时能看到")
        routable = scene_graph.SceneGraph.build(scenes, table)
        self.assertEqual(routable.edge_count(), 0, "寻路时看不到")

    def test_confirmed_edges_included(self):
        """Confirmed 边参与寻路。"""
        scenes = [_scene("scene-0001", [_area("e")]), _scene("scene-0002", [])]
        table = _routes([("scene-0001", "e", "scene-0002", model.SOURCE_CONFIRMED)])
        graph = scene_graph.SceneGraph.build(scenes, table)
        self.assertEqual(graph.edge_count(), 1)

    def test_area_without_route_is_not_an_edge(self):
        """没有路由的区域只点不记边。"""
        scenes = [_scene("scene-0001", [_area("close")]), _scene("scene-0002", [])]
        graph = scene_graph.SceneGraph.build(scenes, _routes([]))
        self.assertEqual(graph.edge_count(), 0)

    def test_dangling_edge_raises(self):
        """指向不存在场景的路由必须报错，不静默忽略。"""
        scenes = [_scene("scene-0001", [_area("bad")])]
        table = _routes([("scene-0001", "bad", "scene-9999")])
        with self.assertRaises(scene_graph.GraphError) as context:
            scene_graph.SceneGraph.build(scenes, table)
        self.assertIn("scene-9999", str(context.exception))

    def test_route_to_missing_area_raises(self):
        """路由指向已不存在的区域也算不一致——那种边点不到。"""
        scenes = [_scene("scene-0001", []), _scene("scene-0002", [])]
        table = _routes([("scene-0001", "ghost", "scene-0002")])
        with self.assertRaises(scene_graph.GraphError) as context:
            scene_graph.SceneGraph.build(scenes, table)
        self.assertIn("ghost", str(context.exception))

    def test_pending_edge_to_draft_is_not_dangling(self):
        """待确认边的目标是草稿时不报悬空：草稿尚未转正，那条边是合法数据。

        草稿转正时会把入边一起升档（见 `store.promote_draft`）。按悬空边处理的
        话，一次探索发现新场景就让流转图整块打不开。
        """
        scenes = [_scene("scene-0001", [_area("e")])]
        table = _routes([("scene-0001", "e", "draft-0001", model.SOURCE_EXPLORED)])
        graph = scene_graph.SceneGraph.build(scenes, table, include_pending=True)
        self.assertEqual(graph.scene_count(), 1)
        self.assertEqual(graph.edge_count(), 0, "草稿不是正式节点，这条边不画")

    def test_empty_scene_list(self):
        """空场景列表得到空图，不报错。"""
        graph = scene_graph.SceneGraph.build([])
        self.assertEqual(graph.scene_count(), 0)
        self.assertEqual(graph.edge_count(), 0)


class TestRouting(unittest.TestCase):
    """寻路。"""

    def setUp(self):
        """建三点链路图。"""
        self.graph = scene_graph.SceneGraph.build(*_chain())

    def test_direct_edge_preferred(self):
        """有直达边时走一步，不绕两跳。"""
        steps = self.graph.path("scene-0001", "scene-0003")
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].area_name, "to_c")

    def test_two_step_path(self):
        """无可达边时走最短的两步路径。"""
        scenes = [
            _scene("scene-0001", [_area("to_b")]),
            _scene("scene-0002", [_area("to_c")]),
            _scene("scene-0003", []),
        ]
        table = _routes([("scene-0001", "to_b", "scene-0002"), ("scene-0002", "to_c", "scene-0003")])
        graph = scene_graph.SceneGraph.build(scenes, table)
        steps = graph.path("scene-0001", "scene-0003")
        self.assertEqual([step.area_name for step in steps], ["to_b", "to_c"])
        self.assertEqual([step.from_scene for step in steps], ["scene-0001", "scene-0002"])
        self.assertEqual(steps[-1].to_scene, "scene-0003")

    def test_same_scene_returns_empty_path(self):
        """起止相同时空路径（已在目标场景无需移动）。"""
        self.assertEqual(self.graph.path("scene-0001", "scene-0001"), [])

    def test_unreachable_returns_none(self):
        """不可达返回 None，而不是抛异常（不可达是正常业务结果）。"""
        self.assertIsNone(self.graph.path("scene-0003", "scene-0001"))

    def test_unknown_scene_raises(self):
        """场景不存在属调用方错误，抛异常。"""
        with self.assertRaises(scene_graph.GraphError):
            self.graph.path("scene-9999", "scene-0001")

    def test_reachable_from(self):
        """可达集合包含自身与下游全部场景。"""
        self.assertEqual(
            self.graph.reachable_from("scene-0001"),
            {"scene-0001", "scene-0002", "scene-0003"},
        )
        self.assertEqual(self.graph.reachable_from("scene-0003"), {"scene-0003"})
        self.assertEqual(self.graph.reachable_from("scene-9999"), set())

    def test_edge_snapshot_for_table(self):
        """边快照供界面表格展示，含来源与等待时间。"""
        rows = self.graph.edges_snapshot()
        self.assertEqual(len(rows), 3)
        self.assertIn(("scene-0001", "to_b", "scene-0002", model.SOURCE_MANUAL, 1.0), rows)


class TestReturnStep(unittest.TestCase):
    """探索回退。"""

    def test_return_step_ignores_explore_flag(self):
        """场景级的探索返回标记不影响 `return_step`：它只按入边反推。

        标记是探索专用的事实，运行期导航不该因为一个探索标注改变落点。
        """
        scenes = [
            _scene("scene-0001", [_area("to_b")]),
            _scene("scene-0002", [_area("to_c"), _area("back")], return_area="to_c"),
            _scene("scene-0003", []),
        ]
        table = _routes(
            [
                ("scene-0001", "to_b", "scene-0002"),
                ("scene-0002", "to_c", "scene-0003"),
                ("scene-0002", "back", "scene-0001"),
            ]
        )
        graph = scene_graph.SceneGraph.build(scenes, table)
        self.assertEqual(graph.return_step("scene-0002"), ("back", "scene-0001"))

    def test_falls_back_to_incoming_edge(self):
        """未配返回区域时，靠「当前场景里能回上游的那个区域」反推。

        注意 `b_back` 必须属于 **scene-0002**：回退动作发生在当前画面上，
        拿上游场景的区域名（`to_b` 属于 scene-0001）去 scene-0002 里找，
        只会得到「该区域不存在」——而这正是先前的缺陷：探索点进新场景后
        永远回不来，表现为「点一次、跳转一次就结束」。
        """
        scenes = [
            _scene("scene-0001", [_area("to_b")]),
            _scene("scene-0002", [_area("b_back")]),
        ]
        table = _routes(
            [
                ("scene-0001", "to_b", "scene-0002"),
                ("scene-0002", "b_back", "scene-0001"),
            ]
        )
        graph = scene_graph.SceneGraph.build(scenes, table)
        self.assertEqual(graph.return_step("scene-0002"), ("b_back", "scene-0001"))

    def test_no_return_when_current_scene_has_no_back_edge(self):
        """当前场景没有能回上游的区域时返回 None，**不能借用上游的区域名**。

        这是用户实际遇到的情形：上游点「开始按钮」跳到新画面，而新画面里
        没有任何能回去的区域。此时正确行为是「如实报告无法回退」——
        借用上游的 `to_b` 去新画面上点击是**真实误点**（可能花资源、进战斗）。

        探索会因此提前结束，但界面要能说明原因（`warn` 事件）。
        """
        scenes = [
            _scene("scene-0001", [_area("to_b")]),
            _scene("scene-0002", []),
        ]
        table = _routes([("scene-0001", "to_b", "scene-0002")])
        graph = scene_graph.SceneGraph.build(scenes, table)
        self.assertIsNone(graph.return_step("scene-0002"))

    def test_root_scene_has_no_return(self):
        """无入边且未配返回区域的场景无处可退。"""
        graph = scene_graph.SceneGraph.build(*_chain())
        self.assertIsNone(graph.return_step("scene-0001"))

    def test_invalid_return_flag_name_has_no_effect(self):
        """标记名对不上任何区域时等于没标，按出边反推。"""
        scenes = [
            _scene("scene-0001", [_area("to_b")]),
            _scene("scene-0002", [_area("b_back")], return_area="deleted_area"),
        ]
        table = _routes([("scene-0001", "to_b", "scene-0002"), ("scene-0002", "b_back", "scene-0001")])
        graph = scene_graph.SceneGraph.build(scenes, table)
        self.assertEqual(graph.return_step("scene-0002"), ("b_back", "scene-0001"))

    def test_incoming_lists_all_sources(self):
        """入边列出全部上游，供探索选择。"""
        scenes = [
            _scene("scene-0001", [_area("a")]),
            _scene("scene-0002", [_area("b")]),
            _scene("scene-0003", []),
        ]
        table = _routes([("scene-0001", "a", "scene-0003"), ("scene-0002", "b", "scene-0003")])
        graph = scene_graph.SceneGraph.build(scenes, table)
        self.assertEqual(graph.incoming("scene-0003"), [("scene-0001", "a"), ("scene-0002", "b")])


class TestLayout(unittest.TestCase):
    """布局坐标。"""

    def test_layout_is_stable_across_builds(self):
        """同一份库两次建图得到相同布局（固定随机种子）。"""
        first = scene_graph.SceneGraph.build(*_chain()).layout()
        second = scene_graph.SceneGraph.build(*_chain()).layout()
        self.assertEqual(first, second)

    def test_layout_empty_graph(self):
        """空图的布局为空字典。"""
        self.assertEqual(scene_graph.SceneGraph.build([]).layout(), {})

    def test_layout_single_node_at_origin(self):
        """单节点图把唯一节点放在原点。"""
        graph = scene_graph.SceneGraph.build([_scene("scene-0001", [])])
        self.assertEqual(graph.layout(), {"scene-0001": (0.0, 0.0)})

    def test_layout_covers_all_nodes(self):
        """布局覆盖全部节点。"""
        graph = scene_graph.SceneGraph.build(*_chain())
        positions = graph.layout()
        self.assertEqual(set(positions), {"scene-0001", "scene-0002", "scene-0003"})
        for point in positions.values():
            self.assertEqual(len(point), 2)


class TestRouteStep(unittest.TestCase):
    """路径步。"""

    def test_to_dict_and_equality(self):
        """路径步可序列化并按键比较。"""
        step = scene_graph.RouteStep("scene-0001", "btn", "scene-0002", 1.5)
        self.assertEqual(
            step.to_dict(),
            {"from": "scene-0001", "area": "btn", "to": "scene-0002", "after_wait": 1.5},
        )
        self.assertEqual(step, scene_graph.RouteStep("scene-0001", "btn", "scene-0002"))
        self.assertNotEqual(step, scene_graph.RouteStep("scene-0001", "other", "scene-0002"))


if __name__ == "__main__":
    unittest.main()
