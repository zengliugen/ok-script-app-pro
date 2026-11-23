"""测试靶场的计数与快照。

提供**两组独立计数**（对应 `M0-诊断场景需求.md` §2 的「三方区分」）：

- `MessageCounters`：WndProc 挂钩统计的**原始 Win32 消息**（能力 A）
- `AppCounters`：Tk 事件绑定回调统计的**应用级感知**（能力 B）

两组同时可读，用于区分「消息到达窗口但应用没处理」与「消息根本没到达」——
这是关闭「无响应点是否存在替代解释」这一缺口的关键。
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass


@dataclass
class MessageCounters:
    """原始窗口消息的累计计数（能力 A）。

    Attributes:
        lbutton_down: `WM_LBUTTONDOWN` 次数。
        lbutton_up: `WM_LBUTTONUP` 次数。
        rbutton_down: `WM_RBUTTONDOWN` 次数。
        rbutton_up: `WM_RBUTTONUP` 次数。
        mousemove: `WM_MOUSEMOVE` 次数。
        wheel: `WM_MOUSEWHEEL` 次数。
        wheel_net: 滚轮累计净增量（以 120 为一格的有符号累加）。
        key_down: `WM_KEYDOWN` 次数。
        key_up: `WM_KEYUP` 次数。
        char: `WM_CHAR` 次数。
        activate: `WM_ACTIVATE` 次数。
        setfocus: `WM_SETFOCUS` 次数。
        killfocus: `WM_KILLFOCUS` 次数。
        activateapp: `WM_ACTIVATEAPP` 次数。
        last_click_xy: 最近一次点击的**客户区坐标** `[x, y]`。
        last_key: 最近的虚拟键码。
        last_char: 最近的字符（已转成单字符串）。
    """

    lbutton_down: int = 0
    lbutton_up: int = 0
    rbutton_down: int = 0
    rbutton_up: int = 0
    mousemove: int = 0
    wheel: int = 0
    wheel_net: int = 0
    key_down: int = 0
    key_up: int = 0
    char: int = 0
    activate: int = 0
    setfocus: int = 0
    killfocus: int = 0
    activateapp: int = 0
    last_click_xy: list[int] | None = None
    last_key: int | None = None
    last_char: str | None = None

    def snapshot(self) -> dict[str, object]:
        """返回当前计数的快照（供事件日志使用）。

        Returns:
            字段名到当前值的字典。
        """
        return asdict(self)


# 需要展示在界面上的消息计数项：字段名 → 界面标签
_MESSAGE_LABELS: tuple[tuple[str, str], ...] = (
    ("lbutton_down", "LBUTTONDOWN"),
    ("lbutton_up", "LBUTTONUP"),
    ("rbutton_down", "RBUTTONDOWN"),
    ("mousemove", "MOUSEMOVE"),
    ("wheel", "WHEEL"),
    ("wheel_net", "WHEEL净量"),
    ("key_down", "KEYDOWN"),
    ("char", "CHAR"),
    ("activate", "ACTIVATE"),
    ("setfocus", "SETFOCUS"),
    ("activateapp", "ACTIV"),
)


@dataclass
class AppCounters:
    """应用级感知的累计计数（能力 B）。

    Attributes:
        mouse_down: Tk 感知到的鼠标按下次数。
        mouse_up: Tk 感知到的鼠标抬起次数。
        double: Tk 感知到的双击次数。
        key_down: Tk 感知到的按键次数。
        char: Tk 感知到的可打印字符次数。
        motion: Tk 感知到的鼠标移动次数。
        wheel: Tk 感知到的滚轮事件次数。
        drag: Tk 感知到的拖拽完成次数。
        last_mouse_xy: 最近一次事件报告的鼠标**客户区坐标** `[x, y]`。
        last_key: 最近一次按键的 keysym 文本。
        text: 累积的可打印文本。
    """

    mouse_down: int = 0
    mouse_up: int = 0
    double: int = 0
    key_down: int = 0
    char: int = 0
    motion: int = 0
    wheel: int = 0
    drag: int = 0
    last_mouse_xy: list[int] | None = None
    last_key: str | None = None
    text: str = ""

    def snapshot(self) -> dict[str, object]:
        """返回当前计数的快照（供事件日志使用）。

        Returns:
            字段名到当前值的字典（含 `text`）。
        """
        return asdict(self)


# 需要展示在界面上的应用计数项：字段名 → 界面标签
_APP_LABELS: tuple[tuple[str, str], ...] = (
    ("mouse_down", "鼠标按下"),
    ("mouse_up", "鼠标抬起"),
    ("double", "双击"),
    ("key_down", "按键"),
    ("char", "字符"),
    ("motion", "移动"),
    ("wheel", "滚轮"),
    ("drag", "拖拽"),
)


class CounterStore:
    """两组计数的统一持有者，并负责线程安全地递增。

    Attributes:
        message: 原始消息计数（能力 A）。
        app: 应用级计数（能力 B）。
    """

    def __init__(self) -> None:
        """初始化两组计数。"""
        self.message = MessageCounters()
        self.app = AppCounters()
        # WndProc 回调与 Tk 主线程会并发改写计数，故统一加锁
        self._lock = threading.Lock()

    def touch_message(self, field_name: str, delta: int = 1) -> None:
        """递增一个消息计数字段。

        Args:
            field_name: 字段名（`MessageCounters` 的成员）。
            delta: 增量，默认 1。

        Raises:
            AttributeError: 字段名不存在时抛出（属代码错误，不静默忽略）。
        """
        with self._lock:
            current = getattr(self.message, field_name)
            setattr(self.message, field_name, current + delta)

    def touch_app(self, field_name: str, delta: int = 1) -> None:
        """递增一个应用计数字段。

        Args:
            field_name: 字段名（`AppCounters` 的成员）。
            delta: 增量，默认 1。

        Raises:
            AttributeError: 字段名不存在时抛出（属代码错误，不静默忽略）。
        """
        with self._lock:
            current = getattr(self.app, field_name)
            setattr(self.app, field_name, current + delta)

    def set_message(self, field_name: str, value: object) -> None:
        """设置一个消息计数字段（用于记录「最近坐标 / 最近键码」这类非累计值）。

        Args:
            field_name: 字段名。
            value: 目标值。
        """
        with self._lock:
            setattr(self.message, field_name, value)

    def set_app(self, field_name: str, value: object) -> None:
        """设置一个应用计数字段（用于记录「最近坐标 / 累积文本」这类非累计值）。

        Args:
            field_name: 字段名。
            value: 目标值。
        """
        with self._lock:
            setattr(self.app, field_name, value)

    def append_text(self, text: str) -> None:
        """向累积文本追加内容。

        Args:
            text: 待追加的字符串（单个可打印字符）。
        """
        with self._lock:
            self.app.text += text

    def message_snapshot(self) -> dict[str, object]:
        """返回消息计数的线程安全快照。

        Returns:
            字段名到当前值的字典。
        """
        with self._lock:
            return self.message.snapshot()

    def app_snapshot(self) -> dict[str, object]:
        """返回应用计数的线程安全快照。

        Returns:
            字段名到当前值的字典。
        """
        with self._lock:
            return self.app.snapshot()

    def reset(self) -> None:
        """清空两组计数（`F5` 快捷键触发）。"""
        with self._lock:
            self.message = MessageCounters()
            self.app = AppCounters()

    def message_lines(self) -> list[str]:
        """生成消息计数区的界面文本行。

        Returns:
            文本行列表；计数项按**每行 6 项**打包（共 2 行），末行附最近一次的
            点击坐标与键码——共 3 行，与区域容量一致（见 `layout._MIN_CONTENT_H`）。

        Note:
            打包而非「一项一行」：区域高度有限，逐项占一行会被裁掉，
            使部分计数在界面上不可见。
        """
        with self._lock:
            parts = [f"{label} {getattr(self.message, name)}" for name, label in _MESSAGE_LABELS]
            lines = _pack(parts, 6)
            last = self.message.last_click_xy
            last_text = f"[{last[0]},{last[1]}]" if last else "—"
            key = self.message.last_key
            lines.append(f"最近点击 {last_text}  最近键码 {key if key is not None else '—'}")
            return lines

    def app_lines(self) -> list[str]:
        """生成应用计数区的界面文本行。

        Returns:
            文本行列表；计数项按**每行 6 项**打包（共 2 行），末行附最近一次的
            鼠标坐标与按键名——共 3 行，与区域容量一致。
        """
        with self._lock:
            parts = [f"{label} {getattr(self.app, name)}" for name, label in _APP_LABELS]
            lines = _pack(parts, 6)
            last = self.app.last_mouse_xy
            last_text = f"[{last[0]},{last[1]}]" if last else "—"
            lines.append(f"最近鼠标 {last_text}  最近按键 {self.app.last_key or '—'}")
            return lines


def _pack(parts: list[str], per_line: int) -> list[str]:
    """把若干片段按每行固定数量拼成文本行。

    Args:
        parts: 待拼接的片段列表。
        per_line: 每行容纳的片段数。

    Returns:
        拼接后的文本行列表。
    """
    return ["  ".join(parts[index : index + per_line]) for index in range(0, len(parts), per_line)]
