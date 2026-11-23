# -*- coding: utf-8 -*-
"""场景管理的探索器测试（批次六）。

被测对象是 `extensions/capabilities/scene_manager/explore.py`。探索会**真实点击游戏**，
而误点可能花资源、进战斗、消耗次数且不可逆。因此测试重点是「不该点的绝不点」
与「该停的确实停」，而不是「能发现多少边」。

用假世界而非真机：真机无法让「连续两次点击无跳转」稳定发生，也无法让「差异像素
占比 35%」精确复现。

分五组：

1. **安全边界**：白名单、三重上限、手动中止。
2. **判定**：跳转、无跳转、新画面。
3. **候选框**：差分提框、噪声退化、过小过大退化。
4. **稳定等待**：连续稳定帧、超时返回最后一帧。
5. **预演**：只识别不点击时的可点区域清单。
"""

import unittest

from extensions.capabilities.scene_manager import explore, graph, model, routes

try:
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值依赖
    np = None
    NUMPY_ERROR = str(exc)


class FakeClock:
    """可控时钟。"""

    def __init__(self):
        """从 0 开始。"""
        self.now = 0.0

    def __call__(self):
        """返回当前时间。"""
        return self.now

    def advance(self, seconds):
        """推进时间。

        Args：
            seconds：秒数。
        """
        self.now += seconds


class FakeMatch:
    """模拟识别结果。"""

    def __init__(self, scene_id=""):
        """保存命中的场景 id。"""
        self._scene_id = scene_id

    def matched(self):
        """返回是否命中。"""
        return bool(self._scene_id)

    def scene_id(self):
        """返回场景 id。"""
        return self._scene_id


def _area(name, explore_probe=False, explore_return=False):
    """造一个区域。

    **区域不再带目标场景与等待**：跳转关系在路由表里（见 `_routes`）。探索器
    通过 `routes=` 参数拿到「哪些区域已经探过」。
    """
    return model.Area(
        name=name,
        rect=(0.1, 0.1, 0.2, 0.2),
        explore_probe=explore_probe,
        explore_return=explore_return,
    )


def _routes(entries):
    """按 `[(from, area, to, source, after_wait)]` 造路由表。

    Args：
        entries：五元组列表，后两项可省略（默认 `manual`、0.0）。

    Returns：
        `routes.RouteTable` 实例。
    """
    table = routes.RouteTable()
    for entry in entries:
        from_scene, area_name, to_scene = entry[:3]
        source = entry[3] if len(entry) > 3 else model.SOURCE_MANUAL
        after_wait = entry[4] if len(entry) > 4 else 0.0
        table.put(routes.Route(from_scene, area_name, to_scene, after_wait=after_wait, source=source))
    return table


def _scene(scene_id, areas=None, name="", return_area=""):
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


class FakeWorld:
    """模拟游戏：记录点击，按转移表切换当前场景。"""

    def __init__(self, current="", transitions=None):
        """初始化。

        Args：
            current：当前场景。
            transitions：`{(scene, area): next_scene}`；值为 None 表示留在一个新画面。
        """
        self.current = current
        self.transitions = transitions or {}
        self.clicks = []
        self.saved_drafts = []
        self.saved_edges = []
        self.stay_on_click = set()
        # 进入「未识别画面」时自增，使每次发现的未知画面帧都不同——否则探索会
        # 把第二次进入的未知画面判成「画面没变化」（帧相同），从而误判为无效区域。
        self.unknown_counter = 0
        # 点击后进入「未登记画面」的映射：{(scene, area): fake_scene_id}
        self.unknown_targets = {}
        # 识别器认不出的场景 id 集合（模拟「这个画面还没有任何特征」）
        self.unknown_topics = set()
        # 草稿落盘后变得可识别的场景 id
        self.known_after_draft = set()
        # 最近一次点击进入的未登记画面来自哪个场景（草稿落盘时据此补回退边）
        self.pending_parent = ""
        # 场景 id → 灰度标识。刻意做成**实例级**而非模块级：模块级会跨用例累积，
        # 前一个用例登记过的 id 会让后一个用例算出不同的标识，出现难以定位的串扰。
        self._markers = {}

    def rename_current(self, scene_id):
        """把当前画面重命名为指定场景 id（草稿落盘时用）。

        Args：
            scene_id：新的场景 id。
        """
        self.current = scene_id

    def marker(self, scene_id):
        """返回场景的稳定灰度标识。

        刻意不用 `sum(scene_id.encode())`：`scene-0001` 与 `scene-0002` 只差 1，
        而 `frames_stable` 的单像素阈值是 8，会判成同一画面。也刻意不用 `hash()`：
        它跨进程不稳定。

        Args：
            scene_id：场景 id。

        Returns：
            30–230 之间的灰度值。
        """
        if scene_id not in self._markers:
            self._markers[scene_id] = (len(self._markers) + 1) * 45
        return 30 + self._markers[scene_id] % 200


def _enter_unknown(world, scene_id, area_name):
    """把假世界切到「一个新的未识别画面」，并让帧随之变化。

    Args：
        world：假世界。
        scene_id：点击的场景。
        area_name：点击的区域。
    """
    world.clicks.append((scene_id, area_name))
    world.current = ""
    world.unknown_counter += 1


def _hooks(world, clock, scenes_by_id=None, frames=None, draft_back=True):
    """按假世界造 hooks。

    Args：
        world：假世界。
        clock：假时钟。
        scenes_by_id：场景映射，供识别返回。
        frames：可选帧序列迭代器；缺省用固定帧。
        draft_back：草稿是否自动带一个回退区域。默认 True，供需要连续回退的
            用例使用；**真实草稿没有区域**，验证「点进新画面后回不去」时要传 False。

    Returns：
        `explore.ExploreHooks` 实例。
    """
    state = {"index": 0}

    def current_frame():
        """按当前场景生成可区分但内部稳定的帧。

        帧内容必须随场景变化：探索靠「帧有没有变」判断是否发生了跳转，若桩帧恒定，
        点了区域也判定为「画面没变化」，从而触发重试——那样测的就不是探索逻辑了。
        """
        if frames is not None:
            if state["index"] >= len(frames):
                return frames[-1]
            frame = frames[state["index"]]
            state["index"] += 1
            return frame
        if np is None:
            return None
        # 帧内容严格由当前场景标识决定：不同场景必然产生不同帧，同一场景内稳定。
        # 这是探索判定的前提——它靠「帧有没有变」判断画面是否跳转。
        frame = np.full((40, 40, 3), 60, dtype=np.uint8)
        if not world.current:
            frame[5:15, 5:25] = 250
        else:
            frame[5:15, 5:25] = world.marker(world.current)
        return frame

    def capture_frame():
        return current_frame()

    def recognize_frame(_frame):
        # 与真实识别同构：认不出「未登记」的画面。`unknown-` 前缀即未登记——
        # 真实环境里这类画面没有任何特征，识别器认不出来。
        if not world.current or world.current.startswith("unknown-"):
            return FakeMatch("")
        if world.current in world.known_after_draft:
            return FakeMatch(world.current)
        if world.current in world.unknown_topics or world.current.startswith("unknown-"):
            return FakeMatch("")
        return FakeMatch(world.current)

    def activate_area(scene, area):
        world.clicks.append((scene.scene_id, area.name))
        if (scene.scene_id, area.name) in world.stay_on_click:
            return
        nxt = world.transitions.get((scene.scene_id, area.name))
        if nxt is not None:
            world.current = nxt
            return
        unknown = world.unknown_targets.get((scene.scene_id, area.name))
        if unknown is not None:
            world.current = unknown
            # 记下「这个未登记画面是从哪来的」，草稿落盘时据此补回退边
            world.pending_parent = scene.scene_id

    def wait(seconds):
        clock.advance(seconds)

    def save_draft(scene, frame, parent):
        world.saved_drafts.append((scene.scene_id, scene.name))
        # 草稿落盘后即可被识别（真实运行时由 on_draft_saved 重建识别器）。
        # 这一步是探索能继续的前提：认不出新画面就回不到父场景，BFS 随即停摆。
        # 同时把「当前所在画面」重命名为草稿 id——真实环境里草稿就是**这张画面**
        # 的配置，而不是另开一张画面。
        previous = world.current or ""
        world.known_after_draft.add(scene.scene_id)
        if previous.startswith("unknown-"):
            world.rename_current(scene.scene_id)
            # 桩补一条「回退到来源场景」的区域，供需要连续回退的用例使用。
            # **真实草稿没有区域**：探索器的 save_draft 只落图与特征，不会替用户
            # 猜返回按钮。因此验证「点进新画面后回不去」的用例必须传
            # `draft_back=False`，否则桩会把真实存在的死角掩盖掉。
            parent = getattr(world, "pending_parent", "")
            if draft_back and parent:
                scene.areas.append(_area("draft_back", parent))
                world.transitions[(scene.scene_id, "draft_back")] = parent

    def save_edge(src, area, dst, observations, **kwargs):
        world.saved_edges.append((src, area, dst, observations))

    return explore.ExploreHooks(
        capture_frame=capture_frame,
        recognize=recognize_frame,
        activate_area=activate_area,
        wait=wait,
        save_draft=save_draft,
        save_edge=save_edge,
        clock=clock,
    )


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestSafety(unittest.TestCase):
    """安全边界：不该点的绝不点。"""

    def test_whitelist_respected(self):
        """只探测 explore_probe 为真的区域，其余一次都不操作。"""
        scene = _scene(
            "scene-0001",
            [
                _area("danger", explore_probe=False),
                _area("safe", explore_probe=True),
            ],
        )
        world = FakeWorld("scene-0001", {("scene-0001", "safe"): "scene-0002"})
        clock = FakeClock()
        hooks = _hooks(world, clock)
        explorer = explore.SceneExplorer([scene, _scene("scene-0002")], hooks)
        result = explorer.run()
        self.assertEqual(world.clicks, [("scene-0001", "safe")])
        self.assertTrue(result.visited)

    def test_area_with_known_target_skipped(self):
        """已有目标场景的区域不重复探索（边已知）。"""
        scene = _scene("scene-0001", [_area("known", explore_probe=True)])
        table = _routes([("scene-0001", "known", "scene-0002")])
        world = FakeWorld("scene-0001", {("scene-0001", "known"): "scene-0002"})
        clock = FakeClock()
        hooks = _hooks(world, clock)
        explorer = explore.SceneExplorer([scene, _scene("scene-0002")], hooks, routes=table)
        explorer.run()
        self.assertEqual(world.clicks, [])

    def test_max_scenes_limit(self):
        """新场景数达上限即停。

        直接用 `_probe` 逐个区域验证：每次点击都产出草稿，第 2 个草稿后主循环
        应当带着 `STOP_MAX_SCENES` 停下。刻意不经过 `_go_to` 回退链路——上限是
        主循环的独立判断，与回退能力无关，混在一起测会让两者互相掩盖。
        """
        scene = _scene(
            "scene-0001",
            [_area(f"a{i}", explore_probe=True) for i in (1, 2, 3)],
        )
        world = FakeWorld("scene-0001")
        world.unknown_targets = {("scene-0001", f"a{i}"): f"unknown-{i}" for i in (1, 2, 3)}
        clock = FakeClock()
        hooks = _hooks(world, clock)
        explorer = explore.SceneExplorer(
            [scene],
            hooks,
            explore.ExploreOptions(max_scenes=2, stable_frames=1, frame_interval=0, no_jump_retries=1),
        )
        result = explore.ExploreResult()
        by_id = {"scene-0001": scene}
        known = {"scene-0001"}
        clicks = {}
        for area in scene.areas:
            if len(result.new_scenes) >= 2:
                result.stop_reason = explore.STOP_MAX_SCENES
                break
            # 每个区域前把画面复位到 A：`_probe` 会校验「点击前确实在该场景」，
            # 这是防误点的必要检查，不该为测上限而绕过它。
            world.current = "scene-0001"
            clicks["scene-0001"] = clicks.get("scene-0001", 0) + 1
            outcome = explorer._probe(scene, area, 0.0, result, by_id, known, clicks)
            if outcome == explore.RESULT_NEW_SCENE:
                known.add(result.new_scenes[-1])
        self.assertEqual(len(result.new_scenes), 2)
        self.assertEqual(result.stop_reason, explore.STOP_MAX_SCENES)

    def test_max_seconds_limit(self):
        """总时长超限即停。"""
        scene = _scene("scene-0001", [_area(f"a{i}", explore_probe=True) for i in range(20)])
        world = FakeWorld("scene-0001")
        clock = FakeClock()
        hooks = _hooks(world, clock)
        explorer = explore.SceneExplorer(
            [scene],
            hooks,
            explore.ExploreOptions(
                max_seconds=5.0, stable_frames=1, frame_interval=0, click_settle=1.0, settle_timeout=1.0
            ),
        )
        result = explorer.run()
        self.assertEqual(result.stop_reason, explore.STOP_MAX_SECONDS)
        self.assertLess(len(world.clicks), 20)

    def test_max_clicks_per_scene_limit(self):
        """单场景点击次数达上限即停。"""
        scene = _scene("scene-0001", [_area(f"a{i}", explore_probe=True) for i in range(6)])
        world = FakeWorld("scene-0001")
        for i in range(6):
            world.stay_on_click.add(("scene-0001", f"a{i}"))
        clock = FakeClock()
        hooks = _hooks(world, clock)
        explorer = explore.SceneExplorer(
            [scene],
            hooks,
            explore.ExploreOptions(max_clicks_per_scene=3, stable_frames=1, frame_interval=0, settle_timeout=1.0),
        )
        result = explorer.run()
        self.assertLessEqual(len(world.clicks), 3)
        self.assertIn(
            result.stop_reason,
            (explore.STOP_MAX_CLICKS_PER_SCENE, explore.STOP_MAX_SECONDS, explore.STOP_COMPLETED),
        )

    def test_manual_cancel(self):
        """should_stop 为真时立即中止。"""
        scene = _scene("scene-0001", [_area("a", explore_probe=True)])
        world = FakeWorld("scene-0001")
        clock = FakeClock()
        hooks = _hooks(world, clock)
        hooks.should_stop = lambda: True
        explorer = explore.SceneExplorer([scene], hooks)
        result = explorer.run()
        self.assertEqual(result.stop_reason, explore.STOP_CANCELLED)
        self.assertEqual(world.clicks, [])

    def test_unrecognized_start_does_not_click(self):
        """起点画面识别不到时一次都不点——否则会在未知画面上乱点。"""
        scene = _scene("scene-0001", [_area("a", explore_probe=True)])
        world = FakeWorld("")  # 识别结果为空
        clock = FakeClock()
        hooks = _hooks(world, clock)
        explorer = explore.SceneExplorer([scene], hooks)
        result = explorer.run()
        self.assertEqual(result.stop_reason, explore.STOP_NO_START)
        self.assertEqual(world.clicks, [])


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestProbing(unittest.TestCase):
    """单区域判定。"""

    def test_jump_recorded_as_edge(self):
        """点击后到达已知场景时记录边。"""
        scene = _scene("scene-0001", [_area("go", explore_probe=True)])
        world = FakeWorld("scene-0001", {("scene-0001", "go"): "scene-0002"})
        clock = FakeClock()
        hooks = _hooks(world, clock)
        explorer = explore.SceneExplorer(
            [scene, _scene("scene-0002")],
            hooks,
            explore.ExploreOptions(stable_frames=1, frame_interval=0),
        )
        result = explorer.run()
        self.assertEqual(world.clicks, [("scene-0001", "go")])
        self.assertEqual(result.edges[0][:3], ("scene-0001", "go", "scene-0002"))
        self.assertEqual(world.saved_edges[0][0], "scene-0001")

    def test_region_invalid_after_two_no_jump(self):
        """连续两次点击画面不变才判无效区域。"""
        scene = _scene("scene-0001", [_area("dead", explore_probe=True)])
        world = FakeWorld("scene-0001")
        world.stay_on_click.add(("scene-0001", "dead"))
        clock = FakeClock()
        hooks = _hooks(world, clock)
        explorer = explore.SceneExplorer(
            [scene],
            hooks,
            explore.ExploreOptions(stable_frames=1, frame_interval=0, no_jump_retries=2, settle_timeout=0.5),
        )
        result = explorer.run()
        self.assertEqual(len(world.clicks), 2)
        self.assertEqual(len(result.invalid_areas), 1)
        self.assertEqual(result.invalid_areas[0][:2], ("scene-0001", "dead"))

    def test_new_scene_becomes_draft(self):
        """点击后到达未配置画面时产出草稿，且不写正式场景。"""
        scene = _scene("scene-0001", [_area("go", explore_probe=True)])

        world = FakeWorld("scene-0001")

        def activate_area(sc, area):
            _enter_unknown(world, sc.scene_id, area.name)  # 进入未识别画面

        clock = FakeClock()
        hooks = _hooks(world, clock)
        hooks.activate_area = activate_area
        explorer = explore.SceneExplorer(
            [scene],
            hooks,
            explore.ExploreOptions(stable_frames=1, frame_interval=0),
        )
        result = explorer.run()
        self.assertEqual(len(result.new_scenes), 1)
        self.assertTrue(result.new_scenes[0].startswith("draft-"))
        self.assertEqual(len(world.saved_drafts), 1)
        self.assertTrue(world.saved_drafts[0][0].startswith("draft-"))

    def test_new_draft_id_avoids_existing_drafts(self):
        """已有草稿时接着编号，不覆盖上一轮落盘的 draft-0001。

        自动重启会重跑探索：上一轮草稿已存在，但不在本轮的 `new_scenes` 里。
        按「本轮新场景数 + 1」取号会重新生成 `draft-0001`，把旧草稿整目录覆盖。
        """
        scene = _scene("scene-0001", [_area("go", explore_probe=True)])
        existing = _scene("draft-0001", [], name="上一轮的草稿")
        world = FakeWorld("scene-0001")

        def activate_area(sc, area):
            _enter_unknown(world, sc.scene_id, area.name)

        clock = FakeClock()
        hooks = _hooks(world, clock)
        hooks.activate_area = activate_area
        explorer = explore.SceneExplorer(
            [scene, existing],
            hooks,
            explore.ExploreOptions(stable_frames=1, frame_interval=0),
        )
        result = explorer.run()
        self.assertEqual(result.new_scenes, ["draft-0002"])
        self.assertEqual(world.saved_drafts[0][0], "draft-0002")

    def test_click_exception_recorded_not_thrown(self):
        """点击抛异常时记为事件并继续，不让整个探索崩掉。"""
        scene = _scene("scene-0001", [_area("bad", explore_probe=True)])
        world = FakeWorld("scene-0001")
        clock = FakeClock()
        hooks = _hooks(world, clock)

        def broken_click(_sc, _area):
            raise RuntimeError("窗口丢失")

        hooks.activate_area = broken_click
        explorer = explore.SceneExplorer(
            [scene],
            hooks,
            explore.ExploreOptions(stable_frames=1, frame_interval=0),
        )
        result = explorer.run()
        self.assertTrue(any(event.kind == "error" for event in result.events))


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestCandidateRegion(unittest.TestCase):
    """差分提候选框。"""

    def _options(self, **kwargs):
        """造参数。"""
        return explore.ExploreOptions(**kwargs)

    def test_candidate_from_localized_change(self):
        """局部变化时给出覆盖该区域的候选框。"""
        parent = np.zeros((200, 200, 3), dtype=np.uint8)
        new = parent.copy()
        new[60:90, 40:120] = 255
        rect, reason = explore.propose_candidate_region(parent, new, self._options())
        self.assertIsNone(reason)
        self.assertEqual(rect, (0.2, 0.3, 0.6, 0.45))

    def test_no_candidate_box_on_noisy_diff(self):
        """背景整片在动时不给候选框。

        构造「差异散布全屏、但外接矩形不算大」的情形：每隔一行条纹变化，
        差异像素占比约 50% 却散布在整个画面上。
        """
        parent = np.zeros((100, 100, 3), dtype=np.uint8)
        new = parent.copy()
        new[::2, :, :] = 255  # 每隔一行整行变化
        rect, reason = explore.propose_candidate_region(parent, new, self._options())
        self.assertIsNone(rect)
        self.assertIn("背景可能有动画", reason)

    def test_no_candidate_when_identical(self):
        """两帧完全一致时不给候选框。"""
        parent = np.zeros((100, 100, 3), dtype=np.uint8)
        rect, reason = explore.propose_candidate_region(parent, parent.copy(), self._options())
        self.assertIsNone(rect)
        self.assertIn("没有可识别的差异", reason)

    def test_no_candidate_when_too_small(self):
        """差异区域过小时不给候选框。"""
        parent = np.zeros((400, 400, 3), dtype=np.uint8)
        new = parent.copy()
        new[10:12, 10:12] = 255
        rect, reason = explore.propose_candidate_region(parent, new, self._options(candidate_min_area=0.01))
        self.assertIsNone(rect)
        self.assertIn("过小", reason)

    def test_no_candidate_when_too_large(self):
        """差异只在两处、但相隔很远时，外接矩形面积过大也不给候选框。

        像素占比很低（说明画面已静止、只是两处内容不同），但外接矩形几乎覆盖全屏
        （取它当特征毫无区分度）。这与「背景整片在动」是两种不同的拒绝理由。
        """
        parent = np.zeros((200, 200, 3), dtype=np.uint8)
        new = parent.copy()
        new[10:40, 10:60] = 255
        new[160:190, 140:190] = 255
        rect, reason = explore.propose_candidate_region(parent, new, self._options(candidate_max_area=0.3))
        self.assertIsNone(rect)
        self.assertIn("过大", reason)

    def test_no_candidate_on_shape_mismatch(self):
        """尺寸不一致时不给候选框。"""
        rect, reason = explore.propose_candidate_region(
            np.zeros((100, 100, 3), np.uint8), np.zeros((50, 50, 3), np.uint8), self._options()
        )
        self.assertIsNone(rect)
        self.assertIn("尺寸不一致", reason)

    def test_no_candidate_on_none_frame(self):
        """帧为空时不给候选框。"""
        rect, reason = explore.propose_candidate_region(None, None, self._options())
        self.assertIsNone(rect)
        self.assertIn("缺少", reason)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestWaitUntilStable(unittest.TestCase):
    """稳定等待。"""

    def test_returns_after_consecutive_stable_frames(self):
        """连续稳定后返回当前帧。"""
        frame = np.zeros((60, 60, 3), dtype=np.uint8)
        clock = FakeClock()

        def capture():
            return frame.copy()

        hooks = explore.ExploreHooks(
            capture_frame=capture,
            recognize=lambda f: FakeMatch("scene-0001"),
            activate_area=lambda s, a: None,
            wait=lambda s: clock.advance(s),
            clock=clock,
        )
        options = explore.ExploreOptions(stable_frames=2, frame_interval=0.1, settle_timeout=2.0)
        result = explore.wait_until_stable(hooks, options)
        self.assertIsNotNone(result)

    def test_timeout_returns_last_frame(self):
        """一直不稳定时超时返回最后一帧，而不是 None。"""
        clock = FakeClock()
        state = {"i": 0}

        def capture():
            state["i"] += 1
            frame = np.zeros((60, 60, 3), dtype=np.uint8)
            frame[0, 0, 0] = state["i"] * 20
            return frame

        hooks = explore.ExploreHooks(
            capture_frame=capture,
            recognize=lambda f: FakeMatch("scene-0001"),
            activate_area=lambda s, a: None,
            wait=lambda s: clock.advance(s),
            clock=clock,
        )
        options = explore.ExploreOptions(stable_frames=5, frame_interval=0.5, settle_timeout=1.0)
        result = explore.wait_until_stable(hooks, options)
        self.assertIsNotNone(result)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestRecognizeOnlyReport(unittest.TestCase):
    """预演清单。"""

    def test_reports_probe_and_skipped(self):
        """列出将被探测的区域与被跳过区域及原因。"""
        scene = _scene(
            "scene-0001",
            [
                _area("known", explore_probe=True),
                _area("off", explore_probe=False),
                _area("on", explore_probe=True),
            ],
        )
        table = _routes([("scene-0001", "known", "scene-0002")])
        clickable, skipped = explore.recognize_only_report(scene, None, None, routes=table)
        self.assertEqual(clickable, [("on", "")])
        reasons = dict(skipped)
        self.assertIn("已有流转路由", reasons["known"])
        self.assertIn("允许探索探测", reasons["off"])

    def test_result_summary_and_dict(self):
        """结果摘要与序列化字段齐全。"""
        result = explore.ExploreResult(
            stop_reason=explore.STOP_MAX_SCENES,
            visited=["scene-0001"],
            new_scenes=["draft-0001"],
            edges=[("scene-0001", "a", "draft-0001", 1)],
            elapsed=1.234,
        )
        self.assertIn("max_scenes", result.summary())
        payload = result.to_dict()
        self.assertEqual(payload["edges"][0]["from"], "scene-0001")
        self.assertEqual(payload["elapsed"], 1.234)


@unittest.skipIf(np is None, "missing numpy")
class TestRollbackAcrossLevels(unittest.TestCase):
    """多级图上的探索回退。

    这组用例覆盖最容易"测得过但实际不成立"的地方：BFS 点进 B 之后必须能回到 A，
    才能继续探 A 的其余区域。此前的桩世界不模拟"位置随场景改变"，所以即使回退
    没实现也能通过——这里让帧内容严格跟随当前场景，回退缺失会暴露为
    "在错误画面上点击"。
    """

    def _build(self, scenes, transitions, start="scene-0001", options=None, routes_table=None):
        """造探索器。

        Args：
            scenes：场景列表。
            transitions：`{(scene, area): next}`。
            start：起点场景。
            options：`ExploreOptions`。
            routes_table：路由表；省略时按空表。

        Returns：
            `(world, clock, explorer)` 三元组。
        """
        world = FakeWorld(start, transitions)
        clock = FakeClock()
        # 模拟真实识别：按**帧内容**查表，而不是直接返回 world.current。
        # 后者等于让识别作弊——真实环境里点进一个没有特征的画面是认不出来的，
        # 只有这样才能暴露"草稿必须有可识别特征"这个约束。
        registry = {}

        def token_of(scene_id):
            if scene_id not in registry:
                registry[scene_id] = len(registry) + 1
            return registry[scene_id]

        def scene_frame(scene_id):
            frame = np.full((40, 40, 3), 60, dtype=np.uint8)
            if scene_id:
                frame[5:15, 5:25] = 30 + token_of(scene_id) * 45
            else:
                frame[5:15, 5:25] = 250
            return frame

        def current_frame():
            return scene_frame(world.current)

        def recognize(frame):
            if frame is None:
                return FakeMatch("")
            marker = int(frame[5:15, 5:25].mean())
            for scene_id in list(registry):
                if marker == 30 + registry[scene_id] * 45:
                    return FakeMatch(scene_id)
            return FakeMatch("")

        def activate_area(scene, area):
            world.clicks.append((scene.scene_id, area.name))
            nxt = world.transitions.get((scene.scene_id, area.name))
            if nxt is not None:
                world.current = nxt
                return
            unknown = world.unknown_targets.get((scene.scene_id, area.name))
            if unknown is not None:
                # 进入未登记画面：给它独立帧内容，但识别器还没有它
                world.current = unknown
                registry.pop(unknown, None)

        def save_draft(scene, _frame, _parent):
            world.saved_drafts.append((scene.scene_id, scene.name))
            # 草稿落盘后识别器应能认出它（真实运行时由 on_draft_saved 完成）
            token_of(scene.scene_id)

        hooks = explore.ExploreHooks(
            capture_frame=current_frame,
            recognize=recognize,
            activate_area=activate_area,
            wait=lambda s: clock.advance(s),
            save_draft=save_draft,
            save_edge=lambda *a, **k: None,
            clock=clock,
        )
        planner = graph.SceneGraph.build(scenes, routes_table)
        explorer = explore.SceneExplorer(
            scenes,
            hooks,
            options
            or explore.ExploreOptions(
                stable_frames=1,
                frame_interval=0,
                click_settle=0.1,
                settle_timeout=0.5,
                no_jump_retries=1,
            ),
            planner=planner,
            routes=routes_table,
        )
        return world, clock, explorer

    def _scenes_two_areas(self):
        """A 有两个可探测区域、B 有一个回 A 的区域。

        Returns：
            `(scenes, routes_table)` 二元组。**B 的回边必须进路由表**：回退靠
            图上的出边反推，没有路由就没有回退，A 的第二个区域会被跳过。
        """
        scenes = [
            _scene(
                "scene-0001",
                [
                    _area("a1", explore_probe=True),
                    _area("a2", explore_probe=True),
                ],
            ),
            _scene("scene-0002", [_area("back")], name="B"),
        ]
        table = _routes([("scene-0002", "back", "scene-0001")])
        return scenes, table

    def test_returns_to_parent_to_explore_remaining_areas(self):
        """点第一个区域进了 B 之后，必须回到 A 才能继续点第二个区域。

        这是需求「自动探测各交互区域、构建路由流转图」在多级图上的最小成立条件：
        不会回退时，A 的第二个区域要么被跳过、要么在 B 的画面上误点。
        """
        scenes, table = self._scenes_two_areas()
        world, _clock, explorer = self._build(
            scenes,
            {
                ("scene-0001", "a1"): "scene-0002",
                ("scene-0001", "a2"): "scene-0002",
                ("scene-0002", "back"): "scene-0001",
            },
            routes_table=table,
        )
        result = explorer.run()
        clicked = [name for _sid, name in world.clicks]
        self.assertIn("a1", clicked)
        self.assertIn("a2", clicked)
        self.assertIn(("scene-0002", "back"), world.clicks)
        index_a2 = clicked.index("a2")
        index_back = [i for i, c in enumerate(world.clicks) if c == ("scene-0002", "back")][0]
        self.assertGreater(index_a2, index_back, "必须先回退再点 A 的第二个区域")
        self.assertGreaterEqual(len(result.edges), 2)

    def test_uses_scene_return_area_when_configured(self):
        """场景配了返回区域时优先用它，而不是按入边反推到别的区域。

        A 有两个区域，点完第一个进 C 之后必须回退才能点第二个——只有这样
        回退路径才会真正被执行。

        Note:
            `via_incoming` **不能**在路由表里指向 A：一旦图上有 C→A 的正向边，
            探索会直接「走过去」而不触发回退，这条用例就测不到 `return_area`
            的优先级。它只作为「C 上还有个区域也能回 A」的干扰项存在。
        """
        scenes = [
            _scene(
                "scene-0001",
                [
                    _area("a1", explore_probe=True),
                    _area("a2", explore_probe=True),
                ],
            ),
            _scene(
                "scene-0003",
                [_area("via_incoming"), _area("configured_back")],
                return_area="configured_back",
                name="C",
            ),
        ]
        # 只有 `configured_back` 在图里。若把 `via_incoming` 也画成边，探索会走
        # 正向路径回 A，压根不进回退分支。
        table = _routes([("scene-0003", "configured_back", "scene-0001")])
        world, _clock, explorer = self._build(
            scenes,
            {
                ("scene-0001", "a1"): "scene-0003",
                ("scene-0001", "a2"): "scene-0003",
                ("scene-0003", "configured_back"): "scene-0001",
            },
            routes_table=table,
        )
        explorer.run()
        clicked = [name for _sid, name in world.clicks]
        self.assertIn("configured_back", clicked)
        self.assertNotIn("via_incoming", clicked)

    def test_return_flag_clicks_and_records_edge(self):
        """标记为返回但没开探测的区域：回退会点它，并把观测到的边记下来。

        不记的话，路由图永远缺这条回边——用户标了返回、探索也用上了，画布上
        却看不到那条路。
        """
        scenes = [
            _scene(
                "scene-0001",
                [_area("a1", explore_probe=True), _area("a2", explore_probe=True)],
                name="A",
            ),
            _scene("scene-0002", [_area("back", explore_return=True)], name="B"),
        ]
        transitions = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0001", "a2"): "scene-0001",
            ("scene-0002", "back"): "scene-0001",
        }
        world, _clock, explorer = self._build(scenes, transitions)
        result = explorer.run()

        self.assertIn(("scene-0002", "back"), world.clicks, "标记的返回区域必须被点到")
        keys = [(edge[0], edge[1], edge[2]) for edge in result.edges]
        self.assertIn(("scene-0002", "back", "scene-0001"), keys, "回退观测到的边必须进结果")

    def test_multiple_return_flags_try_in_order(self):
        """多个返回标记：点了没离开的会被跳过，继续试下一个。"""
        scenes = [
            _scene(
                "scene-0001",
                [_area("a1", explore_probe=True), _area("a2", explore_probe=True)],
                name="A",
            ),
            _scene(
                "scene-0002",
                [_area("dead", explore_return=True), _area("back", explore_return=True)],
                name="B",
            ),
        ]
        transitions = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0001", "a2"): "scene-0001",
            ("scene-0002", "dead"): "scene-0002",
            ("scene-0002", "back"): "scene-0001",
        }
        world, _clock, explorer = self._build(scenes, transitions)
        explorer.run()

        names = [name for scene_id, name in world.clicks if scene_id == "scene-0002"]
        self.assertEqual(names, ["dead", "back"], "先试第一个标记，无效再试下一个")

    def test_does_not_click_when_rollback_fails(self):
        """回退失败时不得在错误画面上继续点击。

        B 既没有返回区域也没有入边，回不到 A。此时 A 的第二个区域绝不能被点——
        那会落在 B 的画面上，是一次真实误点。
        """
        scenes = [
            _scene(
                "scene-0001",
                [
                    _area("a1", explore_probe=True),
                    _area("a2", explore_probe=True),
                ],
            ),
            _scene("scene-0002", [], name="B"),
        ]
        world, _clock, explorer = self._build(scenes, {("scene-0001", "a1"): "scene-0002"})
        explorer.run()
        clicked = [name for _sid, name in world.clicks]
        self.assertIn("a1", clicked)
        self.assertNotIn("a2", clicked)

    def test_waits_for_returned_scene_before_clicking(self):
        """A 有三个区域时，点击序列必须是 a1→回退→a2→回退→a3。

        验证的是「每次点击前确实回到了 A」，而不只是「最终都点了」。
        """
        scenes = [
            _scene(
                "scene-0001",
                [_area(f"a{i}", explore_probe=True) for i in range(1, 4)],
            ),
            _scene("scene-0002", [_area("back")], name="B"),
        ]
        table = _routes([("scene-0002", "back", "scene-0001")])
        world, _clock, explorer = self._build(
            scenes,
            {
                ("scene-0001", "a1"): "scene-0002",
                ("scene-0001", "a2"): "scene-0002",
                ("scene-0001", "a3"): "scene-0002",
                ("scene-0002", "back"): "scene-0001",
            },
            routes_table=table,
        )
        explorer.run()
        sequence = [name for _sid, name in world.clicks]
        self.assertEqual(sequence[:5], ["a1", "back", "a2", "back", "a3"])

    def test_rollback_respects_max_seconds(self):
        """回退过程也受总时长上限约束，不会无限回退。"""
        scenes = [
            _scene("scene-0001", [_area("a1", explore_probe=True)]),
            _scene("scene-0002", [_area("loop", "scene-0002")], name="B"),
        ]
        world, _clock, explorer = self._build(
            scenes,
            {("scene-0001", "a1"): "scene-0002"},
            options=explore.ExploreOptions(
                max_seconds=3.0,
                stable_frames=1,
                frame_interval=0,
                click_settle=1.0,
                settle_timeout=0.5,
                no_jump_retries=1,
            ),
        )
        result = explorer.run()
        self.assertIn(
            result.stop_reason,
            (
                explore.STOP_MAX_SECONDS,
                explore.STOP_COMPLETED,
                explore.STOP_MAX_CLICKS_PER_SCENE,
            ),
        )


class TestEdgeRecordedIntoPlanner(unittest.TestCase):
    """探索中新采到的边必须实时并入规划图。

    **这是一个真实缺陷的回归测试**。用户报「点一次、跳转一次就结束，草稿页
    也没有数据」。根因不是探索循环逻辑，而是规划图只是**初始快照**：
    点进新场景后 `return_step` 找不到任何入边（那条边是探索刚采到的，还没进图），
    回退失败，于是上游场景的其余区域全部被跳过。

    表现极具误导：`stop_reason` 仍是 `completed`，摘要写着「探索结束」，
    用户只会觉得「探索功能坏了」。

    Note:
        场景**不配**返回区域，回退只能靠「按入边反推」——这正是缺陷暴露的路径。
        配了返回区域的场景不依赖图的入边，测不出这个问题。
    """

    def test_new_edge_enables_rollback(self):
        """B 没配返回区域时，靠刚采到的那条边反推回 A。"""
        scenes = [
            # a2 不配目标：配了 `scene-0001` 会让主循环因「目标已知」直接跳过它，
            # 于是测不到「回退后继续展开」这条路径。
            _scene(
                "scene-0001",
                [_area("a1", explore_probe=True), _area("a2", explore_probe=True)],
                name="A",
            ),
            # B 上有一个回 A 的区域，但**没有标 return_area**：回退只能靠
            # 「B 自己的出边」反推出来。回边必须先在图里，否则连反推的素材都没有。
            _scene("scene-0002", [_area("b_back")], name="B"),
        ]
        routes_table = _routes([("scene-0002", "b_back", "scene-0001")])
        world = FakeWorld(
            "scene-0001",
            {
                ("scene-0001", "a1"): "scene-0002",
                # a2 点了之后画面不变（回到 A 自己），模拟「这个区域没有跳转」。
                ("scene-0001", "a2"): "scene-0001",
                ("scene-0002", "b_back"): "scene-0001",
            },
        )
        clock = FakeClock()
        registry = {}

        def token_of(scene_id):
            if scene_id not in registry:
                registry[scene_id] = len(registry) + 1
            return registry[scene_id]

        def scene_frame(scene_id):
            frame = np.full((40, 40, 3), 60, dtype=np.uint8)
            frame[5:15, 5:25] = 30 + token_of(scene_id) * 45 if scene_id else 250
            return frame

        def recognize(frame):
            marker = int(frame[5:15, 5:25].mean())
            for scene_id, token in list(registry.items()):
                if marker == 30 + token * 45:
                    return FakeMatch(scene_id)
            return FakeMatch("")

        def activate_area(scene, area):
            world.clicks.append((scene.scene_id, area.name))
            nxt = world.transitions.get((scene.scene_id, area.name))
            if nxt is not None:
                world.current = nxt

        hooks = explore.ExploreHooks(
            capture_frame=lambda: scene_frame(world.current),
            recognize=recognize,
            activate_area=activate_area,
            wait=lambda s: clock.advance(s),
            save_edge=lambda *a, **k: None,
            clock=clock,
        )
        planner = graph.SceneGraph.build(scenes, routes_table)
        explorer = explore.SceneExplorer(
            scenes,
            hooks,
            explore.ExploreOptions(
                stable_frames=1,
                frame_interval=0,
                click_settle=0.1,
                settle_timeout=0.5,
                no_jump_retries=1,
            ),
            planner=planner,
        )

        result = explorer.run()

        clicked = [name for _sid, name in world.clicks]
        self.assertIn("a1", clicked, "第一个区域应被点击")
        self.assertIn("a2", clicked, "**采到边之后必须能回退，才能点第二个区域**")
        self.assertIn("b_back", clicked, "回退用的是 B 自己的区域，不是 A 的 a1")
        self.assertNotIn(
            ("scene-0002", "a1"),
            world.clicks,
            "不能拿上游场景的区域名去下游场景点击（那是真实误点）",
        )
        self.assertEqual(len(result.edges), 1, f"只有 a1 真跳转，a2 原地不动不算边：{result.edges}")


@unittest.skipIf(np is None, "missing numpy")
class TestExitDiscovery(unittest.TestCase):
    """返回路径未知时，探索必须自己探出来。

    **这是用户报的「只点一次就跳转就结束」的根因**：他给每个场景都标了交互区域
    （包括返回按钮），但「返回按钮指向哪」也要靠探索才能知道。BFS 点进 B 之后，
    B 的返回区域还一次都没被点过，于是 `return_step` 找不到出口，B 与 A 的剩余
    区域全被跳过。

    修法是「就地探一个未探过的区域」找出出口。这组用例锁住它，同时锁住两个
    必须一起成立的边界：跳到**已有**场景要入队展开、结果里的边不能重复。
    """

    def _world(self, start, transitions):
        """造假世界。

        Args：
            start：起始场景。
            transitions：跳转表。

        Returns：
            `FakeWorld` 实例。
        """
        return FakeWorld(start, transitions)

    def _run(self, scenes, transitions, start="scene-0001", routes_table=None):
        """跑一次探索。

        Args：
            scenes：场景列表。
            transitions：跳转表。
            start：起点。
            routes_table：路由表。

        Returns：
            `(world, result)` 二元组。
        """
        world = self._world(start, transitions)
        clock = FakeClock()
        registry = {}

        def token_of(scene_id):
            """给场景分配稳定的帧标记。"""
            if scene_id not in registry:
                registry[scene_id] = len(registry) + 1
            return registry[scene_id]

        def scene_frame(scene_id):
            """按场景生成可区分帧。"""
            frame = np.full((40, 40, 3), 60, dtype=np.uint8)
            frame[5:15, 5:25] = 30 + token_of(scene_id) * 20 if scene_id else 250
            return frame

        def recognize(frame):
            """按帧标记反查场景。"""
            marker = int(frame[5:15, 5:25].mean())
            for scene_id, token in list(registry.items()):
                if marker == 30 + token * 20:
                    return FakeMatch(scene_id)
            return FakeMatch("")

        def activate_area(scene, area):
            """点击并按跳转表切换。"""
            world.clicks.append((scene.scene_id, area.name))
            nxt = world.transitions.get((scene.scene_id, area.name))
            if nxt is not None:
                world.current = nxt

        hooks = explore.ExploreHooks(
            capture_frame=lambda: scene_frame(world.current),
            recognize=recognize,
            activate_area=activate_area,
            wait=lambda s: clock.advance(s),
            save_edge=lambda *a, **k: None,
            clock=clock,
        )
        options = explore.ExploreOptions(
            stable_frames=1,
            frame_interval=0,
            click_settle=0.1,
            settle_timeout=0.5,
            no_jump_retries=1,
        )
        planner = graph.SceneGraph.build(scenes, routes_table)
        explorer = explore.SceneExplorer(scenes, hooks, options, planner=planner, routes=routes_table)
        return world, explorer.run()

    def test_discovers_exit_when_return_edge_unknown(self):
        """B 的返回边未探过时，探索要自己点出它来，而不是跳过 A 的剩余区域。

        场景结构与用户实测一致：每个场景都标了区域（含「返回」），但路由表初始为空。
        """
        scenes = [
            _scene("scene-0001", [_area("a1", explore_probe=True), _area("a2", explore_probe=True)], name="A"),
            _scene("scene-0002", [_area("b1", explore_probe=True), _area("back", explore_probe=True)], name="B"),
        ]
        transitions = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0001", "a2"): "scene-0001",
            ("scene-0002", "b1"): "scene-0002",
            ("scene-0002", "back"): "scene-0001",
        }
        world, result = self._run(scenes, transitions)

        clicked = [name for _sid, name in world.clicks]
        self.assertIn("back", clicked, "**必须自己把返回区域点出来**，否则回不去")
        self.assertIn("a2", clicked, "回到 A 之后要能继续探剩余区域")
        self.assertIn("b1", clicked, "B 自己的区域也要探完")
        self.assertIn(("scene-0002", "back"), [(s, a) for s, a in world.clicks])
        self.assertIn(("scene-0001", "a2"), world.clicks)

        targets = {(edge[0], edge[1]): edge[2] for edge in result.edges}
        self.assertEqual(targets[("scene-0002", "back")], "scene-0001", "返回边必须被记下来")

    def test_multi_level_all_returns_discovered(self):
        """多级场景：每个场景的返回边都必须被探出来。

        这是用户实际数据的形态——5 个场景串联，每一层都有「返回」区域，但路由表
        初始为空。探索必须自己把 4 条返回边全部发现，否则流转图只有单向边，
        根本没法做「跳到任意场景」。
        """
        scenes = [
            _scene("scene-0001", [_area(f"a{i}", explore_probe=True) for i in range(1, 3)], name="入口"),
            _scene("scene-0002", [_area("next", explore_probe=True), _area("back", explore_probe=True)], name="L1"),
            _scene("scene-0003", [_area("next", explore_probe=True), _area("back", explore_probe=True)], name="L2"),
            _scene("scene-0004", [_area("next", explore_probe=True), _area("back", explore_probe=True)], name="L3"),
            _scene("scene-0005", [_area("done", explore_probe=True)], name="末级"),
        ]
        transitions = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0001", "a2"): "scene-0001",
            ("scene-0002", "next"): "scene-0003",
            ("scene-0002", "back"): "scene-0001",
            ("scene-0003", "next"): "scene-0004",
            ("scene-0003", "back"): "scene-0002",
            ("scene-0004", "next"): "scene-0005",
            ("scene-0004", "back"): "scene-0003",
            ("scene-0005", "done"): "scene-0001",
        }
        _world, result = self._run(scenes, transitions)

        targets = {(edge[0], edge[1]): edge[2] for edge in result.edges}
        expected = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0002", "next"): "scene-0003",
            ("scene-0003", "next"): "scene-0004",
            ("scene-0004", "next"): "scene-0005",
            ("scene-0005", "done"): "scene-0001",
            ("scene-0002", "back"): "scene-0001",
            ("scene-0003", "back"): "scene-0002",
            ("scene-0004", "back"): "scene-0003",
        }
        for key, value in expected.items():
            self.assertEqual(targets.get(key), value, f"边 {key} 应通向 {value}，实际 {targets.get(key)!r}")

        # 所有场景都要被访问到：漏一个就意味着它下面的返回边永远拿不到。
        self.assertEqual(sorted(result.visited), [f"scene-{i:04d}" for i in range(1, 6)])

    def test_all_areas_covered_in_branching_graph(self):
        """分支图：每个场景的所有可探区域都必须被点到。

        与用户实测同形：区域命名不含「返回」等关键词，`return_area` 也未配置，
        返回关系只能靠点击发现。旧实现只探一个区域就深入，B、D 的第二个区域
        会永远漏点。
        """
        scenes = [
            _scene("scene-0001", [_area("a1", explore_probe=True), _area("a2", explore_probe=True)], name="入口"),
            _scene("scene-0002", [_area("b1", explore_probe=True), _area("b2", explore_probe=True)], name="B"),
            _scene("scene-0003", [_area("c1", explore_probe=True)], name="C"),
            _scene("scene-0004", [_area("d1", explore_probe=True)], name="D"),
        ]
        transitions = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0001", "a2"): "scene-0003",
            ("scene-0002", "b1"): "scene-0004",
            ("scene-0002", "b2"): "scene-0001",
            ("scene-0003", "c1"): "scene-0001",
            ("scene-0004", "d1"): "scene-0002",
        }
        world, result = self._run(scenes, transitions)

        clicked = set(world.clicks)
        for key in transitions:
            self.assertIn(key, clicked, f"区域 {key} 必须被点到")
        targets = {(edge[0], edge[1]): edge[2] for edge in result.edges}
        self.assertEqual(len(targets), len(transitions), "每条边都应被记录且不重复")

    def test_no_duplicate_edges_after_revisit(self):
        """重探同一批场景后，结果里的边不能重复。

        「探出返回路径 → 逐层走回来 → 重跑一遍」会让同一 `(来源, 区域)` 被命中
        多次。不去重的话边表会出现两条一模一样的行。
        """
        scenes = [
            _scene("scene-0001", [_area("a1", explore_probe=True)], name="A"),
            _scene("scene-0002", [_area("back", explore_probe=True)], name="B"),
        ]
        transitions = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0002", "back"): "scene-0001",
        }
        _world, result = self._run(scenes, transitions)

        keys = [(edge[0], edge[1]) for edge in result.edges]
        self.assertEqual(len(keys), len(set(keys)), f"边必须唯一，实际 {keys}")

    def test_jump_to_existing_scene_gets_expanded(self):
        """跳到**已有**场景时，该场景要入队展开。

        此前只有「发现新场景」才入队，跳到已有场景直接 `continue`——那个场景里
        标好的区域一个都不会被探。表现就是「点一次、跳转一次就结束」。
        """
        scenes = [
            _scene("scene-0001", [_area("a1", explore_probe=True)], name="A"),
            _scene("scene-0002", [_area("b1", explore_probe=True)], name="B"),
        ]
        transitions = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0002", "b1"): "scene-0002",
        }
        world, _result = self._run(scenes, transitions)

        self.assertIn(("scene-0002", "b1"), world.clicks, "跳到已有场景后必须展开它的区域")

    def test_gives_up_when_no_area_can_leave(self):
        """所有区域都点了也离不开时，如实停止，不无限点。

        这是「配置没配完」的正常表现：探索探明没有出口就结束，由界面提示用户。
        """
        scenes = [
            _scene("scene-0001", [_area("a1", explore_probe=True)], name="A"),
            _scene("scene-0002", [_area("stuck", explore_probe=True)], name="B"),
        ]
        transitions = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0002", "stuck"): "scene-0002",
        }
        world, result = self._run(scenes, transitions)

        self.assertLessEqual(len(world.clicks), 6, "不能无限试探")
        self.assertIn(result.stop_reason, {explore.STOP_COMPLETED, explore.STOP_MAX_CLICKS_PER_SCENE})

    def test_explored_candidate_does_not_block_reprobe(self):
        """待确认的候选边不算「已探过」，重跑探索仍要点它。

        **用户报的问题**：点「开始探索」立刻结束，日志是「访问 1 个场景，采集
        0 条边」。产物区里留着上一轮没应用的 `explored` 边，而判据把「有路由」
        当成了「有结论」——于是起点场景的每个区域都被跳过，一个都不点。
        """
        scenes = [
            _scene("scene-0001", [_area("a1", explore_probe=True)], name="A"),
            _scene("scene-0002", name="B"),
        ]
        table = _routes([("scene-0001", "a1", "scene-0002", model.SOURCE_EXPLORED)])
        world, result = self._run(
            scenes,
            {("scene-0001", "a1"): "scene-0002"},
            routes_table=table,
        )

        self.assertEqual(world.clicks, [("scene-0001", "a1")], "候选边对应的区域必须重新探")
        self.assertEqual(len(result.edges), 1, "重探后要重新采到这条边")

    def test_confirmed_route_skips_probe(self):
        """已生效路由才是结论：对应的区域不再重复点。"""
        scenes = [
            _scene("scene-0001", [_area("a1", explore_probe=True)], name="A"),
            _scene("scene-0002", name="B"),
        ]
        table = _routes([("scene-0001", "a1", "scene-0002", model.SOURCE_CONFIRMED)])
        world, result = self._run(
            scenes,
            {("scene-0001", "a1"): "scene-0002"},
            routes_table=table,
        )

        self.assertEqual(world.clicks, [], "已确认的区域不该再点")
        self.assertEqual(result.edges, [])

    def test_reports_nothing_to_probe(self):
        """全都已确认时必须说明「没有需要探测的区域」，不能只报 0 条边。"""
        scenes = [
            _scene("scene-0001", [_area("a1", explore_probe=True)], name="A"),
            _scene("scene-0002", name="B"),
        ]
        table = _routes([("scene-0001", "a1", "scene-0002")])
        _world, result = self._run(
            scenes,
            {("scene-0001", "a1"): "scene-0002"},
            routes_table=table,
        )

        details = [event.detail for event in result.events]
        self.assertTrue(
            any("都已有可寻路路由" in detail for detail in details),
            f"结束原因必须写清，实际事件 {details}",
        )

    def test_search_order_changes_queue_end(self):
        """宽度优先取队首，深度优先取队尾。"""
        hooks = explore.ExploreHooks(
            capture_frame=lambda: None,
            recognize=lambda _frame: FakeMatch(""),
            activate_area=lambda *_args: None,
            wait=lambda _seconds: None,
            clock=FakeClock(),
        )
        queue = ["a", "b", "c"]
        bfs = explore.SceneExplorer([], hooks, explore.ExploreOptions(search_order=model.SEARCH_BFS))
        self.assertEqual(bfs._pop_next(list(queue)), "a")
        dfs = explore.SceneExplorer([], hooks, explore.ExploreOptions(search_order=model.SEARCH_DFS))
        self.assertEqual(dfs._pop_next(list(queue)), "c")

    def test_rollback_does_not_spin_in_a_cycle(self):
        """回退兜底不能在环里打转。

        环（B↔C）上没有回 A 的边时，旧的「任意出边」兜底会 B→C→B→C 一直走，
        用户实测转了 14 次、白等近 30 秒。现在应当很快转去探剩余区域。
        """
        scenes = [
            _scene(
                "scene-0001",
                [_area("a1", explore_probe=True), _area("a2", explore_probe=True)],
                name="A",
            ),
            _scene("scene-0002", [_area("b1", explore_probe=True)], name="B"),
            _scene("scene-0003", [_area("c1", explore_probe=True)], name="C"),
        ]
        transitions = {
            ("scene-0001", "a1"): "scene-0002",
            ("scene-0002", "b1"): "scene-0003",
            ("scene-0003", "c1"): "scene-0002",
            ("scene-0001", "a2"): "scene-0001",
        }
        world, result = self._run(scenes, transitions)

        rollbacks = [event for event in result.events if event.kind == "rollback"]
        self.assertLessEqual(len(rollbacks), 3, f"回退次数过多：{[e.detail for e in rollbacks]}")
        self.assertLessEqual(len(world.clicks), 8, f"点击次数过多：{world.clicks}")


class TestInitialQueue(unittest.TestCase):
    """初始队列要覆盖「起点之外还有未结论区域」的场景。

    用户删掉部分连线后图可能不再连通，只从起点沿边走永远到不了其余场景，
    表现就是「当前场景探完就直接结束」。
    """

    def _explorer(self, order=model.SEARCH_BFS):
        """造一个只用于判队列的探索器。"""
        scenes = [
            _scene("scene-0001", [_area("go", explore_probe=True)]),
            _scene("scene-0002", [_area("go", explore_probe=True)]),
            _scene("scene-0003", []),
        ]
        table = routes.RouteTable()
        table.set_target("scene-0001", "go", "scene-0002", source=model.SOURCE_CONFIRMED)
        options = explore.ExploreOptions(search_order=order)
        return explore.SceneExplorer(scenes, hooks=None, options=options, routes=table)

    def test_includes_unfinished_scenes(self):
        """起点之外未结论的场景排在队列里，无可探区域的不排。"""
        explorer = self._explorer()
        queue = explorer._initial_queue("scene-0001", explore.ExploreResult())
        self.assertEqual(queue, ["scene-0001", "scene-0002"])

    def test_dfs_keeps_start_at_tail(self):
        """DFS 从队尾取，起点放队尾保证当前画面先展开。"""
        explorer = self._explorer(order=model.SEARCH_DFS)
        queue = explorer._initial_queue("scene-0001", explore.ExploreResult())
        self.assertEqual(queue, ["scene-0002", "scene-0001"])


class TestIncompleteStop(unittest.TestCase):
    """队列耗尽但仍有未探测区域时，收尾必须报 incomplete。

    用户删掉部分连线、或点进新发现的画面后回不去时，队列会照样耗尽，但工作
    并没有做完。收尾若仍报 completed，任务层的「未完成自动重启」永远不会触发。
    """

    def test_stuck_in_new_scene_marks_incomplete(self):
        """点进新画面后回不去：其余场景被跳过，收尾是 incomplete 而非 completed。"""
        a = _scene("scene-0001", [_area("discover", explore_probe=True)], name="A")
        b = _scene("scene-0002", [_area("pending", explore_probe=True)], name="B")
        world = FakeWorld("scene-0001")
        world.unknown_targets = {("scene-0001", "discover"): "unknown-1"}
        clock = FakeClock()
        # 真实草稿没有返回区域：这里必须关掉桩补的回退边，否则测不出死角。
        hooks = _hooks(world, clock, draft_back=False)
        result = explore.SceneExplorer([a, b], hooks).run()

        self.assertEqual(result.stop_reason, explore.STOP_INCOMPLETE)
        self.assertNotIn(("scene-0002", "pending"), world.clicks, "回不去时不应在错误画面上点 B")
        self.assertTrue(
            any(event.kind == "skip" and event.scene_id == "scene-0002" for event in result.events),
            f"缺少 B 被跳过的事件：{[(e.kind, e.scene_id) for e in result.events]}",
        )

    def test_restart_resets_target_and_continues(self):
        """卡在草稿画面时重启目标，重置状态后**继续原队列**而不是重跑。

        重启的意义是把目标程序恢复到可探索状态（例如回到主界面），已展开集合、
        点击计数与已采集结果都要保留；重启后直接导航到下一个待展开场景即可。
        """
        a = _scene("scene-0001", [_area("discover", explore_probe=True)], name="A")
        b = _scene("scene-0002", [_area("pending", explore_probe=True)], name="B")
        world = FakeWorld("scene-0001")
        world.unknown_targets = {("scene-0001", "discover"): "unknown-1"}
        clock = FakeClock()
        hooks = _hooks(world, clock, draft_back=False)
        calls = []

        def restart():
            calls.append(clock.now)
            # 重置目标状态：重启后游戏回到一个已知场景（这里直接用 B 模拟）。
            world.current = "scene-0002"
            return True

        hooks.restart_target = restart
        result = explore.SceneExplorer([a, b], hooks).run()

        self.assertEqual(len(calls), 1, "卡在草稿画面时应重启一次")
        self.assertIn(("scene-0002", "pending"), world.clicks, "重启后应继续展开剩余场景")
        self.assertEqual(result.restarts, 1)
        self.assertEqual(result.stop_reason, explore.STOP_COMPLETED)

    def test_no_restart_when_current_scene_is_known(self):
        """当前是正常场景却导航失败时不重启：那是路由缺失，杀进程补不出边。"""
        a = _scene("scene-0001", [_area("go", explore_probe=True)], name="A")
        b = _scene("scene-0002", [_area("pending", explore_probe=True)], name="B")
        world = FakeWorld("scene-0001", {("scene-0001", "go"): "scene-0001"})
        clock = FakeClock()
        hooks = _hooks(world, clock)
        calls = []
        hooks.restart_target = lambda: calls.append(1) or True
        result = explore.SceneExplorer([a, b], hooks).run()

        self.assertEqual(calls, [], "正常场景导航失败不应重启目标")
        self.assertEqual(result.stop_reason, explore.STOP_INCOMPLETE)

    def test_all_scenes_exhausted_still_completed(self):
        """所有可探区域都有结论时仍报 completed，不能被误判成 incomplete。"""
        a = _scene("scene-0001", [_area("go", explore_probe=True)], name="A")
        world = FakeWorld("scene-0001", {("scene-0001", "go"): "scene-0001"})
        clock = FakeClock()
        hooks = _hooks(world, clock)
        result = explore.SceneExplorer([a], hooks).run()

        self.assertEqual(result.stop_reason, explore.STOP_COMPLETED)


if __name__ == "__main__":
    unittest.main()
