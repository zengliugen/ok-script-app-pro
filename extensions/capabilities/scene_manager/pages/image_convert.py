# -*- coding: utf-8 -*-
"""numpy 帧与 QImage 的互转。

单独成模块而不是放在某个页面里：配置页与编辑弹窗都要用，而「编辑器 import 配置页」
是反向依赖——配置页已经在 import 编辑器，双向引用会让导入顺序变成隐式约束，
改动任意一方都可能触发循环导入。
"""


def bgr_to_qimage(frame):
    """把 numpy 帧转成 QImage。

    Args：
        frame：BGR 或灰度数组。

    Returns：
        `QImage` 实例；帧为空或形状不支持时返回 None。
    """
    if frame is None:
        return None
    import numpy as np
    from PySide6.QtGui import QImage

    array = np.ascontiguousarray(frame)
    height, width = array.shape[:2]
    if array.ndim == 2:
        return QImage(
            array.data,
            width,
            height,
            array.strides[0],
            QImage.Format.Format_Grayscale8,
        ).copy()
    if array.ndim == 3 and array.shape[2] == 3:
        return QImage(
            array.data,
            width,
            height,
            array.strides[0],
            QImage.Format.Format_BGR888,
        ).copy()
    if array.ndim == 3 and array.shape[2] == 4:
        return QImage(
            array.data,
            width,
            height,
            array.strides[0],
            QImage.Format.Format_RGBA8888,
        ).copy()
    return None


def qimage_to_bgr(image):
    """把 QImage 转成 BGR numpy 数组。

    Args：
        image：`QImage` 实例。

    Returns：
        BGR 数组；输入为空或格式不支持时返回 None。
    """
    if image is None or image.isNull():
        return None
    import numpy as np
    from PySide6.QtGui import QImage

    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width, height = converted.width(), converted.height()
    if width <= 0 or height <= 0:
        return None
    buffer = converted.constBits()
    array = np.frombuffer(buffer, dtype=np.uint8, count=converted.sizeInBytes())
    rows = array.reshape(height, converted.bytesPerLine())[:, : width * 3]
    rgb = rows.reshape(height, width, 3)
    # RGB888 在内存里的字节序是 R、G、B，cv2 要 B、G、R。
    return np.ascontiguousarray(rgb[:, :, ::-1])
