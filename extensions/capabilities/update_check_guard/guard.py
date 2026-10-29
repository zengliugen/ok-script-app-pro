# -*- coding: utf-8 -*-
"""更新检查的可用性守卫。

上游 `MainWindow` 在首次显示后 30 秒自动调用 `pyappify.get_version_list()`。该调用
要求能定位 PyAppify 启动器（`<data>/apps/<应用名>/working/` 结构下的 Launcher.exe，
或环境变量 `PYAPPIFY_EXECUTABLE`）。从源码直接运行 `main_debug.py` 时这两者都不存在，
于是每次都抛 `FileNotFoundError`，日志里留下约 30 行栈，About 页的更新卡片长期显示红色
错误文案。

本模块在启动器确实不存在时把更新卡片从 About 页摘掉：没有启动器时这张卡片无论如何
都用不了，留着只会持续报错。判据直接用 `pyappify.find_pyappify_executable()`——
与报错来源同一个函数，因此不会出现「判断说能用、实际用不了」的偏差。

注意这里不是掩盖失败：启动器存在但检查失败（网络、鉴权等）仍由上游照常报错并显示。
"""

from collections.abc import Callable

from ok.util.logger import Logger

logger = Logger.get_logger(__name__)


def apply(main_window: object, launcher_probe: Callable[[], str | None] | None = None) -> bool:
    """启动器不可用时摘掉更新卡片。

    Args：
        main_window：上游 `MainWindow` 实例，需带 `about_tab` 属性。
        launcher_probe：探测启动器路径的可调用对象，默认用 pyappify 的探测函数；
            测试时注入以便跳过真实文件系统。

    Returns：
        True 表示已摘除卡片，False 表示未处理（启动器可用或上游未创建卡片）。

    Raises：
        RuntimeError：About 页或卡片容器结构与预期不符，说明上游已重构。
    """
    try:
        about_tab = main_window.about_tab
    except AttributeError as exc:
        raise RuntimeError(
            "更新检查守卫的目标缺失：MainWindow 上不存在 about_tab 属性，"
            "请检查 ok-script 升级后更新 extensions/capabilities/update_check_guard/guard.py"
        ) from exc

    # 上游在没有 get_version_list 时本就不建卡片，属正常分支，保持静默。
    update_card = getattr(about_tab, "update_card", None)
    if update_card is None:
        return False

    probe = launcher_probe or _probe_launcher
    if probe():
        return False

    container = _find_card_container(update_card)
    about_tab.removeWidget(container)
    container.setParent(None)
    container.deleteLater()
    # 置空后 MainWindow._schedule_update_check 直接返回，不再排定时器。
    about_tab.update_card = None

    logger.warning(
        "未找到 PyAppify 启动器，已隐藏「App update」卡片：源码方式运行时无法检查更新，"
        "需要更新请使用 PyAppify 安装的发行版"
    )
    return True


def _probe_launcher() -> str | None:
    """探测 PyAppify 启动器是否可定位。

    Returns：
        启动器绝对路径；未找到时为 None。

    Raises：
        RuntimeError：pyappify 的探测函数不存在，说明依赖已重构。
    """
    try:
        from pyappify import find_pyappify_executable
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "更新检查守卫的目标缺失：pyappify.find_pyappify_executable 不存在，"
            "请检查依赖升级后更新 extensions/capabilities/update_check_guard/guard.py"
        ) from exc
    return find_pyappify_executable()


def _find_card_container(update_card: object) -> object:
    """定位包裹更新卡片的 Card 容器。

    `Card` 会把传入的 widget 挂到内部的 `QFrame` 上，因此 `UpdateCard` 的祖父
    就是 Card 本体。

    Args：
        update_card：`UpdateCard` 实例。

    Returns：
        包裹它的 `Card` 实例。

    Raises：
        RuntimeError：容器层级与预期不符，说明上游改了 Card 的装配方式。
    """
    inner = update_card.parent()
    container = inner.parent() if inner is not None else None
    if container is None or not hasattr(container, "titleLabel"):
        raise RuntimeError(
            "更新检查守卫的目标缺失：期望 UpdateCard.parent().parent() 是带标题的 Card 容器，"
            "请检查 ok-script 升级后更新 extensions/capabilities/update_check_guard/guard.py"
        )
    return container
