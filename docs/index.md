# ok-script-app-pro

[English](en/index.md)

ok-script-app-pro 是基于 [ok-script-app](https://github.com/ok-oldking/ok-script-app) 模板构建的能力扩展项目，底层依赖 [ok-script](https://github.com/ok-oldking/ok-script)，适用于 Windows 原生游戏、Android 模拟器和浏览器游戏。

这个仓库不是某个具体游戏的自动化成品。它保留上游模板的可运行 GUI、任务与配置控件示例、OCR、模板匹配、测试、i18n、EXE 打包和更新发布配置，并在此基础上叠加扩展能力。

## 从这里开始

1. 按照[快速开始](getting-started.md)准备本地开发环境。
2. 在[应用配置](configuration.md)中确认运行目标与项目标识。
3. 根据[任务开发](tasks.md)新增扩展任务。
4. 使用[打包与发布](release.md)中的流程生成 EXE。

## 与上游的关系

本项目通过 `upstream` 远端跟踪 [ok-script-app](https://github.com/ok-oldking/ok-script-app)：上游提供模板骨架与通用能力，本项目在其之上开发扩展功能，并将安装包与更新源指向本项目仓库。

## 功能演示

### API 列表与脚本录制

![API 列表与脚本录制](images/image_scripting.png)

### 多种截图与交互方式

![截图与交互方式](images/image_capture.png)

### 标注管理与模板匹配

![模板匹配](images/image_template.png)

![标注管理](images/image_markup.png)

## 主要内容

- 保留上游的 `MyOneTimeTask` 和 `MyTriggerTask` 任务示例。
- 下拉框、布尔值、数值、文本、列表、多选、文件选择、全局配置和按钮组等配置控件。
- OCR、相对区域识别和模板匹配示例。
- `ConfigOption` 全局配置和 `TaskTestCase` 自动化测试示例。
- gettext i18n 翻译文件和编译产物。
- PyAppify 和 GitHub Actions 打包发布配置。

## 继续阅读

- [游戏自动化入门](https://github.com/ok-oldking/ok-script/blob/master/docs/intro_to_automation/README.md)
- [ok-script 快速开始](https://github.com/ok-oldking/ok-script/blob/master/docs/quick_start/README.md)
- [进阶使用](https://github.com/ok-oldking/ok-script/blob/master/docs/after_quick_start/README.md)
- [API 文档](https://github.com/ok-oldking/ok-script/blob/master/docs/api_doc/README.md)
