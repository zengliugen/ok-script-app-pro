"""测试靶场的手势判定（`drag` / `swipe` / `double_click`）。

靶场的「手势类元素」不靠单击触发，而靠**一次按压到抬起的位移与耗时**判定：
`battle.dodge_zone` 用 `swipe`，`settings_menu.sensitivity_slider` 用 `drag`。
两种交互在程序侧是同一套判定，只是**结果标签不同**——故集中在本模块，
避免绘制与判定逻辑分散在 `screens.py` 与 `app.py` 两处。

**为什么需要它**：`extensions_testbed/README.md` §3.2 要求画面图覆盖 5 种交互类型
（`click` / `double_click` / `drag` / `swipe` / `none`）。其中 `drag` 与 `swipe`
在配置侧是 `predicate` + `interaction`，在程序侧必须**真的对手势产生反应**——
否则 M2 的「点击后画面变化」验证会因「手势没被处理」而误判为注入失败。
"""

from __future__ import annotations

from dataclasses import dataclass

# 判定为「手势」的最小位移（像素）：低于此值视为单击（抖动而非拖动）
MIN_GESTURE_DISTANCE = 24

# 位移超过该值即记为滑动（`swipe`），否则记为拖拽（`drag`）。
#
# 阈值取 160：`swipe` 是「快速划一下」（靶场用于闪避动作），`drag` 是「按住拖到某处」
# （靶场用于拖动滑块）。两者在**距离**上区分而非在时长上——因为时长受运行环境
# 调度抖动影响，不可靠；而同一元素上的位移量是配置能预先声明的稳定事实。
SWIPE_MIN_DISTANCE = 160

# 手势类型取值（与适配层 `interaction.type` 的枚举同一套词汇）
GESTURE_DRAG = "drag"
GESTURE_SWIPE = "swipe"


@dataclass(frozen=True, slots=True)
class GestureResult:
    """一次手势的判定结果。

    Attributes:
        kind: 手势类型（`drag` / `swipe`）。
        dx: 水平位移（有符号，右为正）。
        dy: 垂直位移（有符号，下为正）。
        distance: 位移的欧氏距离（像素）。
        duration_ms: 按压到抬起的耗时（毫秒）。
        start: 起点（客户区坐标）。
        end: 终点（客户区坐标）。
    """

    kind: str
    dx: int
    dy: int
    distance: float
    duration_ms: float
    start: tuple[int, int]
    end: tuple[int, int]

    def describe(self) -> str:
        """返回一行可读摘要（写事件日志用）。

        Returns:
            含类型、位移与耗时的文本。
        """
        return (
            f"{self.kind}：({self.start[0]},{self.start[1]}) → ({self.end[0]},{self.end[1]})，"
            f"位移 ({self.dx},{self.dy})={self.distance:.0f}px，耗时 {self.duration_ms:.0f}ms"
        )


def classify(
    start: tuple[int, int],
    end: tuple[int, int],
    duration_ms: float,
) -> GestureResult | None:
    """把一次「按下 → 抬起」判定为手势。

    Args:
        start: 按下位置（客户区坐标）。
        end: 抬起位置（客户区坐标）。
        duration_ms: 按压到抬起的耗时（毫秒）。

    Returns:
        手势结果；位移小于 `MIN_GESTURE_DISTANCE` 时返回 None（那是一次单击，
        不是手势——交由画面元素的点击处理逻辑负责）。

    Note:
        以**距离**而非时长区分 `drag` 与 `swipe`：时长受调度抖动影响不可靠，
        而位移是配置能预先声明的稳定事实。这与 D-19 的取向一致——滑动的方向与
        幅度由配置显式声明，程序侧只负责如实报告观测到的位移。
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = (dx * dx + dy * dy) ** 0.5
    if distance < MIN_GESTURE_DISTANCE:
        return None

    kind = GESTURE_SWIPE if distance >= SWIPE_MIN_DISTANCE else GESTURE_DRAG
    return GestureResult(
        kind=kind,
        dx=dx,
        dy=dy,
        distance=distance,
        duration_ms=duration_ms,
        start=start,
        end=end,
    )


def apply_slider_offset(
    value: int,
    dx: int,
    width: int,
    *,
    minimum: int = 0,
    maximum: int = 100,
) -> int:
    """按拖拽位移更新滑块数值（`settings_menu.sensitivity_slider` 用）。

    Args:
        value: 当前数值。
        dx: 拖拽的水平位移（像素，右为正）。
        width: 滑块轨道的可用宽度（像素）。
        minimum: 数值下界。
        maximum: 数值上界。

    Returns:
        更新并夹紧到 `[minimum, maximum]` 的新数值。

    Note:
        **必须夹紧**：拖拽位移可以任意大，不夹紧会让数值越界，而越界值会以
        「滑块跑出轨道」的形式表现为界面缺陷（那属程序 bug，不是配置问题）。
        换算按位移占轨道宽度的比例——使数值变化与视觉位置一致，配置侧的
        `drag` 判据才能按位移推断出数值应落在哪个区间。
    """
    span = maximum - minimum
    if width <= 0 or span <= 0:
        return value
    delta = round(span * dx / width)
    return max(minimum, min(maximum, value + delta))
