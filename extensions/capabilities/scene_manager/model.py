# -*- coding: utf-8 -*-
"""场景管理的数据模型与 JSON 序列化。

本模块**不导入 `ok` 与 `PySide6`**：场景库是全部换算与判定的输入，必须能在
无 GUI、无设备的测试环境里完整验证。混入运行依赖后，最需要精确验证的部分反而
必须装齐依赖才能测。

坐标系口径（全模块统一）：

- **特征区域与交互区域一律存相对比例**（0.0–1.0），基准是基准分辨率下的帧宽/帧高。
  存比例而非像素，是因为分辨率变化时比例天然可迁移，而像素需要逐处换算。
- **模板图是像素资产**，其尺寸等于在基准分辨率下裁出的真实像素。库级基准分辨率
  记录在 `library.json`，识别时把帧缩放到该尺寸后直接匹配，模板不缩放。
- 矩形一律用 `(x1, y1, x2, y2)` 四元组，右下开区间，与 numpy 切片一致。

**交互区域是「一块画面区域」，不是「一个点击动作」**。区域后续可能被用来单击、
双击、长按、拖拽、滚轮滚动等等，具体做什么由**使用方**决定；场景管理只记录
「这块区域在哪」「操作后可能到哪个场景」，不解释也不实现动作类型。因此模型里
没有 `action` 字段——一旦加上，场景管理就被迫对动作种类做枚举判断，新增一种动作
就要改场景库结构。唯一的例外是探索：探索自身只做单击（这是探索的实现手段，不是
区域的属性），由 `Area.explore_probe` 这个**探索标记**控制是否允许被探。

边（跳转关系）不单独存储：一条边由某个交互区域的 `target_scene` 表达。
这样「区域操作完到哪个场景」只有一处记录，不会出现「区域说有边、边表说没有」的矛盾。
"""

import re
from dataclasses import dataclass, field

from extensions.capabilities.scene_manager import mask_shape, shapes

# 场景库的 JSON schema 版本。
#
# 只用于**向前**防御：读到比本程序版本更高的库时明确报错，而不是按当前字段结构
# 硬解——那会把新字段静默丢弃，再存回去就把数据改坏了。不承担向后的兼容读取。
SCHEMA_VERSION = 1

# 特征手段。模板匹配走 `find_feature(template=ndarray)`，OCR 走 `task.ocr()`。
KIND_TEMPLATE = "template"
KIND_OCR = "ocr"
FEATURE_KINDS = (KIND_TEMPLATE, KIND_OCR)

# 边的来源。寻路只走 MANUAL 与 CONFIRMED：探索产物未经人工确认不得参与寻路。
SOURCE_MANUAL = "manual"
SOURCE_EXPLORED = "explored"
SOURCE_CONFIRMED = "confirmed"
EDGE_SOURCES = (SOURCE_MANUAL, SOURCE_EXPLORED, SOURCE_CONFIRMED)
# 允许参与寻路的来源。
ROUTABLE_SOURCES = (SOURCE_MANUAL, SOURCE_CONFIRMED)

# 探索的队列顺序。
#
# - `bfs`：先进先出。先把当前层的场景全部展开，再看下一层，适合「画面之间
#   的层级关系清楚」的库，探索结果按层排列，容易核对有没有漏。
# - `dfs`：后进先出。探到一个新场景就立刻展开它，沿一条链路走到头再回头，
#   适合「链式流程」（开始 → 关卡 → 战斗 → 结算），能更快把一条完整链路
#   探通，但过程跳转更频繁。
SEARCH_BFS = "bfs"
SEARCH_DFS = "dfs"
SEARCH_ORDERS = (SEARCH_BFS, SEARCH_DFS)

# 探索时对区域执行的动作。**只影响探索**，不参与识别与寻路——区域在别处怎么用
# 由调用方决定（见 `Area` 的类文档）。默认单击，因为多数界面元素单击即可。
ACTION_CLICK = "click"
ACTION_DOUBLE_CLICK = "double_click"
ACTION_LONG_PRESS = "long_press"
ACTION_SWIPE = "swipe"
ACTION_RIGHT_CLICK = "right_click"
EXPLORE_ACTIONS = (ACTION_CLICK, ACTION_DOUBLE_CLICK, ACTION_LONG_PRESS, ACTION_SWIPE, ACTION_RIGHT_CLICK)

# 滑动方向。方向 + 距离共同决定滑动手势，缺一不可。
SWIPE_UP = "up"
SWIPE_DOWN = "down"
SWIPE_LEFT = "left"
SWIPE_RIGHT = "right"
SWIPE_DIRECTIONS = (SWIPE_UP, SWIPE_DOWN, SWIPE_LEFT, SWIPE_RIGHT)

# 动作的**数值参数**规格：`{动作: {参数名: (默认值, 下限, 上限)}}`。
#
# 为什么参数放在独立的 `explore_params` 字典里、而不是给每种动作加一个字段：动作
# 种类会继续扩展（拖拽、滚轮），每加一种就往 `Area` 上添字段会让数据结构无限膨胀，
# 而它们的取值又必须各自校验。用「动作 → 参数规格」一张表，新增动作只改这张表。
#
# 没有列出的动作（单击、右键）不需要参数。
ACTION_NUMERIC_PARAMS = {
    ACTION_DOUBLE_CLICK: {"interval": (0.08, 0.01, 1.0)},
    ACTION_LONG_PRESS: {"duration": (1.0, 0.1, 10.0)},
    # 距离是相对**区域所在帧**的短边比例：0.3 表示滑动短边的 30%。用比例而不是
    # 像素，换分辨率后手势幅度不会变。
    ACTION_SWIPE: {"distance": (0.3, 0.05, 0.9)},
}

# 动作的**枚举参数**规格：`{动作: {参数名: (默认值, 允许值)}}`。
ACTION_ENUM_PARAMS = {
    ACTION_SWIPE: {"direction": (SWIPE_UP, SWIPE_DIRECTIONS)},
}

# 场景 id 的形态：`scene-` + 四位以上数字。稳定、可排序、与显示名解耦。
SCENE_ID_PATTERN = re.compile(r"^scene-(\d{4,})$")
# 草稿 id 的形态。草稿与正式场景共用同一套数据类，靠前缀区分归属。
DRAFT_ID_PATTERN = re.compile(r"^draft-(\d{4,})$")

# 相对比例保留的小数位数。六位在 4K 宽度下仍有亚像素精度，又不会在 JSON 里
# 留下浮点尾巴（同一矩形反复保存不会因表示误差漂移）。
_RATIO_DIGITS = 6


class SceneModelError(ValueError):
    """场景数据结构非法。"""


def is_valid_scene_id(scene_id):
    """判断 id 是否是合法的场景或草稿 id。

    Args：
        scene_id：待判断的 id。

    Returns：
        匹配 `scene-NNNN` 或 `draft-NNNN` 时为 True。
    """
    if not scene_id:
        return False
    return bool(SCENE_ID_PATTERN.match(scene_id) or DRAFT_ID_PATTERN.match(scene_id))


def is_draft_id(scene_id):
    """判断 id 是否属于草稿区。

    Args：
        scene_id：待判断的 id。

    Returns：
        匹配 `draft-NNNN` 时为 True。
    """
    return bool(DRAFT_ID_PATTERN.match(scene_id or ""))


def normalize_action_params(action, params=None):
    """按动作种类校验参数并补齐默认值。

    Args：
        action：动作 key，取 `EXPLORE_ACTIONS`。
        params：调用方给的参数字典；None 或空表示全用默认值。

    Returns：
        `{参数名: 值}` 新字典，只含该动作声明的参数。

    Raises：
        SceneModelError：未知动作，或某个参数超出允许范围。

    Note：
        **只保留该动作声明的参数**：从长按切到滑动时，旧的 `duration` 不能留在
        字典里——它会跟着一起落盘，下次读出来看着像「滑动也有个 duration」，而
        实际不生效。多余键直接丢弃比报错友好：用户在对话框里改动作不该被拦下。

        未知参数同样丢弃而不是报错，理由同上；已知参数的**取值非法**才报错，因为
        那通常意味着配置文件被手改坏了，静默纠正会掩盖问题。
    """
    numeric = ACTION_NUMERIC_PARAMS.get(action, {})
    enum = ACTION_ENUM_PARAMS.get(action, {})
    given = params or {}
    result = {}
    for key, (default, low, high) in numeric.items():
        try:
            value = float(given.get(key, default))
        except (TypeError, ValueError) as error:
            raise SceneModelError(f"动作参数 {key} 必须是数值，收到 {given.get(key)!r}") from error
        if not low <= value <= high:
            raise SceneModelError(f"动作参数 {key} 必须落在 [{low}, {high}]，收到 {value}")
        result[key] = round(value, 4)
    for key, (default, allowed) in enum.items():
        value = given.get(key, default)
        if value not in allowed:
            raise SceneModelError(f"动作参数 {key} 只支持 {allowed}，收到 {value!r}")
        result[key] = value
    return result


def action_param_spec(action):
    """返回某动作的参数规格，供界面生成输入控件。

    Args：
        action：动作 key。

    Returns：
        `(numeric, enum)` 二元组：前者 `{名: (默认, 下限, 上限)}`，后者
        `{名: (默认, 允许值)}`。无参数的动作返回两个空字典。
    """
    return ACTION_NUMERIC_PARAMS.get(action, {}), ACTION_ENUM_PARAMS.get(action, {})


def id_sequence(scene_id):
    """从场景或草稿 id 里取出数字序号。

    用于让「显示名」与「id」对上：`scene-0005` 叫「场景 5」。**名称不能自己维护
    计数器**——id 由库级计数器分配、名称若另起一套（如「现有 场景N 的最大 N+1」），
    用户手动改名后两套就会错位，而且错位之后永远追不回来。

    Args：
        scene_id：形如 `scene-0005` 或 `draft-0012` 的 id。

    Returns：
        整数序号；格式不合法时返回 0。
    """
    for pattern in (SCENE_ID_PATTERN, DRAFT_ID_PATTERN):
        matched = pattern.match(scene_id or "")
        if matched:
            return int(matched.group(1))
    return 0


def round_ratio(value):
    """把相对比例规整到固定小数位。

    Args：
        value：原始比例值。

    Returns：
        规整后的浮点数。

    Raises：
        SceneModelError：值不是数值。
    """
    try:
        return round(float(value), _RATIO_DIGITS)
    except (TypeError, ValueError) as error:
        raise SceneModelError(f"相对比例必须是数值，收到 {value!r}") from error


def normalize_rect(rect):
    """校验并规整一个相对比例矩形。

    Args：
        rect：长度为 4 的序列，依次为 x1、y1、x2、y2。

    Returns：
        `(x1, y1, x2, y2)` 浮点元组，已规整小数位。

    Raises：
        SceneModelError：长度不是 4、超出 0–1、或退化成零面积。
    """
    if rect is None or len(rect) != 4:
        raise SceneModelError(f"矩形必须是 4 个数值，收到 {rect!r}")
    values = tuple(round_ratio(item) for item in rect)
    for item in values:
        if item < 0.0 or item > 1.0:
            raise SceneModelError(f"矩形分量必须落在 0–1，收到 {values}")
    x1, y1, x2, y2 = values
    if x2 <= x1 or y2 <= y1:
        raise SceneModelError(f"矩形面积必须为正（x2>x1 且 y2>y1），收到 {values}")
    return values


@dataclass
class Feature:
    """场景的一个识别特征。

    **特征 = 一个形状 + 一张模板 + 一张掩码**。形状决定「哪块像素参与匹配」，
    掩码把形状之外的像素排除掉。

    为什么不只存矩形：圆形按钮的外接矩形四角是背景。实测同一张模板匹配「四角有
    干扰元素」的画面，不带掩码得分 0.9221，带圆形掩码 0.9918——不排掉四角内容，
    置信度会被无关像素拉低。

    **搜索区域由形状派生，不单独存储**（:meth:`region`）：另存一个 region 字段会
    与形状分叉——改了形状忘了改 region，模板就与搜索区对不上，而表现是「这个特征
    从来匹配不到」，不报错。

    Attributes：
        name: 特征名，场景内唯一，同时是模板文件名主干。
        kind: 识别手段，取 `FEATURE_KINDS` 之一。
        mandatory: 是否必需。必需特征之间是 AND 关系，缺一即整个场景不命中。
        shape: `shapes.Shape` 实例，描述参与匹配的区域几何。
        threshold: 匹配阈值（模板匹配的置信度下限 / OCR 的文本相似度下限）。
        text: OCR 期望文本，仅 `KIND_OCR` 使用。
        template_file: 模板图相对场景目录的路径，仅 `KIND_TEMPLATE` 使用。
        mask_file: 掩码图相对场景目录的路径，仅 `KIND_TEMPLATE` 使用。
        bg_color: 背景色 `(b, g, r)`，仅 `KIND_TEMPLATE` 使用。非 None 时，与该
            颜色相近的像素**不参与匹配**（由 :meth:`Feature.combine_mask` 与形状
            掩码叠加）。None 表示不按颜色排除。
        bg_tolerance: 背景色匹配容差（每通道最大差值），仅 `bg_color` 非 None 时
            生效。
        mask_shape: `mask_shape.MaskShape` 实例，描述**模板内部**哪些像素参与
            匹配。坐标相对特征包围盒（不是整帧），与 `shape` 的坐标系不同——
            详见 `mask_shape` 模块文档。
    """

    name: str
    kind: str = KIND_TEMPLATE
    mandatory: bool = True
    shape: shapes.Shape = None
    threshold: float = 0.8
    text: str = ""
    template_file: str = ""
    mask_file: str = ""
    bg_color: tuple | None = None
    bg_tolerance: int = 4
    mask_shape: object = None

    def __post_init__(self):
        """校验字段并补齐默认文件名。"""
        if not self.name:
            raise SceneModelError("特征名不能为空")
        if self.kind not in FEATURE_KINDS:
            raise SceneModelError(f"未知的特征手段 {self.kind!r}，只支持 {FEATURE_KINDS}")
        if self.shape is None:
            # 全画面矩形：只在代码里构造临时特征时出现（如测试桩），落盘前必须换成
            # 真实形状，否则模板会按整帧裁，体积与耗时都失控。
            self.shape = shapes.Shape.rectangle(0.0, 0.0, 1.0, 1.0)
        elif not isinstance(self.shape, shapes.Shape):
            raise SceneModelError(f"shape 必须是 Shape 实例，收到 {type(self.shape).__name__}")
        try:
            self.threshold = round(float(self.threshold), 4)
        except (TypeError, ValueError) as error:
            raise SceneModelError(f"阈值必须是数值，收到 {self.threshold!r}") from error
        if not 0.0 < self.threshold <= 1.0:
            raise SceneModelError(f"阈值必须落在 (0, 1]，收到 {self.threshold}")
        if self.kind == KIND_OCR and not self.text:
            raise SceneModelError(f"OCR 特征 {self.name!r} 必须填写期望文本")
        if self.kind == KIND_TEMPLATE:
            if not self.template_file:
                self.template_file = f"templates/{self.name}.png"
            if not self.mask_file:
                self.mask_file = f"templates/{self.name}_mask.png"
        if self.bg_color is not None:
            self.bg_color = self._normalize_bg_color(self.bg_color)
            try:
                self.bg_tolerance = int(round(float(self.bg_tolerance)))
            except (TypeError, ValueError) as error:
                raise SceneModelError(f"背景色容差必须是数值，收到 {self.bg_tolerance!r}") from error
            if not 0 <= self.bg_tolerance <= 255:
                raise SceneModelError(f"背景色容差必须落在 0–255，收到 {self.bg_tolerance}")
        else:
            self.bg_tolerance = 0
        if self.mask_shape is None:
            # 缺省「无形状」：升级前保存的场景没有这个字段，它们的正确语义就是
            # 「没有掩码形状」。兜底成矩形会改变这些场景原有的识别行为，
            # 而那种改变不报错、只表现为识别率变化，极难归因。
            self.mask_shape = mask_shape.MaskShape.none()
        elif isinstance(self.mask_shape, dict):
            self.mask_shape = mask_shape.MaskShape.from_dict(self.mask_shape)
        elif not isinstance(self.mask_shape, mask_shape.MaskShape):
            raise SceneModelError(f"mask_shape 必须是 MaskShape 实例，收到 {type(self.mask_shape).__name__}")

    @staticmethod
    def _normalize_bg_color(value):
        """把背景色规整成 `(b, g, r)` 三元组。

        Args：
            value：长度为 3 的序列，元素为 0–255 的整数或整值浮点。

        Returns：
            `(b, g, r)` 整数元组。

        Raises：
            SceneModelError：长度不对或分量越界。

        Note：
            分量顺序是 **BGR**，与 OpenCV 读出的数组一致，避免存盘与读盘之间
            反复交换通道。界面上显示 RGB 由界面层自己反转。
        """
        try:
            channels = list(value)
        except TypeError as error:
            raise SceneModelError(f"背景色必须是三元组，收到 {value!r}") from error
        if len(channels) != 3:
            raise SceneModelError(f"背景色必须是三元组，收到 {value!r}")
        result = []
        for channel in channels:
            try:
                number = int(round(float(channel)))
            except (TypeError, ValueError) as error:
                raise SceneModelError(f"背景色分量必须是数值，收到 {channel!r}") from error
            if not 0 <= number <= 255:
                raise SceneModelError(f"背景色分量必须落在 0–255，收到 {number}")
            result.append(number)
        return tuple(result)

    def build_mask(self, template):
        """按「掩码形状 ∧ 背景色」生成最终掩码。

        Args：
            template：模板图（BGR 或灰度数组），用于取尺寸与实际像素。

        Returns：
            单通道 uint8 掩码（255 参与 / 0 排除）；两项都未启用时返回 None。

        Note：
            这是**掩码的唯一生成入口**，保存场景时用它写 `_mask.png`，运行期不再
            重复叠加。此前保存时只写形状掩码、颜色靠运行期现算，导致落盘的
            `_mask.png` 与真实参与匹配的像素不一致——排查时看 mask 图会得出错误
            结论，而两份逻辑也迟早分叉（本仓库确实分叉过：mask 文件是 97.87%
            纯色，而识别其实已经排掉了 24.4% 的背景）。

            返回 None 表示「全部像素参与」，调用方应把掩码文件删掉或传 None 给
            `cv2.matchTemplate`，而不是写一张全 255 的图——两者语义相同，但后者
            会让「这个特征有没有掩码」变得看不出来。
        """
        import numpy as np

        if template is None or getattr(template, "size", 0) == 0:
            return None
        height, width = template.shape[:2]
        shape_mask = self.mask_shape.mask(width, height) if self.mask_shape is not None else None
        color_mask = self._color_mask(template)
        if shape_mask is None:
            return color_mask
        if color_mask is None:
            return shape_mask
        if shape_mask.shape[:2] != color_mask.shape[:2]:
            # 尺寸对不上说明两者不同源；以形状掩码为准（几何是显式配置的），
            # 但这种情况本身就是缺陷，界面保存时应当已经拦住。
            return shape_mask
        return np.minimum(shape_mask, color_mask)

    def _color_mask(self, template):
        """按背景色生成掩码。

        Args：
            template：模板图。

        Returns：
            单通道 uint8 掩码；未启用颜色剔除或模板不可用时返回 None。

        Note：
            匹配判据是**每通道差值都 ≤ 容差**，不是欧氏距离：后者会让「纯黑与
            纯白」这类高对比色对落进同一个球形邻域（半径 221 的球能同时包住
            两者），在深色 UI 上把关键前景一起误排除。
        """
        if self.bg_color is None:
            return None
        import numpy as np

        if template is None or getattr(template, "size", 0) == 0:
            return None
        color = np.array(self.bg_color, dtype=np.int16)
        diff = np.abs(template[..., :3].astype(np.int16) - color)
        background = np.all(diff <= self.bg_tolerance, axis=2)
        return np.where(background, 0, 255).astype(np.uint8)

    def combine_mask(self, shape_mask, template):
        """把形状掩码与背景色排除合并成最终掩码（运行期兼容入口）。

        Args：
            shape_mask：形状掩码（单通道 uint8），与 `template` 前两维一致。
            template：模板图。

        Returns：
            新的单通道掩码。

        Note：
            **运行期已不再调用本方法**：掩码在保存时由 :meth:`build_mask` 一次
            算好并落盘，识别链路直接用文件里的 mask。保留本方法是为了兼容
            手写 `scene.json` 后未重新保存的场景——那种情况下文件的 mask 与配置
            不一致，靠这一步现场纠正，好过直接信任过期文件。
        """
        import numpy as np

        color_mask = self._color_mask(template)
        if color_mask is None:
            return shape_mask
        if shape_mask is None:
            return color_mask
        if shape_mask.shape[:2] != color_mask.shape[:2]:
            return shape_mask
        return np.minimum(shape_mask, color_mask)

    def region(self, width, height):
        """返回形状包围盒的相对比例形式，供搜索区域使用。

        Args：
            width：基准帧宽。
            height：基准帧高。

        Returns：
            `(x1, y1, x2, y2)` 相对比例。
        """
        return self.shape.region(width, height)

    def pixel_rect(self, width, height):
        """返回形状包围盒的像素矩形。

        Args：
            width：基准帧宽。
            height：基准帧高。

        Returns：
            `(x1, y1, x2, y2)` 整数像素矩形。
        """
        return self.shape.pixel_bbox(width, height)

    def to_dict(self):
        """序列化为可写入 JSON 的字典。

        Returns：
            字段与 :meth:`from_dict` 对称的字典。
        """
        return {
            "name": self.name,
            "kind": self.kind,
            "mandatory": self.mandatory,
            "shape": self.shape.to_dict(),
            "threshold": self.threshold,
            "text": self.text,
            "template_file": self.template_file,
            "mask_file": self.mask_file,
            "bg_color": list(self.bg_color) if self.bg_color else None,
            "bg_tolerance": self.bg_tolerance,
            "mask_shape": self.mask_shape.to_dict(),
        }

    @classmethod
    def from_dict(cls, data):
        """从字典还原特征。

        Args：
            data：`to_dict` 产出的字典。

        Returns：
            `Feature` 实例。

        Raises：
            SceneModelError：缺少 `shape` 字段。不按全画面矩形兜底——那样模板会按
                整帧裁剪，一个特征一张 23.7MB 的图，启动即卡住，而表现是「打开很慢」
                而不是「数据不对」。
        """
        if "shape" not in data:
            raise SceneModelError(f"特征 {data.get('name', '')!r} 缺少 shape 字段，场景数据不完整")
        return cls(
            name=data.get("name", ""),
            kind=data.get("kind", KIND_TEMPLATE),
            mandatory=data.get("mandatory", True),
            shape=shapes.Shape.from_dict(data.get("shape")),
            threshold=data.get("threshold", 0.8),
            text=data.get("text", ""),
            template_file=data.get("template_file", ""),
            mask_file=data.get("mask_file", ""),
            bg_color=data.get("bg_color"),
            bg_tolerance=data.get("bg_tolerance", 4),
            mask_shape=mask_shape.MaskShape.from_dict(data.get("mask_shape")),
        )


@dataclass
class Area:
    """场景中的一块**交互区域**。

    这里刻意叫「区域」而不是「按钮」或「点击目标」：同一块区域后续可能被用来
    单击、双击、长按、拖拽、滚轮滚动等等，**具体做什么由使用方决定**，场景管理
    不关心、也不记录动作类型。因此本类没有 `action` 字段——一旦加上，每新增一种
    动作都要改场景库结构，而场景管理对动作语义并无判断依据。

    **本类只描述「这个画面上的这块区域在哪」，不描述它通向哪里**。跳转关系属
    「画面之间的关系」，存在库根的 `routes.json`（见 `routes` 模块），键为
    `(scene_id, area.name)`。

    为什么把跳转从区域里搬走：原先 `target_scene` / `after_wait` / `edge_source`
    都挂在区域上，于是「点这个按钮会到哪」这件事同时存在于场景文件与流转图两处。
    改一处漏一处就分叉，而分叉的表现是「流转图上有一条边，点下去到不了」——
    不报错。现在场景文件只回答「画面长什么样」，路由表只回答「画面之间怎么走」。

    Attributes：
        name: 区域名，场景内唯一。
        rect: 区域矩形，相对比例 `(x1, y1, x2, y2)`；需要坐标点时取中心。
        explore_probe: **探索专用标记**，表示「允许自动探索探测这块区域」。
            它与「区域是什么」无关，只是探索这一种使用方式的安全开关：默认 False，
            因为误点可能花资源、进战斗，且不可逆，必须由用户显式打开。
        explore_return: **探索专用标记**，表示「回退时优先点这块区域」。
            与 `explore_probe` 独立：没开探测的区域也能被标为返回（回退会点它，
            但正常展开阶段不会）。一个场景可以标多个，探索时按「能否到达本次
            目标」排序后依次尝试；一个都不标则沿用入边反推。
        explore_action: **探索时用哪种动作**，取 `EXPLORE_ACTIONS` 之一。默认单击。
            滑动列表、需要长按蓄力的按钮、右键菜单——这些用单击探测会得到错误结论
            （画面不变化或变成另一个功能），而探索据此写出的边是错的。
        explore_params: 该动作的额外参数，只在与动作种类匹配时生效。按
            `ACTION_NUMERIC_PARAMS` / `ACTION_ENUM_PARAMS` 校验并补默认值；切换
            动作时旧参数会被丢弃，避免把长按的 `duration` 带到滑动上。
    """

    name: str
    rect: tuple = (0.0, 0.0, 0.0, 0.0)
    explore_probe: bool = False
    explore_return: bool = False
    explore_action: str = ACTION_CLICK
    explore_params: dict = field(default_factory=dict)

    def __post_init__(self):
        """校验字段并规整矩形。"""
        if not self.name:
            raise SceneModelError("区域名不能为空")
        self.rect = normalize_rect(self.rect)
        if self.explore_action not in EXPLORE_ACTIONS:
            raise SceneModelError(f"未知的探索动作 {self.explore_action!r}，只支持 {EXPLORE_ACTIONS}")
        self.explore_params = normalize_action_params(self.explore_action, self.explore_params)

    def to_dict(self):
        """序列化为可写入 JSON 的字典。

        Returns：
            字段与 :meth:`from_dict` 对称的字典。
        """
        return {
            "name": self.name,
            "rect": list(self.rect),
            "explore_probe": self.explore_probe,
            "explore_return": self.explore_return,
            "explore_action": self.explore_action,
            "explore_params": dict(self.explore_params),
        }

    @classmethod
    def from_dict(cls, data):
        """从字典还原区域。

        Args：
            data：`to_dict` 产出的字典。

        Returns：
            `Area` 实例。

        Note：
            旧库在区域上还带着 `target_scene` / `after_wait` / `edge_source` /
            `observations`。这里**读出来但不使用**——迁移由 `store` 在加载库时
            一次性搬到路由表，之后保存就把旧字段丢掉。读取时必须容忍这些字段
            存在，否则老库直接打不开。
        """
        return cls(
            name=data.get("name", ""),
            rect=data.get("rect", (0.0, 0.0, 0.0, 0.0)),
            explore_probe=bool(data.get("explore_probe", False)),
            explore_return=bool(data.get("explore_return", False)),
            # 旧库没有这两个字段：动作默认单击、参数为空，由 `__post_init__` 按
            # 动作补齐默认值。这样老数据不需要迁移就能读。
            explore_action=data.get("explore_action", ACTION_CLICK),
            explore_params=data.get("explore_params") or {},
        )


@dataclass
class Scene:
    """一个画面状态。

    Attributes：
        scene_id: 稳定 id（`scene-0001` 形态），所有边引用它，与显示名解耦。
        name: 显示名，可随时改名，改名不影响任何边。
        features: 识别特征列表。
        areas: 交互区域列表。

    Note：
        旧库有 `return_area` / `created_at` / `updated_at` 三个字段：前者的语义
        已改为区域的 `explore_return`（读取时迁移），后两者从未有写入点，直接丢弃。
        `from_dict` 仍容忍它们存在，否则老库打不开。
    """

    scene_id: str
    name: str = ""
    features: list = field(default_factory=list)
    areas: list = field(default_factory=list)

    def __post_init__(self):
        """校验 id 与字段。"""
        if not is_valid_scene_id(self.scene_id):
            raise SceneModelError(f"场景 id 必须形如 scene-0001 或 draft-0001，收到 {self.scene_id!r}")
        if not self.name:
            self.name = self.scene_id
        self._check_unique(self.features, "特征")
        self._check_unique(self.areas, "区域")

    def is_draft(self):
        """返回本场景是否属于草稿区。

        草稿与正式场景共用同一数据类：结构完全一致，草稿转正只需换 id 与搬目录。
        分成两个类会让「保存/加载/导入导出」各写两份，而它们的结构永远同步变化。

        Returns：
            草稿 id 时为 True。
        """
        return bool(DRAFT_ID_PATTERN.match(self.scene_id or ""))

    @staticmethod
    def _check_unique(items, label):
        """检查名称在列表内唯一。

        Args：
            items：特征或区域列表。
            label：错误消息中使用的名称。

        Raises：
            SceneModelError：出现重名。
        """
        seen = set()
        for item in items:
            if item.name in seen:
                raise SceneModelError(f"场景内{label}名重复：{item.name!r}")
            seen.add(item.name)

    def mandatory_features(self):
        """返回必需特征，按名称排序（保证每次顺序一致）。

        Returns：
            `Feature` 列表。

        Note：
            原先按「区分度」排序，区分度来自 `hit_count / attempt_count`；那两个
            字段从来没有写入点，恒为 0，排序结果实际就是按名称。字段删除后直接
            按名称排，行为不变，也少了两处永远为空的落盘字段。
        """
        return sorted(
            (item for item in self.features if item.mandatory),
            key=lambda item: item.name,
        )

    def optional_features(self):
        """返回可选特征，按名称排序以保证结果稳定。

        Returns：
            `Feature` 列表。
        """
        return sorted(
            (item for item in self.features if not item.mandatory),
            key=lambda item: item.name,
        )

    def find_feature(self, name):
        """按名称查找特征。

        Args：
            name：特征名。

        Returns：
            `Feature` 实例；不存在时返回 None。
        """
        for item in self.features:
            if item.name == name:
                return item
        return None

    def find_area(self, name):
        """按名称查找区域。

        Args：
            name：区域名。

        Returns：
            `Area` 实例；不存在时返回 None。
        """
        for item in self.areas:
            if item.name == name:
                return item
        return None

    def explore_return_areas(self):
        """返回标记为「探索返回」的区域，按场景内顺序。

        Returns：
            `Area` 列表；没有标记时为空列表（调用方按入边反推）。
        """
        return [item for item in self.areas if item.explore_return]

    def to_dict(self):
        """序列化为可写入 JSON 的字典。

        Returns：
            含 `schema_version` 的完整场景字典。
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.scene_id,
            "name": self.name,
            "features": [item.to_dict() for item in self.features],
            "areas": [item.to_dict() for item in self.areas],
        }

    @classmethod
    def from_dict(cls, data):
        """从字典还原场景。

        Args：
            data：`to_dict` 产出的字典。

        Returns：
            `Scene` 实例。

        Raises：
            SceneModelError：缺少 id 或结构非法。

        Note：
            旧库的 `return_area`（场景级单值）在这里迁移成区域的
            `explore_return`：名称能对上某个区域就把它标上。只读兼容，不再回写。
        """
        if not isinstance(data, dict):
            raise SceneModelError(f"场景数据必须是字典，收到 {type(data).__name__}")
        version = data.get("schema_version", SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise SceneModelError(f"场景数据版本 {version} 高于本程序支持的 {SCHEMA_VERSION}，请升级程序")
        scene = cls(
            scene_id=data.get("id", ""),
            name=data.get("name", ""),
            features=[Feature.from_dict(item) for item in data.get("features", [])],
            areas=[Area.from_dict(item) for item in data.get("areas", [])],
        )
        legacy_return = data.get("return_area") or ""
        if legacy_return:
            area = scene.find_area(legacy_return)
            if area is not None:
                area.explore_return = True
        return scene


@dataclass
class LibraryMeta:
    """场景库的库级元数据。

    基准分辨率是唯一的：识别时把当前帧缩放到该尺寸后一次，所有场景复用同一帧。
    模板图按该尺寸裁出，因此不缩放模板。

    `next_scene_seq` / `next_draft_seq` 是**单调递增的编号计数器**，不是「当前最大
    id + 1」。理由：若按最大值推算，删掉最大 id 后下一次会重新用到它，而指向被删
    场景的边就成了悬空引用——它不会报错，只会静默指向一个完全不同的新场景。
    计数器把「已分配过的号」永久记下，编号只增不复用。

    Attributes：
        base_width: 采集场景时的帧宽。
        base_height: 采集场景时的帧高。
        schema_version: 库结构版本。
        next_scene_seq: 下一个可分配的正式场景序号。
        next_draft_seq: 下一个可分配的草稿序号。
        probe_interval: 点击后识别轮询间隔（秒）。
        settle_timeout: 点击后等待跳转的上限（秒）。
        search_order: 探索队列顺序（`bfs` / `dfs`）。
        auto_save: 场景编辑器的「自动保存」开关。
        name_with_scene_prefix: 新建特征/区域时默认名是否带场景名前缀。
        auto_launch: 探索前是否自动启动目标程序。
        restart_on_incomplete: 探索未完成时是否重启目标并重试。
        max_restarts: 未完成时最多重启几次（0–3）。
        restart_wait: 重启后等待目标恢复取帧的上限（秒）。
    """

    base_width: int = 0
    base_height: int = 0
    schema_version: int = SCHEMA_VERSION
    next_scene_seq: int = 1
    next_draft_seq: int = 1
    probe_interval: float = 0.2
    settle_timeout: float = 5.0
    search_order: str = SEARCH_BFS
    auto_save: bool = False
    name_with_scene_prefix: bool = False
    auto_launch: bool = False
    restart_on_incomplete: bool = False
    max_restarts: int = 1
    restart_wait: float = 8.0

    def __post_init__(self):
        """校验分辨率与计数器。允许分辨率为 0（空库尚未采集过）。"""
        self.base_width = int(self.base_width or 0)
        self.base_height = int(self.base_height or 0)
        if self.base_width < 0 or self.base_height < 0:
            raise SceneModelError("基准分辨率不能为负数")
        self.next_scene_seq = max(1, int(self.next_scene_seq or 1))
        self.next_draft_seq = max(1, int(self.next_draft_seq or 1))
        # 轮询间隔下限 0.05 秒：再小就是在空转取帧，对判定没有帮助。
        self.probe_interval = min(5.0, max(0.05, float(self.probe_interval or 0.2)))
        self.settle_timeout = min(60.0, max(0.5, float(self.settle_timeout or 5.0)))
        if self.search_order not in SEARCH_ORDERS:
            # 手改坏的值退回默认，不报错：它只影响顺序，不影响探索能否完成。
            self.search_order = SEARCH_BFS
        self.auto_save = bool(self.auto_save)
        self.name_with_scene_prefix = bool(self.name_with_scene_prefix)
        self.auto_launch = bool(self.auto_launch)
        self.restart_on_incomplete = bool(self.restart_on_incomplete)
        # 重启次数 0–3：再多会在登录/公告这类无法自动通过的画面上反复杀进程。
        self.max_restarts = min(3, max(0, int(self.max_restarts or 0)))
        self.restart_wait = min(60.0, max(1.0, float(self.restart_wait or 8.0)))

    def has_resolution(self):
        """返回是否已记录基准分辨率。

        Returns：
            两者均大于 0 时为 True。
        """
        return self.base_width > 0 and self.base_height > 0

    def aspect_ratio(self):
        """返回基准宽高比。

        Returns：
            宽高比浮点数；未记录分辨率时返回 0.0。
        """
        if not self.has_resolution():
            return 0.0
        return self.base_width / self.base_height

    def to_dict(self):
        """序列化为可写入 JSON 的字典。

        Returns：
            字典。
        """
        return {
            "schema_version": self.schema_version,
            "base_width": self.base_width,
            "base_height": self.base_height,
            "next_scene_seq": self.next_scene_seq,
            "next_draft_seq": self.next_draft_seq,
            "probe_interval": self.probe_interval,
            "settle_timeout": self.settle_timeout,
            "search_order": self.search_order,
            "auto_save": self.auto_save,
            "name_with_scene_prefix": self.name_with_scene_prefix,
            "auto_launch": self.auto_launch,
            "restart_on_incomplete": self.restart_on_incomplete,
            "max_restarts": self.max_restarts,
            "restart_wait": self.restart_wait,
        }

    @classmethod
    def from_dict(cls, data):
        """从字典还原库元数据。

        Args：
            data：`to_dict` 产出的字典。

        Returns：
            `LibraryMeta` 实例。

        Raises：
            SceneModelError：版本高于本程序支持的范围。

        Note：
            版本校验与 `Scene.from_dict` 一致：高版本库按旧结构读会静默丢字段，
            宁可明确拒绝。
        """
        if not isinstance(data, dict):
            return cls()
        version = data.get("schema_version", SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise SceneModelError(f"库元数据版本 {version} 高于本程序支持的 {SCHEMA_VERSION}，请升级程序")
        return cls(
            base_width=data.get("base_width", 0),
            base_height=data.get("base_height", 0),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            next_scene_seq=data.get("next_scene_seq", 1),
            next_draft_seq=data.get("next_draft_seq", 1),
            probe_interval=data.get("probe_interval", 0.2),
            settle_timeout=data.get("settle_timeout", 5.0),
            search_order=data.get("search_order", SEARCH_BFS),
            auto_save=data.get("auto_save", False),
            name_with_scene_prefix=data.get("name_with_scene_prefix", False),
            auto_launch=data.get("auto_launch", False),
            restart_on_incomplete=data.get("restart_on_incomplete", False),
            max_restarts=data.get("max_restarts", 1),
            restart_wait=data.get("restart_wait", 8.0),
        )
