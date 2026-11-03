# -*- coding: utf-8 -*-
"""扩展翻译域能力。

能力名：`extension-i18n`（总览表与测试文件名的命名来源）。
实现见同包的 `translator` 模块；本文件只做对外装配契约。

本能力的生效时机不是 config 装配期，而是 `og.app` 构造完成之后——翻译对象挂在
`app.po_translation` 上，装配期它还不存在。因此装配期的 `register` 是空实现，
真正的动作在 `on_app_ready`。
"""

from extensions.capabilities.extension_i18n import translator


def register(config):
    """装配期不做任何事。

    之所以仍然定义 `register`、而不是省略它：能力包契约要求每个能力都显式
    回答「装配期做什么」。省略后无法区分「这个能力无需装配期动作」与
    「这个能力忘了写装配代码」，后者会让新能力静默不生效。

    Args：
        config：应用配置字典，本能力不读取。
    """
    return


def on_app_ready(app):
    """`og.app` 构造完成、`my_app` 尚未创建时调用。

    必须在 `my_app` 之前：框架的 `tr()` 在 `po_translation` 为 `None` 时会去读上游
    `ok` 域并缓存，晚于任何一次 `tr()` 就只能改缓存值、无法接管链。

    Args：
        app：框架的 App 或 HeadlessApp 实例。
    """
    translator.install(app)
