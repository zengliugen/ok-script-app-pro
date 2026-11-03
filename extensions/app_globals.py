# -*- coding: utf-8 -*-
"""项目全局单例（装配基础设施）。

在 `src.globals.Globals` 基础上追加启动期修正。上游 `Globals` 没有任何行为，
本项目需要 `on_show_main_window` 回调来在主窗口构造完成、显示之前调整界面，
因此继承它并在 `config['my_app']` 中替换注册项。

本模块**不含业务逻辑**：它只把生命周期事件按清单顺序派发给各能力包。
新增能力只要在自己的 `__init__.py` 定义 `on_show_main_window(main_window)` 或
`on_app_ready(app)` 即自动生效，不需要回来改本文件——这也是「能力之间不互相 import」
的落地方式。

注意：`on_show_main_window` 在 `MainWindow.show()` **之前**被调用，此时
`showEvent`（含更新检查定时器）尚未执行，因此在那里摘掉卡片能阻止它被排队。
"""

from ok import og

from extensions.capabilities.registry import dispatch_hook
from src.globals import Globals as UpstreamGlobals


class AppGlobals(UpstreamGlobals):
    """项目全局单例，继承上游实现并追加启动期修正。"""

    def __init__(self, exit_event):
        """构造时派发 `on_app_ready`。

        时机由框架保证：`og.app` 已赋值、`my_app`（本对象）正在构造。翻译对象
        必须在 `my_app` 构造完成前预置，否则任何一次 `tr()` 都会先把上游域缓存下来。

        Args：
            exit_event：框架的退出事件。
        """
        super().__init__(exit_event)
        dispatch_hook("on_app_ready", og.app)

    def on_show_main_window(self, main_window):
        """主窗口构造完成、尚未显示时调用。

        Args：
            main_window：上游 `MainWindow` 实例。
        """
        dispatch_hook("on_show_main_window", main_window)
