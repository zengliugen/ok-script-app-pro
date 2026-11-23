# -*- coding: utf-8 -*-
"""模板掩码形状：描述模板图里**哪些像素参与匹配**。

## 与 `shapes.Shape` 的分工

| 类 | 坐标系 | 用途 |
|---|---|---|
| `shapes.Shape` | 相对**整帧** | 特征在画面上的定位框（搜索区域由它派生） |
| `MaskShape`（本模块） | 相对**特征包围盒** | 模板内哪些像素参与相关性计算 |

两者刻意分开。定位框是「这个元素在画面哪个位置」，掩码形状是「这个元素内部
哪些像素有效」——它们的坐标系、生命周期、修改入口都不同。混成一个会让
「我调了掩码，怎么搜索区域也变了」变成无法解释的现象。

**为什么掩码形状相对包围盒而不是整帧**：模板就是从包围盒裁出来的，掩码形状
与模板同源才能保证「形状里的 (0.5,0.5)」就是「模板正中心」这个直觉成立。
若用帧坐标，特征一移动，掩码形状就与模板错位，而错位的表现是识别率莫名下降。

## `none` 与 `rect` 的区别

- `none`：**不生成形状掩码**，整个包围盒都参与，只靠颜色剔除。适合整块都是
  前景的元素（如纯图标），也让「没配形状」与「配了矩形」这两种状态区分开。
- `rect`：生成一个矩形掩码。视觉上与 `none` 等价，但它有可编辑的边角，
  用户能把矩形拖小、拖出包围盒、或旋转——那是「我要排除边缘一圈」的明确表达。

因此两者不能合并：`none` 是「没有形状这个概念」，`rect` 是「形状恰好是矩形」。

## 为什么坐标不夹在 0–1

掩码形状是「参与匹配的像素范围」，用户把圆形拖到只覆盖模板一角、或让矩形的
角伸到模板外一部分，都是合法诉求（典型：只保留图标中心那个圆，其余留白）。
早期实现把坐标夹在 0–1 并在越界时抛异常，而异常发生在 `mouseMoveEvent` 里——
界面表现是**拖到一半就不动了，没有任何提示**。现在允许 ±8 倍包围盒，
既能表达「形状远大于模板」，又能挡住换算写错导致的荒谬数值。

## 旋转为什么需要 `aspect`

坐标是相对包围盒的比例，而包围盒通常不是正方形（本仓库的色卡是 430×47）。
在比例空间里做旋转，等于在像素空间里做**非均匀缩放后再旋转**——一个 45° 的
矩形会变成平行四边形。因此所有旋转相关计算都必须带上 `aspect = 高/宽`，
先把比例换算到像素尺度、转完再换回来。
"""

from __future__ import annotations

import math

# 形状种类。`none` 排在最前：它是「不启用形状」的默认值。
MASK_NONE = "none"
MASK_RECT = "rect"
MASK_CIRCLE = "circle"
MASK_SECTOR = "sector"
MASK_POLYGON = "polygon"

MASK_SHAPES = (MASK_NONE, MASK_RECT, MASK_CIRCLE, MASK_SECTOR, MASK_POLYGON)

# 各形状需要的顶点数下限（相对包围盒的坐标）。
_MIN_POINTS = {
    MASK_NONE: 0,
    MASK_RECT: 2,
    MASK_CIRCLE: 1,
    MASK_SECTOR: 1,
    MASK_POLYGON: 3,
}

# 相对比例保留的小数位，与 model 的 `_RATIO_DIGITS` 一致。
_RATIO_DIGITS = 6

# 坐标允许超出包围盒的倍数。见模块文档「为什么坐标不夹在 0–1」。
_COORD_OVERSHOOT = 8.0

# 拖拽模式。与「控制点下标」是两套概念：控制点是离散的可拖点，模式是
# 「这次拖动要改什么」——同一处按下在不同形状下的语义完全不同。
GRAB_NONE = "none"
GRAB_MOVE = "move"
GRAB_RESIZE = "resize"
GRAB_ROTATE = "rotate"
GRAB_SWEEP = "sweep"

# 中心旋转区的半径（以容差为单位）。必须大于 1：容差是「贴着边/角」的判定
# 宽度，而中心区要能被稳定点中，用同样的宽度会让它小到几乎点不到。
_CENTER_ZONE = 2.0

# 扇形半径边的最大角度容差（度）。
#
# 线性容差在小半径上会换算出 70° 这种值（`atan2(0.003/0.005)`）。角度容差一旦
# 大到覆盖大半个扇形，两条半径边就互相「吃掉」，只剩一条能调——用户看到的
# 现象是「张角只有单边能调」，而完全想不到是容差换算的问题。
_MAX_EDGE_ANGLE_TOLERANCE = 25.0

# 扇形半径边判定所需的**最小径向距离**（相对半径的比例）。
#
# 贴着圆心的点方位角不稳定：同一个像素的抖动能让角度跳几十度，且按小半径
# 换算出的角度容差极大（`atan2(tol/0.005)` ≈ 60°）。两者叠加的后果是「整个
# 扇形内部都被判成拖边」，用户想在里面拖动移动，却总在改张角。
# 只在外侧 30% 半径范围内认边，中心区域留给移动。
_SECTOR_EDGE_MIN_RATIO = 0.3

# 圆/扇形的初始半径（相对短边的比例）。0.7 表示占 70% 短边。
#
# 早期取 0.5（贴边）。贴边的形状在宽扁模板上只有 23px 高，用户既看不清圆弧、
# 也几乎点不中「圆弧带」（带本身就 8px），交互形同虚设。留一圈余量后形状
# 明显、拖动余量也够。
_DEFAULT_RADIUS_RATIO = 0.7

# 多边形的初始半宽/半高（相对包围盒）。
#
# 0.4 表示内缩 20%：既明显（占 80% 面积）又留出「往里拖收窄」的空间。
_POLYGON_DEFAULT_HALF = 0.4

# 矩形的边/角标识。用字符串而不是下标：下标会随角点数变化而漂移，
# 而「拖右边」这个意图是不变的。
RECT_LEFT = "left"
RECT_RIGHT = "right"
RECT_TOP = "top"
RECT_BOTTOM = "bottom"
RECT_TL = "tl"
RECT_TR = "tr"
RECT_BL = "bl"
RECT_BR = "br"


class MaskShapeError(ValueError):
    """掩码形状参数非法。"""


def _round_ratio(value):
    """把相对比例规整到固定小数位。

    Args:
        value: 浮点数。

    Returns:
        规整后的浮点数。
    """
    return round(float(value), _RATIO_DIGITS)


def _normalize_angle(value):
    """把角度归一到 [0, 360)。

    Args:
        value: 角度（度）。

    Returns:
        归一后的角度。
    """
    return float(value) % 360.0


def _clamp_coord(value):
    """把坐标夹到允许范围内。

    Args:
        value: 坐标值。

    Returns:
        夹取后的浮点数。
    """
    return min(_COORD_OVERSHOOT, max(-_COORD_OVERSHOOT, float(value)))


def _rotate_scaled(px, py, cx, cy, angle_deg, aspect):
    """在「像素尺度」下绕中心旋转一个比例坐标点。

    Args:
        px: 点 x（相对包围盒）。
        py: 点 y（相对包围盒）。
        cx: 中心 x。
        cy: 中心 y。
        angle_deg: 旋转角（度），顺时针为正（y 轴向下）。
        aspect: 高 / 宽。

    Returns:
        `(x, y)` 比例坐标。

    Note:
        比例空间不是等比的（宽高比通常 ≠ 1），直接对比例做旋转会把矩形拉成
        平行四边形。这里先把 y 乘 `aspect` 换到「宽度为 1 的像素尺度」，
        转完再除回来。
    """
    if abs(angle_deg) < 1e-12 or aspect <= 0:
        return px, py
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    dx = px - cx
    dy = (py - cy) * aspect
    rx = dx * cos_a - dy * sin_a
    ry = dx * sin_a + dy * cos_a
    return cx + rx, cy + ry / aspect


class MaskShape:
    """模板掩码形状（坐标相对特征包围盒，0–1）。

    Attributes:
        kind: 形状种类，取 `MASK_SHAPES` 之一。
        points: 顶点序列，含义随 `kind` 变：
            - `rect`：两个对角点（未旋转时的左上、右下）
            - `circle` / `sector`：圆心一个点
            - `polygon`：任意 ≥3 个顶点
            - `none`：空
        angle: 旋转角（度），`rect` 与 `sector` 使用。顺时针为正。
            对 `rect` 是绕自身中心的旋转，对 `sector` 是起始边方向。
        sweep: 扇形张角（度），仅 `sector` 使用。
        radius: 圆/扇形半径，**相对包围盒宽度**，仅这两类使用。
    """

    def __init__(self, kind=MASK_NONE, points=None, angle=0.0, sweep=90.0, radius=0.0):
        """构造并校验。

        Args:
            kind: 形状种类。
            points: 顶点序列，每点为 `(x, y)`，相对包围盒的比例。
            angle: 旋转角/起始角，归一到 [0, 360)。
            sweep: 扇形张角，限 (0, 360]。
            radius: 圆/扇形半径（相对包围盒宽度）。

        Raises:
            MaskShapeError: 种类未知、点数不足、坐标超出 ±8 倍或参数非法。
        """
        if kind not in MASK_SHAPES:
            raise MaskShapeError(f"未知的掩码形状 {kind!r}，只支持 {MASK_SHAPES}")
        self.kind = kind
        self.points = [(_round_ratio(x), _round_ratio(y)) for x, y in (points or [])]
        for x, y in self.points:
            if not -_COORD_OVERSHOOT <= x <= _COORD_OVERSHOOT:
                raise MaskShapeError(f"掩码形状 x 超出允许范围 ±{_COORD_OVERSHOOT:g}，收到 {x}")
            if not -_COORD_OVERSHOOT <= y <= _COORD_OVERSHOOT:
                raise MaskShapeError(f"掩码形状 y 超出允许范围 ±{_COORD_OVERSHOOT:g}，收到 {y}")
        need = _MIN_POINTS[kind]
        if len(self.points) < need:
            raise MaskShapeError(f"{kind} 至少需要 {need} 个点，收到 {len(self.points)} 个")

        self.angle = _normalize_angle(angle)
        self.sweep = 0.0
        self.radius = 0.0
        if kind == MASK_SECTOR:
            try:
                self.sweep = float(sweep)
            except (TypeError, ValueError) as error:
                raise MaskShapeError(f"扇角必须是数值，收到 {sweep!r}") from error
            if not 0.0 < self.sweep <= 360.0:
                raise MaskShapeError(f"扇角必须落在 (0, 360]，收到 {self.sweep}")

        if kind in (MASK_CIRCLE, MASK_SECTOR):
            try:
                self.radius = float(radius)
            except (TypeError, ValueError) as error:
                raise MaskShapeError(f"半径必须是数值，收到 {radius!r}") from error
            if self.radius <= 0.0:
                raise MaskShapeError(f"半径必须为正，收到 {self.radius}")

        if kind == MASK_RECT:
            x1, y1 = self.points[0]
            x2, y2 = self.points[1]
            if x2 <= x1 or y2 <= y1:
                raise MaskShapeError(f"矩形的对角两点必须满足 x2>x1 且 y2>y1，收到 {self.points}")

    # --- 构造辅助 -----------------------------------------------------------

    @classmethod
    def none(cls):
        """返回「无形状」。

        Returns:
            `MaskShape` 实例。
        """
        return cls(MASK_NONE)

    @classmethod
    def full_rect(cls):
        """返回覆盖整个包围盒的矩形。

        Returns:
            `MaskShape` 实例。
        """
        return cls(MASK_RECT, [(0.0, 0.0), (1.0, 1.0)])

    @classmethod
    def centered_circle(cls, aspect=1.0, radius=0.45):
        """返回居中的圆。

        Args:
            aspect: 包围盒高宽比（height / width）。
            radius: 相对包围盒宽度的半径。

        Returns:
            `MaskShape` 实例。
        """
        del aspect
        return cls(MASK_CIRCLE, [(0.5, 0.5)], radius=radius)

    @classmethod
    def centered_sector(cls, angle=0.0, sweep=90.0, radius=0.9):
        """返回居中的扇形。

        Args:
            angle: 起始角（度）。
            sweep: 张角（度）。
            radius: 相对包围盒宽度的半径。

        Returns:
            `MaskShape` 实例。
        """
        return cls(MASK_SECTOR, [(0.5, 0.5)], angle=angle, sweep=sweep, radius=radius)

    @classmethod
    def full_polygon(cls):
        """返回铺满整个包围盒的多边形（多边形的首次初始形状）。

        用「四角铺满」而不是菱形：切换过来时用户要的是「先有个能用的形状」，
        铺满意味着此时掩码等价于矩形，他再从四角往里拖就能收窄。给菱形会让
        掩码面积骤降到一半，而用户不一定想要那个形状。

        Returns:
            `MaskShape` 实例。
        """
        return cls(MASK_POLYGON, [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])

    @classmethod
    def inset_polygon(cls, half=_POLYGON_DEFAULT_HALF):
        """返回内缩一圈的多边形（多边形的默认初始形状）。

        Args:
            half: 半宽/半高（相对包围盒）。

        Returns:
            `MaskShape` 实例。

        Note:
            **不能铺满**（`full_polygon`）也不能太小的原因不同：

            - 铺满时覆盖率 100%，与 `rect` 完全等价，用户看到的是「切过来什么
              都没变」，找不到可以拖的顶点；
            - 太小（早期版本在宽扁模板上只有 1.4%）时几乎看不见形状，用户以为
              「多边形没生效」，也就无从拖拽。

            内缩 20% 两头都避开：形状明显、四条边都看得见，往里往外都有空间。
        """
        half = max(1e-3, min(0.98, float(half)))
        cx, cy = 0.5, 0.5
        return cls(
            MASK_POLYGON,
            [
                (cx - half, cy - half),
                (cx + half, cy - half),
                (cx + half, cy + half),
                (cx - half, cy + half),
            ],
        )

    @classmethod
    def diamond(cls):
        """返回菱形多边形。

        用菱形而不是矩形：矩形与 `rect` 完全重合，用户看不出「现在已经是多边形
        模式」，也就找不到可以拖的顶点。菱形一望即知是可编辑的多边形。

        Returns:
            `MaskShape` 实例。
        """
        return cls(MASK_POLYGON, [(0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5)])

    @classmethod
    def from_kind(cls, kind, aspect=1.0):
        """按种类生成一个可用的初始形状。

        Args:
            kind: 目标种类。
            aspect: 包围盒高宽比，用于把圆限制在盒内。

        Returns:
            `MaskShape` 实例。

        Note:
            「切换形状类型时自动给一个初始形状」而不是留空：留空会让用户面对一块
            什么都没有的图，得先猜「怎么开始画」。自动给一个居中的形状，最坏情况
            只是他不满意再拖，比面对空白好。
        """
        if kind == MASK_NONE:
            return cls.none()
        if kind == MASK_RECT:
            return cls.full_rect()
        radius = cls._radius_for_aspect(aspect)
        if kind == MASK_CIRCLE:
            return cls(MASK_CIRCLE, [(0.5, 0.5)], radius=radius)
        if kind == MASK_SECTOR:
            return cls(MASK_SECTOR, [(0.5, 0.5)], radius=radius)
        return cls.inset_polygon()

    @staticmethod
    def _radius_for_aspect(aspect, half_ratio=_DEFAULT_RADIUS_RATIO):
        """按包围盒比例算一个合适的初始半径。

        Args:
            aspect: 高 / 宽。
            half_ratio: 期望占据的高度比例的一半。默认 0.7 表示「占据 70% 的
                短边」，比贴边留一圈余量——用户拿到一个能看清、还有拖动空间的形状。

        Returns:
            相对**宽度**的半径。

        Note:
            计算方向容易写反，这里固定写法：`half_ratio × aspect` 才是
            「高度方向的比例换算成宽度方向」。例如高 47、宽 430、`half_ratio=0.7`
            时得到 0.7 × 0.109 = 0.076，即 32.8px。

            长边方向也设上限 `half_ratio`：正方形模板上半径就等于 `half_ratio`，
            不会因为 `aspect=1` 就跳到 1.0（那会整幅铺满，反而看不出是圆）。
        """
        if aspect <= 0:
            return half_ratio
        return max(1e-4, min(half_ratio, half_ratio * aspect))

    def with_kind(self, kind, aspect=1.0):
        """换成另一种形状，尽量保留当前几何。

        Args:
            kind: 目标种类。
            aspect: 包围盒高宽比。

        Returns:
            新的 `MaskShape`。

        Note:
            **不保留顶点**：不同形状的顶点语义完全不同（矩形是 2 个对角点、圆是
            1 个圆心、多边形是 N 个轮廓点），强行映射只会得到怪形状。保留的是
            「大致位置」——圆心取原包围盒中心，半径取短边一半。
        """
        if kind == self.kind:
            return self
        if kind == MASK_NONE:
            return self.none()
        if self.kind != MASK_NONE:
            cx, cy = self.center()
            x1, y1, x2, y2 = self.bounds()
            half_w = max(0.02, (x2 - x1) / 2.0)
            half_h = max(0.02, (y2 - y1) / 2.0)
        else:
            cx, cy, half_w, half_h = 0.5, 0.5, 0.5, 0.5
        if kind == MASK_RECT:
            return MaskShape(
                MASK_RECT,
                [(cx - half_w, cy - half_h), (cx + half_w, cy + half_h)],
                angle=self.angle if self.kind == MASK_RECT else 0.0,
            )
        # 圆与扇形：半径以**宽度**为基准（与 `mask()` 的口径一致），下限与上限
        # 见 `_radius_for_aspect`。
        #
        # **尺寸只在「上一个形状是圆/扇形」时才沿用它的半径**，其余情况一律用
        # 默认值。原因：圆的 `bounds()` 是按半径算的小盒，把一个半径 0.055 的圆
        # 切到扇形时，若再拿这个 0.055 当半高去算半径，会得到 0.006 —— 连切几次
        # 形状就缩成一个点，而用户完全看不出是哪一步导致的。
        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            radius = max(1e-4, self.radius)
        else:
            radius = self._radius_for_aspect(aspect)
        if kind == MASK_CIRCLE:
            return MaskShape(MASK_CIRCLE, [(cx, cy)], radius=radius)
        if kind == MASK_SECTOR:
            return MaskShape(
                MASK_SECTOR,
                [(cx, cy)],
                angle=self.angle if self.kind == MASK_SECTOR else 0.0,
                sweep=self.sweep if self.kind == MASK_SECTOR else 90.0,
                radius=radius,
            )
        # 多边形：以包围盒内缩一圈的四角为初始顶点。
        #
        # **不用圆的半径撑盒**：宽扁模板上那个盒只有 11% 宽（色卡 430×47 时
        # 覆盖率 1.4%），用户切到多边形后几乎看不见形状，也就无从拖拽。
        # 「内缩 20% 的包围盒」在各种模板比例下都足够大，且保留了「边界在哪」
        # 这个信息——用户往里拖就是收窄。
        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            half_w = half_h = _POLYGON_DEFAULT_HALF
        return MaskShape(
            MASK_POLYGON,
            [
                (cx - half_w, cy - half_h),
                (cx + half_w, cy - half_h),
                (cx + half_w, cy + half_h),
                (cx - half_w, cy + half_h),
            ],
        )

    # --- 几何 ---------------------------------------------------------------

    def center(self):
        """返回形状中心（旋转与移动都以它为基准）。

        Returns:
            `(x, y)` 相对比例。

        Note:
            多边形用**顶点平均值**而不是包围盒中心：凹多边形的包围盒中心可能
            落在形状外，用它当旋转中心会让「绕中心转」看起来像在乱飘。
        """
        if self.kind == MASK_NONE:
            return 0.5, 0.5
        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            return self.points[0]
        if self.kind == MASK_POLYGON:
            count = len(self.points)
            return (
                sum(point[0] for point in self.points) / count,
                sum(point[1] for point in self.points) / count,
            )
        x1, y1 = self.points[0]
        x2, y2 = self.points[1]
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def bounds(self):
        """返回形状的相对包围盒（不含旋转，即未旋转时的外接盒）。

        Returns:
            `(x1, y1, x2, y2)` 相对比例。

        Note:
            矩形返回其**旋转前**的对角矩形，而不是旋转后的外接盒。旋转后的外接盒
            会随角度变大，用它做「切换形状时保留位置」的基准会让形状越转越大。
        """
        if self.kind == MASK_NONE:
            return 0.0, 0.0, 1.0, 1.0
        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            cx, cy = self.points[0]
            return (
                cx - self.radius,
                cy - self.radius,
                cx + self.radius,
                cy + self.radius,
            )
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def corners(self, aspect=1.0):
        """返回矩形的四角（**已应用旋转**）。

        Args:
            aspect: 高 / 宽，用于在像素尺度下旋转。

        Returns:
            四个 `(x, y)`；非矩形返回 `outline()`。

        Note:
            旋转必须在这里就完成而不是留给调用方：画布画轮廓、`mask()` 生成掩码
            都要用同一份角点，两处各算一次必然分叉，而分叉的表现是「画出来的框
            与实际的掩码对不上」。
        """
        if self.kind != MASK_RECT:
            return list(self.outline())
        x1, y1 = self.points[0]
        x2, y2 = self.points[1]
        base = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        if abs(self.angle) < 1e-9:
            return base
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        return [_rotate_scaled(px, py, cx, cy, self.angle, aspect) for px, py in base]

    def outline(self):
        """返回**未旋转**的轮廓顶点（相对包围盒）。

        Returns:
            `[(x, y), ...]`。

        Note:
            **矩形的两个对角点必须展开成四个角**。`cv2.fillPoly` 至少要 3 个点
            才画得出面，只给 2 个点会退化成画一条线——掩码覆盖率从 100% 掉到 1%，
            而界面上看不出任何异常，只表现为「这个特征从来匹配不到」。

            需要已旋转的角点请用 `corners()`。
        """
        if self.kind == MASK_RECT:
            x1, y1 = self.points[0]
            x2, y2 = self.points[1]
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        return list(self.points)

    def handles(self, aspect=1.0):
        """返回可拖动的控制点（相对包围盒）。

        Args:
            aspect: 高 / 宽，矩形旋转换算用。

        Returns:
            `[(x, y), ...]`。圆与扇形返回 `[圆心, 右边界点]`，矩形按
            「左上、右上、右下、左下」返回四角（已应用旋转），多边形返回全部顶点。

        Note:
            这个方法是**模型层的数据接口**，不等于「界面上要画几个点」。界面上
            圆与矩形不画点（见 `draw_handles()`），而是靠「贴着边/弧就拖」的
            命中判定，与场景编辑器的手感一致。

            矩形必须带上 `aspect` 才能算出正确的旋转角点。早期实现硬编码
            `aspect=1.0`，宽扁模板上转 45° 后返回的角点与画布绘制的角点不重合，
            表现为「按位置找手柄找不到」。
        """
        if self.kind == MASK_NONE:
            return []
        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            cx, cy = self.points[0]
            return [(cx, cy), (cx + self.radius, cy)]
        if self.kind == MASK_RECT:
            return self.corners(aspect)
        return list(self.points)

    def draw_handles(self):
        """返回界面上应当画出来的控制点。

        Returns:
            `[(x, y), ...]`。

        Note:
            **只有多边形画点**。矩形的四角、圆的圆周都不画点：
            - 矩形有点会让人以为「必须抓住那个点才能改大小」，而实际是贴着边就能拖；
            - 圆周上画一个半径点，在宽扁模板上它会离圆很远（半径按宽度算），
              看起来像浮在形状外面的一个孤立白点。

            多边形必须画：它的顶点是真正的编辑对象，不画就无从下手。
        """
        if self.kind == MASK_POLYGON:
            return list(self.points)
        return []

    def contains(self, x, y, tolerance=0.0, aspect=1.0):
        """判断一点是否落在形状内（画布命中测试用）。

        Args:
            x: 相对包围盒 x。
            y: 相对包围盒 y。
            tolerance: 容差（**以宽度为单位**的比例）。
            aspect: 高 / 宽，用于把 y 换算到等比空间。

        Returns:
            True 表示命中。

        Note:
            距离在**等比空间**里算：`hypot(Δx, Δy × aspect)`。直接在比例空间
            算距离会让宽扁包围盒（色卡 430×47）上下方向的距离被放大 9 倍——
            同一个像素距离，纵向算出 0.028、横向算出 0.003，容差判定随之失真。

            `none` 恒为 True：它代表整个包围盒，点哪都算在形状内。
        """
        if self.kind == MASK_NONE:
            return True
        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            cx, cy = self.points[0]
            distance = math.hypot(x - cx, (y - cy) * aspect)
            if distance > self.radius + tolerance:
                return False
            if self.kind == MASK_CIRCLE:
                return True
            if distance <= tolerance:
                return True
            degrees = self.point_angle(x, y, aspect)
            return (degrees - self.angle) % 360.0 <= self.sweep
        if self.kind == MASK_RECT:
            return _point_in_polygon(x, y, self.corners(aspect))
        return _point_in_polygon(x, y, self.points)

    def point_angle(self, x, y, aspect=1.0):
        """返回某点相对中心的方位角（度，顺时针，y 轴向下）。

        Args:
            x: 相对包围盒 x。
            y: 相对包围盒 y。
            aspect: 高 / 宽。

        Returns:
            `[0, 360)` 的角度。

        Note:
            必须按 `aspect` 换算：宽扁包围盒里，「中心右侧 0.1」与「中心下方 0.1」
            在像素上的距离差 4 倍多，不换算会让旋转手柄的手感严重偏离视觉。
        """
        cx, cy = self.center()
        return math.degrees(math.atan2((y - cy) * max(aspect, 1e-9), x - cx)) % 360.0

    def nearest_edge(self, x, y, tolerance):
        """找离给定点最近的边（多边形加顶点用）。

        Args:
            x: 相对包围盒 x。
            y: 相对包围盒 y。
            tolerance: 判定「贴着某条边」的最大距离。超出时返回 None。

        Returns:
            `(边序号, 该边上的投影点)` 二元组；不在任何边附近时返回 None。

        Note:
            **边序号 i 表示「顶点 i 到顶点 i+1」**，插入新顶点时就插在 i 与
            i+1 之间。返回投影点而不是原始点，是为了让新顶点正好落在线上——
            否则加完点之后多边形轮廓会跳一下。
        """
        if self.kind != MASK_POLYGON or len(self.points) < 2:
            return None
        best = None
        count = len(self.points)
        for index in range(count):
            ax, ay = self.points[index]
            bx, by = self.points[(index + 1) % count]
            px, py = _project_on_segment(x, y, ax, ay, bx, by)
            distance = math.hypot(x - px, y - py)
            if distance <= tolerance and (best is None or distance < best[0]):
                best = (distance, index, (_round_ratio(px), _round_ratio(py)))
        if best is None:
            return None
        return best[1], best[2]

    def handle_at(self, x, y, tolerance, aspect=1.0):
        """找离给定点最近的控制点。

        Args:
            x: 相对包围盒 x。
            y: 相对包围盒 y。
            tolerance: 最大距离。
            aspect: 高 / 宽，矩形旋转换算用。

        Returns:
            控制点下标；没有命中的返回 None。
        """
        best_index = None
        best_distance = None
        for index, (hx, hy) in enumerate(self.handles(aspect)):
            distance = math.hypot(x - hx, y - hy)
            if distance <= tolerance and (best_distance is None or distance < best_distance):
                best_index = index
                best_distance = distance
        return best_index

    # --- 拖拽判定 -----------------------------------------------------------

    def grab_at(self, x, y, tolerance, aspect=1.0):
        """判断在 `(x, y)` 按下时应当执行哪种编辑。

        Args:
            x: 相对包围盒 x。
            y: 相对包围盒 y。
            tolerance: 容差，**以宽度为单位**的比例。调用方按
                `像素容差 / (宽 × 缩放)` 换算后传入，两轴天然一致。
            aspect: 高 / 宽。

        Returns:
            `(mode, detail)`。`mode` 为 `GRAB_*` 之一，`detail` 含义随模式：
            - `GRAB_MOVE` / `GRAB_ROTATE`：None
            - `GRAB_RESIZE`：矩形是边标识（`RECT_*`），圆/扇形是 `"radius"`，
              多边形是顶点下标
            - `GRAB_SWEEP`：`"start"` 或 `"end"`（拖扇形的两个半径边）
            - `GRAB_NONE`：不处理（点空白处，交给画布平移视图）

        Note:
            **容差是单标量，不是两轴各一个**。早期实现分 x/y 传入，在宽扁包围盒
            上必须取两者之一，而两者差 9 倍：取大者让圆弧的判定区间覆盖半径的
            一半（整个圆内部都被判成「改半径」，于是圆无法拖动），取小者让纵向
            几乎点不中。统一到「以宽度为单位」的等比空间后，两轴一致，
            不会再出现这种失真。

            **圆没有中心旋转区**。旋转对圆没有可观察效果，给它中心区只会抢走
            移动操作——用户想在圆心附近拖动移动整个圆，却被判成「旋转」，而
            旋转后画面毫无变化，看起来就是「圆拖不动」。

            判定顺序：中心旋转（仅矩形/扇形）→ 边/弧 → 内部移动 → 空白。
        """
        if self.kind == MASK_NONE:
            return GRAB_NONE, None

        cx, cy = self.center()
        tol = max(float(tolerance), 1e-6)

        if self.kind == MASK_RECT:
            # 矩形的中心区是旋转。圆与扇形**没有**中心区——见下方说明。
            if math.hypot(x - cx, (y - cy) * aspect) <= tol * _CENTER_ZONE:
                return GRAB_ROTATE, None
            key = self.rect_edge_at(x, y, tol, aspect)
            if key is not None:
                return GRAB_RESIZE, key
            if self.contains(x, y, aspect=aspect):
                return GRAB_MOVE, None
            return GRAB_NONE, None

        if self.kind == MASK_CIRCLE:
            distance = math.hypot(x - cx, (y - cy) * aspect)
            # 圆弧带：**只在外侧判**（`distance >= radius - tol`）。内侧留给移动，
            # 否则半径小的圆（宽扁模板上常见 0.05）会被圆弧带吃掉大半个内部。
            if self.radius - tol <= distance <= self.radius + tol:
                return GRAB_RESIZE, "radius"
            if distance < self.radius:
                return GRAB_MOVE, None
            return GRAB_NONE, None

        if self.kind == MASK_SECTOR:
            # **先判半径边，再判圆弧**：两条半径边与圆弧在端点相交，先判圆弧
            # 会把「拖起始边」误判成「改半径」。
            which = self.sector_edge_at(x, y, tol, aspect)
            if which is not None:
                return GRAB_SWEEP, which
            distance = math.hypot(x - cx, (y - cy) * aspect)
            if self.radius - tol <= distance <= self.radius + tol:
                return GRAB_RESIZE, "radius"
            if self.contains(x, y, aspect=aspect):
                return GRAB_MOVE, None
            return GRAB_NONE, None

        # 多边形：顶点优先（它落在边上），再判内部移动
        for index, (vx, vy) in enumerate(self.points):
            if math.hypot(x - vx, (y - vy) * aspect) <= tol:
                return GRAB_RESIZE, index
        if _point_in_polygon(x, y, self.points):
            return GRAB_MOVE, None
        return GRAB_NONE, None

    def rect_edge_at(self, x, y, tolerance, aspect=1.0):
        """判断点是否贴着矩形的某条边或某个角。

        Args:
            x: 相对包围盒 x。
            y: 相对包围盒 y。
            tolerance: 容差（以宽度为单位）。
            aspect: 高 / 宽。

        Returns:
            `RECT_*` 之一；都不贴时返回 None。

        Note:
            **先把点转回矩形的未旋转坐标系再判定**。否则矩形转 45° 后，用户
            看到的是斜边，而判定还在水平的「上下左右」上——表现为「鼠标明明在边上，
            却拖不动」。

            判定在**等比空间**里做（`ly` 已乘 `aspect` 换到宽度尺度），
            否则纵向容差会被放大 9 倍，宽扁矩形上「离上边很远」也判成贴边。
        """
        if self.kind != MASK_RECT:
            return None
        x1, y1 = self.points[0]
        x2, y2 = self.points[1]
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        lx, ly = _rotate_scaled(x, y, cx, cy, -self.angle, aspect)
        tol = max(float(tolerance), 1e-6)
        # 换到等比空间后比较：纵向坐标乘 aspect
        ly_u = ly * aspect
        y1_u = y1 * aspect
        y2_u = y2 * aspect
        tol_u = tol

        near_left = abs(lx - x1) <= tol_u and (y1_u - tol_u) <= ly_u <= (y2_u + tol_u)
        near_right = abs(lx - x2) <= tol_u and (y1_u - tol_u) <= ly_u <= (y2_u + tol_u)
        near_top = abs(ly_u - y1_u) <= tol_u and (x1 - tol_u) <= lx <= (x2 + tol_u)
        near_bottom = abs(ly_u - y2_u) <= tol_u and (x1 - tol_u) <= lx <= (x2 + tol_u)

        # 角优先：角的两个方向都命中时，若先判边就永远拖不到角
        if near_top and near_left:
            return RECT_TL
        if near_top and near_right:
            return RECT_TR
        if near_bottom and near_left:
            return RECT_BL
        if near_bottom and near_right:
            return RECT_BR
        if near_top:
            return RECT_TOP
        if near_bottom:
            return RECT_BOTTOM
        if near_left:
            return RECT_LEFT
        if near_right:
            return RECT_RIGHT
        return None

    def sector_edge_at(self, x, y, tolerance, aspect=1.0):
        """判断点是否贴着扇形的两条半径边。

        Args:
            x: 相对包围盒 x。
            y: 相对包围盒 y。
            tolerance: 容差（以宽度为单位）。
            aspect: 高 / 宽。

        Returns:
            `"start"` / `"end"` / None。

        Note:
            距离与角度都在**等比空间**里算（`Δy × aspect`）。早期实现用未换算的
            `hypot(Δx, Δy)` 求角度容差，在宽扁模板上算出 70°——整个扇形内部
            都被判成「拖起始边」，而另一边永远点不中。这是「张角只有单边能调」
            的直接原因。
        """
        if self.kind != MASK_SECTOR:
            return None
        cx, cy = self.points[0]
        distance = math.hypot(x - cx, (y - cy) * aspect)
        tol = max(float(tolerance), 1e-6)
        if distance > self.radius + tol or distance <= 1e-9:
            return None
        degrees = self.point_angle(x, y, aspect)
        start = self.angle % 360.0
        end = (self.angle + self.sweep) % 360.0
        # 角度容差还要受**半径方向距离的下限**约束：贴着圆心的点角度不稳定
        # （同一像素的抖动能让方位角跳几十度），用小半径算出的巨大会让整个
        # 扇形内部都被判成「拖边」。这里按「离圆心至少一定距离」来限制。
        if distance < self.radius * _SECTOR_EDGE_MIN_RATIO:
            return None
        tol_deg = self._tolerance_degrees(tol, distance)
        delta_start = _angle_delta(degrees, start)
        delta_end = _angle_delta(degrees, end)
        # 取**更近的那条边**：两条边夹角很小时（窄扇形），先判 start 会让
        # end 永远点不中——用户看到「只能调一边」。
        if delta_start <= tol_deg and delta_start <= delta_end:
            return "start"
        if delta_end <= tol_deg:
            return "end"
        return None

    @staticmethod
    def _tolerance_degrees(tolerance, distance):
        """把线性容差换算成角度容差。

        Args:
            tolerance: 线性容差（以宽度为单位）。
            distance: 该点到圆心的距离（等比空间）。

        Returns:
            角度容差（度）。

        Note:
            `atan2(容差/半径)` 才是「这个像素宽度对应多少度」。直接给固定角度
            （如 5°）会在大半径时太窄、小半径时太宽——同一个容差在小扇形上
            能覆盖半个形状。

            上限压到 25°：线性容差在小半径上会算出 70° 这种荒谬值，而那种
            值一旦生效，扇形的两条边就互相「吃掉」，只剩一条能调。
        """
        if distance <= 1e-9:
            return 25.0
        ratio = tolerance / distance
        return min(_MAX_EDGE_ANGLE_TOLERANCE, math.degrees(math.atan2(ratio, 1.0)))

    # --- 编辑 ---------------------------------------------------------------

    def moved(self, index, x, y, aspect=1.0):
        """返回把第 `index` 个控制点移到 `(x, y)` 后的新形状。

        Args:
            index: 控制点下标（`handles()` 的顺序）。
            x: 新的相对 x。
            y: 新的相对 y。
            aspect: 高 / 宽，矩形旋转时的坐标换算。

        Returns:
            新的 `MaskShape`。

        Raises:
            MaskShapeError: 下标越界，或移动后形状退化（如矩形宽高归零）。

        Note:
            返回新实例而不是原地改：形状是不可变值，原地改会让「撤销/取消」
            这类操作无从实现，也让「改到一半失败」留下半个坏形状。

            坐标只夹到 ±8 倍，不夹在 0–1：见模块文档「为什么坐标不夹在 0–1」。
        """
        handles = self.handles(aspect)
        if not 0 <= index < len(handles):
            raise MaskShapeError(f"控制点下标越界：{index}（共 {len(handles)} 个）")
        x = _clamp_coord(x)
        y = _clamp_coord(y)

        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            if index == 0:
                return MaskShape(self.kind, [(x, y)], self.angle, self.sweep, self.radius)
            cx, cy = self.points[0]
            radius = max(1e-4, math.hypot(x - cx, (y - cy) * aspect))
            return MaskShape(self.kind, [(cx, cy)], self.angle, self.sweep, radius)

        if self.kind == MASK_RECT:
            # 四角按「左上、右上、右下、左下」排列，正好对应 `RECT_*` 四个角。
            # 交给 `resized` 做换算——它已经包含「转回未旋转坐标系」这一步，
            # 这里再转一次会把角度抵消掉，表现为「旋转后拖角，矩形不跟着鼠标」。
            edge = (RECT_TL, RECT_TR, RECT_BR, RECT_BL)[index]
            return self.resized(edge, x, y, aspect)

        points = list(self.points)
        points[index] = (x, y)
        return MaskShape(self.kind, points, self.angle, self.sweep, self.radius)

    def translated(self, dx, dy):
        """整体平移到新位置。

        Args:
            dx: x 偏移（相对比例）。
            dy: y 偏移（相对比例）。

        Returns:
            新的 `MaskShape`。
        """
        if self.kind == MASK_NONE:
            return self
        points = [(_clamp_coord(x + dx), _clamp_coord(y + dy)) for x, y in self.points]
        return MaskShape(self.kind, points, self.angle, self.sweep, self.radius)

    def rotated(self, angle):
        """设置旋转角。

        Args:
            angle: 新的角度（度），会归一到 [0, 360)。

        Returns:
            新的 `MaskShape`。

        Note:
            圆没有可感知的旋转（转了也看不出来），但仍保留角度值，避免切换形状
            类型时丢掉用户已调好的角度。
        """
        if self.kind == MASK_NONE:
            return self
        return MaskShape(self.kind, self.points, angle, self.sweep, self.radius)

    def resized(self, detail, x, y, aspect=1.0):
        """按边/角/半径把形状调整到新的位置。

        Args:
            detail: `grab_at` 返回的 detail。
            x: 鼠标当前相对 x。
            y: 鼠标当前相对 y。
            aspect: 高 / 宽，矩形旋转换算用。

        Returns:
            新的 `MaskShape`。

        Raises:
            MaskShapeError: 调整后形状退化。

        Note:
            `detail` 是**按下时**判定的那个边/角，整个拖动过程固定不变。若每帧
            重新判定，鼠标一旦滑出容差范围就会「松开原来那条边、改去拖另一条」，
            形状会跳。

            **矩形拖角时对角固定、被拖的角精确落在鼠标上**。这是主流图形编辑器
            的手感：用户抓着一个角拖，期待那个角跟着鼠标，而对面不动。若绕中心
            改边界，旋转后的矩形会因为中心同时移动而让角偏离鼠标——表现为
            「拖角不跟手」，且偏移量随角度增大，很难归因。
        """
        if self.kind == MASK_NONE:
            return self
        x = _clamp_coord(x)
        y = _clamp_coord(y)

        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            cx, cy = self.points[0]
            radius = max(1e-4, math.hypot(x - cx, (y - cy) * aspect))
            return MaskShape(self.kind, [(cx, cy)], self.angle, self.sweep, radius)

        if self.kind == MASK_RECT:
            return self._resized_rect(detail, x, y, aspect)

        # 多边形：拖某个顶点
        try:
            index = int(detail)
        except (TypeError, ValueError) as error:
            raise MaskShapeError(f"多边形的 detail 必须是顶点下标，收到 {detail!r}") from error
        if not 0 <= index < len(self.points):
            raise MaskShapeError(f"顶点下标越界：{index}")
        points = list(self.points)
        points[index] = (x, y)
        return MaskShape(MASK_POLYGON, points)

    def _resized_rect(self, detail, x, y, aspect):
        """矩形缩放：对角/对边固定，被拖的那一侧落到鼠标上。

        Args:
            detail: `RECT_*` 之一。
            x: 鼠标相对 x。
            y: 鼠标相对 y。
            aspect: 高 / 宽。

        Returns:
            新的 `MaskShape`。

        Note:
            **拖角与拖边用两套算法，因为几何约束不同**：

            - **拖角**：对角视觉位置 `A` 固定，被拖角必须落到鼠标 `M`。
              矩形中心对称 → 新中心 = `(A + M) / 2`；新半宽/半高由对角向量
              `D = M − A` 在矩形自身坐标系下的分量给出（`R⁻¹·D` 的绝对值的一半）。
              用「绕中心改边界」的近似会让角偏离鼠标，且偏移随角度增大——
              表现为「拖角不跟手」。
            - **拖边**：对边中点固定，整条边沿**法向**平移。把 `M − A` 投影到
              法向上得到位移量，横向分量忽略。这符合主流编辑器手感：拖旋转矩形
              的边时，边沿垂直方向移动，而不是「右边界 x 等于鼠标 x」。

            坐标先在**像素尺度**（`v = y × aspect`）里算，再换回比例：比例空间
            不是等比的，直接在比例上做旋转/投影会让宽扁模板上的角度严重失真。
        """
        x1, y1 = self.points[0]
        x2, y2 = self.points[1]
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        half_w = (x2 - x1) / 2.0
        half_h = (y2 - y1) / 2.0

        # 固定点（未旋转坐标）+ 参与变化的轴。拖角固定对角、拖边固定对边。
        anchors = {
            RECT_TL: ((x2, y2), True, True),
            RECT_TR: ((x1, y2), True, True),
            RECT_BR: ((x1, y1), True, True),
            RECT_BL: ((x2, y1), True, True),
            RECT_LEFT: ((x2, cy), True, False),
            RECT_RIGHT: ((x1, cy), True, False),
            RECT_TOP: ((cx, y2), False, True),
            RECT_BOTTOM: ((cx, y1), False, True),
        }
        if detail not in anchors:
            raise MaskShapeError(f"未知的矩形边标识 {detail!r}")
        (ax, ay), change_x, change_y = anchors[detail]

        scale_y = aspect if aspect > 0 else 1.0
        rad = math.radians(self.angle)
        cos_a, sin_a = math.cos(rad), math.sin(rad)

        # 固定点的视觉位置（比例坐标），再转到像素尺度算偏移
        avx, avy = _rotate_scaled(ax, ay, cx, cy, self.angle, aspect)
        dx = x - avx
        dy = (y - avy) * scale_y

        if change_x and change_y:
            # 拖角：R⁻¹ · D 给出新矩形的对角向量
            ddx = cos_a * dx + sin_a * dy
            ddy = -sin_a * dx + cos_a * dy
            new_half_w = max(1e-3, abs(ddx) / 2.0)
            new_half_h = max(1e-3, abs(ddy) / 2.0 / scale_y)
            new_cx = (avx + x) / 2.0
            new_cy = (avy + y) / 2.0
        else:
            # 拖边：把偏移投影到边的法向上，投影量就是**新的一整条边长**
            # （固定点在对面，所以从固定点到鼠标的距离就是新的宽/高）
            if change_x:
                nx, ny = (cos_a, sin_a) if detail == RECT_RIGHT else (-cos_a, -sin_a)
            else:
                nx, ny = (-sin_a, cos_a) if detail == RECT_BOTTOM else (sin_a, -cos_a)
            proj = dx * nx + dy * ny
            if change_x:
                new_half_w = max(1e-3, abs(proj) / 2.0)
                new_half_h = half_h
            else:
                new_half_w = half_w
                new_half_h = max(1e-3, abs(proj) / 2.0 / scale_y)
            new_cx = avx + (proj / 2.0) * nx
            new_cy = avy + (proj / 2.0) * ny / scale_y

        return MaskShape(
            MASK_RECT,
            [
                (new_cx - new_half_w, new_cy - new_half_h),
                (new_cx + new_half_w, new_cy + new_half_h),
            ],
            angle=self.angle,
        )

    def swept(self, which, x, y, aspect=1.0):
        """拖动扇形的某条半径边，调整起始角或张角。

        Args:
            which: `"start"` 或 `"end"`。
            x: 鼠标当前相对 x。
            y: 鼠标当前相对 y。
            aspect: 高 / 宽。

        Returns:
            新的 `MaskShape`。

        Note:
            拖起始边时**保持终止边不动**，即同时改 `angle` 与 `sweep`。若只改
            `angle`，终止边会跟着转，表现为「拖左边右边也动」——用户会觉得
            形状在乱跳。
        """
        if self.kind != MASK_SECTOR:
            return self
        degrees = self.point_angle(x, y, aspect)
        end_angle = (self.angle + self.sweep) % 360.0
        if which == "start":
            new_angle = degrees
            new_sweep = (end_angle - new_angle) % 360.0
        else:
            new_angle = self.angle
            new_sweep = (degrees - new_angle) % 360.0
        if new_sweep <= 0.5:
            # 拖过头：保持原状而不是翻转。翻转会让扇形突然变成 350°，
            # 而用户只是想把边拖到接近重合
            return self
        return MaskShape(MASK_SECTOR, self.points, new_angle, new_sweep, self.radius)

    def with_vertex_count(self, count):
        """把多边形重建成指定顶点数的正多边形。

        Args:
            count: 目标顶点数（≥3）。

        Returns:
            新的 `MaskShape`；非多边形或点数相同时返回自身。

        Raises:
            MaskShapeError: 顶点数少于 3。

        Note:
            **重建而不是增删顶点**。用户调「顶点数」时想的是「我要一个 N 边形」，
            而保留原顶点再补点/删点的结果依赖「补在哪条边、删哪个点」，得到的
            轮廓往往很怪（比如 4 点加第 5 点，新点落在某条边中间，看着像缺角）。

            重建时保留**当前包围盒的中心与外接半径**，位置和大小都不跳。
            外接半径取「包围盒的一半」，所以 4 边形时它正好是矩形（与内缩矩形
            的默认轮廓一致），换点数时视觉过渡自然。
        """
        if self.kind != MASK_POLYGON:
            return self
        count = int(count)
        if count < 3:
            raise MaskShapeError(f"多边形至少需要 3 个顶点，收到 {count}")
        # 只有「点数相同**且形状已经是规整轮廓**」才原样返回。
        # 单看点数不够：菱形的点数也是 4，直接返回会让「改成 4 边形」变成
        # 「什么都没变」，用户以为控件失效。
        if count == len(self.points) and self._is_regular(count):
            return self

        x1, y1, x2, y2 = self.bounds()
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        half_w = max(1e-3, (x2 - x1) / 2.0)
        half_h = max(1e-3, (y2 - y1) / 2.0)
        if count == 4:
            # 4 边形直接用矩形：正四边形的「外接圆」顶点在 45° 处，
            # 算出来是一个旋转 45° 的正方形，与用户的「矩形」直觉不符。
            return MaskShape(
                MASK_POLYGON,
                [
                    (cx - half_w, cy - half_h),
                    (cx + half_w, cy - half_h),
                    (cx + half_w, cy + half_h),
                    (cx - half_w, cy + half_h),
                ],
            )

        # N 边形：内切于当前包围盒的椭圆。
        #
        # **按椭圆内切而不是外接**：外接时顶点落在包围盒边界上，边会往里凹，
        # 面积比原形状小；内切时边贴着包围盒边界中段，视觉上「差不多一样大」。
        # 选内切是因为用户的心理预期是「形状别变小了」。
        points = []
        for index in range(count):
            rad = -math.pi / 2 + 2 * math.pi * index / count
            points.append(
                (
                    cx + half_w * math.cos(rad),
                    cy + half_h * math.sin(rad),
                )
            )
        return MaskShape(MASK_POLYGON, points)

    def _is_regular(self, count):
        """判断当前多边形是否已经是「规整的 N 边形轮廓」。

        Args:
            count: 期望的点数。

        Returns:
            True 表示无需重建。

        Note:
            判据是「所有顶点到中心距离一致（在等比空间里）」且「轴对齐矩形或
            正多边形」。这里只做足够用的近似：矩形轮廓（两两轴对齐）与
            顶点均匀分布的正多边形都算规整。
        """
        if count != len(self.points):
            return False
        if count == 4:
            xs = {round(point[0], 6) for point in self.points}
            ys = {round(point[1], 6) for point in self.points}
            return len(xs) == 2 and len(ys) == 2
        cx, cy = self.center()
        distances = [math.hypot(point[0] - cx, point[1] - cy) for point in self.points]
        if not distances:
            return False
        mean = sum(distances) / len(distances)
        return all(abs(distance - mean) <= mean * 0.05 for distance in distances)

    def inserted(self, edge_index, x, y):
        """在多边形的某条边上插入一个顶点。

        Args:
            edge_index: 边序号（顶点 `i` 到 `i+1`）。
            x: 新顶点的相对 x。
            y: 新顶点的相对 y。

        Returns:
            新的 `MaskShape`。

        Raises:
            MaskShapeError: 不是多边形，或边序号越界。
        """
        if self.kind != MASK_POLYGON:
            raise MaskShapeError("只有多边形支持插入顶点")
        count = len(self.points)
        if not 0 <= edge_index < count:
            raise MaskShapeError(f"边序号越界：{edge_index}（共 {count} 条边）")
        points = list(self.points)
        points.insert(edge_index + 1, (_round_ratio(_clamp_coord(x)), _round_ratio(_clamp_coord(y))))
        return MaskShape(MASK_POLYGON, points)

    def removed(self, index):
        """删除多边形的一个顶点。

        Args:
            index: 顶点下标。

        Returns:
            新的 `MaskShape`。

        Raises:
            MaskShapeError: 不是多边形、下标越界，或删完不足 3 个点。

        Note:
            **不允许删到少于 3 个点**：两个点构不成多边形，退化成一条线之后
            掩码面积为零，该特征永远匹配不到。要在界面上提示而不是静默拒绝。
        """
        if self.kind != MASK_POLYGON:
            raise MaskShapeError("只有多边形支持删除顶点")
        if not 0 <= index < len(self.points):
            raise MaskShapeError(f"顶点下标越界：{index}（共 {len(self.points)} 个）")
        if len(self.points) <= 3:
            raise MaskShapeError("多边形至少需要 3 个顶点，无法再删")
        points = [point for position, point in enumerate(self.points) if position != index]
        return MaskShape(MASK_POLYGON, points)

    # --- 掩码 ---------------------------------------------------------------

    def mask(self, width, height):
        """生成形状掩码。

        Args:
            width: 目标宽度（像素）。
            height: 目标高度（像素）。

        Returns:
            单通道 uint8 数组，255 参与 / 0 排除；`kind` 为 `none` 时返回 None。

        Note:
            **`none` 返回 None 而不是全 255**：调用方据此可以跳过掩码计算与
            传参（`cv2.matchTemplate` 不带 mask 比带全 255 的掩码更快），
            而语义完全等价。返回全 255 会让「无形状」在代码里看起来像
            「形状是整块矩形」，两种状态又混在一起了。
        """
        if self.kind == MASK_NONE:
            return None
        import cv2
        import numpy as np

        width = max(1, int(width))
        height = max(1, int(height))
        mask = np.zeros((height, width), dtype=np.uint8)

        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            cx, cy = self.points[0]
            # 半径以宽度为基准，纵向按同一像素数——这样在非正方形包围盒里
            # 得到的仍是正圆而不是随分辨率变形的椭圆
            radius_px = self.radius * width
            center = (int(round(cx * width)), int(round(cy * height)))
            if self.kind == MASK_CIRCLE:
                cv2.circle(mask, center, int(round(radius_px)), 255, -1)
            else:
                cv2.ellipse(
                    mask,
                    center,
                    (int(round(radius_px)), int(round(radius_px))),
                    0,
                    self.angle,
                    self.angle + self.sweep,
                    255,
                    -1,
                )
            return mask

        # 矩形要先按 aspect 旋转，再换成像素坐标：`corners` 已在比例空间里
        # 做过等比换算，这里直接乘回像素即可
        aspect = height / width if width else 1.0
        corners = self.corners(aspect)
        points = np.round([(x * width, y * height) for x, y in corners]).astype(np.int32)
        cv2.fillPoly(mask, [points], 255)
        return mask

    # --- 序列化 -------------------------------------------------------------

    def to_dict(self):
        """序列化为可写入 JSON 的字典。

        Returns:
            与 :meth:`from_dict` 对称的字典。

        Note:
            只写该形状用得上的字段：`none` 只写 kind，矩形不写半径与扇角，
            圆不写扇角。多余的零值字段会让「这个字段对当前形状是否生效」
            变得不可判断。
        """
        data = {"kind": self.kind}
        if self.kind == MASK_NONE:
            return data
        data["points"] = [[x, y] for x, y in self.points]
        if self.kind in (MASK_RECT, MASK_SECTOR) and abs(self.angle) > 1e-9:
            data["angle"] = self.angle
        if self.kind == MASK_SECTOR:
            data["sweep"] = self.sweep
        if self.kind in (MASK_CIRCLE, MASK_SECTOR):
            data["radius"] = self.radius
        return data

    @classmethod
    def from_dict(cls, data):
        """从字典还原。

        Args:
            data: `to_dict` 产出的字典；None 或缺失 kind 时返回「无形状」。

        Returns:
            `MaskShape` 实例。

        Raises:
            MaskShapeError: 字段非法。

        Note:
            `None` 与缺失 `kind` 都按「无形状」处理：升级前保存的场景没有
            `mask_shape` 字段，那些场景的正确语义就是「没有掩码形状」。
            报错会让旧库全部打不开，而兜底成矩形会改变它们原有的识别行为。
        """
        if not data:
            return cls.none()
        kind = data.get("kind") or MASK_NONE
        return cls(
            kind=kind,
            points=[tuple(point) for point in data.get("points", [])],
            angle=data.get("angle", 0.0),
            sweep=data.get("sweep", 90.0),
            radius=data.get("radius", 0.0),
        )

    def __eq__(self, other):
        """按字段比较（测试与去重需要）。

        Args:
            other: 待比较对象。

        Returns:
            True 表示字段完全一致。
        """
        if not isinstance(other, MaskShape):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __repr__(self):
        """返回可读表示（调试用）。

        Returns:
            形如 `MaskShape(kind=circle, ...)` 的字符串。
        """
        return f"MaskShape({self.to_dict()})"


def _angle_delta(a, b):
    """返回两个角度之间的最小夹角（0–180）。

    Args:
        a: 角度 a（度）。
        b: 角度 b（度）。

    Returns:
        最小夹角（度）。

    Note:
        直接相减会在 0°/360° 交界处得到 359 —— 表现为「起始边在 359° 时，
        鼠标放到 1° 上要转一整圈才能命中」。
    """
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _point_in_polygon(x, y, points):
    """判断点是否在多边形内（射线法）。

    Args:
        x: 点 x。
        y: 点 y。
        points: 多边形顶点序列。

    Returns:
        True 表示在内。
    """
    inside = False
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            cross_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross_x:
                inside = not inside
    return inside


def _project_on_segment(px, py, ax, ay, bx, by):
    """把点投影到线段上。

    Args:
        px: 点 x。
        py: 点 y。
        ax: 线段起点 x。
        ay: 线段起点 y。
        bx: 线段终点 x。
        by: 线段终点 y。

    Returns:
        线段上距离该点最近的点 `(x, y)`。
    """
    dx = bx - ax
    dy = by - ay
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-12:
        return ax, ay
    t = ((px - ax) * dx + (py - ay) * dy) / length_squared
    t = min(1.0, max(0.0, t))
    return ax + t * dx, ay + t * dy
