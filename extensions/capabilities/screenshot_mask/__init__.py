# -*- coding: utf-8 -*-
"""截图遮罩能力。

能力名：`screenshot-mask`（总览表与测试文件名的命名来源）。
实现见同包的 `geometry` / `processor` / `tab` 三个模块；本文件只做对外装配契约。

装配期做三件事，顺序不可交换：

1. **迁移旧配置**。必须在 `OK(config)` 构造 `GlobalConfig` 之前完成——那时
   `Config` 会把未知键当脏数据丢弃，旧中文键里的数值就再也拿不回来了。
2. **登记全局配置项**。`OK.__init__` 用 `config['global_configs']` 一次性构造
   `GlobalConfig`，之后再追加不会被看到。
3. **替换 `screenshot_processor` 并登记页签**。前者是上游提供的正式回调扩展点，
   后者走 `custom_tabs`。

以上全部发生在 `bootstrap.apply(config)` 里，早于 `OK(config)`，因此无需改动
`src/config.py`——本项目对上游的差异面没有增加。
"""

from extensions.capabilities.screenshot_mask import processor

TAB_MODULE = "extensions.capabilities.screenshot_mask.tab"
TAB_CLASS = "ScreenshotMaskTab"


def register(config):
    """向框架装载本能力。

    Args：
        config：应用配置字典。

    Raises：
        RuntimeError：上游缺少 `screenshot_processor` / `global_configs`，
            说明扩展点已被改动。必须崩溃并指名：静默不生效的表现是「功能莫名消失」，
            比启动失败更难排查。

    注：`custom_tabs` 不要求预先存在——上游用 ``config.get('custom_tabs')`` 读取，
    没有该键时新建即可。`screenshot_processor` 与 `global_configs` 则必须已在
    `src/config.py` 的字典字面量里，因为它们决定「这个应用怎么截图」与「设置页有什么」，
    缺失说明上游结构变了。
    """
    missing = [key for key in ("screenshot_processor", "global_configs") if key not in config]
    if missing:
        raise RuntimeError(
            "截图遮罩能力装载失败：上游 config 中不存在 "
            + "、".join(missing)
            + "，请检查 ok-script 升级后更新 "
            "extensions/capabilities/screenshot_mask/__init__.py"
        )

    processor.migrate_legacy_config(config.get("config_folder"))

    config["global_configs"].append(processor.build_config_option())
    config["screenshot_processor"] = processor.make_processor()
    config.setdefault("custom_tabs", []).append([TAB_MODULE, TAB_CLASS])
