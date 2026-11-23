"""后台点击兜接：让投递到窗口的鼠标消息也能触发界面动作。

**为什么需要它**：Tk 只处理进入系统输入队列的真实输入，对 `PostMessage`
投递到窗口的 `WM_LBUTTONDOWN` **一律丢弃**（消息进了窗口过程，但不翻译成
`<Button-1>` 事件）。于是「后台点击」这类自动化手段在本靶场上完全失效——
不是注入端有问题，而是目标程序不认这条路径。原靶场因此把输入模式固定为
`foreground`，后台点击在它上面无法验证。

**做法**：在窗口过程里把「Tk 丢弃的点击」补回来。难点在于**不能重复处理**：
真实输入既进窗口过程、又被 Tk 转成事件，若两处都处理就会点一次触发两次。

**区分依据（实测）**：

| 输入方式 | 进 WndProc | 产生 Tk 事件 | `GetMessageExtraInfo` |
|---|---|---|---|
| `PostMessage` 投递 | 是 | **否** | 0 |
| pynput 真实输入 | 是 | **是** | 0 |

`GetMessageExtraInfo` 对两者都是 0，**不能**用作判据（同进程实测）。
唯一可靠的区分是「Tk 有没有处理它」，因此采用**认领制**：

1. 窗口过程收到 `WM_LBUTTONDOWN` → 记一条「待定」记录（只写数据，不碰界面）；
2. 主线程稍后核对：若 Tk 已为该消息触发过绑定（认领），什么都不做；
3. 未被认领的，视为 Tk 丢弃的投递点击 → 用与 Tk 相同的处理入口补一次。

**必须在主线程核对**：Tk 不是线程安全的。实测在窗口过程回调里直接调
`root.after_idle` 会让解释器在 `PyEval_RestoreThread` 处崩溃——窗口过程可能
在任意线程被回调。本模块只负责记录与判定，核对由 `app.py` 的主线程定时器驱动。

**也必须在同进程内**：`SetWindowSubclass` 不能跨进程（实测返回 0），
所以兜接逻辑只能长在靶场自己身上。
"""

from __future__ import annotations

import time

# 待定记录的有效期（秒）。超过这个时间还没被认领，就不再兜接。
#
# 取 0.5s：真实输入时 Tk 的认领发生在同一条消息的处理过程中（毫秒级），
# 留 0.5s 足够覆盖主线程偶发的短暂占用；再长则会把「用户真实点击但界面
# 当时正忙」误判成投递点击，凭空多出一次点击。
PENDING_TTL = 0.5

# 同一条消息可能被多个挂钩窗口收到（顶层与画布），去重窗口取 0.05s：
# 两侧收到的 lParam 完全一致，合并成一条，否则一次点击会兜接两次。
DUPLICATE_WINDOW = 0.05


class ClickClaimer:
    """判定哪些点击是「Tk 丢弃的投递点击」，并只在那种情况下兜接。

    Attributes:
        synthesized: 累计兜接次数（供日志与自检使用）。
        claimed: 累计被 Tk 认领的次数（说明真实输入被正常处理）。
    """

    def __init__(self, ttl: float = PENDING_TTL, duplicate_window: float = DUPLICATE_WINDOW) -> None:
        """初始化。

        Args:
            ttl: 待定记录的有效期（秒），见 `PENDING_TTL`。
            duplicate_window: 合并多窗口重复消息的时间窗（秒），见 `DUPLICATE_WINDOW`。
        """
        self._ttl = ttl
        self._duplicate_window = duplicate_window
        self._pending: list[dict] = []
        self.synthesized = 0
        self.claimed = 0

    # ---------- 由窗口过程调用（任意线程，只写数据） ----------

    def note_message(self, msg: int, lparam: int) -> None:
        """记录一条消息级点击（`WM_LBUTTONDOWN` 或 `WM_LBUTTONUP`）。

        Args:
            msg: 消息号（`0x0201` / `0x0202`）。
            lparam: 消息的 `lParam`，低 16 位为 x、高 16 位为 y。

        Note:
            **不要在调用方做任何界面操作**：本方法可能在非主线程执行。
            只把事实记下来，判定与兜接都交给主线程。
        """
        if msg not in (0x0201, 0x0202):
            return
        x = lparam & 0xFFFF
        y = (lparam >> 16) & 0xFFFF
        # 有符号扩展：与 `wndproc._client_xy` 保持同一口径，避免负坐标被读成 6 万
        if x >= 0x8000:
            x -= 0x10000
        if y >= 0x8000:
            y -= 0x10000
        now = time.monotonic()
        kind = "down" if msg == 0x0201 else "up"
        # 同一位置同一类型的消息在多窗口钩子下会重复到达，合并成一条
        for item in reversed(self._pending):
            if now - item["at"] > self._duplicate_window:
                break
            if item["kind"] == kind and (item["x"], item["y"]) == (x, y):
                return
        self._pending.append({"kind": kind, "x": x, "y": y, "at": now, "claimed": False})

    # ---------- 由主线程调用 ----------

    def claim(self, x: int, y: int) -> bool:
        """把一次 Tk 绑定触发认领到最近一条待定记录上。

        Args:
            x: Tk 事件报告的客户区 x。
            y: Tk 事件报告的客户区 y。

        Returns:
            True 表示认领成功（找到了匹配的待定记录）；False 表示没有对应记录
            （例如用户直接点了窗口但消息未经过本挂钩）。

        Note:
            坐标允许小幅偏差：Tk 报告的是控件相对坐标，而 `lParam` 是窗口客户区
            坐标，两者在多级子窗口下可能差几个像素。用 8px 容差吸收这个差值，
            比要求精确相等更稳。
        """
        now = time.monotonic()
        for item in reversed(self._pending):
            if item["claimed"] or item["kind"] != "down":
                continue
            if now - item["at"] > self._ttl:
                break
            if abs(item["x"] - x) <= 8 and abs(item["y"] - y) <= 8:
                item["claimed"] = True
                self.claimed += 1
                return True
        return False

    def take_unclaimed(self) -> list[tuple[int, int]]:
        """取出并清空「未被认领的按下点击」。

        Returns:
            `(x, y)` 列表，每项对应一次需要兜接的点击。

        Note:
            **只返回 `down`**：兜接的动作入口只接受「按在哪里」，
            抬起与按下成对出现，按一次就够。若把 `up` 也兜接，动作会执行两次。

            过期的记录一并丢弃：那是「Tk 迟迟没认领但也不该兜接」的历史残留
            （例如界面忙了很久），继续保留会让下一次核对凭空多出一次点击。
        """
        now = time.monotonic()
        result: list[tuple[int, int]] = []
        keep: list[dict] = []
        for item in self._pending:
            expired = now - item["at"] > self._ttl
            if expired:
                continue
            if item["kind"] == "down" and not item["claimed"]:
                result.append((item["x"], item["y"]))
                continue
            keep.append(item)
        self._pending = keep
        self.synthesized += len(result)
        return result

    def has_pending(self) -> bool:
        """是否有待核对的记录。

        Returns:
            True 表示主线程需要安排一次核对。
        """
        return bool(self._pending)

    def snapshot(self) -> dict:
        """返回当前判定状态（写事件日志用）。

        Returns:
            含计数与待定条数的字典。
        """
        return {
            "pending": len(self._pending),
            "claimed": self.claimed,
            "synthesized": self.synthesized,
        }
