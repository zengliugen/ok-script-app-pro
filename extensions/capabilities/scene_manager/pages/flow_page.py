# -*- coding: utf-8 -*-
"""场景路由子页签：节点画布 + 边表格 + 路径预览 + 单步试走。

**编辑走工作副本，保存才落盘**。进页面时把路由表克隆一份，画布上的增删改只
作用于副本：

- 新增或修改的边画成绿色，待删除的画成红色虚线；
- 顶部「保存修改」写回 `ok_scenes/routes.json`，「还原」丢弃副本；
- 删除连线不弹确认——有保存按钮兜底，误删可以还原。

画布操作按主流节点图约定：左键选择 / 空白处框选，Ctrl+左键增减选择，中键
（或 Alt+左键）平移，右键节点 / 连线 / 空白出菜单，Delete 删除选中连线。

**连线的两端都能拖出重连**：靠近目标端拖出只需等待时间；靠近来源端拖出要先选
新来源节点及其交互区域。右键节点的「倒序连线」是先定目标、再选来源的反向流程。
"""

import json

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from extensions.capabilities.scene_manager import graph, navigate, routes, store
from extensions.capabilities.scene_manager.flow_canvas import FlowCanvas
from extensions.capabilities.scene_manager.translate import tr


class FlowPage(QWidget):
    """场景路由子页签。"""

    def __init__(
        self,
        library,
        build_bridge=None,
        on_changed=None,
        parent=None,
    ):
        """构造路由页。

        Args：
            library：`store.SceneLibrary` 实例。
            build_bridge：返回 `runtime.TaskBridge` 的工厂，供试走使用。
            on_changed：保存成功后通知外部刷新其它页签。
            parent：父控件。
        """
        super().__init__(parent)
        self.library = library
        self._build_bridge = build_bridge
        self._on_changed = on_changed
        self.scene_graph = graph.SceneGraph()
        # 工作副本与加载时的快照。编辑只改副本，保存时整表写回。
        self._working = routes.RouteTable()
        self._baseline = {}
        self._working_ready = False
        # 磁盘路由表的签名：有未保存修改时用它判断其它页签是否改过路由。
        self._disk_signature = ""
        self._selected_key = None
        self._selected_edges = set()
        self._selected_node = None
        self._link = None
        self._refresh_guard = False
        self._last_failures = ""
        self._build_ui()
        self.reload()

    def sizeHint(self):
        """返回页面的默认尺寸。

        Returns：
            一个较小的固定尺寸。实际高度由外层页签分配；不覆盖时，底部
            自动换行的提示标签会按最小宽度把 sizeHint 撑到上千像素，
            外层滚动区因此出现上下滚动条。
        """
        return QSize(640, 480)

    def minimumSizeHint(self):
        """返回页面的最小尺寸，避免子控件把外层滚动区撑高。

        Returns：
            一个较小的固定尺寸。
        """
        return QSize(320, 240)

    # --- 界面 ---------------------------------------------------------------

    def _build_ui(self):
        """搭建界面。"""
        from ok.ui.qt.kit import Button, ComboBox, Label

        root = QVBoxLayout(self)
        # 外层页签已有内边距，这里收紧默认边距，把高度留给画布。
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        toolbar = QHBoxLayout()
        self.refresh_button = Button(tr("Refresh"), variant="secondary", size="small")
        self.refresh_button.clicked.connect(self.reload)
        toolbar.addWidget(self.refresh_button)

        self.preview_button = Button(tr("Preview Path"), variant="tonal", size="small")
        self.preview_button.clicked.connect(self._on_preview)
        toolbar.addWidget(self.preview_button)

        self.step_button = Button(tr("Try One Step"), variant="tonal", size="small")
        self.step_button.clicked.connect(self._on_try_step)
        self.step_button.setEnabled(self._build_bridge is not None)
        toolbar.addWidget(self.step_button)

        self.reset_view_button = Button(tr("Reset View"), variant="subtle", size="small")
        self.reset_view_button.clicked.connect(self._on_reset_view)
        toolbar.addWidget(self.reset_view_button)

        self.auto_layout_button = Button(tr("Auto Layout"), variant="subtle", size="small")
        self.auto_layout_button.clicked.connect(self._on_reset_layout)
        toolbar.addWidget(self.auto_layout_button)

        toolbar.addStretch(1)
        self.save_button = Button(tr("Save Changes"), variant="primary", size="small")
        self.save_button.clicked.connect(self._on_save)
        toolbar.addWidget(self.save_button)
        self.revert_button = Button(tr("Revert"), variant="secondary", size="small")
        self.revert_button.clicked.connect(self._on_revert)
        toolbar.addWidget(self.revert_button)
        # 清除全部连线：只改工作副本，保存后生效，点「还原」可恢复。
        self.clear_edges_button = Button(tr("Clear All Edges"), variant="subtle", size="small")
        self.clear_edges_button.clicked.connect(self._on_clear_edges)
        toolbar.addWidget(self.clear_edges_button)
        self.table_toggle = Button(tr("Inspect Table"), variant="subtle", size="small")
        self.table_toggle.clicked.connect(self._toggle_table)
        toolbar.addWidget(self.table_toggle)
        root.addLayout(toolbar)

        route_row = QHBoxLayout()
        route_row.addWidget(Label(tr("From"), "body"))
        self.start_combo = ComboBox()
        route_row.addWidget(self.start_combo, 1)
        route_row.addWidget(Label(tr("To"), "body"))
        self.target_combo = ComboBox()
        route_row.addWidget(self.target_combo, 1)
        root.addLayout(route_row)

        self.canvas = FlowCanvas(self, editable=True)
        self.canvas.node_clicked.connect(self._on_node_clicked)
        self.canvas.edge_clicked.connect(self._on_edge_clicked)
        self.canvas.selection_changed.connect(self._on_canvas_selection_changed)
        self.canvas.node_context_menu.connect(self._on_node_menu)
        self.canvas.edge_context_menu.connect(self._on_edge_menu)
        self.canvas.canvas_context_menu.connect(self._on_canvas_menu)
        self.canvas.positions_changed.connect(self._on_positions_changed)
        self.canvas.edge_relink_started.connect(self._on_edge_relink_started)
        self.canvas.edge_dropped_on_empty.connect(self._on_edge_dropped)
        self.canvas.link_completed.connect(self._on_link_completed)
        self.canvas.link_cancelled.connect(self._on_link_cancelled)
        root.addWidget(self.canvas, 1)

        # 底部只留提示与统计：编辑入口全部收进右键菜单。
        self.hint_label = Label("", "caption", word_wrap=True)
        root.addWidget(self.hint_label)
        self.status_label = Label("", "caption")
        root.addWidget(self.status_label)

        self.edge_table = QTableWidget(0, 5)
        self.edge_table.setHorizontalHeaderLabels(
            [tr("From Scene"), tr("Area"), tr("Target Scene"), tr("Source"), tr("Wait After Action")]
        )
        header = self.edge_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.edge_table.verticalHeader().setDefaultSectionSize(30)
        self.edge_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.edge_table.verticalHeader().setVisible(False)
        self.edge_table.setWordWrap(False)
        self.edge_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.edge_table.setMinimumHeight(110)
        self.edge_table.setMaximumHeight(180)
        self.edge_table.setVisible(False)
        self.edge_table.itemSelectionChanged.connect(self._on_table_selection)
        root.addWidget(self.edge_table)

        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self._on_delete_selected)

    # --- 数据 ---------------------------------------------------------------

    def reload(self):
        """重建视图。

        Note：
            **有未保存修改时只重建视图、不回读磁盘**。页签切换与其它页签的刷新
            都会走到这里，回读会把工作副本直接冲掉，而界面上没有任何提示。
        """
        scenes, failures = self.library.load_library()
        if failures:
            signature = "；".join(f"{sid}（{msg}）" for sid, msg in failures)
            if signature != self._last_failures:
                self._last_failures = signature
                self._tip(signature, "error", "有场景无法读取")
        if not self._working_ready or not self._dirty():
            self._pull_from_disk()
        else:
            self._warn_if_disk_changed()
        self._rebuild(scenes)

    def _pull_from_disk(self):
        """从磁盘加载路由表并重置工作副本与快照。"""
        try:
            self.library.load_routes()
        except store.SceneStoreError as error:
            self._tip(str(error), "error", "流转路由表无法读取")
            self.library.routes = routes.RouteTable()
            self.library._routes_ready = True
        self._working = self.library.routes.clone()
        self._baseline = self.library.routes.to_dict()
        self._disk_signature = self._signature(self.library.routes)
        self._working_ready = True
        self._selected_key = None
        self._selected_edges.clear()
        self._selected_node = None
        self._link = None

    @staticmethod
    def _signature(table):
        """返回路由表的稳定签名（比较磁盘是否变化）。"""
        return json.dumps(table.to_dict(), sort_keys=True, ensure_ascii=False)

    def _warn_if_disk_changed(self):
        """有未保存修改时，检测其它页签是否改过磁盘上的路由。"""
        try:
            self.library.load_routes()
        except store.SceneStoreError:
            return
        if self._signature(self.library.routes) != self._disk_signature:
            self._tip(
                "其它页签已更新路由；当前有未保存修改，保存或还原后再刷新",
                "warning",
                "路由已变化",
            )

    def _dirty(self):
        """返回工作副本相对快照是否有改动。"""
        return self._working.to_dict() != self._baseline

    def _routable_table(self):
        """返回工作副本里的可寻路路由表。"""
        return routes.RouteTable(route for route in self._working if route.routable())

    def _changed_keys(self):
        """返回新增或修改、尚未保存的边键。"""
        baseline = routes.RouteTable.from_dict(self._baseline)
        changed = set()
        for route in self._working.routable_routes():
            old = baseline.get(route.from_scene, route.area_name)
            if old is None or old.to_dict() != route.to_dict():
                changed.add((route.from_scene, route.area_name))
        return changed

    def _deleted_edges(self):
        """返回已标记删除、尚未保存的边（用于画红色虚线）。"""
        baseline = routes.RouteTable.from_dict(self._baseline)
        deleted = []
        for route in baseline.routable_routes():
            if self._working.get(route.from_scene, route.area_name) is None:
                deleted.append((route.from_scene, route.target_scene, route.area_name))
        return deleted

    def _rebuild(self, scenes, keep_view=False):
        """按工作副本重建画布、表格与统计。

        Args：
            scenes：场景列表。
            keep_view：为 True 时保留当前缩放与视口中心。编辑已有连线的动作
                （改目标、改等待、删边、撤销删边）传 True——每次编辑都把视图重置
                到「全部节点可见」，用户刚对准的位置会丢，连改几条就要重新找。
        """
        try:
            self.scene_graph = graph.SceneGraph.build(scenes, self._routable_table(), include_pending=False)
        except graph.GraphError as error:
            self.scene_graph = graph.SceneGraph.build([])
            self.canvas.clear_graph()
            self.edge_table.setRowCount(0)
            self._tip(str(error), "error", "无法建图")
            self._reload_combos(scenes)
            self._refresh_stats()
            self._refresh_actions()
            return
        # **编辑不重排**：保留视图时沿用画布上的当前位置。弹簧布局依赖边集，
        # 删一条边会让没保存过位置的节点整体重算，节点与连线一起跳——用户看到的
        # 是「标记删除后线自己动了」。首次加载与切页签仍按布局文件/自动布局排。
        positions = self.canvas.remember_positions() if keep_view else self.library.load_layout()
        self.canvas.set_graph(
            self.scene_graph,
            positions,
            changed_keys=self._changed_keys(),
            ghost_edges=self._deleted_edges(),
            keep_view=keep_view,
        )
        self.canvas.set_selected_edges(self._selected_edges, self._selected_key)
        self._refresh_edges()
        self._reload_combos(scenes)
        self._refresh_stats()
        self._refresh_actions()
        self._refresh_hint()

    def _refresh_edges(self):
        """刷新边表格（只列可寻路路由）。"""
        self._refresh_guard = True
        try:
            routes_list = sorted(self._working.routable_routes(), key=lambda item: (item.from_scene, item.area_name))
            self.edge_table.setRowCount(len(routes_list))
            for index, route in enumerate(routes_list):
                from_item = QTableWidgetItem(self._label(route.from_scene))
                from_item.setData(Qt.ItemDataRole.UserRole, (route.from_scene, route.area_name))
                self.edge_table.setItem(index, 0, from_item)
                self.edge_table.setItem(index, 1, self._read_only(route.area_name))
                self.edge_table.setItem(index, 2, self._read_only(self._label(route.target_scene)))
                self.edge_table.setItem(index, 3, self._read_only(route.source))
                self.edge_table.setItem(index, 4, self._read_only(f"{route.after_wait:.1f}s"))
            row_height = self.edge_table.verticalHeader().defaultSectionSize()
            self.edge_table.setFixedHeight(
                min(180, self.edge_table.horizontalHeader().height() + max(1, len(routes_list)) * row_height + 4)
            )
            self._select_table_row(self._selected_key)
        finally:
            self._refresh_guard = False

    def _read_only(self, text):
        """造一个不可编辑的表格项。"""
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _reload_combos(self, scenes):
        """刷新起止场景下拉。"""
        for combo in (self.start_combo, self.target_combo):
            combo.blockSignals(True)
            combo.clear()
            for scene in scenes:
                combo.addItem(f"{scene.name}（{scene.scene_id}）", scene.scene_id)
            combo.blockSignals(False)

    def _label(self, scene_id):
        """返回场景的显示文本。"""
        scene = self.scene_graph.scenes.get(scene_id)
        if scene is None:
            return scene_id
        return f"{scene.name}（{scene_id}）"

    def _refresh_stats(self):
        """刷新底部统计行。"""
        routable = len(self._working.routable_routes())
        pending = len(self._working) - routable
        isolated = self.scene_graph.isolated_scenes() if self.scene_graph.scenes else []
        notes = [f"{self.scene_graph.scene_count()} 个场景 / {routable} 条可寻路"]
        if pending:
            notes.append(f"{pending} 条待确认")
        if isolated:
            notes.append(f"孤立场景 {len(isolated)} 个")
        if self._dirty():
            notes.insert(0, "● 有未保存修改")
        self.status_label.setText("　".join(notes))

    def _refresh_actions(self):
        """刷新保存/还原/重置按钮的可用状态。"""
        dirty = self._dirty()
        self.save_button.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)

    def _refresh_hint(self):
        """刷新底部提示行。"""
        if self._link is not None:
            if self._link["kind"] in ("reverse", "relink_source"):
                self.hint_label.setText("连线中：点击来源场景节点；Esc 或右键取消")
            else:
                self.hint_label.setText("连线中：点击目标场景节点；Esc 或右键取消")
            return
        if len(self._selected_edges) > 1:
            self.hint_label.setText(f"已选中 {len(self._selected_edges)} 条连线　Delete 批量删除　右键编辑主选中边")
            return
        if self._selected_key is not None:
            route = self._working.get(*self._selected_key)
            if route is not None:
                changed = self._selected_key in self._changed_keys()
                state = "未保存的修改" if changed else "已生效"
                self.hint_label.setText(
                    f"{self._label(route.from_scene)} · {route.area_name} → {self._label(route.target_scene)}"
                    f"　等待 {route.after_wait:.1f}s　{state}"
                )
                return
            baseline = routes.RouteTable.from_dict(self._baseline).get(*self._selected_key)
            if baseline is not None and baseline.routable():
                self.hint_label.setText(
                    f"{self._label(baseline.from_scene)} · {baseline.area_name} → "
                    f"{self._label(baseline.target_scene)}　已标记删除，保存后生效"
                )
                return
        if self._selected_node and self._selected_node in self.scene_graph.scenes:
            self.hint_label.setText(navigate.describe_scene(self.scene_graph, self._selected_node))
            return
        self.hint_label.setText(
            "左键选择 / 空白处框选 · Ctrl+左键增减选择 · 中键或 Alt+左键平移 · 滚轮缩放 · "
            "悬停高亮连线，拖动任一端可重连 · 右键节点新建/倒序连线 · 右键连线编辑 · "
            "右键空白自动布局 · Delete 删除"
        )

    def _select_table_row(self, key):
        """把表格选中行同步到指定边。

        Note：
            同步时置 `_refresh_guard`：`selectRow` 会触发 `itemSelectionChanged`，
            不挡住的话表格会把画布的多选集合重新压成单选。
        """
        if not key:
            return
        for row in range(self.edge_table.rowCount()):
            item = self.edge_table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                previous = self._refresh_guard
                self._refresh_guard = True
                try:
                    self.edge_table.selectRow(row)
                finally:
                    self._refresh_guard = previous
                return

    def _toggle_table(self):
        """显示或收起边表格。"""
        visible = not self.edge_table.isVisible()
        self.edge_table.setVisible(visible)
        self.table_toggle.setText(tr("Hide Table") if visible else tr("Inspect Table"))

    # --- 选择 ---------------------------------------------------------------

    def _on_node_clicked(self, scene_id):
        """点节点：显示它的概要。"""
        self._selected_key = None
        self._selected_edges.clear()
        self._selected_node = scene_id
        self.canvas.set_selected_edges(set())
        self._refresh_hint()

    def _on_edge_clicked(self, from_scene, area_name):
        """点边：同步多选集合，并在提示行显示主选中边的状态。"""
        self._selected_node = None
        self._selected_edges = self.canvas.selected_edges()
        key = (from_scene, area_name)
        self._selected_key = key if key in self._selected_edges else None
        if self._selected_key is not None:
            self._select_table_row(self._selected_key)
        self._refresh_hint()

    def _on_canvas_selection_changed(self):
        """框选结束后同步表格与提示行。"""
        self._selected_edges = self.canvas.selected_edges()
        self._selected_node = None
        self._selected_key = next(iter(self._selected_edges), None)
        if self._selected_key is not None:
            self._select_table_row(self._selected_key)
        self._refresh_hint()

    def _on_table_selection(self):
        """表格选中行变化时同步画布。"""
        if self._refresh_guard:
            return
        row = self.edge_table.currentRow()
        item = self.edge_table.item(row, 0) if row >= 0 else None
        key = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not key:
            return
        self._selected_key = key
        self._selected_edges = {key}
        self._selected_node = None
        self.canvas.set_selected_edges(self._selected_edges, key)
        self._refresh_hint()

    # --- 右键菜单 -----------------------------------------------------------

    def _on_node_menu(self, scene_id, global_pos):
        """右键节点：新建连线（子菜单列出全部交互区域）。"""
        scene = self.scene_graph.scenes.get(scene_id)
        if scene is None:
            return
        menu = QMenu(self)
        submenu = menu.addMenu(tr("New Connection"))
        if not scene.areas:
            action = submenu.addAction(tr("No interaction regions"))
            action.setEnabled(False)
        for area in scene.areas:
            target = self._working.target_of(scene_id, area.name)
            text = area.name
            if target:
                text = f"{area.name} → {self._label(target)}"
            action = submenu.addAction(text)
            action.triggered.connect(lambda _checked=False, name=area.name: self._start_link(scene_id, name))
        menu.addSeparator()
        # 倒序连线：先定目标（当前节点），再点来源节点并选它的交互区域。
        menu.addAction(tr("Reverse Connection"), lambda: self._start_reverse_link(scene_id))
        menu.exec(global_pos)

    def _on_edge_menu(self, from_scene, area_name, global_pos):
        """右键连线：改等待 / 改目标 / 删除；已删除的边提供撤销。"""
        key = (from_scene, area_name)
        self._selected_edges = self.canvas.selected_edges()
        self._selected_key = key if key in self._selected_edges else None
        route = self._working.get(*key)
        menu = QMenu(self)
        if route is None:
            baseline = routes.RouteTable.from_dict(self._baseline)
            old = baseline.get(*key)
            if old is not None and old.routable():
                menu.addAction(tr("Undo Delete"), lambda: self._restore_edge(old))
            menu.exec(global_pos)
            return
        targets = self._menu_targets(key)
        menu.addAction(tr("Set Wait"), lambda: self._edit_wait(key, targets))
        menu.addAction(tr("Change Target"), lambda: self._start_retarget(key))
        delete_text = tr("Delete {count} Edges").format(count=len(targets)) if len(targets) > 1 else tr("Delete Edge")
        menu.addAction(delete_text, lambda: self._delete_edges(targets))
        menu.exec(global_pos)

    def _menu_targets(self, key):
        """右键菜单的作用集合。

        Args：
            key：被右键的边键。

        Returns：
            该边在选中集合里时返回全部选中边，否则只返回它自己。

        Note：
            右键菜单只对「触发菜单的那条边」生效，多选后点删除只删一条，
            用户会以为多选没生效。改目标仍只作用于主键那条：把多条边指向
            同一个目标风险太大，需要逐条确认。
        """
        if key in self._selected_edges and len(self._selected_edges) > 1:
            return sorted(self._selected_edges)
        return [key]

    def _on_canvas_menu(self, global_pos):
        """右键空白：重置视图 / 自动布局。"""
        menu = QMenu(self)
        menu.addAction(tr("Reset View"), self._on_reset_view)
        menu.addAction(tr("Auto Layout"), self._on_reset_layout)
        menu.exec(global_pos)

    # --- 编辑（只改工作副本） -----------------------------------------------

    def _start_link(self, scene_id, area_name):
        """从节点的出端开始新建连线（松手时选目标）。"""
        self._link = {"kind": "new", "from": scene_id, "area": area_name}
        self.canvas.begin_link(scene_id, area_name, loose="in")
        self._refresh_hint()

    def _start_reverse_link(self, scene_id):
        """从节点的入端开始倒序连线（松手时选来源）。"""
        self._link = {"kind": "reverse", "target": scene_id}
        self.canvas.begin_link(scene_id, "", loose="out")
        self._refresh_hint()

    def _start_retarget(self, key):
        """右键菜单里的「更改目标」：等价于拖住边的目标端。

        与拖拽同形：**立即隐藏旧边**，选中节点后由 `_on_link_completed` 替换目标。
        """
        route = self._working.get(*key)
        if route is None:
            return
        self._link = {"kind": "relink_target", "key": key}
        self.canvas.begin_retarget(key)
        self._refresh_hint()

    def _on_edge_relink_started(self, from_scene, area_name, end):
        """画布从连线一端拖出了阈值：进入重连。

        Args：
            from_scene：边键的来源场景。
            area_name：边键的区域名。
            end：拖的是哪一端，`"in"` 目标端 / `"out"` 来源端。
        """
        key = (from_scene, area_name)
        route = self._working.get(*key)
        if route is None:
            # 已标记删除的边不允许重连，直接退出。
            self.canvas.cancel_link()
            return
        if end == "in":
            self._link = {"kind": "relink_target", "key": key}
        else:
            self._link = {"kind": "relink_source", "key": key, "target": route.target_scene}
        self._refresh_hint()

    def _on_link_completed(self, other_scene):
        """连线落下节点：按活动端类型决定后续。

        Args：
            other_scene：被点中的场景 id。

        Note：
            **活动端是出端时需要再选交互区域**（边必须有「从哪个区域出去」），
            活动端是入端时只需要等待时间。这是两种方向唯一的差别。
        """
        link = self._link
        self._link = None
        if link is None:
            return
        kind = link["kind"]
        selected = None
        if kind == "new":
            existing = self._working.get(link["from"], link["area"])
            default_wait = existing.after_wait if existing is not None else 1.0
            wait = self._ask_wait(default_wait)
            if wait is None:
                self._refresh_hint()
                return
            self._working.set_target(
                link["from"],
                link["area"],
                other_scene,
                after_wait=wait,
                source=existing.source if existing is not None and existing.routable() else None,
            )
            selected = (link["from"], link["area"])
        elif kind == "relink_target":
            route = self._working.get(*link["key"])
            if route is None:
                self._refresh_hint()
                return
            wait = self._ask_wait(route.after_wait)
            if wait is None:
                self._refresh_hint()
                return
            self._working.set_target(
                route.from_scene,
                route.area_name,
                other_scene,
                after_wait=wait,
                source=route.source,
            )
            selected = link["key"]
        elif kind == "reverse":
            area_name = self._ask_area(other_scene)
            if area_name is None:
                self._refresh_hint()
                return
            wait = self._ask_wait(1.0)
            if wait is None:
                self._refresh_hint()
                return
            self._working.set_target(other_scene, area_name, link["target"], after_wait=wait)
            selected = (other_scene, area_name)
        else:
            # relink_source：活动端是出端，弹新来源的交互区域菜单
            route = self._working.get(*link["key"])
            if route is None:
                self._refresh_hint()
                return
            area_name = self._ask_area(other_scene, default=route.area_name)
            if area_name is None:
                self._refresh_hint()
                return
            wait = self._ask_wait(route.after_wait)
            if wait is None:
                self._refresh_hint()
                return
            if (other_scene, area_name) != link["key"]:
                self._working.remove(*link["key"])
            self._working.set_target(
                other_scene,
                area_name,
                route.target_scene,
                after_wait=wait,
                source=route.source,
            )
            selected = (other_scene, area_name)
        self._selected_key = selected
        self._selected_node = None
        scenes, _failures = self.library.load_library()
        self._rebuild(scenes, keep_view=True)
        self._tip("已修改连线，保存后生效", "info", "未保存")

    def _on_edge_dropped(self, from_scene, area_name):
        """拖拽重连后落在空白：删除这条边（只改工作副本）。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。
        """
        key = (from_scene, area_name)
        self._selected_edges.discard(key)
        if self._selected_key == key:
            self._selected_key = None
        if self._working.get(*key) is None:
            return
        self._mark_deleted({key})

    def _on_link_cancelled(self):
        """连线取消。"""
        self._link = None
        self._refresh_hint()

    def _ask_area(self, scene_id, default=""):
        """弹出菜单选择一个交互区域。

        Args：
            scene_id：来源场景 id。
            default：已有区域的名称，菜单里加粗/标注。

        Returns：
            区域名；取消或没有区域时返回 None。
        """
        from PySide6.QtGui import QCursor

        scene = self.scene_graph.scenes.get(scene_id)
        if scene is None or not scene.areas:
            self._tip("该场景没有交互区域，无法作为连线来源", "warning")
            return None
        menu = QMenu(self)
        chosen = []
        for area in scene.areas:
            target = self._working.target_of(scene_id, area.name)
            text = area.name
            if target:
                text = f"{area.name} → {self._label(target)}"
            if area.name == default:
                text = "● " + text
            action = menu.addAction(text)
            action.triggered.connect(lambda _checked=False, name=area.name: chosen.append(name))
        menu.exec(QCursor.pos())
        return chosen[0] if chosen else None

    def _edit_wait(self, key, targets=None):
        """修改一条或多条边的等待时间（只改副本）。

        Args：
            key：主选中边的键，用它的当前等待作为弹窗默认值。
            targets：要一起修改的边键列表；省略时只改 `key` 这一条。
        """
        route = self._working.get(*key)
        if route is None:
            return
        wait = self._ask_wait(route.after_wait)
        if wait is None:
            return
        changed = 0
        for target_key in targets or [key]:
            item = self._working.get(*target_key)
            if item is None:
                continue
            self._working.set_target(
                item.from_scene,
                item.area_name,
                item.target_scene,
                after_wait=wait,
                source=item.source,
            )
            changed += 1
        if not changed:
            return
        scenes, _failures = self.library.load_library()
        self._rebuild(scenes, keep_view=True)
        if changed > 1:
            self._tip(f"{changed} 条连线的等待改为 {wait:.1f}s，保存后生效", "info", "未保存")
        else:
            self._tip(f"等待改为 {wait:.1f}s，保存后生效", "info", "未保存")

    def _ask_wait(self, default):
        """弹窗收集等待秒数。

        Args：
            default：初始值。

        Returns：
            秒数；用户取消或输入非法时返回 None。
        """
        values = self._ask_fields(
            tr("Wait After Action"),
            [("wait", tr("Seconds"), f"{float(default):.1f}", None)],
        )
        if values is None:
            return None
        try:
            seconds = float(values.get("wait") or default)
        except ValueError:
            self._tip("等待时间必须是数字", "error")
            return None
        if seconds < 0:
            self._tip("等待时间不能为负", "error")
            return None
        return seconds

    def _delete_edge(self, key):
        """从工作副本删除一条边（不弹确认）。"""
        self._delete_edges({key})

    def _delete_edges(self, keys):
        """批量从工作副本删除边（不弹确认）。

        Args：
            keys：`(from_scene, area_name)` 的可迭代集合。
        """
        targets = set(keys)
        self._selected_edges -= targets
        if self._selected_key in targets:
            self._selected_key = None
        self._mark_deleted(targets)

    def _mark_deleted(self, keys):
        """把一组边从工作副本移除并重建视图。

        Args：
            keys：`(from_scene, area_name)` 集合。
        """
        removed = 0
        for key in keys:
            if self._working.get(*key) is not None:
                self._working.remove(*key)
                removed += 1
        if not removed:
            return
        scenes, _failures = self.library.load_library()
        self._rebuild(scenes, keep_view=True)
        self._tip(f"已标记删除 {removed} 条，保存后生效", "info", "未保存")

    def _restore_edge(self, old):
        """撤销对一条已删除边的标记。"""
        self._working.put(routes.Route.from_dict(old.to_dict()))
        self._selected_key = (old.from_scene, old.area_name)
        self._selected_edges = {self._selected_key}
        scenes, _failures = self.library.load_library()
        self._rebuild(scenes, keep_view=True)

    def _on_delete_selected(self):
        """Delete 键：批量删除当前选中的全部连线。"""
        if self._link is not None or not self._selected_edges:
            return
        self._delete_edges(self._selected_edges)

    def _on_save(self):
        """把工作副本整表写回磁盘。"""
        if not self._dirty():
            return
        try:
            self.library.replace_routes(self._working)
        except store.SceneStoreError as error:
            self._tip(str(error), "error", "保存失败")
            return
        self._working = self.library.routes.clone()
        self._baseline = self.library.routes.to_dict()
        self._disk_signature = self._signature(self.library.routes)
        self.reload()
        self._on_scenes_changed()
        self._tip("已保存路由修改", "success", "已保存")

    def _on_revert(self):
        """丢弃工作副本，重新从磁盘加载。"""
        if not self._dirty():
            return
        from ok.ui.qt.kit import confirm

        if not confirm(self, tr("Revert"), tr("Discard all unsaved route changes?"), ok_text=tr("Revert")):
            return
        self._pull_from_disk()
        self.reload()
        self._tip("已还原为磁盘上的路由", "info")

    def _on_clear_edges(self):
        """清除工作副本里的全部连线（保存后生效，还原可恢复）。"""
        total = len(self._working)
        if not total:
            self._tip("当前没有连线可清除", "info")
            return
        from ok.ui.qt.kit import confirm

        message = tr("Remove all {count} edges from the working copy? Save applies it; Revert restores them.").format(
            count=total
        )
        if not confirm(self, tr("Clear All Edges"), message, ok_text=tr("Clear")):
            return
        self._working = routes.RouteTable()
        self._selected_key = None
        self._selected_edges.clear()
        scenes, _failures = self.library.load_library()
        self._rebuild(scenes, keep_view=True)
        self._tip(f"已清除 {total} 条连线，保存后生效", "info", "未保存")

    def _on_reset_view(self):
        """缩放回全部节点可见。"""
        self.canvas.reset_view()

    def _on_reset_layout(self):
        """自动重新排列全部节点并保存位置。"""
        try:
            self.library.clear_layout()
        except store.SceneStoreError as error:
            self._tip(str(error), "error", "自动布局失败")
            return
        self.canvas.reset_layout()
        self._tip("已自动布局并保存节点位置", "info", "自动布局")

    def _on_positions_changed(self, positions):
        """节点拖动或重置布局后保存位置。"""
        try:
            self.library.save_layout(positions)
        except store.SceneStoreError as error:
            self._tip(str(error), "error", "保存节点位置失败")

    # --- 预览与试走 ---------------------------------------------------------

    def _current_route(self):
        """读取界面上的起止场景。"""
        return self.start_combo.currentData(), self.target_combo.currentData()

    def _on_preview(self):
        """预览最短路径并在画布上高亮。"""
        start, target = self._current_route()
        if not start or not target:
            self._tip("请先选择起点与目标场景", "warning")
            return
        steps, error = navigate.plan_preview(self.scene_graph, start, target)
        if error:
            self.canvas.clear_highlight()
            self._tip(error, "error", "无法规划路径")
            return
        self.canvas.set_highlight(steps)
        if not steps:
            self._tip("起点与目标相同，无需移动", "info")
            return
        self.hint_label.setText(
            f"路径（{len(steps)} 步）："
            + " ; ".join(
                f"{self._label(step.from_scene)} --{step.area_name}--> {self._label(step.to_scene)}" for step in steps
            )
        )

    def _on_try_step(self):
        """只走当前路径的第一步，验证这条边是否真的通。"""
        if self._build_bridge is None:
            return
        start, target = self._current_route()
        if not start or not target:
            self._tip("请先选择起点与目标场景", "warning")
            return
        steps, error = navigate.plan_preview(self.scene_graph, start, target)
        if error or not steps:
            self._tip(error or "起点与目标相同，没有可试走的边", "error", "无法试走")
            return
        first = steps[0]
        scene = self.scene_graph.scenes.get(first.from_scene)
        area = scene.find_area(first.area_name) if scene else None
        if area is None:
            self._tip(f"区域 {first.area_name} 已不存在", "error", "无法试走")
            return
        route = self._working.get(first.from_scene, first.area_name)
        wait = route.after_wait if route is not None else 1.0
        try:
            bridge = self._build_bridge()
            result = bridge.click_and_identify(scene, area, max(0.5, wait))
        except Exception as error:  # noqa: BLE001 — 试走失败只提示，不影响界面
            self._tip(str(error), "error", "试走失败")
            return
        expected = self._label(first.to_scene)
        actual = self._label(result.scene_id()) if result.matched() else "未识别"
        if result.matched() and result.scene_id() == first.to_scene:
            self._tip(f"{area.name} → {expected}", "success", "试走成功")
        else:
            self._tip(
                f"点 {area.name} 后识别为 {actual}，期望 {expected}",
                "warning",
                "试走未达预期",
            )

    # --- 其它 ---------------------------------------------------------------

    def _ask_fields(self, title, fields):
        """用属性对话框收集一组字段。"""
        from extensions.capabilities.scene_manager.pages.scene_editor import PropertyDialog

        dialog = PropertyDialog(title, fields, parent=self)
        accepted = dialog.exec()
        values = dialog.values() if accepted else None
        dialog.deleteLater()
        return values

    def _on_scenes_changed(self):
        """保存后通知外部刷新其它页签。"""
        if self._on_changed is not None:
            self._on_changed()

    def _tip(self, message, level="info", title=None):
        """弹一条浮层提示。"""
        from extensions.capabilities.scene_manager import notify

        notify.notify(self, message, level, title)
