"""测试靶场的静态色卡（能力 E）。

一块**完全静止**的区域，含 6 个**精确已知 RGB** 的纯色块。用途是让「截图取到了
什么」变成可读数的事实——没有已知参照物，无法区分「应用内容 / 遮挡物 / 空帧 /
过期帧」（`M0-诊断场景需求.md` §3E）。

**中灰 128 是伽马探针**：若被当作线性值渲染，会显示成约 55 而非 128，一眼可辨。
"""

from __future__ import annotations

import tkinter as tk

# 6 个色块：#RRGGBB → 用途说明
# 中灰必须用 #808080（= 128,128,128）——它是判定色彩空间是否正确的探针
COLORS: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("#000000", (0, 0, 0)),  # 纯黑：下限参照
    ("#FFFFFF", (255, 255, 255)),  # 纯白：上限参照
    ("#FF0000", (255, 0, 0)),  # 纯红：通道独立校验
    ("#00FF00", (0, 255, 0)),  # 纯绿：通道独立校验
    ("#0000FF", (0, 0, 255)),  # 纯蓝：通道独立校验
    ("#808080", (128, 128, 128)),  # 中灰：伽马 / 色彩空间探针
)

# 色块之间的留白（像素）——纯色块互不相邻，便于消费方按固定步长采样
_GAP = 4


def draw(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, tags: tuple[str, ...] = ()) -> list[int]:
    """在画布上绘制色卡。

    Args:
        canvas: 目标画布。
        x1: 区域左边界（客户区像素）。
        y1: 区域上边界。
        x2: 区域右边界。
        y2: 区域下边界。
        tags: 附加到色块上的画布标签，供调用方控制 z 序。

    Returns:
        各色块中心的 x 坐标列表（与 `COLORS` 同序），供调用方写界面文本。

    Note:
        色块横排、等宽，块间留 `_GAP` 的空隙。色块上下各留出空间给区域标题，
        这样标题文本不会与色块像素混淆——色卡区域的像素在**无标题处**必须精确。
        必须传入 `tags`：调用方会按标签重排 z 序，无标签的项会留在原地而
        被区域底色盖住（色块属于 `chrome` 层）。
    """
    top = y1 + 24
    bottom = y2 - 6
    left = x1 + 8
    right = x2 - 8
    count = len(COLORS)
    # 每块宽度含一个间隔，末尾再减掉一个多余间隔
    block = (right - left - _GAP * (count - 1)) // count
    centers: list[int] = []
    for index, (hex_color, _rgb) in enumerate(COLORS):
        bx1 = left + index * (block + _GAP)
        bx2 = bx1 + block
        # outline="" 去掉边框：边框像素会被采样污染，破坏「纯色」前提
        canvas.create_rectangle(bx1, top, bx2, bottom, fill=hex_color, outline="", tags=tags)
        centers.append((bx1 + bx2) // 2)
    return centers


def labels() -> list[str]:
    """返回各色块的文本标签（色值 + RGB 三元组）。

    Returns:
        与 `COLORS` 同序的标签列表。
    """
    return [f"{hex_color} {rgb}" for hex_color, rgb in COLORS]
