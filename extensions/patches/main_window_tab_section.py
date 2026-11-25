# -*- coding: utf-8 -*-
"""补上自定义页签的声明式分组能力，让 `section` 属性在发布版也生效。

上游 `MainWindow` 装配 `custom_tabs` 时把分组硬编码成 `SECTION_GAME`：
`before_custom_tabs` 与 `after_custom_tabs` 两处 `add_page` 调用都直接传这个常量，
`CustomTab` 也没有声明分组的属性。页签即使写了 `section = "develop"`，实际仍会落到
「游戏」分组——开发机若手工改过 site-packages 就会看不到这个差异，表现为
「本地正常、安装后跑到游戏下面」。

**为什么只能 patch**（四条实现路径逐条排除）：

1. **继承 + config 注册**：`custom_tabs` 只接受 ``[模块, 类名]`` 二元组，没有分组字段，
   `MainWindow` 也不读取任何「页签分组」配置。没有可注册的扩展点。
2. **子类化 `MainWindow`**：窗口由框架 `ok.OK._create_main_window` 内部构造，
   config 里没有替换窗口类的入口，子类不会被使用。
3. **事件订阅与钩子**：`add_page` 不派发任何信号；页签构造完毕时导航项已经挂到
   「游戏」分组，事后移动要重排 `NavigationView` 的内部控件，比替换一个方法更脆弱。
4. **monkey patch `add_page`**：拦截 `custom_tabs` 唯一的落点，按 `section` 属性改写
   传入的分组——即本模块。

**失效行为**：目标类、方法或分组常量缺失时启动即抛错并指名替换点，不静默降级。
上游若在未来版本原生支持（模块里出现 `_tab_section`），本 patch 自动让位、不包装。
"""

from functools import wraps
from importlib import import_module

# 目标模块与类。上游把窗口放在该模块，`ok.OK` 在 `_create_main_window` 里延迟导入。
_TARGET_MODULE = "ok.ui.qt.MainWindow"
_TARGET_CLASS = "MainWindow"
_TARGET_METHOD = "add_page"

# 允许的导航分组常量。上游改名或增删时，`apply` 必须失败而不是猜。
_SECTION_CONSTANTS = ("SECTION_RUN", "SECTION_GAME", "SECTION_DEVELOP")

# 幂等标记与原始实现引用，都挂在目标类上，便于排查与恢复。
_PATCHED_FLAG = "_tab_section_patch_applied"
_ORIGINAL_ATTR = "_tab_section_patch_original_add_page"


def section_for(widget, section, valid_sections):
    """返回页面最终应挂的分组。

    页面声明的 `section` 优先；未声明或声明了未知值时沿用调用方传入的分组。
    未知值必须回退而不是直接采用——一个拼错的字符串会让导航项落进不存在的
    分组，页面从界面上消失，且没有任何报错。

    Args：
        widget：待挂载的页面对象。
        section：调用方传入的分组。
        valid_sections：允许的分组集合。

    Returns：
        最终分组值。
    """
    declared = getattr(widget, "section", None)
    if declared in valid_sections:
        return declared
    return section


def make_add_page(original, valid_sections):
    """返回替换 `MainWindow.add_page` 的实现。

    只改写 `section` 一个参数，其余参数原样透传：`bottom=True` 的页面（关于页）
    不参与分组，它没有 `section` 属性，自然沿用原值。

    Args：
        original：原 `add_page` 实现。
        valid_sections：允许的分组集合。

    Returns：
        新的 `add_page` 函数。
    """

    @wraps(original)
    def add_page(self, widget, icon, text, section=None, bottom=False):
        """按页面声明改写分组后调用原实现。"""
        return original(self, widget, icon, text, section_for(widget, section, valid_sections), bottom=bottom)

    return add_page


def apply():
    """把分组改写逻辑装到 `MainWindow.add_page` 上。

    Raises：
        RuntimeError：目标模块、类、方法或分组常量缺失，说明框架结构已变。
            必须指名失败——静默跳过会退回「页签全挂游戏分组」，而安装版看不出来。
    """
    module = import_module(_TARGET_MODULE)
    main_window_cls = getattr(module, _TARGET_CLASS, None)
    if main_window_cls is None:
        raise RuntimeError(
            f"页签分组补丁失败：{_TARGET_MODULE} 中不存在 {_TARGET_CLASS}，"
            "请检查 ok-script 升级后更新 extensions/patches/main_window_tab_section.py"
        )
    if getattr(main_window_cls, _PATCHED_FLAG, False):
        return
    if callable(getattr(module, "_tab_section", None)):
        # 上游已原生支持，直接让位并打标记，避免重复包装。
        setattr(main_window_cls, _PATCHED_FLAG, True)
        return

    missing = [name for name in _SECTION_CONSTANTS if not hasattr(module, name)]
    if missing:
        raise RuntimeError(
            "页签分组补丁失败："
            + _TARGET_MODULE
            + " 中不存在分组常量 "
            + "、".join(missing)
            + "，请检查 ok-script 升级后更新 extensions/patches/main_window_tab_section.py"
        )
    valid_sections = frozenset(getattr(module, name) for name in _SECTION_CONSTANTS)

    original = getattr(main_window_cls, _TARGET_METHOD, None)
    if not callable(original):
        raise RuntimeError(
            f"页签分组补丁失败：{_TARGET_CLASS} 上不存在可调用的 {_TARGET_METHOD}，"
            "请检查 ok-script 升级后更新 extensions/patches/main_window_tab_section.py"
        )

    setattr(main_window_cls, _ORIGINAL_ATTR, original)
    setattr(main_window_cls, _TARGET_METHOD, make_add_page(original, valid_sections))
    setattr(main_window_cls, _PATCHED_FLAG, True)
