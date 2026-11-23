# -*- coding: utf-8 -*-
"""流转图：建图、寻路与布局坐标。

图由**路由表**（`routes.RouteTable`）构成：一条路由只要来源是 `manual` 或
`confirmed`，就是一条可寻路的边。`explored` 的边（探索产出、未人工确认）默认
不进图——一条误边会让后续**每一次**导航都点错地方，而错误表现为「到了奇怪的
地方」，不是报错。界面要看「探索探到了什么」时用 `include_pending=True` 把
它们画出来，但寻路仍然不会走。

**场景与路由分开存**：场景文件只描述画面（特征、区域定义、探索动作），路由表
（库根的 `routes.json`）单独描述画面之间怎么跳转。原先边藏在区域字段里，导致
同一份事实有两处落点，改一处漏一处就分叉。

本模块依赖 networkx，**不导入 `ok` 与 `PySide6`**：寻路是纯算法，界面只在
拿到布局坐标后绘制。
"""

import networkx as nx

from extensions.capabilities.scene_manager import model


class GraphError(ValueError):
    """建图或寻路的前置条件不满足。"""


class RouteStep:
    """路径上的一步：在某个场景点某个区域，到达下一个场景。

    Attributes:
        from_scene: 起点场景 id。
        area_name: 被点击的区域名。
        to_scene: 到达场景 id。
        after_wait: 该区域配置的额外等待秒数。
    """

    def __init__(self, from_scene, area_name, to_scene, after_wait=0.0):
        """构造一步。

        Args：
            from_scene：起点场景 id。
            area_name：区域名。
            to_scene：到达场景 id。
            after_wait：额外等待秒数。
        """
        self.from_scene = from_scene
        self.area_name = area_name
        self.to_scene = to_scene
        self.after_wait = after_wait

    def to_dict(self):
        """序列化为字典，供结果对象与界面展示。

        Returns：
            字典。
        """
        return {
            "from": self.from_scene,
            "area": self.area_name,
            "to": self.to_scene,
            "after_wait": self.after_wait,
        }

    def __repr__(self):
        """返回便于排查的字符串表示。"""
        return f"RouteStep({self.from_scene}--{self.area_name}-->{self.to_scene})"

    def __eq__(self, other):
        """按字段比较相等。"""
        if not isinstance(other, RouteStep):
            return NotImplemented
        return (
            self.from_scene == other.from_scene
            and self.area_name == other.area_name
            and self.to_scene == other.to_scene
        )


class SceneGraph:
    """场景库的流转图。

    Attributes:
        graph: networkx 有向图，节点为场景 id，边属性含 `area` 与 `after_wait`。
        scenes: 场景 id → `model.Scene` 的映射。
    """

    def __init__(self):
        """构造空图。"""
        self.graph = nx.DiGraph()
        self.scenes = {}

    @classmethod
    def build(cls, scenes, routes=None, include_pending=False):
        """从场景列表与路由表建图。

        孤立场景也会成为节点（没有出边），因为「图里有这个场景」与「它能不能到别处」
        是两件事——界面上必须显示孤立节点，否则用户会以为场景没被加载。

        Args：
            scenes：`model.Scene` 列表。
            routes：`routes.RouteTable` 实例；省略时按「没有任何边」建图。
            include_pending：是否把 `explored`（待确认）的边也画进图。默认 False
                ——寻路绝不能走它们。流转图要看「探索探到了什么」时传 True，此时
                这些边会以不同样式呈现（由界面读边的 `source` 属性决定）。

        Returns：
            `SceneGraph` 实例。

        Raises：
            GraphError：出现指向不存在场景的边。悬空边必须报错而非静默忽略：
                它意味着场景库已不一致，而忽略后表现为「这条路走不通」，
                与「本来就没配这条路」无法区分。

        Note：
            **边只从路由表来，不再从区域上读**。区域只描述「这块地方在哪」，跳转
            关系全在 `routes.json`。两处各存一份的话，改一处漏一处就分叉，而分叉
            的表现是「流转图上有边，点下去到不了」——不报错。
        """
        instance = cls()
        for scene in scenes:
            instance.scenes[scene.scene_id] = scene
            instance.graph.add_node(scene.scene_id, name=scene.name)

        if routes is None:
            return instance

        dangling = []
        for route in routes:
            if not route.routable() and not include_pending:
                continue
            if route.from_scene not in instance.scenes:
                # 来源场景不在本次建图范围内：可能是草稿区里的场景（未纳入正式
                # 库），不算悬空——它的边在草稿转正后才需要。
                continue
            if route.target_scene not in instance.scenes:
                # 目标仍是草稿：它尚未转正，本就不在正式场景集里。探索产出的
                # 草稿边是合法数据（草稿转正时会与入边一起升档），不能按悬空边
                # 报错——否则一次探索发现新场景，流转图整块就打不开了。
                if model.is_draft_id(route.target_scene):
                    continue
                dangling.append((route.from_scene, route.area_name, route.target_scene))
                continue
            # 区域可能已被删除而路由残留：那种边点不到，也属不一致。
            source_scene = instance.scenes[route.from_scene]
            if source_scene.find_area(route.area_name) is None:
                dangling.append((route.from_scene, route.area_name, route.target_scene))
                continue
            instance.graph.add_edge(
                route.from_scene,
                route.target_scene,
                area=route.area_name,
                after_wait=route.after_wait,
                source=route.source,
            )
        if dangling:
            detail = "、".join(f"{src}.{area}→{dst}" for src, area, dst in dangling)
            raise GraphError(f"存在指向不存在场景的边：{detail}。请检查流转图上的边是否指向已删除的场景")
        return instance

    def scene_count(self):
        """返回节点数。

        Returns：
            场景数量。
        """
        return self.graph.number_of_nodes()

    def add_edge(self, from_scene, area_name, to_scene, after_wait=0.0, source="explored"):
        """增量并入一条边，并确保两端节点存在。

        Args：
            from_scene：来源场景 id。
            area_name：触发的区域名。
            to_scene：目标场景 id。
            after_wait：动作后等待秒数。
            source：边的来源标记，取 `model.EDGE_SOURCES`；默认 `explored`
                （探索现场写出来的边都是待确认的）。

        Note：
            **探索过程中必须实时并入新边**。探索开始时构造的图只是初始快照，
            而 BFS 需要「点进新场景后回退到上游继续展开」——回退靠
            `return_step` 找入边。图不更新时新场景没有任何入边，回退失败，
            上游场景的其余区域全部被跳过。

            现象极具误导性：探索「跑完了」，`stop_reason` 是 `completed`，
            但只访问了起点和一个邻居、采集 1 条边。

            两端节点用 `add_node` 兜底：探索可能先发现边、后落盘草稿场景，
            缺节点会让 networkx 自动建一个无属性的节点，后续按场景取数据时
            拿到 None。
        """
        for scene_id in (from_scene, to_scene):
            if scene_id not in self.graph:
                self.graph.add_node(scene_id, name=None)
        self.graph.add_edge(from_scene, to_scene, area=area_name, after_wait=after_wait, source=source)

    def edge_count(self):
        """返回边数。

        Returns：
            可寻路边数量。
        """
        return self.graph.number_of_edges()

    def has_scene(self, scene_id):
        """判断场景是否在图中。

        Args：
            scene_id：场景 id。

        Returns：
            存在时为 True。
        """
        return scene_id in self.graph

    def unknown_targets(self):
        """列出图中指向不存在场景的边（不抛异常的诊断入口）。

        可用在「想展示问题而不是直接失败」的场合。建图本身遇到悬空边会抛
        `GraphError`，这里返回内容供界面提示。

        Returns：
            `(from_scene, area_name, target)` 列表，按来源场景排序。
        """
        found = []
        for source, target, data in self.graph.edges(data=True):
            if not self.has_scene(target):
                found.append((source, data.get("area", ""), target))
        found.sort()
        return found

    def target_of_area(self, from_scene, area_name):
        """返回某区域在图里的目标场景。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。

        Returns：
            目标场景 id；该区域没有出边时返回空串。
        """
        for _source, target, payload in self.graph.out_edges(from_scene, data=True):
            if payload.get("area") == area_name:
                return target
        return ""

    def wait_for(self, from_scene, area_name):
        """返回某条边配置的「操作后等待」秒数。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。

        Returns：
            等待秒数；该区域没有出边时返回 0.0（不额外等待）。

        Note：
            与「区域上不再有 `after_wait`」配套：等待时长属于边的属性，随路由一起
            存。取不到边时返回 0 而不是默认 1.0——回退用的区域往往不是一条正式边
            （例如场景自己的「返回」按钮），给它凭空加一秒会拖慢每一次回退。
        """
        data = self.graph.get_edge_data(from_scene, None) or {}
        del data
        for _source, _target, payload in self.graph.out_edges(from_scene, data=True):
            if payload.get("area") == area_name:
                return payload.get("after_wait", 0.0)
        return 0.0

    def outgoing(self, scene_id):
        """列出一个场景的全部可寻路出边。

        Args：
            scene_id：场景 id。

        Returns：
            `(area_name, target_scene, after_wait)` 列表；场景不存在或没有出边时为空。
        """
        if not self.has_scene(scene_id):
            return []
        result = []
        for _source, target, data in self.graph.out_edges(scene_id, data=True):
            result.append((data["area"], target, data.get("after_wait", 0.0)))
        result.sort()
        return result

    def incoming(self, scene_id):
        """列出一个场景的全部入边。

        探索的回退逻辑用它在「场景没配返回区域」时反推如何回到上游。

        Args：
            scene_id：场景 id。

        Returns：
            `(from_scene, area_name)` 列表；无入边时为空。
        """
        if not self.has_scene(scene_id):
            return []
        result = []
        for source, _target, data in self.graph.in_edges(scene_id, data=True):
            result.append((source, data["area"]))
        result.sort()
        return result

    def return_step(self, scene_id):
        """返回从指定场景回退到上一层的建议步骤。

        在**当前场景**里找一个能回到上游的区域；没有时返回 None。

        Args：
            scene_id：场景 id。

        Returns：
            `(area_name, target_scene)`；无法回退时为 None。

        Note:
            **反推必须返回「当前场景里的区域」，不能直接拿入边的 area**。
            入边的 `area` 属于**上游场景**（A 上点 `a1` 到了 B），而回退要在
            **B 上点**某个区域才能回 A——拿 A 的 `a1` 去 B 里找，只会得到
            「返回区域 a1 已不存在」。

            正确的反推是：在 B 的出边里找指向 source 的那条，用它的 area。

            探索专用的「返回区域」标记（`Area.explore_return`）**不在这里读**：
            它只服务探索，运行期导航不该因为一个探索标注改变行为。探索侧会
            先按标记尝试，再退到本方法。
        """
        if scene_id not in self.scenes:
            return None
        incoming = self.incoming(scene_id)
        if not incoming:
            return None
        for source, _triggered_area in incoming:
            # 在当前场景的出边里找「回到该上游场景」的那条，用它的区域名。
            for area_name, target, _wait in self.outgoing(scene_id):
                if target == source:
                    return area_name, source
        # 有入边但当前场景没有回边：不能凭空点上游的区域名。
        return None

    def path(self, from_scene, to_scene):
        """求两点间最短路径。

        用无权最短路（每条边跳一次，代价相同）。边有权重时（如某些跳转更慢）
        可改为按 `after_wait` 加权，但那样「步数最少」与「耗时最少」会给出不同
        结果，需要先明确用户要哪一个——当前按步数，因为它可预测。

        Args：
            from_scene：起点场景 id。
            to_scene：目标场景 id。

        Returns：
            `RouteStep` 列表；起点等于终点时为空列表；不可达时返回 None。

        Raises：
            GraphError：任一场景不在图中。
        """
        for scene_id in (from_scene, to_scene):
            if not self.has_scene(scene_id):
                raise GraphError(f"场景 {scene_id!r} 不在流转图中")
        if from_scene == to_scene:
            return []
        try:
            nodes = nx.shortest_path(self.graph, from_scene, to_scene)
        except nx.NetworkXNoPath:
            return None
        except nx.NodeNotFound as error:
            raise GraphError(f"寻路失败：{error}") from error

        steps = []
        for index in range(len(nodes) - 1):
            source = nodes[index]
            target = nodes[index + 1]
            data = self.graph.get_edge_data(source, target) or {}
            steps.append(
                RouteStep(
                    source,
                    data.get("area", ""),
                    target,
                    data.get("after_wait", 0.0),
                )
            )
        return steps

    def reachable_from(self, scene_id):
        """列出从指定场景可达的全部场景（含自身）。

        Args：
            scene_id：场景 id。

        Returns：
            场景 id 集合；场景不在图中时为空集合。
        """
        if not self.has_scene(scene_id):
            return set()
        return nx.descendants(self.graph, scene_id) | {scene_id}

    def layout(self, seed=17, scale=1.0):
        """计算节点布局坐标，供界面绘制。

        用 `spring_layout` 并**固定随机种子**：布局每次打开都不同会让用户无法形成
        空间记忆，而「那个场景在右上角」正是他们找东西的方式。种子固定后同一份
        库的布局稳定可复现。

        Args：
            seed：随机种子。
            scale：坐标缩放系数。

        Returns：
            `{scene_id: (x, y)}` 字典；空图时为空字典。
        """
        if self.graph.number_of_nodes() == 0:
            return {}
        if self.graph.number_of_nodes() == 1:
            only = next(iter(self.graph.nodes))
            return {only: (0.0, 0.0)}
        positions = nx.spring_layout(self.graph, seed=seed, scale=scale)
        return {node: (float(point[0]), float(point[1])) for node, point in positions.items()}

    def isolated_scenes(self):
        """列出既无入边也无出边的场景。

        Returns：
            场景 id 列表，按 id 排序。
        """
        found = []
        for node in self.graph.nodes:
            if self.graph.in_degree(node) == 0 and self.graph.out_degree(node) == 0:
                found.append(node)
        return sorted(found)

    def edges_snapshot(self):
        """导出全部可寻路边，供界面表格展示。

        Returns：
            `(from_scene, area_name, to_scene, source, after_wait)` 列表，按来源排序。
        """
        rows = []
        for source, target, data in self.graph.edges(data=True):
            rows.append(
                (
                    source,
                    data.get("area", ""),
                    target,
                    data.get("source", model.SOURCE_MANUAL),
                    data.get("after_wait", 0.0),
                )
            )
        rows.sort()
        return rows
