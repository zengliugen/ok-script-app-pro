"""测试靶场的画面状态机。

画面定义是**界面绘制的唯一来源**：各元素的矩形在**基准坐标（1280×720）**下书写，
绘制时经 `layout.scale_rect` 换算。适配层 `screens/<app_lang>/*.yaml` 中的
`rect` 取自同一套坐标，故程序与配置不会脱节（`extensions_testbed/README.md`
§3.4 的要求：界面一次画全，避免回头改程序时连带改判别集与坐标）。

各画面在画布上绘制三部分：

1. **标题区**（`screen_header` 区域）：标题文本 + 版本标签；
2. **主体区**（`screen_body` 区域）：画面专属的元素（按钮 / 图标网格 / 滑块 / 手势区）；
3. **自证区**：由 `app.py` 在其余区域统一绘制，各画面共用。

**元素坐标一律相对容器算出，不写死绝对坐标**——原因见 `_body_rect` 附近的说明。

**与配置的对应关系**：本模块的 `SCREENS[<id>].elements` 与
`apps/testbed/screens/zh/<id>.yaml` 的 `regions` 一一对应（`extensions_testbed/README.md` §5.4 的
同源要求）。`make_templates.py` 即按本表裁模板图。

**画面的导航关系**也定义在本模块（`Element.action` / `Element.double_action`）——
它与配置的 `goto` 边是同一条事实的两侧：配置侧是**给工具读的**路由声明，
程序侧是**被测目标的真实行为**。两者必须一致，否则导航测试会失真
（`screens.actions()` 汇总出映射，供逐条对照）。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass

from extensions_testbed import l10n, layout, yolo_scene

# 按钮标签的字号（基准像素；实际绘制时按客户区尺寸换算）
_BUTTON_FONT = 20
_TITLE_FONT = 30
_VERSION_FONT = 16
_ZONE_FONT = 14

# 画面标题在 `screen_header` 区域内的基准矩形
_TITLE_RECT = (40, 72, 620, 126)
# 版本标签的基准矩形（`entry` 的 ocr 判据之一）
_VERSION_RECT = (40, 128, 360, 154)

# 版本标签文本：适配层的 `version_label` 判据按 `v` 做子串匹配
VERSION_TEXT = "v0.1.0"

# ── 主体区内元素的位置**一律相对容器算出，不写死绝对坐标** ──────────────
#
# 原因：`screen_body` 的上下边界会随其余区域的容量需要而调整，而写死的绝对
# 坐标一旦超出容器下沿就会压进下一个区域。实测发生过 `exit_btn` 下沿越界 4px、
# 压在静态色卡上方的事故（使用者截图发现）。相对定位使元素随容器自动重排，
# 从结构上消除这类越界；再由 `check_elements_inside()` 在启动时兜底校验。

# 按钮尺寸与间距（基准像素）
_BUTTON_W, _BUTTON_H = 240, 56
_BUTTON_GAP = 20
# 按钮列的水平中心（基准坐标；各画面共用同一列，便于模板复用）
_BUTTON_CENTER_X = 640
# 按钮列距主体区上沿的偏移（需要额外放滑块或手势区时，按钮列上移至此）
_BUTTON_TOP_OFFSET = 40

# 主体区内的平坦探测矩形（供 `color` 判据配置）：贴右上角，避开居中的按钮列
_PROBE_W, _PROBE_H = 220, 90
_PROBE_RIGHT_MARGIN, _PROBE_TOP_MARGIN = 100, 26

# 手势区（`battle.dodge_zone`）的尺寸与位置：贴主体区右侧、平坦探测矩形**下方**
_ZONE_W, _ZONE_H = 340, 110
_ZONE_RIGHT_MARGIN = 100
_ZONE_TOP_MARGIN = 140

# 滑块（`settings_menu.sensitivity_slider`）的尺寸与位置：横向居中、贴主体区下部
_SLIDER_W, _SLIDER_H = 440, 50
_SLIDER_TOP_MARGIN = 196

# `battle` 的进度条与倒计时（**持续动画区域**，均位于整块外观比对矩形之内）。
#
# **位置约束来自三处**（`check_elements_inside()` 逐条校验）：
#   ① 不得压到平坦探测矩形（主体区 rel 26~116 的右上角）——否则该画面的底色判据
#      会读到动画而非底色。实测确认：贴顶横贯的进度条会覆盖探测矩形；
#   ② 不得与主体区内的元素（按钮列、手势区）重叠；
#   ③ 必须落在 `BATTLE_PANEL_RECT` 之内——否则「配了忽略区域后画面可稳定」
#      这条验证就失去了对象。
# 故两者都排在主体区**下部**且横向错开：倒计时靠左，进度条在按钮与手势区之下。
_BATTLE_PROGRESS_TOP, _BATTLE_PROGRESS_H = 250, 20
_BATTLE_COUNTDOWN_TOP, _BATTLE_COUNTDOWN_H = 196, 36
_BATTLE_COUNTDOWN_W = 400

# 画面标题区内的元素（标题 / 版本标签），供包含性自检使用
_HEADER_ELEMENTS: dict[str, tuple[int, int, int, int]] = {
    "title": _TITLE_RECT,
    "version": _VERSION_RECT,
}

# `action` / `double_action` 的取值约定（**必须与配置的 `goto` 边一致**）：
#   ""              无动作（纯判定区域，如手势区与滑块——它们不切换画面）
#   "<screen_id>"   切换到该画面
#   ":close"        关闭靶场（`entry.exit_btn`，对应 M3 的 `explorable: false`）
#   ":lang"         切换界面语言（`settings_menu.lang_toggle_btn`，等价于 F10）
#   ":popup_hide"   关闭弹窗覆盖层（`popup_notice.close_btn`）
ACTION_CLOSE = ":close"
ACTION_TOGGLE_LANG = ":lang"
ACTION_POPUP_HIDE = ":popup_hide"


@dataclass(frozen=True, slots=True)
class Element:
    """画面上的一个交互元素。

    Attributes:
        name: 元素名（对应适配层该画面 region 的 `name`）。
        rect: 基准坐标下的矩形 `[x1, y1, x2, y2]`。
        kind: 元素类型，`button` / `zone` / `slider` / `icon_grid`。
        label_key: 文案键（查 `l10n.screen_text`）；为空串时不绘制文本。
        action: **单击**触发的动作（取值见模块头部的约定）。
        double_action: **双击**触发的动作（仅图标网格用）。
    """

    name: str
    rect: tuple[int, int, int, int]
    kind: str
    label_key: str
    action: str = ""
    double_action: str = ""


@dataclass(frozen=True, slots=True)
class ScreenDef:
    """一个画面的定义。

    Attributes:
        screen_id: 画面 id（与适配层 `screens/<app_lang>/<screen_id>.yaml` 同名）。
        body_bg: 主体区底色（各画面不同，供 `color` / `bg_probe` 判据使用）。
        elements: 交互元素列表。
        overlay: 是否为**覆盖层画面**——它叠加在当前画面之上而不替换它
            （`popup_notice` 是唯一的覆盖层，由 `F9` 弹出）。
        animated: 是否含**持续动画**（`battle` 的进度条与倒计时）。
            这类画面是「无输入时区域静止」这一契约的**刻意例外**：画面本身
            含持续动画，配置侧须用 `ignore_regions` 排除，否则永不稳定
            （`extensions_testbed/README.md` §3.2 / §3.6）。
    """

    screen_id: str
    body_bg: str
    elements: tuple[Element, ...]
    overlay: bool = False
    animated: bool = False


def _body_rect() -> tuple[int, int, int, int]:
    """取主体区的基准矩形。

    Returns:
        基准坐标下的矩形 `[x1, y1, x2, y2]`。
    """
    return layout.rect_of("screen_body")


def _button_column(
    specs: tuple[tuple[str, str, str], ...],
    *,
    top: int | None = None,
) -> tuple[Element, ...]:
    """在 `screen_body` 内生成一列等间距按钮，**整列垂直居中**。

    Args:
        specs: 每个按钮的 `(元素名, 文案键, 动作)` 三元组，从上到下排列。
        top: 按钮列的顶部 y 坐标（基准坐标）；None 时在主体区内垂直居中。

    Returns:
        按钮元素元组，横向居中对齐。

    Note:
        坐标**由主体区矩形算出**而非写死——主体区高度会随其余区域的容量需要
        而调整，写死坐标会有越界风险（见文件头部关于相对定位的说明）。
        元素名与文案键**分开给出**：`back_btn` 这类名字在各画面复用，
        而动作各不相同（有的回 `entry`、有的回 `chapter_select`），
        合在一起表达会让「同名元素的动作」变得难以核对。
    """
    _bx1, by1, _bx2, by2 = _body_rect()
    count = len(specs)
    total_h = count * _BUTTON_H + (count - 1) * _BUTTON_GAP
    # 整列在主体区内垂直居中；用 max(0, …) 兜底——区域过矮时宁可贴顶也不要算出负偏移
    start = top if top is not None else by1 + max(0, (by2 - by1 - total_h) // 2)
    left = _BUTTON_CENTER_X - _BUTTON_W // 2

    elements: list[Element] = []
    for index, (name, label_key, action) in enumerate(specs):
        y1 = start + index * (_BUTTON_H + _BUTTON_GAP)
        elements.append(
            Element(
                name=name,
                rect=(left, y1, left + _BUTTON_W, y1 + _BUTTON_H),
                kind="button",
                label_key=label_key,
                action=action,
            )
        )
    return tuple(elements)


def _buttons_at_top(specs: tuple[tuple[str, str, str], ...]) -> tuple[Element, ...]:
    """生成贴主体区上沿的按钮列（供需要在其下方放滑块或手势区的画面用）。

    Args:
        specs: 每个按钮的 `(元素名, 文案键, 动作)` 三元组。

    Returns:
        按钮元素元组。
    """
    return _button_column(specs, top=_body_rect()[1] + _BUTTON_TOP_OFFSET)


def _body_probe() -> tuple[int, int, int, int]:
    """计算主体区内的平坦探测矩形（供 `color` 判据配置）。

    Returns:
        基准坐标下的矩形 `[x1, y1, x2, y2]`。

    Note:
        贴右上角而非写死绝对坐标：与按钮同理，避免主体区调整后探测点落到
        元素上（那会让 `color` 判据读到按钮底色而非画面底色，判据失真）。
        **所有画面元素都必须避开这个矩形**——`check_elements_inside()` 会强制校验。
    """
    _bx1, by1, bx2, _by2 = _body_rect()
    return (
        bx2 - _PROBE_RIGHT_MARGIN - _PROBE_W,
        by1 + _PROBE_TOP_MARGIN,
        bx2 - _PROBE_RIGHT_MARGIN,
        by1 + _PROBE_TOP_MARGIN + _PROBE_H,
    )


def _zone_rect() -> tuple[int, int, int, int]:
    """计算手势区矩形（`battle.dodge_zone`）。

    Returns:
        基准坐标下的矩形 `[x1, y1, x2, y2]`。

    Note:
        贴主体区右侧、平坦探测矩形**下方**（探测矩形占 y 至 `by1+116`），
        故本区从 `by1+140` 起，两者不重叠。
    """
    _bx1, by1, bx2, _by2 = _body_rect()
    x2 = bx2 - _ZONE_RIGHT_MARGIN
    return (x2 - _ZONE_W, by1 + _ZONE_TOP_MARGIN, x2, by1 + _ZONE_TOP_MARGIN + _ZONE_H)


def _slider_rect() -> tuple[int, int, int, int]:
    """计算滑块矩形（`settings_menu.sensitivity_slider`）。

    Returns:
        基准坐标下的矩形 `[x1, y1, x2, y2]`。

    Note:
        横向居中（与按钮列同轴）、贴主体区下部——避开上方的按钮列与右上角的探测矩形。
    """
    _bx1, by1, _bx2, _by2 = _body_rect()
    left = _BUTTON_CENTER_X - _SLIDER_W // 2
    return (left, by1 + _SLIDER_TOP_MARGIN, left + _SLIDER_W, by1 + _SLIDER_TOP_MARGIN + _SLIDER_H)


# `battle` 的整块外观比对矩形（`panel_appearance` 的 `image_diff` 基准图即按此裁出）。
#
# 取值覆盖主体区的大部分：左右各留 60px、上留 8px、下留 12px 避免压到相邻区域边框。
# **进度条与倒计时都在其内**——这是刻意的：§3.6 要求用算子级 `ignore_regions` 排除它们，
# 从而让「配了忽略区域后画面可稳定」这条结论在靶场上可被直接验证
# （若把动画放在矩形之外，这条验证就成了空转）。
BATTLE_PANEL_RECT: tuple[int, int, int, int] = (
    _body_rect()[0] + 60,
    _body_rect()[1] + 8,
    _body_rect()[2] - 60,
    _body_rect()[3] - 12,
)


def _battle_progress_rect() -> tuple[int, int, int, int]:
    """计算 `battle` 进度条的矩形（**持续动画区域**）。

    Returns:
        基准坐标下的矩形 `[x1, y1, x2, y2]`。
    """
    x1, y1, x2, _y2 = BATTLE_PANEL_RECT
    return (x1 + 20, y1 + _BATTLE_PROGRESS_TOP, x2 - 20, y1 + _BATTLE_PROGRESS_TOP + _BATTLE_PROGRESS_H)


def _battle_countdown_rect() -> tuple[int, int, int, int]:
    """计算 `battle` 倒计时的矩形（**持续动画区域**）。

    Returns:
        基准坐标下的矩形 `[x1, y1, x2, y2]`。

    Note:
        与进度条**分开登记**的理由：两者是独立的动态来源，
        排查「忽略区域是否配全」时要能逐项对照。
        横向靠左且不横贯整宽——避开右侧的手势区。
    """
    x1, y1, _x2, _y2 = BATTLE_PANEL_RECT
    return (
        x1 + 20,
        y1 + _BATTLE_COUNTDOWN_TOP,
        x1 + 20 + _BATTLE_COUNTDOWN_W,
        y1 + _BATTLE_COUNTDOWN_TOP + _BATTLE_COUNTDOWN_H,
    )


def _popup_cover_rect() -> tuple[int, int, int, int]:
    """计算弹窗覆盖层的覆盖矩形（`screen_header` + `screen_body`）。

    Returns:
        基准坐标下的矩形 `[x1, y1, x2, y2]`。

    Note:
        **只覆盖画面标题区与主体区**，不覆盖其余区域：自述区、帧计数器、动画区
        属 `always` 层，绘制在 `screen` 层之上，本就遮不住；而它们继续更新正是
        「应用仍在运行」的证据（弹窗期间帧计数不停，消费方仍能判活）。
    """
    _hx1, hy1, _hx2, _hy2 = layout.rect_of("screen_header")
    _bx1, _by1, bx2, by2 = layout.rect_of("screen_body")
    return (_hx1, hy1, bx2, by2)


# 画面定义表。**8 个画面**（`extensions_testbed/README.md` §3.1）：
# 其中 7 个是正常可达的画面（`F7` 循环），`popup_notice` 是覆盖层（`F9` 弹出）。
#
# 各画面底色**互不相同**，使 `color` 判据具备区分力。色相刻意拉开
# （蓝 / 绿 / 棕 / 绯 / 紫 / 青 / 橄榄 / 近黑），避免两个画面在 HSV 空间上相近。
#
# 按钮的 `top` 取值：主体区高 296px。4 个按钮居中占 284px（贴满）；
# 1~2 个按钮居中占 56/132px；需要在其下方放滑块时用 `_buttons_at_top` 上移。
SCREENS: dict[str, ScreenDef] = {
    "entry": ScreenDef(
        screen_id="entry",
        # 深蓝：H≈210
        body_bg="#102840",
        elements=_button_column(
            (
                ("start_btn", "start", "chapter_select"),
                ("settings_btn", "settings", "settings_menu"),
                ("help_btn", "help", "help_page"),
                ("exit_btn", "exit", ACTION_CLOSE),
            )
        ),
    ),
    "chapter_select": ScreenDef(
        screen_id="chapter_select",
        # 墨绿：H≈140
        body_bg="#1A3A28",
        elements=(
            # 单击不动作、双击才进入关卡详情：若单击也切画面，双击会被第一次单击提前吃掉，
            # 于是「双击关卡图标进入详情」这一交互在程序侧不可成立（配置侧无法验证）
            *_button_column((("back_btn", "back", "entry"),)),
            Element(
                name="chapter_list",
                rect=yolo_scene.GRID_RECT,
                kind="icon_grid",
                label_key="",
                double_action="chapter_detail",
            ),
        ),
    ),
    "chapter_detail": ScreenDef(
        screen_id="chapter_detail",
        # 深棕：H≈25
        body_bg="#3A2418",
        elements=_button_column(
            (
                ("enter_battle_btn", "enter_battle", "battle"),
                ("back_btn", "back", "chapter_select"),
            )
        ),
    ),
    "battle": ScreenDef(
        screen_id="battle",
        # 绯红：H≈355
        body_bg="#401828",
        elements=(
            *_button_column(
                (
                    ("enter_settle_btn", "enter_settle", "settlement"),
                    ("back_btn", "back", "chapter_detail"),
                )
            ),
            Element(
                name="dodge_zone",
                rect=_zone_rect(),
                kind="zone",
                label_key="dodge",
                # 手势区不切换画面：闪避只改变战斗内状态，属「不期望画面变化」的交互
                # （配置侧须声明 `expect_change: false`，见 03-identify.md §5.4）
                action="",
            ),
        ),
        animated=True,
    ),
    "settlement": ScreenDef(
        screen_id="settlement",
        # 暗紫：H≈270
        body_bg="#2A1A3A",
        elements=_button_column(
            (
                # ★ 环路所在：结算 → 关卡选择。与
                # `entry→chapter_select→chapter_detail→battle→settlement`
                # 共同构成 07-roadmap.md T0 验收所要求的环路
                ("confirm_btn", "confirm", "chapter_select"),
            )
        ),
    ),
    "settings_menu": ScreenDef(
        screen_id="settings_menu",
        # 深青：H≈180
        body_bg="#103A3A",
        elements=(
            # 按钮列上移，为下方的滑块腾出空间（主体区仅 296px 高）
            *_buttons_at_top(
                (
                    ("back_btn", "back", "entry"),
                    ("lang_toggle_btn", "lang_toggle", ACTION_TOGGLE_LANG),
                )
            ),
            Element(
                name="sensitivity_slider",
                rect=_slider_rect(),
                kind="slider",
                label_key="slider",
                # 滑块不切换画面：拖动只改变数值。但**必须产生可观测的变化**（数值文本），
                # 否则点击有效性验证会（正确地）判为输入未生效——详见 `drag_slider`
                action="",
            ),
        ),
    ),
    "help_page": ScreenDef(
        screen_id="help_page",
        # 橄榄：H≈60
        body_bg="#3A3A18",
        elements=_button_column((("back_btn", "back", "entry"),)),
    ),
    "popup_notice": ScreenDef(
        screen_id="popup_notice",
        # 近黑：作为覆盖层，它的底色必须与所有正常画面都不同
        body_bg="#1A1A1A",
        elements=_button_column((("close_btn", "close", ACTION_POPUP_HIDE),)),
        # 覆盖层：叠加在当前画面之上而不替换它（`F9` 弹出、`Esc` 或点击关闭按钮收回）
        overlay=True,
    ),
}

# 主体区内的平坦探测矩形（基准坐标）：位于右上角，避开居中的按钮列
BODY_PROBE_RECT: tuple[int, int, int, int] = _body_probe()

# 画面顺序（`F7` 依次切换）。
#
# **不含 `popup_notice`**：它是覆盖层，由 `F9` 弹出而非顺序切换到达。
# 起点由 `--screen` 参数或构造时的 `screen_id` 指定（见 `ScreenView.__init__`）。
SCREEN_ORDER: tuple[str, ...] = (
    "entry",
    "chapter_select",
    "chapter_detail",
    "battle",
    "settlement",
    "settings_menu",
    "help_page",
)

# 覆盖层画面 id（由 `F9` 弹出；不属于 `SCREEN_ORDER`）
OVERLAY_SCREEN = "popup_notice"

# `battle` 动画的周期（帧）：60 FPS 下约 30 秒一个来回
BATTLE_PERIOD_FRAMES = 1800


def all_elements(screen_id: str) -> tuple[Element, ...]:
    """取指定画面的全部元素。

    Args:
        screen_id: 画面 id。

    Returns:
        元素元组。

    Raises:
        KeyError: 画面未定义时抛出（属代码错误，不静默忽略）。
    """
    return SCREENS[screen_id].elements


class ScreenView:
    """画面绘制与命中测试。

    Attributes:
        screen_id: 当前画面 id。
        popup: 当前显示的覆盖层画面 id；无弹窗时为 None。
        slider_value: 灵敏度滑块的当前数值（拖动后变化，用于观测手势是否生效）。
    """

    def __init__(
        self,
        canvas: tk.Canvas,
        width: int,
        height: int,
        lang: str,
        *,
        screen_id: str | None = None,
    ) -> None:
        """绑定画布、客户区尺寸与当前语言。

        Args:
            canvas: 用于绘制的画布。
            width: 客户区宽度（像素）。
            height: 客户区高度（像素）。
            lang: 当前界面语言（`zh` / `en`）。
            screen_id: 启动时进入的画面；None 时取 `SCREEN_ORDER[0]`（`entry`）。

        Raises:
            KeyError: 指定的画面未定义时抛出（属代码错误，不静默忽略）。
        """
        if screen_id is not None and screen_id not in SCREENS:
            raise KeyError(f"画面未定义：{screen_id}")
        self._canvas = canvas
        self._width = width
        self._height = height
        self.lang = lang
        self.screen_id = screen_id or SCREEN_ORDER[0]
        self.popup: str | None = None
        self.slider_value = 50
        # 图标网格：**只在进入 `chapter_select` 时重建**（进入即回到顶部）。
        # 不能每次 `draw()` 都重建——`F4`（色卡显隐）与 `F10`（语言）都会触发整屏重绘，
        # 若跟着重建，滚动位置会被意外重置（「每次进入才重置」的语义被破坏）。
        self._icon_grid: yolo_scene.IconGrid | None = None
        # 承载网格的子画布与其 window 项（平滑滚动要靠子画布裁剪）
        self._grid_canvas = None
        self._grid_window = None
        # `battle` 动画的持久画布项（每帧只改属性，不删除重建）
        self._battle_ids: dict[str, int] | None = None

    # ---------- 坐标换算 ----------

    def _scaled(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """把基准矩形换算到当前客户区尺寸。

        Args:
            rect: 基准坐标下的矩形。

        Returns:
            实际像素矩形。
        """
        return layout.scale_rect(rect, self._width, self._height)

    def _font(self, size: int) -> int:
        """把基准字号按客户区高度换算为实际字号。

        Args:
            size: 基准字号（1280×720 下的像素高度）。

        Returns:
            实际字号（至少 8，避免过小导致不可读）。

        Note:
            按高度比例换算使不同窗口尺寸下文字与布局的相对大小一致；
            而字号下限保证小窗口下文字仍可被 OCR 识别。
        """
        return max(8, round(size * self._height / layout.BASE_H))

    # ---------- 画面切换 ----------

    def switch(self, screen_id: str) -> None:
        """切换当前画面。

        Args:
            screen_id: 目标画面 id。

        Raises:
            KeyError: 画面未定义时抛出（属代码错误，不静默忽略）。

        Note:
            切换会**丢弃图标网格**，使下次进入 `chapter_select` 从顶部开始（滚动位置
            属于「本次进入」的状态，不应跨画面残留）。
            切换同时收回弹窗：弹窗是覆盖层，不应跨越画面切换而残留。
        """
        if screen_id not in SCREENS:
            raise KeyError(f"画面未定义：{screen_id}")
        self.screen_id = screen_id
        self.popup = None
        self._icon_grid = None
        self.draw()

    def next_screen(self) -> str:
        """切到顺序表中的下一个画面（`F7` 触发）。

        Returns:
            切换后的画面 id。

        Note:
            有弹窗时**不切换**：弹窗是模态覆盖层，应先关闭它——否则「当前画面」
            与「可见内容」会不一致，据画面判定得出的导航结论会失真。
        """
        if self.popup is not None:
            return self.screen_id
        index = SCREEN_ORDER.index(self.screen_id)
        target = SCREEN_ORDER[(index + 1) % len(SCREEN_ORDER)]
        self.switch(target)
        return target

    def show_popup(self) -> bool:
        """弹出覆盖层（`F9` 触发）。

        Returns:
            是否成功弹出；已是弹出状态时返回 False。

        Note:
            弹窗**不改变 `screen_id`**——被覆盖的画面仍是当前画面，只是其内容
            被判据看不见了。这正是 M2 要验证的场景：判别集无命中 → 兜底恢复。
        """
        if self.popup is not None:
            return False
        self.popup = OVERLAY_SCREEN
        self.draw()
        return True

    def hide_popup(self) -> bool:
        """收回覆盖层。

        Returns:
            是否实际收回了弹窗。
        """
        if self.popup is None:
            return False
        self.popup = None
        self.draw()
        return True

    # ---------- 绘制 ----------

    def draw(self) -> None:
        """绘制当前画面（标题区 + 主体区，必要时叠加覆盖层）。

        Note:
            先整体删除 `screen` 标签的画布项：画面可能被反复重绘（`F4` / `F10` /
            弹窗显隐），不删会让旧内容与新内容叠加。删除后 `battle` 动画项与图标
            网格项也一并消失，故两者的句柄必须同时清空。
        """
        self._canvas.delete("screen")
        self._battle_ids = None
        # 图标网格跑在子画布里（见 `_draw_icon_grid`）：重绘会删掉承载它的 window
        # 项，子画布本身必须显式销毁，否则每次重绘都留一个不可见的子窗口。
        if self._grid_canvas is not None:
            self._grid_canvas.destroy()
            self._grid_canvas = None
            self._grid_window = None

        screen = SCREENS[self.screen_id]
        self._draw_title(screen)
        self._draw_body(screen)

        if self.popup is not None:
            self._draw_overlay(SCREENS[self.popup])

    def _draw_title(self, screen: ScreenDef) -> None:
        """绘制标题区（画面标题 + 版本标签）。

        Args:
            screen: 画面定义。
        """
        canvas = self._canvas
        x1, y1, x2, y2 = self._scaled(_TITLE_RECT)
        canvas.create_text(
            (x1 + x2) // 2,
            (y1 + y2) // 2,
            text=l10n.screen_text(screen.screen_id, self.lang, "title"),
            fill="#FFFFFF",
            font=("Microsoft YaHei", self._font(_TITLE_FONT)),
            anchor="center",
            tags=("screen",),
        )

        # 版本标签：`entry` 的 ocr 判据之一，位置在标题区左下
        vx1, vy1, vx2, vy2 = self._scaled(_VERSION_RECT)
        canvas.create_text(
            vx1,
            (vy1 + vy2) // 2,
            text=VERSION_TEXT,
            fill="#C8D8E8",
            font=("Consolas", self._font(_VERSION_FONT)),
            anchor="w",
            tags=("screen",),
        )

    def _draw_body(self, screen: ScreenDef) -> None:
        """绘制主体区（底色 + 各元素 + 按需的持续动画）。

        Args:
            screen: 画面定义。
        """
        canvas = self._canvas
        bx1, by1, bx2, by2 = layout.scaled_rect("screen_body", self._width, self._height)
        canvas.create_rectangle(bx1, by1, bx2, by2, fill=screen.body_bg, outline="", tags=("screen",))

        for element in screen.elements:
            self._draw_element(screen.screen_id, element)

        if screen.animated:
            # 动画项必须在元素之后创建：`battle` 的进度条横跨整块主体区，
            # 若先于按钮创建，按钮会被压在下面而不可见（Tk 按创建顺序决定 z 序）
            self._create_battle_items()

    def _draw_overlay(self, overlay: ScreenDef) -> None:
        """绘制覆盖层（弹窗）。

        Args:
            overlay: 覆盖层画面定义。

        Note:
            覆盖矩形**盖住画面标题区与主体区**，使当前画面的判别集全部失效
            ——这是设计意图（`extensions_testbed/README.md` §3.1：`popup_notice` 是「判别集无命中」
            的兜底恢复素材）。若不让它盖住判据区域，弹窗就不会被判为「未知画面」，
            M2 的兜底恢复验收便无从验证。
        """
        canvas = self._canvas
        # 网格是子窗口（window 项），永远画在画布自己的图形之上——不隐藏的话
        # 弹窗会被网格挡住一块。
        if self._grid_window is not None:
            canvas.itemconfigure(self._grid_window, state="hidden")
        x1, y1, x2, y2 = self._scaled(_popup_cover_rect())
        canvas.create_rectangle(x1, y1, x2, y2, fill=overlay.body_bg, outline="", tags=("screen",))
        canvas.create_text(
            (x1 + x2) // 2,
            y1 + (y2 - y1) // 3,
            text=l10n.screen_text(overlay.screen_id, self.lang, "title"),
            fill="#FFD060",
            font=("Microsoft YaHei", self._font(_TITLE_FONT)),
            anchor="center",
            tags=("screen",),
        )
        for element in overlay.elements:
            self._draw_element(overlay.screen_id, element)

    def _draw_element(self, screen_id: str, element: Element) -> None:
        """绘制一个交互元素。

        Args:
            screen_id: 该元素所属的画面 id（**文案按它查表**，故不能取
                `self.screen_id`——覆盖层的元素属于覆盖层画面）。
            element: 元素定义。

        Raises:
            ValueError: 遇到未实现的 `kind` 时抛出。

        Note:
            **未实现的 `kind` 必须报错而非静默跳过**：`Element.kind` 声明支持四种
            类型，若某个分支漏写，界面会空缺而配置侧的判据找不到元素——表现为
            「识别不出来」且原因不浮现（`extensions_testbed/README.md` §0.6 的已知陷阱 2）。
        """
        if element.kind == "button":
            self._draw_button(screen_id, element)
            return
        if element.kind == "zone":
            self._draw_zone(screen_id, element)
            return
        if element.kind == "slider":
            self._draw_slider(screen_id, element)
            return
        if element.kind == "icon_grid":
            self._draw_icon_grid(element)
            return
        raise ValueError(f"未实现的元素类型：{element.kind!r}（元素 {element.name}）")

    def _label(self, screen_id: str, element: Element) -> str:
        """取元素的显示文案（无文案键时返回空串）。

        Args:
            screen_id: 元素所属画面 id。
            element: 元素定义。

        Returns:
            文案文本。
        """
        if not element.label_key:
            return ""
        return l10n.screen_text(screen_id, self.lang, element.label_key)

    def _draw_button(self, screen_id: str, element: Element) -> None:
        """绘制按钮。

        Args:
            screen_id: 元素所属画面 id。
            element: 元素定义。
        """
        x1, y1, x2, y2 = self._scaled(element.rect)
        canvas = self._canvas
        canvas.create_rectangle(x1, y1, x2, y2, fill="#3C6E9C", outline="#8FB8D8", width=2, tags=("screen", "element"))
        text = self._label(screen_id, element)
        if not text:
            return
        canvas.create_text(
            (x1 + x2) // 2,
            (y1 + y2) // 2,
            text=text,
            fill="#FFFFFF",
            font=("Microsoft YaHei", self._font(_BUTTON_FONT)),
            anchor="center",
            tags=("screen", "element"),
        )

    def _draw_zone(self, screen_id: str, element: Element) -> None:
        """绘制手势区（虚线框 + 文案）。

        Args:
            screen_id: 元素所属画面 id。
            element: 元素定义。

        Note:
            用**虚线**（`dash`）而非实线：手势区在视觉上应区别于可点击按钮，
            使「这块区域是滑动用的」这一语义对人一眼可见（人工调试时会用到）。
        """
        x1, y1, x2, y2 = self._scaled(element.rect)
        canvas = self._canvas
        canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            fill="",
            outline="#C080D0",
            width=2,
            dash=(6, 4),
            tags=("screen", "element"),
        )
        canvas.create_text(
            (x1 + x2) // 2,
            (y1 + y2) // 2,
            text=self._label(screen_id, element),
            fill="#E0C0F0",
            font=("Microsoft YaHei", self._font(_ZONE_FONT)),
            anchor="center",
            tags=("screen", "element"),
        )

    def _draw_slider(self, screen_id: str, element: Element) -> None:
        """绘制滑块（轨道 + 已填充段 + 手柄 + 数值文本）。

        Args:
            screen_id: 元素所属画面 id。
            element: 元素定义。

        Note:
            数值文本是**拖动是否生效的观测证据**：该区域不切换画面，
            若不产生任何可读变化，点击有效性验证会（正确地）判为输入未生效
            （`03-identify.md` §5.4 所描述的场景）。
        """
        x1, y1, x2, y2 = self._scaled(element.rect)
        canvas = self._canvas
        track_top, track_bottom = y1 + (y2 - y1) // 2 - 4, y1 + (y2 - y1) // 2 + 4
        canvas.create_rectangle(x1, track_top, x2, track_bottom, fill="#204850", outline="", tags=("screen", "element"))
        ratio = max(0.0, min(1.0, self.slider_value / 100))
        knob_x = x1 + round((x2 - x1) * ratio)
        canvas.create_rectangle(
            x1, track_top, knob_x, track_bottom, fill="#60C0D0", outline="", tags=("screen", "element")
        )
        canvas.create_oval(
            knob_x - 10,
            track_top - 10,
            knob_x + 10,
            track_bottom + 10,
            fill="#E0F8FF",
            outline="#60C0D0",
            width=2,
            tags=("screen", "element"),
        )
        canvas.create_text(
            x1,
            y1 - 6,
            text=f"{self._label(screen_id, element)} {self.slider_value}",
            fill="#D0F0F8",
            font=("Microsoft YaHei", self._font(_ZONE_FONT)),
            anchor="sw",
            tags=("screen", "element"),
        )

    def _draw_icon_grid(self, element: Element) -> None:
        """绘制图标网格（委托 `yolo_scene.IconGrid`）。

        Args:
            element: 元素定义。

        Raises:
            AssertionError: 元素矩形与 `yolo_scene.GRID_RECT` 不一致时抛出。

        Note:
            断言两处矩形一致：网格的实际绘制由 `yolo_scene.GRID_RECT` 决定，
            而配置侧的 `chapter_list` 区域取自本元素的 `rect`——两者若不一致，
            识别范围会与实际绘制的网格错位，且不报错（属静默失败）。
            本次进入若还没有网格则新建（进入即回到顶部）；已有则沿用，
            使 `F4` / `F10` 的整屏重绘不会意外重置滚动位置。
        """
        assert element.rect == yolo_scene.GRID_RECT, (
            f"图标网格元素 {element.name} 的矩形 {element.rect} 与 yolo_scene.GRID_RECT {yolo_scene.GRID_RECT} 不一致"
        )
        # **网格画在子画布里**：Tk 画布不能逐项裁剪，平滑滚动时半行会溢出到标题区/下方
        # 区域。子画布是个真实窗口，天然把内容裁在视口内；滚动只需重画图标。
        gx1, gy1, gx2, gy2 = self._scaled(yolo_scene.GRID_RECT)
        child = tk.Canvas(
            self._canvas,
            width=max(1, gx2 - gx1),
            height=max(1, gy2 - gy1),
            bg=SCREENS[self.screen_id].body_bg,
            highlightthickness=0,
            bd=0,
        )
        self._grid_canvas = child
        self._grid_window = self._canvas.create_window(
            gx1,
            gy1,
            anchor="nw",
            window=child,
            tags=("screen", "yolo_icons"),
        )
        if self._icon_grid is None:
            self._icon_grid = yolo_scene.IconGrid(
                child,
                gx2 - gx1,
                gy2 - gy1,
                self._scaled,
                origin=(gx1, gy1),
            )
        else:
            self._icon_grid.rebind(child, gx2 - gx1, gy2 - gy1, (gx1, gy1))
        self._icon_grid.draw()

    def scroll_icon_grid(self, delta: int) -> bool:
        """滚轮滚动关卡图标网格（竖向）。

        Args:
            delta: 滚轮增量（Windows 每格 ±120，正数向上）。

        Returns:
            是否真的发生了滚动；当前画面没有网格、或有弹窗遮挡时返回 False。

        Note:
            按**整行**滚动（一格一行）：可视区高度是行高的整数倍，整行滚动时
            图标始终整格可见；按像素滚动会让上下两行各露半格。
            弹窗显示时列表被覆盖层遮住，滚轮不应改变底下画面的内容。
        """
        if self._icon_grid is None or self.popup is not None:
            return False
        return self._icon_grid.scroll(-1 if delta > 0 else 1)

    @property
    def icon_scroll_row(self):
        """关卡图标网格的当前滚动行；当前没有网格时返回 None。"""
        if self._icon_grid is None:
            return None
        return self._icon_grid.scroll_row

    def _grid_local(self, x: int, y: int) -> tuple[int, int]:
        """把客户区坐标换算到网格视口的局部坐标。

        Args:
            x: 客户区 x。
            y: 客户区 y。

        Returns:
            视口内坐标（子画布坐标系）。
        """
        gx1, gy1, _gx2, _gy2 = self._scaled(yolo_scene.GRID_RECT)
        return x - gx1, y - gy1

    def begin_scrollbar_drag(self, x: int, y: int):
        """在滚动条上按下：返回拖动锚点，未命中返回 None。

        Args:
            x: 客户区 x。
            y: 客户区 y。

        Returns:
            拖动锚点 `{"offset": 按下点相对滑块顶端的偏移}`；未命中滚动条或
            弹窗遮挡时返回 None。

        Note:
            **按在轨道（非滑块）上时先跳到该位置**，再以「按下点在滑块中央」为
            锚点继续拖——这是滚动条的通行交互；只按在滑块上时保持原偏移，
            避免一按就跳半格。
        """
        if self._icon_grid is None or self.popup is not None:
            return None
        track = self._icon_grid.scrollbar_rect()
        thumb = self._icon_grid.thumb_rect()
        if track is None or thumb is None:
            return None
        lx, ly = self._grid_local(x, y)
        track_x1, track_y1, track_x2, track_y2 = track
        thumb_x1, thumb_y1, _thumb_x2, thumb_y2 = thumb
        if not (track_x1 <= lx <= track_x2 and track_y1 <= ly <= track_y2):
            return None
        if thumb_y1 <= ly <= thumb_y2:
            return {"offset": ly - thumb_y1}
        track_h = track_y2 - track_y1
        thumb_h = thumb_y2 - thumb_y1
        self._icon_grid.scroll_to_ratio((ly - track_y1) / max(1, track_h - thumb_h))
        moved = self._icon_grid.thumb_rect()
        if moved is None:
            return None
        return {"offset": (moved[3] - moved[1]) // 2}

    def drag_scrollbar_to(self, y: int, offset: int) -> bool:
        """把滑块拖到鼠标位置。

        Args:
            y: 当前客户区 y。
            offset: `begin_scrollbar_drag` 返回的偏移。

        Returns:
            是否真的发生了滚动。
        """
        if self._icon_grid is None or self.popup is not None:
            return False
        track = self._icon_grid.scrollbar_rect()
        thumb = self._icon_grid.thumb_rect()
        if track is None or thumb is None:
            return False
        _lx, ly = self._grid_local(0, y)
        _track_x1, track_y1, _track_x2, track_y2 = track
        travel = max(1, (track_y2 - track_y1) - (thumb[3] - thumb[1]))
        return self._icon_grid.scroll_to_ratio((ly - offset - track_y1) / travel)

    def root_xy(self, x: int, y: int, widget):
        """把事件坐标换算成客户区坐标。

        Args:
            x: 事件 x。
            y: 事件 y。
            widget: 事件发生的控件（`event.widget`）。

        Returns:
            客户区坐标 `(x, y)`。

        Note:
            网格画在子画布里，子画布上发生的事件其坐标是**子画布局部坐标**；
            Tk 的 `%x/%y` 以事件窗口为基准，不换算会让列表内的点击落到错误
            元素上（双击图标进不了关卡详情）。
        """
        if self._grid_canvas is not None and str(widget) == str(self._grid_canvas):
            gx1, gy1, _gx2, _gy2 = self._scaled(yolo_scene.GRID_RECT)
            return int(x) + gx1, int(y) + gy1
        return int(x), int(y)

    def begin_grid_drag(self, x: int, y: int):
        """在关卡列表区域内按下：返回拖拽滚动的锚点。

        Args:
            x: 客户区 x。
            y: 客户区 y。

        Returns:
            `{"y": 按下 y, "px": 按下时的滚动像素}`；不在列表区域、列表不可滚动
            或有弹窗遮挡时返回 None。

        Note:
            与滚动条分开的两条入口：列表内拖拽是「抓住内容往上/下拖」，方向与
            滚动条相反（内容上移 = 看后面的行），换算在 `drag_grid_to` 里做。
        """
        if self._icon_grid is None or self.popup is not None:
            return None
        if self._icon_grid.max_scroll_px() <= 0:
            return None
        gx1, gy1, gx2, gy2 = self._scaled(yolo_scene.GRID_RECT)
        if not (gx1 <= x <= gx2 and gy1 <= y <= gy2):
            return None
        return {"y": y, "px": self._icon_grid.scroll_px}

    def drag_grid_to(self, y: int, start_y: int, start_px: float) -> bool:
        """按列表内拖拽的位移平滑滚动（向上拖 = 看后面的行）。

        Args:
            y: 当前客户区 y。
            start_y: 按下时的 y。
            start_px: 按下时的滚动像素。

        Returns:
            是否真的发生了滚动。
        """
        if self._icon_grid is None or self.popup is not None:
            return False
        return self._icon_grid.scroll_to_px(start_px - (y - start_y))

    # ---------- `battle` 的持续动画 ----------

    def _create_battle_items(self) -> None:
        """创建 `battle` 动画的持久画布项（进入该画面时一次）。"""
        canvas = self._canvas
        ids: dict[str, int] = {}
        px1, py1, px2, py2 = self._scaled(_battle_progress_rect())
        canvas.create_rectangle(px1, py1, px2, py2, fill="#201010", outline="#604040", tags=("screen", "element"))
        ids["progress"] = canvas.create_rectangle(
            px1, py1, px1, py2, fill="#E06060", outline="", tags=("screen", "element")
        )
        cx1, cy1, _cx2, cy2 = self._scaled(_battle_countdown_rect())
        ids["countdown"] = canvas.create_text(
            cx1,
            (cy1 + cy2) // 2,
            text="",
            fill="#FFD060",
            font=("Consolas", self._font(_TITLE_FONT)),
            anchor="w",
            tags=("screen", "element"),
        )
        self._battle_ids = ids

    def tick(self, frame: int) -> None:
        """按帧号更新 `battle` 的进度条与倒计时（由 `app.py` 每帧调用）。

        Args:
            frame: 当前帧序号。

        Note:
            **只在该画面为 `battle` 且无弹窗时更新**：弹窗覆盖时动画即使仍在跑
            也看不见，更新它只是白费开销。
            进度与倒计时均由**帧号推导**（与 `anim.anim_phase` 同一取向），
            使消费方可用帧号反推应看到的值——动态因此成为**可校验**的事实。
        """
        if self.screen_id != "battle" or self._battle_ids is None or self.popup is not None:
            return
        canvas = self._canvas
        phase = (frame % BATTLE_PERIOD_FRAMES) / BATTLE_PERIOD_FRAMES
        px1, py1, px2, py2 = self._scaled(_battle_progress_rect())
        canvas.coords(self._battle_ids["progress"], px1, py1, px1 + (px2 - px1) * phase, py2)
        remaining = (1.0 - phase) * BATTLE_PERIOD_FRAMES / 60.0
        canvas.itemconfigure(self._battle_ids["countdown"], text=f"倒计时 {remaining:.1f}s")

    # ---------- 交互 ----------

    def hit_test(self, x: int, y: int) -> Element | None:
        """判断一个客户区坐标落在哪个元素上。

        Args:
            x: 客户区 x 坐标。
            y: 客户区 y 坐标。

        Returns:
            命中的元素；未命中任何元素时返回 None。

        Note:
            弹窗显示时**只在弹窗内做命中测试**：弹窗是模态覆盖层，
            它盖住的内容不应还能被点到——否则「关闭弹窗」与「点到底下的按钮」
            会同时发生，导航测试的结论随之失真。
        """
        target_id = self.popup or self.screen_id
        for element in SCREENS[target_id].elements:
            x1, y1, x2, y2 = self._scaled(element.rect)
            if x1 <= x < x2 and y1 <= y < y2:
                return element
        return None

    def set_slider_value(self, value: int) -> int:
        """直接设置灵敏度数值（拖动过程中按鼠标位置实时调用）。

        Args:
            value: 目标数值，会夹紧到 `[0, 100]`。

        Returns:
            实际生效的数值。

        Note:
            **数值没变时不重绘**：拖动时鼠标每次移动都会调用（每秒数百次），
            每次都 `draw()` 会让画面闪烁且拖慢事件处理。数值每变化 1 才重绘一次
            （约每 4px 位移），足够跟手。
        """
        clamped = max(0, min(100, int(value)))
        if clamped == self.slider_value:
            return self.slider_value
        self.slider_value = clamped
        self.draw()
        return self.slider_value

    def set_slider_from_drag(self, origin_value: int, dx: int) -> int:
        """按「按下时的数值 + 相对位移」实时设置滑块。

        Args:
            origin_value: 按下鼠标那一刻的数值。
            dx: 相对按下位置的水平位移（像素，右为正）。

        Returns:
            更新后的数值。

        Note:
            **必须基于固定锚点换算**，不能拿当前值逐次叠加位移：拖动过程中
            鼠标每次移动都会调用一次，按当前值累加会让数值随事件次数膨胀，
            且与鼠标的实际位置脱节。
        """
        from extensions_testbed import gestures

        x1, _y1, x2, _y2 = self._scaled(_slider_rect())
        return self.set_slider_value(gestures.apply_slider_offset(origin_value, dx, max(1, x2 - x1)))

    def drag_slider(self, dx: int) -> int:
        """按拖拽位移更新灵敏度滑块（`settings_menu`）。

        Args:
            dx: 拖拽的水平位移（像素，右为正）。

        Returns:
            更新后的数值。

        Note:
            夹紧到 `[0, 100]` 由 [`gestures.apply_slider_offset`][testbed.gestures.apply_slider_offset]
            保证——拖拽位移可任意大，不夹紧会让手柄跑出轨道（那会表现为界面缺陷）。
            更新后必须重绘该画面，否则数值文本与手柄位置不反映新值，
            拖动就成了一次「无效果」的交互，会被判为输入未生效。
        """
        return self.set_slider_from_drag(self.slider_value, dx)

    def body_probe_rect(self) -> tuple[int, int, int, int]:
        """返回主体区中一块**不含元素**的平坦区域矩形（供 `color` 判据配置）。

        Returns:
            实际像素矩形 `[x1, y1, x2, y2]`。

        Note:
            该矩形固定位于主体区右上角：各画面的按钮集中在水平中央，
            故右上角是稳定的平坦底色区域。`check_elements_inside()` 会校验
            **没有任何元素压到它上面**——那会让 `color` 判据读到元素底色而非画面底色。
        """
        return self._scaled(BODY_PROBE_RECT)


def _rects_overlap(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> bool:
    """判断两个矩形是否相交（边界相接不算相交）。

    Args:
        left: 第一个矩形。
        right: 第二个矩形。

    Returns:
        是否重叠。
    """
    return not (left[2] <= right[0] or right[2] <= left[0] or left[3] <= right[1] or right[3] <= left[1])


def _rect_contains(
    outer: tuple[int, int, int, int],
    inner: tuple[int, int, int, int],
) -> bool:
    """判断 `outer` 是否完整包含 `inner`。

    Args:
        outer: 外层矩形。
        inner: 内层矩形。

    Returns:
        是否包含。
    """
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def check_elements_inside() -> None:
    """校验所有画面元素、探测矩形与动画区域都落在其所属区域内。

    Raises:
        AssertionError: 某矩形越出所属区域，或两个元素互相重叠时抛出。

    Note:
        这是防「元素压进相邻区域」的**启动自检**（同 `layout.check_capacity` 的思路）：
        不依赖截图比对即可判定。实测曾发生 `exit_btn` 下沿越界 4px、
        压在静态色卡上方的事故——当时是靠使用者看截图才发现的。
        元素坐标现在由容器算出，本函数是兜底：
        若将来有人改回写死坐标或调整主体区高度，这里会立即报错。

        **元素两两重叠也在校验之列**：重叠会让「点击命中的是哪个元素」变得不确定
        （`hit_test` 只返回第一个命中的），而配置侧的区域却是各自独立的矩形——
        两侧对同一次点击的归属判断会分叉。T0-4 编写画面定义时曾因此出现两个按钮
        完全叠在同一个位置，本校验即为此而加。
    """
    bx1, by1, bx2, by2 = _body_rect()
    for screen in SCREENS.values():
        for element in screen.elements:
            x1, y1, x2, y2 = element.rect
            assert x1 >= bx1 and y1 >= by1 and x2 <= bx2 and y2 <= by2, (
                f"画面 {screen.screen_id} 的元素 {element.name} 越出 screen_body："
                f"元素 {element.rect}，主体区 {(bx1, by1, bx2, by2)}"
            )
        elements = screen.elements
        for index, left in enumerate(elements):
            for right in elements[index + 1 :]:
                assert not _rects_overlap(left.rect, right.rect), (
                    f"画面 {screen.screen_id} 的元素 {left.name} {left.rect} 与 "
                    f"{right.name} {right.rect} 重叠——点击归属将不确定"
                )

    # 标题区内的两个元素（标题 / 版本标签）同样校验，且彼此不得重叠
    hx1, hy1, hx2, hy2 = layout.rect_of("screen_header")
    for name, rect in _HEADER_ELEMENTS.items():
        x1, y1, x2, y2 = rect
        assert x1 >= hx1 and y1 >= hy1 and x2 <= hx2 and y2 <= hy2, (
            f"画面标题区的元素 {name} 越出 screen_header：元素 {rect}，标题区 {(hx1, hy1, hx2, hy2)}"
        )
    assert not _rects_overlap(_TITLE_RECT, _VERSION_RECT), (
        f"画面标题区的标题 {_TITLE_RECT} 与版本标签 {_VERSION_RECT} 重叠"
    )

    # 平坦探测矩形必须落在主体区内（`color` 判据依赖它取画面底色）
    px1, py1, px2, py2 = BODY_PROBE_RECT
    assert px1 >= bx1 and py1 >= by1 and px2 <= bx2 and py2 <= by2, f"平坦探测矩形越出 screen_body：{BODY_PROBE_RECT}"

    # **任何画面元素都不得压到平坦探测矩形上**：那会让该画面的 `color` 判据
    # 读到元素底色而非画面底色，判据失真且不会报错（属静默失败）
    for screen in SCREENS.values():
        for element in screen.elements:
            assert not _rects_overlap(element.rect, BODY_PROBE_RECT), (
                f"画面 {screen.screen_id} 的元素 {element.name} 与平坦探测矩形"
                f"{BODY_PROBE_RECT} 重叠——`color` 判据会读到元素底色而非画面底色"
            )

    # **`battle` 的两处动画区域同样不得压到平坦探测矩形**：它们是画布项而非
    # `Element`，不会出现在上面的循环里。实测曾发生进度条横贯宽度、贴顶部
    # 因而覆盖探测矩形（该画面的底色判据会读到进度条），故单列一条校验。
    for name, rect in (
        ("进度条", _battle_progress_rect()),
        ("倒计时", _battle_countdown_rect()),
    ):
        assert not _rects_overlap(rect, BODY_PROBE_RECT), (
            f"`battle` 的{name} {rect} 与平坦探测矩形 {BODY_PROBE_RECT} 重叠——该画面的 `color` 判据会读到动画而非底色"
        )

    # `battle` 的两处动画区域必须落在整块外观比对矩形 `panel_appearance` 之内：
    # 那正是适配层要用算子级 `ignore_regions` 排除它们的场景（§3.6）
    for name, rect in (
        ("进度条", _battle_progress_rect()),
        ("倒计时", _battle_countdown_rect()),
    ):
        assert _rect_contains(BATTLE_PANEL_RECT, rect), (
            f"`battle` 的{name} {rect} 不在整块外观比对矩形 {BATTLE_PANEL_RECT} 之内——"
            "『配了忽略区域后画面可稳定』这条验证将失去对象"
        )

    # `popup_notice` 的覆盖矩形必须盖住画面标题区与主体区：否则弹窗不会让当前画面的
    # 判别集失效，「判别集无命中」这一前提不成立
    cover = _popup_cover_rect()
    for region_id in ("screen_header", "screen_body"):
        assert _rect_contains(cover, layout.rect_of(region_id)), (
            f"弹窗覆盖矩形 {cover} 未盖住区域 {region_id} {layout.rect_of(region_id)}——弹窗将不会使当前画面的判别集失效"
        )


def check_label_keys() -> list[str]:
    """校验各画面元素的文案键在文案表中存在。

    Returns:
        **缺失**的键列表（形如 `画面 id/键名`）；全部存在时为空列表。

    Note:
        `l10n.screen_text` 找不到键时会**静默回退成键名本身**，界面上便显示
        `enter_battle` 这样的字面量。T0-2 曾因区域标题的同类问题而出现 7 处字面量；
        本函数把「静默显示错误」变成启动时可检出的显式问题。
    """
    missing: list[str] = []
    for screen_id, screen in SCREENS.items():
        table = l10n.SCREEN_TEXTS.get(screen_id, {})
        for element in screen.elements:
            if not element.label_key:
                continue
            if not any(element.label_key in table.get(lang, {}) for lang in l10n.LANGUAGES):
                missing.append(f"{screen_id}/{element.label_key}")
        # 画面自身的标题键也一并校验：它同样由 `screen_text` 查询，缺失时显示键名
        if not any("title" in table.get(lang, {}) for lang in l10n.LANGUAGES):
            missing.append(f"{screen_id}/title")
    return missing


def actions() -> dict[str, str]:
    """汇总全部「元素 → 动作」映射（供人工与自动化逐条核对）。

    Returns:
        `画面 id/元素名` → 动作 的字典；无动作的元素不出现。

    Note:
        该映射与适配层 `screens/zh/*.yaml` 的 `goto` 边是**同一条事实的两侧**：
        配置侧给工具读，程序侧是真实行为。两者不一致时导航测试会失真，
        故本函数存在的意义是让 T0-4 的验收可以逐条对照，而不是靠人工回忆。
    """
    result: dict[str, str] = {}
    for screen_id, screen in SCREENS.items():
        for element in screen.elements:
            if element.action:
                result[f"{screen_id}/{element.name}"] = element.action
            if element.double_action:
                result[f"{screen_id}/{element.name}(双击)"] = element.double_action
    return result
