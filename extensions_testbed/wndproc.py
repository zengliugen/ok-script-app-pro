"""WndProc 挂钩（能力 A）：统计原始 Win32 消息与**客户区坐标**。

用 `comctl32!SetWindowSubclass` 而非 `SetWindowLongPtr(GWL_WNDPROC)`——
Tk 自身可能已对窗口做过子类化，手工换 WndProc 容易与其冲突或漏掉调用链；
子类化由系统维护链，卸载也更干净（见 `extensions_testbed/README.md` §5.2）。

**记录客户区坐标的意义**：核对「注入点」与「目标实际收到的点」是否一致——
这是验证坐标打包正确性的唯一客观手段（`M0-诊断场景需求.md` §3A）。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any, Callable

# 需要挂钩的消息：虚拟键码 → 名称（名称与 M0 受控窗口基准一致）
MSG_NAMES: dict[int, str] = {
    0x0100: "WM_KEYDOWN",
    0x0101: "WM_KEYUP",
    0x0102: "WM_CHAR",
    0x0200: "WM_MOUSEMOVE",
    0x0201: "WM_LBUTTONDOWN",
    0x0202: "WM_LBUTTONUP",
    0x0204: "WM_RBUTTONDOWN",
    0x0205: "WM_RBUTTONUP",
    0x020A: "WM_MOUSEWHEEL",
    0x0006: "WM_ACTIVATE",
    0x0007: "WM_SETFOCUS",
    0x0008: "WM_KILLFOCUS",
    0x001C: "WM_ACTIVATEAPP",
}

# 需要记录**坐标与事件详情**的消息（`WM_MOUSEMOVE` 只需计数，不必逐条记录）
_DETAIL_MSGS: frozenset[int] = frozenset(
    {0x0201, 0x0202, 0x0204, 0x0205, 0x020A, 0x0100, 0x0101, 0x0102, 0x0006, 0x0007, 0x0008, 0x001C}
)

# lParam 为**客户区坐标**的消息：只有这些才能据 lParam 解出客户区坐标。
# `WM_MOUSEWHEEL` 刻意不在内——它的 lParam 是**屏幕**坐标；
# 而 `WM_ACTIVATEAPP` 等非鼠标消息的 lParam 是别的语义（如线程 id），
# 一律按坐标解析会得到「看起来像坐标、实际毫无意义」的值——
# 那会被后人误读为「坐标打包有误」，故必须显式区分。
_CLIENT_XY_MSGS: frozenset[int] = frozenset({0x0200, 0x0201, 0x0202, 0x0204, 0x0205})

# 消息号 → `MessageCounters` 中对应的累计字段
_FIELD_BY_MSG: dict[int, str] = {
    0x0100: "key_down",
    0x0101: "key_up",
    0x0102: "char",
    0x0200: "mousemove",
    0x0201: "lbutton_down",
    0x0202: "lbutton_up",
    0x0204: "rbutton_down",
    0x0205: "rbutton_up",
    0x020A: "wheel",
    0x0006: "activate",
    0x0007: "setfocus",
    0x0008: "killfocus",
    0x001C: "activateapp",
}

# SetWindowSubclass 的回调原型。
# LRESULT 在 64 位下必须用 c_longlong——用 c_long 会在返回值超出 32 位时被截断。
SUBCLASSPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    ctypes.c_ulonglong,
    ctypes.c_ulonglong,
)

# 必须保活：回调对象一旦被 GC，Windows 会回调到已释放地址 → 崩溃且无法捕获
_KEEPALIVE: list[Any] = []


def _sign_extend(value: int) -> int:
    """把 16 位无符号值按补码解释为有符号值。

    Args:
        value: 低 16 位数值。

    Returns:
        符号扩展后的整数。

    Note:
        点击消息的 `lParam` 高低 16 位分别是有符号的 x / y 客户区坐标；
        若目标客户区坐标超过 32767（多显示器负坐标或超宽窗口），
        不扩展会得到 65535 量级的错误值。
    """
    return value - 0x10000 if value >= 0x8000 else value


def _client_xy(lparam: int) -> list[int]:
    """从 `lParam` 解出客户区坐标（含符号扩展）。

    Args:
        lparam: 消息的 `lParam`。

    Returns:
        `[x, y]` 客户区坐标。
    """
    x = _sign_extend(lparam & 0xFFFF)
    y = _sign_extend((lparam >> 16) & 0xFFFF)
    return [x, y]


def _cursor_screen() -> list[int] | None:
    """取当前光标的屏幕坐标。

    Returns:
        `[x, y]` 屏幕坐标；取不到时返回 None。
    """
    try:
        point = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return [point.x, point.y]
    except Exception:
        # 光标位置只是辅助信息，取不到不应中断消息处理
        pass
    return None


def _is_foreground(hwnd: int) -> bool:
    """判断窗口所属的顶层窗口当前是否为前台窗口。

    Args:
        hwnd: 窗口句柄（可以是子窗口）。

    Returns:
        是否为前台窗口。

    Note:
        必须把 `hwnd` **上溯到顶层**再与 `GetForegroundWindow()` 比较：
        本模块挂钩的是 Tk 的画布子窗口（`TkChild`），而 `GetForegroundWindow()`
        返回的永远是顶层窗口，直接比较会恒为 False——那会把每次输入都误标为
        「后台发生」，使 `is_foreground` 字段彻底失去判别力。
    """
    try:
        user32 = ctypes.windll.user32
        top = hwnd
        while True:
            parent = user32.GetParent(top)
            if not parent:
                break
            top = parent
        return user32.GetForegroundWindow() == top
    except Exception:
        return False


class WndProcHook:
    """窗口消息挂钩：统计消息并按需写入事件日志（支持**多窗口同时挂钩**）。

    Attributes:
        hwnds: 被挂钩的窗口句柄列表。
        active: 挂钩是否已安装。
    """

    def __init__(
        self,
        hwnds: list[int],
        counters: Any,
        log: Any,
        *,
        on_message: Callable[[int, int, int], None] | None = None,
    ) -> None:
        """准备挂钩（**不安装**，需再调 `install`）。

        Args:
            hwnds: 目标窗口句柄列表。**必须包含全部接收输入的子窗口**——
                Tk 把鼠标消息投给画布窗口、键盘消息投给客户区容器窗口，
                只挂其一必漏掉另一类（见 `app.TestbedApp._hook_hwnds` 的说明）。
            counters: `CounterStore` 实例，用于累计计数。
            log: `EventLog` 实例，用于写 `wndproc` 事件。
            on_message: 可选回调，收到任何已挂钩消息时调用，参数为
                `(msg, wparam, lparam)`——供上层在收到输入时刷新界面。
        """
        self.hwnds = hwnds
        self._counters = counters
        self._log = log
        self._on_message = on_message
        self._subclass_id = 1
        # 每个窗口一个回调对象；卸载时需按窗口取回对应的那一个
        self._procs: dict[int, Any] = {}
        self._user32 = ctypes.windll.user32
        self._comctl32 = ctypes.windll.comctl32
        self._def_subclass_proc = self._comctl32.DefSubclassProc
        self._def_subclass_proc.restype = ctypes.c_longlong
        self._def_subclass_proc.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.active = False

    def install(self) -> bool:
        """安装子类化挂钩（逐个窗口）。

        Returns:
            是否**全部**安装成功。

        Note:
            `SetWindowSubclass` 由 comctl32 维护调用链，卸载时只需 `RemoveWindowSubclass`，
            无需（也不能）用 `SetWindowLongPtr` 还原原过程。
        """

        def _make_proc() -> Any:
            def _proc(hwnd: int, msg: int, wparam: int, lparam: int, uid: int, ref: int) -> int:
                # 回调内任何异常都必须吞掉：抛出会打断目标的消息循环，界面直接卡死
                try:
                    self._handle(hwnd, msg, wparam, lparam)
                except Exception:
                    pass
                return self._def_subclass_proc(hwnd, msg, wparam, lparam)

            proc = SUBCLASSPROC(_proc)
            # 必须保活：回调对象一旦被 GC，Windows 会回调到已释放地址 → 崩溃且无法捕获
            _KEEPALIVE.append(proc)
            return proc

        ok = True
        for hwnd in self.hwnds:
            proc = _make_proc()
            self._procs[hwnd] = proc
            installed = bool(
                self._comctl32.SetWindowSubclass(
                    wintypes.HWND(hwnd), ctypes.cast(proc, ctypes.c_void_p), self._subclass_id, 0
                )
            )
            ok = ok and installed
        self.active = ok
        return ok

    def uninstall(self) -> None:
        """卸载全部挂钩。"""
        for hwnd, proc in self._procs.items():
            try:
                self._comctl32.RemoveWindowSubclass(
                    wintypes.HWND(hwnd), ctypes.cast(proc, ctypes.c_void_p), self._subclass_id
                )
            except Exception:
                # 窗口可能已销毁；此时无需（也无法）卸载
                pass
        self._procs.clear()
        self.active = False

    def _handle(self, hwnd: int, msg: int, wparam: int, lparam: int) -> None:
        """处理一条挂钩到的消息（计数 + 按需记录）。

        Args:
            hwnd: **消息所在的窗口**（同一挂钩可能覆盖多个窗口，故不能取成员变量）。
            msg: 消息号。
            wparam: 消息的 `wParam`。
            lparam: 消息的 `lParam`。
        """
        name = MSG_NAMES.get(msg)
        if name is None:
            return

        counters = self._counters
        field = _FIELD_BY_MSG[msg]
        if msg == 0x020A:
            # 滚轮：wParam 高 16 位是有符号增量（以 120 为一格）
            delta = _sign_extend((wparam >> 16) & 0xFFFF)
            counters.touch_message("wheel")
            counters.touch_message("wheel_net", delta)
        else:
            counters.touch_message(field)

        # 只有客户区坐标类的消息才能据 lParam 解出坐标（滚轮是屏幕坐标，已排除）
        client_xy = _client_xy(lparam) if msg in _CLIENT_XY_MSGS else None
        if msg in (0x0201, 0x0204):
            counters.set_message("last_click_xy", client_xy)
        if msg in (0x0100, 0x0101):
            counters.set_message("last_key", int(wparam))
        if msg == 0x0102:
            # `WM_CHAR` 的 wParam 是 UTF-16 码元；控制字符（<32）不记录，避免日志噪声
            code = wparam & 0xFFFF
            counters.set_message("last_char", chr(code) if code >= 32 else None)

        if msg in _DETAIL_MSGS:
            self._log.write(
                "wndproc",
                msg=msg,
                msg_name=name,
                hwnd=hwnd,
                wparam=int(wparam),
                lparam=int(lparam),
                client_xy=client_xy,
                cursor_screen=_cursor_screen(),
                is_foreground=_is_foreground(hwnd),
                counters=counters.message_snapshot(),
            )

        if self._on_message is not None:
            self._on_message(msg, int(wparam), int(lparam))
