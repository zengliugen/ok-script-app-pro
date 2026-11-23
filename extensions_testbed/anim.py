"""测试靶场的帧计数器与动画区（能力 F / G）。

**单一 `after` 循环**同时驱动两者，避免多定时器竞争与漂移（见
`extensions_testbed/README.md` §5.2）。

**帧计数无论动画开关都自增**——它是过期帧检测（读到递增的已知数字才能证明
拿到的是当前帧而非缓存帧）的唯一依据，故其 `change_when` 恒为 `always`。
**不要为配合「画面静止」而冻结本区域**（`M0-诊断场景需求.md` §3F）。
"""

from __future__ import annotations

import time
from typing import Callable

# 循环间隔（毫秒）：约 60 FPS。与 M0 诊断场景的固定帧率约定一致，使帧率可复现
TICK_MS = 16

# 动画一个往返的周期（毫秒）
ANIM_PERIOD_MS = 2000

# 心跳间隔（毫秒）：每秒一条，用于从外部确认「应用仍在运行」
HEARTBEAT_MS = 1000


class Ticker:
    """帧计数器与心跳的驱动器。

    Attributes:
        frame: 自启动以来的帧序号（每次 tick 自增）。
        running: 循环是否在运行。
    """

    def __init__(
        self,
        root: object,
        on_tick: Callable[[int], None],
        on_heartbeat: Callable[[int, float], None],
    ) -> None:
        """初始化驱动器（**不启动**，需再调 `start`）。

        Args:
            root: Tk 根窗口（提供 `after` 与 `after_cancel`）。
            on_tick: 每帧回调，参数为当前帧序号；用于更新帧计数文本与动画位置。
            on_heartbeat: 每秒回调，参数为 `(帧序号, 帧率)`；用于写心跳事件。
        """
        self._root = root
        self._on_tick = on_tick
        self._on_heartbeat = on_heartbeat
        self._after_id: str | None = None
        self._last_heartbeat = time.monotonic()
        self._frames_at_heartbeat = 0
        self.frame = 0
        self.running = False

    def start(self) -> None:
        """启动循环。"""
        if self.running:
            return
        self.running = True
        self._last_heartbeat = time.monotonic()
        self._frames_at_heartbeat = 0
        self._schedule()

    def stop(self) -> None:
        """停止循环。"""
        self.running = False
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:
                # 窗口已销毁时 after_cancel 会失败；此时循环本就不需要继续
                pass
            self._after_id = None

    def _schedule(self) -> None:
        """安排下一次 tick。"""
        self._after_id = self._root.after(TICK_MS, self._tick)

    def _tick(self) -> None:
        """一次 tick：自增帧序、回调上层、按需发心跳。"""
        if not self.running:
            return
        self.frame += 1
        try:
            self._on_tick(self.frame)
        except Exception:
            # 单帧绘制失败不应终止整个循环——否则帧计数停滞，会被误判为「应用冻结」
            pass

        now = time.monotonic()
        elapsed_ms = (now - self._last_heartbeat) * 1000.0
        if elapsed_ms >= HEARTBEAT_MS:
            # 帧率按「本心跳周期内的帧数 / 实际耗时」计算，而非固定 1000/16——
            # 后者会把调度抖动掩盖掉，而抖动正是需要被观察的事实
            frames = self.frame - self._frames_at_heartbeat
            fps = round(frames * 1000.0 / elapsed_ms, 1) if elapsed_ms > 0 else 0.0
            self._last_heartbeat = now
            self._frames_at_heartbeat = self.frame
            try:
                self._on_heartbeat(self.frame, fps)
            except Exception:
                # 心跳写日志失败不应中断循环
                pass

        self._schedule()


def anim_phase(frame: int) -> float:
    """计算动画的相位。

    Args:
        frame: 当前帧序号。

    Returns:
        相位值，范围 `0.0 ~ 1.0`，一个往返对应 `ANIM_PERIOD_MS` 毫秒。

    Note:
        相位由**帧序号**而非墙上时钟推导——这样动画与帧计数同源，
        消费方可用「帧号 → 相位」反推色块应在的位置，动画因此成为**可校验**的动态。
    """
    ticks_per_period = max(1, ANIM_PERIOD_MS // TICK_MS)
    return (frame % ticks_per_period) / ticks_per_period
