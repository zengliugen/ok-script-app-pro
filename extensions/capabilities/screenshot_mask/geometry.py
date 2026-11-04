# -*- coding: utf-8 -*-
"""截图遮罩的几何换算（纯函数，不依赖 Qt）。

坐标系与取值口径：

- 矩形一律用帧像素表示，形状为 ``(x1, y1, x2, y2)``，右下开区间——与 numpy 切片
  ``frame[y1:y2, x1:x2]`` 完全一致。改用 ``(x, y, w, h)`` 会让「贴右边」每次多一次
  减法，而每一处减法都是越界判断的漏点。
- 配置里的宽高与偏移是**百分比**（``13.0`` 表示 13%），解释基准是帧宽/帧高。
  只存一套值：多单位会引入正反换算，而正反换算必然产生累积误差。

本模块刻意不导入 ``ok`` 与 ``PySide6``：几何是本能力唯一需要精确验证的部分，
它必须能在没有运行依赖的环境里被完整测试。
"""

import numpy as np

# 8 个锚点，取值是稳定标识（不随界面语言变化），持久化在配置里。
ANCHOR_TOP_LEFT = "top_left"
ANCHOR_TOP = "top"
ANCHOR_TOP_RIGHT = "top_right"
ANCHOR_RIGHT = "right"
ANCHOR_BOTTOM_RIGHT = "bottom_right"
ANCHOR_BOTTOM = "bottom"
ANCHOR_BOTTOM_LEFT = "bottom_left"
ANCHOR_LEFT = "left"

# 锚点 → (水平贴靠, 垂直贴靠)。水平取 left/center/right，垂直取 top/center/bottom。
ANCHOR_SIDES = {
    ANCHOR_TOP_LEFT: ("left", "top"),
    ANCHOR_TOP: ("center", "top"),
    ANCHOR_TOP_RIGHT: ("right", "top"),
    ANCHOR_RIGHT: ("right", "center"),
    ANCHOR_BOTTOM_RIGHT: ("right", "bottom"),
    ANCHOR_BOTTOM: ("center", "bottom"),
    ANCHOR_BOTTOM_LEFT: ("left", "bottom"),
    ANCHOR_LEFT: ("left", "center"),
}

# 锚点的展示顺序：4 角 + 4 边中点，界面上按此顺序排列。
ANCHOR_ORDER = (
    ANCHOR_TOP_LEFT,
    ANCHOR_TOP,
    ANCHOR_TOP_RIGHT,
    ANCHOR_RIGHT,
    ANCHOR_BOTTOM_RIGHT,
    ANCHOR_BOTTOM,
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_LEFT,
)

# 配置键。键会原样写进持久化 JSON 且被当作界面标签，因此必须是稳定的 ASCII 英文。
KEY_ENABLED = "Enable Mask"
KEY_ANCHOR = "Mask Anchor"
KEY_WIDTH = "Mask Width"
KEY_HEIGHT = "Mask Height"
KEY_OFFSET_X = "Mask Offset X"
KEY_OFFSET_Y = "Mask Offset Y"

# 数值键。四个一起写、一起读，任何一处单独变化都会让几何自相矛盾。
NUMERIC_KEYS = (KEY_WIDTH, KEY_HEIGHT, KEY_OFFSET_X, KEY_OFFSET_Y)

DEFAULT_VALUES = {
    KEY_ENABLED: True,
    KEY_ANCHOR: ANCHOR_BOTTOM_RIGHT,
    KEY_WIDTH: 13.0,
    KEY_HEIGHT: 2.5,
    KEY_OFFSET_X: 0.0,
    KEY_OFFSET_Y: 0.0,
}

# 旧版中文键。仅用于把已有配置迁移到新键，新代码不再写入这些键。
LEGACY_KEYS = {
    "启用遮罩": KEY_ENABLED,
    "遮罩位置": KEY_ANCHOR,
    "宽度": KEY_WIDTH,
    "高度": KEY_HEIGHT,
    "水平内缩": KEY_OFFSET_X,
    "垂直内缩": KEY_OFFSET_Y,
}

# 旧版锚点取值（中文标签）→ 稳定标识。
LEGACY_ANCHORS = {
    "左上角": ANCHOR_TOP_LEFT,
    "上边中点": ANCHOR_TOP,
    "右上角": ANCHOR_TOP_RIGHT,
    "右边中点": ANCHOR_RIGHT,
    "右下角": ANCHOR_BOTTOM_RIGHT,
    "下边中点": ANCHOR_BOTTOM,
    "左下角": ANCHOR_BOTTOM_LEFT,
    "左边中点": ANCHOR_LEFT,
}

# 百分比保留的小数位数。四位足以让「拖拽 → 存储 → 还原」逐像素一致，
# 又不会在 json 里留下浮点尾巴。
_PERCENT_DIGITS = 4


def resolve_rect(values, width, height):
    """按配置算出遮罩在帧上的矩形。

    锚点决定贴哪条边（或哪个角）；宽高与偏移按帧尺寸换算成像素；结果按帧边界裁剪。

    Args：
        values：配置字典，需含 ``KEY_ANCHOR`` 与四个数值键。
        width：帧宽（像素）。
        height：帧高（像素）。

    Returns：
        ``(x1, y1, x2, y2)`` 整数元组；帧尺寸非法、锚点未知或遮罩完全落在画面外时
        返回 ``None``。
    """
    if not width or not height or width <= 0 or height <= 0:
        return None

    anchor = values.get(KEY_ANCHOR, DEFAULT_VALUES[KEY_ANCHOR])
    sides = ANCHOR_SIDES.get(anchor)
    if sides is None:
        return None

    mask_width = _scale_percent(values.get(KEY_WIDTH, 0.0), width)
    mask_height = _scale_percent(values.get(KEY_HEIGHT, 0.0), height)
    offset_x = _scale_percent(values.get(KEY_OFFSET_X, 0.0), width)
    offset_y = _scale_percent(values.get(KEY_OFFSET_Y, 0.0), height)

    x1, x2 = _place(mask_width, width, sides[0], offset_x)
    y1, y2 = _place(mask_height, height, sides[1], offset_y)

    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _place(size, total, side, offset):
    """在一条轴上按贴靠方向算出区间。

    Args：
        size：遮罩在该轴上的长度（像素）。
        total：该轴总长（像素）。
        side：``left`` / ``center`` / ``right``（垂直轴同理传 ``top`` / ``center`` / ``bottom``）。
        offset：沿该轴向画面内部的偏移（像素）。

    Returns：
        ``(start, end)``；偏移使区间跑出边界时不在此裁剪，交给调用方统一处理。
    """
    if side in ("right", "bottom"):
        return total - size - offset, total - offset
    if side == "center":
        start = (total - size) // 2 + offset
        return start, start + size
    return offset, offset + size


def apply_mask(frame, values):
    """在帧上涂黑遮罩区域。

    只改遮罩覆盖的像素，其余像素原样保留。**不判断开关**：开关由处理器统一判，
    两处各判一次会让「关掉开关仍被涂黑」这类问题无从定位。

    Args：
        frame：BGR 或灰度帧（numpy 数组）。
        values：配置字典，语义同 :func:`resolve_rect`。

    Returns：
        同一个帧对象（原地修改）。帧为空或尺寸非法时原样返回。
    """
    if frame is None:
        return frame
    height, width = frame.shape[:2]
    rect = resolve_rect(values, width, height)
    if rect is None:
        return frame

    x1, y1, x2, y2 = rect
    if frame.ndim == 3:
        frame[y1:y2, x1:x2] = np.zeros((y2 - y1, x2 - x1, frame.shape[2]), dtype=frame.dtype)
    else:
        frame[y1:y2, x1:x2] = np.zeros((y2 - y1, x2 - x1), dtype=frame.dtype)
    return frame


def values_from_rect(rect, width, height, base=None):
    """把用户拖出来的矩形反解成配置值。

    反解必须能**精确复现**同一个矩形，否则「拖到哪、存到哪」就不成立。这里不做
    「猜一个最像的锚点」，而是把 8 个锚点逐个试算、用 :func:`resolve_rect` 验证能否
    还原，再从中挑「偏移最小」的那个。

    为什么要挑偏移最小：同一个矩形往往能被多个锚点精确复现——例如 ``(0, 0, 166, 18)``
    既可以是「左上角 + 偏移 0」，也可以是「右下角 + 偏移 87%」。后者的语义是「遮罩本该
    在右下角，只是被推到了左上角」，与用户看到的完全不符。偏移总量最小的那个，才对应
    「遮罩确实贴在这条边上」。

    Args：
        rect：``(x1, y1, x2, y2)`` 帧像素矩形。
        width：帧宽。
        height：帧高。
        base：其余键的基准值，通常是上一次的配置；缺省用 :data:`DEFAULT_VALUES`。

    Returns：
        新的配置字典（含 ``KEY_ANCHOR`` 与四个数值键）。
    """
    values = dict(base or DEFAULT_VALUES)
    target = tuple(rect)
    previous = values.get(KEY_ANCHOR)

    best = None
    best_key = None
    for anchor in ANCHOR_ORDER:
        trial = _trial_values(values, anchor, target, width, height)
        if resolve_rect(trial, width, height) != target:
            continue
        cost = abs(trial[KEY_OFFSET_X]) + abs(trial[KEY_OFFSET_Y])
        # 代价相同时优先沿用上一次的锚点，避免同一位置在不同锚点间来回跳。
        key = (cost, anchor != previous)
        if best_key is None or key < best_key:
            best, best_key = trial, key

    if best is not None:
        return best

    # 没有任何锚点能逐像素还原（画面极小、百分比精度不够）。退到上一锚点，
    # 让遮罩落在用户拖出的位置附近，而不是跳回默认值。
    return _trial_values(values, previous if previous in ANCHOR_SIDES else ANCHOR_BOTTOM_RIGHT,
                         target, width, height)


def _trial_values(values, anchor, rect, width, height):
    """按给定锚点把矩形反解成一套配置值（不校验能否还原）。

    Args：
        values：基准配置。
        anchor：锚点标识。
        rect：目标矩形 ``(x1, y1, x2, y2)``。
        width：帧宽。
        height：帧高。

    Returns：
        新的配置字典。
    """
    trial = dict(values)
    trial[KEY_ANCHOR] = anchor
    trial[KEY_WIDTH] = _to_percent(rect[2] - rect[0], width)
    trial[KEY_HEIGHT] = _to_percent(rect[3] - rect[1], height)
    offset_x, offset_y = _offsets_for(anchor, rect, width, height)
    trial[KEY_OFFSET_X] = _to_percent(offset_x, width)
    trial[KEY_OFFSET_Y] = _to_percent(offset_y, height)
    return trial


def _offsets_for(anchor, rect, width, height):
    """按锚点反解出「背离所贴边缘」的偏移像素。

    Args：
        anchor：锚点标识。
        rect：目标矩形 ``(x1, y1, x2, y2)``。
        width：帧宽。
        height：帧高。

    Returns：
        ``(offset_x, offset_y)`` 像素值（可能为负）。
    """
    x1, y1, x2, y2 = rect
    sides = ANCHOR_SIDES[anchor]

    if sides[0] == "right":
        offset_x = width - x2
    elif sides[0] == "center":
        offset_x = x1 - (width - (x2 - x1)) // 2
    else:
        offset_x = x1

    if sides[1] == "bottom":
        offset_y = height - y2
    elif sides[1] == "center":
        offset_y = y1 - (height - (y2 - y1)) // 2
    else:
        offset_y = y1
    return offset_x, offset_y


def _to_percent(pixels, total):
    """把像素换算成百分比，取「能被 :func:`_scale_percent` 精确还原」的那个值。

    `_scale_percent` 用截断，因此一个像素值对应的是一段百分比**区间**而非单点：
    ``p`` 落在 ``[pixels/total*100, (pixels+1)/total*100)`` 内都会截断回 ``pixels``。
    取区间中点再保留 6 位小数，既避开两端边界（边界上的浮点误差会让还原落到相邻像素），
    又保证往返逐像素一致。

    为什么不直接 `round(pixels/total*100, 2)`：两位小数在 1920 宽下只有 19 像素的粒度，
    拖到的位置存下去会跑偏几像素，直接违反「拖到哪、存到哪」。

    Args：
        pixels：像素值（可为负——偏移允许把遮罩推出画面）。
        total：该轴总长。

    Returns：
        百分比（浮点）；``total`` 为 0 时返回 0.0。
    """
    if not total:
        return 0.0
    if pixels >= 0:
        low = pixels / total * 100.0
        high = (pixels + 1) / total * 100.0
    else:
        # 负数截断向零：可还原区间是开区间 (pixels-1, pixels]
        low = (pixels - 1) / total * 100.0
        high = pixels / total * 100.0
    return round((low + high) / 2.0, _PERCENT_DIGITS)


def _scale_percent(percent, total):
    """把百分比换算成像素。

    **用截断而非四舍五入**：上游 `make_bottom_right_black` 写的是
    ``int(0.13 * width)``。默认配置必须复现上游的像素结果，否则同一张画面在 1920 宽下
    会差 1 像素（round(0.13*1920)=250 而 int 得 249），与改造前就不是像素级一致了。
    差 1 像素在遮罩边缘是可见的细缝，且只在高分辨率下出现，最难排查。

    Args：
        percent：百分比值（``13.0`` 表示 13%）。
        total：该轴总长（像素）。

    Returns：
        像素整数（截断）。
    """
    return int(float(percent) / 100.0 * total)
