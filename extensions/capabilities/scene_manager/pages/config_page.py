# -*- coding: utf-8 -*-
"""配置子页签：卡片网格 + 四个动作。

布局对齐上游「模板」页签（`ok/ui/qt/tasks/TemplateTab.py`）：

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 场景  3    [🔍 按场景名搜索...]           [删除] [编辑] [保存] [截图]      │
├──────────────────────────────────────────────────────────────────────────┤
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                        │
│  │   缩略图    │  │            │  │            │                        │
│  ├────────────┤  ├────────────┤  ├────────────┤                        │
│  │ 主页界面    │  │ 设置页      │  │ 背包        │                        │
│  └────────────┘  └────────────┘  └────────────┘                        │
└──────────────────────────────────────────────────────────────────────────┘
```

六条设计判断：

1. **卡片网格而非下拉框 + 画布**：原设计里场景只能靠下拉框逐个切换才知道长什么样，
   而画布占满整页只用来「看一眼」。卡片网格一屏能看到所有场景。
2. **卡片只显示缩略图与场景名**：不显示特征数/区域数/边数，也不在缩略图上叠框。
   卡片是「找到那个场景」的入口，不是信息面板；详细信息在编辑弹窗里看。
3. **卡片继承上游 `ImageCard`**（见 `scene_card.SceneImageCard`）：样式走同一套 QSS
   选择器，外观与模板页签不会漂移。
4. **新建不弹窗**：点「截图」在后台取帧，完成后自动分配 id 与名称（`场景 N`）并插
   一张卡片；导入图片同理。用户看到卡片后才决定是否双击进编辑——先抓后配更符合实际
   操作顺序（常常一次抓好几张再逐个配）。
5. **删除只作用于编辑区**：产物区等下次「保存」同步。保留「保存」作为唯一导出动作，
   不做双向同步——两个方向都能改产物区时，「产物区的内容是哪来的」就没有唯一答案。
6. **按钮随选中态显隐**：`删除`/`编辑` 只在选中卡片后出现，与模板页签的
   `Delete`/`Markup` 一致。没有选中时点它们没有意义。

**编辑仍在弹窗里完成**（见 `scene_editor.SceneEditorDialog`）：场景编辑要同时看画面
与多个框，页签里分割后画面只剩几百像素高，4K 图上连一个按钮都看不清。
"""

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from extensions.capabilities.scene_manager.pages.scene_card import build_card
from extensions.capabilities.scene_manager.pages.scene_editor import SceneEditorDialog
from extensions.capabilities.scene_manager.translate import tr

# 页面边距与控件间距，与模板页签的 PAGE_MARGINS / PAGE_SPACING 同量级。
_PAGE_MARGINS = (28, 18, 28, 20)
_PAGE_SPACING = 16

# 搜索框固定宽度，与模板页签一致。
_SEARCH_WIDTH = 300


def _flow_widget_class():
    """延迟导入上游 `FlowWidget`（流式换行布局）。

    Returns：
        `FlowWidget` 类。

    Raises：
        RuntimeError：上游不再提供该类——卡片排列依赖它，符号消失时必须立刻报错。
    """
    try:
        from ok.ui.qt.tasks.TemplateTab import FlowWidget
    except ImportError as error:
        raise RuntimeError(
            "场景配置页依赖上游 FlowWidget（ok.ui.qt.tasks.TemplateTab），"
            "请检查 ok-script 升级后更新 pages/config_page.py"
        ) from error
    return FlowWidget


class ConfigPage(QWidget):
    """场景配置子页签（卡片网格 + 四个动作）。"""

    def __init__(self, library, on_scene_changed=None, capture_frame=None, on_export=None, parent=None):
        """构造配置页。

        Args：
            library：`store.SceneLibrary` 实例（编辑区）。
            on_scene_changed：场景变更回调（编辑/删除/新建后调用），供主页签刷新其它子页签。
            capture_frame：无参取帧函数；None 时「截图」不可用。
            on_export：无参导出函数，把编辑区同步到产物区；None 时「保存」不可用。
            parent：父控件。
        """
        super().__init__(parent)
        self.library = library
        self._on_scene_changed = on_scene_changed
        self._capture_frame = capture_frame
        self._on_export = on_export
        # 当前选中的场景 id（不是 Scene 实例）：卡片刷新后实例会重建，用 id 更稳。
        self.selected_scene_id = ""
        self._cards = {}
        self._items = []
        self._capturing = False
        self._build_ui()

    # --- 界面 ---------------------------------------------------------------

    def _build_ui(self):
        """搭建界面。"""
        from ok.ui.qt.kit import Button, Label

        root = QVBoxLayout(self)
        root.setContentsMargins(*_PAGE_MARGINS)
        root.setSpacing(_PAGE_SPACING)

        # 标题行：标题 + 计数 / 搜索框 / 四个动作
        self.count_label = Label("", "caption")
        title = QHBoxLayout()
        title.setSpacing(10)
        title.addWidget(Label(tr("Scenes"), "display"))
        title.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignBottom)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Search by scene name..."))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setFixedWidth(_SEARCH_WIDTH)
        self.search_box.textChanged.connect(self._apply_filter)

        self.delete_button = Button(tr("Delete"), "trash", "subtle")
        self.delete_button.clicked.connect(self._on_delete_scene)
        self.delete_button.setVisible(False)

        self.edit_button = Button(tr("Edit"), "pen")
        self.edit_button.clicked.connect(self._on_edit_scene)
        self.edit_button.setVisible(False)

        self.save_button = Button(tr("Save"), "export")
        self.save_button.setToolTip(tr("Sync the edit library to assets/scenes"))
        self.save_button.clicked.connect(self._on_export_clicked)
        self.save_button.setEnabled(self._on_export is not None)

        # 「导入图片」与「截图」并列：两者都是新建场景的入口，区别只是画面从哪来
        # （设备实时抓取 / 本机已有图片）。与截图一样，导入后直接建卡片、不弹窗。
        self.import_button = Button(tr("Import Image"), "image")
        self.import_button.clicked.connect(self._on_import)

        self.capture_button = Button(tr("Screenshot"), "camera", "primary")
        self.capture_button.clicked.connect(self._on_capture)
        self.capture_button.setEnabled(self._capture_frame is not None)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addLayout(title)
        toolbar.addStretch(1)
        toolbar.addWidget(self.search_box)
        toolbar.addSpacing(8)
        toolbar.addWidget(self.delete_button)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.save_button)
        toolbar.addWidget(self.import_button)
        toolbar.addWidget(self.capture_button)
        root.addLayout(toolbar)

        # 卡片网格
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.flow_widget = _flow_widget_class()()
        self.flow_widget.setObjectName("scrollContents")
        self.scroll_area.setWidget(self.flow_widget)
        self._enable_touch_scrolling(self.scroll_area)
        root.addWidget(self.scroll_area, 1)

        # 空态
        self.empty_widget = QWidget()
        empty_layout = QVBoxLayout(self.empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(8)
        self.empty_label = Label(tr("No scenes yet"), "subtitle", self.empty_widget)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_label)
        # 提示必须换行：`QLabel` 的最小宽度等于整段文本宽度，不换行时这一句
        # 会把本页的最小宽度撑到几百像素，而 `QStackedWidget` 取所有子页的
        # 最小值——配置页与其它子页都会被这行提示推得比窗口还宽。
        self.empty_hint_label = Label(
            tr("Take a screenshot of the game, then mark up its features and interaction areas."),
            "secondary",
            self.empty_widget,
            word_wrap=True,
        )
        self.empty_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_hint_label)
        empty_layout.addSpacing(8)
        self.empty_capture_button = Button(tr("Take Screenshot"), "camera", "primary")
        self.empty_capture_button.clicked.connect(self._on_capture)
        empty_layout.addWidget(self.empty_capture_button, alignment=Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_widget, 1)
        self.empty_widget.setVisible(False)
        self.empty_capture_button.setEnabled(self._capture_frame is not None)

        self.status_label = Label("", "caption")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    def _enable_touch_scrolling(self, area):
        """给滚动区启用触屏拖动（上游有该工具时）。

        Args：
            area：`QScrollArea` 实例。
        """
        try:
            from ok.ui.qt.util.touch_scroll import enable_touch_scrolling
        except ImportError:
            return
        enable_touch_scrolling(area)

    # --- 数据 ---------------------------------------------------------------

    def reload_scenes(self, selected=None):
        """重建卡片网格。

        Args：
            selected：要选中的场景 id；省略时保持当前选择。场景已不存在时清空选择。
        """
        wanted = selected if selected is not None else self.selected_scene_id
        scenes, failures = self.library.load_library()
        self._items = scenes

        # 重建卡片而不是复用：场景名、缩略图、数量都可能变，逐个同步的代码量远大于重建，
        # 而卡片数量在几十的量级，重建开销可忽略。
        self.flow_widget.clear_items()
        self._cards = {}
        cards = []
        for scene in scenes:
            card = build_card(self.library, scene)
            card.clicked.connect(self._on_card_clicked)
            card.double_clicked.connect(self._on_card_double_clicked)
            self._cards[scene.scene_id] = card
            cards.append(card)
        self.flow_widget.set_items(cards)

        if failures:
            self.status_label.setText(
                tr("Some scenes could not be read: {list}").format(
                    list="；".join(f"{sid}（{msg}）" for sid, msg in failures)
                )
            )
        else:
            self.status_label.setText("")

        self.count_label.setText(str(len(scenes)) if scenes else "")
        has_scenes = bool(scenes)
        self.scroll_area.setVisible(has_scenes)
        self.empty_widget.setVisible(not has_scenes)

        if wanted and wanted in self._cards:
            self._select(wanted)
        elif wanted:
            # 选中的场景已不存在（被删或改名），清掉选择，否则按钮对着空气可用。
            self._select("")
        else:
            self._update_buttons()

        self._apply_filter()

    def _select(self, scene_id):
        """设置选中项并刷新卡片高亮与按钮可见性。

        Args：
            scene_id：要选中的场景 id；空串表示取消全选。
        """
        self.selected_scene_id = scene_id or ""
        for card_id, card in self._cards.items():
            card.set_selected(card_id == self.selected_scene_id)
        self._update_buttons()

    def _update_buttons(self):
        """按选中态与忙碌状态刷新按钮。"""
        has_selection = bool(self.selected_scene_id)
        self.delete_button.setVisible(has_selection)
        self.edit_button.setVisible(has_selection)
        busy = self._capturing
        self.delete_button.setEnabled(has_selection and not busy)
        self.edit_button.setEnabled(has_selection and not busy)
        self.save_button.setEnabled(self._on_export is not None and not busy)
        self.capture_button.setEnabled(self._capture_frame is not None and not busy)
        self.empty_capture_button.setEnabled(self._capture_frame is not None and not busy)
        self.import_button.setEnabled(not busy)

    def _apply_filter(self):
        """按搜索框内容过滤卡片。"""
        query = self.search_box.text().strip().lower()
        if not query:
            visible = [card for card in (self._cards.get(s.scene_id) for s in self._items) if card is not None]
        else:
            visible = [
                self._cards[scene.scene_id]
                for scene in self._items
                if query in (scene.name or "").lower() or query in scene.scene_id.lower()
            ]
        self.flow_widget.set_items(visible)
        self.scroll_area.setVisible(bool(visible))
        self.empty_widget.setVisible(not visible and not self._items)
        if not visible and self._items:
            self.status_label.setText(tr("No scene matches the search."))

    # --- 卡片交互 -----------------------------------------------------------

    def _on_card_clicked(self, scene_id):
        """单击卡片：切换选中。

        Args：
            scene_id：被点击的场景 id。
        """
        self._select("" if self.selected_scene_id == scene_id else scene_id)

    def _on_card_double_clicked(self, scene_id):
        """双击卡片：选中并打开编辑弹窗。

        Args：
            scene_id：被双击的场景 id。
        """
        self._select(scene_id)
        self._edit_scene(scene_id)

    # --- 动作 ---------------------------------------------------------------

    def _on_import(self):
        """从本机图片新建场景。

        与「截图」同一条路径：落盘后直接建卡片，不弹编辑窗。画面来源不同，但
        「先有画面、再配置」的顺序一致。

        导入的图**必须遮罩后再落盘**：`base.png` 会入库、随包分发，而未遮罩的
        整帧可能带游戏 UID。遮罩在 `_create_scene_from_frame` 里做，这里只负责取帧。
        """
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QFileDialog

        from extensions.capabilities.scene_manager.pages.image_convert import qimage_to_bgr

        path, _filter = QFileDialog.getOpenFileName(self, tr("Import Image"), "", "图片 (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            self._fail(tr("Import failed: cannot read {name}").format(name=os.path.basename(path)))
            return
        frame = qimage_to_bgr(image)
        if frame is None:
            self._fail(tr("Import failed: unsupported image format"))
            return
        self._create_scene_from_frame(frame)

    def _on_capture(self):
        """后台取帧并新建场景。

        与模板页签的「截图」一致：取帧是 IO，放后台；完成后自动建卡片，**不弹窗**——
        弹窗是「我要配这个场景」的明确动作，与「先把画面抓下来」不是同一件事。

        忙碌时禁用按钮：连点会产生多个指向同一帧的空场景，而用户看不出哪个是哪个。
        """
        if self._capture_frame is None or self._capturing:
            return
        self._capturing = True
        self._update_buttons()
        self.status_label.setText(tr("Capturing..."))
        # 取帧放到事件循环的下一轮：按钮的禁用状态需要先画出来，否则界面在取帧期间
        # 看起来是卡住的（实测取帧约 60–120ms，肉眼可感）。
        QTimer.singleShot(0, self._do_capture)

    def _do_capture(self):
        """实际取帧并建场景（由 :meth:`_on_capture` 调度）。"""
        try:
            frame = self._capture_frame()
        except Exception as error:  # noqa: BLE001 — 取帧失败提示即可，不崩页签
            self._capturing = False
            self._update_buttons()
            self._fail(tr("Screenshot failed: {e}").format(e=error))
            return

        self._capturing = False
        self._update_buttons()
        if frame is None:
            self._fail(
                tr(
                    "Failed to capture: no frame. Check that a device is connected and that "
                    "src/config.py matches the game window."
                )
            )
            return
        self._create_scene_from_frame(frame)

    def _create_scene_from_frame(self, frame):
        """用一帧画面新建场景并落盘到编辑区。

        **分辨率三道判断的第二道（截图准入）在这里落地**：库为空时用当前帧立基准，
        已有场景时要求与基准一致，不一致就弹窗拒绝。判定逻辑在 `resolution.py`，
        本方法只负责「问用户」与「落盘」。

        Args：
            frame：BGR 原始帧。

        Returns:
            是否真的建了场景。导入与截图两条路径共用本方法，调用方据此决定提示。
        """
        from extensions.capabilities.scene_manager import resolution, runtime

        height, width = frame.shape[:2]
        meta = self.library.meta
        warning = None
        if meta.has_resolution():
            problem = resolution.frame_problem((width, height), meta.base_width, meta.base_height)
            if problem is not None:
                self._show_resolution_blocked(problem, can_rebase=self._has_no_scenes())
                return False
            warning = resolution.frame_warning((width, height), meta.base_width, meta.base_height)

        try:
            self.library.ensure_layout()
            if not self.library.meta.has_resolution():
                self.library.set_base_resolution(width, height)
            # 名称交给 `new_scene` 按 id 序号生成（`scene-0005` → `场景 5`）。
            # 这里**不传名字**：页面自己算一套序号会与 id 计数器分叉，实测
            # `scene-0004` 被叫成「场景 1」就是这么来的。
            scene = self.library.new_scene()
            self.library.save_scene(scene)
            # base.png 必须脱敏：它会入库、随包分发。
            self.library.save_base_image(scene.scene_id, runtime.apply_global_mask(frame))
        except Exception as error:  # noqa: BLE001 — 落盘失败提示即可
            self._fail(tr("Failed to create scene: {e}").format(e=error))
            return False
        self.reload_scenes(selected=scene.scene_id)
        self._notify_changed()
        message = tr("Created 「{name}」. Double-click the card to configure it.").format(name=scene.name)
        # 软提醒必须与成功提示**一起**显示：先写提醒、随后被「已创建」覆盖的话，
        # 用户永远看不到「这次是缩放过的」——而那正是解释「为什么识别得分略低」的
        # 唯一线索。
        self._done(message, warning)
        return True

    def _has_no_scenes(self):
        """返回编辑区当前是否有任何场景（含未读出的）。

        Returns：
            库目录里没有任何场景目录时为 True。

        Note：
            按目录判断而不是按 `self._items`：后者是「读出来的场景」，一个损坏的
            `scene.json` 会让它为空，但库里其实有数据——此时不该把基准直接改掉。
        """
        try:
            return not self.library.list_scene_ids() and not self.library.list_draft_ids()
        except OSError:
            return False

    def _show_resolution_blocked(self, problem, can_rebase=False):
        """弹窗告知分辨率不一致，并禁止本次操作。

        Args：
            problem：可读原因，来自 `resolution.frame_problem`。
            can_rebase：库里是否真的没有任何场景。为 True 时额外提供「以当前画面
                重建基准」的选项——那种情况下基准本身没有参照价值，改它不会破坏
                任何已存数据。
        """
        from ok.ui.qt.kit import Dialog

        dialog = Dialog(
            self,
            tr("Resolution Mismatch"),
            problem,
            ok_text=tr("Got It") if not can_rebase else tr("Use Current Frame"),
            cancel_text=tr("Cancel"),
        )
        try:
            accepted = dialog.exec()
        finally:
            dialog.deleteLater()
        if accepted and can_rebase:
            self._rebase_resolution()
        else:
            self.status_label.setText(tr("Screenshot blocked: resolution mismatch."))

    def _rebase_resolution(self):
        """在库里没有任何场景时，用当前画面尺寸重建基准。

        只在确认「库是空的」之后调用：已存场景的相对坐标都按旧基准写入，改基准
        会让它们的框全部错位，而错位不报错、只是识别不到。
        """
        from extensions.capabilities.scene_manager import resolution

        if not self._has_no_scenes():
            self._fail(tr("Cannot change the base resolution: the library is not empty."))
            return
        try:
            frame = self._capture_frame() if self._capture_frame else None
        except Exception as error:  # noqa: BLE001 — 取帧失败提示即可
            self._fail(tr("Screenshot failed: {e}").format(e=error))
            return
        if frame is None:
            self._fail(tr("Cannot rebase: no frame available."))
            return
        height, width = frame.shape[:2]
        self.library.set_base_resolution(width, height)
        self._done(tr("Base resolution set to {size}.").format(size=resolution.describe_size(width, height)))
        self._notify_changed()

    def _on_edit_scene(self):
        """编辑选中的场景。"""
        if self.selected_scene_id:
            self._edit_scene(self.selected_scene_id)

    def _edit_scene(self, scene_id):
        """打开弹窗编辑指定场景。

        Args：
            scene_id：场景 id。
        """
        if not scene_id:
            return
        dialog = SceneEditorDialog(self.library, scene_id=scene_id, parent=self)
        if dialog.exec():
            saved = dialog.saved_scene
            self.reload_scenes(selected=saved.scene_id if saved else scene_id)
            self._notify_changed()
        dialog.deleteLater()

    def _on_delete_scene(self):
        """删除选中的场景（只删编辑区）。"""
        if not self.selected_scene_id:
            return
        scene = self.library.load_scene(self.selected_scene_id)
        name = scene.name if scene else self.selected_scene_id
        from ok.ui.qt.kit import confirm

        accepted = bool(
            confirm(
                self,
                tr("Delete Scene"),
                tr("Delete 「{name}」 with its preview and templates? This cannot be undone.").format(name=name),
                ok_text=tr("Delete"),
                danger=True,
            )
        )
        if not accepted:
            return
        self.library.delete_scene(self.selected_scene_id)
        # 产物区不动：等「保存」同步。这样「保存」是唯一导出动作，产物区的内容只有
        # 一个来源。
        self._select("")
        self.reload_scenes(selected="")
        self._notify_changed()
        self._done(tr("Deleted 「{name}」 from the edit library.").format(name=name))

    def _on_export_clicked(self):
        """把编辑区同步到产物区。"""
        if self._on_export is None:
            return
        self._on_export()

    def _notify_changed(self):
        """通知外部场景已变更。"""
        if self._on_scene_changed is not None:
            self._on_scene_changed()

    # --- 操作结果提示 -------------------------------------------------------

    def _done(self, message, detail=None):
        """报告一次**成功的操作**：弹成功浮层，同时把文案留在状态栏。

        Args：
            message：结论，如「已创建『靶场入口』」。
            detail：可选的补充说明（如「画面被缩放过」的软提醒）。写进浮层正文，
                状态栏同样带上——它解释的是「为什么之后识别得分略低」，事后也
                可能要看。

        Note:
            **状态栏也要写**。浮层几秒后消失，而配置页底部那行状态栏是常驻的；
            用户回头核对「刚才那个场景建成了吗」时只能靠它。
        """
        from extensions.capabilities.scene_manager import notify

        body = f"{message}\n{detail}" if detail else message
        self.status_label.setText(body)
        notify.notify_success(self, body)

    def _fail(self, message, title=None):
        """报告一次**失败的操作**：弹错误浮层，同时写进状态栏。

        Args：
            message：失败原因（通常含异常文本）。
            title：可选的加粗标题；省略时只有正文。

        Note:
            失败提示必须走浮层：这类消息用户**不会主动去状态栏找**——他点了一个
            按钮，期待的是「成了」或「没成」，消息若不主动推过来，表现就是
            「点了没反应」。
        """
        from extensions.capabilities.scene_manager import notify

        self.status_label.setText(f"{title}：{message}" if title else message)
        notify.notify_error(self, message, title)


# --- 辅助 ------------------------------------------------------------------
