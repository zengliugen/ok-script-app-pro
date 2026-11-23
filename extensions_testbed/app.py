"""测试靶场的主窗口装配。

职责：建窗口与画布、按 `layout.REGIONS` 绘制全部 9 个区域、安装 WndProc 挂钩、
绑定输入事件与快捷键、驱动帧计数与心跳、写 `ready` 事件。

**绘制分层**（决定各区域的实际变化时机，须与 `layout.REGIONS` 的 `change_when`
一致，否则「按区域校验变化方式」这一自证手段会失效）：

| 层 | 内容 | 重绘时机 |
|---|---|---|
| `chrome` | 区域底色、区域标题、边框、静态色卡、快捷键提示 | 仅初始化 / 语言切换 |
| `screen` | 画面标题、版本标签、画面主体、覆盖层（弹窗） | 画面切换 / 语言切换 |
| `always` | 自述区、帧计数器、动画区 | 每帧（约 60Hz） |
| `input` | 原始消息计数、应用计数（含状态横幅底色） | 收到输入时 |
| `text` | 文本输入区 | 文本变化时 |

**画面元素会切换画面**：画面的按钮带着真实动作（见 `screens.Element.action`）——
点击后靶场真的会跳到目标画面。这是 T0-4 的关键改动：T0-3 时靶场只有 `entry` 一个
画面，点击按钮不改变界面，于是点击有效性验证只能声明 `expect_change: false`；
补全 8 个画面后，点击**真的**切换画面，配置侧便可如实声明 `expect_change: true`
并配 `goto` 边（`extensions_testbed/README.md` §7.3 记录的待办由此闭合）。

**持续动画**：`battle` 的进度条与倒计时由每帧回调更新（见 `screens.ScreenView.tick`），
是本靶场**唯一**在无输入时变化的画面内容——它是刻意保留的例外，用于验证
`ignore_regions`（`extensions_testbed/README.md` §3.2）。
"""

from __future__ import annotations

import ctypes
import time
import tkinter as tk

from extensions_testbed import anim, click_claim, colorcard, gestures, l10n, layout, screens
from extensions_testbed.counters import CounterStore
from extensions_testbed.eventlog import EventLog
from extensions_testbed.wndproc import WndProcHook

# 各区域的底色（互不相同，使区域边界在截图上一眼可辨——M0 §4 的布局要求）
_REGION_BG: dict[str, str] = {
    "status_bar": "#181818",
    "screen_header": "#000000",
    "screen_body": "#000000",
    "color_card": "#303030",
    "frame_counter": "#101010",
    "raw_counters": "#1C1C1C",
    "app_counters": "#2A4A2A",
    "anim_region": "#202020",
    "text_field": "#101810",
}

# 状态横幅的可循环底色（`F3` 依次切换）
BANNER_COLORS: tuple[str, ...] = ("#2A4A2A", "#4A2A2A", "#2A2A4A", "#4A4A2A")

# 动画区色块使用的颜色（避开品红——它是 M0 遮挡测试的标记色）
_ANIM_BLOCK_COLOR = "#40C0FF"

# 分隔线颜色：区域之间画 1px 亮线，便于机器按坐标核对布局
_BORDER_COLOR = "#606060"


class _ShiftedEvent:
    """事件坐标换算成客户区坐标后的代理（其余属性原样透传）。

    关卡列表画在子画布里，Tk 上报的 `%x/%y` 以**事件发生的窗口**为基准；
    不换算会让列表内点击落到错误元素上（双击图标进不了详情）。
    """

    def __init__(self, event, x, y):
        self._event = event
        self.x = x
        self.y = y

    def __getattr__(self, name):
        return getattr(self._event, name)


class TestbedApp:
    """测试靶场的主窗口。

    Attributes:
        screen: 画面状态机（当前画面与绘制）。
        counters: 两组计数（原始消息 / 应用感知）。
    """

    def __init__(
        self,
        *,
        log: EventLog,
        width: int = layout.BASE_W,
        height: int = layout.BASE_H,
        lang: str = "zh",
        screen_id: str | None = None,
    ) -> None:
        """建窗口、画布与各层面板（**不进入事件循环**）。

        Args:
            log: 事件日志写入器。
            width: 客户区宽度（像素）。
            height: 客户区高度（像素）。
            lang: 初始界面语言。
            screen_id: 启动时进入的画面；None 时用 `entry`（`launch.entry_screen`）。

        Raises:
            RuntimeError: 画面元素的文案键缺失时抛出（界面会显示字面量键名）。
            KeyError: 指定的画面未定义时抛出。

        Note:
            `screen_id` 供开发工具使用（`make_templates.py` 需逐个画面裁模板图，
            而靶场启动后默认停在 `entry`）。正常由 `run_testbed.py` 启动时不传该参数，
            起点由适配层的 `launch.entry_screen` 决定。
        """
        self.log = log
        self.width = width
        self.height = height
        self.lang = lang if lang in l10n.LANGUAGES else "zh"

        self.counters = CounterStore()
        self._anim_on = False
        self._colorcard_on = True
        self._status_on = True
        self._banner_index = 0
        # 鼠标按下位置与时刻（手势判定用：按下 → 抬起构成一次 drag / swipe）
        self._press_origin: tuple[int, int] | None = None
        self._press_time = 0.0
        # 滑块拖动锚点：按下时的横坐标与数值。拖动过程中按「锚点 + 相对位移」
        # 实时换算，抬起时再用同一锚点算最终值——不会把位移重复叠加。
        self._slider_drag: dict | None = None
        # 滚动条拖动锚点：按下点在滑块内的偏移，拖动时据此换算滑块顶端位置。
        self._scrollbar_drag: dict | None = None
        # 列表内拖拽锚点：按下时的 y 与滚动行，拖动时按行距换算滚几行。
        self._grid_drag: dict | None = None

        # 布局自检：9 个区域必须恰好铺满客户区（「裁剪后余下部分」的前提），
        # 且各区域高度足以容纳「标题带 + 内容」（防标题与内容叠字 / 内容被裁）
        layout.check_tiled(width, height)
        layout.check_capacity(width, height)
        # 画面元素自检：所有元素必须落在其所属区域内且两两不重叠，
        # 否则会压进相邻区域或让点击归属不确定（T0-2 曾出现 exit_btn 越界；
        # T0-4 曾出现两个按钮完全重叠）
        screens.check_elements_inside()
        # 文案自检：区域标题的键由 `header.<region_id>` 动态拼出，拼错不会报错，
        # 只会让界面显示字面量键名——故启动时显式检出。
        # 画面元素与画面标题的键同理（`screen_text` 也静默回退成键名）。
        missing = l10n.check_keys([f"header.{item.id}" for item in layout.REGIONS])
        missing.extend(screens.check_label_keys())
        if missing:
            raise RuntimeError(f"文案表缺少键：{'、'.join(missing)}（界面会显示字面量键名）")

        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.geometry(f"{width}x{height}")
        self.root.resizable(False, False)
        # 深色底：与各区域底色协调，且避免默认主题色干扰色卡采样
        self.root.configure(bg="#000000")
        self.canvas = tk.Canvas(
            self.root,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg="#000000",
        )
        self.canvas.pack(fill="both", expand=True)

        self.screen = screens.ScreenView(self.canvas, width, height, self.lang, screen_id=screen_id)
        self._hook: WndProcHook | None = None
        self._ticker: anim.Ticker | None = None
        self._fps = 0.0
        self._closing = False
        # WndProc 回调置位、每帧消费；用于合并高频消息带来的重绘请求
        self._raw_dirty = False
        self._top_hwnd_cache: int | None = None
        # `always` 层的持久画布项（首次绘制时创建，之后只更新，不删除重建）
        self._always_ids: dict[str, int] | None = None
        # 后台点击兜接：判定哪些点击是 Tk 丢弃的投递消息（见 `click_claim`）
        self.claimer = click_claim.ClickClaimer()
        # 兜接路径的双击判定用：`(时刻, x, y)`，只在 `_dispatch_click` 里维护
        self._last_click: tuple[float, int, int] | None = None

    # ---------- 生命周期 ----------

    def run(self) -> int:
        """装配完成后进入事件循环。

        Returns:
            进程退出码（正常关闭为 0）。
        """
        self.root.update_idletasks()
        hwnds = self._hook_hwnds()
        self._install_hook(hwnds)
        self._bind_events()
        self._draw_all()
        self._write_ready(hwnds)

        self._ticker = anim.Ticker(self.root, self._on_tick, self._on_heartbeat)
        self._ticker.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()
        return 0

    def _hook_hwnds(self) -> list[int]:
        """取需要挂钩的全部窗口句柄。

        Returns:
            待挂钩的窗口句柄列表——顶层窗口、客户区容器窗口与画布窗口。

        Note:
            **实测发现（T0-2，重要）**：Tk 在 Windows 上有三层窗口，且**鼠标与
            键盘消息投递给不同的子窗口**：

            | 层 | 类名 | 收到的消息 |
            |---|---|---|
            | 顶层 | `TkTopLevel` | 仅 `WM_ACTIVATE` / `WM_MOUSEACTIVATE` 等窗口级消息 |
            | 客户区容器 | `TkChild`（`root.winfo_id()`） | **键盘**消息（`WM_KEYDOWN` / `WM_CHAR`） |
            | 画布控件 | `TkChild`（`canvas.winfo_id()`） | **鼠标**消息（`WM_LBUTTONDOWN` 等） |

            实测证据（对三层及全部子树挂钩后注入输入）：点击客户区时
            `WM_LBUTTONDOWN` 只出现在 `canvas.winfo_id()`；按键时 `WM_KEYDOWN`
            只出现在 `root.winfo_id()`；两者互不交叉。

            **顶层窗口也必须挂钩**（后台点击的关键）。自动化工具的
            `PostMessage` 后端默认把点击投给**顶层窗口**：它先找
            `hwnd_window.hwnds` 里坐标命中的子窗口，为空时退回 `top_hwnd`，
            而 `top_hwnd` 正是 `TkTopLevel`。若只有两个 `TkChild` 被挂钩，
            投到顶层的那次点击谁也看不见——表现为「消息发出去了，靶场却一点
            反应都没有」，且原始消息计数完全不动，极难归因。

            真实输入不受影响：它由系统投给实际命中的子窗口，顶层窗口收不到
            鼠标消息，因此这里多挂一层不会造成重复计数。
        """
        # 去重：正常情况下三者互不相同，但在某些 WM 下 `root.winfo_id()`
        # 就是顶层窗口本身。重复挂钩会让同一条消息被计数两次，
        # 干扰「注入点 vs 实收坐标」的核对。
        return list(dict.fromkeys([self._top_hwnd(), self.root.winfo_id(), self.canvas.winfo_id()]))

    def _top_hwnd(self) -> int:
        """取顶层窗口句柄（`TkTopLevel`）。

        Returns:
            顶层窗口句柄。

        Note:
            前台判定与 `ready` 事件的 `class_name` / `title` 都用顶层窗口：
            适配层 `window.candidates` 匹配的正是顶层窗口（其 `class_name` 为
            `TkTopLevel`），而 `GetForegroundWindow()` 返回的也是顶层窗口——
            与画布窗口比较永远不相等，会把「前台」误报为「后台」。
        """
        if self._top_hwnd_cache is None:
            user32 = ctypes.windll.user32
            hwnd = self.root.winfo_id()
            while True:
                parent = user32.GetParent(hwnd)
                # GetParent 返回 0 表示已到顶层；此时 hwnd 即目标
                if not parent:
                    self._top_hwnd_cache = hwnd
                    break
                hwnd = parent
        return self._top_hwnd_cache

    def _install_hook(self, hwnds: list[int]) -> None:
        """安装 WndProc 挂钩。

        Args:
            hwnds: 待挂钩的窗口句柄列表（客户区容器 + 画布）。
        """
        self._hook = WndProcHook(hwnds, self.counters, self.log, on_message=self._peek_raw)
        ok = self._hook.install()
        top = self._top_hwnd()
        self.log.write(
            "note",
            event="wndproc_hook",
            ok=ok,
            hwnds=hwnds,
            hook_classes=[self._class_name(hwnd) for hwnd in hwnds],
            top_hwnd=top,
            top_class=self._class_name(top),
            title=self._window_title(top),
        )
        if ok:
            # 挂钩装好后主动刷新一次计数区，使界面立刻反映初始状态
            self._draw_input()

    def _on_close(self) -> None:
        """处理 `WM_CLOSE`：卸载挂钩、停循环、关日志。"""
        if self._closing:
            return
        self._closing = True
        self.log.write("note", event="closing")
        if self._hook is not None:
            self._hook.uninstall()
        if self._ticker is not None:
            self._ticker.stop()
        self.log.close()
        self.root.destroy()

    # ---------- 事件绑定 ----------

    def _bind_events(self) -> None:
        """绑定输入事件与快捷键。

        Note:
            绑定挂在**顶层窗口**上：Tk 的 bindtags 使子控件（画布）收到的事件
            继续冒泡到顶层，故点击客户区任意位置都会被计入（能力 C 的要求——
            不允许存在「点了没反应」的死区）。
        """
        self.root.bind("<Button-1>", lambda e: self._on_button_down(self._shift_event(e)), add="+")
        self.root.bind("<ButtonRelease-1>", lambda e: self._on_button_up(self._shift_event(e)), add="+")
        self.root.bind("<Double-Button-1>", lambda e: self._on_double(self._shift_event(e)), add="+")
        self.root.bind("<Motion>", lambda e: self._on_motion(self._shift_event(e)), add="+")
        self.root.bind("<MouseWheel>", lambda e: self._on_wheel(self._shift_event(e)), add="+")
        self.root.bind("<Button-2>", lambda e: self._on_button_down(self._shift_event(e)), add="+")
        self.root.bind("<KeyPress>", self._on_key, add="+")

    def _shift_event(self, event):
        """把事件坐标换算成客户区坐标。

        Args:
            event: Tk 事件。

        Returns:
            原事件，或带客户区坐标的事件代理。

        Note:
            关卡列表画在子画布里，Tk 上报的坐标以子画布为基准；不换算会让列表
            内的点击落到错误元素上（双击图标进不了详情）。
        """
        x, y = self.screen.root_xy(event.x, event.y, event.widget)
        if x == event.x and y == event.y:
            return event
        return _ShiftedEvent(event, x, y)

    def _peek_raw(self, msg: int = 0, wparam: int = 0, lparam: int = 0) -> None:
        """WndProc 收到已挂钩消息时的回调：记录待定点击 + 标记待刷新。

        Args:
            msg: 消息号（由 `WndProcHook.on_message` 传入）。
            wparam: 消息的 `wParam`（本方法不使用）。
            lparam: 消息的 `lParam`，点击消息里是客户区坐标。

        Note:
            由 Win32 消息驱动，即使 Tk 未把该消息转成事件也会触发——这正是
            「消息到达」与「应用感知」两组计数能够互相印证的原因。
            `WM_MOUSEMOVE` 同样会触发本回调（每秒数百次），若每次都重绘会让界面
            卡死，故此处只置标记，由每帧的 `_flush_raw` 合并刷新。

            **点击消息要额外转给 `claimer`**：那是后台点击兜接的判据来源。
            `claimer.note_message` 只写数据、不碰界面——本回调可能在非主线程执行，
            在此调任何 Tk 接口都会让解释器崩溃（实测 `PyEval_RestoreThread`）。
        """
        del wparam
        self.claimer.note_message(msg, lparam)
        self._raw_dirty = True

    def _flush_raw(self) -> None:
        """若收到过原始消息则刷新计数区（每帧至多一次）。"""
        if not self._raw_dirty:
            return
        self._raw_dirty = False
        self._draw_input()

    def _on_button_down(self, event: tk.Event) -> None:
        """处理鼠标按下（应用级计数 + 界面响应 + 记录手势起点）。

        Args:
            event: Tk 事件对象。

        Note:
            第一件事是**认领**对应的窗口消息：这条 Tk 事件证明该点击已被 Tk 处理，
            后台点击兜接据此跳过它，避免同一次点击被处理两次（见 `click_claim`）。
        """
        self.claimer.claim(event.x, event.y)
        self.counters.touch_app("mouse_down")
        self.counters.set_app("last_mouse_xy", [event.x, event.y])
        # 记录手势起点与时刻：抬起时据此判定是单击还是 drag / swipe
        self._press_origin = (event.x, event.y)
        self._press_time = time.monotonic()
        # 按在滑块上时记下锚点：拖动过程中按「锚点 + 相对位移」实时更新数值，
        # 不再等抬起才算——否则拖动时数值与手柄纹丝不动，像界面卡住了。
        element = self.screen.hit_test(event.x, event.y)
        # 只有左键（`event.num == 1`）按在滑块上才建立锚点：本回调也绑了中键，
        # 中键拖动不该改数值。
        if event.num == 1 and element is not None and element.kind == "slider":
            self._slider_drag = {"x": event.x, "value": self.screen.slider_value}
        else:
            self._slider_drag = None
        # 关卡列表的滚动条：按住滑块（或点在轨道上）即可拖动滚动。
        self._scrollbar_drag = self.screen.begin_scrollbar_drag(event.x, event.y) if event.num == 1 else None
        # 列表区域内的拖拽滚动：按住内容往上/下拖（与滚动条是两条独立入口）。
        self._grid_drag = self.screen.begin_grid_drag(event.x, event.y) if event.num == 1 else None
        self.log.write(
            "app",
            event=event.type,
            mouse_position=[event.x, event.y],
            counters=self.counters.app_snapshot(),
        )
        self._draw_input()

    def _on_button_up(self, event: tk.Event) -> None:
        """处理鼠标抬起：识别手势或元素点击，并执行对应动作。

        Args:
            event: Tk 事件对象。

        Note:
            判定顺序是**先手势后点击**：手势（位移超过阈值）与点击（位移极小）
            互斥，故顺序不影响结果，但先判手势能让日志更易读——一次拖动不应
            同时记成 `element_click`。
        """
        self.counters.touch_app("mouse_up")
        self.counters.set_app("last_mouse_xy", [event.x, event.y])
        self.log.write(
            "app",
            event=event.type,
            mouse_position=[event.x, event.y],
            counters=self.counters.app_snapshot(),
        )

        origin = self._press_origin
        self._press_origin = None
        slider_anchor = self._slider_drag
        self._slider_drag = None
        scrollbar_anchor = self._scrollbar_drag
        self._scrollbar_drag = None
        grid_anchor = self._grid_drag
        self._grid_drag = None
        if grid_anchor is not None:
            # 列表内拖拽同样留可观测结果：只有移动超过半行才算一次滚动，
            # 仅按下抬起（未移动）时行号不变，日志里的 scroll_row 能直接看出来。
            self.log.write(
                "app",
                event="grid_drag",
                scroll_row=self.screen.icon_scroll_row,
                mouse_position=[event.x, event.y],
                counters=self.counters.app_snapshot(),
            )
        if scrollbar_anchor is not None:
            # 拖动滚动条也要留下可观测的结果：自动化侧据此确认「拖了列表」，
            # 否则只能看到按下/抬起两条消息，无法判断是否真的滚动了。
            self.log.write(
                "app",
                event="scrollbar_drag",
                scroll_row=self.screen.icon_scroll_row,
                mouse_position=[event.x, event.y],
                counters=self.counters.app_snapshot(),
            )
        if origin is not None:
            duration_ms = (time.monotonic() - self._press_time) * 1000.0
            gesture = gestures.classify(origin, (event.x, event.y), duration_ms)
            if gesture is not None:
                self._handle_gesture(gesture, slider_anchor=slider_anchor)
                self._draw_input()
                return

        element = self.screen.hit_test(event.x, event.y)
        if element is not None:
            self.log.write(
                "app",
                event="element_click",
                element=element.name,
                screen=self.screen.screen_id,
                popup=self.screen.popup,
                mouse_position=[event.x, event.y],
            )
            self._perform_action(element)
        self._draw_input()

    def _on_double(self, event: tk.Event) -> None:
        """处理双击（图标网格的双击才有动作）。

        Args:
            event: Tk 事件对象。

        Note:
            双击前必然先有一次单击。`chapter_list` 的单击动作刻意留空
            （见 `screens.SCREENS`），故「第一次单击」不会提前跳到别的画面，
            双击得以成立。
        """
        self.counters.touch_app("double")
        self.log.write(
            "app",
            event=event.type,
            mouse_position=[event.x, event.y],
            counters=self.counters.app_snapshot(),
        )
        element = self.screen.hit_test(event.x, event.y)
        if element is not None and element.double_action:
            self.log.write(
                "app",
                event="element_double_click",
                element=element.name,
                screen=self.screen.screen_id,
                mouse_position=[event.x, event.y],
            )
            self._switch_screen(element.double_action)
        self._draw_input()

    def _handle_gesture(self, gesture: gestures.GestureResult, slider_anchor=None) -> None:
        """处理一次手势（drag / swipe）。

        Args:
            gesture: 手势判定结果。
            slider_anchor: 按在滑块上时的拖动锚点（`{"x", "value"}`）；省略时
                按「当前值 + 位移」更新。拖动过程中已按同一锚点实时更新，
                这里用锚点重算最终值，保证与画面一致且**不重复累加位移**。

        Note:
            手势**只在落到元素上时才有副作用**：落在空白处的手势仅记录日志。
            这与「全窗口响应区」（能力 C）不冲突——手势区本身不切换画面，
            它的价值在于**产生可观测的变化**（滑块数值）或**被日志如实记录**
            （闪避区），从而让配置侧的 `drag` / `swipe` 判据有依据可循。
        """
        self.log.write(
            "app",
            event="gesture",
            kind=gesture.kind,
            start=list(gesture.start),
            end=list(gesture.end),
            distance=round(gesture.distance, 1),
            duration_ms=round(gesture.duration_ms, 1),
            detail=gesture.describe(),
        )
        element = self.screen.hit_test(*gesture.start)
        if element is None:
            return
        if element.kind == "slider":
            # 滑块：按水平位移更新数值，数值变化即「拖动生效」的观测证据
            if slider_anchor is not None:
                value = self.screen.set_slider_from_drag(slider_anchor["value"], gesture.dx)
            else:
                value = self.screen.drag_slider(gesture.dx)
            self.log.write(
                "app",
                event="slider_drag",
                element=element.name,
                screen=self.screen.screen_id,
                value=value,
                distance=round(gesture.distance, 1),
            )
            self.counters.touch_app("drag")
        elif element.kind == "zone":
            # 手势区（闪避）：记录动作但不改画面——配置侧据此声明 expect_change: false
            self.log.write(
                "app",
                event="zone_swipe",
                element=element.name,
                screen=self.screen.screen_id,
                kind=gesture.kind,
                dx=gesture.dx,
                dy=gesture.dy,
            )

    def _perform_action(self, element: screens.Element) -> None:
        """执行元素声明的单击动作。

        Args:
            element: 被点击的元素。

        Note:
            **覆盖层的元素也走这里**——它的动作是 `:popup_hide`，
            与被覆盖画面的元素动作互不干扰（`hit_test` 在弹窗显示时只在弹窗内搜索）。
        """
        action = element.action
        if not action:
            return
        if action == screens.ACTION_CLOSE:
            self.log.write("note", event="element_action", element=element.name, action="close")
            self._on_close()
            return
        if action == screens.ACTION_TOGGLE_LANG:
            self._toggle_language(element.name)
            return
        if action == screens.ACTION_POPUP_HIDE:
            if self.screen.hide_popup():
                self.log.write("note", event="element_action", element=element.name, action="popup_hide")
                self._draw_all()
            return
        self._switch_screen(action)

    def _switch_screen(self, screen_id: str) -> None:
        """切换画面并同步各层（画面内容在 `screen` 层，帧计数等在 `always` 层）。

        Args:
            screen_id: 目标画面 id。

        Note:
            `_draw_all` 而非只重绘 `screen` 层：自述区显示当前画面 id，
            须随之更新；且 `F4` / 弹窗显隐后 z 序需重排（见 `_raise_layers`）。
        """
        old = self.screen.screen_id
        self.screen.switch(screen_id)
        self.log.write(
            "note",
            event="screen_switch",
            from_screen=old,
            to_screen=self.screen.screen_id,
            reason="element_action",
        )
        self._draw_all()

    def _toggle_language(self, source: str) -> None:
        """切换界面语言（`F10` 与 `settings_menu.lang_toggle_btn` 共用）。

        Args:
            source: 触发来源（快捷键名或元素名），写入日志便于区分两条入口。

        Note:
            切换语言会触发整屏重绘（`_draw_all`）：画面标题与元素文案都要更新。
            图标网格**不重新洗牌**——语言切换不是「进入画面」，
            若跟着洗牌就破坏了「每次进入变一次」的语义（见 `screens.ScreenView`）。
        """
        index = l10n.LANGUAGES.index(self.lang)
        self.lang = l10n.LANGUAGES[(index + 1) % len(l10n.LANGUAGES)]
        self.screen.lang = self.lang
        self.log.write("note", event="lang_switch", source=source, lang=self.lang)
        self._draw_all()

    def _on_motion(self, event: tk.Event) -> None:
        """处理鼠标移动。

        Args:
            event: Tk 事件对象。

        Note:
            鼠标移动事件频率极高（每秒可达数百次）。此处**只计数、不写日志**，
            避免日志膨胀与界面卡顿。唯一例外是**按着滑块拖动**：按「锚点 + 相对
            位移」实时更新数值，让滑块跟手；`set_slider_value` 在数值未变时不重绘，
            因此普通移动仍然零开销。
        """
        self.counters.touch_app("motion")
        self.counters.set_app("last_mouse_xy", [event.x, event.y])
        # 0x0100 是 Tk 的 Button1 掩码：只有左键仍按着时才跟手。
        if self._slider_drag is not None and event.state & 0x0100:
            self.screen.set_slider_from_drag(self._slider_drag["value"], event.x - self._slider_drag["x"])
        if self._scrollbar_drag is not None and event.state & 0x0100:
            self.screen.drag_scrollbar_to(event.y, self._scrollbar_drag["offset"])
        if self._grid_drag is not None and event.state & 0x0100:
            self.screen.drag_grid_to(event.y, self._grid_drag["y"], self._grid_drag["px"])

    def _on_wheel(self, event: tk.Event) -> None:
        """处理滚轮：计数、写日志，并在关卡列表上竖向滚动网格。

        Args:
            event: Tk 事件对象。

        Note:
            滚动只发生在 `chapter_select` 的图标网格上（其他画面 `scroll_icon_grid`
            返回 False，不重绘）。日志里记 `scrolled`，便于自动化侧确认「滚轮确实
            滚动了列表」而不是只看到一条滚轮事件。
        """
        self.counters.touch_app("wheel")
        scrolled = self.screen.scroll_icon_grid(event.delta)
        self.log.write(
            "app",
            event=event.type,
            delta=event.delta,
            scrolled=scrolled,
            scroll_row=self.screen.icon_scroll_row,
            mouse_position=[event.x, event.y],
            counters=self.counters.app_snapshot(),
        )
        self._draw_input()

    def _on_key(self, event: tk.Event) -> None:
        """处理按键：先分发快捷键，再计入应用级计数。

        Args:
            event: Tk 事件对象。

        Note:
            快捷键**同时也是输入测试对象**，故功能键也照常计入计数
            （`M0-诊断场景需求.md` §5）。
            `Esc` 明确不做任何事——避免长时间测试中误触退出。
        """
        self.counters.touch_app("key_down")
        self.counters.set_app("last_key", event.keysym)
        handled = self._dispatch_hotkey(event.keysym)

        if not handled and event.char and event.char.isprintable():
            self.counters.touch_app("char")
            self.counters.append_text(event.char)
            self._draw_text()

        self.log.write(
            "app",
            event=event.type,
            keysym=event.keysym,
            keycode=event.keycode,
            char=event.char or None,
            hotkey=handled,
            counters=self.counters.app_snapshot(),
        )
        self._draw_input()

    # ---------- 快捷键 ----------

    def _dispatch_hotkey(self, keysym: str) -> bool:
        """分发快捷键。

        Args:
            keysym: Tk 的按键符号名。

        Returns:
            是否为已处理的快捷键（True 表示已消费，不再作为文本输入）。

        Note:
            `F7`（下一画面）与 `F9`（弹窗）在**弹窗显示时都不生效**：
            弹窗是模态覆盖层，先关它才谈得上切画面；否则「当前画面」与
            「可见内容」不一致，据画面判定得出的结论会失真。
            `Esc` 在有弹窗时关闭弹窗，无弹窗时**不做任何事**——后者是硬要求，
            避免长时间测试中误触退出（`M0-诊断场景需求.md` §5）；前者使适配层
            `recovery.actions` 里的 `key: esc` 真正具备恢复能力。
        """
        if keysym == "F1":
            self._anim_on = not self._anim_on
            self.log.write("note", event="hotkey", key="F1", anim_on=self._anim_on)
            self._draw_always()
            return True
        if keysym == "F3":
            self._banner_index = (self._banner_index + 1) % len(BANNER_COLORS)
            self.log.write("note", event="hotkey", key="F3", banner=self._banner_index)
            self._draw_input()
            return True
        if keysym == "F4":
            self._colorcard_on = not self._colorcard_on
            self.log.write("note", event="hotkey", key="F4", colorcard_on=self._colorcard_on)
            # 色卡属于 chrome 层，与画面同层；单项重绘会让 chrome 压到其他层之上，
            # 故此处整层重绘（z 序由创建顺序决定，必须按底到顶重建）
            self._draw_all()
            return True
        if keysym == "F5":
            self.counters.reset()
            self.log.write("note", event="hotkey", key="F5")
            self._draw_input()
            self._draw_text()
            return True
        if keysym == "F6":
            self._status_on = not self._status_on
            self.log.write("note", event="hotkey", key="F6", status_on=self._status_on)
            self._draw_always()
            return True
        if keysym == "F7":
            if self.screen.popup is not None:
                # 弹窗未关时不切画面：此时界面内容归弹窗，切了也看不见
                self.log.write("note", event="hotkey", key="F7", ignored="popup_open")
                return True
            target = self.screen.next_screen()
            self.log.write("note", event="hotkey", key="F7", screen=target)
            self._draw_all()
            return True
        if keysym == "F9":
            if self.screen.show_popup():
                self.log.write("note", event="hotkey", key="F9", screen=screens.OVERLAY_SCREEN)
                self._draw_all()
            else:
                self.log.write("note", event="hotkey", key="F9", already_open=True)
            return True
        if keysym == "F10":
            self._toggle_language("F10")
            return True
        if keysym == "Escape":
            # 有弹窗时关闭它——这使适配层 recovery.actions 的 `key: esc` 具备恢复能力。
            # 无弹窗时**刻意不做任何事**：避免测试过程中误触退出（M0 §5 的硬要求）。
            if self.screen.hide_popup():
                self.log.write("note", event="hotkey", key="Escape", action="popup_hide")
                self._draw_all()
            return True
        return False

    # ---------- 绘制 ----------

    def _rect(self, region_id: str) -> tuple[int, int, int, int]:
        """取区域在当前客户区尺寸下的矩形。

        Args:
            region_id: 区域标识。

        Returns:
            实际像素矩形。
        """
        return layout.scaled_rect(region_id, self.width, self.height)

    def _content(self, region_id: str) -> tuple[int, int, int, int]:
        """取区域**内容区**的矩形（已扣掉顶部标题带）。

        Args:
            region_id: 区域标识。

        Returns:
            实际像素矩形。

        Note:
            所有区域内容都必须画在这个矩形内。画在区域矩形里会侵占标题带，
            与标题叠字（T0-2 曾因此出现 5 处重叠）。
        """
        return layout.content_rect(region_id, self.width, self.height)

    def _px(self, size: int) -> int:
        """把基准字号换算为当前尺寸下的字号（像素）。

        Args:
            size: 基准字号。

        Returns:
            实际字号。

        Note:
            字号用**负数**传给 Tk——负值表示「像素」而非「点」，这样不受
            `tk scaling` 影响，字号与布局的比例关系严格可控。
        """
        return -max(8, round(size * self.height / layout.BASE_H))

    def _draw_all(self) -> None:
        """按 z 序重绘全部层（底 → 顶）。"""
        self._draw_chrome()
        self.screen.draw()
        self._draw_always()
        self._draw_input()
        self._draw_text()
        self._raise_layers()

    def _raise_layers(self) -> None:
        """按固定顺序把各层置顶，使 z 序不依赖创建顺序。

        Note:
            `tag_raise` 把目标标签的**全部项**提到最顶层，故必须**从底到顶依次调用**：
            最后调用的标签位于最上方。若顺序颠倒，区域底色会盖住画面内容。
            `chrome` 层会被重绘（`F4` / `F10`），重绘出的项天然位于其他层之上，
            故每次整层重绘后统一重排一次，使分层关系始终确定。
        """
        for tag in ("chrome_bg", "screen", "chrome", "always", "input", "text"):
            self.canvas.tag_raise(tag)

    def _draw_chrome(self) -> None:
        """绘制 `chrome` 层：区域底色、标题、边框、静态色卡、快捷键提示。

        Note:
            先建全部底色与边框（`chrome_bg`），再建色卡与快捷键提示，最后建标题文本
            （`chrome`）——Tk 按创建顺序决定 z 序，故标题必须最后建才不被底色覆盖。
        """
        canvas = self.canvas
        canvas.delete("chrome")
        canvas.delete("chrome_bg")

        for item in layout.REGIONS:
            x1, y1, x2, y2 = self._rect(item.id)
            bg = _REGION_BG[item.id]
            if item.id == "app_counters":
                # 应用计数区的底色即状态横幅（F3 切换），由 input 层负责
                bg = BANNER_COLORS[self._banner_index]
            canvas.create_rectangle(x1, y1, x2, y2, fill=bg, outline="", tags=("chrome_bg",))
            canvas.create_line(x1, y1, x2 - 1, y1, fill=_BORDER_COLOR, tags=("chrome_bg",))
            canvas.create_line(x1, y1, x1, y2 - 1, fill=_BORDER_COLOR, tags=("chrome_bg",))

        # 静态色卡（能力 E）：只有开启时才画色块，关闭时保留底色
        if self._colorcard_on:
            cx1, cy1, cx2, cy2 = self._content("color_card")
            # 标为 chrome_bg 层：色块在区域底色之上、但仍在标题与画面内容之下
            colorcard.draw(canvas, cx1, cy1, cx2, cy2, tags=("chrome_bg",))

        # 快捷键提示（静态），置于动画区右端的内容区，避免占用其他区域
        self._draw_hotkeys()

        # 区域标题最后绘制：Tk 按创建顺序决定 z 序，标题须盖在色卡与提示之上。
        # 只画在 LABELED_REGIONS 登记的区域——画面标题区与主体区承载被识别的画面
        # 内容，往其中画区域标题会污染模板匹配与 OCR 的 ROI。
        for item in layout.REGIONS:
            if item.id not in layout.LABELED_REGIONS:
                continue
            x1, y1, _x2, _y2 = self._rect(item.id)
            canvas.create_text(
                x1 + 8,
                y1 + 4,
                text=l10n.text(self.lang, f"header.{item.id}"),
                fill="#A0A0A0",
                font=("Microsoft YaHei", self._px(12)),
                anchor="nw",
                tags=("chrome",),
            )

    def _draw_hotkeys(self) -> None:
        """绘制快捷键提示（位于动画区右端的内容区）。

        Note:
            提示很长，与动画状态文本共用 `anim_region` 的内容区——靠左右两端
            对齐（状态在左、提示在右）分开，故两者不会重叠。
        """
        _x1, y1, x2, y2 = self._content("anim_region")
        self.canvas.create_text(
            x2 - 8,
            (y1 + y2) // 2,
            text=l10n.text(self.lang, "hotkeys"),
            fill="#909090",
            font=("Microsoft YaHei", self._px(11)),
            anchor="e",
            tags=("chrome",),
        )

    def _draw_always(self) -> None:
        """刷新 `always` 层：自述区、帧计数器、动画区（每帧调用）。

        Note:
            本层**不删除重项**，而是首次创建持久画布项、之后只改文本与坐标。
            「每帧删除再重建」会让帧率掉到约 30FPS（实测），且使画布项数量
            在每次重绘时短暂翻倍——帧率是心跳日志要报告的事实，不能因绘制方式失真。
        """
        if self._always_ids is None:
            self._create_always_items()
        self._update_always_items()

    def _create_always_items(self) -> None:
        """创建 `always` 层的持久画布项（只调用一次）。"""
        canvas = self.canvas
        ids: dict[str, int] = {}

        # 自述区（能力 I）两行文本，画在内容区内（标题带以下）。
        # 用 anchor="nw"（左上对齐）而非 "w"——后者是**垂直居中**，会让 y 变成
        # 文本中心，使首行向上溢出到标题带里与标题叠字。
        x1, y1, _x2, _y2 = self._content("status_bar")
        ids["status_line1"] = canvas.create_text(
            x1 + 8,
            y1 + 2,
            text="",
            fill="#E0E0E0",
            font=("Microsoft YaHei", self._px(13)),
            anchor="nw",
            tags=("always",),
        )
        ids["status_line2"] = canvas.create_text(
            x1 + 8,
            y1 + 21,
            text="",
            fill="#909090",
            font=("Consolas", self._px(11)),
            anchor="nw",
            tags=("always",),
        )

        # 帧计数器（能力 F）：大号数字，**无论动画开关都自增**
        fx1, fy1, fx2, fy2 = self._content("frame_counter")
        ids["frame"] = canvas.create_text(
            (fx1 + fx2) // 2,
            (fy1 + fy2) // 2,
            text="",
            fill="#FFFFFF",
            font=("Consolas", self._px(40)),
            anchor="center",
            tags=("always",),
        )

        # 动画区（能力 G）：状态文本始终显示；运动色块仅在开启时可见
        ax1, ay1, ax2, ay2 = self._content("anim_region")
        ids["anim_state"] = canvas.create_text(
            ax1 + 8,
            (ay1 + ay2) // 2,
            text="",
            fill="#C0C0C0",
            font=("Microsoft YaHei", self._px(13)),
            anchor="w",
            tags=("always",),
        )
        ids["anim_block"] = canvas.create_rectangle(
            0, 0, 0, 0, fill=_ANIM_BLOCK_COLOR, outline="", state="hidden", tags=("always",)
        )
        self._always_ids = ids

    def _update_always_items(self) -> None:
        """更新 `always` 层持久画布项的文本、坐标与显隐。"""
        ids = self._always_ids
        assert ids is not None  # 由 `_draw_always` 保证先创建
        canvas = self.canvas

        # 自述区（能力 I）：客户区尺寸 / DPI / 前台状态 / 帧率 / 当前画面 / 语言
        if self._status_on:
            fg = l10n.text(self.lang, "foreground" if self._is_foreground() else "background")
            line1 = (
                f"客户区 {self.width}x{self.height}   DPI {self._dpi()}   {fg}   "
                f"FPS {self._fps:.1f}   "
                f"{l10n.text(self.lang, 'screen_prefix')}{self.screen.screen_id}   "
                f"{l10n.text(self.lang, 'lang_prefix')}{self.lang}"
            )
            canvas.itemconfigure(ids["status_line1"], text=line1, state="normal")
            canvas.itemconfigure(
                ids["status_line2"],
                text=f"{l10n.text(self.lang, 'log_prefix')}{self.log.path}",
                state="normal",
            )
        else:
            canvas.itemconfigure(ids["status_line1"], state="hidden")
            canvas.itemconfigure(ids["status_line2"], state="hidden")

        frame = self._ticker.frame if self._ticker else 0
        canvas.itemconfigure(ids["frame"], text=f"{l10n.text(self.lang, 'frame_prefix')}{frame}")

        # 动画区
        canvas.itemconfigure(
            ids["anim_state"],
            text=l10n.text(self.lang, "anim_on" if self._anim_on else "anim_off"),
        )
        if not self._anim_on:
            # 关闭时隐藏运动块——该区域此时必须逐像素静止（关闭后会被纳入静止判定）
            canvas.itemconfigure(ids["anim_block"], state="hidden")
            return

        # 相位由帧号推导，使动画位置可被消费方按帧号反推校验。
        # 轨道限制在左端 200px 起、右端留 640px 给快捷键提示，避免两者相撞。
        ax1, ay1, ax2, ay2 = self._content("anim_region")
        phase = anim.anim_phase(frame)
        track_left = ax1 + 200
        track_right = ax2 - 640
        block_w = max(20, self._px(40) * -1)
        span = max(1, track_right - track_left - block_w)
        bx = track_left + int(span * phase)
        canvas.coords(ids["anim_block"], bx, ay1 + 2, bx + block_w, ay2 - 2)
        canvas.itemconfigure(ids["anim_block"], state="normal")

    def _draw_input(self) -> None:
        """重绘 `input` 层：原始消息计数与应用计数（收到输入时调用）。"""
        canvas = self.canvas
        canvas.delete("input")

        # 应用计数区的底色是状态横幅（F3 切换），随本层一起重绘
        bx1, by1, bx2, by2 = self._rect("app_counters")
        canvas.create_rectangle(bx1, by1, bx2, by2, fill=BANNER_COLORS[self._banner_index], outline="", tags=("input",))

        self._draw_counter_lines("raw_counters", self.counters.message_lines())
        self._draw_counter_lines("app_counters", self.counters.app_lines())

    def _draw_counter_lines(self, region_id: str, lines: list[str]) -> None:
        """在指定区域的内容区内分行绘制计数文本。

        Args:
            region_id: 区域标识。
            lines: 待绘制的文本行。

        Raises:
            AssertionError: 行数超出区域容量时抛出（见 `_line_capacity`）。

        Note:
            行数超容量时**报错而不是静默截断**——静默截断会让末行的计数数据在界面上
            消失而无人察觉（T0-2 曾因此丢掉「最近点击 / 最近按键」两行）。
        """
        x1, y1, _x2, y2 = self._content(region_id)
        font_size = self._px(12)
        line_h = self._line_h(font_size)
        capacity = self._line_capacity(y2 - y1, line_h)
        assert len(lines) <= capacity, (
            f"区域 {region_id} 需绘制 {len(lines)} 行，容量仅 {capacity} 行；请调整计数项的每行数量或区域高度"
        )
        for index, line in enumerate(lines):
            self.canvas.create_text(
                x1 + 8,
                y1 + 3 + index * line_h,
                text=line,
                fill="#D0E0D0",
                font=("Consolas", font_size),
                anchor="nw",
                tags=("input",),
            )

    @staticmethod
    def _line_h(font_size: int) -> int:
        """按字号推算行高。

        Args:
            font_size: Tk 字号（负值表示像素）。

        Returns:
            行高（像素）。

        Note:
            取 1.18 倍字号：Consolas 的默认行距偏紧，1.18 倍在「不叠字」与
            「不浪费区域高度」之间取平衡。
        """
        return max(11, round(-font_size * 1.18))

    @staticmethod
    def _line_capacity(available_h: int, line_h: int) -> int:
        """计算一个区域内能容纳的文本行数。

        Args:
            available_h: 可用高度（像素，已扣掉标题带与上下留白）。
            line_h: 单行高度（像素）。

        Returns:
            可容纳行数（至少 1）。
        """
        return max(1, (available_h - 6) // line_h)

    def _draw_text(self) -> None:
        """重绘 `text` 层：文本输入区（文本变化时调用）。"""
        self.canvas.delete("text")
        x1, y1, _x2, y2 = self._content("text_field")
        self.canvas.create_text(
            x1 + 8,
            (y1 + y2) // 2,
            text=f"{l10n.text(self.lang, 'text_prefix')}{self.counters.app.text}",
            fill="#FFFFFF",
            font=("Consolas", self._px(18)),
            anchor="w",
            tags=("text",),
        )

    # ---------- 循环回调 ----------

    def _on_tick(self, frame: int) -> None:
        """每帧回调：兜接点击、刷新 `always` 层与画面内的持续动画。

        Args:
            frame: 当前帧序号。

        Note:
            `_flush_raw` 在此调用（而非在消息回调里直接重绘）——鼠标移动会以
            每秒数百次的频率触发消息回调，逐次重绘会让界面卡死。
            `screen.tick` 只更新 `battle` 的进度条与倒计时（其他画面立即返回），
            故本回调对绝大多数画面几乎无开销。

            **兜接核对也在这里**（`_drain_posted_clicks`）：它必须跑在主线程，
            而每帧回调正是主线程；放在窗口过程里会让解释器崩溃（见 `click_claim`）。
        """
        self._drain_posted_clicks(frame)
        self._draw_always()
        self.screen.tick(frame)
        self._flush_raw()

    def _drain_posted_clicks(self, frame: int) -> None:
        """把「Tk 丢弃的投递点击」补处理成一次点击。

        Args:
            frame: 当前帧序号（写日志用）。

        Note:
            **为什么不在窗口过程里直接处理**：窗口过程可能在任意线程被回调，
            而 Tk 不是线程安全的。这里在主线程统一处理。

            **为什么要等一帧**：真实输入到达时，Tk 会在处理同一条消息的过程中
            同步触发绑定（`_on_button_down` 里认领）。等过一帧后仍未认领的，
            才是 Tk 真正丢弃的投递点击。直接在同一轮里判定会因为「绑定还没跑」
            把真实输入也当成投递点击，导致点一次触发两次。

            兜接走的入口与 Tk 完全一致（`_dispatch_click`），因此画面切换、
            弹窗关闭、语言切换、双击等行为与前台点击完全等价——这是
            「后台点击可验证」的前提。
        """
        if not self.claimer.has_pending():
            return
        for x, y in self.claimer.take_unclaimed():
            self.log.write(
                "app",
                event="posted_click_synthesized",
                mouse_position=[x, y],
                frame=frame,
                claim=self.claimer.snapshot(),
            )
            self._dispatch_click(x, y)

    def _dispatch_click(self, x: int, y: int) -> None:
        """按客户区坐标执行一次点击（真实输入与兜接共用同一入口）。

        Args:
            x: 客户区 x。
            y: 客户区 y。

        Note:
            **两个入口必须走同一套逻辑**：真实输入走 Tk 绑定、投递输入走兜接。
            若各写一套，两种输入的模式会出现行为差异（例如后台点击进不了弹窗），
            那会让「后台点击」这件事变得不可验证——配置侧看到的差异无法归因到
            是被测目标还是注入方式。

            双击判定也在这里：兜接路径没有 Tk 的 `<Double-Button-1>`，
            故按「同一位置短时间连点两次」自行识别。
        """
        now = time.monotonic()
        is_double = (
            self._last_click is not None
            and now - self._last_click[0] <= 0.4
            and abs(self._last_click[1] - x) <= 8
            and abs(self._last_click[2] - y) <= 8
        )
        self._last_click = (now, x, y)

        self.counters.touch_app("mouse_down")
        self.counters.touch_app("mouse_up")
        self.counters.set_app("last_mouse_xy", [x, y])
        self.log.write(
            "app",
            event="synthesized_click",
            mouse_position=[x, y],
            double=is_double,
            counters=self.counters.app_snapshot(),
        )

        element = self.screen.hit_test(x, y)
        if is_double:
            self.counters.touch_app("double")
            if element is not None and element.double_action:
                self.log.write(
                    "app",
                    event="element_double_click",
                    element=element.name,
                    screen=self.screen.screen_id,
                    mouse_position=[x, y],
                )
                self._switch_screen(element.double_action)
                self._draw_input()
                return
        if element is not None:
            self.log.write(
                "app",
                event="element_click",
                element=element.name,
                screen=self.screen.screen_id,
                popup=self.screen.popup,
                mouse_position=[x, y],
            )
            self._perform_action(element)
        self._draw_input()

    def _on_heartbeat(self, frame: int, fps: float) -> None:
        """每秒回调：更新帧率并写心跳事件。

        Args:
            frame: 当前帧序号。
            fps: 本周期实测帧率。
        """
        self._fps = fps
        self.log.write(
            "heartbeat",
            frame=frame,
            fps=fps,
            foreground=self._is_foreground(),
            client=[self.width, self.height],
            screen=self.screen.screen_id,
        )

    # ---------- 系统信息 ----------

    def _is_foreground(self) -> bool:
        """判断本窗口当前是否为前台窗口。

        Returns:
            是否为前台窗口。
        """
        try:
            return ctypes.windll.user32.GetForegroundWindow() == self._top_hwnd()
        except Exception:
            return False

    @staticmethod
    def _dpi() -> int:
        """取当前进程的 DPI。

        Returns:
            DPI 值；取不到时返回 96（100% 缩放）。
        """
        try:
            return int(ctypes.windll.user32.GetDpiForSystem())
        except Exception:
            return 96

    @staticmethod
    def _class_name(hwnd: int) -> str:
        """取窗口类名。

        Args:
            hwnd: 窗口句柄。

        Returns:
            类名文本；取不到时返回空串。
        """
        try:
            buffer = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, buffer, 256)
            return buffer.value
        except Exception:
            return ""

    @staticmethod
    def _window_title(hwnd: int) -> str:
        """取窗口标题。

        Args:
            hwnd: 窗口句柄。

        Returns:
            标题文本；取不到时返回空串。
        """
        try:
            user32 = ctypes.windll.user32
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value
        except Exception:
            return ""

    # ---------- 就绪事件 ----------

    def _write_ready(self, hwnds: list[int]) -> None:
        """写 `ready` 事件（含动态区域清单）。

        Args:
            hwnds: 已挂钩的窗口句柄列表。

        Note:
            动态区域清单（能力 K）是静止判据与输入判据的**前提**——缺少它，
            消费方只能按比例猜区域，猜窄则动态内容残留被误判为「画面有变化」，
            猜宽则掩盖真实响应。

            `class_name` / `title` 报告的是**顶层窗口**（`TkTopLevel` + 标题），
            因为适配层 `window.candidates` 匹配的正是顶层窗口；`hook_hwnds` /
            `hook_classes` 报告挂钩所在的子窗口，便于核对「消息挂钩究竟挂在
            哪些窗口上」——这个信息是排查「原始消息计数为 0」的第一手线索。
        """
        top = self._top_hwnd()
        self.log.write(
            "ready",
            title=self._window_title(top) or TITLE,
            lang=self.lang,
            log_path=str(self.log.path),
            client_size=[self.width, self.height],
            class_name=self._class_name(top),
            hook_hwnds=hwnds,
            hook_classes=[self._class_name(hwnd) for hwnd in hwnds],
            dpi=self._dpi(),
            python_version=self._python_version(),
            dynamic_regions=layout.as_dynamic_regions(self.width, self.height),
        )

    @staticmethod
    def _python_version() -> str:
        """返回当前 Python 版本字符串。

        Returns:
            形如 `3.13.6` 的版本号。
        """
        import sys

        return sys.version.split()[0]


# 窗口标题常量。**必须与应用侧绑定设备时用的 title_regex 一致**——
# 两处互相指向，改动其一时另一处必须同步（见 extensions_testbed/README.md §3）。
TITLE = "ok-script-app-pro 测试靶场"
