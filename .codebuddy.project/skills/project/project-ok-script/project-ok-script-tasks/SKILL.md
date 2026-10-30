---
name: project-ok-script-tasks
description: 创建与修改基于 PyPI ok-script 库的自动化任务类：BaseTask 一次性任务、TriggerTask 后台触发任务、任务配置界面元数据、在应用 config 中注册、ok_tasks 自定义脚本，以及中英双语的匹配文本。用户说"新建任务"、"加个后台任务"、"改任务配置项"、"任务怎么注册"、"这个 BaseTask 怎么写"、"TriggerTask 的 trigger_interval 怎么设"、"任务要不要重写 run"时使用。不适用于：只生成 run(self) 里的自动化动作代码（用 project-ok-script-codegen）、只同步翻译目录（用 project-ok-script-i18n）。
---

# ok-script 任务开发

## 定位

本技能只管**任务骨架层**：任务类怎么建、元数据怎么填、配置怎么声明、怎么注册进应用。
`run(self)` 内部的自动化动作（OCR、模板匹配、点击、等待）由 `project-ok-script-codegen` 负责，
翻译目录由 `project-ok-script-i18n` 负责。三者的边界在「产出物」上：本技能产出任务类骨架，
codegen 产出方法体，i18n 产出 `.po`/`.mo`。

指导以 `ok-script` 库本身为准，**不绑定本项目**：先读目标项目，找出它自己的基类、
场景辅助方法、特征名与注册风格，再按本项目的既有写法落地。

## 核心契约

| 维度 | 定义 |
|------|------|
| 拥有的任务 | 判断任务类型、填元数据、声明配置、实现 `run()` 骨架、注册进 config |
| 输出 | 任务类代码 + 注册行 + 需要时同步的翻译条目 |
| 排除 | 不写 OCR/模板匹配的具体动作序列（交给 codegen）；不编辑 `.po` 目录（交给 i18n）；不改框架内部实现 |
| 前提 | 已确认目标项目的任务基类与注册位置（`src/config.py` 或 `ok_tasks/`） |

## 工作流

1. **先读项目，再动手**。查看目标项目现有的任务、应用配置与项目专有基类。
   项目已有基类时优先继承它，不要直接继承 `BaseTask`。
2. **定任务类型**：
   - 用户点击执行一次、跑完自动关闭 → `BaseTask`（或项目的同名一次性基类）；
   - 启用后持续轮询检查 → `TriggerTask`；
   - 只有确实需要特征/OCR/项目辅助能力时才混入对应 mixin。
3. **在 `__init__` 里填元数据**：`name`、`description`、`default_config`、`config_description`、
   `config_type`、`supported_languages`、图标、分组、调度开关。
4. **实现 `run()`**，拆成可观察的小步骤。等待与状态判断用框架方法
   （`wait_until`、`wait_ocr`、`wait_click_feature`、`sleep`、`next_frame`），
   不要写自建的轮询循环，也不要直接调设备层。
5. **按项目风格注册**：内置配置列表、`ok_tasks/` 自定义任务目录，或已导入的脚本包。
6. **需要翻译时**同步任务文案（交给 `project-ok-script-i18n`）。
7. **验证**：项目有测试或无头运行路径时优先用它；设备相关代码跑不了时，
   至少 import 任务模块并实例化类。

## 必备规则

- 设置任务字段**之前**先调 `super().__init__(*args, **kwargs)`。
- 不要绕过 `Config`：默认值写进 `self.default_config`，读取一律 `self.config.get(...)`，
  且必须在 `after_init()` 载入配置之后。
- `TriggerTask` 的 `self.default_config['_enabled']` 要有明确默认值，并设置
  `trigger_interval` 避免过度轮询。
- 触发任务的 `run()` **只有真的处理了事情才返回真值**；返回假值表示让执行器继续扫描其他触发任务。
- 一次性任务正常结束即可，执行器会在 `run()` 返回后自动关闭它。
- 直接 `sleep` 只用于短等待；等状态变化用 `wait_until`、`wait_ocr`、`wait_click_feature`。
- 配置键保持稳定的英文（它会成为持久化 JSON 字段名），中文说明放在
  `config_description` 或翻译目录里。项目已有本地化键名风格时随项目。

## 中英双语

- 回答语言跟用户；不确定时用中文，必要时附英文标签。
- 配置**键**用稳定英文；配置**值**与 OCR 匹配文本在游戏界面可能是中文时，
  按列表给出中英两种写法（如 `["Confirm", "确定"]`）。
- `supported_languages` 只用于「在不支持的语言里隐藏该任务」，常见取值
  `en_US`、`zh_CN`、`zh_TW`、`ja_JP`、`ko_KR`、`es_ES`。
- 不要把本项目源码里的假设硬套到目标项目，除非目标项目明确也是这么写的。

## 参考文献

| 文件 | 内容 |
|------|------|
| `references/task-api.md` | 任务生命周期、执行器语义、配置模型、注册方式、设备/特征/OCR 方法清单 |
| `references/templates.md` | 一次性任务、触发任务、特征任务、下拉与多选配置、内置注册、ok_tasks 自定义脚本模板，以及提交前检查清单 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |

## 不适用范围

| 请求 | 该去哪 |
|------|--------|
| 只写 `run(self)` 里的点击、OCR、模板匹配动作 | `project-ok-script-codegen` |
| 只补/改 `i18n/<locale>/LC_MESSAGES/ok.po` | `project-ok-script-i18n` |
| 打 tag、发布版本 | `project-ok-script-release` |
| 需要提交代码 | `vcs-router` 技能路由 |

## 已知局限

- `references/task-api.md` 依据 ok-script 2.0.0b7 的公开 API 整理。库升级后字段增删不会自动
  报错，落地前用目标项目的解释器核一遍要用的方法是否存在。
- 本技能不判断「这个任务该不该做」——只负责按已有设计实现。
