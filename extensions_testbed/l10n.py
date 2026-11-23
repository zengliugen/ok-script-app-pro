"""测试靶场的界面文案表（本期只启用 `zh`）。

`F10` 可在 `zh` / `en` 之间切换。**配置不做回退**——`en` 版一旦启用必须独立一套
（见 `extensions_testbed/README.md` §3.5）；本模块只负责程序内的界面文案，
与适配层 `screens/<app_lang>/` 无关。
"""

from __future__ import annotations

# 支持的语言（顺序即 F10 的循环顺序）
LANGUAGES: tuple[str, ...] = ("zh", "en")

# 界面文案：语言 → 键 → 文本
TEXTS: dict[str, dict[str, str]] = {
    "zh": {
        "title": "ok-script-app-pro 测试靶场",
        # 区域标题：键名格式为 `header.<layout.REGIONS[].id>`，与布局区域**一一对应**。
        # 键名与区域 id 必须逐字相同——`text()` 找不到键会回退成键名本身，
        # 界面上就会显示 `header.xxx` 这样的字面量（T0-2 曾因 7 处不匹配而如此）。
        "header.status_bar": "状态自述",
        "header.screen_header": "画面标题",
        "header.screen_body": "画面主体",
        "header.color_card": "静态色卡",
        "header.frame_counter": "帧计数",
        "header.raw_counters": "原始消息计数（WndProc）",
        "header.app_counters": "应用计数（Tk 回调）",
        "header.anim_region": "动画区",
        "header.text_field": "文本输入区",
        "hotkeys": "F1 动画开关  F3 横幅底色  F4 色卡显隐  F5 清空计数  F6 自述区显隐  F7 下一画面  F9 弹窗  F10 语言  Esc 无操作",
        "text_prefix": "输入：",
        "anim_on": "动画区：开",
        "anim_off": "动画区：关",
        "frame_prefix": "帧 ",
        "log_prefix": "日志 ",
        "screen_prefix": "画面 ",
        "foreground": "前台",
        "background": "后台",
        "lang_prefix": "语言 ",
    },
    "en": {
        "title": "ok-script-app-pro TestBed",
        "header.status_bar": "Status",
        "header.screen_header": "Screen title",
        "header.screen_body": "Screen body",
        "header.color_card": "Color card",
        "header.frame_counter": "Frame counter",
        "header.raw_counters": "Raw messages (WndProc)",
        "header.app_counters": "App counters (Tk callbacks)",
        "header.anim_region": "Animation",
        "header.text_field": "Text field",
        "hotkeys": "F1 anim  F3 banner  F4 color card  F5 reset  F6 status  F7 next screen  F9 popup  F10 language  Esc none",
        "text_prefix": "Input: ",
        "anim_on": "Animation: on",
        "anim_off": "Animation: off",
        "frame_prefix": "Frame ",
        "log_prefix": "Log ",
        "screen_prefix": "Screen ",
        "foreground": "foreground",
        "background": "background",
        "lang_prefix": "Lang ",
    },
}

# 画面文案：画面 id → 语言 → 键 → 文本
SCREEN_TEXTS: dict[str, dict[str, str]] = {
    "entry": {
        "zh": {
            "title": "靶场入口",
            "start": "开始",
            "settings": "设置",
            "help": "帮助",
            "exit": "退出",
        },
        "en": {"title": "Testbed Entry", "start": "Start", "settings": "Settings", "help": "Help", "exit": "Exit"},
    },
    "chapter_select": {
        "zh": {"title": "关卡选择", "back": "返回"},
        "en": {"title": "Chapter Select", "back": "Back"},
    },
    "chapter_detail": {
        "zh": {"title": "关卡详情", "enter_battle": "进入战斗", "back": "返回"},
        "en": {"title": "Chapter Detail", "enter_battle": "Enter Battle", "back": "Back"},
    },
    "battle": {
        "zh": {"title": "战斗中", "enter_settle": "结束战斗", "back": "返回", "dodge": "闪避区"},
        "en": {"title": "Battle", "enter_settle": "Settle", "back": "Back", "dodge": "Dodge"},
    },
    "settlement": {
        "zh": {"title": "结算", "confirm": "确认"},
        "en": {"title": "Settlement", "confirm": "Confirm"},
    },
    "settings_menu": {
        "zh": {"title": "设置菜单", "back": "返回", "lang_toggle": "语言切换", "slider": "灵敏度"},
        "en": {"title": "Settings", "back": "Back", "lang_toggle": "Language", "slider": "Sensitivity"},
    },
    "help_page": {
        "zh": {"title": "帮助", "back": "返回"},
        "en": {"title": "Help", "back": "Back"},
    },
    "popup_notice": {
        "zh": {"title": "提示弹窗", "close": "关闭"},
        "en": {"title": "Notice", "close": "Close"},
    },
}


def text(lang: str, key: str) -> str:
    """取通用界面文案。

    Args:
        lang: 语言代码，`zh` 或 `en`。
        key: 文案键。

    Returns:
        对应文案；键在中英文表中都缺失时返回键本身。

    Note:
        回退**仅用于程序界面文案**，与适配层配置的「语言不做回退」约束无关
        （后者是 04-config-spec.md §4.7.3 的硬要求）。

        注意：返回键名本身是**兜底**，不是正常路径——界面出现 `header.xxx` 这类
        字面量即说明文案表缺键。故启动时用 `check_keys()` 主动校验，不依赖肉眼发现。
    """
    return TEXTS.get(lang, TEXTS["zh"]).get(key) or TEXTS["zh"].get(key, key)


def check_keys(keys: list[str]) -> list[str]:
    """校验一组文案键在中英文表中均存在。

    Args:
        keys: 待校验的文案键列表。

    Returns:
        **缺失**的键列表（中文或英文任一缺失即计入）；全部存在时为空列表。

    Note:
        区域标题的键由 `f"header.{region_id}"` 动态拼出，拼错不会报错——只会让
        界面显示字面量键名（T0-2 曾因此有 7 个区域如此）。本函数把这种「静默
        显示错误」变成启动时可检出的显式问题。
    """
    missing: list[str] = []
    for key in keys:
        if key not in TEXTS["zh"] or key not in TEXTS["en"]:
            missing.append(key)
    return missing


def screen_text(screen_id: str, lang: str, key: str) -> str:
    """取画面内文案。

    Args:
        screen_id: 画面 id。
        lang: 语言代码。
        key: 文案键。

    Returns:
        对应文案；缺失时逐级回退（语言 → 中文 → 键本身）。
    """
    per_screen = SCREEN_TEXTS.get(screen_id, {})
    table = per_screen.get(lang) or per_screen.get("zh") or {}
    return table.get(key, key)
