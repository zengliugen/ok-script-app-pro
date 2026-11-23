# -*- coding: utf-8 -*-
"""场景识别：必需特征全中 + 可选打分。

判据只有一条硬规则：**一个场景的全部必需特征都命中，才算命中该场景**。可选特征
只参与打分，用于在多个候选之间排序与给出「疑似」结果。

为什么不纯用评分制：所有特征加权过阈值，就要为每个场景逐调阈值。场景到几十个后
每加一个场景都要调参，而调参没有客观依据——这是纯评分制在参考实现里最终被换成
「必需全中」的原因。

开销控制（实测依据）：

- 全图匹配 0.470s（3840x2160），限定 25% 区域 0.0265s，差 18 倍；主因是搜索面积。
  因此特征必须有搜索区域，缺失该特征直接判未命中并记原因。
- 多场景全扫约 9ms/场景。先试缓存的上次场景、不中再全扫，命中时省下全扫开销。

本模块**不导入 `ok`，只在匹配时延迟导入 cv2**：识别是核心判定，必须能在无设备
环境用合成图验证。匹配调用注入的 `matcher`，真实环境由 `recognize_runtime` 提供。
"""

import time

import numpy as np

from extensions.capabilities.scene_manager import model, normalize

# 疑似候选的返回数量上限。只用于诊断与回退决策，不用于寻路。
SUSPICIOUS_LIMIT = 3

# OCR 裁剪时向外扩的像素数。与 `match_template` 的搜索区外扩一致。
#
# **不能设成 0**：DB 检测器把文字区域 unclip（向外扩张）后作为识别输入，扩张后
# 的框超出裁剪边界就会被裁掉，导致检测失败。实测文字离边界不足 4px 时识别率崩到
# 只能得到碎片（'TT'、'五'），≥4px 恢复正常。
TEXT_PAD = 8

# 送 OCR 前把图补齐到该倍数。
#
# 检测器内部把输入边长吸附到 32 的倍数，且用 `round`——一半情况会向下取整把图压小
# （实测 46 高压到 32，压缩 30%，笔画糊在一起）。先按 `ceil` 补齐，吸附就恒等，
# 压缩从结构上不可能发生。
OCR_SIDE_MULTIPLE = 32

# 命中状态。
MATCH_NONE = "none"
MATCH_EXACT = "exact"
MATCH_SUSPICIOUS = "suspicious"


class RecognizeError(ValueError):
    """识别前置条件不满足。"""


class MatchResult:
    """一次识别或一次单场景判定的结果。

    Attributes：
        status: 取 `MATCH_NONE` / `MATCH_EXACT` / `MATCH_SUSPICIOUS`。
        scene: 命中的场景（`MATCH_NONE` 时为 None）。
        candidates: 疑似场景列表，元素为 `(scene, score)`，按分值降序。
        details: 每个场景的判定明细，元素为 `(scene_id, mandatory_hits,
            mandatory_total, optional_hits, missing, similarity, name)`。`similarity` 是
            **已评估特征**的置信度均值——短路时只覆盖前几个特征，因此不同场景之间
            **不可比**（一个全中、一个首特征即失败，两者平均的样本数不同）。
        normalized: 本次是否发生过帧缩放。
        duration: 识别耗时（秒），覆盖归一化 + 全场景扫描。`0.0` 表示**没有真正
            识别**（库为空或缺基准时 `runtime.make_matcher` 返回的兜底结果），
            界面据此不显示耗时——显示 `0 ms` 会让人以为识别快到不需要时间。
        diagnostics: 可选的**全量**诊断明细（同样是 6 元组，但 `similarity` 覆盖
            该场景的全部特征）。由 `runtime.TaskBridge.diagnose_blocking` 填入，
            只在调试页使用。它为 None 时界面退回 `details`。两者的区别只在样本
            范围：`details` 受短路影响，跨场景不可比；`diagnostics` 全部评估完，
            可以放心排序。
    """

    def __init__(
        self,
        status=MATCH_NONE,
        scene=None,
        candidates=None,
        details=None,
        normalized=False,
        duration=0.0,
        diagnostics=None,
    ):
        """构造识别结果。

        Args：
            status：命中状态。
            scene：命中的场景。
            candidates：疑似场景列表。
            details：判定明细列表。
            normalized：是否发生过帧缩放。
            duration：识别耗时（秒）；未真正识别时保持 0.0。
            diagnostics：全量诊断明细；None 表示没有做过全量评估。
        """
        self.status = status
        self.scene = scene
        self.candidates = [] if candidates is None else candidates
        self.details = [] if details is None else details
        self.normalized = normalized
        self.duration = duration
        self.diagnostics = diagnostics
        # 点击前后画面是否有变化。由 `runtime.TaskBridge.click_and_identify` 填入，
        # 供调试页判断「点击是否真的产生效果」；识别类调用不涉及，保持 None。
        self.frame_changed = None
        # 本次识别所用**原始帧**的 `(宽, 高)`；未知时为 None。
        #
        # 存在的唯一目的是让界面能回答「我截的是哪个窗口」：识别只关心归一化后的
        # 图像内容，尺寸差异会被缩放吸收，所以尺寸不参与判定。但排查时恰恰最需要
        # 它——实测出现过「截图目标被选成输入法窗口（2560×1440）」导致所有特征
        # 都不命中，而界面只显示「未识别」，看着像识别功能坏了。
        self.frame_size = None

    def matched(self):
        """返回是否精确命中。

        Returns：
            状态为 `MATCH_EXACT` 时为 True。
        """
        return self.status == MATCH_EXACT

    def scene_id(self):
        """返回命中场景的 id。

        Returns：
            命中时为其 id；未命中时为空串。
        """
        return self.scene.scene_id if self.scene else ""

    def best_suspicion(self):
        """返回分值最高的疑似场景。

        Returns：
            `(scene, score)`；无候选时为 `(None, 0.0)`。
        """
        if not self.candidates:
            return None, 0.0
        return self.candidates[0]

    def detail_for(self, scene_id):
        """按场景 id 取判定明细。

        Args：
            scene_id：场景 id。

        Returns：
            明细元组；不存在时返回 None。
        """
        for item in self.details:
            if item[0] == scene_id:
                return item
        return None

    def details_by_similarity(self):
        """返回按相似度降序排列的判定明细。

        Returns：
            明细元组列表（与 :attr:`details` 同构）。

        Note：
            **优先用 `diagnostics`**（全量评估），没有时才退回 `details`。原因：
            `details` 受短路影响，每个场景的相似度是「不同数量特征」的平均值——
            一个全中的场景平均了 5 个特征，一个首特征就失败的平均了 1 个，两者
            数值不可比，排序会失真。调试页会给 `diagnostics`，所以那条路径上排序
            是可信的；运行期不排序，不受影响。

            排序键取第 6 项，**并用原下标做次级键**——相似度完全相同时保持扫描
            顺序。不写成 `details.index(item)`：元组内容完全相同时它会返回第一个
            匹配项的下标，次级键就失效了。缺第 6 项的旧格式元组按 `-1` 参与排序
            （排在最后），不按 0 处理——0 是「算出来全是 0 置信度」，与「没有这个
            数据」是两回事。
        """
        source = self.diagnostics if self.diagnostics is not None else self.details
        indexed = list(enumerate(source))
        indexed.sort(key=lambda pair: (-(pair[1][5] if len(pair[1]) > 5 else -1.0), pair[0]))
        return [item for _index, item in indexed]


def _region_pixels(feature, base_width, base_height):
    """把特征的形状换算成基准分辨率下的像素包围盒。

    Args：
        feature：`model.Feature` 实例。
        base_width：基准帧宽。
        base_height：基准帧高。

    Returns：
        `(x1, y1, x2, y2)` 整数元组；形状退化时返回 None。
    """
    try:
        return feature.pixel_rect(base_width, base_height)
    except (ValueError, AttributeError):
        return None


def _crop(frame, rect):
    """按像素矩形裁剪帧。

    Args：
        frame：numpy 数组。
        rect：像素矩形 `(x1, y1, x2, y2)`。

    Returns：
        裁剪结果。
    """
    x1, y1, x2, y2 = rect
    return frame[y1:y2, x1:x2]


def match_template(frame, template, region_rect, threshold, mask=None, pad=8):
    """在指定区域（向外扩一圈）内做模板匹配，可选掩码。

    **搜索区域必须比模板大**。模板按特征形状的包围盒裁出，若搜索区域也是同一个
    包围盒，`cv2.matchTemplate` 的结果会退化成 1×1——它只做了一次整图比对，没有
    任何滑动窗口。实测：画面平移 1 个像素，得分就从 1.0000 掉到 0.0028，直接判
    未命中。因此搜索区域向外扩 `pad` 像素，给匹配留出位移空间（实测扩 8px 可容忍
    1–8px 的位移，12px 才开始失效）。

    **掩码排除形状之外的像素**。圆形按钮的外接矩形四角是背景，不排掉会拉低置信度：
    实测同一张模板匹配「四角有干扰」的画面，无掩码 0.9221、圆形掩码 0.9918。

    **零方差模板必须拒绝**。`TM_CCOEFF_NORMED` 计算的是相关系数，模板方差为 0 时
    分母为 0，OpenCV 返回全 1.0——实测纯黑与纯白模板在完全不含它们的画面上
    `max()` 都是 1.0。若不拦，用户框选一块纯色区域当特征，该场景就会**永远命中**，
    而表现是「总是识别成这个场景」，比识别不到更难排查。

    Args：
        frame：基准分辨率的帧（BGR 或灰度）。
        template：模板图（与帧同色彩空间）。
        region_rect：形状包围盒的像素矩形。
        threshold：置信度下限。
        mask：与模板同尺寸的单通道掩码；None 表示全部像素参与匹配。
        pad：搜索区域向外扩的像素数。

    Returns：
        `(hit, confidence)` 二元组。尺寸不匹配、任一方为空、或模板零方差时返回
        `(False, 0.0)`——这些都不是错误，只是该特征无法判定。
    """
    if frame is None or template is None or region_rect is None:
        return False, 0.0
    import cv2

    if not template_has_texture(template):
        return False, 0.0

    frame_h, frame_w = frame.shape[:2]
    x1, y1, x2, y2 = region_rect
    sx1, sy1 = max(0, x1 - pad), max(0, y1 - pad)
    sx2, sy2 = min(frame_w, x2 + pad), min(frame_h, y2 + pad)
    search = frame[sy1:sy2, sx1:sx2]
    if search.size == 0 or template.size == 0:
        return False, 0.0
    if search.shape[0] < template.shape[0] or search.shape[1] < template.shape[1]:
        return False, 0.0
    if search.ndim != template.ndim:
        if search.ndim == 3 and template.ndim == 2:
            template = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)
        elif search.ndim == 2 and template.ndim == 3:
            search = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    if mask is not None:
        if mask.shape[:2] != template.shape[:2]:
            # 尺寸对不上时退化为不带掩码，而不是直接判失败：掩码是优化项，缺了
            # 只会降低精度，不会导致匹配错误。但要在返回里体现出来由调用方记录。
            mask = None
        elif len(mask.shape) == 3:
            mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    try:
        if mask is None:
            result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        else:
            result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED, mask=mask)
    except cv2.error:
        return False, 0.0
    if result.size == 0:
        return False, 0.0
    confidence = float(result.max())
    if not np.isfinite(confidence):
        # 相关系数无定义（搜索区方差为 0，典型是截错了窗口、画面纯色）。**必须归零**：
        # 直接把 nan/inf 传出去会让调用方求均值时整行变成 nan，界面显示 `nan%`，
        # 排查时看不出「其实是画面没内容」。归零的语义是「完全没有相似度证据」，
        # 与「算出 0」在判断上等价，但至少是一个能参与计算、能显示的数值。
        #
        # 两条路径都会出非有限值：无掩码时是 `nan`（0/0），带掩码时实测是 `inf`。
        return False, 0.0
    return confidence >= threshold, confidence


def template_has_texture(template, min_std=1.0):
    """判断模板是否有足够纹理可供模板匹配。

    Args：
        template：模板图（numpy 数组）。
        min_std：标准差下限，低于该值视为纯色。

    Returns：
        标准差达到下限时为 True。空图或非数组返回 False。
    """
    if template is None:
        return False
    if not isinstance(template, np.ndarray) or template.size == 0:
        return False
    return float(np.std(template)) >= min_std


def match_text(frame, expected, region_rect, threshold, ocr=None, pad=TEXT_PAD):
    """在指定区域内做文本匹配。

    裁剪后要过两道处理，**缺一道都会让部分场景识别不到**，而且失败方式都很难归因。

    **第一道：向外扩 `pad` 像素**，不能只裁特征框本身。实测「靶场入口」那块
    162×46 的区域（文字墨迹实际在 `y 82..120`，四边都有 3~5px 余量）：

    | 裁剪上边界 | 文字上方留空 | 结果 |
    |---|---|---|
    | y=82 | 0px | `'五'`、`'分'`（碎片） |
    | y=80 | 2px | 空结果 |
    | **y=79（特征框）** | **3px** | **`'TT'`** |
    | y=78 | 4px | `'靶场入口'` 0.998 |
    | y=74 | 8px | `'靶场入口'` 0.999 |

    原因是 DB 检测器的 `unclip`：它把检测到的文字区域向外扩张后作为识别输入，
    扩张后的框一旦超出图片边界就会被裁掉，导致文字区域不完整、检测失败。因此
    **特征框画得再准也没用**——文字离裁剪边界只要不足 4px 就会失效。

    **第二道：把图补齐到 32 的倍数**。检测器会把输入边长吸附到 32 的倍数，而它用的
    是 `round`：`resize_h = max(round(resize_h / 32) * 32, 32)`。`round` 有一半的
    情况会**向下**取整，把图压小：

    | 原高 | 吸附后 | 变化 |
    |---|---|---|
    | 40 | 32 | **压缩 20%** |
    | 46 | 32 | **压缩 30%** |
    | 47 | 32 | **压缩 32%** |
    | 48 | 64 | 放大 |

    压缩会把笔画糊在一起，识别直接失效。而且这跟 `pad` 是**互相独立**的：原框高
    49 外扩到 65，`round(65/32)=2` 仍会压到 64。实测 `round` 在 468 个高度里有
    **232 个（50%）会压缩**，靠调 `pad` 只能碰运气避开。

    我们自己按 `ceil` 补齐到 32 的倍数后，检测器的吸附就变成**恒等**（已验证
    1..500 全部成立，零例外），压缩从结构上不可能发生。代价是平均放大 1.18 倍、
    最坏 1.94 倍——换「永不压缩」这个确定性是值得的。

    Args：
        frame：基准分辨率的帧。
        expected：期望文本。
        region_rect：搜索区域像素矩形。
        threshold：相似度下限。
        ocr：可调用的 OCR 函数，签名 `ocr(frame, rect) -> list[str]`；
            为 None 时该特征按未命中处理（无 OCR 引擎时不应静默判命中）。
        pad：裁剪时向外扩的像素数。默认 8，与模板匹配的搜索区外扩一致。

    Returns：
        `(hit, confidence)` 二元组，置信度为最佳相似度。
    """
    if ocr is None or frame is None or region_rect is None or not expected:
        return False, 0.0
    frame_h, frame_w = frame.shape[:2]
    x1, y1, x2, y2 = region_rect
    padded = (
        max(0, x1 - pad),
        max(0, y1 - pad),
        min(frame_w, x2 + pad),
        min(frame_h, y2 + pad),
    )
    search = _crop(frame, padded)
    if search.size == 0:
        return False, 0.0
    search = _pad_to_multiple(search, OCR_SIDE_MULTIPLE)
    try:
        texts = ocr(search, padded) or []
    except Exception:  # noqa: BLE001 — OCR 引擎异常不应让整个识别失败
        return False, 0.0
    best = 0.0
    for text in texts:
        best = max(best, _similarity(expected, str(text)))
    return best >= threshold, best


def _pad_to_multiple(image, multiple):
    """把图的高与宽补齐到 `multiple` 的倍数（只补不裁）。

    检测器内部会把输入边长吸附到 32 的倍数，且用的是 `round`——一半的情况会向下
    取整把图压小（实测 468 个高度里 232 个被压缩，46 高会压到 32，压缩 30%），
    笔画糊在一起导致识别失效。这里先按 `ceil` 补齐，吸附就变成恒等，压缩不可能发生。

    **补边而不是缩放**：在右侧与下侧补原图的边缘像素（`BORDER_REPLICATE`），不改动
    已有像素。若改用 `cv2.resize` 拉伸到目标尺寸，文字会被拉伸变形，等于换了一个
    新问题——而且横向纵向拉伸比例不同时，字会被拉宽或压扁。

    Args：
        image：待处理的图（H×W×C 或 H×W）。
        multiple：目标倍数，通常为 32。

    Returns：
        补齐后的图。已满足倍数时原样返回（不复制）。

    Note:
        用 `BORDER_REPLICATE` 而不是补黑边：文字边缘外侧通常是与背景接近的颜色，
        复制边缘像素不会在图上造出一条突兀的黑线干扰检测。实测补黑边与复制边缘
        都能识别，但复制边缘更贴近原图分布。
    """
    if image is None or multiple <= 0:
        return image
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        return image
    target_h = max(multiple, -(-height // multiple) * multiple)
    target_w = max(multiple, -(-width // multiple) * multiple)
    if (target_h, target_w) == (height, width):
        return image
    try:
        import cv2
    except ModuleNotFoundError:
        return image
    try:
        return cv2.copyMakeBorder(
            image,
            0,
            target_h - height,
            0,
            target_w - width,
            cv2.BORDER_REPLICATE,
        )
    except Exception:  # noqa: BLE001 — 补边失败退回原图，不让 OCR 因此中断
        return image


def _similarity(expected, actual):
    """计算两段文本的相似度。

    用最长公共子序列占期望文本长度的比例，与参考实现的 lcs_percent 同源：
    对「结果文本比期望长」的场景（OCR 常把邻近文字一起识别出来）比编辑距离更宽容。

    Args：
        expected：期望文本。
        actual：实际识别文本。

    Returns：
        0.0–1.0 的浮点数。
    """
    if not expected:
        return 0.0
    if not actual:
        return 0.0
    if expected in actual:
        return 1.0
    expected_chars = list(expected)
    actual_chars = list(actual)
    previous = [0] * (len(actual_chars) + 1)
    for left in expected_chars:
        current = [0]
        for index, right in enumerate(actual_chars):
            if left == right:
                current.append(previous[index] + 1)
            else:
                current.append(max(previous[index + 1], current[index]))
        previous = current
    return previous[-1] / len(expected_chars)


def _evaluate_feature(feature, frame, base_width, base_height, template_loader, match_options):
    """判定单个特征是否命中。

    Args：
        feature：`model.Feature` 实例。
        frame：基准分辨率的帧。
        base_width：基准帧宽。
        base_height：基准帧高。
        template_loader：模板加载函数，签名 `loader(scene_id, feature_name) -> (模板, 掩码)`。
        match_options：`MatchOptions`，含 scene_id、ocr 与阈值覆盖。

    Returns：
        `(hit, confidence)` 二元组。
    """
    rect = _region_pixels(feature, base_width, base_height)
    if rect is None:
        return False, 0.0
    if feature.kind == model.KIND_TEMPLATE:
        template, mask = template_loader(match_options.scene_id, feature.name)
        # **掩码在保存时已算好**（`Feature.build_mask` = 掩码形状 ∧ 背景色），
        # 这里直接信任文件，不再现场叠加颜色。
        #
        # 为什么不在运行期再叠一次：同一套筛选逻辑放两处，文件里的掩码就无法
        # 代表真实行为——排查时看 mask 图会得出错误结论（本仓库确实踩过：
        # mask 文件是 97.87% 纯色，而识别其实已经排掉了 24.4% 的背景）。
        # 代价是手改 `scene.json` 的 bg_color 不会自动生效，必须重新保存场景；
        # 这个代价换来「文件即事实」，值得。
        return match_template(frame, template, rect, feature.threshold, mask=mask)
    # OCR 特征仍按形状包围盒裁出的区域做文本匹配：文本识别不需要掩码，
    # 掩码是给逐像素相关性计算用的。
    return match_text(frame, feature.text, rect, feature.threshold, ocr=match_options.ocr)


class MatchOptions:
    """一次识别调用的可选项。

    Attributes：
        scene_id: 当前正在判定的场景 id，供模板加载器定位目录。
        ocr: OCR 函数，签名 `ocr(frame, rect) -> list[str]`。
        stop_on_first: 是否在首个精确命中后停止扫描（默认 True）。
    """

    def __init__(self, scene_id="", ocr=None, stop_on_first=True):
        """构造可选项。

        Args：
            scene_id：场景 id。
            ocr：OCR 函数。
            stop_on_first：是否首个命中即停。
        """
        self.scene_id = scene_id
        self.ocr = ocr
        self.stop_on_first = stop_on_first


def evaluate_scene(scene, frame, base_width, base_height, template_loader, options=None):
    """判定一个场景是否命中。

    必需特征按区分度升序执行（区分度低者先跑），任一必需特征不中即返回，不再
    继续判其余特征——这就是「短路」的全部实现，也是最主要的开销节省手段。

    Args：
        scene：`model.Scene` 实例。
        frame：基准分辨率的帧。
        base_width：基准帧宽。
        base_height：基准帧高。
        template_loader：模板加载函数。
        options：`MatchOptions`；为 None 时按该场景 id 新建。

    Returns：
        `(hit, score, detail)` 三元组。`detail` 是
        `(scene_id, mandatory_hits, mandatory_total, optional_hits, missing,
        similarity, name)`。`name` 是场景显示名——界面要同时展示名称与 id，
        而明细只带 id 时界面得再查一次库，多一次 IO 且可能取到已删的场景。

        `similarity` 是**已评估特征置信度的平均值**（0–1），不是「整个场景的匹配
        概率」。短路的场景只评估了前几个特征，这个数字只代表那几个；一个特征都没
        评估成功（首个必需特征就失败）时它是那个失败特征的置信度。界面上据此排序
        与展示，需要完整诊断时用 :func:`diagnose_scene`。
    """
    if options is None:
        options = MatchOptions(scene_id=scene.scene_id)
    else:
        options.scene_id = scene.scene_id

    mandatory = scene.mandatory_features()
    optional = scene.optional_features()
    mandatory_hits = 0
    missing = None
    confidences = []
    for feature in mandatory:
        hit, confidence = _evaluate_feature(feature, frame, base_width, base_height, template_loader, options)
        confidences.append(confidence)
        if not hit:
            missing = feature.name
            break
        mandatory_hits += 1

    if missing is not None:
        # 必需特征没全中时不再评估可选特征（短路，省时间），但**可选总数照实带上**：
        # 界面要显示「可选命中 X/Y」，Y 是配置事实、与本次是否评估无关。缺了它
        # 界面只能显示一个孤零零的 0，用户会以为「可选特征一个都没命中」，
        # 而真实情况是「还没轮到评估」。
        detail = (
            scene.scene_id,
            mandatory_hits,
            len(mandatory),
            0,
            missing,
            _mean(confidences),
            scene.name,
            len(optional),
        )
        return False, 0.0, detail

    optional_hits = 0
    for feature in optional:
        hit, confidence = _evaluate_feature(feature, frame, base_width, base_height, template_loader, options)
        confidences.append(confidence)
        if hit:
            optional_hits += 1

    # 得分：必需特征全中记满分，可选特征按命中比例加成，用于在多候选间排序。
    score = 1.0
    if optional:
        score += optional_hits / len(optional)
    detail = (
        scene.scene_id,
        mandatory_hits,
        len(mandatory),
        optional_hits,
        None,
        _mean(confidences),
        scene.name,
        len(optional),
    )
    return True, score, detail


def _mean(values):
    """返回序列的平均值。

    Args：
        values：数值序列。

    Returns：
        平均值；空序列返回 0.0。
    """
    if not values:
        return 0.0
    return sum(values) / len(values)


def diagnose_scene(scene, frame, base_width, base_height, template_loader, options=None):
    """完整评估一个场景的**全部**特征（不短路），用于调试页给出可比的相似度。

    **只在调试页调用，绝不进运行期识别链路**。实测 50 场景 × 2 特征时，首中即停
    只要 4ms，而全量评估要 189ms——差 47 倍。为了在界面上显示一个更完整的数字
    而让每次导航判断都慢两个数量级，是不可接受的取舍。

    Args：
        scene：`model.Scene` 实例。
        frame：基准分辨率的帧。
        base_width：基准帧宽。
        base_height：基准帧高。
        template_loader：模板加载函数。
        options：`MatchOptions`；为 None 时按该场景 id 新建。

    Returns：
        `(similarity, hit_count, total, mandatory_hit, missing, optional_hit, missing_all)`
        七元组。前六项含义同 :func:`evaluate_scene`；`missing_all` 是**全部**未命中的
        必需特征名列表（全中时为空列表）。

        `similarity` 是全部特征置信度的平均值（含未命中的，它们的置信度会拉低
        均值，因此与「命中比例」是两种信息）；`hit_count` 是命中数（按各自阈值
        判定）；`total` 是特征总数；`mandatory_hit` 表示是否所有必需特征都命中；
        `missing` 是第一个未命中的必需特征名（全中时为 None）；`optional_hit`
        是**可选特征**里命中的个数。

    Note：
        `optional_hit` 单独返回而不是让调用方从 `hit_count` 里减：调用方拿不到
        「命中里哪些是可选特征」这个信息——必需特征也可能没全中。多返回一个数
        比让每个调用点各推一遍可靠。

        **`missing` 与 `missing_all` 并存，不是重复**：`missing` 保持「第一个失败的
        特征」，运行期的短路诊断与既有调用点依赖这个语义；而调试页需要看**全部**
        失败项——只显示第一个时，一个 3 特征全失败的行只能看到 1 个名字，用户会
        以为另外两个命中了。多列一个列表比让界面自己再算一遍可靠（界面拿不到
        「哪些是必需特征」以外的判定细节）。
    """
    if options is None:
        options = MatchOptions(scene_id=scene.scene_id)
    else:
        options.scene_id = scene.scene_id

    confidences = []
    hit_count = 0
    optional_hit = 0
    all_mandatory_hit = True
    missing = None
    missing_all = []
    for feature in scene.features:
        hit, confidence = _evaluate_feature(feature, frame, base_width, base_height, template_loader, options)
        confidences.append(confidence)
        if hit:
            hit_count += 1
            if not feature.mandatory:
                optional_hit += 1
        elif feature.mandatory:
            missing_all.append(feature.name)
            if missing is None:
                missing = feature.name
                all_mandatory_hit = False
    return (
        _mean(confidences),
        hit_count,
        len(scene.features),
        all_mandatory_hit,
        missing,
        optional_hit,
        missing_all,
    )


def recognize(frame, scenes, base_width, base_height, template_loader, options=None, cached_scene_id=""):
    """识别当前帧属于哪个场景。

    扫描顺序：先试缓存的上次场景（命中则省掉全扫），再按 id 顺序扫其余场景。
    首个精确命中即停——多个场景同时满足必需特征意味着库本身有歧义，此时按 id
    顺序取第一个并保持稳定，不随扫描顺序抖动。

    Args：
        frame：当前帧（任意分辨率，宽高比须与基准一致）。
        scenes：`model.Scene` 列表。
        base_width：基准帧宽。
        base_height：基准帧高。
        template_loader：模板加载函数。
        options：`MatchOptions`；OCR 与「是否首中即停」由此传入。
        cached_scene_id：上次识别的场景 id，优先尝试。

    Returns：
        `MatchResult` 实例。

    Raises：
        RecognizeError：帧为空、无场景，或宽高比与基准不一致。
    """
    if frame is None:
        raise RecognizeError("帧为空，无法识别场景")
    if not scenes:
        raise RecognizeError("场景库为空，无法识别场景")

    # 计时覆盖「归一化 + 全场景扫描」，**不含取帧与线程排队**：界面拿这个数字
    # 判断的是「识别本身快不快」。把取帧或排队算进来，同一台机器上会随执行器
    # 忙闲大幅抖动，反而看不出优化的效果。
    started = time.perf_counter()

    options = options or MatchOptions()
    normalized = normalize.normalize_frame(frame, base_width, base_height)
    did_resize = normalized is not frame

    ordered = list(scenes)
    if cached_scene_id:
        ordered.sort(key=lambda item: 0 if item.scene_id == cached_scene_id else 1)

    details = []
    hits = []
    for scene in ordered:
        hit, score, detail = evaluate_scene(scene, normalized, base_width, base_height, template_loader, options)
        details.append(detail)
        if hit:
            hits.append((scene, score))
            if options.stop_on_first:
                break

    if hits:
        hits.sort(key=lambda item: (-item[1], item[0].scene_id))
        return MatchResult(
            status=MATCH_EXACT,
            scene=hits[0][0],
            candidates=hits[1:],
            details=details,
            normalized=did_resize,
            duration=time.perf_counter() - started,
        )

    suspicious = _suspicious_candidates(details, scenes)
    return MatchResult(
        status=MATCH_SUSPICIOUS if suspicious else MATCH_NONE,
        scene=None,
        candidates=suspicious,
        details=details,
        normalized=did_resize,
        duration=time.perf_counter() - started,
    )


def _suspicious_candidates(details, scenes):
    """从判定明细中挑出「最接近命中」的场景。

    排序依据依次为：必需特征命中数（多者优先）、可选特征命中数（多者优先）。
    只返回前 `SUSPICIOUS_LIMIT` 个，且**排除必需特征全不中的场景**——一个必需
    特征都没中的场景与当前画面的关系不比随机更近，列出来只会分散注意力。

    Args：
        details：`(scene_id, mandatory_hits, mandatory_total, optional_hits, missing,
            similarity)` 列表。
        scenes：`model.Scene` 列表，用于把 id 映射回实例。

    Returns：
        `(scene, score)` 列表，按相关度降序。
    """
    by_id = {scene.scene_id: scene for scene in scenes}
    ranked = []
    for item in details:
        scene_id, mandatory_hits, mandatory_total, optional_hits = item[:4]
        if mandatory_hits <= 0:
            continue
        scene = by_id.get(scene_id)
        if scene is None:
            continue
        progress = mandatory_hits / mandatory_total if mandatory_total else 0.0
        ranked.append((scene, progress, optional_hits))
    ranked.sort(key=lambda item: (-item[1], -item[2], item[0].scene_id))
    return [(scene, progress) for scene, progress, _optional in ranked[:SUSPICIOUS_LIMIT]]


def mask_overlap_warnings(scene, mask_rect_px, base_width, base_height):
    """检查场景内特征与截图遮罩矩形是否重叠。

    重叠意味着该特征的模板在交集内是纯色块（遮罩会把落盘截图涂黑），匹配必然
    失败，而失效表现为「这个特征从来没命中过」，不会报错。因此配置时就要提示。

    Args：
        scene：`model.Scene` 实例。
        mask_rect_px：遮罩在基准分辨率下的像素矩形 `(x1, y1, x2, y2)`；
            遮罩关闭或完全在画面外时传 None。
        base_width：基准帧宽。
        base_height：基准帧高。

    Returns：
        `(feature_name, intersection, ratio)` 列表，按占比降序；无重叠时为空。
    """
    if mask_rect_px is None:
        return []
    warnings = []
    for feature in scene.features:
        rect = _region_pixels(feature, base_width, base_height)
        if rect is None:
            continue
        overlaps, intersection = normalize.rects_intersect(rect, mask_rect_px)
        if not overlaps:
            continue
        ratio = normalize.intersection_ratio(rect, mask_rect_px)
        warnings.append((feature.name, intersection, ratio))
    warnings.sort(key=lambda item: (-item[2], item[0]))
    return warnings
