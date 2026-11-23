# -*- coding: utf-8 -*-
"""模板图处理对话框：形状编辑 + 背景色排除，合成最终掩码。

## 这个对话框产出什么

两条独立的筛选，最终取交集（都排除才排除）：

| 筛选 | 表达什么 | 坐标系 |
|---|---|---|
| 掩码形状 | 模板内部哪个几何区域有效 | 相对模板包围盒 0–1 |
| 背景色 | 哪个颜色范围的像素无效 | 模板像素颜色 |

结果就是 `templates/<特征>_mask.png`：255 参与匹配、0 排除。**保存场景时算好
落盘**，运行期直接用文件，不再现算（见 `model.Feature.build_mask`）。

## 为什么要有「无形状」

`无形状` 与 `矩形` 看起来等价，实际是两种状态：前者表示「没有形状这个概念」，
只靠颜色筛选；后者是「形状恰好是矩形」，它有可拖的顶点，用户能把它拖小。
合并成一种会让「我要排除边缘一圈」这类需求无处表达。

## 为什么预览要把排除区涂成品红

用户改的是「哪些像素参与匹配」，这件事在原图上**完全看不见**。只让他填参数，
他无法判断对不对：取色取到前景主色会把模板排空，识别直接失效，而界面一点异常
都没有。品红几乎不出现在游戏 UI 里，不会被误读成原图内容。
"""

from __future__ import annotations

import numpy as np
from ok.ui.qt.kit import Button, Label
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from extensions.capabilities.scene_manager import bgcolor, mask_shape
from extensions.capabilities.scene_manager.pages.mask_canvas import MaskShapeCanvas
from extensions.capabilities.scene_manager.translate import tr

# 覆盖率上下限：超过上限说明筛选把模板排空了，匹配必然失效。
_COVERAGE_WARN_HIGH = 0.9
# 低于下限说明筛选几乎没效果，用户可能以为配上了。
_COVERAGE_WARN_LOW = 0.01

# 形状开关的显示名与取值。顺序即界面顺序：`无形状` 在最前，它是默认值。
_SHAPE_CHOICES = (
    (mask_shape.MASK_NONE, "None"),
    (mask_shape.MASK_RECT, "Rectangle"),
    (mask_shape.MASK_CIRCLE, "Circle"),
    (mask_shape.MASK_SECTOR, "Sector"),
    (mask_shape.MASK_POLYGON, "Polygon"),
)


class TemplateImageDialog(QDialog):
    """模板图处理：编辑掩码形状、识别背景色、预览排除效果。

    Attributes:
        color: 选定的背景色 `(b, g, r)`，未选时为 None。
        tolerance: 每通道容差。
        shape: 编辑后的 `MaskShape`。
    """

    def __init__(self, template, color=None, tolerance=bgcolor.DEFAULT_TOLERANCE, shape=None, parent=None):
        """构造对话框。

        Args:
            template: 模板图（BGR 数组）。
            color: 已有背景色；None 表示当前未启用颜色筛选。
            tolerance: 已有容差。
            shape: 已有 `MaskShape`；None 表示无形状。
            parent: 父控件。
        """
        super().__init__(parent)
        self.setWindowTitle(tr("Template Image"))
        self.resize(980, 760)

        self._template = template
        self.color = tuple(color) if color else None
        self.tolerance = int(tolerance)
        self.shape = shape if shape is not None else mask_shape.MaskShape.none()
        # 多边形加点的待插入坐标：由画布算出后暂存，等用户点菜单项时才落盘
        self._pending_vertex = None

        root = QVBoxLayout(self)
        root.setSpacing(6)

        # --- 1. 图像区（置顶，吃掉全部剩余高度）---
        #
        # **画布排在最上面**：用户打开这个对话框的第一件事是看模板长什么样，
        # 按钮与参数都是「针对这张图做什么」。把它们压在图上，等于让人先读
        # 一堆控件再找图，每次都得往下扫一遍。
        #
        # 拉伸因子给 1、其余行 0：窗口变高时只有画布长高。若给其它行也留
        # 拉伸，画布会在窗口变大时反而不长——用户会觉得「窗口大了图没大」。
        self.canvas = MaskShapeCanvas()
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.shape_changed.connect(self._on_shape_changed)
        self.canvas.vertex_menu_requested.connect(self._on_vertex_menu)
        self.canvas.color_sample_requested.connect(self._on_canvas_sample)
        root.addWidget(self.canvas, 1)

        # --- 2. 背景色区块（提示 + 操作紧挨着，置于图下）---
        #
        # 提示文字与按钮**必须在同一区块内相邻**：提示说的是「品红表示排除」，
        # 而按钮就是排除的配置入口。分置上下两端时，用户点了按钮要抬头找
        # 说明，或者先读说明再往下找按钮，来回跳。
        color_box = QVBoxLayout()
        color_box.setSpacing(4)
        color_box.addWidget(
            Label(
                tr(
                    "Pixels matching the background color, and pixels outside the mask shape, "
                    "are excluded from matching. Excluded areas are shown in magenta."
                ),
                "caption",
            )
        )

        row = QHBoxLayout()
        self.edge_button = Button(tr("Detect from Edges"), variant="tonal", size="small")
        self.edge_button.clicked.connect(self._on_detect_edges)
        row.addWidget(self.edge_button)

        self.pick_button = Button(tr("Pick on Image"), variant="tonal", size="small")
        self.pick_button.setCheckable(True)
        self.pick_button.toggled.connect(self._on_pick_toggled)
        row.addWidget(self.pick_button)

        row.addWidget(Label(tr("Tolerance"), "body"))
        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(0, 128)
        self.tolerance_spin.setValue(self.tolerance)
        self.tolerance_spin.valueChanged.connect(self._on_tolerance_changed)
        row.addWidget(self.tolerance_spin)

        self.clear_button = Button(tr("No Color Filter"), variant="subtle", size="small")
        self.clear_button.clicked.connect(self._on_clear)
        row.addWidget(self.clear_button)

        row.addWidget(Label(tr("Background Color (B, G, R)"), "body"))
        self.color_edit = QLineEdit()
        self.color_edit.setPlaceholderText(tr("e.g. 48, 48, 48"))
        self.color_edit.editingFinished.connect(self._on_color_edit)
        row.addWidget(self.color_edit, 1)
        color_box.addLayout(row)

        # 视图操作与覆盖率提示：同属「针对这张图」的操作与反馈，跟取色行同一区块。
        view_row = QHBoxLayout()
        for text, handler in (
            ("Zoom In", self._zoom_in),
            ("Zoom Out", self._zoom_out),
            ("100%", self._zoom_reset),
            ("Fit Window", self._zoom_fit),
        ):
            button = Button(tr(text), variant="subtle", size="small")
            button.clicked.connect(handler)
            view_row.addWidget(button)
        view_row.addStretch(1)
        self.result_label = Label("", "caption", word_wrap=True)
        view_row.addWidget(self.result_label, 1)
        color_box.addLayout(view_row)

        root.addLayout(color_box)

        # --- 3. 参数区（单行横向排列，相关显示 / 不相关隐藏）---
        #
        # **改为「相关显示、不相关隐藏」而不是置灰**：置灰能表达「参数存在但
        # 无效」，但六组参数全摆着时，用户要逐个读灰掉的项才知道哪些能用。
        # 隐藏后一眼只剩当前形状真正可调的项，参数与形状的对应关系变得可读。
        #
        # **单行而不是两行**：分行时必须先决定「谁跟谁一行」，而各形状可见的
        # 参数组合不同（圆是半径+中心、矩形只有角度、多边形是中心+顶点），
        # 预先分行会让某一行只剩一个控件、另一行挤四个。放同一行后，隐藏的
        # 容器不占位（`QHBoxLayout` 对隐藏控件自动收拢），任何组合都是紧凑一行。
        #
        # 参数在形状按钮**之上**：形状按钮是这块区域的标题与「切换对象」的入口，
        # 参数是它的属性。标题放最下会让人先看到属性再看到「这些属性属于谁」。
        params_row = QHBoxLayout()
        params_row.setSpacing(12)
        self.angle_field, self.angle_label = self._make_field(tr("Rotate angle"), self._make_angle_spin())
        params_row.addWidget(self.angle_field)
        self.sweep_field, _ = self._make_field(tr("Sweep angle"), self._make_sweep_spin())
        params_row.addWidget(self.sweep_field)
        self.radius_field, _ = self._make_field(tr("Radius"), self._make_radius_spin())
        params_row.addWidget(self.radius_field)
        self.cx_field, _ = self._make_field(tr("Center X"), self._make_offset_spin("cx"))
        params_row.addWidget(self.cx_field)
        self.cy_field, _ = self._make_field(tr("Center Y"), self._make_offset_spin("cy"))
        params_row.addWidget(self.cy_field)
        self.vertex_field, _ = self._make_field(tr("Vertices"), self._make_vertex_spin())
        params_row.addWidget(self.vertex_field)
        params_row.addStretch(1)
        root.addLayout(params_row)

        # --- 4. 掩码形状区块（置底）---
        shape_row = QHBoxLayout()
        shape_row.addWidget(Label(tr("Mask Shape"), "body"))
        self.shape_buttons = {}
        for kind, text in _SHAPE_CHOICES:
            button = Button(tr(text), variant="tonal", size="small")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, target=kind: self._on_shape_chosen(target))
            self.shape_buttons[kind] = button
            shape_row.addWidget(button)
        shape_row.addStretch(1)
        self.reset_params_button = Button(tr("Reset Parameters"), variant="subtle", size="small")
        self.reset_params_button.clicked.connect(self._on_reset_params)
        shape_row.addWidget(self.reset_params_button)
        root.addLayout(shape_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("OK"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._sync_color_edit()
        self._refresh()

    # --- 参数控件构造 -------------------------------------------------------

    @staticmethod
    def _make_field(text, spin):
        """把「标签 + 输入框」包成一个可整体显隐的容器。

        Args:
            text: 标签文字。
            spin: 输入框控件。

        Returns:
            `(容器, 标签)` 二元组。

        Note:
            必须包成容器：`QHBoxLayout` 对**隐藏的控件**会自动收拢空位，但
            标签与输入框是两个独立控件，分别 `setVisible(False)` 需要记两处；
            包一层后只需要控制容器的可见性，也不会出现「标签还在、框没了」。

            同时把标签引用交出去：角度那组的文字要随形状在「旋转角」与
            「起始角」之间切换，没有引用就只能从容器里按索引翻控件，很脆。
        """
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = Label(text, "body")
        layout.addWidget(label)
        layout.addWidget(spin)
        return holder, label

    def _make_angle_spin(self):
        """构造角度输入框（矩形旋转角 / 扇形起始角）。"""
        self.angle_spin = QSpinBox()
        self.angle_spin.setRange(0, 359)
        self.angle_spin.setSuffix("°")
        self.angle_spin.setValue(int(self.shape.angle))
        self.angle_spin.valueChanged.connect(self._on_params_changed)
        return self.angle_spin

    def _make_sweep_spin(self):
        """构造张角输入框。"""
        self.sweep_spin = QSpinBox()
        self.sweep_spin.setRange(1, 360)
        self.sweep_spin.setSuffix("°")
        self.sweep_spin.setValue(int(self.shape.sweep) if self.shape.sweep > 0 else 90)
        self.sweep_spin.valueChanged.connect(self._on_params_changed)
        return self.sweep_spin

    def _make_radius_spin(self):
        """构造半径输入框（按宽度百分比呈现）。

        Note:
            模型里是相对宽度的比例，直接显示 `0.0765` 对用户没有意义。
        """
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(1, 400)
        self.radius_spin.setSuffix(" %")
        self.radius_spin.valueChanged.connect(self._on_params_changed)
        return self.radius_spin

    def _make_offset_spin(self, axis):
        """构造位置输入框（百分比，可超出 0–100）。

        Args:
            axis: `"cx"` 或 `"cy"`，决定存到哪个属性上。

        Returns:
            `QDoubleSpinBox`。

        Note:
            两个位置框结构相同，但要分别挂到 `cx_spin` / `cy_spin`——测试与
            刷新逻辑都按名字取，统一存成列表会让「哪个是 x」变成隐式约定。
        """
        spin = QDoubleSpinBox()
        spin.setRange(-800.0, 800.0)
        spin.setDecimals(1)
        spin.setSuffix(" %")
        spin.valueChanged.connect(self._on_params_changed)
        setattr(self, f"{axis}_spin", spin)
        return spin

    def _make_vertex_spin(self):
        """构造顶点数输入框。"""
        self.vertex_count = QSpinBox()
        self.vertex_count.setRange(3, 64)
        self.vertex_count.valueChanged.connect(self._on_vertex_count_changed)
        return self.vertex_count

    # --- 形状 ---------------------------------------------------------------

    def _aspect(self):
        """返回模板的高宽比。

        Returns:
            浮点数；模板不可用时返回 1.0。
        """
        if self._template is None or getattr(self._template, "size", 0) == 0:
            return 1.0
        height, width = self._template.shape[:2]
        if width <= 0:
            return 1.0
        return height / width

    def _on_shape_chosen(self, kind):
        """切换形状类型。

        Args:
            kind: 目标形状种类。

        Note:
            切换时**自动给一个居中的初始形状**，不是留空：留空会让用户面对一块
            什么都没有的图，得先猜「怎么开始画」。最坏情况是他不满意再拖，
            比面对空白好。
        """
        self.shape = self.shape.with_kind(kind, self._aspect())
        self._refresh()

    def _on_shape_changed(self, shape):
        """画布拖拽产生新形状。

        Args:
            shape: 新的 `MaskShape`。
        """
        self.shape = shape
        self._refresh()

    def _on_params_changed(self):
        """参数区任一输入框变化时，把整组参数写回形状。

        Note:
            **整组写回而不是逐字段增量改**。字段之间有关联（改圆心要保留半径、
            改半径要保留角度），逐字段改会让「哪个字段以哪个为准」变成隐式约定；
            一次读全部输入框重建形状，规则只有一条：输入框里的值就是形状的值。

            `none` 时直接返回：它没有几何，任何参数都无意义；此时输入框是
            置灰的，正常也触发不到这里。
        """
        if self.shape.kind == mask_shape.MASK_NONE:
            return
        angle = float(self.angle_spin.value())
        cx = self.cx_spin.value() / 100.0
        cy = self.cy_spin.value() / 100.0

        if self.shape.kind == mask_shape.MASK_RECT:
            self.shape = self.shape.rotated(angle)
        elif self.shape.kind == mask_shape.MASK_CIRCLE:
            self.shape = mask_shape.MaskShape(
                mask_shape.MASK_CIRCLE,
                [(cx, cy)],
                radius=max(1e-4, self.radius_spin.value() / 100.0),
            )
        elif self.shape.kind == mask_shape.MASK_SECTOR:
            self.shape = mask_shape.MaskShape(
                mask_shape.MASK_SECTOR,
                [(cx, cy)],
                angle=angle,
                sweep=float(self.sweep_spin.value()),
                radius=max(1e-4, self.radius_spin.value() / 100.0),
            )
        else:
            # 多边形：圆心输入框改为整体平移（它没有「圆心」这个概念，
            # 但「把整个多边形挪一挪」是同一个诉求）
            x1, y1, x2, y2 = self.shape.bounds()
            current_cx = (x1 + x2) / 2.0
            current_cy = (y1 + y2) / 2.0
            self.shape = self.shape.translated(cx - current_cx, cy - current_cy)
        self._refresh()

    def _on_vertex_count_changed(self, count):
        """调整多边形顶点数（只对多边形生效）。

        Args:
            count: 目标顶点数。

        Note:
            **顶点数变化靠「重建正多边形」实现**，不是保留原顶点再增删：用户调
            数字时想的是「我要一个 N 边形」，而保留原顶点会在 N 变化后得到形状
            怪异的轮廓（比如 4 点的基础上加第 5 点，第五点会插在某条边中间）。
            重建时保留包围盒中心与外接尺寸，位置和大小不跳。
        """
        if self.shape.kind != mask_shape.MASK_POLYGON:
            return
        if count == len(self.shape.points):
            return
        self.shape = self.shape.with_vertex_count(count)
        self._refresh()

    def _on_reset_params(self):
        """把当前形状的全部参数重置为默认值。

        Note:
            **只重置形状参数，不动背景色**。这个按钮长在形状区块里，用户按它
            想的是「把形状调回默认」；顺手把颜色筛选也清掉会让人措手不及——
            而颜色是另一套配置，有自己的「不排除颜色」按钮。

            重置用 `from_kind` 重新生成该形状的初始几何，而不是把参数逐个改回
            常量：默认值本来就集中定义在模型层（半径比例、多边形内缩量），
            在界面上再抄一份必然与那边分叉。
        """
        if self.shape.kind == mask_shape.MASK_NONE:
            return
        self.shape = mask_shape.MaskShape.from_kind(self.shape.kind, self._aspect())
        self._refresh()

    def _on_vertex_menu(self, action, index, coords, global_point):
        """多边形顶点右键菜单。

        Args:
            action: `"add"` 或 `"remove"`。
            index: 加时是边序号，删时是顶点下标。
            coords: 加点时的插入位置（相对比例）；删点时为 None。
            global_point: 菜单弹出位置（全局坐标）。缺失时退回鼠标当前位置。

        Note:
            **点右键才弹菜单**而不是直接执行：用户可能只是想在顶点上右键看看
            有没有菜单，直接删掉他辛苦调好的点会让人不敢右键。

            `global_point` 为空时退回 `QCursor.pos()`。`QMenu.exec(None)` 会抛
            `TypeError`，而堆栈全在 Qt 内部——用户看到的是「右键就报错」，
            完全看不出是信号参数没传。
        """
        menu = QMenu(self)
        if action == "add":
            # 插入位置由画布算出（线上投影点）并随信号传下来：
            # 界面自己再算一遍会与画布的判定口径分叉。
            target = coords if coords is not None else (0.5, 0.5)
            menu.addAction(tr("Add Vertex"), lambda: self._insert_vertex(index, target))
        else:
            menu.addAction(tr("Delete Vertex"), lambda: self._remove_vertex(index))
        menu.exec(global_point or QCursor.pos())

    def _insert_vertex(self, edge_index, coords):
        """在边上插入顶点。

        Args:
            edge_index: 边序号。
            coords: 新顶点的相对坐标。

        Note:
            位置取画布算出的**投影点**，新顶点正好落在线上，加完多边形轮廓不会跳。
        """
        x, y = coords
        try:
            self.shape = self.shape.inserted(edge_index, x, y)
        except mask_shape.MaskShapeError as error:
            self.result_label.setText(str(error))
            return
        self._refresh()

    def _remove_vertex(self, index):
        """删除顶点。

        Args:
            index: 顶点下标。

        Note:
            删到只剩 3 个点就不能再删——两个点构不成多边形，掩码面积为零，
            该特征永远匹配不到。这里明确提示而不是静默拒绝。
        """
        try:
            self.shape = self.shape.removed(index)
        except mask_shape.MaskShapeError as error:
            self.result_label.setText(str(error))
            return
        self._refresh()

    # --- 取色 ---------------------------------------------------------------

    def _on_detect_edges(self):
        """按四边众数取背景色。"""
        color, ratio = bgcolor.edge_mode_color(self._template)
        if color is None:
            self.result_label.setText(tr("Template image is unavailable"))
            return
        self.color = color
        self._sync_color_edit()
        self._refresh()
        if ratio < 0.5:
            # 众数占比低说明四边颜色不统一（渐变、描边、前景贴边），
            # 自动取到的颜色未必是背景——必须提示，否则用户以为成功了。
            self.result_label.setText(
                self.result_label.text()
                + "\n"
                + tr("Warning: edge color is not consistent ({pct}); check the preview").format(pct=f"{ratio:.0%}")
            )

    def _on_pick_toggled(self, checked):
        """切换「图上取色」模式。"""
        self.pick_button.setText(tr("Click the image...") if checked else tr("Pick on Image"))
        self.canvas.set_sample_mode(checked)

    def _on_canvas_sample(self, x, y):
        """画布上报一个取色点。

        Args:
            x: 相对包围盒 x。
            y: 相对包围盒 y。

        Note:
            取色后**自动退出取色模式**：一次点击配一个颜色是用户的预期，
            留在取色模式会让下一次拖顶点变成改颜色，而改颜色只影响掩码、
            界面上很难察觉。
        """
        if self._template is None:
            return
        color = bgcolor.sample_color(self._template, x, y)
        if color is None:
            return
        self.color = color
        self._sync_color_edit()
        self.pick_button.setChecked(False)
        self._refresh()

    def _on_clear(self):
        """停用颜色筛选。"""
        self.color = None
        self._sync_color_edit()
        self._refresh()

    def _on_tolerance_changed(self, value):
        """容差变化时重算预览。

        Args:
            value: 新的容差值。
        """
        self.tolerance = int(value)
        self._refresh()

    def _reject(self, message):
        """报告一次输入校验失败：弹错误浮层，同时写进结果行。

        Args：
            message：失败原因。

        Note:
            **浮层挂在弹窗自己身上**（模态 `QDialog` 自身是一个窗口），不会跑到
            主窗口。同时写 `result_label` 是因为它就在输入框旁边——用户回头改
            输入时能看到上一次为什么被拒，而浮层几秒后就消失了。
        """
        from extensions.capabilities.scene_manager import notify

        self.result_label.setText(message)
        notify.notify_error(self, message, title="输入有误")

    def _on_color_edit(self):
        """手工输入色值。"""
        text = self.color_edit.text().strip()
        if not text:
            self.color = None
            self._refresh()
            return
        parts = [item for item in text.replace("，", ",").split(",") if item.strip()]
        if len(parts) != 3:
            self._reject(tr("Color must be three numbers: B, G, R"))
            return
        try:
            channels = [int(round(float(item))) for item in parts]
        except ValueError:
            self._reject(tr("Color must be three numbers: B, G, R"))
            return
        if any(not 0 <= value <= 255 for value in channels):
            self._reject(tr("Each channel must be 0-255"))
            return
        self.color = tuple(channels)
        self._refresh()

    def _sync_color_edit(self):
        """把当前色值写回输入框。"""
        if self.color is None:
            self.color_edit.setText("")
        else:
            self.color_edit.setText(", ".join(str(value) for value in self.color))

    # --- 视图 ---------------------------------------------------------------

    def _zoom_in(self):
        """放大。"""
        self.canvas.zoom_in()

    def _zoom_out(self):
        """缩小。"""
        self.canvas.zoom_out()

    def _zoom_fit(self):
        """适配窗口。"""
        self.canvas.zoom_fit()

    def _zoom_reset(self):
        """回到 100%。"""
        self.canvas.zoom_reset()

    # --- 刷新 ---------------------------------------------------------------

    def _refresh(self):
        """把当前状态推到参数区、画布与提示行。"""
        for kind, button in self.shape_buttons.items():
            button.setChecked(kind == self.shape.kind)

        kind = self.shape.kind
        has_angle = kind in (mask_shape.MASK_RECT, mask_shape.MASK_SECTOR)
        is_sector = kind == mask_shape.MASK_SECTOR
        has_radius = kind in (mask_shape.MASK_CIRCLE, mask_shape.MASK_SECTOR)
        is_polygon = kind == mask_shape.MASK_POLYGON
        has_center = kind in (mask_shape.MASK_CIRCLE, mask_shape.MASK_SECTOR, mask_shape.MASK_POLYGON)

        # 标签随形状改：矩形的 `angle` 是旋转角，扇形的是起始角。
        self.angle_label.setText(tr("Start angle") if is_sector else tr("Rotate angle"))

        # **相关显示、不相关隐藏**：整组容器一起显隐，`QHBoxLayout` 会自动收拢
        # 空位，不会留下「标签还在、框没了」的残缺行。
        for holder, visible in (
            (self.angle_field, has_angle),
            (self.sweep_field, is_sector),
            (self.radius_field, has_radius),
            (self.cx_field, has_center),
            (self.cy_field, has_center),
            (self.vertex_field, is_polygon),
        ):
            holder.setVisible(visible)
        # 只有多边形才有顶点数可调，重置参数的适用范围见 `_on_reset_params`。
        self.reset_params_button.setVisible(kind != mask_shape.MASK_NONE)

        # 输入框与模型对齐：用户可能是拖动改的，也可能是切换形状重置的。
        # 只在值不同时写入，避免触发 `valueChanged` 造成递归刷新。
        self._sync_spin(self.angle_spin, int(round(self.shape.angle)) % 360)
        if is_sector:
            self._sync_spin(self.sweep_spin, max(1, int(round(self.shape.sweep))))
        if has_radius:
            self._sync_spin(self.radius_spin, max(1, int(round(self.shape.radius * 100))))
        if has_center:
            cx, cy = self.shape.center()
            self._sync_double(self.cx_spin, round(cx * 100, 1))
            self._sync_double(self.cy_spin, round(cy * 100, 1))
        if is_polygon:
            self._sync_spin(self.vertex_count, len(self.shape.points))

        self.canvas.set_template(self._template, keep_view=True)
        self.canvas.set_background(self.color, self.tolerance)
        self.canvas.set_shape(self.shape)
        self._update_summary()

    @staticmethod
    def _sync_spin(spin, value):
        """把值写进整数输入框，值未变时不触发信号。

        Args:
            spin: `QSpinBox`。
            value: 目标值。
        """
        if spin.value() == value:
            return
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    @staticmethod
    def _sync_double(spin, value):
        """把值写进浮点输入框，值未变时不触发信号。

        Args:
            spin: `QDoubleSpinBox`。
            value: 目标值。
        """
        if abs(spin.value() - value) < 1e-6:
            return
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    def _update_summary(self):
        """更新覆盖率提示。"""
        if self._template is None:
            self.result_label.setText(tr("Save the scene once to generate the template image, then reopen this dialog"))
            return
        keep = self._combined_keep(self._template)
        if keep is None:
            self.result_label.setText(tr("No filtering: all pixels take part in matching"))
            return
        coverage = float((keep == 0).mean())
        parts = [tr("Excluded {pct} of pixels").format(pct=f"{coverage:.1%}")]
        if self.color is not None:
            parts.append(tr("Color B={b} G={g} R={r}").format(b=self.color[0], g=self.color[1], r=self.color[2]))
        if self.shape.kind != mask_shape.MASK_NONE:
            parts.append(tr("Shape: {kind}").format(kind=tr(self._shape_label(self.shape.kind))))
        if coverage >= _COVERAGE_WARN_HIGH:
            parts.append(tr("Too much excluded: the filter is probably wrong, so matching will fail"))
        elif coverage <= _COVERAGE_WARN_LOW:
            parts.append(tr("Almost nothing excluded: the filter may not take effect"))
        self.result_label.setText(" · ".join(parts))

    def _combined_keep(self, template):
        """按「形状 ∧ 颜色」算参与匹配的像素。

        Args:
            template: 模板图。

        Returns:
            单通道 uint8 掩码；两项都未启用时返回 None。

        Note:
            与 `model.Feature.build_mask` 同一算法。不直接调那个方法是因为它属于
            模型层、需要构造 `Feature`；而且**必须保持同一口径**，否则预览与
            保存结果不同步——两处都由「形状掩码 ∧ 颜色掩码」表达，改动时同步。
        """
        if template is None or getattr(template, "size", 0) == 0:
            return None
        height, width = template.shape[:2]
        keep = self.shape.mask(width, height)
        if self.color is not None:
            reference = np.array(self.color, dtype=np.int16)
            diff = np.abs(template[..., :3].astype(np.int16) - reference)
            color_mask = np.where(np.all(diff <= self.tolerance, axis=2), 0, 255).astype(np.uint8)
            keep = color_mask if keep is None else np.minimum(keep, color_mask)
        return keep

    @staticmethod
    def _shape_label(kind):
        """形状种类的显示名 key。

        Args:
            kind: 形状种类。

        Returns:
            显示名（英文源串）。
        """
        for value, text in _SHAPE_CHOICES:
            if value == kind:
                return text
        return kind
