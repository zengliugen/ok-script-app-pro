"""测试靶场的关卡图标网格（YOLO 对比场景的界面，`chapter_select` 用）。

**20 个同类关卡图标，按 1~20 从左到右、从上到下排列**，网格共 7 行、可视 5 行，
滚轮竖向滚动（`IconGrid.scroll`）。

**为什么不再随机排列**：随机位置原先用于让固定 `rect` 定位器失效（命中错误的
图标）。但它让「同一画面的历史截图」无法复现——样本采集、回归对比都要先对齐
位置，收益被抵消。位置固定后仍保留三项「每次进入随机」的外观，模板匹配依旧
无法预先适配：

| 特性 | 模板匹配为何失效 |
|---|---|
| **装饰环起始角每次进入随机** | 同格内容与历史模板不同源，相似度不可复现 |
| **外观色相每次进入随机偏移** | 同上（增加外观多样性，对 M5 的样本采集也有利） |
| **尺寸 ±2px 抖动（每次进入取值）** | 需多尺度搜索，进一步降低稳定性 |

## 为什么是「每次进入」而非「每帧」

1. **色相游走在灰度匹配下几乎不可见**。`template` 算子转灰度后匹配
   （见 `src/ao_identify/operators/template.py`），而色相变化对
   `0.299R + 0.587G + 0.114B` 的加权和影响极小。实测（同次进入、固定 ROI、
   模板小于 ROI）：色相游走 0.6°/帧 的匹配分数均值 0.939，而不游走为 0.934
   ——**相差 0.005**，对阈值判定毫无影响。真正起作用的是**尺寸抖动**
   （均值 0.869、最低 0.680，已跌破 0.85）与环起始角。
2. **逐帧变化与分层架构相悖**。`app.py` 的 `screen` 层只在切画面时重绘；
   「每帧游走」要靠每帧手动重绘去对抗该架构，而 `layout` 声明
   `screen_body` 的 `change_when` 为 `on_screen`（**无输入时不得变化**）——
   逐帧游走会让 `screen_body` 违反自己的声明。
3. **逐帧变化使 `chapter_select` 无法判稳**。`extensions_testbed/README.md` §3.6 登记的动态区域
   只有帧计数器、动画区与 `battle` 的进度条，**没有覆盖图标网格**；图标逐帧变化
   会让该画面的相邻帧相似度长期低于 0.98 阈值，画面永不稳定——而稳定判定是
   点击有效性验证与等待过场结束的前提。

**为什么外观按「本次进入」的随机量而非帧号推导**：随机量在本次进入内固定，
故「本帧截图与上一帧逐像素一致」成立，静止判据可用；而随机性体现在**每次进入
都不同**，这正是模板匹配无法预先适配的原因。

**滚动为什么按整行对齐**：可视区高度是行高的整数倍，一次滚一行时图标始终整格
可见；按像素滚动会让上下两行各露半格，而 `rect` 定位与截图对比都以整格为单位。
"""

from __future__ import annotations

import colorsys
import random
import tkinter as tk

# 网格整体矩形（基准坐标 1280×720）。位于 `screen_body` (0,164,1280,460) 内的左侧，
# 刻意避开：
#   ① 水平中央的按钮列（x 520~760）——两者都是可点目标，重叠会让命中归属不确定；
#   ② `screens._body_probe()` 的平坦探测矩形（960,190,1180,280）——那是 `color`
#      判据的取色点，被图标覆盖会让该画面自己的底色判据失真。
GRID_RECT: tuple[int, int, int, int] = (100, 184, 500, 440)

# 图标数量（3 列 × 7 行，最后一行 2 个）
ICON_COUNT = 20
_COLS = 3
_ROWS = 7
# 可视区一次显示的行数：其余行靠滚轮滚动查看
_VISIBLE_ROWS = 5

# 图标的基准尺寸与网格留白（基准像素）
ICON_W, ICON_H = 104, 40

# 尺寸抖动幅度（±像素）：M5 需要多尺度搜索才能稳定命中
SIZE_JITTER = 2

# 色相随机偏移的范围（度）：0~360 全覆盖，使每次进入的外观差异最大化
_HUE_SPAN = 360.0

# 竖向滚动条的宽度与右移量（基准像素）。放在网格右侧、按钮列之前，
# 属纯展示，不参与命中测试。
_SCROLLBAR_W = 6
_SCROLLBAR_OFFSET = 6


def _gap(total: int, count: int, size: int) -> int:
    """计算网格内的均匀留白。

    Args:
        total: 网格在该方向的总长度（像素）。
        count: 该方向的格子数。
        size: 单个格子的边长（像素）。

    Returns:
        相邻格子之间（以及边界处）的留白（像素）。
    """
    return (total - count * size) // (count + 1)


# 网格留白（由网格矩形与格子尺寸算出，**不写死**）
_H_GAP = _gap(GRID_RECT[2] - GRID_RECT[0], _COLS, ICON_W)
_V_GAP = _gap(GRID_RECT[3] - GRID_RECT[1], _VISIBLE_ROWS, ICON_H)


def _cell_rect(cell: int) -> tuple[int, int, int, int]:
    """取第 N 个格子的基准矩形。

    Args:
        cell: 格子下标（0~19，按行优先：0~2 为第一行）。

    Returns:
        基准坐标下（未滚动）的矩形 `[x1, y1, x2, y2]`。第 5 行起的 y 会超出
        可视区，绘制时按滚动行整体上移。
    """
    row, column = divmod(cell, _COLS)
    x1 = GRID_RECT[0] + _H_GAP + column * (ICON_W + _H_GAP)
    y1 = GRID_RECT[1] + _V_GAP + row * (ICON_H + _V_GAP)
    return (x1, y1, x1 + ICON_W, y1 + ICON_H)


# 全部格子的基准矩形（位置固定，图标编号与格子一一对应）
CELL_RECTS: tuple[tuple[int, int, int, int], ...] = tuple(_cell_rect(index) for index in range(ICON_COUNT))


def _hsl_to_hex(hue: float, saturation: float, lightness: float) -> str:
    """把 HSL 转为 `#RRGGBB`。

    Args:
        hue: 色相（度，0~360）。
        saturation: 饱和度（0~1）。
        lightness: 亮度（0~1）。

    Returns:
        `#RRGGBB` 形式的颜色字符串。

    Note:
        用 `colorsys` 而非手写转换：Tk 只接受十六进制颜色串，而随机色相需要
        连续的 HSL 计算——手写转换易在边界（色相接近 360 回绕）上出错。
    """
    red, green, blue = colorsys.hls_to_rgb((hue % 360) / 360.0, lightness, saturation)
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


class IconGrid:
    """关卡图标网格的绘制（**每次构造即一次「进入画面」**）。

    Attributes:
        perm: 格子 → 图标编号的映射（固定为 1~20 的自然顺序，不再洗牌）。
        labels: 各图标显示的数字（按图标编号）。
    """

    def __init__(
        self,
        canvas: tk.Canvas,
        width: int,
        height: int,
        scale_rect: object,
        *,
        seed: int | None = None,
        origin: tuple[int, int] = (0, 0),
    ) -> None:
        """绑定画布，并生成本次进入的全部随机量。

        Args:
            canvas: 目标画布（真实运行时是**视口子画布**，滚动与裁剪由它完成）。
            width: 视口宽度（像素）。
            height: 视口高度（像素）。
            scale_rect: 基准矩形 → 客户区实际矩形的换算函数（`layout.scale_rect` 的偏函数）。
            seed: 随机种子；None 时每次进入都不同（正常运行时用 None，
                只有需要可复现的调试场景才指定）。
            origin: 视口左上角在客户区中的位置；子画布坐标从这里减去。

        Note:
            传入 `scale_rect` 而非自行换算：缩放必须以**共享分割点**为单位
            （`layout.scaled_rect` 的要求），各元素自行换算会在相邻区域间留下缝隙。
            滚动按**像素**而不是整行：整行滚动看起来像翻页，拖动时内容不跟手。
            裁剪由子画布完成（Tk 画布不能逐项裁剪），因此绘制坐标要减去 `origin`。
        """
        self._canvas = canvas
        self._viewport_w = width
        self._viewport_h = height
        self._origin = origin
        self._scale_rect = scale_rect

        rng = random.Random(seed) if seed is not None else random.Random()
        # 固定顺序：第 N 个格子放第 N 号图标（左→右、上→下）。
        self.perm = tuple(range(ICON_COUNT))
        self.labels = tuple(str(index + 1) for index in range(ICON_COUNT))

        # 本次进入的随机外观：尺寸抖动、色相偏移、装饰环起始角——
        # 三者**在本次进入内固定**，故画面逐像素静止（静止判据可用）；
        # 而每次进入都不同，这正是模板匹配无法预先适配的原因（见模块说明）。
        self._jitters = tuple(rng.randint(-SIZE_JITTER, SIZE_JITTER) for _ in range(ICON_COUNT))
        self._hue_offsets = tuple(rng.uniform(0, _HUE_SPAN) for _ in range(ICON_COUNT))
        self._ring_starts = tuple(rng.randint(0, 359) for _ in range(ICON_COUNT))
        # 竖向滚动位置（像素，0 表示顶部）
        self._scroll_px = 0.0
        self._items: dict[int, dict[str, int]] = {}

    def rebind(self, canvas, width, height, origin) -> None:
        """换绑到新的视口画布（整屏重绘会重建子画布）。

        Args:
            canvas: 新画布。
            width: 视口宽度。
            height: 视口高度。
            origin: 视口左上角在客户区中的位置。
        """
        self._canvas = canvas
        self._viewport_w = width
        self._viewport_h = height
        self._origin = origin

    @property
    def scroll_px(self) -> float:
        """当前滚动的像素偏移（0 表示顶部）。"""
        return self._scroll_px

    @property
    def scroll_row(self) -> int:
        """当前滚动到的行号（按行距四舍五入，供日志查看）。"""
        return round(self._scroll_px / self.row_step())

    @property
    def max_scroll_row(self) -> int:
        """可滚动到的最大行号（供界面判断「还需不需要滚动」）。"""
        return max(0, _ROWS - _VISIBLE_ROWS)

    def max_scroll_px(self) -> float:
        """可滚动到的最大像素偏移。"""
        return max(0.0, self._content_height() - float(self._viewport_h))

    def _content_height(self) -> float:
        """列表内容的总高度（含首尾留白，实际像素）。"""
        _x1, y1, _x2, y2 = self._scale_rect((0, 0, 0, _V_GAP + _ROWS * (ICON_H + _V_GAP)))  # type: ignore[operator]
        return float(y2 - y1)

    def visible_cells(self) -> tuple[int, ...]:
        """当前可见的格子下标（与视口相交的行，按行优先）。"""
        offset_y = self._origin[1]
        cells = []
        for cell in range(ICON_COUNT):
            _x1, y1, _x2, y2 = _jittered_rect(cell, self._jitters[cell])
            _sx1, top, _sx2, bottom = self._scale_rect((0, y1, 0, y2))  # type: ignore[operator]
            top = float(top - offset_y)
            bottom = float(bottom - offset_y)
            if bottom > self._scroll_px and top < self._scroll_px + self._viewport_h:
                cells.append(cell)
        return tuple(cells)

    def scroll_by(self, delta_px: float) -> bool:
        """按像素滚动网格。

        Args:
            delta_px: 滚动像素，正数向下、负数向上；越界时夹紧。

        Returns:
            是否真的发生了滚动（已到顶/底时返回 False，不重绘）。
        """
        return self.scroll_to_px(self._scroll_px + float(delta_px))

    def scroll_to_px(self, px: float) -> bool:
        """滚动到指定像素偏移（夹紧到可滚动范围）。

        Args:
            px: 目标偏移。

        Returns:
            是否真的发生了滚动。
        """
        target = max(0.0, min(self.max_scroll_px(), float(px)))
        if abs(target - self._scroll_px) < 0.5:
            return False
        self._scroll_px = target
        self.draw()
        return True

    def scroll(self, delta_rows: int) -> bool:
        """按行滚动网格。

        Args:
            delta_rows: 滚动行数，正数向下、负数向上；越界时夹紧。

        Returns:
            是否真的发生了滚动（已到顶/底时返回 False，不重绘）。
        """
        return self.scroll_by(int(delta_rows) * self.row_step())

    def scroll_to_row(self, row: int) -> bool:
        """滚动到指定行（夹紧到可滚动范围）。

        Args:
            row: 目标行号。

        Returns:
            是否真的发生了滚动。
        """
        return self.scroll_to_px(int(row) * self.row_step())

    def scroll_to_ratio(self, ratio: float) -> bool:
        """按滑块在轨道上的位置比例滚动（0 顶部、1 底部）。

        Args:
            ratio: 滑块顶端在可移动范围内的比例，越界时夹紧。

        Returns:
            是否真的发生了滚动。
        """
        return self.scroll_to_px(max(0.0, min(1.0, float(ratio))) * self.max_scroll_px())

    def row_step(self) -> float:
        """一行占的实际像素高度（图标高度 + 行距）。

        Returns:
            实际像素值，至少 1.0。

        Note:
            列表内拖拽滚动按这个步长换算「拖过了几行」；用缩放后的值而不是基准值，
            窗口尺寸变化时拖拽手感与视觉行高一致。
        """
        _x1, y1, _x2, y2 = self._scale_rect((0, 0, 0, ICON_H + _V_GAP))  # type: ignore[operator]
        return max(1.0, float(y2 - y1))

    def scrollbar_rect(self) -> tuple[int, int, int, int] | None:
        """竖向滚动条的轨道矩形（**视口内局部坐标**）。

        Returns:
            `(x1, y1, x2, y2)`；内容装得下时返回 None（不画滚动条）。

        Note:
            条画在视口右侧内部（`_SCROLLBAR_INSET` 起），而不是视口外的客户区：
            滚动条随视口子画布一起绘制，超出子画布的部分会被裁掉。
        """
        if _ROWS <= _VISIBLE_ROWS:
            return None
        x2 = self._viewport_w - 4
        x1 = x2 - _SCROLLBAR_W
        return (x1, 0, x2, self._viewport_h)

    def thumb_rect(self) -> tuple[int, int, int, int] | None:
        """滚动条滑块的矩形（**视口内局部坐标**）。

        Returns:
            `(x1, y1, x2, y2)`；不画滚动条时返回 None。

        Note:
            滑块高度按「视口高度 / 内容高度」比例，位置按当前像素偏移比例——
            平滑滚动下滑块连续移动，不再跳行。
        """
        track = self.scrollbar_rect()
        if track is None:
            return None
        track_x1, track_y1, track_x2, track_y2 = track
        track_h = track_y2 - track_y1
        content_h = max(1.0, self._content_height())
        thumb_h = max(8, round(track_h * track_h / content_h))
        max_px = self.max_scroll_px()
        ratio = 0.0 if max_px <= 0 else self._scroll_px / max_px
        thumb_y1 = round(track_y1 + (track_h - thumb_h) * ratio)
        return (track_x1, thumb_y1, track_x2, thumb_y1 + thumb_h)

    def draw(self) -> None:
        """绘制全部可见图标与竖向滚动条（进入画面、切换语言或滚动时调用）。

        Note:
            先删除上一批画布项：同一画面可能被反复进入（`F7` 循环、导航往返），
            残留的旧图标会与新图标叠加，使画面不可辨认。
            只画**整行可见**的格子：可视区高度是行高的整数倍，按行滚动时图标
            始终整格可见；滚动到可视区外的行不绘制，天然完成裁剪（Tk 画布没有
            逐项裁剪）。
        """
        self._canvas.delete("yolo_icons")
        self._items = {}
        ox, oy = self._origin
        shift = oy + self._scroll_px
        for cell in self.visible_cells():
            icon = self.perm[cell]
            x1, y1, x2, y2 = _jittered_rect(cell, self._jitters[cell])
            x1, y1, x2, y2 = self._scale_rect((x1, y1, x2, y2))  # type: ignore[operator]
            x1 -= ox
            x2 -= ox
            y1 -= shift
            y2 -= shift
            center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
            fill = self._color(icon, 0.55)
            ring = self._canvas.create_arc(
                x1,
                y1,
                x2,
                y2,
                start=self._ring_starts[icon],
                extent=270,
                style="arc",
                outline=self._color(icon, 0.72),
                width=2,
                tags=("screen", "yolo_icons"),
            )
            inset = max(2, (y2 - y1) // 5)
            body = self._canvas.create_oval(
                x1 + inset,
                y1 + inset,
                x2 - inset,
                y2 - inset,
                fill=fill,
                outline="",
                tags=("screen", "yolo_icons"),
            )
            label = self._canvas.create_text(
                center_x,
                center_y,
                text=self.labels[icon],
                fill="#101010",
                font=("Consolas", 18),
                anchor="center",
                tags=("screen", "yolo_icons"),
            )
            self._items[cell] = {"ring": ring, "body": body, "label": label, "icon": icon}
        self._draw_scrollbar()

    def _draw_scrollbar(self) -> None:
        """绘制竖向滚动条（纯展示，不参与命中测试）。

        Note:
            轨道与滑块的几何由 `scrollbar_rect` / `thumb_rect` 统一计算——拖动
            命中测试用的是同一组矩形，画与点必须来自同一处，否则会出现「看着
            在滑块上、按下去判定到轨道」的错位。
        """
        track = self.scrollbar_rect()
        thumb = self.thumb_rect()
        if track is None or thumb is None:
            return
        self._canvas.create_rectangle(
            *track,
            fill="#20301F",
            outline="#50704F",
            tags=("screen", "yolo_icons"),
        )
        self._canvas.create_rectangle(
            *thumb,
            fill="#A0C89F",
            outline="",
            tags=("screen", "yolo_icons"),
        )

    def _color(self, icon: int, lightness: float) -> str:
        """取某图标在当前进入下的颜色。

        Args:
            icon: 图标编号。
            lightness: 亮度（0~1）；环用更高亮度以便与主体区分。

        Returns:
            `#RRGGBB` 形式的颜色。

        Note:
            基础色相按编号均匀铺开（`20 + icon*37`），使 9 个图标一眼可分；
            再叠加本次进入的随机偏移——故同一编号在不同次进入中颜色不同。
        """
        base_hue = (20 + icon * 37) % 360
        return _hsl_to_hex(base_hue + self._hue_offsets[icon], 0.55 if lightness < 0.7 else 0.7, lightness)


def _jittered_rect(cell: int, jitter: int) -> tuple[int, int, int, int]:
    """取某格子加入尺寸抖动后的矩形（以格子中心为基准缩放）。

    Args:
        cell: 格子下标。
        jitter: 本次进入的抖动量（像素，可正可负）。

    Returns:
        基准坐标下的矩形 `[x1, y1, x2, y2]`。

    Note:
        抖动以**格子中心**为基准，故图标不会漂移到相邻格子——否则「哪个图标在哪个格」
        这一信息会被破坏，配置侧的 `rect` 区域与图标的对应关系也随之失效。
    """
    x1, y1, x2, y2 = CELL_RECTS[cell]
    center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
    half_w = (ICON_W + jitter) // 2
    half_h = (ICON_H + jitter) // 2
    return (center_x - half_w, center_y - half_h, center_x + half_w, center_y + half_h)
