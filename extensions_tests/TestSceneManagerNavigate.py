# -*- coding: utf-8 -*-
"""场景管理的导航器测试（批次五）。

被测对象是 `extensions/capabilities/scene_manager/navigate.py`。导航器把「跳到指定
场景」做成了可复用的纯逻辑，全部交互经依赖注入，因此可以用假桩精确控制每一步。

用假桩而非真机：导航的错误形态是「点了不该点的区域」「没识别到就走了」「失败不
返回而是抛异常」，这些都能用桩精确复现，而真机上无法让「第 2 步识别失败」稳定发生。

分六组：

1. **识别**：重试、取帧失败、连续失败。
2. **成功路径**：已在目标、一步直达、多步。
3. **失败路径**：不可达、目标不存在、起点无法识别、步超时。
4. **回退**：失败后回退成功继续、回退失败中止、回退后重规划。
5. **中止**：should_stop 在步前与步中都能生效。
6. **辅助**：plan_preview、describe_scene。
"""

import unittest

from extensions.capabilities.scene_manager import graph as scene_graph
from extensions.capabilities.scene_manager import model, navigate, routes


class FakeMatch:
    """模拟 `recognize.MatchResult`，只需 matched/scene_id 两个行为。"""

    def __init__(self, scene_id=""):
        """保存命中的场景 id；空串表示未命中。"""
        self._scene_id = scene_id

    def matched(self):
        """返回是否命中。"""
        return bool(self._scene_id)

    def scene_id(self):
        """返回场景 id。"""
        return self._scene_id

    def __repr__(self):
        """便于断言失败时阅读。"""
        return f"FakeMatch({self._scene_id!r})"


class FakeClock:
    """可控时钟：`wait` 会推进它，因此超时逻辑无需真实等待。"""

    def __init__(self):
        """从 0 开始计时。"""
        self.now = 0.0

    def __call__(self):
        """返回当前时间。"""
        return self.now

    def advance(self, seconds):
        """推进时间。

        Args：
            seconds：推进的秒数。
        """
        self.now += seconds


class FakeWorld:
    """模拟游戏世界：记录点击，按预定规则切换当前场景。

    Attributes:
        current: 当前场景 id。
        clicks: 已点击的 `(scene_id, area_name)` 列表。
        transitions: `{(scene_id, area_name): next_scene_id}` 点击后的目标；
            缺省时点击不改变当前场景（模拟「点了没反应」）。
        unrecognizable: 为 True 时识别恒为未命中。
        frames_left_before_fail: 剩余可成功取帧次数，用尽后取帧返回 None。
    """

    def __init__(self, current="", transitions=None):
        """初始化世界。

        Args：
            current：初始场景 id。
            transitions：点击转移表。
        """
        self.current = current
        self.clicks = []
        self.transitions = transitions or {}
        self.unrecognizable = False
        self.frames_left_before_fail = None
        self.click_raises = None


def _scene(scene_id, areas=None, return_area="", name=""):
    """造一个场景。

    `return_area` 是旧参数名，等价于把同名区域标成 `explore_return`。
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


def _area(name):
    """造一个交互区域。

    **区域不再带目标场景**：跳转关系在路由表里（见 `_routes`）。
    """
    return model.Area(name=name, rect=(0.1, 0.1, 0.2, 0.2))


def _routes(entries):
    """按 `[(from, area, to, after_wait)]` 造路由表。

    Args：
        entries：四元组列表；`after_wait` 可省略（默认 0.0，与旧 `_area` 一致）。

    Returns：
        `routes.RouteTable` 实例。
    """
    table = routes.RouteTable()
    for entry in entries:
        from_scene, area_name, to_scene = entry[:3]
        after_wait = entry[3] if len(entry) > 3 else 0.0
        table.put(routes.Route(from_scene, area_name, to_scene, after_wait=after_wait))
    return table


def _chain_scenes():
    """造 A→B→C 三点链路（场景 + 路由表）。"""
    scenes = [
        _scene("scene-0001", [_area("to_b")]),
        _scene("scene-0002", [_area("to_c")]),
        _scene("scene-0003", []),
    ]
    return scenes, _routes([("scene-0001", "to_b", "scene-0002"), ("scene-0002", "to_c", "scene-0003")])


def _make_navigator(world, scenes, routes_table=None, options=None):
    """按假世界造一个导航器。

    Args：
        world：假世界。
        scenes：场景列表。
        routes_table：路由表；省略时按「没有任何边」建图。
        options：`NavigateOptions`。

    Returns：
        `navigate.SceneNavigator` 实例。
    """
    scene_graph_instance = scene_graph.SceneGraph.build(scenes, routes_table)
    clock = FakeClock()

    def capture_frame():
        if world.frames_left_before_fail is not None:
            if world.frames_left_before_fail <= 0:
                return None
            world.frames_left_before_fail -= 1
        return "frame"

    def recognize_frame(_frame):
        if world.unrecognizable:
            return FakeMatch("")
        return FakeMatch(world.current)

    def activate_area(scene, area):
        if world.click_raises is not None:
            raise world.click_raises
        world.clicks.append((scene.scene_id, area.name))
        nxt = world.transitions.get((scene.scene_id, area.name))
        if nxt is not None:
            world.current = nxt

    def wait(seconds):
        clock.advance(seconds)

    hooks = navigate.NavigatorHooks(
        capture_frame=capture_frame,
        recognize=recognize_frame,
        activate_area=activate_area,
        wait=wait,
        clock=clock,
    )
    return navigate.SceneNavigator(scene_graph_instance, hooks, options or navigate.NavigateOptions())


class TestIdentify(unittest.TestCase):
    """识别与重试。"""

    def test_recognize_retries_until_hit(self):
        """前几次未命中会重试，最终命中即返回。"""
        world = FakeWorld("scene-0001")
        navigator = _make_navigator(world, *_chain_scenes())

        class Flaky:
            """前两次返回未命中，第三次命中。"""

            def __init__(self):
                """初始化计数。"""
                self.calls = 0

            def __call__(self, _frame):
                """按调用次数返回结果。"""
                self.calls += 1
                return FakeMatch("" if self.calls < 3 else "scene-0001")

        flaky = Flaky()
        navigator.hooks.recognize = flaky
        result = navigator.identify()
        self.assertTrue(result.matched())
        self.assertEqual(flaky.calls, 3)

    def test_identify_raises_when_frames_unavailable(self):
        """持续取帧失败时抛 NavigationError（不是运行失败，是环境不可用）。"""
        world = FakeWorld("scene-0001")
        world.frames_left_before_fail = 0
        navigator = _make_navigator(world, *_chain_scenes())
        with self.assertRaises(navigate.NavigationError):
            navigator.identify()


class TestSuccessPaths(unittest.TestCase):
    """成功路径。"""

    def test_already_at_target(self):
        """已在目标场景时零步返回成功。"""
        world = FakeWorld("scene-0001")
        navigator = _make_navigator(world, *_chain_scenes())
        result = navigator.goto("scene-0001")
        self.assertTrue(result.succeeded())
        self.assertEqual(result.steps, [])
        self.assertEqual(world.clicks, [])

    def test_single_step(self):
        """一步直达。"""
        world = FakeWorld("scene-0001", {("scene-0001", "to_b"): "scene-0002"})
        navigator = _make_navigator(world, *_chain_scenes())
        result = navigator.goto("scene-0002")
        self.assertTrue(result.succeeded())
        self.assertEqual(world.clicks, [("scene-0001", "to_b")])
        self.assertEqual(len(result.steps), 1)
        self.assertTrue(result.steps[0].success)

    def test_two_step_navigation_result(self):
        """两步到达，结果含实际路径与每步耗时。"""
        world = FakeWorld(
            "scene-0001",
            {
                ("scene-0001", "to_b"): "scene-0002",
                ("scene-0002", "to_c"): "scene-0003",
            },
        )
        navigator = _make_navigator(world, *_chain_scenes())
        result = navigator.goto("scene-0003")
        self.assertTrue(result.succeeded())
        self.assertEqual(world.clicks, [("scene-0001", "to_b"), ("scene-0002", "to_c")])
        self.assertEqual(len(result.steps), 2)
        self.assertEqual([step.success for step in result.steps], [True, True])
        for step in result.steps:
            self.assertGreaterEqual(step.elapsed, 0.0)
        self.assertIn("scene-0001", result.path_description())

    def test_result_to_dict(self):
        """结果可序列化，字段齐全。"""
        world = FakeWorld("scene-0001", {("scene-0001", "to_b"): "scene-0002"})
        navigator = _make_navigator(world, *_chain_scenes())
        payload = navigator.goto("scene-0002").to_dict()
        for key in ("status", "target", "start", "reason", "message", "planned", "steps"):
            self.assertIn(key, payload)


class TestFailurePaths(unittest.TestCase):
    """失败路径。"""

    def test_no_path(self):
        """不可达时返回 STATUS_NO_PATH，不抛异常。"""
        scenes = [_scene("scene-0001", []), _scene("scene-0002", [])]
        world = FakeWorld("scene-0001")
        navigator = _make_navigator(world, scenes)
        result = navigator.goto("scene-0002")
        self.assertEqual(result.status, navigate.STATUS_NO_PATH)
        self.assertEqual(world.clicks, [])

    def test_unknown_target_raises(self):
        """目标不在图中属调用方错误，抛异常。"""
        world = FakeWorld("scene-0001")
        navigator = _make_navigator(world, *_chain_scenes())
        with self.assertRaises(navigate.NavigationError):
            navigator.goto("scene-9999")

    def test_empty_target_raises(self):
        """空目标抛异常。"""
        world = FakeWorld("scene-0001")
        navigator = _make_navigator(world, *_chain_scenes())
        with self.assertRaises(navigate.NavigationError):
            navigator.goto("")

    def test_start_not_recognized(self):
        """起点识别不到时返回 STATUS_NOT_IN_LIBRARY。"""
        world = FakeWorld("")
        world.unrecognizable = True
        navigator = _make_navigator(world, *_chain_scenes())
        result = navigator.goto("scene-0002")
        self.assertEqual(result.status, navigate.STATUS_NOT_IN_LIBRARY)
        self.assertEqual(result.reason, navigate.REASON_RECOGNIZE_FAILED)

    def test_click_raises_is_reported_not_thrown(self):
        """点击抛异常时转为步失败记录，不让整个导航抛异常。"""
        world = FakeWorld("scene-0001", {("scene-0001", "to_b"): "scene-0002"})
        world.click_raises = RuntimeError("窗口丢了")
        navigator = _make_navigator(world, *_chain_scenes())
        result = navigator.goto("scene-0002")
        self.assertEqual(result.status, navigate.STATUS_STEP_FAILED)
        self.assertIn(navigate.REASON_EXCEPTION, result.steps[0].note)

    def test_returns_failure_not_exception_after_timeout(self):
        """点击后画面不变（步超时）时返回失败结果而非抛异常。"""
        world = FakeWorld("scene-0001")  # 无转移表：点了不改变场景
        navigator = _make_navigator(world, *_chain_scenes())
        result = navigator.goto("scene-0002")
        self.assertEqual(result.status, navigate.STATUS_STEP_FAILED)
        self.assertTrue(result.steps)
        self.assertFalse(result.succeeded())

    def test_arriving_at_unexpected_scene_fails_step(self):
        """点 A 的区域却到了 C，该步判失败并记录实际场景。"""
        scenes = [
            _scene("scene-0001", [_area("to_b")]),
            _scene("scene-0002", []),
            _scene("scene-0003", []),
        ]
        table = _routes([("scene-0001", "to_b", "scene-0002")])
        world = FakeWorld("scene-0001", {("scene-0001", "to_b"): "scene-0003"})
        navigator = _make_navigator(world, scenes, table)
        result = navigator.goto("scene-0002")
        self.assertFalse(result.succeeded())
        self.assertEqual(result.steps[0].actual, "scene-0003")

    def test_missing_area_recorded(self):
        """计划里的区域在场景中不存在时记 AREA_MISSING。"""
        scenes = [
            _scene("scene-0001", [_area("to_b")]),
            _scene("scene-0002", []),
        ]
        table = _routes([("scene-0001", "to_b", "scene-0002")])
        world = FakeWorld("scene-0001", {("scene-0001", "to_b"): "scene-0002"})
        navigator = _make_navigator(world, scenes, table)
        # 建好图后删掉区域，模拟「图与场景数据不一致」
        navigator.scene_graph.scenes["scene-0001"].areas = []
        result = navigator.goto("scene-0002")
        self.assertEqual(result.status, navigate.STATUS_STEP_FAILED)
        self.assertEqual(result.steps[0].note, navigate.REASON_AREA_MISSING)


class TestRollback(unittest.TestCase):
    """回退。"""

    def test_rollback_then_retry_succeeds(self):
        """第一步失败后回退成功，重新规划后走通。"""
        scenes = [
            _scene("scene-0001", [_area("to_b")]),
            _scene("scene-0002", [_area("back"), _area("to_c")], return_area="back"),
            _scene("scene-0003", []),
        ]
        table = _routes(
            [
                ("scene-0001", "to_b", "scene-0002"),
                ("scene-0002", "back", "scene-0001"),
                ("scene-0002", "to_c", "scene-0003"),
            ]
        )
        # 第一次点 to_b 到 B；在 B 点 to_c 失败一次（模拟动画慢），回退后第二次成功
        world = FakeWorld("scene-0001", {("scene-0001", "to_b"): "scene-0002"})

        call_state = {"to_c_attempts": 0}

        scene_graph_instance = scene_graph.SceneGraph.build(scenes, table)
        clock = FakeClock()

        def capture_frame():
            return "frame"

        def recognize_frame(_frame):
            return FakeMatch(world.current)

        def activate_area(scene, area):
            world.clicks.append((scene.scene_id, area.name))
            if area.name == "back":
                world.current = "scene-0001"
                return
            if area.name == "to_b":
                world.current = "scene-0002"
                return
            if area.name == "to_c":
                call_state["to_c_attempts"] += 1
                if call_state["to_c_attempts"] >= 2:
                    world.current = "scene-0003"

        hooks = navigate.NavigatorHooks(
            capture_frame=capture_frame,
            recognize=recognize_frame,
            activate_area=activate_area,
            wait=lambda s: clock.advance(s),
            clock=clock,
        )
        navigator = navigate.SceneNavigator(
            scene_graph_instance, hooks, navigate.NavigateOptions(step_timeout=2.0, poll_interval=0.5)
        )
        result = navigator.goto("scene-0003")
        self.assertTrue(result.succeeded())
        self.assertIn(("scene-0002", "back"), world.clicks)

    def test_rollback_failure_aborts(self):
        """回退也失败时中止，返回失败结果。"""
        world = FakeWorld("scene-0001")  # 点什么都不切换
        navigator = _make_navigator(world, *_chain_scenes())
        result = navigator.goto("scene-0002")
        self.assertEqual(result.status, navigate.STATUS_STEP_FAILED)
        self.assertFalse(result.succeeded())

    def test_zero_rollback_limit_skips_rollback(self):
        """回退次数上限为 0 时不尝试回退。"""
        world = FakeWorld("scene-0001")
        navigator = _make_navigator(world, *_chain_scenes(), options=navigate.NavigateOptions(rollback_limit=0))
        result = navigator.goto("scene-0002")
        self.assertEqual(result.status, navigate.STATUS_STEP_FAILED)
        # 只点了计划里的那一次，没有回退点击
        self.assertEqual(world.clicks, [("scene-0001", "to_b")])


class TestCancel(unittest.TestCase):
    """中止。"""

    def test_stop_before_first_step(self):
        """should_stop 一开始就为真时直接返回取消。"""
        world = FakeWorld("scene-0001", {("scene-0001", "to_b"): "scene-0002"})
        navigator = _make_navigator(world, *_chain_scenes())
        result = navigator.goto("scene-0002", should_stop=lambda: True)
        self.assertEqual(result.status, navigate.STATUS_CANCELLED)
        self.assertEqual(world.clicks, [])

    def test_stop_midway(self):
        """走完第一步后中止，不再继续第二步。

        `should_stop` 在每步的轮询循环里都会被调用，因此不能用「调用次数」判定，
        改用「第一次点击已发生」作为中止条件——这正好对应「用户点了停止按钮」。
        """
        world = FakeWorld(
            "scene-0001",
            {
                ("scene-0001", "to_b"): "scene-0002",
                ("scene-0002", "to_c"): "scene-0003",
            },
        )
        navigator = _make_navigator(world, *_chain_scenes())
        base_goto = navigator.goto

        def should_stop():
            return len(world.clicks) >= 1

        result = base_goto("scene-0003", should_stop=should_stop)
        self.assertEqual(result.status, navigate.STATUS_CANCELLED)
        self.assertEqual(world.clicks, [("scene-0001", "to_b")])


class TestHelpers(unittest.TestCase):
    """辅助函数。"""

    def test_plan_preview_success(self):
        """预览给出路径。"""
        instance = scene_graph.SceneGraph.build(*_chain_scenes())
        steps, error = navigate.plan_preview(instance, "scene-0001", "scene-0002")
        self.assertIsNone(error)
        self.assertEqual(len(steps), 1)

    def test_plan_preview_unreachable(self):
        """不可达时给出错误说明而非抛异常。"""
        instance = scene_graph.SceneGraph.build(*_chain_scenes())
        steps, error = navigate.plan_preview(instance, "scene-0003", "scene-0001")
        self.assertIsNone(steps)
        self.assertIn("不可达", error)

    def test_plan_preview_unknown_scene(self):
        """场景不存在时返回错误说明。"""
        instance = scene_graph.SceneGraph.build(*_chain_scenes())
        steps, error = navigate.plan_preview(instance, "scene-9999", "scene-0001")
        self.assertIsNone(steps)
        self.assertIn("scene-9999", error)

    def test_describe_scene(self):
        """场景描述含名称、id、特征数与出边数。"""
        instance = scene_graph.SceneGraph.build(*_chain_scenes())
        text = navigate.describe_scene(instance, "scene-0001")
        self.assertIn("scene-0001", text)
        self.assertIn("1 条出边", text)
        self.assertIn("未知场景", navigate.describe_scene(instance, "scene-9999"))


if __name__ == "__main__":
    unittest.main()
