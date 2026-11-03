# -*- coding: utf-8 -*-
"""ok 日志噪音过滤。

上游 `ok.util.window.get_exe_by_hwnd` 对每个可见窗口调用 `process.cmdline()`，
遇到以其他账户（如 LocalSystem 服务）运行的进程会抛 `psutil.AccessDenied`。
该异常已被上游吞掉并返回空值，但日志仍按 ERROR 打印完整 traceback；而
`find_hwnd` 由 `HwndWindow` 的轮询线程每 0.2 秒调用一次，于是同一异常
每分钟刷数百条、日志文件每分钟增长数百 KB。

本模块在 logging 层做收敛：命中的噪音首次以 WARNING 记录一次（去掉
traceback），随后在冷却窗口内静默，冷却期满后再报告累计抑制条数。
只作用于 ok logger 实例，不改动任何框架符号。

刻意只收敛 `cmdline()` 这一条特征：`name()` 与 `exe()` 的同类异常在本机
不常触发（需要进程名可读但命令行不可读的权限差），保留原样输出以便及早发现
新的权限场景。若将来它们也开始刷屏，把特征追加进 `NOISE_PATTERNS` 即可。
"""

import logging
import threading
import time
from collections.abc import Callable, Iterable

# 需要收敛的日志消息特征。按「消息包含该子串」判定，避免与具体进程名绑定。
NOISE_PATTERNS = ("get_exe_by_hwnd process.cmdline() error",)

# 同一特征的再次报告间隔（秒）。
COOLDOWN_SECONDS = 300

# traceback 起始标记：首次报告时从这里截断，只保留异常摘要。
_TRACEBACK_MARKERS = ("Traceback (most recent call last):",)

# 安装用锁保护：apply() 在 import 期执行，但测试与热重载可能并发调用。
_install_lock = threading.Lock()


class NoiseFilter(logging.Filter):
    """按消息特征折叠重复噪音日志。

    logging 的 Filter 与 Handler 不在同一把锁内执行，而本过滤器被挂在全局
    logger 上、由多个线程并发写入，因此内部状态必须自己加锁。
    """

    def __init__(
        self,
        patterns: Iterable[str] = NOISE_PATTERNS,
        cooldown: float = COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化过滤器。

        Args：
            patterns：需要收敛的消息特征子串集合。
            cooldown：同一特征的再次报告间隔，单位秒。
            clock：单调时钟函数，测试时可注入以跳过真实等待。
        """
        super().__init__()
        self.patterns: tuple[str, ...] = tuple(patterns)
        self.cooldown = cooldown
        self.clock = clock
        self._lock = threading.Lock()
        self._last_emit: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        """判定并可能改写日志记录。

        未命中特征时原样放行；命中且仍在冷却窗口内时丢弃并累计计数。

        Args：
            record：logging 生成的日志记录。

        Returns：
            True 表示放行，False 表示丢弃。
        """
        matched = self._match(record)
        if matched is None:
            return True

        suppressed = self._admit(matched)
        if suppressed is None:
            return False

        record.levelno = logging.WARNING
        record.levelname = "WARNING"
        record.msg = self._render(record.getMessage(), matched, suppressed)
        record.args = ()
        # 上游把 traceback 拼在 message 里，exc_info 通常为 None；显式清空以免
        # 将来上游改用 exc_info 时又把栈打回来。
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True

    def _admit(self, matched: str) -> int | None:
        """判断该特征此刻是否可放行，并更新折叠状态。

        Args：
            matched：命中的特征子串。

        Returns：
            可放行时返回自上次报告以来折叠的条数（首次为 0），冷却期内返回 None。
        """
        now = self.clock()
        with self._lock:
            last = self._last_emit.get(matched)
            if last is not None and now - last < self.cooldown:
                self._suppressed[matched] = self._suppressed.get(matched, 0) + 1
                return None
            suppressed = self._suppressed.pop(matched, 0)
            self._last_emit[matched] = now
            return suppressed

    def _match(self, record: logging.LogRecord) -> str | None:
        """返回首个命中的特征，未命中时返回 None。

        Args：
            record：logging 生成的日志记录。

        Returns：
            命中的特征子串；级别低于 ERROR 或无匹配时返回 None。
        """
        if record.levelno < logging.ERROR:
            return None
        message = record.getMessage()
        for pattern in self.patterns:
            if pattern in message:
                return pattern
        return None

    def _render(self, message: str, pattern: str, suppressed: int) -> str:
        """生成折叠后的单行消息，去掉 traceback 主体但保留异常摘要。

        Args：
            message：原始日志正文。
            pattern：命中的特征子串。
            suppressed：自上次报告以来被折叠的条数。

        Returns：
            适合直接落盘的单行文本。traceback 只保留末行（异常类型与信息），
            中间栈帧全部丢弃；正文中的换行统一折叠为空格，避免打乱日志解析。
        """
        head, exception_line = self._split_traceback(message)
        exception_line = " ".join(exception_line.split())
        head = " ".join(head.split())
        detail = f"{head} {exception_line}".strip()
        if suppressed:
            note = f"冷却 {self.cooldown} 秒，期间已折叠 {suppressed} 条"
        else:
            note = f"下次同类日志将在 {self.cooldown} 秒后报告"
        return f"已折叠重复噪音日志，特征「{pattern}」：{detail} （{note}）"

    @staticmethod
    def _split_traceback(message: str) -> tuple[str, str]:
        """把消息拆成「摘要部分」与「异常末行」。

        Args：
            message：原始日志正文，可能内嵌完整 traceback。

        Returns：
            (摘要部分, 异常末行)。无 traceback 时异常末行为空串。
        """
        for marker in _TRACEBACK_MARKERS:
            index = message.find(marker)
            if index != -1:
                head = message[:index]
                tail_lines = [line.strip() for line in message[index:].splitlines() if line.strip()]
                # 末行形如「psutil.AccessDenied: (pid=...)」，是排查时最有价值的信息。
                return head, (tail_lines[-1] if tail_lines else "")
        return message, ""


def install() -> None:
    """把过滤器装到 ok logger 上，重复调用无副作用。

    幂等判据取「logger 上是否已有 NoiseFilter」，而不是模块级布尔标志：
    标志会与实际挂载状态脱钩（例如别处移除了过滤器后 install() 不再补装）。

    Raises：
        RuntimeError：ok 的日志入口消失，说明 ok-script 已重构日志实现。
    """
    with _install_lock:
        ok_logger = _get_ok_logger()
        if not any(isinstance(item, NoiseFilter) for item in ok_logger.filters):
            ok_logger.addFilter(NoiseFilter())


def _get_ok_logger() -> logging.Logger:
    """取 ok 包共用的 logging.Logger 实例。

    Returns：
        ok 包内 Logger 包装的底层 logging.Logger。

    Raises：
        RuntimeError：ok 的日志模块或实例不再存在。
    """
    try:
        from ok.util.logger import Logger
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "日志过滤器目标缺失：ok.util.logger.Logger 不存在，请检查 ok-script 升级后更新 extensions/capabilities/log_noise_filter/noise_filter.py"
        ) from exc

    ok_logger = Logger.get_logger(__name__).logger
    if not isinstance(ok_logger, logging.Logger):
        raise RuntimeError(
            "日志过滤器目标缺失：Logger.get_logger() 未返回 logging.Logger，请检查 ok-script 升级后更新 extensions/capabilities/log_noise_filter/noise_filter.py"
        )
    return ok_logger
