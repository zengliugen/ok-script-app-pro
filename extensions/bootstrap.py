# -*- coding: utf-8 -*-
"""项目扩展装载入口。

由 src/config.py 末尾一行调用，是项目扩展生效的**唯一接线点**：

    from extensions import bootstrap; bootstrap.apply(config)

时序保证（已核实的框架行为）：
    config 字典字面量已构造完毕，尚未调用 ok.OK(config)。
    OK.__init__ 会原地读写同一个 dict 且不做深合并，因此覆写与注册追加
    必须在此刻完成，之后再无机会。
"""

from extensions import overrides
from extensions.core import log_noise_filter


def apply(config):
    """按固定顺序装载全部项目扩展。

    顺序：
        1. overrides  —— 先改值，后追加注册项（注册项可能依赖覆写后的值）
        2. patches    —— patch 在注册前完成，保证注册的类被使用前已就位
        3. 能力装载   —— 各能力自行安装钩子或向 config 追加任务、Tab 等

    Args：
        config：应用配置字典，由 src/config.py 构造并原地修改
    """
    overrides.apply(config)
    _apply_patches()
    _register_capabilities(config)


def _apply_patches():
    """装载 extensions/patches/ 下的全部 patch。

    每个 patch 模块需提供 apply() 函数，且内部必须断言目标符号存在。
    当前无 patch——优先使用扩展点，patch 是最后手段。
    """
    # 无 patch 时为空实现；新增 patch 后在此按需 import 并调用
    return


def _register_capabilities(config):
    """装载各能力。

    日志噪音过滤在此安装：它在 config 装配期只做一次 logger 挂载，
    不依赖 OK 实例；此处装上后，后续 TaskExecutor 配置日志时不会被覆盖
    （config_logger 只重建 handlers，不清 filters）。

    Args：
        config：应用配置字典，当前能力未读取该参数，保留以固定签名
    """
    log_noise_filter.install()
