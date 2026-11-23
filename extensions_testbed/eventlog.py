"""测试靶场的事件日志（JSON Lines）。

**首要证据通道**（不受截图正确性影响，且可跨进程实时读取，见
`extensions_testbed/README.md` §6）。

字段命名沿用 M0 受控窗口基准，使其分析代码可复用；唯一必要的改名是
`source: "unity"` → `"app"`（靶场无游戏引擎，对应概念是 Tk 的事件绑定回调，
见 §6.1——**语义等价而非同一 API，不可直接数值比较**）。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

# 本进程的单调起点（秒），用于计算 t_ms（自启动起算的毫秒时间戳）
_T0 = time.monotonic()


def elapsed_ms() -> float:
    """返回自进程启动起算的毫秒数。

    Returns:
        单调递增的毫秒时间戳。
    """
    return round((time.monotonic() - _T0) * 1000.0, 1)


class EventLog:
    """JSONL 事件日志写入器。

    Attributes:
        path: 日志文件的绝对路径。
    """

    def __init__(self, path: str | Path) -> None:
        """打开日志文件并准备写入。

        Args:
            path: 日志文件路径；父目录不存在时自动创建。

        Note:
            以 `line buffering`（`buffering=1`）打开，保证**每写一行即落盘**——
            消费方要在另一个进程里边跑边读，缓冲到退出才写会使全部数据丢失
            （`M0-诊断场景需求.md` §6.3 的硬要求）。
            另加锁：WndProc 回调在 Tk 主线程之外触发，写入可能并发。
        """
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0
        # buffering=1 即行缓冲；文本模式下每遇到换行即 flush
        self._file = self.path.open("a", encoding="utf-8", buffering=1, newline="\n")

    def close(self) -> None:
        """关闭日志文件。"""
        with self._lock:
            if not self._file.closed:
                self._file.close()

    def write(self, source: str, **fields: Any) -> dict[str, Any]:
        """写入一条事件。

        Args:
            source: 事件来源，取值 `wndproc` / `app` / `heartbeat` / `ready`。
            **fields: 事件的其余字段，原样写入；值为 None 的键会被丢弃。

        Returns:
            实际写入的事件字典（含补全的 `t_ms` 与 `seq`），便于调用方复用。

        Note:
            `seq` 由本方法分配：`ready` 固定为 0 且不占序号（它表示「就绪」，
            不是序列中的一次事件），其余事件按写入顺序自增。调用方传入的
            `t_ms` / `seq` 会覆盖自动值。
        """
        event: dict[str, Any] = {"t_ms": elapsed_ms(), **fields}
        with self._lock:
            if source == "ready":
                event.setdefault("seq", 0)
            else:
                event.setdefault("seq", self._seq)
                self._seq += 1
            event["source"] = source
            # 值为 None 的字段不写入：避免消费者对「字段存在但无意义」做特判
            clean = {key: value for key, value in event.items() if value is not None}
            self._file.write(json.dumps(clean, ensure_ascii=False) + "\n")
            self._file.flush()
        return clean
