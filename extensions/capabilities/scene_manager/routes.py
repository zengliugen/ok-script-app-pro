# -*- coding: utf-8 -*-
"""流转路由表：场景之间怎么跳转，单独一张表。

## 为什么要独立于场景

场景信息只该描述**这个画面是什么**：识别特征、可交互区域的定义、探索用的动作。
「点这个区域会到哪个场景」「点完等多久」属于**画面之间的关系**，不是画面自身的
属性——一旦写进 `Area`，同一份事实就有两处落点（区域里一份、流转图一份），
改一处漏一处就分叉。

分叉的表现是「流转图上有一条边，点下去到不了」，或者反过来「明明探索探到了，
流转图上看不见」。两者都不报错。

因此本模块把路由独立成库根下的 `routes.json`：

```json
{
  "schema_version": 1,
  "routes": [
    {
      "from": "scene-0001",
      "area": "开始按钮",
      "to": "scene-0002",
      "after_wait": 1.0,
      "source": "explored",
      "observations": 2
    }
  ]
}
```

## 键是 `(from, area)` 而不是区域里存目标

边由「来源场景 + 区域名」唯一确定。用这个键的理由与场景 id 一致：区域名在场景内
唯一，改名时随场景一起改即可；而如果把目标存在区域上，区域的序列化就同时承载了
「画面的定义」与「画面之间的连接」两件事。

## 边来源三档，寻路只走两档

`manual`（人工配）/ `explored`（探索产出待确认）/ `confirmed`（人工确认过）。
寻路只用 `manual` 与 `confirmed`——探索是无人值守跑出来的，一条误边会让**后续每
一次**导航都点错地方，而错误表现为「到了奇怪的地方」而非「报错」。

本模块**不导入 `ok` 与 `PySide6`**，也不依赖 `store`：它只处理一张表的内存模型与
JSON 形态，落盘位置由调用方给。
"""

import json
import os

from extensions.capabilities.scene_manager import model

# 磁盘文件名。改名会让已有库失联。
ROUTES_FILE = "routes.json"

# 表的 schema 版本。与场景的 `SCHEMA_VERSION` 独立演进：路由结构与场景结构
# 各自增删字段的时机不同，共用一个版本号会逼着两边同时升版。
ROUTES_SCHEMA = 1


class RoutesError(ValueError):
    """路由表读写或校验失败。"""


class Route:
    """一条跳转路由：在某个场景的某个区域上操作，会到达另一个场景。

    Attributes：
        from_scene：来源场景 id。
        area_name：被操作的区域名。
        target_scene：目标场景 id。
        after_wait：操作后额外等待秒数，用于动画较长的画面。
        source：来源档位，取 `model.EDGE_SOURCES` 之一。
        observations：被观测到的次数，探索重复命中时累加。
    """

    __slots__ = ("from_scene", "area_name", "target_scene", "after_wait", "source", "observations")

    def __init__(
        self,
        from_scene,
        area_name,
        target_scene,
        after_wait=1.0,
        source=model.SOURCE_MANUAL,
        observations=1,
    ):
        """构造并校验一条路由。

        Args：
            from_scene：来源场景 id。
            area_name：区域名，不能为空。
            target_scene：目标场景 id，不能为空。
            after_wait：操作后等待秒数，负数按 0 处理。
            source：来源档位。
            observations：观测次数，至少 1。

        Raises：
            RoutesError：区域名或目标场景为空，或来源档位非法。
        """
        if not from_scene:
            raise RoutesError("路由的来源场景不能为空")
        if not area_name:
            raise RoutesError("路由的区域名不能为空")
        if not target_scene:
            raise RoutesError(f"路由 {from_scene}.{area_name} 的目标场景不能为空")
        if source not in model.EDGE_SOURCES:
            raise RoutesError(f"未知的边来源 {source!r}，只支持 {model.EDGE_SOURCES}")
        self.from_scene = from_scene
        self.area_name = area_name
        self.target_scene = target_scene
        self.source = source
        try:
            self.after_wait = max(0.0, round(float(after_wait), 2))
        except (TypeError, ValueError) as error:
            raise RoutesError(f"额外等待必须是数值，收到 {after_wait!r}") from error
        try:
            self.observations = max(1, int(observations))
        except (TypeError, ValueError) as error:
            raise RoutesError(f"观测次数必须是整数，收到 {observations!r}") from error

    @property
    def key(self):
        """返回唯一键 `(from_scene, area_name)`。"""
        return (self.from_scene, self.area_name)

    def routable(self):
        """返回本路由是否可参与寻路。

        Returns：
            来源属于 `model.ROUTABLE_SOURCES` 时为 True。
        """
        return self.source in model.ROUTABLE_SOURCES

    def to_dict(self):
        """序列化为字典。

        Returns：
            与 :meth:`from_dict` 对称的字典。
        """
        return {
            "from": self.from_scene,
            "area": self.area_name,
            "to": self.target_scene,
            "after_wait": self.after_wait,
            "source": self.source,
            "observations": self.observations,
        }

    @classmethod
    def from_dict(cls, data):
        """从字典还原一条路由。

        Args：
            data：`to_dict` 产出的字典。

        Returns：
            `Route` 实例。

        Raises：
            RoutesError：缺少必需字段或取值非法。
        """
        if not isinstance(data, dict):
            raise RoutesError(f"路由条目必须是对象，收到 {type(data).__name__}")
        return cls(
            from_scene=data.get("from", ""),
            area_name=data.get("area", ""),
            target_scene=data.get("to", ""),
            after_wait=data.get("after_wait", 1.0),
            source=data.get("source", model.SOURCE_MANUAL),
            observations=data.get("observations", 1),
        )

    def __repr__(self):
        """返回便于排查的表示。"""
        return (
            f"Route({self.from_scene}.{self.area_name} → {self.target_scene}, "
            f"source={self.source}, obs={self.observations})"
        )


class RouteTable:
    """路由表：以 `(from_scene, area_name)` 为键的边集合。

    所有写操作都只改内存，落盘由 :meth:`save` 完成——这样探索过程中可以批量
    累积（每条边都落盘会让一次探索写几十次文件），而界面层改一条边后显式保存。
    """

    def __init__(self, routes=None):
        """构造路由表。

        Args：
            routes：可迭代的 `Route`；重复键时后者覆盖前者。
        """
        self._routes = {}
        for route in routes or ():
            self._routes[route.key] = route

    # --- 查询 ---------------------------------------------------------------

    def __len__(self):
        """返回路由条数。"""
        return len(self._routes)

    def clone(self):
        """返回一份深拷贝。

        Returns：
            新的 `RouteTable`；`Route` 也是新对象，改它不会影响原表。

        Note：
            路由页的编辑走工作副本：不克隆的话，页面上改一条边会直接改到库里
            缓存的表对象，既做不到「保存才生效」，也会让磁盘与内存悄悄不一致。
        """
        return RouteTable(Route.from_dict(route.to_dict()) for route in self._routes.values())

    def __iter__(self):
        """按「来源场景、区域名」排序迭代，保证界面与序列化顺序稳定。"""
        for key in sorted(self._routes):
            yield self._routes[key]

    def is_empty(self):
        """返回表是否为空。"""
        return not self._routes

    def get(self, from_scene, area_name):
        """按来源场景与区域名取路由。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。

        Returns：
            `Route` 实例；不存在时返回 None。
        """
        return self._routes.get((from_scene, area_name))

    def target_of(self, from_scene, area_name):
        """返回某区域的目标场景 id。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。

        Returns：
            目标场景 id；没有路由时返回空串。**返回空串而不是 None**，让调用方
            可以直接用于字符串判断与拼接。
        """
        route = self.get(from_scene, area_name)
        return route.target_scene if route else ""

    def after_wait_of(self, from_scene, area_name):
        """返回某区域操作后的等待秒数。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。

        Returns：
            等待秒数；没有路由时按历史默认值 1.0 处理。

        Note：
            默认值取 1.0 而不是 0：旧实现在区域上就是这么默认的，且动画类画面
            立刻判画面会误判成「没跳转」。宁可多等一秒，也不要误判。
        """
        route = self.get(from_scene, area_name)
        return route.after_wait if route else 1.0

    def outgoing(self, from_scene):
        """列出某场景的全部出边。

        Args：
            from_scene：来源场景 id。

        Returns：
            `Route` 列表，按区域名排序。
        """
        found = [route for key, route in self._routes.items() if key[0] == from_scene]
        found.sort(key=lambda item: item.area_name)
        return found

    def incoming(self, target_scene):
        """列出指向某场景的全部入边。

        Args：
            target_scene：目标场景 id。

        Returns：
            `Route` 列表，按来源场景、区域名排序。
        """
        found = [route for route in self._routes.values() if route.target_scene == target_scene]
        found.sort(key=lambda item: (item.from_scene, item.area_name))
        return found

    def routable_routes(self):
        """返回可参与寻路的路由。

        Returns：
            `Route` 列表。
        """
        return [route for route in self if route.routable()]

    # --- 修改 ---------------------------------------------------------------

    def put(self, route):
        """写入一条路由，同键覆盖。

        Args：
            route：`Route` 实例。
        """
        self._routes[route.key] = route

    def set_target(self, from_scene, area_name, target_scene, after_wait=None, source=None):
        """设置某区域的目标场景；目标为空时删除该路由。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。
            target_scene：目标场景 id；空串表示「这个区域不构成边」。
            after_wait：等待秒数；None 表示沿用已有值或默认 1.0。
            source：来源档位；None 表示沿用已有值，新建时用 `manual`。

        Returns：
            写入后的 `Route`；目标为空而删除时为 None。
        """
        existing = self.get(from_scene, area_name)
        if not target_scene:
            self.remove(from_scene, area_name)
            return None
        if after_wait is None:
            # 沿用已有值；新建时用 1.0（与 `after_wait_of` 的默认口径一致）。
            stored_wait = existing.after_wait if existing else 1.0
        else:
            stored_wait = after_wait
        route = Route(
            from_scene,
            area_name,
            target_scene,
            after_wait=stored_wait,
            source=source or (existing.source if existing else model.SOURCE_MANUAL),
            observations=existing.observations if existing else 1,
        )
        self.put(route)
        return route

    def remove(self, from_scene, area_name):
        """删除一条路由。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。

        Returns：
            真的删掉了时为 True。
        """
        return self._routes.pop((from_scene, area_name), None) is not None

    def remove_area(self, from_scene, area_name):
        """删除一条路由（别名，语义上强调「区域被删了，边跟着走」）。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。

        Returns：
            真的删掉了时为 True。
        """
        return self.remove(from_scene, area_name)

    def remove_scene(self, scene_id):
        """删除与某个场景相关的全部路由（出边与入边都删）。

        Args：
            scene_id：场景 id。

        Returns：
            删除的路由条数。
        """
        keys = [key for key, route in self._routes.items() if key[0] == scene_id or route.target_scene == scene_id]
        for key in keys:
            del self._routes[key]
        return len(keys)

    def rename_area(self, scene_id, old_name, new_name):
        """区域改名时同步改键。

        Args：
            scene_id：场景 id。
            old_name：旧区域名。
            new_name：新区域名。

        Returns：
            发生了迁移时为 True。

        Note：
            **不做的话边的键会指向一个已经不存在的区域**，表现为「边缘无故消失」
            或「探索又把它探了一遍」。
        """
        route = self.get(scene_id, old_name)
        if route is None or old_name == new_name:
            return False
        del self._routes[(scene_id, old_name)]
        route.area_name = new_name
        self._routes[route.key] = route
        return True

    def migrate_scene(self, old_id, new_id):
        """把指向某场景的边改写到新 id（草稿转正时用）。

        Args：
            old_id：旧场景 id。
            new_id：新场景 id。

        Returns：
            被改写的路由条数（出边与入边都算）。

        Note：
            **这是草稿转正必须做的一步**。只改草稿自己的 id 而不改指向它的边，
            库里就留下一条指向不存在场景的边，建图会直接抛
            「存在指向不存在场景的边」——而那其实是代码自己造出来的，界面上却
            提示「请检查场景库是否被手工改动」。
        """
        touched = 0
        for route in list(self._routes.values()):
            changed = False
            if route.from_scene == old_id:
                route.from_scene = new_id
                changed = True
            if route.target_scene == old_id:
                route.target_scene = new_id
                changed = True
            if changed:
                touched += 1
        # 改写后可能撞键（新旧 id 的同名区域），重建索引。
        self._routes = {route.key: route for route in self._routes.values()}
        return touched

    def set_source(self, from_scene, area_name, source):
        """改一条路由的来源档位（确认探索边时用）。

        Args：
            from_scene：来源场景 id。
            area_name：区域名。
            source：目标档位。

        Returns：
            改写成功时为 True；路由不存在或档位非法时为 False。
        """
        route = self.get(from_scene, area_name)
        if route is None or source not in model.EDGE_SOURCES:
            return False
        route.source = source
        return True

    def confirm_scene_edges(self, scene_id, include_incoming=False):
        """把某场景的出边（可选含入边）升为 `confirmed`。

        Args：
            scene_id：场景 id。
            include_incoming：是否把**指向该场景的边**也一起升档。

        Returns：
            升档的条数。

        Note：
            草稿转正时调用：草稿里的边都是探索产物，人工确认整条草稿即等于确认
            了它的出边。只升 `explored` 的，`manual` 保持不变——人工配的边不该
            被标成「探索来的」。

            `include_incoming` 的意义：确认一条草稿时，用户看到的是「这个画面是
            对的」，那么「点父场景的那个按钮能到这里」同样被确认了。不升入边的话
            转正完流转图里那条边仍是待确认状态——用户刚点过「确认」，界面上却还
            要再确认一次，而两次确认之间没有任何提示。
        """
        touched = 0
        for route in self.outgoing(scene_id):
            if route.source == model.SOURCE_EXPLORED:
                route.source = model.SOURCE_CONFIRMED
                touched += 1
        if include_incoming:
            for route in self.incoming(scene_id):
                if route.source == model.SOURCE_EXPLORED:
                    route.source = model.SOURCE_CONFIRMED
                    touched += 1
        return touched

    # --- 序列化 -------------------------------------------------------------

    def to_dict(self):
        """序列化为可写入 JSON 的字典。

        Returns：
            含 `schema_version` 与 `routes` 的字典。
        """
        return {
            "schema_version": ROUTES_SCHEMA,
            "routes": [route.to_dict() for route in self],
        }

    @classmethod
    def from_dict(cls, data):
        """从字典还原路由表。

        Args：
            data：`to_dict` 产出的字典；None 或空字典视为空表。

        Returns：
            `RouteTable` 实例。

        Raises：
            RoutesError：条目无法解析。

        Note：
            单条坏数据**跳过而不是整表失败**：一条手改坏的边不该让整个流转图打
            不开。跳过时无法提示用户（本模块不碰界面），但至少其余边仍可用，
            且下次保存会把坏条目丢掉——它已经不在内存里了。
        """
        if not data:
            return cls()
        entries = data.get("routes") or []
        routes = []
        for entry in entries:
            try:
                routes.append(Route.from_dict(entry))
            except RoutesError:
                continue
        return cls(routes)

    def load(self, path):
        """从文件加载，原地替换表内容。

        Args：
            path：`routes.json` 路径。

        Returns：
            self，便于链式调用。

        Raises：
            RoutesError：文件存在但无法解析为 JSON 对象。

        Note：
            文件不存在**不是错误**：老库没有这张表，等价于「还没有任何路由」。
        """
        if not os.path.isfile(path):
            self._routes = {}
            return self
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise RoutesError(f"路由表无法读取：{error}") from error
        loaded = self.from_dict(data)
        self._routes = loaded._routes
        return self

    def save(self, path):
        """写入文件（UTF-8、缩进 2、无 BOM）。

        Args：
            path：`routes.json` 路径。

        Raises：
            RoutesError：写入失败。
        """
        directory = os.path.dirname(os.path.abspath(path))
        try:
            os.makedirs(directory, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except OSError as error:
            raise RoutesError(f"路由表无法写入：{error}") from error
