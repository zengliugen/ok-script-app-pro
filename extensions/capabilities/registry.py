# -*- coding: utf-8 -*-
"""扩展能力清单与派发（唯一登记点）。

`extensions/` 的结构是**按能力分包**：一个能力一个子包，包名是能力名的 snake_case 形式。
本清单按**装载顺序**列出全部能力包，`bootstrap` 与 `app_globals` 都只经本模块访问能力，
因此新增能力只需在此追加一行，不必改动任何装配逻辑。

三处命名必须一致，缺一即被体检判为错误：

| 位置 | 形态 | 示例 |
|---|---|---|
| 能力包目录 | `extensions/capabilities/<snake_case>/` | `log_noise_filter/` |
| 本清单条目 | 完整模块路径 | `extensions.capabilities.log_noise_filter` |
| 文档总览表「能力」列 | kebab-case 能力名 | `log-noise-filter` |

顺序有意义：先装的能力先执行 `register(config)`，也先生效其生命周期钩子。
依赖别的能力先就位的，必须排在后面。
"""

from importlib import import_module

CAPABILITIES = (
    "extensions.capabilities.log_noise_filter",
    "extensions.capabilities.update_check_guard",
)


def register_all(config):
    """按清单顺序调用每个能力包的 `register(config)`。

    `register` 是**必备契约**：能力包必须有它，否则视为结构错误而非
    「该能力没有注册动作」——缺契约会让新能力静默不生效。

    Args：
        config：应用配置字典，由 `src/config.py` 构造并原地修改。

    Raises：
        AttributeError：某个能力包未提供 `register`。
    """
    for module_path in CAPABILITIES:
        module = import_module(module_path)
        register = getattr(module, "register", None)
        if register is None:
            raise AttributeError(
                "能力契约缺失：" + module_path + " 未提供 register(config)，"
                "请按 extensions/capabilities/__init__.py 的约定补齐"
            )
        register(config)


def dispatch_hook(hook_name, *args):
    """按清单顺序，把生命周期事件派发给实现了该钩子的能力包。

    与 `register` 不同，钩子是**可选**的：多数能力只在 config 装配期注册，
    不需要生命周期回调，缺钩子属正常情形。但能力包本身 import 失败必须抛出，
    否则「能力静默未装载」会被误当成「该能力没有这个钩子」。

    Args：
        hook_name：钩子函数名，如 `on_show_main_window`。
        *args：透传给钩子的位置参数。
    """
    for module_path in CAPABILITIES:
        module = import_module(module_path)
        hook = getattr(module, hook_name, None)
        if hook is not None:
            hook(*args)
