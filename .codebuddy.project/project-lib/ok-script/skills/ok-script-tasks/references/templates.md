# ok-script 任务模板

> 模板里的代码注释一律中文。示例类名与配置键保持英文，因为配置键会成为持久化 JSON 字段名。
> 本项目已有 `src/tasks/MyBaseTask.py`，落地时优先继承 `MyBaseTask` 而不是直接用 `BaseTask`。

## 一、最小一次性任务

用于用户点击后跑到底的任务。

```python
from ok import BaseTask, Logger  # 导入任务基类与日志工具

logger = Logger.get_logger(__name__)  # 为本模块建立独立日志器


class ClaimRewardTask(BaseTask):  # 继承一次性任务基类
    def __init__(self, *args, **kwargs):  # 构造函数透传框架参数
        super().__init__(*args, **kwargs)  # 必须先调父类，元数据要在其后设置
        self.name = "领取奖励"  # 界面显示名
        self.description = "打开奖励界面并领取可领取的奖励。"  # 界面说明
        self.default_config = {  # 声明默认配置，键即持久化字段名
            "Retry Count": 3,  # 最大重试次数
            "Confirm Text": ["领取", "Claim"],  # 确认按钮的 OCR 文本，中英任一命中
        }
        self.config_description = {  # 逐键给出界面帮助文本
            "Retry Count": "任务停止前的最大尝试次数。",
            "Confirm Text": "要点击的按钮 OCR 文本。",
        }

    def run(self):  # 任务主体
        for attempt in range(self.config.get("Retry Count", 3)):  # 按配置次数重试
            self.info_set("Attempt", attempt + 1)  # 在界面展示当前尝试次数
            button = self.wait_ocr(match=self.config.get("Confirm Text"), time_out=3)  # 等待按钮出现
            if button:  # 命中按钮则点击并结束任务
                self.click_box(button, after_sleep=1)  # 点击命中区域并等待界面刷新
                self.log_info("任务完成", notify=True)  # 记录完成并发送通知
                return  # 正常结束，执行器会自动关闭任务
            self.next_frame()  # 未命中则取新帧，避免下一轮读到旧画面
        self.log_warning("未找到领取按钮")  # 重试耗尽后告警，不抛异常
```

## 二、触发任务

用于启用后持续检查某个条件的后台任务。

```python
from ok import TriggerTask  # 导入后台触发任务基类


class AutoClosePopupTask(TriggerTask):  # 继承触发任务基类
    def __init__(self, *args, **kwargs):  # 构造函数透传框架参数
        super().__init__(*args, **kwargs)  # 先调父类
        self.name = "自动关闭弹窗"  # 界面显示名
        self.description = "弹窗出现时自动关闭。"  # 界面说明
        self.trigger_interval = 1.0  # 两次触发之间的最小间隔，避免过度轮询
        self.default_config.update({  # 追加默认配置
            "_enabled": True,  # 启用开关，触发任务必须显式声明
            "Close Text": ["关闭", "Close"],  # 关闭按钮的 OCR 文本
        })
        self.config_description.update({  # 追加帮助文本
            "Close Text": "弹窗关闭按钮的 OCR 文本。",
        })

    def run(self):  # 每次触发时执行
        close_button = self.ocr(match=self.config.get("Close Text"))  # 单帧识别关闭按钮
        if not close_button:  # 没有弹窗，交给其他触发任务
            return False  # 返回假值，执行器继续扫描下一个触发任务
        self.click_box(close_button, after_sleep=0.5)  # 点击关闭并等待界面稳定
        self.log_info("已关闭弹窗")  # 记录动作
        return True  # 返回真值，让执行器从头重扫更高优先级的触发任务
```

继承项目基类时，`TriggerTask` 放在项目基类之前还是之后，按项目现有写法照抄，
不要混用两种方法解析顺序。

## 三、基于特征匹配的任务

特征名必须来自目标项目真实的模板匹配数据。

```python
from ok import BaseTask  # 导入一次性任务基类


class ClickFeatureTask(BaseTask):  # 定义任务类
    def __init__(self, *args, **kwargs):  # 构造函数透传框架参数
        super().__init__(*args, **kwargs)  # 先调父类
        self.name = "点击入口"  # 界面显示名
        self.description = "等待指定特征出现并点击。"  # 界面说明
        self.default_config = {  # 默认配置
            "Feature Name": "start_button",  # 特征名，须与项目模板数据一致
            "Threshold": 0.8,  # 匹配阈值
        }

    def run(self):  # 任务主体
        feature = self.config.get("Feature Name")  # 读取特征名
        threshold = self.config.get("Threshold", 0.8)  # 读取阈值
        self.wait_click_feature(  # 等待特征出现并点击
            feature,  # 特征名
            threshold=threshold,  # 传入阈值
            time_out=10,  # 超时秒数
            raise_if_not_found=True,  # 找不到即抛异常，终止任务
            after_sleep=1,  # 点击后等待界面刷新
        )
        self.log_info("已点击入口")  # 记录动作
```

## 四、下拉与多选配置

```python
self.default_config.update({  # 默认值决定控件初值
    "Mode": "Safe",  # 下拉当前值
    "Enabled Labels": ["A"],  # 多选当前值
})
self.config_type.update({  # 显式指定控件类型
    "Mode": {"type": "drop_down", "options": ["Safe", "Fast"]},  # 下拉选项
    "Enabled Labels": {"type": "multi_selection", "options": ["A", "B", "C"]},  # 多选项
})
self.config_description.update({  # 帮助文本
    "Mode": "执行模式。",  # 下拉说明
    "Enabled Labels": "允许参与匹配的标签。",  # 多选说明
})
```

`config_type` 里的每个键都必须同时出现在 `default_config` 里，否则该控件不会渲染。

## 五、内置注册

```python
config = {  # 应用配置字典
    "onetime_tasks": [  # 一次性任务清单
        ["my_app.tasks.claim_reward", "ClaimRewardTask"],  # [模块路径, 类名]
    ],
    "trigger_tasks": [  # 后台任务清单
        ["my_app.tasks.auto_close_popup", "AutoClosePopupTask"],  # [模块路径, 类名]
    ],
}
```

## 六、ok_tasks 自定义脚本

应用打开 `custom_tasks` 后，在 `ok_tasks/my_task.py` 写一个顶层任务类即可：

```python
from ok import BaseTask  # 导入一次性任务基类


class MyCustomTask(BaseTask):  # 定义自定义任务
    def __init__(self, *args, **kwargs):  # 构造函数透传框架参数
        super().__init__(*args, **kwargs)  # 先调父类
        self.name = "我的自定义任务"  # 界面显示名
        self.default_config = {"Message": "你好"}  # 默认配置

    def run(self):  # 任务主体
        self.log_info(self.config.get("Message"))  # 输出配置里的消息
```

加载器只扫描顶层类，实例化第一个 `BaseTask` 或 `TriggerTask` 子类；
一个文件放多个顶层子类会导致行为不可预期。

## 七、提交前检查清单

- 用目标项目的解释器 import 成功。
- 类能被 ok-script 以 `executor` 与 `app` 关键字参数实例化。
- `super().__init__` 在设置任何任务字段之前执行。
- 每个用户可改的设置在 `default_config` 里出现。
- `config_type` 的每个键都在 `default_config` 里。
- 触发任务含 `_enabled`，且 `trigger_interval` 取值合理。
- 目标界面可能中英混排时，OCR 文本覆盖两种写法。
- 注册路径与类名和真实模块一致。
