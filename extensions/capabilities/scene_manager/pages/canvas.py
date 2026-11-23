# -*- coding: utf-8 -*-
"""场景编辑用的可交互画布。

实现完全按上游模板标注编辑器的画布（`ok/ui/qt/tasks/MarkUpWindow.py` 的
`AnnotationCanvas`）重写：坐标换算、缩放、平移、手柄判定、绘制、右键菜单、
取色采样都取同一套口径，只把业务边界换成「多矩形 + 稳定 key + 发信号」。

设计要点：

1. **缩放以指针为锚点，1.1 倍/格，下限夹到适配视口**。再小只是把图缩成小点，
   没有查看价值，还会让用户以为「图丢了」。
2. **小于视口强制居中，大于视口夹到边界**。两侧不留黑边——留白会让用户以为
   「图没居中」，实际是夹取上限被放开了。
3. **平移只在图像超出视口时可用**。缩到适配时整幅都在视野里，没有可平移的东西；
   此时给「开手掌」光标是误导。
4. **手柄判定靠边距而非固定方块**：离某条边 ≤ 8px 即可拖动，不要求先选中。
5. **悬停/选中各有独立视觉**，且悬停时高亮即将拖动的那条边——用户能预判。
6. **位移相对起点换算一次**，不做增量累加：逐步取整会在放大后表现为「拖不动」。
"""

import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

# 边距判定容差（控件像素）。与上游 EDGE_MARGIN 同值。
EDGE_MARGIN = 8

# 手柄标识。与上游 HANDLE_* 取值一致，便于逐项对照。
HANDLE_NONE = 0
HANDLE_TOP = 1
HANDLE_BOTTOM = 2
HANDLE_LEFT = 3
HANDLE_RIGHT = 4
HANDLE_TL = 5
HANDLE_TR = 6
HANDLE_BL = 7
HANDLE_BR = 8

# 手柄 → 受影响的边。用「包含哪些方向字母」统一驱动调整与高亮。
HANDLE_SIDES = {
    HANDLE_TOP: ("n",),
    HANDLE_BOTTOM: ("s",),
    HANDLE_LEFT: ("w",),
    HANDLE_RIGHT: ("e",),
    HANDLE_TL: ("n", "w"),
    HANDLE_TR: ("n", "e"),
    HANDLE_BL: ("s", "w"),
    HANDLE_BR: ("s", "e"),
}

# 滚轮每格倍率与缩放上限。与上游同值。
WHEEL_FACTOR = 1.1
MAX_SCALE = 50.0

# 调整与新建的最小尺寸（图像像素）。与上游 min_size / 3px 门槛同值。
MIN_RESIZE = 5
MIN_DRAW = 3

# 操作模式。
MODE_SELECT = "select"
MODE_DRAW_FEATURE = "draw_feature"
MODE_DRAW_AREA = "draw_area"
MODE_DELETE = "delete"

# 绘制用色。
# 画布底色不写死：跟随主题的 `sidebar`，与标注编辑器一致。
#
# 写死一个深灰会在浅色主题下露出一圈深色边——用户看到的是「画布背后有一块黑色
# 背景」，而实际那只是画布自己的底色，与页面底色不匹配。跟随主题则两个窗口观感
# 一致，切换深色/浅色主题时也自动跟上。
BACKGROUND_COLOR_NAME = "sidebar"

# 主题不可用时的兜底色（浅灰，与浅色主题的 sidebar 接近）。
_FALLBACK_BG = QColor(239, 239, 241)
COLOR_SELECTED = QColor(255, 190, 60)
COLOR_HOVERED = QColor(120, 200, 255)
COLOR_HANDLE = QColor(0, 200, 0)
COLOR_LABEL_BG = QColor(0, 0, 0, 150)
COLOR_LABEL_TEXT = QColor(255, 255, 255)

_COLOR_CACHE = {}


def background_color():
    """返回画布底色，跟随主题。

    延迟取色而不是在模块加载时读一次：主题可能在运行期切换（设置页可改），
    缓存住会把旧主题的颜色固定下来。

    Returns:
        `QColor`。主题不可用时返回兜底浅灰。
    """
    try:
        from ok.ui.qt.kit import theme
    except ImportError:
        return QColor(_FALLBACK_BG)
    try:
        return QColor(theme.color(BACKGROUND_COLOR_NAME))
    except Exception:  # noqa: BLE001 — 主题名不存在时用兜底色，不崩画布
        return QColor(_FALLBACK_BG)


def detect_handle(pos, rect, margin=EDGE_MARGIN):
    """判断位置靠近矩形的哪条边或哪个角。

    先判角（两轴都近），再判边——与上游 `_detect_handle` 的判定顺序一致：角优先，
    否则在交点附近会随机命中一条边。

    Args：
        pos：`QPoint` 控件坐标。
        rect：`QRect` 控件坐标矩形。
        margin：容差像素。

    Returns：
        `HANDLE_*`；都不近时返回 `HANDLE_NONE`。
    """
    x, y = pos.x(), pos.y()
    rx, ry, rw, rh = rect.x(), rect.y(), rect.width(), rect.height()
    right = rx + rw
    bottom = ry + rh

    near_left = abs(x - rx) <= margin and ry - margin <= y <= bottom + margin
    near_right = abs(x - right) <= margin and ry - margin <= y <= bottom + margin
    near_top = abs(y - ry) <= margin and rx - margin <= x <= right + margin
    near_bottom = abs(y - bottom) <= margin and rx - margin <= x <= right + margin

    if near_top and near_left:
        return HANDLE_TL
    if near_top and near_right:
        return HANDLE_TR
    if near_bottom and near_left:
        return HANDLE_BL
    if near_bottom and near_right:
        return HANDLE_BR
    if near_top:
        return HANDLE_TOP
    if near_bottom:
        return HANDLE_BOTTOM
    if near_left:
        return HANDLE_LEFT
    if near_right:
        return HANDLE_RIGHT
    return HANDLE_NONE


def cursor_for_handle(handle):
    """返回手柄对应的调整光标。

    Args：
        handle：`HANDLE_*`。

    Returns：
        `Qt.CursorShape`。
    """
    if handle in (HANDLE_TL, HANDLE_BR):
        return Qt.CursorShape.SizeFDiagCursor
    if handle in (HANDLE_TR, HANDLE_BL):
        return Qt.CursorShape.SizeBDiagCursor
    if handle in (HANDLE_TOP, HANDLE_BOTTOM):
        return Qt.CursorShape.SizeVerCursor
    if handle in (HANDLE_LEFT, HANDLE_RIGHT):
        return Qt.CursorShape.SizeHorCursor
    return Qt.CursorShape.ArrowCursor


def high_contrast_color(seed):
    """按序号生成高对比的（描边色, 填充色）。

    用黄金分割比取色相并避开红蓝区间，与上游 `get_high_contrast_color` 同一算法。
    固定算法而非手写配色：框多时手写必然撞色，而撞色的两个框在画布上分不清。

    Args：
        seed：整数序号，通常取插入顺序。

    Returns：
        `(QColor, QColor)`，第二个带 40 alpha。
    """
    if seed in _COLOR_CACHE:
        return _COLOR_CACHE[seed]
    val = int(seed)
    hue = (val * 0.618033988749895) % 1.0
    if (0.0 <= hue <= 0.15) or (0.55 <= hue <= 0.65):
        hue = (hue + 0.25) % 1.0
    color = QColor.fromHsvF(hue, 0.7 + (val % 3) * 0.1, 0.6 + (val % 2) * 0.3)
    fill = QColor(color.red(), color.green(), color.blue(), 40)
    _COLOR_CACHE[seed] = (color, fill)
    return color, fill


class CanvasItem:
    """画布上的一个矩形。

    Attributes：
        key: 稳定标识，如 `("feature", "btn_ok")`。
        rect: 图像坐标 `(x1, y1, x2, y2)` 浮点元组。
        name: 显示名，画在框内左上。
        editable: 是否允许拖动与调整。
        color_seed: 高对比色种子；None 时由 `set_items` 按顺序补。
        explicit_color: 显式描边色；None 时用高对比色。
    """

    def __init__(self, key, rect, color=None, label="", editable=True, color_seed=None, name=None):
        """构造画布项。

        Args：
            key：稳定标识。
            rect：图像坐标矩形。
            color：显式描边色（可传 `#rrggbb` 或 `QColor`）；None 时用高对比色。
            label：显示名（与 `name` 等价，保留旧参数名）。
            editable：是否可拖动与调整。
            color_seed：高对比色种子。
            name：显示名；优先于 `label`。
        """
        self.key = key
        self.rect = tuple(float(value) for value in rect)
        self.name = name if name is not None else (label or "")
        self.editable = editable
        self.color_seed = color_seed
        self.explicit_color = QColor(color) if color else None

    @property
    def color(self):
        """返回描边色。

        显式色优先，否则按种子取高对比色——与绘制时同一来源，避免「画出来的颜色
        和读到的颜色不一致」。

        Returns：
            `#rrggbb` 字符串。
        """
        if self.explicit_color is not None:
            return self.explicit_color.name()
        return high_contrast_color(self.color_seed or 1)[0].name()

    @property
    def label(self):
        """返回显示名（兼容旧属性名）。

        Returns：
            显示名字符串。
        """
        return self.name

    @label.setter
    def label(self, value):
        """设置显示名（兼容旧属性名）。

        Args：
            value：新的显示名。
        """
        self.name = value or ""


class SceneCanvas(QWidget):
    """多矩形可交互画布。

    交互与上游标注编辑器画布一致：滚轮缩放、拖拽平移、边距手柄调整、框内拖动、
    右键菜单、悬停取色。
    """

    # 新画出一个矩形：图像坐标整数 `(x1, y1, x2, y2)`。
    rect_created = Signal(int, int, int, int)
    # 已有矩形被移动或调整：`(key, (x1, y1, x2, y2))`。
    rect_changed = Signal(object, tuple)
    # 选中项变化：key 或 None。
    selection_changed = Signal(object)
    # 缩放比例变化。
    zoom_changed = Signal(float)
    # 双击命中：key 或 None。
    double_clicked = Signal(object)
    # 右键命中某个框：`(action, key)`，action 固定为 `menu`，由调用方弹菜单。
    context_action = Signal(str, object)
    # 删除请求：key。
    delete_requested = Signal(object)
    # 复制请求：key。
    copy_requested = Signal(object)
    # 悬停取色：`(abs_x, abs_y, rel_x, rel_y, QColor 或 None)`。
    color_sampled = Signal(int, int, float, float, object)

    def __init__(self, parent=None):
        """构造空画布。"""
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setMinimumSize(420, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._image = None
        self._items = {}
        self._order = []
        self._selected = None
        self._hovered = None
        self._hovered_handle = HANDLE_NONE
        self._mode = MODE_SELECT
        self._preview_only = False
        self._mask_rect = None

        self._scale = 1.0
        self._fit_scale = 1.0
        self._offset = QPointF(0.0, 0.0)

        # 绘制中（控件坐标）。
        self._draw_start = None
        self._draw_preview = None

        # 移动（控件起点 + 图像原始矩形）。
        self._dragging = False
        self._drag_start = None
        self._drag_original = None

        # 平移（控件起点 + 起始偏移）。
        self._panning = False
        self._pan_start = None
        self._pan_origin = None

        # 调整（控件起点 + 图像原始矩形 + 手柄）。
        self._resizing = False
        self._resize_handle = HANDLE_NONE
        self._resize_start = None
        self._resize_original = None

    # --- 数据 ---------------------------------------------------------------

    def set_image(self, image, keep_view=False):
        """载入底图。

        Args：
            image：`QImage` 实例；None 表示清空。
            keep_view：为 True 时保留当前缩放与平移，只换底图像素。

        Note：
            默认取景为「整幅进视口」。弹窗改完一个框会重画底图，那种情况必须传
            `keep_view=True`——底图没换，没有理由重置视图。
        """
        self._image = image
        self._items = {}
        self._order = []
        self._selected = None
        self._hovered = None
        self._hovered_handle = HANDLE_NONE
        self._clear_interactions()
        if image is None or image.isNull():
            self._scale = 1.0
            self._fit_scale = 1.0
            self._offset = QPointF(0, 0)
        else:
            self._recalc_fit_scale()
            if keep_view and self._scale > 0:
                self._offset = self._clamp_offset(self._offset, self._scale)
            else:
                self._scale = self._fit_scale
                self._offset = self._centered_offset(self._scale)
        self.update()
        self.zoom_changed.emit(self._scale)

    def set_items(self, items):
        """一次替换全部矩形。

        Args：
            items：`CanvasItem` 列表，顺序即绘制顺序（后加的在上）。
        """
        self._items = {}
        self._order = []
        for index, item in enumerate(items):
            if item.color_seed is None:
                item.color_seed = index + 1
            self._items[item.key] = item
            self._order.append(item.key)
        if self._selected is not None and self._selected not in self._items:
            self._selected = None
            self.selection_changed.emit(None)
        if self._hovered is not None and self._hovered not in self._items:
            self._hovered = None
            self._hovered_handle = HANDLE_NONE
        self.update()

    def update_item(self, key, rect=None, label=None):
        """更新单个矩形。

        Args：
            key：矩形标识。
            rect：新的图像坐标矩形；None 表示不改。
            label：新的显示名；None 表示不改。
        """
        item = self._items.get(key)
        if item is None:
            return
        if rect is not None:
            item.rect = tuple(float(value) for value in rect)
        if label is not None:
            item.name = label
        self.update()

    def selected_key(self):
        """返回当前选中的 key。

        Returns：
            key 或 None。
        """
        return self._selected

    def set_selected(self, key):
        """设置选中项并发信号。

        Args：
            key：矩形标识；None 表示取消选中。
        """
        value = key if key in self._items else None
        if value == self._selected:
            return
        self._selected = value
        self.selection_changed.emit(value)
        self.update()

    def set_mask_rect(self, rect):
        """设置遮罩区域（图像坐标），只涂黑不画框。

        不画框是有意的：遮罩区不可操作，画框会让用户以为能拖。

        Args：
            rect：`(x1, y1, x2, y2)`；None 表示无遮罩。
        """
        self._mask_rect = tuple(float(value) for value in rect) if rect is not None else None
        self.update()

    def set_preview_only(self, value):
        """切换预览模式。

        预览模式下不响应选中、拖动、调整与右键菜单，只保留缩放与平移。

        Args：
            value：True 表示只读。
        """
        self._preview_only = bool(value)
        if self._preview_only:
            self._selected = None
            self._hovered = None
            self._hovered_handle = HANDLE_NONE
            self.selection_changed.emit(None)
        self.update()

    def is_preview_only(self):
        """返回是否处于预览模式。

        Returns：
            True 表示只读。
        """
        return self._preview_only

    def set_mode(self, mode):
        """切换操作模式。

        Args：
            mode：`MODE_SELECT` / `MODE_DRAW_FEATURE` / `MODE_DRAW_AREA` / `MODE_DELETE`。
        """
        self._mode = mode
        self._draw_start = None
        self._draw_preview = None
        self._clear_interactions()
        if self._is_draw_mode():
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif mode == MODE_DELETE:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._can_pan() else Qt.CursorShape.ArrowCursor)
        self.update()

    def mode(self):
        """返回当前操作模式。

        Returns：
            模式字符串。
        """
        return self._mode

    def frame_size(self):
        """返回底图尺寸。

        Returns：
            `(width, height)`；无底图时为 `(0, 0)`。
        """
        if self._image is None or self._image.isNull():
            return 0, 0
        return self._image.width(), self._image.height()

    def item_count(self):
        """返回矩形数量。

        Returns：
            数量。
        """
        return len(self._items)

    # --- 缩放与平移 ---------------------------------------------------------

    def zoom_scale(self):
        """返回当前缩放比例。

        Returns：
            控件像素 / 图像像素。
        """
        return self._scale

    def zoom_in(self):
        """放大一级（以视口中心为锚点）。"""
        self._zoom_at(self._scale * WHEEL_FACTOR, QPointF(self.width() / 2, self.height() / 2))

    def zoom_out(self):
        """缩小一级（以视口中心为锚点）。"""
        self._zoom_at(self._scale / WHEEL_FACTOR, QPointF(self.width() / 2, self.height() / 2))

    def zoom_fit(self):
        """取景为整幅进视口。"""
        if self._image is None or self._image.isNull():
            return
        self._recalc_fit_scale()
        self._scale = self._fit_scale
        self._offset = self._centered_offset(self._scale)
        self.update()
        self.zoom_changed.emit(self._scale)

    def zoom_reset(self):
        """回到 1:1 并居中。"""
        if self._image is None or self._image.isNull():
            return
        self._scale = 1.0
        self._offset = self._clamp_offset(self._centered_offset(1.0), 1.0)
        self.update()
        self.zoom_changed.emit(self._scale)

    def _zoom_at(self, target_scale, anchor):
        """以锚点为不动点缩放。

        记下锚点处的图像坐标，改比例后反算偏移，使该坐标仍落在锚点——否则放大后
        想看的细节会跑出视野，每次都要重新找。

        Args：
            target_scale：目标比例。
            anchor：锚点（控件坐标 `QPointF`）。
        """
        if self._image is None or self._image.isNull():
            return
        frame_x, frame_y = self._view_to_frame(anchor)
        new_scale = max(self._fit_scale, min(MAX_SCALE, target_scale))
        if abs(new_scale - self._scale) < 1e-9:
            return
        self._scale = new_scale
        self._offset = self._clamp_offset(
            QPointF(anchor.x() - frame_x * self._scale, anchor.y() - frame_y * self._scale),
            self._scale,
        )
        self.update()
        self.zoom_changed.emit(self._scale)

    def _recalc_fit_scale(self):
        """重算「整幅进视口」的比例。

        取两轴的最小值，允许放大——小图放大铺满视口是标注编辑器的既有行为。
        """
        if self._image is None or self._image.isNull():
            self._fit_scale = 1.0
            return
        image_w, image_h = self._image.width(), self._image.height()
        view_w, view_h = self.width(), self.height()
        if image_w <= 0 or image_h <= 0 or view_w <= 0 or view_h <= 0:
            self._fit_scale = 1.0
            return
        self._fit_scale = min(view_w / image_w, view_h / image_h)

    def fit_scale(self):
        """返回当前适配比例。

        Returns：
            浮点比例。
        """
        return self._fit_scale

    def _centered_offset(self, scale):
        """返回画面居中时的偏移。

        Args：
            scale：缩放比例。

        Returns：
            `QPointF`。
        """
        if self._image is None or self._image.isNull():
            return QPointF(0, 0)
        return QPointF(
            (self.width() - self._image.width() * scale) / 2,
            (self.height() - self._image.height() * scale) / 2,
        )

    def _clamp_offset(self, offset, scale):
        """把偏移夹到「画面不出视口边界」。

        画面小于视口时居中，大于时夹到 `[total - size, 0]`——两侧都不留黑边。
        留白会让用户以为「图没居中」，实际是夹取上限被放开了。

        Args：
            offset：期望偏移。
            scale：缩放比例。

        Returns：
            夹取后的偏移。
        """
        if self._image is None or self._image.isNull():
            return QPointF(0, 0)
        return QPointF(
            self._clamp_axis(offset.x(), self._image.width() * scale, self.width()),
            self._clamp_axis(offset.y(), self._image.height() * scale, self.height()),
        )

    @staticmethod
    def _clamp_axis(value, size, total):
        """在一条轴上夹取偏移。

        Args：
            value：期望偏移。
            size：画面在该轴的显示长度。
            total：视口在该轴的长度。

        Returns：
            夹取后的偏移；画面小于视口时返回居中位置。
        """
        if size <= total:
            return (total - size) / 2
        return min(0.0, max(total - size, value))

    def _can_pan(self):
        """返回画面是否超出视口（即可平移）。

        Returns：
            布尔值。缩到适配时整幅都在视野里，没有可平移的东西。
        """
        if self._image is None or self._image.isNull():
            return False
        return self._image.width() * self._scale > self.width() or self._image.height() * self._scale > self.height()

    # --- 坐标换算 -----------------------------------------------------------

    def _frame_to_view(self, point):
        """图像坐标 → 控件坐标。

        Args：
            point：`(x, y)` 图像坐标。

        Returns：
            `QPointF`。
        """
        return QPointF(point[0] * self._scale + self._offset.x(), point[1] * self._scale + self._offset.y())

    def _view_to_frame(self, point):
        """控件坐标 → 图像坐标。

        Args：
            point：`QPointF` 或 `QPoint`。

        Returns：
            `(x, y)` 浮点元组。
        """
        if not self._scale:
            return point.x(), point.y()
        return (point.x() - self._offset.x()) / self._scale, (point.y() - self._offset.y()) / self._scale

    def _item_view_rect(self, item):
        """矩形在控件上的显示区域。

        用整数矩形（与上游一致），命中判定更贴合实际显示像素。

        Args：
            item：`CanvasItem` 实例。

        Returns：
            `QRect`。
        """
        top_left = self._frame_to_view((item.rect[0], item.rect[1]))
        bottom_right = self._frame_to_view((item.rect[2], item.rect[3]))
        return QRect(
            int(top_left.x()),
            int(top_left.y()),
            int(bottom_right.x() - top_left.x()),
            int(bottom_right.y() - top_left.y()),
        )

    # --- 命中测试 -----------------------------------------------------------

    def _hit_item(self, pos):
        """返回鼠标下的矩形。

        逆序查找（后加的在上），与上游 `_find_ann_at` 一致。

        Args：
            pos：`QPoint` 控件坐标。

        Returns：
            `CanvasItem` 或 None。
        """
        if self._preview_only:
            return None
        for key in reversed(self._order):
            item = self._items[key]
            if self._item_view_rect(item).contains(pos):
                return item
        return None

    def _hit_any_handle(self, pos):
        """在所有可编辑项里找手柄命中。

        **不要求该项已选中**——离边 8px 就能拖，不必先点一下。

        Args：
            pos：`QPoint` 控件坐标。

        Returns：
            `(item, handle)`；未命中返回 `(None, HANDLE_NONE)`。
        """
        if self._preview_only:
            return None, HANDLE_NONE
        for key in reversed(self._order):
            item = self._items[key]
            if not item.editable:
                continue
            handle = detect_handle(pos, self._item_view_rect(item))
            if handle != HANDLE_NONE:
                return item, handle
        return None, HANDLE_NONE

    def _hit_handle(self, pos, item=None):
        """返回鼠标下的手柄。

        Args：
            pos：`QPoint` 控件坐标。
            item：指定项；None 时遍历所有可编辑项。

        Returns：
            `HANDLE_*`；未命中时返回 None。
        """
        if self._preview_only:
            return None
        if item is not None:
            if not item.editable:
                return None
            handle = detect_handle(pos, self._item_view_rect(item))
            return None if handle == HANDLE_NONE else handle
        handle = self._hit_any_handle(pos)[1]
        return None if handle == HANDLE_NONE else handle

    def _update_cursor(self, pos):
        """按悬停位置更新光标（兼容旧入口名）。

        Args：
            pos：`QPoint` 控件坐标。
        """
        self._hover_at(pos)

    # --- 鼠标与滚轮 ---------------------------------------------------------

    def wheelEvent(self, event):
        """滚轮以指针为锚点缩放。

        Args：
            event：滚轮事件。
        """
        if self._image is None or self._image.isNull():
            return
        frame_x, frame_y = self._view_to_frame(event.position())
        factor = WHEEL_FACTOR if event.angleDelta().y() > 0 else 1 / WHEEL_FACTOR
        new_scale = self._scale * factor
        new_scale = max(self._fit_scale, min(MAX_SCALE, new_scale))
        self._scale = new_scale
        self._offset = self._clamp_offset(
            QPointF(event.position().x() - frame_x * new_scale, event.position().y() - frame_y * new_scale),
            new_scale,
        )
        self.update()
        self.zoom_changed.emit(self._scale)
        event.accept()

    def resizeEvent(self, event):
        """控件尺寸变化时重算适配比例并重夹偏移。

        必须重算：`set_image` 常在控件完成布局前被调用，那一刻宽高是默认值，
        算出的比例可能是 1.0；之后控件变大却不再更新，图会按原尺寸铺出去。

        Args：
            event：尺寸事件。
        """
        super().resizeEvent(event)
        if self._image is None or self._image.isNull():
            return
        self._recalc_fit_scale()
        if self._scale < self._fit_scale:
            self._scale = self._fit_scale
        self._offset = self._clamp_offset(self._offset, self._scale)
        self.update()
        self.zoom_changed.emit(self._scale)

    def leaveEvent(self, event):
        """鼠标离开控件时清悬停态。

        不清的话鼠标移出后那个框仍高亮，看起来像「还指着它」。

        Args：
            event：事件对象。
        """
        del event
        if self._hovered is not None or self._hovered_handle != HANDLE_NONE:
            self._hovered = None
            self._hovered_handle = HANDLE_NONE
            self.update()

    def mousePressEvent(self, event):
        """按下：右键请求菜单，左键按模式分派。

        Args：
            event：鼠标事件。
        """
        if self._image is None or self._image.isNull():
            return
        pos = event.position().toPoint()

        if event.button() == Qt.MouseButton.RightButton:
            if self._preview_only:
                return
            item = self._hit_item(pos)
            if item is not None:
                self.set_selected(item.key)
                self.context_action.emit("menu", item.key)
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._preview_only:
            if self._can_pan():
                self._start_pan(pos)
            return

        if self._is_draw_mode():
            self._draw_start = pos
            self._draw_preview = pos
            self.update()
            return

        if self._mode == MODE_DELETE:
            item = self._hit_item(pos)
            if item is not None:
                self.set_selected(item.key)
                self.delete_requested.emit(item.key)
            return

        item, handle = self._hit_any_handle(pos)
        if item is not None:
            self.set_selected(item.key)
            self._resizing = True
            self._resize_handle = handle
            self._resize_start = pos
            self._resize_original = item.rect
            return

        item = self._hit_item(pos)
        if item is not None and item.editable:
            self.set_selected(item.key)
            self._dragging = True
            self._drag_start = pos
            self._drag_original = item.rect
            return

        self.set_selected(None)
        if self._can_pan():
            self._start_pan(pos)

    def mouseMoveEvent(self, event):
        """移动：绘制预览 / 调整 / 移动 / 平移 / 更新悬停。

        Args：
            event：鼠标事件。
        """
        if self._image is None or self._image.isNull():
            return
        pos = event.position().toPoint()
        self._sample_color(pos)

        if self._draw_start is not None:
            self._draw_preview = pos
            self.update()
            return
        if self._resizing:
            self._resize_to(pos)
            self.update()
            return
        if self._dragging:
            self._move_to(pos)
            self.update()
            return
        if self._panning:
            self._pan_to(pos)
            self.update()
            return
        self._hover_at(pos)

    def mouseReleaseEvent(self, event):
        """松手：结束绘制/调整/移动/平移。

        不重算偏移（拖到哪儿停在哪儿），否则画面会当场弹回。

        Args：
            event：鼠标事件。
        """
        if self._draw_start is not None and event.button() == Qt.MouseButton.LeftButton:
            self._finish_draw(event.position().toPoint())
            return
        if self._resizing or self._dragging or self._panning:
            self._clear_interactions()
            self._hover_at(event.position().toPoint())

    def mouseDoubleClickEvent(self, event):
        """双击：发出命中的 key。

        第一次按下可能已经开始拖动，这里要先把矩形还原——否则「想打开属性，
        结果框被挪了几像素」。

        Args：
            event：鼠标事件。
        """
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._image is None or self._image.isNull():
            return
        if self._is_draw_mode() or self._mode == MODE_DELETE:
            return
        item = self._hit_item(event.position().toPoint())
        if item is not None and self._dragging and self._drag_original is not None:
            item.rect = self._drag_original
            self.rect_changed.emit(item.key, item.rect)
        self._clear_interactions()
        if item is not None:
            self.set_selected(item.key)
        self.update()
        self.double_clicked.emit(item.key if item is not None else None)

    # --- 内部操作 -----------------------------------------------------------

    def _is_draw_mode(self):
        """返回当前是否处于绘制模式。

        Returns：
            布尔值。
        """
        return self._mode in (MODE_DRAW_FEATURE, MODE_DRAW_AREA)

    def _clear_interactions(self):
        """清空全部拖拽/绘制状态。"""
        self._dragging = False
        self._drag_start = None
        self._drag_original = None
        self._resizing = False
        self._resize_handle = HANDLE_NONE
        self._resize_start = None
        self._resize_original = None
        self._panning = False
        self._pan_start = None
        self._pan_origin = None
        self._draw_start = None
        self._draw_preview = None

    def _start_pan(self, pos):
        """开始平移。

        Args：
            pos：`QPoint` 控件坐标。
        """
        self._panning = True
        self._pan_start = pos
        self._pan_origin = QPointF(self._offset)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _pan_to(self, pos):
        """按位移更新偏移。

        Args：
            pos：`QPoint` 控件坐标。
        """
        if self._pan_start is None or self._pan_origin is None:
            return
        delta = pos - self._pan_start
        self._offset = self._clamp_offset(
            QPointF(self._pan_origin.x() + delta.x(), self._pan_origin.y() + delta.y()), self._scale
        )

    def _move_to(self, pos):
        """按位移移动矩形。

        位移相对起点换算一次，不做增量累加：逐步取整会在放大后表现为「拖不动」。

        Args：
            pos：`QPoint` 控件坐标。
        """
        item = self._items.get(self._selected)
        if item is None or self._drag_original is None or self._drag_start is None:
            return
        delta_x = (pos.x() - self._drag_start.x()) / self._scale if self._scale else 0
        delta_y = (pos.y() - self._drag_start.y()) / self._scale if self._scale else 0
        item.rect = self._moved_rect(self._drag_original, delta_x, delta_y)
        self.rect_changed.emit(item.key, item.rect)

    def _resize_to(self, pos):
        """按手柄调整矩形。

        Args：
            pos：`QPoint` 控件坐标。
        """
        item = self._items.get(self._selected)
        if item is None or self._resize_original is None or self._resize_start is None:
            return
        delta_x = (pos.x() - self._resize_start.x()) / self._scale if self._scale else 0
        delta_y = (pos.y() - self._resize_start.y()) / self._scale if self._scale else 0
        item.rect = self._resized_rect(self._resize_original, self._resize_handle, delta_x, delta_y)
        self.rect_changed.emit(item.key, item.rect)

    def _finish_draw(self, end_pos):
        """完成一次绘制并发信号。

        Args：
            end_pos：`QPoint` 控件坐标（松手位置）。
        """
        start = self._draw_start
        self._draw_start = None
        self._draw_preview = None
        self.update()
        if start is None:
            return
        x1, y1 = self._view_to_frame(start)
        x2, y2 = self._view_to_frame(end_pos)
        frame_w, frame_h = self.frame_size()
        left = int(min(max(0.0, min(x1, x2)), float(frame_w)))
        top = int(min(max(0.0, min(y1, y2)), float(frame_h)))
        right = int(min(max(0.0, max(x1, x2)), float(frame_w)))
        bottom = int(min(max(0.0, max(y1, y2)), float(frame_h)))
        if right - left < MIN_DRAW or bottom - top < MIN_DRAW:
            return
        self.rect_created.emit(left, top, right, bottom)

    def _hover_at(self, pos):
        """更新悬停项、悬停手柄与光标。

        顺序：手柄 → 框体 → 空白。手柄优先，因为贴边处拖手柄比拖框更常见。

        Args：
            pos：`QPoint` 控件坐标。
        """
        if self._is_draw_mode():
            self._set_hovered(None, HANDLE_NONE)
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._mode == MODE_DELETE:
            item = self._hit_item(pos)
            self._set_hovered(item.key if item else None, HANDLE_NONE)
            self.setCursor(Qt.CursorShape.PointingHandCursor if item else Qt.CursorShape.ArrowCursor)
            return
        item, handle = self._hit_any_handle(pos)
        if item is not None:
            self._set_hovered(item.key, handle)
            self.setCursor(cursor_for_handle(handle))
            return
        item = self._hit_item(pos)
        if item is not None:
            self._set_hovered(item.key, HANDLE_NONE)
            self.setCursor(Qt.CursorShape.ArrowCursor if not item.editable else Qt.CursorShape.SizeAllCursor)
            return
        self._set_hovered(None, HANDLE_NONE)
        self.setCursor(Qt.CursorShape.OpenHandCursor if self._can_pan() else Qt.CursorShape.ArrowCursor)

    def _set_hovered(self, key, handle):
        """设置悬停项与手柄，变化时重绘。

        Args：
            key：悬停项 key 或 None。
            handle：`HANDLE_*`。
        """
        if key == self._hovered and handle == self._hovered_handle:
            return
        self._hovered = key
        self._hovered_handle = handle
        self.update()

    def _sample_color(self, pos):
        """采样鼠标处的像素颜色并发信号。

        越界时只发坐标、颜色为 None——界面可以据此显示「画面外」。

        Args：
            pos：`QPoint` 控件坐标。
        """
        if self._image is None or self._image.isNull():
            return
        frame_x, frame_y = self._view_to_frame(pos)
        ix, iy = int(frame_x), int(frame_y)
        width, height = self._image.width(), self._image.height()
        rel_x = ix / width if width else 0.0
        rel_y = iy / height if height else 0.0
        if 0 <= ix < width and 0 <= iy < height:
            self.color_sampled.emit(ix, iy, rel_x, rel_y, self._image.pixelColor(ix, iy))
        else:
            self.color_sampled.emit(ix, iy, rel_x, rel_y, None)

    def _moved_rect(self, rect, dx, dy):
        """整体平移矩形并夹在画面内。

        Args：
            rect：起始矩形。
            dx：图像坐标位移。
            dy：图像坐标位移。

        Returns：
            新矩形元组。
        """
        x1, y1, x2, y2 = rect
        width, height = x2 - x1, y2 - y1
        frame_w, frame_h = self.frame_size()
        new_x = max(0.0, min(x1 + dx, max(0.0, frame_w - width)))
        new_y = max(0.0, min(y1 + dy, max(0.0, frame_h - height)))
        return (new_x, new_y, new_x + width, new_y + height)

    def _resized_rect(self, rect, handle, dx, dy):
        """按手柄调整矩形。

        允许翻转（拖过对边），由 :meth:`_normalize` 排序纠正——比钉住对边自然：
        钉住后继续拖不会有任何变化，用户以为卡住了。

        Args：
            rect：起始矩形。
            handle：`HANDLE_*`。
            dx：图像坐标位移。
            dy：图像坐标位移。

        Returns：
            新矩形元组。
        """
        sides = handle if isinstance(handle, str) else HANDLE_SIDES.get(handle, ())
        x1, y1, x2, y2 = rect
        if "w" in sides:
            x1 += dx
        if "e" in sides:
            x2 += dx
        if "n" in sides:
            y1 += dy
        if "s" in sides:
            y2 += dy
        return self._normalize(x1, y1, x2, y2)

    def _normalize(self, x1, y1, x2, y2):
        """纠正翻转、夹进画面、保证最小尺寸。

        Args：
            x1：左上 x。
            y1：左上 y。
            x2：右下 x。
            y2：右下 y。

        Returns：
            合法矩形元组。
        """
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        frame_w, frame_h = self.frame_size()
        left = min(max(0.0, left), float(frame_w))
        right = min(max(0.0, right), float(frame_w))
        top = min(max(0.0, top), float(frame_h))
        bottom = min(max(0.0, bottom), float(frame_h))
        if right - left < MIN_RESIZE:
            right = min(float(frame_w), left + MIN_RESIZE)
        if bottom - top < MIN_RESIZE:
            bottom = min(float(frame_h), top + MIN_RESIZE)
        return (left, top, right, bottom)

    # --- 绘制 ---------------------------------------------------------------

    def paintEvent(self, event):
        """绘制底图、遮罩、矩形、绘制预览。

        Args：
            event：绘制事件。
        """
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), background_color())

        if self._image is None or self._image.isNull():
            painter.setPen(QPen(QColor(130, 130, 130)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "尚无截图")
            painter.end()
            return

        painter.drawImage(
            QRectF(
                self._offset.x(),
                self._offset.y(),
                self._image.width() * self._scale,
                self._image.height() * self._scale,
            ),
            self._image,
        )

        # 遮罩先于元素：它属于底图的一部分，元素应压在它上面——这样能看清
        # 「这个框落在遮罩里」，而那正是要被警告的情形。
        if self._mask_rect is not None:
            painter.fillRect(
                QRectF(
                    self._frame_to_view((self._mask_rect[0], self._mask_rect[1])),
                    self._frame_to_view((self._mask_rect[2], self._mask_rect[3])),
                ),
                QColor(0, 0, 0),
            )

        for key in self._order:
            self._paint_item(painter, self._items[key])

        if self._draw_start is not None and self._draw_preview is not None:
            painter.setPen(QPen(COLOR_HANDLE, 2, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(0, 200, 0, 30)))
            painter.drawRect(QRect(self._draw_start, self._draw_preview).normalized())
            painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.end()

    def _paint_item(self, painter, item):
        """绘制单个矩形、悬停手柄高亮与标签。

        三档视觉：选中（强调色 + 50 alpha）、悬停（悬停色 + 30 alpha，并高亮即将
        拖动的边）、普通（高对比色 + 40 alpha）。填充必须有——只描边时框内仍是原图，
        框密的地方分不清哪个框在哪。

        Args：
            painter：`QPainter`。
            item：`CanvasItem` 实例。
        """
        rect = self._item_view_rect(item)
        is_selected = item.key == self._selected
        is_hovered = item.key == self._hovered

        if item.explicit_color is not None:
            base = item.explicit_color
            fill = QColor(base)
            fill.setAlpha(40)
        else:
            base, fill = high_contrast_color(item.color_seed or 1)

        if is_selected:
            pen_color = QColor(COLOR_SELECTED)
            fill = QColor(COLOR_SELECTED)
            fill.setAlpha(50)
        elif is_hovered:
            pen_color = QColor(COLOR_HOVERED)
            fill = QColor(COLOR_HOVERED)
            fill.setAlpha(30)
        else:
            pen_color = QColor(base)

        painter.setPen(QPen(pen_color, 2))
        painter.setBrush(QBrush(fill))
        painter.drawRect(rect)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if is_hovered and self._hovered_handle != HANDLE_NONE:
            self._paint_handle_highlight(painter, rect, self._hovered_handle)

        if item.name:
            self._paint_label(painter, rect, item.name, pen_color)

    def _paint_handle_highlight(self, painter, rect, handle):
        """悬停手柄时加粗对应的边，角上画点。

        让用户在实际拖动前就知道「拖的是哪条边」。

        Args：
            painter：`QPainter`。
            rect：`QRect` 控件矩形。
            handle：`HANDLE_*`。
        """
        painter.setPen(QPen(COLOR_HANDLE, 3))
        left, top = rect.x(), rect.y()
        right, bottom = rect.x() + rect.width(), rect.y() + rect.height()
        sides = HANDLE_SIDES.get(handle, ())
        if "n" in sides:
            painter.drawLine(left, top, right, top)
        if "s" in sides:
            painter.drawLine(left, bottom, right, bottom)
        if "w" in sides:
            painter.drawLine(left, top, left, bottom)
        if "e" in sides:
            painter.drawLine(right, top, right, bottom)
        if handle in (HANDLE_TL, HANDLE_TR, HANDLE_BL, HANDLE_BR):
            corner = {
                HANDLE_TL: (left, top),
                HANDLE_TR: (right, top),
                HANDLE_BL: (left, bottom),
                HANDLE_BR: (right, bottom),
            }[handle]
            painter.setBrush(QBrush(COLOR_HANDLE))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPoint(corner[0], corner[1]), 5, 5)
            painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_label(self, painter, rect, name, color):
        """在框内左上角绘制名称。

        画在框内而不是框外：框外的位置会被相邻框覆盖，而框内左下留白通常不影响
        识别。加一层深色底，避免与画面内容同色看不清。

        Args：
            painter：`QPainter`。
            rect：`QRect` 控件矩形。
            name：显示名。
            color：描边色（用于兜底，实际文字用白色）。
        """
        del color
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_width = metrics.horizontalAdvance(name)
        text_height = metrics.height()
        # 框太小时不画标签：标签比框还大会盖住整个框，反而看不清框在哪。
        if text_width + 4 > rect.width() or text_height + 2 > rect.height():
            return
        painter.fillRect(QRect(rect.x() + 1, rect.y() + 1, text_width + 4, text_height), COLOR_LABEL_BG)
        painter.setPen(QPen(COLOR_LABEL_TEXT))
        painter.drawText(rect.x() + 3, rect.y() + metrics.ascent() + 2, name)


def distance(a, b):
    """返回两点距离（供测试与调试）。

    Args：
        a：`(x, y)`。
        b：`(x, y)`。

    Returns：
        欧氏距离。
    """
    return math.hypot(a[0] - b[0], a[1] - b[1])
