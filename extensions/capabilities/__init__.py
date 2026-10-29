# -*- coding: utf-8 -*-
"""扩展能力包根。

**一个能力一个子包**，子包名是能力名的 snake_case 形式（能力名 `log-noise-filter`
对应包 `log_noise_filter`）。能力名同时是 `extensions_docs/README.md` 总览表的「能力」列
与 `extensions_tests/Test<能力名>.py` 的命名来源，三处必须一致。

每个能力子包**必须**满足下面两条，缺一即被体检判为错误：

1. `__init__.py` 暴露 `register(config) -> None`，由 `bootstrap` 统一调用；
2. 在 `bootstrap.CAPABILITIES` 清单里登记，顺序即装载顺序。

能力包内**自包含**：实现模块、任务类、页面类、该能力私用的常量都放同一包内，
不再按 `tasks/` / `ui/` / `core/` 这类技术层次拆分目录——层次目录无法回答
「这个文件属于哪个能力」，能力迁移或删除时必然漏文件。

能力之间**不得互相 import**。需要共享的代码上升为装配基础设施（`bootstrap.py`、
`overrides.py`、`app_globals.py`）或独立工具模块，不留在某个能力包里被别人引用。
"""
