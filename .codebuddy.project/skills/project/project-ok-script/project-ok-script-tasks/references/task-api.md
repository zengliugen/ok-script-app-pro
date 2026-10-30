# ok-script 任务 API 参考

> 依据 ok-script 2.0.0b7 的公开 API 整理。库升级后请用目标项目的解释器核实方法签名是否存在。

## 一、核心类

常用任务 API 都从 `ok` 导入：

```python
from ok import BaseTask, TriggerTask, Logger, ConfigOption, run_task
```

`BaseTask` 是一次性任务的基类，库初始化时会填好这些字段：

| 字段 | 含义 |
|------|------|
| `name` | 界面显示名，默认取类名 |
| `description` | 界面上的简短说明 |
| `default_config` | 用于创建与校验持久化配置的字典 |
| `config_description` | 按配置键给出帮助文本 |
| `config_type` | 显式指定配置控件类型 |
| `supported_languages` | 为空表示所有语言都可见 |
| `trigger_interval` | 两次触发之间的最小秒数 |
| `icon`、`group_name`、`group_icon` | 界面展示字段 |
| `support_schedule_task` | 是否允许被定时任务界面选用 |

`TriggerTask` 继承 `BaseTask`，用于后台任务。它向 `default_config` 追加 `_enabled`，
在 `on_create()` 里从配置载入启用状态，在 `enable()` / `disable()` 里持久化变更。

## 二、生命周期

任务先由 `executor` 与 `app` 实例化，再经 `after_init(executor=..., scene=...)` 初始化。
可用钩子：

| 钩子 | 时机与用途 |
|------|-----------|
| `__init__` | 设置元数据、默认配置、说明、控件类型、间隔、图标、语言支持 |
| `on_create` | 配置载入后执行。少用；`TriggerTask.on_create()` 已负责载入 `_enabled` |
| `post_init` | 所有内置任务创建完后再建立任务间引用或昂贵辅助对象 |
| `run` | 任务主体行为 |
| `sleep_check` | `sleep_check_interval >= 0` 时，`self.sleep()` 期间的周期回调 |
| `on_destroy` | 释放资源 |
| `validate_config` | 配置非法时返回提示字符串，合法返回 `None` |

## 三、执行器语义

执行器**先检查一次性任务**：一次性任务被启用后运行，`run()` 返回后执行器自动关闭它。

触发任务被反复扫描，只有 `enabled` 为真且 `should_trigger()` 为真时才运行。
`should_trigger()` 依据 `trigger_interval` 判定：

- `trigger_interval = 0`：每次扫描都合格；
- `trigger_interval = 5`：最多每 5 秒合格一次。

触发任务的 `run()` 返回真值时，执行器会**从头重新扫描**触发任务。因此只有确实做了
「应让更高优先级的触发任务重新有机会执行」的动作时，才返回真值。

## 四、配置模型

所有设置都走 `self.default_config`。`Config` 只持久化默认值里出现过的键，并按默认值类型校验。

```python
self.default_config.update({
    "_enabled": True,                       # 触发任务必须显式声明启用开关
    "Retry Count": 3,                       # 整数 → 数字微调框
    "Target Text": "Start",                 # 字符串 → 单行输入框
    "Accepted Text": ["Start", "开始"],      # 列表 → 可编辑列表
})
self.config_description.update({
    "Retry Count": "任务停止前的最大尝试次数。",
    "Target Text": "点击前要搜索的文本。",
})
```

界面控件由默认值类型推断：

| 类型 | 控件 |
|------|------|
| `bool` | 开关 |
| `int` | 数字微调框 |
| `float` | 浮点微调框 |
| `str` | 单行输入框；长文本或含换行时用多行编辑框 |
| `list` | 可编辑列表 |

需要指定控件时用 `config_type`：

```python
self.config_type = {
    "Mode": {"type": "drop_down", "options": ["Fast", "Safe"]},          # 下拉单选
    "Labels": {"type": "multi_selection", "options": ["A", "B", "C"]},   # 多选
    "Notes": {"type": "text_edit"},                                      # 多行文本
}
```

ok-script 界面代码中出现的显式类型：`drop_down`、`multi_selection`、`global`、
`text_edit`、`button`。

## 五、任务注册

### 5.1 内置任务注册

内置任务通常注册在应用配置里，语法是 `[模块路径, 类名]` 二元组：

```python
config = {
    "onetime_tasks": [
        ["my_project.tasks.daily", "DailyTask"],
    ],
    "trigger_tasks": [
        ["my_project.tasks.auto_pick", "AutoPickTask"],
    ],
    "scene": ["my_project.scene", "MyScene"],
}
```

本项目对应位置是 `src/config.py`，另有扩展能力经
`extensions/capabilities/<能力名>/__init__.py` 的 `register(config)` 追加注册
（见 `docs/extension-guide.md`）。两种方式选一种，不要同一任务注册两次。

### 5.2 自定义任务脚本

应用支持自定义任务时打开开关，再把 `.py` 文件放进 `ok_tasks/`：

```python
config["custom_tasks"] = True
```

加载器解析顶层类，实例化第一个 `BaseTask` 或 `TriggerTask` 的子类。

### 5.3 无界面运行

无头辅助可按索引、名称、类或实例运行：

```python
from ok import run_task
from my_project.config import config
from my_project.tasks.daily import DailyTask

run_task(config, task=DailyTask, debug=True)
```

## 六、设备、特征与 OCR 方法

优先用任务方法，不要直接访问执行器或设备层：

| 用途 | 方法 |
|------|------|
| 取帧与等待 | `self.next_frame()`、`self.wait_until(...)`、`self.sleep(...)` |
| 输入 | `self.click(...)`、`self.click_relative(...)`、`self.click_box(...)`、`self.send_key(...)`、`self.send_key_down(...)`、`self.send_key_up(...)` |
| 特征匹配 | `self.find_one(...)`、`self.find_feature(...)`、`self.wait_click_feature(...)`、`self.get_box_by_name(...)` |
| OCR | `self.ocr(...)`、`self.wait_ocr(...)`、`self.wait_click_ocr(...)` |
| 状态与日志 | `self.log_info(...)`、`self.log_warning(...)`、`self.log_error(...)`、`self.info_set(...)`、`self.info_incr(...)` |
| 任务协作 | `self.get_task_by_class(...)`、`self.run_task_by_class(...)` |

## 七、中英双语写法

在数据边界上做双语匹配，而不是在逻辑里判断语言：

```python
self.default_config.update({
    "Confirm Text": ["Confirm", "确定"],   # OCR 文本列表，命中任一即算
    "Cancel Text": ["Cancel", "取消"],
})
```

配置键保持稳定英文，说明写双语：

```python
self.config_description.update({
    "Confirm Text": "Texts to accept as confirmation buttons. 确认按钮文本列表。",
})
```

只希望在特定语言区显示该任务时：

```python
self.supported_languages = ["en_US", "zh_CN"]
```

`self.tr("...")` 只在目标项目确实有该串的翻译时才用；否则写简洁的双语文案，
或按用户要求的语言写日志。

翻译目录位于 `i18n/<locale>/LC_MESSAGES/ok.po`，编译产物为同名 `.mo`，
相关操作走 `project-ok-script-i18n`。
