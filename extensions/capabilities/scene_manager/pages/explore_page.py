# -*- coding: utf-8 -*-
"""探索子页签：启动自动探索，并在**已确认的路由图**上叠加候选边。

与「场景路由」的分工：

- 探索页 = 候选池：已生效路径作底图，上面叠加历史待确认（紫色虚线）与
  本次新发现（橙色加粗虚线），在这里逐条或批量应用；
- 场景路由页 = 最终结果：只显示已生效路径，承担改目标、设等待、删除等编辑。

这样路由页打开时看到的就是「当前真实能走的路」，不会被未确认的探索产物污染。
"""

import time

from PySide6.QtCore import QSize, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QMenu,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from extensions.capabilities.scene_manager import graph, model
from extensions.capabilities.scene_manager.flow_canvas import FlowCanvas
from extensions.capabilities.scene_manager.translate import tr


class ExplorePage(QWidget):
    """探索页：候选池与启动入口。"""

    def __init__(self, library=None, on_explore=None, on_changed=None, parent=None):
        """构造探索页。

        Args：
            library：编辑区库实例，用于读写参数、构建路由图与应用候选边。
            on_explore：启动探索的回调；None 时按钮禁用。
            on_changed：候选边被应用或忽略后的回调，用于刷新场景路由页。
            parent：父控件。
        """
        super().__init__(parent)
        self.library = library
        self._on_explore = on_explore
        self._on_changed = on_changed
        self._new_edge_keys = set()
        self._selected_edge = None
        self._selected_edges = set()
        self._settings_dialog = None
        self._progress_dialog = None
        # 进度快照与本地计时：任务侧只在事件发生时更新 elapsed，两次事件
        # 之间可能隔好几秒，弹窗上的计时器看起来像卡住。定时器按本地时钟
        # 补齐，保证每秒都在走。
        self._progress_snapshot = None
        self._progress_started = 0.0
        self._progress_lines = 0
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(100)
        self._progress_timer.timeout.connect(self._render_progress)
        self._build_ui()
        self.reload()

    def sizeHint(self):
        """返回页面的默认尺寸。

        Returns：
            一个较小的固定尺寸，占满外层分配的可用高度，不让内容把
            外层页签撑出上下滚动条。
        """
        return QSize(640, 480)

    def minimumSizeHint(self):
        """返回页面的最小尺寸，避免子控件把外层滚动区撑高。

        Returns：
            一个较小的固定尺寸。
        """
        return QSize(320, 240)

    def _build_ui(self):
        """搭建界面。"""
        from ok.ui.qt.kit import Button, Label

        root = QVBoxLayout(self)
        # 外层页签已有内边距，这里收紧默认边距，把高度留给画布。
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        toolbar = QHBoxLayout()
        self.explore_button = Button(tr("Start Exploration"), variant="primary", size="small")
        self.explore_button.clicked.connect(self._on_start_explore)
        self.explore_button.setEnabled(self._on_explore is not None)
        toolbar.addWidget(self.explore_button)

        self.auto_layout_button = Button(tr("Auto Layout"), variant="subtle", size="small")
        self.auto_layout_button.clicked.connect(self._on_auto_layout)
        toolbar.addWidget(self.auto_layout_button)

        self.reset_view_button = Button(tr("Reset View"), variant="subtle", size="small")
        self.reset_view_button.clicked.connect(self._on_reset_view)
        toolbar.addWidget(self.reset_view_button)

        self.settings_button = Button(tr("Params"), variant="subtle", size="small")
        self.settings_button.clicked.connect(self._open_settings)
        toolbar.addWidget(self.settings_button)

        toolbar.addStretch(1)
        root.addLayout(toolbar)

        self.canvas = FlowCanvas(self, editable=False)
        self.canvas.edge_clicked.connect(self._on_edge_clicked)
        self.canvas.selection_changed.connect(self._on_canvas_selection_changed)
        self.canvas.edge_context_menu.connect(self._on_edge_menu)
        self.canvas.canvas_context_menu.connect(self._on_canvas_menu)
        self.canvas.edge_double_clicked.connect(self._on_edge_double_clicked)
        self.canvas.positions_changed.connect(self._on_positions_changed)
        root.addWidget(self.canvas, 1)
        root.addWidget(Label(tr("Solid: active    Dashed: pending    Thick orange: new in this run"), "caption"))

        selected_row = QHBoxLayout()
        self.selected_label = Label("", "body", word_wrap=True)
        selected_row.addWidget(self.selected_label, 1)
        self.apply_all_button = Button(tr("Apply All"), variant="secondary", size="small")
        self.apply_all_button.clicked.connect(self._on_apply_all)
        selected_row.addWidget(self.apply_all_button)
        self.ignore_all_button = Button(tr("Ignore All"), variant="subtle", size="small")
        self.ignore_all_button.clicked.connect(self._on_ignore_all)
        selected_row.addWidget(self.ignore_all_button)
        root.addLayout(selected_row)

        self.status_label = Label("", "caption", word_wrap=True)
        root.addWidget(self.status_label)

    def show_progress(self, on_interrupt=None):
        """弹出探索进度窗口，阻塞主界面操作直到探索结束。

        Args：
            on_interrupt：点「中断」时的回调；None 时按钮只关闭窗口。

        Note：
            用非阻塞 `open()` 拉开遮罩：探索在后台线程跑，`exec()` 会让界面
            无法刷新进度。`Dialog` 的遮罩覆盖整个主窗口，用户点不到其他控件，
            但事件循环仍在转，进度每 100ms 更新一次。
        """
        from ok.ui.qt.kit.dialogs import Dialog

        if self._progress_dialog is not None:
            self._progress_dialog.deleteLater()
            self._progress_dialog = None
        dialog = Dialog(
            self,
            tr("Exploring"),
            tr("Starting exploration..."),
            ok_text=tr("Interrupt"),
            danger=True,
            width=520,
        )
        dialog.set_cancel_visible(False)
        # 进度文本会折成多行，`adjustSize` 只按单行给高度，后几行会被裁掉。
        # 给消息区一个基础高度，后续按实际行数再调。
        dialog.message_label.setMinimumHeight(120)
        # 断开默认的「确认即关闭」：中断按钮只发停止请求，窗口等探索真正
        # 结束后由 `hide_progress` 关闭，否则用户会以为已经停了。
        dialog.ok_button.clicked.disconnect()
        dialog.ok_button.clicked.connect(lambda: self._request_interrupt(on_interrupt))
        self._progress_dialog = dialog
        self._progress_snapshot = {
            "visited": 0,
            "new_scenes": 0,
            "edges": 0,
            "elapsed": 0.0,
            "event": "",
        }
        self._progress_started = time.monotonic()
        self._progress_lines = 0
        self._progress_timer.start()
        dialog.open()

    def update_progress(self, progress):
        """刷新进度文本。

        Args：
            progress：`SceneExploreTask.progress` 字典。
        """
        if self._progress_dialog is None:
            return
        self._progress_snapshot = dict(progress)
        self._render_progress()

    def _render_progress(self):
        """按快照与本地时钟重绘进度文本。

        Note：
            任务侧的 `elapsed` 只在事件发生时更新；没有事件时用本地计时补足，
            计时器每秒都会变，用户不会以为探索卡住。
        """
        if self._progress_dialog is None or self._progress_snapshot is None:
            return
        progress = self._progress_snapshot
        elapsed = max(
            float(progress.get("elapsed", 0.0) or 0.0),
            time.monotonic() - self._progress_started,
        )
        text = (
            f"{tr('Elapsed')} {elapsed:.1f}s　"
            f"{tr('Visited')} {progress.get('visited', 0)}　"
            f"{tr('New Scenes')} {progress.get('new_scenes', 0)}　"
            f"{tr('Edges')} {progress.get('edges', 0)}"
        )
        event = progress.get("event", "")
        if event:
            text += "\n" + self._wrap_event(event)
        self._progress_dialog.message_label.setText(text)
        lines = text.count("\n") + 1
        if lines != self._progress_lines:
            self._progress_lines = lines
            self._resize_progress_dialog(lines)

    def _resize_progress_dialog(self, lines):
        """按文本行数加高进度弹窗并重新居中。

        Args：
            lines：当前文本占用的行数。

        Note：
            `QLabel` 的自动换行不会改变 `sizeHint` 的高度，`Dialog._fit` 的
            `adjustSize` 仍按单行算，多出来的行会溢出卡片被裁掉。直接给
            消息区设最小高度，再让它重新居中。
        """
        if self._progress_dialog is None:
            return
        height = max(120, 26 * lines + 24)
        self._progress_dialog.message_label.setMinimumHeight(height)
        self._progress_dialog._fit()

    def _wrap_event(self, text, limit=26):
        """把过长的探索事件按固定字数折行。

        Args：
            text：事件文本。
            limit：每行最多字符数。

        Returns：
            折行后的文本。

        Note：
            `QLabel` 的自动换行依赖布局给宽度；弹窗是固定宽度的卡片，
            长事件会被直接裁掉。手动折行能保证每行都落在卡片内。
        """
        if len(text) <= limit:
            return text
        return "\n".join(text[index : index + limit] for index in range(0, len(text), limit))

    def hide_progress(self):
        """关闭进度窗口。"""
        self._progress_timer.stop()
        self._progress_snapshot = None
        self._progress_lines = 0
        if self._progress_dialog is None:
            return
        dialog = self._progress_dialog
        self._progress_dialog = None
        dialog.reject()
        dialog.deleteLater()

    def _request_interrupt(self, callback):
        """点「中断」：禁用按钮并发出停止请求。

        Args：
            callback：停止回调。
        """
        if self._progress_dialog is not None:
            self._progress_dialog.ok_button.setEnabled(False)
            self._progress_dialog.ok_button.setText(tr("Interrupting..."))
            self._progress_dialog.message_label.setText(tr("Interrupting, waiting for the current step to finish..."))
        if callback is not None:
            callback()

    def _open_settings(self):
        """打开探索参数弹窗。

        Note：
            参数原先常驻在画布上方，两行控件把画布压得很矮。收进弹窗后画布
            拿回整块高度；参数仍然即改即存，不引入「应用」按钮。
        """
        if self._settings_dialog is None:
            self._settings_dialog = self._build_settings_dialog()
            self._load_settings()
        self._settings_dialog.open()

    def _build_settings_dialog(self):
        """构建探索参数弹窗。

        Returns：
            `ok.ui.qt.kit.dialogs.Dialog` 实例。
        """
        from ok.ui.qt.kit import Label, Switch
        from ok.ui.qt.kit.dialogs import Dialog
        from PySide6.QtWidgets import QGridLayout

        dialog = Dialog(self, tr("Exploration Settings"), ok_text=tr("Close"), width=640)
        dialog.set_cancel_visible(False)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        def add_field(row, column, label_text, widget, tooltip=""):
            """在网格里放一组「标签 + 控件」，标签给足最小宽度不被截断。"""
            label = Label(label_text, "body")
            label.setMinimumWidth(96)
            grid.addWidget(label, row, column)
            if tooltip:
                widget.setToolTip(tooltip)
            widget.setMinimumWidth(140)
            grid.addWidget(widget, row, column + 1)

        def add_toggle(row, column, label_text, widget, tooltip=""):
            """放一组「标签 + 开关」。"""
            label = Label(label_text, "body")
            label.setMinimumWidth(96)
            grid.addWidget(label, row, column)
            if tooltip:
                widget.setToolTip(tooltip)
            grid.addWidget(widget, row, column + 1)

        # 每行两组，避免 7 列挤在一起把标签与数值裁掉。
        self.interval_input = QDoubleSpinBox()
        self.interval_input.setRange(0.05, 5.0)
        self.interval_input.setSingleStep(0.05)
        self.interval_input.setDecimals(2)
        self.interval_input.setSuffix(" s")
        add_field(
            0,
            0,
            tr("Poll Interval"),
            self.interval_input,
            tr("How often to check the screen after a click"),
        )

        self.timeout_input = QDoubleSpinBox()
        self.timeout_input.setRange(0.5, 60.0)
        self.timeout_input.setSingleStep(0.5)
        self.timeout_input.setDecimals(1)
        self.timeout_input.setSuffix(" s")
        add_field(
            0,
            2,
            tr("Settle Timeout"),
            self.timeout_input,
            tr("How long to wait for a scene change after a click"),
        )

        self.order_input = QComboBox()
        self.order_input.addItem(tr("Breadth First"), model.SEARCH_BFS)
        self.order_input.addItem(tr("Depth First"), model.SEARCH_DFS)
        add_field(
            1,
            0,
            tr("Search Order"),
            self.order_input,
            tr("Breadth expands the current level first; depth follows one chain to the end"),
        )

        # 目标恢复设置：只在异常中断时重启，正常完成/达到上限/取消都不动。
        self.auto_launch_switch = Switch(False)
        add_toggle(
            1,
            2,
            tr("Auto Launch Target"),
            self.auto_launch_switch,
            tr("Start the target automatically before exploration"),
        )

        self.restart_switch = Switch(False)
        add_toggle(
            2,
            0,
            tr("Restart On Incomplete"),
            self.restart_switch,
            tr("Restart the Windows target and retry when exploration stops abnormally"),
        )

        self.max_restarts_input = QSpinBox()
        self.max_restarts_input.setRange(0, 3)
        add_field(
            2,
            2,
            tr("Max Restarts"),
            self.max_restarts_input,
            tr("How many times the target may be restarted in one exploration"),
        )

        self.restart_wait_input = QDoubleSpinBox()
        self.restart_wait_input.setRange(1.0, 60.0)
        self.restart_wait_input.setSingleStep(1.0)
        self.restart_wait_input.setDecimals(1)
        self.restart_wait_input.setSuffix(" s")
        add_field(
            3,
            0,
            tr("Restart Wait"),
            self.restart_wait_input,
            tr("How long to wait for the target to come back after a restart"),
        )
        dialog.body.addLayout(grid)

        for widget in (
            self.interval_input,
            self.timeout_input,
            self.max_restarts_input,
            self.restart_wait_input,
        ):
            widget.valueChanged.connect(self._save_settings)
        self.order_input.currentIndexChanged.connect(self._save_settings)
        self.auto_launch_switch.toggled.connect(self._save_settings)
        self.restart_switch.toggled.connect(self._save_settings)
        return dialog

    def _on_start_explore(self):
        """触发探索。"""
        if self._on_explore is not None:
            self._on_explore()

    def _on_auto_layout(self):
        """自动重新排列节点并保存布局。"""
        if self.library is None:
            return
        try:
            self.library.clear_layout()
        except Exception as error:  # noqa: BLE001 — 失败只写状态栏，不阻断探索页
            self.set_status(f"自动布局失败：{error}")
            return
        self.canvas.reset_layout()
        self.set_status("已自动布局并保存节点位置")

    def _on_reset_view(self):
        """缩放回全部节点可见，不改变节点位置。"""
        self.canvas.reset_view()

    def _on_positions_changed(self, positions):
        """拖动节点或自动布局后保存位置。

        Args：
            positions：`{scene_id: (x, y)}` 画布坐标。
        """
        if self.library is None:
            return
        try:
            self.library.save_layout(positions)
        except Exception as error:  # noqa: BLE001 — 失败只写状态栏
            self.set_status(f"保存节点位置失败：{error}")

    def _load_settings(self):
        """从库元数据读取参数并填到控件上。"""
        if self.library is None:
            self.interval_input.setEnabled(False)
            self.timeout_input.setEnabled(False)
            self.order_input.setEnabled(False)
            return
        try:
            meta = self.library.load_meta()
        except Exception as error:  # noqa: BLE001 — 读失败不影响探索页其它功能
            self.set_status(f"读取探索参数失败：{error}")
            return
        for widget, value in (
            (self.interval_input, meta.probe_interval),
            (self.timeout_input, meta.settle_timeout),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self.order_input.blockSignals(True)
        self.order_input.setCurrentIndex(max(0, self.order_input.findData(meta.search_order)))
        self.order_input.blockSignals(False)
        for widget, value in (
            (self.auto_launch_switch, bool(meta.auto_launch)),
            (self.restart_switch, bool(meta.restart_on_incomplete)),
        ):
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)
        for widget, value in (
            (self.max_restarts_input, meta.max_restarts),
            (self.restart_wait_input, meta.restart_wait),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def _save_settings(self):
        """把参数写回库元数据。

        Note：
            写失败只写状态栏，不弹浮层：用户拖一次数字就弹一次框会打断操作。
        """
        if self.library is None:
            return
        try:
            self.library.set_probe_settings(
                probe_interval=self.interval_input.value(),
                settle_timeout=self.timeout_input.value(),
                search_order=self.order_input.currentData(),
                auto_launch=self.auto_launch_switch.isChecked(),
                restart_on_incomplete=self.restart_switch.isChecked(),
                max_restarts=self.max_restarts_input.value(),
                restart_wait=self.restart_wait_input.value(),
            )
        except Exception as error:  # noqa: BLE001 — 写失败时提示，不阻断界面
            self.set_status(f"保存探索参数失败：{error}")

    def set_status(self, text):
        """写一行状态提示。

        Args：
            text：提示文本；空串表示清空。
        """
        self.status_label.setText(text)

    # --- 候选图 -------------------------------------------------------------

    def reload(self):
        """重建候选图：已生效路径作底图，叠加全部待确认边。

        Note：
            用 `include_pending=True` 建一张完整的图：灰色实线是已经能走的
            路，紫色虚线是历史待确认，橙色加粗是本次新发现。用户看到的是
            「新边接在现有拓扑的哪里」，而不是一条孤立的记录。
        """
        if self.library is None:
            return
        try:
            scenes, _failures = self.library.load_library()
            self.library.load_routes()
            graph_instance = graph.SceneGraph.build(scenes, self.library.routes, include_pending=True)
        except Exception as error:  # noqa: BLE001 — 读失败时提示，不崩页签
            self.set_status(f"读取路由图失败：{error}")
            return
        positions = self.library.load_layout()
        if not positions and self.canvas._nodes:
            positions = self.canvas.remember_positions()
        self.canvas.set_graph(graph_instance, positions or None)
        self.canvas.set_new_edges(self._new_edge_keys)
        self.canvas.set_selected_edges(self._selected_edges, self._selected_edge)
        pending = self._pending_routes()
        self.selected_label.setText(
            f"待确认 {len(pending)} 条　本次新发现 {len(self._new_edge_keys)} 条　点画布上的边可选中"
        )
        self._refresh_actions()

    def show_explore_result(self, result):
        """把一次探索的结果叠加到候选图上，并用一次性弹窗报告摘要。

        Args：
            result：`explore.ExploreResult` 实例；None 表示收起「本次新发现」标记。

        Note：
            只有「当前不可寻路」的本次边标成橙色加粗；重复采到的已生效
            路径不再提示，避免把已确认的路当成新发现。
            摘要原先常驻在画布上方，四行文字长期占掉画布高度；改为探索结束
            弹一次，确认后不再占据界面。
        """
        if result is None:
            self._new_edge_keys = set()
            self.canvas.set_new_edges(set())
            return
        keys = {(edge[0], edge[1]) for edge in result.edges}
        fresh = set()
        for key in keys:
            route = self.library.routes.get(*key)
            if route is not None and not route.routable():
                fresh.add(key)
        self._new_edge_keys = fresh
        self.reload()
        if not self._show_result_dialog(self._result_lines(result, fresh)):
            return
        # 确认摘要后自动重排一次：新边接进来后手工布局的位置往往已经挤在
        # 一起，用户点确认就是想看结果。布局顺带写入 layout.json。
        self._on_auto_layout()
        self.set_status("已自动布局并保存节点位置")

    def _result_lines(self, result, fresh):
        """拼出探索摘要的文本行。

        Args：
            result：`explore.ExploreResult` 实例。
            fresh：本次新发现且仍待确认的边键集合。

        Returns：
            多行文本。
        """
        lines = [
            result.summary(),
            f"橙色加粗 = 本次新发现 {len(fresh)} 条；紫色虚线 = 历史待确认",
        ]
        if result.new_scenes:
            lines.append("新增草稿：" + "、".join(self._scene_name(draft_id) for draft_id in result.new_scenes))
        else:
            lines.append("没有发现新场景（草稿页不会有新数据）")
        problem = self._problem_line(result)
        if problem:
            lines.append(problem)
        return "\n".join(lines)

    def _show_result_dialog(self, message):
        """弹出探索摘要，确认后关闭。

        Args：
            message：摘要文本。

        Returns：
            确认返回 True；窗口被关闭返回 False。
        """
        from ok.ui.qt.kit.dialogs import Dialog

        dialog = Dialog(self, tr("Exploration Finished"), message, ok_text=tr("OK"))
        dialog.set_cancel_visible(False)
        try:
            return dialog.exec()
        finally:
            dialog.deleteLater()

    def _problem_line(self, result):
        """把探索过程中的问题聚合成一行。

        Args：
            result：`explore.ExploreResult` 实例。

        Returns：
            一行提示；没有问题事件时返回空串。

        Note：
            只显示第一类原因与涉及场景，避免七条一模一样的「无法回退」把
            横幅撑满。完整事件仍在日志里。
        """
        problems = [event for event in result.events if event.kind in ("warn", "skip", "error", "limit")]
        if not problems:
            return ""
        grouped = {}
        for event in problems:
            grouped.setdefault(event.detail, []).append(event.scene_id)
        detail, scene_ids = next(iter(grouped.items()))
        where = "、".join(self._scene_label(scene_id) for scene_id in scene_ids[:3])
        if len(scene_ids) > 3:
            where += f" 等 {len(scene_ids)} 个场景"
        more = f"（另有 {len(grouped) - 1} 类提示）" if len(grouped) > 1 else ""
        return f"注意：{detail}（{where}）{more}"

    # --- 候选操作 -----------------------------------------------------------

    def _scene_name(self, scene_id):
        """返回场景名称（不带 id），读不到时退回 id。

        Args：
            scene_id：场景 id 或草稿 id。

        Returns:
            显示文本。
        """
        try:
            scene = self.library.load_scene(scene_id)
        except Exception:  # noqa: BLE001 — 读不到就退回 id
            scene = None
        return scene.name if scene is not None and scene.name else scene_id

    def _scene_label(self, scene_id):
        """返回场景的显示文本，供选中行与结果摘要使用。

        Args：
            scene_id：场景 id。

        Returns:
            `名称（scene-0001）`；名称缺失或与 id 相同时只显示 id。
        """
        graph_instance = getattr(self.canvas, "_graph", None)
        scene = graph_instance.scenes.get(scene_id) if graph_instance is not None else None
        if scene is None:
            try:
                scene = self.library.load_scene(scene_id)
            except Exception:  # noqa: BLE001 — 读不到就退回 id，不阻断界面
                scene = None
        if scene is None or not scene.name or scene.name == scene_id:
            return scene_id
        return f"{scene.name}（{scene_id}）"

    def _pending_routes(self):
        """返回候选池里的全部待确认路由。"""
        return [route for route in self.library.routes if not route.routable()]

    def _refresh_actions(self):
        """按候选总数量刷新批量按钮。"""
        pending = self._pending_routes()
        self.apply_all_button.setEnabled(bool(pending))
        self.ignore_all_button.setEnabled(bool(pending))

    def _on_canvas_menu(self, global_pos):
        """右键空白：重置视图 / 自动布局。"""
        menu = QMenu(self)
        menu.addAction(tr("Reset View"), self.canvas.reset_view)
        menu.addAction(tr("Auto Layout"), self._on_auto_layout)
        menu.exec(global_pos)

    def _on_edge_menu(self, from_scene, area_name, global_pos):
        """右键连线：对选中的待确认边应用或忽略。

        Args：
            from_scene：右键的边来源场景。
            area_name：右键的边区域名。
            global_pos：菜单弹出的全局坐标。
        """
        self._selected_edges = self.canvas.selected_edges()
        key = (from_scene, area_name)
        self._selected_edge = key if key in self._selected_edges else None
        pending = self._selected_pending_keys()
        menu = QMenu(self)
        apply_action = menu.addAction(tr("Apply"), lambda: self._apply_keys(pending))
        apply_action.setEnabled(bool(pending))
        ignore_action = menu.addAction(tr("Ignore"), lambda: self._ignore_keys(pending))
        ignore_action.setEnabled(bool(pending))
        menu.exec(global_pos)

    def _on_edge_double_clicked(self, from_scene, area_name):
        """双击候选边：直接应用这一条。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。
        """
        route = self.library.routes.get(from_scene, area_name)
        if route is None or route.routable():
            self.set_status("这条边已生效，无需应用")
            return
        self._apply_keys({(from_scene, area_name)})

    def _selected_pending_keys(self):
        """返回选中集合里仍待确认的边键。"""
        keys = []
        for key in sorted(self._selected_edges):
            route = self.library.routes.get(*key)
            if route is not None and not route.routable():
                keys.append(key)
        return keys

    def _on_canvas_selection_changed(self):
        """框选结束后刷新按钮状态与选中提示。"""
        self._selected_edges = self.canvas.selected_edges()
        self._selected_edge = next(iter(self._selected_edges), None)
        if not self._selected_edges:
            pending = self._pending_routes()
            self.selected_label.setText(
                f"待确认 {len(pending)} 条　本次新发现 {len(self._new_edge_keys)} 条　框选或点线可选中"
            )
        elif len(self._selected_edges) > 1:
            self.selected_label.setText(f"已选中 {len(self._selected_edges)} 条边　右键可应用或忽略")
        else:
            key = self._selected_edge
            route = self.library.routes.get(*key)
            if route is not None:
                state = "已生效" if route.routable() else "待确认"
                self.selected_label.setText(
                    f"{key[0]}.{key[1]} → {route.target_scene}　{state}　等待 {route.after_wait:.1f}s"
                )
        self._refresh_actions()

    def _on_edge_clicked(self, from_scene, area_name):
        """同步画布多选集合，并显示主选中边的状态。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。
        """
        self._selected_edges = self.canvas.selected_edges()
        key = (from_scene, area_name)
        self._selected_edge = key if key in self._selected_edges else None
        if len(self._selected_edges) > 1:
            self.selected_label.setText(f"已选中 {len(self._selected_edges)} 条边　右键可应用或忽略")
            self._refresh_actions()
            return
        route = self.library.routes.get(from_scene, area_name) if key in self._selected_edges else None
        if route is None:
            self.selected_label.setText("")
            self._refresh_actions()
            return
        state = "已生效" if route.routable() else "待确认"
        self.selected_label.setText(
            f"{self._scene_label(from_scene)} · {area_name} → "
            f"{self._scene_label(route.target_scene)}　{state}　等待 {route.after_wait:.1f}s"
        )
        self._refresh_actions()

    def _apply_keys(self, keys):
        """应用一组待确认候选（只升档，不动已生效路径）。

        Args：
            keys：`(from_scene, area_name)` 的可迭代集合。
        """
        pending = []
        for key in dict.fromkeys(keys):
            route = self.library.routes.get(*key)
            if route is not None and not route.routable():
                pending.append(key)
        if not pending:
            self.set_status("没有可应用的待确认边")
            return
        self.library.ensure_routes()
        for from_scene, area_name in pending:
            self.library.routes.set_source(from_scene, area_name, model.SOURCE_CONFIRMED)
        self.library.save_routes()
        self._new_edge_keys.difference_update(pending)
        self._clear_selection()
        self.reload()
        self._notify_changed()
        self.set_status(f"已应用 {len(pending)} 条候选")

    def _ignore_keys(self, keys):
        """忽略一组待确认候选（已生效路径保留）。

        Args：
            keys：`(from_scene, area_name)` 的可迭代集合。
        """
        pending = []
        for key in dict.fromkeys(keys):
            route = self.library.routes.get(*key)
            if route is not None and not route.routable():
                pending.append(key)
        if not pending:
            self.set_status("没有可忽略的待确认边")
            return
        self.library.ensure_routes()
        for from_scene, area_name in pending:
            self.library.routes.remove(from_scene, area_name)
        self.library.save_routes()
        self._new_edge_keys.difference_update(pending)
        self._clear_selection()
        self.reload()
        self._notify_changed()
        self.set_status(f"已忽略 {len(pending)} 条候选")

    def _clear_selection(self):
        """清空画布与页面上的选中状态。"""
        self._selected_edge = None
        self._selected_edges = set()
        self.canvas.set_selected_edges(set())

    def _on_apply_all(self):
        """应用**全部**待确认候选。

        Note：
            用户选择的范围包含历史候选；数量大时先确认一次，避免误点。探索页
            没有保存按钮可回退，这一步是唯一的拦截点。
        """
        pending = self._pending_routes()
        if not pending:
            return
        from ok.ui.qt.kit import confirm

        if not confirm(
            self,
            tr("Apply All"),
            tr("Apply all {count} pending edges?").format(count=len(pending)),
            ok_text=tr("Apply"),
        ):
            return
        self._apply_keys({(route.from_scene, route.area_name) for route in pending})

    def _on_ignore_all(self):
        """忽略**全部**待确认候选（不动已生效路径）。"""
        pending = self._pending_routes()
        if not pending:
            return
        from ok.ui.qt.kit import confirm

        if not confirm(
            self,
            tr("Ignore All"),
            tr("Remove all {count} pending edges? Active routes are kept.").format(count=len(pending)),
            ok_text=tr("Ignore"),
            danger=True,
        ):
            return
        self._ignore_keys({(route.from_scene, route.area_name) for route in pending})

    def _notify_changed(self):
        """通知外部刷新场景路由页。"""
        if self._on_changed is not None:
            self._on_changed()
