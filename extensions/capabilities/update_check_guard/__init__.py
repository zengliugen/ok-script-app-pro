# -*- coding: utf-8 -*-
"""更新检查守卫能力。

能力名：`update-check-guard`（总览表与测试文件名的命名来源）。
实现见同包的 `guard` 模块；本文件只做对外装配契约。

本能力的生效时机不是 config 装配期，而是主窗口构造完成之后——它要读
`about_tab` 上的更新卡片，config 装配期那棵树还不存在。因此装配期的
`register` 是空实现，真正的动作在 `on_show_main_window`。
"""

from extensions.capabilities.update_check_guard import guard


def register(config):
    """装配期不做任何事。

    之所以仍然定义 `register`、而不是省略它：能力包契约要求每个能力都显式
    回答「装配期做什么」。省略后无法区分「这个能力无需装配期动作」与
    「这个能力忘了写装配代码」，后者会让新能力静默不生效。

    Args：
        config：应用配置字典，本能力不读取。
    """
    return


def on_show_main_window(main_window):
    """主窗口构造完成、尚未显示时调用，启动器不可用时摘掉更新卡片。

    Args：
        main_window：上游 `MainWindow` 实例。
    """
    guard.apply(main_window)
