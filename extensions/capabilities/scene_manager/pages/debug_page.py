# -*- coding: utf-8 -*-
"""测试子页签：只在 debug 模式下创建。

三段式设计，各自定位一类失败：

| 段 | 回答的问题 | 失败时的典型现象 |
|---|---|---|
| 实时识别 | 识别对了没有 | 「在 A 画面却识别成 B」或「什么都识别不到」 |
| 单步试走 | 这条边通不通 | 「点完还在原地」或「点完去了别的地方」 |
| 完整跳转 | 整条路径能不能走通 | 前两段都对但连起来失败（一般是时序问题） |

不做批量回归：跑全库基准图比对属于测试代码，落 `extensions_tests/`，
放在界面里既慢又会让「测试页」变得不可点。
"""

import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from extensions.capabilities.scene_manager.translate import tr

# 「缺失特征」列的固定宽度（像素）。
#
# 取 360：原先按内容自适应只有约 90px，特征名被压成一两个字，用户看到的是
# 「靶场…」，无法判断到底是哪个特征没命中——而这一列的全部价值就在于指出那个名字。
# 4 倍宽度足以容纳十来字的中文特征名，特征多时由单行省略号兜底（完整名单在 tooltip）。
_MISSING_COLUMN_WIDTH = 360

# 表格行高（像素）。**固定行高**：缺失特征列不换行，行高就该恒定，
# 否则内容长短会让各行高度参差不齐，扫读时视线要重新找基线。
_ROW_HEIGHT = 30


def _hits_text(hits, total):
    """把「命中数 / 总数」格式化成表格文本。

    Args:
        hits: 命中个数。
        total: 参与判定的总数。

    Returns:
        形如 `2/3` 的文本。

    Note:
        **总数为 0 时显示 `0/0` 而不是「—」**：没有可选特征与「可选特征一个都
        没命中」是两件事，前者的 `0/0` 一眼能读出「这个场景没配可选特征」。
        用「—」会让两种含义混在一起，反而要回头查配置。
    """
    return f"{hits}/{total}"


def _backend_is_background(build_bridge):
    """判断当前交互方式是否为「后台投递类」。

    Args：
        build_bridge：返回 `runtime.TaskBridge` 的工厂。

    Returns：
        True 表示当前后端走消息投递（`PostMessage` / `Genshin`）。

    Note：
        判据与 `runtime.TaskBridge._backend_needs_foreground` 互为反面，但**这里
        不复用它**：那个方法在 bridge 实例上，而提示文案生成于 GUI 线程，构造
        bridge 会碰配置与会话；这里只需读类型名，代价更小。
        取不到后端时返回 False（按「前台方式」给提示）——那种情况下「确认窗口
        未被遮挡」才是更可能的原因。
    """
    if build_bridge is None:
        return False
    try:
        bridge = build_bridge()
    except Exception:  # noqa: BLE001 — 提示文案不该因为装配失败而整段丢掉
        return False
    task = getattr(bridge, "task", None)
    interaction = getattr(getattr(task, "executor", None), "interaction", None)
    if interaction is None:
        return False
    name = type(interaction).__name__
    if "Foreground" in name:
        return False
    return "PostMessage" in name or "Genshin" in name


class DebugPage(QWidget):
    """测试子页签（仅 debug 可见）。"""

    def __init__(self, library, build_bridge=None, parent=None):
        """构造测试页。

        Args：
            library：`store.SceneLibrary` 实例。
            build_bridge：返回 `runtime.TaskBridge` 的工厂。
            parent：父控件。
        """
        super().__init__(parent)
        self.library = library
        self._build_bridge = build_bridge
        self._timer = None
        # 识别进行中标记：实时刷新每 800ms 触发一次，而一次识别可能更久（场景多、
        # 或执行器上有别的 job 在排队）。没有这个标记时，定时器会在上一次还没返回时
        # 再发一次——两次识别各拿一帧、各写一次结果，界面上的耗时与实际状态对不上。
        self._identifying = False
        # 上一次弹过的「截图目标可能选错」文案。实时刷新每 800ms 识别一次，
        # 同样的提示只弹一次——否则浮层会不断叠加，把界面糊住。
        self._last_capture_hint = ""
        self._build_ui()

    def _build_ui(self):
        """搭建界面。"""
        from ok.ui.qt.kit import Button, ComboBox, Label

        root = QVBoxLayout(self)

        # 第一段：实时识别
        root.addWidget(Label(tr("(1) Live Recognition"), "section"))
        row1 = QHBoxLayout()
        self.identify_button = Button(tr("Identify Current Screen"), variant="primary", size="small")
        self.identify_button.clicked.connect(self._on_identify_clicked)
        row1.addWidget(self.identify_button)
        self.auto_button = Button(tr("Start Live Refresh"), variant="tonal", size="small")
        self.auto_button.clicked.connect(self._toggle_auto)
        row1.addWidget(self.auto_button)
        self.identify_label = Label(tr("Not identified yet"), "body", word_wrap=True)
        row1.addWidget(self.identify_label, 1)
        root.addLayout(row1)

        # 识别结果表。**必须只读**：它展示的是「这一次识别算出来的东西」，编辑它
        # 没有任何落点——写回场景库？还是改本次结果？两种解释都说不通，而可编辑的
        # 表格会让用户以为改了有用。数据以 `setEditTriggers(NoEditTriggers)` 关掉
        # 编辑入口，同时去掉垂直表头（行号没有信息量，排序后还会误导）。
        #
        # **命中列一律显示 `X/Y`**：单看命中数无法判断「命中了几个、一共几个」，
        # 而这两件事差一个数就完全是另一种状况（0/1 与 0/5 的排查方向不同）。
        # 原先另设「必须总数」列既占宽度又要横向对照，改成 `X/Y` 后一列读完。
        self.detail_table = QTableWidget(0, 5)
        self.detail_table.setHorizontalHeaderLabels(
            [
                tr("Scene"),
                tr("Similarity"),
                tr("Mandatory Hits"),
                tr("Optional Hits"),
                tr("Missing Features"),
            ]
        )
        # 场景列显示「名称（scene-0001）」，需要比纯 id 宽。用 Stretch 让它吃掉
        # 剩余宽度、其余列按内容自适应：反过来（场景列固定、数值列 Stretch）会让
        # 名称被截断、数字列却空出大片留白——数值列的内容最多三四位，不需要弹性。
        header = self.detail_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        # 缺失特征列按**固定像素**而不是 ResizeToContents：特征名可能很长（如
        # 「靶场入口-开始按钮-图标」），自适应会把它压成一两个字宽，用户只能
        # 看到「靶场…」而无法判断是哪个特征没命中。取 4 倍于原内容宽度的估算。
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.detail_table.setColumnWidth(4, _MISSING_COLUMN_WIDTH)
        # **单行 + 省略号，不换行**。该格会列出全部未命中的必需特征（顿号连接），
        # 特征多时一行装不下。换行会让这一行的高度随内容涨到两三倍，几行就撑爆
        # 表格的固定高度（180px），后面的场景要滚动才能看到——而这张表是「扫一眼
        # 谁最像」的用途，行高不齐反而更难扫。
        #
        # 被省略的后半截不能丢：完整名单放进该格的 tooltip，鼠标停留即可看到。
        self.detail_table.setWordWrap(False)
        self.detail_table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.detail_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.detail_table.setMaximumHeight(180)
        root.addWidget(self.detail_table)

        # 第二段：单步试走
        root.addWidget(Label(tr("(2) Try One Step"), "section"))
        row2 = QHBoxLayout()
        row2.addWidget(Label(tr("Area"), "body"))
        self.scene_combo = ComboBox()
        self.scene_combo.currentIndexChanged.connect(self._reload_area_combo)
        row2.addWidget(self.scene_combo, 1)
        self.area_combo = ComboBox()
        row2.addWidget(self.area_combo, 1)
        self.step_button = Button(tr("Activate And Observe"), variant="tonal", size="small")
        self.step_button.clicked.connect(self._on_step)
        self.step_button.setEnabled(self._build_bridge is not None)
        row2.addWidget(self.step_button)
        root.addLayout(row2)
        self.step_label = Label("", "caption", word_wrap=True)
        root.addWidget(self.step_label)

        # 第三段：完整跳转
        root.addWidget(Label(tr("(3) Full Navigation"), "section"))
        row3 = QHBoxLayout()
        row3.addWidget(Label(tr("Target Scene"), "body"))
        self.target_combo = ComboBox()
        row3.addWidget(self.target_combo, 1)
        self.goto_button = Button(tr("Navigate"), variant="primary", size="small")
        self.goto_button.clicked.connect(self._on_goto)
        self.goto_button.setEnabled(self._build_bridge is not None)
        row3.addWidget(self.goto_button)
        root.addLayout(row3)
        # **必须换行**：完整跳转成功时这里写的是整条路径（「A --区域--> B；B --…」），
        # 长到远超窗口宽度。QLabel 不换行时最小宽度就是整段文本宽度，会把整个页面的
        # 最小宽度撑到几千像素——同一行右侧的「开始」按钮被推出可视区，用户以为按钮
        # 消失了。换行后最小宽度只取决于最长的不可断词，布局回到窗口宽度内。
        self.goto_label = Label("", "caption", word_wrap=True)
        root.addWidget(self.goto_label)

        self.status_label = Label("", "caption", word_wrap=True)
        root.addWidget(self.status_label)
        root.addStretch(1)

    # --- 数据 ---------------------------------------------------------------

    def reload(self):
        """重新加载场景下拉。"""
        scenes, failures = self.library.load_library()
        for combo in (self.scene_combo, self.target_combo):
            combo.blockSignals(True)
            combo.clear()
            for scene in scenes:
                combo.addItem(f"{scene.name}（{scene.scene_id}）", scene.scene_id)
            combo.blockSignals(False)
        self._reload_area_combo()
        if failures:
            self.status_label.setText("有场景无法读取：" + "；".join(f"{sid}（{msg}）" for sid, msg in failures))

    def _reload_area_combo(self):
        """按当前选中的场景刷新区域下拉。"""
        scene_id = self.scene_combo.currentData()
        scene = self.library.load_scene(scene_id) if scene_id else None
        self.area_combo.blockSignals(True)
        self.area_combo.clear()
        if scene is not None:
            for area in scene.areas:
                # 目标场景来自路由表，不在区域上。
                route = self.library.routes.get(scene_id, area.name)
                target = ""
                if route is not None:
                    target_scene = self.library.load_scene(route.target_scene)
                    target = " → " + _scene_label(
                        target_scene.name if target_scene else "",
                        route.target_scene,
                    )
                self.area_combo.addItem(f"{area.name}{target}", area.name)
        self.area_combo.blockSignals(False)

    # --- 第一段 -------------------------------------------------------------

    def _on_identify_clicked(self):
        """「识别当前画面」按钮：先清掉提示去重记录，再识别。

        Note:
            手动点一次就是想拿一个当下的结论，哪怕与上次的提示文案完全相同也该
            再弹一次；而实时刷新是自动重复的，靠 :meth:`_show_capture_hint` 里的
            去重避免刷屏。两者的差别只在这一处。
        """
        self._reset_capture_hint()
        self._on_identify()

    def _on_identify(self):
        """识别当前画面并列出各场景得分明细。

        「识别当前画面」按钮与实时刷新定时器共用本方法，因此耗时显示对两者同时生效。

        **整段识别通过 `bridge.identify_blocking()` 派到执行器线程**。直接在 GUI
        线程调 `task.ocr` 会让界面永久卡死：执行器未运行时处于暂停态，而 `task.ocr`
        第一件事就是因暂停而进 `executor.sleep(1)`，那个循环只在暂停解除后退出，
        而解除只能由执行器线程推进。GUI 线程被占住时两者互相等待，只能杀进程。
        """
        if self._identifying:
            # 上一次还没返回（实时刷新间隔 800ms 可能短于一次识别）。丢弃本次触发，
            # 不排队：排队会积压，放开后连跑好几次，界面上的耗时与当前画面不同步。
            return
        if self._build_bridge is None:
            self.identify_label.setText("执行器未就绪：无法识别")
            return
        self._identifying = True
        try:
            bridge = self._build_bridge()
            if bridge is None:
                self.identify_label.setText("执行器未就绪：无法识别")
                return
            # 用 diagnose_blocking 而不是 identify_blocking：只有全量评估的相似度
            # 才能跨场景比较与排序（短路下的均值样本数不同，见 recognize 的说明）。
            diagnose = getattr(bridge, "diagnose_blocking", None)
            result = diagnose() if callable(diagnose) else bridge.identify_blocking()
        except Exception as error:  # noqa: BLE001 — 测试页只展示，不让界面崩
            self.identify_label.setText(f"识别失败：{error}")
            return
        finally:
            self._identifying = False

        if result.matched():
            # 状态行与表格用同一套标签格式（名称 + id），否则同一场景在两处显示
            # 成不同样子，用户会以为是两个东西。
            label = _scene_label(result.scene.name if result.scene else "", result.scene_id())
            self.identify_label.setText(
                f"识别为 {label}（{_timing_text(result)}，帧缩放：{'是' if result.normalized else '否'}）"
            )
        else:
            suspects = "、".join(
                _scene_label(scene.name, scene.scene_id) + f" {score:.0%}" for scene, score in result.candidates
            )
            self.identify_label.setText(
                "未识别" + (f"；疑似 {suspects}" if suspects else "") + f"（{_timing_text(result)}）"
            )
        self._show_capture_hint(result)

        self.detail_table.setRowCount(len(result.details))
        # 按相似度降序：识别的第一诉求是「最像的是哪个」，扫描顺序（id 升序）
        # 对这个诉求没有意义。
        for row, detail in enumerate(result.details_by_similarity()):
            scene_id, hits, total, optional_hits = detail[:4]
            # 相似度取第 6 项、名称取第 7 项、可选总数取第 8 项。**按位置容错读取**：
            # `detail` 是元组协议，历史调用点可能只给 5–7 项，直接解包会抛
            # ValueError 把整张表打空。
            similarity = detail[5] if len(detail) > 5 else None
            name = detail[6] if len(detail) > 6 else ""
            optional_total = detail[7] if len(detail) > 7 else 0
            self.detail_table.setItem(row, 0, _read_only_item(_scene_label(name, scene_id)))
            self.detail_table.setItem(row, 1, _read_only_item(_percent_text(similarity)))
            self.detail_table.setItem(row, 2, _read_only_item(_hits_text(hits, total)))
            self.detail_table.setItem(row, 3, _read_only_item(_hits_text(optional_hits, optional_total)))
            # 「缺失特征」用 danger 色：这一列标的是**没通过的特征**，是这一行
            # 之所以未命中的原因，颜色让它一眼可见，而不必逐个读特征名。
            #
            # **必须给 tooltip**：该格是单行 + 省略号，特征多时后半截看不见。
            # 没有 tooltip 就等于「列了全部但看得到一部分」，比只列第一个更糟
            # ——用户以为看到的就是全部。
            missing_text = detail[4] or ""
            missing_item = _read_only_item(missing_text, danger=bool(missing_text))
            if missing_text:
                missing_item.setToolTip(missing_text)
            self.detail_table.setItem(row, 4, missing_item)

    def _show_capture_hint(self, result):
        """按本次识别用到的画面尺寸，弹浮层提示「截图目标可能选错」。

        Args：
            result：`recognize.MatchResult` 实例，需带 `frame_size`。

        Note:
            判据来自 :func:`resolution.capture_warning`——它只做提示、绝不阻断识别。
            **尺寸不同本身不算问题**：识别会缩放，2560×1440 的游戏窗口配 1280×720
            的库基准是合法配置。因此只在「没命中」时才提。

            **同一条提示不重复弹**。实时刷新每 800ms 跑一次识别，若每次都弹，
            屏幕上会不断叠新的浮层，反而把界面糊住。判据是「文案与上一次相同即
            跳过」——文案里带着画面尺寸，只有尺寸变了（用户切换了截图目标）才
            值得再提醒一次。清空记录用 `_reset_capture_hint()`。
        """
        from extensions.capabilities.scene_manager import notify, resolution

        meta = self.library.meta
        text = resolution.capture_warning(
            getattr(result, "frame_size", None),
            meta.base_width,
            meta.base_height,
            matched=result.matched(),
        )
        if not text:
            # 恢复正常（命中、或尺寸对上了）时清掉记录，下次再出问题能再提醒。
            self._last_capture_hint = ""
            return
        if text == self._last_capture_hint:
            return
        self._last_capture_hint = text
        notify.notify_warning(self, text, title="截图目标可能选错")

    def _reset_capture_hint(self):
        """清掉「已提示过」的记录，让下次识别能重新弹浮层。

        Note:
            在「识别当前画面」按钮上调用：用户主动点一次，就是要一个当下的结论，
            哪怕与上次完全相同也该再提醒一次；而实时刷新是自动重复的，靠去重
            避免刷屏。
        """
        self._last_capture_hint = ""

    def _toggle_auto(self):
        """开关实时刷新定时器。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
            self.auto_button.setText(tr("Start Live Refresh"))
            return
        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._on_identify)
        self._timer.start()
        self.auto_button.setText(tr("Stop Live Refresh"))

    # --- 第二段 -------------------------------------------------------------

    def _on_step(self):
        """点击选中区域并观察到达哪个场景。

        Note:
            结果同时走浮层与 `step_label`：浮层让「点完这一下」立刻可见，标签留
            一句话供事后回看（浮层会自己消失）。
        """
        if self._build_bridge is None:
            return
        scene_id = self.scene_combo.currentData()
        area_name = self.area_combo.currentData()
        if not scene_id or not area_name:
            self._step_result("请先选择场景与区域", level="warning")
            return
        scene = self.library.load_scene(scene_id)
        area = scene.find_area(area_name) if scene else None
        if area is None:
            self._step_result("区域不存在", level="error")
            return
        # 等待时长与目标场景都取自路由表。没有路由时用 0.5s 的观察窗——试走的目的
        # 就是「看看点了会到哪」，不必等一条没配的边。
        wait = self.library.route_after_wait(scene_id, area_name)
        try:
            bridge = self._build_bridge()
            # 点击、等待、识别三步一起派到执行器线程（原因见 `_on_identify`）。
            result = bridge.click_and_identify(scene, area, max(0.5, wait))
        except Exception as error:  # noqa: BLE001 — 试走失败只提示
            self._step_result(f"点击失败：{error}", level="error")
            return
        expected_id = self.library.route_target(scene_id, area_name)
        if expected_id:
            expected_scene = self.library.load_scene(expected_id)
            expected = _scene_label(
                expected_scene.name if expected_scene else "",
                expected_id,
            )
        else:
            expected = "（尚未探索出目标）"
        actual = (
            _scene_label(
                result.scene.name if getattr(result, "scene", None) else "",
                result.scene_id(),
            )
            if result.matched()
            else "未识别"
        )
        text = f"点击 {area_name} → 识别为 {actual}；配置的目标为 {expected}"
        if result.frame_changed is False:
            self._step_result(text, level="warning", extra=self._no_effect_hint())
            return
        self._step_result(text, level="success")

    def _step_result(self, text, level="info", extra=None):
        """把试走结果同时写到标签与浮层。

        Args：
            text：结论（含区域名与识别结果）。
            level：浮层级别，语义见 `notify` 模块。
            extra：可选的补充说明（如「点击后画面没变化」的成因与处置）；
                省略时只弹结论。
        """
        from extensions.capabilities.scene_manager import notify

        self.step_label.setText(text)
        if extra:
            notify.notify_warning(self, extra, "点击后画面没有变化")
        else:
            notify.notify(self, text, level)

    def _no_effect_hint(self):
        """点击前后画面没变化时的提示文本。

        这类失败**不报错**：后端把输入发出去了，只是目标程序没响应。最典型的
        成因是交互方式与目标程序不匹配——投递类方式（`PostMessage`）依赖目标
        处理窗口消息，而工具包自绘的程序（如 Tkinter）通常直接读输入队列，不会
        响应投递来的鼠标消息。用户看到的只有「点了没反应」，拿不到任何线索。

        Returns：
            提示文本。
        """
        if _backend_is_background(self._build_bridge):
            return (
                "点击后画面没有变化：当前是后台投递方式，目标程序可能不响应"
                "投递的鼠标消息。若确认目标在前台仍无反应，请把「交互方式」"
                "改为 Pynput 或 PyDirect 重试"
            )
        return "点击后画面没有变化：目标可能未响应，请确认目标窗口未被遮挡"

    # --- 第三段 -------------------------------------------------------------

    def _on_goto(self):
        """执行完整跳转。

        导航会反复点击/等待/识别，整段派到执行器线程；拆成多次派发会打断
        「点完立刻识别」的时序依赖。
        """
        if self._build_bridge is None:
            return
        target = self.target_combo.currentData()
        if not target:
            self._goto_result("请先选择目标场景", level="warning")
            return
        try:
            bridge = self._build_bridge()
            result = bridge.goto_blocking(target)
        except Exception as error:  # noqa: BLE001 — 跳转失败只提示
            self._goto_result(f"跳转失败：{error}", level="error")
            return
        if result.succeeded():
            scenes, _failures = self.library.load_library()
            labels = {scene.scene_id: _scene_label(scene.name, scene.scene_id) for scene in scenes}
            self._goto_result(
                f"到达 {self.target_combo.currentText()}，用时 {result.elapsed:.1f}s",
                level="success",
                detail=result.path_description(labels),
            )
        else:
            self._goto_result(f"跳转失败（{result.status}）：{result.message}", level="error")

    def _goto_result(self, text, level="info", detail=None):
        """把完整跳转的结果同时写到标签与浮层。

        Args：
            text：结论。
            level：浮层级别。
            detail：可选细节（成功时是路径描述）；写进标签与浮层的正文，
                不占标题——标题留给「这是什么级别的消息」，正文放事实。
        """
        from extensions.capabilities.scene_manager import notify

        message = f"{text}\n{detail}" if detail else text
        self.goto_label.setText(f"{text}；路径：{detail}" if detail else text)
        notify.notify(self, message, level)

    # --- 生命周期 -----------------------------------------------------------

    def stop_timer(self):
        """停止实时刷新（页签隐藏时调用，避免后台持续取帧）。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
            self.auto_button.setText(tr("Start Live Refresh"))


def _read_only_item(text, danger=False):
    """造一个不可编辑的表格项。

    只设 `setEditTriggers(NoEditTriggers)` 还不够：双击以外的入口（F2、右键菜单
    在部分平台）也可能进入编辑态。把每个 item 的 `ItemIsEditable` 位去掉是控件级
    与数据级的双重保证。

    Args：
        text：单元格文本。
        danger：为 True 时用主题的 danger 色（「缺失特征」这类问题项）。

    Returns:
        `QTableWidgetItem` 实例。
    """
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if danger:
        item.setForeground(QColor(_theme_color("danger")))
    return item


def _theme_color(name):
    """取主题色，主题不可用时给出安全的兜底色。

    Args：
        name：主题 token 名（如 `danger`）。

    Returns:
        `QColor` 实例。

    Note:
        只在主题未安装时才有兜底：测试环境与「主题加载失败」都会走到这里。
        兜底取深红 `#B42318`（与亮色主题的 danger 同值），保证在任何底色上都
        清晰可读；返回 `None` 会让 `QColor(None)` 造出无效色，文字直接变黑——
        那样提示就失去醒目作用，而且不报错。
    """
    from ok.ui.qt.kit import theme

    try:
        return theme.color(name)
    except (KeyError, AttributeError):
        return QColor("#B42318")


def _scene_label(name, scene_id):
    """把场景名与 id 拼成表格里显示的一格。

    Args：
        name：场景显示名；可能为空（旧格式明细不带名称）。
        scene_id：场景 id。

    Returns：
        `名称（scene-0001）` 形式；没有名称或名称与 id 相同时只显示 id，不重复。

    Note:
        名称与 id 都保留：名称是给人读的，id 是排查时用来定位 `ok_scenes/<id>/`
        目录的，两者缺一都会让用户再点一次编辑弹窗。
        名称等于 id 时不重复显示——新建场景默认就是这个状态，重复会显示成
        `scene-0002（scene-0002）`。
    """
    if not name or name == scene_id:
        return scene_id
    return f"{name}（{scene_id}）"


def _percent_text(value):
    """把 0–1 的相似度格式化为百分比。

    Args：
        value：相似度；None 表示该行没有相似度数据。

    Returns：
        形如 `93%` 的文本；`None` 时返回 `—`——那种行的相似度是**未知**，
        显示 `0%` 会被读成「算出 0」。

    Note:
        非有限值（`nan` / `inf`）同样返回 `—`。源头已在 `recognize.match_template`
        归零，这里是**第二道防线**：相似度经由均值、聚合等多层传递，任一层漏掉
        都会让界面出现 `nan%`——那串字符对排查毫无帮助，还会盖住真正的原因。
    """
    if value is None:
        return "—"
    if not math.isfinite(value):
        return "—"
    return f"{value * 100:.0f}%"


def _timing_text(result):
    """把识别耗时格式化为界面展示文本。

    Args：
        result：`recognize.MatchResult` 实例。

    Returns：
        形如 `耗时 64 ms` 的文本；`duration` 为 0（库为空或缺基准时的兜底结果，
        并没有真正识别）时返回 `未实际识别`——显示 `0 ms` 会被读成「快到没耗时」，
        与事实相反。
    """
    duration = getattr(result, "duration", 0.0)
    if not duration:
        return "未实际识别"
    milliseconds = duration * 1000
    if milliseconds < 10:
        return f"耗时 {milliseconds:.1f} ms"
    return f"耗时 {milliseconds:.0f} ms"


def build_route_summary(steps, labeler):
    """把路径格式化为可读文本。

    Args：
        steps：`graph.RouteStep` 列表。
        labeler：`(scene_id) -> str` 的显示名函数。

    Returns：
        形如 `A --btn--> B ; B --ok--> C` 的字符串；空路径时为 `（无需移动）`。
    """
    if not steps:
        return "（无需移动）"
    return " ; ".join(f"{labeler(step.from_scene)} --{step.area_name}--> {labeler(step.to_scene)}" for step in steps)


def describe_options(options):
    """把导航参数格式化为可读文本（供测试页展示当前参数）。

    Args：
        options：`navigate.NavigateOptions` 实例。

    Returns：
        一行说明。
    """
    return f"重试 {options.retry} 次 / 回退上限 {options.rollback_limit} / 单步超时 {options.step_timeout:.1f}s"
