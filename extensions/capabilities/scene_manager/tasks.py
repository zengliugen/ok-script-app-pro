# -*- coding: utf-8 -*-
"""场景管理的两个任务：跳转与探索。

两个类都只是**薄壳**：导航与探索的全部逻辑在 `navigate.py` / `explore.py`，
这里只做三件事——从任务配置取参数、接上执行器（暂停/停止）、把结果写进
任务面板。这样拆分是为了让「流转图页的试走一步」与离线测试能复用同一份逻辑，
而不必先造一个任务实例。

**为什么必须是任务而不是普通函数**：
- 长跑（探索）要能被暂停与停止，这套机制由执行器提供；
- 从任务列表直接启动，用户不必先打开页签。

**注意不要对执行器申请独占**。`acquire_exclusive` 会禁用所有 enabled 的一次性
任务，而这两个任务自己就是一次性任务——申请独占等于把自己停掉，探索会当场
终止（症状是「点一次、跳转一次就结束」）。串行执行由执行器保证，不需要独占。

`register(config)` 里把两个任务都注册进 `onetime_tasks`，并都设 `visible=False`：
它们是被别处调用的内部能力，直接暴露在任务列表只会让用户困惑「这个任务是干什么的」。
"""

from ok import BaseTask, Icon

from extensions.capabilities.scene_manager import explore, runtime

# 任务名。同时是配置文件名（`configs/<名>.json`）与界面显示名。
GOTO_TASK_NAME = "Scene Goto"
EXPLORE_TASK_NAME = "Scene Explore"


class SceneGotoTask(BaseTask):
    """把游戏导航到指定场景。

    供两处使用：
    1. 代码调用：`task = get_task_by_class(SceneGotoTask); task.go("scene-0003")`；
    2. 任务列表直接运行：使用配置里的目标场景。

    `go()` 不抛导航失败异常，返回 `NavigationResult`，调用方按 `status` 分支。
    参数错误（场景不存在）才抛异常——那是编程错误，不是运行失败。
    """

    def __init__(self, *args, **kwargs):
        """构造任务并声明配置项。"""
        super().__init__(*args, **kwargs)
        self.name = GOTO_TASK_NAME
        self.description = "Navigate to the target scene along the scene flow graph"
        self.icon = Icon.SYNC
        # 内部能力：不在任务列表里显示，避免用户困惑。
        self.visible = False
        self.default_config.update(
            {
                "Target Scene": "",
                "Timeout Seconds": 120,
            }
        )
        self.config_description.update(
            {
                "Target Scene": "Target scene id (e.g. scene-0001); ignored when called from code",
                "Timeout Seconds": "Overall timeout; abort when exceeded",
            }
        )

    def go(self, scene_name, use_exclusive=None):
        """跳转到指定场景。

        Args：
            scene_name：目标场景 id。
            use_exclusive：**已废弃，传入也会被忽略**。保留参数只为兼容旧调用点。

        Returns：
            `navigate.NavigationResult` 实例。

        Raises：
            navigate.NavigationError：目标场景不存在或参数非法。

        Note：
            与本文件的探索任务同理：`acquire_exclusive` 会禁用所有 enabled 的
            一次性任务，而本任务自己就在其中——申请独占等于把自己停掉。区别是
            导航比探索短，很多时候在第一次 `check_enabled` 之前就跑完了，所以
            症状更隐蔽（表现为「偶尔能跑完、偶尔只走一步」）。
        """
        del use_exclusive  # 已废弃，见 docstring 的说明
        bridge = runtime.TaskBridge(self)
        navigator = bridge.build_navigator()
        result = navigator.goto(scene_name, should_stop=bridge.should_stop)
        self._report(result, navigator.scene_graph)
        return result

    def run(self):
        """任务入口：使用配置里的目标场景导航。"""
        target = str(self.config.get("Target Scene", "") or "").strip()
        if not target:
            self.log_warning("未配置目标场景，请在任务配置里填写场景 id")
            return
        self.go(target)

    def _report(self, result, scene_graph=None):
        """把导航结果写进任务面板与日志。

        Args：
            result：`navigate.NavigationResult` 实例。
            scene_graph：可选的流转图，用于把路径里的 id 换成场景名。
        """
        labels = {}
        if scene_graph is not None:
            for scene_id, scene in scene_graph.scenes.items():
                labels[scene_id] = f"{scene.name}（{scene_id}）" if scene.name and scene.name != scene_id else scene_id
        self.info_set("Target", labels.get(result.target, result.target))
        self.info_set("Status", result.status)
        self.info_set("Path", result.path_description(labels))
        if result.succeeded():
            self.log_info(f"已到达 {labels.get(result.target, result.target)}，耗时 {result.elapsed:.1f}s")
        else:
            self.log_warning(result.message)


class SceneExploreTask(BaseTask):
    """自动探索画面并采集跳转边。

    产出的是**草稿**场景与草稿边，需要在页签里人工确认后才进入正式路由图。
    探索会真实操作游戏（用单击探测），因此默认只探测标记了「允许探索探测」的区域。
    """

    def __init__(self, *args, **kwargs):
        """构造任务并声明配置项。"""
        super().__init__(*args, **kwargs)
        self.name = EXPLORE_TASK_NAME
        self.description = "BFS-explore the screen, collect transition edges, and produce drafts"
        self.icon = Icon.SYNC
        self.visible = False
        # 最近一次探索结果，供界面读取（探索在流转页触发，结果也要能在那里看到）。
        self.last_result = None
        # 实时进度：探索在后台线程跑，界面每秒读一次这个字典刷新弹窗。
        # 只放只读快照，避免界面直接碰探索器内部状态。
        self.progress = {
            "visited": 0,
            "new_scenes": 0,
            "edges": 0,
            "elapsed": 0.0,
            "scene": "",
            "event": "",
            "running": False,
        }
        # 界面「中断」按钮只置这个标记，探索器在每个检查点读 `should_stop`。
        self.cancel_requested = False
        self._seen_scenes = set()
        # 页签注入的编辑区库实例；None 时 `explore()` 自行打开编辑区。
        self.edit_library = None
        self.default_config.update(
            {
                "Max New Scenes": 30,
                "Max Seconds": 600,
                "Max Clicks Per Scene": 20,
            }
        )
        self.config_description.update(
            {
                "Max New Scenes": "Max number of new scenes before stopping",
                "Max Seconds": "Max total seconds before stopping",
                "Max Clicks Per Scene": "Max activations per scene, retries included",
            }
        )

    def explore(self, start_scene_id=None, options=None, use_exclusive=None):
        """执行一次探索。

        Args：
            start_scene_id：起点场景 id；省略时按当前画面识别。
            options：`explore.ExploreOptions`；省略时用配置值构造。
            use_exclusive：**已废弃，传入也会被忽略**。保留参数只为兼容旧调用点。

        Returns：
            `explore.ExploreResult` 实例。

        Note：
            **不能对执行器申请独占**。`TaskExecutor.acquire_exclusive` 的语义是
            「停掉正在跑的任务、把**所有 enabled 的一次性任务禁用**、取消别人的
            job」——而探索自己就是一个正在运行的一次性任务。申请独占的结果是
            **把自己禁用掉**，下一次 `check_enabled` 立即抛 `TaskDisabledException`
            终止探索。

            实测现象：点「开始探索」后只跳转一次就结束，日志里是
            `探索结束（completed）：访问 1 个场景，发现 0 个新场景，采集 0 条边`，
            中间夹着一行 `... is disabled, raise Exception`。而 `stop_reason`
            被记成 `completed` 而不是「被中断」，所以界面上看不出异常。

            独占本身也是多余的：一次性任务由执行器**串行**执行，探索运行期间
            轮不到别的任务；而 `Max Seconds` 已给出时间上限。
        """
        del use_exclusive  # 已废弃，见 docstring 的说明
        self.cancel_requested = False
        self._seen_scenes = set()
        self.progress.update(
            visited=0,
            new_scenes=0,
            edges=0,
            elapsed=0.0,
            scene="",
            event="",
            running=True,
        )
        # 探索是**编辑期工具**：读写的都应是编辑区（ok_scenes/），包括刚配好、
        # 还没导出的场景与路由。用产物区会「刚连的边探不到、认不出新场景」。
        # 产物区只服务运行期导航（`SceneGotoTask`）与导出。
        # 界面上由页签注入同一个实例；从代码直接调用时退回自行打开。
        edit_library = getattr(self, "edit_library", None) or runtime.open_edit_library()
        bridge = runtime.TaskBridge(self, edit_library, edit_library)
        # 重启设置必须从库元数据读取：它随场景库走，与探索顺序、点击参数同源。
        meta = bridge.draft_library.load_meta()
        if options is None:
            # 点击后的识别间隔与等待上限随库走（探索页可调），不写死在代码里：
            # 不同游戏的过场时间差别很大，同一个数字不可能都合适。
            options = explore.ExploreOptions(
                max_scenes=int(self.config.get("Max New Scenes", 30)),
                max_seconds=float(self.config.get("Max Seconds", 600)),
                max_clicks_per_scene=int(self.config.get("Max Clicks Per Scene", 20)),
                frame_interval=meta.probe_interval,
                settle_timeout=meta.settle_timeout,
                search_order=meta.search_order,
            )
        # 重启的两种用途共用同一个次数预算：
        # 1. **中途恢复**：探索卡在回不去的画面（草稿 / 未识别）时，经 hook 重启
        #    目标把它的状态重置到可继续探索，**队列与已采集结果全部保留**，不重跑；
        # 2. **启动/异常重试**：一开始就取不到画面或运行中抛异常，重跑一轮——此时
        #    还没有有效进度，重跑等价于继续。
        # max_scenes / max_seconds / max_clicks_per_scene 是用户设定的正常截止，
        # cancelled 是用户主动停止，都不重启。
        restartable = {explore.STOP_NO_START}
        max_restarts = int(meta.max_restarts) if meta.restart_on_incomplete else 0
        restarts = 0
        # 目标路径只在**第一次重启前**解析并锁定。优先用「当前已绑定窗口」的
        # exe：那是探索实际在操作的应用。只有还没绑定成功时才退回「选中设备」。
        # 目标被 kill 后框架会把首选设备回落到别的窗口，第二次再解析就会杀错进程。
        pinned_path = {"value": None}

        def restart_target_path():
            if pinned_path["value"] is None:
                bound = runtime.bound_target_path()
                if bound:
                    pinned_path["value"] = bound
                else:
                    status = runtime.target_status()
                    if status["supported"]:
                        pinned_path["value"] = status["path"]
            return pinned_path["value"]

        def restart_for_recovery():
            """探索中途请求重启目标；次数用尽、已取消或重启失败时返回 False。"""
            nonlocal restarts
            if bridge.should_stop() or restarts >= max_restarts:
                return False
            ok, reason = runtime.restart_target(meta.restart_wait, bridge.should_stop, restart_target_path())
            if not ok:
                if not bridge.should_stop():
                    self.log_warning(f"重启目标失败：{reason}")
                return False
            restarts += 1
            self.log_warning(f"目标状态无法继续，已重启目标（{restarts}/{max_restarts}）")
            return True

        # 开关关闭时连 hook 都不注入：探索器据此按原逻辑跳过，不做无谓的重启尝试。
        restart_hook = restart_for_recovery if max_restarts > 0 else None
        launched = False
        result = None
        while True:
            if meta.auto_launch and not launched:
                launched = True
                ok, reason = runtime.launch_target()
                if ok:
                    self.log_info("探索前已确保目标启动")
                else:
                    self.log_warning(f"探索前自动启动目标失败：{reason}")
            try:
                explorer = bridge.build_explorer(options, restart_target=restart_hook)
                result = explorer.run(start_scene_id=start_scene_id)
            except Exception as error:  # noqa: BLE001 — 异常按「未完成」处理
                # 中断是用户意图，不是「探索坏了」：不重启、不抛异常，按取消收尾。
                if bridge.should_stop():
                    result = explore.ExploreResult(stop_reason=explore.STOP_CANCELLED)
                    break
                if restarts >= max_restarts:
                    self.log_error(f"探索异常，且重启次数已用尽：{error}")
                    raise
                self.log_warning(f"探索异常（{error}），准备重启目标后重试（{restarts + 1}/{max_restarts}）")
                ok, reason = runtime.restart_target(meta.restart_wait, bridge.should_stop, restart_target_path())
                if not ok:
                    if bridge.should_stop():
                        result = explore.ExploreResult(stop_reason=explore.STOP_CANCELLED)
                        break
                    self.log_error(f"重启目标失败：{reason}")
                    raise
                restarts += 1
                if bridge.should_stop():
                    result = explore.ExploreResult(stop_reason=explore.STOP_CANCELLED)
                    break
                continue
            if result.stop_reason not in restartable or restarts >= max_restarts:
                break
            if bridge.should_stop():
                result.stop_reason = explore.STOP_CANCELLED
                break
            self.log_warning(f"探索未完成（{result.stop_reason}），准备重启目标后重试（{restarts + 1}/{max_restarts}）")
            ok, reason = runtime.restart_target(meta.restart_wait, bridge.should_stop, restart_target_path())
            if not ok:
                if bridge.should_stop():
                    result.stop_reason = explore.STOP_CANCELLED
                    break
                self.log_warning(f"重启目标失败：{reason}")
                break
            restarts += 1
            if bridge.should_stop():
                result.stop_reason = explore.STOP_CANCELLED
                break
        if result is not None:
            result.restarts = restarts
            self.progress["elapsed"] = max(self.progress["elapsed"], result.elapsed)
        self.progress["running"] = False
        self._report(result)
        return result

    def _on_explore_event(self, event):
        """把探索事件累加成进度快照，供界面弹窗每秒读取。

        Args：
            event：`explore.ExploreEvent` 实例。

        Note：
            只做累加与覆盖，不碰探索器状态：探索在后台线程，界面只读这个字典。
            重启后不重置计数——用户关心的是「这一轮总共发现多少」。
        """
        runtime.log_explore_event(self, event)
        progress = self.progress
        progress["elapsed"] = max(progress["elapsed"], event.elapsed)
        progress["event"] = event.detail
        if event.scene_id:
            progress["scene"] = event.scene_id
            self._seen_scenes.add(event.scene_id)
            progress["visited"] = len(self._seen_scenes)
        if event.kind == "new_scene":
            progress["new_scenes"] += 1
        elif event.kind == "edge":
            progress["edges"] += 1

    def run(self):
        """任务入口：按当前画面开始探索。"""
        self.explore()

    def _report(self, result):
        """把探索结果写进任务面板与日志，**并存下最近一次结果**。

        Args：
            result：`explore.ExploreResult` 实例。

        Note：
            `last_result` 是给界面读的：任务面板（`info_set`）只在任务页显示，
            而用户是在**流转页**点的「开始探索」，结果必须能在那里看到——
            否则表现为「跑完了什么都没留下」。
        """
        self.last_result = result
        self.info_set("Stop Reason", result.stop_reason)
        self.info_set("Visited", str(len(result.visited)))
        self.info_set("New Scenes", str(len(result.new_scenes)))
        self.info_set("Edges", str(len(result.edges)))
        self.log_info(result.summary())
        # 事件逐条进日志：出问题时「哪一步被跳过、为什么」只能从这些事件里看，
        # 摘要只有四个总数。与实时回调走同一个函数，格式保持一致。
        for event in result.events:
            runtime.log_explore_event(self, event)


def build_tasks():
    """返回要注册进 `onetime_tasks` 的任务清单。

    Returns：
        `[模块路径, 类名]` 列表，供 `config['onetime_tasks']` 追加。
    """
    return [
        ["extensions.capabilities.scene_manager.tasks", "SceneGotoTask"],
        ["extensions.capabilities.scene_manager.tasks", "SceneExploreTask"],
    ]
