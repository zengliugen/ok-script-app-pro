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
# extensions/capabilities/my_task/task.py
from src.tasks.MyBaseTask import MyBaseTask

class MyNewTask(MyBaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "我的新任务"
        self.description = "做什么"
        self.icon = "icons/icon.png"
        self.default_config = {"参数": 1}

# extensions/capabilities/my_task/__init__.py —— 能力包契约
from extensions.capabilities.my_task import task

def register(config):
    config.setdefault("onetime_tasks", []).append(
        ["extensions.capabilities.my_task.task", "MyNewTask"]
    )
```

新增能力包后**在 `extensions/capabilities/registry.py` 的 `CAPABILITIES` 里追加一行**，
它才会被装载。`bootstrap.py` 与 `app_globals.py` 不需要改。

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
# extensions/capabilities/my_ok/my_ok.py
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
extensions/                          # 只放运行时代码
├── __init__.py
├── bootstrap.py                     # 装配基础设施：只串顺序
├── overrides.py                     # 装配基础设施：config 值覆写（带键存在性校验）
├── app_globals.py                   # 装配基础设施：全局单例 + 生命周期派发
├── capabilities/                    # 一个能力一个包（唯一的能力代码区）
│   ├── registry.py                  #   能力清单 CAPABILITIES（唯一登记点）
│   ├── log_noise_filter/            #   能力 log-noise-filter
│   │   ├── __init__.py              #     契约：register(config)
│   │   └── noise_filter.py          #     实现
│   └── update_check_guard/          #   能力 update-check-guard
│       ├── __init__.py              #     契约：register(config) + on_show_main_window
│       └── guard.py                 #     实现
└── patches/                         # 侵入式 monkey patch，一个目标一个文件

extensions_tests/                    # 只放测试（与 extensions/ 分目录）
├── __init__.py
└── Test<能力名>.py
```

**按能力分包，不按技术层次分包。** 目录名就是能力名（snake_case），包内自包含该能力的
实现、任务类、页面类与私用常量。曾经按 `core/` `tasks/` `ui/` 分类，问题是那两个基本问题
都答不上来：这个文件属于哪个能力？删掉某个能力要动哪些文件？

**能力名三处一致**，由体检脚本机械核对：

| 位置 | 形态 | 示例 |
|---|---|---|
| 能力包目录 | `extensions/capabilities/<snake_case>/` | `log_noise_filter/` |
| `registry.py` 的 `CAPABILITIES` | 完整模块路径 | `extensions.capabilities.log_noise_filter` |
| `extensions_docs/README.md` 总览表 | kebab-case 能力名 | `log-noise-filter` |

新增能力的完整步骤：

1. 建 `extensions/capabilities/<能力名 snake_case>/` 目录与 `__init__.py`；
2. 在 `__init__.py` 里实现 `register(config)`（无事可做写空实现并说明理由）；
3. 在 `registry.py` 的 `CAPABILITIES` 里追加模块路径；
4. 按文档流程登记到 `extensions_docs/README.md` 总览表；
5. 补 `extensions_tests/Test<能力名>.py`；
6. 跑体检：`audit` 会核对目录、清单、`register` 契约与总览表是否一致。

`bootstrap.py` 与 `app_globals.py` **不需要改**——它们只经 `registry` 访问能力。

**能力之间不得互相 import**。需要共享的代码上升为装配基础设施或独立工具模块，
否则被依赖的能力就无法单独删除或迁移。

`src/config.py` 相对上游**只允许一行差异**——末尾的接线：

```python
from extensions import bootstrap; bootstrap.apply(config)
```

其余任何 config 改动都应写进 `overrides.py`。

## 差异登记

改动上游文件必须登记到 `.codebuddy.project/configs/upstream/divergence.yaml`，
与改动放在同一次提交。校验：

```bash
python .codebuddy.project/skills/project/project-upstream-compat-guard/scripts/upstream_compat_guard.py
```

## 常见判断

| 需求 | 走哪条路 | 代码落点 |
|---|---|---|
| 加一个自动化任务 | 路径 1（`onetime_tasks`） | 该能力包内的任务类 + `register()` |
| 加载模型给多个任务用 | 路径 1（`my_app`） | 能力包 + `app_globals.py` 派发 |
| 截图后做处理 | 路径 1（`screenshot_processor`） | 该能力包的 `register()` |
| 自定义输入方式 | 路径 1（`windows.interaction` 放类对象） | 该能力包的 `register()` |
| 换任务管理器 | 路径 2（覆写 `do_init`） | 能力包内的 `MyOK` 子类 |
| 主窗口显示后做事 | 路径 3（`on_show_main_window`） | 能力包的 `on_show_main_window` 钩子 |
| 换配置存储后端 | 路径 4（patch，需登记） | `extensions/patches/<目标名>.py` |

## 相关文档

- 规则正文：`.codebuddy.project/rules/project/project-extension-capability/`
- 上游差异登记规则：`.codebuddy.project/rules/project/project-upstream-compat/`
- 差异清单：`.codebuddy.project/configs/upstream/divergence.yaml`
- 能力清单：`extensions_docs/README.md` 的能力总览表
