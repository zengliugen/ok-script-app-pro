# -*- coding: utf-8 -*-
"""场景库的读写、编辑区/产物区导出与导入。

## 两区结构（对齐上游 `ok_templates/` → `assets/templates/` 的做法）

```text
ok_scenes/                    # 编辑区：不入库，只在本机
└── scene-0001/
    ├── scene.json            # 场景定义（特征形状 + 交互区域 + 边）
    ├── base.png             # 场景基准图（整幅截图，已脱敏）
    └── templates/            # 编辑态的模板与掩码

assets/scenes/                # 产物区：入库，运行期只读
├── library.json
├── scene-0001/
│   ├── scene.json
│   ├── base.png             # 场景基准图（整幅截图，已脱敏）
│   └── templates/
│       ├── <特征>.png       # 外接矩形内的真实像素
│       └── <特征>_mask.png  # 同尺寸掩码
└── _drafts/
```

**为什么分两区**：编辑区要随时改形状、改角度、换截图，这些都只对本机有意义；产物区
是要入库、要随包分发的东西，必须干净。混在一起时，「哪些文件该提交」只能靠一条条
gitignore 反选判断，而漏判的表现是本地实测数据进了发布包。

**原始帧一律不落盘**：未遮罩的整帧截图含游戏 UID，写进任何目录都是泄漏面——
即使目录不入库，按目录打包的动作（如 `export_library`）也会把它带出去。因此它只
在弹窗内存里短暂存在，用于当次裁模板；重新打开已有场景时改用遮罩后的
`base.png` 当模板源（落在遮罩区内的特征本来就会被判为无效，配置页会警告）。
## 目录布局（产物区，一场景一目录）

一场景一目录，删场景即删目录，模板、掩码与基准图不会成为孤儿。

**草稿区与正式区严格隔离**：`load_library` 只读正式目录，不读 `_drafts/`。
草稿是探索产物，未经人工确认进识别会让导航走到未验证的场景。

本模块**不导入 `ok` 与 `PySide6`**：它是场景库的唯一输入口，必须能在无 GUI
环境完整测试。文件与图片读写只用标准库与 numpy（numpy 是框架的既有依赖）。
"""

import json
import os
import shutil
import tempfile
import zipfile

from extensions.capabilities.scene_manager import model, routes

# 库级文件名与目录名。这些是磁盘契约，改名会让已有场景库失联。
LIBRARY_FILE = "library.json"
README_FILE = "README.md"
SCENE_FILE = "scene.json"
# 场景基准图。整幅截图，已脱敏，是模板的裁剪来源、缩略图与分辨率依据。
#
# 原名 `preview.png` 语义有偏差：它不是「预览」，而是**该场景的基准画面**——
# 特征坐标按它换算、模板从它裁、库一致性按它的尺寸校验。叫 preview 会让人以为
# 它只是展示用的缩略图，从而误判「删了也不影响识别」。
BASE_FILE = "base.png"
# 旧文件名。读取时回退到它，使本次改名之前的库不必手工迁移；
# 写入一律用新名，因此场景被再次保存后旧文件自然废弃。
LEGACY_PREVIEW_FILE = "preview.png"
TEMPLATE_DIR = "templates"
DRAFT_DIR = "_drafts"
# 流转图的节点位置。只服务编辑器视图，**不导出到产物区**：产物区只需要场景与
# 路由，节点坐标是给人看的布局，不是运行期数据。
LAYOUT_FILE = "layout.json"

# 草稿 id 前缀。与正式场景的 `scene-` 前缀不同，避免两区互相误认。
DRAFT_ID_PREFIX = "draft-"
DRAFT_ID_PATTERN = model.DRAFT_ID_PATTERN

# 导出包内的清单文件名。
EXPORT_MANIFEST = "export.json"

# 新建场景的默认名主干。**不走翻译**：名称要落进 `scene.json`，是持久化数据，
# 随界面语言变化会让同一场景在不同语言下叫不同名字。
SCENE_NAME_PREFIX = "场景"


def scene_default_name(scene_id):
    """按场景 id 生成默认显示名（`scene-0005` → `场景 5`）。

    Args：
        scene_id：场景或草稿 id。

    Returns：
        默认显示名。id 格式不合法时退回 id 本身，不返回空串——`Scene.__post_init__`
        会把空名替换成 id，但那要多绕一层。

    Note：
        序号取 id 里的数字，不用「现有 场景N 的最大 N+1」。后者在库里存在用户
        自定义名的场景时会错位：实测 `scene-0004` 被叫成「场景 1」，因为名称从 1
        数起而 id 已经到 4。id 是唯一权威来源，名称跟着它走才不会分叉。
    """
    sequence = model.id_sequence(scene_id)
    if sequence <= 0:
        return scene_id or ""
    return f"{SCENE_NAME_PREFIX} {sequence}"


class SceneStoreError(RuntimeError):
    """场景库读写失败。"""


def _read_json(path):
    """读取 JSON 文件。

    Args：
        path：文件绝对路径。

    Returns：
        解析后的对象；文件不存在时返回 None。

    Raises：
        SceneStoreError：文件存在但无法解析。
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise SceneStoreError(f"读取 {path} 失败：{error}") from error


def _write_json(path, data):
    """原子地写入 JSON 文件（先写临时文件再替换）。

    直接覆盖在写入中断时会留下半截文件，而场景库的 JSON 是唯一副本。原子替换
    保证「要么是旧内容、要么是新内容」，不会出现两者都不是。

    Args：
        path：目标文件绝对路径。
        data：可序列化对象。

    Raises：
        SceneStoreError：写入失败。
    """
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
        handle_descriptor, temp_path = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=".json")
        os.close(handle_descriptor)
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(temp_path, path)
    except OSError as error:
        raise SceneStoreError(f"写入 {path} 失败：{error}") from error
    finally:
        if "temp_path" in locals() and os.path.exists(temp_path):
            os.remove(temp_path)


def _next_id(existing, prefix, pattern, counter):
    """按「已分配过的最大序号」推算下一个 id。

    **不使用「当前最大 id + 1」**：删掉最大 id 后该编号会被重新分配，而指向被删
    场景的边就成了悬空引用——它不报错，只是静默指向一个完全不同的新场景。
    因此取「现有 id 的最大序号」与「库元数据记录的计数器」两者较大值，计数器
    保证编号只增不复用。

    Args：
        existing：已有 id 的集合（可能已被删除一部分，故只能用于兜底）。
        prefix：前缀，如 `scene-`。
        pattern：匹配该前缀的正则，需有一个捕获数字的组。
        counter：库元数据记录的下一可用序号。

    Returns：
        形如 `scene-0001` 的新 id。
    """
    largest = max(0, int(counter) - 1)
    for item in existing:
        matched = pattern.match(item)
        if matched:
            largest = max(largest, int(matched.group(1)))
    return f"{prefix}{largest + 1:04d}", largest + 1


class SceneLibrary:
    """一个场景库的读写入口。

    所有写操作都会同步更新 `library.json` 与对应场景目录，不存在「只改内存不落盘」
    的状态——场景库一旦与内存不一致，界面显示的就是幻觉。

    Attributes：
        root：场景库根目录（绝对路径）。
        meta：库级元数据。
    """

    def __init__(self, root):
        """绑定一个库目录，不创建任何文件。

        Args：
            root：场景库根目录；不存在时由 :meth:`ensure_layout` 创建。
        """
        self.root = os.path.abspath(root)
        self.meta = model.LibraryMeta()
        # 流转路由表。**不随场景文件走**：场景只描述画面，路由描述画面之间怎么
        # 跳转，两者分开存（见 `routes` 模块的说明）。
        self.routes = routes.RouteTable()
        # 路由表是否已从磁盘读过。写操作前用它避免每次都重读文件。
        self._routes_ready = False
        # 计数器推进后必须落盘，否则下次启动会重新用到已删场景的编号。
        # 用脏标记而非「每次 save_scene 都写 library.json」：场景保存很频繁，
        # 无谓的库文件写入会让导入大库变慢。
        self._meta_dirty = False

    # --- 布局 ---------------------------------------------------------------

    @property
    def library_path(self):
        """返回库元数据文件路径。"""
        return os.path.join(self.root, LIBRARY_FILE)

    @property
    def routes_path(self):
        """返回流转路由表文件路径。"""
        return os.path.join(self.root, routes.ROUTES_FILE)

    @property
    def layout_path(self):
        """返回流转图节点位置文件路径。"""
        return os.path.join(self.root, LAYOUT_FILE)

    def load_layout(self):
        """读取节点位置覆盖表。

        Returns：
            `{scene_id: (x, y)}`；文件缺失或损坏时返回空字典。

        Note：
            损坏时静默回退自动布局：节点位置只是视图偏好，为它弹错会挡住整个
            流转页。缺失的场景由调用方按布局算法补位。
        """
        data = _read_json(self.layout_path)
        if not isinstance(data, dict):
            return {}
        positions = {}
        for scene_id, pair in (data.get("positions") or {}).items():
            try:
                positions[str(scene_id)] = (float(pair[0]), float(pair[1]))
            except (TypeError, ValueError, IndexError):
                continue
        return positions

    def save_layout(self, positions):
        """把节点位置写入 `layout.json`。

        Args：
            positions：`{scene_id: (x, y)}`，画布坐标。

        Raises：
            SceneStoreError：写入失败。
        """
        payload = {
            "schema_version": model.SCHEMA_VERSION,
            "positions": {
                str(scene_id): [round(float(pair[0]), 2), round(float(pair[1]), 2)]
                for scene_id, pair in positions.items()
            },
        }
        self.ensure_layout()
        try:
            _write_json(self.layout_path, payload)
        except OSError as error:
            raise SceneStoreError(f"写入节点位置失败：{error}") from error

    def clear_layout(self):
        """删除节点位置文件（重置布局时用）。

        Raises：
            SceneStoreError：删除失败。
        """
        try:
            if os.path.isfile(self.layout_path):
                os.remove(self.layout_path)
        except OSError as error:
            raise SceneStoreError(f"删除节点位置失败：{error}") from error

    @property
    def drafts_root(self):
        """返回草稿区目录路径。"""
        return os.path.join(self.root, DRAFT_DIR)

    def ensure_layout(self):
        """创建库目录骨架（幂等）。

        Returns：
            是否新建了库目录（原本不存在时为 True）。
        """
        created = not os.path.isdir(self.root)
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(self.drafts_root, exist_ok=True)
        if not os.path.isfile(self.library_path):
            _write_json(self.library_path, self.meta.to_dict())
        if not os.path.isfile(self.routes_path):
            # 建库时就落一张空表：让「路由存在但为空」与「根本没有这个概念」
            # 在磁盘上可区分。老库没有这个文件时由 `load_routes` 当作空表，
            # 首次保存路由时再生成。
            try:
                self.routes.save(self.routes_path)
            except routes.RoutesError:
                # 建表失败不该阻断建库——场景本身的读写仍然可用。
                pass
        readme_path = os.path.join(self.root, README_FILE)
        if not os.path.isfile(readme_path):
            with open(readme_path, "w", encoding="utf-8") as handle:
                handle.write(_README_TEXT)
        return created

    def scene_dir(self, scene_id):
        """返回某个场景的目录路径。

        Args：
            scene_id：场景 id 或草稿 id。

        Returns：
            绝对路径。
        """
        if self.is_draft_id(scene_id):
            return os.path.join(self.drafts_root, scene_id)
        return os.path.join(self.root, scene_id)

    @staticmethod
    def is_draft_id(scene_id):
        """判断 id 是否属于草稿区。

        Args：
            scene_id：待判断的 id。

        Returns：
            以 `draft-` 开头时为 True。
        """
        return bool(DRAFT_ID_PATTERN.match(scene_id or ""))

    # --- 读取 ---------------------------------------------------------------

    def load_meta(self):
        """读取库级元数据。

        Returns：
            `LibraryMeta` 实例；文件缺失时返回默认值（分辨率未记录）。

        Raises：
            SceneStoreError：结构非法，或版本高于本程序支持的范围。
        """
        data = _read_json(self.library_path)
        try:
            self.meta = model.LibraryMeta.from_dict(data)
        except model.SceneModelError as error:
            raise SceneStoreError(f"库元数据结构非法：{error}") from error
        self._meta_dirty = False
        return self.meta

    # --- 流转路由 -----------------------------------------------------------

    def load_routes(self):
        """读取流转路由表。

        Returns：
            `routes.RouteTable` 实例（同时缓存在 `self.routes`）。

        Raises：
            SceneStoreError：`routes.json` 存在但无法解析。

        Note：
            文件缺失不是错误——老库没有这张表，等价于「还没有任何路由」。
        """
        try:
            self.routes = routes.RouteTable().load(self.routes_path)
        except routes.RoutesError as error:
            raise SceneStoreError(str(error)) from error
        self._routes_ready = True
        return self.routes

    def save_routes(self):
        """把当前路由表写回磁盘。

        Raises：
            SceneStoreError：写入失败。
        """
        try:
            self.routes.save(self.routes_path)
        except routes.RoutesError as error:
            raise SceneStoreError(str(error)) from error

    def replace_routes(self, table):
        """整表替换并落盘。

        Args：
            table：新的 `routes.RouteTable`。

        Raises：
            SceneStoreError：写入失败。

        Note：
            路由页的编辑走工作副本，保存时才调这里。传进来的表会再克隆一次：
            调用方之后继续改它，不应影响已经落盘的库对象。
        """
        self.routes = table.clone()
        self._routes_ready = True
        self.save_routes()

    def route_target(self, scene_id, area_name):
        """返回某区域的目标场景 id（空串表示没有边）。

        Args：
            scene_id：来源场景 id。
            area_name：区域名。

        Returns：
            目标场景 id 或空串。
        """
        return self.routes.target_of(scene_id, area_name)

    def route_after_wait(self, scene_id, area_name):
        """返回某区域操作后的等待秒数。

        Args：
            scene_id：来源场景 id。
            area_name：区域名。

        Returns：
            等待秒数；没有路由时为默认的 1.0。
        """
        return self.routes.after_wait_of(scene_id, area_name)

    def set_route(self, scene_id, area_name, target_scene, after_wait=None, source=None):
        """设置一条路由并落盘；目标为空即删除。

        Args：
            scene_id：来源场景 id。
            area_name：区域名。
            target_scene：目标场景 id；空串表示「这个区域不构成边」。
            after_wait：等待秒数；None 沿用已有值或默认 1.0。
            source：来源档位；None 沿用已有值或 `manual`。

        Returns：
            写入后的 `routes.Route`；删除时为 None。

        Raises：
            SceneStoreError：写入失败。
        """
        self._ensure_routes()
        route = self.routes.set_target(scene_id, area_name, target_scene, after_wait=after_wait, source=source)
        self.save_routes()
        return route

    def remove_route(self, scene_id, area_name):
        """删除一条路由并落盘。

        Args：
            scene_id：来源场景 id。
            area_name：区域名。

        Returns：
            真的删掉了时为 True。
        """
        self._ensure_routes()
        removed = self.routes.remove(scene_id, area_name)
        if removed:
            self.save_routes()
        return removed

    def confirm_route(self, scene_id, area_name):
        """把一条探索边确认为可寻路（`confirmed`）。

        Args：
            scene_id：来源场景 id。
            area_name：区域名。

        Returns：
            改写成功时为 True。
        """
        self._ensure_routes()
        changed = self.routes.set_source(scene_id, area_name, model.SOURCE_CONFIRMED)
        if changed:
            self.save_routes()
        return changed

    def ensure_routes(self):
        """确保路由表已从磁盘读过（幂等）。

        Raises：
            SceneStoreError：`routes.json` 无法解析。

        Note：
            对外暴露版：调用方（如探索落边）需要在读写表之前确认已加载磁盘内容，
            否则会把磁盘上已有的边覆盖成「只有这一条」。
        """
        self._ensure_routes()

    def _ensure_routes(self):
        """确保路由表已从磁盘读过（幂等）。

        Raises：
            SceneStoreError：`routes.json` 无法解析。
        """
        if not self._routes_ready:
            self.load_routes()

    def migrate_legacy_edges(self):
        """把旧库挂在区域上的边搬到路由表（一次性迁移）。

        Returns：
            迁移的边条数。

        Note：
            **旧库的区域 JSON 里带着 `target_scene` / `after_wait` /
            `edge_source` / `observations`**。`Area.from_dict` 已经不认这些字段，
            直接读会静默丢掉所有手工配的边——用户看到的是「升级之后流转图空了」。

            因此这里反过来读原始 JSON，把字段搬进 `routes.json`，然后由下一次
            `save_scene` 把旧字段写掉。迁移是幂等的：路由表里已有同键条目时不覆盖
            （表是新的权威来源）。
        """
        migrated = 0
        for scene_id in self.list_scene_ids() + self.list_draft_ids():
            path = os.path.join(self.scene_dir(scene_id), SCENE_FILE)
            try:
                data = _read_json(path)
            except SceneStoreError:
                # **坏文件跳过**：`load_library` 承诺「一个坏 JSON 不该让整个库
                # 打不开」，迁移属加载的一部分，同样不能整库失败。
                continue
            if not data:
                continue
            for entry in data.get("areas") or []:
                target = entry.get("target_scene") or ""
                name = entry.get("name") or ""
                if not target or not name:
                    continue
                if self.routes.get(scene_id, name) is not None:
                    continue
                try:
                    self.routes.put(
                        routes.Route(
                            scene_id,
                            name,
                            target,
                            after_wait=entry.get("after_wait", 1.0),
                            source=entry.get("edge_source", model.SOURCE_MANUAL),
                            observations=entry.get("observations", 1),
                        )
                    )
                except routes.RoutesError:
                    # 单条坏数据跳过：一条手改错的边不该让整个库打不开。
                    continue
                migrated += 1
        if migrated:
            self.save_routes()
        return migrated

    def set_base_resolution(self, width, height):
        """记录基准分辨率并落盘。

        Args：
            width：帧宽。
            height：帧高。

        Raises：
            SceneStoreError：分辨率非法（必须为正）。
        """
        if width <= 0 or height <= 0:
            raise SceneStoreError(f"基准分辨率必须为正，收到 {width}x{height}")
        self.meta.base_width = int(width)
        self.meta.base_height = int(height)
        self.ensure_layout()
        _write_json(self.library_path, self.meta.to_dict())
        self._meta_dirty = False

    def save_meta(self):
        """把库元数据写回 `library.json`。

        Raises：
            SceneStoreError：写入失败。

        Note：
            页面上的探索参数（轮询间隔、等待上限）改完就要落盘，否则用户重启
            后又回到默认值，而界面上看不出「改了但没保存」。
        """
        self.ensure_layout()
        try:
            _write_json(self.library_path, self.meta.to_dict())
        except OSError as error:
            raise SceneStoreError(f"写入库元数据失败：{error}") from error
        self._meta_dirty = False

    def set_probe_settings(
        self,
        probe_interval=None,
        settle_timeout=None,
        search_order=None,
        auto_launch=None,
        restart_on_incomplete=None,
        max_restarts=None,
        restart_wait=None,
    ):
        """更新探索参数与目标恢复设置并落盘。

        Args：
            probe_interval：轮询间隔（秒）；None 表示不改。
            settle_timeout：等待跳转上限（秒）；None 表示不改。
            search_order：探索队列顺序（`bfs` / `dfs`）；None 表示不改。
            auto_launch：探索前自动启动目标；None 表示不改。
            restart_on_incomplete：未完成时重启目标；None 表示不改。
            max_restarts：最多重启次数；None 表示不改。
            restart_wait：重启后等待上限（秒）；None 表示不改。

        Note：
            夹取与合法性校验与 `LibraryMeta.__post_init__` 共用同一处，界面传
            越界值或非法顺序不会写进库文件。
        """
        if probe_interval is not None:
            self.meta.probe_interval = probe_interval
        if settle_timeout is not None:
            self.meta.settle_timeout = settle_timeout
        if search_order is not None:
            self.meta.search_order = search_order
        if auto_launch is not None:
            self.meta.auto_launch = auto_launch
        if restart_on_incomplete is not None:
            self.meta.restart_on_incomplete = restart_on_incomplete
        if max_restarts is not None:
            self.meta.max_restarts = max_restarts
        if restart_wait is not None:
            self.meta.restart_wait = restart_wait
        self.meta.__post_init__()
        self.save_meta()

    def set_auto_save(self, enabled):
        """记录场景编辑器的「自动保存」开关并落盘。

        Args：
            enabled：开关状态。

        Raises：
            SceneStoreError：写入失败。

        Note：
            它是**编辑器偏好**而不是库结构：存在 `library.json` 里，换机器时
            这个选择跟着库走；不写进场景文件，避免每个场景各存一份互相矛盾。
        """
        self.meta.auto_save = bool(enabled)
        self.save_meta()

    def set_name_with_scene_prefix(self, enabled):
        """记录「新建区域/特征的默认名是否带场景前缀」并落盘。

        Args：
            enabled：开关状态，默认关闭。

        Raises：
            SceneStoreError：写入失败。
        """
        self.meta.name_with_scene_prefix = bool(enabled)
        self.save_meta()

    def list_scene_ids(self):
        """列出正式区的场景 id（不读草稿区）。

        Returns：
            按 id 升序排列的字符串列表。
        """
        return self._list_ids(self.root, model.SCENE_ID_PATTERN)

    def list_draft_ids(self):
        """列出草稿区的草稿 id。

        Returns：
            按 id 升序排列的字符串列表。
        """
        return self._list_ids(self.drafts_root, DRAFT_ID_PATTERN)

    @staticmethod
    def _list_ids(root, pattern):
        """列出目录下符合 id 模式且含 `scene.json` 的子目录。

        Args：
            root：要扫描的目录。
            pattern：id 模式。

        Returns：
            排序后的 id 列表。
        """
        if not os.path.isdir(root):
            return []
        found = []
        for entry in os.listdir(root):
            if not pattern.match(entry):
                continue
            if not os.path.isfile(os.path.join(root, entry, SCENE_FILE)):
                continue
            found.append(entry)
        return sorted(found)

    def load_scene(self, scene_id):
        """读取一个场景。

        Args：
            scene_id：场景 id 或草稿 id。

        Returns：
            `Scene` 实例；目录或文件不存在时返回 None。

        Raises：
            SceneStoreError：JSON 无法解析或数据结构非法。
        """
        path = os.path.join(self.scene_dir(scene_id), SCENE_FILE)
        data = _read_json(path)
        if data is None:
            return None
        try:
            return model.Scene.from_dict(data)
        except model.SceneModelError as error:
            raise SceneStoreError(f"场景 {scene_id} 结构非法：{error}") from error

    def load_library(self):
        """读取正式区的全部场景。

        **跳过无法解析的场景而不是整体失败**：一个损坏的文件不应让整个场景库
        不可用，那会让「修好一个文件」变成「先能打开程序」。

        Returns：
            `(scenes, failures)` 二元组。`scenes` 是 `Scene` 列表（按 id 升序），
            `failures` 是 `(scene_id, 错误消息)` 列表。

        Note：
            读取时会先把旧库挂在区域上的边迁进路由表（见
            :meth:`migrate_legacy_edges`）。不迁的话，`Area.from_dict` 已经不再读
            那些字段，用户升级后会看到**所有手工配的边凭空消失**，而且没有任何
            报错。
        """
        if not self._routes_ready:
            try:
                self.load_routes()
            except SceneStoreError:
                # 路由表坏了不该让场景列表也读不出来：场景与路由是两份数据，
                # 一份坏掉时仍应能打开另一份。此时按空表继续，保存时会被覆盖。
                self.routes = routes.RouteTable()
                self._routes_ready = True
            self.migrate_legacy_edges()
        scenes = []
        failures = []
        for scene_id in self.list_scene_ids():
            try:
                scene = self.load_scene(scene_id)
            except SceneStoreError as error:
                failures.append((scene_id, str(error)))
                continue
            if scene is not None:
                scenes.append(scene)
        return scenes, failures

    def load_drafts(self):
        """读取草稿区的全部草稿。

        Returns：
            `(drafts, failures)` 二元组，语义同 :meth:`load_library`。
        """
        drafts = []
        failures = []
        for draft_id in self.list_draft_ids():
            try:
                scene = self.load_scene(draft_id)
            except SceneStoreError as error:
                failures.append((draft_id, str(error)))
                continue
            if scene is not None:
                drafts.append(scene)
        return drafts, failures

    # --- 写入 ---------------------------------------------------------------

    def next_scene_id(self):
        """分配下一个正式场景 id 并推进计数器（不落盘，需随后 :meth:`save_scene`）。

        Returns：
            形如 `scene-0001` 的字符串。
        """
        scene_id, seq = _next_id(
            self.list_scene_ids(),
            "scene-",
            model.SCENE_ID_PATTERN,
            self.meta.next_scene_seq,
        )
        self.meta.next_scene_seq = seq
        self._meta_dirty = True
        return scene_id

    def next_draft_id(self):
        """分配下一个草稿 id 并推进计数器（不落盘，需随后 :meth:`save_scene`）。

        Returns：
            形如 `draft-0001` 的字符串。
        """
        draft_id, seq = _next_id(
            self.list_draft_ids(),
            DRAFT_ID_PREFIX,
            DRAFT_ID_PATTERN,
            self.meta.next_draft_seq,
        )
        self.meta.next_draft_seq = seq
        self._meta_dirty = True
        return draft_id

    def new_scene(self, name=""):
        """分配一个新场景对象（不落盘）。

        Args：
            name：显示名；为空时按 id 序号生成（`scene-0005` → `场景 5`）。

        Returns：
            新建的 `Scene` 实例。

        Note：
            名称的序号**从 id 派生**，不另起计数器。库里有 `scene-0002` 这类用户
            自定义名的场景时，两套计数器会错位——实测 `scene-0004` 被叫成「场景 1」，
            因为名称是按「现有 场景N 的最大 N+1」从 1 数起的，而 id 已经到 4 了。
        """
        scene_id = self.next_scene_id()
        return model.Scene(scene_id=scene_id, name=name or scene_default_name(scene_id))

    def save_scene(self, scene, as_draft=False):
        """保存场景到磁盘。

        Args：
            scene：`Scene` 实例。
            as_draft：为 True 时写入草稿区；否则写入正式区。

        Raises：
            SceneStoreError：id 与目标区前缀不匹配，或写入失败。
        """
        is_draft = self.is_draft_id(scene.scene_id)
        if as_draft and not is_draft:
            raise SceneStoreError(f"草稿 id 必须以 {DRAFT_ID_PREFIX} 开头，收到 {scene.scene_id!r}")
        if not as_draft and is_draft:
            raise SceneStoreError(f"正式场景 id 不能以 {DRAFT_ID_PREFIX} 开头，收到 {scene.scene_id!r}")
        self.ensure_layout()
        folder = self.scene_dir(scene.scene_id)
        os.makedirs(os.path.join(folder, TEMPLATE_DIR), exist_ok=True)
        _write_json(os.path.join(folder, SCENE_FILE), scene.to_dict())
        self._flush_meta()

    def _flush_meta(self):
        """若计数器有推进则写回库元数据。"""
        if not self._meta_dirty:
            return
        _write_json(self.library_path, self.meta.to_dict())
        self._meta_dirty = False

    def delete_scene(self, scene_id):
        """删除一个场景（含其目录下全部资产）。

        Args：
            scene_id：场景 id 或草稿 id。

        Returns：
            实际删除了目录时为 True；目录本不存在时为 False。
        """
        folder = self.scene_dir(scene_id)
        if not os.path.isdir(folder):
            return False
        shutil.rmtree(folder)
        # 路由表里的出边与入边一起清掉：留着就是悬空边，而建图遇到悬空边会抛错，
        # 表现为「删了一个场景，流转图整块打不开」。
        self._ensure_routes()
        if self.routes.remove_scene(scene_id):
            self.save_routes()
        return True

    def save_base_image(self, scene_id, image):
        """保存场景基准图（脱敏后的整幅截图）到场景目录。

        Args：
            scene_id：场景 id 或草稿 id。
            image：BGR 或灰度 numpy 数组。

        Raises：
            SceneStoreError：图像为空或写入失败。

        Note：
            写入后顺手删掉旧的 `preview.png`：两个文件同时存在会让「到底以哪个
            为准」变成隐含约定，而读取侧的回退只在新文件缺失时才生效，旧文件
            留着就是一份永远不更新的陈旧副本。
        """
        if image is None:
            raise SceneStoreError("场景基准图为空，无法保存")
        folder = self.scene_dir(scene_id)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, BASE_FILE)
        if not _write_image(path, image):
            raise SceneStoreError(f"写入场景基准图失败：{path}")
        legacy = os.path.join(folder, LEGACY_PREVIEW_FILE)
        if os.path.isfile(legacy):
            try:
                os.remove(legacy)
            except OSError:
                # 删不掉不算失败：新文件已写好，读取优先用新名，旧文件只是冗余。
                pass

    def base_image_path(self, scene_id):
        """返回场景基准图路径（不保证存在）。

        Args：
            scene_id：场景 id 或草稿 id。

        Returns：
            绝对路径。新名文件不存在但旧名存在时返回旧路径——这样调用方不必
            关心迁移状态，直接拿去读/画即可。
        """
        folder = self.scene_dir(scene_id)
        path = os.path.join(folder, BASE_FILE)
        if not os.path.isfile(path):
            legacy = os.path.join(folder, LEGACY_PREVIEW_FILE)
            if os.path.isfile(legacy):
                return legacy
        return path

    def load_base_image(self, scene_id):
        """读取场景基准图。

        Args：
            scene_id：场景 id 或草稿 id。

        Returns：
            BGR numpy 数组；文件缺失或无法解码时返回 None（界面据此显示占位）。
        """
        return _read_image(self.base_image_path(scene_id))

    def save_template(self, scene_id, feature_name, image):
        """保存一个特征模板图。

        Args：
            scene_id：场景 id 或草稿 id。
            feature_name：特征名，作为文件名主干。
            image：BGR 或灰度 numpy 数组。

        Raises：
            SceneStoreError：图像为空、特征名非法或写入失败。
        """
        if image is None:
            raise SceneStoreError(f"特征 {feature_name!r} 的模板图为空")
        path = self.template_path(scene_id, feature_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not _write_image(path, image):
            raise SceneStoreError(f"写入模板图失败：{path}")

    def template_path(self, scene_id, feature_name):
        """返回模板图路径。

        Args：
            scene_id：场景 id 或草稿 id。
            feature_name：特征名。

        Returns：
            绝对路径。
        """
        return os.path.join(self.scene_dir(scene_id), TEMPLATE_DIR, f"{feature_name}.png")

    def load_template(self, scene_id, feature_name):
        """读取模板图。

        Args：
            scene_id：场景 id 或草稿 id。
            feature_name：特征名。

        Returns：
            BGR numpy 数组；文件缺失或无法解码时返回 None（识别时该特征按未命中处理）。
        """
        return _read_image(self.template_path(scene_id, feature_name))

    def save_mask(self, scene_id, feature_name, image):
        """保存一个特征掩码。

        Args：
            scene_id：场景 id 或草稿 id。
            feature_name：特征名。
            image：单通道 uint8 数组，255 参与匹配、0 排除。

        Raises：
            SceneStoreError：图像为空或写入失败。
        """
        if image is None:
            raise SceneStoreError(f"特征 {feature_name!r} 的掩码为空")
        path = self.mask_path(scene_id, feature_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not _write_image(path, image):
            raise SceneStoreError(f"写入掩码失败：{path}")

    def remove_mask(self, scene_id, feature_name):
        """删除一个特征的掩码文件（存在时）。

        Args：
            scene_id：场景 id 或草稿 id。
            feature_name：特征名。

        Returns:
            True 表示删除成功或文件本来就不存在；False 表示删除失败。

        Note：
            「掩码不存在」是**合法状态**：它表示该特征不做任何像素级筛选
            （既没有掩码形状也没有背景色剔除），识别时全部像素参与匹配。
            这与写一张全 255 的掩码图语义相同，但文件不存在更容易识别出
            「这个特征本来就没配筛选」，也不会在改配置后留下过期文件。
        """
        path = self.mask_path(scene_id, feature_name)
        if not os.path.isfile(path):
            return True
        try:
            os.remove(path)
        except OSError:
            return False
        return True

    def mask_path(self, scene_id, feature_name):
        """返回掩码路径。

        Args：
            scene_id：场景 id 或草稿 id。
            feature_name：特征名。

        Returns：
            绝对路径。
        """
        return os.path.join(self.scene_dir(scene_id), TEMPLATE_DIR, f"{feature_name}_mask.png")

    def load_mask(self, scene_id, feature_name):
        """读取掩码。

        Args：
            scene_id：场景 id 或草稿 id。
            feature_name：特征名。

        Returns：
            单通道 uint8 数组；文件缺失时返回 None（识别时退化为不带掩码）。
        """
        return _read_image(self.mask_path(scene_id, feature_name), grayscale=True)

    def promote_draft(self, draft_id, name=None):
        """把草稿转为正式场景。

        分配一个新的正式 id、搬移目录、删掉旧草稿目录。搬移而非复制：草稿一旦
        转正就不应再存在于草稿区，否则同一内容会有两份可编辑副本。

        **路由表必须一起迁移**：草稿 id 在转正后不复存在，所有指向它的边若不改写
        就成了悬空边——建图会直接抛「存在指向不存在场景的边」，而界面上会提示
        「请检查场景库是否被手工改动」，其实是我们自己造出来的。

        迁移分三步：

        1. 改写指向旧 id 的入边与出边的场景 id；
        2. 草稿自己的出边升为 `confirmed`（人工确认整条草稿 = 确认它的出边）；
        3. **指向其它草稿的边被清空**——草稿之间可能互指（先发现 A 再从 A 发现
           B），而路由只能指向正式场景。留着草稿 id 会再次形成悬空引用，而寻路
           时该边被静默忽略，用户看不到「这里本该有条边」。

        Args：
            draft_id：草稿 id。
            name：转正后的显示名；为空时沿用草稿名。

        Returns：
            `(scene, dropped_edges)` 二元组。`scene` 是转正后的 `Scene`（id 已更新），
            `dropped_edges` 是被清空的草稿间边数量。

        Raises：
            SceneStoreError：草稿不存在或搬移失败。
        """
        draft = self.load_scene(draft_id)
        if draft is None:
            raise SceneStoreError(f"草稿 {draft_id} 不存在")
        new_id = self.next_scene_id()
        source = self.scene_dir(draft_id)
        target = self.scene_dir(new_id)
        try:
            shutil.move(source, target)
        except OSError as error:
            raise SceneStoreError(f"草稿转正失败（搬移目录）：{error}") from error

        # 先把 id 迁移好：路由表里凡是引用旧 id 的（出边与入边）都要改写，
        # 否则会留下悬空边让建图直接报错。
        self._ensure_routes()
        self.routes.migrate_scene(draft_id, new_id)
        # 草稿自己的出边与指向它的入边一起升档：确认「这个画面是对的」，
        # 等于确认「点父场景那个按钮能到这里」——两者是同一件事。
        self.routes.confirm_scene_edges(new_id, include_incoming=True)
        # 指向其它草稿的边清空——路由只能指向正式场景。
        dropped_edges = 0
        for route in self.routes.outgoing(new_id):
            if self.is_draft_id(route.target_scene):
                self.routes.remove(route.from_scene, route.area_name)
                dropped_edges += 1
        self.save_routes()

        draft.scene_id = new_id
        draft.name = name or draft.name or new_id
        self.save_scene(draft)
        return draft, dropped_edges

    # --- 分辨率一致性 -------------------------------------------------------

    def base_image_size(self, scene_id):
        """读取某个场景基准图的像素尺寸。

        只读文件头，不解码整幅图——`base.png` 在 4K 下有几 MB，遍历几十个场景
        时整体解码会明显拖慢检查。

        Args：
            scene_id：场景 id 或草稿 id。

        Returns：
            `(width, height)` 元组；文件缺失或无法解析时返回 None。
        """
        return image_size(self.base_image_path(scene_id))

    def resolution_problems(self):
        """检查库里每个场景的基准图是否与基准分辨率一致。

        **这是「保存前全面检查」的依据**。基准分辨率是识别链路的唯一口径：所有
        相对坐标都按它换算成像素，帧也按它缩放。某个场景的基准图尺寸与之不同，
        说明它是在另一套画面构成下采集的，它的特征框到运行期一定错位——而错位的
        表现是「这个场景从来识别不到」或「点到了别处」，不报错。

        宽高比用 `normalize.ratio_differs` 的同一套容差判定，不另立标准：两处
        口径不同会出现「保存说不行、识别说可以」。

        Returns：
            `(width, height, problems)` 三元组。`(width, height)` 是基准分辨率
            （未记录时为 `(0, 0)`）；`problems` 是
            `[(scene_id, 场景名, (实际宽, 实际高), 原因)]` 列表，全部一致时为空。
        """
        from extensions.capabilities.scene_manager import normalize

        width, height = self.meta.base_width, self.meta.base_height
        problems = []
        if width <= 0 or height <= 0:
            return 0, 0, problems
        for scene in self.load_library()[0]:
            size = self.base_image_size(scene.scene_id)
            if size is None:
                problems.append((scene.scene_id, scene.name, None, "场景基准图缺失或无法读取"))
                continue
            actual_width, actual_height = size
            if (actual_width, actual_height) == (width, height):
                continue
            try:
                differs = normalize.ratio_differs(actual_width, actual_height, width, height)
            except normalize.NormalizeError:
                differs = True
            if differs:
                reason = "宽高比与库基准不同"
            else:
                # 比例相同但尺寸不同是可以接受的：识别时帧会被等比缩放到基准，
                # 基准图本身只是展示与裁模板用。这里仍要提示，因为它说明采集
                # 分辨率变了。
                reason = "尺寸与库基准不同（比例一致，可正常识别）"
            problems.append((scene.scene_id, scene.name, (actual_width, actual_height), reason))
        return width, height, problems

    # --- 导出到产物区 -------------------------------------------------------

    def export_to(self, target_root, scene_ids=None):
        """把编辑区内容导出到产物区。

        导出的内容：`library.json`、每个场景的 `scene.json`、`base.png` 与
        `templates/`（含掩码）。**不导出原始帧**——未遮罩的整帧截图含游戏 UID，
        打包带出去等于把它交给收包方。

        采用「先清空目标场景目录再整体复制」而不是逐文件覆盖：编辑时删掉的特征、
        改名的模板都会在旧产物里留下残留文件，而残留的模板不会被任何特征引用，
        却会随包分发出去。整体替换保证「产物区 = 编辑区快照」这一条恒成立。

        Args：
            target_root：产物区根目录。
            scene_ids：要导出的场景 id 列表；为 None 时导出正式区全部场景。
                草稿不进产物区——未经人工确认的探索产物不应参与运行期识别。

        Returns：
            `(exported, skipped)` 二元组。`exported` 是实际导出的场景 id 列表，
            `skipped` 是缺少必需资产的 `(scene_id, 原因)` 列表。

        Raises：
            SceneStoreError：目标目录无法创建或写入失败。
        """
        ids = self.list_scene_ids() if scene_ids is None else [i for i in scene_ids if not self.is_draft_id(i)]
        target_root = os.path.abspath(target_root)
        try:
            os.makedirs(os.path.join(target_root, DRAFT_DIR), exist_ok=True)
        except OSError as error:
            raise SceneStoreError(f"创建产物区目录失败：{error}") from error

        exported = []
        skipped = []
        for scene_id in ids:
            source = self.scene_dir(scene_id)
            if not os.path.isfile(os.path.join(source, SCENE_FILE)):
                skipped.append((scene_id, "缺少 scene.json"))
                continue
            scene = self.load_scene(scene_id)
            if scene is None:
                skipped.append((scene_id, "scene.json 无法解析"))
                continue
            missing = self._missing_assets(scene)
            if missing:
                skipped.append((scene_id, missing))
                continue

            target = os.path.join(target_root, scene_id)
            try:
                if os.path.isdir(target):
                    shutil.rmtree(target)
                os.makedirs(target, exist_ok=True)
                shutil.copyfile(os.path.join(source, SCENE_FILE), os.path.join(target, SCENE_FILE))
                # 基准图按**新名**写入产物区：产物区随包分发，不应带旧命名的历史包袱。
                # 源侧可能是旧名（改名前的库），用 `base_image_path` 取实际路径。
                base_source = self.base_image_path(scene_id)
                if os.path.isfile(base_source):
                    shutil.copyfile(base_source, os.path.join(target, BASE_FILE))
                template_target = os.path.join(target, TEMPLATE_DIR)
                os.makedirs(template_target, exist_ok=True)
                for feature in scene.features:
                    if feature.kind != model.KIND_TEMPLATE:
                        continue
                    for relative in (feature.template_file, feature.mask_file):
                        if not relative:
                            continue
                        shutil.copyfile(
                            os.path.join(source, relative),
                            os.path.join(target, relative),
                        )
            except OSError as error:
                raise SceneStoreError(f"导出场景 {scene_id} 失败：{error}") from error
            exported.append(scene_id)

        _write_json(
            os.path.join(target_root, LIBRARY_FILE),
            self.meta.to_dict(),
        )
        # 路由表随库一起导出：运行期导航要靠它才知道点哪能到哪。**只导出指向本次
        # 导出的场景的路由**——指向未导出场景的边在产物区就是悬空边，会让运行期
        # 建图直接报错。
        self._ensure_routes()
        kept = routes.RouteTable(
            route for route in self.routes if route.from_scene in exported and route.target_scene in exported
        )
        try:
            kept.save(os.path.join(target_root, routes.ROUTES_FILE))
        except routes.RoutesError as error:
            raise SceneStoreError(f"导出路由表失败：{error}") from error
        return exported, skipped

    def _missing_assets(self, scene):
        """检查场景的必需资产是否齐全，缺哪个返回可读原因。

        Args：
            scene：`model.Scene` 实例。

        Returns：
            缺资产时返回原因字符串；齐全时返回空串。
        """
        source = self.scene_dir(scene.scene_id)
        if not os.path.isfile(self.base_image_path(scene.scene_id)):
            return f"缺少 {BASE_FILE}"
        for feature in scene.features:
            if feature.kind != model.KIND_TEMPLATE:
                continue
            if not os.path.isfile(os.path.join(source, feature.template_file)):
                return f"特征 {feature.name!r} 缺少模板图（在场景编辑里重新保存该场景即可生成）"
            if not os.path.isfile(os.path.join(source, feature.mask_file)):
                return f"特征 {feature.name!r} 缺少掩码（在场景编辑里重新保存该场景即可生成）"
        return ""

    # --- 导入导出 -----------------------------------------------------------

    def export_library(self, zip_path, include_drafts=False):
        """把整个场景库打包成 zip。

        Args：
            zip_path：目标 zip 路径。
            include_drafts：是否包含草稿区。

        Returns：
            写入的条目数。

        Raises：
            SceneStoreError：目录不存在或写入失败。
        """
        if not os.path.isdir(self.root):
            raise SceneStoreError(f"场景库目录不存在：{self.root}")
        entries = 0
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for folder, _dirs, files in os.walk(self.root):
                    relative_folder = os.path.relpath(folder, self.root)
                    if relative_folder == ".":
                        relative_folder = ""
                    if not include_drafts and relative_folder.split(os.sep)[0] == DRAFT_DIR:
                        continue
                    for name in files:
                        if name.endswith(".tmp"):
                            continue
                        absolute = os.path.join(folder, name)
                        arcname = os.path.join(relative_folder, name)
                        archive.write(absolute, arcname.replace(os.sep, "/"))
                        entries += 1
                manifest = {
                    "schema_version": model.SCHEMA_VERSION,
                    "base_width": self.meta.base_width,
                    "base_height": self.meta.base_height,
                    "scene_count": len(self.list_scene_ids()),
                    "include_drafts": include_drafts,
                }
                archive.writestr(
                    EXPORT_MANIFEST,
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                entries += 1
        except (OSError, zipfile.BadZipFile) as error:
            raise SceneStoreError(f"导出场景库失败：{error}") from error
        return entries

    def import_library(self, zip_path, replace=False):
        """从 zip 导入场景库。

        Args：
            zip_path：源 zip 路径。
            replace：为 True 时先清空目标库（保留库目录本身）。

        Returns：
            导入的场景数。

        Raises：
            SceneStoreError：包损坏、缺少清单，或写入失败。
        """
        if not os.path.isfile(zip_path):
            raise SceneStoreError(f"导入包不存在：{zip_path}")
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                names = archive.namelist()
                if EXPORT_MANIFEST not in names:
                    raise SceneStoreError(f"导入包缺少清单 {EXPORT_MANIFEST}，不是有效的场景库导出包")
                if replace and os.path.isdir(self.root):
                    for entry in os.listdir(self.root):
                        target = os.path.join(self.root, entry)
                        if os.path.isdir(target):
                            shutil.rmtree(target)
                        else:
                            os.remove(target)
                for name in names:
                    if name == EXPORT_MANIFEST or name.endswith("/"):
                        continue
                    archive.extract(name, self.root)
        except zipfile.BadZipFile as error:
            raise SceneStoreError(f"导入包损坏：{error}") from error
        except OSError as error:
            raise SceneStoreError(f"导入场景库失败：{error}") from error

        self.load_meta()
        return len(self.list_scene_ids())


_README_TEXT = """# 场景库

本目录由「场景管理」能力维护，请勿手工改目录名——目录名就是场景的稳定 id，
所有跳转边都引用它，改名会让边断开。

## 两区结构

```text
ok_scenes/                    # 编辑区（不入库，只在本机）
└── scene-0001/
    ├── scene.json            # 场景定义
    ├── base.png             # 场景基准图（整幅截图，已脱敏）
    └── templates/            # 编辑态模板与掩码

assets/scenes/                # 产物区（入库，运行期只读）
├── library.json            # 基准分辨率
├── routes.json             # 流转路由：哪个场景的哪个区域通向哪个场景
├── scene-0001/             # 目录名 = 场景 id
│   ├── scene.json          # 场景定义：识别特征 + 交互区域（**不含跳转信息**）
│   ├── base.png           # 场景基准图（已脱敏，展示、缩略图与裁模板的来源）
│   └── templates/          # 识别用的模板与掩码
│       ├── btn.png         # 外接矩形内的真实像素
│       └── btn_mask.png    # 同尺寸掩码
└── _drafts/                # 自动探索产生的草稿，人工确认后才进正式区
```

编辑区与产物区之间没有自动同步：在弹窗里改完点「保存」只写编辑区，再点配置页的
「保存到 assets」才导出到产物区。

## 字段约定

- **坐标一律是相对比例**（0.0–1.0），基准是 `library.json` 记录的分辨率。
  换分辨率时把帧缩放到该尺寸即可，坐标不需要改。
- **特征 = 一个形状 + 一张模板 + 一张掩码**。形状取 `rect` / `circle` /
  `sector` / `polygon` 之一；掩码把形状之外的像素排除在匹配之外。
- **圆的半径按帧宽折算**，不是两轴各自折算——否则非 1:1 的画面里圆会变成椭圆，
  掩码把圆弧上下切掉。
- **没有独立的 `region` 字段**：搜索区域由形状派生。另存一个 region 会与形状
  分叉，改了形状忘了改 region，模板就与搜索区对不上，表现是「这个特征从来匹配
  不到」，不报错。
- **交互区域不绑定业务动作类型**：区域只记录矩形，单击/双击/长按/拖拽/
  滚动由使用方自己实现。**探索是例外**：它必须自己触发区域，因此用
  `explore_probe` 开关「允许被探测」、用 `explore_action` 选动作
  （单击/双击/长按/滑动/右键），`explore_params` 存该动作的参数。这三个字段都带
  `explore_` 前缀，不参与识别与寻路。
- **场景不描述跳转**。`scene.json` 只回答「这个画面长什么样」；「点哪个区域会到哪」
  在 `routes.json` 里，键为 `(场景 id, 区域名)`。原先这些字段挂在区域上，同一份事实
  有两处落点，改一处漏一处就分叉——表现为「流转图上有边，点下去到不了」，不报错。
- **路由的 `source`** 取 `manual` / `explored` / `confirmed`，寻路只走 `manual`
  与 `confirmed`——探索产物未经人工确认不得参与寻路。流转图页会把 `explored` 的边
  画出来（虚线区分），但那只是让用户看到「探到了什么」，不参与寻路。
- **区域的键是路由的唯一纽带**：区域改名、删除、场景删除、草稿转正这四类操作必须
  同步路由表，漏一处的故障都是静默的（见 `routes` 模块与设计文档）。

## 隐私

**遮罩是输出脱敏**：只在落盘与展示时施加，运行期识别跑在原始帧上。这与框架的
设计一致——`screenshot_processor` 只在把图变成文件时生效
（`ok/core/screenshot.py` 的 `to_pil_image`），`get_frame` 返回原始像素。

因此：

- `base.png` 是遮罩后的图，不含游戏 UID；模板与掩码只覆盖特征所在的小块。
- **原始帧一律不落盘**：它只在弹窗内存里用于当次裁模板，不写进任何目录。
- 若某个特征与遮罩矩形有交集，配置页会提示——交集部分在落盘时被涂成纯色，而模板
  取自原始帧，因此该特征仍可能匹配成功，但基准图上会看不到那块内容。

若将来要求「运行期也拿不到 UID」，只能在框架的 `next_frame` 处统一施加遮罩，
那属改上游文件，需登记 `divergence.yaml`。本能力不自行引入该分歧。
"""


def _write_image(path, image):
    """把 numpy 数组写成 PNG。

    延迟导入 cv2：本模块的纯数据部分（JSON 读写）不需要图像库，装了 cv2 与否
    只影响图片操作。用 cv2 而非 PIL 是因为框架已依赖 cv2，不额外引入。

    Args：
        path：目标路径。
        image：BGR 或灰度数组。

    Returns：
        写入成功时为 True。

    Note：
        **不能用 `cv2.imwrite(path, image)` 直接写**。OpenCV 在 Windows 上按
        ANSI 代码页解释路径，中文文件名会写到一个「乱码名」的新文件上，而目标
        路径本身不存在——更糟的是它**返回 True**，调用方看不出任何异常。

        实测：`cv2.imwrite('/tmp/靶场入口测试.png', img)` 返回 True，但该路径
        不存在，目录里多出一个乱码名的文件。而本模块的模板文件名正是特征名
        （中文），于是保存后 `templates/` 里是乱码文件、`scene.json` 里记的是
        正确名字，二者对不上——表现就是「保存了但没有模板图」，导出时被判定
        「特征缺少模板图」而跳过整个场景。

        修法是用 `imencode` 编码到内存，再由 Python 按 UTF-8 路径写字节。
    """
    try:
        import cv2
    except ModuleNotFoundError:  # 极端情况下（仅跑 JSON 测试）允许缺失
        return False
    # 扩展名决定编码格式；非 PNG 也按原扩展名问 cv2 要对应编码器。
    suffix = os.path.splitext(path)[1] or ".png"
    try:
        ok, buffer = cv2.imencode(suffix, image)
    except cv2.error:
        return False
    if not ok:
        return False
    try:
        with open(path, "wb") as handle:
            handle.write(buffer.tobytes())
    except OSError:
        return False
    return True


def image_size(path):
    """读取 PNG 的像素尺寸，不解码整幅图。

    只读文件头（PNG 的 IHDR），因此几十个场景逐个检查也很快；对非 PNG 或损坏
    文件返回 None 而不是抛异常——调用方要的是「能不能确认尺寸」，读不出来就该
    走「无法核对」那条分支。

    Args：
        path：图片路径。

    Returns：
        `(width, height)` 元组；文件不存在、不是 PNG 或头部损坏时返回 None。
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    # PNG 签名 8 字节 + 长度 4 + 类型 4 + 宽 4 + 高 4。要求「IHDR」在场，
    # 否则可能是别的格式改了扩展名，按 PNG 布局读出来的宽高是垃圾值。
    if len(header) < 24 or not header.startswith(b"\x89PNG\r\n\x1a\n") or header[12:16] != b"IHDR":
        return None
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def _read_image(path, grayscale=False):
    """从 PNG 读取 numpy 数组。

    Args：
        path：文件路径。
        grayscale：为 True 时按单通道读入（掩码用）。掩码必须是单通道——三通道
            掩码传给 `cv2.matchTemplate` 会直接被拒。

    Returns：
        BGR（或灰度）数组；文件不存在、无法解码或缺少 cv2 时返回 None。

    Note：
        与 `_write_image` 同理，**不能用 `cv2.imread(path)` 直接读**：中文路径
        在 Windows 上会读不到（返回 None），表现为「模板明明存在却识别不到」。
        改为 Python 读字节后交给 `cv2.imdecode`。
    """
    if not os.path.isfile(path):
        return None
    try:
        import cv2
    except ModuleNotFoundError:
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if not data:
        return None
    import numpy

    buffer = numpy.frombuffer(data, dtype=numpy.uint8)
    flag = cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR
    try:
        return cv2.imdecode(buffer, flag)
    except cv2.error:
        return None
