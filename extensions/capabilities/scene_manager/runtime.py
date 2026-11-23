# -*- coding: utf-8 -*-
"""把框架的真实能力接到纯逻辑层。

纯逻辑层（`model` / `normalize` / `recognize` / `graph` / `navigate` / `explore`）
不知道 `ok` 的存在，全部交互经注入。本模块是唯一的接线处：它把任务实例上的
取帧、识别、点击、等待包成纯逻辑层要的函数，并把场景库、识别器、导航器组装好。

**为什么单独一层**：接线代码依赖框架 API，而框架 API 会随版本变化。把它集中在
一个文件里，升级时只需改这里；若散落在各页签与任务里，每处都要各自跟进。

所有对框架符号的引用都做**存在性断言**：符号消失时立即抛出并指明缺什么，
而不是让功能静默失效——静默失效的表现是「点了没反应」，比启动报错难查得多。
"""

import logging
import os
import time

from extensions.capabilities.scene_manager import explore, graph, model, navigate, normalize, recognize, routes, store

# 本模块不建自己的 logger 体系：只在前台化失败等「不影响主流程」的地方记一条警告，
# 用标准库 logger 即可，不必依赖 ok 的日志装配（纯逻辑测试环境没有它）。
logger = logging.getLogger(__name__)

# 场景库默认位置。相对项目根，与 assets 下其他资产同规。
# 产物区位置：入库、运行期只读、随包分发。
DEFAULT_LIBRARY_ROOT = os.path.join("assets", "scenes")

# 编辑区位置：不入库、只在本机，保留原始帧便于随时重新导出。
EDIT_ROOT = os.path.join("ok_scenes")

# 置前后等待生效的上限与轮询步长（秒）。
# `SetForegroundWindow` 是异步的：实测调用返回后立即读前台窗口仍是旧值，
# 真正生效耗时 6—12ms。判定「切不过去」之前必须等过这个窗口，否则会把成功
# 误判成失败——表现为点击明明生效了，界面却弹出「无法切到前台」。
FOREGROUND_WAIT = 0.3
FOREGROUND_POLL = 0.005

# 判断「点击前后画面是否变化」的阈值（见 `frames_differ`）。
# `FRAME_CHANGE_RATIO` 是差异像素占比上限：低于它视为没变化。
# 取值偏小是因为靶场这类界面上，真正生效的点击往往只改几个小区域（按钮高亮、
# 画面切换），若阈值过大，正常生效也会被误判成「没反应」。
# `FRAME_CHANGE_PIXEL` 是单通道差值阈值，用来忽略压缩噪声与抗锯齿抖动。
FRAME_CHANGE_RATIO = 0.0005
FRAME_CHANGE_PIXEL = 12


def library_root(base_dir=None):
    """返回产物区根目录的绝对路径。

    Args：
        base_dir：项目根；省略时用当前工作目录（框架以 cwd 为项目根）。

    Returns：
        绝对路径。
    """
    root = base_dir or os.getcwd()
    return os.path.abspath(os.path.join(root, DEFAULT_LIBRARY_ROOT))


def edit_root(base_dir=None):
    """返回编辑区根目录的绝对路径。

    Args：
        base_dir：项目根。

    Returns：
        绝对路径。
    """
    root = base_dir or os.getcwd()
    return os.path.abspath(os.path.join(root, EDIT_ROOT))


def open_library(base_dir=None):
    """打开（并按需初始化）**产物区**场景库。运行期识别读的是这个。

    Args：
        base_dir：项目根。

    Returns：
        `store.SceneLibrary` 实例。
    """
    library = store.SceneLibrary(library_root(base_dir))
    library.ensure_layout()
    library.load_meta()
    return library


def open_edit_library(base_dir=None):
    """打开（并按需初始化）**编辑区**场景库。界面读写的是这个。

    分成两个入口而不是一个带 flag 的函数：调用点必须显式表明自己要读哪一区，
    否则「界面改了却没生效」这类问题只能靠翻实现才能判断——因为它改的是产物区
    还是编辑区，从调用点看不出来。

    Args：
        base_dir：项目根。

    Returns：
        `store.SceneLibrary` 实例。
    """
    library = store.SceneLibrary(edit_root(base_dir))
    library.ensure_layout()
    library.load_meta()
    return library


def read_mask_rect():
    """读取当前遮罩配置在基准分辨率下的像素矩形。

    用于检测「特征框与遮罩相交」。遮罩关掉或配置不可用时返回 None。

    **不导入遮罩能力模块**：能力之间不得互相 import，否则被依赖的能力就无法单独
    删除或迁移，而这条依赖在目录结构上完全看不见。这里只读全局配置里的键值，
    自己算矩形——遮罩的几何口径（锚点 + 百分比）是**全局配置契约**，不是那个
    能力包的私有实现。

    Returns：
        `(x1, y1, x2, y2)` 像素元组；遮罩不可用或未启用时返回 None。
    """
    library = open_library()
    meta = library.meta
    if not meta.has_resolution():
        return None
    return mask_rect_for(meta.base_width, meta.base_height)


def mask_rect_for(width, height):
    """按指定分辨率计算遮罩矩形（用于尚未确定基准分辨率的场合）。

    Args：
        width：帧宽。
        height：帧高。

    Returns：
        像素矩形；遮罩不可用或未启用时返回 None。
    """
    values = read_mask_values()
    if values is None:
        return None
    return resolve_mask_rect(values, width, height)


# 遮罩配置的键名。这些是**全局配置契约**（由 `config['global_configs']` 里的
# `Screenshot Mask` 项定义），不是某个能力包的私有常量。写在本地是为了避免
# 跨能力 import；若上游改了键名，这里与遮罩能力会同时失效，而交集检测本身只是
# 提示性功能，失效时返回 None 不影响识别与导航。
MASK_OPTION_NAME = "Screenshot Mask"
MASK_KEY_ENABLED = "Enable Mask"
MASK_KEY_ANCHOR = "Mask Anchor"
MASK_KEY_WIDTH = "Mask Width"
MASK_KEY_HEIGHT = "Mask Height"
MASK_KEY_OFFSET_X = "Mask Offset X"
MASK_KEY_OFFSET_Y = "Mask Offset Y"

# 锚点 → (水平贴靠, 垂直贴靠)。取值与遮罩能力的几何层一致。
_MASK_ANCHOR_SIDES = {
    "top_left": ("left", "top"),
    "top": ("center", "top"),
    "top_right": ("right", "top"),
    "right": ("right", "center"),
    "bottom_right": ("right", "bottom"),
    "bottom": ("center", "bottom"),
    "bottom_left": ("left", "bottom"),
    "left": ("left", "center"),
}


def apply_global_mask(frame):
    """对帧应用全局截图遮罩，返回**新帧**。

    **遮罩是输出脱敏**：只在「要落盘、要入库、要显示」的地方调用，不进入识别链路。
    框架自己也只在把图变成文件时施加 `screenshot_processor`
    （`ok/core/screenshot.py` 的 `to_pil_image`），本能力沿用同一口径。

    因此实现为复制后遮罩（`frame.copy()` 再涂）而不是原地改：调用方通常还留着原始
    帧做识别，原地改会把遮罩区涂黑，让该区域内的特征永远匹配不到。

    调用点只有两处：保存 `base.png`、以及界面上展示的预览图。

    Args：
        frame：原始帧（BGR 或灰度）。

    Returns：
        遮罩后的新帧；遮罩关闭、配置不可用时返回原帧的副本（保持「调用方可以随便改
        返回值」的语义，不必每次判断是否是同一个对象）。
    """
    if frame is None:
        return None
    masked = frame.copy()
    values = read_mask_values()
    if values is None:
        return masked
    height, width = masked.shape[:2]
    rect = resolve_mask_rect(values, width, height)
    if rect is None:
        return masked
    x1, y1, x2, y2 = rect
    masked[y1:y2, x1:x2] = 0
    return masked


def capture_frame():
    """取一帧**原始画面**（不施遮罩）。

    这是场景管理唯一的取帧入口，但返回的是设备原始像素——与框架的设计一致：
    `screenshot_processor` 只在把图变成文件/显示时生效
    （`ok/core/screenshot.py` 的 `to_pil_image`），运行期识别一律跑在原始帧上。

    **遮罩是输出脱敏，不是输入脱敏**。因此本能力只在「要落盘的地方」显式调
    :func:`apply_global_mask`：写 `base.png` 与保存预览图时。识别、探索、点击
    坐标换算都不需要遮罩，涂黑只会让遮罩区内的特征无法匹配。

    **本函数存在的作用是收口设备调用**：所有取帧都经它，将来若要切换取帧口径
    （例如改用 `task.next_frame`）只改一处；它也是唯一允许 import `og.device_manager`
    的地方。

    Returns：
        BGR 原始帧；取不到时返回 None。

    Raises：
        RuntimeError：取帧接口不可用（设备管理器或 capture_method 缺失）。
    """
    from ok import og

    device_manager = getattr(og, "device_manager", None)
    if device_manager is None:
        raise RuntimeError("场景管理取帧失败：og.device_manager 不存在，请检查框架装配")
    capture = getattr(device_manager, "capture_method", None)
    if capture is None or not callable(getattr(capture, "get_frame", None)):
        raise RuntimeError("场景管理取帧失败：capture_method.get_frame 不可用，请检查 ok-script 升级后更新 runtime.py")
    from ok.device.capture_methods.base import CAPTURE_BUSY_WAIT

    return capture.get_frame(busy_wait=CAPTURE_BUSY_WAIT)


# 探索前自动启动与未完成重启只支持 Windows 目标。ADB 模拟器杀进程会关闭
# 多开实例并改变串口，恢复链路与 Windows 完全不同；真机也无法由主应用重启。
RESTART_SUPPORTED_DEVICE = "windows"


def target_status():
    """返回当前目标的启动/重启能力。

    Returns：
        `{device, path, connected, supported, reason}` 字典。`supported` 为 False
        时 `reason` 说明为什么（未选中设备、非 Windows、没有 exe 路径等）。
    """
    from ok import og

    manager = getattr(og, "device_manager", None)
    if manager is None:
        return {"device": "", "path": "", "connected": False, "supported": False, "reason": "设备管理器不可用"}
    try:
        device = manager.get_preferred_device()
    except Exception as error:  # noqa: BLE001 — 取设备失败只影响本功能，不阻断探索
        return {
            "device": "",
            "path": "",
            "connected": False,
            "supported": False,
            "reason": f"读取目标设备失败：{error}",
        }
    if not device:
        return {"device": "", "path": "", "connected": False, "supported": False, "reason": "没有选中的目标设备"}
    kind = device.get("device", "")
    try:
        path = manager.get_exe_path(device) or ""
    except Exception:  # noqa: BLE001 — 路径计算失败按「没有路径」处理
        path = ""
    supported = kind == RESTART_SUPPORTED_DEVICE and bool(path) and "://" not in path
    reason = ""
    if kind != RESTART_SUPPORTED_DEVICE:
        reason = "仅支持 Windows 目标"
    elif not path:
        reason = "没有配置目标 exe 路径"
    elif "://" in path:
        reason = "目标是 URL 协议，无法重启"
    return {
        "device": kind,
        "path": path,
        "connected": bool(device.get("connected")),
        "supported": supported,
        "reason": reason,
    }


def launch_target():
    """确保目标程序已启动（不杀进程）。

    Returns：
        `(ok, reason)` 二元组；目标已连接时直接返回 True。
    """
    status = target_status()
    if not status["supported"]:
        return False, status["reason"]
    if status["connected"]:
        return True, "目标已连接"
    from ok import og

    controller = getattr(getattr(og, "app", None), "start_controller", None)
    if controller is None or not callable(getattr(controller, "start_device", None)):
        return False, "框架未提供启动控制器"
    try:
        if controller.start_device():
            return True, ""
        return False, "目标启动失败"
    except Exception as error:  # noqa: BLE001 — 启动失败转成可读原因
        return False, f"目标启动异常：{error}"


def restart_target(wait_seconds=8.0, should_stop=None, target_path=None):
    """重启 Windows 目标并等待恢复取帧。

    Args：
        wait_seconds：启动后等待恢复取帧的上限（秒）。
        should_stop：可选回调，返回 True 时中止等待与重启。
        target_path：**任务开始时锁定的目标 exe 路径**。省略时才现场解析。

    Returns：
        `(ok, reason)` 二元组。

    Note：
        先 `stop_hwnd()` 杀已绑定窗口，再用 `kill_exe` 兜底——探索一开始就取不到
        画面时窗口可能还没绑定，只靠 `stop_hwnd()` 杀不掉。`kill_exe` 会跳过当前
        进程与父进程，不会误杀主应用。

        **目标必须按路径锁定，不能每次重新读「当前选中设备」**。目标被 kill 后，
        框架的 `get_preferred_device()` 会因为原设备已从窗口列表消失而回落到
        「第一个 connected 窗口」——实测回落到了微信（144x20），于是第二次重启
        杀的是微信、绑的也是微信，目标反而没起来。因此由调用方在任务开始时取一次
        `target_status()` 并把 `path` 传进来，后续所有重启都只认这个路径。

        启动后也不能直接 `do_refresh()`：它内部会 `do_start()` 并再次走回落逻辑。
        这里改为刷新窗口列表、按 exe 找到**新窗口**后显式 `set_preferred_device`，
        再等取帧。
    """
    if should_stop is not None and should_stop():
        return False, "已取消"
    if target_path is None:
        status = target_status()
        if not status["supported"]:
            return False, status["reason"]
        path = status["path"]
    else:
        path = target_path
        if not os.path.exists(path):
            return False, f"目标 exe 不存在：{path}"
    from ok import og
    from ok.util.process import execute, kill_exe

    manager = getattr(og, "device_manager", None)
    if manager is None:
        return False, "设备管理器不可用"
    if should_stop is not None and should_stop():
        return False, "已取消"
    # `stop_hwnd()` 杀的是**当前绑定窗口**，不一定是目标：上一次错误的重启可能
    # 把绑定落到了别的应用（实测微信）。只在确认绑定就是目标时才用它，否则
    # 只走下面的 `kill_exe(path)`，避免误杀无关程序。
    hwnd_window = getattr(manager, "hwnd_window", None)
    bound_exe = str(getattr(hwnd_window, "exe_full_path", "") or "") if hwnd_window is not None else ""
    bound_is_target = bool(bound_exe) and os.path.normcase(os.path.abspath(bound_exe)) == os.path.normcase(
        os.path.abspath(path)
    )
    if bound_is_target:
        try:
            manager.stop_hwnd()
        except Exception as error:  # noqa: BLE001 — 继续走兜底杀进程
            logger.warning(f"场景管理：停止目标窗口失败：{error}")
    kill_exe(abs_path=path)
    if not _wait_process_gone(path, 5.0, should_stop):
        return False, "已取消" if (should_stop is not None and should_stop()) else "目标进程未能在 5s 内退出"
    if not _sleep_cancellable(1.0, should_stop):
        return False, "已取消"
    if not os.path.exists(path):
        return False, f"目标 exe 不存在：{path}"
    if should_stop is not None and should_stop():
        return False, "已取消"
    if not execute(path, arguments=_target_arguments(), start_method=_target_start_method()):
        return False, "目标启动失败"
    bound, reason = _bind_target_window(manager, path, wait_seconds, should_stop)
    if not bound:
        return False, reason
    deadline = time.monotonic() + max(1.0, float(wait_seconds))
    target_path_key = os.path.normcase(os.path.abspath(path))
    while time.monotonic() < deadline:
        if should_stop is not None and should_stop():
            return False, "已取消"
        # 绑定还没生效时不要取帧：旧 capture_method 还指着上一个窗口，
        # 取到的会是别人的画面（实测 144x20 的微信窗口）。
        hwnd_window = getattr(manager, "hwnd_window", None)
        bound_exe = str(getattr(hwnd_window, "exe_full_path", "") or "") if hwnd_window is not None else ""
        if bound_exe and os.path.normcase(os.path.abspath(bound_exe)) != target_path_key:
            if not _sleep_cancellable(0.2, should_stop):
                return False, "已取消"
            continue
        try:
            frame = capture_frame()
        except RuntimeError:
            frame = None
        if frame is not None:
            return True, ""
        if not _sleep_cancellable(0.5, should_stop):
            return False, "已取消"
    return False, f"目标在 {wait_seconds:.0f}s 内未恢复取帧"


def bound_target_path():
    """返回当前**已绑定窗口**的 exe 路径。

    Returns：
        绝对路径；尚未绑定或读不到时返回空串。

    Note：
        这是探索实际在取帧/点击的那个应用，比「当前选中设备」可靠：设备选中项
        可能被框架在目标退出后回落成前台窗口，而绑定窗口不会凭空换应用。
        重启时优先锁定这个路径，避免杀错目标。
    """
    from ok import og

    manager = getattr(og, "device_manager", None)
    hwnd_window = getattr(manager, "hwnd_window", None) if manager is not None else None
    return str(getattr(hwnd_window, "exe_full_path", "") or "")


def _bind_target_window(manager, path, timeout, should_stop=None):
    """把设备绑定回目标 exe 的窗口。

    Args：
        manager：`og.device_manager`。
        path：目标 exe 绝对路径。
        timeout：等待窗口出现的上限（秒）。
        should_stop：可选回调，返回 True 时中止。

    Returns：
        `(ok, reason)` 二元组。

    Note：
        **不能依赖 `get_preferred_device()`**：目标进程被 kill 后原设备条目会从
        窗口列表消失，框架会回落到「第一个 connected 窗口」。这里刷新窗口列表，
        按 `full_path` / exe 名找到属于目标的新窗口，取面积最大的那个（同一 exe
        可能有启动器、日志窗等小窗口），再显式设为当前设备并同步 `do_start()`。
    """
    deadline = time.monotonic() + max(3.0, float(timeout))
    target_path = os.path.normcase(os.path.abspath(path))
    target_name = os.path.basename(path).lower()
    while time.monotonic() < deadline:
        if should_stop is not None and should_stop():
            return False, "已取消"
        try:
            manager.update_pc_device()
        except Exception as error:  # noqa: BLE001 — 刷新失败下一轮再试
            logger.warning(f"场景管理：重启后刷新窗口列表失败：{error}")
        candidates = []
        for device in (getattr(manager, "device_dict", {}) or {}).values():
            if device.get("device") != "windows" or not device.get("real_hwnd"):
                continue
            full_path = str(device.get("full_path") or "")
            full_match = bool(full_path) and os.path.normcase(os.path.abspath(full_path)) == target_path
            exe_match = str(device.get("exe") or "").lower() == target_name
            if full_match or exe_match:
                candidates.append(device)
        if candidates:
            # 同一 exe 可能同时有启动器/日志窗，游戏主窗口面积最大。
            candidate = max(candidates, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0))
            try:
                manager.set_preferred_device(imei=candidate.get("imei"))
                # **必须校验选中项真的切到了候选窗口**：`set_preferred_device` 在
                # 候选消失时会静默回落到「第一个 connected 窗口」（实测 TextInputHost
                # / 启动器 / Unity Hub），随后 do_start 就把捕获绑到别的应用上。
                preferred = manager.get_preferred_device()
                if not preferred or preferred.get("imei") != candidate.get("imei"):
                    return False, "目标窗口在刷新中丢失，未完成绑定"
                # set_preferred_device 内部经 handler 异步 do_start；这里同步执行
                # 一次，确保 capture_method 立刻指向新窗口而不是继续用旧窗口。
                manager.do_start()
            except Exception as error:  # noqa: BLE001 — 绑定失败按「没恢复」处理
                return False, f"绑定目标窗口失败：{error}"
            return True, ""
        if not _sleep_cancellable(0.5, should_stop):
            return False, "已取消"
    return False, "重启后未找到目标窗口"


def _target_arguments():
    """读取项目的目标启动参数。

    Returns：
        启动参数字符串；未配置时返回 None。
    """
    from ok import og

    windows = (getattr(og, "config", None) or {}).get("windows") or {}
    arguments = windows.get("launch_arguments")
    if callable(arguments):
        try:
            arguments = arguments()
        except Exception as error:  # noqa: BLE001 — 参数函数异常不影响默认启动
            logger.warning(f"场景管理：launch_arguments 调用失败：{error}")
            return None
    return arguments


def _target_start_method():
    """返回框架配置的目标启动方式。"""
    from ok import og
    from ok.util.process import WINDOWS_START_METHOD_START

    controller = getattr(getattr(og, "app", None), "start_controller", None)
    return getattr(controller, "start_method", WINDOWS_START_METHOD_START)


def _wait_process_gone(path, timeout, should_stop=None):
    """等待指定 exe 的所有进程退出。

    Args：
        path：目标 exe 绝对路径。
        timeout：等待上限（秒）。
        should_stop：可选回调。

    Returns：
        进程已退出时 True；超时或被取消时 False。
    """
    import psutil

    target = os.path.normcase(os.path.abspath(path))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if should_stop is not None and should_stop():
            return False
        running = False
        for proc in psutil.process_iter(["exe"]):
            try:
                exe = proc.info.get("exe")
                if exe and os.path.normcase(os.path.abspath(exe)) == target:
                    running = True
                    break
            except Exception:  # noqa: BLE001 — 进程在遍历中退出，忽略
                continue
        if not running:
            return True
        time.sleep(0.2)
    return False


def _sleep_cancellable(seconds, should_stop=None):
    """可被停止回调打断的等待。

    Args：
        seconds：等待秒数。
        should_stop：可选回调。

    Returns：
        正常等完返回 True；被取消返回 False。
    """
    deadline = time.monotonic() + max(0.0, float(seconds))
    while True:
        if should_stop is not None and should_stop():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.2, remaining))


def _frame_size(frame):
    """返回帧的 `(宽, 高)`；帧为空或形状异常时返回 None。

    Args：
        frame：numpy 数组或 None。

    Returns：
        `(width, height)` 整数二元组；无法判定时 None。

    Note：
        只给界面做诊断用，不参与任何识别判定——尺寸差异会被归一化缩放吸收，
        判定结果与它无关。但排查「所有特征都不命中」时，第一个要看的恰恰是
        「截到的画面到底多大」，因为截错窗口的典型表现就是尺寸与库基准对不上。
    """
    if frame is None:
        return None
    shape = getattr(frame, "shape", None)
    if not shape or len(shape) < 2:
        return None
    height, width = shape[0], shape[1]
    if width <= 0 or height <= 0:
        return None
    return int(width), int(height)


def log_explore_event(task, event):
    """把一条探索事件写进任务日志。

    Args：
        task：任务实例（提供 `log_info`）。
        event：`explore.ExploreEvent`。

    Note：
        **失败只记 debug，不抛**：日志是诊断手段，不能因为任务对象上没有
        `log_info`（测试桩、框架改版）就打断正在跑的探索。而事件的 kind 决定
        级别——`error` / `warn` 用告警级别，其余用 info，免得真正的失败被淹没在
        一堆正常流水里。

        行首不再拼 `scene_id`：`detail` 由探索器生成，已统一写成
        `名称（scene-0001）`；再拼 id 会让同一行出现两次。
    """
    text = f"探索事件 [{event.kind}] {event.detail}".strip()
    try:
        if event.kind in ("error", "warn", "skip", "limit"):
            log = getattr(task, "log_warning", None)
            if callable(log):
                log(text)
                return
        log = getattr(task, "log_info", None)
        if callable(log):
            log(text)
    except Exception:  # noqa: BLE001 — 记日志失败不能打断探索
        pass


def frames_differ(before, after, tolerance=FRAME_CHANGE_RATIO):
    """判断两帧画面是否有可见差异。

    用于识别「点击了但目标毫无反应」。有些目标程序不处理某些注入方式——
    典型是工具包窗口不响应投递的 `WM_LBUTTONDOWN`——此时点击不抛异常、画面
    也不变，界面却会照常显示成功。没有这项比对，用户只能看到「点了没反应」
    而拿不到任何线索。

    Args：
        before：点击前的帧（BGR 数组），可以为 None。
        after：点击后的帧（BGR 数组），可以为 None。
        tolerance：判定「没变化」的像素差异比例上限。

    Returns：
        True 表示画面有变化（视为点击生效），False 表示没变化。

    Note：
        **取不到任意一帧时返回 True**（视为有变化）。理由：没有证据时不应
        断言「点击无效」——那会把「取帧偶发失败」误报成「点击方式不适配」，
        而后者会引导用户去改配置。宁可漏报，也不要给错方向。

        差异阈值按**像素比例**而非绝对像素数：高分辨率下同一次画面变化的绝对
        像素数大得多，用绝对数会让阈值随分辨率漂移。
    """
    if before is None or after is None:
        return True
    if getattr(before, "shape", None) != getattr(after, "shape", None):
        # 尺寸都变了，说明画面确实变了（例如窗口被调整）。
        return True
    try:
        import numpy

        diff = numpy.abs(before.astype(numpy.int16) - after.astype(numpy.int16))
        changed = int((diff.max(axis=2) > FRAME_CHANGE_PIXEL).sum())
        total = int(before.shape[0] * before.shape[1])
        if total <= 0:
            return True
        return changed / total > tolerance
    except Exception:  # noqa: BLE001 — 比对失败不应影响主流程，按「有变化」处理
        return True


def read_mask_values():
    """读取遮罩的全局配置。

    Returns：
        配置字典；配置项未注册或遮罩未启用时返回 None。
    """
    from ok import og

    global_config = getattr(og, "global_config", None)
    if global_config is None:
        return None
    try:
        values = global_config.get_config(MASK_OPTION_NAME)
    except RuntimeError:
        return None
    if not values.get(MASK_KEY_ENABLED, False):
        return None
    return values


def resolve_mask_rect(values, width, height):
    """按遮罩配置算出像素矩形。

    与遮罩能力 `geometry.resolve_rect` 的口径一致：锚点决定贴哪条边，宽高与偏移
    按帧尺寸换算成像素（**截断**而非四舍五入，与上游 `int(0.13 * width)` 同源），
    结果按帧边界裁剪。

    Args：
        values：遮罩配置字典。
        width：帧宽。
        height：帧高。

    Returns：
        `(x1, y1, x2, y2)` 整数元组；参数非法或遮罩完全在画面外时返回 None。
    """
    if width <= 0 or height <= 0:
        return None
    anchor = values.get(MASK_KEY_ANCHOR, "bottom_right")
    sides = _MASK_ANCHOR_SIDES.get(anchor)
    if sides is None:
        return None

    mask_width = int(values.get(MASK_KEY_WIDTH, 0.0) * width / 100.0)
    mask_height = int(values.get(MASK_KEY_HEIGHT, 0.0) * height / 100.0)
    offset_x = int(values.get(MASK_KEY_OFFSET_X, 0.0) * width / 100.0)
    offset_y = int(values.get(MASK_KEY_OFFSET_Y, 0.0) * height / 100.0)

    x1, x2 = _place_mask_axis(mask_width, width, sides[0], offset_x)
    y1, y2 = _place_mask_axis(mask_height, height, sides[1], offset_y)
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _place_mask_axis(size, total, side, offset):
    """在一条轴上按贴靠方向算出区间。

    Args：
        size：遮罩在该轴上的长度。
        total：该轴总长。
        side：`left` / `center` / `right`（垂直轴同理）。
        offset：向画面内部的偏移。

    Returns：
        `(start, end)` 二元组。
    """
    if side in ("right", "bottom"):
        return total - size - offset, total - offset
    if side == "center":
        start = (total - size) // 2 + offset
        return start, start + size
    return offset, offset + size


class TemplateLoader:
    """按 (场景 id, 特征名) 从场景库读模板与掩码，带内存缓存。

    缓存是必要的：一次识别会用到几十个模板，每次都读盘会让判断慢一个量级。
    缓存按两个文件的修改时间失效——配置页可能刚改过模板或掩码，不失效会一直用旧图。

    返回 `(模板, 掩码)` 二元组而不是只返回模板：掩码与模板必须成对使用，分开取
    会让调用点有机会「只拿模板忘了掩码」，而漏用掩码不报错、只是精度下降。
    """

    def __init__(self, library):
        """绑定场景库。

        Args：
            library：`store.SceneLibrary` 实例。
        """
        self.library = library
        self._cache = {}
        self._mtime = {}

    def __call__(self, scene_id, feature_name):
        """返回模板与掩码。

        Args：
            scene_id：场景 id。
            feature_name：特征名。

        Returns：
            `(模板, 掩码)` 二元组。模板缺失时返回 `(None, None)`；掩码缺失时
            返回 `(模板, None)`——掩码是精度优化项，缺了只降低精度，不该让整个
            特征失效。
        """
        key = (scene_id, feature_name)
        template_path = self.library.template_path(scene_id, feature_name)
        mask_path = self.library.mask_path(scene_id, feature_name)
        try:
            stamp = (os.path.getmtime(template_path), os.path.getmtime(mask_path))
        except OSError:
            # 掩码可能不存在，单独再试一次模板。
            try:
                stamp = (os.path.getmtime(template_path), 0.0)
            except OSError:
                self._cache.pop(key, None)
                self._mtime.pop(key, None)
                return None, None
        if self._mtime.get(key) == stamp and key in self._cache:
            return self._cache[key]
        template = self.library.load_template(scene_id, feature_name)
        mask = self.library.load_mask(scene_id, feature_name)
        self._cache[key] = (template, mask)
        self._mtime[key] = stamp
        return template, mask

    def invalidate(self, scene_id=None):
        """清空缓存。

        Args：
            scene_id：只清该场景的缓存；省略时清全部。
        """
        if scene_id is None:
            self._cache.clear()
            self._mtime.clear()
            return
        for key in list(self._cache):
            if key[0] == scene_id:
                self._cache.pop(key, None)
                self._mtime.pop(key, None)


class OcrAdapter:
    r"""把任务的 OCR 包装成识别层要的 `ocr(frame, rect) -> list[str]`。

    识别层拿到的是**裁剪后的小图**，而 `task.ocr` 按整帧坐标工作。因此这里把
    「在小图上做 OCR」翻译成一次带区域参数的调用——直接对裁剪图调用 `task.ocr`
    会因为它内部按整帧坐标换算而错位。

    **裁剪范围由识别层决定，本类不再改动图像**。早先这里做过「小图放大 2 倍」，
    那是基于「检测器在低矮图上失效」的判断，但那个判断只解释了表象：真正的成因
    是文字离裁剪边界太近（DB 检测器 unclip 后超出边界被裁），以及输入高度被吸附
    到 32 的倍数。两者都在 `match_text` 的外扩边距里一并解决，放大只会额外付出
    插值成本、并让 OCR 处理面积翻 4 倍。
    """

    def __init__(self, task):
        """绑定任务。

        Args：
            task：`BaseTask` 实例（提供 `ocr` 方法）。
        """
        ocr_method = getattr(task, "ocr", None)
        if ocr_method is None or not callable(ocr_method):
            raise RuntimeError(
                "场景管理装配失败：任务上不存在可调用的 ocr，"
                "请检查 ok-script 升级后更新 extensions/capabilities/scene_manager/runtime.py"
            )
        self.task = task

    def __call__(self, frame, rect=None):
        """在小图上做 OCR。

        Args：
            frame：已裁剪的小图（numpy 数组）。外扩边距由 `recognize.match_text`
                在裁剪时就加上，本方法原样送检。
            rect：该小图在整帧中的像素矩形；OCR 适配不使用它，因为这里直接对
                小图调用。

        Returns：
            识别出的文本列表；失败时为空列表。
        """
        del rect
        if frame is None or getattr(frame, "size", 0) == 0:
            return []
        try:
            results = self.task.ocr(frame=frame)
        except TypeError:
            # 上游若改了参数名，退回位置参数调用（frame 是第一个位置参数）。
            results = self.task.ocr(frame)
        except Exception:  # noqa: BLE001 — OCR 失败不应让识别整体失败
            return []
        texts = []
        for item in results or []:
            name = getattr(item, "name", None)
            if name:
                texts.append(str(name))
            elif isinstance(item, str):
                texts.append(item)
        return texts


class TaskBridge:
    """把一个任务实例包装成纯逻辑层要的全部依赖。

    Attributes：
        task: 任务实例。
        library: 识别与导航读的库。**界面注入编辑区**（`ok_scenes/`），
            运行期任务（`SceneGotoTask`）用默认的产物区（`assets/scenes/`）。
        draft_library: 探索产物落盘的库（**编辑区**，`ok_scenes/`）。
        loader: `TemplateLoader` 实例（绑定 `library`）。
    """

    def __init__(self, task, library=None, draft_library=None):
        """绑定任务与场景库。

        Args：
            task：`BaseTask` 实例。
            library：识别与导航用的库；省略时打开产物区。
            draft_library：探索草稿落盘用的库；省略时打开编辑区。

        Raises：
            RuntimeError：任务缺少取帧或点击能力，说明框架 API 已变。

        Note：
            **界面与运行期的库不同，这不是冗余**：
            - 界面（配置、路由、探索、调试）读写**编辑区**（`ok_scenes/`）——
              刚连的边、刚建的场景必须立即可见；用产物区会出现「刚连的边在
              调试页提示没有可用路径」。
            - 运行期任务（`SceneGotoTask`）读**产物区**（`assets/scenes/`，
              随包分发、已脱敏）。
            - 探索草稿写**编辑区**——草稿页读编辑区、`promote_draft` 也在同一个
              库内把草稿搬正。写错区的结果是「探索跑完了，草稿页永远是空的」，
              而两个库都在磁盘上、都在被正常读写，**没有任何报错**。
        """
        self.task = task
        self.library = library or open_library()
        self.draft_library = draft_library or open_edit_library()
        self.loader = TemplateLoader(self.library)
        # 最近一次建图失败的原因。探索在无图时仍可运行（它的目的就是补边），
        # 但界面要能把「为什么探索只探了起点」讲清楚。
        self.last_graph_error = ""

        for name in ("click", "ocr"):
            if not callable(getattr(task, name, None)):
                raise RuntimeError(
                    f"场景管理装配失败：任务上不存在可调用的 {name}，"
                    "请检查 ok-script 升级后更新 extensions/capabilities/scene_manager/runtime.py"
                )
        self._ocr = OcrAdapter(task)

    # --- 线程 -------------------------------------------------------------

    def run_on_executor(self, fn, timeout=30.0):
        """在**执行器线程**上运行一段会碰设备或任务的代码，并等它结束。

        界面层调用 `task.ocr` / `task.click` / `task.sleep` 必须走这里，直接在
        GUI 线程调用会**永久卡死**：

        `TaskExecutor` 启动时 `paused = True`（应用未开始运行时一直是这个状态），
        而 `task.ocr` 内部第一件事就是 `if self.blocked_by_pause(): self.executor.sleep(1)`
        ——`sleep` 在暂停态下进入 `while True`，每轮只有 `blocked_by_pause()` 变假才
        退出。而让暂停解除的唯一途径是执行器线程收到 `start()`。GUI 线程被这个
        循环占住时，界面连重绘都做不了，任务队列也不可能推进，于是**没有任何恢复
        路径**，只能杀进程。

        上游自己的诊断页也是这么做的（`ok/ui/qt/debug/diagnosis_page.py` 的
        `run_ocr` 用 `submit_call(og.executor, ...)`），注释写得很直接：
        「A job on the executor gets frames even while the executor is paused」。
        job 的 `ignore_pause` 默认为 True，所以暂停也会被执行。

        Args：
            fn：无参可调用对象，会在执行器线程上运行。**不得**碰 Qt 控件——跨线程
                改界面会让程序崩溃，结果要通过返回值带回本线程处理。
            timeout：等待上限（秒）。超时后取消 job 并抛 `RuntimeError`。

        Returns：
            `fn` 的返回值。

        Raises：
            RuntimeError：执行器不可用、被独占、任务抛异常，或等待超时。
        """
        executor = getattr(self.task, "executor", None)
        if executor is None or not callable(getattr(executor, "submit", None)):
            raise RuntimeError("执行器不可用：无法在后台线程执行操作")
        try:
            job = executor.submit(fn, name="scene_manager")
        except Exception as error:  # noqa: BLE001 — 被独占等情况统一转成可读原因
            raise RuntimeError(f"执行器忙，操作未执行：{error}") from error

        try:
            return job.future.result(timeout)
        except Exception as error:  # noqa: BLE001 — 超时与任务异常都在这里收口
            job.cancel()
            raise RuntimeError(f"操作未完成：{error}") from error

    # --- 帧 ---------------------------------------------------------------

    def capture_frame(self):
        """取一帧原始画面（未遮罩、未归一化）。

        内部转调模块级 :func:`capture_frame`，把设备调用收敛到一处。

        **返回原始帧是刻意的**：识别与坐标换算跑在原始像素上，与框架一致。
        遮罩只在要落盘时施加，见 :func:`apply_global_mask`。

        Returns：
            BGR 数组；取不到时返回 None。

        Raises：
            RuntimeError：取帧接口不可用。
        """
        return capture_frame()

    # --- 识别 -------------------------------------------------------------

    def build_scenes(self):
        """读取正式区场景。

        Returns：
            `(scenes, failures)` 二元组，语义同 `SceneLibrary.load_library`。
        """
        return self.library.load_library()

    def make_matcher(self):
        """返回识别函数 `(frame) -> MatchResult`。

        Returns：
            可调用对象；库为空或缺少基准分辨率时返回一个恒返回未命中的函数。
        """
        scenes, _failures = self.build_scenes()
        meta = self.library.meta
        if not scenes or not meta.has_resolution():
            return lambda _frame: recognize.MatchResult(status=recognize.MATCH_NONE)
        options = recognize.MatchOptions(ocr=self._ocr)

        def matcher(frame):
            return recognize.recognize(
                frame,
                scenes,
                meta.base_width,
                meta.base_height,
                self.loader,
                options=options,
            )

        return matcher

    def identify_once(self):
        """识别当前画面一次。

        Returns：
            `recognize.MatchResult`；库不可用时为未命中。

        Raises：
            RuntimeError：取帧失败。
        """
        frame = self.capture_frame()
        matcher = self.make_matcher()
        result = matcher(frame)
        # 与 `diagnose_blocking` 一致：带上**归一化之前**的原始尺寸，界面据此判断
        # 截图目标是否选错。库为空时 matcher 是兜底函数，这条仍然成立。
        result.frame_size = _frame_size(frame)
        return result

    # --- 界面层入口（必须走执行器线程）-------------------------------------

    def identify_blocking(self, timeout=30.0):
        """取一帧并识别一次，整段在执行器线程完成。

        界面上的「识别当前画面」「实时刷新」都调它，**不要**在 GUI 线程自己拼
        `capture_frame() + matcher()`：识别里的 OCR 会碰 `task.ocr`，那条路径在
        暂停态下会死循环（原因见 :meth:`run_on_executor`）。

        Args：
            timeout：等待上限（秒）。

        Returns：
            `recognize.MatchResult`。

        Raises：
            RuntimeError：执行器不可用、取帧失败或等待超时。
        """
        return self.run_on_executor(self.identify_once, timeout=timeout)

    def diagnose_blocking(self, timeout=60.0):
        """识别一次，并在**同一帧**上对全部场景做完整评估，供调试页排序展示。

        与 :meth:`identify_blocking` 的区别只在明细的样本范围：

        - `identify_blocking` 的明细受短路影响——每个场景只评估到第一个失败特征，
          各场景平均的样本数不同，相似度**跨场景不可比**；
        - 本方法对每个场景评估**全部特征**，相似度可比，排序才有意义。

        **绝不用在运行期识别链路上**。实测 50 场景 × 2 特征：首中即停 4ms，全量
        评估 189ms，差 47 倍。只有调试页这种「人点一下、等半秒也无所谓」的场景
        才适合。

        Args：
            timeout：等待上限（秒）。全量评估比识别慢，默认给到 60s。

        Returns：
            `recognize.MatchResult`，`diagnostics` 已填好。库不可用时为未命中。

        Raises：
            RuntimeError：执行器不可用、取帧失败或等待超时。
        """

        def run():
            scenes, _failures = self.build_scenes()
            meta = self.library.meta
            frame = self.capture_frame()
            if not scenes or not meta.has_resolution() or frame is None:
                result = recognize.MatchResult(status=recognize.MATCH_NONE)
                # 帧可能为空，但「取到了多大的画面」仍要带回去：空帧与「截错了
                # 窗口」都表现为识别不到，只有尺寸能帮人区分这两者。
                result.frame_size = _frame_size(frame)
                return result
            normalized = normalize.normalize_frame(frame, meta.base_width, meta.base_height)
            # **必须 stop_on_first=False**：调试页要列出「全部场景」的匹配结果，
            # 首中即停会让命中之后的场景压根不评估，表格只显示到第一个命中项为止。
            # 运行期识别仍用默认的 True（见 `make_matcher`）——那里每轮都要判断，
            # 不能为完整性牺牲速度。
            options = recognize.MatchOptions(ocr=self._ocr, stop_on_first=False)
            diagnostics = []
            for scene in scenes:
                (
                    similarity,
                    hit_count,
                    _total,
                    _mandatory_hit,
                    _missing,
                    optional_hit,
                    missing_all,
                ) = recognize.diagnose_scene(
                    scene,
                    normalized,
                    meta.base_width,
                    meta.base_height,
                    self.loader,
                    options,
                )
                mandatory_total = len(scene.mandatory_features())
                # **必需命中数 = 全部命中 − 可选命中**，这是精确值，不能靠 `1` 猜。
                #
                # 此前写的是 `mandatory_total if mandatory_hit else mandatory_total - 1`，
                # 等于「没全中就是只差一个」。但没全中可能有多个失败，甚至**一个都没中**：
                # 截错窗口时两行都返回空，真实命中是 0/2，界面却显示 `1/2`，并且
                # 同一行的「缺失特征」列还写着某个特征名——两列互相矛盾，读的人只会
                # 更困惑（曾据此误判「有一个特征命中了」）。`hit_count` 已含可选命中，
                # 减去 `optional_hit` 即得必需命中，无需再传新字段。
                mandatory_hits = max(0, hit_count - optional_hit)
                # 第 4、8 项是**可选**特征的命中数与总数：界面按「X/Y」显示，
                # X 是本次命中、Y 是配置事实。此前这里放的是「全部特征命中数」，
                # 与列名「可选命中」不符——一个场景有 2 个可选特征全中、同时有
                # 1 个必需特征失败时，那一列会显示 2，看着像可选全中，实际必需
                # 没通过，整个场景根本没命中。
                #
                # 第 5 项放**全部**未命中的必需特征名（顿号连接），不是只有第一个。
                # 只列第一个时，3 个必需特征全失败的场景只能看到一个名字，用户会
                # 以为另外两个命中了——而那正是本次要修的误导。
                diagnostics.append(
                    (
                        scene.scene_id,
                        mandatory_hits,
                        mandatory_total,
                        optional_hit,
                        "、".join(missing_all),
                        similarity,
                        scene.name,
                        len(scene.optional_features()),
                    )
                )
            # 汇总结果复用正式识别，保证「命中哪个场景」与运行期一致；明细用全量的。
            result = recognize.recognize(
                frame,
                scenes,
                meta.base_width,
                meta.base_height,
                self.loader,
                options=options,
            )
            result.diagnostics = diagnostics
            # 原始帧尺寸要在**归一化之前**取：归一化之后所有帧都是基准尺寸，
            # 再读就只能得到「库基准」，永远看不出截图目标是否选错。
            result.frame_size = _frame_size(frame)
            return result

        return self.run_on_executor(run, timeout=timeout)

    def click_and_identify(self, scene, area, after_wait=0.0, timeout=60.0):
        """点击区域、等待、再识别，整段在执行器线程完成。

        界面上的「试走一步」「单步试走」都调它。点击与后续等待都是任务方法，
        必须在执行器线程上跑——`task.click` 内部同样会 `check_enabled`，在暂停态
        下会卡住 GUI。

        **点击前后各取一帧比对**，用于识别「点了等于没点」：有些交互方式在特定
        目标程序上完全不产生输入（典型：`PostMessage` 投递到不处理投递消息的
        工具包窗口），此时点击不报错、画面也不变，界面却会照常显示「点击成功」。
        比对结果放在返回值的 `frame_changed` 上，由界面决定怎么提示。

        Args：
            scene：`model.Scene` 实例。
            area：`model.Area` 实例。
            after_wait：点击后等待的秒数。
            timeout：等待上限（秒）。

        Returns：
            `recognize.MatchResult`，即点击之后识别到的场景；`frame_changed`
            表示点击前后画面是否变化。

        Raises：
            RuntimeError：执行器不可用、取帧失败或等待超时。
        """

        def run():
            before = self.capture_frame()
            self.activate_area(scene, area)
            self.wait(after_wait)
            after = self.capture_frame()
            result = self.identify_once()
            result.frame_changed = frames_differ(before, after)
            return result

        return self.run_on_executor(run, timeout=timeout)

    def goto_blocking(self, target, timeout=300.0):
        """执行完整跳转，整段在执行器线程完成。

        导航会反复点击、等待、识别，必须整体放在执行器线程；拆成多次
        `run_on_executor` 会让「点完之后立刻识别」的时序依赖被打断。

        Args：
            target：目标场景 id。
            timeout：等待上限（秒）。导航本身可能走十几步，默认给足。

        Returns：
            `navigate.NavigateResult`。

        Raises：
            RuntimeError：执行器不可用或等待超时。
        """

        def run():
            navigator = self.build_navigator()
            return navigator.goto(target, should_stop=self.should_stop)

        return self.run_on_executor(run, timeout=timeout)

    # --- 区域操作 ---------------------------------------------------------

    def activate_area(self, scene, area):
        """让区域生效：按 `area.explore_action` 分派到具体动作。

        为什么叫 `activate_area` 而不是 `click_area`：区域本身不绑定动作类型。
        动作只影响**探索**这一种使用方式，导航与外部调用方仍可注入自己的实现
        （两者的 hook 都是注入的）。

        **为什么动作必须可配**：探索原先固定单击。碰到滑动列表、需要长按蓄力的
        按钮、右键菜单时，单击要么画面不变化（被判定为「无效区域」，连着两次就
        永久跳过），要么触发另一个功能（写出的边是错的）。而这两种失败都不报错。

        Args：
            scene：`model.Scene` 实例（本方法不使用，签名与纯逻辑层一致）。
            area：`model.Area` 实例。

        Raises：
            RuntimeError：取不到帧，无法换算坐标。
            ValueError：区域矩形退化。
        """
        del scene
        action = area.explore_action
        params = area.explore_params or {}
        if action == model.ACTION_DOUBLE_CLICK:
            self._double_click(area, params.get("interval", 0.08))
        elif action == model.ACTION_LONG_PRESS:
            self._long_press(area, params.get("duration", 1.0))
        elif action == model.ACTION_SWIPE:
            self._swipe(area, params.get("direction", model.SWIPE_UP), params.get("distance", 0.3))
        elif action == model.ACTION_RIGHT_CLICK:
            self._click_with_key(area, "right")
        else:
            # 默认单击。动作值在 `Area.__post_init__` 已校验，走到这里只可能是
            # 旧库（无 explore_action 字段）——默认单击正是它原本的行为。
            self.click_point_of(area)

    def _click_with_key(self, area, key):
        """在区域中心按指定鼠标键点击。

        Args：
            area：`model.Area` 实例。
            key：`left` / `right` / `middle`。

        Raises：
            RuntimeError：取不到帧；窗口切不到前台而当前后端只在前台生效。
            ValueError：区域矩形退化。
        """
        x, y = self.area_center(area)
        self._ensure_actionable(with_pointer=True)
        self.task.click(x, y, key=key)

    def _double_click(self, area, interval):
        """在区域中心双击。

        Args：
            area：`model.Area` 实例。
            interval：两次单击之间的间隔（秒）。

        Raises：
            RuntimeError：取不到帧。
            ValueError：区域矩形退化。

        Note：
            用两次 `click` 而不是上游的某个 `double_click`：上游没有跨设备一致的
            双击接口，两次单击加间隔在各种 interaction 后端上都成立。
        """
        x, y = self.area_center(area)
        self._ensure_actionable(with_pointer=True)
        self.task.click(x, y)
        self.wait(interval)
        self.task.click(x, y)

    def _long_press(self, area, duration):
        """在区域中心长按。

        Args：
            area：`model.Area` 实例。
            duration：按住时长（秒）。

        Raises：
            RuntimeError：取不到帧。
            ValueError：区域矩形退化。

        Note：
            用 `click(down_time=...)`：上游把「按住时长」作为 click 的参数，
            各 interaction 后端都实现了它，比手动 mouse_down/mouse_up 可靠——
            后者在异常路径上可能留下一个没松开的按键。
        """
        x, y = self.area_center(area)
        self._ensure_actionable(with_pointer=True)
        self.task.click(x, y, down_time=duration)

    def _swipe(self, area, direction, distance):
        """从区域中心沿指定方向滑动。

        Args：
            area：`model.Area` 实例。
            direction：`up` / `down` / `left` / `right`。
            distance：滑动距离占帧短边的比例（0–1）。

        Raises：
            RuntimeError：取不到帧。
            ValueError：区域矩形退化。

        Note：
            **距离按帧短边折算**，不是像素：换分辨率后手势幅度不变，否则在 4K 上
            同一份配置会滑出画面。起点取区域中心，终点按方向偏移 `distance`。
        """
        frame = self.capture_frame()
        if frame is None:
            raise RuntimeError("滑动失败：取不到当前画面，无法换算坐标")
        height, width = frame.shape[:2]
        x, y = navigate_point(area, width, height)
        span = max(1, int(round(min(width, height) * distance)))
        offsets = {
            model.SWIPE_UP: (0, -span),
            model.SWIPE_DOWN: (0, span),
            model.SWIPE_LEFT: (-span, 0),
            model.SWIPE_RIGHT: (span, 0),
        }
        delta_x, delta_y = offsets.get(direction, (0, -span))
        end_x = max(0, min(width - 1, x + delta_x))
        end_y = max(0, min(height - 1, y + delta_y))
        self._ensure_actionable(with_pointer=True)
        self.task.swipe(x, y, end_x, end_y)

    def _ensure_front(self):
        """点击类动作之前把游戏窗口切到前台，返回是否成功。

        **必须做，否则点击会被静默丢弃**：`Pynput` / `PyDirect` /
        `ForegroundPostMessage` 这些后端只在前台窗口上合成输入，窗口在后台时
        `interaction.click` 直接 return（`pynput.py:145`：
        `if not self.clickable(): logger.info("window in background, not clickable"); return`）。
        日志实证：`TaskExecutor pynput:window in background, not clickable` 之后
        游戏里什么都没发生，而界面照样显示「点击 → 识别为 xxx」。

        **为什么不直接复用 `task.ensure_in_front()`**：它最终落到
        `hwnd_window.bring_to_front()`，那是一次 `SetForegroundWindow` 裸调用，
        **调用后立刻返回**，不等切换真正生效。而 `SetForegroundWindow` 是异步的
        ——实测调用返回后立即读 `GetForegroundWindow()` 全部得到旧值，真正生效
        耗时 6—12ms。因此上游实现无法判断「到底切过去没有」，遇到前台锁拒绝时
        只能当作成功，点击就被静默丢弃。

        本实现补上两件事：**等待生效**（轮询到超时）与**前台锁兜底**
        （裸调失败后挂到当前前台线程再试）。细节见 `_force_foreground`。

        Returns：
            True 表示调用后目标窗口确为前台，False 表示未能切过去（调用方据此
            决定是否仍继续点击——继续点击在真后台后端上仍可能生效）。
        """
        hwnd = self._window_handle()
        if not hwnd:
            # ADB 设备没有 hwnd，交给框架处理（adb_ensure_in_front）。
            self._call_task_ensure_front()
            return True

        if self._force_foreground(hwnd):
            return True

        # 两条路径都没成功时，仍调一次框架实现：它内部有 hwnd 刷新逻辑
        # （窗口被重建后旧 hwnd 失效的场景），结果照实返回。
        self._call_task_ensure_front()
        return self._is_foreground(hwnd)

    def _call_task_ensure_front(self):
        """调用任务的 `ensure_in_front`，失败只记警告。

        Note：
            失败不抛异常：窗口可能被系统挡在最上层，但点一次仍可能生效；抛异常会
            让「点击并观察」直接失败，而用户更需要的是一条可读提示。
        """
        ensure = getattr(self.task, "ensure_in_front", None)
        if not callable(ensure):
            return
        try:
            ensure()
        except Exception as error:  # noqa: BLE001 — 前台化失败不阻断动作，由结果体现
            logger.warning(f"场景管理：切前台失败，点击可能不生效：{error}")

    def _window_handle(self):
        """取本任务窗口的句柄。

        Returns：
            整数句柄；ADB 设备或框架尚未探测到窗口时返回 0。

        Note：
            优先 `hwnd_window.hwnd`（客户区窗口），为空时退回 `top_hwnd`。
        """
        window = getattr(self.task, "hwnd", None)
        if window is None:
            return 0
        return getattr(window, "hwnd", 0) or getattr(window, "top_hwnd", 0) or 0

    def _is_foreground(self, hwnd):
        """按框架口径判断窗口是否为前台。

        Args：
            hwnd：窗口句柄。

        Returns：
            True 表示该窗口是当前前台窗口且可见。
        """
        try:
            import win32gui
        except ModuleNotFoundError:
            return True
        try:
            return bool(win32gui.IsWindowVisible(hwnd)) and win32gui.GetForegroundWindow() == hwnd
        except Exception:  # noqa: BLE001 — 判断失败按「未知」处理，不阻断点击
            return True

    def _force_foreground(self, hwnd, timeout=FOREGROUND_WAIT):
        """把窗口切到前台，并**等到切换真正生效**再返回。

        Args：
            hwnd：窗口句柄。
            timeout：等待上限（秒）。

        Returns：
            True 表示超时前该窗口确已成为前台，False 表示没切过去。

        Note：
            **`SetForegroundWindow` 是异步的，调用后立刻判断必然读到旧前台**。
            实测（干净窗口、每次先把前台交给无关进程、5 次采样）：
            「调用返回后立即读 `GetForegroundWindow()`」全部得到旧值，
            真正生效耗时 6—12ms。所以这里必须轮询等待，否则会把成功误判成失败
            ——表现为「点击明明生效了，却弹出『无法切到前台』」。

            **先裸调，失败才挂线程**。实测在同机同窗口下裸调 5/5 成功，`AttachThreadInput`
            并非必需；它只在裸调被前台锁拒绝时（例如前台进程正被用户拖拽、或
            系统判定调用方无权限）才作为兜底。把挂线程放第一位会让每一次点击都
            承担队列合并的开销与风险，而收益只在少数场景。

            `AttachThreadInput` 的挂载对象**只能是「当前持有前台的线程」**，不能
            连带挂目标窗口自己的线程：挂了会让目标线程也参与「谁持有前台」的
            判断，调用方身份不再唯一，切换反而被拒（实测该组合失败，只挂前台
            线程成功）。

            **不做** `SW_MINIMIZE` 之类的抖动手法：它会改变窗口状态，在
            `resizable(False, False)` 的窗口上还会把尺寸永久压坏（实测靶场被
            压成 136x40、客户区只剩 120x1，之后所有点击都落到别的窗口上，
            故障现象与「置前失败」完全混淆，排查代价极高）。
        """
        try:
            import win32con
            import win32gui
        except ModuleNotFoundError:
            return True

        try:
            if not win32gui.IsWindow(hwnd):
                return False
        except Exception:  # noqa: BLE001 — 取不到状态按「未知」放行，由后续调用暴露
            return True

        # 第一次：裸调。绝大多数场景一次就够。
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as error:  # noqa: BLE001 — 裸调失败不算终点，下面还有兜底
            logger.debug(f"场景管理：裸调切前台失败，改用线程挂载：{error}")

        if self._await_foreground(hwnd, timeout):
            return True

        # 第二次：挂到当前前台线程后再切（绕过前台锁）。
        self._attach_and_activate(hwnd)
        return self._await_foreground(hwnd, timeout)

    def _await_foreground(self, hwnd, timeout):
        """轮询等待窗口成为前台。

        Args：
            hwnd：窗口句柄。
            timeout：等待上限（秒）。

        Returns：
            True 表示已在前台。

        Note：
            步长取 `FOREGROUND_POLL`：实测延迟上限 12ms，5ms 粒度足以在 3 次内
            命中，不会为一次点击引入可感的额外停顿。
        """
        import time

        deadline = time.monotonic() + timeout
        while True:
            if self._is_foreground(hwnd):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(FOREGROUND_POLL)

    def _attach_and_activate(self, hwnd):
        """把本线程挂到当前前台线程的输入队列上，再激活目标窗口。

        Args：
            hwnd：窗口句柄。

        Note：
            只挂前台线程，**不挂目标窗口自己的线程**（理由见 `_force_foreground`）。
            解挂放在 `finally`：残留的队列关联会让本线程与前台的输入互相影响，
            本该发往游戏窗口的键鼠消息可能被投到自己的界面线程上。
        """
        try:
            import ctypes

            import win32con
            import win32gui
        except ModuleNotFoundError:
            return

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        my_thread = kernel32.GetCurrentThreadId()
        fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        attached = False
        try:
            if fg_thread and fg_thread != my_thread:
                attached = bool(user32.AttachThreadInput(my_thread, fg_thread, True))
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)
        except Exception as error:  # noqa: BLE001 — 兜底路径失败由调用方按结果决定
            logger.warning(f"场景管理：线程挂载切前台失败：{error}")
        finally:
            if attached:
                user32.AttachThreadInput(my_thread, fg_thread, False)

    def _ensure_actionable(self, with_pointer):
        """按当前交互后端的需要决定是否切前台，必要时要求切换成功。

        Args：
            with_pointer：True 表示动作走真实指针（Pynput / PyDirect）。

        Raises：
            RuntimeError：后端只在前台生效，且窗口切不过去。

        Note：
            **后台后端（`PostMessage` / `Genshin`）一律不动窗口**。它们走
            `PostMessage` 投递，与前台无关；此时把目标窗口弹到前台是纯干扰——
            用户选了「后台操作」就是不想被打断，弹窗会让这个选择形同虚设。

            这个判定必须**先于**切前台，不能先切再判断：先切的代价是不可逆的
            （窗口已经被抢到前台），事后判断再正确也收不回来。
        """
        if not with_pointer:
            return
        if not self._backend_needs_foreground():
            return
        if self._ensure_front():
            return
        raise RuntimeError(
            "点击未执行：目标窗口无法切到前台，而当前交互方式只在前台生效。"
            "请在设置中把「交互方式」改为 PostMessage 或 Genshin（可后台点击），"
            "或先把目标窗口切到前台再试。"
        )

    def _backend_needs_foreground(self):
        """判断当前交互后端是否只在前台生效。

        Returns：
            True 表示后端会因窗口不在前台而丢弃输入。

        Note：
            依据是各后端的 `should_capture()` / `clickable()` 实现：
            `Pynput` / `PyDirect` 与它们的前台投递变体（`ForegroundPostMessage`）
            都返回 `hwnd_window.is_foreground()`；只有 `PostMessage` 与 `Genshin`
            恒为 True。取不到后端实例时按「需要前台」处理——宁可多报一次错，
            也不要让用户面对「点了没反应」。
        """
        interaction = getattr(getattr(self.task, "executor", None), "interaction", None)
        if interaction is None:
            return True
        name = type(interaction).__name__
        if "PostMessage" in name and "Foreground" not in name:
            return False
        if "Genshin" in name:
            return False
        return True

    def _restore_window(self):
        """把本任务的窗口从「可见但被遮挡」恢复为可前台化状态。

        Note：
            保留此方法供外部或后续调用；`_ensure_front` 已不再依赖它
            （`_force_foreground` 内部自带 `SW_RESTORE`）。
        """
        hwnd = self._window_handle()
        if not hwnd:
            return
        try:
            import win32con
            import win32gui
        except ModuleNotFoundError:
            # 非 Windows 或未装 pywin32：交给 ensure_in_front 自行处理。
            return
        try:
            if win32gui.IsWindow(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception as error:  # noqa: BLE001 — 恢复失败不阻断，后续仍尝试前台化
            logger.warning(f"场景管理：恢复窗口失败：{error}")

    def click_point_of(self, area):
        """取区域中心并单击一次。

        Args：
            area：`model.Area` 实例。

        Raises：
            RuntimeError：取不到帧，无法换算坐标。
            ValueError：区域矩形退化。
        """
        frame = self.capture_frame()
        if frame is None:
            raise RuntimeError("点击失败：取不到当前画面，无法换算点击坐标")
        height, width = frame.shape[:2]
        x, y = navigate_point(area, width, height)
        self._ensure_actionable(with_pointer=True)
        self.task.click(x, y)

    def area_center(self, area):
        """返回区域中心在当前帧上的坐标（不点击）。

        供水印类操作与外部调用方使用——外部拿到坐标后可以自己做任意动作。

        Args：
            area：`model.Area` 实例。

        Returns：
            `(x, y)` 整数元组。

        Raises：
            RuntimeError：取不到帧。
            ValueError：区域矩形退化。
        """
        frame = self.capture_frame()
        if frame is None:
            raise RuntimeError("取坐标失败：取不到当前画面")
        height, width = frame.shape[:2]
        return navigate_point(area, width, height)

    def wait(self, seconds):
        """等待若干秒（**不经过 `task.sleep`**）。

        Args：
            seconds：秒数。

        Note：
            这里用 `time.sleep` 而不是 `task.sleep`，与「必须走任务方法」的直觉相反，
            原因是 `TaskExecutor.sleep` 有一个**不保证退出**的循环：

            ```python
            while True:
                self.check_enabled(check_pause=False)
                if not (self.blocked_by_pause() or (self.current_task is not None
                        and self.current_task.paused) or self.interaction is None
                        or not self.interaction.should_capture()):
                    to_sleep = self.pause_end_time - time.time()
                    if to_sleep <= 0:
                        return          # ← 唯一的正常出口
                    ...
                else:
                    self._wait_for_activity(0.1)
            ```

            `should_capture()` 在 `Pynput` / `PyDirect` / `ForegroundPostMessage` 里
            返回 `hwnd_window.is_foreground()`——用户点「点击并观察」时焦点在调试页上，
            游戏窗口不在前台，该判断恒为 False，于是**永远进不到那个 return**，只在
            else 分支里每 0.1 秒空转一次。实测表现就是「点了按钮整个界面卡死」。

            它本来的设计意图是「等用户把窗口切回前台再继续」，但我们这里的等待是
            「点击后等画面稳定」的固定时长，不是可无限推迟的操作：卡住不动的结果比
            多等两秒糟得多，而且此时画面本来也没在被操作。

            在**执行器线程**上直接 `time.sleep` 是安全的：该线程只跑这一个 job，
            阻塞它不会影响 GUI；GUI 线程在 `future.result(timeout)` 上等，超时后仍能
            拿回控制权。
        """
        if seconds <= 0:
            return
        import time

        time.sleep(seconds)

    def should_stop(self):
        """返回任务是否已被要求停止。

        Returns：
            任务处于停止/禁用状态，或界面点了「中断」时为 True。
        """
        if getattr(self.task, "cancel_requested", False):
            return True
        executor = getattr(self.task, "executor", None)
        if executor is None:
            return False
        exit_event = getattr(executor, "exit_event", None)
        if exit_event is not None and exit_event.is_set():
            return True
        return False

    # --- 组装 -------------------------------------------------------------

    def build_graph(self):
        """按当前库建流转图。

        Returns：
            `graph.SceneGraph` 实例。

        Raises：
            graph.GraphError：存在悬空边。
        """
        scenes, _failures = self.build_scenes()
        return graph.SceneGraph.build(scenes, self.library.routes)

    def build_navigator(self, options=None):
        """组装导航器。

        Args：
            options：`navigate.NavigateOptions`；省略时用默认值。

        Returns：
            `navigate.SceneNavigator` 实例。
        """
        hooks = navigate.NavigatorHooks(
            capture_frame=self.capture_frame,
            recognize=self.make_matcher(),
            activate_area=self.activate_area,
            wait=self.wait,
        )
        return navigate.SceneNavigator(self.build_graph(), hooks, options)

    def build_explorer(self, options=None, save_hooks=True, restart_target=None):
        """组装探索器。

        Args：
            options：`explore.ExploreOptions`；省略时用默认值。
            save_hooks：为 True 时接入「草稿落盘」与「草稿边记录」。
            restart_target：可选的「重启目标并重置状态」函数；探索卡在回不去的
                画面时调用它，成功后继续当前队列。为 None 时不做这种恢复。

        Returns：
            `explore.SceneExplorer` 实例。
        """
        formal, _failures = self.build_scenes()
        # 探索认识「产物区正式场景 + 编辑区草稿」：起点可能是刚刚在编辑区配好、
        # 还没导出到产物区的新场景，只读产物区会让探索一开局就「当前画面无法识别」。
        scenes = self._explore_scenes(formal)
        # 识别器按「当前库快照」构造一次，草稿落盘后必须重建，否则新画面永远认不出。
        # 用可变容器承载，让 matcher 能原地换掉而不用改 ExploreHooks 的引用。
        matcher_holder = {"fn": self._make_explore_matcher(scenes)}

        def recognize_with_refresh(frame):
            return matcher_holder["fn"](frame)

        def refresh_after_draft(_scene):
            # 新草稿落在**编辑区**，而正式场景在产物区——重建时必须把两边都并进来，
            # 否则识别器认不出刚发现的画面，BFS 停在第一个新画面上再也走不动。
            matcher_holder["fn"] = self._make_explore_matcher(self._explore_scenes(formal))

        hooks = explore.ExploreHooks(
            capture_frame=self.capture_frame,
            recognize=recognize_with_refresh,
            activate_area=self.activate_area,
            wait=self.wait,
            save_draft=self.save_draft if save_hooks else None,
            save_edge=self.save_edge if save_hooks else None,
            should_stop=self.should_stop,
            on_draft_saved=refresh_after_draft if save_hooks else None,
            on_event=self._forward_explore_event,
            restart_target=restart_target,
        )
        # 探索必须拿到流转图：没有它就只能「当前恰好在目标场景」才展开，
        # 多级图上 BFS 无法回退到上一级继续探其余区域。
        #
        # 用两区合并后的路由表建图与判「已有结论」：产物区是运行期那份，编辑区是
        # 刚应用、还没导出的那份。只读产物区会把刚应用的边当成不存在，探索重复
        # 探测同一区域，还可能因为图里缺这条边而回不到上层。
        merged_routes = self._merged_routes()
        try:
            planner = graph.SceneGraph.build(scenes, merged_routes)
        except graph.GraphError as error:
            # 图不完整（有悬空边）时仍允许探索：探索本身就是为了补边。
            # 但要显式告知——静默降级会让「探索漏了很多区域」无从解释。
            planner = None
            self.last_graph_error = str(error)
        return explore.SceneExplorer(scenes, hooks, options, planner=planner, routes=merged_routes)

    def _forward_explore_event(self, event):
        """把探索事件交给任务，用于日志与实时进度。

        Args：
            event：`explore.ExploreEvent` 实例。

        Note：
            任务实现了 `_on_explore_event` 时由它统一记日志并更新进度；测试用的
            假任务没有该方法，退回只记日志。
        """
        handler = getattr(self.task, "_on_explore_event", None)
        if handler is not None:
            handler(event)
        else:
            log_explore_event(self.task, event)

    def _merged_routes(self):
        """把 `library` 与 `draft_library` 的路由表合并，后者优先。

        Returns：
            `routes.RouteTable` 实例（新对象，写它不会落到任何一个库）。

        Note：
            界面路径下两个库都是编辑区，结果就是编辑区自己的路由表；保留合并
            逻辑是为了运行期任务复用同一桥接时，仍能看到编辑区未导出的边。

            合并顺序固定为「先 `library`、后 `draft_library`」：同键时后者覆盖，
            因为它是工作副本，也是最近一次人工修改的地方。

            传出去的是**合并表而不是任何一个库的表对象**：探索期间 `_remember_edge`
            会往这张表里写边，若直接传编辑区的表，未落盘的临时边会被下一次
            `save_routes` 顺带写进文件；传副本则改动只影响本轮的内存判断。
        """
        merged = routes.RouteTable()
        self.library.ensure_routes()
        for route in self.library.routes:
            merged.put(route)
        self.draft_library.ensure_routes()
        for route in self.draft_library.routes:
            merged.put(route)
        return merged

    def _explore_scenes(self, formal_scenes):
        """把产物区的正式场景与编辑区的草稿合并成识别集。

        Args：
            formal_scenes：产物区的正式场景列表。

        Returns：
            `model.Scene` 列表。草稿按 id 升序追加在正式场景之后。

        Note：
            探索期间**必须同时认识两边**：正式场景在产物区（运行期在读的那份），
            而新发现的草稿落编辑区（草稿页与 `promote_draft` 都在那边）。只读
            一边的话，要么认不出已有的正式场景（探索一开始就「当前画面无法识别」），
            要么认不出刚发现的草稿（点进去就再也回不来）。

            同名 id 冲突时以正式场景为准：草稿 id 形如 `draft-0001`，与 `scene-xxxx`
            不重叠，出现同 id 说明库已被人手工改乱，此时保留正式那份更安全。
        """
        known = {scene.scene_id for scene in formal_scenes}
        merged = list(formal_scenes)
        try:
            drafts, _failures = self.draft_library.load_drafts()
        except Exception:  # noqa: BLE001 — 草稿区读不出来不该阻断探索
            return merged
        for draft in drafts:
            if draft.scene_id not in known:
                merged.append(draft)
        return merged

    def _make_explore_matcher(self, scenes):
        """构造探索用的识别函数，直接吃给定的场景列表。

        Args：
            scenes：参与识别的场景列表（正式 + 草稿）。

        Returns：
            `(frame) -> recognize.MatchResult` 可调用对象。

        Note：
            不复用 :meth:`make_matcher`：那个方法内部自己调 `build_scenes()`
            只读产物区，无法注入草稿。二者共用同一套 `recognize.recognize`，
            只是场景集的来源不同。
        """
        meta = self.library.meta
        if not scenes or not meta.has_resolution():
            return lambda _frame: recognize.MatchResult(status=recognize.MATCH_NONE)
        options = recognize.MatchOptions(ocr=self._ocr)

        def matcher(frame):
            return recognize.recognize(
                frame,
                scenes,
                meta.base_width,
                meta.base_height,
                self.loader,
                options=options,
            )

        return matcher

    # --- 草稿落盘 ---------------------------------------------------------

    def save_draft(self, scene, frame, parent_frame):
        """把探索发现的新画面存为草稿场景。

        默认图取自 `frame`（已脱敏的落盘图由调用方保证）；模板图按候选特征区域
        从同一帧裁出。裁出的模板若零方差（纯色），识别层会拒绝——但草稿仍保留，
        让用户看到「这里发现了一个画面，但自动候选不可用」。

        Args：
            scene：`model.Scene` 实例（id 为 draft- 形态）。
            frame：新画面的帧。
            parent_frame：上游画面的帧（本方法不使用，留给后续差分增强）。

        Raises：
            store.SceneStoreError：落盘失败。
        """
        del parent_frame
        # **草稿写编辑区**，与草稿页、`promote_draft` 同一个库。写产物区的话，
        # 草稿页永远读不到（它读编辑区），而两个库都在正常读写、不会报错。
        library = self.draft_library
        library.save_scene(scene, as_draft=True)
        if frame is None:
            return
        library.save_base_image(scene.scene_id, frame)
        if not library.meta.has_resolution():
            height, width = frame.shape[:2]
            library.set_base_resolution(width, height)
        meta = library.meta
        for feature in scene.features:
            if feature.kind != model.KIND_TEMPLATE:
                continue
            x1, y1, x2, y2 = feature.pixel_rect(meta.base_width, meta.base_height)
            height, width = frame.shape[:2]
            if (width, height) != (meta.base_width, meta.base_height):
                continue
            crop = frame[y1:y2, x1:x2]
            if crop.size:
                library.save_template(scene.scene_id, feature.name, crop)
            library.save_mask(
                scene.scene_id,
                feature.name,
                feature.shape.mask(meta.base_width, meta.base_height),
            )
        self.loader.invalidate(scene.scene_id)

    def save_edge(self, from_scene, area_name, to_scene, observations, after_wait=None):
        """记录一条探索得到的边。

        边写进**编辑区**库的路由表（`ok_scenes/routes.json`）——流转页读的就是这
        一份，确认、改目标、设置等待都在那里操作。草稿边一律标 `explored`，人工
        确认后才升为 `confirmed`。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。
            to_scene：目标场景 id。
            observations：观测次数。
            after_wait：点击到识别出目标场景的实测耗时；None 时按路由默认值。

        Note：
            **必须写编辑区，不能按来源场景归属分库**。探索报告写着「采集 N 条边」，
            而表格读编辑区：边若落进产物区，用户看到的就是「有 N 条边却没有一行
            可选中」——没有报错，只是确认按钮永远提示「请先选中一条边」。

            正式场景与草稿都写编辑区：产物区由编辑区导出，探索边属于待人工确认的
            编辑内容，确认后随下一次导出进入产物区。直接写产物区等于跳过确认，
            下次导出的路由表还会把它覆盖掉。

            来源场景或区域在编辑区不存在时直接返回：探索的识别集来自产物区，两区
            不同步时硬写会留下指向不存在区域的悬空边，建图直接报错。
        """
        library = self.draft_library
        scene = library.load_scene(from_scene)
        if scene is None or scene.find_area(area_name) is None:
            return
        library.ensure_routes()
        # 观测次数按「本区域已观测到几次」写：探索每轮从 1 开始数重复点击，
        # 累加会把同一轮的重试当成历史观测。
        existing = library.routes.get(from_scene, area_name)
        source = model.SOURCE_EXPLORED
        if existing is not None and existing.routable():
            # 已生效的路径不因再次探索而降级：探索只补新边，不改已有结论。
            source = existing.source
        library.routes.put(
            routes.Route(
                from_scene,
                area_name,
                to_scene,
                after_wait=1.0 if after_wait is None else after_wait,
                source=source,
                observations=observations,
            )
        )
        library.save_routes()


def navigate_point(area, frame_width, frame_height):
    """把区域矩形换算成当前帧上的点击坐标。

    Args：
        area：`model.Area` 实例。
        frame_width：当前帧宽。
        frame_height：当前帧高。

    Returns：
        `(x, y)` 整数元组。

    Raises：
        ValueError：矩形退化或分辨率非法。
    """
    from extensions.capabilities.scene_manager import normalize

    try:
        return normalize.click_point(area.rect, frame_width, frame_height)
    except normalize.NormalizeError as error:
        raise ValueError(str(error)) from error


def rect_pixels(rect, width, height):
    """把相对矩形换算成像素矩形（供模板裁剪）。

    Args：
        rect：相对比例 `(x1, y1, x2, y2)`。
        width：帧宽。
        height：帧高。

    Returns：
        `(x1, y1, x2, y2)` 整数元组。

    Raises：
        ValueError：矩形退化或分辨率非法。
    """
    from extensions.capabilities.scene_manager import normalize

    try:
        return normalize.rect_to_pixels(rect, width, height)
    except normalize.NormalizeError as error:
        raise ValueError(str(error)) from error


def frame_size(frame):
    """返回帧的 `(width, height)`。

    Args：
        frame：numpy 数组。

    Returns：
        `(width, height)` 元组；帧为空时返回 `(0, 0)`。
    """
    if frame is None:
        return 0, 0
    height, width = frame.shape[:2]
    return width, height
