# -*- coding: utf-8 -*-
"""项目全局单例。

在 `src.globals.Globals` 基础上追加启动期修正。上游 `Globals` 没有任何行为，
本项目需要 `on_show_main_window` 回调来在主窗口构造完成、显示之前调整界面，
因此继承它并在 `config['my_app']` 中替换注册项。

注意：`on_show_main_window` 在 `MainWindow.show()` **之前**被调用，此时
`showEvent`（含更新检查定时器）尚未执行，因此在这里摘掉卡片能阻止它被排队。
"""

from extensions.core import update_check_guard
from src.globals import Globals as UpstreamGlobals


class AppGlobals(UpstreamGlobals):
    """项目全局单例，继承上游实现并追加启动期修正。"""

    def on_show_main_window(self, main_window):
        """主窗口构造完成、尚未显示时调用。

        Args：
            main_window：上游 `MainWindow` 实例。
        """
        update_check_guard.apply(main_window)
