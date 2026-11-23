# -*- coding: utf-8 -*-
"""分辨率归一化与坐标换算。

本模块是「同一套场景库在任意分辨率下都能用」的唯一依据，**不导入 `ok` 与
`PySide6`**：换算是纯数学，必须能在无 GUI 环境逐值验证。

三条口径：

1. **归一化只做一次**。识别时把当前帧等比缩放到库的基准分辨率，之后所有场景
   复用同一帧、模板不缩放。若反过来缩放模板，每次匹配都要插值，且不同场景的
   模板各自插值会累积不同误差。
2. **库内坐标一律是相对比例**，与帧尺寸无关。换算成像素只在「裁剪 / 点击」这两个
   真正需要像素的时刻发生。
3. **宽高比不一致时不做换算**，直接报错。比例变了说明画面构成已变（裁过边、
   加了黑边、换了窗口模式），此时按比例硬算出来的坐标是错的——而错坐标的表现
   是「点到了别处」，比明确报错更难排查。

容差沿用框架的 1%（`TaskExecutor.check_frame_and_resolution` 用
`difference <= 0.01 * supported_ratio` 判比例是否受支持），避免出现「框架认为
分辨率可用、场景管理认为不可用」的两套口径。
"""

import numpy as np

# 宽高比容差。与框架 `check_frame_and_resolution` 的 1% 保持一致。
RATIO_TOLERANCE = 0.01


class NormalizeError(ValueError):
    """分辨率换算失败。"""


def ratio_differs(width_a, height_a, width_b, height_b, tolerance=RATIO_TOLERANCE):
    """判断两个分辨率的宽高比是否超出容差。

    Args：
        width_a：第一个分辨率的宽。
        height_a：第一个分辨率的高。
        width_b：第二个分辨率的宽。
        height_b：第二个分辨率的高。
        tolerance：相对容差，默认 1%。

    Returns：
        超出容差时为 True。

    Raises：
        NormalizeError：任一分辨率含非正分量。
    """
    if min(width_a, height_a, width_b, height_b) <= 0:
        raise NormalizeError(f"分辨率必须为正，收到 {width_a}x{height_a} 与 {width_b}x{height_b}")
    ratio_a = width_a / height_a
    ratio_b = width_b / height_b
    return abs(ratio_a - ratio_b) > tolerance * ratio_a


def scale_factor(width, height, base_width, base_height):
    """返回当前分辨率到基准分辨率的等比缩放系数。

    Args：
        width：当前帧宽。
        height：当前帧高。
        base_width：基准帧宽。
        base_height：基准帧高。

    Returns：
        缩放系数（基准/当前）；两分辨率相同且比例一致时为 1.0。

    Raises：
        NormalizeError：分辨率非正，或宽高比超出容差。
    """
    if ratio_differs(width, height, base_width, base_height):
        raise NormalizeError(
            f"画面宽高比与场景库不一致：当前 {width}x{height}"
            f"（{width / height:.4f}），库基准 {base_width}x{base_height}"
            f"（{base_width / base_height:.4f}）。比例不同意味着画面构成已变"
            f"（裁边、黑边或窗口模式不同），换算出的坐标不可信，请用当前分辨率重新采集场景"
        )
    return base_width / width


def normalize_frame(frame, base_width, base_height, tolerance=RATIO_TOLERANCE):
    """把当前帧缩放到基准分辨率。

    尺寸已一致时**返回原帧对象**（不复制）：识别链路上帧被反复传递，无谓的复制
    会在每轮判断里多付一次全帧内存拷贝。

    Args：
        frame：BGR 或灰度帧（numpy 数组）。
        base_width：基准帧宽。
        base_height：基准帧高。
        tolerance：宽高比容差。

    Returns：
        缩放后的帧（尺寸等于基准分辨率）。

    Raises：
        NormalizeError：帧为空、分辨率非正，或宽高比超出容差。
    """
    if frame is None:
        raise NormalizeError("帧为空，无法归一化")
    height, width = frame.shape[:2]
    if ratio_differs(width, height, base_width, base_height, tolerance):
        raise NormalizeError(
            f"画面宽高比与场景库不一致：当前 {width}x{height}"
            f"（{width / height:.4f}），库基准 {base_width}x{base_height}"
            f"（{base_width / base_height:.4f}）"
        )
    if width == base_width and height == base_height:
        return frame

    import cv2

    shrinking = base_width < width
    if shrinking:
        interpolation = cv2.INTER_AREA
    else:
        interpolation = cv2.INTER_LINEAR
    return cv2.resize(frame, (base_width, base_height), interpolation=interpolation)


def resize_cost_hint(width, height, base_width, base_height):
    """返回本次归一化是否会真正发生缩放（供界面提示用）。

    Args：
        width：当前帧宽。
        height：当前帧高。
        base_width：基准帧宽。
        base_height：基准帧高。

    Returns：
        `(needs_resize, direction)` 二元组。`direction` 取 `"none"` / `"down"` / `"up"`。
    """
    if width == base_width and height == base_height:
        return False, "none"
    if base_width < width:
        return True, "down"
    return True, "up"


def rect_to_pixels(rect, width, height):
    """把相对比例矩形换算成像素矩形（右下开区间）。

    取整用**截断**而非四舍五入：与遮罩几何的口径一致（上游写的是
    `int(0.13 * width)`），两处口径不同会让同一矩形在遮罩与场景里差 1 像素。

    Args：
        rect：相对比例 `(x1, y1, x2, y2)`。
        width：目标帧宽。
        height：目标帧高。

    Returns：
        `(x1, y1, x2, y2)` 整数元组，已裁剪到帧边界内。

    Raises：
        NormalizeError：分辨率非正或矩形退化成零面积。
    """
    if width <= 0 or height <= 0:
        raise NormalizeError(f"分辨率必须为正，收到 {width}x{height}")
    x1 = int(rect[0] * width)
    y1 = int(rect[1] * height)
    x2 = int(rect[2] * width)
    y2 = int(rect[3] * height)
    x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
    y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        raise NormalizeError(f"矩形 {rect} 在 {width}x{height} 下退化为零面积")
    return x1, y1, x2, y2


def rect_center(rect, width, height):
    """返回相对比例矩形的像素中心点。

    Args：
        rect：相对比例 `(x1, y1, x2, y2)`。
        width：目标帧宽。
        height：目标帧高。

    Returns：
        `(x, y)` 整数元组，可直接传给点击 API。

    Raises：
        NormalizeError：分辨率非正或矩形退化。
    """
    x1, y1, x2, y2 = rect_to_pixels(rect, width, height)
    return (x1 + x2) // 2, (y1 + y2) // 2


def click_point(rect, frame_width, frame_height):
    """把区域矩形换算成当前帧上的点击坐标。

    区域存的是相对比例，因此识别（基准分辨率）与点击（当前分辨率）用的是同一份
    坐标，这一步不需要知道帧来自哪一侧。

    Args：
        rect：相对比例 `(x1, y1, x2, y2)`，与帧尺寸无关。
        frame_width：当前帧宽。
        frame_height：当前帧高。

    Returns：
        `(x, y)` 整数元组。

    Raises：
        NormalizeError：分辨率非正或矩形退化。
    """
    return rect_center(rect, frame_width, frame_height)


def resolve_region_pixels(rect, base_width, base_height):
    """把相对比例矩形换算成基准分辨率下的像素矩形。

    用于「在基准帧上裁剪模板图」——模板按基准分辨率裁出，识别时帧也被缩放到
    基准分辨率，两者尺寸一致，模板不需要缩放。

    Args：
        rect：相对比例 `(x1, y1, x2, y2)`。
        base_width：基准帧宽。
        base_height：基准帧高。

    Returns：
        `(x1, y1, x2, y2)` 整数元组。

    Raises：
        NormalizeError：分辨率非正或矩形退化。
    """
    return rect_to_pixels(rect, base_width, base_height)


def pixels_to_rect(x1, y1, x2, y2, width, height):
    """把像素矩形反解成相对比例矩形。

    Args：
        x1：左上 x。
        y1：左上 y。
        x2：右下 x（开区间）。
        y2：右下 y（开区间）。
        width：帧宽。
        height：帧高。

    Returns：
        相对比例四元组，保留六位小数（与数据模型的精度一致）。

    Raises：
        NormalizeError：分辨率非正，或矩形退化 / 越界。
    """
    if width <= 0 or height <= 0:
        raise NormalizeError(f"分辨率必须为正，收到 {width}x{height}")
    x1 = max(0, min(width, int(x1)))
    x2 = max(0, min(width, int(x2)))
    y1 = max(0, min(height, int(y1)))
    y2 = max(0, min(height, int(y2)))
    if x2 <= x1 or y2 <= y1:
        raise NormalizeError(f"像素矩形 ({x1}, {y1}, {x2}, {y2}) 退化为零面积")
    return (
        round(x1 / width, 6),
        round(y1 / height, 6),
        round(x2 / width, 6),
        round(y2 / height, 6),
    )


def rects_intersect(rect_a_px, rect_b_px):
    """判断两个**像素**矩形是否相交，并返回交集。

    用于检测「特征框与截图遮罩矩形是否重叠」。重叠时裁出的模板在交集内是纯色块，
    匹配必然失败，而失效表现为「这个特征从来没命中过」，不是报错。

    Args：
        rect_a_px：像素矩形 `(x1, y1, x2, y2)`。
        rect_b_px：像素矩形 `(x1, y1, x2, y2)`。

    Returns：
        `(overlaps, intersection)`。`intersection` 为交集矩形；不相交时返回 None。
    """
    ax1, ay1, ax2, ay2 = rect_a_px
    bx1, by1, bx2, by2 = rect_b_px
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax2, bx2)
    y2 = min(ay2, by2)
    if x2 <= x1 or y2 <= y1:
        return False, None
    return True, (x1, y1, x2, y2)


def intersection_ratio(rect_a_px, rect_b_px):
    """返回交集面积占第一个矩形面积的比例。

    Args：
        rect_a_px：像素矩形 `(x1, y1, x2, y2)`，作为分母。
        rect_b_px：像素矩形 `(x1, y1, x2, y2)`。

    Returns：
        0.0–1.0 的浮点数；第一个矩形面积为 0 时返回 0.0。
    """
    ax1, ay1, ax2, ay2 = rect_a_px
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    if area_a <= 0:
        return 0.0
    overlaps, intersection = rects_intersect(rect_a_px, rect_b_px)
    if not overlaps:
        return 0.0
    ix1, iy1, ix2, iy2 = intersection
    return ((ix2 - ix1) * (iy2 - iy1)) / area_a


def frames_stable(previous, current, mean_threshold=3.0, changed_ratio=0.1, pixel_threshold=8):
    """判断两帧之间画面是否已稳定。

    阈值**复用框架 `patches_changed` 的取值**（`ok/util/blur.py`）：单像素差超过 8
    记为变化像素，平均差超过 3.0 或变化像素占比超过 0.1 判为仍在变。不自己定一套：
    探索的「连续两次无跳转」与「连续两帧稳定」必须用同一判据，否则会出现
    「判为稳定但仍在动画中」的自相矛盾，且两个数字要各自调参。

    Args：
        previous：上一帧（numpy 数组）。
        current：当前帧（numpy 数组）。
        mean_threshold：平均差阈值。
        changed_ratio：变化像素占比阈值。
        pixel_threshold：单像素差阈值。

    Returns：
        两帧都稳定时为 True。任一帧为空、形状不一致时返回 False
        （无法判定即视为不稳定）。
    """
    if previous is None or current is None:
        return False
    if previous.shape != current.shape:
        return False

    import cv2

    difference = cv2.absdiff(previous, current)
    if difference.ndim == 3:
        changed = np.mean(difference, axis=2) > pixel_threshold
    else:
        changed = difference > pixel_threshold
    if float(np.mean(difference)) > mean_threshold:
        return False
    return float(np.mean(changed)) <= changed_ratio
