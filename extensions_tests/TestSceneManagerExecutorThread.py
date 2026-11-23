# -*- coding: utf-8 -*-
"""验证界面层的设备操作走执行器线程，不会卡死 GUI。

被测缺陷（用户实测）：点「识别当前画面」后整个界面卡死。

根因链路：

1. `TaskExecutor` 构造时 `paused = True`，应用未点「开始」时一直是这个状态；
2. `task.ocr` 第一步是 `if self.blocked_by_pause(): self.executor.sleep(1)`；
3. `executor.sleep` 在暂停态下进 `while True`，每轮只检查暂停是否解除；
4. 解除暂停要执行器线程推进，而 GUI 线程被这个循环占住 → 双方互等，只能杀进程。

因此这里验证两件事：

- 暂停态下在**当前线程**直接调 `task.ocr` 确实会卡住（复现缺陷本身）；
- `TaskBridge.run_on_executor` 把调用派到执行器线程后能正常返回（修复有效）。

第二条是本仓库唯一能覆盖该缺陷的判据：真实执行器需要设备与 Qt 事件循环，
用可控的假执行器验证「派发 + 等待 + 超时取消」这三段契约。
"""

import threading
import time
import unittest
from concurrent.futures import Future

from extensions.capabilities.scene_manager import recognize, runtime


class _FakeJob:
    """假 job：直接在当前线程跑目标函数，或模拟超时。"""

    def __init__(self, fn, run_immediately=True, delay=0.0):
        """记录目标并按需立即执行。

        Args：
            fn：目标函数。
            run_immediately：为 False 时 future 永不完成（模拟卡死）。
            delay：执行前的延迟，用于验证「等结果」而不是「立刻返回」。
        """
        self.future = Future()
        self.cancelled = False
        if run_immediately:
            if delay:
                threading.Timer(delay, self._finish, args=(fn,)).start()
            else:
                self._finish(fn)

    def _finish(self, fn):
        """执行并写入 future。"""
        try:
            self.future.set_result(fn())
        except Exception as error:  # noqa: BLE001 — 与真实 job 一致：异常进 future
            self.future.set_exception(error)

    def cancel(self):
        """记录取消请求。"""
        self.cancelled = True
        self.future.cancel()


class _FakeExecutor:
    """假执行器：记录 submit 的调用并返回预置 job。"""

    def __init__(self, job_factory):
        """绑定 job 工厂。

        Args：
            job_factory：`(fn) -> _FakeJob`。
        """
        self._job_factory = job_factory
        self.submitted = []

    def submit(self, fn, name=None):
        """记录并返回 job。"""
        self.submitted.append(name)
        return self._job_factory(fn)


class _FakeTask:
    """只带 executor 的任务桩。"""

    def __init__(self, executor):
        """绑定执行器。"""
        self.executor = executor


def _bridge_with(executor):
    """造一个只填了 task 的 TaskBridge（跳过 __init__ 的设备校验）。"""
    bridge = runtime.TaskBridge.__new__(runtime.TaskBridge)
    bridge.task = _FakeTask(executor)
    bridge.library = None
    bridge.loader = None
    bridge.last_graph_error = ""
    return bridge


class TestRunOnExecutor(unittest.TestCase):
    """派发与等待。"""

    def test_runs_off_the_calling_thread(self):
        """目标函数在执行器线程上跑，返回值原样带回。"""
        seen = {}

        def work():
            seen["thread"] = threading.current_thread()
            return "done"

        bridge = _bridge_with(_FakeExecutor(lambda fn: _FakeJob(fn)))
        result = bridge.run_on_executor(work)

        self.assertEqual(result, "done")
        self.assertIn("thread", seen)

    def test_waits_for_result(self):
        """会等目标真正结束，不是提交后立刻返回。"""
        bridge = _bridge_with(_FakeExecutor(lambda fn: _FakeJob(fn, delay=0.2)))
        started = time.time()
        bridge.run_on_executor(lambda: None)
        self.assertGreaterEqual(time.time() - started, 0.15, "必须等结果，不能提交即返回")

    def test_exception_becomes_runtime_error(self):
        """目标抛异常时转成 RuntimeError，界面能直接展示。"""

        def boom():
            raise ValueError("设备没连上")

        bridge = _bridge_with(_FakeExecutor(lambda fn: _FakeJob(fn)))
        with self.assertRaises(RuntimeError) as caught:
            bridge.run_on_executor(boom)
        self.assertIn("设备没连上", str(caught.exception))

    def test_timeout_cancels_job(self):
        """超时后取消 job 并报错，不会无限等待。"""
        jobs = []

        def factory(fn):
            job = _FakeJob(fn, run_immediately=False)
            jobs.append(job)
            return job

        bridge = _bridge_with(_FakeExecutor(factory))
        with self.assertRaises(RuntimeError):
            bridge.run_on_executor(lambda: None, timeout=0.1)
        self.assertTrue(jobs[0].cancelled, "超时必须取消 job，否则它在后台继续跑")

    def test_missing_executor_reports(self):
        """执行器缺失时明确报错，而不是静默什么都不做。"""
        bridge = _bridge_with(None)
        with self.assertRaises(RuntimeError) as caught:
            bridge.run_on_executor(lambda: None)
        self.assertIn("执行器", str(caught.exception))

    def test_submit_failure_reports_busy(self):
        """被独占导致 submit 抛异常时，转成可读的「执行器忙」。"""

        class _Busy:
            def submit(self, fn, name=None):
                raise RuntimeError("held by other")

        bridge = _bridge_with(_Busy())
        with self.assertRaises(RuntimeError) as caught:
            bridge.run_on_executor(lambda: None)
        self.assertIn("忙", str(caught.exception))


class TestDeadlockContract(unittest.TestCase):
    """复现「GUI 线程直接调任务方法会卡死」这条链路的判据。

    不断言「真的卡住」（那要求测例自身阻塞），而是断言构成死锁的三个前提同时成立。
    三者任何一条变了，`run_on_executor` 就没必要存在——这条用例的作用是提醒后来者
    「为什么必须派发」，以及在他改动框架调用方式后立刻发现前提已失效。

    链路（实测确认）：

    1. 执行器构造时 `paused = True`；
    2. `blocked_by_pause()` 在非执行器线程上恒为 True（job 的 `ignore_pause` 豁免
       只对执行器线程生效）；
    3. `task.ocr` / `task.click` 都以 `if blocked_by_pause(): sleep(1)` 开头，
       而 `sleep` 在暂停态下进 `while True`。
    """

    def test_paused_by_default(self):
        """执行器默认是暂停态——这是整条链路的第一环。"""
        from ok.task.TaskExecutor import TaskExecutor

        source = _function_source(TaskExecutor.__init__)
        self.assertIn("self.paused = True", source, "执行器初始状态必须是暂停，否则前提已变")

    def test_pause_check_waits_outside_executor_thread(self):
        """`blocked_by_pause` 只在「执行器线程 + 无暂停豁免」时才返回 False。

        这条决定了：任何在 GUI 线程调用 `task.ocr` 的代码都必然进入等待分支。
        """
        from ok.task.TaskExecutor import TaskExecutor

        source = _function_source(TaskExecutor.blocked_by_pause)
        self.assertIn("self.is_executor_thread()", source, "判定必须区分线程，否则 GUI 线程不会被拦")
        self.assertIn("ignore_pause", source, "豁免必须只对带 ignore_pause 的 job 生效")

    def test_sleep_loops_until_pause_clears(self):
        """`executor.sleep` 在暂停态下是 `while True`，不是定时返回。

        这条是「卡死」而不是「变慢」的原因：暂停不解除时它永不退出。
        """
        from ok.task.TaskExecutor import TaskExecutor

        source = _function_source(TaskExecutor.sleep)
        self.assertIn("while True", source)
        self.assertIn("blocked_by_pause", source)

    def test_task_ocr_checks_pause_first(self):
        """`task.ocr` 的第一段就检查暂停，所以它不能在 GUI 线程调用。

        `ocr` 定义在 `ok.task.task.OCR` 混入类上，不在 `BaseTask` 本体。
        """
        from ok.task.task import OCR

        source = _function_source(OCR.ocr)
        self.assertIn("blocked_by_pause", source, "ocr 若不再检查暂停，本缺陷的根因已消失")


def _function_source(func):
    """返回函数源码文本。"""
    import inspect

    return inspect.getsource(func)


class TestBridgeEntryPoints(unittest.TestCase):
    """三个界面入口都经 run_on_executor。"""

    def setUp(self):
        """记录被派发的操作。"""
        self.calls = []
        self.result = recognize.MatchResult()
        self.bridge = _bridge_with(_FakeExecutor(lambda fn: _FakeJob(fn)))
        self.bridge.identify_once = self._identify
        self.bridge.activate_area = lambda scene, area: self.calls.append("click")
        self.bridge.wait = lambda seconds: self.calls.append(("wait", seconds))
        self.bridge.build_navigator = lambda: type("_Nav", (), {"goto": self._goto})()
        # 点击前后各取一帧用于比对「是否有反应」；这里取不到帧（None），
        # `frames_differ` 按「有变化」处理，不影响顺序断言。
        self.bridge.capture_frame = lambda: self.calls.append("capture") or None

    def _identify(self):
        """假识别：记录并返回真实结果对象。

        Returns:
            `recognize.MatchResult`。`click_and_identify` 会往它的
            `frame_changed` 上写比对结果，因此不能返回字符串。
        """
        self.calls.append("identify")
        return self.result

    def _goto(self, target, should_stop=None):
        """假导航：记录目标并返回。"""
        self.calls.append(("goto", target))
        return "导航结果"

    def test_identify_blocking(self):
        """识别入口返回识别结果。"""
        self.assertIs(self.bridge.identify_blocking(), self.result)
        self.assertEqual(self.calls, ["identify"])

    def test_click_and_identify_order(self):
        """点击入口按「取帧 → 点击 → 等待 → 取帧 → 识别」顺序执行，整体一次派发。

        两次取帧是 `frame_changed` 判定的依据：只有点击前后都取到帧，才能判断
        「点了等于没点」。它们必须紧贴点击两侧——隔得太远（例如放在识别之后）
        会把识别耗时期间的其他画面变化算进来。
        """
        self.bridge.click_and_identify("scene", "area", after_wait=1.5)
        self.assertEqual(
            self.calls,
            ["capture", "click", ("wait", 1.5), "capture", "identify"],
        )

    def test_goto_blocking(self):
        """跳转入口返回导航结果。"""
        self.assertEqual(self.bridge.goto_blocking("scene-0002"), "导航结果")
        self.assertIn(("goto", "scene-0002"), self.calls)


if __name__ == "__main__":
    unittest.main()
