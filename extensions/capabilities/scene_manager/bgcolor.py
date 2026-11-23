# -*- coding: utf-8 -*-
"""模板背景色识别：把「与背景同色」的像素从匹配中排除。

## 为什么需要它

模板匹配用 `TM_CCOEFF_NORMED` 计算相关性，**每个像素都参与**。圆形按钮的
四角、卡片外围的底色都属于背景，它们不区分画面，却会稀释有效像素的权重。

实测（设计文档 §「特征是一块形状，不是矩形」）：同一张「四角有干扰元素」的
模板，不带掩码 0.9221，带掩码 0.9918。本靶场色卡模板更极端——24.4% 的像素
是四周底色 `(48,48,48)`。

形状掩码（矩形/圆/多边形）只能表达「几何区域」，表达不了「这块几何区域里
哪些像素是背景」。色卡就是典型：整块矩形里左右两端的底色块与中间的色块
在几何上是同一片区域，只能靠颜色区分。

## 三种取色方式

| 方式 | 适用 | 失败形态 |
|---|---|---|
| 四边众数 | 四周同色的元素（卡片、色卡、带边框的按钮） | 背景是渐变或带纹理时众数不唯一 |
| 鼠标取色 | 用户指着某块纯色说「这是背景」 | 点到的像素属于前景 |
| 手工指定 | 上述都不适用 | — |

**四边众数为什么可行**：UI 元素的背景通常延伸到元素边缘，而前景内容（文字、
图标）居中。取四条边出现最多的颜色，天然落在背景上。这不是猜测——本靶场
色卡模板四条边的众数色是同一个 `(48,48,48)`，占比 954/954（全中）。

## 容差的口径

按**每通道差值**判定，不按欧氏距离：

```text
|b - b0| <= tol 且 |g - g0| <= tol 且 |r - r0| <= tol
```

欧氏距离会让「纯黑 (0,0,0) 与纯白 (255,255,255)」这类高对比色对落进同一个
球形邻域——半径 221 的球能同时包住两者。深色 UI 上那等于把关键前景一起排除。
"""

from __future__ import annotations

import numpy as np

# 默认容差（每通道）。
#
# 取 4：纯色块经 PNG 无损编码后本应完全一致，但实际模板可能来自屏幕截图，
# 受抗锯齿、色彩管理与 HDR 色调映射影响，同一块底色常有 1–3 的漂移。
# 再大就会开始吞掉「深灰底 + 稍浅灰图标」这类低对比前景。
DEFAULT_TOLERANCE = 4


def edge_mode_color(template):
    """取模板图四条边上出现次数最多的颜色。

    Args：
        template：BGR 数组。灰度图会被当作三通道同样处理。

    Returns：
        `(颜色元组, 占比)` 二元组；`颜色元组` 是 `(b, g, r)`，占比是该方法在
        四边像素里的比例（0–1）。模板为空或尺寸过小时返回 `(None, 0.0)`。

    Note：
        只取**最外一圈**像素，不取「靠近边的若干行」：后者在前景贴边时会把
        前景算进统计，众数可能落在前景色上。代价是边缘有 1px 描边时众数会被
        描边色占据——那种情况用户应当改用鼠标取色。
    """
    if template is None or getattr(template, "size", 0) == 0:
        return None, 0.0
    height, width = template.shape[:2]
    if height < 3 or width < 3:
        return None, 0.0

    # 统一成 `(n, 3)` 的像素矩阵：灰度图把单通道复制成三份，这样后续的众数
    # 统计只有一条代码路径。分支写法容易在图省一处、把「高度当通道数」这类
    # 错误藏进去（本函数第一版就在灰度上用错了维度）。
    if len(template.shape) == 2:
        pixels = np.repeat(template[:, :, None], 3, axis=2)
    else:
        pixels = template[:, :, :3]

    # 拼出四边像素：上、下两整行 + 左、右两整列（去掉与上下重复的角点）
    edges = [
        pixels[0, :, :].reshape(-1, 3),
        pixels[-1, :, :].reshape(-1, 3),
        pixels[1:-1, 0, :].reshape(-1, 3),
        pixels[1:-1, -1, :].reshape(-1, 3),
    ]
    flat = np.concatenate(edges, axis=0)
    if flat.size == 0:
        return None, 0.0

    colors, counts = np.unique(flat, axis=0, return_counts=True)
    index = int(np.argmax(counts))
    color = tuple(int(value) for value in colors[index])
    ratio = float(counts[index]) / float(flat.shape[0])
    return color, ratio


def sample_color(template, x, y):
    """取模板图上某点的颜色。

    Args：
        template：BGR 数组。
        x：相对比例 x（0–1），不是像素。
        y：相对比例 y（0–1）。

    Returns：
        `(b, g, r)` 元组；坐标越界或模板为空时返回 None。

    Note：
        坐标按**相对比例**传入，与场景里其它坐标口径一致——界面上的画布可能
        带缩放，直接传像素会让「用户点的地方」与「读到的像素」错位。
    """
    if template is None or getattr(template, "size", 0) == 0:
        return None
    height, width = template.shape[:2]
    if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
        return None
    px = min(width - 1, max(0, int(round(x * (width - 1)))))
    py = min(height - 1, max(0, int(round(y * (height - 1)))))
    pixel = template[py, px]
    if len(template.shape) == 2:
        value = int(pixel)
        return (value, value, value)
    return tuple(int(value) for value in pixel[:3])


def background_coverage(template, color, tolerance=DEFAULT_TOLERANCE):
    """统计与指定颜色相近的像素占比。

    Args：
        template：BGR 数组。
        color：`(b, g, r)`。
        tolerance：每通道容差。

    Returns：
        占比（0–1）。模板为空或颜色为 None 时返回 0.0。

    Note：
        界面用它回答「这次排除掉了多少」——用户需要看到这个数字才能判断
        取色是否取对了。占比接近 1 说明取到了前景主色（会把整个模板排空），
        接近 0 说明没取到真正的背景。
    """
    if template is None or getattr(template, "size", 0) == 0 or color is None:
        return 0.0
    reference = np.array(color[:3], dtype=np.int16)
    diff = np.abs(template[..., :3].astype(np.int16) - reference)
    matched = np.all(diff <= int(tolerance), axis=2)
    return float(matched.mean())
