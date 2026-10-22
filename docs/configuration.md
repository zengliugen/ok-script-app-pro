# 应用与运行目标配置

## 应用设置

在 `src/config.py` 中至少检查：

- `gui_title`：应用窗口名称。
- `gui_icon`：GUI 图标路径。
- `supported_resolution`：支持的画面比例和最低分辨率。
- `links`：项目主页、反馈渠道和社区链接。
- `onetime_tasks`、`trigger_tasks`：任务注册列表。

## 运行目标

`windows`、`adb`、`browser` 必须至少配置一种，也可以同时配置多种。

### Windows 原生游戏

配置 `windows` 中的：

- `exe`：游戏进程文件名列表。
- `hwnd_class`：可选的窗口类名，用于提高窗口匹配准确度。
- `interaction`：允许使用的输入方式及优先级。
- `capture_method`：允许使用的截图方式及优先级。
- HDR 和后台截图相关选项。

### Android 模拟器或设备

在 `adb.packages` 中填写游戏包名。MuMu 模拟器可使用原生截图和输入；其他模拟器或设备通常通过 ADB 工作。

### 浏览器游戏

取消 `browser` 示例配置的注释，并设置：

```python
'browser': {
    'url': 'https://example.com/game',
    'nick': 'Browser',
    'resolution': (1280, 720),
},
```

浏览器目标还需要 `playwright`。将它加入 `pyproject.toml` 中需要浏览器目标的
profile，然后重新编译对应的 `requirements.txt` 或 `requirements-web.txt`，
确保本地环境和 GitHub 打包环境都会安装该依赖。

## 替换图标

用自己的资源替换 `icons/icon.png` 和 `icons/icon.ico`。保持文件名不变可以避免额外配置；如需改名，同步修改 `src/config.py` 和 `pyappify.yml` 中的路径。

## 更新仓库

本项目已将 `pyappify.yml` 的三个 profile 全部指向源码仓库 `https://github.com/zengliugen/ok-script-app-pro.git`，早期开发阶段无需维护独立更新仓库。

- 应用名称：`ok-script-app-pro`。
- `git_url`：源码仓库，安装包的自动更新直接从这里拉取。
- 后续如需减轻用户更新体积，可以另建轻量更新仓库，并同步修改 `.github/workflows/build.yml` 的同步步骤。

## 上游同步后的检查

本项目从 `upstream`（`ok-oldking/ok-script-app`）合并更新后，重新确认以下内容没有被上游覆盖：

- `src/config.py` 中的 `gui_title` 与 `links`。
- `pyappify.yml` 中的 `name` 与各 profile 的 `git_url`。
- `.github/workflows/build.yml` 中的 Git 身份、安装包名称与下载链接。
