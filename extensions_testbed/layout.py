"""测试靶场的布局定义（**布局的唯一真值**）。

界面绘制由本模块的 `REGIONS` 驱动——不是「清单尽量跟布局一致」，而是
「**布局由清单生成**」（见 `extensions_testbed/README.md` §5.4）。

9 个区域**恰好铺满**客户区（面积和 = 1280×720、无重叠、无空隙），
这样「裁剪掉动态区域后余下部分」的概念才严格成立（`M0-诊断场景需求.md` §3K）。

**缩放策略**：区域边界由两套共享的分割点（`_X_CUTS` / `_Y_CUTS`）表达，
按客户区尺寸等比例换算。这样任意尺寸下仍然严格铺满——若改为「逐区域缩放宽高」，
取整误差会在相邻区域间留下 1px 缝隙或重叠。
"""

from __future__ import annotations

from dataclasses import dataclass

# 基准客户区尺寸：配置坐标与 `REGIONS` 一律以此表达（见 extensions_testbed/README.md §5.3）
BASE_W, BASE_H = 1280, 720


@dataclass(frozen=True, slots=True)
class Region:
    """一个布局区域。

    Attributes:
        id: 区域标识（`dynamic_regions` 清单中的 `id`）。
        rect: 基准客户区像素矩形 `[x1, y1, x2, y2]`（左闭右开风格：右下不含）。
        change_when: 该区域按什么方式变化，见 `CHANGE_WHEN` 的取值说明。
    """

    id: str
    rect: tuple[int, int, int, int]
    change_when: str


# `change_when` 取值（沿用 `M0-诊断场景需求.md` §3K，把 `engine` 改为 `app`——
# 靶场无游戏引擎，对应概念是 Tk 的事件绑定回调是否被触发，见 extensions_testbed/README.md §6.1）。
#
# always           每帧都可能变（帧计数器、自述区、动画区开启时）
# on_raw_message   仅当窗口收到消息时变（WndProc 计数区）
# on_app_input     仅当应用层感知到输入时变（应用计数区 / 状态横幅）
# on_app_or_raw    任一计数变化时变（应用计数区）
# on_text_change   文本内容变化时（文本输入区）
# on_screen        画面切换时变（画面标题 / 画面主体）
# static           永不变化（静态色卡、快捷键提示）
CHANGE_WHEN: tuple[str, ...] = (
    "always",
    "on_raw_message",
    "on_app_input",
    "on_app_or_raw",
    "on_text_change",
    "on_screen",
    "static",
)

# 区域标题带的高度（像素，基准坐标）。标题画在这条带内，**内容一律不得进入**——
# 否则标题与内容会重叠（T0-2 曾因此出现 5 处文字叠字）。
HEADER_H = 20

# 需要绘制标题带的区域。**画面标题区与画面主体区不在此列**：
# 这两个区域承载的是「被识别的画面内容」，往里面画区域标题会污染
# 模板匹配与 OCR 的 ROI，使判据不稳。它们靠边框与画面自身内容区分。
LABELED_REGIONS: frozenset[str] = frozenset(
    {
        "status_bar",
        "color_card",
        "frame_counter",
        "raw_counters",
        "app_counters",
        "anim_region",
        "text_field",
    }
)

# 9 个区域，恰好铺满 1280×720（面积和 = 921600）
#
# 纵向分割点（各区域高度已按「标题带 + 内容」的实际需要分配，见下方注释）：
#   0 / 64 / 164 / 460 / 548 / 624 / 672 / 720
REGIONS: tuple[Region, ...] = (
    # ① 自述区（64px = 标题带 20 + 两行文本 42 + 余量）：客户区尺寸 / DPI / 前台 / 帧率 / 画面 / 日志
    Region("status_bar", (0, 0, 1280, 64), "always"),
    # ② 画面标题区（100px，**无标题带**）：各画面绘制自己的标题与版本标签
    Region("screen_header", (0, 64, 1280, 164), "on_screen"),
    # ③ 画面主体区（296px，**无标题带**）：按钮 / 图标 / 滑块 / 进度条等交互元素
    Region("screen_body", (0, 164, 1280, 460), "on_screen"),
    # ④ 静态色卡（88px = 标题带 20 + 色块 64）：6 个精确 RGB 纯色块，含中灰 128
    Region("color_card", (0, 460, 440, 548), "static"),
    # ⑤ 帧计数器（88px = 标题带 20 + 大号数字 54）：每帧自增（**过期帧检测的唯一手段**）
    Region("frame_counter", (440, 460, 1280, 548), "always"),
    # ⑥ 原始消息计数（76px = 标题带 20 + 三行 48）：WndProc 统计的 Win32 消息
    Region("raw_counters", (0, 548, 640, 624), "on_raw_message"),
    # ⑦ 应用计数区（76px = 标题带 20 + 三行 48）：Tk 回调统计；**底色即状态横幅**（F3 切换）
    Region("app_counters", (640, 548, 1280, 624), "on_app_or_raw"),
    # ⑧ 可开关动画区（48px = 标题带 20 + 一行 19）：F1 开关；关闭时必须完全静止
    Region("anim_region", (0, 624, 1280, 672), "always"),
    # ⑨ 文本输入区（48px = 标题带 20 + 一行 22）：验证字符链路；可打印字符累积显示
    Region("text_field", (0, 672, 1280, 720), "on_text_change"),
)

# 区域标识 → 基准矩形的查找表（避免每次线性扫描）
_BY_ID: dict[str, Region] = {region.id: region for region in REGIONS}


def region(region_id: str) -> Region:
    """按标识取区域定义。

    Args:
        region_id: 区域标识。

    Returns:
        对应的区域定义。

    Raises:
        KeyError: 标识不存在时抛出。
    """
    return _BY_ID[region_id]


def rect_of(region_id: str) -> tuple[int, int, int, int]:
    """取区域的基准矩形（1280×720 下的像素坐标）。

    Args:
        region_id: 区域标识。

    Returns:
        基准矩形 `[x1, y1, x2, y2]`。
    """
    return _BY_ID[region_id].rect


def _scale_cut(value: int, base: int, actual: int) -> int:
    """把一个分割点按比例换算到实际尺寸。

    Args:
        value: 基准尺寸下的分割点坐标。
        base: 基准尺寸（宽或高）。
        actual: 实际尺寸（宽或高）。

    Returns:
        换算后的坐标，按四舍五入取整。
    """
    return round(value * actual / base)


def scale_rect(rect: tuple[int, int, int, int], width: int = BASE_W, height: int = BASE_H) -> tuple[int, int, int, int]:
    """把基准坐标下的矩形按比例换算到实际客户区尺寸。

    Args:
        rect: 基准坐标下的矩形 `[x1, y1, x2, y2]`。
        width: 实际客户区宽度（像素）。
        height: 实际客户区高度（像素）。

    Returns:
        实际像素矩形 `[x1, y1, x2, y2]`。

    Note:
        各画面内的元素矩形（按钮、图标等）在**基准坐标**下书写，绘制时经本函数换算。
        这样配置里的 `rect` 与程序里的绘制始终是同一个坐标系（1280×720）。
    """
    x1, y1, x2, y2 = rect
    return (
        _scale_cut(x1, BASE_W, width),
        _scale_cut(y1, BASE_H, height),
        _scale_cut(x2, BASE_W, width),
        _scale_cut(y2, BASE_H, height),
    )


def scaled_rect(region_id: str, width: int = BASE_W, height: int = BASE_H) -> tuple[int, int, int, int]:
    """取区域在实际客户区尺寸下的矩形。

    Args:
        region_id: 区域标识。
        width: 实际客户区宽度（像素）。
        height: 实际客户区高度（像素）。

    Returns:
        实际像素矩形 `[x1, y1, x2, y2]`。

    Note:
        缩放以**共享分割点**为单位而不是逐区域缩放宽高——后者会因取整误差
        在相邻区域之间留下缝隙或重叠，破坏「恰好铺满」这一前提。
    """
    return scale_rect(_BY_ID[region_id].rect, width, height)


def content_rect(region_id: str, width: int = BASE_W, height: int = BASE_H) -> tuple[int, int, int, int]:
    """取区域**内容区**的矩形（扣掉顶部标题带）。

    Args:
        region_id: 区域标识。
        width: 实际客户区宽度（像素）。
        height: 实际客户区高度（像素）。

    Returns:
        实际像素矩形 `[x1, y1, x2, y2]`。

    Note:
        内容必须画在内容区内，否则会与区域标题重叠（T0-2 曾因此出现 5 处叠字）。
        未被 `LABELED_REGIONS` 登记的区域没有标题带，内容区即整个区域。
    """
    x1, y1, x2, y2 = scaled_rect(region_id, width, height)
    if region_id not in LABELED_REGIONS:
        return (x1, y1, x2, y2)
    return (x1, y1 + _scale_cut(HEADER_H, BASE_H, height), x2, y2)


def as_dynamic_regions(width: int = BASE_W, height: int = BASE_H) -> list[dict[str, object]]:
    """生成 `dynamic_regions` 清单（供 `ready` 事件使用）。

    Args:
        width: 实际客户区宽度（像素）。
        height: 实际客户区高度（像素）。

    Returns:
        清单列表，每项含 `id` / `change_when` / `rect` / `rect_ratio` 四个键。

    Note:
        `rect` 与 `rect_ratio` 两套坐标都要给（`M0-诊断场景需求.md` §3K）：
        `rect` 让消费方无需猜测即可裁剪；`rect_ratio` 使布局变化时仍可换算。
        二者不一致时以 `rect` 为准。
    """
    items: list[dict[str, object]] = []
    for item in REGIONS:
        x1, y1, x2, y2 = scaled_rect(item.id, width, height)
        items.append(
            {
                "id": item.id,
                "change_when": item.change_when,
                "rect": [x1, y1, x2, y2],
                "rect_ratio": [
                    round(x1 / width, 6),
                    round(y1 / height, 6),
                    round(x2 / width, 6),
                    round(y2 / height, 6),
                ],
            }
        )
    return items


def check_tiled(width: int = BASE_W, height: int = BASE_H) -> None:
    """校验 9 个区域恰好铺满客户区（无重叠、无空隙）。

    Args:
        width: 实际客户区宽度（像素）。
        height: 实际客户区高度（像素）。

    Raises:
        AssertionError: 面积和与客户区面积不等时抛出，说明布局定义有误。

    Note:
        这是「裁剪后余下部分」概念成立的前提，故在启动时自检一次。
    """
    total = sum(
        (rect[2] - rect[0]) * (rect[3] - rect[1]) for rect in (scaled_rect(item.id, width, height) for item in REGIONS)
    )
    assert total == width * height, f"布局区域未铺满客户区：面积和 {total} != 客户区 {width * height}"


# 各区域内容所需的**最小高度**（基准像素），含标题带。
# 值是「行数 × 行高 + 标题带 + 上下留白」的实际需要，不是随手取的整数。
# 用途是容量自检：区域被改窄到装不下内容时立刻暴露，而不是等到界面上叠字或被裁掉。
_MIN_CONTENT_H: dict[str, int] = {
    "status_bar": HEADER_H + 2 * 19 + 4,  # 标题 + 两行文本（域名行 / 日志行）
    "color_card": HEADER_H + 56,  # 标题 + 色块（top +24 / bottom -6）
    "frame_counter": HEADER_H + 50,  # 标题 + 40px 大号数字
    "raw_counters": HEADER_H + 3 * 17 + 4,  # 标题 + 三行计数
    "app_counters": HEADER_H + 3 * 17 + 4,  # 标题 + 三行计数
    "anim_region": HEADER_H + 28,  # 标题 + 状态行 / 运动块
    "text_field": HEADER_H + 24,  # 标题 + 一行输入文本
}


def check_capacity(width: int = BASE_W, height: int = BASE_H) -> None:
    """校验各区域高度足以容纳「标题带 + 内容」。

    Args:
        width: 实际客户区宽度（像素）。
        height: 实际客户区高度（像素）。

    Raises:
        AssertionError: 某区域高度小于其最小需要时抛出，附区域名与差额。

    Note:
        这是防「叠字 / 内容被裁掉」的**静态防线**——不依赖渲染即可判定。
        T0-2 曾因缺少本校验而出现 5 处标题与内容重叠、2 处末行被裁。
    """
    for region_id, min_h in _MIN_CONTENT_H.items():
        _x1, y1, _x2, y2 = scaled_rect(region_id, width, height)
        actual = y2 - y1
        # 留 1px 取整余量：非整数比例下分割点的四舍五入可能使区域少 1px，
        # 这属正常缩放误差而非布局错误；真正装不下时差额远大于此。
        need = _scale_cut(min_h, BASE_H, height) - 1
        assert actual >= need, (
            f"区域 {region_id} 高度不足：实际 {actual}px < 需要 {need}px"
            "（标题带 + 内容），会导致标题与内容重叠或内容被裁掉"
        )
