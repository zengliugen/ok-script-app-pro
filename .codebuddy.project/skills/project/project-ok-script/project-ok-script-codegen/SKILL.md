---
name: project-ok-script-codegen
description: 按用户描述和可选截图，生成 ok-script 任务 run(self) 方法内的 Python 自动化代码。用户说"帮我写这段自动化"、"根据截图生成点击步骤"、"OCR 识别这个按钮然后点"、"这段 run 方法怎么写"、"模板匹配怎么写"、"为什么要点完要 sleep"、"等待特征出现再点"时使用。产出物是 run(self) 方法体（OCR、模板匹配、相对坐标点击、等待方法、帧刷新规则、逐行中文注释），不负责任务类骨架与注册（用 project-ok-script-tasks）、不负责翻译目录（用 project-ok-script-i18n）。
---

# ok-script 自动化代码生成

## 定位

产出物只有一样：`run(self)` 方法内的自动化代码。任务类怎么建、元数据怎么填、怎么注册
不由本技能决定——若用户还没建任务类，先用 `project-ok-script-tasks` 建骨架，再回到本技能。

API 细节以官方文档为准：
`https://raw.githubusercontent.com/ok-oldking/ok-script/refs/heads/master/docs/api_doc/README.md`

## 响应方式

| 用户意图 | 输出 |
|---|---|
| 要自动化代码 | 直接给 `run(self)` 的 Python 代码 |
| 问生成好的代码怎么用 | 说明如何放进 `BaseTask` 子类并运行 |
| 两者都要 | 先代码，后简短说明 |
| 需求含糊 | 仅在合理假设风险高时问 1—3 个问题，不要连环追问 |

除非用户要求解释，**只返回代码**。

## 生成规则

- 代码要能跑：兼容 ok-script、ok-script-app 与普通 Python。
- **不要编造 API**。只用官方文档里的 ok-script 任务 API，或目标项目里已存在的 API。
- 依赖状态的界面跳转优先用 `wait_` 系列：`wait_ocr`、`wait_click_ocr`、
  `wait_feature`、`wait_click_feature`。
- 除非在循环内或需要自定义多步检测，否则优先 `wait_` 而不是 `find_`、`ocr` 这类单帧方法。
- 坐标优先用 0 到 1 的相对值，点击用 `click_relative(x, y)`。
- OCR 或模板匹配返回框后，用 `click_box(box)` 点击。
- **不要**写 `self.ensure_in_front()`，除非用户明确要求把窗口切到前台。
- 进度与状态用 `self.log_info(...)`、`self.log_warning(...)`、`self.info_set(...)`。
- 非必要不引入 OpenCV；除用户明确要求，不加外部库。

## 逐行中文注释（硬约束）

- 每一行生成的代码都要有行内注释说明这行做什么。
- 控制流行（`if`、`for`、`while`、`return`）同样要注释。
- `import` 行也要注释。
- 空行不需要注释。
- 注释简洁、说清意图，不复述语法。

```python
import re  # 导入正则模块，用于 OCR 文本的部分匹配。

def run(self):  # 定义任务运行入口。
    self.log_info("开始执行任务")  # 记录任务开始执行。
    start_button = self.wait_ocr(match="开始", time_out=5)  # 等待 OCR 精确匹配「开始」按钮。
    if start_button:  # 如果找到了开始按钮，就继续执行点击。
        self.click_box(start_button, after_sleep=0.5)  # 点击找到的按钮，并等待界面刷新。
```

## 帧刷新规则

这几条是最容易写错的地方，违反会表现为「读到旧画面」而不是报错：

- `self.sleep(...)` 会**清空当前帧**。
- `self.next_frame()` 清零当前帧并取新帧。
- 带 `after_sleep` 的 API（`click(..., after_sleep=...)`、
  `click_relative(..., after_sleep=...)`、`wait_click_ocr(..., after_sleep=...)`）
  在等待后同样会清空当前帧。
- 点击、按键、滑动等改变界面的动作之后，要等界面稳定再做单帧检测，例如 `self.sleep(0.5)`。
- 循环内每轮都要取新帧：调 `self.sleep(...)` 或 `self.next_frame()`。
- **循环内不要调 `self.exit_is_set()`**。用户手动停止任务时 `sleep` 会抛异常并结束任务，
  自己判退出是多余的，且会漏掉这条中断路径。
- 循环内的轮询用 `self.sleep(...)` 控制节奏。
- `wait_` 方法内部自带循环并自动取新帧，所以**不要**在 `wait_ocr`、`wait_click_ocr`、
  `wait_feature`、`wait_click_feature` 之前加多余的 `sleep`。
- 只有动作触发动画、加载或界面延迟变化时，才补短 sleep。

## OCR 规则

- 全屏 OCR 较贵，知道文字位置时尽量缩小区域。
- 给了截图时，尽量据截图推断 OCR 区域。
- `match="文本"` 是精确字符串匹配。
- 字符串匹配即精确匹配，要部分匹配得用正则，例如 `re.compile("部分文本")`。
- `match` 可同时接受字符串与正则对象：`match=["文本1", "文本2", regex1, regex2]`。
- 多个文本命中任一即可时，用列表一次 OCR 完成。
- 要求多个文本同时存在时，调一次 `ocr` 后用 Python 判断返回的框。
- `ocr` 返回所有满足条件的框。
- 复杂文本查找可以直接 `texts = self.ocr()` 一次，再本地处理。
- OCR 对特定文本稳定误识别时用 `add_text_fix`。
- 用了正则就要 `import re`，除非确认所在文件已导入。

## 模板匹配规则

- 除非用户明确给出已有模板名，否则**假定项目里还没有模板**。
- 不要凭空编造真实模板名。
- 模板匹配是对的做法、但用户没给模板名时，用明确的占位名（如 `"start_button_template"`），
  并在注释里写清用户需要手动标注什么。
- 模板使用处的注释要说清待标注的界面元素或图像区域：开始按钮图标、设置齿轮、奖励图标、
  固定横幅等。
- 等模板出现用 `wait_feature`；等并点击用 `wait_click_feature`。
- `find_one`、`find_feature` 只用于单帧检测或自定义循环检测。

## 截图推断

用户附了截图时：

- 按截图尺寸换算相对点击坐标。
- 截图里有稳定文字或稳定图标时，优先 OCR 或模板匹配。
- 目标是视觉元素但 OCR/模板都不可靠时，才用相对坐标。
- 坐标是按截图估算的，就在注释里写明。
- 不要声称「精确」，用谨慎措辞，如「根据截图估算」。

## 错误处理

- 可选等待用 `raise_if_not_found=False`，随后判空并记警告。
- 只有失败就该终止任务时才用 `raise_if_not_found=True`。
- 失败不该让任务崩时，`self.log_warning(...)` 后 `return`。
- `try/except` 只包住真正有意义的失败边界，并用 `self.log_error(...)` 记录。
- 除非用户明确要求持续自动化，不要写死循环。
- 用户确实要循环时，用 `while True:`，循环内放 `self.sleep(...)`，且**不写** `self.exit_is_set()`。

## 代码怎么用

用户问怎么用时，简述：

1. 把生成的 `run(self)` 放进继承 `BaseTask` 的任务类。
2. 任务文件放 `src/tasks`（本项目）或 `ok_tasks`（自定义任务），取决于项目结构。
3. 任务需要特定窗口、浏览器、模拟器或 ADB 设备时，配置 `capture_config`。
4. 用到模板匹配时，手动标注引用的模板，并保证名字与代码一致。
5. 启动应用，选中任务，运行。
6. OCR 或点击失败时，据截图与日志调整 OCR 区域、阈值、模板名或相对坐标。

## 输出前自检

- 只用了 ok-script 的 API。
- 每个非空代码行都有行内中文注释。
- 没有 `self.ensure_in_front()`，除非用户明确要求。
- 循环里没有 `self.exit_is_set()`。
- UI 等待优先用了 `wait_` 方法。
- 多目标 OCR 尽量用列表一次完成。
- 部分文本匹配用了正则。
- 模板使用处写明需要手动标注，除非用户给了已有模板名。

## 参考文献

| 文件 | 内容 |
|------|------|
| `references/frame-and-wait.md` | 帧生命周期与等待方法的取舍、写错后的可观察现象 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |
