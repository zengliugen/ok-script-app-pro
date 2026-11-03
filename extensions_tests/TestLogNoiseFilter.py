# -*- coding: utf-8 -*-
"""日志噪音过滤器测试。

被测对象是 `extensions/capabilities/log_noise_filter/noise_filter.py`。折叠逻辑的用例不依赖 ok / PySide6：
过滤器本身只用标准库 logging，因此用自行构造的 LogRecord 验证，避免把「环境没装」
误判成「代码坏了」。只有安装用例需要 ok，缺依赖时整条用例跳过。
"""

import logging
import unittest

from extensions.capabilities.log_noise_filter.noise_filter import COOLDOWN_SECONDS, NOISE_PATTERNS, NoiseFilter

NOISE_TEXT = "capture:get_exe_by_hwnd process.cmdline() error Traceback (most recent call last):\n  File ...\n"


class _FakeClock:
    """可控单调时钟，避免测试依赖真实时间流逝。"""

    def __init__(self) -> None:
        """从零开始计时。"""
        self.now = 0.0

    def __call__(self) -> float:
        """返回当前秒数。"""
        return self.now


def _make_record(message: str, level: int = logging.ERROR) -> logging.LogRecord:
    """构造一条日志记录。

    Args：
        message：日志正文。
        level：日志级别，默认 ERROR。

    Returns：
        logging.LogRecord 实例。
    """
    return logging.LogRecord(name="ok", level=level, pathname=__file__, lineno=1, msg=message, args=None, exc_info=None)


class TestNoiseFilter(unittest.TestCase):
    """验证噪音日志的折叠语义。"""

    def setUp(self) -> None:
        """每个用例使用独立的时钟与过滤器。"""
        self.clock = _FakeClock()
        self.filter = NoiseFilter(clock=self.clock)

    def test_noise_record_is_downgraded_on_first_hit(self) -> None:
        """首次命中的噪音降级为 WARNING，不再是 ERROR。"""
        record = _make_record(NOISE_TEXT)
        self.assertTrue(self.filter.filter(record))
        self.assertEqual(logging.WARNING, record.levelno)
        self.assertEqual("WARNING", record.levelname)

    def test_noise_traceback_removed_from_message(self) -> None:
        """首次命中时消息中不含完整 traceback，只留摘要与折叠说明。"""
        record = _make_record(NOISE_TEXT)
        self.filter.filter(record)
        message = record.getMessage()
        self.assertNotIn("Traceback (most recent call last)", message, "traceback 主体必须被截掉")
        self.assertIn(NOISE_PATTERNS[0], message, "折叠后的消息要保留特征，便于定位来源")
        self.assertIn("已折叠", message)

    def test_noise_keeps_exception_last_line(self) -> None:
        """折叠后仍保留异常末行，排查时能直接看出异常类型与信息。"""
        text = (
            "capture:get_exe_by_hwnd process.cmdline() error Traceback (most recent call last):\n"
            '  File "x.py", line 1, in cmdline\n'
            "psutil.AccessDenied: (pid=7780, name='GameViewerServer.exe')\n"
        )
        record = _make_record(text)
        self.filter.filter(record)
        message = record.getMessage()
        self.assertIn("psutil.AccessDenied", message, "异常末行必须保留")
        self.assertNotIn('File "x.py"', message, "中间栈帧应被丢弃")
        self.assertEqual(1, len(message.splitlines()), "折叠后必须是单行，避免多行日志打断解析")

    def test_noise_record_has_no_exc_or_stack_info(self) -> None:
        """改写后的记录不残留 exc_info 与 stack_info，否则 handler 仍会打完整栈。"""
        record = _make_record(NOISE_TEXT)
        record.exc_info = (ValueError, ValueError("boom"), None)
        record.stack_info = "Stack (most recent call last):"
        self.filter.filter(record)
        self.assertIsNone(record.exc_info, "exc_info 必须清空")
        self.assertIsNone(record.stack_info, "stack_info 必须清空")

    def test_repeated_noise_suppressed_within_cooldown(self) -> None:
        """冷却窗口内同特征的第二条噪音被丢弃。"""
        self.filter.filter(_make_record(NOISE_TEXT))
        self.clock.now = COOLDOWN_SECONDS / 2
        self.assertFalse(self.filter.filter(_make_record(NOISE_TEXT)))

    def test_noise_reported_again_after_cooldown_with_count(self) -> None:
        """冷却期结束后再次报告，并带上期间折叠条数。"""
        self.filter.filter(_make_record(NOISE_TEXT))
        self.clock.now = 1
        self.filter.filter(_make_record(NOISE_TEXT))
        self.clock.now = 2
        self.filter.filter(_make_record(NOISE_TEXT))

        self.clock.now = COOLDOWN_SECONDS + 1
        record = _make_record(NOISE_TEXT)
        self.assertTrue(self.filter.filter(record))
        self.assertIn("期间已折叠 2 条", record.getMessage())

    def test_unrelated_error_passes_through_untouched(self) -> None:
        """非噪音的 ERROR 原样放行，级别与消息不变。"""
        record = _make_record("capture:get_exe_by_hwnd process.exe() error boom")
        self.assertTrue(self.filter.filter(record))
        self.assertEqual(logging.ERROR, record.levelno)
        self.assertIn("boom", record.getMessage())

    def test_info_level_is_never_filtered(self) -> None:
        """INFO 级别不参与折叠，避免把普通日志当噪音。"""
        record = _make_record(NOISE_TEXT, level=logging.INFO)
        self.assertTrue(self.filter.filter(record))
        self.assertEqual(logging.INFO, record.levelno)

    def test_suppressed_count_conserved(self) -> None:
        """放行次数与折叠计数之和等于总调用次数，不丢也不重复计。"""
        total = 6
        for _ in range(total):
            self.clock.now += 1
            self.filter.filter(_make_record(NOISE_TEXT))
        # 首条放行、其余 5 条进入折叠计数；冷却窗口内不再放行。
        self.assertEqual(total - 1, self.filter._suppressed[NOISE_PATTERNS[0]])


class TestInstall(unittest.TestCase):
    """验证安装到 ok logger 的行为。"""

    def test_install_is_idempotent(self) -> None:
        """重复安装后过滤器数量精确为 1，且已挂载时不再重复 addFilter。"""
        try:
            from extensions.capabilities.log_noise_filter import noise_filter as log_noise_filter

            ok_logger = log_noise_filter._get_ok_logger()
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"缺少运行依赖，跳过安装用例：{exc}")

        original = list(ok_logger.filters)
        try:
            for item in original:
                ok_logger.removeFilter(item)
            log_noise_filter.install()
            log_noise_filter.install()
            installed = [item for item in ok_logger.filters if isinstance(item, NoiseFilter)]
            self.assertEqual(1, len(installed), "重复 install() 后应恰有一个过滤器")
        finally:
            for item in list(ok_logger.filters):
                ok_logger.removeFilter(item)
            for item in original:
                ok_logger.addFilter(item)

    def test_install_skips_when_filter_already_present(self) -> None:
        """已有过滤器时，install() 不重复挂载（覆盖 addFilter 去重分支）。"""
        try:
            from extensions.capabilities.log_noise_filter import noise_filter as log_noise_filter

            ok_logger = log_noise_filter._get_ok_logger()
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"缺少运行依赖，跳过安装用例：{exc}")

        original = list(ok_logger.filters)
        try:
            for item in original:
                ok_logger.removeFilter(item)
            ok_logger.addFilter(NoiseFilter())
            log_noise_filter.install()
            installed = [item for item in ok_logger.filters if isinstance(item, NoiseFilter)]
            self.assertEqual(1, len(installed), "已挂载时不得再追加一个过滤器")
        finally:
            for item in list(ok_logger.filters):
                ok_logger.removeFilter(item)
            for item in original:
                ok_logger.addFilter(item)


if __name__ == "__main__":
    unittest.main()
