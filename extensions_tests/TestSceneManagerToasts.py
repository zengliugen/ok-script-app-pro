# -*- coding: utf-8 -*-
"""场景管理各页面的「操作结果走浮层」约定。

**用户报的问题**：点了按钮只有底部一行小字，表现就是「点了没反应」。这次把
操作结果统一改走浮层（`notify` 模块），这里逐页锁住这条约定。

区分两类文案：

| 类别 | 例子 | 归宿 |
|---|---|---|
| **一次性操作结果** | 导入失败、试走成功、已丢弃 3 条草稿 | 浮层（同时留状态栏） |
| **常驻数据 / 实时重算** | 路径描述、建图统计、必需特征数警告 | 只留状态栏 |

常驻类也弹浮层的话，实时刷新会让提示不停叠加，把界面糊住。
"""

import math
import os
import shutil
import tempfile
import unittest
from unittest import mock

from extensions.capabilities.scene_manager import model, store

try:
    from PySide6.QtWidgets import QApplication

    QT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 GUI 依赖
    QApplication = None
    QT_ERROR = str(exc)


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class _ToastCase(unittest.TestCase):
    """浮层断言公共部分。"""

    @classmethod
    def setUpClass(cls):
        """确保有 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def toasts(self, widget):
        """取该控件所在窗口上的浮层。

        Args：
            widget：任意控件。

        Returns：
            `Toast` 列表。
        """
        from ok.ui.qt.kit.feedback import Toast

        for _ in range(5):
            self.app.processEvents()
        return widget.findChildren(Toast)

    def clear(self, widget):
        """关掉已有浮层，避免污染后续断言。"""
        for toast in self.toasts(widget):
            toast.close()
        for _ in range(4):
            self.app.processEvents()


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class TestConfigPageToasts(_ToastCase):
    """配置页：失败与成功结果都弹浮层。"""

    def _page(self):
        """造一个配置页。

        Returns:
            `ConfigPage` 实例（已显示）。
        """
        from ok.ui.qt.kit import theme

        from extensions.capabilities.scene_manager.pages import config_page

        temp_dir = tempfile.mkdtemp(prefix="cfg-toast-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()
        library.set_base_resolution(1280, 720)

        page = config_page.ConfigPage(library)
        theme.install(page)
        page.resize(1000, 620)
        page.show()
        for _ in range(6):
            self.app.processEvents()
        self.addCleanup(page.deleteLater)
        return page

    def test_fail_shows_error_toast(self):
        """失败提示必须是错误级浮层（danger 色）。"""
        page = self._page()
        page._fail("无法读取图片文件 example.png")
        toasts = self.toasts(page)
        self.assertEqual(len(toasts), 1)
        self.assertEqual(toasts[0].tone, "danger")
        self.assertIn("无法读取图片文件", toasts[0].message_label.text())

    def test_fail_also_keeps_status_label(self):
        """失败文案同时留在状态栏，浮层消失后还能回看。"""
        page = self._page()
        page._fail("导入失败")
        self.assertIn("导入失败", page.status_label.text())

    def test_done_shows_success_toast(self):
        """成功提示是成功级浮层（success 色）。"""
        page = self._page()
        page._done("已创建「靶场入口」")
        toasts = self.toasts(page)
        self.assertEqual(len(toasts), 1)
        self.assertEqual(toasts[0].tone, "success")

    def test_done_carries_detail(self):
        """补充说明（如分辨率软提醒）随成功提示一起显示。

        只留成功文案的话，用户永远看不到「这次是缩放过的」——而那正是解释
        「为什么识别得分略低」的唯一线索。
        """
        page = self._page()
        page._done("已创建「靶场入口」", "画面尺寸与基准不同，识别时会自动缩放")
        body = self.toasts(page)[0].message_label.text()
        self.assertIn("已创建", body)
        self.assertIn("自动缩放", body)


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class TestFlowPageToasts(_ToastCase):
    """流转页：试走结果弹浮层，路径描述不弹。"""

    def _page(self):
        """造一个空的流转页。

        Returns:
            `FlowPage` 实例（已显示）。
        """
        from ok.ui.qt.kit import theme

        from extensions.capabilities.scene_manager.pages import flow_page

        temp_dir = tempfile.mkdtemp(prefix="flow-toast-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()
        library.set_base_resolution(1280, 720)

        page = flow_page.FlowPage(library, build_bridge=lambda: None)
        theme.install(page)
        page.resize(900, 600)
        page.show()
        for _ in range(6):
            self.app.processEvents()
        self.addCleanup(page.deleteLater)
        return page

    def test_missing_route_shows_warning(self):
        """没选起止场景就点预览，弹警告浮层。"""
        page = self._page()
        page._on_preview()
        toasts = self.toasts(page)
        self.assertEqual(len(toasts), 1)
        self.assertEqual(toasts[0].tone, "warning")
        self.assertIn("起点与目标", toasts[0].message_label.text())

    def test_tip_only_shows_toast(self):
        """一次性操作结果只弹浮层，不写状态栏。

        状态栏留给建图统计与路径预览；「边已确认」这类结果留在页面上没有用途，
        还会把统计行顶掉。
        """
        page = self._page()
        before = page.status_label.text()
        page._tip("试走成功", "success", "第一步")
        self.assertEqual(page.status_label.text(), before)
        self.assertEqual(len(self.toasts(page)), 1)


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class TestDraftsPageToasts(_ToastCase):
    """草稿页：未选中就操作时弹提示。"""

    def test_no_selection_shows_toast(self):
        """没选草稿就点确认，弹警告浮层而不是只写状态栏。"""
        from ok.ui.qt.kit import theme

        from extensions.capabilities.scene_manager.pages import drafts_page

        temp_dir = tempfile.mkdtemp(prefix="draft-toast-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()

        page = drafts_page.DraftsPage(library)
        theme.install(page)
        page.resize(900, 600)
        page.show()
        for _ in range(6):
            self.app.processEvents()
        self.addCleanup(page.deleteLater)

        page._on_confirm_selected()

        toasts = self.toasts(page)
        self.assertEqual(len(toasts), 1, "未选中就操作必须有可见提示")
        self.assertIn("选择草稿", toasts[0].message_label.text())


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class TestExploreResultDisplay(_ToastCase):
    """探索结果叠加在探索页的候选画布上，路由页只显示最终结果。

    **用户报的问题**：结果单独放一个页签、用表格看不直观。现在探索页用
    「已确认路由图 + 待确认/新发现叠加」的画布展示，应用后才进入路由页。
    """

    def _page(self):
        """造一个探索页（候选池）。

        Returns:
            `ExplorePage` 实例（已显示）。
        """
        from ok.ui.qt.kit import theme

        from extensions.capabilities.scene_manager.pages import explore_page

        temp_dir = tempfile.mkdtemp(prefix="explore-result-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()
        library.set_base_resolution(1280, 720)

        page = explore_page.ExplorePage(library, on_explore=lambda: None)
        # 摘要改为模态弹窗：测试里替换成收集文本，避免 exec() 阻塞用例。
        self._dialogs = []
        page._show_result_dialog = self._dialogs.append
        theme.install(page)
        page.resize(900, 620)
        page.show()
        for _ in range(4):
            self.app.processEvents()
        self.addCleanup(page.deleteLater)
        return page

    def _dialog_text(self):
        """返回最近一次探索摘要弹窗的文本。"""
        return self._dialogs[-1] if self._dialogs else ""

    def _result(self, new_scenes=None, problems=None):
        """造一个探索结果。

        Args：
            new_scenes：发现的新场景 id 列表。
            problems：要附带的问题事件（`(kind, detail)` 列表）。

        Returns：
            `explore.ExploreResult` 实例。
        """
        from extensions.capabilities.scene_manager import explore

        events = [explore.ExploreEvent(kind, "scene-0001", detail, 1.0) for kind, detail in (problems or [])]
        return explore.ExploreResult(
            stop_reason=explore.STOP_COMPLETED,
            visited=["scene-0001"],
            new_scenes=list(new_scenes or []),
            edges=[],
            events=events,
            elapsed=2.0,
        )

    def test_shows_summary(self):
        """摘要写进流转页，不只是日志。"""
        page = self._page()
        page.show_explore_result(self._result())
        self.assertIn("探索结束", self._dialog_text())

    def test_lists_new_drafts(self):
        """列出新发现的草稿，用户据此去草稿页确认。"""
        page = self._page()
        page.show_explore_result(self._result(new_scenes=["draft-0001", "draft-0002"]))
        text = self._dialog_text()
        self.assertIn("draft-0001", text)
        self.assertIn("draft-0002", text)

    def test_says_so_when_no_new_scene(self):
        """没有新场景时明确说出来——否则用户会去草稿页找那不存在的草稿。"""
        page = self._page()
        page.show_explore_result(self._result())
        self.assertIn("没有发现新场景", self._dialog_text())

    def test_shows_why_exploration_stopped_early(self):
        """**必须显示提前结束的原因**。

        `stop_reason` 是 `completed` 并不等于「探遍了」——回退失败、单场景点击
        超限都会让探索提前结束却仍记为 `completed`。只显示摘要的话，用户会以为
        「这个场景本来就没有别的区域」。
        """
        page = self._page()
        page.show_explore_result(
            self._result(
                problems=[
                    ("warn", "该场景没有返回区域也没有入边，无法回退"),
                    ("skip", "已离开该场景且无法回来，跳过它的剩余区域"),
                ]
            )
        )
        text = self._dialog_text()
        self.assertIn("无法回退", text)
        self.assertIn("注意", text)
        self.assertIn("scene-0001", text, "提示要带场景 id，否则看不出是哪里")

    def test_groups_identical_problems(self):
        """同一原因重复出现时只占一行，并带场景清单。

        探索每次回退失败都会发一条相同的 warn；七条一模一样的话会把结果区
        占满，也让人误以为最终失败。
        """
        from extensions.capabilities.scene_manager import explore

        events = [explore.ExploreEvent("warn", f"scene-{index:04d}", "无法回退", 1.0) for index in range(1, 8)]
        result = explore.ExploreResult(
            stop_reason=explore.STOP_COMPLETED,
            visited=[],
            new_scenes=[],
            edges=[],
            events=events,
            elapsed=1.0,
        )
        page = self._page()
        page.show_explore_result(result)
        text = self._dialog_text()
        self.assertEqual(text.count("无法回退"), 1, "同一原因只应出现一行")
        self.assertIn("等 7 个场景", text)

    def test_result_dialog_is_confirm_only(self):
        """摘要弹窗只保留确认按钮，确认后关闭（没有取消/超时兜底）。"""
        from unittest import mock

        page = self._page()
        # `_page` 把弹窗替换成了文本收集器；这里恢复真实实现再断言调用。
        del page._show_result_dialog
        with mock.patch("ok.ui.qt.kit.dialogs.Dialog.exec", return_value=True) as exec_mock:
            page._show_result_dialog("探索结束（completed）")
        exec_mock.assert_called_once()

    def test_progress_dialog_updates_and_interrupts(self):
        """进度弹窗实时刷新；点「中断」只发停止请求，不立即关窗。"""
        page = self._page()
        calls = []
        page.show_progress(lambda: calls.append(1))
        self.assertIsNotNone(page._progress_dialog)
        self.assertTrue(page._progress_dialog.isVisible())
        page.update_progress(
            {
                "visited": 3,
                "new_scenes": 1,
                "edges": 4,
                "elapsed": 12.3,
                "event": "点击 区域1",
            }
        )
        text = page._progress_dialog.message_label.text()
        self.assertIn("12.3", text)
        self.assertIn("点击 区域1", text)
        page._progress_dialog.ok_button.click()
        self.assertEqual(calls, [1])
        self.assertTrue(page._progress_dialog.isVisible(), "中断后要等任务真正结束才关窗")
        self.assertFalse(page._progress_dialog.ok_button.isEnabled())
        page.hide_progress()
        self.assertIsNone(page._progress_dialog)

    def test_progress_wraps_long_event(self):
        """过长的探索事件按字数折行，不会在弹窗右侧被裁掉。"""
        page = self._page()
        page.show_progress(lambda: None)
        before = page._progress_dialog.message_label.minimumHeight()
        long_event = "返回路径未知，先探 关卡选择·区域2 试图找靶场入口；" * 3
        page.update_progress({"visited": 5, "new_scenes": 0, "edges": 5, "elapsed": 6.5, "event": long_event})
        text = page._progress_dialog.message_label.text()
        lines = text.splitlines()
        self.assertGreater(len(lines), 2, "长事件应折成多行")
        for line in lines[1:]:
            self.assertLessEqual(len(line), 26)
        self.assertGreater(
            page._progress_dialog.message_label.minimumHeight(),
            before,
            "行数变多后消息区应加高",
        )
        page.hide_progress()

    def test_progress_timer_ticks_without_events(self):
        """没有新事件时计时器也要持续走，刷新间隔 100ms。"""
        page = self._page()
        page.show_progress(lambda: None)
        self.assertEqual(page._progress_timer.interval(), 100)
        page._progress_started -= 3.2
        page._render_progress()
        text = page._progress_dialog.message_label.text()
        self.assertIn("3.2s", text)
        page.hide_progress()

    def test_result_confirm_triggers_auto_layout(self):
        """结果弹窗点确认后自动执行一次布局。"""
        page = self._page()
        calls = []
        page._show_result_dialog = lambda message: True
        page._on_auto_layout = lambda: calls.append(1)
        page.show_explore_result(self._result())
        self.assertEqual(calls, [1])

    def test_clear_result(self):
        """传 None 时清掉「本次新发现」标记，画布不再高亮。"""
        page = self._page()
        page._new_edge_keys = {("scene-0001", "go")}
        page.show_explore_result(None)
        self.assertEqual(page._new_edge_keys, set())
        self.assertEqual(page.canvas._new_edge_keys, set())

    def _library_with_explored_edge(self, page):
        """给探索页的库造两个场景与一条待确认边。"""
        from extensions.capabilities.scene_manager import model

        source = model.Scene(scene_id="scene-0001", name="入口")
        source.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        page.library.save_scene(source)
        page.library.save_scene(model.Scene(scene_id="scene-0002", name="目标"))
        page.library.set_route("scene-0001", "go", "scene-0002", source="explored")

    def test_pending_edge_drawn_on_candidate_canvas(self):
        """待确认边画在探索页画布上——候选不再进路由页。"""
        page = self._page()
        self._library_with_explored_edge(page)
        page.reload()
        self.assertEqual(len(page.canvas._edges), 1, "候选边必须画出来")
        self.assertEqual(len(page.library.routes.routable_routes()), 0, "但还不能寻路")

    def test_reload_keeps_node_positions_stable(self):
        """刷新画布不能把布局坐标再乘一次间距。

        `remember_positions` 给的已经是画布坐标；再次乘 `NODE_GAP` 会把节点
        送到几万像素外，视图自适应缩到极小后整块画布看起来是空的——用户报的
        「探索完成后画布看不到内容」就是这个重复缩放。
        """
        page = self._page()
        self._library_with_explored_edge(page)
        page.reload()
        before = {scene_id: (node.pos().x(), node.pos().y()) for scene_id, node in page.canvas._nodes.items()}
        page.reload()
        after = {scene_id: (node.pos().x(), node.pos().y()) for scene_id, node in page.canvas._nodes.items()}

        self.assertEqual(set(before), set(after))
        for scene_id, point in before.items():
            self.assertAlmostEqual(after[scene_id][0], point[0], places=1)
            self.assertAlmostEqual(after[scene_id][1], point[1], places=1)
        self.assertGreater(page.canvas.transform().m11(), 0.0, "视图缩放不能是 0")

    def test_apply_all_confirms_new_edges(self):
        """「应用全部」把本次新发现升为 confirmed。"""
        from extensions.capabilities.scene_manager import explore

        page = self._page()
        self._library_with_explored_edge(page)
        result = explore.ExploreResult(
            stop_reason=explore.STOP_COMPLETED,
            visited=["scene-0001"],
            new_scenes=[],
            edges=[("scene-0001", "go", "scene-0002", 1)],
            events=[],
            elapsed=1.0,
        )
        page.show_explore_result(result)
        with mock.patch("ok.ui.qt.kit.confirm", lambda *a, **k: True):
            page._on_apply_all()
        page.library.load_routes()
        self.assertEqual(page.library.routes.get("scene-0001", "go").source, "confirmed")


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class TestEditorToasts(_ToastCase):
    """场景编辑器弹窗内的一次性提示走浮层。"""

    def _dialog(self):
        """造一个带场景的编辑器弹窗。

        Returns:
            `SceneEditorDialog` 实例（已显示）。
        """
        from ok.ui.qt.kit import theme

        from extensions.capabilities.scene_manager import model, shapes
        from extensions.capabilities.scene_manager.pages import scene_editor

        temp_dir = tempfile.mkdtemp(prefix="editor-toast-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        library = store.SceneLibrary(os.path.join(temp_dir, "scenes"))
        library.ensure_layout()
        library.set_base_resolution(800, 600)
        scene = library.new_scene("测试")
        scene.features.append(model.Feature(name="f1", shape=shapes.Shape.rectangle(0.1, 0.1, 0.3, 0.3)))
        library.save_scene(scene)
        import numpy as np

        image = np.zeros((600, 800, 3), dtype=np.uint8)
        image[::4] = 255
        library.save_base_image(scene.scene_id, image)

        dialog = scene_editor.SceneEditorDialog(library, scene_id=scene.scene_id)
        theme.install(dialog)
        dialog.show()
        for _ in range(6):
            self.app.processEvents()
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_validation_error_shows_toast(self):
        """校验失败（特征名重复）弹错误浮层。"""
        dialog = self._dialog()
        dialog._notify("特征名「f1」已存在", "error", "无法创建特征")
        toasts = self.toasts(dialog)
        self.assertEqual(len(toasts), 1)
        self.assertEqual(toasts[0].tone, "danger")
        self.assertEqual(toasts[0].title_label.text(), "无法创建特征")

    def test_toast_attaches_to_dialog_not_main_window(self):
        """浮层挂在**弹窗自己**的窗口上，不会跑到主窗口。

        `show_toast` 挂到传入控件的 `window()`；模态 QDialog 自身就是一个窗口，
        因此编辑器里的提示不会跑到主界面顶部去。
        """
        dialog = self._dialog()
        dialog._notify("测试提示", "info")
        toasts = self.toasts(dialog)
        self.assertEqual(len(toasts), 1)
        self.assertIs(toasts[0].window(), dialog.window())

    def test_status_label_still_updated(self):
        """浮层之外状态栏也写，弹窗关闭前还能回看。"""
        dialog = self._dialog()
        dialog._notify("框选无效：区域退化", "error")
        self.assertIn("框选无效", dialog.status_label.text())

    def test_mouse_move_does_not_spam_toasts(self):
        """**在画布上移动鼠标不能弹浮层**，只更新状态栏。

        这是一个真实缺陷的回归测试：取色回调 `_on_color_sampled` 由
        `mouseMoveEvent` 触发，鼠标每移动一像素调一次。曾把它改成浮层，结果
        鼠标划过画布时取色提示连弹，几秒内铺满屏幕。

        判据用「移动 N 次后浮层数为 0」——只看一次移动抓不到累积效应，而累积
        正是这个缺陷的表现。
        """
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        dialog = self._dialog()
        canvas = dialog.canvas

        for index in range(20):
            point = QPointF(20 + index * 3, 20 + index * 2)
            event = QMouseEvent(
                QEvent.Type.MouseMove,
                point,
                canvas.mapToGlobal(point.toPoint()),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            self.app.sendEvent(canvas, event)

        self.assertEqual(self.toasts(dialog), [], "鼠标移动不该弹浮层")
        self.assertIn("(", dialog.status_label.text(), "取色坐标应显示在状态栏")

    def test_color_sampled_writes_status_label(self):
        """取色回调直接调用时也只写状态栏，不弹浮层。"""
        dialog = self._dialog()
        dialog._on_color_sampled(10, 20, 0.1, 0.2, None)
        self.assertEqual(self.toasts(dialog), [])
        self.assertIn("10, 20", dialog.status_label.text())


@unittest.skipIf(QApplication is None, f"缺少 GUI 依赖：{QT_ERROR}")
class TestFlowEdgeMaintenance(_ToastCase):
    """场景路由页只维护**已生效**的边。

    候选边（`explored`）归探索页，路由页打开时看到的必须是最终结果。
    这组用例锁住：表格只列已生效边、画布不画待确认边、删除只删路由。
    """

    def _page(self):
        """造一个带两个场景与一条已生效边的路由页。

        Returns:
            `(page, library)` 二元组。
        """
        from ok.ui.qt.kit import theme

        from extensions.capabilities.scene_manager import model
        from extensions.capabilities.scene_manager.pages import flow_page

        temp_dir = tempfile.mkdtemp(prefix="flow-edge-")
        self.addCleanup(shutil.rmtree, temp_dir, True)
        library = store.SceneLibrary(temp_dir)
        library.ensure_layout()
        library.set_base_resolution(1280, 720)
        for scene_id in ("scene-0001", "scene-0002"):
            scene = model.Scene(scene_id=scene_id, name=scene_id)
            scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
            library.save_scene(scene)
        library.set_route("scene-0001", "go", "scene-0002", source=model.SOURCE_MANUAL)

        page = flow_page.FlowPage(library, build_bridge=lambda: None)
        theme.install(page)
        page.resize(980, 720)
        page.show()
        page.reload()
        for _ in range(6):
            self.app.processEvents()
        self.addCleanup(page.deleteLater)
        return page, library

    def test_active_edge_listed_in_table(self):
        """已生效边出现在表格里，来源列显示 manual。"""
        page, _library = self._page()
        self.assertEqual(page.edge_table.rowCount(), 1)
        self.assertEqual(page.edge_table.item(0, 3).text(), "manual")

    def test_pending_edge_hidden_from_routes_page(self):
        """待确认的候选边不进入路由页：表格不列、画布不画。"""
        page, library = self._page()
        library.set_route("scene-0002", "go", "scene-0001", source="explored")
        page.reload()
        for _ in range(4):
            self.app.processEvents()
        self.assertEqual(page.edge_table.rowCount(), 1, "只应列出已生效的那条")
        self.assertEqual(page.scene_graph.edge_count(), 1, "待确认边归探索页")

    def test_delete_edge_keeps_region(self):
        """删除边只标记工作副本，保存后才落盘；区域本身保留。"""
        page, library = self._page()
        page.edge_table.setCurrentCell(0, 0)
        page._on_delete_selected()
        for _ in range(4):
            self.app.processEvents()

        library.load_routes()
        self.assertEqual(len(library.routes), 1, "保存前不该落盘")
        page._on_save()
        library.load_routes()
        self.assertEqual(len(library.routes), 0, "保存后路由应被删除")
        scene = library.load_scene("scene-0001")
        self.assertIsNotNone(scene.find_area("go"), "区域不该被连带删除")

    def test_delete_without_selection_does_nothing(self):
        """没有选中边时 Delete 不删任何东西。"""
        page, library = self._page()
        page.edge_table.clearSelection()
        page.edge_table.setCurrentCell(-1, -1)
        page._on_delete_selected()
        library.load_routes()
        self.assertEqual(len(library.routes), 1)

    def test_scene_editor_no_longer_offers_target(self):
        """场景编辑器的区域对话框不再有「目标场景」「操作后等待」。"""
        import inspect

        from extensions.capabilities.scene_manager.pages import scene_editor

        source = inspect.getsource(scene_editor.SceneEditorDialog._edit_area_at)
        self.assertNotIn("Target Scene", source, "区域对话框不该再有目标场景")
        self.assertNotIn("Wait After Action", source, "区域对话框不该再有等待字段")

    def test_unknown_target_does_not_break_flow_page(self):
        """已生效边指向已删除场景时给出提示而不是整块白屏。"""
        page, library = self._page()
        library.set_route("scene-0001", "go", "scene-9999")
        page.reload()
        for _ in range(4):
            self.app.processEvents()
        self.assertTrue(self.toasts(page), "悬空边必须提示")

    def test_wait_edit_is_pending_until_save(self):
        """改等待只改工作副本，保存后才落盘。"""
        page, library = self._page()
        key = ("scene-0001", "go")
        with mock.patch.object(page, "_ask_wait", return_value=2.5):
            page._edit_wait(key)

        library.load_routes()
        self.assertAlmostEqual(library.routes.get(*key).after_wait, 1.0, msg="保存前不该落盘")
        page._on_save()
        library.load_routes()
        self.assertAlmostEqual(library.routes.get(*key).after_wait, 2.5)

    def test_reverse_link_creates_edge(self):
        """倒序连线：先定目标，再选来源场景与它的交互区域。"""
        page, _library = self._page()
        page._link = {"kind": "reverse", "target": "scene-0001"}
        with (
            mock.patch.object(page, "_ask_area", return_value="go"),
            mock.patch.object(page, "_ask_wait", return_value=1.0),
        ):
            page._on_link_completed("scene-0002")

        route = page._working.get("scene-0002", "go")
        self.assertIsNotNone(route, "应生成 scene-0002.go 的边")
        self.assertEqual(route.target_scene, "scene-0001")
        self.assertTrue(page._dirty(), "只改副本，等待保存")

    def test_relink_source_moves_edge(self):
        """拖来源端重连：旧键删除，新键指向原目标。"""
        page, library = self._page()
        scene = model.Scene(scene_id="scene-0003", name="scene-0003")
        scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        library.save_scene(scene)
        page.reload()
        page._working.set_target("scene-0001", "go", "scene-0003")
        page._link = {"kind": "relink_source", "key": ("scene-0001", "go"), "target": "scene-0003"}
        with (
            mock.patch.object(page, "_ask_area", return_value="go"),
            mock.patch.object(page, "_ask_wait", return_value=1.0),
        ):
            page._on_link_completed("scene-0002")

        self.assertIsNone(page._working.get("scene-0001", "go"), "旧键应被移除")
        route = page._working.get("scene-0002", "go")
        self.assertIsNotNone(route)
        self.assertEqual(route.target_scene, "scene-0003")

    def test_canvas_hover_uses_curve_hit(self):
        """悬停高亮按绘制曲线命中，而不是按直线/仅描边。"""
        page, _library = self._page()
        page.reload()
        for _ in range(4):
            self.app.processEvents()
        geometry = page.canvas._edge_geometries[0]
        point = geometry["path"].pointAtPercent(0.5)
        page.canvas._update_cursor(page.canvas.mapFromScene(point))
        self.assertEqual(page.canvas._hover_edge, geometry["key"])

    def test_canvas_detects_grabbed_end(self):
        """靠近来源端判 out、靠近目标端判 in。"""
        page, _library = self._page()
        page.reload()
        for _ in range(4):
            self.app.processEvents()
        page.canvas._fit_scene()
        geometry = page.canvas._edge_geometries[0]
        key = geometry["key"]
        start = page.canvas.mapFromScene(geometry["path"].pointAtPercent(0.02))
        end = page.canvas.mapFromScene(geometry["path"].pointAtPercent(0.98))
        self.assertEqual(page.canvas._edge_end_at(key, start), "out")
        self.assertEqual(page.canvas._edge_end_at(key, end), "in")

    def test_layout_roundtrip(self):
        """节点位置写入 layout.json，重置后清空。"""
        _page, library = self._page()
        library.save_layout({"scene-0001": (12.5, -30.0)})
        self.assertEqual(library.load_layout(), {"scene-0001": (12.5, -30.0)})
        library.clear_layout()
        self.assertEqual(library.load_layout(), {})

    def test_parallel_reverse_edges_do_not_overlap(self):
        """A→B 与 B→A 的曲线必须分居两侧，不能重合。"""
        page, library = self._page()
        library.set_route("scene-0002", "go", "scene-0001", source=model.SOURCE_MANUAL)
        page.reload()
        for _ in range(4):
            self.app.processEvents()
        paths = [item["path"] for item in page.canvas._edge_geometries]
        self.assertEqual(len(paths), 2, "应画出两条反向边")
        first = paths[0].pointAtPercent(0.5)
        second = paths[1].pointAtPercent(0.5)
        distance = math.hypot(first.x() - second.x(), first.y() - second.y())
        self.assertGreater(distance, 30.0, f"反向平行边应分居两侧，实际相距 {distance:.1f}")

    def test_auto_layout_keeps_nodes_apart(self):
        """自动布局后：有连线的节点用大间距，孤岛用小间距。"""
        from extensions.capabilities.scene_manager import flow_canvas

        page, _library = self._page()
        page.canvas.reset_layout()
        isolated = set(page.scene_graph.isolated_scenes())
        ids = list(page.canvas._nodes)
        for left in range(len(ids)):
            for right in range(left + 1, len(ids)):
                first = page.canvas._nodes[ids[left]].pos()
                second = page.canvas._nodes[ids[right]].pos()
                distance = math.hypot(first.x() - second.x(), first.y() - second.y())
                limit = (
                    flow_canvas.ISOLATED_MIN_DISTANCE
                    if ids[left] in isolated or ids[right] in isolated
                    else flow_canvas.MIN_NODE_DISTANCE
                )
                self.assertGreaterEqual(
                    distance,
                    limit - 0.5,
                    f"{ids[left]} 与 {ids[right]} 挨得太近：{distance:.1f}",
                )

    def test_isolated_gap_is_quarter_of_connected_gap(self):
        """有连线的通道 32，孤岛 16：圆间空隙各翻一倍。"""
        from extensions.capabilities.scene_manager import flow_canvas

        connected_gap = flow_canvas.MIN_NODE_DISTANCE - flow_canvas.NODE_RADIUS * 2
        isolated_gap = flow_canvas.ISOLATED_MIN_DISTANCE - flow_canvas.NODE_RADIUS * 2
        self.assertAlmostEqual(connected_gap, 32.0, places=6, msg="有连线通道应从 16 翻倍到 32")
        self.assertAlmostEqual(isolated_gap, 16.0, places=6, msg="孤岛通道应从 8 翻倍到 16")

    def test_relax_positions_uses_isolated_distance(self):
        """松弛时孤岛对用更小间距，主图节点之间仍用大间距。"""
        from extensions.capabilities.scene_manager import flow_canvas

        points = {"a": (0.0, 0.0), "b": (10.0, 0.0), "island": (40.0, 0.0)}
        result = flow_canvas._relax_positions(
            points,
            flow_canvas.MIN_NODE_DISTANCE,
            {"island"},
            flow_canvas.ISOLATED_MIN_DISTANCE,
        )
        distances = {
            "ab": math.hypot(result["a"][0] - result["b"][0], result["a"][1] - result["b"][1]),
            "island-b": math.hypot(result["island"][0] - result["b"][0], result["island"][1] - result["b"][1]),
        }
        self.assertGreaterEqual(distances["ab"], flow_canvas.MIN_NODE_DISTANCE - 0.5)
        self.assertGreaterEqual(distances["island-b"], flow_canvas.ISOLATED_MIN_DISTANCE - 0.5)
        self.assertLess(distances["island-b"], flow_canvas.MIN_NODE_DISTANCE)

    def test_pack_isolated_makes_even_grid(self):
        """孤岛排在主图下方，行列间距完全一致，主图节点不动。"""
        from extensions.capabilities.scene_manager import flow_canvas

        points = {
            "a": (0.0, 0.0),
            "b": (300.0, 0.0),
            "i1": (999.0, 999.0),
            "i2": (-500.0, 400.0),
            "i3": (0.0, -900.0),
        }
        isolated = {"i1", "i2", "i3"}
        result = flow_canvas._pack_isolated(points, isolated, flow_canvas.ISOLATED_MIN_DISTANCE)
        step = flow_canvas.ISOLATED_MIN_DISTANCE * 1.05
        self.assertAlmostEqual(result["i2"][0] - result["i1"][0], step, places=3)
        self.assertAlmostEqual(result["i2"][1], result["i1"][1], places=3)
        self.assertAlmostEqual(result["i3"][0], result["i1"][0], places=3)
        self.assertAlmostEqual(result["i3"][1] - result["i1"][1], step, places=3)
        self.assertGreater(result["i1"][1], max(points["a"][1], points["b"][1]), "孤岛应在主图下方")
        self.assertEqual(result["a"], points["a"], "主图节点不该被移动")
        self.assertEqual(result["b"], points["b"], "主图节点不该被移动")

    def test_zoom_is_clamped(self):
        """滚轮缩放不能越过上下限。"""
        from extensions.capabilities.scene_manager import flow_canvas

        page, _library = self._page()
        for _ in range(40):
            page.canvas.wheelEvent(_wheel_event(page.canvas, 120))
        self.assertLessEqual(page.canvas.transform().m11(), flow_canvas.ZOOM_MAX + 1e-6)
        for _ in range(80):
            page.canvas.wheelEvent(_wheel_event(page.canvas, -120))
        self.assertGreaterEqual(page.canvas.transform().m11(), flow_canvas.ZOOM_MIN - 1e-6)

    def test_text_hidden_when_zoomed_out(self):
        """缩小到阈值以下时，节点文字与边标签都隐藏。"""
        from PySide6.QtGui import QTransform

        page, _library = self._page()
        page.canvas.setTransform(QTransform().scale(0.3, 0.3))
        page.canvas._sync_text_visibility()
        self.assertFalse(any(node.show_label for node in page.canvas._nodes.values()))
        labels = [item for item in page.canvas._scene.items() if item.data(1) == "label"]
        self.assertTrue(labels, "边标签应存在，只是被隐藏")
        self.assertTrue(all(not item.isVisible() for item in labels))

        page.canvas.setTransform(QTransform())
        page.canvas._sync_text_visibility()
        self.assertTrue(all(node.show_label for node in page.canvas._nodes.values()))
        self.assertTrue(all(item.isVisible() for item in labels))

    def test_auto_layout_button_triggers_relayout(self):
        """顶部自动布局按钮存在，点击后重写已保存的位置。"""
        page, library = self._page()
        library.save_layout({"scene-0001": (10.0, 10.0), "scene-0002": (20.0, 20.0)})
        page.auto_layout_button.click()
        for _ in range(3):
            self.app.processEvents()
        self.assertNotEqual(library.load_layout().get("scene-0001"), (10.0, 10.0))

    def test_edge_label_omits_target_name(self):
        """边标签只写来源场景与区域，不重复标目标场景名。"""
        page, _library = self._page()
        labels = [
            item.text() for item in page.canvas._scene.items() if item.data(1) == "label" and hasattr(item, "text")
        ]
        self.assertTrue(any("go" in text for text in labels))
        self.assertFalse(any("→" in text for text in labels), "箭头端的目标标签应已移除")

    def test_canvas_hides_scrollbars(self):
        """画布不显示横竖滚动条，平移交给中键 / Alt+左键。"""
        from PySide6.QtCore import Qt

        page, _library = self._page()
        self.assertEqual(page.canvas.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(page.canvas.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def test_reset_view_centers_content(self):
        """重置视图后，视图中心落在全部内容的包围盒中心。"""
        page, _library = self._page()
        page.canvas.reset_view()
        for _ in range(3):
            self.app.processEvents()
        content = page.canvas._scene.itemsBoundingRect()
        center = page.canvas.mapToScene(page.canvas.viewport().rect().center())
        self.assertLess(abs(center.x() - content.center().x()), 5.0)
        self.assertLess(abs(center.y() - content.center().y()), 5.0)

    def test_explore_page_has_reset_view_button(self):
        """探索页有重置视图按钮，点击不改变节点位置。"""
        from extensions.capabilities.scene_manager.pages import explore_page

        _page, library = self._page()
        explore = explore_page.ExplorePage(library, on_explore=lambda: None)
        self.addCleanup(explore.deleteLater)
        explore.reset_view_button.click()
        for _ in range(3):
            self.app.processEvents()
        self.assertTrue(explore.canvas._nodes, "重置视图不应清空画布")

    def test_layout_stretches_to_wide_viewport(self):
        """横向视口里，竖长布局会被轻微横向拉伸而不是留出大片空白。"""
        from extensions.capabilities.scene_manager import flow_canvas

        points = {"a": (-100.0, -300.0), "b": (0.0, 0.0), "c": (100.0, 300.0)}
        stretched = flow_canvas._stretch_to_viewport(points, 1200, 400)
        xs = [point[0] for point in stretched.values()]
        ys = [point[1] for point in stretched.values()]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        self.assertGreater(width, 250.0, "横轴应被轻微拉开")
        self.assertLessEqual(width / 200.0, flow_canvas.MAX_LAYOUT_STRETCH + 1e-6)
        self.assertGreater(width / height, 0.4, "宽高比应比原来的 1:3 更接近视口")

    def test_scene_rect_allows_pan_when_zoomed_out(self):
        """缩到最小时场景矩形仍足够大，平移不被内容边界挡住。"""
        page, _library = self._page()
        page.canvas.reset_view()
        self.assertGreater(page.canvas.sceneRect().width(), 100000)

    def test_wheel_zoom_uses_smaller_step(self):
        """滚轮每格约 8%，比原先的 15% 更平缓。"""
        page, _library = self._page()
        before = page.canvas.transform().m11()
        page.canvas.wheelEvent(_wheel_event(page.canvas, 120))
        after = page.canvas.transform().m11()
        self.assertAlmostEqual(after / before, 1.08, places=3)

    def test_zoom_label_shows_current_percent(self):
        """右下角缩放比例与实际变换一致。"""
        page, _library = self._page()
        page.canvas.reset_view()
        percent = round(page.canvas.transform().m11() * 100)
        self.assertEqual(page.canvas._zoom_label.text(), f"{percent}%")

    def test_auto_layout_uses_max_zoom_for_small_graph(self):
        """自动布局时单节点图按 ZOOM_MAX 顶格放大，不被默认 1.6 限制。"""
        from extensions.capabilities.scene_manager import flow_canvas, graph

        canvas = flow_canvas.FlowCanvas(editable=False)
        self.addCleanup(canvas.deleteLater)
        canvas.resize(900, 600)
        canvas.show()
        scene = model.Scene(scene_id="scene-0001", name="only")
        scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        canvas.set_graph(graph.SceneGraph.build([scene]))
        canvas.reset_layout()
        for _ in range(3):
            self.app.processEvents()
        self.assertGreater(canvas.transform().m11(), flow_canvas.FIT_MAX_SCALE)
        self.assertEqual(
            canvas._zoom_label.text(),
            f"{round(canvas.transform().m11() * 100)}%",
        )

    def test_explore_page_has_auto_layout_button(self):
        """探索页也有自动布局，并保存重排后的位置。"""
        from extensions.capabilities.scene_manager.pages import explore_page

        _page, library = self._page()
        library.save_layout({"scene-0001": (5.0, 5.0), "scene-0002": (6.0, 6.0)})
        explore = explore_page.ExplorePage(library, on_explore=lambda: None)
        self.addCleanup(explore.deleteLater)
        explore.auto_layout_button.click()
        for _ in range(3):
            self.app.processEvents()
        self.assertNotEqual(library.load_layout().get("scene-0001"), (5.0, 5.0))

    def test_explore_page_restart_settings_roundtrip(self):
        """探索页的重启开关与数值写入 meta，并在重建页面时恢复。"""
        from extensions.capabilities.scene_manager.pages import explore_page

        _page, library = self._page()
        explore = explore_page.ExplorePage(library, on_explore=lambda: None)
        self.addCleanup(explore.deleteLater)
        # 参数控件住在懒创建的弹窗里：先建出来再操作。
        explore._settings_dialog = explore._build_settings_dialog()
        explore._load_settings()
        explore.auto_launch_switch.setChecked(True)
        explore.restart_switch.setChecked(True)
        explore.max_restarts_input.setValue(2)
        explore.restart_wait_input.setValue(12.0)
        for _ in range(3):
            self.app.processEvents()
        meta = library.load_meta()
        self.assertTrue(meta.auto_launch)
        self.assertTrue(meta.restart_on_incomplete)
        self.assertEqual(meta.max_restarts, 2)
        self.assertAlmostEqual(meta.restart_wait, 12.0)

        rebuilt = explore_page.ExplorePage(library, on_explore=lambda: None)
        self.addCleanup(rebuilt.deleteLater)
        rebuilt._settings_dialog = rebuilt._build_settings_dialog()
        rebuilt._load_settings()
        self.assertTrue(rebuilt.auto_launch_switch.isChecked())
        self.assertTrue(rebuilt.restart_switch.isChecked())
        self.assertEqual(rebuilt.max_restarts_input.value(), 2)
        self.assertAlmostEqual(rebuilt.restart_wait_input.value(), 12.0)

    def test_marquee_selects_edges(self):
        """框选矩形经过连线时选中该边。"""
        from PySide6.QtCore import QPoint

        page, _library = self._page()
        for _ in range(4):
            self.app.processEvents()
        canvas = page.canvas
        geometry = canvas._edge_geometries[0]
        middle = canvas.mapFromScene(geometry["path"].pointAtPercent(0.5))
        _mouse_drag(canvas, middle + QPoint(-14, -14), middle + QPoint(14, 14))
        self.assertIn(geometry["key"], canvas.selected_edges())

    def test_marquee_ignores_corner_of_node(self):
        """框到节点方框的左外侧空白时，不该选中节点。"""
        from PySide6.QtCore import QPoint

        from extensions.capabilities.scene_manager import flow_canvas

        page, _library = self._page()
        for _ in range(4):
            self.app.processEvents()
        canvas = page.canvas
        node = canvas._nodes["scene-0001"]
        center = canvas.mapFromScene(node.pos())
        radius = int(canvas.transform().m11() * flow_canvas.NODE_RADIUS)
        # 方框左上角但落在圆外：x、y 各偏移 0.95R。
        corner = center + QPoint(int(-radius * 0.95), int(-radius * 0.95))
        _mouse_drag(canvas, corner + QPoint(-3, -3), corner + QPoint(3, 3))
        self.assertFalse(node.isSelected(), "圆外空白不应命中节点")

    def test_marquee_selects_multiple_edges(self):
        """一次框选可以把多条边一起选中。"""
        from PySide6.QtCore import QPoint

        page, library = self._page()
        scene = model.Scene(scene_id="scene-0003", name="scene-0003")
        scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        library.save_scene(scene)
        library.set_route("scene-0002", "go", "scene-0003", source=model.SOURCE_MANUAL)
        page.reload()
        for _ in range(4):
            self.app.processEvents()
        canvas = page.canvas
        rect = canvas._scene.itemsBoundingRect()
        top_left = canvas.mapFromScene(rect.topLeft()) + QPoint(-20, -20)
        bottom_right = canvas.mapFromScene(rect.bottomRight()) + QPoint(20, 20)
        _mouse_drag(canvas, top_left, bottom_right)
        self.assertGreaterEqual(len(canvas.selected_edges()), 2)

    def test_reduce_crossings_removes_intersection(self):
        """贪心交换能把一个明显交叉的布局改成不交叉。"""
        from extensions.capabilities.scene_manager import flow_canvas

        points = {"a": (0.0, 0.0), "b": (100.0, 100.0), "c": (0.0, 100.0), "d": (100.0, 0.0)}
        edges = [("a", "b"), ("c", "d")]
        self.assertEqual(flow_canvas._count_crossings(points, edges), 1)
        result = flow_canvas._reduce_crossings(points, edges)
        self.assertEqual(flow_canvas._count_crossings(result, edges), 0)

    def test_edge_label_has_no_background(self):
        """边标签只有文字，不再画淡白色底框。"""
        from PySide6.QtWidgets import QGraphicsRectItem

        page, _library = self._page()
        labels = [item for item in page.canvas._scene.items() if item.data(1) == "label"]
        self.assertTrue(labels, "场景里应有边标签")
        self.assertFalse(
            any(isinstance(item, QGraphicsRectItem) for item in labels),
            "标签不应再带背景矩形",
        )

    def test_edge_label_is_display_only(self):
        """边标签仅展示：不携带边键，点它不会选中边。"""
        page, _library = self._page()
        labels = [item for item in page.canvas._scene.items() if item.data(1) == "label"]
        self.assertTrue(labels, "场景里应有边标签")
        label = labels[0]
        self.assertIsNone(label.data(0), "标签不应携带边键")
        point = page.canvas.mapFromScene(label.sceneBoundingRect().center())
        self.assertIsNone(page.canvas._edge_at(point), "点标签不应命中边")

    def test_edge_label_follows_flow_direction(self):
        """标签贴起点端：左→右左对齐、右→左右对齐、上→下顶边对齐。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QFont, QPainterPath
        from PySide6.QtWidgets import QGraphicsSimpleTextItem

        page, _library = self._page()
        canvas = page.canvas
        label = QGraphicsSimpleTextItem("结算 · 区域1")
        font = QFont()
        font.setPointSize(7)
        label.setFont(font)
        width = label.boundingRect().width()
        height = label.boundingRect().height()

        rightward = QPainterPath(QPointF(100, 100))
        rightward.lineTo(QPointF(300, 100))
        pos = canvas._edge_label_pos(label, rightward)
        self.assertAlmostEqual(pos.x(), 110 + 3, places=3, msg="左→右时左缘贴起点")
        self.assertLess(pos.y(), 100, "文字应落在线条上方")

        leftward = QPainterPath(QPointF(300, 100))
        leftward.lineTo(QPointF(100, 100))
        pos = canvas._edge_label_pos(label, leftward)
        self.assertAlmostEqual(pos.x() + width, 290 - 3, places=3, msg="右→左时右缘贴起点")

        downward = QPainterPath(QPointF(100, 100))
        downward.lineTo(QPointF(100, 300))
        pos = canvas._edge_label_pos(label, downward)
        self.assertAlmostEqual(pos.y(), 110 + 2, places=3, msg="上→下时顶边贴起点")
        self.assertGreater(pos.x(), 100, "文字应落在线条右侧")

        upward = QPainterPath(QPointF(100, 300))
        upward.lineTo(QPointF(100, 100))
        pos = canvas._edge_label_pos(label, upward)
        self.assertAlmostEqual(pos.y() + height, 290 - 2, places=3, msg="下→上时底边贴起点")

    def test_clear_all_edges_clears_working_copy(self):
        """「清除全部连线」清空工作副本，保存前处于未保存状态。"""
        page, library = self._page()
        scene = model.Scene(scene_id="scene-0003", name="scene-0003")
        scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        library.save_scene(scene)
        library.set_route("scene-0002", "go", "scene-0003", source=model.SOURCE_MANUAL)
        page.reload()
        self.assertEqual(len(page._working.routable_routes()), 2)
        with mock.patch("ok.ui.qt.kit.confirm", return_value=True):
            page._on_clear_edges()
        self.assertEqual(len(page._working), 0)
        self.assertTrue(page._dirty(), "清空后应处于未保存状态")

    def test_clear_all_edges_can_be_cancelled(self):
        """确认框点取消时不清空。"""
        page, _library = self._page()
        before = len(page._working)
        with mock.patch("ok.ui.qt.kit.confirm", return_value=False):
            page._on_clear_edges()
        self.assertEqual(len(page._working), before)

    def test_menu_delete_applies_to_selection(self):
        """多选后右键删除作用于全部选中边，而不是只删触发菜单的那条。"""
        page, library = self._page()
        scene = model.Scene(scene_id="scene-0003", name="scene-0003")
        scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        library.save_scene(scene)
        library.set_route("scene-0002", "go", "scene-0003", source=model.SOURCE_MANUAL)
        page.reload()
        keys = {("scene-0001", "go"), ("scene-0002", "go")}
        page._selected_edges = set(keys)
        targets = page._menu_targets(("scene-0001", "go"))
        self.assertEqual(sorted(targets), sorted(keys), "多选时作用集合应是全部选中边")
        page._delete_edges(targets)
        self.assertEqual(len(page._working.routable_routes()), 0)

    def test_menu_set_wait_applies_to_selection(self):
        """多选后「改等待」一次改全部选中边。"""
        page, library = self._page()
        scene = model.Scene(scene_id="scene-0003", name="scene-0003")
        scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        library.save_scene(scene)
        library.set_route("scene-0002", "go", "scene-0003", source=model.SOURCE_MANUAL)
        page.reload()
        keys = {("scene-0001", "go"), ("scene-0002", "go")}
        page._selected_edges = set(keys)
        with mock.patch.object(page, "_ask_wait", return_value=3.0):
            page._edit_wait(("scene-0001", "go"), page._menu_targets(("scene-0001", "go")))
        for key in keys:
            route = page._working.get(*key)
            self.assertIsNotNone(route)
            self.assertAlmostEqual(route.after_wait, 3.0)

    def test_ctrl_click_selects_multiple_edges(self):
        """Ctrl+点击可以把多条连线加入选中集合。"""
        from PySide6.QtCore import Qt

        page, library = self._page()
        scene = model.Scene(scene_id="scene-0003", name="scene-0003")
        scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        library.save_scene(scene)
        library.set_route("scene-0002", "go", "scene-0003", source=model.SOURCE_MANUAL)
        page.reload()
        for _ in range(4):
            self.app.processEvents()
        geometries = page.canvas._edge_geometries
        self.assertEqual(len(geometries), 2)
        first = page.canvas.mapFromScene(geometries[0]["path"].pointAtPercent(0.45))
        second = page.canvas.mapFromScene(geometries[1]["path"].pointAtPercent(0.45))
        _mouse_press(page.canvas, first, Qt.KeyboardModifier.ControlModifier)
        _mouse_press(page.canvas, second, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(page.canvas.selected_edges()), 2)

    def test_drop_on_empty_deletes_edge(self):
        """拖拽重连后落在空白：工作副本里删除，保存前不落盘。"""
        page, library = self._page()
        key = ("scene-0001", "go")
        page.canvas._start_edge_relink(key, "in")
        page.canvas._drop_link()
        self.assertIsNone(page._working.get(*key), "拖到空白应标记删除")
        library.load_routes()
        self.assertIsNotNone(library.routes.get(*key), "保存前不该落盘")

    def _retarget_page(self):
        """造一个带三个场景与一条边的路由页。

        Returns：
            `(page, library)` 二元组。
        """
        from extensions.capabilities.scene_manager import model

        page, library = self._page()
        scene = model.Scene(scene_id="scene-0003", name="scene-0003")
        scene.areas.append(model.Area(name="go", rect=(0.1, 0.1, 0.2, 0.2)))
        library.save_scene(scene)
        page.reload()
        for _ in range(4):
            self.app.processEvents()
        return page, library

    def test_menu_retarget_hides_old_edge_immediately(self):
        """右键「更改目标」进入选择状态时旧边立即隐藏，与拖拽一致。

        不隐藏时画面上同时有旧边与跟随鼠标的虚线预览，看起来像「旧的没删、
        又新建了一条」。拖拽重连一直有隐藏行为，菜单入口此前漏了。
        """
        page, _library = self._retarget_page()
        key = ("scene-0001", "go")
        page._start_retarget(key)
        self.assertEqual(page.canvas._hidden_edge_key, key)
        self.assertNotIn(key, [item["key"] for item in page.canvas._edge_geometries], "旧边应已从画面移除")
        page.canvas.cancel_link()

    def test_menu_retarget_click_empty_only_cancels(self):
        """菜单改目标后点空白只取消，不删边——删边只属于拖拽落空。"""
        page, _library = self._retarget_page()
        key = ("scene-0001", "go")
        page._start_retarget(key)
        page.canvas._drop_link()
        self.assertIsNotNone(page._working.get(*key), "点空白不应把边删掉")
        self.assertIsNone(page.canvas._hidden_edge_key, "取消后应恢复显示旧边")

    def test_menu_retarget_keeps_view(self):
        """改目标确认后保持当前缩放与视口中心，不再重置视图。"""
        from PySide6.QtCore import QPointF

        page, library = self._retarget_page()
        canvas = page.canvas
        # 先持久化节点位置：重建时按保存的位置还原，内容不会因布局重算而移动。
        library.save_layout(canvas.remember_positions())
        canvas.scale(1.25, 1.25)
        canvas.centerOn(QPointF(0.0, 0.0))
        for _ in range(4):
            self.app.processEvents()
        before_scale = canvas.transform().m11()
        before_center = canvas.mapToScene(canvas.viewport().rect().center())

        page._ask_wait = lambda _default: 1.0
        page._start_retarget(("scene-0001", "go"))
        canvas._complete_link("scene-0003")

        after_scale = canvas.transform().m11()
        after_center = canvas.mapToScene(canvas.viewport().rect().center())
        self.assertAlmostEqual(after_scale, before_scale, places=3, msg="缩放不应被重置")
        self.assertLess((after_center - before_center).manhattanLength(), 10.0, "视口中心不应跳走")
        self.assertEqual(page._working.get("scene-0001", "go").target_scene, "scene-0003")

    def _parallel_page(self):
        """造一个带两条平行边（同对节点、不同区域）的路由页。

        Returns：
            `(page, library)` 二元组。
        """
        from extensions.capabilities.scene_manager import model

        page, library = self._page()
        scene = library.load_scene("scene-0001")
        scene.areas.append(model.Area(name="go2", rect=(0.3, 0.3, 0.4, 0.4)))
        library.save_scene(scene)
        library.set_route("scene-0001", "go2", "scene-0002", source=model.SOURCE_MANUAL)
        page.reload()
        for _ in range(6):
            self.app.processEvents()
        return page, library

    @staticmethod
    def _edge_middle(canvas, key):
        """返回画布上某条边曲线中点；找不到时返回 None。"""
        for geometry in canvas._edge_geometries:
            if geometry["key"] == key:
                return geometry["path"].pointAtPercent(0.5)
        return None

    @staticmethod
    def _edge_pen(canvas, key):
        """返回画布上某条边的画笔（排除宽度为 0 的箭头笔画）。"""
        from PySide6.QtWidgets import QGraphicsPathItem

        for item in canvas._scene.items():
            if isinstance(item, QGraphicsPathItem) and item.data(0) == key and item.pen().widthF() > 1.0:
                return item.pen()
        return None

    def test_selected_deleted_edge_keeps_deleted_style(self):
        """选中待删除边仍是红色虚线，只加粗——与选中普通边可区分。"""
        from PySide6.QtCore import Qt

        from extensions.capabilities.scene_manager import flow_canvas

        page, _library = self._page()
        key = ("scene-0001", "go")
        page.canvas.set_selected_edge(key)
        normal_pen = self._edge_pen(page.canvas, key)
        self.assertEqual(normal_pen.color(), flow_canvas.COLOR_SELECTED, "普通选中边是蓝色")
        self.assertEqual(normal_pen.style(), Qt.PenStyle.SolidLine)

        page._delete_edges([key])
        # 删除会清掉选中；再点一下那条待删除边（画布上仍以红色虚线存在）。
        page.canvas.set_selected_edge(key)
        deleted_pen = self._edge_pen(page.canvas, key)
        self.assertEqual(deleted_pen.color(), flow_canvas.COLOR_DELETED, "选中待删除边应保持删除色")
        self.assertEqual(deleted_pen.style(), Qt.PenStyle.DashLine, "待删除边应保持虚线")
        self.assertGreater(deleted_pen.widthF(), 2.4, "选中应在删除样式上加粗")

    def test_deleting_parallel_edge_keeps_other_geometry(self):
        """标记删除一条平行边后，另一条边的曲线位置不变。

        槽位若按 `_edges` 列表顺序分配，被删的边会从「图里的边」变成追加在末尾的
        ghost，剩下的平行边换槽、整条线跳到另一侧——用户看到的是「标记删除后线
        自己动了」。槽位必须按与删除状态无关的稳定键分配。
        """
        page, _library = self._parallel_page()
        before = self._edge_middle(page.canvas, ("scene-0001", "go2"))
        self.assertIsNotNone(before)
        page._delete_edges([("scene-0001", "go")])
        after = self._edge_middle(page.canvas, ("scene-0001", "go2"))
        self.assertIsNotNone(after, "未删除的那条边应仍然可见")
        self.assertLess((after - before).manhattanLength(), 0.5, "标记删除不应让另一条边移位")

    def test_explore_apply_and_ignore_keys(self):
        """探索页的右键应用/忽略作用于待确认候选。"""
        from extensions.capabilities.scene_manager import model as scene_model
        from extensions.capabilities.scene_manager.pages import explore_page

        _page, library = self._page()
        library.set_route("scene-0002", "go", "scene-0001", source="explored")
        explore = explore_page.ExplorePage(library, on_explore=lambda: None)
        self.addCleanup(explore.deleteLater)
        explore._apply_keys({("scene-0002", "go")})
        library.load_routes()
        self.assertTrue(library.routes.get("scene-0002", "go").routable())
        self.assertEqual(library.routes.get("scene-0002", "go").source, scene_model.SOURCE_CONFIRMED)

        library.set_route("scene-0002", "go", "scene-0001", source="explored")
        explore.reload()
        explore._ignore_keys({("scene-0002", "go")})
        library.load_routes()
        self.assertIsNone(library.routes.get("scene-0002", "go"))

    def test_explore_double_click_applies_candidate(self):
        """双击候选边：直接应用这一条。"""
        from extensions.capabilities.scene_manager.pages import explore_page

        _page, library = self._page()
        library.set_route("scene-0002", "go", "scene-0001", source="explored")
        explore = explore_page.ExplorePage(library, on_explore=lambda: None)
        self.addCleanup(explore.deleteLater)
        explore._on_edge_double_clicked("scene-0002", "go")
        library.load_routes()
        self.assertTrue(library.routes.get("scene-0002", "go").routable())


def _mouse_press(view, point, modifiers=None):
    """向画布发一个左键按下事件。

    Args：
        view：目标画布。
        point：视图坐标 `QPoint`。
        modifiers：键盘修饰键。
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(point),
        QPointF(view.mapToGlobal(point)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        modifiers or Qt.KeyboardModifier.NoModifier,
    )
    view.mousePressEvent(event)


def _mouse_drag(view, start, end, modifiers=None):
    """在画布上模拟一次按下—移动—松开。

    Args：
        view：目标画布。
        start：起点（视图坐标 `QPoint`）。
        end：终点（视图坐标 `QPoint`）。
        modifiers：键盘修饰键。
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    modifiers = modifiers or Qt.KeyboardModifier.NoModifier
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(start),
        QPointF(view.mapToGlobal(start)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        modifiers,
    )
    view.mousePressEvent(press)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(end),
        QPointF(view.mapToGlobal(end)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        modifiers,
    )
    view.mouseMoveEvent(move)
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(end),
        QPointF(view.mapToGlobal(end)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        modifiers,
    )
    view.mouseReleaseEvent(release)


def _wheel_event(view, delta):
    """造一个滚轮事件，用于测试缩放限幅。

    Args：
        view：目标画布。
        delta：`angleDelta().y()` 的取值，正数放大、负数缩小。

    Returns：
        `QWheelEvent` 实例。
    """
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    center = view.viewport().rect().center()
    scene_pos = QPointF(center)
    global_pos = QPointF(view.mapToGlobal(center))
    return QWheelEvent(
        scene_pos,
        global_pos,
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


if __name__ == "__main__":
    unittest.main()
