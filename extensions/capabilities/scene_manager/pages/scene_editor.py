# -*- coding: utf-8 -*-
"""场景编辑弹窗。

新建场景与编辑已有场景都走这个弹窗；外部（配置子页签）只保留只读预览。

布局对齐「标注编辑器」（`ok/ui/qt/tasks/MarkUpWindow.py`）的形态：

```text
┌──────────────────────────────────────────────────────────────────┐
│ 名称 [____]  [选择|框选特征区域|框选交互区域]   [抓取][导入][取消][保存] │
├───────────────────────────────────────┬──────────────────────────┤
│                                       │ 识别特征  2              │
│                                       │ ┌──────────────────────┐ │
│              画  布                    │ │ feature-1            │ │
│                                       │ │ Template · Required  │ │
│                                       │ └──────────────────────┘ │
│                                       │ 交互区域  1              │
│                                       │ ┌──────────────────────┐ │
│                                       │ │ area-1               │ │
│                                       │ │ 10,10→30,30 · Probe  │ │
│                                       │ └──────────────────────┘ │
├───────────────────────────────────────┴──────────────────────────┤
│ 滚轮缩放 · 拖动空白处平移 · 拖动框可移动，拖手柄改大小 · 双击编辑属性      │
└──────────────────────────────────────────────────────────────────┘
```

六条设计要求各自对应的实现：

1. **模式用 toggle 组**：`TabStrip(look="pill")`，自带选中高亮与互斥；不写
   「当前功能」说明文本——选中态本身已经说明一切。
2. **按钮命名**：`选择` / `框选特征区域` / `框选交互区域`。
3. **框选完成后弹属性对话框**；双击列表项、双击画布上的框也打开它做编辑。属性
   对话框是唯一的结构化输入入口：名称是必填项，先建后改会让画布上出现一个叫
   `feature-1` 的框，而用户很可能忘了改。
4. **遮罩不画框**：遮罩属不可操作区域，画框会让用户以为能拖。改为把遮罩区涂黑。
5. **左侧图 + 右侧窄栏**：右侧是固定宽度的信息栏，标题带计数、下面是可滚动的列表。
6. **保存/取消在右上角**，紧跟「导入图片」。

**为什么用列表而不是表格**：右侧只需回答「这个场景配了什么」，因此每个元素占一条
两行文本——第一行是名称，第二行是属性摘要。表格要为每列留标题与固定宽度，在
280px 的窄栏里会把内容压成省略号；而列宽本身还随语言变化（「必需」26px vs
`Required` 51px），维护成本高于它的收益。真要逐项比较属性时走属性对话框。

**列表项只读**：所有改数据入口都收敛到属性对话框（双击）+ 画布拖拽。列表可编辑时
会出现「表格能改一半、对话框能改全部」的两套口径，字段一多就分叉。

**「交互区域」不是「点击区域」**：同一块区域后续可能被用来单击、双击、长按、
拖拽、滚轮滚动，场景管理不关心也不记录动作类型（见 `model.Area` 的说明）。因此
界面文案一律用「交互区域」「操作后等待」，只有「探索探测」这一项明确写着它做的是
单击——那是探索自己的探测手段，不是区域属性。

**默认编辑与落盘分离，可选自动保存**：窗口里的改动只作用于内存中的 `Scene`，
点「保存」才写盘；打开顶部的「自动保存」开关后，停止操作约 0.8 秒就会把改动
写入编辑区（新建场景需先手动保存一次）。开关关闭时行为与原先完全一致。
"""

import os

from ok.ui.qt.kit import Card
from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTime, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from extensions.capabilities.scene_manager import mask_shape, model, recognize, runtime, shapes
from extensions.capabilities.scene_manager.pages import canvas as canvas_module
from extensions.capabilities.scene_manager.translate import tr

# 右侧信息栏宽度。对齐标注编辑器的 260px，再加一点余量容纳「名称 + 属性摘要」两行：
# 摘要里有「模板 · 必需」「10,10→30,30 · 探测」这类组合串，260 在中文下会贴边。
_SIDE_WIDTH = 300

# 「不限制最大高度」的取值，等于 Qt 的 `QWIDGETSIZE_MAX`。
# 动态参数区测量高度前要先把既有约束清掉，否则 `resize`/`adjustSize` 会被
# `setFixedHeight` 夹住，每轮量出同一个数。
_UNCONSTRAINED_HEIGHT = 16777215

# 三种元素的颜色。画布标签底条与图例同色，看颜色就能对上表里的行。
COLOR_FEATURE_MANDATORY = "#2060c0"
COLOR_FEATURE_OPTIONAL = "#3fa06a"
COLOR_AREA = "#c07a20"
# 遮罩不是「元素」，只是不可操作区域，画布上直接涂黑、不加框。
COLOR_MASK = "#000000"

# 模式 key 与显示名。
MODE_SELECT = "select"
MODE_FEATURE = "feature"
MODE_AREA = "area"

# 模式显示名是英文源串：`msgid` 必须与它逐字符一致，中文只存在于 zh_CN 的译文里。
MODE_LABELS = (
    (MODE_SELECT, "Select"),
    (MODE_FEATURE, "Draw Feature Region"),
    (MODE_AREA, "Draw Interaction Region"),
)

# `PropertyDialog` 的字段声明里，`options` 取这个值表示开关（布尔）。
# 用字符串而不是 `True`：`True` 太容易被误当成「有选项」，读调用点时也看不出
# 这里要的是开关。
TOGGLE_FIELD = "toggle"


class PropertyDialog(QDialog):
    """单个元素的属性编辑对话框。

    框选完成后、双击表格行时都会打开它。用对话框而不是表格内联编辑，是因为
    属性含枚举（必需/可选、目标场景、识别方式）与较长的说明文本，表格单元格
    放不下这些控件。

    **字段支持三种输入形态**：`options` 为 None 是文本输入；为列表是下拉选项；
    为 :data:`TOGGLE_FIELD` 是开关（布尔）。布尔语义用开关而不是「是/否」下拉——
    两个选项的下拉要先点开再选，而开关一次点击就切换，也更直观地表达「开/关」。

    **支持动态参数区**：某些字段的可用参数取决于另一个下拉字段的取值（探索动作 →
    动作参数）。这类字段由 `dynamic_params` 提供，下拉变化时重建内容，而
    **参数区高度固定为所有取值里的最大值**——否则切换动作会让对话框变高变矮，
    底部的「确定/取消」跟着上下跑。
    """

    def __init__(self, title, fields, parent=None, dynamic_params=None):
        """构造属性对话框。

        Args：
            title：窗口标题。
            fields：字段声明列表，每项 `(key, label, value, options)`。
                `options` 为 None 表示文本输入；为列表时是下拉选项 `[(显示, 值)]`。
            parent：父控件。
            dynamic_params：可选的动态参数区，`(controller_key, provider)` 二元组。
                `controller_key` 是某个下拉字段的 key，它的值变化时重建参数区；
                `provider` 是 `(value) -> [(key, label, value, options)]`，返回该
                取值下要显示的参数字段，可为空列表。省略表示没有动态区。

        Raises：
            ValueError：`dynamic_params` 指向的控制器不存在或不是下拉字段。
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self._fields = {}
        # 动态行：key → (editor, options)
        self._dynamic_fields = {}
        # 跨动作记住用户已填的值：切到滑动再切回长按时，之前填的 2.5 秒不应丢失
        self._dynamic_values = {}
        # 当前插入的动态行行号（升序），切换动作时按它原位移除
        self._dynamic_rows = []
        self._dynamic_controller = None
        self._dynamic_provider = None
        self._controller_row = -1
        # 动态行插在这个字段之后；省略时与控制器同值
        self._anchor_row = -1
        # 标签列固定宽度（0 表示还未定宽）；动态行插入时按它设宽
        self._label_width = 0
        # 是否还需在首次显示时固定高度（有动态区才置位）
        self._height_pending = False

        from ok.ui.qt.kit import ComboBox, Label, Switch

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)
        self._form = form
        # key → 该字段在 form 里的行号，动态区要靠它定位插入位置。
        self._row_of = {}

        for key, label, value, options in fields:
            if options is None:
                editor = QLineEdit(str(value))
                form.addRow(Label(label, "body"), editor)
            elif options == TOGGLE_FIELD:
                editor = Switch(bool(value))
                form.addRow(Label(label, "body"), editor)
            else:
                editor = ComboBox()
                for text, option_value in options:
                    editor.addItem(text, option_value)
                for position in range(editor.count()):
                    if editor.itemData(position) == value:
                        editor.setCurrentIndex(position)
                        break
                form.addRow(Label(label, "body"), editor)
            self._fields[key] = (editor, options)
            self._row_of[key] = form.rowCount() - 1

        root.addLayout(form)
        if dynamic_params is not None:
            self._setup_dynamic(dynamic_params)
        # 弹簧把按钮压到底部：参数行少时多余空间集中到按钮上方，而不是把字段
        # 之间的间距拉开（后者看着像「少了一块内容」）。
        root.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        # **必须显式设文字**：OK / Cancel 由 Qt 内置翻译提供，而本项目用的是 minimal
        # 版 PySide6，Qt 的 zh_CN 翻译不随包分发——不设就一直是英文。
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("OK"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._buttons = buttons

        if dynamic_params is not None:
            self._freeze_height()

    def _freeze_height(self):
        """标记需要在**首次显示前**固定高度。

        三条要求同时成立，只有固定高度能全满足：

        | 要求 | 说明 |
        |---|---|
        | 标签不对齐 | 参数行插进主表单，天然共列（与高度无关） |
        | 不显示占位提示 | 无参数时不插任何行，也不写提示文字 |
        | 切换动作按钮不跳 | 高度固定，底部按钮位置恒定 |

        **为什么不用「给参数区设固定高度」**：那会在无参数时于字段之间留一块空白，
        看起来像少了一块内容；把高度定格在整个对话框上，多余空间被底部的弹簧吃掉，
        集中在按钮上方，视觉上不突兀。

        **为什么不在构造时测量**：`sizeHint()` 在布局未激活时是「最小可接受尺寸」
        而非真实尺寸——实测只有 259px 而实际需要 353px，据此设固定高度会把所有输入
        框压扁。构造时强行 `show()` 测量则会让对话框在屏幕上一闪。

        因此把测量推迟到 `showEvent`：那时 Qt 已完成一轮布局，`height()` 可信。
        """
        self._height_pending = True

    def showEvent(self, event):
        """首次显示后固定高度。

        Args：
            event：事件对象。

        Note：
            真正测量放到 `QTimer.singleShot(0, ...)`：`showEvent` 里布局还没
            settle，此时 `height()` 与 `adjustSize()` 都按未激活的状态算，量出来
            偏小（实测 265 而实际需要 353），据此固定会压扁内容。排到事件循环的下
            一轮，Qt 已完成首轮布局，`height()` 才可信。
        """
        super().showEvent(event)
        if getattr(self, "_height_pending", False):
            self._height_pending = False
            QTimer.singleShot(0, self._measure_and_freeze)

    def _measure_and_freeze(self):
        """逐值测量并固定**高度**与**标签列宽**。

        两件事都得固定，否则切换取值时对话框会跳：

        | 项 | 不固定时的现象 |
        |---|---|
        | 高度 | 底部「确定/取消」上下移动（最多 88px） |
        | 标签列宽 | 某一取值的标签更长时，标签列变宽、**所有输入框左边界右移且宽度变窄**——实测「期望文本（OCR 用）」出现时阈值框 x 92→140、宽 337→289 |

        标签列宽由 `QFormLayout` 按该列最长标签自动算，因此「多一行长标签」会牵连
        其余所有行。固定成所有取值里的最大值后，输入框的位置与宽度全程不变。

        **关键在每轮之后跑一次事件循环**：`insertRow` / `removeRow` 之后表单的
        `sizeHint` 需要一轮事件循环才会重算，只调 `invalidate()` + `activate()`
        拿到的是旧值——实测五个动作的行数从 3 变到 5，量出的高度却全是 177，
        据此固定会把滑动的两行参数压扁。

        **测量前解除既有尺寸约束**：`setFixedHeight` 一旦生效，`resize` 与
        `adjustSize` 都会被夹住，后续轮次永远量出同一个数。

        **为什么用 `resize` 再 `adjustSize`**：先把窗口撑高到不挤压字段区，再让
        `adjustSize` 收回内容真正需要的高度；直接 `adjustSize` 时窗口可能停在上一
        轮更小的尺寸上，收回结果会偏小。
        """
        controller = self._dynamic_controller
        if controller is None:
            return
        original = controller.currentData()
        width = self.width()
        natural = 0
        widest_label = 0
        controller.blockSignals(True)
        self.setMinimumHeight(0)
        self.setMaximumHeight(_UNCONSTRAINED_HEIGHT)
        try:
            for index in range(controller.count()):
                controller.setCurrentIndex(index)
                self._render_dynamic(controller.itemData(index), keep_inputs=False)
                self._form.invalidate()
                self.layout().invalidate()
                self.layout().activate()
                self.resize(width, 2000)
                self.adjustSize()
                # 让 Qt 完成这一轮的尺寸计算：缺这一句时 height() 还是上一轮的值。
                QApplication.processEvents()
                natural = max(natural, self.height())
                widest_label = max(widest_label, self._current_label_width())
        finally:
            for position in range(controller.count()):
                if controller.itemData(position) == original:
                    controller.setCurrentIndex(position)
                    break
            self._render_dynamic(original, keep_inputs=False)
            controller.blockSignals(False)
            self.adjustSize()
            QApplication.processEvents()
        if widest_label > 0:
            self._fix_label_column(widest_label)
        if natural > 0:
            self.setFixedHeight(natural)

    def _current_label_width(self):
        """返回当前表单里最宽的标签宽度。

        Returns：
            像素宽度；表单没有标签时返回 0。
        """
        widest = 0
        for row in range(self._form.rowCount()):
            item = self._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is None or item.widget() is None:
                continue
            widest = max(widest, item.widget().sizeHint().width())
        return widest

    def _fix_label_column(self, width):
        """把所有标签固定为同一宽度。

        Args：
            width：目标宽度（取所有取值里最宽的那个）。

        Note：
            逐个 `setFixedWidth` 而不是 `QFormLayout` 的列宽设置：Qt 没有「固定某一
            列宽度」的接口，`setColumnMinimumWidth` 只能设下限、遇到更宽的标签仍会
            撑开。逐个固定标签控件才真的锁定这一列。

            **固定宽度后必须显式设右对齐**：`QFormLayout.setLabelAlignment` 只影响
            未设固定宽度的标签——一旦标签宽度定死，它会按自己的默认对齐（左）渲染，
            表现为「所有标签挤到最左边」，与原先右对齐的观感完全不同。
        """
        self._label_width = width
        for row in range(self._form.rowCount()):
            item = self._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is None or item.widget() is None:
                continue
            label = item.widget()
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _setup_dynamic(self, dynamic_params):
        """建立动态参数区。

        Args：
            dynamic_params：`(controller_key, provider)` 或
                `(controller_key, provider, anchor_key)` 三元组。
                `anchor_key` 指定参数行插在哪个字段之后；省略时插在控制器之后。

        Raises：
            ValueError：控制器或锚点不存在，或控制器不是下拉字段。
        """
        controller_key, provider = dynamic_params[0], dynamic_params[1]
        anchor_key = dynamic_params[2] if len(dynamic_params) > 2 else controller_key
        if controller_key not in self._fields:
            raise ValueError(f"动态区控制器 {controller_key!r} 不存在")
        if anchor_key not in self._row_of:
            raise ValueError(f"动态区锚点 {anchor_key!r} 不存在")
        controller, options = self._fields[controller_key]
        if options is None:
            # 文本输入没有「有限取值集合」，无法枚举出所有取值对应的参数。
            raise ValueError(f"动态区控制器 {controller_key!r} 必须是下拉字段")
        self._dynamic_controller = controller
        self._dynamic_provider = provider
        self._controller_row = self._row_of[controller_key]
        self._anchor_row = self._row_of[anchor_key]

        # 先按当前取值渲染一次，之后每次下拉变化再重建。
        self._render_dynamic(controller.currentData(), keep_inputs=False)
        controller.currentIndexChanged.connect(self._on_dynamic_changed)

    def set_dynamic_values(self, values):
        """给动态区灌入初值并重渲染。

        Args：
            values：`{字段 key: 值}`，例如已有区域的 `{"param_duration": 2.5}`。

        Note：
            必须在构造之后调用，因为初值要看的是**当前控制器取值**对应的参数集——
            构造时还不知道调用方要灌什么。不灌初值的后果是「编辑一个填过 2.5 秒的
            长按区域，输入框显示模型默认的 1.0」，用户会以为值被改了。
        """
        self._dynamic_values.update(values or {})
        if self._dynamic_controller is not None:
            self._render_dynamic(self._dynamic_controller.currentData(), keep_inputs=False)

    def _on_dynamic_changed(self, _index):
        """下拉变化：重建参数行。"""
        if self._dynamic_controller is not None:
            self._render_dynamic(self._dynamic_controller.currentData())

    def _clear_dynamic_rows(self):
        """移除当前显示的所有动态行。

        Note：
            **必须 `setParent(None)` 立即脱离**，只调 `removeRow` 时旧控件要等事件
            循环回收才消失，期间仍渲染在原位——表现为「上一次动作的标签和新标签
            叠在一起」。
        """
        for row in sorted(self._dynamic_rows, reverse=True):
            self._form.removeRow(row)
        self._dynamic_rows = []
        self._dynamic_fields = {}

    def _render_dynamic(self, value, keep_inputs=True):
        """按给定取值重建参数行。

        Args：
            value：控制器的当前值。
            keep_inputs：为 True 时先把参数行现有的输入值记下来再重建（切换动作
                时保住用户填过的值）。**灌初值时必须传 False**——否则刚写进
                `_dynamic_values` 的初值会被上一次渲染残留的输入框内容覆盖，表现为
                「编辑一个填过 3.5 秒的长按区域，输入框显示默认的 1」。

        Note：
            参数行**直接插进主表单**，不另建容器。容器里放一个独立 `QFormLayout`
            时，标签列宽由它自己算，与主表单对不齐——实测「双击间隔（秒）」的标签
            明显比其他标签更靠左。插进同一个 QFormLayout 就自动共享列宽。

            无参数时插 0 行（不是插一句提示占位）：用户明确要求「没有参数的时候
            直接不显示那个提示」。
        """
        if keep_inputs:
            self._dynamic_values.update(self._dynamic_current_texts())
        self._clear_dynamic_rows()

        from ok.ui.qt.kit import ComboBox, Label

        fields = self._dynamic_provider(value) if self._dynamic_provider else []
        # 从锚点行的下一行开始逐行插入，顺序即 provider 返回的顺序。
        #
        # **锚点可以与控制器不同**：识别特征的「期望文本」要排在「阈值」之后（因为
        # 它和阈值一起构成匹配参数），而控制它显隐的是更上方的「识别方式」。若一律
        # 紧贴控制器插入，这个字段就会插到「必需」前面，读起来与阈值脱节。
        row = self._anchor_row + 1
        for key, label, fallback, options in fields:
            if options is None:
                editor = QLineEdit(str(self._dynamic_values.get(key, fallback)))
            else:
                editor = ComboBox()
                for text, option_value in options:
                    editor.addItem(text, option_value)
                current = self._dynamic_values.get(key, fallback)
                for position in range(editor.count()):
                    if editor.itemData(position) == current:
                        editor.setCurrentIndex(position)
                        break
            field_label = Label(label, "body")
            # 标签列宽一旦定死，新插入的标签也要跟着定宽 + 右对齐。否则动态行的长
            # 标签会把整列撑宽（其余行的输入框被推着右移），或按默认左对齐渲染
            # （与其余标签观感不一致）。
            if self._label_width:
                field_label.setFixedWidth(self._label_width)
                field_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._form.insertRow(row, field_label, editor)
            self._dynamic_rows.append(row)
            self._dynamic_fields[key] = (editor, options)
            row += 1

    def _dynamic_current_texts(self):
        """读取动态区当前的输入值。

        Returns：
            `{key: value}` 字典。
        """
        result = {}
        for key, (editor, options) in self._dynamic_fields.items():
            if options is None:
                result[key] = editor.text().strip()
            else:
                result[key] = editor.currentData()
        return result

    def values(self):
        """返回用户输入的值。

        Returns：
            `{key: value}` 字典。文本字段按去空白后的字符串返回；动态区的字段一并
            并入（只含当前动作实际显示的参数）。
        """
        result = {}
        for key, (editor, options) in self._fields.items():
            if options is None:
                result[key] = editor.text().strip()
            elif options == TOGGLE_FIELD:
                result[key] = bool(editor.isChecked())
            else:
                result[key] = editor.currentData()
        result.update(self._dynamic_current_texts())
        return result


class SceneEditorDialog(QDialog):
    """场景编辑弹窗。"""

    def __init__(self, library, scene_id="", parent=None):
        """构造弹窗。

        Args：
            library：`store.SceneLibrary` 实例。
            scene_id：要编辑的场景 id；空串表示新建（保存时才分配 id）。
            parent：父控件。
        """
        super().__init__(parent)
        self.setWindowTitle(tr("Scene Editor"))
        self.resize(1320, 860)
        self.setSizeGripEnabled(True)

        self.library = library
        self.scene = self._load_scene(scene_id)
        self.saved_scene = None
        # 原始帧：裁模板用。模板是识别数据，必须是真实像素——从遮罩图裁会让遮罩区
        # 变成纯色块，而纯色模板在 TM_CCOEFF_NORMED 下恒得 1.0（等于永远命中）。
        self._raw_frame = None
        # 展示/落盘帧：遮罩后的。base.png 会入库、随包分发，不能带游戏 UID。
        self._display_frame = None
        self._loading = False
        # 脏标记：当前场景自上次落盘以来是否有改动。
        #
        # 必须由状态驱动而不是「一律按有改动处理」：后者的表现是「什么都没动，点
        # 左右切换也弹『未保存的改动将丢失』」，用户会以为程序在乱报。而保存按钮
        # 同理——无改动时可点只是让用户白点一次。
        self._modified = False
        # 自动保存的防抖计时器。拖动框、拖滑块会在一次操作里触发几十次
        # `rect_changed`，每次落盘都要重算模板与掩码；等手停下来再写。
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.setInterval(800)
        self._auto_save_timer.timeout.connect(self._auto_save)

        # 宽度监听目标（视口 → True）。必须在 `_build_ui` 之前建好：
        # 列表一创建就可能收到 Resize，`eventFilter` 会立刻来查这张表。
        self._width_tracking_targets = {}

        # 场景导航：列表在构造时取一次——弹窗是模态的，打开期间库不会变；每次切换
        # 都重扫目录反而会让「下标突然变化」。
        self.scene_ids = self._collection_scene_ids(scene_id)
        self.current_index = self.scene_ids.index(scene_id) if scene_id in self.scene_ids else -1

        self._build_ui()
        self._load_auto_save_preference()
        self._load_name_prefix_preference()
        # 监听列表视口宽度：项高与宽度绑定，宽度一变（拖分栏、字号变化、
        # 主题 padding 变化）必须重算折行高度，否则文字会被裁或下方多出空白。
        self._install_width_tracking()
        self._load_frame_or_blank()
        self._refresh_canvas()
        self._refresh_lists()
        self._update_nav_buttons()
        # 刚打开时没有任何改动：保存按钮应为灰，切换也不该弹确认。
        self._modified = False
        self._refresh_save_button()

    # --- 数据 ---------------------------------------------------------------

    def _collection_scene_ids(self, current_id):
        """返回用于左右切换的场景 id 列表（按 id 升序）。

        Args：
            current_id：当前场景 id。不在库里（新建）时仍返回全库列表，
                由调用方用 -1 表示「未定位」。

        Returns：
            场景 id 列表。
        """
        try:
            return self.library.list_scene_ids()
        except OSError:
            return [current_id] if current_id else []

    def prev_scene(self):
        """切换到上一个场景。"""
        if self.current_index > 0:
            self._navigate(self.current_index - 1)

    def next_scene(self):
        """切换到下一个场景。"""
        if 0 <= self.current_index < len(self.scene_ids) - 1:
            self._navigate(self.current_index + 1)

    def _navigate(self, new_index):
        """切到指定下标的场景。

        **默认不自动保存**：点切换时用户没有预期产生写入，而自动保存会让「取消」
        形同虚设（改动已经落盘，取消也回不去）。改为确认提示，把取舍交给用户。

        自动保存开关打开时例外：先把改动落盘再切，不打断用户；写不进去就留在
        当前场景（否则切走会丢掉这次改动）。

        Args：
            new_index：目标下标。
        """
        if not 0 <= new_index < len(self.scene_ids):
            return
        target_id = self.scene_ids[new_index]
        if target_id == self.scene.scene_id:
            return
        if self._dirty():
            if self.auto_save_switch.isChecked() and not self._is_new():
                self._auto_save_timer.stop()
                if not self._write_scene():
                    return
            elif not self._confirm_discard():
                return
        scene = self.library.load_scene(target_id)
        if scene is None:
            self._notify(tr("Scene {id} cannot be read").format(id=target_id), "error", "无法切换场景")
            return
        self.current_index = new_index
        self.scene = scene
        self.saved_scene = None
        self.name_edit.setText(scene.name)
        self._loading = True
        try:
            self.feature_list.clear()
            self.area_list.clear()
        finally:
            self._loading = False
        self._load_frame_or_blank()
        # 换了底图，旧缩放与位置不再对应同一画面，必须重置视图。
        self._refresh_canvas(reset_view=True)
        self._refresh_lists()
        self._update_nav_buttons()
        # 切到新场景即回到「无改动」：否则上一个场景的脏标记会带过来，让新场景
        # 的保存按钮一亮、再切走又弹确认。
        self._modified = False
        self._refresh_save_button()

    def _dirty(self):
        """返回当前场景是否有未落盘的改动。

        读 `_modified` 标记而不是逐字段与磁盘比对：改动的入口有十来处（新建/编辑/
        删除特征与区域、拖动框、改名、复制），逐处比对的等价判据要覆盖全部字段与
        浮点误差，成本远高于收益。标记由各入口负责置位，落盘后清零。

        Returns：
            布尔值。
        """
        return self._modified

    def _mark_modified(self):
        """标记有改动，并刷新保存按钮的可用状态。

        所有改动数据的入口都要调它。漏调的表现是「改了但保存按钮是灰的」「改了但
        切换时不提示」——两种都是静默的。
        """
        self._modified = True
        self._refresh_save_button()
        if self.auto_save_switch.isChecked():
            # 防抖：一次拖动会连续触发几十次，等手停下来再写盘。
            self._auto_save_timer.start()

    def _on_auto_save_toggled(self, checked):
        """自动保存开关切换。

        Args：
            checked：开关的新状态。

        Note：
            打开时如果已经有未保存改动，也要排一次保存——否则用户改完才打开开关，
            会以为「已经保存了」，其实还要等下一次改动。

            状态立即写进库元数据：它是编辑器偏好，用户下次打开窗口时应保持上次的
            选择。写失败只提示，不阻断编辑。
        """
        try:
            self.library.set_auto_save(checked)
        except Exception as error:  # noqa: BLE001 — 偏好写失败不该让编辑不可用
            self._notify(f"保存自动保存设置失败：{error}", "error")
        if not checked:
            self._auto_save_timer.stop()
            return
        if self._modified:
            self._auto_save_timer.start()

    def _load_auto_save_preference(self):
        """把上次的自动保存开关状态填到控件上。

        Note：
            用 `blockSignals` 包住 `setChecked`：这一步只是还原界面，不该触发
            「开关切换」的副作用（写库元数据、排保存）。
        """
        try:
            meta = self.library.load_meta()
        except Exception:  # noqa: BLE001 — 读不到偏好时保持默认关闭
            return
        self.auto_save_switch.blockSignals(True)
        self.auto_save_switch.setChecked(bool(meta.auto_save))
        self.auto_save_switch.blockSignals(False)

    def _on_name_prefix_toggled(self, checked):
        """名称前缀开关切换。

        Args：
            checked：开关的新状态。

        Note：
            与自动保存开关同口径：它是编辑器偏好，立即写进库元数据，用户下次打开
            窗口时保持上次的选择。写失败只提示，不阻断编辑。
        """
        try:
            self.library.set_name_with_scene_prefix(checked)
        except Exception as error:  # noqa: BLE001 — 偏好写失败不该让编辑不可用
            self._notify(f"保存名称前缀设置失败：{error}", "error")

    def _load_name_prefix_preference(self):
        """把上次的「名称带场景前缀」开关状态填到控件上。

        Note：
            用 `blockSignals` 包住 `setChecked`：这一步只是还原界面，不该触发
            「开关切换」的副作用（再写一次库元数据）。
        """
        try:
            meta = self.library.load_meta()
        except Exception:  # noqa: BLE001 — 读不到偏好时保持默认关闭
            return
        self.name_prefix_switch.blockSignals(True)
        self.name_prefix_switch.setChecked(bool(meta.name_with_scene_prefix))
        self.name_prefix_switch.blockSignals(False)

    def _default_prefix(self, kind):
        """返回新建特征/区域的默认名前缀。

        Args：
            kind：`特征` 或 `区域`。

        Returns：
            开关关闭时是 `kind` 本身（`特征1`）；开启时是 `<场景名>-特征`
            （`主页-特征1`）。

        Note：
            场景名取名称输入框的**当前文本**而不是 `scene.name`：用户常常先改名再
            画框，而改名要保存才落盘，取已保存的名字会生成旧名前缀。
        """
        if not self.name_prefix_switch.isChecked():
            return kind
        return _name_prefix(self.scene, kind, self.name_edit.text())

    def _auto_save(self):
        """把当前改动写入编辑区。

        Note：
            **只对已落盘的场景生效**。新建场景先手动保存一次：否则拖一个框就会以
            「新场景」的名字直接入库，用户还没来得及命名。

            前置条件不满足时静默跳过，只更新开关提示——每次拖动都弹一条浮窗会
            打断编辑。
        """
        if not self.auto_save_switch.isChecked() or not self._modified:
            return
        if self._is_new():
            self.auto_save_switch.setToolTip("新建场景：先手动保存一次，之后修改会自动保存")
            return
        if self._display_frame is None or not self.scene.features:
            self.auto_save_switch.setToolTip("自动保存暂不可用：需要画面与至少一个识别特征")
            return
        if self._write_scene():
            stamp = QTime.currentTime().toString("HH:mm:ss")
            self.auto_save_switch.setToolTip(f"已自动保存 {stamp}")

    def reject(self):
        """关闭前补一次自动保存。

        Note：
            开关打开时，最后一次改动可能还在防抖计时里；直接关窗会丢掉这几百毫秒
            内的修改。写失败不再拦截——用户已经明确点了取消或关闭。
        """
        self._auto_save_timer.stop()
        if self.auto_save_switch.isChecked() and self._modified and not self._is_new():
            self._write_scene()
        super().reject()

    # --- 路由联动 -----------------------------------------------------------

    def _drop_route(self, area_name):
        """删除某个区域的路由。

        Args：
            area_name：区域名。

        Note：
            编辑器里删区域、改名都只作用于**内存里的场景对象**，路由表是另一份
            文件。不在这里同步的话，那次删除要等到「保存」才落盘、而路由会永远
            留在表里，形成一条点不到的边——建图时被判为不一致。
        """
        if self._is_new():
            return
        try:
            self.library.remove_route(self.scene.scene_id, area_name)
        except Exception:  # noqa: BLE001 — 路由清理失败不该阻断编辑
            pass

    def _migrate_route_on_rename(self, old_name, new_name):
        """区域改名时同步迁移路由表的键。

        Args：
            old_name：旧区域名。
            new_name：新区域名。

        Note：
            路由以 `(场景 id, 区域名)` 定位。不同步的话，改名之后原来那条边就指向
            一个不存在的区域——**表现得像「边无故消失」**，而用户以为自己只是改
            了个名字。
        """
        if self._is_new():
            return
        try:
            self.library.ensure_routes()
            if self.library.routes.rename_area(self.scene.scene_id, old_name, new_name):
                self.library.save_routes()
        except Exception:  # noqa: BLE001 — 迁移失败不该阻断编辑
            pass

    def _refresh_save_button(self):
        """按脏标记刷新「应用」与「保存」按钮的可用状态。

        Note:
            **两个按钮共用一个判据**：它们做的是同一件事，只是一个不关窗。
            若让「应用」常亮，用户会在没有任何改动时点它，得到「已应用」的
            虚假反馈；若让它只看自己的状态，两个按钮会在不同时机亮/灰，
            而那种不一致没有任何提示。
        """
        self.save_button.setEnabled(self._modified)
        self.apply_button.setEnabled(self._modified)

    def _confirm_discard(self):
        """确认放弃未保存的改动。

        Returns：
            用户确认放弃时为 True。
        """
        from ok.ui.qt.kit import confirm

        return bool(
            confirm(
                self,
                tr("Unsaved changes"),
                tr("Unsaved changes to 「{name}」 will be lost. Continue?").format(name=self.scene.name),
                ok_text=tr("Discard"),
                danger=True,
            )
        )

    def _update_nav_buttons(self):
        """按当前下标启用/禁用左右按钮并刷新位置标签。"""
        self.prev_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(0 <= self.current_index < len(self.scene_ids) - 1)
        if 0 <= self.current_index < len(self.scene_ids):
            self.position_label.setText(f"{self.current_index + 1}/{len(self.scene_ids)}")
        else:
            self.position_label.setText("")

    def keyPressEvent(self, event):
        """左右方向键切换场景，与标注编辑器一致；Delete 删除当前选中项。

        Args：
            event：按键事件。

        Note:
            **删除只在这里处理，不装全局 `QShortcut`**。`QShortcut` 是窗口级
            的，会连输入框里的 Delete 一起吃掉——用户在「名称」里删字符时整个
            特征被删掉，那是灾难性的。

            而 `keyPressEvent` 只在**本控件及以上**的未处理按键上才会被调用：
            `QLineEdit` 自己消费 Delete（删光标前的字），事件不再冒泡到对话框；
            `QListWidget` 不处理 Delete，事件冒泡到这里，正好命中「选中特征后
            按 Delete」这条路径。实测已验证这两种情形的分流。
        """
        if event.key() == Qt.Key.Key_Left:
            self.prev_scene()
            return
        if event.key() == Qt.Key.Key_Right:
            self.next_scene()
            return
        if event.key() == Qt.Key.Key_Delete:
            # 取画布选中项而不是列表当前行：两者由 `_on_selection_changed` /
            # `_on_feature_row_changed` 双向同步，但点在画布空白处取消选中时
            # 列表仍保留旧行号，用它会删掉一个看起来没被选中的项。
            key = self.canvas.selected_key()
            if key is not None:
                # 复用右键菜单的删除路径：按钮文案、危险配色、二次确认只有一份。
                self._on_canvas_delete(key)
                return
        super().keyPressEvent(event)

    def _load_scene(self, scene_id):
        """读取场景，或造一个待分配 id 的空场景。

        Args：
            scene_id：场景 id；空串表示新建。

        Returns：
            `model.Scene` 实例。
        """
        if scene_id:
            scene = self.library.load_scene(scene_id)
            if scene is not None:
                return scene
        # 新建时用占位 id：`Scene` 要求 id 合法，真实 id 等保存时按计数器分配。
        return model.Scene(scene_id="scene-9999", name="新场景")

    def _is_new(self):
        """返回是否在新建（尚未落盘）。

        Returns：
            True 表示新建。
        """
        return self.library.load_scene(self.scene.scene_id) is None

    # --- 界面 ---------------------------------------------------------------

    def _build_ui(self):
        """搭建界面。"""
        from ok.ui.qt.kit import Button, Label, Switch, TabStrip

        root = QVBoxLayout(self)

        # 顶部一行：名称 + 模式 toggle 组 + 右侧动作按钮
        top = QHBoxLayout()
        top.addWidget(Label(tr("Name"), "body"))
        self.name_edit = QLineEdit(self.scene.name)
        self.name_edit.setMaximumWidth(200)
        # 框架给 QLineEdit 设了 min-height: 34px，会把整行撑高、与 30px 的小按钮
        # 不对齐。这一行是工具条，统一压到 30。
        self.name_edit.setFixedHeight(30)
        self.name_edit.textEdited.connect(self._on_name_edited)
        top.addWidget(self.name_edit)

        self.mode_strip = TabStrip(look="pill")
        for key, text in MODE_LABELS:
            self.mode_strip.add_tab(key, tr(text))
        self.mode_strip.current_changed.connect(self._on_mode_changed)
        top.addWidget(self.mode_strip)
        top.addStretch(1)

        # 上一个 / 下一个场景：与标注编辑器的左右切换一致——图标按钮、subtle 变体、
        # 边界处禁用、支持 ← → 方向键。放在「取消」左侧，符合「浏览类在左、提交类在右」
        # 的阅读顺序。中间的位置标签（`3/12`）让用户知道自己在哪、还剩多少。
        self.prev_button = Button(icon="chevron-left", variant="subtle")
        self.prev_button.setToolTip(tr("Previous scene (←)"))
        self.prev_button.clicked.connect(self.prev_scene)
        self.next_button = Button(icon="chevron-right", variant="subtle")
        self.next_button.setToolTip(tr("Next scene (→)"))
        self.next_button.clicked.connect(self.next_scene)
        self.position_label = Label("", "caption")
        top.addWidget(self.prev_button)
        top.addWidget(self.next_button)
        top.addWidget(self.position_label)
        top.addSpacing(8)

        # 自动保存：只对已落盘的场景生效（新建场景要先手动保存一次，否则拖一个框
        # 就会以「新场景」的名字直接入库）。开关状态存在库元数据里，随库走；
        # 下次打开窗口时恢复上次的选择。
        top.addWidget(Label(tr("Auto Save"), "body"))
        self.auto_save_switch = Switch(False)
        self.auto_save_switch.setToolTip(tr("Save changes automatically (new scenes need one manual save first)"))
        self.auto_save_switch.toggled.connect(self._on_auto_save_toggled)
        top.addWidget(self.auto_save_switch)
        top.addSpacing(8)

        # 新建特征/区域的默认名是否带场景前缀。默认关闭：名称保持「特征1」这种
        # 简洁形式；开启后生成「主页-特征1」，便于在列表里看出归属。开关状态
        # 与自动保存一样存在库元数据里，随库走。
        top.addWidget(Label(tr("Prefix Names"), "body"))
        self.name_prefix_switch = Switch(False)
        self.name_prefix_switch.setToolTip(tr("Add the scene name as prefix to new feature/region names"))
        self.name_prefix_switch.toggled.connect(self._on_name_prefix_toggled)
        top.addWidget(self.name_prefix_switch)
        top.addSpacing(8)

        self.cancel_button = Button(tr("Cancel"), variant="secondary", size="small")
        self.cancel_button.clicked.connect(self.reject)
        top.addWidget(self.cancel_button)

        # 「应用」与「保存」做同一件事，只是前者不关窗：改完想立刻在画布上继续
        # 调形状时，不必「保存 → 关窗 → 重新打开」。
        #
        # 放在保存左侧、用 `tonal` 而不是 `primary`：主按钮只有一个，两个同色的
        # 主按钮会让用户看不出哪个是「做完就走」。
        self.apply_button = Button(tr("Apply"), variant="tonal", size="small")
        self.apply_button.clicked.connect(self._on_apply)
        top.addWidget(self.apply_button)

        self.save_button = Button(tr("Save"), variant="primary", size="small")
        self.save_button.clicked.connect(self._on_save)
        top.addWidget(self.save_button)
        root.addLayout(top)

        # 主体：左侧画布 + 右侧信息区，比例 8:2。
        #
        # 两块各自套一层 `Card()` 圆角外框，与标注编辑器一致（那边也是
        # `canvas_card = Card()` + `side = Card()`，内边距同为 1px）。外框不是装饰：
        # 画布底色与页面底色都是浅灰，只靠颜色分不出边界，边界由 Card 的圆角描边提供。
        # `setContentsMargins(1, 1, 1, 1)` 留出 1px 让 Card 自己的描边可见。
        body = QHBoxLayout()
        body.setSpacing(10)

        self.canvas = canvas_module.SceneCanvas()
        self.canvas.rect_created.connect(self._on_rect_created)
        self.canvas.rect_changed.connect(self._on_rect_changed)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        self.canvas.double_clicked.connect(self._on_canvas_double_clicked)
        # 右键菜单、删除、复制、取色：与标注编辑器同能力。
        self.canvas.context_action.connect(self._on_canvas_context)
        self.canvas.delete_requested.connect(self._on_canvas_delete)
        self.canvas.copy_requested.connect(self._on_canvas_copy)
        self.canvas.color_sampled.connect(self._on_color_sampled)

        canvas_card = Card()
        canvas_layout = QVBoxLayout(canvas_card)
        canvas_layout.setContentsMargins(1, 1, 1, 1)
        canvas_layout.addWidget(self.canvas)
        body.addWidget(canvas_card, 8)

        side = Card()
        side.setFixedWidth(_SIDE_WIDTH)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 10, 10, 10)
        side_layout.setSpacing(8)

        self.feature_list, self._feature_header = _build_side_list(tr("Recognition Features"))
        self.feature_list.itemDoubleClicked.connect(self._on_feature_double_clicked)
        self.feature_list.currentRowChanged.connect(self._on_feature_row_changed)
        # 模板背景色入口挂在右键菜单：它不是「改属性」，而是「调模板的参与像素」，
        # 与属性对话框是两件事。塞进双击那条路径会让用户以为改的是特征属性。
        self.feature_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.feature_list.customContextMenuRequested.connect(self._on_feature_menu)
        side_layout.addWidget(self._feature_header)
        side_layout.addWidget(self.feature_list, 1)

        self.area_list, self._area_header = _build_side_list(tr("Interaction Areas"))
        self.area_list.itemDoubleClicked.connect(self._on_area_double_clicked)
        self.area_list.currentRowChanged.connect(self._on_area_row_changed)
        side_layout.addWidget(self._area_header)
        side_layout.addWidget(self.area_list, 1)
        # 两区等权重分配剩余高度：元素少时各留一段空白，比「上区被内容压扁、下区占满」
        # 更稳定——后者在元素数变化时会让两区高度来回跳。

        body.addWidget(side, 2)
        root.addLayout(body, 1)

        # 底部一行操作提示，与标注编辑器同位置。比状态栏更适合放这类常驻说明——
        # 状态栏留给「警告」这类会变化的内容。
        hint = Label(
            tr(
                "Scroll to zoom · Drag empty space to pan · Drag a box to move it, "
                "its edges to resize · Double-click to edit"
            ),
            "caption",
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.status_label = Label("", "secondary", word_wrap=True)
        root.addWidget(self.status_label)

        self.mode_strip.set_current(MODE_SELECT)
        self._on_mode_changed(MODE_SELECT)

    # --- 帧 ---------------------------------------------------------------

    def _load_frame_or_blank(self):
        """载入已有场景的基准图；新建场景则留空。"""
        if self._is_new():
            self._raw_frame = None
            self._display_frame = None
            return
        preview = self.library.load_base_image(self.scene.scene_id)
        self._display_frame = preview
        # **磁盘上没有未遮罩的原始帧**（它含游戏 UID，绝不落盘），因此重新打开已有
        # 场景时只能用遮罩后的预览图当模板源。落在遮罩区内的特征会裁出纯色块，
        # 由 `template_has_texture` 判为无效，配置页另就重叠给出警告。
        #
        # 必须真的赋值：保存逻辑里是 `if self._raw_frame is not None` 才裁模板，
        # 留 None 会让「重新打开场景改形状后再保存」不更新模板——而界面上看不出
        # 任何异常，只是识别率悄悄下降。
        self._raw_frame = preview

    # --- 画布刷新 -----------------------------------------------------------

    def _refresh_canvas(self, reset_view=False):
        """按当前场景数据重建画布上的矩形。

        **遮罩不画框**：它属不可操作区域，画框会让用户以为能拖。改为把遮罩区
        涂黑——遮罩的语义本来就是「这块内容不可见」。

        Args：
            reset_view：为 True 时把视图重置为「整幅进视口」。只在**换了底图**
                （抓图、导入、载入已有场景）时传 True；改框后的刷新必须保留用户
                当前的缩放与位置，否则「调好位置 → 点确认 → 弹回适应窗口」会反复
                发生。
        """
        from extensions.capabilities.scene_manager.pages.image_convert import bgr_to_qimage

        image = bgr_to_qimage(self._display_frame)
        selected = self.canvas.selected_key()
        self.canvas.set_image(image, keep_view=not reset_view)

        meta = self.library.meta
        if not meta.has_resolution() or self._display_frame is None:
            self.canvas.set_items([])
            self.canvas.set_mask_rect(None)
            return

        items = []
        for feature in self.scene.features:
            # 形状的包围盒即模板裁剪范围，同时是画布上要画的框。
            rect = feature.pixel_rect(meta.base_width, meta.base_height)
            color = COLOR_FEATURE_MANDATORY if feature.mandatory else COLOR_FEATURE_OPTIONAL
            suffix = "" if feature.mandatory else "（可选）"
            items.append(canvas_module.CanvasItem(("feature", feature.name), rect, color, f"{feature.name}{suffix}"))
        # 目标场景名只加载一次，避免每个区域都读一遍磁盘。
        scenes_by_id = {scene.scene_id: scene for scene in self.library.load_library()[0]}
        for area in self.scene.areas:
            rect = _ratio_to_pixels(area.rect, meta.base_width, meta.base_height)
            if rect is None:
                continue
            # 目标场景来自路由表，不在区域上。没有路由就画纯名称。
            target = self.library.route_target(self.scene.scene_id, area.name)
            target_scene = scenes_by_id.get(target) if target else None
            if target_scene is not None and target_scene.name and target_scene.name != target:
                suffix = f"→{target_scene.name}（{target}）"
            else:
                suffix = f"→{target}" if target else ""
            items.append(canvas_module.CanvasItem(("area", area.name), rect, COLOR_AREA, f"{area.name}{suffix}"))
        self.canvas.set_items(items)
        # 遮罩只涂黑，不加框、不可选、不可拖。
        self.canvas.set_mask_rect(runtime.mask_rect_for(meta.base_width, meta.base_height))
        if selected is not None:
            self.canvas.set_selected(selected)

    def _refresh_lists(self):
        """按当前场景数据重建右侧两个列表。

        每项两行：名称 + 属性摘要。摘要只放「改完想核对的那几项」——识别方式、是否
        必需、位置、探测开关、目标场景、等待时长；阈值之类只在属性对话框里看，
        全塞进来会让窄栏的每项都折行。
        """
        self._loading = True
        meta = self.library.meta
        try:
            self.feature_list.clear()
            for feature in self.scene.features:
                kind = tr("Template") if feature.kind == model.KIND_TEMPLATE else tr("Text")
                required = tr("Required") if feature.mandatory else tr("Optional")
                summary = f"{kind} · {required} · {_format_shape(feature, meta)}"
                # 背景色排除是「看不见的配置」——不显示的话用户改完关了窗口
                # 就再也想不起来这个特征到底排不排颜色。
                if feature.bg_color:
                    summary += " · " + tr("BG {color}±{tol}").format(
                        color=",".join(str(value) for value in feature.bg_color),
                        tol=feature.bg_tolerance,
                    )
                self._append_item(self.feature_list, feature.name, summary)

            self.area_list.clear()
            for area in self.scene.areas:
                parts = [_format_rect(area.rect)]
                if area.explore_probe:
                    parts.append(tr("Probe"))
                # 路由信息（目标场景、等待）不在区域上：它们属于流转图。这里只
                # 顺带显示目标，让用户知道「这个区域已经通了」，改在流转页改。
                route = self.library.routes.get(self.scene.scene_id, area.name)
                if route is not None:
                    parts.append(f"→ {route.target_scene}")
                    parts.append(f"{route.after_wait:.1f}s")
                self._append_item(self.area_list, area.name, " · ".join(parts))
        finally:
            self._loading = False

        _set_side_count(self._feature_header, len(self.scene.features))
        _set_side_count(self._area_header, len(self.scene.areas))
        self._refresh_status()
        # 项高终值要等布局完成才准（主题 QSS 的 item padding 此时才算进去）。
        # `singleShot(0)` 把校正排到本轮事件循环末尾，而不是在这里同步取——
        # 同步取到的仍是加入项之前的估算值。
        QTimer.singleShot(0, self._fit_list_heights)

    def _fit_list_heights(self):
        """布局完成后校正两个列表的项高（见 :func:`_fit_item_heights`）。"""
        _fit_item_heights(self.feature_list)
        _fit_item_heights(self.area_list)

    def _install_width_tracking(self):
        """给两个列表装上宽度变化监听，宽度一变就重算项高。

        Note:
            **不能只挂对话框的 `resizeEvent`**。侧栏宽度是固定的
            （`_SIDE_WIDTH`），缩对话框不会让列表变窄；而真正会变窄的情形
            （拖动分栏、字号变化、主题切换改变 padding）都不会触发对话框的
            `resizeEvent`——那时项高停留在旧宽度的折行结果，表现为「文字被裁」
            或「下方多一片空白」。

            因此直接监听**列表自己的**视口尺寸变化。装 QObject 事件过滤器而不是
            子类化：列表是公共控件（`_build_side_list` 里创建），子类化会让两处
            都要记得用新类。

            监听目标记在 `_width_tracking_targets` 里，`eventFilter` 据此判断
            「这个 Resize 是否来自本对话框关心的列表」。不用 `is` 逐个比对视图：
            目标可能有多个，逐个写条件在新增列表时会漏。
        """
        targets = {}
        for widget in (self.feature_list.viewport(), self.area_list.viewport()):
            widget.installEventFilter(self)
            targets[widget] = True
        self._width_tracking_targets = targets

    def eventFilter(self, watched, event):
        """列表视口尺寸变化时重算项高。

        Args:
            watched: 被监听对象。
            event: 事件。

        Returns:
            False（不拦截，让控件自己继续处理）。

        Note:
            用 `QTimer.singleShot(0, ...)` 推迟：`QEvent.Resize` 到达时视口宽度
            还没更新到新值，同步算出来的仍是旧宽度的折行结果。
        """
        if event.type() == QEvent.Type.Resize and watched in self._width_tracking_targets:
            QTimer.singleShot(0, self._fit_list_heights)
        return False

    def resizeEvent(self, event):
        """窗口尺寸变化后按新宽度重算项高。

        Args:
            event: 尺寸事件。

        Note:
            列表视口另有 :meth:`_install_width_tracking` 监听。这里保留是为了
            覆盖「窗口缩放导致列表宽度变化」这条主路径——它同时会触发视口的
            Resize，但多算一次无害（`_fit_item_heights` 幂等）。
        """
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_list_heights)

    @staticmethod
    def _append_item(list_widget, title, summary):
        """向列表追加一个「名称 + 摘要」两行项。

        用 `QListWidgetItem` 的文本换行而不是 `setItemWidget` 放自定义控件：
        自定义控件会随每个项创建 widget，几十个元素时列表滚动明显发滞；而这里
        只需要两行不同字号——换行文本加统一的 item 样式已经够用，且选中态自动生效。

        Args：
            list_widget：目标列表。
            title：第一行，元素名称。
            summary：第二行，属性摘要。

        Note:
            `sizeHint` 的**宽度必须给视口宽度**。宽度传 0 时 delegate 认为没有
            可用宽度，于是不换行、直接单行省略——长摘要被截成 `…` 而项高按多行
            算好了，下方留一片空白。这个组合最容易误导：看着像「宽度不够」，
            实际是「没告诉它有多宽」。
        """
        item = QListWidgetItem(f"{title}\n{summary}")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        list_widget.addItem(item)
        width = max(1, list_widget.viewport().width())
        # 先给一个含内边距的两行保底高度（无主题 QSS 时 `QListWidget` 完全不按
        # 换行符撑高项）；真实高度由 `_fit_item_heights` 在布局完成后按折行结果校正。
        item.setSizeHint(QSize(width, _two_line_height(list_widget) + _item_text_box(list_widget)[1]))

    # --- 状态栏 -------------------------------------------------------------

    def _notify(self, message, level="info", title=None):
        """弹一条浮层提示，同时写进弹窗底部的状态栏。

        Args：
            message：正文。
            level：`info` / `success` / `warning` / `error`。
            title：可选的加粗标题。

        Note:
            **浮层挂在弹窗自己身上**，不会跑到主窗口——`show_toast` 取的是
            传入控件的 `window()`，而编辑器是模态 `QDialog`，它自己就是一个窗口。

            与 :meth:`_refresh_status` 的分工：那个是**随编辑实时重算**的常驻
            警告（必需特征数、遮罩重叠），每次改动都会算一遍，弹浮层会刷屏；
            本方法只用于**一次性操作结果**（校验没通过、取色、保存被拦）。
        """
        from extensions.capabilities.scene_manager import notify

        self.status_label.setText(f"{title}：{message}" if title else message)
        notify.notify(self, message, level, title)

    def _refresh_status(self):
        """刷新提示：必需特征数、遮罩重叠。"""
        warnings = []
        notes = []
        mandatory = self.scene.mandatory_features()
        if not mandatory and self.scene.features:
            warnings.append("警告：没有必需特征，该场景永远不会被命中")
        if len(mandatory) > 3:
            notes.append(f"提示：必需特征 {len(mandatory)} 个，超过 3 个时任一不中就整场景不命中")

        meta = self.library.meta
        if meta.has_resolution():
            mask = runtime.mask_rect_for(meta.base_width, meta.base_height)
            overlaps = recognize.mask_overlap_warnings(self.scene, mask, meta.base_width, meta.base_height)
            for name, _intersection, ratio in overlaps:
                warnings.append(f"警告：特征「{name}」与截图遮罩重叠 {ratio:.0%}，裁出的模板会是纯色块，匹配必然失败")
        lines = warnings + notes
        separator = chr(10)
        self.status_label.setText(separator.join(lines) if lines else "")

    # --- 模式 ---------------------------------------------------------------

    def _on_mode_changed(self, key):
        """模式切换（toggle 组已保证互斥与高亮）。

        Args：
            key：`MODE_*` 之一。
        """
        mapping = {
            MODE_SELECT: canvas_module.MODE_SELECT,
            MODE_FEATURE: canvas_module.MODE_DRAW_FEATURE,
            MODE_AREA: canvas_module.MODE_DRAW_AREA,
        }
        self.canvas.set_mode(mapping.get(key, canvas_module.MODE_SELECT))

    def _current_mode(self):
        """返回当前模式 key。

        Returns：
            `MODE_*` 之一。
        """
        return self.mode_strip.current_key() or MODE_SELECT

    # --- 新建与编辑 ---------------------------------------------------------

    def _on_rect_created(self, x1, y1, x2, y2):
        """框选完成：先弹属性对话框，确认后才新增。

        Args：
            x1：左上 x（帧像素）。
            y1：左上 y。
            x2：右下 x。
            y2：右下 y。
        """
        meta = self.library.meta
        if not meta.has_resolution():
            self._notify("缺少基准分辨率：请先抓取画面或导入图片", "warning")
            return
        try:
            rect = _pixels_to_ratio(x1, y1, x2, y2, meta.base_width, meta.base_height)
        except ValueError as error:
            self._notify(f"框选无效：{error}", "error")
            return

        mode = self._current_mode()
        if mode == MODE_FEATURE:
            self._create_feature(rect, x1, y1, x2, y2)
        elif mode == MODE_AREA:
            self._create_area(rect)
        # 画完回到选择模式：连续画多个是少数需求，而留在画图模式会让用户想调整
        # 刚画的框时又画出一个新的。
        self.mode_strip.set_current(MODE_SELECT)

    def _create_feature(self, rect, x1, y1, x2, y2):
        """弹出属性对话框并新增特征。

        Args：
            rect：相对比例矩形。
            x1：左上 x（帧像素）。
            y1：左上 y。
            x2：右下 x。
            y2：右下 y。
        """
        existing = [item.name for item in self.scene.features]
        prefix = self._default_prefix("特征")
        default_name = _next_name(existing, prefix)
        dialog = PropertyDialog(
            tr("New Recognition Feature"),
            [
                ("name", tr("Name"), default_name, None),
                (
                    "kind",
                    tr("Recognition Method"),
                    model.KIND_TEMPLATE,
                    [(tr("Template Match"), model.KIND_TEMPLATE), (tr("Text (OCR)"), model.KIND_OCR)],
                ),
                (
                    "mandatory",
                    tr("Required"),
                    True,
                    TOGGLE_FIELD,
                ),
                ("threshold", tr("Threshold (0-1)"), "0.80", None),
            ],
            parent=self,
            # 「期望文本」只在 OCR 下显示，且排在「阈值」之后（两者合起来构成匹配
            # 参数）。控制器是「识别方式」，锚点是「阈值」。
            dynamic_params=("kind", _feature_kind_fields, "threshold"),
        )
        accepted = dialog.exec()
        values = dialog.values() if accepted else {}
        dialog.deleteLater()
        if not accepted:
            return

        name = values.get("name") or default_name
        if any(item.name == name for item in self.scene.features):
            self._notify(f"特征名「{name}」已存在", "error")
            return
        try:
            threshold = float(values.get("threshold") or 0.8)
        except ValueError:
            self._notify("阈值必须是数字", "error")
            return
        if not 0.0 < threshold <= 1.0:
            self._notify("阈值必须落在 (0, 1]", "error")
            return

        kind = values.get("kind")
        text = values.get("text", "")
        if kind == model.KIND_OCR and not text:
            self._notify("OCR 特征必须填写期望文本", "error")
            return

        feature = model.Feature(
            name=name,
            kind=kind,
            mandatory=bool(values.get("mandatory")),
            shape=shapes.Shape.rectangle(*rect),
            threshold=threshold,
            text=text if kind == model.KIND_OCR else "",
        )
        self.scene.features.append(feature)
        self._mark_modified()

        notes = []
        if kind == model.KIND_TEMPLATE and self._raw_frame is not None:
            crop = self._raw_frame[y1:y2, x1:x2]
            if crop.size and not _has_texture(crop):
                notes.append(
                    f"特征「{name}」框内几乎纯色，模板匹配无法用它区分画面"
                    f"（识别时恒判未命中），建议改框有纹理或文字的区域"
                )
        self._refresh_canvas()
        self._refresh_lists()
        self.canvas.set_selected(("feature", name))
        if notes:
            self._notify(chr(10).join(notes), "warning", "特征已创建，但有以下问题")

    def _create_area(self, rect):
        """弹出属性对话框并新增区域。

        Args：
            rect：相对比例矩形。
        """
        existing = [item.name for item in self.scene.areas]
        prefix = self._default_prefix("区域")
        default_name = _next_name(existing, prefix)
        dialog = PropertyDialog(
            tr("New Interaction Region"),
            [
                ("name", tr("Name"), default_name, None),
                (
                    "explore_probe",
                    tr("Allow Exploration Probe"),
                    False,
                    TOGGLE_FIELD,
                ),
                *_explore_action_field(model.ACTION_CLICK),
                # 与编辑弹窗同序：布尔开关排在动作参数之后，切动作时多出的行
                # 不会把开关挤到中间。
                (
                    "explore_return",
                    tr("Exploration Return Region"),
                    False,
                    TOGGLE_FIELD,
                ),
            ],
            parent=self,
            dynamic_params=("explore_action", _explore_param_fields),
        )
        accepted = dialog.exec()
        values = dialog.values() if accepted else {}
        dialog.deleteLater()
        if not accepted:
            return

        name = values.get("name") or default_name
        if any(item.name == name for item in self.scene.areas):
            self._notify(f"区域名「{name}」已存在", "error")
            return

        action = values.get("explore_action") or model.ACTION_CLICK
        try:
            params = _collect_action_params(action, values)
        except ValueError as error:
            self._notify(f"动作参数无效：{error}", "error")
            return

        area = model.Area(
            name=name,
            rect=rect,
            explore_probe=bool(values.get("explore_probe")),
            explore_return=bool(values.get("explore_return")),
            explore_action=action,
            explore_params=params,
        )
        self.scene.areas.append(area)
        self._mark_modified()
        self._refresh_canvas()
        self._refresh_lists()
        self.canvas.set_selected(("area", name))

    def _on_canvas_context(self, action, key):
        """右键菜单：复制、编辑属性、编辑模板图、删除。

        Args：
            action：`canvas` 发来的动作名，固定为 `menu`。
            key：命中的画布项 key。

        Note：
            「编辑模板图」只对**模板匹配特征**可点。坐标框上右键是用户最自然的
            入口——他正看着那个框，想调的就是框里裁出来的模板；要求他先去右侧
            列表里找到同名项再右键，等于把「我已经知道是哪个」的信息丢掉重找。
        """
        del action
        if key is None:
            return
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction(tr("Copy"), lambda: self._on_canvas_copy(key))
        menu.addSeparator()
        menu.addAction(tr("Edit Properties"), lambda: self._on_canvas_double_clicked(key))
        if key[0] == "feature":
            feature = self.scene.find_feature(key[1])
            template_action = menu.addAction(
                tr("Edit Template Image"),
                lambda: self._edit_template_image_at(_index_of(self.scene.features, key[1])),
            )
            # OCR 特征没有模板图，菜单项保留但禁用：直接隐藏会让用户以为
            # 「这个特征不能调模板」，而禁用加灰能传达「不是这个类型」。
            template_action.setEnabled(feature is not None and feature.kind == model.KIND_TEMPLATE)
        menu.addAction(tr("Delete"), lambda: self._on_canvas_delete(key))
        menu.exec(QCursor.pos())

    def _on_canvas_delete(self, key):
        """删除画布项对应的特征或区域。

        Args：
            key：`("feature"|"area", name)`。
        """
        if not key:
            return
        kind, name = key
        from ok.ui.qt.kit import confirm

        if not confirm(
            self,
            tr("Delete"),
            tr("Delete 「{name}」? This cannot be undone.").format(name=name),
            ok_text=tr("Delete"),
            danger=True,
        ):
            return
        if kind == "feature":
            feature = self.scene.find_feature(name)
            if feature is not None:
                self.scene.features.remove(feature)
                self._mark_modified()
        elif kind == "area":
            area = self.scene.find_area(name)
            if area is not None:
                self.scene.areas.remove(area)
                # 区域删了，它的路由必须跟着删：留着就是一条「点不到的边」，
                # 建图时会因为「区域不存在」被判为不一致。
                self._drop_route(name)
                self._mark_modified()
        self._refresh_canvas()
        self._refresh_lists()

    def _on_canvas_copy(self, key):
        """复制画布项：新名字加 `_copy` 后缀，位置下移 20px。

        Args：
            key：`("feature"|"area", name)`。
        """
        if not key:
            return
        kind, name = key
        self._mark_modified()
        if kind == "feature":
            source_feature = self.scene.find_feature(name)
            if source_feature is None:
                return
            new_name = _next_name([item.name for item in self.scene.features], f"{name}_copy")
            self.scene.features.append(
                model.Feature(
                    name=new_name,
                    kind=source_feature.kind,
                    mandatory=source_feature.mandatory,
                    shape=shapes.Shape(
                        source_feature.shape.kind,
                        [(x, y) for x, y in source_feature.shape.points],
                        angle=source_feature.shape.angle,
                        sweep=source_feature.shape.sweep,
                        radius=source_feature.shape.radius,
                    ),
                    threshold=source_feature.threshold,
                    text=source_feature.text,
                )
            )
        elif kind == "area":
            source_area = self.scene.find_area(name)
            if source_area is None:
                return
            new_name = _next_name([item.name for item in self.scene.areas], f"{name}_copy")
            rect = source_area.rect
            # **不复制路由**：新区域是新键，没有边。复制目标会让「复制的那个按钮」
            # 凭空拥有一条没被验证过的边，而用户以为是复制过去的。
            self.scene.areas.append(
                model.Area(
                    name=new_name,
                    rect=(rect[0], min(0.99, rect[1] + 0.02), rect[2], min(1.0, rect[3] + 0.02)),
                    explore_probe=source_area.explore_probe,
                    explore_action=source_area.explore_action,
                    explore_params=dict(source_area.explore_params),
                )
            )
        self._refresh_canvas()
        self._refresh_lists()

    def _on_color_sampled(self, abs_x, abs_y, rel_x, rel_y, color):
        """在状态栏显示鼠标处的像素信息与坐标。

        与标注编辑器一致：越界时只显示坐标。

        Args：
            abs_x：图像坐标 x。
            abs_y：图像坐标 y。
            rel_x：相对比例 x。
            rel_y：相对比例 y。
            color：`QColor` 或 None。

        Note:
            **只写状态栏，绝不弹浮层**。这个回调由 `mouseMoveEvent` 触发——鼠标
            在画布上每移动一像素就会调一次。改成浮层的话，每移动一下弹一条，
            几秒内堆满整个屏幕（实测踩过：鼠标划过画布，取色提示连续弹出）。

            它是**常驻的指针坐标读数**，性质与状态栏一致：用户需要时看一眼，
            不需要时不打扰。
        """
        position = f"({abs_x}, {abs_y}) · ({rel_x:.3f}, {rel_y:.3f})"
        if color is None:
            self.status_label.setText(position)
            return
        self.status_label.setText(f"R:{color.red()} G:{color.green()} B:{color.blue()} · {position}")

    def _on_canvas_double_clicked(self, key):
        """双击画布上的框：直接打开该框的属性对话框。

        与双击列表项走同一条路径（都落到 :meth:`_edit_feature_at` /
        :meth:`_edit_area_at`）。两条入口必须共用同一份编辑逻辑——各写一份的话，
        「列表里改」与「图上改」迟早出现字段覆盖范围不一致。

        未命中任何框（`key` 为 None）时不弹窗：空白处双击多半是无意。

        Args：
            key：`("feature"|"area", name)` 或 None。
        """
        if key is None:
            return
        kind, name = key
        if kind == "feature":
            self._edit_feature_at(_index_of(self.scene.features, name))
        elif kind == "area":
            self._edit_area_at(_index_of(self.scene.areas, name))

    def _on_feature_double_clicked(self, item):
        """双击列表项时按它所在的行打开属性对话框。

        Args：
            item：被双击的 `QListWidgetItem`。
        """
        self._edit_feature_at(self.feature_list.row(item))

    def _on_feature_menu(self, position):
        """特征列表的右键菜单。

        Args：
            position：点击位置（控件坐标）。

        Note：
            只对**模板匹配**特征开放「模板图处理」：OCR 特征没有模板图，
            那个菜单项点了也没有可处理的对象。
        """
        row = self.feature_list.row(self.feature_list.itemAt(position))
        if not 0 <= row < len(self.scene.features):
            return
        feature = self.scene.features[row]
        menu = QMenu(self)
        edit_action = menu.addAction(tr("Edit Properties"))
        shape_action = menu.addAction(tr("Edit Template Image"))
        shape_action.setEnabled(feature.kind == model.KIND_TEMPLATE)
        chosen = menu.exec(self.feature_list.viewport().mapToGlobal(position))
        if chosen is edit_action:
            self._edit_feature_at(row)
        elif chosen is shape_action:
            self._edit_template_image_at(row)

    def _edit_template_image_at(self, row):
        """打开模板图处理对话框，并把结果写回特征。

        Args：
            row：特征下标；越界或非模板匹配特征时直接返回。

        Note：
            模板图取自**当前工作帧**的对应区域，而不是磁盘上的旧图：用户可能
            刚换了截图、刚改了形状，磁盘上的模板还没更新。用旧图取色会让
            「看到的颜色」与「实际参与匹配的模板」不一致。

            `_raw_frame` 为空（新建场景且尚未截图）时不打开对话框——那种情况下
            连模板都裁不出来，让用户去配背景色只会更困惑。
        """
        if not 0 <= row < len(self.scene.features):
            return
        feature = self.scene.features[row]
        if feature.kind != model.KIND_TEMPLATE:
            self._notify("只有模板匹配特征需要配置模板图", "warning")
            return

        meta = self.library.meta
        template = None
        x1 = y1 = x2 = y2 = 0
        base_width, base_height = meta.base_width, meta.base_height
        if self._raw_frame is not None and base_width > 0 and base_height > 0:
            x1, y1, x2, y2 = feature.pixel_rect(base_width, base_height)
            crop = self._raw_frame[y1:y2, x1:x2]
            if crop.size:
                template = crop
        if template is None:
            template = self.library.load_template(self.scene.scene_id, feature.name)

        from extensions.capabilities.scene_manager.pages.template_image import TemplateImageDialog

        dialog = TemplateImageDialog(
            template,
            color=feature.bg_color,
            tolerance=feature.bg_tolerance or 4,
            shape=feature.mask_shape,
            parent=self,
        )
        accepted = dialog.exec()
        color = dialog.color
        tolerance = dialog.tolerance
        edited_shape = dialog.shape
        dialog.deleteLater()
        if not accepted:
            return

        feature.bg_color = color
        feature.bg_tolerance = tolerance if color else 0
        feature.mask_shape = edited_shape
        self._mark_modified()
        self._refresh_lists()
        shape_note = "" if edited_shape.kind == mask_shape.MASK_NONE else f"，掩码形状 {edited_shape.kind}"
        if color is None:
            self._notify(f"特征「{feature.name}」已停用背景色排除{shape_note}；保存后生效", "info", "模板图处理")
        else:
            self._notify(
                f"特征「{feature.name}」背景色 (B,G,R)=({color[0]}, {color[1]}, {color[2]})，"
                f"容差 {tolerance}{shape_note}；保存后生效",
                "info",
                "模板图处理",
            )

    def _on_area_double_clicked(self, item):
        """双击列表项时按它所在的行打开属性对话框。

        Args：
            item：被双击的 `QListWidgetItem`。
        """
        self._edit_area_at(self.area_list.row(item))

    def _edit_feature_at(self, row):
        """按行号编辑特征。

        Args：
            row：特征在 `scene.features` 中的下标；越界时直接返回。
        """
        if not 0 <= row < len(self.scene.features):
            return
        feature = self.scene.features[row]
        dialog = PropertyDialog(
            tr("Edit Recognition Feature"),
            [
                ("name", tr("Name"), feature.name, None),
                (
                    "kind",
                    tr("Recognition Method"),
                    feature.kind,
                    [(tr("Template Match"), model.KIND_TEMPLATE), (tr("Text (OCR)"), model.KIND_OCR)],
                ),
                (
                    "mandatory",
                    tr("Required"),
                    feature.mandatory,
                    TOGGLE_FIELD,
                ),
                ("threshold", tr("Threshold (0-1)"), f"{feature.threshold:.2f}", None),
            ],
            parent=self,
            dynamic_params=("kind", _feature_kind_fields, "threshold"),
        )
        # 已有文本作为动态区初值：否则编辑一个 OCR 特征时，「期望文本」会显示空串，
        # 用户以为原文被清掉了。
        dialog.set_dynamic_values({"text": feature.text} if feature.text else {})
        accepted = dialog.exec()
        values = dialog.values() if accepted else {}
        dialog.deleteLater()
        if not accepted:
            return

        name = values.get("name") or feature.name
        if name != feature.name and any(item.name == name for item in self.scene.features):
            self._notify(f"特征名「{name}」已存在", "error")
            return
        try:
            threshold = float(values.get("threshold") or feature.threshold)
        except ValueError:
            self._notify("阈值必须是数字", "error")
            return
        if not 0.0 < threshold <= 1.0:
            self._notify("阈值必须落在 (0, 1]", "error")
            return
        kind = values.get("kind")
        text = values.get("text", "")
        if kind == model.KIND_OCR and not text:
            self._notify("OCR 特征必须填写期望文本", "error")
            return

        renamed = name != feature.name
        feature.name = name
        self._mark_modified()
        # 改名后模板与掩码的文件名主干都要跟着改，否则下次保存会找不到旧文件、
        # 清理时又把新文件当残留删掉。
        feature.template_file = f"templates/{name}.png"
        feature.mask_file = f"templates/{name}_mask.png"
        feature.kind = kind
        feature.mandatory = bool(values.get("mandatory"))
        feature.threshold = round(threshold, 4)
        feature.text = text if kind == model.KIND_OCR else ""
        # 名字变了意味着画布标识变了，必须重建；否则旧标识的框还在。
        if renamed:
            self._refresh_canvas()
        self._refresh_lists()
        self.canvas.set_selected(("feature", name))

    def _edit_area_at(self, row):
        """按行号编辑交互区域。

        Args：
            row：区域在 `scene.areas` 中的下标；越界时直接返回。
        """
        if not 0 <= row < len(self.scene.areas):
            return
        area = self.scene.areas[row]
        dialog = PropertyDialog(
            tr("Edit Interaction Region"),
            [
                ("name", tr("Name"), area.name, None),
                (
                    "explore_probe",
                    tr("Allow Exploration Probe"),
                    area.explore_probe,
                    TOGGLE_FIELD,
                ),
                *_explore_action_field(area.explore_action),
                # 布尔开关放在最后：动作参数是动作的附属项，插在动作行之后，
                # 开关排在它们后面才不会被「切动作时多出几行」挤到中间。
                (
                    "explore_return",
                    tr("Exploration Return Region"),
                    area.explore_return,
                    TOGGLE_FIELD,
                ),
            ],
            parent=self,
            dynamic_params=("explore_action", _explore_param_fields),
        )
        # 已有参数作为动态区的初值：否则编辑一个填过 2.5 秒的长按区域，输入框会
        # 显示模型默认的 1.0——用户以为值被改了。
        dialog.set_dynamic_values({f"param_{key}": value for key, value in (area.explore_params or {}).items()})
        accepted = dialog.exec()
        values = dialog.values() if accepted else {}
        dialog.deleteLater()
        if not accepted:
            return

        name = values.get("name") or area.name
        if name != area.name and any(item.name == name for item in self.scene.areas):
            self._notify(f"区域名「{name}」已存在", "error")
            return
        action = values.get("explore_action") or model.ACTION_CLICK
        try:
            params = _collect_action_params(action, values)
        except ValueError as error:
            self._notify(f"动作参数无效：{error}", "error")
            return

        # 区域改名要同步路由表的键：路由以 `(场景 id, 区域名)` 定位，不同步的话
        # 改名之后原来那条边就指向一个不存在的区域，表现为「边无故消失」。
        old_name = area.name
        renamed = name != old_name
        area.name = name
        self._mark_modified()
        area.explore_probe = bool(values.get("explore_probe"))
        area.explore_return = bool(values.get("explore_return"))
        area.explore_action = action
        area.explore_params = params
        if renamed:
            self._migrate_route_on_rename(old_name, name)
        self._refresh_canvas()
        self._refresh_lists()
        self.canvas.set_selected(("area", name))

    def _on_name_edited(self, _text):
        """名称被用户编辑：标记有改动。

        用 `textEdited` 而不是 `textChanged`：后者在程序 `setText` 时也会触发
        （载入场景、切换场景都会），会把「刚打开」误标成「已修改」，于是保存按钮
        一亮、切换就弹确认。

        Args：
            _text：新文本（未使用）。
        """
        self._mark_modified()

    def _on_rect_changed(self, key, rect_pixels):
        """画布上拖动或调整了矩形：写回场景数据。

        Args：
            key：`("feature"|"area", name)`。
            rect_pixels：帧像素矩形。
        """
        meta = self.library.meta
        if not meta.has_resolution():
            return
        try:
            ratio = _pixels_to_ratio(
                rect_pixels[0],
                rect_pixels[1],
                rect_pixels[2],
                rect_pixels[3],
                meta.base_width,
                meta.base_height,
            )
        except ValueError:
            return
        kind, name = key
        self._mark_modified()
        if kind == "feature":
            feature = self.scene.find_feature(name)
            if feature is not None:
                # 拖矩形手柄改的是包围盒。非矩形形状（圆/扇形/多边形）的包围盒被拖
                # 动时无法反推原参数，因此只有矩形直接改形状，其余保持原样——宁可
                # 不响应，也不要猜出一个形状完全变了的掩码。
                if feature.shape.kind == shapes.SHAPE_RECT:
                    feature.shape = shapes.Shape.rectangle(*ratio, angle=feature.shape.angle)
        elif kind == "area":
            area = self.scene.find_area(name)
            if area is not None:
                area.rect = ratio
        self._refresh_lists()
        self._refresh_status()

    def _on_selection_changed(self, key):
        """画布选中项变化时同步列表选中行。

        Args：
            key：`("feature"|"area", name)` 或 None。
        """
        if key is None:
            return
        kind, name = key
        self._loading = True
        try:
            if kind == "feature":
                for row, feature in enumerate(self.scene.features):
                    if feature.name == name:
                        self.feature_list.setCurrentRow(row)
                        break
            elif kind == "area":
                for row, area in enumerate(self.scene.areas):
                    if area.name == name:
                        self.area_list.setCurrentRow(row)
                        break
        finally:
            self._loading = False

    def _on_feature_row_changed(self, row):
        """列表选中特征时同步画布。

        Args：
            row：被选中的行号；-1 表示取消选中。
        """
        if self._loading or not 0 <= row < len(self.scene.features):
            return
        self.canvas.set_selected(("feature", self.scene.features[row].name))

    def _on_area_row_changed(self, row):
        """列表选中区域时同步画布。

        Args：
            row：被选中的行号；-1 表示取消选中。
        """
        if self._loading or not 0 <= row < len(self.scene.areas):
            return
        self.canvas.set_selected(("area", self.scene.areas[row].name))

    # --- 保存 ---------------------------------------------------------------

    def _on_apply(self):
        """「应用」：写盘但**不关窗**，便于接着调。

        Returns：
            无。

        Note:
            与「保存」共用 :meth:`_write_scene`，只是省掉 `accept()`。写盘后要
            清脏标记并刷按钮——否则用户点完「应用」，保存按钮还是亮的、标题仍
            显示「未保存」，会以为没生效。
        """
        if not self._write_scene():
            return
        self._notify("已保存到编辑区，可继续调整", "success", "已应用")

    def _on_save(self):
        """保存场景到编辑区并关闭弹窗。

        这里只写**编辑区**。产物区由页签上的「保存到 assets」按钮经
        :meth:`store.SceneLibrary.export_to` 导出——两件事分开是刻意的：编辑随时
        发生且频繁，导出是明确的一次性动作，且要能拒绝资产不全的场景。
        """
        if not self._write_scene():
            return
        self.accept()

    def _write_scene(self):
        """把场景写进编辑区（不关窗、不弹成功提示）。

        Returns：
            True 表示已写盘；False 表示前置条件不满足（调用方应中止）。

        Note:
            「应用」与「保存」共用本方法。两条入口各写一份的话，模板与掩码的
            生成规则、陈旧资质的清理迟早分叉，而那种分叉**不会报错**——只会
            表现为「点这个按钮存的是对的、点那个存的是旧的」。
        """
        if self._display_frame is None:
            self._notify("请先抓取画面或导入图片再保存", "warning")
            return False
        if not self.scene.features:
            self._notify("至少需要一个识别特征，否则该场景永远不会被识别", "error")
            return False

        name = self.name_edit.text().strip()
        self.library.ensure_layout()

        if self._is_new():
            self.scene.scene_id = self.library.next_scene_id()
        self.scene.name = name or self.scene.scene_id

        meta = self.library.meta
        # 模板与掩码都按**原始帧**裁：预览图是遮罩后的，从它裁出的模板在遮罩区会是
        # 纯色块，而纯色模板在 TM_CCOEFF_NORMED 下恒得 1.0，等于永远命中。
        for feature in self.scene.features:
            if feature.kind != model.KIND_TEMPLATE:
                continue
            x1, y1, x2, y2 = feature.pixel_rect(meta.base_width, meta.base_height)
            template = None
            if self._raw_frame is not None:
                crop = self._raw_frame[y1:y2, x1:x2]
                if crop.size:
                    self.library.save_template(self.scene.scene_id, feature.name, crop)
                    template = crop
            if template is None:
                # 没有原始帧（重新打开已有场景时用的是遮罩后的基准图）时，
                # 掩码仍要更新：**形状参数是用户刚改的配置**，不写盘就等于没改。
                # 颜色部分退化为用磁盘上的旧模板算——它和刚裁出的模板只差遮罩区，
                # 而在遮罩区里本来就不该有有效前景。
                template = self.library.load_template(self.scene.scene_id, feature.name)
            # **掩码 = 掩码形状 ∧ 背景色**，一次算好写盘（见 `Feature.build_mask`）。
            #
            # 这是修一个真实 bug：此前只写 `feature.shape.mask(...)`（纯几何），
            # 颜色剔除全靠运行期现算，于是落盘的 `_mask.png` 与真实参与匹配的像素
            # 不一致——排查时看 mask 图会得出「颜色剔除没生效」的错误结论，
            # 而配置里明明已经设了 bg_color。
            mask = feature.build_mask(template)
            if mask is None:
                # 既不筛形状也不筛颜色：删掉旧的掩码文件而不是写一张全 255。
                # 全 255 的文件让「这个特征有没有掩码」看不出来，而删掉后
                # `mask_path` 返回不存在的路径、加载器返回 None，语义一致。
                self.library.remove_mask(self.scene.scene_id, feature.name)
            else:
                self.library.save_mask(self.scene.scene_id, feature.name, mask)

        self.library.save_scene(self.scene)
        self.library.save_base_image(self.scene.scene_id, self._display_frame)
        self._cleanup_stale_assets()
        self._modified = False
        # 清脏后必须刷按钮：否则点完「应用」，保存/应用仍是亮的、切换场景时还会
        # 弹「未保存的改动将丢失」——用户会以为刚才那次没存进去。
        self._refresh_save_button()
        self.saved_scene = self.scene
        return True

    def _cleanup_stale_assets(self):
        """删掉不再对应任何特征的模板与掩码文件。

        两类文件都要在 expected 里：只列模板会把掩码当残留删掉，而掩码缺失不会
        报错——匹配照常进行，只是退化为整矩形，精度悄悄下降。
        """
        folder = os.path.join(self.library.scene_dir(self.scene.scene_id), "templates")
        if not os.path.isdir(folder):
            return
        expected = set()
        for feature in self.scene.features:
            if feature.kind != model.KIND_TEMPLATE:
                continue
            expected.add(os.path.basename(feature.template_file))
            expected.add(os.path.basename(feature.mask_file))
        for name in os.listdir(folder):
            if name.endswith(".png") and name not in expected:
                os.remove(os.path.join(folder, name))


# --- 界面构件 --------------------------------------------------------------


def _build_side_list(title):
    """造右侧信息栏的一个分区（标题 + 计数 + 列表）。

    布局对齐标注编辑器：标题行是「分区名 + 计数」，下面是 `QListWidget`。计数与标题
    同一行靠左，没有元素时计数留空而不是显示 0——「0」会让标题看起来像在报错。

    Args：
        title：分区标题（已翻译）。

    Returns：
        `(list_widget, header_widget)` 二元组。`header_widget` 内部持有计数标签，
        刷新时由调用方经 :func:`_set_side_count` 更新。
    """
    from ok.ui.qt.kit import Label

    header = QWidget()
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.setSpacing(6)
    header_layout.addWidget(Label(title, "subtitle"))
    count_label = Label("", "caption")
    header_layout.addWidget(count_label)
    header_layout.addStretch(1)
    header.setProperty("sideCountLabel", count_label)

    list_widget = QListWidget()
    # 复用标注编辑器的列表样式（透明底、圆角 hover/选中），两处观感一致。
    list_widget.setObjectName("featureList")
    # 必须保留 wordWrap（默认即为 True）：列表项靠换行符分成「名称 / 摘要」两行，
    # 关掉换行会把换行符当空白折叠，第二行整行消失且不报错——只剩带省略号的名称。
    list_widget.setWordWrap(True)
    # 项高随内容变化（两行比一行高），不能开启统一尺寸。
    list_widget.setUniformItemSizes(False)
    list_widget.setTextElideMode(Qt.TextElideMode.ElideRight)
    return list_widget, header


def _set_side_count(header, count):
    """更新分区标题上的计数。

    没有元素时留空而不是显示 0——「0」会让标题看起来像在报错。

    Args：
        header：:func:`_build_side_list` 返回的标题控件。
        count：元素个数。
    """
    label = header.property("sideCountLabel")
    if label is not None:
        label.setText(str(count) if count else "")


def _item_text_box(list_widget):
    """向样式查询列表项内「文字可用宽度」与「上下内边距之和」。

    ## 为什么不能写常数

    内边距来自主题 QSS（`QListWidget#featureList::item { padding: 6px 8px;
    margin-bottom: 2px; }`），实际占位是**上下各 6px + 下边距 2px = 14px**，
    而文本可用宽度是「视口宽 − 左右各 8px」。早期实现把这两项当成常数
    `-20` 与 `+12` 写死，结果每项**少算 2px**：文字需要 51px 才能排三行
    （17px/行），加上真实的 14px 内边距要 65px，而代码给出 63px——Qt 只能
    排下两行半，于是**连第一行的名称都被省略号截掉**，看起来像「栏宽不够」。

    主题属外部依赖，padding 随时可能改；只有向样式要几何才能跟着变。

    Args：
        list_widget：列表控件。

    Returns：
        `(text_width, vertical_padding)`，两值均不小于 0。
    """
    # 高度取一个极大值：`subElementRect` 返回的文本区高度 = 极大值扣除上下
    # 内边距，相减即得 padding 之和。宽度则直接用返回的文本区宽度。
    probe_height = 100000
    option = QStyleOptionViewItem()
    option.initFrom(list_widget)
    option.rect = QRect(0, 0, max(1, list_widget.viewport().width()), probe_height)
    text_rect = list_widget.style().subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, list_widget)
    return max(1, text_rect.width()), max(0, probe_height - text_rect.height())


def _fit_item_heights(list_widget):
    """按列表**当前视口宽度**重算每个项的换行高度并设 sizeHint。

    ## 为什么必须显式给宽度

    `QListWidgetItem.setSizeHint(QSize(0, h))` 的宽度传 0 时，delegate 会认为
    「没有可用宽度」，于是**不换行、直接按单行省略**——即使 `setWordWrap(True)`
    也一样。表现是长摘要被截成 `… BG 48,48,48…`，而项高却按多行算好了，下方
    留一片空白。这个组合（高度够、文字被省略）最容易误导：看着像「宽度不够」，
    实际是「没告诉它有多宽」。

    宽度必须传视口宽度，delegate 才会按真实可用宽度折行。

    ## 高度必须含真实内边距

    文字所需高度加上 :func:`_item_text_box` 查到的上下内边距。少算内边距会
    让最后一行排不下，Qt 的降级方式是**整体压成两行并加省略号**，不是裁掉
    末尾——所以「差 2px」的表现是「内容看不全」。

    ## 为什么不用 `visualItemRect` 实测

    早期实现先清 sizeHint 再读 `visualItemRect` 求自然高度。但列表项高度与宽度
    绑定：清空后读到的仍是「按旧宽度折行」的结果，视口宽度变化（窗口拉伸、
    侧栏宽度变化）后不会重算。改为直接用字体度量算：
    `boundingRect(0, 0, 文字宽度, 极大高度, TextWordWrap, 文本)`。

    Args：
        list_widget：列表控件。
    """
    width = max(1, list_widget.viewport().width())
    text_width, padding = _item_text_box(list_widget)
    metrics = list_widget.fontMetrics()
    # 两行是保底下限：`boundingRect` 在某些字体下会比实际行距少算一两像素，
    # 而少算的后果是「最后一行排不下 → delegate 压成两行加省略号」，不是简单裁掉。
    floor = _two_line_height(list_widget)
    for row in range(list_widget.count()):
        item = list_widget.item(row)
        needed = metrics.boundingRect(0, 0, text_width, 10000, int(Qt.TextFlag.TextWordWrap), item.text()).height()
        item.setSizeHint(QSize(width, max(needed, floor) + padding))


def _two_line_height(list_widget):
    """返回「两行文字」所需的**纯文字**高度，不含项内边距。

    用 `lineSpacing()` 而非 `height()`：把行间距算进去才是两行文本实际占的高度，
    否则中文字体下第二行会被压掉一两个像素。

    Args：
        list_widget：列表控件，用于取字体度量。

    Returns：
        像素高度。余量取 8：无主题下两行只需 `lineSpacing() * 2`（实测 26px 够用，
        13px/行），留 8px 冗余吸收不同字体的行高差异。

        余量不能给太大：无主题时它就是终值（内边距为 0），给 20px 会让每项多出
        一截空白，列表看起来松垮。有主题时内边距由 :func:`_item_text_box` 单独
        查出并叠加，不在这里估。
    """
    return list_widget.fontMetrics().lineSpacing() * 2 + 8


def _target_options(library, exclude_scene_id):
    """造「目标场景」下拉选项。

    Args：
        library：`store.SceneLibrary` 实例。
        exclude_scene_id：要排除的场景 id（自己），自环在寻路里无意义。

    Returns：
        `[(显示文本, 值)]` 列表，首项为「不记录边」。
    """
    options = [(tr("(No edge)"), "")]
    scenes, _failures = library.load_library()
    for scene in scenes:
        if scene.scene_id == exclude_scene_id:
            continue
        options.append((f"{scene.name}（{scene.scene_id}）", scene.scene_id))
    return options


def _has_texture(image, min_std=1.0):
    """判断裁剪出的模板是否有足够纹理。

    与识别层的 `recognize.template_has_texture` 同阈值：两处口径必须一致，否则
    会出现「界面说能用、识别时恒不命中」。

    Args：
        image：裁剪出的 BGR 或灰度数组。
        min_std：标准差下限。

    Returns：
        有纹理时为 True；空图或非数组时返回 False。
    """
    if image is None or getattr(image, "size", 0) == 0:
        return False
    import numpy as np

    return float(np.std(image)) >= min_std


def _index_of(items, name):
    """返回元素在列表中的下标。

    Args：
        items：特征或区域列表。
        name：元素名。

    Returns：
        下标；不存在时返回 -1（调用方按越界处理，不做弹窗）。
    """
    for index, item in enumerate(items):
        if item.name == name:
            return index
    return -1


def _format_shape(feature, meta):
    """把特征的形状格式化为摘要文本。

    Args：
        feature：`model.Feature` 实例。
        meta：库元数据，用于把相对比例换算成像素。

    Returns：
        形如 `10,10→30,30` 的包围盒文本；非矩形时前缀形状名与角度，
        如 `circle 50% 10,10→30,30`、`polygon(5) 10,10→30,30 · 30°`。
    """
    shape = feature.shape
    if meta.has_resolution():
        region = shape.region(meta.base_width, meta.base_height)
    else:
        region = (0.0, 0.0, 0.0, 0.0)
    text = _format_rect(region)
    if shape.kind == shapes.SHAPE_RECT:
        return f"{text} · {shape.angle:g}°" if shape.angle else text
    if shape.kind == shapes.SHAPE_CIRCLE:
        return f"circle r{round(shape.radius * 100)}% {text}"
    if shape.kind == shapes.SHAPE_SECTOR:
        return f"sector {shape.angle:g}°+{shape.sweep:g}° {text}"
    return f"polygon({len(shape.points)}) {text}"


def _format_rect(rect):
    """把相对矩形格式化为百分比短文本。

    只写数字、不写百分号：表头已写明「范围（%）」。带百分号时一行要占 ~160px，
    右侧五列就压不进 1200 宽的窗口，表格出现横向滚动条。

    Args：
        rect：四元组，0–1 相对比例。

    Returns：
        形如 `10,10→30,30` 的字符串。
    """
    return f"{round(rect[0] * 100)},{round(rect[1] * 100)}→{round(rect[2] * 100)},{round(rect[3] * 100)}"


def _ratio_to_pixels(rect, width, height):
    """相对比例 → 帧像素。

    Args：
        rect：相对比例四元组。
        width：帧宽。
        height：帧高。

    Returns：
        像素矩形元组；换算失败时返回 None。
    """
    try:
        return runtime.rect_pixels(rect, width, height)
    except ValueError:
        return None


def _pixels_to_ratio(x1, y1, x2, y2, width, height):
    """帧像素 → 相对比例。

    Args：
        x1：左上 x。
        y1：左上 y。
        x2：右下 x。
        y2：右下 y。
        width：帧宽。
        height：帧高。

    Returns：
        相对比例四元组。

    Raises：
        ValueError：分辨率非法或矩形退化。
    """
    from extensions.capabilities.scene_manager import normalize

    try:
        return normalize.pixels_to_rect(x1, y1, x2, y2, width, height)
    except normalize.NormalizeError as error:
        raise ValueError(str(error)) from error


# 探索动作的显示名。英文源串，`msgid` 必须逐字符一致。
EXPLORE_ACTION_LABELS = (
    (model.ACTION_CLICK, "Single Click"),
    (model.ACTION_DOUBLE_CLICK, "Double Click"),
    (model.ACTION_LONG_PRESS, "Long Press"),
    (model.ACTION_SWIPE, "Swipe"),
    (model.ACTION_RIGHT_CLICK, "Right Click"),
)

# 滑动方向的显示名。
SWIPE_DIRECTION_LABELS = (
    (model.SWIPE_UP, "Up"),
    (model.SWIPE_DOWN, "Down"),
    (model.SWIPE_LEFT, "Left"),
    (model.SWIPE_RIGHT, "Right"),
)

# 动作参数的显示名与说明。参数名 → (标签, 说明)。
#
# **参数字段固定平铺，不按所选动作显隐**：这个对话框此前试过「按条件显隐行」，
# 结果切一次动作整个布局就上下跳一次，用户正在点的按钮会跑位（已回滚）。因此这里
# 一律显示全部参数，标签里注明适用于哪个动作，不适用时对应参数会在保存时被丢弃。
ACTION_PARAM_LABELS = {
    "interval": ("Double Click Interval (s)", "两击间隔"),
    "duration": ("Long Press Duration (s)", "按住时长"),
    "distance": ("Swipe Distance (0-1)", "滑动距离，按画面短边比例"),
    "direction": ("Swipe Direction", "滑动方向"),
}


def _explore_action_field(action):
    """生成「探索动作」这一个下拉字段。

    Args：
        action：当前动作 key。

    Returns：
        单元素字段声明列表，可直接展开进 `PropertyDialog` 的 `fields`。

    Note：
        **参数不在这个列表里**——参数随动作变化，由 `_explore_param_fields` 通过
        对话框的动态区渲染。早先把参数一起静态生成，导致「新建时动作是单击、单击
        没有参数，于是切到长按也看不到按住时长输入框」（用户实测报的缺陷）。
    """
    return [
        (
            "explore_action",
            tr("Exploration Action"),
            action,
            [(tr(label), key) for key, label in EXPLORE_ACTION_LABELS],
        )
    ]


def _feature_kind_fields(kind, text=""):
    """返回某个识别方式下需要显示的特征字段。

    Args：
        kind：识别方式，取 `model.FEATURE_KINDS` 之一。
        text：OCR 期望文本的当前值。

    Returns：
        字段声明列表，可为空。

    Note：
        模板匹配不显示「期望文本」——判定路径 `match_template(...)` 根本不读
        `feature.text`，保存时也直接写 `text if kind == KIND_OCR else ""`。摆一个
        填了不生效的输入框，用户会反复试而结果永远不变。

        目前两种方式都只需要各自的一个字段（模板匹配无、OCR 需文本），但列表形态
        保留：将来模板匹配若加「是否允许旋转」这类参数，往这里添即可。
    """
    if kind == model.KIND_OCR:
        return [("text", tr("Expected text (OCR)"), text, None)]
    return []


def _explore_param_fields(action):
    """返回某个动作要显示的参数控件声明（供对话框的动态区调用）。

    Args：
        action：动作 key。

    Returns：
        字段声明列表，可为空（单击、右键没有参数）；元素为
        `(key, label, 默认值, options)`。

    Note：
        这里只给**默认值**。用户已填过的值由对话框自己记忆并优先使用——切到滑动
        再切回长按时，之前填的 2.5 秒不该丢。
    """
    numeric_spec, enum_spec = model.action_param_spec(action)
    fields = []
    for key, (default, _low, _high) in numeric_spec.items():
        label = ACTION_PARAM_LABELS.get(key, (key, ""))[0]
        fields.append((f"param_{key}", tr(label), f"{default:g}", None))
    for key, (default, _allowed) in enum_spec.items():
        label = ACTION_PARAM_LABELS.get(key, (key, ""))[0]
        fields.append(
            (
                f"param_{key}",
                tr(label),
                default,
                [(tr(text), value) for value, text in SWIPE_DIRECTION_LABELS],
            )
        )
    return fields


def _collect_action_params(action, values):
    """从对话框返回值里取出该动作的参数。

    Args：
        action：用户选择的动作 key。
        values：`PropertyDialog.values()` 的结果。

    Returns：
        `{参数名: 值}` 字典，已由 `model.normalize_action_params` 校验与补默认。

    Raises：
        ValueError：参数不是数值，或超出模型声明范围。

    Note：
        值传原始字符串给模型，由它统一做数值转换与范围校验——界面这里再校验一遍
        会形成两套判据，改一处漏一处就是「对话框放行、模型报错」或反过来。
    """
    numeric_spec, enum_spec = model.action_param_spec(action)
    raw = {}
    for key in numeric_spec:
        raw[key] = values.get(f"param_{key}")
    for key in enum_spec:
        raw[key] = values.get(f"param_{key}")
    try:
        return model.normalize_action_params(action, raw)
    except model.SceneModelError as error:
        raise ValueError(str(error)) from error


def _name_prefix(scene, kind, typed_name=None):
    """拼带场景前缀的默认名：`<场景名>-特征` / `<场景名>-区域`。

    Args：
        scene：`model.Scene` 实例。
        kind：`特征` 或 `区域`。
        typed_name：场景名输入框里的当前文本（可为 None）。

    Returns：
        前缀字符串。

    Note：
        是否启用由编辑器顶部的「名称带场景前缀」开关决定，默认关闭。开启后
        多个场景之间切换时，`主页-特征1` 能直接看出归属。

        `typed_name` 优先于 `scene.name`：用户常常先改名字再画框，而改名要保存才落盘，
        此时取 `scene.name` 会拿到旧名，生成 `旧名字-特征1` 与界面显示的当前名不一致。
        输入框改回空时回落到已保存的名字，不会生成 `-特征1` 这种无主前缀。
    """
    name = (typed_name or "").strip() or (scene.name or scene.scene_id or "").strip()
    if not name:
        return kind
    return f"{name}-{kind}"


def _next_name(existing, prefix):
    """生成场景内唯一名称。

    Args：
        existing：已有名称。
        prefix：前缀（可为 `<场景名>-特征` 这类含连字符的组合）。

    Returns：
        形如 `主页-特征1` 的名称。编号从 1 起，跳过已占用的编号。

    Note：
        编号**紧贴前缀、不加分隔符**（`主页-特征1` 而非 `主页-特征-1`）：场景名本身
        可能含 `-`，再加一个会让 `特征-1` 与 `特征1` 两种形态混在库里，用户分不清
        哪个是自动生成的。
    """
    used = set(existing)
    index = 1
    while True:
        candidate = f"{prefix}{index}"
        if candidate not in used:
            return candidate
        index += 1
