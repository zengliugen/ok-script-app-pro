# -*- coding: utf-8 -*-
"""日志噪音过滤能力。

能力名：`log-noise-filter`（总览表与测试文件名的命名来源）。
实现见同包的 `noise_filter` 模块；本文件只做对外装配契约。
"""

from extensions.capabilities.log_noise_filter import noise_filter


def register(config):
    """向框架装载本能力。

    本能力只需在 config 装配期把 logging 过滤器挂到 ok logger 上，不向 config
    写入任何键，因此忽略入参、保留以统一签名。

    Args：
        config：应用配置字典，本能力不读取。
    """
    noise_filter.install()
