# -*- coding: utf-8 -*-
"""场景管理统一的浮层提示入口。

整个能力里凡「一次操作的结果需要用户当下就知道」的提示，都走这里，而不是各页面
自己往 `status_label` 里塞一行字：

- **状态栏是常驻数据**（当前选中的场景、搜索条件、路径描述），用户会主动去看；
- **操作结果是事件**，需要主动推到用户眼前。实测反例：用户点「开始探索」后
  视线还在按钮上，而结果写在流转页底部那行小字里，表现就是「点了没反应」。

底层用框架的 `ok.ui.qt.kit.show_toast`（与框架自己的调试页同一套组件）：自动
定位到窗口顶部居中、跟随窗口缩放重排、点 × 关闭、超时消失、主题切换跟着变色。
本模块只做三件事——**分级、默认时长、文案约定**，避免每个调用点各写一份参数。

## 分级

| 级别 | 用于 | 图标 |
|---|---|---|
| `info` | 「已入队」「已同步 3 个场景」这类纯告知 | 信息 |
| `success` | 「试走成功」这类操作成功 | 对勾 |
| `warning` | 「请先选择起点与目标」「截图目标可能选错」——**用户能做点什么** | 警告三角 |
| `error` | 「导入失败」「取不到任务」——**操作没做成** | 警告三角 |

## 与确认框的分工

浮层只做**单向告知**，不承载选择。需要用户回答（是/否）的一律用
`ok.ui.qt.kit.confirm`——浮层没有模态性，用户可能直接忽略它继续操作。
"""

from ok.ui.qt.kit import show_toast

from extensions.capabilities.scene_manager.translate import tr

# 级别 → 框架 Toast 的 tone。框架支持 neutral/accent/success/warning/danger，
# 这里映射成业务语义，调用点不必记住框架的取值。
_LEVEL_TONES = {
    "info": "accent",
    "success": "success",
    "warning": "warning",
    "error": "danger",
}

# 级别 → 默认停留时长（毫秒）。
#
# 分级而不是统一：警告与错误需要读完再行动（含「去主界面点启动」这种跨页操作），
# 4 秒不够；纯告知看完就够，留太久会盖住页面内容。
_DEFAULT_DURATION = {
    "info": 5000,
    "success": 4000,
    "warning": 8000,
    "error": 8000,
}


def _tone(level):
    """把业务级别翻译成框架 tone。

    Args：
        level：`info` / `success` / `warning` / `error`。

    Returns：
        框架 tone 字符串。

    Raises：
        ValueError：级别未知。**不静默回退**——拼错的级别会让提示以错误的
            颜色出现，而「看起来没那么严重」的错误提示比没有提示更糟。
    """
    try:
        return _LEVEL_TONES[level]
    except KeyError as error:
        raise ValueError(f"未知的提示级别 {level!r}，只支持 {sorted(_LEVEL_TONES)}") from error


def notify(parent, message, level="info", title=None, duration=None, action=None):
    """在 `parent` 所在窗口顶部弹一条浮层提示。

    Args：
        parent：任意控件；浮层挂到它**所在的窗口**（对话框会挂到对话框自身，
            因此编辑器里的提示不会跑到主窗口上）。
        message：正文。
        level：`info` / `success` / `warning` / `error`。
        title：可选的加粗标题。警告与错误建议给，单看正文容易漏掉「这是问题」。
        duration：停留毫秒数；省略时按级别取默认值。
        action：可选的 `(按钮文字, 回调)`，例如「去启动」。

    Returns：
        `ok.ui.qt.kit.feedback.Toast` 实例（测试与调用方可以用来断言）。

    Note：
        **父窗口不可用时静默返回 None**，不抛异常：提示是辅助手段，让它把正常
        业务逻辑带崩是本末倒置。典型不可用场景是窗口正在销毁（切页签、关弹窗）。
    """
    if parent is None:
        return None
    tone = _tone(level)
    if duration is None:
        duration = _DEFAULT_DURATION[level]
    try:
        return show_toast(parent, message, title, tone=tone, duration=duration, action=action)
    except RuntimeError:
        # 控件的 C++ 对象已销毁（窗口关闭、页签重建）。此时提示无处可挂，
        # 直接放弃即可——用户已经看不到那个界面了。
        return None


def notify_info(parent, message, title=None, **kwargs):
    """弹一条信息级浮层。见 :func:`notify`。"""
    return notify(parent, message, "info", title, **kwargs)


def notify_success(parent, message, title=None, **kwargs):
    """弹一条成功级浮层。见 :func:`notify`。"""
    return notify(parent, message, "success", title, **kwargs)


def notify_warning(parent, message, title=None, **kwargs):
    """弹一条警告级浮层。见 :func:`notify`。"""
    return notify(parent, message, "warning", title, **kwargs)


def notify_error(parent, message, title=None, **kwargs):
    """弹一条错误级浮层。见 :func:`notify`。"""
    return notify(parent, message, "error", title, **kwargs)


def translated(text):
    """翻译一条要给用户看的文案。

    Args：
        text：英文源串。

    Returns：
        当前语言下的译文。

    Note：
        浮层文案多数是 f-string 拼出来的动态串（含场景名、尺寸、错误原因），
        这类不进 catalog；只有固定短语才需要经由本函数。把两者混在一起翻译
        只会得到一串永远匹配不上的条目。
    """
    return tr(text)
