# -*- coding: utf-8 -*-
"""截图遮罩页签：在真实画面上拖出遮罩范围。

交互约定：

- 「启用遮罩」开关放在预览工具条最左，位于缩放按钮之前。开关关闭时预览只显示原始画面，
  不画矩形与手柄——此时没有任何可编辑对象，留一个不可编辑的框只会让人以为它在生效。
- 拖拽只改界面上的临时值，点「保存配置」才写配置。这是需求 R6 的核心：编辑即落盘会让
  「重置为配置值」永远无事可做，误拖也无法撤销。
- 开关本身**不参与**这套分离：它是全局开关，即时落盘符合「开关就该立刻生效」的预期。

预览用**未经遮罩处理的原始帧**：若预览也走一遍遮罩，那块区域本来就是黑的，调整时看不出
边界，也没法确认遮罩是否落在想要的位置。
"""

from ok import og
from ok.ui.qt.kit import Button, Label, Switch
from ok.ui.qt.kit.theme import theme
from ok.ui.qt.widget.CustomTab import CustomTab
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from extensions.capabilities.screenshot_mask import geometry, processor

# 「适应窗口」显示留白。为 0 表示画面直接贴住控件边缘——这两个动作的语义是
# 「尽量看清整幅画面」，留白只会白缩一圈，而贴边手柄的遮挡由下面的平移留白接管。
PREVIEW_PADDING = 0

# 手动平移时的留白。拖拽时用间隔，用户才能明确看到「画面到此为止」，
# 而不是靠一条紧贴控件边缘的线去猜；同时给贴边的手柄留出抓取空间。
DRAG_PADDING = 60

# 手柄边长与命中容差（像素，控件坐标）。
HANDLE_SIZE = 10
HANDLE_HIT_PADDING = 4

# 缩放上下限与步进。
MIN_SCALE = 0.05
MAX_SCALE = 20.0
ZOOM_STEP = 1.25

# 拖拽时的最小矩形边长（帧像素）。允许拖成 0 会让遮罩彻底消失，且无法再抓回。
MIN_RECT_SIZE = 4

# 8 个手柄：4 角 + 4 边中点。手柄画在矩形外侧并跨骑边界——遮罩常常很薄
# （默认只有画面高的 2.5%），居中手柄会叠成一团，抓不到对应的边。
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

HANDLE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
}


def _to_qrect(rect):
    """把 ``(x1, y1, x2, y2)`` 换算成宽高正确的 ``QRect``。

    ``QRect(*rect)`` 会把 ``(1114, 702, 1280, 720)`` 读成「宽 1280、高 720」，
    于是遮罩铺满整幅画面、手柄全跑到画外。默认的右下角遮罩恰好贴着右下边界，
    肉眼看「差不多」就漏过了——因此这里单独成一个函数，任何地方都不直接用
    ``QRect(*rect)``。

    Args：
        rect：``(x1, y1, x2, y2)``。

    Returns：
        ``QRect``，宽高由差值算出。
    """
    x1, y1, x2, y2 = rect
    return QRect(int(x1), int(y1), int(x2 - x1), int(y2 - y1))


def _clamp_interval(start, end, total, delta, move_start):
    """把一次单边拖拽的位移夹到合法范围，返回被拖那条边的新坐标。

    被拖的边越过对边时，**由被拖的那一侧让位**，保证边始终跟着鼠标。若让对边让位，
    用户会看到「拖左边，右边在动」，这与框选交互的直觉相悖。

    Args：
        start：该轴区间起点（拖拽前）。
        end：该轴区间终点（拖拽前）。
        total：该轴总长。
        delta：位移（像素，已换算到帧坐标）。
        move_start：True 表示拖的是起点侧，False 表示终点侧。

    Returns：
        被拖那条边的新坐标。
    """
    if move_start:
        return min(max(start + delta, 0), end - MIN_RECT_SIZE)
    return min(max(end + delta, start + MIN_RECT_SIZE), total)


class PreviewCanvas(QWidget):
    """显示原始帧与可拖拽遮罩的预览画布。

    只负责「把帧画出来、把拖拽换算成帧矩形」，不读写配置——保存与重置由页签决定，
    这样「编辑」与「落盘」的边界唯一。
    """

    rect_changed = Signal(tuple)
    zoom_changed = Signal(float)

    def __init__(self, parent=None):
        """构造空画布。"""
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self._frame = None
        self._rect = None
        self._scale = 1.0
        self._offset = QPointF(0, 0)

        self._drag_mode = None
        self._drag_handle = None
        self._drag_origin = QPointF()
        self._pan_offset = QPointF(0, 0)
        self._drag_rect = None

        theme.changed.connect(self.update)

    # --- 数据 ---------------------------------------------------------------

    def set_frame(self, image):
        """载入预览帧并取景为「整幅进视口」。

        Args：
            image：``QImage``；``None`` 表示清空。
        """
        self._frame = image
        if image is None:
            self._rect = None
            self._offset = QPointF(0, 0)
        else:
            self._scale = self._fit_scale()
            self._offset = self._clamp_offset(self._centered_offset(self._scale), self._scale)
        self.update()
        self._emit_zoom()

    def frame(self):
        """返回当前预览帧（可能为 ``None``）。"""
        return self._frame

    def set_rect(self, rect):
        """设置遮罩矩形（帧坐标）。

        Args：
            rect：``(x1, y1, x2, y2)``；``None`` 表示不显示。
        """
        self._rect = tuple(float(value) for value in rect) if rect is not None else None
        self.update()

    def mask_rect(self):
        """返回当前遮罩矩形（帧坐标整数，可能为 ``None``）。

        内部保存的是浮点值，对外一律取整——配置里存的是整像素，界面比较也要可预期。
        刻意不叫 ``rect()``：``QWidget.rect()`` 已被框架用来表示控件自身区域，覆写它会让
        ``paintEvent`` 里的 ``fillRect(self.rect(), ...)`` 拿到遮罩矩形，表现为画布底色
        只填了遮罩那一块。
        """
        if self._rect is None:
            return None
        return tuple(int(round(value)) for value in self._rect)

    # --- 缩放 ---------------------------------------------------------------

    def zoom_scale(self):
        """当前缩放比例（控件像素 / 帧像素）。"""
        return self._scale

    def zoom_in(self):
        """放大一级。"""
        self._set_scale(self._scale * ZOOM_STEP)

    def zoom_out(self):
        """缩小一级。"""
        self._set_scale(self._scale / ZOOM_STEP)

    def zoom_reset(self):
        """回到 1:1，并把遮罩移进可见范围。"""
        self._scale = 1.0
        self._offset = self._centered_offset(1.0)
        if self._rect is not None:
            self._offset = self._offset_for_rect(self._rect, 1.0)
        self.update()
        self._emit_zoom()

    def zoom_fit(self):
        """一次性取景：按当前控件尺寸算比例，整幅画面进视口并居中。

        **不保留任何状态**。适应窗口只是「此刻把比例算成多少」的一次动作，算完就忘：
        之后控件缩放、用户拖动，都不会因为「还处于适应模式」而重算。这样才不会出现
        「点了适应，拖动时比例突然变」这种交互。窗口尺寸随后变化也不自动跟随——
        用户想重新取景再点一次即可。
        """
        self._scale = self._fit_scale()
        self._offset = self._clamp_offset(self._centered_offset(self._scale), self._scale)
        self.update()
        self._emit_zoom()

    def _set_scale(self, scale):
        """设置缩放比例并夹到上下限。

        Args：
            scale：目标比例。
        """
        self._scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        self._offset = self._clamp_offset(self._offset, self._scale)
        self.update()
        self._emit_zoom()

    def _fit_scale(self):
        """算出「整幅画面进视口」所需的比例。

        `PREVIEW_PADDING` 为 0 时画面正好占满视口的一边，另一边居中留黑边。
        """
        if self._frame is None or self._frame.isNull():
            return 1.0
        available_width = max(1, self.width() - 2 * PREVIEW_PADDING)
        available_height = max(1, self.height() - 2 * PREVIEW_PADDING)
        return min(available_width / self._frame.width(), available_height / self._frame.height())

    def _centered_offset(self, scale):
        """让画面在控件中居中的偏移。"""
        if self._frame is None or self._frame.isNull():
            return QPointF(0, 0)
        width = self._frame.width() * scale
        height = self._frame.height() * scale
        return QPointF((self.width() - width) / 2, (self.height() - height) / 2)

    def _clamp_offset(self, offset, scale, padding=None):
        """把偏移夹到「任一侧空白不超过 padding 像素」的范围。

        判据只有一条：**画面两侧各自最多拖出 padding 像素空白**。不按「画面比视口大还是小」
        分情况——那样会得到两套语义（大画面限「空白」、小画面限「可见」），同一张画面在
        不同缩放下可拖幅度相差一个数量级，用户看到的就是「有时能拖很宽，有时只能拖 60」。

        Args：
            offset：期望偏移。
            scale：当前比例。
            padding：单侧最大空白；缺省取 :data:`DRAG_PADDING`。

        Returns：
            夹取后的偏移。
        """
        if self._frame is None or self._frame.isNull():
            return QPointF(0, 0)
        if padding is None:
            padding = DRAG_PADDING
        width = self._frame.width() * scale
        height = self._frame.height() * scale
        return QPointF(
            self._clamp_axis(offset.x(), width, self.width(), padding),
            self._clamp_axis(offset.y(), height, self.height(), padding),
        )

    def _clamp_axis(self, value, size, total, padding):
        """在一条轴上把偏移夹到「两侧空白各不超过 padding」的范围。

        设偏移为 ``o``、画面尺寸为 ``size``、视口为 ``total``，则左侧空白是 ``o``、
        右侧空白是 ``total - size - o``。两者都不超过 ``padding`` 即：

        ``o ∈ [total - size - padding, padding]``

        画面比视口小时这个区间仍然成立（此时下限为负），意味着允许画面离开边界、露出底色。

        **画面太小导致区间倒置时（``size < total - 2*padding``），固定居中**：此时不可能
        让两侧空白都 ≤padding，任何位置都违反判据。居中至少左右对称，不会出现「这次能拖
        很宽、下次拖不动」的漂移感。

        Args：
            value：期望偏移。
            size：画面在该轴上的显示尺寸。
            total：视口在该轴上的长度。
            padding：单侧最大空白。

        Returns：
            夹取后的偏移。
        """
        low = total - size - padding
        high = padding
        if low > high:
            # 画面太小，两侧空白必然超过 padding：居中，两侧一致
            return (total - size) / 2
        return min(max(value, low), high)

    def _offset_for_rect(self, rect, scale):
        """算出让给定矩形进入视野的偏移（把它移到视口中央）。"""
        view_rect = _to_qrect(rect)
        target = QPointF(
            self.width() / 2 - (view_rect.x() + view_rect.width() / 2) * scale,
            self.height() / 2 - (view_rect.y() + view_rect.height() / 2) * scale,
        )
        return self._clamp_offset(target, scale)

    def _geometry_offset(self):
        """当前绘制的偏移。"""
        return self._offset

    def _emit_zoom(self):
        """通知外部比例已变化。"""
        self.zoom_changed.emit(self._scale)

    # --- 坐标换算 -----------------------------------------------------------

    def _frame_to_view(self, point):
        """帧坐标 → 控件坐标。

        Args：
            point：``(x, y)`` 或 ``QPointF``。

        Returns：
            ``QPointF``。
        """
        if isinstance(point, QPointF):
            x, y = point.x(), point.y()
        elif isinstance(point, QPoint):
            x, y = point.x(), point.y()
        else:
            x, y = point
        scale = self.zoom_scale()
        offset = self._geometry_offset()
        return QPointF(x * scale + offset.x(), y * scale + offset.y())

    def _view_to_frame(self, point):
        """控件坐标 → 帧坐标。

        Args：
            point：``QPoint`` 或 ``QPointF``。

        Returns：
            ``QPointF``。
        """
        scale = self.zoom_scale()
        offset = self._geometry_offset()
        return QPointF((point.x() - offset.x()) / scale, (point.y() - offset.y()) / scale)

    def _handle_rect(self, handle):
        """返回某个手柄在控件坐标下的矩形（跨骑矩形边界）。"""
        if self._rect is None:
            return QRect()
        x1, y1, x2, y2 = self._rect
        centers = {
            "nw": (x1, y1),
            "n": ((x1 + x2) / 2, y1),
            "ne": (x2, y1),
            "e": (x2, (y1 + y2) / 2),
            "se": (x2, y2),
            "s": ((x1 + x2) / 2, y2),
            "sw": (x1, y2),
            "w": (x1, (y1 + y2) / 2),
        }
        center = self._frame_to_view(centers[handle])
        half = HANDLE_SIZE / 2
        return QRectF(center.x() - half, center.y() - half, HANDLE_SIZE, HANDLE_SIZE)

    def _hit_handle(self, position):
        """判断控件坐标落在了哪个手柄上。

        两条规则，都来自「矩形小的时候手柄会互相挤占」：

        1. **角优先于边**：角手柄与边中点重叠时，若按固定顺序返回边手柄，用户点角落
           会得到「只能上下拉」的手柄。
        2. **矩形内部深处不算命中手柄**：手柄跨骑边界、且有 4px 容差，矩形很小时
           （缩小到 10% 后只有 30×10 像素）四个手柄几乎盖满整块区域。此时若整块内部都
           判成手柄，点遮罩中间会变成缩放，用户怎么拖都平移不了。

        判据是「离四条边都超过手柄尺寸」才算内部深处——手柄中心正落在边界上，
        若用「在矩形内即不算」会把贴边的手柄整条废掉。

        Args：
            position：``QPoint`` 控件坐标。

        Returns：
            手柄名，未命中返回 ``None``。
        """
        if self._rect is None:
            return None
        order = ("nw", "ne", "se", "sw", "n", "e", "s", "w")
        for handle in order:
            rect = self._handle_rect(handle).adjusted(
                -HANDLE_HIT_PADDING, -HANDLE_HIT_PADDING, HANDLE_HIT_PADDING, HANDLE_HIT_PADDING
            )
            if rect.contains(QPointF(position)):
                return handle
        return None

    def _in_rect_interior(self, position):
        """判断控件坐标是否落在遮罩矩形的内部深处（远离四条边）。

        远离改边的距离取手柄边长：手柄中心在边界上，半个手柄伸进内部，因此
        「距边超过一个手柄边长」才算真正的内部，那里点下去一定是整体平移。

        Args：
            position：``QPoint`` 控件坐标。

        Returns：
            布尔值。
        """
        if self._rect is None:
            return False
        top_left = self._frame_to_view((self._rect[0], self._rect[1]))
        bottom_right = self._frame_to_view((self._rect[2], self._rect[3]))
        margin = HANDLE_SIZE
        inner = QRectF(top_left, bottom_right).adjusted(margin, margin, -margin, -margin)
        return inner.contains(QPointF(position))

    # --- 拖拽 ---------------------------------------------------------------

    def _resized_rect(self, rect, handle, dx, dy):
        """按手柄拖拽量算出新矩形。

        每个手柄只改它所属的边，对边保持不动。返回**浮点值**：缩小显示时 1 帧像素不足
        1 个屏幕像素，若在这里取整，微小位移会被反复吃掉，表现为「拖了没反应」。

        Args：
            rect：原矩形 ``(x1, y1, x2, y2)``。
            handle：手柄名。
            dx：水平位移（帧像素，浮点）。
            dy：垂直位移（帧像素，浮点）。

        Returns：
            新矩形（浮点）。
        """
        x1, y1, x2, y2 = rect
        frame_width, frame_height = self.frame_size()

        if "w" in handle:
            x1 = _clamp_interval(x1, x2, frame_width, dx, True)
        if "e" in handle:
            x2 = _clamp_interval(x1, x2, frame_width, dx, False)
        if "n" in handle:
            y1 = _clamp_interval(y1, y2, frame_height, dy, True)
        if "s" in handle:
            y2 = _clamp_interval(y1, y2, frame_height, dy, False)
        return (x1, y1, x2, y2)

    def _moved_rect(self, rect, dx, dy):
        """按拖拽量整体平移矩形，尺寸不变且不越界。

        Args：
            rect：原矩形。
            dx：水平位移（帧像素，浮点）。
            dy：垂直位移（帧像素，浮点）。

        Returns：
            新矩形（浮点）。
        """
        x1, y1, x2, y2 = rect
        frame_width, frame_height = self.frame_size()
        width = x2 - x1
        height = y2 - y1
        x1 = min(max(0.0, x1 + dx), max(0.0, frame_width - width))
        y1 = min(max(0.0, y1 + dy), max(0.0, frame_height - height))
        return (x1, y1, x1 + width, y1 + height)

    def frame_size(self):
        """返回帧尺寸；无帧时返回 ``(0, 0)``。"""
        if self._frame is None or self._frame.isNull():
            return 0, 0
        return self._frame.width(), self._frame.height()

    def mousePressEvent(self, event):
        """开始拖手柄、拖遮罩内部，或平移画面。

        拖拽始终在**当前比例**下进行。比例只由缩放动作（适应窗口 / 1:1 / 放大缩小 /
        滚轮）改变，拖拽不会动它——所以按住画面移动时不会出现比例或位置的跳变。

        Args：
            event：鼠标按下事件。
        """
        if event.button() != Qt.MouseButton.LeftButton or self._rect is None:
            super().mousePressEvent(event)
            return
        position = event.position()
        point = position.toPoint()
        # 遮罩内部深处优先判为整体平移：缩得很小时手柄几乎盖满整块区域，
        # 若让手柄优先，点中间就变成缩放，用户根本平移不了。
        handle = None if self._in_rect_interior(point) else self._hit_handle(point)
        # 记浮点坐标：用 toPoint() 会先截断成整数，放大后每步 1px 折算不到 1 帧像素时，
        # 截断误差在多次移动间累积，画面就对不上鼠标了。
        self._drag_origin = QPointF(position)
        self._drag_rect = tuple(self._rect)
        if handle is not None:
            self._drag_mode = "resize"
            self._drag_handle = handle
        elif self._rect_contains(point):
            self._drag_mode = "move"
            self._drag_handle = None
        else:
            self._drag_mode = "pan"
            self._drag_handle = None
            # 平移以按下瞬间的偏移为基准，后续位移全部相对它计算。
            self._pan_offset = QPointF(self._offset)
        event.accept()

    def mouseMoveEvent(self, event):
        """拖拽中更新矩形；未拖拽时按悬停位置切换光标。

        **位移一律相对按下点计算，不按「上一次位置」增量累加**。原因有两个，都会让画面
        对不上鼠标：

        1. 增量累加要求每一步都能整除，而屏幕位移 ÷ 比例往往有小数。每步取整后再作为
           下一步基准，误差会一笔笔丢掉——缩小到 25% 时拖 100px 只走 95px。
        2. 放大后步长更小：200% 时鼠标每动 1px 折算 0.5 帧像素，逐步取整后永远是 0，
           表现为「完全拖不动」。

        用按下点作为固定基准，总位移只做一次换算与一次取整，误差不随步数累积。

        Args：
            event：鼠标移动事件。
        """
        if self._drag_mode is None:
            self._update_cursor(event.position().toPoint())
            super().mouseMoveEvent(event)
            return

        position = event.position()
        delta = position - self._drag_origin
        scale = self.zoom_scale() or 1.0
        dx = delta.x() / scale
        dy = delta.y() / scale

        if self._drag_mode == "pan":
            self._offset = self._clamp_offset(
                QPointF(self._pan_offset.x() + delta.x(), self._pan_offset.y() + delta.y()),
                self.zoom_scale(),
            )
        elif self._drag_mode == "resize":
            self._rect = self._resized_rect(self._drag_rect, self._drag_handle, dx, dy)
            self.rect_changed.emit(self._rect)
        else:
            self._rect = self._moved_rect(self._drag_rect, dx, dy)
            self.rect_changed.emit(self._rect)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        """结束拖拽。

        **不重算偏移**：拖到哪儿就停在哪儿。松手时若再夹一次（例如把留白收小后重夹），
        画面会当场弹回去，用户拖了半天等于白拖——这是必须避免的。
        """
        self._drag_mode = None
        self._drag_handle = None
        self._update_cursor(event.position().toPoint())
        event.accept()

    def _rect_contains(self, position):
        """判断控件坐标是否落在遮罩矩形内。"""
        if self._rect is None:
            return False
        top_left = self._frame_to_view((self._rect[0], self._rect[1]))
        bottom_right = self._frame_to_view((self._rect[2], self._rect[3]))
        return QRectF(top_left, bottom_right).contains(QPointF(position))

    def _update_cursor(self, position):
        """按悬停位置设置光标形状。

        平移光标按「画面是否真的可平移」判断，而不是按某个模式标志：画面在任一轴大于
        视口才显示抓手，否则显示箭头。

        Args：
            position：``QPoint`` 控件坐标。
        """
        handle = self._hit_handle(position)
        if handle is not None:
            self.setCursor(QCursor(HANDLE_CURSORS[handle]))
        elif self._rect_contains(position):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._can_pan():
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _can_pan(self):
        """判断画面是否可以被拖动。

        只要载入了画面就能拖：画面比视口大时是「平移看别处」，比视口小时是「挪开露底色」，
        两种情况都允许，因此不必再按尺寸分支。

        Returns：
            True 表示可以拖动。
        """
        return self._frame is not None and not self._frame.isNull()

    def wheelEvent(self, event):
        """滚轮围绕指针缩放。"""
        if self._frame is None or self._frame.isNull():
            event.accept()
            return
        steps = event.angleDelta().y() / 120.0
        if not steps:
            event.accept()
            return
        anchor = event.position()
        before = self._view_to_frame(anchor)
        self._scale = max(MIN_SCALE, min(MAX_SCALE, self._scale * (ZOOM_STEP ** steps)))
        after = self._view_to_frame(anchor)
        self._offset = self._clamp_offset(
            QPointF(
                self._offset.x() + (after.x() - before.x()) * self._scale,
                self._offset.y() + (after.y() - before.y()) * self._scale,
            ),
            self._scale,
        )
        self.update()
        self._emit_zoom()
        event.accept()

    # --- 绘制 ---------------------------------------------------------------

    def paintEvent(self, _event):
        """绘制背景、画面、遮罩与手柄。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(18, 18, 20))

        if self._frame is None or self._frame.isNull():
            painter.end()
            return

        scale = self.zoom_scale()
        offset = self._geometry_offset()
        target = QRectF(
            offset.x(), offset.y(), self._frame.width() * scale, self._frame.height() * scale
        )
        painter.drawImage(target, self._frame)

        if self._rect is not None:
            self._paint_mask(painter)
        painter.end()

    def _paint_mask(self, painter):
        """绘制半透明遮罩与 8 个手柄。"""
        top_left = self._frame_to_view((self._rect[0], self._rect[1]))
        bottom_right = self._frame_to_view((self._rect[2], self._rect[3]))
        rect = QRectF(top_left, bottom_right)

        painter.fillRect(rect, QColor(0, 0, 0, 150))
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
        painter.drawRect(rect)

        painter.setBrush(QColor(255, 255, 255, 240))
        painter.setPen(QPen(QColor(30, 30, 30), 1))
        for handle in HANDLES:
            painter.drawRect(self._handle_rect(handle))
        painter.setBrush(Qt.BrushStyle.NoBrush)


class ScreenshotMaskTab(CustomTab):
    """侧边栏「截图遮罩」页签。

    页签只负责「把遮罩长什么样告诉用户并让他改」，配置的真值仍在全局配置对象里。
    关闭开关时只隐藏编辑框，不动已保存的遮罩数值——重新开启后按原配置恢复。
    """

    icon = "image"

    def __init__(self):
        """构造页签并装配工具条与画布。"""
        super().__init__()
        self._pending = None

        self.canvas = PreviewCanvas(self)
        self.canvas.rect_changed.connect(self._on_rect_changed)
        self.canvas.zoom_changed.connect(self._on_zoom_changed)

        self.enable_switch = Switch(self._config().get(geometry.KEY_ENABLED, True))
        self.enable_switch.toggled.connect(self._on_enabled_toggled)

        self.zoom_in_button = Button(self.tr("Zoom In"), "plus", "subtle", "small")
        self.zoom_out_button = Button(self.tr("Zoom Out"), "minus", "subtle", "small")
        self.zoom_reset_button = Button("1:1", None, "subtle", "small")
        self.zoom_fit_button = Button(self.tr("Fit Window"), None, "subtle", "small")
        self.zoom_label = Label("", "secondary")

        self.capture_button = Button(self.tr("Capture Frame"), "camera", "secondary", "small")
        self.save_button = Button(self.tr("Save Config"), "check", "primary", "small")
        self.reset_button = Button(self.tr("Reset To Saved"), "reset", "subtle", "small")
        self.default_button = Button(self.tr("Reset To Default"), "reset", "subtle", "small")
        self.pending_label = Label(self.tr("Unsaved changes"), "secondary")

        self._build_layout()
        self._connect_buttons()
        self._reload_from_config()
        self._refresh_pending_state()
        self._refresh_zoom_label(self.canvas.zoom_scale())

    @property
    def name(self):
        """侧边栏显示名。"""
        return "Screenshot Mask"

    # --- 界面 ---------------------------------------------------------------

    def _build_layout(self):
        """构建「工具条 + 画布 + 操作栏」三段式布局。"""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        # 开关在放大按钮左侧：它是这个页面的总开关，先于任何局部操作出现。
        toolbar.addWidget(self.enable_switch)
        toolbar.addSpacing(6)
        toolbar.addWidget(self.zoom_in_button)
        toolbar.addWidget(self.zoom_out_button)
        toolbar.addWidget(self.zoom_reset_button)
        toolbar.addWidget(self.zoom_fit_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.zoom_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.capture_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.default_button)
        actions.addStretch(1)
        actions.addWidget(self.pending_label)

        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        column.addLayout(toolbar)
        column.addWidget(self.canvas, 1)
        column.addLayout(actions)

        self.add_card(self.tr("Preview"), holder, stretch=1)

    def _connect_buttons(self):
        """接线全部按钮。"""
        self.zoom_in_button.clicked.connect(self.canvas.zoom_in)
        self.zoom_out_button.clicked.connect(self.canvas.zoom_out)
        self.zoom_reset_button.clicked.connect(self.canvas.zoom_reset)
        self.zoom_fit_button.clicked.connect(self.canvas.zoom_fit)
        self.capture_button.clicked.connect(self.capture_frame)
        self.save_button.clicked.connect(self.save_config)
        self.reset_button.clicked.connect(self.reset_to_saved)
        self.default_button.clicked.connect(self.reset_to_default)

    # --- 配置 ---------------------------------------------------------------

    def _config(self):
        """返回遮罩配置对象。

        Raises：
            RuntimeError：`og.global_config` 尚未构造，说明页签被早于
                `OK(config)` 实例化。必须指名失败而不是返回默认值——静默用默认值会让
                页签显示与实际配置不一致，而用户看不出任何异常。
        """
        global_config = getattr(og, "global_config", None)
        if global_config is None:
            raise RuntimeError(
                "截图遮罩页签构造失败：og.global_config 尚未就绪。"
                "页签必须在 OK(config) 之后由 MainWindow 实例化，"
                "当前调用时机早于全局配置构造"
            )
        return global_config.get_config(processor.OPTION_NAME)

    def _reload_from_config(self):
        """从配置读取遮罩矩形覆盖临时值。"""
        self._pending = None
        config = self._config()
        size = self.canvas.frame_size()
        if size[0] and size[1]:
            self.canvas.set_rect(geometry.resolve_rect(config, *size))
        else:
            self.canvas.set_rect(None)

    def _on_enabled_toggled(self, checked):
        """开关变化：即时落盘，并同步预览上的编辑框显隐。"""
        config = self._config()
        config[geometry.KEY_ENABLED] = bool(checked)
        self._sync_mask_visibility()

    def _sync_mask_visibility(self):
        """按开关状态决定预览上是否显示可编辑的遮罩框。"""
        enabled = bool(self._config().get(geometry.KEY_ENABLED, True))
        if enabled:
            self._reload_from_config()
        else:
            self.canvas.set_rect(None)
            self._pending = None
        self._refresh_pending_state()

    def _on_rect_changed(self, rect):
        """拖拽只更新临时值，不写配置。"""
        if not self._config().get(geometry.KEY_ENABLED, True):
            return
        self._pending = tuple(rect)
        self._refresh_pending_state()

    def _refresh_pending_state(self):
        """刷新「未保存」提示与三个按钮的可用性。

        - **保存配置**：有临时调整才可用，无内容可保存。
        - **重置为配置值**：同样是「有临时调整才可用」——它的作用是丢弃临时调整，
          界面已经等于已保存值时点它什么都不会发生。
        - **重置为默认值**：已保存的几何已等于默认值时不可用。
        """
        has_pending = self._has_pending()
        self.pending_label.setVisible(has_pending)
        self.save_button.setEnabled(has_pending)
        self.reset_button.setEnabled(has_pending)
        self.default_button.setEnabled(not self._is_default())

    def _is_default(self):
        """判断当前已保存的几何是否已等于默认值。

        Returns：
            True 表示配置里的锚点与四个数值键都与默认值相同。
        """
        defaults = self._defaults()
        if defaults is None:
            return False
        config = self._config()
        if config.get(geometry.KEY_ANCHOR) != defaults[geometry.KEY_ANCHOR]:
            return False
        return all(config.get(key) == defaults[key] for key in geometry.NUMERIC_KEYS)

    def _defaults(self):
        """取配置对象的出厂默认值。

        框架的 `Config` 把默认值存在 `.default`；`ConfigOption` 用的是 `.default_config`。
        这里只认前者并在缺失时返回 ``None``——`Config` 是 `_config()` 唯一可能的返回类型，
        两个名字都试会让「默认值来自哪」变得含糊。

        Returns：
            默认值字典；对象不是框架的 `Config` 时返回 ``None``。
        """
        return getattr(self._config(), "default", None)

    def _has_pending(self):
        """判断界面上是否存在未保存的调整。"""
        if self._pending is None:
            return False
        size = self.canvas.frame_size()
        if not size[0] or not size[1]:
            return False
        saved = geometry.resolve_rect(self._config(), *size)
        return saved != self._pending

    # --- 动作 ---------------------------------------------------------------

    def capture_frame(self):
        """抓一帧**原始**画面作为预览底图。"""
        from ok.device.capture_methods.base import CAPTURE_BUSY_WAIT

        frame = og.device_manager.capture_method.get_frame(busy_wait=CAPTURE_BUSY_WAIT)
        if frame is None:
            self.logger.warning("截图遮罩：未取到画面，预览保持不变")
            return

        # 只应用模糊区（属于「截什么」），**不**应用遮罩处理器（属于本页签要编辑的东西）。
        from ok.util.blur import apply_blur_areas, get_blur_algorithm

        frame = apply_blur_areas(
            frame, og.config.get('blur_area'), get_blur_algorithm(getattr(og, 'global_config', None))
        )
        image = _to_qimage(frame)
        if image is None:
            return
        self.canvas.set_frame(image)
        self._reload_from_config()
        self._refresh_pending_state()

    def save_config(self):
        """把界面上的临时调整写入配置并落盘。"""
        if not self._has_pending():
            return
        size = self.canvas.frame_size()
        values = geometry.values_from_rect(self._pending, size[0], size[1], self._config())
        config = self._config()
        config[geometry.KEY_ANCHOR] = values[geometry.KEY_ANCHOR]
        for key in geometry.NUMERIC_KEYS:
            config[key] = values[key]
        self._pending = None
        self._refresh_pending_state()

    def reset_to_saved(self):
        """丢弃临时调整，回到已保存的配置值。"""
        self._reload_from_config()
        self._refresh_pending_state()

    def reset_to_default(self):
        """把遮罩几何还原成默认值并落盘。

        与「重置为配置值」的区别：那个是「撤销本次编辑」，这个是「回到出厂设置」。
        默认值即上游写死的右下角 13% × 2.5%、偏移 0，所以重置后的截图行为与改造前一致。

        只改锚点与四个数值键，**不动「启用遮罩」开关**：用户点这个按钮是想恢复遮罩范围，
        不是想把关掉的遮罩也一起打开。

        Raises：
            RuntimeError：配置对象没有 `default`（不是框架的 `Config`）。必须指名失败——
                静默退回硬编码值会让「默认值」出现第二份事实来源。
        """
        config = self._config()
        defaults = self._defaults()
        if defaults is None:
            raise RuntimeError(
                "截图遮罩重置失败：配置对象没有 default，无法取得出厂默认值。"
                "默认值只在 ConfigOption.default_config 一处维护"
            )

        # 逐个赋值而不是 config.reset_to_default()：后者会把「启用遮罩」也一并恢复成
        # 默认的开启，用户关掉遮罩后点这个按钮不该被偷偷打开。
        config[geometry.KEY_ANCHOR] = defaults[geometry.KEY_ANCHOR]
        for key in geometry.NUMERIC_KEYS:
            config[key] = defaults[key]
        self._reload_from_config()
        self._refresh_pending_state()

    # --- 缩放显示 -----------------------------------------------------------

    def _on_zoom_changed(self, scale):
        """缩放变化时刷新比例文案与 1:1 按钮状态。

        Args：
            scale：当前比例。
        """
        self._refresh_zoom_label(scale)

    def _refresh_zoom_label(self, scale):
        """更新比例文案。

        **永远显示具体百分比**，包括刚点完「适应窗口」——那时画面已按当前控件算出
        确定比例（如 31%）。显示「Fit」等于把已知数字藏起来，用户还会以为拖拽时
        比例会跟着变。

        Args：
            scale：当前比例。
        """
        self.zoom_label.setText(f"{round(scale * 100)}%")
        # 已在 1:1 时「1:1」按钮不可用——重复点击没有意义。
        self.zoom_reset_button.setEnabled(abs(scale - 1.0) > 1e-6)


def processor_option_name():
    """返回遮罩全局配置项名。

    Returns：
        配置项名字符串。
    """
    return processor.OPTION_NAME


def _to_qimage(frame):
    """把 BGR / 灰度 numpy 帧转成 ``QImage``。

    Args：
        frame：numpy 帧。

    Returns：
        ``QImage``；帧为空或形状不受支持时返回 ``None``。
    """
    import numpy as np

    if frame is None or not getattr(frame, "size", 0):
        return None
    array = np.ascontiguousarray(frame)
    if array.ndim == 2:
        height, width = array.shape
        return QImage(array.data, width, height, array.strides[0], QImage.Format.Format_Grayscale8).copy()
    if array.ndim == 3 and array.shape[2] == 3:
        import cv2

        rgb = cv2.cvtColor(array, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        return QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
    return None
