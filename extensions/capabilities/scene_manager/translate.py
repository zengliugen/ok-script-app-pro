# -*- coding: utf-8 -*-
"""页面层的统一翻译入口。

`CustomTab` 自带 `self.tr()`，但本能力的四个子页签与两个对话框都是普通
`QWidget` / `QDialog`，没有这个方法。它们又必须能拿到同一个翻译域（`ok_ext`），
否则「同一个按钮在页签里是中文、在弹窗里是英文」。

因此这里提供一个模块级函数：能拿到 `og.app` 就走它的 `tr()`（与 `CustomTab.tr`
同一条链路，含 `ok_ext` 扩展域与上游域的回退），拿不到（单元测试、无 GUI 环境）
就原样返回。**不静默吞掉异常**：只有 app 不存在或 tr 本身不可调用时才回落，
其余异常照常抛出——那说明框架的翻译装配出了问题，掩盖它会让界面悄悄退化成英文。
"""


def tr(text):
    """翻译一条界面文案。

    Args：
        text：英文源串。`msgid` 必须与它逐字符一致。

    Returns：
        当前语言下的译文；无翻译环境时返回原文。
    """
    if not text:
        return text
    try:
        from ok import og
    except ModuleNotFoundError:  # 纯逻辑测试环境下没有框架，直接返回原文
        return text
    app = getattr(og, "app", None)
    if app is None:
        return text
    translate = getattr(app, "tr", None)
    if not callable(translate):
        return text
    return translate(text)
