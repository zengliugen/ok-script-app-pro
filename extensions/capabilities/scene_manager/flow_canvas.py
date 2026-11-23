# -*- coding: utf-8 -*-
"""流转图节点画布：QGraphicsView 自绘。

为什么不用现成的图库：项目无 graphviz / pyqtgraph / matplotlib，而「拖动节点、
框选、点选边、右键菜单」这类交互只有自绘才可控。节点坐标由 `graph.layout()` 给出
（networkx spring 布局 + 固定随机种子），本模块负责画与响应。

交互按主流节点图的约定设计（Unity Visual Scripting / LiteGraph / NodeCanvas 一致）：

| 操作 | 行为 |
|---|---|
| 左键点节点 | 选中；拖动时移动全部选中节点 |
| 左键拖空白 | 框选 |
| Ctrl + 左键 | 增减选择 |
| 中键 / Alt + 左键 | 平移画布 |
| 右键节点 / 边 / 空白 | 发出对应菜单信号 |
| 滚轮 | 以鼠标为中心缩放（限幅） |

三个刻意的设计：

- **边按曲线命中**：绘制用二次贝塞尔，命中区用同一条路径扩 14px，不存在
  「线是弯的、判定按直线」。
- **拖动时实时重绘边**：节点位置一变就重画，边不会留在原位。
- **节点位置由外部持久化**：画布只在拖动结束时发出 `positions_changed`，
  存哪里、什么时候存由页面决定。
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QLabel,
)

# 节点半径与间距（场景坐标单位）。
NODE_RADIUS = 34.0
# 布局单位 → 画布坐标的系数。spring 布局的坐标范围约 ±1，乘它之后才是像素距离。
# 220 时 7 节点环的跨度约 940×305，加上边标签把自适应比例压到 0.88——节点与
# 文字都偏小；180 让自然布局更紧凑，比例回到 1.1 左右。
NODE_GAP = 180.0
# 自适应视图时四周留白。与 NODE_GAP 分开：留白随间距一起变大会把图缩得太小。
# 80 在 480px 高的画布里占掉三分之一，收到 40。
FIT_MARGIN = 40.0
# 自动布局后**有连线**的两节点中心最小距离（圆直径 + 32 通道）。通道连续收紧
# 到 16 后节点几乎相切，用户要求圆间空隙翻倍：16 → 32。
MIN_NODE_DISTANCE = NODE_RADIUS * 2 + 32.0
# **孤岛**（无任何连线）与任何节点的最小中心距离：通道同样翻倍（8 → 16）。
ISOLATED_MIN_DISTANCE = NODE_RADIUS * 2 + 16.0
EDGE_ARROW_SIZE = 9.0
# 边的命中带宽度。太窄（1~2px）时用户必须精确压线，手感像「按直线算」。
EDGE_HIT_WIDTH = 14.0
# 平行边的基准弯距与槽位间距。同一对节点之间的每条边占一个槽位，
# 反向边也按「规范方向」分槽，否则 A→B 与 B→A 会弯到同一侧完全重合。
BASE_BEND = 26.0
PARALLEL_SPACING = 84.0
# 缩放限幅：低于 0.22 节点挤成一团，高于 2.6 曲线会被拉成折线。
ZOOM_MIN = 0.22
ZOOM_MAX = 2.6
# 滚轮每 120 单位的缩放倍率（8%）。原先固定 15%，连续滚动时累积过快。
ZOOM_STEP = 0.08
# 自动布局适配视口时允许的最大拉伸倍数。4 倍会把 7 节点环横向拉成 940px，
# 节点间距失真、整图又宽又扁；1.35 只做轻微适配，保持布局自然形状。
MAX_LAYOUT_STRETCH = 1.35
# 场景矩形半径。设得足够大，缩到最小时也能把内容平移到视口任意位置。
SCENE_RECT_MARGIN = 100000.0
# 自适应视图的最大放大倍数。与滚轮上限分开：节点很少时按视口铺满会放大到
# 两倍以上，节点巨大反而看不出拓扑，1.6 倍足够读又不会撑满屏。
FIT_MAX_SCALE = 1.6
# 缩放低于该值时隐藏节点文字与边标签：此时文字已缩成黑点，只剩干扰。
TEXT_HIDE_SCALE = 0.5

# 颜色。不跟随主题：图是数据可视化，固定配色比跟随主题更易读。
COLOR_NODE = QColor(70, 130, 200)
COLOR_NODE_EDGE = QColor(50, 100, 160)
COLOR_TEXT = QColor(255, 255, 255)
COLOR_LABEL = QColor(60, 60, 60)
COLOR_EDGE = QColor(140, 140, 140)
COLOR_HIGHLIGHT = QColor(230, 140, 40)
COLOR_EXPLORED = QColor(190, 120, 190)
# 本次探索新发现（探索页）：橙色加粗虚线。
COLOR_NEW_RUN = QColor(230, 80, 40)
# 路由页里「新增或已修改、尚未保存」的边：绿色实线。
COLOR_CHANGED = QColor(46, 160, 90)
# 路由页里「标记删除、尚未保存」的边：红色虚线。
COLOR_DELETED = QColor(210, 70, 70)
# 画布上选中的边。
COLOR_SELECTED = QColor(0, 120, 215)
# 鼠标悬停的边（预选高亮）。比选中态浅，避免与「已选中」混淆。
COLOR_HOVER = QColor(90, 170, 235)


class SceneNode(QGraphicsItem):
    """一个场景节点：圆 + 场景名 + 出边数。"""

    def __init__(self, scene_id, label, out_degree=0):
        """构造节点。

        Args：
            scene_id：场景 id。
            label：显示名。
            out_degree：出边数量，用于判断是否孤立。
        """
        super().__init__()
        self.scene_id = scene_id
        self.label = label
        self.out_degree = out_degree
        self.in_degree = 0
        self.highlighted = False
        self.selected = False
        # 缩放低于阈值时隐藏文字，只留圆圈：文字缩到几个像素后只有噪点作用。
        self.show_label = True
        # 拖动由画布统一处理，节点自身不可移动：QGraphicsItem 的默认拖动只移动
        # 被按住的那一个，做不到「多选一起移动」。
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setToolTip(f"{label}\n{scene_id}\n出边 {out_degree} 条")

    def boundingRect(self):
        """返回绘制区域（含描边余量）。

        Returns：
            矩形。
        """
        margin = 6.0
        return QRectF(
            -NODE_RADIUS - margin,
            -NODE_RADIUS - margin,
            (NODE_RADIUS + margin) * 2,
            (NODE_RADIUS + margin) * 2,
        )

    def shape(self):
        """返回命中区域，用圆形而不是 boundingRect 的方形。

        Returns：
            椭圆路径。

        Note：
            默认命中区是 boundingRect，方框的四个角（尤其左侧空白）也会选中
            节点：框选时明明只框到圆外，节点却被选中。改用圆形后点击与框选都
            按圆的实际范围判定。
        """
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), NODE_RADIUS, NODE_RADIUS)
        return path

    def paint(self, painter, option, widget=None):
        """绘制圆与文字。

        Args：
            painter：QPainter。
            option：样式选项（未使用）。
            widget：目标控件（未使用）。
        """
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outline = COLOR_HIGHLIGHT if self.highlighted else COLOR_NODE_EDGE
        width = 4.0 if self.highlighted else 2.0
        if self.out_degree == 0 and self.in_degree == 0:
            fill = QColor(190, 190, 190)
        else:
            fill = COLOR_NODE
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(outline, width))
        painter.drawEllipse(QPointF(0, 0), NODE_RADIUS, NODE_RADIUS)
        if self.selected:
            painter.setPen(QPen(COLOR_SELECTED, 2.4, Qt.PenStyle.DashLine))
            painter.drawEllipse(QPointF(0, 0), NODE_RADIUS + 4, NODE_RADIUS + 4)

        painter.setPen(QPen(COLOR_TEXT))
        if not self.show_label:
            return
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        text = _shorten(self.label, 9)
        rect = QRectF(-NODE_RADIUS, -10, NODE_RADIUS * 2, 20)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def itemChange(self, change, value):
        """选中状态变化时更新自身标记并通知场景。

        Args：
            change：变化类型。
            value：新值。

        Returns：
            原样返回 value。
        """
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.selected = bool(value)
            self.update()
        return super().itemChange(change, value)


def _shorten(text, limit):
    """把过长文本截断并加省略号。

    Args：
        text：原文本。
        limit：最大字符数。

    Returns：
        截断后的文本。
    """
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class FlowCanvas(QGraphicsView):
    """场景流转图的可交互画布。

    Signals:
        node_clicked：单击节点。
        edge_clicked：单击边（先按曲线命中，再发信号）。
        edge_double_clicked：双击边，参数 `(from_scene, area_name)`；
            探索页用它快速应用候选。
        node_context_menu：右键节点，参数 `(scene_id, 全局坐标)`。
        edge_context_menu：右键边，参数 `(from_scene, area_name, 全局坐标)`。
        canvas_context_menu：右键空白，参数 `(全局坐标)`。
        positions_changed：节点被拖动或重置布局后发出，参数是画布坐标字典。
        edge_relink_started：从连线一端拖出、进入重连模式，参数
            `(from_scene, area_name, "in"/"out")`；`"in"` 表示拖的是目标端，
            `"out"` 表示拖的是来源端。
        link_completed：连线模式下点中目标节点，参数是目标场景 id。
        link_cancelled：连线被 Esc / 右键 / 点击空白取消。
        edge_dropped_on_empty：拖拽重连后在空白处松手，参数 `(from_scene, area_name)`；
            页面应把这条边从工作副本删除。
    """

    node_clicked = Signal(str)
    edge_clicked = Signal(str, str)
    edge_double_clicked = Signal(str, str)
    node_context_menu = Signal(str, object)
    edge_context_menu = Signal(str, str, object)
    canvas_context_menu = Signal(object)
    positions_changed = Signal(dict)
    edge_relink_started = Signal(str, str, str)
    link_completed = Signal(str)
    link_cancelled = Signal()
    edge_dropped_on_empty = Signal(str, str)
    selection_changed = Signal()

    def __init__(self, parent=None, editable=True):
        """构造画布。

        Args：
            parent：父控件。
            editable：为 True 时启用右键菜单与连线模式；探索页只看不改，传 False。
        """
        super().__init__(parent)
        self._editable = editable
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 框选自己实现：Qt 的 RubberBandDrag 只能选 QGraphicsItem，边是自绘
        # 曲线，内置框选永远选不到。改为自己画矩形，松手时同时判定节点与边。
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        # 平移靠中键 / Alt+左键，滚动条纯属遮挡画布，直接关掉。
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor(250, 250, 250)))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._nodes = {}
        self._positions = {}
        self._edges = []
        self._edge_geometries = []
        self._highlight_path = []
        self._new_edge_keys = set()
        self._changed_keys = set()
        self._selected_edge = None
        self._selected_edges = set()
        self._hover_edge = None
        self._graph = None
        # 建图时控件可能还没拿到真实尺寸（页签未显示），此时 `fitInView` 会把
        # 缩放算成 0，整块画布永久空白。留一个待办，等尺寸就绪后再自适应一次。
        self._pending_fit = False
        # 首次显示时补一次自适应：构造期的视口可能只有几十像素，按它算出的
        # 比例会一直留在视图上。只做一次，避免每次切回页签都把用户缩放重置。
        self._ever_shown = False
        # 平移状态。
        self._panning = False
        self._pan_anchor = None
        # 节点拖动状态：按下时的场景坐标与全部选中节点的原始位置。
        self._drag_state = None
        # 连线模式。
        self._link_source = None
        self._link_loose = "in"
        self._link_item = None
        self._link_text = None
        self._link_hover = None
        # 在连线上按下、尚未拖出阈值：松手算「选中」，拖出才算「重连」。
        self._pending_edge_drag = None
        # 框选状态：按下点、当前点与框选前的选中集合（Ctrl 时累加）。
        self._marquee = None
        self._marquee_item = None
        # 拖拽重连时暂隐的旧边：拖出后旧线消失，落在空白即删除，落在节点即重连。
        self._hidden_edge_key = None
        # 拖拽松手是否直接完成连线（只有从旧线拖出的重连为 True）。
        self._link_drag_release = False
        # 落在空白时是否删除旧边。只有「拖出重连」为 True；菜单改目标也隐藏旧边，
        # 但它没有拖拽动作，点空白应取消而不是删除。
        self._link_delete_on_empty = False
        # 右下角的缩放比例。用户需要知道当前视图是放大还是缩小，尤其自动布局后
        # 比例会变化；放在画布内而不是页面底部，避免视线来回跳。
        self._zoom_label = QLabel("100%", self)
        self._zoom_label.setStyleSheet(
            "QLabel { background-color: rgba(255, 255, 255, 210); border: 1px solid #d0d0d0; "
            "border-radius: 4px; padding: 1px 6px; color: #333; font-size: 11px; }"
        )
        self._zoom_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    # --- 对外接口 -----------------------------------------------------------

    def set_editable(self, editable):
        """切换是否允许编辑（右键菜单 / 连线）。

        Args：
            editable：布尔值。
        """
        self._editable = bool(editable)
        if not self._editable:
            self.cancel_link(notify=False)

    def clear_graph(self):
        """清空画布与内部状态。"""
        self.cancel_link(notify=False)
        self._scene.clear()
        self._nodes.clear()
        self._positions = {}
        self._edges = []
        self._edge_geometries = []
        self._highlight_path = []
        self._selected_edge = None
        self._selected_edges = set()
        self._graph = None
        self._drag_state = None

    def set_graph(self, scene_graph_instance, positions=None, changed_keys=None, ghost_edges=None, keep_view=False):
        """按流转图重建画面。

        Args：
            scene_graph_instance：`graph.SceneGraph` 实例。
            positions：`{scene_id: (x, y)}` 覆盖位置，单位是**画布坐标**
                （`remember_positions` 的返回值）；省略时用图自带布局。
            changed_keys：路由页里新增/修改、尚未保存的边键集合，画成绿色。
            ghost_edges：`[(from_scene, to_scene, area_name)]`，已标记删除、
                尚未保存的边，画成红色虚线；它们不在 `scene_graph` 里。
            keep_view：为 True 时保留当前缩放与视口中心，不重新自适应。编辑已有
                连线（改目标、改等待、删边）走这里——每改一次就把视图重置到
                「全部节点可见」，用户刚放大对准的位置会丢，连改几条就要重新找。

        Note：
            两种坐标的单位不同，必须分开换算：`layout()` 给的是布局单位，要乘
            `NODE_GAP` 才是画布坐标；`positions` 已经是画布坐标，再乘一次会让
            节点飞到几万像素外——视图自适应缩到极小后整块画布看起来是空的。
        """
        preserved = None
        if keep_view and self._nodes:
            # 用「变换 + 视口中心对应的场景点」而不是滚动条数值：重建后 sceneRect
            # 会按新内容重算，直接回填旧滚动值落点会偏。centerOn 负责重新换算。
            preserved = (self.transform(), self.mapToScene(self.viewport().rect().center()))
        self.clear_graph()
        self._graph = scene_graph_instance
        self._changed_keys = set(changed_keys or ())
        layout = scene_graph_instance.layout()
        kept = positions or {}
        # 没有持久化位置时按布局坐标排布，并把挨得太近的节点推开；有位置时
        # 完全尊重用户的手工布局，不擅自移动。
        points = {}
        for scene_id in scene_graph_instance.scenes:
            if scene_id in kept:
                continue
            point = _to_point(layout.get(scene_id, (0.0, 0.0)))
            points[scene_id] = (point.x(), point.y())
        if not kept and points:
            points = self._auto_layout_points(points, scene_graph_instance)
        for scene_id, scene in scene_graph_instance.scenes.items():
            out_degree = scene_graph_instance.graph.out_degree(scene_id)
            node = SceneNode(scene_id, scene.name, out_degree)
            node.in_degree = scene_graph_instance.graph.in_degree(scene_id)
            point = kept.get(scene_id) or points.get(scene_id, (0.0, 0.0))
            node.setPos(QPointF(float(point[0]), float(point[1])))
            node.setZValue(2)
            self._scene.addItem(node)
            self._nodes[scene_id] = node
            self._positions[scene_id] = node.pos()

        for source, target, data in scene_graph_instance.graph.edges(data=True):
            self._edges.append((source, target, data))
        for source, target, area_name in ghost_edges or ():
            if source in self._nodes and target in self._nodes:
                self._edges.append((source, target, {"area": area_name, "source": "deleted", "state": "deleted"}))
        self._draw_edges()
        if preserved is not None and self._refresh_scene_rect() is not None:
            transform, center = preserved
            self.setTransform(transform)
            self.centerOn(center)
            # 保留视图下不再等待自适应：否则下一次尺寸变化会突然把视图重置。
            self._pending_fit = False
            self._sync_text_visibility()
            self._update_zoom_label()
        else:
            self._fit_scene()

    def set_new_edges(self, keys):
        """标记「本次探索新发现」的边，用更醒目的橙色虚线画出来。

        Args：
            keys：`(from_scene, area_name)` 集合；空集合表示没有新发现。

        Note：
            与路由页的「新增/修改」分开：探索页看的是「这次探到了什么」，
            路由页看的是「哪些改动还没保存」，两者颜色与线型不同。
        """
        self._new_edge_keys = set(keys)
        self._draw_edges()

    def set_changed_keys(self, keys):
        """标记路由页里新增/修改、尚未保存的边。

        Args：
            keys：`(from_scene, area_name)` 集合。
        """
        self._changed_keys = set(keys)
        self._draw_edges()

    def set_selected_edge(self, key):
        """设置画布上唯一的选中边。

        Args：
            key：`(from_scene, area_name)`；None 表示取消选中。
        """
        self.set_selected_edges({key} if key else set(), key)

    def set_selected_edges(self, keys, primary=None):
        """设置画布上选中的边集合。

        Args：
            keys：`(from_scene, area_name)` 的可迭代集合。
            primary：主选中边，用于表格同步与提示行；省略时取集合中的任一项。
        """
        self._selected_edges = set(keys or ())
        if primary is not None and primary in self._selected_edges:
            self._selected_edge = primary
        else:
            self._selected_edge = next(iter(self._selected_edges), None)
        self._draw_edges()

    def selected_edges(self):
        """返回当前选中的边键集合。

        Returns：
            `{(from_scene, area_name)}` 的副本。
        """
        return set(self._selected_edges)

    def set_highlight(self, steps):
        """高亮一条路径。

        Args：
            steps：`graph.RouteStep` 列表；空列表表示取消高亮。
        """
        self._highlight_path = [(step.from_scene, step.to_scene, step.area_name) for step in steps]
        highlighted_nodes = set()
        for step in steps:
            highlighted_nodes.add(step.from_scene)
            highlighted_nodes.add(step.to_scene)
        for scene_id, node in self._nodes.items():
            node.highlighted = scene_id in highlighted_nodes
            node.update()
        self._draw_edges()

    def clear_highlight(self):
        """取消全部高亮。"""
        self.set_highlight([])

    def remember_positions(self):
        """把当前节点位置收进覆盖表，供持久化与刷新使用。

        Returns：
            `{scene_id: (x, y)}` 字典（画布坐标）。
        """
        for scene_id, node in self._nodes.items():
            self._positions[scene_id] = node.pos()
        return {scene_id: (point.x(), point.y()) for scene_id, point in self._positions.items()}

    def reset_view(self):
        """把视图缩放回「全部节点可见」，不改变节点位置。"""
        self._fit_scene()

    def reset_layout(self):
        """清除位置覆盖，按布局算法重新排列全部节点。

        Note：
            发 `positions_changed` 让页面把新坐标写回布局文件；否则下次打开又
            回到旧位置。布局后做一次最小间距校正，避免节点圆边相切。
        """
        if self._graph is None:
            return
        layout = self._graph.layout()
        points = {}
        for scene_id in self._nodes:
            point = _to_point(layout.get(scene_id, (0.0, 0.0)))
            points[scene_id] = (point.x(), point.y())
        points = self._auto_layout_points(points, self._graph)
        for scene_id, node in self._nodes.items():
            x, y = points.get(scene_id, (node.pos().x(), node.pos().y()))
            node.setPos(QPointF(x, y))
        self._draw_edges()
        # 自动布局后尽量放大：内容小、节点少时也按 ZOOM_MAX 顶格，文字更清楚。
        self._fit_scene(max_scale=ZOOM_MAX)
        self.positions_changed.emit(self.remember_positions())

    def _auto_layout_points(self, points, scene_graph_instance):
        """把原始布局坐标整理成最终位置：松弛 → 孤岛收拢 → 消交叉 → 适配视口。

        Args：
            points：`{scene_id: (x, y)}`，`spring_layout` 乘 `NODE_GAP` 后的坐标。
            scene_graph_instance：`graph.SceneGraph` 实例，用于取孤岛与边。

        Returns：
            调整后的坐标字典（画布坐标）。
        """
        isolated = set(scene_graph_instance.isolated_scenes())
        points = _relax_positions(points, MIN_NODE_DISTANCE, isolated, ISOLATED_MIN_DISTANCE)
        points = _pack_isolated(points, isolated, ISOLATED_MIN_DISTANCE)
        edges = [(source, target) for source, target in scene_graph_instance.graph.edges()]
        points = _reduce_crossings(points, edges)
        return _stretch_to_viewport(points, self.viewport().width(), self.viewport().height())

    # --- 连线模式 -----------------------------------------------------------

    def begin_link(self, scene_id, label="", loose="in"):
        """进入连线模式：从固定端拉一条跟随鼠标的预览线。

        Args：
            scene_id：固定端的场景 id（`loose="in"` 时是来源节点，
                `loose="out"` 时是目标节点）。
            label：预览线上显示的区域名。
            loose：活动端类型。`"in"` 表示接下来选目标，`"out"` 表示选来源。
        """
        node = self._nodes.get(scene_id)
        if node is None:
            return
        self.cancel_link(notify=False)
        self._link_source = scene_id
        self._link_loose = loose
        self._link_item = self._scene.addPath(QPainterPath(), QPen(COLOR_NEW_RUN, 2.2, Qt.PenStyle.DashLine))
        self._link_item.setZValue(1.35)
        self._link_text = QGraphicsSimpleTextItem(_shorten(label, 16))
        font = QFont()
        font.setPointSize(7)
        self._link_text.setFont(font)
        self._link_text.setBrush(QBrush(COLOR_LABEL))
        self._link_text.setZValue(1.4)
        self._link_text.setPos(node.pos())
        self._scene.addItem(self._link_text)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _edge_target(self, key):
        """按边键取当前目标场景。

        Args：
            key：`(from_scene, area_name)`。

        Returns：
            目标场景 id；找不到时返回空串。
        """
        for source, target, data in self._edges:
            if source == key[0] and data.get("area", "") == key[1]:
                return target
        return ""

    def _edge_end_at(self, key, view_pos):
        """判断按下点更靠近边的哪一端。

        Args：
            key：`(from_scene, area_name)`。
            view_pos：视图坐标。

        Returns：
            `"out"`（靠近来源端）或 `"in"`（靠近目标端）。
        """
        geometry = next((item for item in self._edge_geometries if item["key"] == key), None)
        if geometry is None:
            return "in"
        scene_pos = self.mapToScene(view_pos)
        path = geometry["path"]
        best_percent = 0.0
        best_distance = float("inf")
        for step in range(25):
            percent = step / 24.0
            distance = _point_distance(path.pointAtPercent(percent), scene_pos)
            if distance < best_distance:
                best_distance = distance
                best_percent = percent
        return "out" if best_percent < 0.5 else "in"

    def _start_edge_relink(self, key, end):
        """从连线的一端进入重连模式。

        Args：
            key：`(from_scene, area_name)`。
            end：`"out"` 拖来源端，`"in"` 拖目标端。
        """
        source, area_name = key
        if end == "out":
            target = self._edge_target(key)
            if not target:
                return
            self.begin_link(target, "", loose="out")
        else:
            self.begin_link(source, area_name, loose="in")
        # 拖出即隐藏旧线：落在节点上重连，落在空白处删除，视觉上都不该再留着它。
        self._hidden_edge_key = key
        self._link_drag_release = True
        self._link_delete_on_empty = True
        self._draw_edges()
        self.edge_relink_started.emit(source, area_name, end)

    def begin_retarget(self, key):
        """右键菜单「更改目标」：与拖住边的目标端一致，立即隐藏旧边。

        Args：
            key：`(from_scene, area_name)`。

        Note：
            **必须隐藏旧边**，否则进入选择状态后画面上同时有旧边与跟随鼠标的虚线
            预览，看起来像「旧的没删、又新建了一条」。与拖拽的区别只在落空处理：
            菜单没有拖拽动作，点空白应取消而不是删边。
        """
        source, area_name = key
        self.begin_link(source, area_name, loose="in")
        self._hidden_edge_key = key
        self._draw_edges()

    def cancel_link(self, notify=True):
        """退出连线模式并清理预览图元。

        Args：
            notify：为 True 时发出 `link_cancelled`，让页面清掉自己的状态。
        """
        was_linking = self._link_source is not None
        if self._link_item is not None:
            self._scene.removeItem(self._link_item)
            self._link_item = None
        if self._link_text is not None:
            self._scene.removeItem(self._link_text)
            self._link_text = None
        if self._link_hover is not None:
            self._link_hover.highlighted = False
            self._link_hover.update()
            self._link_hover = None
        self._link_source = None
        self._link_loose = "in"
        self._pending_edge_drag = None
        hidden = self._hidden_edge_key is not None
        self._hidden_edge_key = None
        self._link_drag_release = False
        self._link_delete_on_empty = False
        self.unsetCursor()
        if hidden:
            self._draw_edges()
        if was_linking and notify:
            self.link_cancelled.emit()

    def _drop_link(self):
        """在空白处松开连线：拖拽重连时删除原边，其余情况只是取消。

        Note：
            先取旧边键再 `cancel_link`——取消会把 `_hidden_edge_key` 清掉。
            菜单「更改目标」也隐藏旧边，但它没有拖拽动作，此时点在空白只能取消，
            删边必须由真正的拖出触发（`_link_delete_on_empty`）。
        """
        key = self._hidden_edge_key if self._link_delete_on_empty else None
        self.cancel_link(notify=False)
        if key is not None:
            self.edge_dropped_on_empty.emit(key[0], key[1])
        self.link_cancelled.emit()

    def _complete_link(self, target_scene):
        """连线落下目标节点。

        Args：
            target_scene：目标场景 id。
        """
        self.cancel_link(notify=False)
        self.link_completed.emit(target_scene)

    def _update_link(self, scene_pos):
        """更新预览线的终点与标签位置。

        Args：
            scene_pos：鼠标在场景坐标系里的位置。
        """
        if self._link_item is None or self._link_source is None:
            return
        start = self._nodes[self._link_source].pos()
        dx = scene_pos.x() - start.x()
        dy = scene_pos.y() - start.y()
        distance = math.hypot(dx, dy)
        if distance <= NODE_RADIUS:
            return
        ux, uy = dx / distance, dy / distance
        begin = QPointF(start.x() + ux * NODE_RADIUS, start.y() + uy * NODE_RADIUS)
        path = QPainterPath(begin)
        middle = QPointF((begin.x() + scene_pos.x()) / 2 - uy * 18, (begin.y() + scene_pos.y()) / 2 + ux * 18)
        path.quadTo(middle, scene_pos)
        self._link_item.setPath(path)
        point = path.pointAtPercent(0.5)
        if self._link_text is not None:
            self._link_text.setPos(QPointF(point.x() + 4, point.y() - 12))

    def _update_link_hover(self, view_pos):
        """高亮鼠标下的目标节点。

        Args：
            view_pos：鼠标在视图里的位置。
        """
        item = self.itemAt(view_pos)
        node = item if isinstance(item, SceneNode) else None
        if node is self._link_hover:
            return
        if self._link_hover is not None:
            self._link_hover.highlighted = False
            self._link_hover.update()
        self._link_hover = node
        if node is not None:
            node.highlighted = True
            node.update()

    # --- 绘制 ---------------------------------------------------------------

    def _draw_edges(self):
        """按当前节点位置重绘全部边。

        Note：
            **槽位只按真实边分配，ghost 不参与**，且真实边之间按稳定键排序。原因：
            标记删除的边会从「图里的边」变成追加在末尾的 ghost，若它参与同对的槽位
            总数，剩下的真实边会从居中槽移到半槽位——用户看到的是「标记删除后线
            自己动了」。ghost 改为排在真实边束之外（`n + k` / `n + k + 1`），既
            不挤动真实边，也不会和真实边重叠成一条看不清的线。
        """
        for item in list(self._scene.items()):
            if 1 <= item.zValue() < 2 and item is not self._link_item and item is not self._link_text:
                self._scene.removeItem(item)
        self._edge_geometries = []
        ordered = sorted(self._edges, key=lambda item: (item[0], item[1], item[2].get("area", "")))
        real_totals = {}
        for source, target, data in ordered:
            if data.get("state") == "deleted":
                continue
            pair = tuple(sorted((source, target)))
            real_totals[pair] = real_totals.get(pair, 0) + 1
        real_counts = {}
        ghost_counts = {}
        for source, target, data in ordered:
            pair = tuple(sorted((source, target)))
            if data.get("state") == "deleted":
                # 排在真实边束之外：第 k 条 ghost 占「真实边数 + k」号槽。
                ghost_index = ghost_counts.get(pair, 0)
                ghost_counts[pair] = ghost_index + 1
                real_total = real_totals.get(pair, 0)
                index = real_total + ghost_index
                total = index + 1
            else:
                index = real_counts.get(pair, 0)
                real_counts[pair] = index + 1
                total = real_totals[pair]
            if self._hidden_edge_key == (source, data.get("area", "")):
                continue
            self._draw_edge(source, target, data, index, total)

    def _draw_edge(self, source, target, data, index=0, total=1):
        """绘制一条带箭头的曲线边。

        Args：
            source：起点场景 id。
            target：终点场景 id。
            data：边属性（含 area 与 source）。
            index：同一对节点之间的第几条边。
            total：同一对节点之间的边总数，用于把平行边对称分槽。

        Note：
            槽位按**排序后的节点对**计算，而不是按每条边的方向。否则 A→B 与
            B→A 的法线相反、弯距符号再相反，两条曲线会弯到同一侧完全重合。
        """
        source_node = self._nodes.get(source)
        target_node = self._nodes.get(target)
        if source_node is None or target_node is None:
            return

        start = source_node.pos()
        end = target_node.pos()
        key = (source, data.get("area", ""))
        offset = (index - (total - 1) / 2.0) * PARALLEL_SPACING
        if source == target:
            # 自环：用一段椭圆弧绕开节点，多个自环按序号逐层外扩。
            size = 44.0 + index * 18.0
            loop = QRectF(start.x() - size / 2, start.y() - NODE_RADIUS - size * 0.8, size, size)
            path = QPainterPath(QPointF(start.x(), start.y() - NODE_RADIUS))
            path.arcTo(loop, 90, -300)
        else:
            dx = end.x() - start.x()
            dy = end.y() - start.y()
            distance = math.hypot(dx, dy)
            if distance <= 0:
                return
            ux, uy = dx / distance, dy / distance
            path = QPainterPath(QPointF(start.x() + ux * NODE_RADIUS, start.y() + uy * NODE_RADIUS))
            endpoint = QPointF(end.x() - ux * NODE_RADIUS, end.y() - uy * NODE_RADIUS)
            # 法线按排序后的节点对统一，保证同对节点的每条边落在不同侧。
            first, second = sorted((source, target))
            canon_start = self._nodes[first].pos()
            canon_end = self._nodes[second].pos()
            cdx = canon_end.x() - canon_start.x()
            cdy = canon_end.y() - canon_start.y()
            cdist = math.hypot(cdx, cdy)
            if cdist <= 0:
                nx, ny = 0.0, 1.0
            else:
                nx, ny = -cdy / cdist, cdx / cdist
            # 单条边保留一点弧度；平行边在基准弯距上叠加槽位偏移。
            bend = BASE_BEND + offset
            middle = QPointF(
                (path.currentPosition().x() + endpoint.x()) / 2 + nx * bend,
                (path.currentPosition().y() + endpoint.y()) / 2 + ny * bend,
            )
            path.quadTo(middle, endpoint)

        highlighted = any(
            source == step_source and target == step_target and key[1] == step_area
            for step_source, step_target, step_area in self._highlight_path
        )
        state = data.get("state", "")
        color = COLOR_EDGE
        width = 1.6
        style = Qt.PenStyle.SolidLine
        selected = key in self._selected_edges
        if state == "deleted" and selected:
            # 选中不能盖掉「已删除」语义：仍然红色虚线，只加粗表示选中。
            # 否则选中一条待删除边后与选中普通边完全同形（都是蓝色实线），
            # 用户分不清自己选中的是「会被删掉的那条」还是正常边。
            color, width, style = COLOR_DELETED, 3.6, Qt.PenStyle.DashLine
        elif selected:
            color, width, style = COLOR_SELECTED, 3.6, Qt.PenStyle.SolidLine
        elif state == "deleted":
            color, width, style = COLOR_DELETED, 2.4, Qt.PenStyle.DashLine
        elif highlighted:
            color, width, style = COLOR_HIGHLIGHT, 3.0, Qt.PenStyle.SolidLine
        elif key in self._changed_keys:
            color, width, style = COLOR_CHANGED, 2.8, Qt.PenStyle.SolidLine
        elif key in self._new_edge_keys:
            color, width, style = COLOR_NEW_RUN, 3.2, Qt.PenStyle.DashLine
        elif data.get("source") == "explored":
            color, width, style = COLOR_EXPLORED, 2.0, Qt.PenStyle.DashLine
        if key == self._hover_edge and not selected:
            # 悬停预选：加粗；普通灰边顺带变浅蓝，彩色状态（红/绿/紫）保持本义。
            width += 0.8
            if color == COLOR_EDGE:
                color = COLOR_HOVER
        pen = QPen(color, width)
        pen.setStyle(style)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        edge_item = self._scene.addPath(path, pen)
        edge_item.setZValue(1.2)
        edge_item.setData(0, key)
        if state == "deleted":
            edge_item.setToolTip("待删除（保存后生效）")

        if source != target:
            self._add_arrow(path, color, key)
        self._add_edge_label(source, data, path)
        stroker = QPainterPathStroker()
        stroker.setWidth(EDGE_HIT_WIDTH)
        self._edge_geometries.append({"key": key, "path": path, "hit": stroker.createStroke(path)})

    def _add_arrow(self, path, color, key=None):
        """在路径末端画箭头。

        Args：
            path：边路径。
            color：箭头颜色。
            key：边键，点击箭头也能选中边。
        """
        end = path.pointAtPercent(1.0)
        before = path.pointAtPercent(0.94)
        angle = math.atan2(end.y() - before.y(), end.x() - before.x())
        left = QPointF(
            end.x() - EDGE_ARROW_SIZE * math.cos(angle - math.pi / 7),
            end.y() - EDGE_ARROW_SIZE * math.sin(angle - math.pi / 7),
        )
        right = QPointF(
            end.x() - EDGE_ARROW_SIZE * math.cos(angle + math.pi / 7),
            end.y() - EDGE_ARROW_SIZE * math.sin(angle + math.pi / 7),
        )
        arrow = QPainterPath(end)
        arrow.lineTo(left)
        arrow.lineTo(right)
        arrow.closeSubpath()
        item = self._scene.addPath(arrow, QPen(color), QBrush(color))
        item.setZValue(1.1)
        item.setData(0, key)

    def _add_edge_label(self, source, data, path):
        """在边的起点一侧标注来源场景与区域名。

        Args：
            source：起点场景 id。
            data：边属性。
            path：边路径。

        Note：
            不标目标场景：箭头指向的节点本身就写着名字，重复标注会让边中段
            挤满文字，平行边靠近时尤其明显。
            标签不再固定挂在 45% 处，而是贴着**起点端**、按线条走向对齐：
            左→右时文本左缘对齐起点，右→左时文本右缘对齐起点，上→下时
            文本顶边中心对着起点，下→上时底边中心对着起点——文字始终从
            出发的那一端开始排列，并且落在线的外侧，不压住线条。
            **文字仅做展示**：不写边键、不接收鼠标事件，点它不会选中边。
        """
        area_name = data.get("area", "")
        if not area_name:
            return
        source_label = self._nodes[source].label if source in self._nodes else source
        label = QGraphicsSimpleTextItem(_shorten(f"{source_label} · {area_name}", 20))
        font = QFont()
        font.setPointSize(7)
        label.setFont(font)
        label.setBrush(QBrush(COLOR_LABEL))
        label.setPos(self._edge_label_pos(label, path))
        label.setZValue(1.3)
        # 仅做展示：不写边键、不接收鼠标事件。写边键会让点文字选中边，
        # 用户只是想选中线时反而容易误点。
        label.setData(1, "label")
        label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        label.setVisible(self._text_visible())
        self._scene.addItem(label)

    def _edge_label_pos(self, label, path):
        """按边的走向计算标签位置。

        Args：
            label：`QGraphicsSimpleTextItem` 实例（用于取宽高）。
            path：边路径。

        Returns：
            `QPointF` 标签左上角坐标。

        Note：
            锚点取路径 5% 处，尽量贴近出发端但不压住节点圆。水平为主的边
            把标签放在线上方，垂直为主的边放在线右侧。
        """
        rect = label.boundingRect()
        width, height = rect.width(), rect.height()
        start = path.pointAtPercent(0.05)
        end = path.pointAtPercent(1.0)
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        if abs(dx) >= abs(dy):
            # 横向为主：文字在线上方，左缘/右缘对齐起点。
            y = start.y() - height - 3
            if dx >= 0:
                return QPointF(start.x() + 3, y)
            return QPointF(start.x() - width - 3, y)
        # 纵向为主：文字在线右侧，顶边/底边中心对齐起点。
        x = start.x() + 4
        if dy >= 0:
            return QPointF(x, start.y() + 2)
        return QPointF(x, start.y() - height - 2)

    # --- 命中与视图 ---------------------------------------------------------

    def _edge_at(self, view_pos):
        """按**绘制曲线**命中最靠近的一条边。

        Args：
            view_pos：视图坐标。

        Returns：
            `(from_scene, area_name)`；没有命中时返回 None。

        Note：
            先看 `itemAt`（方便点中标签与箭头），再用 14px 的描边命中带逐条
            计算最近的边。只按 1.6px 线宽判定时，用户必须精确压线才点得中。
        """
        item = self.itemAt(view_pos)
        if isinstance(item, SceneNode):
            # 鼠标在节点上时不算命中下方的边，否则悬停节点会误高亮被压住的线。
            return None
        if item is not None and item.data(1) == "label":
            # 标签仅做展示：点文字不选中边，也不继续用描边命中带兜底。
            return None
        if item is not None:
            key = item.data(0)
            if key:
                return (key[0], key[1])
        scene_pos = self.mapToScene(view_pos)
        best_key = None
        best_distance = EDGE_HIT_WIDTH
        for geometry in self._edge_geometries:
            if not geometry["hit"].contains(scene_pos):
                continue
            path = geometry["path"]
            distance = min(_point_distance(path.pointAtPercent(step / 24.0), scene_pos) for step in range(25))
            if distance < best_distance:
                best_distance = distance
                best_key = geometry["key"]
        return best_key

    def _update_cursor(self, view_pos):
        """按鼠标下的对象切换光标，并更新悬停预选。

        Args：
            view_pos：视图坐标。
        """
        key = self._edge_at(view_pos)
        if key != self._hover_edge:
            self._hover_edge = key
            self._draw_edges()
        if key is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()

    def _text_visible(self):
        """当前缩放比例下文字是否应当显示。

        Returns：
            布尔值；缩放低于 `TEXT_HIDE_SCALE` 时为 False。
        """
        return self.transform().m11() >= TEXT_HIDE_SCALE

    def _sync_text_visibility(self):
        """按当前缩放比例显示或隐藏节点文字与边标签。"""
        visible = self._text_visible()
        for item in self._scene.items():
            if isinstance(item, SceneNode):
                if item.show_label != visible:
                    item.show_label = visible
                    item.update()
            elif item.data(1) == "label":
                item.setVisible(visible)

    def _update_zoom_label(self):
        """刷新右下角的缩放比例。"""
        percent = max(1, round(self.transform().m11() * 100))
        self._zoom_label.setText(f"{percent}%")
        self._zoom_label.adjustSize()
        self._place_zoom_label()

    def _place_zoom_label(self):
        """把缩放比例放到视口右下角。"""
        margin = 8
        viewport = self.viewport()
        self._zoom_label.move(
            max(0, viewport.width() - self._zoom_label.width() - margin),
            max(0, viewport.height() - self._zoom_label.height() - margin),
        )

    def _refresh_scene_rect(self):
        """按当前内容更新场景矩形，返回内容包围盒。

        Returns：
            `QRectF` 内容包围盒；没有内容时为 None。

        Note：
            场景矩形留出极大余量：缩到最小时视口覆盖的场景范围很大，±2000 会让
            平移在内容附近被滚动范围挡住，表现为「缩到最小后拖不动」。
        """
        content = self._scene.itemsBoundingRect()
        if content.isNull():
            return None
        self._scene.setSceneRect(
            content.adjusted(
                -SCENE_RECT_MARGIN,
                -SCENE_RECT_MARGIN,
                SCENE_RECT_MARGIN,
                SCENE_RECT_MARGIN,
            )
        )
        return content

    def _fit_scene(self, max_scale=FIT_MAX_SCALE):
        """让全部节点落入视野，并给自由平移留出余量。

        Args：
            max_scale：自适应允许的最大放大倍数。默认 1.6；自动布局时传
                `ZOOM_MAX`，让节点少、内容小的图尽量放大，文字更清楚。

        Note：
            控件还没有真实尺寸时只记待办，不做缩放：`fitInView` 在空视口上会把
            比例算成 0，节点从此不可见。等首次 `resizeEvent` / `showEvent` 再补。
            `fitInView` 不受滚轮限幅约束，两个节点时会放大到三四倍、文字撑满
            屏幕，所以自适应之后同样要把比例夹回 `[ZOOM_MIN, ZOOM_MAX]`。
        """
        if not self._nodes:
            return
        content = self._refresh_scene_rect()
        if content is None:
            return
        if self.viewport().width() < 120 or self.viewport().height() < 120:
            # 尺寸过小时算出的比例没有意义，等布局完成后再补。
            self._pending_fit = True
            return
        # 不用 `fitInView`：它按 rect 适配后不保证视图中心落在内容中心，而且
        # 节点少时会放大到视口铺满。这里自己算比例并显式居中，保证「包裹全部
        # 节点」与「内容居中」两件事同时成立。
        target = content.adjusted(-FIT_MARGIN, -FIT_MARGIN, FIT_MARGIN, FIT_MARGIN)
        viewport = self.viewport()
        if target.width() <= 0 or target.height() <= 0:
            return
        ratio = min(viewport.width() / target.width(), viewport.height() / target.height())
        ratio = max(ZOOM_MIN, min(max_scale, ratio))
        self.resetTransform()
        self.scale(ratio, ratio)
        self.centerOn(content.center())
        self._pending_fit = False
        self._sync_text_visibility()
        self._update_zoom_label()

    def _expand_scene_rect(self):
        """节点被拖出原范围时扩大场景矩形。

        Note：
            不扩的话滚动条止步于旧边界，节点会停在可视区外再也找不回来。
        """
        content = self._scene.itemsBoundingRect()
        if content.isNull():
            return
        self._scene.setSceneRect(self._scene.sceneRect().united(content.adjusted(-400, -400, 400, 400)))

    def resizeEvent(self, event):
        """窗口尺寸变化时补做一次延迟的自适应。

        Args：
            event：尺寸事件。
        """
        super().resizeEvent(event)
        self._place_zoom_label()
        if self._pending_fit:
            self._fit_scene()

    def showEvent(self, event):
        """首次显示时补做一次延迟的自适应。

        Args：
            event：显示事件。
        """
        super().showEvent(event)
        if self._pending_fit or not self._ever_shown:
            self._fit_scene()
            if not self._pending_fit:
                self._ever_shown = True

    # --- 鼠标与键盘 ---------------------------------------------------------

    def _selected_nodes(self):
        """返回当前选中的节点。

        Returns：
            `SceneNode` 列表。
        """
        return [item for item in self._scene.selectedItems() if isinstance(item, SceneNode)]

    # --- 框选 ---------------------------------------------------------------

    def _begin_marquee(self, event):
        """在空白处按下左键：开始框选。

        Args：
            event：鼠标事件。
        """
        start = self.mapToScene(event.position().toPoint())
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        self._marquee = {
            "start": start,
            "ctrl": ctrl,
            "edges": set(self._selected_edges) if ctrl else set(),
        }
        if not ctrl:
            self._scene.clearSelection()
            self.set_selected_edges(set())
        self._marquee_item = self._scene.addRect(
            QRectF(start, start),
            QPen(COLOR_SELECTED, 1.0, Qt.PenStyle.DashLine),
            QBrush(QColor(0, 120, 215, 30)),
        )
        self._marquee_item.setZValue(3)

    def _update_marquee(self, position):
        """刷新框选矩形。

        Args：
            position：当前鼠标位置（控件坐标）。
        """
        if self._marquee is None or self._marquee_item is None:
            return
        rect = QRectF(self._marquee["start"], self.mapToScene(position)).normalized()
        self._marquee_item.setRect(rect)

    def _finish_marquee(self):
        """松开左键：按矩形选中相交的节点与连线。

        Note：
            节点用 `shape()`（圆形）判定，不能用 boundingRect 的方形——否则
            只框到圆外侧的空白也会选中节点。连线用曲线路径与矩形求交，
            Qt 内置框选只认 QGraphicsItem，自绘边必须在松手时自己算。
        """
        marquee = self._marquee
        item = self._marquee_item
        self._marquee = None
        self._marquee_item = None
        if item is None:
            return
        rect = item.rect()
        self._scene.removeItem(item)
        if marquee is None:
            return
        if rect.width() < 3.0 and rect.height() < 3.0:
            # 空白处单击：保留已清空的选择。
            self._draw_edges()
            self.selection_changed.emit()
            return
        selected = set(marquee["edges"])
        for geometry in self._edge_geometries:
            if geometry["path"].intersects(rect):
                selected.add(geometry["key"])
        for node in self._nodes.values():
            if node.shape().intersects(node.mapRectFromScene(rect)):
                node.setSelected(True)
        self.set_selected_edges(selected)
        self.selection_changed.emit()

    def _begin_node_drag(self, node, event):
        """处理节点左键按下：选中并准备拖动（支持多选一起移动）。

        Args：
            node：被按下的节点。
            event：鼠标事件。
        """
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl:
            node.setSelected(not node.isSelected())
        elif not node.isSelected():
            self._scene.clearSelection()
            node.setSelected(True)
        self.set_selected_edge(None)
        self.node_clicked.emit(node.scene_id)
        if not node.isSelected():
            self._drag_state = None
            return
        self._drag_state = {
            "start": self.mapToScene(event.position().toPoint()),
            "origins": {item.scene_id: item.pos() for item in self._selected_nodes()},
            "moved": False,
        }

    def mousePressEvent(self, event):
        """按下鼠标：分派平移、连线、节点拖动、边选中与框选。

        Args：
            event：鼠标事件。
        """
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton and bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        ):
            self._panning = True
            self._pan_anchor = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            # 右键交给 contextMenuEvent；这里吃掉，避免清空选择或启动框选。
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._link_source is not None:
                item = self.itemAt(event.position().toPoint())
                if isinstance(item, SceneNode):
                    self._complete_link(item.scene_id)
                else:
                    self._drop_link()
                event.accept()
                return
            item = self.itemAt(event.position().toPoint())
            if isinstance(item, SceneNode):
                self._begin_node_drag(item, event)
                event.accept()
                return
            key = self._edge_at(event.position().toPoint())
            if key is not None:
                self._scene.clearSelection()
                ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if ctrl:
                    # Ctrl+点击：在已有选中集合里增减，支持多选连线。
                    if key in self._selected_edges:
                        self._selected_edges.discard(key)
                    else:
                        self._selected_edges.add(key)
                    primary = key if key in self._selected_edges else None
                    self.set_selected_edges(self._selected_edges, primary)
                else:
                    self.set_selected_edge(key)
                self.edge_clicked.emit(key[0], key[1])
                # 先记录按下位置：松手算「选中」，拖出 5px 才算「重连」。
                # Ctrl 多选时不进入重连，否则一次取消选择会被判成拖拽。
                self._pending_edge_drag = (
                    None
                    if ctrl
                    else {
                        "key": key,
                        "end": self._edge_end_at(key, event.position().toPoint()),
                        "press": event.position().toPoint(),
                    }
                )
                event.accept()
                return
            self._begin_marquee(event)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """移动鼠标：平移、连线预览、拖动节点或悬停光标。

        Args：
            event：鼠标事件。
        """
        pos = event.position().toPoint()
        if self._panning and self._pan_anchor is not None:
            delta = pos - self._pan_anchor
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_anchor = pos
            event.accept()
            return
        if self._link_source is not None:
            self._update_link(self.mapToScene(pos))
            self._update_link_hover(pos)
            event.accept()
            return
        if self._drag_state is not None and event.buttons() & Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(pos)
            delta = scene_pos - self._drag_state["start"]
            if abs(delta.x()) + abs(delta.y()) > 1.5:
                self._drag_state["moved"] = True
            for scene_id, origin in self._drag_state["origins"].items():
                node = self._nodes.get(scene_id)
                if node is not None:
                    node.setPos(origin + delta)
            # 节点一动就重绘全部边：不重绘的表现是「线还在原位」。
            self._draw_edges()
            self._expand_scene_rect()
            event.accept()
            return
        if self._pending_edge_drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._editable:
                self._pending_edge_drag = None
            else:
                pending = self._pending_edge_drag
                if (pos - pending["press"]).manhattanLength() > 5:
                    self._pending_edge_drag = None
                    self._start_edge_relink(pending["key"], pending["end"])
                event.accept()
                return
        if self._marquee is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._update_marquee(event.position().toPoint())
            event.accept()
            return
        self._update_cursor(pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """松开鼠标：结束平移或节点拖动。

        Args：
            event：鼠标事件。
        """
        if self._panning:
            self._panning = False
            self._pan_anchor = None
            self.unsetCursor()
            event.accept()
            return
        if self._link_source is not None and self._link_drag_release and event.button() == Qt.MouseButton.LeftButton:
            # 从旧线拖出后直接松手：落在节点上重连，落在空白处删除。
            item = self.itemAt(event.position().toPoint())
            if isinstance(item, SceneNode):
                self._complete_link(item.scene_id)
            else:
                self._drop_link()
            self._link_drag_release = False
            event.accept()
            return
        if self._drag_state is not None:
            moved = self._drag_state["moved"]
            self._drag_state = None
            if moved:
                self._expand_scene_rect()
                self.positions_changed.emit(self.remember_positions())
            event.accept()
            return
        if self._pending_edge_drag is not None:
            self._pending_edge_drag = None
            event.accept()
            return
        if self._marquee is not None:
            self._finish_marquee()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """双击连线：发出双击信号，供探索页快速应用候选。

        Args：
            event：鼠标事件。
        """
        if event.button() == Qt.MouseButton.LeftButton and self._link_source is None:
            key = self._edge_at(event.position().toPoint())
            if key is not None:
                self.set_selected_edge(key)
                self.edge_double_clicked.emit(key[0], key[1])
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        """鼠标离开画布时清掉悬停高亮。

        Args：
            event：离开事件。
        """
        super().leaveEvent(event)
        if self._hover_edge is not None:
            self._hover_edge = None
            self._draw_edges()

    def contextMenuEvent(self, event):
        """右键菜单分派。

        Args：
            event：上下文菜单事件。

        Note：
            不可编辑页（探索页）也要能右键：空白出「重置视图 / 自动布局」，
            连线出「应用 / 忽略」。节点右键只在可编辑页发出，探索页不提供
            新建或倒序连线。
        """
        if self._link_source is not None:
            self.cancel_link()
            event.accept()
            return
        pos = event.pos()
        item = self.itemAt(pos)
        if isinstance(item, SceneNode):
            if self._editable:
                self.node_context_menu.emit(item.scene_id, event.globalPos())
            event.accept()
            return
        key = self._edge_at(pos)
        if key is not None:
            # 右键未选中的边时先单选它；右键已选中的边时保留整个多选集合。
            if key not in self._selected_edges:
                self.set_selected_edge(key)
            else:
                self._selected_edge = key
            self.edge_context_menu.emit(key[0], key[1], event.globalPos())
        else:
            self.canvas_context_menu.emit(event.globalPos())
        event.accept()

    def keyPressEvent(self, event):
        """Esc 取消连线。

        Args：
            event：键盘事件。
        """
        if event.key() == Qt.Key.Key_Escape and self._link_source is not None:
            self.cancel_link()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        """滚轮缩放，按滚动量连续调整并夹在上下限内。

        Args：
            event：滚轮事件。

        Note：
            原先每格固定乘 1.15，连续滚动时累积很快、触控板的小增量也会被当成
            一整格。改为按 `angleDelta / 120` 的指数缩放：一格 8%，小增量按比例
            生效；夹取后统一换算成实际倍率，避免在边界附近突然跳一大步。
        """
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        if delta == 0:
            event.accept()
            return
        factor = (1.0 + ZOOM_STEP) ** (delta / 120.0)
        current = self.transform().m11()
        if current <= 0:
            event.accept()
            return
        target = min(ZOOM_MAX, max(ZOOM_MIN, current * factor))
        factor = target / current
        if abs(factor - 1.0) > 1e-6:
            self.scale(factor, factor)
            self._sync_text_visibility()
            self._update_zoom_label()
        event.accept()


def _stretch_to_viewport(points, view_width, view_height):
    """按视口宽高比拉伸布局，避免横向视口里排成竖长条。

    Args：
        points：`{scene_id: (x, y)}` 画布坐标。
        view_width：视口宽度。
        view_height：视口高度。

    Returns：
        调整后的坐标字典。

    Note：
        只拉伸较窄的那个轴，不压缩较宽的轴——压缩会让最小间距失效。拉伸上限
        `MAX_LAYOUT_STRETCH`：再大链式图会被拉成一条横线，边与标签挤成一列。
        拉伸会放大内容包围盒、把自适应比例压低：7 个节点的环状图被拉 4 倍后
        缩放只剩 74%，节点与文字都看不清。因此拉伸后要估算一次缩放比例，
        若比不拉伸时明显更小（且低于 1.0），放弃拉伸、保留自然布局。
    """
    if len(points) < 2 or view_width < 120 or view_height < 120:
        return points
    xs = [point[0] for point in points.values()]
    ys = [point[1] for point in points.values()]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if width <= 1e-6 or height <= 1e-6:
        return points
    usable_width = max(1.0, float(view_width) - FIT_MARGIN * 2)
    usable_height = max(1.0, float(view_height) - FIT_MARGIN * 2)
    target_ratio = usable_width / usable_height
    current_ratio = width / height
    scale_x = 1.0
    scale_y = 1.0
    if current_ratio < target_ratio:
        scale_x = min(MAX_LAYOUT_STRETCH, target_ratio / current_ratio)
    else:
        scale_y = min(MAX_LAYOUT_STRETCH, current_ratio / target_ratio)
    if scale_x == 1.0 and scale_y == 1.0:
        return points
    original_fit = min(usable_width / max(1.0, width), usable_height / max(1.0, height))
    stretched_fit = min(
        usable_width / max(1.0, width * scale_x),
        usable_height / max(1.0, height * scale_y),
    )
    if stretched_fit < 1.0 and stretched_fit < original_fit * 0.9:
        return points
    center_x = (max(xs) + min(xs)) / 2.0
    center_y = (max(ys) + min(ys)) / 2.0
    return {
        scene_id: (
            center_x + (x - center_x) * scale_x,
            center_y + (y - center_y) * scale_y,
        )
        for scene_id, (x, y) in points.items()
    }


def _relax_positions(points, min_distance, isolated=None, isolated_distance=None):
    """把相互靠得太近的节点推开，直到满足各自的最小中心距。

    Args：
        points：`{scene_id: (x, y)}`，画布坐标。
        min_distance：两个**都有连线**的节点之间允许的最小中心距。
        isolated：孤岛场景 id 集合（度为 0）。省略表示没有孤岛。
        isolated_distance：任一端是孤岛时的最小中心距；省略时沿用
            `min_distance`。

    Returns：
        调整后的坐标字典。

    Note：
        spring 布局只保证「大致分散」，节点多时仍会出现圆边相切。这里做一次
        确定性斥力松弛：不引入随机数，同一份布局每次结果一致。
        孤岛不参与拓扑阅读，用更小的间距约束，避免它们以主图的间距散在外围。
    """
    isolated = isolated or set()
    isolated_distance = min_distance if isolated_distance is None else isolated_distance
    items = [(scene_id, [float(x), float(y)]) for scene_id, (x, y) in points.items()]
    for _ in range(200):
        moved = False
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                ax, ay = items[i][1]
                bx, by = items[j][1]
                dx, dy = bx - ax, by - ay
                distance = math.hypot(dx, dy)
                limit = isolated_distance if items[i][0] in isolated or items[j][0] in isolated else min_distance
                if distance >= limit:
                    continue
                if distance < 1e-6:
                    dx, dy, distance = 1.0, 0.0, 1.0
                push = (limit - distance) * 0.5 * 0.65
                ux, uy = dx / distance, dy / distance
                items[i][1][0] -= ux * push
                items[i][1][1] -= uy * push
                items[j][1][0] += ux * push
                items[j][1][1] += uy * push
                moved = True
        if not moved:
            break
    return {scene_id: (xy[0], xy[1]) for scene_id, xy in items}


def _pack_isolated(points, isolated, distance):
    """把孤岛排成主图下方的均匀网格。

    Args：
        points：`{scene_id: (x, y)}`，画布坐标。
        isolated：孤岛场景 id 集合。
        distance：孤岛之间的最小中心距。

    Returns：
        调整后的坐标字典。

    Note：
        spring 布局把度为 0 的节点甩在外圈，间距取决于斥力，既远又不均匀。
        孤岛不参与拓扑阅读，整齐排在主图下方更省视野：列数取 √n，步长按
        `distance × 1.05`，同一行、同一列的间距完全一致。
        全部节点都是孤岛时从原点开始排网格；主图节点一律不动。
    """
    ids = sorted(scene_id for scene_id in isolated if scene_id in points)
    if not ids:
        return points
    result = dict(points)
    anchors = [point for scene_id, point in points.items() if scene_id not in isolated]
    step = distance * 1.05
    if anchors:
        start_x = min(point[0] for point in anchors)
        start_y = max(point[1] for point in anchors) + step
    else:
        start_x, start_y = 0.0, 0.0
    columns = max(1, math.ceil(math.sqrt(len(ids))))
    for index, scene_id in enumerate(ids):
        row, column = divmod(index, columns)
        result[scene_id] = (start_x + column * step, start_y + row * step)
    return result


def _segment_crosses(first, second, third, fourth):
    """判断两条线段是否严格相交（共享端点、共线都不算）。

    Args：
        first：第一条线段的起点 `(x, y)`。
        second：第一条线段的终点。
        third：第二条线段的起点。
        fourth：第二条线段的终点。

    Returns：
        相交返回 True。
    """

    def orient(origin, left, right):
        return (left[0] - origin[0]) * (right[1] - origin[1]) - (left[1] - origin[1]) * (right[0] - origin[0])

    d1 = orient(third, fourth, first)
    d2 = orient(third, fourth, second)
    d3 = orient(first, second, third)
    d4 = orient(first, second, fourth)
    return d1 * d2 < 0 and d3 * d4 < 0


def _count_crossings(points, segments):
    """统计线段集合的交叉数。

    Args：
        points：`{scene_id: (x, y)}`。
        segments：`[(from_scene, to_scene)]`，自环会被跳过。

    Returns：
        交叉对数量。
    """
    total = 0
    for left in range(len(segments)):
        a, b = segments[left]
        if a == b or a not in points or b not in points:
            continue
        for right in range(left + 1, len(segments)):
            c, d = segments[right]
            if c == d or c not in points or d not in points:
                continue
            if len({a, b, c, d}) < 4:
                continue
            if _segment_crosses(points[a], points[b], points[c], points[d]):
                total += 1
    return total


def _reduce_crossings(points, edges, rounds=2, max_nodes=40):
    """用成对位置交换贪心消减边交叉。

    Args：
        points：`{scene_id: (x, y)}` 画布坐标。
        edges：`[(from_scene, to_scene)]`，有向边按无向段处理。
        rounds：最多扫描几轮，每轮尝试所有节点对。
        max_nodes：超过该节点数直接返回原布局，避免 O(n² × E²) 卡住界面。

    Returns：
        调整后的坐标字典。

    Note：
        `spring_layout` 不保证无交叉，项目又不带 scipy，无法用
        `kamada_kawai_layout`。这里做确定性交换：交换两个节点的位置，交叉数
        下降才保留。交换不改变两两距离的多重集合，最小间距仍然成立。只调整
        位置、不增删节点，所以不会破坏边与孤岛判定。
    """
    if len(points) < 3 or not edges:
        return points
    segments = [(source, target) for source, target in edges if source != target]
    ids = [scene_id for scene_id in points if scene_id in {end for seg in segments for end in seg}]
    if len(ids) < 3 or len(ids) > max_nodes:
        return points
    coords = {scene_id: (float(x), float(y)) for scene_id, (x, y) in points.items()}
    best = _count_crossings(coords, segments)
    if best == 0:
        return points
    for _ in range(rounds):
        improved = False
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                left, right = ids[i], ids[j]
                coords[left], coords[right] = coords[right], coords[left]
                crossings = _count_crossings(coords, segments)
                if crossings < best:
                    best = crossings
                    improved = True
                    if best == 0:
                        return coords
                else:
                    coords[left], coords[right] = coords[right], coords[left]
        if not improved:
            break
    return coords


def _to_point(pair):
    """把布局单位转换成画布坐标。

    Args：
        pair：`(x, y)` 二元组。

    Returns：
        `QPointF` 实例。
    """
    return QPointF(float(pair[0]) * NODE_GAP, -float(pair[1]) * NODE_GAP)


def _point_distance(first, second):
    """计算两点距离。

    Args：
        first：`QPointF`。
        second：`QPointF`。

    Returns：
        浮点距离。
    """
    return math.hypot(first.x() - second.x(), first.y() - second.y())
