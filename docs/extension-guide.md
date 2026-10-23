# 扩展开发指南

本项目 fork 自 [ok-script-app](https://github.com/ok-oldking/ok-script-app)，通过 `git merge upstream/master`
跟进上游更新。新增能力时若直接修改上游文件，每次同步都会产生冲突；改动越深，冲突越难解，
最终会失去跟进上游的能力。

本页说明本项目约定的扩展方式：**优先扩展与注入，不修改上游代码**。

## 四条路径，按顺序尝试

| 顺序 | 路径 | 用什么 | 典型场景 |
|---|---|---|---|
| 1 | 继承 + config 注册 | 继承框架类，在 `config` 里注册自己的类路径 | 新任务、新页面、全局单例 |
| 2 | 子类化 `OK` / `App` | 覆写 `do_init` 等方法，入口换成 `MyOK(config).start()` | 替换任务管理器、设备管理器 |
| 3 | 回调与事件订阅 | `screenshot_processor`、`communicate.*.connect` | 截图后处理、界面刷新联动 |
| 4 | monkey patch | 运行时替换框架内部实现 | 换配置存储、改 Web 路由 |

路径 1 到 3 都是零改动，**优先用它们**。路径 4 是最后手段，需满足准入条件并单独登记。

## 路径 1：继承 + config 注册

框架从 `config` 读取类路径并实例化，语法是二元组 `[模块路径, 类名]`：

```python
# extensions/tasks/my_task.py
from src.tasks.MyBaseTask import MyBaseTask

class MyNewTask(MyBaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "我的新任务"
        self.description = "做什么"
        self.icon = "icons/icon.png"
        self.default_config = {"参数": 1}

# extensions/bootstrap.py 中注册
config.setdefault("onetime_tasks", []).append(
    ["extensions.tasks.my_task", "MyNewTask"]
)
```

可注册的 config 键：

| 键 | 注册内容 | 生效范围 |
|---|---|---|
| `onetime_tasks` | 一次性任务 | Qt + Web |
| `trigger_tasks` | 后台触发任务 | Qt + Web |
| `custom_tabs` | 侧边栏页面 | 仅 Qt |
| `web_tabs` | Web 页面 | 仅 Web |
| `my_app` | 全局单例（加载模型等） | Qt + Web |

另有三个回调型扩展点：`screenshot_processor`（截图后处理）、
`template_matching.feature_processor`（匹配结果处理）、
`windows.interaction` 列表里直接放自定义交互类对象。

## 路径 2：子类化 OK

框架把装配逻辑拆成了独立方法，可以覆写：

```python
# extensions/core/my_ok.py
from ok import OK

class MyOK(OK):
    def init_device_manager(self, *args, **kwargs):
        # 自定义设备管理器
        return super().init_device_manager(*args, **kwargs)
```

入口改为 `MyOK(config).start()`。注意：**四个入口脚本各改一行，均需登记**。

## 路径 3：回调与事件

```python
from ok import og
from ok.core.events import communicate

# 任务列表变化时刷新
communicate.task_list_updated.connect(lambda: print("列表变了"))

# 主窗口显示后做事（在 Globals 中实现）
def on_show_main_window(self):
    ...
```

事件总线能让你**观察**框架行为，但**不能**用它新增任务或页面——那些只能在 `OK` 构造期从
`config` 一次性建立。

## 目录结构

```
extensions/                 # 只放运行时代码
├── __init__.py
├── bootstrap.py            # 唯一入口，串起下面几层
├── overrides.py            # config 值覆写（带键存在性校验）
├── core/                   # 核心能力扩展（patch、hook、框架行为调整）
├── tasks/                  # 任务与触发器
└── patches/                # monkey patch，一个目标一个文件

extensions_tests/           # 只放测试（与 extensions/ 分目录）
├── __init__.py
└── Test<能力名>.py
```

扩展**不止任务**：凡是上游没有、本项目要加的运行时能力都放 `extensions/`，
按性质分 `core/`（框架级）、`tasks/`（任务）、`patches/`（侵入式 patch）。
新增一类能力时新建子目录，不要往 `bootstrap.py` 里堆逻辑。

`src/config.py` 相对上游**只允许一行差异**——末尾的接线：

```python
from extensions import bootstrap; bootstrap.apply(config)
```

其余任何 config 改动都应写进 `overrides.py`。

## 差异登记

改动上游文件必须登记到 `.codebuddy.project/configs/upstream/divergence.yaml`，
与改动放在同一次提交。校验：

```bash
python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py
```

## 常见判断

| 需求 | 走哪条路 |
|---|---|
| 加一个自动化任务 | 路径 1（`onetime_tasks`） |
| 加载模型给多个任务用 | 路径 1（`my_app`） |
| 截图后做处理 | 路径 1（`screenshot_processor`） |
| 自定义输入方式 | 路径 1（`windows.interaction` 放类对象） |
| 换任务管理器 | 路径 2（覆写 `do_init`） |
| 主窗口显示后做事 | 路径 3（`on_show_main_window`） |
| 换配置存储后端 | 路径 4（patch，需登记） |

## 相关文档

- 规则正文：`.codebuddy.project/rules/project/upstream-compat/`
- 差异清单：`.codebuddy.project/configs/upstream/divergence.yaml`
- 能力清单：`.codebuddy.project/configs/upstream/capabilities.yaml`
