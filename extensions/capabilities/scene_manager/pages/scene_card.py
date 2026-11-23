# -*- coding: utf-8 -*-
"""场景卡片：缩略图 + 场景名 + 场景 id。

继承上游 `ok.ui.qt.tasks.TemplateTab.ImageCard`，只改三件事：

| 项 | 父类 | 本类 |
|---|---|---|
| 名称来源 | 文件名（`splitext(basename(image_path))`） | `scene.json` 的 `name` 字段，后面跟灰色小字 id |
| 信号载荷 | 图片路径 | 场景 id（`scene-0001`） |
| 副文本 | `features_text` | 空串（不显示；id 走名称行，不占副文本行） |

**为什么继承而不是复制一份**：卡片的样式走 QSS 的 `QFrame#templateCard` 与
`QLabel#templateThumb` 选择器，尺寸走 `CARD_WIDTH` / `CARD_PADDING`。复制一份等于把
这两套约定抄进本能力，上游一改样式这里就与模板页签不一致，而这种不一致没有任何检查
能发现。继承则自动跟着走。

**副文本传空串即可**：父类在 `_features_text_full` 为空时会把 `features_label` 隐藏，
并相应减少卡片高度。

**为什么 id 走名称行的富文本而不是副文本行**：副文本行是独立的一行，会让卡片固定高出
一个行高（实测 131 → 151px），而 id 与名称是同一件事的两个标识，放同一行读起来是一句
「主页界面 scene-0001」。富文本让 id 用小一号的灰色字，视觉上从属于名称，同时名称过长
时两者能一起自然换行、卡片按需增高，不会截断（`set_card_width` 用 `heightForWidth`）。

**为什么必须绕开父类的鼠标处理**：父类的 `mousePressEvent` 里 `self.clicked.emit(self.
image_path)` 是硬编码的，子类改不掉——`self.clicked` 指向同一个 Signal，
`emit(image_path)` 会额外发一次图片路径。所以子类直接调 `QFrame` 的同名方法，跳过
父类那一层。
"""

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame

# 场景 id 的显示样式：小一号、次要色。用内联样式而不是新建 Label，是因为要挤在
# 名称同一条富文本里——独立控件做不到「同一行内换字体」。
_ID_STYLE = "color:#8a8a92;font-size:11px;"


def _image_card_class():
    """延迟导入上游 `ImageCard`。

    延迟到函数内：本模块在包导入期就被加载，而 `ok.ui.qt.tasks.TemplateTab` 会连带
    拉起整套 Qt 任务界面。延迟后，「只跑纯逻辑测试」的路径不必装齐 GUI 依赖。

    Returns：
        `ImageCard` 类。

    Raises：
        RuntimeError：上游不再提供 `ImageCard`——本卡片依赖它的样式与布局，符号消失时
            必须立刻报错并指名缺什么，而不是让界面变成一堆无样式的 QFrame。
    """
    try:
        from ok.ui.qt.tasks.TemplateTab import ImageCard
    except ImportError as error:
        raise RuntimeError(
            "场景卡片依赖上游 ImageCard（ok.ui.qt.tasks.TemplateTab），请检查 ok-script 升级后更新 pages/scene_card.py"
        ) from error
    return ImageCard


class SceneImageCard(_image_card_class()):
    """场景卡片：缩略图 + 场景名 + 场景 id，单击/双击携带场景 id。

    Attributes:
        scene_id: 场景 id（`scene-0001`），信号载荷，同时显示在名称右侧。
        scene_name: 场景显示名，取自 `scene.json`。
    """

    def __init__(self, preview_path, scene_id, scene_name, parent=None):
        """构造卡片。

        Args：
            preview_path：缩略图路径（通常是 `ok_scenes/<id>/base.png`）。
            scene_id：场景 id，作为信号载荷。
            scene_name：场景显示名；为空时退回 scene_id。
            parent：父控件。
        """
        # 副文本传空串：父类会隐藏该行并压缩高度，id 不占这一行。
        super().__init__(preview_path, parent=parent, features_text="")
        self.scene_id = scene_id
        self.scene_name = scene_name or scene_id
        # 父类用文件名当名称，这里换成「场景名 + 灰色 id」；随后必须重算尺寸——
        # 拼接后的文本与文件名的长度不同，不重算会让内容被截断。
        self._refresh_name_text()
        self.set_card_width(self.width() or _default_card_width())

    def _rich_name(self):
        """把场景名与 id 拼成名称行要显示的富文本。

        Returns：
            富文本字符串。名称与 id 之间用两个不断行空格分隔。
        """
        return f'{escape(self.scene_name)}<span style="{_ID_STYLE}">&nbsp;&nbsp;{escape(self.scene_id)}</span>'

    def _refresh_name_text(self):
        """按当前名称与 id 重设名称行文本。

        名称必须 `escape`：它是用户输入，含 `<` 或 `&` 时会被当成 HTML 标签解析，
        表现为名称被吞掉一段。id 同样转义，防止将来 id 规则变化时留下同类缺口。
        """
        self.name_label.setText(self._rich_name())

    def set_scene_name(self, name):
        """更新显示名并重算尺寸。

        Args：
            name：新的场景名；为空时退回 scene_id。
        """
        name = name or self.scene_id
        if name == self.scene_name:
            return
        self.scene_name = name
        self._refresh_name_text()
        self.set_card_width(self.width())

    def mousePressEvent(self, event):
        """单击发出场景 id。

        直接调 `QFrame` 的实现，绕开父类——父类在这里会额外 emit 一次图片路径。

        Args：
            event：鼠标事件。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.scene_id)
        QFrame.mousePressEvent(self, event)

    def mouseDoubleClickEvent(self, event):
        """双击发出场景 id。

        Args：
            event：鼠标事件。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.scene_id)
        QFrame.mouseDoubleClickEvent(self, event)


def _default_card_width():
    """返回上游的默认卡片宽度。

    Returns：
        宽度像素值；上游常量不可用时退回 176（与当前 `THUMB_SIZE + 2 * CARD_PADDING` 一致）。
    """
    try:
        from ok.ui.qt.tasks.TemplateTab import CARD_WIDTH
    except ImportError:
        return 176
    return CARD_WIDTH


def build_card(library, scene, parent=None):
    """按场景数据造一张卡片。

    Args：
        library：`store.SceneLibrary` 实例（用于定位 base.png）。
        scene：`model.Scene` 实例。
        parent：父控件。

    Returns：
        `SceneImageCard` 实例。缺少 base.png 时仍会创建：父类在没有可读图片时显示
        空缩略图框，比让整个场景从列表里消失好——用户至少能看到它并去补图。
    """
    return SceneImageCard(
        preview_path=library.base_image_path(scene.scene_id),
        scene_id=scene.scene_id,
        scene_name=scene.name,
        parent=parent,
    )
