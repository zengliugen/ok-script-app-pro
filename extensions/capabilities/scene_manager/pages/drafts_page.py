# -*- coding: utf-8 -*-
"""草稿子页签：确认 / 丢弃探索产物。

草稿必须逐条或批量人工确认才进正式库，因为探索是无人值守跑出来的，一条误边
会让后续**每一次**导航都点错地方，而错误表现为「到了奇怪的地方」而非报错。

确认时的两个判断：

- **草稿转正换新 id**。指向草稿的边会被清空并计数提示——边只能指向正式场景，
  留着草稿 id 会形成悬空引用，而寻路时该边被静默忽略，用户看不到「这里本该有条边」。
- **确认后边升为 confirmed**。`explored` 的边不参与寻路，只有人工确认过才升档。
"""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from extensions.capabilities.scene_manager.translate import tr


class DraftsPage(QWidget):
    """草稿子页签。"""

    def __init__(self, library, on_changed=None, parent=None):
        """构造草稿页。

        Args：
            library：`store.SceneLibrary` 实例。
            on_changed：草稿变更回调（确认/丢弃后调用），供主页签刷新其它子页签。
            parent：父控件。
        """
        super().__init__(parent)
        self.library = library
        self._on_changed = on_changed
        self._drafts = []
        self._build_ui()

    def _build_ui(self):
        """搭建界面。"""
        from ok.ui.qt.kit import Button, Label

        root = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.refresh_button = Button(tr("Refresh"), variant="secondary", size="small")
        self.refresh_button.clicked.connect(self.reload)
        toolbar.addWidget(self.refresh_button)
        self.confirm_button = Button(tr("Confirm Selected"), variant="primary", size="small")
        self.confirm_button.clicked.connect(self._on_confirm_selected)
        toolbar.addWidget(self.confirm_button)
        self.confirm_all_button = Button(tr("Confirm All"), variant="tonal", size="small")
        self.confirm_all_button.clicked.connect(self._on_confirm_all)
        toolbar.addWidget(self.confirm_all_button)
        self.discard_button = Button(tr("Discard Selected"), variant="danger", size="small")
        self.discard_button.clicked.connect(self._on_discard_selected)
        toolbar.addWidget(self.discard_button)
        self.discard_all_button = Button(tr("Discard All"), variant="danger", size="small")
        self.discard_all_button.clicked.connect(self._on_discard_all)
        toolbar.addWidget(self.discard_all_button)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        # 说明文字必须换行：`QLabel` 的最小宽度等于整段文本宽度，不换行时
        # 这一句会把本页最小宽度撑到上千像素；`QStackedWidget` 取所有子页的
        # 最小值，于是配置页、路由页都会被这行说明推得比窗口还宽，卡片列表
        # 右侧被裁掉且没有横向滚动条。换行后最小宽度只取决于最长的不可断词。
        self.hint_label = Label(
            tr(
                "Drafts come from exploration and only join the library after confirmation. "
                "Confirming marks edges as confirmed; edges pointing to other drafts are cleared "
                "because edges may only target real scenes."
            ),
            "secondary",
            word_wrap=True,
        )
        root.addWidget(self.hint_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Draft ID"), tr("Name"), tr("Features"), tr("Areas")])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        self.status_label = Label("", "caption")
        root.addWidget(self.status_label)

    # --- 数据 ---------------------------------------------------------------

    def reload(self):
        """重新读取草稿区。"""
        drafts, failures = self.library.load_drafts()
        self._drafts = drafts
        self.table.setRowCount(len(drafts))
        for row, draft in enumerate(drafts):
            self.table.setItem(row, 0, QTableWidgetItem(draft.scene_id))
            self.table.setItem(row, 1, QTableWidgetItem(draft.name))
            self.table.setItem(row, 2, QTableWidgetItem(str(len(draft.features))))
            self.table.setItem(row, 3, QTableWidgetItem(str(len(draft.areas))))
        notes = [f"{len(drafts)} 条草稿待确认"]
        if failures:
            notes.append("无法读取：" + "；".join(f"{sid}（{msg}）" for sid, msg in failures))
        if not drafts:
            notes = ["没有待确认的草稿"]
        self.status_label.setText("　".join(notes))

    # --- 动作 ---------------------------------------------------------------

    def _selected_ids(self):
        """返回表格中选中的草稿 id。

        Returns：
            草稿 id 列表。
        """
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        result = []
        for row in rows:
            item = self.table.item(row, 0)
            if item is not None:
                result.append(item.text())
        return result

    def _on_confirm_selected(self):
        """确认选中的草稿。"""
        ids = self._selected_ids()
        if not ids:
            self._tip("请先在列表里选择草稿", "warning")
            return
        self._confirm(ids)

    def _on_confirm_all(self):
        """确认全部草稿。"""
        ids = [draft.scene_id for draft in self._drafts]
        if not ids:
            return
        self._confirm(ids)

    def _confirm(self, draft_ids):
        """把草稿转正。

        Args：
            draft_ids：草稿 id 列表。
        """
        promoted = []
        dropped_total = 0
        failures = []
        for draft_id in draft_ids:
            try:
                scene, dropped = self.library.promote_draft(draft_id)
            except Exception as error:  # noqa: BLE001 — 单条失败不影响其余
                failures.append(f"{draft_id}（{error}）")
                continue
            promoted.append(f"{draft_id} → {scene.name}（{scene.scene_id}）")
            dropped_total += dropped
        self.reload()
        notes = []
        if promoted:
            notes.append("已确认：" + "、".join(promoted))
        if dropped_total:
            notes.append(f"有 {dropped_total} 条指向其它草稿的边被清空，请在流转页补配目标场景")
        if failures:
            notes.append("失败：" + "；".join(failures))
        self.status_label.setText("　".join(notes))
        # 失败项要显式报错，成功项报成功；两者都有时按「有失败」处理——
        # 用户最需要知道的是「有东西没成」。
        if failures:
            self._tip("；".join(failures), "error", f"确认草稿：{len(promoted)} 条成功，{len(failures)} 条失败")
        elif promoted:
            self._tip("　".join(notes), "success", f"已确认 {len(promoted)} 条草稿")
        self._notify_changed()

    def _on_discard_selected(self):
        """丢弃选中的草稿。"""
        ids = self._selected_ids()
        if not ids:
            self._tip("请先在列表里选择草稿", "warning")
            return
        self._discard(ids)

    def _on_discard_all(self):
        """丢弃全部草稿。"""
        ids = [draft.scene_id for draft in self._drafts]
        if not ids:
            return
        self._discard(ids)

    def _discard(self, draft_ids):
        """删除草稿目录。

        Args：
            draft_ids：草稿 id 列表。
        """
        removed = 0
        for draft_id in draft_ids:
            if self.library.delete_scene(draft_id):
                removed += 1
        self.reload()
        self._tip(f"已丢弃 {removed} 条草稿", "info")
        self._notify_changed()

    def _notify_changed(self):
        """通知外部草稿区已变更。"""
        if self._on_changed is not None:
            self._on_changed()

    def _tip(self, message, level="info", title=None):
        """弹一条浮层提示，同时写进状态栏。

        Args：
            message：正文。
            level：`info` / `success` / `warning` / `error`。
            title：可选的加粗标题。

        Note：
            两条路径都写：浮层负责「点完立刻看到结果」，状态栏负责事后回看
            （浮层会自动消失）。
        """
        from extensions.capabilities.scene_manager import notify

        self.status_label.setText(f"{title}：{message}" if title else message)
        notify.notify(self, message, level, title)
