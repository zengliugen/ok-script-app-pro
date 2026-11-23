# -*- coding: utf-8 -*-
"""点击前的前台化：等异步切换生效、前台锁兜底、失败时明确报错、不破坏窗口。

用户实测「所有后台模式都点不动」。定位到三层原因：

1. **`SetForegroundWindow` 是异步的**。调用返回后立即读前台窗口仍是旧值，
   真正生效耗时 6—12ms（5 次采样）。上游 `bring_to_front()` 就是这么写的，
   于是它无法判断是否真的切过去——遇到拒绝也只能当成功，点击被静默丢弃。
   修法是轮询等待，而不是调用后立刻判断。
2. **前台锁会静默拒绝裸调**。此时可用 `AttachThreadInput` 挂到「当前持有前台的
   线程」再切。实测挂上与不挂上的差异，以及「挂目标窗口线程反而失败」的坑。
3. 切不过去时后端仍继续执行，`pynput` 只打一行
   `window in background, not clickable` 就 return，界面却照样显示点击成功。

另有一条踩过的坑写进断言：**不得用 `SW_MINIMIZE` 制造前台事件**。它对
`resizable(False, False)` 的窗口会永久压坏尺寸（靶场被压成 136x40、客户区只剩
120x1），此后所有点击都落到别的窗口上，故障现象与「置前失败」完全混淆。

> 排查记录里曾有一版结论写「裸调 3/3 失败」。那一轮实验的窗口已被 `SW_MINIMIZE`
> 压坏（点击全落到别的窗口），数据不可采信；在干净窗口上复测，裸调 5/5 成功。
> 保留这段是为了提醒：**验证窗口类问题时先确认窗口尺寸没被前一轮实验破坏**。
"""

import unittest

from extensions.capabilities.scene_manager import runtime


class _HwndWindow:
    """最小化的窗口替身：只暴露句柄。"""

    def __init__(self, hwnd=0, top_hwnd=0):
        """记录句柄。

        Args:
            hwnd: 客户区窗口句柄。
            top_hwnd: 顶层窗口句柄。
        """
        self.hwnd = hwnd
        self.top_hwnd = top_hwnd


class _Backend:
    """交互后端替身，类名可配置——判定依赖类型名。"""

    def __init__(self, class_name):
        """绑定类型名。

        Args:
            class_name: 期望的 `type(instance).__name__`。
        """
        self.__class__ = type(class_name, (object,), {})


class _Executor:
    """执行器替身：只提供 interaction。"""

    def __init__(self, interaction):
        """绑定交互后端。

        Args:
            interaction: 交互后端实例或 None。
        """
        self.interaction = interaction


class _Task:
    """任务替身：可配置窗口、后端与 ensure_in_front 行为。"""

    def __init__(self, hwnd_window=None, interaction=None, ensure_ok=True):
        """初始化替身。

        Args:
            hwnd_window: 窗口替身，None 表示 ADB 设备。
            interaction: 交互后端实例。
            ensure_ok: `ensure_in_front` 是否"成功"（本替身不改变真实前台）。
        """
        self.hwnd = hwnd_window
        self.executor = _Executor(interaction)
        self.ensure_calls = 0
        self._ensure_ok = ensure_ok

    def ensure_in_front(self):
        """记录一次调用。"""
        self.ensure_calls += 1


def _bridge(task):
    """造一个只填了 task 的 bridge（跳过 __init__）。

    Args:
        task: 任务替身。

    Returns:
        `TaskBridge` 实例。
    """
    bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
    bridge.task = task
    return bridge


class TestBackendNeedsForeground(unittest.TestCase):
    """判定哪些交互后端只在前台生效。

    依据是各后端的 `should_capture()` / `clickable()`：`Pynput`、`PyDirect` 与
    `ForegroundPostMessage` 都返回 `hwnd_window.is_foreground()`；只有
    `PostMessage` 与 `Genshin` 恒为 True。

    这条判定决定「切前台失败时该不该报错」——判错的两个方向都有代价：
    判成需要前台 → 后台后端被无谓拦截；判成不需要 → 前台后端静默丢点击。
    """

    def test_post_message_is_background(self):
        """`PostMessage` 可后台点击。"""
        bridge = _bridge(_Task(_HwndWindow(1), _Backend("PostMessageInteraction")))
        self.assertFalse(bridge._backend_needs_foreground())

    def test_genshin_is_background(self):
        """`Genshin` 可后台点击。"""
        bridge = _bridge(_Task(_HwndWindow(1), _Backend("GenshinInteraction")))
        self.assertFalse(bridge._backend_needs_foreground())

    def test_foreground_post_message_still_needs_foreground(self):
        """`ForegroundPostMessage` 名字里带 PostMessage，但仍要求前台。

        这是最容易判错的一个：名字看起来像后台投递，实际继承 `GenshinInteraction`
        后重写了 `clickable()` 为 `hwnd_window.is_foreground()`。
        """
        bridge = _bridge(_Task(_HwndWindow(1), _Backend("ForegroundPostMessageInteraction")))
        self.assertTrue(bridge._backend_needs_foreground())

    def test_pynput_needs_foreground(self):
        """`Pynput` 只在前台合成输入。"""
        bridge = _bridge(_Task(_HwndWindow(1), _Backend("PynputInteraction")))
        self.assertTrue(bridge._backend_needs_foreground())

    def test_pydirect_needs_foreground(self):
        """`PyDirect` 只在前台合成输入。"""
        bridge = _bridge(_Task(_HwndWindow(1), _Backend("PyDirectInteraction")))
        self.assertTrue(bridge._backend_needs_foreground())

    def test_missing_interaction_defaults_to_needing_foreground(self):
        """取不到后端时按「需要前台」处理。

        宁可多报一次错，也不要让用户面对「点了没反应」。
        """
        bridge = _bridge(_Task(_HwndWindow(1), None))
        self.assertTrue(bridge._backend_needs_foreground())


class TestWindowHandle(unittest.TestCase):
    """句柄选取：优先客户区窗口，退回顶层窗口。"""

    def test_prefers_client_hwnd(self):
        """客户区句柄优先。"""
        bridge = _bridge(_Task(_HwndWindow(11, 22)))
        self.assertEqual(bridge._window_handle(), 11)

    def test_falls_back_to_top_hwnd(self):
        """客户区句柄为空时退回顶层句柄。"""
        bridge = _bridge(_Task(_HwndWindow(0, 22)))
        self.assertEqual(bridge._window_handle(), 22)

    def test_returns_zero_without_window(self):
        """ADB 设备没有 hwnd，返回 0。"""
        bridge = _bridge(_Task(None))
        self.assertEqual(bridge._window_handle(), 0)

    def test_returns_zero_when_both_empty(self):
        """两个句柄都为空时返回 0。"""
        bridge = _bridge(_Task(_HwndWindow(0, 0)))
        self.assertEqual(bridge._window_handle(), 0)


class TestEnsureActionable(unittest.TestCase):
    """前台化失败时的处理分歧。"""

    def _bridge_with(self, interaction_name, front_ok):
        """造一个前台化结果可控的 bridge。

        Args:
            interaction_name: 交互后端类型名。
            front_ok: `_ensure_front` 的返回值。

        Returns:
            (bridge, task) 二元组。
        """
        task = _Task(_HwndWindow(1), _Backend(interaction_name))
        bridge = _bridge(task)
        bridge._ensure_front = lambda: front_ok
        return bridge, task

    def _spy_bridge(self, interaction_name):
        """造一个记录「是否尝试过切前台」的 bridge。

        Args:
            interaction_name: 交互后端类型名。

        Returns:
            (bridge, 调用计数列表)。列表为 [次数]，便于闭包内累加。
        """
        task = _Task(_HwndWindow(1), _Backend(interaction_name))
        bridge = _bridge(task)
        attempts = [0]

        def spy():
            """记录一次切前台尝试，并报告成功。"""
            attempts[0] += 1
            return True

        bridge._ensure_front = spy
        return bridge, attempts

    def test_background_backend_does_not_touch_foreground(self):
        """后台后端**一次都不许**尝试切前台。

        用户实测反馈：「不管交互选的是前还是后，都会先把目标程序弹到前台」。
        后台投递与焦点无关，切前台是纯干扰——用户选后台就是不想被打断。
        这个判定必须**先于**切前台：切了就无法撤销，事后判断再正确也晚了。
        """
        for name in ("PostMessageInteraction", "GenshinInteraction"):
            bridge, attempts = self._spy_bridge(name)
            bridge._ensure_actionable(with_pointer=True)
            self.assertEqual(attempts[0], 0, f"{name} 不该尝试切前台")

    def test_foreground_backend_does_attempt_foreground(self):
        """只在前台生效的后端必须尝试切前台（对照，防止上条写成永不移除）。"""
        bridge, attempts = self._spy_bridge("PynputInteraction")
        bridge._ensure_actionable(with_pointer=True)
        self.assertEqual(attempts[0], 1, "前台后端应当且只应当切一次")

    def test_raises_when_pointer_and_foreground_required(self):
        """真实指针 + 只在前台生效的后端：切不过去必须报错。

        否则后端只打一行日志就 return，界面仍显示点击成功，用户看到的是
        「点了没反应却没有任何提示」。
        """
        bridge, _ = self._bridge_with("PynputInteraction", front_ok=False)
        with self.assertRaises(RuntimeError) as caught:
            bridge._ensure_actionable(with_pointer=True)
        message = str(caught.exception)
        self.assertIn("无法切到前台", message)
        self.assertIn("PostMessage", message, "报错里要给出可操作的替代方案")

    def test_no_raise_when_backend_is_background(self):
        """后台后端切不过去仍继续——它走 PostMessage，与焦点无关。"""
        bridge, _ = self._bridge_with("PostMessageInteraction", front_ok=False)
        bridge._ensure_actionable(with_pointer=True)

    def test_no_raise_when_pointer_not_required(self):
        """动作不依赖真实指针时不报错。"""
        bridge, _ = self._bridge_with("PynputInteraction", front_ok=False)
        bridge._ensure_actionable(with_pointer=False)

    def test_no_raise_when_foreground_succeeds(self):
        """切前台成功时不报错。"""
        bridge, _ = self._bridge_with("PynputInteraction", front_ok=True)
        bridge._ensure_actionable(with_pointer=True)


class TestEnsureFrontFallback(unittest.TestCase):
    """`_ensure_front` 的分支：没有 hwnd 时交给框架。"""

    def test_adb_delegates_to_task(self):
        """ADB 设备（无 hwnd）改调 `task.ensure_in_front`。

        框架的该方法同时处理 `adb_ensure_in_front` 与窗口设备两条分支，
        自己判断设备类型会漏掉 ADB 那条。
        """
        task = _Task(None)
        bridge = _bridge(task)
        self.assertTrue(bridge._ensure_front())
        self.assertEqual(task.ensure_calls, 1)

    def test_adb_without_ensure_method_does_not_crash(self):
        """任务没有 `ensure_in_front` 时静默跳过，不抛异常。"""
        bridge = _bridge(object())
        self.assertTrue(bridge._ensure_front())


class TestForceForegroundSafety(unittest.TestCase):
    """`_force_foreground` 的安全约束与异步等待。

    这些是**行为断言**：用假的 `ctypes.windll` / `win32gui` 记录真实调用序列与
    前台状态变化。只查源码字样不够——实测把挂载条件改成 `if False:`（等于完全
    不挂线程）后，源码里 `AttachThreadInput` 仍然存在，字样检查照样通过，缺陷逃逸。
    """

    def _run_with_fake_win32(
        self, hwnd=1234, fg_hwnd=999, my_thread=1, fg_thread=2, target_thread=3, switch_on=None, switch_delay_ms=0
    ):
        """在假的 win32 环境里跑一次 `_force_foreground`。

        Args:
            hwnd: 目标窗口句柄。
            fg_hwnd: 初始前台窗口句柄。
            my_thread: 本线程 ID。
            fg_thread: 前台窗口所属线程 ID。
            target_thread: 目标窗口所属线程 ID。
            switch_on: 哪个调用会真正完成切换——`"naked"` / `"attach"` / None
                （None 表示永远不切成功，用于验证失败路径）。
            switch_delay_ms: 切换生效的延迟（毫秒），模拟 `SetForegroundWindow`
                的异步性。0 表示调用返回前就已生效。

        Returns:
            (结果, 调用记录列表)。调用记录里的元素形如 `("attach", 线程, True)`。
        """
        import time

        calls = []
        state = {"foreground": fg_hwnd, "pending_at": None}
        phase = {"name": "naked"}

        def apply_switch():
            """把待生效的切换落地（若已到时间）。"""
            deadline = state["pending_at"]
            if deadline is not None and time.monotonic() >= deadline:
                state["foreground"] = hwnd
                state["pending_at"] = None

        def maybe_schedule(name):
            """按配置安排一次切换。

            Args:
                name: 当前阶段名。
            """
            if switch_on != name:
                return
            state["pending_at"] = time.monotonic() + switch_delay_ms / 1000.0

        def fake_set_foreground(window):
            """记录切换调用并按配置安排生效。"""
            calls.append(("setfg", window))
            maybe_schedule(phase["name"])

        def fake_show(window, cmd):
            """记录 ShowWindow。"""
            calls.append(("show", window, cmd))

        def fake_get_foreground():
            """读当前前台（先落地到期的切换）。"""
            apply_switch()
            return state["foreground"]

        made = type(
            "Made",
            (object,),
            {
                "windll": type(
                    "Windll",
                    (object,),
                    {
                        "user32": type(
                            "User32",
                            (object,),
                            {
                                "GetForegroundWindow": staticmethod(fake_get_foreground),
                                "GetWindowThreadProcessId": staticmethod(
                                    lambda window, _proc: fg_thread if window == fg_hwnd else target_thread
                                ),
                                "AttachThreadInput": staticmethod(
                                    lambda mine, theirs, attach: (calls.append(("attach", theirs, bool(attach))), True)[
                                        1
                                    ]
                                ),
                                "SetForegroundWindow": staticmethod(fake_set_foreground),
                                "SetActiveWindow": staticmethod(lambda window: calls.append(("setactive", window))),
                                "SetFocus": staticmethod(lambda window: calls.append(("setfocus", window))),
                            },
                        )(),
                        "kernel32": type(
                            "Kernel32",
                            (object,),
                            {
                                "GetCurrentThreadId": staticmethod(lambda: my_thread),
                            },
                        )(),
                    },
                )(),
            },
        )()
        fake_gui = type(
            "FakeGui",
            (object,),
            {
                "IsWindow": staticmethod(lambda window: True),
                "IsWindowVisible": staticmethod(lambda window: True),
                "GetForegroundWindow": staticmethod(fake_get_foreground),
                "ShowWindow": staticmethod(fake_show),
                "SetForegroundWindow": staticmethod(fake_set_foreground),
                "BringWindowToTop": staticmethod(lambda window: calls.append(("bring", window))),
            },
        )()
        fake_con = type("FakeCon", (object,), {"SW_RESTORE": 9, "SW_MINIMIZE": 6})()

        original_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "ctypes":
                return made
            if name == "win32gui":
                return fake_gui
            if name == "win32con":
                return fake_con
            return original_import(name, *args, **kwargs)

        bridge = _bridge(_Task(_HwndWindow(hwnd)))
        import builtins

        builtins.__import__ = fake_import
        try:
            # 第二阶段（挂线程）靠切换阶段名来区分：裸调没成功就会进入它。
            original_attach = bridge._attach_and_activate

            def wrapped_attach(target):
                """进入挂线程阶段前切换阶段名。"""
                phase["name"] = "attach"
                original_attach(target)

            bridge._attach_and_activate = wrapped_attach
            result = bridge._force_foreground(hwnd)
        finally:
            builtins.__import__ = original_import
        return result, calls

    def test_does_not_attach_when_naked_call_succeeds(self):
        """裸调能切过去时，不应再去挂线程。

        `AttachThreadInput` 会合并输入队列，本身有风险与开销；实测同机同窗口下
        裸调 5/5 成功，它并非必需。把它放第一位会让每一次点击都承担这份成本，
        而收益只出现在少数被前台锁拒绝的场景。
        """
        _result, calls = self._run_with_fake_win32(switch_on="naked", switch_delay_ms=0)
        attached = [call for call in calls if call[0] == "attach" and call[2]]
        self.assertEqual(attached, [], f"裸调已成功，不该挂线程：{attached}")

    def test_falls_back_to_attach_when_naked_call_fails(self):
        """裸调切不过去时必须用挂线程兜底。

        这是前台锁（foreground lock）拒绝裸调时的唯一出路。
        """
        result, calls = self._run_with_fake_win32(switch_on="attach", switch_delay_ms=0)
        attached = [call for call in calls if call[0] == "attach" and call[2]]
        self.assertTrue(attached, "兜底路径必须真的挂上线程")
        self.assertTrue(result, "兜底成功后应返回 True")

    def test_attaches_the_thread_that_owns_foreground(self):
        """兜底时只挂「持有前台的线程」。

        这是绕过前台锁的机制：挂错对象等于没挂。
        """
        _result, calls = self._run_with_fake_win32(fg_thread=2, target_thread=3, switch_on="attach", switch_delay_ms=0)
        attached = [call for call in calls if call[0] == "attach" and call[2]]
        self.assertIn(
            ("attach", 2, True),
            attached,
            f"必须挂上前台窗口所属线程 2，实际挂载序列：{attached}",
        )

    def test_does_not_attach_the_target_window_thread(self):
        """不得挂目标窗口自己的线程。

        实测四个变体（同一台机器、同一窗口、每轮前把前台交给无关窗口）：
        只挂前台线程成功；**挂前台线程 + 目标线程失败**；挂前台线程 + 激活
        调用成功；挂前台线程且解挂后复查仍成功。

        失败机理：`AttachThreadInput` 会合并输入队列，把目标线程也挂上来等于让它
        参与「谁持有前台」的判断，`SetForegroundWindow` 的调用方身份不再唯一，
        切换被拒。这条很容易写反——直觉上「把目标线程也挂上」看起来更保险。
        """
        _result, calls = self._run_with_fake_win32(fg_thread=2, target_thread=3, switch_on="attach", switch_delay_ms=0)
        attached = {call[1] for call in calls if call[0] == "attach" and call[2]}
        self.assertNotIn(
            3,
            attached,
            f"不得挂目标窗口线程 3，实际挂载：{attached}",
        )

    def test_detaches_every_attached_thread(self):
        """挂上的线程必须全部解挂。

        残留的队列关联会让本线程与目标线程的输入互相影响——本该发往游戏窗口的
        键鼠消息可能被投到自己的界面线程上。
        """
        _result, calls = self._run_with_fake_win32(fg_thread=2, target_thread=3, switch_on="attach", switch_delay_ms=0)
        attached = {call[1] for call in calls if call[0] == "attach" and call[2]}
        detached = {call[1] for call in calls if call[0] == "attach" and not call[2]}
        self.assertEqual(attached, detached, "挂载与解挂的线程集合必须一致")
        self.assertTrue(attached, "必须至少挂载一个线程")

    def test_never_minimizes(self):
        """不得调用 `ShowWindow(SW_MINIMIZE)`。

        实测：`SW_MINIMIZE` 会把 `resizable(False, False)` 的窗口永久压成
        136x40（客户区 120x1），此后所有点击都落到别的窗口上。这个故障表现为
        「置前成功但点击无效」，与真正的前台锁问题完全混淆，排查代价极高。
        """
        _result, calls = self._run_with_fake_win32(switch_on="naked", switch_delay_ms=0)
        shown = [call for call in calls if call[0] == "show"]
        self.assertTrue(shown, "应当调用了 ShowWindow")
        minimised = [call for call in shown if call[2] == 6]
        self.assertEqual(minimised, [], f"不得最小化窗口，实际调用：{shown}")

    def test_calls_set_foreground(self):
        """必须调用 `SetForegroundWindow`（挂线程只是拿到权限，切换仍要显式调）。"""
        _result, calls = self._run_with_fake_win32(switch_on="naked", switch_delay_ms=0)
        self.assertTrue([call for call in calls if call[0] == "setfg"])

    def test_waits_for_async_switch_to_take_effect(self):
        """切换是异步的：裸调阶段必须等它生效，不能立刻判失败。

        实测 `SetForegroundWindow` 调用返回后立即读 `GetForegroundWindow()`
        **必然得到旧值**，真正生效耗时 6—12ms（5 次采样全部如此）。若不等就判失败，
        表现是「点击明明生效了，界面却弹出『无法切到前台』」。

        Note：
            断言「裸调阶段就等到成功」，而不是只看最终返回值。只看返回值会被
            兜底路径掩盖——实测把裸调后的等待去掉、改成立即判断，兜底路径的等待
            仍能救回 True，用例照样通过。所以这里断言**没有进入兜底**
            （即调用记录里没有 `attach`）。
        """
        result, calls = self._run_with_fake_win32(switch_on="naked", switch_delay_ms=40)
        attached = [call for call in calls if call[0] == "attach"]
        self.assertEqual(
            attached,
            [],
            f"裸调延迟 40ms 生效时应在裸调阶段等到，不该落到兜底：{attached}",
        )
        self.assertTrue(result, "延迟生效的切换必须被正确判定为成功")

    def test_returns_false_when_never_switches(self):
        """始终切不过去时返回 False（供上层决定是否报错）。"""
        result, _calls = self._run_with_fake_win32(switch_on=None)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
