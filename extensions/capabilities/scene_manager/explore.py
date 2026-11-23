# -*- coding: utf-8 -*-
"""自动探索器：BFS 遍历画面，采集跳转边，产出草稿。

探索的产出是**草稿**而不是正式场景或正式边：机器跑出来的边若直接进正式路由图，
一条误判就会让后续每次导航都点错地方，而错误表现为「到了奇怪的地方」而非报错。
人工确认这一步不能省。

四条安全约束：

1. **只探测白名单区域**（`Area.explore_probe` 为真）。探测动作可能花资源、进战斗、
   消耗次数，且不可逆；黑名单无法穷尽（「充值」「删除」「退出」之外还有游戏自定义的
   危险按钮）。这个标记是**探索自己的开关**，与区域被外部如何使用无关。
   **具体用哪种动作由 `Area.explore_action` 决定**（单击/双击/长按/滑动/右键）——
   滑动列表用单击探测不会变化、长按按钮用单击不触发，两者都会被误判成「无效区域」。
2. **三重上限**：状态数、总时长、单状态点击次数，任一触发即停。状态空间可能
   无限（同一画面因进度不同差分出新状态），不可能靠「自然跑完」收尾。
3. **等画面稳定再判定**。动画/过渡中识别必错，而错判会产出错误的边。
4. **连续两次无跳转才判无效区域**。一次可能是卡顿或冷却（存在第二次点击才生效
   的按钮），一次即判会永久误杀。

本模块**不导入 `ok` 与 `PySide6`**：探索逻辑经依赖注入，可用假世界完整验证。
"""

import time

from extensions.capabilities.scene_manager import model, normalize, shapes
from extensions.capabilities.scene_manager import routes as routes_module

# 探索停止原因。
STOP_COMPLETED = "completed"
# 队列耗尽但仍有场景带未探测区域（典型：点进新画面后回不去）。任务层会据此重启重试。
STOP_INCOMPLETE = "incomplete"
STOP_MAX_SCENES = "max_scenes"
STOP_MAX_SECONDS = "max_seconds"
STOP_MAX_CLICKS_PER_SCENE = "max_clicks_per_scene"
STOP_CANCELLED = "cancelled"
STOP_NO_START = "no_start_scene"

# 单区域的判定结果。
RESULT_JUMPED = "jumped"
RESULT_NO_JUMP = "no_jump"
RESULT_NEW_SCENE = "new_scene"
RESULT_ERROR = "error"

# 未传路由表时用的空表。用它而不是 `None`，让「查一条不存在的边」永远返回空串，
# 调用方不必到处判空。
_EMPTY_ROUTES = routes_module.RouteTable()

# 单个场景「暂时回不去」的重试次数上限。
#
# 为什么要重试：探索是逐步发现的过程，一个场景第一次回不去，不代表以后回不去——
# 可能只是通往它的那条边还没被探出来。等更多场景展开、图更完整之后往往就能回去。
#
# 为什么要上限：真实环境里可能存在**真的回不去**的场景（配置缺了返回按钮）。
# 不设上限会无限重排队，把时间全耗在同一个场景上。
_MAX_SCENE_RETRIES = 3

# 「找出口」递归的最大深度。
#
# 进入一个场景后要先把它的可探区域点完，点到未知场景就继续展开，直到探出
# 一条回已知场景的边。正常游戏链路远小于这个深度；上限只为兜住异常图
# （例如每层都发现新画面且没有回头路），防止无限下探。
_MAX_EXIT_DEPTH = 6


class ExploreOptions:
    """探索的可调参数与上限。

    Attributes：
        max_scenes: 新发现场景数上限。
        max_seconds: 总时长上限（秒）。
        max_clicks_per_scene: 单个场景的点击次数上限。
        stable_frames: 判定「已稳定」所需连续稳定帧次数。
        frame_interval: 取帧间隔（秒）。
        settle_timeout: 等待画面稳定的上限（秒）。
        click_settle: 每次探测后的基线等待秒数。
        no_jump_retries: 判「无跳转」前的重复点击次数。
        diff_noise_limit: 差分提候选框时，差异像素占比超过该值视为背景有动画。
        candidate_min_area: 候选框的最小面积占比，低于此值不给候选框。
        candidate_max_area: 候选框的最大面积占比，高于此值不给候选框。
        search_order: 待展开场景的取出顺序（`bfs` 先进先出 / `dfs` 后进先出）。
    """

    def __init__(
        self,
        max_scenes=30,
        max_seconds=600.0,
        max_clicks_per_scene=20,
        stable_frames=2,
        frame_interval=0.15,
        settle_timeout=8.0,
        click_settle=0.8,
        no_jump_retries=2,
        diff_noise_limit=0.3,
        candidate_min_area=0.001,
        candidate_max_area=0.5,
        search_order=model.SEARCH_BFS,
    ):
        """构造参数。

        Args：
            max_scenes：新场景上限。
            max_seconds：总时长上限。
            max_clicks_per_scene：单场景点击上限。
            stable_frames：稳定帧次数。
            frame_interval：取帧间隔。
            settle_timeout：稳定等待上限。
            click_settle：点击后基线等待。
            no_jump_retries：无跳转重试次数。
            diff_noise_limit：差分噪声上限。
            candidate_min_area：候选框最小面积占比。
            candidate_max_area：候选框最大面积占比。
            search_order：展开顺序，`bfs` 或 `dfs`。
        """
        self.max_scenes = max(1, int(max_scenes))
        self.max_seconds = max(1.0, float(max_seconds))
        self.max_clicks_per_scene = max(1, int(max_clicks_per_scene))
        self.stable_frames = max(1, int(stable_frames))
        self.frame_interval = max(0.0, float(frame_interval))
        self.settle_timeout = max(0.5, float(settle_timeout))
        self.click_settle = max(0.0, float(click_settle))
        self.no_jump_retries = max(1, int(no_jump_retries))
        self.diff_noise_limit = min(1.0, max(0.0, float(diff_noise_limit)))
        self.candidate_min_area = max(0.0, float(candidate_min_area))
        self.candidate_max_area = min(1.0, float(candidate_max_area))
        self.search_order = search_order if search_order in model.SEARCH_ORDERS else model.SEARCH_BFS


class ExploreHooks:
    """探索器依赖的外部动作。

    Attributes：
        capture_frame: 无参函数，取当前帧。
        recognize: 函数 `(frame) -> MatchResult`，识别当前场景。
        activate_area: 函数 `(scene, area) -> None`，对区域执行一次探测动作。
            缺省实现是单击；动作类型由注入方决定，探索只观察画面对不对。
        wait: 函数 `(seconds) -> None`，等待。
        clock: 无参函数，返回单调时间。
        save_draft: 函数 `(scene, frame, parent_frame) -> None`，落盘新场景草稿
            （含基准图、特征模板）。
        save_edge: 函数 `(from_scene_id, area_name, to_scene_id, observations, after_wait) -> None`，
            记录一条草稿边。
        should_stop: 可选的判定函数，返回 True 时中止。
        on_draft_saved: 可选的函数 `(scene) -> None`，草稿落盘后调用。真实运行时用它
            把新草稿并进识别器的场景集——**否则识别器永远认不出新画面**，探索点进去
            就再也回不来，BFS 停在第一个新画面上。
        restart_target: 可选的无参函数，重启被操作的目标程序并返回是否成功。
            **这是「重置目标状态」而不是「重跑任务」**：探索卡在回不去的画面
            （草稿或未识别画面）时调用它，成功后继续当前的队列与进度。
    """

    def __init__(
        self,
        capture_frame,
        recognize,
        activate_area,
        wait,
        save_draft=None,
        save_edge=None,
        clock=None,
        should_stop=None,
        on_draft_saved=None,
        on_event=None,
        restart_target=None,
    ):
        """构造依赖集合。

        Args：
            capture_frame：取帧函数。
            recognize：识别函数。
            activate_area：对区域执行一次探测动作的函数（缺省是单击）。
            wait：等待函数。
            save_draft：草稿落盘函数。
            save_edge：草稿边记录函数。
            clock：计时函数。
            should_stop：中止判定函数。
            on_draft_saved：草稿落盘后的回调，用于刷新识别器。
            on_event：可选的事件回调 `(explore_event) -> None`。真实运行时由任务
                接到日志上——**否则探索过程对用户完全不可见**：事件只存在内存里，
                任务一结束就随对象丢弃，日志里只剩最后一行摘要，用户看到的只有
                「跑完了但什么都没发生」。
            restart_target：可选的重启目标函数，返回是否成功。用于重置目标状态后
                继续探索（见类 docstring）。
        """
        self.capture_frame = capture_frame
        self.recognize = recognize
        self.activate_area = activate_area
        self.wait = wait
        self.save_draft = save_draft or (lambda scene, frame, parent: None)
        self.save_edge = save_edge or (lambda *args, **kwargs: None)
        self.clock = clock or time.monotonic
        self.should_stop = should_stop
        self.on_draft_saved = on_draft_saved
        self.on_event = on_event
        self.restart_target = restart_target


class ExploreEvent:
    """探索过程中的一条事件（供界面实时展示）。

    Attributes：
        kind: 事件类型。
        scene_id: 相关场景。
        detail: 说明文本。
        elapsed: 相对开始的秒数。
    """

    def __init__(self, kind, scene_id="", detail="", elapsed=0.0):
        """构造事件。

        Args：
            kind：事件类型。
            scene_id：相关场景。
            detail：说明。
            elapsed：相对开始的秒数。
        """
        self.kind = kind
        self.scene_id = scene_id
        self.detail = detail
        self.elapsed = elapsed

    def __repr__(self):
        """便于打印。"""
        return f"ExploreEvent({self.kind}, {self.scene_id}, {self.detail})"

    def to_dict(self):
        """序列化为字典。

        Returns：
            字典。
        """
        return {
            "kind": self.kind,
            "scene": self.scene_id,
            "detail": self.detail,
            "elapsed": round(self.elapsed, 3),
        }


class ExploreResult:
    """一次探索的结果。

    Attributes：
        stop_reason: 停止原因，取 `STOP_*` 之一。
        visited: 被访问过的场景 id 列表（按发现顺序）。
        new_scenes: 新发现的场景 id 列表。
        edges: 采集到的边，元素为 `(from_scene, area, to_scene, observations)`。
        invalid_areas: 判定无效的区域，元素为 `(scene_id, area_name, reason)`。
        events: 事件列表。
        elapsed: 总耗时。
        restarts: 为重置目标状态实际重启目标的次数（中途恢复 + 启动失败重试）。
    """

    def __init__(
        self,
        stop_reason=STOP_COMPLETED,
        visited=None,
        new_scenes=None,
        edges=None,
        invalid_areas=None,
        events=None,
        elapsed=0.0,
        restarts=0,
    ):
        """构造结果。

        Args：
            stop_reason：停止原因。
            visited：访问过的场景。
            new_scenes：新发现的场景。
            edges：采集到的边。
            invalid_areas：无效区域。
            events：事件。
            elapsed：耗时。
            restarts：目标重启次数。
        """
        self.stop_reason = stop_reason
        # 一律用「is None」判定而不是 `or []`：传入的空列表会被 `or` 当成假值替换成
        # 新列表，调用方后续 append 到自己的列表时结果里永远是空的——这类缺陷不会
        # 报错，只表现为「事件/边丢了」。
        self.visited = [] if visited is None else visited
        self.new_scenes = [] if new_scenes is None else new_scenes
        self.edges = [] if edges is None else edges
        self.invalid_areas = [] if invalid_areas is None else invalid_areas
        self.events = [] if events is None else events
        self.elapsed = elapsed
        self.restarts = int(restarts or 0)

    def summary(self):
        """返回一句话摘要。

        Returns：
            形如 `探索结束（max_scenes）：访问 3 个场景，发现 2 个新场景，采集 4 条边`。
        """
        return (
            f"探索结束（{self.stop_reason}）：访问 {len(self.visited)} 个场景，"
            f"发现 {len(self.new_scenes)} 个新场景，采集 {len(self.edges)} 条边，"
            f"无效区域 {len(self.invalid_areas)} 个" + (f"，重启目标 {self.restarts} 次" if self.restarts else "")
        )

    def to_dict(self):
        """序列化为字典。

        Returns：
            字典。
        """
        return {
            "stop_reason": self.stop_reason,
            "visited": list(self.visited),
            "new_scenes": list(self.new_scenes),
            "edges": [
                {"from": src, "area": area, "to": dst, "observations": obs} for src, area, dst, obs in self.edges
            ],
            "invalid_areas": [
                {"scene": scene, "area": area, "reason": reason} for scene, area, reason in self.invalid_areas
            ],
            "elapsed": round(self.elapsed, 3),
            "restarts": self.restarts,
        }


def wait_until_stable(hooks, options):
    """等待画面稳定并返回最后一帧。

    连续 `stable_frames` 次相邻帧都被判为稳定才算稳定。只用一次判定不够：
    有些动画是「帧间差很小但一直缓慢变化」，单次判定容易误判。

    Args：
        hooks：`ExploreHooks` 实例。
        options：`ExploreOptions` 实例。

    Returns：
        稳定时的帧；超时则返回最后一帧（可能为 None）。
    """
    deadline = hooks.clock() + options.settle_timeout
    previous = None
    stable_count = 0
    last = None
    while hooks.clock() < deadline:
        frame = hooks.capture_frame()
        if frame is not None:
            last = frame
            if previous is not None and normalize.frames_stable(previous, frame):
                stable_count += 1
                if stable_count >= options.stable_frames:
                    return frame
            else:
                stable_count = 0
            previous = frame
        if options.frame_interval > 0:
            hooks.wait(options.frame_interval)
    return last


def propose_candidate_region(parent_frame, new_frame, options):
    """用两帧差分给新画面提一个候选特征区域。

    父帧与新帧的差异处就是「这个画面与上游画面不同的地方」，通常正是识别它所需
    的特征。差分不可靠时**不给候选框**——给一个错误候选框比不给更糟，用户会以为
    自动结果可信。

    Args：
        parent_frame：上游场景的帧。
        new_frame：新画面的帧。
        options：`ExploreOptions` 实例。

    Returns：
        `(rect, reason)` 二元组。`rect` 是相对比例矩形，无法给出时为 None；
        `reason` 说明为什么给不出（给出时为 None）。
    """
    if parent_frame is None or new_frame is None:
        return None, "缺少父帧或新帧，无法差分"
    if parent_frame.shape != new_frame.shape:
        return None, "父帧与新帧尺寸不一致，无法差分"

    import cv2
    import numpy as np

    difference = cv2.absdiff(parent_frame, new_frame)
    if difference.ndim == 3:
        magnitude = np.mean(difference, axis=2)
    else:
        magnitude = difference.astype(np.float32)

    height, width = magnitude.shape[:2]
    changed = magnitude > 8
    changed_ratio = float(np.mean(changed))
    if changed_ratio <= 0.0:
        return None, "两帧没有可识别的差异"

    rows = np.any(changed, axis=1)
    cols = np.any(changed, axis=0)
    if not rows.any() or not cols.any():
        return None, "差异区域无法定位"
    y1, y2 = int(np.argmax(rows)), int(len(rows) - np.argmax(rows[::-1]))
    x1, x2 = int(np.argmax(cols)), int(len(cols) - np.argmax(cols[::-1]))

    # 两条独立的拒绝条件，顺序按判据的可靠性排：
    #
    # 1. **差异像素占比**（用户确认的规则：超过阈值视为背景有动画）。这是判「画面
    #    本身没静止」的依据，比外接矩形面积更本质——整屏都在动时，候选框无论取哪
    #    都没有意义。
    # 2. **外接矩形面积**。为「差异只在两处、但相隔很远」准备：像素占比很低（说明
    #    不是背景在动），外接矩形却几乎覆盖全屏（说明取这个框当特征毫无区分度）。
    area_ratio = ((x2 - x1) * (y2 - y1)) / float(width * height)
    if changed_ratio > options.diff_noise_limit:
        return None, (
            f"差异像素占比 {changed_ratio:.0%} 超过 {options.diff_noise_limit:.0%}，背景可能有动画，不提供候选框"
        )
    if area_ratio > options.candidate_max_area:
        return None, f"差异区域过大（{area_ratio:.0%}），几乎覆盖半个画面，不提供候选框"
    if area_ratio < options.candidate_min_area:
        return None, f"差异区域过小（{area_ratio:.2%}），建议手工框选"

    return (
        (
            round(x1 / width, 6),
            round(y1 / height, 6),
            round(x2 / width, 6),
            round(y2 / height, 6),
        ),
        None,
    )


class SceneExplorer:
    """BFS 探索器。

    Attributes：
        scenes: 起始场景列表（正式库中的场景，作为已访问集合的初始成员）。
        hooks: `ExploreHooks` 实例。
        options: `ExploreOptions` 实例。
        events: 事件累积列表。
    """

    def __init__(self, scenes, hooks, options=None, planner=None, routes=None):
        """构造探索器。

        Args：
            scenes：起始已访问的场景列表（`model.Scene`）。
            hooks：`ExploreHooks` 实例。
            options：`ExploreOptions` 实例。
            planner：`graph.SceneGraph` 实例，用于求回退与正向路径。为 None 时
                探索只能「当前就在目标场景」时才展开该场景——多级图上会漏探，
                因此生产装配必须传。
            routes：`routes.RouteTable` 实例，提供「这条边是否已知、目标是谁」。
                为 None 时按空表处理——探索会把所有可探测区域都点一遍（不知道
                哪些已经探过）。
        """
        self.scenes = list(scenes)
        self.hooks = hooks
        self.options = options or ExploreOptions()
        self.events = []
        self._planner = planner
        # `_owns_routes` 区分「调用方给的活表」与「共享的占位空表」。占位表绝不能
        # 被写入——它是模块级单例，写进去的边会漏到其它实例与后续运行。
        self._owns_routes = routes is not None
        self._routes = routes if routes is not None else _EMPTY_ROUTES
        # 探索期新增的草稿场景。它们还没落进正式库，但回退路径上可能经过它们。
        self._extra_scenes = {}
        # 最近一次探测跳到了哪个场景。`_probe` 返回值只有四种结果码，而主循环
        # 需要知道「跳到了哪」才能把已有场景排进队列。
        self._last_jump_target = ""
        # `_go_to` 的逐层回退需要「当前探索上下文」才能主动探路（见
        # `_discover_exit`）。三者由 `run` 在开始时挂上，避免把这个上下文
        # 穿过 `_ensure_at` → `_go_to` 的每一层参数。
        self._run_by_id = {}
        self._run_known = set()
        self._run_clicks = {}
        # 回退探路时新登记的草稿：不能就地展开（当前任务是把画面带回上游），
        # 攒起来交回主循环排队。
        self._deferred_scenes = []
        # 每个场景「暂时回不去」的次数，用于限制重排队。
        self._retry_counts = {}

    def _emit(self, kind, scene_id="", detail="", started=0.0):
        """记录一条事件，并转给外部回调。

        Args：
            kind：事件类型。
            scene_id：相关场景。
            detail：说明。
            started：本次探索的开始时间。

        Note：
            同时做两件事：存进内存列表（供界面/测试断言）与转给 `on_event`
            （真实运行时接到日志）。只存内存的话，任务结束后事件随对象丢弃，
            日志里只剩最后一行摘要——排查时完全看不到「哪一步被跳过、为什么」。
        """
        event = ExploreEvent(kind, scene_id, detail, self.hooks.clock() - started)
        self.events.append(event)
        if self.hooks.on_event is not None:
            try:
                self.hooks.on_event(event)
            except Exception:  # noqa: BLE001 — 回调出错不能打断探索
                pass

    def run(self, start_scene_id=None, recognize_only=False):
        """执行探索。

        Args：
            start_scene_id：起点场景 id；省略时要求调用方已在某个场景，由识别结果决定。
            recognize_only：为 True 时只识别并列出当前画面的可探测区域，不做任何操作。

        Returns：
            `ExploreResult` 实例。
        """
        started = self.hooks.clock()
        # 顺序必须先是「清空 self.events，再把同一个列表交给 result」：反过来会让
        # result 拿到上一个列表对象，本轮的 `_emit` 写进新列表而结果里始终为空。
        self.events = []
        # **所有运行期状态都要重置**。同一个 explorer 可以被重复 `run()`（测试就
        # 这么用，界面上的「再探一次」也会），残留的队列/计数会让第二轮行为错乱：
        # 上一轮的「待展开草稿」会被当成这一轮发现的，而重试计数会提前耗尽。
        self._deferred_scenes = []
        self._retry_counts = {}
        self._last_jump_target = ""
        result = ExploreResult(events=self.events)

        frame = wait_until_stable(self.hooks, self.options)
        if frame is None:
            result.stop_reason = STOP_NO_START
            self._emit("error", "", "取不到画面，无法开始探索", started)
            result.elapsed = self.hooks.clock() - started
            return result

        current = self.hooks.recognize(frame)
        if current is None or not current.matched():
            result.stop_reason = STOP_NO_START
            self._emit("error", "", "当前画面无法识别为已知场景，请先为它建立场景再探索", started)
            result.elapsed = self.hooks.clock() - started
            return result

        start_id = current.scene_id()
        if start_scene_id and start_id != start_scene_id:
            self._emit(
                "warn",
                start_id,
                f"当前画面是 {self._label(start_id)}，与指定的起点 {self._label(start_scene_id)} 不同，按实际画面探索",
                started,
            )
        result.visited.append(start_id)

        by_id = {scene.scene_id: scene for scene in self.scenes}
        queue = self._initial_queue(start_id, result)
        known = set(by_id)
        # 已经排进队列或已展开过的场景。**必须单独记**，不能只看 `visited`：
        # `visited` 是「到过这个画面」，而这里要回答的是「它的区域探过没有」。
        # 两者混用会导致「点进一个已有场景但不展开它」——用户看到的就是
        # 「只点了一次就结束了」。
        expanded = set()
        clicks_in_scene = {}
        # 把探索上下文挂到实例上：`_go_to` 的逐层回退要能在「返回路径未知」时
        # 主动探路，而那一步发生在 `_ensure_at` → `_go_to` 内部，穿参数会把
        # 五个参数一路透传到底。
        self._run_by_id = by_id
        self._run_known = known
        self._run_clicks = clicks_in_scene

        while queue:
            if self.hooks.should_stop is not None and self.hooks.should_stop():
                result.stop_reason = STOP_CANCELLED
                break
            if self.hooks.clock() - started > self.options.max_seconds:
                result.stop_reason = STOP_MAX_SECONDS
                break
            if len(result.new_scenes) >= self.options.max_scenes:
                result.stop_reason = STOP_MAX_SCENES
                break

            # 取出下一个待展开场景。BFS 取队首（先把这一层探完），DFS 取队尾
            # （一探到新场景就沿链路走到底，再回头）。
            scene_id = self._pop_next(queue)
            scene = by_id.get(scene_id)
            if scene is None:
                continue

            nav = self._go_to(scene_id, started, result)
            if not nav and self._restart_for_navigation(started, result):
                # 重启只是重置目标状态：队列、已展开集合与本轮采集结果都保留，
                # 换一个能导航的画面继续，而不是把整个探索重跑一遍。
                nav = self._go_to(scene_id, started, result)
            if not nav:
                self._emit(
                    "skip",
                    scene_id,
                    f"无法回到 {self._label(scene_id)}，跳过它的剩余区域（其余场景继续探索）",
                    started,
                )
                continue

            for area in scene.areas:
                if self.hooks.should_stop is not None and self.hooks.should_stop():
                    result.stop_reason = STOP_CANCELLED
                    break
                if self.hooks.clock() - started > self.options.max_seconds:
                    result.stop_reason = STOP_MAX_SECONDS
                    break
                if len(result.new_scenes) >= self.options.max_scenes:
                    result.stop_reason = STOP_MAX_SCENES
                    break
                # 已有结论的区域不再点：要么路由已知（探过了），要么已被判为无效。
                #
                # **判据不能用「目标场景已知」**。那个条件看着等价，实际漏掉一半：
                # 目标已知但区域本身还没点过时（例如从别处得知 A→B，但 A 的这个
                # 区域还没被验证），跳过它会让「这个区域通向哪」永远没有结论；
                # 反过来，判无效的区域不在路由表里，用「目标已知」判断就永远
                # 不会跳过它，于是每轮重探、白烧点击次数。
                if self._area_resolved(scene.scene_id, area.name, result):
                    continue
                if not area.explore_probe:
                    continue
                if clicks_in_scene.get(scene_id, 0) >= self.options.max_clicks_per_scene:
                    result.stop_reason = STOP_MAX_CLICKS_PER_SCENE
                    self._emit(
                        "limit",
                        scene_id,
                        f"{self._label(scene_id)} 已点击 {clicks_in_scene[scene_id]} 次，达到单场景上限",
                        started,
                    )
                    break

                # 每一轮都确保还在本场景：上一次点击可能把画面带到了别处，
                # 在别处点本场景的区域是**真实误判**。
                if not self._ensure_at(scene_id, started, result):
                    # 先尝试重启目标重置状态，再重试一次回本场景；重启失败或仍回不去
                    # 才按原来的「重新排队」处理。
                    recovered = self._restart_for_navigation(started, result) and self._ensure_at(
                        scene_id, started, result
                    )
                    if not recovered:
                        self._emit(
                            "skip",
                            scene_id,
                            f"暂时回不到 {self._label(scene_id)}，稍后重试它的剩余区域",
                            started,
                        )
                        # **重新排队而不是彻底放弃**：本次回不去不等于以后回不去。
                        # 探路可能刚发现一条更远的出口，等那些场景展开完、图更完整时，
                        # 这个场景往往就能回得去了。此前直接丢弃，后果是「一次回不去
                        # 就永久跳过」，用户看到的是「区域明明都标了却没被探」。
                        #
                        # 已展开完的场景不再排：它没有剩余区域可探，回去也只是重跑一遍
                        # 已知区域，白烧点击次数。
                        if scene_id not in expanded:
                            self._retry_counts[scene_id] = self._retry_counts.get(scene_id, 0) + 1
                            if self._retry_counts[scene_id] <= _MAX_SCENE_RETRIES and scene_id not in queue:
                                queue.append(scene_id)
                        break
                # 回退探路时发现的草稿要在这里入队：那时不能就地展开（当前任务
                # 是把画面带回上游），否则会在错误的画面上继续点击。
                self._enqueue_deferred(queue, known)

                # **必须重判一次**：`_ensure_at` 为了回到本场景会调用
                # `_discover_exit`，那里点的正是「还没有结论」的区域，很可能就是
                # 当前这个区域。只在进入循环时判一次的话，同一区域会被连点两次，
                # 日志里出现两条一模一样的边，还多等一轮点击后的稳定时间。
                if self._area_resolved(scene.scene_id, area.name, result):
                    continue

                clicks_in_scene[scene_id] = clicks_in_scene.get(scene_id, 0) + 1
                if recognize_only:
                    self._emit("preview", scene_id, f"将探测 {self._label(scene_id)} 的区域 {area.name}", started)
                    continue

                outcome = self._probe(scene, area, started, result, by_id, known, clicks_in_scene)
                if outcome == RESULT_JUMPED:
                    # 跳进了一个**已有**的场景：它自己的区域还没探过，必须排进队列。
                    # 这条此前漏掉了，后果是「点一次、跳转一次就结束」——用户看到
                    # 探索只走了一步，而目标场景里那些标好的区域（包括返回按钮）
                    # 一个都没被点到。
                    jumped_to = self._last_jump_target
                    if jumped_to and jumped_to in by_id and jumped_to not in expanded:
                        if jumped_to not in queue:
                            queue.append(jumped_to)
                    continue
                if outcome == RESULT_NEW_SCENE:
                    new_id = result.new_scenes[-1]
                    known.add(new_id)
                    if new_id not in queue:
                        queue.append(new_id)
                    continue

            if result.stop_reason != STOP_COMPLETED:
                break
            # **只有「区域全走完」才算展开完成**。半途因回不去而 break 的场景不进
            # `expanded`，这样重排队后还能再试一次——`expanded` 的语义是
            # 「探完了」，不是「开始过」。
            if scene_id not in result.visited:
                result.visited.append(scene_id)
            if self._scene_exhausted(scene, result):
                expanded.add(scene_id)
            # 收尾也要收一次：最后一次回退探路可能刚发现一个新画面。
            self._enqueue_deferred(queue, known)

        # **队列空了不等于探索完成**。回不去时场景会被跳过，队列照样会耗尽，
        # 但那些场景的可探区域一个结论都没有。此前这种收尾仍记 `completed`：
        # 摘要写着「探索结束（completed）」，而图里明明还有整片没探的区域；
        # 任务层的「未完成自动重启」也因此永不触发——用户看到的正是「发现了
        # 新场景却没探完，也没重启」。
        #
        # 典型触发路径：点进一个刚发现的画面（草稿），而草稿没有配返回区域，
        # 整个 BFS 停在那儿，其余场景全部「无法回到」被跳过。
        unfinished = self._unfinished_scenes(by_id, result)
        if result.stop_reason == STOP_COMPLETED and unfinished:
            result.stop_reason = STOP_INCOMPLETE
            shown = "、".join(self._label(scene_id) for scene_id in unfinished[:5])
            suffix = f" 等 {len(unfinished)} 个场景" if len(unfinished) > 5 else ""
            self._emit(
                "warn",
                "",
                f"探索未完成：{shown}{suffix} 仍有未探测区域（当前画面可能回不去）",
                started,
            )
        # 一个区域都没点、也没采到任何东西时，必须说清原因：摘要只有「采集 0 条边」
        # 时用户会以为探索坏了。两种原因差别很大——没配可探区域是配置问题，
        # 「全部已确认」则是本来就没有新东西可探，处理方式完全不同。
        if (
            result.stop_reason == STOP_COMPLETED
            and not result.edges
            and not result.new_scenes
            and not result.invalid_areas
        ):
            start_scene = by_id.get(start_id)
            probe_areas = [area for area in start_scene.areas if area.explore_probe] if start_scene else []
            if not probe_areas:
                self._emit(
                    "warn",
                    start_id,
                    "起点场景没有开启「允许探索探测」的区域，无法探索",
                    started,
                )
            else:
                self._emit(
                    "warn",
                    start_id,
                    "起点场景的可探区域都已有可寻路路由，本次没有需要探测的区域；"
                    "如需重探，请先在场景路由页删除对应路由",
                    started,
                )
        result.elapsed = self.hooks.clock() - started
        self._emit("done", "", result.summary(), started)
        return result

    def _pop_next(self, queue):
        """取出下一个待展开场景。

        Args：
            queue：待展开场景 id 列表（原地修改）。

        Returns：
            取出的场景 id。

        Note：
            BFS 取队首：先把当前层的场景全部展开，再看下一层。
            DFS 取队尾：探到一个新场景就立刻展开它，沿一条链路走到底。
        """
        return queue.pop() if self.options.search_order == model.SEARCH_DFS else queue.pop(0)

    def _ensure_at(self, scene_id, started, result):
        """确保当前位于指定场景，必要时回退或走过去。

        区域循环里的每次点击前都调一次：上一次点击可能把画面带到了别处，在别处
        点本场景的区域是真实误点。

        Args：
            scene_id：目标场景 id。
            started：探索开始时间。
            result：正在累积的结果（事件要写进它，不能另建一个丢事件）。

        Returns：
            当前确实在目标场景时为 True。
        """
        current = self._current_scene_id(started)
        if current == scene_id:
            return True
        return self._go_to(scene_id, started, result)

    def _go_to(self, scene_id, started, result):
        """把游戏带回指定场景。

        BFS 进入新场景后，必须回到待展开的场景才能继续点它的其余区域。回退策略按
        优先级三级：

        1. **已在目标场景**：直接返回。
        2. **沿边正向走**：图上有从当前位置到目标的可用路径时走过去（例如已探索出
           A→B 之外还有 A→C→D，而当前在 B、目标是 D）。
        3. **逐层回退到公共祖先再正向走**：没有正向路径时，反复用
           `graph.return_step`（场景级返回区域，缺失时按入边反推）退回上游，直到
           出现到目标的正向路径，或退到无法再退。

        为什么必须有第 3 级：BFS 是「先展开 A 的全部区域，再展开 B」。点进 B 之后
        若不回退，A 的其余区域只能跳过——需求「自动探测各区域，构建路由流转图」
        在多级图上就不成立。

        Args：
            scene_id：要回到的场景 id。
            started：探索开始时间。
            result：正在累积的结果。

        Returns：
            成功回到目标场景时为 True。
        """
        current = self._current_scene_id(started)
        if current is None:
            self._emit("warn", scene_id, f"{self._label(scene_id)} 无法识别当前画面", started)
            return False
        if current == scene_id:
            return True

        if self._walk_to(current, scene_id, started):
            return True

        # 逐层回退。
        #
        # **这个循环里不再尝试 `_walk_to`**：正向路径会把我们送回刚才离开的场景，
        # 于是「点 a1 进 B → 回退到 A → 又沿 A→B 走到 B」无限往复，直到点击上限。
        # 实测就是这样烧掉 20 次点击的。回退阶段的唯一目标是**向上退**，退到能
        # 出现新路径时由下一轮 `_go_to` 重新规划。
        #
        # 上限取「已访问场景数 × 2」，最低 8：正常不超过图的深度；乘 2 是为了容纳
        # 「探路时先点进更深一层、再逐层退回来」这种情况。
        limit = max(len(self._reachable_ids()) * 2, 8)
        # 本次回退已经走过的场景。**必须记**：兜底出口只要求「离开这里」，
        # 在 0003→0004→0005→0006→0003 这种环里它会一圈圈转下去，
        # 实测转了 14 次、白等近 30 秒才发现这条环上根本没有回边的入口。
        trail = set()
        # 本次回退已经点过的 `(场景, 区域)`。多个返回标记里若有一个点了没离开，
        # 下一轮不能再点它——否则每一轮都从同一个候选开始，白烧点击次数。
        tried = set()
        for _attempt in range(limit):
            if self.hooks.should_stop is not None and self.hooks.should_stop():
                return False
            if self.hooks.clock() - started > self.options.max_seconds:
                return False
            current = self._current_scene_id(started)
            if current is None:
                return False
            if current == scene_id:
                return True
            trail.add(current)
            if self._rollback_one(current, started, result, avoid=trail, goal=scene_id, tried=tried):
                continue
            # 已知的返回路径用完了。**此时不能直接放弃**：用户给每个场景都标了
            # 交互区域（含返回按钮），只是「返回按钮指向哪」还没被探索出来。
            # 主动探一个没有结论的区域，把那条返回边**发现出来**。
            if not self._discover_exit(current, started, result):
                return False

        self._emit(
            "warn",
            scene_id,
            f"回退 {limit} 次仍未到达 {self._label(scene_id)}，跳过该场景",
            started,
        )
        return False

    def _current_scene_id(self, started):
        """识别当前所在场景。

        Args：
            started：探索开始时间（仅用于日志定位，不参与判断）。

        Returns：
            场景 id；取不到帧或识别不到时返回 None。
        """
        del started
        frame = wait_until_stable(self.hooks, self.options)
        if frame is None:
            return None
        current = self.hooks.recognize(frame)
        if current is None or not current.matched():
            return None
        return current.scene_id()

    def _reachable_ids(self):
        """返回探索器认识的场景 id 集合。

        Returns：
            已知场景 id 集合（含草稿）。
        """
        ids = {scene.scene_id for scene in self.scenes}
        ids.update(self._extra_scenes)
        return ids

    def _walk_to(self, from_scene, to_scene, started):
        """沿图上已有边从当前场景走到目标。

        只点 `_planner` 给出的第一步，然后重新判断——一步一判而非一次走完全程，
        因为探索期间画面可能被弹窗打断。

        Args：
            from_scene：当前场景 id。
            to_scene：目标场景 id。
            started：探索开始时间。

        Returns：
            走到目标时为 True；无路径或走失败时为 False。
        """
        steps = self._plan(from_scene, to_scene)
        if not steps:
            return False

        for step in steps:
            if self.hooks.should_stop is not None and self.hooks.should_stop():
                return False
            if self.hooks.clock() - started > self.options.max_seconds:
                return False
            scene = self._scene_by_id(step.from_scene)
            area = scene.find_area(step.area_name) if scene else None
            if area is None:
                self._emit(
                    "warn",
                    step.from_scene,
                    f"{self._label(step.from_scene)} 上的区域 {step.area_name} 已不存在",
                    started,
                )
                return False
            try:
                self.hooks.activate_area(scene, area)
            except Exception as error:  # noqa: BLE001 — 回退点击失败即视为没走成
                self._emit("warn", step.from_scene, f"{self._label(step.from_scene)} 回退点击失败：{error}", started)
                return False
            self._settle_after_click(step.from_scene, step.area_name)
            current = self._current_scene_id(started)
            if current != step.to_scene:
                self._emit(
                    "warn",
                    step.from_scene,
                    f"回退点 {step.area_name} 后到达 {self._label(current) if current else '未识别'}，期望 {self._label(step.to_scene)}",
                    started,
                )
                return False
        return True

    def _initial_queue(self, start_id, result):
        """构造探索初始队列：起点 + 其他还有未结论区域的已知场景。

        Args：
            start_id：起点场景 id。
            result：本轮累积结果（用于判区域是否已有结论）。

        Returns：
            场景 id 列表，顺序符合 BFS / DFS 的取出规则。

        Note：
            只从起点沿边走的话，用户删掉部分连线后图不再连通，其余场景永远
            进不了队列——表现就是「当前场景探完就结束，没有导航去别的场景」。
            这里把所有「还有可探且尚无结论区域」的场景一起排进来：能导航过去
            就继续探，到不了则由 `_go_to` 失败并跳过，至少不会整轮直接结束。

            DFS 从队尾取，所以起点放队尾，保证当前画面先展开；BFS 相反。
        """
        pending = []
        for scene in self.scenes:
            if scene.scene_id == start_id:
                continue
            if any(
                area.explore_probe and not self._area_resolved(scene.scene_id, area.name, result)
                for area in scene.areas
            ):
                pending.append(scene.scene_id)
        if self.options.search_order == model.SEARCH_DFS:
            return pending + [start_id]
        return [start_id] + pending

    def _label(self, scene_id):
        """返回场景的显示文本，供事件与提示使用。

        Args：
            scene_id：场景 id。

        Returns：
            `名称（scene-0001）`；名称缺失或与 id 相同时只显示 id。
        """
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                if not scene.name or scene.name == scene_id:
                    return scene_id
                return f"{scene.name}（{scene_id}）"
        return scene_id

    def _area_resolved(self, scene_id, area_name, result):
        """判断某个区域是否「已有结论」。

        Args：
            scene_id：区域所属场景 id。
            area_name：区域名。
            result：正在累积的结果。

        Returns：
            已有结论（可寻路路由、本轮已采到、或已判无效）时为 True。

        Note：
            三种结论合起来才算完备：可寻路路由是「确认过能去哪」，本轮采到的
            边是「这一轮已经点过」，`invalid_areas` 里的是「点了没反应」。
            少了任一类都会导致区域被反复重探——既浪费点击次数，也会让摘要里的
            「无效区域」随轮次增长。

            **待确认的 `explored` 边不算结论**。它是候选，不是最终事实：把它当成
            「探过了」，下次进探索时一个区域都不会点，用户看到的是「点一下探索就
            直接结束、采集 0 条边」。产物区里历史遗留的探索边尤其容易触发这一点
            ——那些边既没被应用，也不会被寻路使用，却会永久挡住重新探测。
        """
        route = self._routes.get(scene_id, area_name)
        if route is not None and route.routable():
            return True
        if any(edge[0] == scene_id and edge[1] == area_name for edge in result.edges):
            return True
        return any(
            item_scene == scene_id and item_area == area_name for item_scene, item_area, _reason in result.invalid_areas
        )

    def _scene_exhausted(self, scene, result):
        """判断一个场景的可探测区域是否都已有结论。

        Args：
            scene：`model.Scene` 实例。
            result：正在累积的结果（读 `invalid_areas`）。

        Returns：
            每个 `explore_probe` 为真的区域都已有结论时为 True。

        Note：
            **判据是「有没有结论」而不是「点没点过」**。点了但画面没变的区域会被
            记进 `invalid_areas`，那也是一种结论；再点一次不会得到新信息，反而
            浪费点击次数，还会把结果里的「无效区域」重复计数。

            结论的口径与 :meth:`_area_resolved` 完全一致——两边各写一套判据时，
            「区域跳过但场景不算探完」会让同一个场景被反复排队重试。
        """
        for area in scene.areas:
            if not area.explore_probe:
                continue
            if self._area_resolved(scene.scene_id, area.name, result):
                continue
            return False
        return True

    def _next_draft_id(self, by_id):
        """分配一个不与已知场景/草稿冲突的草稿 id。

        Args：
            by_id：当前认识的场景映射（含已加载的草稿与本轮新发现的草稿）。

        Returns：
            形如 `draft-0002` 的新 id。

        Note：
            不能再用「本轮新场景数 + 1」取号：自动重启会重跑探索，上一轮落盘的
            草稿**已经存在但不在本轮的 `result.new_scenes` 里**，编号会从 1 重新
            开始，把同名草稿整目录覆盖——用户确认前的画面就丢了。

            取「已认识草稿的最大编号 + 1」：`by_id` 含运行时加载的全部草稿，
            足以保证本轮内不重号，也不依赖库的计数器状态。
        """
        largest = 0
        for scene_id in by_id:
            if not scene_id.startswith("draft-"):
                continue
            suffix = scene_id[len("draft-") :]
            if suffix.isdigit():
                largest = max(largest, int(suffix))
        return f"draft-{largest + 1:04d}"

    def _unfinished_scenes(self, by_id, result):
        """列出仍有未探测区域的场景 id。

        Args：
            by_id：场景映射。
            result：本轮累积结果。

        Returns：
            场景 id 列表，顺序与 `by_id` 一致。

        Note：
            判据与 :meth:`_scene_exhausted` 同源，避免「一个说探完、一个说没探完」
            两套口径。用来判定收尾原因而不是只看队列是否为空：队列会因「回不去」
            而清空，但工作并没有做完。

            刚发现的草稿没有区域，天然算完成；它挡路的问题由「其余场景未完成」
            体现出来。
        """
        return [scene_id for scene_id, scene in by_id.items() if not self._scene_exhausted(scene, result)]

    def _restart_for_navigation(self, started, result):
        """卡在回不去的画面时重启目标，重置状态后继续探索。

        Args：
            started：探索开始时间。
            result：本轮累积结果（重启成功时累加 `restarts`）。

        Returns：
            实际重启成功时为 True。

        Note：
            只在**当前画面本身无法离开**时重启：未识别画面，或草稿。草稿没有可点
            的区域，`_discover_exit` 再怎么探路也走不出去；未识别画面则连「在点
            什么」都不知道。

            当前是正常场景却导航失败时不重启——那是路由缺失，重启不会补出一条边，
            杀掉目标只会把进度推倒重来。任务层未注入 `restart_target`（开关关闭
            或次数用尽）时同样不重启。

            **重启不重置探索状态**：队列、`expanded`、点击计数与已采集结果都保留，
            调用方的 `_go_to` / `_ensure_at` 重试即可接着当前进度走。
        """
        if self.hooks.restart_target is None:
            return False
        # 已请求中断时不再重启：重启是耗时且破坏性的动作，用户点了中断就应该尽快停。
        if self.hooks.should_stop is not None and self.hooks.should_stop():
            return False
        current = self._current_scene_id(started)
        if current and not current.startswith("draft-"):
            return False
        self._emit("restart", "", "当前画面无法继续导航，重启目标重置状态后继续探索", started)
        try:
            ok = bool(self.hooks.restart_target())
        except Exception as error:  # noqa: BLE001 — 重启失败按「没恢复」处理
            self._emit("warn", "", f"重启目标失败：{error}", started)
            return False
        if not ok:
            return False
        result.restarts += 1
        self._wait_recognizable(started)
        # 重启期间用户可能点了中断：交给主循环收尾，不要再重试导航。
        if self.hooks.should_stop is not None and self.hooks.should_stop():
            return False
        return True

    def _wait_recognizable(self, started):
        """重启后等目标回到可识别画面。

        Args：
            started：探索开始时间（仅用于事件定位）。

        Note：
            `restart_target` 只等到「能取到帧」，而启动动画/加载画面同样能取帧。
            立刻重试导航会把加载画面判成未识别，接着又请求一次重启，白烧次数。
            这里最多等一个 `settle_timeout`；超时仍不可识别也返回，由调用方按
            正常导航失败处理。
        """
        deadline = self.hooks.clock() + max(1.0, self.options.settle_timeout)
        while self.hooks.clock() < deadline:
            if self.hooks.should_stop is not None and self.hooks.should_stop():
                return
            frame = self.hooks.capture_frame()
            if frame is not None:
                matched = self.hooks.recognize(frame)
                if matched is not None and matched.matched():
                    return
            self.hooks.wait(0.5)
        self._emit("warn", "", "重启后目标仍未回到可识别画面", started)

    def _enqueue_deferred(self, queue, known):
        """把回退探路时发现的草稿排进 BFS 队列。

        Args：
            queue：BFS 队列。
            known：已认识场景 id 集合。

        Note：
            这些草稿是在「试图回到上游」时点出来的新画面。它们必须被访问，否则
            探索会漏掉整个分支——而它们的父场景那条边已经记下了，不展开就只能是
            「探到边、不知内容」。
        """
        while self._deferred_scenes:
            pending = self._deferred_scenes.pop(0)
            if not pending:
                continue
            # **不能按 `known` 过滤**：这里排的多数是「已有场景」，它们本来就在
            # `known` 里。要判的是「展开过没有」——`known` 的语义是「认识」，
            # 不是「探完」，用它过滤会把已有场景全部丢掉。
            known.add(pending)
            if pending not in queue:
                queue.append(pending)

    def _discover_exit(self, current_scene, started, result):
        """在当前场景及其后继里，找一条能回到已知场景的边。

        旧实现只探**一个**未探区域：点到前进按钮就继续深入，深层场景的其余
        区域（尤其是返回按钮）再也没有机会被点。用户实测表现为「0003 点进
        0004 后一路往下，0004、0005 的第二个区域一个都没探」。

        现在改成一次小范围 DFS：当前场景的可探区域逐个点，点到已知场景就算
        找到出口；点到刚发现的新草稿就递归展开它，直到它探出回已知场景的边。
        回退路径由点击本身发现，不再依赖用户事先配置返回区域。

        Args：
            current_scene：当前被困的场景 id。
            started：探索开始时间。
            result：正在累积的结果。

        Returns：
            找到出口时为 True。
        """
        return self._explore_exit(current_scene, started, result, frozenset(), 0)

    def _explore_exit(self, scene_id, started, result, trail, depth):
        """`_discover_exit` 的递归体。

        Args：
            scene_id：要展开的场景 id（调用时画面应停在该场景）。
            started：探索开始时间。
            result：正在累积的结果。
            trail：本次探路已经过的场景 id，避免原地打转。
            depth：递归深度，超过上限即放弃，防止异常图无限下探。

        Note：
            **点到一个已知场景就直接算出口**，不要求它一定是上游。外层拿到
            出口后会重新规划回目标场景；回不去时下一轮会从新位置继续探。
            只有刚发现的新草稿才需要递归展开——它还没有任何出边，不展开就
            只能停在它上面。
        """
        if depth > _MAX_EXIT_DEPTH:
            return False
        scene = self._scene_by_id(scene_id)
        if scene is None:
            return False
        for area in self._exit_candidates(scene):
            if self._area_resolved(scene_id, area.name, result):
                continue
            if self._run_clicks.get(scene_id, 0) >= self.options.max_clicks_per_scene:
                self._emit(
                    "limit",
                    scene_id,
                    f"{self._label(scene_id)} 为寻找返回路径已达单场景点击上限 {self.options.max_clicks_per_scene}",
                    started,
                )
                return False
            if self.hooks.should_stop is not None and self.hooks.should_stop():
                return False
            if self.hooks.clock() - started > self.options.max_seconds:
                return False
            self._emit(
                "probe", scene_id, f"{self._label(scene_id)} 返回路径未知，先探 {area.name} 试图找出出口", started
            )
            outcome = self._probe(scene, area, started, result, self._run_by_id, self._run_known, self._run_clicks)
            if outcome == RESULT_NO_JUMP:
                # 画面没动，仍在当前场景，继续点下一个区域。
                continue
            if outcome == RESULT_ERROR:
                return False
            target = self._last_jump_target
            if not target and outcome == RESULT_NEW_SCENE and result.new_scenes:
                target = result.new_scenes[-1]
            if not target:
                continue
            newest_draft = result.new_scenes[-1] if result.new_scenes else None
            if target == scene_id or target in trail:
                # 回到了本次探路经过的场景：出口已经找到。
                return True
            if target == newest_draft:
                # 刚发现的新草稿没有区域可探，交给主循环入队处理。
                return True
            target_scene = self._scene_by_id(target)
            if target_scene is None or self._scene_exhausted(target_scene, result):
                # 已知且已探完的场景：回到它之后外层能重新规划。
                return True
            # 已知但还有未探区域：递归展开它，直到它探出回已知场景的边。
            # 递归展开的作用是把「深层场景的返回按钮」探出来，避免旧实现
            # 「点一次前进就继续深入、上一层剩余区域永远漏点」。
            if self._explore_exit(target, started, result, trail | {scene_id}, depth + 1):
                return True
            # 递归没能带回出口，画面已经不在 scene_id，剩余区域本轮无法继续点。
            return False
        return False

    def _exit_candidates(self, scene):
        """按「更像返回区域」的顺序排列待探区域。

        Args：
            scene：`model.Scene` 实例。

        Returns：
            可探区域列表，名称含返回/关闭/退出等词的排在前面。

        Note：
            这一步只影响顺序，不影响覆盖面：即使命名不含关键词，递归展开也会
            在更深的场景里把返回按钮探出来。先点候选返回区域能在常见命名下
            少下探好几层。
        """
        keywords = ("返回", "后退", "关闭", "取消", "退出", "back", "close", "cancel", "exit", "return")
        return sorted(
            (area for area in scene.areas if area.explore_probe),
            key=lambda area: 0 if any(word in area.name.lower() for word in keywords) else 1,
        )

    def _rollback_one(self, current_scene, started, result=None, avoid=None, goal=None, tried=None):
        """从当前场景退回上一层。

        候选顺序见 :meth:`_return_candidates`：标记为探索返回的区域在前，
        入边反推与已知出口在后。前一个候选点了没离开场景时继续试下一个；
        `tried` 保证一次 `_go_to` 内不重复点同一个区域。

        Args：
            current_scene：当前场景 id。
            started：探索开始时间。
            result：正在累积的结果；省略时不记边、也不做「已探完场景优先」排序。
            avoid：本次回退已经走过的场景，不允许再作为落点。
            goal：本次要回到的目标场景；兜底出口必须能通向它。
            tried：本次回退已经点过的 `(场景, 区域)` 集合；原地更新。

        Returns：
            成功退回（不校验退到哪，只校验确实离开了当前场景）时为 True。
        """
        scene = self._scene_by_id(current_scene)
        if scene is None:
            return False
        if tried is None:
            tried = set()
        for area_name, _target in self._return_candidates(current_scene, result, avoid=avoid, goal=goal):
            if (current_scene, area_name) in tried:
                continue
            tried.add((current_scene, area_name))
            area = scene.find_area(area_name)
            if area is None:
                continue
            clicked_at = self.hooks.clock()
            try:
                self.hooks.activate_area(scene, area)
            except Exception as error:  # noqa: BLE001 — 回退点击失败不抛，交给上层判定
                self._emit("warn", current_scene, f"{self._label(current_scene)} 回退点击失败：{error}", started)
                return False
            self._settle_after_click(current_scene, area_name)
            after = self._current_scene_id(started)
            if after is None:
                return False
            if after == current_scene:
                self._emit(
                    "warn",
                    current_scene,
                    f"{self._label(current_scene)} 点 {area_name} 后仍在原场景",
                    started,
                )
                continue
            self._record_rollback_edge(current_scene, area_name, after, clicked_at, result)
            self._emit("rollback", current_scene, f"{self._label(current_scene)} 回退到 {self._label(after)}", started)
            return True
        self._emit(
            "warn",
            current_scene,
            self._rollback_blocked_reason(current_scene),
            started,
        )
        return False

    def _return_candidates(self, current_scene, result=None, avoid=None, goal=None):
        """按优先级列出回退候选。

        Args：
            current_scene：当前场景 id。
            result：正在累积的结果。
            avoid：本次回退已经走过的场景。
            goal：本次要回到的目标场景。

        Returns：
            `[(area_name, target_scene)]`，已去重、保持优先级。

        Note：
            标记为探索返回的区域排在最前——它们是用户明确指认的「往回走」按钮，
            比任何推断都可靠。多个标记之间按「目标能否到达本次目标」排序：
            能直达的优先，其次是目标未知的（点一下就知道），最后是目标已知但
            到不了本次目标的（仍可能通往别的分支）。全部排完再退到入边反推与
            已知出口，即没有标记时与旧行为完全一致。
        """
        candidates = []
        scene = self._scene_by_id(current_scene)
        if scene is not None:
            direct = []
            unknown = []
            elsewhere = []
            for area in scene.explore_return_areas():
                target = self._routes.target_of(current_scene, area.name)
                if not target or target == current_scene:
                    unknown.append((area.name, ""))
                    continue
                if not goal or target == goal or self._plan(target, goal):
                    distance = 0 if (not goal or target == goal) else len(self._plan(target, goal))
                    direct.append((distance, area.name, target))
                else:
                    elsewhere.append((area.name, target))
            direct.sort()
            candidates.extend((name, target) for _distance, name, target in direct)
            candidates.extend(unknown)
            candidates.extend(elsewhere)

        hint = self._return_hint(current_scene)
        if hint is not None:
            candidates.append(hint)
        known = self._known_exit(current_scene, result, avoid=avoid, goal=goal)
        if known is not None:
            candidates.append(known)

        seen = set()
        ordered = []
        for area_name, target in candidates:
            if not area_name or area_name in seen:
                continue
            if avoid and target and target in avoid and target != goal:
                # 落点已经走过：再点一次只是绕圈。与 `_known_exit` 的过滤同一口径，
                # 否则「标记的返回区域」会成为新的绕环入口。
                continue
            seen.add(area_name)
            ordered.append((area_name, target))
        return ordered

    def _record_rollback_edge(self, from_scene, area_name, to_scene, clicked_at, result):
        """把回退时观测到的边记进结果与路由表。

        Args：
            from_scene：来源场景 id。
            area_name：被点击的区域名。
            to_scene：点击后识别到的场景 id。
            clicked_at：点击时刻（`hooks.clock()`）。
            result：正在累积的结果；None 表示只回退、不记账。

        Note：
            回退点的也是真实跳转：标了返回但**没开探测**的区域，如果不在这里
            记录，路由图里就永远没有这条回边。已生效或本轮已采到的边不重复写，
            避免把 `confirmed` 降级成 `explored`。
        """
        if result is None or self._area_resolved(from_scene, area_name, result):
            return
        elapsed = max(0.0, self.hooks.clock() - clicked_at)
        self._record_edge(result, from_scene, area_name, to_scene, 1)
        self._remember_edge(from_scene, area_name, to_scene, after_wait=elapsed)
        self.hooks.save_edge(from_scene, area_name, to_scene, 1, after_wait=elapsed)

    def _rollback_blocked_reason(self, scene_id):
        """说明「为什么当前场景回不去」。

        Args：
            scene_id：当前场景 id。

        Returns：
            面向用户的原因文本。

        Note：
            旧文案统一写「没有返回区域也没有入边」，但多数情况其实是
            「有入边，只是本场景还没探出回边」。说法与事实不符时，用户会
            去检查入边配置，而问题根本不在那里。
        """
        scene = self._scene_by_id(scene_id)
        if scene is not None and scene.explore_return_areas():
            return "标记的返回区域点了仍在原场景，且没有其它已知回边；先探剩余区域找出返回路径"
        incoming = []
        if self._planner is not None:
            try:
                incoming = self._planner.incoming(scene_id)
            except Exception:  # noqa: BLE001 — 图与场景不一致时不阻断探索
                incoming = []
        if incoming:
            return f"有入边（来自 {self._label(incoming[0][0])}），但本场景还没探出回边；先探剩余区域找出返回路径"
        return "没有标记返回区域，也没有已知入边；先探剩余区域找出离开这个场景的路"

    def _known_exit(self, scene_id, result, avoid=None, goal=None):
        """从已记录的出边里挑一条离开当前场景的路。

        Args：
            scene_id：当前场景 id。
            result：正在累积的结果；用于优先选择已探完的目标场景。
            avoid：本次回退已经走过的场景。
            goal：本次要回到的目标场景。

        Returns：
            `(area_name, target_scene)`；没有可用出边时为 None。

        Note：
            这是 `return_step` 之后第三级兜底。它不保证「回到上一级」，
            只保证「离开这里并到达一个已知场景」——到了已知场景就能用图
            重新规划。优先选已探完的目标，避免刚离开又掉进没探完的分支。

            **必须排除「走了也没用」的边**，否则会在环里打转：目标已经走过，
            或者目标到不了本次要回的场景。这类边在图上合法，但走一步只是多等
            一轮画面稳定，最终仍要回到原地——实测在一个四场景的环里转了 14 次、
            白等近 30 秒。排除后直接进入 `_discover_exit`，就地探出真正的回边。
        """
        planner = self._planner
        if planner is None:
            return None
        reachable = self._reachable_ids()
        visited = avoid or set()
        candidates = []
        for area_name, target, _wait in planner.outgoing(scene_id):
            if target not in reachable:
                continue
            if target in visited:
                continue
            if goal and target != goal and not self._plan(target, goal):
                continue
            scene = self._scene_by_id(target)
            done = scene is not None and result is not None and self._scene_exhausted(scene, result)
            candidates.append((0 if done else 1, area_name, target))
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1], candidates[0][2]

    def _settle_after_click(self, scene_id, area_name):
        """探测后等待画面变化。

        Args：
            scene_id：区域所属场景 id。
            area_name：被点击的区域名。

        Note：
            等待时长取自**路由表**（这条边配了多久），取不到就用 `click_settle`
            兜底。区域上已经没有 `after_wait` 字段了——等待属于「这条边走多久」，
            与路由一起存。
        """
        settle = max(self.options.click_settle, self._routes.after_wait_of(scene_id, area_name))
        if settle > 0:
            self.hooks.wait(settle)

    def _return_hint(self, scene_id):
        """返回该场景的回退建议。

        Args：
            scene_id：场景 id。

        Returns：
            `(area_name, target_scene)`；无法回退时为 None。
        """
        planner = self._planner
        if planner is None:
            return None
        try:
            return planner.return_step(scene_id)
        except Exception:  # noqa: BLE001 — 图与场景不一致时不阻断探索
            return None

    def _plan(self, from_scene, to_scene):
        """求从当前场景到目标的最短路径。

        Args：
            from_scene：起点场景 id。
            to_scene：目标场景 id。

        Returns：
            `graph.RouteStep` 列表；无路径时为空列表。
        """
        planner = self._planner
        if planner is None:
            return []
        if from_scene == to_scene:
            return []
        try:
            steps = planner.path(from_scene, to_scene)
        except Exception:  # noqa: BLE001 — 场景不在图中时视为无路径
            return []
        return steps or []

    def _scene_by_id(self, scene_id):
        """按 id 查场景（含探索期新增的草稿）。

        Args：
            scene_id：场景 id。

        Returns：
            `model.Scene` 实例；不存在时返回 None。
        """
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        return self._extra_scenes.get(scene_id)

    def _record_edge(self, result, from_scene, area_name, to_scene, observations):
        """把一条边记进结果（同键去重，保留最大的观测次数）。

        Args：
            result：正在累积的结果。
            from_scene：来源场景 id。
            area_name：区域名。
            to_scene：目标场景 id。
            observations：本次观测到的重复点击次数。

        Note：
            必须去重。探索会**回头重探**同一批场景（第一次跳太深、探出返回路径后
            再逐层回来重跑一遍），同一个 `(来源, 区域)` 会被命中多次。不去重的话
            结果里会出现两条相同的边，界面上的边表也会跟着重复。
        """
        key = (from_scene, area_name)
        for index, existing in enumerate(result.edges):
            if existing[0] == key[0] and existing[1] == key[1]:
                result.edges[index] = (from_scene, area_name, to_scene, max(existing[3], observations))
                return
        result.edges.append((from_scene, area_name, to_scene, observations))

    def _remember_edge(self, from_scene, area_name, to_scene, after_wait=None):
        """把一条新采到的边并入规划图。

        Args：
            from_scene：来源场景 id。
            area_name：触发的区域名。
            to_scene：目标场景 id。
            after_wait：点击后到识别到目标场景的实测耗时（秒）；None 时沿用
                路由表现有值或默认 1.0。

        Note：
            **规划图必须同步更新，这是探索能跑完多个区域的前提**。回退靠
            `graph.return_step` 找入边，而探索开始时构造的图只是初始快照——
            不更新的话，点进新场景后「找不到任何入边」→ 回退失败 → 上游场景的
            其余区域全部被跳过。

            表现极其误导：`stop_reason` 是 `completed`，日志摘要写着「探索结束」，
            但实际只访问了起点和一个邻居。用户看到的就是「点一次、跳转一次、
            然后就结束了」。

            边本身记在 `result.edges` 与草稿里，本方法只负责图，不重复存。

        Note：
            **路由表也要一起更新**。`self._routes` 是构造时拿到的对象引用，探索
            期间所有「这个区域有没有结论」的判断都读它。只更新规划图不更新路由表，
            会导致刚探到的区域仍被当作「没结论」——下一轮重探同一个区域，点进同一个
            场景，再回退，反复烧点击次数直到上限。实测就是这样把 20 次点击耗光的。
        """
        try:
            # **不能写 `self._routes` 如果是没人传的那个共享空表**：那是模块级单例，
            # 一次探索写进去的边会漏到下一次（甚至别的实例）。调用方没传路由表时
            # 说明它只在做「就地探一探」这类不落账的用法，此时只更新规划图即可。
            if self._owns_routes:
                existing = self._routes.get(from_scene, area_name)
                source = model.SOURCE_EXPLORED
                if existing is not None and existing.routable():
                    # 已生效的路径保持原档位，探索不把它降级成待确认。
                    source = existing.source
                self._routes.set_target(
                    from_scene,
                    area_name,
                    to_scene,
                    after_wait=after_wait,
                    source=source,
                )
        except Exception:  # noqa: BLE001 — 路由更新失败不应打断探索
            pass
        if self._planner is None:
            return
        try:
            self._planner.add_edge(from_scene, area_name, to_scene)
        except Exception:  # noqa: BLE001 — 图更新失败不应打断探索
            pass

    def _await_jump(self, scene, before_frame, clicked_at):
        """点击后按配置间隔轮询，判断画面是否已离开当前场景。

        Args：
            scene：点击前的场景。
            before_frame：点击前的稳定帧。
            clicked_at：点击时刻（`hooks.clock()`）。

        Returns：
            `(frame, elapsed)`：画面已离开当前场景时给出该帧与实际耗时；
            超时仍未离开时为 `(None, elapsed)`。

        Note：
            **耗时写入这条边的 `after_wait`**。不同跳转的过场时间差别很大，
            固定 1.0 秒会让瞬时切页白等、加载慢的跳转又不够等。

            「已识别到别的场景」即判定跳转完成，不等画面完全稳定——加载动画
            的最后一帧可能一直在动，但目标场景已经能认出来了，再等只是白等。

            连续 `stable_frames` 帧与点击前相同、且已过 `click_settle` 兜底
            时间，才判「没有跳转」提前返回；否则每个点不动的区域都要耗满
            `settle_timeout`，探索会慢到不可用。
        """
        deadline = clicked_at + self.options.settle_timeout
        stable_hits = 0
        # 每次轮询至少推进 10ms：测试与离线场景会把间隔配成 0（假时钟），
        # 不推进时间会让 `while clock() < deadline` 永远成立。
        step = max(self.options.frame_interval, 0.01)
        while self.hooks.clock() < deadline:
            self.hooks.wait(step)
            frame = self.hooks.capture_frame()
            if frame is None:
                continue
            if before_frame is not None and normalize.frames_stable(before_frame, frame):
                stable_hits += 1
                if (
                    stable_hits >= self.options.stable_frames
                    and self.hooks.clock() - clicked_at >= self.options.click_settle
                ):
                    return None, self.hooks.clock() - clicked_at
                continue
            stable_hits = 0
            current = self.hooks.recognize(frame)
            if current is not None and current.matched():
                if current.scene_id() != scene.scene_id:
                    return frame, self.hooks.clock() - clicked_at
                # 画面变了但仍识别为当前场景（动画中）：继续等。
                continue
            # 画面已变化却识别不出：可能是未配置的新画面。直接返回，让
            # `_probe` 落草稿；继续等只会把它当成「没有跳转」丢掉。
            return frame, self.hooks.clock() - clicked_at
        return None, self.hooks.clock() - clicked_at

    def _probe(self, scene, area, started, result, by_id, known, clicks_in_scene=None):
        """点击一个区域并判定结果。

        重试点击也要计入单场景的点击上限：否则「10 个区域 × 每次重试 2 次」实际会
        点 20 次，而上限按区域数计就会失效。计数由调用方传入同一个字典共享。

        Args：
            scene：区域所属场景。
            area：要点击的区域。
            started：探索开始时间。
            result：正在累积的结果。
            by_id：场景 id → 场景 的映射（可能被新草稿扩充）。
            known：已认识场景 id 集合。
            clicks_in_scene：单场景点击计数（与主循环共享）。

        Returns：
            `RESULT_*` 之一。
        """
        if clicks_in_scene is None:
            clicks_in_scene = {}
        before_frame = wait_until_stable(self.hooks, self.options)
        # 点击前校验当前确实在目标场景。少了这道校验，一旦回退失败回到别处，
        # 后续区域的点击就落在错误画面上——那是**真实误点**，可能花资源或进战斗。
        actual = self.hooks.recognize(before_frame) if before_frame is not None else None
        if actual is None or not actual.matched() or actual.scene_id() != scene.scene_id:
            self._emit(
                "warn",
                scene.scene_id,
                f"点击前不在该场景（当前 {self._label(actual.scene_id()) if actual and actual.matched() else '未识别'}），"
                f"跳过区域 {area.name}",
                started,
            )
            return RESULT_ERROR
        for attempt in range(self.options.no_jump_retries):
            if clicks_in_scene.get(scene.scene_id, 0) >= self.options.max_clicks_per_scene:
                self._emit(
                    "limit",
                    scene.scene_id,
                    f"{self._label(scene.scene_id)} 点击次数已达上限 {self.options.max_clicks_per_scene}",
                    started,
                )
                return RESULT_ERROR
            if self.hooks.should_stop is not None and self.hooks.should_stop():
                return RESULT_ERROR
            clicks_in_scene[scene.scene_id] = clicks_in_scene.get(scene.scene_id, 0) + 1
            clicked_at = self.hooks.clock()
            try:
                self.hooks.activate_area(scene, area)
            except Exception as error:  # noqa: BLE001 — 点击异常记事件并继续
                self._emit(
                    "error", scene.scene_id, f"{self._label(scene.scene_id)} 点击 {area.name} 失败：{error}", started
                )
                return RESULT_ERROR
            after_frame, elapsed = self._await_jump(scene, before_frame, clicked_at)
            if after_frame is None:
                # 超时内画面没有离开当前场景：可能冷却未过，再点一次。
                continue

            current = self.hooks.recognize(after_frame)
            if current is None or not current.matched():
                self._last_jump_target = ""
                return self._register_new_scene(
                    scene, area, before_frame, after_frame, started, result, by_id, known, elapsed
                )

            target_id = current.scene_id()
            observations = attempt + 1
            self._record_edge(result, scene.scene_id, area.name, target_id, observations)
            # 实测耗时就是这条边真实的「操作后等待」：瞬时切页与加载十几秒的
            # 战斗过场差别很大，统一写 1.0 会让前者白等、后者不够等。
            self._remember_edge(scene.scene_id, area.name, target_id, after_wait=elapsed)
            self.hooks.save_edge(scene.scene_id, area.name, target_id, observations, after_wait=elapsed)
            self._emit(
                "edge",
                scene.scene_id,
                f"{self._label(scene.scene_id)} · {area.name} → {self._label(target_id)}（观测 {observations} 次）",
                started,
            )
            if target_id not in result.visited:
                result.visited.append(target_id)
            self._last_jump_target = target_id
            return RESULT_JUMPED

        result.invalid_areas.append(
            (scene.scene_id, area.name, f"连续 {self.options.no_jump_retries} 次点击后画面未变化")
        )
        self._emit(
            "invalid",
            scene.scene_id,
            f"{self._label(scene.scene_id)} · {area.name} 连续 {self.options.no_jump_retries} 次点击无跳转",
            started,
        )
        return RESULT_NO_JUMP

    def _register_new_scene(self, scene, area, before_frame, after_frame, started, result, by_id, known, elapsed=0.0):
        """发现了一个未配置的画面，产出草稿场景。

        Args：
            scene：点击前的场景。
            area：被点击的区域。
            before_frame：点击前的稳定帧。
            after_frame：点击后的稳定帧。
            started：探索开始时间。
            result：正在累积的结果。
            by_id：场景映射。
            known：已认识场景集合。
            elapsed：点击到识别出未知画面的实测耗时，写入该边的 `after_wait`。

        Returns：
            `RESULT_NEW_SCENE`。
        """
        draft_id = self._next_draft_id(by_id)
        candidate, reason = propose_candidate_region(before_frame, after_frame, self.options)
        features = []
        if candidate is not None:
            features.append(
                model.Feature(
                    name="auto",
                    kind=model.KIND_TEMPLATE,
                    mandatory=True,
                    shape=shapes.Shape.rectangle(*candidate),
                )
            )
            self._emit(
                "candidate",
                draft_id,
                f"已给出候选特征框 {candidate}",
                started,
            )
        else:
            # 差分给不出候选框时，仍要留一个**可识别**的特征，否则这个草稿永远
            # 识别不回来：探索点进它之后再想回到父场景就无从判断当前在哪，
            # 于是整个 BFS 停在第一个新画面上。退化方案是「取整帧为特征」——
            # 区分度差但至少能让识别层认出「是这一帧」，用户事后可以改小范围。
            features.append(
                model.Feature(
                    name="auto",
                    kind=model.KIND_TEMPLATE,
                    mandatory=True,
                    shape=shapes.Shape.rectangle(0.0, 0.0, 1.0, 1.0),
                )
            )
            self._emit(
                "candidate",
                draft_id,
                f"未给出候选框（{reason}），退化为整帧特征；请在草稿确认时缩小范围",
                started,
            )

        draft = model.Scene(
            scene_id=draft_id,
            name=f"探索发现（来自 {self._label(scene.scene_id)}.{area.name}）",
            features=features,
        )
        by_id[draft_id] = draft
        self._extra_scenes[draft_id] = draft
        result.new_scenes.append(draft_id)
        self._record_edge(result, scene.scene_id, area.name, draft_id, 1)
        self._remember_edge(scene.scene_id, area.name, draft_id, after_wait=elapsed)
        self.hooks.save_draft(draft, after_frame, before_frame)
        self.hooks.save_edge(scene.scene_id, area.name, draft_id, 1, after_wait=elapsed)
        # 让识别器立刻接纳新草稿：否则「点进去之后认不出自己在哪」，回退与后续
        # 展开全部失效，BFS 停在第一个新画面上。
        if self.hooks.on_draft_saved is not None:
            try:
                self.hooks.on_draft_saved(draft)
            except Exception as error:  # noqa: BLE001 — 刷新失败不阻断探索
                self._emit("warn", draft_id, f"刷新识别器失败：{error}", started)
        self._emit(
            "new_scene",
            draft_id,
            f"发现新画面（来源 {self._label(scene.scene_id)}.{area.name}），已存为草稿",
            started,
        )
        return RESULT_NEW_SCENE


def recognize_only_report(scene, frame, hooks, options=None, routes=None):
    """只识别不操作的预演：列出将被探测的区域。

    用于「运行前确认」——探索会真实点击游戏，让用户先看清这次会点哪些区域。

    Args：
        scene：当前场景。
        frame：当前帧。
        hooks：`ExploreHooks` 实例。
        options：`ExploreOptions` 实例。
        routes：`routes.RouteTable` 实例；用于判断哪些区域已经有边。

    Returns：
        `(clickable, skipped)` 二元组。`clickable` 是 `(area_name, target)` 列表
        （`target` 为已知目标，未探过时为空串）；`skipped` 是
        `(area_name, reason)` 列表。
    """
    del frame, hooks, options
    table = routes if routes is not None else _EMPTY_ROUTES
    clickable = []
    skipped = []
    for area in scene.areas:
        route = table.get(scene.scene_id, area.name)
        # 只有可寻路的路由才算「不用重复探索」：待确认的候选边会重新探一遍，
        # 与 `_area_resolved` 的口径保持一致。
        if route is not None and route.routable():
            skipped.append((area.name, "已有流转路由，无需重复探索"))
            continue
        if not area.explore_probe:
            skipped.append((area.name, "未开启「允许探索探测」"))
            continue
        clickable.append((area.name, ""))
    return clickable, skipped
