# -*- coding: utf-8 -*-
"""场景管理主页签：四个子页签的容器。

框架没有现成的嵌套页签容器（`TabWidget` 只是设背景色的 `QWidget`），因此用
`TabStrip` + `QStackedWidget` 自组合，与框架自己在任务页的做法一致。

**测试子页签只在 debug 时创建**。普通用户点「测试」却看到「执行跳转」这类按钮，
很可能直接点下去让游戏被接管；而这类页签对普通用户也没有任何用途。

主页签持有场景库实例并向四个子页签分发同一个实例——四个页签必须看到同一份数据，
否则在配置页保存后流转页显示的还是旧图。
"""

from ok import og
from ok.ui.qt.kit import TabStrip
from ok.ui.qt.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout

from extensions.capabilities.scene_manager import runtime
from extensions.capabilities.scene_manager.pages.config_page import ConfigPage
from extensions.capabilities.scene_manager.pages.drafts_page import DraftsPage
from extensions.capabilities.scene_manager.pages.explore_page import ExplorePage
from extensions.capabilities.scene_manager.pages.flow_page import FlowPage

# 子页签 key。
KEY_CONFIG = "config"
KEY_FLOW = "flow"
KEY_EXPLORE = "explore"
KEY_DRAFTS = "drafts"
KEY_DEBUG = "debug"


def is_debug_mode():
    """返回当前是否 debug 模式。

    Returns：
        `og.config['debug']` 为真时返回 True；取不到时按非 debug 处理（更保守：
        少显示一个页签比多显示安全）。
    """
    try:
        return bool(og.config.get("debug", False))
    except Exception:  # noqa: BLE001 — 框架尚未就绪时按非 debug
        return False


class SceneManagerTab(CustomTab):
    """场景管理页签。"""

    def __init__(self):
        """构造页签与四个子页签。"""
        super().__init__()
        # 界面读写**编辑区**（ok_scenes/，不入库），运行期识别读**产物区**
        # （assets/scenes/，入库）。两个实例而不是一个：混用会让「改了没生效」
        # 与「改了没入库」这两类问题从调用点看不出来。
        self.edit_library = runtime.open_edit_library()
        self.library = runtime.open_library()
        self._bridge_cache = None
        # 探索进度轮询定时器（见 `_watch_explore`）。必须在 `_build_ui` 之前建好：
        # `_start_explore` 可能在任何时刻被调用，那时它必须已存在。
        self._explore_timer = None
        # 正在跑的探索任务实例。中断按钮**直接用它**，不再经 `get_task` 反查：
        # 热重载或类对象不一致时反查可能拿不到实例，表现就是「点了中断没反应」。
        self._explore_task = None
        self._build_ui()

    # 挂到「开发」分组。框架默认把 custom_tabs 全挂到「游戏」分组，而
    # `ok.ui.qt.MainWindow._tab_section` 支持页签自己声明分组——场景配置只在
    # 配置期用、运行时完全不碰，放在「游戏」会让普通用户误以为它参与日常流程。
    section = "develop"

    @property
    def name(self):
        """返回页签显示名。

        Returns：
            页签名，经 `app.tr()` 翻译。
        """
        return self.tr("Scene Manager")

    def _build_ui(self):
        """搭建界面。"""
        self.icon_ = None
        from ok import Icon

        self.icon = Icon.APPLICATION

        outer = QVBoxLayout()
        self.strip = TabStrip(look="underline")
        self.stack = QStackedWidget()
        self._pages = {}

        self.config_page = ConfigPage(
            self.edit_library,
            on_scene_changed=self._on_scene_changed,
            capture_frame=self._capture_frame,
            on_export=self._export_to_assets,
        )
        self._add_page(KEY_CONFIG, self.tr("Scene Config"), self.config_page)

        self.flow_page = FlowPage(
            self.edit_library,
            build_bridge=self._build_bridge,
            on_changed=self._on_routes_changed,
        )
        self._add_page(KEY_FLOW, self.tr("Scene Routes"), self.flow_page)

        self.explore_page = ExplorePage(
            self.edit_library,
            on_explore=self._start_explore,
            on_changed=self._on_candidates_changed,
        )
        self._add_page(KEY_EXPLORE, self.tr("Exploration"), self.explore_page)

        self.drafts_page = DraftsPage(self.edit_library, on_changed=self._on_drafts_changed)
        self._add_page(KEY_DRAFTS, self.tr("Drafts"), self.drafts_page)

        self.debug_page = None
        if is_debug_mode():
            from extensions.capabilities.scene_manager.pages.debug_page import DebugPage

            self.debug_page = DebugPage(self.edit_library, build_bridge=self._build_bridge)
            self._add_page(KEY_DEBUG, self.tr("Debug"), self.debug_page)

        self.strip.current_changed.connect(self._on_tab_changed)

        # 库级导入导出挂在页签条最右侧：它操作整份场景库，不属于任何单个
        # 子页签。**必须在所有页签加完之后再追加**：`TabStrip._insert` 总是
        # 插在「倒数第一项之前」，先加按钮会让页签插到按钮前面，顺序变成
        # 「导出、页签…、导入」。页签加完后布局是 [页签…, stretch]，此时
        # 追加的按钮落在 stretch 之后，正好靠右。
        from ok.ui.qt.kit import Button

        self.export_button = Button(self.tr("Export Library"), variant="subtle", size="small")
        self.export_button.clicked.connect(self._export_library)
        self.strip.layout().addWidget(self.export_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.import_button = Button(self.tr("Import Library"), variant="subtle", size="small")
        self.import_button.clicked.connect(self._import_library)
        self.strip.layout().addWidget(self.import_button, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addWidget(self.strip)
        outer.addWidget(self.stack, 1)
        self.addLayout(outer, 1)
        # 基类把 `vBoxLayout` 设为顶对齐：子页签只按 sizeHint 取高，外层滚动区
        # 因此出现上下滚动条，画布也被压成固定小高度。取消顶对齐后 stack 才会
        # 填满视口，画布跟着自适应。外层页签本身**永不滚动**（见
        # `_sync_outer_height`）：需要滚动的内容由各子页自己的滚动区承担。
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag(0))

        self.reload_all()

    def _add_page(self, key, text, widget):
        """向页签条与堆栈各加一项。

        Args：
            key：页签 key。
            text：**已翻译**的显示文本。调用方在字面量位置套 `self.tr(...)`——
                若在这里包 `self.tr(text)`，静态扫描看到的是变量，抓不到原文，
                catalog 里就永远缺这几条，界面上一直是英文。
            widget：内容控件。
        """
        self.strip.add_tab(key, text)
        self.stack.addWidget(widget)
        # 记下 key → 控件的映射。不靠「页签条下标 == 堆栈下标」隐式对应：
        # 调试页按条件决定是否创建，一旦有人调整创建顺序，两边的下标就会错位，
        # 而错位的表现是「点 A 页签显示 B 的内容」——没有任何报错。
        self._pages[key] = widget

    def _on_tab_changed(self, key):
        """切换页签：先切显示，再按需刷新。

        Args：
            key：目标页签 key。

        Note：
            **必须先切控件再刷新**。反过来（先 reload 再 setCurrentWidget）时，
            任一页的 reload 抛异常都会让点击彻底没反应——用户看到的是「页签点了
            不切换」，与真正的接线缺失无法区分。先切过去则异常只影响内容新旧。

            reload 也不常驻：每次切换都重建整个图会让拖动过的节点位置丢掉，
            而重建的开销在场景多时也明显。改为「切到哪页刷哪页」。
        """
        page = self._pages.get(key)
        if page is not None:
            self.stack.setCurrentWidget(page)
        self._sync_outer_height()
        if key == KEY_FLOW:
            self.flow_page.reload()
        elif key == KEY_EXPLORE:
            # 路由页的编辑（保存后）会改变边；不刷新的话探索页还画着上一次
            # 加载的旧拓扑，看起来像「删除没生效」。
            self.explore_page.reload()
        elif key == KEY_DRAFTS:
            self.drafts_page.reload()
        elif key == KEY_DEBUG and self.debug_page is not None:
            self.debug_page.reload()
        elif key == KEY_CONFIG:
            self.config_page.reload_scenes()

    def reload_all(self):
        """刷新全部子页签。"""
        self.edit_library.load_meta()
        self.config_page.reload_scenes()
        self.flow_page.reload()
        self.drafts_page.reload()
        if self.debug_page is not None:
            self.debug_page.reload()

    # --- 外层高度 -----------------------------------------------------------

    def _sync_outer_height(self):
        """把外层 view 的高度钉在视口高度，让滚动只发生在各子页内部。

        Note：
            基类 `Tab` 本身是 QScrollArea，内容高度按 `sizeHint` 决定；
            `QStackedWidget` 又按所有子页的最大值报高度。只要外层仍有滚动
            余量，滚轮滚动的就是整个页签——标题、工具栏与状态行会跟着一起走，
            而卡片列表自己反而不滚。

            这里直接把 view 的最大高度钉在视口高度，外层页签因此**在任何
            子页下都不出现上下滚动条**。需要滚动的内容由各页自己的滚动区
            承担：

            | 子页 | 滚动区 |
            |---|---|
            | 场景配置 | 卡片列表（`ConfigPage.scroll_area`） |
            | 场景路由 / 探索 | 画布（自绘，平移不靠滚动条） |
            | 草稿确认 | 表格自身的滚动条 |

            **配置页也必须钉住**：它是最容易「看起来整页在滚」的一页——
            卡片一多，外层的滚动范围先于内层的出现，用户抓到的是整页的
            滚动条，标题行会被滚出视口。钉住之后内层列表才成为唯一滚动区。
        """
        height = self.viewport().height()
        if height <= 0:
            # 尚未显示（或视口被压成 0）时没有可用的目标高度，等首次
            # `resizeEvent` 再校准；此时设成 0 会把 view 直接压没。
            return
        self.view.setMaximumHeight(height)

    def resizeEvent(self, event):
        """窗口尺寸变化时重新校准外层高度。

        Args：
            event：尺寸事件。
        """
        super().resizeEvent(event)
        self._sync_outer_height()

    # --- 与框架的接线 -------------------------------------------------------

    def _capture_frame(self):
        """取一帧画面供配置页截图。

        Returns：
            BGR 帧（**已带全局遮罩**）；失败时返回 None（配置页会提示）。

        Note：
            经 `runtime.capture_frame` 取帧，遮罩在那一层统一施加。不直接调
            `get_frame`：它返回原始像素，含游戏 UID，而 `screenshot_processor`
            钩子只在把截图存成文件时生效，不作用于运行帧——绕过去就是一次静默
            泄漏，没有任何报错。
        """
        try:
            return runtime.capture_frame()
        except Exception as error:  # noqa: BLE001 — 截图失败由界面提示，不崩页签
            self.logger.warning(f"场景管理截图失败：{error}")
            return None

    def _build_bridge(self):
        """返回一个任务桥接对象（按需创建并缓存）。

        Returns：
            `runtime.TaskBridge` 实例；取不到任务时为 None。

        Note：
            `TaskBridge` 需要任务实例（取帧、点击都在任务上）。这里优先用
            `SceneGotoTask`；取不到时退回 `og.executor.current_task`。
        """
        if self._bridge_cache is not None:
            return self._bridge_cache
        task = None
        if self.executor is not None:
            from extensions.capabilities.scene_manager.tasks import SceneGotoTask

            try:
                task = self.get_task(SceneGotoTask)
            except Exception:  # noqa: BLE001 — 任务未注册时退回当前任务
                task = None
            if task is None:
                task = getattr(self.executor, "current_task", None)
        if task is None:
            return None
        # 界面上的调试动作（预览路径、试走一步、执行跳转）都读**编辑区**：
        # 用户刚在 ok_scenes 里配好的场景与路由还没导出，用产物区会出现
        # 「刚连的边在调试页提示没有可用路径」。产物区只服务运行期任务与导出。
        self._bridge_cache = runtime.TaskBridge(task, self.edit_library, self.edit_library)
        return self._bridge_cache

    # --- 动作 ---------------------------------------------------------------

    def _on_scene_changed(self):
        """配置页保存后刷新流转页与草稿页。"""
        self._bridge_cache = None
        self.flow_page.reload()
        self.drafts_page.reload()
        if self.debug_page is not None:
            self.debug_page.reload()

    def _export_to_assets(self):
        """把编辑区同步到产物区（assets/scenes/），覆盖前先确认。

        四件事按序做：**分辨率全面检查**、确认、导出、刷新运行期看到的库。

        **分辨率检查排在最前**：导出是「入库、随包分发」的动作，把与库基准不一致
        的场景发布出去，等于让运行期拿着错位的特征框去匹配——表现是「这个场景
        从来识别不到」，而且排查时很难想到是采集分辨率的问题。检查放在确认框
        之前，是为了避免用户先点了「覆盖」再被拦下，白紧张一次。

        **必须先确认**：导出是「整体替换目标场景目录」，会把产物区里已有的
        同名场景连同它的模板与掩码一起删掉重写。而产物区是**入库、随包分发**的
        那一份——误点一次就可能把别人已提交的内容覆盖掉，且没有任何撤销入口。
        提示里要写清目标路径与将被覆盖的场景数，让用户能判断这是不是他要的那份库。

        **运行期那份库必须重建**：`self.library` 是产物区的实例，导出后磁盘变了，
        但它的 `meta` 还是旧的、`TaskBridge.loader` 里还缓存着旧模板。不刷新的话
        表现是「导出成功但测试页识别的还是旧结果」，而两次导出之间的差异只在对
        形状的微调上时，几乎不可能看出来。
        """
        if not self._check_resolutions():
            return
        if not self._confirm_export():
            return
        exported, skipped = self.edit_library.export_to(self.library.root)
        self.library.load_meta()
        self._bridge_cache = None
        lines = [f"已同步 {len(exported)} 个场景到 {self.library.root}"]
        if skipped:
            lines.append("以下场景因资产不全被跳过：")
            for scene_id, reason in skipped:
                scene = self.edit_library.load_scene(scene_id)
                label = (
                    f"{scene.name}（{scene_id}）"
                    if scene is not None and scene.name and scene.name != scene_id
                    else scene_id
                )
                lines.append(f"  {label}：{reason}")
        self.config_page.status_label.setText(chr(10).join(lines))

    def _check_resolutions(self):
        """保存前逐个场景核对预览图尺寸与库基准是否一致。

        Returns:
            可以继续导出时为 True；查到不一致时为 False（已弹窗说明）。

        Note:
            宽高比不同一律拒绝：相对坐标按基准比例换算，比例变了坐标就是错的。
            尺寸不同但比例一致时只提示——识别会把帧等比缩放到基准，结果正确，
            这种情况多半是换了采集分辨率但窗口比例没变，没必要拦住。
        """
        from ok.ui.qt.kit import Dialog

        from extensions.capabilities.scene_manager import resolution

        base_width, base_height, problems = self.edit_library.resolution_problems()
        if not problems:
            return True

        hard = [item for item in problems if item[3].startswith("宽高比")]
        if not hard:
            # 只有尺寸差异：提示后照常导出。
            size_note = "；".join(
                f"{name or scene_id}（{resolution.describe_size(*actual) if actual else '无法读取'}）"
                for scene_id, name, actual, _reason in problems
            )
            self.logger.warning(f"场景尺寸与库基准不同但比例一致，导入时会被等比缩放：{size_note}")
            return True

        lines = [
            f"库基准：{resolution.aspect_text(base_width, base_height)}",
            "",
            "以下场景与基准不一致，导出后它们的特征框会在运行期整体错位：",
        ]
        for scene_id, name, actual, reason in hard:
            label = name or scene_id
            size_text = resolution.aspect_text(*actual) if actual else "无法读取"
            lines.append(f"  · {label}（{scene_id}）：{size_text} — {reason}")
        lines.append("")
        lines.append("请先重新采集这些场景，或新建一个场景库。本次保存已中止。")

        dialog = Dialog(
            self,
            self.tr("Resolution Mismatch"),
            chr(10).join(lines),
            ok_text=self.tr("Got It"),
        )
        dialog.set_cancel_visible(False)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
        self.config_page.status_label.setText(self.tr("Save blocked: resolution mismatch."))
        return False

    def _confirm_export(self):
        """覆盖产物区前弹确认框。

        Returns：
            用户确认时为 True。

        Note：
            这里不检查「产物区是否为空」再决定要不要问：空库也会在导出时被写入
            `library.json`，而且「有没有东西被覆盖」需要逐场景比对，逻辑一复杂就
            容易漏判。一律确认，代价是一次点击。
        """
        from ok.ui.qt.kit import confirm

        overwritten = self._count_overwrites()
        detail = f"将把编辑区的 {len(self.edit_library.list_scene_ids())} 个场景同步到：\n{self.library.root}\n\n"
        if overwritten:
            detail += f"其中 {overwritten} 个与产物区同名，将被覆盖（原有模板与掩码一并替换）。\n"
        else:
            detail += "产物区目前没有同名场景，本次为新增。\n"
        detail += "\n此操作不可撤销，确定继续吗？"
        return bool(
            confirm(
                self,
                self.tr("Save To assets"),
                detail,
                ok_text=self.tr("Overwrite"),
                danger=True,
            )
        )

    def _count_overwrites(self):
        """统计产物区里与编辑区同名的场景数。

        Returns：
            会被覆盖的场景数量。
        """
        try:
            existing = set(self.library.list_scene_ids())
        except OSError:
            return 0
        return len(existing & set(self.edit_library.list_scene_ids()))

    def _on_drafts_changed(self):
        """草稿区变更后刷新其它页。"""
        self.config_page.reload_scenes()
        self.flow_page.reload()
        if self.debug_page is not None:
            self.debug_page.reload()

    def _on_routes_changed(self):
        """流转边被修改后刷新其它页。

        Note：
            **不回刷 `flow_page` 自己**：调用方（流转页的边按钮）改完边已经自己
            `reload()` 过了，再刷一遍会把用户刚选中的行清掉，接着点「确认」就
            提示「请先选中一条边」。
        """
        if self.debug_page is not None:
            self.debug_page.reload()

    def _export_library(self):
        """导出场景库到 zip。"""
        from PySide6.QtWidgets import QFileDialog

        path, _filter = QFileDialog.getSaveFileName(self, "导出场景库", "scenes.zip", "Zip (*.zip)")
        if not path:
            return
        try:
            entries = self.edit_library.export_library(path)
        except Exception as error:  # noqa: BLE001 — 导出失败提示即可
            self.flow_page.status_label.setText(f"导出失败：{error}")
            return
        self.flow_page.status_label.setText(f"已导出 {entries} 个文件到 {path}")

    def _import_library(self):
        """从 zip 导入场景库。"""
        from PySide6.QtWidgets import QFileDialog

        path, _filter = QFileDialog.getOpenFileName(self, "导入场景库", "", "Zip (*.zip)")
        if not path:
            return
        try:
            count = self.edit_library.import_library(path)
        except Exception as error:  # noqa: BLE001 — 导入失败提示即可
            self.flow_page.status_label.setText(f"导入失败：{error}")
            return
        self._bridge_cache = None
        self.reload_all()
        self.flow_page.status_label.setText(f"已导入 {count} 个场景")

    def _start_explore(self):
        """从页签启动探索任务。

        Note:
            **应用没在运行时会先拦住，并弹浮层提示**。`task.enable()` 只是把任务
            塞进队列，真正的执行要等执行器从暂停态醒过来——也就是用户在主界面点了
            「启动」按钮（右上角的电源图标，默认热键 F9）。

            未运行时点这个按钮，日志里能看到 `queued onetime_task` 却没有任何执行
            记录，用户只会觉得「点了没反应」。因此这里直接拦住并弹浮层——流转页
            底部那行 `status_label` 是小字，实测用户根本不会注意到。
        """
        if self.executor is None:
            self._warn("执行器未就绪，无法启动探索")
            return
        from extensions.capabilities.scene_manager.tasks import SceneExploreTask

        try:
            task = self.get_task(SceneExploreTask)
        except Exception as error:  # noqa: BLE001 — 取不到任务时提示
            self._warn(f"取不到探索任务：{error}")
            return
        if task is None:
            self._warn("探索任务未注册")
            return
        # 未运行就直接告知，不把任务塞进队列——入了队也不会跑，还会在用户
        # 稍后点「启动」时被当作队头意外执行（那时用户可能已改过场景库）。
        if getattr(self.executor, "paused", False):
            self._warn(
                "探索未启动：请先点主界面右上角的「启动」按钮（键盘 F9）让应用进入运行状态，"
                "再回来点这里。探索会真实操作游戏，因此必须由运行态驱动。",
                title="需要先启动应用",
            )
            return
        # 先把库与停止标记装好再入队：执行器线程可能立刻取到任务，
        # 入队后再赋值会有「第一轮读到 None」的竞争。
        task.edit_library = self.edit_library
        task.cancel_requested = False
        self._explore_task = task
        task.enable()
        self.explore_page.show_progress(self._interrupt_explore)
        self.explore_page.set_status(
            "探索任务已入队。探索会真实操作游戏（用单击探测），请确认已开启相应区域的「允许探索探测」"
        )
        self._watch_explore(task)

    def _interrupt_explore(self):
        """界面「中断」：给探索任务置停止标记。"""
        task = self._explore_task
        if task is None:
            # 兜底：极少数情况下（热重载前后）引用没挂上，再按类反查一次。
            from extensions.capabilities.scene_manager.tasks import SceneExploreTask

            try:
                task = self.get_task(SceneExploreTask)
            except Exception as error:  # noqa: BLE001 — 取不到任务时只写状态栏
                self.explore_page.set_status(f"中断失败：{error}")
                return
        if task is not None:
            task.cancel_requested = True
            self.explore_page.set_status("正在中断探索…")
        else:
            self.explore_page.set_status("中断失败：找不到探索任务")

    def _watch_explore(self, task):
        """轮询探索任务的进度，结束后把结果展示到流转页。

        Args：
            task：`SceneExploreTask` 实例。

        Note:
            探索是**异步**的（入队后由执行器线程跑），点按钮时拿不到结果，因此
            只能等。用 `QTimer` 轮询而不是回调：任务侧没有完成通知，而给任务加
            信号会把它与 Qt 绑起来——任务要能在无 GUI 环境跑。

            判据是 `task.enabled` 由真转假（框架在任务跑完后会禁用一次性任务），
            此时 `last_result` 已由 `_report` 填好。轮询间隔取 1 秒：探索本身要
            好几秒到几分钟，1 秒的延迟察觉不到，也不会给界面造成负担。
        """
        from PySide6.QtCore import QTimer

        previous = self._explore_timer
        if previous is not None:
            previous.stop()
        timer = QTimer(self)
        self._explore_timer = timer
        started = {"result_before": getattr(task, "last_result", None)}

        def poll():
            """每秒检查一次任务是否已结束。"""
            progress = getattr(task, "progress", None)
            if progress:
                self.explore_page.update_progress(progress)
            if getattr(task, "enabled", False):
                return
            timer.stop()
            self._explore_timer = None
            self._explore_task = None
            self.explore_page.hide_progress()
            result = getattr(task, "last_result", None)
            if result is None or result is started["result_before"]:
                # 任务被禁用但没有产出（用户手动停止、或启动即失败）。
                self.explore_page.set_status("探索已结束，但没有产生结果")
                return
            # 探索结果留在探索页：候选边叠加在已确认路由图上，用户在这里
            # 应用或忽略，路由页只显示最终结果。
            self._on_scenes_changed_after_explore()
            self.explore_page.show_explore_result(result)
            self.explore_page.set_status("探索已结束，候选边已叠加到画布")

        timer.timeout.connect(poll)
        timer.start(1000)

    def _on_scenes_changed_after_explore(self):
        """探索结束后刷新相关页签（草稿多出来了，流转图也可能多了边）。"""
        self.edit_library.load_meta()
        self.config_page.reload_scenes()
        self.flow_page.reload()
        self.explore_page.reload()
        self.drafts_page.reload()
        self._bridge_cache = None

    def _on_candidates_changed(self):
        """探索页应用或忽略候选边后，刷新场景路由页的最终图。"""
        self.flow_page.reload()

    def _warn(self, message, title=None):
        """弹一条警告级浮层，同时把文案写进流转页状态栏。

        Args：
            message：提示正文。
            title：可选的加粗标题；省略时只有正文。

        Note:
            **两处都写，用途不同**：浮层负责「此刻就能被看到」（用户点完按钮视线
            还在按钮附近，而浮层出现在窗口顶部居中），状态栏负责「事后还能回看」
            ——浮层几秒后自己消失。
        """
        from extensions.capabilities.scene_manager import notify

        notify.notify_warning(self, message, title)
        self.explore_page.set_status(f"{title}：{message}" if title else message)

    # --- 生命周期 -----------------------------------------------------------

    def hideEvent(self, event):
        """隐藏时停掉实时刷新定时器。

        Args：
            event：事件对象。
        """
        if getattr(self, "debug_page", None) is not None:
            self.debug_page.stop_timer()
        super().hideEvent(event)
