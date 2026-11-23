# -*- coding: utf-8 -*-
"""分辨率一致性判断：截图准入、保存前检查共用同一套口径。

为什么单独一个模块：判断散落在两处时，最难发现的是「两处口径不一致」——截图放行
的场景在保存时被拦下，或反过来。这里把三条判断集中：

| 场景 | 判断 | 为什么 |
|---|---|---|
| 库里还没有基准（场景为空） | 用当前帧立基准 | 第一张图就是库的参照系，没有别的可选 |
| 已有场景，准备截图 | 与基准一致才允许 | 不同分辨率拍进来的图，特征框到运行期必然错位 |
| 保存到产物区 | 逐个场景核对预览图 | 历史数据可能早就与基准不符，导出等于把错数据发布出去 |

**宽高比与尺寸分别处理**：宽高比不同是硬错误（换算出的坐标不可信）；比例相同但
尺寸不同只提示（识别时帧会等比缩放到基准，结果正确）。把两者混为一谈会让「换个
分辨率重拍同一画面」这种正常操作被拦住。
"""


def describe_size(width, height):
    """把分辨率格式化为「1280×720」形式。

    Args：
        width：宽。
        height：高。

    Returns：
        显示用字符串；任一分量非正时返回 `未知`。
    """
    if width <= 0 or height <= 0:
        return "未知"
    return f"{width}×{height}"


def aspect_text(width, height):
    """把分辨率格式化为「1280×720（1.7778）」形式。

    Args：
        width：宽。
        height：高。

    Returns：
        显示用字符串，带四位小数的宽高比；分量非正时返回 `未知`。
    """
    if width <= 0 or height <= 0:
        return "未知"
    return f"{width}×{height}（{width / height:.4f}）"


def frame_problem(frame_size, base_width, base_height):
    """判断一帧能否用于「已有场景」的库。

    Args：
        frame_size：当前帧的 `(width, height)`。
        base_width：库基准宽。
        base_height：库基准高。

    Returns：
        None 表示可用；否则返回可读的中文原因，供弹窗直接展示。

    Note：
        容差走 `normalize.ratio_differs`，与识别链路同一套判据——两处口径不同
        会出现「截图时说不行、识别时说可以」这种自相矛盾的提示。
    """
    from extensions.capabilities.scene_manager import normalize

    width, height = frame_size
    if width <= 0 or height <= 0:
        return "取到的画面尺寸无效，请检查设备连接"
    if base_width <= 0 or base_height <= 0:
        # 调用方应在库为空时先立基准；走到这里说明基准丢了，属于数据异常。
        return "场景库没有记录基准分辨率，请重新采集第一个场景"
    if (width, height) == (base_width, base_height):
        return None
    try:
        differs = normalize.ratio_differs(width, height, base_width, base_height)
    except normalize.NormalizeError:
        return "画面尺寸无效，无法与场景库比对"
    if differs:
        return (
            f"画面宽高比与场景库不一致：\n"
            f"当前 {aspect_text(width, height)}\n"
            f"库基准 {aspect_text(base_width, base_height)}\n\n"
            f"比例不同意味着画面构成已变（缩放了窗口、裁了边或加了黑边），"
            f"现在拍进去的特征框到运行期会整体错位。\n"
            f"请把游戏窗口恢复到采集场景时的尺寸，或新建一个场景库。"
        )
    # 比例一致、尺寸不同：识别会等比缩放，结果正确，允许继续。
    return None


def frame_warning(frame_size, base_width, base_height):
    """返回「比例一致但尺寸不同」的提醒文本。

    Args：
        frame_size：当前帧的 `(width, height)`。
        base_width：库基准宽。
        base_height：库基准高。

    Returns：
        提醒文本；尺寸完全一致或存在硬错误时返回 None。
    """
    width, height = frame_size
    if (width, height) == (base_width, base_height):
        return None
    if base_width <= 0 or base_height <= 0:
        return None
    if frame_problem(frame_size, base_width, base_height) is not None:
        return None
    return (
        f"当前画面 {describe_size(width, height)} 与库基准 "
        f"{describe_size(base_width, base_height)} 尺寸不同但比例一致，"
        f"识别时会自动缩放，结果不受影响。"
    )


def capture_warning(frame_size, base_width, base_height, matched=False):
    """返回「截图目标可能选错」的提醒文本，供调试页在识别结果旁展示。

    与 :func:`frame_problem` 的分工：那个函数决定「这一帧能不能用来**采集**
    场景」，会拦下操作；本函数只用于**识别后的诊断提示**，绝不阻断——识别本身
    能跑（比例一致时会缩放、比例不一致时早就抛错了），提示的价值在于让人知道
    「识别不到可能不是识别的问题」。

    为什么需要这条提示：识别只关心图像内容，尺寸差异会被归一化吸收，所以
    「截错窗口」在识别链路里是完全合法的输入，结果只有「全部特征都不命中」。
    实测踩过——截图目标被选成输入法窗口（2560×1440），界面只显示「未识别」，
    排查方向全落在识别逻辑上，实际上一个字都没截到。

    Args：
        frame_size：实际取到的帧 `(width, height)`；None 表示没取到帧。
        base_width：库基准宽。
        base_height：库基准高。
        matched：本次识别是否命中了场景。

    Returns：
        可读提示；尺寸一致或无法判断时返回 None。

    Note：
        **尺寸不同本身不是错误，只有「识别没命中」时才值得提示**。

        必须看 `matched`：2560×1440 的游戏窗口与 1280×720 的库基准宽高比完全
        相同，识别靠缩放就能正确工作——这是合法且常见的配置。若只要尺寸不同
        就提示，这类用户每次都会看到一条「截图目标可能选错」，提示随即贬值。
        反过来，识别没命中又伴随尺寸不一致时，这条信息几乎总能直接指明原因
        （输入法窗口 2560×1440 与库基准比例相同，光看比例无论如何都分辨不出来）。
    """
    if frame_size is None:
        return "没有取到画面：请检查设备连接与截图目标是否已选中。"
    if base_width <= 0 or base_height <= 0:
        return None
    width, height = frame_size
    if (width, height) == (base_width, base_height):
        return None
    if matched:
        # 命中即证明画面内容正确，尺寸差异已被缩放吸收，不必再提。
        return None

    from extensions.capabilities.scene_manager import normalize

    try:
        differs = normalize.ratio_differs(width, height, base_width, base_height)
    except normalize.NormalizeError:
        return None
    base_text = describe_size(base_width, base_height)
    if differs:
        return (
            f"取到的画面 {describe_size(width, height)} 与场景库基准 {base_text} 宽高比不同，"
            f"识别结果不可信。很可能截图目标选错了窗口——"
            f"请在主界面设备下拉里选中游戏窗口后重试。"
        )
    return (
        f"未识别，且取到的画面 {describe_size(width, height)} 与场景库基准 {base_text} 尺寸不同"
        f"（比例一致，识别会自动缩放）。很可能截图目标选错了窗口——"
        f"请在主界面设备下拉里选中游戏窗口后重试。"
    )
