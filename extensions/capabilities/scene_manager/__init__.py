# -*- coding: utf-8 -*-
"""场景管理能力。

能力名：`scene-manager`（总览表与测试文件名的命名来源）。

一套与业务解耦的场景库：场景 = 识别特征 + 交互区域（区域的目标场景同时表达
跳转边）。由它派生出三项能力：识别当前处于哪个场景、沿最短路跳到指定场景、
自动探索未配置的画面并产出草稿边。

**「交互区域」不等于「点击区域」**：区域只是一块画面矩形，可以被用来单击、双击、
长按、拖拽、滚轮滚动，具体做什么由使用方决定，场景管理不记录业务动作类型。只有探索
这一个使用方需要自己动手，因此它用 `Area.explore_probe` 单独开关、用
`Area.explore_action` 选择动作（单击/双击/长按/滑动/右键）——这两个字段都带
`explore_` 前缀，明确不参与识别与寻路。

模块划分与取舍见 `extensions_docs/scene-manager/design.md`：

| 模块 | 职责 | 是否依赖 ok |
|---|---|---|
| `model` | 数据类与 JSON 序列化 | 否 |
| `store` | 场景库读写、草稿区、导入导出 | 否（图片操作延迟导入 cv2） |
| `normalize` | 分辨率归一化、坐标换算、帧稳定判定 | 否（延迟导入 numpy/cv2） |
| `recognize` | 必需全中 + 可选打分、遮罩相交检测 | 否（延迟导入 cv2） |
| `graph` | networkx 建图、寻路、布局 | 否（仅依赖 networkx） |
| `navigate` | 导航器（纯逻辑，依赖注入） | 否 |
| `explore` | BFS 探索器（纯逻辑，依赖注入） | 否 |
| `runtime` | 把框架能力接到纯逻辑层 | 是 |
| `tasks` | 两个薄壳任务 | 是 |
| `tab` | 主页签与四个子页签 | 是 |

前七个模块不导入 `ok`：它们承载全部判定逻辑，必须能在无 GUI、无设备的环境完整
验证。混入运行依赖会让最需要精确验证的部分反而必须装齐依赖才能测。

装配期做两件事：登记页签、登记两个任务。二者都必须在 `OK(config)` 之前完成。
"""

TAB_MODULE = "extensions.capabilities.scene_manager.tab"
TAB_CLASS = "SceneManagerTab"

# 装配必需的 config 键。缺失说明上游结构变了。
REQUIRED_KEYS = ("onetime_tasks",)

# 要注册的任务。清单在此静态声明而非从 tasks 模块读取：装配期导入 tasks 会连带
# 导入 `ok.BaseTask`，而此刻 `ok.OK` 尚未构造。两条记录必须与 tasks.py 的类名一致。
TASK_SPECS = (
    ("extensions.capabilities.scene_manager.tasks", "SceneGotoTask"),
    ("extensions.capabilities.scene_manager.tasks", "SceneExploreTask"),
)


def register(config):
    """向框架装载本能力。

    Args：
        config：应用配置字典，由 `src/config.py` 构造并在 `OK(config)` 之前传入。

    Raises：
        RuntimeError：上游缺少 `onetime_tasks`，说明框架装配结构已变。必须崩溃并
            指名：静默不生效的表现是「任务列表里没有这两个任务」，用户只会以为
            功能没做，不会想到是装配失败。
    """
    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise RuntimeError(
            "场景管理能力装载失败：上游 config 中不存在 " + "、".join(missing) + "，请检查 ok-script 升级后更新 "
            "extensions/capabilities/scene_manager/__init__.py"
        )

    for spec in TASK_SPECS:
        entry = [spec[0], spec[1]]
        if entry not in config["onetime_tasks"]:
            config["onetime_tasks"].append(entry)

    tabs = config.setdefault("custom_tabs", [])
    entry = [TAB_MODULE, TAB_CLASS]
    if entry not in tabs:
        tabs.append(entry)
