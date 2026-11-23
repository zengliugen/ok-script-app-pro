"""测试靶场程序入口。

用法（在项目根目录）：``uv run python extensions_testbed/main.py [--log PATH] [--size WxH] [--lang zh] [--screen ID]``

**必须用 venv 的 `pythonw.exe` 启动**（无控制台窗口），否则启动时会弹出黑框——
启动器 `run_testbed.py` 即指向它。

`--screen` 供开发工具使用：靶场默认停在 `entry`，而 `make_templates.py` 需要
「处于目标画面」的截图才能裁出该画面的模板图。正常启动时不传该参数。
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

# 允许「直接以脚本方式运行」——此时包根目录（tests/）不在 sys.path 上，
# 使 `from extensions_testbed import ...` 失败。以文件位置反推包根并注入，避免要求调用方
# 先设置 PYTHONPATH 或改用 `-m`。
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from extensions_testbed import app as app_module  # noqa: E402  （须在 sys.path 注入之后导入）
from extensions_testbed import layout, screens  # noqa: E402
from extensions_testbed.eventlog import EventLog  # noqa: E402

# 默认日志路径：源码运行落在 `extensions_testbed/_runtime/events.jsonl`；
# 打包成单文件 EXE 后落在 EXE 同目录的 `_runtime/`——onefile 解压出的临时
# 目录会随进程删除，日志写进去等于丢失。
if getattr(sys, "frozen", False):
    _LOG_ROOT = Path(sys.executable).resolve().parent
else:
    _LOG_ROOT = Path(__file__).resolve().parent
DEFAULT_LOG = _LOG_ROOT / "_runtime" / "events.jsonl"

# 单实例互斥量名称。第二实例检测到已存在即拒绝启动——避免手动调试残留的实例
# 被适配层绑走（见 extensions_testbed/README.md §4.3 的失败模式表）。
MUTEX_NAME = "Global\\OkScriptAppProTestbed"
ERROR_ALREADY_EXISTS = 183


def _parse_size(raw: str) -> tuple[int, int]:
    """解析 `--size` 参数。

    Args:
        raw: 形如 `1280x720` 的尺寸字符串。

    Returns:
        宽度与高度。

    Raises:
        argparse.ArgumentTypeError: 格式非法时抛出。
    """
    parts = raw.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"尺寸格式应为 WxH，收到：{raw}")
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"尺寸必须是整数，收到：{raw}") from exc
    if width < 320 or height < 240:
        raise argparse.ArgumentTypeError(f"尺寸过小（至少 320x240），收到：{raw}")
    return width, height


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。

    Returns:
        解析器实例。
    """
    parser = argparse.ArgumentParser(
        prog="extensions_testbed",
        description="ok-script-app-pro 测试靶场：支持后台点击的可自证测试目标程序",
    )
    parser.add_argument(
        "--log",
        default=str(DEFAULT_LOG),
        help=f"事件日志路径（JSONL，每行 flush），默认 {DEFAULT_LOG}",
    )
    parser.add_argument(
        "--size",
        type=_parse_size,
        default=(layout.BASE_W, layout.BASE_H),
        help=f"客户区尺寸，形如 1280x720，默认 {layout.BASE_W}x{layout.BASE_H}",
    )
    parser.add_argument(
        "--lang",
        choices=("zh", "en"),
        default="zh",
        help="界面语言，默认 zh",
    )
    parser.add_argument(
        "--screen",
        choices=tuple(screens.SCREENS),
        default=None,
        help=("启动时进入的画面 id，默认为 entry；开发工具（make_templates.py）裁各画面模板图时使用"),
    )
    return parser


def _acquire_mutex() -> bool:
    """尝试获取单实例互斥量。

    Returns:
        True 表示本次是首个实例；False 表示已有实例在运行。

    Note:
        互斥量句柄**故意不保存也不释放**：进程退出时由系统回收。保存在模块级变量
        会被 GC 提前关闭，导致互斥量失效（第二实例能启动）。
    """
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, True, MUTEX_NAME)
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def _set_timer_resolution(enable: bool) -> None:
    """提高（或恢复）系统定时器分辨率。

    Args:
        enable: True 提到 1ms，False 恢复默认。

    Note:
        **必需的实测发现**：Tk 的 `after(16)` 受 Windows 默认定时器分辨率
        （约 15.6ms）限制，实测每次触发实际耗时约 21.8ms → 仅 45.8FPS；
        调用 `timeBeginPeriod(1)` 后为 16.2ms → 61.8FPS。
        帧率是心跳日志要报告、且被用于标定噪声下限的事实，不能受系统默认精度影响。
        进程退出前必须 `timeEndPeriod` 恢复——否则会持续抬高全系统定时器分辨率，
        增加其他进程的唤醒功耗。
    """
    try:
        if enable:
            ctypes.windll.winmm.timeBeginPeriod(1)
        else:
            ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        # 非 Windows 或 winmm 不可用时静默降级：帧率会偏低，但程序仍可运行
        pass


def main(argv: list[str] | None = None) -> int:
    """程序主入口。

    Args:
        argv: 命令行参数（不含程序名）；None 表示取 `sys.argv[1:]`。

    Returns:
        进程退出码：0 正常，2 已有实例在运行。
    """
    args = _build_parser().parse_args(argv)

    if not _acquire_mutex():
        # 无控制台时打印也看不到，但仍写一份到 stderr：便于前台调试时诊断
        print("测试靶场已在运行，拒绝启动第二个实例", file=sys.stderr)
        return 2

    width, height = args.size
    _set_timer_resolution(True)
    log = EventLog(args.log)
    try:
        application = app_module.TestbedApp(
            log=log,
            width=width,
            height=height,
            lang=args.lang,
            screen_id=args.screen,
        )
    except Exception:
        # 构造失败时日志已打开，必须关闭，否则文件句柄泄漏且「日志无内容」难以解释
        log.close()
        _set_timer_resolution(False)
        raise
    try:
        return application.run()
    finally:
        # 必须恢复系统定时器分辨率，避免长期抬高全系统唤醒频率
        _set_timer_resolution(False)


if __name__ == "__main__":
    sys.exit(main())
