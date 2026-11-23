# -*- coding: utf-8 -*-
"""导航器：把「跳到指定场景」做成一件可复用的纯逻辑。

与执行器的关系是**注入而非继承**：本类只要求调用方提供四个动作（取帧、识别、
激活区域、等待），因此它既能被 `SceneGotoTask` 在任务里调用，也能被流转图页的
「试走一步」与离线测试直接调用，无需先造一个任务实例。

**「激活区域」是一个注入动作，不是一个动作类型**：区域本身不绑定单击/双击/长按，
导航只要求调用方给出「让这块区域生效」的实现（`runtime.TaskBridge.activate_area`
的默认实现是单击）。导航因此对区域的具体操作方式保持中立。

失败恢复按「重试 → 回退 → 报错」三级：

1. **重试**：识别不到当前场景时重试 N 次。游戏有动画、弹窗、加载中，单次识别
   失败是常态。
2. **回退**：重试仍失败时，尝试激活「返回区域」（或入边反推的区域）退回上游，
   重新识别后再走。探索与导航都会被一个意外的弹窗打断，回退比重新规划便宜。
3. **报错**：两者都失败时返回失败结果并保留现场信息（最后识别结果、已走步数），
   由调用方决定后续。**不抛异常**——调用方多是任务流程，要根据结果分支，
   异常会让整条流程中断。

本模块**不导入 `ok` 与 `PySide6`**：全部交互经依赖注入，逻辑可用假桩完整验证。
"""

import time

from extensions.capabilities.scene_manager import graph as scene_graph

# 结果状态。
STATUS_SUCCESS = "success"
STATUS_NO_PATH = "no_path"
STATUS_TARGET_NOT_FOUND = "target_not_found"
STATUS_NOT_IN_LIBRARY = "not_in_library"
STATUS_STEP_FAILED = "step_failed"
STATUS_CANCELLED = "cancelled"

# 失败原因（细分，供界面展示）。
REASON_RECOGNIZE_FAILED = "recognize_failed"
REASON_TARGET_NOT_REACHED = "target_not_reached"
REASON_AREA_MISSING = "area_missing"
REASON_EXCEPTION = "exception"


class NavigationError(ValueError):
    """导航参数错误（属调用方编程错误，不是运行失败）。"""


class StepRecord:
    """一步执行记录。

    Attributes:
        index: 步序号，从 0 开始。
        from_scene: 起点场景 id。
        area_name: 被激活的区域名。
        expected: 预期到达的场景 id。
        actual: 实际识别到的场景 id；未识别到时为空串。
        success: 该步是否达成预期。
        elapsed: 该步耗时（秒）。
        note: 失败原因或补充说明。
    """

    def __init__(self, index, from_scene, area_name, expected, actual="", success=False, elapsed=0.0, note=""):
        """构造一步记录。

        Args：
            index：步序号。
            from_scene：起点场景 id。
            area_name：被激活的区域名。
            expected：预期到达场景 id。
            actual：实际到达场景 id。
            success：是否成功。
            elapsed：耗时秒数。
            note：补充说明。
        """
        self.index = index
        self.from_scene = from_scene
        self.area_name = area_name
        self.expected = expected
        self.actual = actual
        self.success = success
        self.elapsed = elapsed
        self.note = note

    def to_dict(self):
        """序列化为字典。

        Returns：
            字典。
        """
        return {
            "index": self.index,
            "from": self.from_scene,
            "area": self.area_name,
            "expected": self.expected,
            "actual": self.actual,
            "success": self.success,
            "elapsed": round(self.elapsed, 3),
            "note": self.note,
        }


class NavigationResult:
    """一次跳转的结果。

    Attributes:
        status: 取 `STATUS_*` 之一。
        target: 目标场景 id。
        start: 起点场景 id（导航开始时识别到的场景）。
        planned: 规划出的路径（`RouteStep` 列表）。
        steps: 实际执行记录（`StepRecord` 列表）。
        reason: 失败细分原因；成功时为空串。
        message: 面向用户的一句话说明。
        elapsed: 总耗时（秒）。
        last_recognition: 最后一次识别结果（`MatchResult`（来自 `recognize` 模块））。
    """

    def __init__(
        self,
        status,
        target="",
        start="",
        planned=None,
        steps=None,
        reason="",
        message="",
        elapsed=0.0,
        last_recognition=None,
    ):
        """构造结果。

        Args：
            status：结果状态。
            target：目标场景 id。
            start：起点场景 id。
            planned：规划路径。
            steps：执行记录。
            reason：失败原因。
            message：说明文本。
            elapsed：总耗时。
            last_recognition：最后识别结果。
        """
        self.status = status
        self.target = target
        self.start = start
        self.planned = [] if planned is None else planned
        self.steps = [] if steps is None else steps
        self.reason = reason
        self.message = message
        self.elapsed = elapsed
        self.last_recognition = last_recognition

    def succeeded(self):
        """返回是否成功到达目标。

        Returns：
            状态为 `STATUS_SUCCESS` 时为 True。
        """
        return self.status == STATUS_SUCCESS

    def path_description(self, labels=None):
        """返回人类可读的路径描述。

        Args：
            labels：可选的 `{scene_id: 显示名}` 映射；传入时路径里显示场景名。

        Returns：
            形如 `主界面（scene-0001） --btn--> 关卡选择（scene-0002）` 的字符串；
            空路径时为 `（无需移动）`。
        """
        if not self.planned:
            return "（无需移动）"
        labels = labels or {}
        parts = []
        for step in self.planned:
            parts.append(
                f"{labels.get(step.from_scene, step.from_scene)} --{step.area_name}--> "
                f"{labels.get(step.to_scene, step.to_scene)}"
            )
        return " ; ".join(parts)

    def to_dict(self):
        """序列化为字典，供界面与日志使用。

        Returns：
            字典。
        """
        return {
            "status": self.status,
            "target": self.target,
            "start": self.start,
            "reason": self.reason,
            "message": self.message,
            "elapsed": round(self.elapsed, 3),
            "planned": [step.to_dict() for step in self.planned],
            "steps": [step.to_dict() for step in self.steps],
        }


class NavigatorHooks:
    """导航器依赖的四个外部动作。

    全部以可调用对象注入，因此测试里用假桩、运行期用任务实例上的真实方法，
    导航逻辑本身不关心谁提供它们。

    Attributes:
        capture_frame: 无参函数，返回当前帧。
        recognize: 函数 `(frame) -> MatchResult`，识别当前场景。
        activate_area: 函数 `(scene, area) -> None`，让某场景的某区域生效（默认实现是单击）。
        wait: 函数 `(seconds) -> None`，等待。
        clock: 无参函数，返回单调时间（秒），用于计时；便于测试注入假时钟。
    """

    def __init__(self, capture_frame, recognize, activate_area, wait, clock=None):
        """构造依赖集合。

        Args：
            capture_frame：取帧函数。
            recognize：识别函数。
            activate_area：让区域生效的函数。
            wait：等待函数。
            clock：计时函数，缺省用 `time.monotonic`。
        """
        self.capture_frame = capture_frame
        self.recognize = recognize
        self.activate_area = activate_area
        self.wait = wait
        self.clock = clock or time.monotonic


class NavigateOptions:
    """导航的可调参数。

    Attributes:
        retry: 识别当前场景的重试次数。
        retry_wait: 每次重试前等待秒数。
        rollback_limit: 回退尝试次数上限。
        poll_interval: 每步激活后首次轮询间隔（秒）。
        step_timeout: 单步到达目标的等待上限（秒）。
        confirm_target: 到达后是否再识别一次确认（防止刚好路过）。
    """

    def __init__(
        self, retry=3, retry_wait=0.6, rollback_limit=1, poll_interval=0.4, step_timeout=6.0, confirm_target=True
    ):
        """构造参数。

        Args：
            retry：识别重试次数。
            retry_wait：重试等待秒数。
            rollback_limit：回退上限。
            poll_interval：轮询间隔。
            step_timeout：单步超时。
            confirm_target：是否确认到达。
        """
        self.retry = max(1, int(retry))
        self.retry_wait = max(0.0, float(retry_wait))
        self.rollback_limit = max(0, int(rollback_limit))
        self.poll_interval = max(0.05, float(poll_interval))
        self.step_timeout = max(0.5, float(step_timeout))
        self.confirm_target = bool(confirm_target)


class SceneNavigator:
    """沿流转图把游戏导航到指定场景。

    Attributes:
        scene_graph: `graph.SceneGraph` 实例。
        hooks: `NavigatorHooks` 实例。
        options: `NavigateOptions` 实例。
    """

    def __init__(self, scene_graph_instance, hooks, options=None):
        """构造导航器。

        Args：
            scene_graph_instance：`graph.SceneGraph` 实例。
            hooks：`NavigatorHooks` 实例。
            options：`NavigateOptions` 实例；为 None 时用默认值。

        Raises：
            NavigationError：图或依赖为空。
        """
        if scene_graph_instance is None:
            raise NavigationError("导航器需要流转图，收到 None")
        if hooks is None:
            raise NavigationError("导航器需要依赖集合，收到 None")
        self.scene_graph = scene_graph_instance
        self.hooks = hooks
        self.options = options or NavigateOptions()

    # --- 识别 ---------------------------------------------------------------

    def identify(self):
        """识别当前场景。

        Returns：
            `MatchResult`（来自 `recognize` 模块）；连续失败时返回最后一次结果（可能未命中）。

        Raises：
            NavigationError：取帧持续失败。
        """
        last = None
        for attempt in range(self.options.retry):
            frame = self.hooks.capture_frame()
            if frame is None:
                if attempt + 1 < self.options.retry:
                    self.hooks.wait(self.options.retry_wait)
                continue
            last = self.hooks.recognize(frame)
            if last is not None and last.matched():
                return last
            if attempt + 1 < self.options.retry:
                self.hooks.wait(self.options.retry_wait)
        if last is None:
            raise NavigationError("连续取帧失败，无法识别当前场景")
        return last

    # --- 导航 ---------------------------------------------------------------

    def goto(self, target_scene, should_stop=None):
        """导航到目标场景。

        Args：
            target_scene：目标场景 id。
            should_stop：可选的无参函数，返回 True 时中止导航（供任务停止按钮使用）。

        Returns：
            `NavigationResult` 实例。失败不抛异常，由调用方按 `status` 分支。

        Raises：
            NavigationError：目标场景不存在，或参数非法（属调用方错误）。
        """
        started = self.hooks.clock()
        if not target_scene:
            raise NavigationError("目标场景不能为空")
        if not self.scene_graph.has_scene(target_scene):
            raise NavigationError(f"目标场景 {target_scene!r} 不在流转图中")

        current = self.identify()
        if not current.matched():
            return NavigationResult(
                status=STATUS_NOT_IN_LIBRARY,
                target=target_scene,
                reason=REASON_RECOGNIZE_FAILED,
                message="无法识别当前画面属于哪个场景，请先在配置页为该画面建立场景",
                elapsed=self.hooks.clock() - started,
                last_recognition=current,
            )

        start_id = current.scene_id()
        if start_id == target_scene:
            return NavigationResult(
                status=STATUS_SUCCESS,
                target=target_scene,
                start=start_id,
                message="已在目标场景",
                elapsed=self.hooks.clock() - started,
                last_recognition=current,
            )

        planned = self.scene_graph.path(start_id, target_scene)
        if planned is None:
            return NavigationResult(
                status=STATUS_NO_PATH,
                target=target_scene,
                start=start_id,
                reason=REASON_TARGET_NOT_REACHED,
                message=f"从 {self._label(start_id)} 到 {self._label(target_scene)} 没有可用路径，请检查流转图的边",
                elapsed=self.hooks.clock() - started,
                last_recognition=current,
            )

        steps = []
        rollbacks = 0
        index = 0
        while index < len(planned):
            if should_stop is not None and should_stop():
                return NavigationResult(
                    status=STATUS_CANCELLED,
                    target=target_scene,
                    start=start_id,
                    planned=planned,
                    steps=steps,
                    message="导航被手动中止",
                    elapsed=self.hooks.clock() - started,
                    last_recognition=current,
                )

            step = planned[index]
            record, arrived = self._execute_step(index, step, current, should_stop)
            steps.append(record)

            # 中止发生在步内（轮询时用户点了停止）时不是「失败」，不得触发回退：
            # 回退也是一次操作，用户要求停止却继续操作别处，与意图相反。
            if should_stop is not None and should_stop():
                return NavigationResult(
                    status=STATUS_CANCELLED,
                    target=target_scene,
                    start=start_id,
                    planned=planned,
                    steps=steps,
                    message="导航被手动中止",
                    elapsed=self.hooks.clock() - started,
                    last_recognition=current,
                )

            if record.success:
                index += 1
                continue

            # 一步失败：先重试当前步（重新识别），再尝试回退，最后放弃。
            if rollbacks >= self.options.rollback_limit:
                return NavigationResult(
                    status=STATUS_STEP_FAILED,
                    target=target_scene,
                    start=start_id,
                    planned=planned,
                    steps=steps,
                    reason=record.note or REASON_TARGET_NOT_REACHED,
                    message=(
                        f"第 {index + 1} 步失败（{step.from_scene} 激活 {step.area_name} 后"
                        f"未到达 {step.to_scene}），已回退 {rollbacks} 次仍无法继续"
                    ),
                    elapsed=self.hooks.clock() - started,
                    last_recognition=current,
                )

            rollbacks += 1
            recovered = self._try_rollback(step.from_scene, should_stop)
            if recovered is None:
                return NavigationResult(
                    status=STATUS_STEP_FAILED,
                    target=target_scene,
                    start=start_id,
                    planned=planned,
                    steps=steps,
                    reason=REASON_RECOGNIZE_FAILED,
                    message=(f"第 {index + 1} 步失败后无法回退到已知场景，导航中止"),
                    elapsed=self.hooks.clock() - started,
                    last_recognition=current,
                )
            current = recovered
            # 回退后重新规划：回退可能落到了别的分支，原计划未必仍然最优。
            replanned = self.scene_graph.path(current.scene_id(), target_scene)
            if replanned is None:
                return NavigationResult(
                    status=STATUS_NO_PATH,
                    target=target_scene,
                    start=start_id,
                    planned=planned,
                    steps=steps,
                    reason=REASON_TARGET_NOT_REACHED,
                    message=f"回退到 {self._label(current.scene_id())} 后已无可用路径",
                    elapsed=self.hooks.clock() - started,
                    last_recognition=current,
                )
            planned = replanned
            index = 0

        if self.options.confirm_target:
            final = self.identify()
            current = final
            if not final.matched() or final.scene_id() != target_scene:
                return NavigationResult(
                    status=STATUS_STEP_FAILED,
                    target=target_scene,
                    start=start_id,
                    planned=planned,
                    steps=steps,
                    reason=REASON_TARGET_NOT_REACHED,
                    message=f"走完全部步骤但当前识别结果是 {self._label(final.scene_id()) if final.scene_id() else '未识别'}",
                    elapsed=self.hooks.clock() - started,
                    last_recognition=final,
                )

        return NavigationResult(
            status=STATUS_SUCCESS,
            target=target_scene,
            start=start_id,
            planned=planned,
            steps=steps,
            message=f"已到达 {self._label(target_scene)}",
            elapsed=self.hooks.clock() - started,
            last_recognition=current,
        )

    def _label(self, scene_id):
        """返回场景的显示文本。

        Args：
            scene_id：场景 id。

        Returns：
            `名称（scene-0001）`；没有名称或名称与 id 相同时只显示 id。
        """
        scene = self.scene_graph.scenes.get(scene_id)
        if scene is None:
            return scene_id
        if not scene.name or scene.name == scene_id:
            return scene_id
        return f"{scene.name}（{scene_id}）"

    def _execute_step(self, index, step, current, should_stop):
        """执行一步：激活区域，然后等待到达预期场景。

        Args：
            index：步序号。
            step：`RouteStep` 实例。
            current：当前识别结果（`MatchResult`（来自 `recognize` 模块））。
            should_stop：中止判定函数。

        Returns：
            `(StepRecord, MatchResult)` 二元组。第二步在成功时为到达后的识别结果，
            失败时为最后一次识别结果。
        """
        started = self.hooks.clock()
        scene = self.scene_graph.scenes.get(step.from_scene)
        area = scene.find_area(step.area_name) if scene else None
        if area is None:
            return (
                StepRecord(
                    index,
                    step.from_scene,
                    step.area_name,
                    step.to_scene,
                    note=REASON_AREA_MISSING,
                    elapsed=self.hooks.clock() - started,
                ),
                current,
            )

        try:
            self.hooks.activate_area(scene, area)
        except Exception as error:  # noqa: BLE001 — 激活失败应转为结果而非抛出
            return (
                StepRecord(
                    index,
                    step.from_scene,
                    step.area_name,
                    step.to_scene,
                    note=f"{REASON_EXCEPTION}: {error}",
                    elapsed=self.hooks.clock() - started,
                ),
                current,
            )

        # 等待秒数从 **RouteStep** 取，不从区域取：区域已经不再承载跳转信息，
        # 等待时长属于「这条边走多久」，与路由一起存。
        if step.after_wait > 0:
            self.hooks.wait(step.after_wait)

        deadline = self.hooks.clock() + self.options.step_timeout
        last = current
        while self.hooks.clock() < deadline:
            if should_stop is not None and should_stop():
                break
            frame = self.hooks.capture_frame()
            if frame is not None:
                last = self.hooks.recognize(frame)
                if last is not None and last.matched():
                    if last.scene_id() == step.to_scene:
                        return (
                            StepRecord(
                                index,
                                step.from_scene,
                                step.area_name,
                                step.to_scene,
                                actual=last.scene_id(),
                                success=True,
                                elapsed=self.hooks.clock() - started,
                            ),
                            last,
                        )
                    # 到了别的场景：可能走了岔路，结束本步等待交给上层重规划。
                    return (
                        StepRecord(
                            index,
                            step.from_scene,
                            step.area_name,
                            step.to_scene,
                            actual=last.scene_id(),
                            note=REASON_TARGET_NOT_REACHED,
                            elapsed=self.hooks.clock() - started,
                        ),
                        last,
                    )
            self.hooks.wait(self.options.poll_interval)

        return (
            StepRecord(
                index,
                step.from_scene,
                step.area_name,
                step.to_scene,
                actual=last.scene_id() if last else "",
                note=REASON_TARGET_NOT_REACHED,
                elapsed=self.hooks.clock() - started,
            ),
            last,
        )

    def _try_rollback(self, from_scene, should_stop):
        """从指定场景回退到上游。

        Args：
            from_scene：要回退的场景 id。
            should_stop：中止判定函数。

        Returns：
            回退成功时的识别结果（`MatchResult`（来自 `recognize` 模块））；失败返回 None。
        """
        hint = self.scene_graph.return_step(from_scene)
        if hint is None:
            return None
        area_name, _target = hint
        scene = self.scene_graph.scenes.get(from_scene)
        area = scene.find_area(area_name) if scene else None
        if area is None:
            return None
        try:
            self.hooks.activate_area(scene, area)
        except Exception:  # noqa: BLE001 — 回退失败不抛，交给上层判定
            return None
        # 回退的等待时长同样从图上取：回退目标区域未必有对应的路由（可能是场景
        # 自己的「返回」区域），此时取不到边就按 0 处理，不额外等。
        wait = self.scene_graph.wait_for(from_scene, area_name)
        if wait > 0:
            self.hooks.wait(wait)
        if should_stop is not None and should_stop():
            return None
        try:
            result = self.identify()
        except NavigationError:
            return None
        if result is not None and result.matched():
            return result
        return None


def plan_preview(scene_graph_instance, from_scene, to_scene):
    """给出规划预览（不执行），供界面「预览路径」使用。

    Args：
        scene_graph_instance：`graph.SceneGraph` 实例。
        from_scene：起点场景 id。
        to_scene：目标场景 id。

    Returns：
        `(steps, error)` 二元组。`steps` 为 `RouteStep` 列表（不可达时为 None），
        `error` 为错误说明（正常时为 None）。

    Raises：
        None：不抛异常，错误经返回值表达，便于界面直接展示。
    """
    try:
        steps = scene_graph_instance.path(from_scene, to_scene)
    except scene_graph.GraphError as error:
        return None, str(error)
    if steps is None:
        return (
            None,
            f"从 {_scene_name(scene_graph_instance, from_scene)} 到 {_scene_name(scene_graph_instance, to_scene)} 不可达",
        )
    return steps, None


def _scene_name(scene_graph_instance, scene_id):
    """返回 `名称（id）` 形式的场景显示文本，未知场景退回 id。

    Args：
        scene_graph_instance：`graph.SceneGraph` 实例。
        scene_id：场景 id。

    Returns：
        显示文本。
    """
    scene = scene_graph_instance.scenes.get(scene_id)
    if scene is None or not scene.name or scene.name == scene_id:
        return scene_id
    return f"{scene.name}（{scene_id}）"


def describe_scene(scene_graph_instance, scene_id):
    """返回场景的一句话描述，供日志与界面使用。

    Args：
        scene_graph_instance：`graph.SceneGraph` 实例。
        scene_id：场景 id。

    Returns：
        形如 `主界面（scene-0001，3 个区域 / 2 条出边）` 的字符串；不存在时为
        `未知场景（scene-9999）`。
    """
    scene = scene_graph_instance.scenes.get(scene_id)
    if scene is None:
        return f"未知场景（{scene_id}）"
    extended = len(scene.features)
    # 出边数从图上取：区域不再持有跳转信息，`scene.routable_areas()` 已随字段
    # 一起移除。图上只有可寻路的边，因此这个数正好是「能走的路」的条数。
    out_edges = len(scene_graph_instance.outgoing(scene_id))
    return f"{scene.name}（{scene_id}，{extended} 个特征 / {len(scene.areas)} 个区域 / {out_edges} 条出边）"
