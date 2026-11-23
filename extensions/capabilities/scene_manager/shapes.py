# -*- coding: utf-8 -*-
"""特征形状的几何、掩码生成与命中测试。

一个特征只有**一个形状**，取 `SHAPES` 之一：

| 形状 | `points` 含义 | 角度字段 |
|---|---|---|
| `rect` | 对角两点（左上、右下），未旋转时的矩形 | `angle` 绕中心旋转 |
| `circle` | 圆心一点 | 无（圆旋转后仍是自己） |
| `sector` | 圆心一点 | `angle` 是起始边方向，`sweep` 是张开角 |
| `polygon` | 顶点序列（≥3 个） | `angle` 绕质心旋转 |

**旋转只影响形状与掩码，不影响匹配方式**。实测：模板按该角度下的真实像素裁出，
同角度画面得分 1.0000；若要让同一张模板去找旋转 20° 的画面，得分只有 0.6337，
需要 24 个角度的模板库才能覆盖，单次匹配从 17ms 涨到 688ms。因此这里的角度是
「让形状贴合画面里斜置的元素」，不是「让模板在画面里转着找」。

**为什么需要掩码**：圆形按钮的外接矩形四角含背景。实测同一张模板匹配「四角有
干扰元素」的画面，不带掩码得分 0.9221，带圆形掩码 0.9918——掩码把不参与匹配的
像素排除在外，避免四角内容拉低置信度。

## 坐标口径：所有几何方法都必须传帧尺寸

点与半径存**相对比例**，但相对比例的 x 基准是帧宽、y 基准是帧高。二者不等时，
「相对半径 0.1 的圆」在 x 方向占 192px、在 y 方向只占 108px（1920×1080 下），
若按相对包围盒裁图，圆会被上下切掉——实测掩码覆盖率 94.4%，而真圆应为 78.5%。

因此**半径一律按帧宽折算成像素**，所有几何方法都要求传 `width`/`height`，在像素
空间完成旋转与包围盒计算，最后才按需换算回相对比例。这是唯一能保证「画面里的圆
就是圆的掩码」的做法。

本模块**不导入 `ok` 与 `PySide6`**，只在生成掩码时延迟导入 cv2。
"""

import math

# 形状种类。新增一种就要同时补 :meth:`Shape.mask` 与编辑端的绘制/拖动分支。
SHAPE_RECT = "rect"
SHAPE_CIRCLE = "circle"
SHAPE_SECTOR = "sector"
SHAPE_POLYGON = "polygon"
SHAPES = (SHAPE_RECT, SHAPE_CIRCLE, SHAPE_SECTOR, SHAPE_POLYGON)

# 各形状需要的点数（rect 固定 2 点，circle/sector 固定 1 点，polygon 至少 3 点）。
_MIN_POINTS = {
    SHAPE_RECT: 2,
    SHAPE_CIRCLE: 1,
    SHAPE_SECTOR: 1,
    SHAPE_POLYGON: 3,
}

# 相对比例保留的小数位。与 model 的 `_RATIO_DIGITS` 一致，避免同一坐标在两处
# 规整出不同的值。
_RATIO_DIGITS = 6


class ShapeError(ValueError):
    """形状参数非法。"""


def _round_ratio(value):
    """把相对比例规整到固定小数位。

    Args：
        value：原始比例。

    Returns：
        规整后的浮点数。

    Raises：
        ShapeError：不是数值。
    """
    try:
        return round(float(value), _RATIO_DIGITS)
    except (TypeError, ValueError) as error:
        raise ShapeError(f"坐标必须是数值，收到 {value!r}") from error


def _normalize_angle(angle):
    """把角度归一到 [0, 360)。

    Args：
        angle：原始角度（度），可为负或超过 360。

    Returns：
        归一后的浮点数，保留两位小数。

    Raises：
        ShapeError：不是数值。
    """
    try:
        value = float(angle) % 360.0
    except (TypeError, ValueError) as error:
        raise ShapeError(f"角度必须是数值，收到 {angle!r}") from error
    return round(value, 2)


def rotate_point(x, y, angle, center):
    """把一点绕给定中心旋转（像素空间）。

    Args：
        x：原始 x。
        y：原始 y。
        angle：旋转角度（度，顺时针为正，与 cv2 的 y 轴向下一致）。
        center：旋转中心 `(cx, cy)`。

    Returns：
        `(x, y)` 旋转后的浮点元组。
    """
    if not angle:
        return x, y
    cx, cy = center
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    dx, dy = x - cx, y - cy
    return cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a


def _point_in_polygon(x, y, polygon):
    """射线法判断点是否在多边形内。

    不引入 shapely 或 matplotlib：判断本身十几行，多一个依赖不值得。

    Args：
        x：点 x（像素）。
        y：点 y（像素）。
        polygon：多边形顶点列表（像素）。

    Returns：
        在内部时为 True。边界上的判定不稳定，调用方应自行加容差。
    """
    if not polygon or len(polygon) < 3:
        return False
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            cross_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross_x:
                inside = not inside
    return inside


class Shape:
    """一个特征的几何形状。

    Attributes：
        kind: 形状种类，取 `SHAPES` 之一。
        points: 相对比例的点序列，含义随 `kind` 变（见模块文档）。
        angle: 旋转角或扇形起始方向（度）。
        sweep: 扇形的张开角（度），仅 `sector` 使用。
        radius: 圆与扇形的半径，**相对帧宽**，仅这两类使用。
    """

    def __init__(self, kind=SHAPE_RECT, points=None, angle=0.0, sweep=90.0, radius=0.0):
        """构造并校验形状。

        Args：
            kind：形状种类。
            points：点序列，每点为 `(x, y)` 相对比例。
            angle：旋转角或扇形起始方向，负值会被归一到 [0, 360)。
            sweep：扇形张开角，限 (0, 360]。
            radius：圆/扇形半径（相对帧宽）。

        Raises：
            ShapeError：种类未知、点数不足、坐标越界或参数非数值。
        """
        if kind not in SHAPES:
            raise ShapeError(f"未知的形状 {kind!r}，只支持 {SHAPES}")
        self.kind = kind
        self.points = [(_round_ratio(x), _round_ratio(y)) for x, y in (points or [])]
        for x, y in self.points:
            if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
                raise ShapeError(f"坐标必须落在 0–1，收到 ({x}, {y})")
        need = _MIN_POINTS[kind]
        if len(self.points) < need:
            raise ShapeError(f"{kind} 至少需要 {need} 个点，收到 {len(self.points)} 个")

        self.angle = _normalize_angle(angle)
        if kind == SHAPE_SECTOR:
            try:
                self.sweep = float(sweep)
            except (TypeError, ValueError) as error:
                raise ShapeError(f"扇角必须是数值，收到 {sweep!r}") from error
            if not 0.0 < self.sweep <= 360.0:
                raise ShapeError(f"扇角必须落在 (0, 360]，收到 {self.sweep}")
        else:
            self.sweep = 0.0

        if kind in (SHAPE_CIRCLE, SHAPE_SECTOR):
            try:
                self.radius = float(radius)
            except (TypeError, ValueError) as error:
                raise ShapeError(f"半径必须是数值，收到 {radius!r}") from error
            if self.radius <= 0.0:
                raise ShapeError(f"半径必须为正，收到 {self.radius}")
        else:
            self.radius = 0.0

        if kind == SHAPE_RECT:
            x1, y1 = self.points[0]
            x2, y2 = self.points[1]
            if x2 <= x1 or y2 <= y1:
                raise ShapeError(f"矩形的对角两点必须满足 x2>x1 且 y2>y1，收到 {self.points}")

    # --- 像素换算 -----------------------------------------------------------

    def pixel_center(self, width, height):
        """返回旋转中心的像素坐标。

        圆心与扇形圆心直接用其圆心点；矩形与多边形用**未旋转点集**的像素包围盒
        中心。用包围盒中心而不是质心：多边形的质心会随顶点疏密偏移，改一个顶点
        就让整个形状莫名平移。

        Args：
            width：基准帧宽。
            height：基准帧高。

        Returns：
            `(cx, cy)` 浮点像素坐标。
        """
        if self.kind in (SHAPE_CIRCLE, SHAPE_SECTOR):
            cx, cy = self.points[0]
            return cx * width, cy * height
        xs = [x * width for x, _ in self.points]
        ys = [y * height for _, y in self.points]
        return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0

    def pixel_radius(self, width):
        """把相对半径换算成像素半径。

        半径按**帧宽**折算而非两轴各自折算：圆在画面里始终是圆，若按两轴分别
        折算，非 1:1 的帧会让圆变成椭圆，包围盒也随之偏小把圆切掉。

        Args：
            width：基准帧宽。

        Returns：
            浮点像素半径。
        """
        return self.radius * width

    def pixel_outline(self, width, height):
        """返回**旋转后**的闭合轮廓（像素）。

        圆与扇形返回包围盒四角（形状本身由 `mask` 里的 cv2 图元绘制）。

        Args：
            width：基准帧宽。
            height：基准帧高。

        Returns：
            `[(px, py), ...]` 浮点像素点。
        """
        if self.kind in (SHAPE_CIRCLE, SHAPE_SECTOR):
            x1, y1, x2, y2 = self.pixel_bbox(width, height)
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        if self.kind == SHAPE_RECT:
            (x1, y1), (x2, y2) = self.points
            corners = [
                (x1 * width, y1 * height),
                (x2 * width, y1 * height),
                (x2 * width, y2 * height),
                (x1 * width, y2 * height),
            ]
        else:
            corners = [(x * width, y * height) for x, y in self.points]
        center = self.pixel_center(width, height)
        return [rotate_point(x, y, self.angle, center) for x, y in corners]

    def pixel_bbox(self, width, height):
        """返回旋转后轮廓的包围盒（像素）。

        这是模板裁剪与搜索区域的基础：模板按此包围盒裁出，因此形状倾斜时模板会
        包含更多背景像素，由掩码排除。

        Args：
            width：基准帧宽。
            height：基准帧高。

        Returns：
            `(x1, y1, x2, y2)` 整数像素矩形，已收敛到画面内且保证 `x2>x1`、`y2>y1`。
        """
        if self.kind in (SHAPE_CIRCLE, SHAPE_SECTOR):
            cx, cy = self.pixel_center(width, height)
            r = self.pixel_radius(width)
            raw = (cx - r, cy - r, cx + r, cy + r)
        else:
            pts = self.pixel_outline(width, height)
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            raw = (min(xs), min(ys), max(xs), max(ys))
        x1 = max(0, min(width - 1, int(math.floor(raw[0]))))
        y1 = max(0, min(height - 1, int(math.floor(raw[1]))))
        x2 = max(x1 + 1, min(width, int(math.ceil(raw[2]))))
        y2 = max(y1 + 1, min(height, int(math.ceil(raw[3]))))
        return x1, y1, x2, y2

    def region(self, width, height):
        """返回包围盒的相对比例形式（供搜索区域使用）。

        Args：
            width：基准帧宽。
            height：基准帧高。

        Returns：
            `(x1, y1, x2, y2)` 相对比例，四位小数。
        """
        x1, y1, x2, y2 = self.pixel_bbox(width, height)
        return (
            round(x1 / width, _RATIO_DIGITS),
            round(y1 / height, _RATIO_DIGITS),
            round(x2 / width, _RATIO_DIGITS),
            round(y2 / height, _RATIO_DIGITS),
        )

    # --- 掩码 ---------------------------------------------------------------

    def mask(self, width, height):
        """生成与 :meth:`pixel_bbox` 同尺寸的掩码。

        掩码为单通道 uint8：255 表示该像素参与匹配，0 表示排除。

        Args：
            width：基准帧宽。
            height：基准帧高。

        Returns：
            numpy 数组，形状 `(bbox_h, bbox_w)`。
        """
        import cv2
        import numpy as np

        x1, y1, x2, y2 = self.pixel_bbox(width, height)
        mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
        if self.kind == SHAPE_CIRCLE:
            cx, cy = self.pixel_center(width, height)
            radius = int(round(self.pixel_radius(width)))
            cv2.circle(mask, (int(round(cx)) - x1, int(round(cy)) - y1), radius, 255, -1)
        elif self.kind == SHAPE_SECTOR:
            cx, cy = self.pixel_center(width, height)
            radius = int(round(self.pixel_radius(width)))
            cv2.ellipse(
                mask,
                (int(round(cx)) - x1, int(round(cy)) - y1),
                (radius, radius),
                0,
                self.angle,
                self.angle + self.sweep,
                255,
                -1,
            )
        else:
            pts = self.pixel_outline(width, height)
            local = np.round([(px - x1, py - y1) for px, py in pts]).astype(np.int32)
            cv2.fillPoly(mask, [local], 255)
        return mask

    # --- 编辑辅助 -----------------------------------------------------------

    def contains(self, x, y, width, height, tolerance=0):
        """判断一点是否落在形状内（用于画布命中测试）。

        Args：
            x：相对比例 x。
            y：相对比例 y。
            width：基准帧宽。
            height：基准帧高。
            tolerance：容差（像素），让细形状也点得中。

        Returns：
            命中时为 True。
        """
        px, py = x * width, y * height
        if self.kind == SHAPE_CIRCLE:
            cx, cy = self.pixel_center(width, height)
            return math.hypot(px - cx, py - cy) <= self.pixel_radius(width) + tolerance
        if self.kind == SHAPE_SECTOR:
            cx, cy = self.pixel_center(width, height)
            dist = math.hypot(px - cx, py - cy)
            if dist > self.pixel_radius(width) + tolerance:
                return False
            if dist <= tolerance:
                return True
            deg = math.degrees(math.atan2(py - cy, px - cx)) % 360.0
            return (deg - self.angle) % 360.0 <= self.sweep
        return _point_in_polygon(px, py, self.pixel_outline(width, height))

    def handles(self, width, height):
        """返回可拖动的控制点（相对比例）。

        矩形与多边形返回各顶点；圆与扇形返回 `[圆心, 右侧半径点]`。

        Args：
            width：基准帧宽。
            height：基准帧高。

        Returns：
            `[(x, y), ...]` 相对比例点。
        """
        if self.kind in (SHAPE_CIRCLE, SHAPE_SECTOR):
            cx, cy = self.pixel_center(width, height)
            pts = [(cx, cy), (cx + self.pixel_radius(width), cy)]
        else:
            pts = self.pixel_outline(width, height)
        return [(round(px / width, _RATIO_DIGITS), round(py / height, _RATIO_DIGITS)) for px, py in pts]

    # --- 序列化 -------------------------------------------------------------

    def to_dict(self):
        """序列化为可写入 JSON 的字典。

        只写该形状用得上的字段：矩形不写半径，圆不写扇角。多余的零值字段会让
        「这个字段对当前形状是否生效」变得不可判断。

        Returns：
            与 :meth:`from_dict` 对称的字典。
        """
        data = {"kind": self.kind, "points": [[x, y] for x, y in self.points]}
        if self.kind in (SHAPE_RECT, SHAPE_POLYGON, SHAPE_SECTOR):
            data["angle"] = self.angle
        if self.kind == SHAPE_SECTOR:
            data["sweep"] = self.sweep
        if self.kind in (SHAPE_CIRCLE, SHAPE_SECTOR):
            data["radius"] = self.radius
        return data

    @classmethod
    def from_dict(cls, data):
        """从字典还原形状。

        Args：
            data：:meth:`to_dict` 产出的字典。

        Returns：
            `Shape` 实例。
        """
        data = data or {}
        points = [tuple(p) for p in data.get("points", [])]
        return cls(
            kind=data.get("kind", SHAPE_RECT),
            points=points,
            angle=data.get("angle", 0.0),
            sweep=data.get("sweep", 90.0),
            radius=data.get("radius", 0.0),
        )

    @classmethod
    def rectangle(cls, x1, y1, x2, y2, angle=0.0):
        """按对角两点造一个矩形（编辑端最常用）。

        Args：
            x1：左上 x（会被排序，允许反向拖拽）。
            y1：左上 y。
            x2：右下 x。
            y2：右下 y。
            angle：绕中心旋转角。

        Returns：
            `Shape` 实例。
        """
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        return cls(SHAPE_RECT, [(x1, y1), (x2, y2)], angle=angle)
