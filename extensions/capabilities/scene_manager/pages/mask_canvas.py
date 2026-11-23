# -*- coding: utf-8 -*-
"""掩码形状画布：在模板图上绘制与编辑掩码形状。

## 与 `canvas.SceneCanvas` 的关系

`SceneCanvas` 管的是「特征/区域在整帧上的矩形框」，本画布管的是「模板内部哪些
像素参与匹配」。两者的坐标系、可编辑对象、右键菜单都不同，但**缩放与平移的
交互必须一致**——用户在同一套编辑流程里来回切换，两处手感不同会让人怀疑
「是不是这个窗口有问题」。

因此缩放算法（锚点不动点缩放、按视口夹取偏移、适配比例）照抄 `SceneCanvas`
的实现，不共享代码：那边的实现与「矩形框的命中测试」耦合在一起，抽出公共
基类会让两边都变复杂。这里只保留本画布需要的部分。

## 交互模型（与主流图形编辑器一致）

| 位置 | 行为 |
|---|---|
| 形状内部 | 拖动整体平移 |
| 贴边/贴角（矩形）、贴圆弧（圆/扇形） | 调整大小 |
| 中心点附近（矩形/扇形） | 旋转 |
| 扇形两条半径边 | 调整张角 |
| 多边形顶点 | 拖动该顶点 |
| 形状外的空白 | 平移视图 |
| 滚轮 | 以指针为锚点缩放 |

**判定顺序固定为「中心 → 边/弧 → 内部 → 空白」**：三者会重叠（中心既在内部
也在边上），先判中心才能让「想转一下」稳定命中，否则形状越小越转不动。

## 为什么形状可以不画点

矩形画四角点、圆画半径点，看起来「更好拖」，实际有两个问题：

- 矩形有点会让人以为**必须抓住那个点**才能改大小，而真实交互是贴着边就能拖；
- 圆的半径点在宽扁模板上会离圆弧很远（半径按宽度算），看起来像一个浮在
  形状外面的孤立白点。

所以只有**多边形**画点——它的顶点是真正的编辑对象，不画就无从下手。矩形与
扇形的旋转中心画一个小圆圈作为可操作提示，其余一律不画。

## 为什么用相对包围盒坐标

掩码形状的坐标一律是**模板像素的 0–1 比例**，与 `mask_shape.MaskShape` 一致。
画布内部先换算成像素再换算成控件坐标，任何一处都不缓存绝对像素——缓存会让
「窗口缩放后形状错位」，而那种错位看起来像数据坏了。
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from extensions.capabilities.scene_manager import bgcolor, mask_shape

# 缩放范围。上限给到 16 倍：模板常只有几十像素高，看不清顶点位置。
MIN_SCALE = 0.05
MAX_SCALE = 16.0

# 顶点半径（控件坐标，像素）。只有多边形画点。
HANDLE_RADIUS = 5.0
# 命中容差（控件坐标，像素）。贴着边/角/弧多少像素内算命中。
HANDLE_TOLERANCE = 8.0
# 命中边的容差（控件坐标，像素）。比顶点容差小，避免「想加顶点却选中顶点」。
EDGE_TOLERANCE = 6.0

# 旋转中心提示圈的半径（控件坐标，像素）。
ROTATE_HINT_RADIUS = 9.0

# 旋转灵敏度系数。鼠标绕中心转过的角度除以它才是形状转的角度。
#
# 形状越小，绕中心转一周所需的鼠标位移越短——8px 的容差就可能扫过几十度，
# 手感是「一碰就飞掉」。除以 3 后需要绕更久才转一圈，微调可控。
ROTATE_SENSITIVITY = 3.0

# 形状轮廓颜色（绿色，与品红排除色对比明显）。
SHAPE_COLOR = QColor(80, 220, 120)
# 顶点颜色。
HANDLE_COLOR = QColor(255, 255, 255)
HANDLE_BORDER = QColor(30, 30, 30)
# 旋转中心提示色（浅蓝），与轮廓绿区分开。
ROTATE_HINT_COLOR = QColor(120, 200, 255)
# 圆心十字颜色与臂长（控件像素）。
#
# 尺寸固定不随缩放变：放大 16 倍后十字会跟着长成盖住圆弧的大叉，
# 那样它自己就成了干扰。
CENTER_MARK_COLOR = QColor(255, 220, 120)
CENTER_MARK_SIZE = 7.0
# 被排除区域的覆盖色（品红）。与对话框里的预览保持同一口径。
EXCLUDED_COLOR = (255, 0, 255)


def _shortest_delta(start, end):
    """返回两个方位角之间的最短有向增量（−180, 180]。

    Args:
        start: 起始角（度）。
        end: 结束角（度）。

    Returns:
        增量（度），正值表示顺时针。

    Note:
        直接相减会在 0°/360° 交界处得到 ±359：鼠标刚跨过正右方，形状会突然
        转一整圈。取最短增量后交界处的行为与其它角度一致。
    """
    delta = (end - start + 180.0) % 360.0 - 180.0
    return delta


def _to_qimage(image):
    """把 BGR 数组转成 QImage。

    Args:
        image: BGR 或灰度数组。

    Returns:
        QImage 实例；输入不可用时返回 None。

    Note:
        必须 `copy()`：直接引用 numpy 缓冲区会让 QImage 指向一段随时可能被回收的
        内存，表现为预览偶尔花屏或崩溃。
    """
    if image is None or getattr(image, "size", 0) == 0:
        return None
    data = image
    if len(data.shape) == 2:
        height, width = data.shape
        rgb = np.repeat(data[:, :, None], 3, axis=2)
    else:
        height, width = data.shape[:2]
        rgb = data[:, :, ::-1]
    rgb = np.ascontiguousarray(rgb)
    return QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()


class MaskShapeCanvas(QWidget):
    """掩码形状画布。

    Attributes:
        shape_changed: 形状被用户改动时发出，参数为新的 `MaskShape`。
        vertex_menu_requested: 请求顶点右键菜单，参数为
            `("add", 边序号, 投影点, 全局坐标)` 或 `("remove", 顶点下标, None, 全局坐标)`。
        color_sample_requested: 请求取色，参数为相对包围盒的 `(x, y)`。
    """

    shape_changed = Signal(object)
    vertex_menu_requested = Signal(str, int, object, object)
    color_sample_requested = Signal(float, float)

    def __init__(self, parent=None):
        """构造画布。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self.setMinimumHeight(320)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._template = None
        self._image = None
        self._shape = mask_shape.MaskShape.none()
        self._bg_color = None
        self._bg_tolerance = bgcolor.DEFAULT_TOLERANCE
        self._preview = None

        self._scale = 1.0
        self._fit_scale = 1.0
        self._offset = QPointF(0, 0)

        self._panning = False
        self._pan_origin = QPointF(0, 0)
        self._pan_offset = QPointF(0, 0)

        # 拖拽状态：`_grab_mode` 在按下时确定，整个拖动过程不变。
        # 每帧重新判定会让鼠标滑出容差范围时「改去拖另一条边」，形状会跳。
        self._grab_mode = mask_shape.GRAB_NONE
        self._grab_detail = None
        self._grab_origin = QPointF(0, 0)
        self._grab_shape = None
        self._grab_angle = 0.0

        self._hover_mode = mask_shape.GRAB_NONE
        self._hover_detail = None

        # 取色模式：置位后左键点击只取色，不拖控制点也不平移。
        # 用属性而不是让对话框在画布上挂事件处理器——画布已完整接管鼠标事件，
        # 外部再挂一份会与它自己的处理顺序冲突。
        self._sample_mode = False

    # --- 数据 ---------------------------------------------------------------

    def set_template(self, template, keep_view=False):
        """设置模板图。

        Args:
            template: BGR 数组。
            keep_view: 为 True 时保留当前缩放与位置（改形状时用）。
        """
        self._template = template
        self._image = _to_qimage(template)
        if not keep_view:
            self._recalc_fit_scale()
            self._scale = self._fit_scale
            self._offset = self._centered_offset(self._scale)
        self._rebuild_preview()
        self.update()

    def set_shape(self, shape):
        """设置掩码形状。

        Args:
            shape: `MaskShape` 实例。
        """
        self._shape = shape
        self._rebuild_preview()
        self.update()

    def shape(self):
        """返回当前掩码形状。

        Returns:
            `MaskShape` 实例。
        """
        return self._shape

    def set_background(self, color, tolerance):
        """设置背景色剔除参数。

        Args:
            color: `(b, g, r)` 或 None。
            tolerance: 每通道容差。
        """
        self._bg_color = color
        self._bg_tolerance = max(0, int(tolerance))
        self._rebuild_preview()
        self.update()

    def set_sample_mode(self, value):
        """开关取色模式。

        Args:
            value: True 表示下一次左键点击只取色。
        """
        self._sample_mode = bool(value)
        self.setCursor(Qt.CursorShape.CrossCursor if self._sample_mode else Qt.CursorShape.ArrowCursor)

    def aspect(self):
        """返回模板的高宽比。

        Returns:
            高 / 宽；模板不可用时返回 1.0。

        Note:
            所有旋转相关换算都要它。比例空间不是等比的，漏掉 aspect 会让
            45° 的矩形变成平行四边形，而界面上看起来像「形状自己歪了」。
        """
        if self._image is None or self._image.isNull():
            return 1.0
        width = self._image.width()
        if width <= 0:
            return 1.0
        return self._image.height() / width

    # --- 视图 ---------------------------------------------------------------

    def zoom_scale(self):
        """返回当前缩放比例。

        Returns:
            浮点比例。
        """
        return self._scale

    def zoom_in(self):
        """放大一档。"""
        self._zoom_at(self._scale * 1.25, QPointF(self.width() / 2, self.height() / 2))

    def zoom_out(self):
        """缩小一档。"""
        self._zoom_at(self._scale / 1.25, QPointF(self.width() / 2, self.height() / 2))

    def zoom_fit(self):
        """缩放到整幅进视口并居中。"""
        self._recalc_fit_scale()
        self._scale = self._fit_scale
        self._offset = self._centered_offset(self._scale)
        self.update()

    def zoom_reset(self):
        """回到 100% 并居中。"""
        self._scale = max(MIN_SCALE, min(MAX_SCALE, 1.0))
        self._offset = self._centered_offset(self._scale)
        self.update()

    def _recalc_fit_scale(self):
        """重算适配比例。"""
        if self._image is None or self._image.isNull():
            self._fit_scale = 1.0
            return
        image_w, image_h = self._image.width(), self._image.height()
        view_w, view_h = self.width(), self.height()
        if image_w <= 0 or image_h <= 0 or view_w <= 0 or view_h <= 0:
            self._fit_scale = 1.0
            return
        self._fit_scale = min(view_w / image_w, view_h / image_h)

    def _centered_offset(self, scale):
        """返回画面居中时的偏移。

        Args:
            scale: 缩放比例。

        Returns:
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

        Args:
            offset: 目标偏移。
            scale: 缩放比例。

        Returns:
            夹取后的 `QPointF`。
        """
        if self._image is None or self._image.isNull():
            return offset
        total_w = self._image.width() * scale
        total_h = self._image.height() * scale
        return QPointF(
            self._clamp_axis(offset.x(), self.width(), total_w),
            self._clamp_axis(offset.y(), self.height(), total_h),
        )

    @staticmethod
    def _clamp_axis(value, size, total):
        """按单轴夹取偏移。

        Args:
            value: 偏移值。
            size: 视口尺寸。
            total: 内容尺寸。

        Returns:
            夹取后的值。
        """
        if total <= size:
            return (size - total) / 2
        return min(0.0, max(size - total, value))

    def _zoom_at(self, target_scale, anchor):
        """以锚点为不动点缩放。

        Args:
            target_scale: 目标比例。
            anchor: 锚点（控件坐标）。
        """
        if self._image is None or self._image.isNull():
            return
        frame_x, frame_y = self._view_to_image(anchor)
        new_scale = max(MIN_SCALE, min(MAX_SCALE, target_scale))
        if abs(new_scale - self._scale) < 1e-9:
            return
        self._scale = new_scale
        self._offset = self._clamp_offset(
            QPointF(anchor.x() - frame_x * self._scale, anchor.y() - frame_y * self._scale),
            self._scale,
        )
        self.update()

    def _image_to_view(self, x, y):
        """图像像素坐标 → 控件坐标。

        Args:
            x: 图像 x（像素）。
            y: 图像 y（像素）。

        Returns:
            `QPointF`。
        """
        return QPointF(self._offset.x() + x * self._scale, self._offset.y() + y * self._scale)

    def _view_to_image(self, point):
        """控件坐标 → 图像像素坐标。

        Args:
            point: `QPointF` 或 `QPoint`。

        Returns:
            `(x, y)` 浮点像素坐标。
        """
        if self._scale <= 0:
            return 0.0, 0.0
        return (
            (point.x() - self._offset.x()) / self._scale,
            (point.y() - self._offset.y()) / self._scale,
        )

    def _ratio_to_view(self, x, y):
        """相对比例坐标 → 控件坐标。

        Args:
            x: 相对 x（0–1，可超出）。
            y: 相对 y（0–1，可超出）。

        Returns:
            `QPointF`。
        """
        if self._image is None or self._image.isNull():
            return QPointF(0, 0)
        return self._image_to_view(x * self._image.width(), y * self._image.height())

    def _view_to_ratio(self, point, clamp=False):
        """控件坐标 → 相对比例坐标。

        Args:
            point: 控件坐标。
            clamp: 是否夹到 0–1。**拖动形状时必须 False**——夹住会让形状
                拖不出包围盒，而「只覆盖模板一角」是合法诉求。

        Returns:
            `(x, y)` 相对比例。
        """
        if self._image is None or self._image.isNull():
            return 0.0, 0.0
        width = self._image.width()
        height = self._image.height()
        if width <= 0 or height <= 0:
            return 0.0, 0.0
        x, y = self._view_to_image(point)
        x /= width
        y /= height
        if clamp:
            return min(1.0, max(0.0, x)), min(1.0, max(0.0, y))
        return x, y

    def _tolerance(self):
        """返回相对比例容差（**以宽度为单位**，两轴一致）。

        Returns:
            相对比例的容差。

        Note:
            早期返回 `(tol_x, tol_y)` 两个值，而宽扁模板上两者差 9 倍
            （色卡 430×47：x 0.003、y 0.028）。模型层的 `grab_at` 只能取其一，
            取大者让圆弧带吃掉整个圆内部（圆拖不动），取小者让纵向几乎点不中。
            统一按宽度换算后两轴天然一致，模型层也只需一个标量。
        """
        if self._image is None or self._image.isNull() or self._scale <= 0:
            return 0.02
        width = max(1, self._image.width())
        return max(1e-5, HANDLE_TOLERANCE / (width * self._scale))

    def _edge_tolerance(self):
        """返回加顶点用的边容差（以宽度为单位）。

        Returns:
            相对比例容差。

        Note:
            比顶点容差小：顶点落在边的端点上，边容差太大会让「想加顶点」变成
            「选中了端点」。
        """
        return self._tolerance() * EDGE_TOLERANCE / HANDLE_TOLERANCE

    # --- 交互 ---------------------------------------------------------------

    def wheelEvent(self, event):
        """滚轮缩放。

        Args:
            event: 滚轮事件。
        """
        if self._image is None or self._image.isNull():
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        self._zoom_at(self._scale * factor, event.position())
        event.accept()

    def resizeEvent(self, event):
        """窗口尺寸变化时保持相对视野。

        Args:
            event: 尺寸事件。
        """
        super().resizeEvent(event)
        previous_fit = self._fit_scale
        self._recalc_fit_scale()
        # 之前若处于「适配」状态，缩放后继续适配；否则保持用户设定的倍率
        if abs(self._scale - previous_fit) < 1e-6:
            self._scale = self._fit_scale
        self._offset = self._clamp_offset(self._offset, self._scale)

    def mousePressEvent(self, event):
        """按下：右键请求顶点菜单，左键按命中位置分派。

        Args:
            event: 鼠标事件。
        """
        if self._image is None or self._image.isNull():
            return
        pos = event.position()

        if event.button() == Qt.MouseButton.RightButton:
            self._request_vertex_menu(pos)
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._sample_mode:
            # 取色模式下左键只取色：此时用户的目标是「读一个颜色」，
            # 顺手拖走形状会改变掩码，而那种改变不易察觉
            x, y = self._view_to_ratio(pos, clamp=True)
            self.color_sample_requested.emit(x, y)
            event.accept()
            return

        mode, detail = self._grab_at(pos)
        if mode != mask_shape.GRAB_NONE:
            self._grab_mode = mode
            self._grab_detail = detail
            self._grab_origin = pos
            self._grab_shape = self._shape
            self._grab_angle = self._shape.angle
            event.accept()
            return

        # 形状外的空白：平移视图
        self._panning = True
        self._pan_origin = pos
        self._pan_offset = QPointF(self._offset)
        event.accept()

    def mouseMoveEvent(self, event):
        """移动：按按下时确定的模式编辑形状，或平移视图。

        Args:
            event: 鼠标事件。
        """
        if self._image is None or self._image.isNull():
            return
        pos = event.position()

        if self._grab_mode != mask_shape.GRAB_NONE:
            self._apply_grab(pos)
            event.accept()
            return

        if self._panning:
            delta = pos - self._pan_origin
            self._offset = self._clamp_offset(self._pan_offset + delta, self._scale)
            self.update()
            event.accept()
            return

        mode, detail = self._grab_at(pos)
        if (mode, detail) != (self._hover_mode, self._hover_detail):
            self._hover_mode = mode
            self._hover_detail = detail
            self.setCursor(self._cursor_for(mode, detail))
            self.update()

    def mouseReleaseEvent(self, event):
        """释放：结束拖动或平移。

        Args:
            event: 鼠标事件。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab_mode = mask_shape.GRAB_NONE
            self._grab_detail = None
            self._grab_shape = None
            self._panning = False
            # 松手后重算悬停态：拖动过程中光标固定在拖拽形态，松手要恢复
            mode, detail = self._grab_at(event.position())
            self._hover_mode = mode
            self._hover_detail = detail
            self.setCursor(self._cursor_for(mode, detail))
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        """鼠标离开时清悬停态。

        Args:
            event: 离开事件。
        """
        del event
        if self._hover_mode != mask_shape.GRAB_NONE:
            self._hover_mode = mask_shape.GRAB_NONE
            self._hover_detail = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    def _grab_at(self, pos):
        """按控件坐标判定拖拽模式。

        Args:
            pos: 控件坐标。

        Returns:
            `(mode, detail)`。
        """
        x, y = self._view_to_ratio(pos)
        return self._shape.grab_at(x, y, self._tolerance(), self.aspect())

    def _apply_grab(self, pos):
        """按当前拖拽模式更新形状。

        Args:
            pos: 当前控件坐标。

        Note:
            旋转用**角度增量**而不是绝对角：绝对角在按下瞬间会跳（鼠标不一定
            正好落在中心右侧），用户会看到形状「啪」地转一下。

            增量还要除以 `ROTATE_SENSITIVITY`：形状越小，绕中心转一周所需的
            鼠标位移越短——8px 的容差就可能扫过几十度，手感是「一碰就飞掉」。
            除以 3 后需要绕更久才转一圈，微调变得可控。
        """
        x, y = self._view_to_ratio(pos)
        aspect = self.aspect()
        try:
            if self._grab_mode == mask_shape.GRAB_MOVE:
                # 用初始位置算偏移量，避免逐帧累加误差
                ox, oy = self._view_to_ratio(self._grab_origin)
                updated = self._grab_shape.translated(x - ox, y - oy)
            elif self._grab_mode == mask_shape.GRAB_ROTATE:
                current = self._grab_shape.point_angle(x, y, aspect)
                origin = self._grab_shape.point_angle(*self._view_to_ratio(self._grab_origin), aspect)
                delta = _shortest_delta(origin, current) / ROTATE_SENSITIVITY
                updated = self._grab_shape.rotated(self._grab_angle + delta)
            elif self._grab_mode == mask_shape.GRAB_SWEEP:
                updated = self._grab_shape.swept(self._grab_detail, x, y, aspect)
            else:
                updated = self._grab_shape.resized(self._grab_detail, x, y, aspect)
        except mask_shape.MaskShapeError:
            # 拖成退化形状（如矩形宽或高归零）时保持原状，不弹窗打断拖动
            return
        self._shape = updated
        self._rebuild_preview()
        self.shape_changed.emit(updated)
        self.update()

    @staticmethod
    def _cursor_for(mode, detail):
        """返回拖拽模式对应的光标。

        Args:
            mode: `GRAB_*`。
            detail: `grab_at` 的 detail。

        Returns:
            `Qt.CursorShape`。

        Note:
            光标的形状是这个交互唯一的即时反馈。给错光标比不给更糟——用户会
            以为「这里不能拖」，而实际能拖。

            **移动用十字（`CrossCursor`），不用 `SizeAllCursor`**：`SizeAll`
            是 Windows 的「移动窗口」四向箭头，在图形编辑器里表示「整体拖拽」
            意图不明确；十字是绘图类工具的通用「可拖动/绘制」提示，与「按住
            在形状内拖动整体移动」这个语义更贴。旋转同样用十字，但**只对
            矩形与扇形生效**——圆没有旋转。
        """
        if mode == mask_shape.GRAB_MOVE:
            return Qt.CursorShape.CrossCursor
        if mode == mask_shape.GRAB_ROTATE:
            return Qt.CursorShape.CrossCursor
        if mode == mask_shape.GRAB_SWEEP:
            return Qt.CursorShape.SizeHorCursor
        if mode == mask_shape.GRAB_RESIZE:
            if detail == mask_shape.RECT_TOP or detail == mask_shape.RECT_BOTTOM:
                return Qt.CursorShape.SizeVerCursor
            if detail == mask_shape.RECT_LEFT or detail == mask_shape.RECT_RIGHT:
                return Qt.CursorShape.SizeHorCursor
            if detail == mask_shape.RECT_TL or detail == mask_shape.RECT_BR:
                return Qt.CursorShape.SizeFDiagCursor
            if detail == mask_shape.RECT_TR or detail == mask_shape.RECT_BL:
                return Qt.CursorShape.SizeBDiagCursor
            return Qt.CursorShape.PointingHandCursor
        return Qt.CursorShape.ArrowCursor

    def _request_vertex_menu(self, pos):
        """按右键位置请求多边形顶点菜单。

        Args:
            pos: 控件坐标。

        Note:
            顺序是**先判顶点后判边**：顶点落在边的端点上，先判边会让「想删点时
            弹出加点菜单」。这与场景编辑器里「先判手柄后判框」的取向一致。

            **两个分支都必须带上弹出位置**。早期「加点」分支传了 `None`，
            `QMenu.exec(None)` 直接抛 `TypeError`——用户右键一下就是一条异常，
            而堆栈全在 Qt 内部，看不出是自己传错了参数。
        """
        if self._shape.kind != mask_shape.MASK_POLYGON:
            return
        x, y = self._view_to_ratio(pos)
        tol = self._tolerance()
        global_point = self.mapToGlobal(pos.toPoint())
        for index, (vx, vy) in enumerate(self._shape.points):
            if math.hypot(x - vx, (y - vy) * self.aspect()) <= tol:
                self.vertex_menu_requested.emit("remove", index, None, global_point)
                return
        nearest = self._shape.nearest_edge(x, y, self._edge_tolerance())
        if nearest is None:
            return
        edge_index, point = nearest
        # 必须带弹出位置：`QMenu.exec(None)` 会抛 TypeError，而堆栈全在 Qt 内部，
        # 看不出是自己漏传了参数。
        self.vertex_menu_requested.emit("add", edge_index, point, global_point)

    # --- 预览 ---------------------------------------------------------------

    def _rebuild_preview(self):
        """按「形状 ∧ 颜色」重建被排除区域的覆盖图。

        Note:
            覆盖图叠在原图上而不是替换它：用户要同时看清「被排除了哪里」与
            「排除掉的原本是什么」。只显示掩码会让取色判断失去参照。
        """
        if self._template is None or getattr(self._template, "size", 0) == 0:
            self._preview = None
            return
        shown = self._template.copy()
        height, width = shown.shape[:2]
        keep = None
        shape_mask = self._shape.mask(width, height)
        if shape_mask is not None:
            keep = shape_mask
        if self._bg_color is not None:
            color_mask = _color_mask(shown, self._bg_color, self._bg_tolerance)
            keep = color_mask if keep is None else np.minimum(keep, color_mask)
        if keep is not None:
            shown[keep == 0] = EXCLUDED_COLOR
        self._preview = _to_qimage(shown)

    def paintEvent(self, event):
        """绘制底图（含排除覆盖）、形状轮廓与控制点。

        Args:
            event: 绘制事件。
        """
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(40, 40, 40))

        source = self._preview or self._image
        if source is None or source.isNull():
            painter.setPen(QPen(QColor(130, 130, 130)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "无模板图")
            painter.end()
            return

        target = QRectF(
            self._offset.x(),
            self._offset.y(),
            source.width() * self._scale,
            source.height() * self._scale,
        )
        painter.drawImage(target, source)

        self._paint_shape(painter)
        painter.end()

    def _paint_shape(self, painter):
        """绘制形状轮廓、顶点与旋转中心提示。

        Args:
            painter: `QPainter` 实例。
        """
        if self._shape.kind == mask_shape.MASK_NONE:
            # 「无形状」不画任何轮廓：画个包围盒会让人以为那是矩形形状
            return

        aspect = self.aspect()
        painter.setPen(QPen(SHAPE_COLOR, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self._shape.kind in (mask_shape.MASK_CIRCLE, mask_shape.MASK_SECTOR):
            cx, cy = self._shape.points[0]
            center = self._ratio_to_view(cx, cy)
            image = self._image
            if image is None or image.isNull():
                return
            radius = self._shape.radius * image.width() * self._scale
            rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
            if self._shape.kind == mask_shape.MASK_CIRCLE:
                painter.drawEllipse(rect)
            else:
                # Qt 的角度单位是 1/16 度，且 y 轴向下；起始角取反以匹配
                # 掩码里 `cv2.ellipse` 的角度口径
                painter.drawArc(
                    rect,
                    int(round(-(self._shape.angle + self._shape.sweep) * 16)),
                    int(round(self._shape.sweep * 16)),
                )
                self._paint_sector_edges(painter, center, radius)
            # 圆心十字：圆与扇形都画。**不画圆心用户根本不知道往哪拖**——
            # 而圆心恰好是「按住拖动整体移动」的落点。
            self._paint_center_cross(painter, center)
        else:
            points = [self._ratio_to_view(x, y) for x, y in self._shape.corners(aspect)]
            for index in range(len(points)):
                painter.drawLine(points[index], points[(index + 1) % len(points)])

        # 旋转中心提示：**只有矩形**画。圆没有旋转（转了看不出来），
        # 扇形虽然可旋转，但它的圆心已经由十字标记表达，再套一圈会显得杂乱。
        if self._shape.kind == mask_shape.MASK_RECT:
            self._paint_rotate_hint(painter)

        self._paint_handles(painter)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_center_cross(self, painter, center):
        """在圆心画一个小十字，标示「按住这里可以整体移动」。

        Args:
            painter: `QPainter`。
            center: 圆心（控件坐标）。

        Note:
            用十字而不是圆点：圆点在圆形形状内部会被误读成「又一个控制点」，
            而十字是绘图工具里「中心/锚点」的通用记号。尺寸固定（不随缩放变），
            否则放大后十字会长得盖住圆弧。
        """
        highlighted = self._hover_mode == mask_shape.GRAB_MOVE
        painter.setPen(QPen(SHAPE_COLOR if highlighted else CENTER_MARK_COLOR, 1.5))
        size = CENTER_MARK_SIZE
        painter.drawLine(QPointF(center.x() - size, center.y()), QPointF(center.x() + size, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - size), QPointF(center.x(), center.y() + size))

    def _paint_sector_edges(self, painter, center, radius):
        """画出扇形的两条半径边。

        Args:
            painter: `QPainter`。
            center: 圆心（控件坐标）。
            radius: 半径（控件像素）。

        Note:
            只用 `drawArc` 画弧时，扇形看起来像一段孤立曲线，用户看不出圆心在哪、
            也看不出两条边可以拖。补上两条半径边才有「扇形」的形态。
        """
        for angle in (self._shape.angle, self._shape.angle + self._shape.sweep):
            rad = math.radians(angle)
            end = QPointF(
                center.x() + radius * math.cos(rad),
                center.y() + radius * math.sin(rad),
            )
            painter.drawLine(center, end)

    def _paint_rotate_hint(self, painter):
        """画出旋转中心的小圆圈。

        Args:
            painter: `QPainter`。

        Note:
            用一个**空心小圈**而不是实心点：实心点会被误读成「可拖顶点」，
            而它的实际作用是「在这里拖动会旋转」。
        """
        cx, cy = self._shape.center()
        center = self._ratio_to_view(cx, cy)
        highlighted = self._hover_mode == mask_shape.GRAB_ROTATE
        painter.setPen(QPen(SHAPE_COLOR if highlighted else ROTATE_HINT_COLOR, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, ROTATE_HINT_RADIUS, ROTATE_HINT_RADIUS)

    def _paint_handles(self, painter):
        """画出可拖动的顶点（只有多边形有）。

        Args:
            painter: `QPainter`。
        """
        for index, (x, y) in enumerate(self._shape.draw_handles()):
            center = self._ratio_to_view(x, y)
            highlighted = self._hover_mode == mask_shape.GRAB_RESIZE and self._hover_detail == index
            painter.setBrush(QBrush(SHAPE_COLOR if highlighted else HANDLE_COLOR))
            painter.setPen(QPen(HANDLE_BORDER, 1))
            radius = HANDLE_RADIUS + (2 if highlighted else 0)
            painter.drawEllipse(center, radius, radius)


def _color_mask(template, color, tolerance):
    """按背景色生成掩码。

    Args:
        template: BGR 数组。
        color: `(b, g, r)`。
        tolerance: 每通道容差。

    Returns:
        单通道 uint8 掩码（255 参与 / 0 排除）。

    Note:
        与 `model.Feature._color_mask` 同一判据（逐通道与），不共享代码是因为
        模型层不该依赖界面层。两处口径必须一致，否则预览与保存结果会不同步——
        这一点由 `TestSceneManagerBgColor` 的取色用例间接覆盖。
    """
    reference = np.array(color[:3], dtype=np.int16)
    diff = np.abs(template[..., :3].astype(np.int16) - reference)
    background = np.all(diff <= int(tolerance), axis=2)
    return np.where(background, 0, 255).astype(np.uint8)
