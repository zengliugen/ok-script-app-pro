# 快速开始

## 1. 获取仓库

本项目从上游模板派生，已按扩展项目的需要完成初始化。直接 clone 本项目：

```bash
git clone https://github.com/zengliugen/ok-script-app-pro.git
cd ok-script-app-pro
```

仓库已配置两个远端：

```bash
git remote -v
# origin   https://github.com/zengliugen/ok-script-app-pro.git
# upstream https://github.com/ok-oldking/ok-script-app.git
```

项目在模板之上开发扩展能力。需要跟随上游更新时，从 `upstream` 拉取：

```bash
git fetch upstream
git merge upstream/master
```

合并后重新检查 `src/config.py`、`pyappify.yml` 与 `.github/workflows`，避免上游改动覆盖本项目的标识与发布配置。

## 2. 安装 Python 3.12 和项目依赖

安装 [Python 3.12.10](https://www.python.org/downloads/release/python-31210/)，然后在仓库目录中执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
$PypiIndex = "https://pypi.org/simple/"
python -m pip install --index-url $PypiIndex --upgrade pip
```

根据应用的运行目标选择一个依赖 profile。Qt 桌面界面：

```powershell
python -m pip install --index-url $PypiIndex --no-deps --upgrade -r requirements.txt
python main_debug.py
```

Web 界面：

```powershell
python -m pip install --index-url $PypiIndex --no-deps --upgrade -r requirements-web.txt
python web_main_debug.py
```

锁定文件用于可重复安装，是日常开发的推荐方式。如果需要直接从
`pyproject.toml` 解析最新的兼容依赖，可以分别使用：

```powershell
python -m pip install --index-url $PypiIndex ".[qt]"
python -m pip install --index-url $PypiIndex ".[web]"
```

只需执行与目标对应的一条命令。官方 PyPI 的 pip 索引入口是
`https://pypi.org/simple/`，而不是网站首页 `https://pypi.org/`；使用后者会导致
`No matching distribution found`，即使该包实际存在。

通常不需要管理员权限。如果目标游戏以管理员权限运行，自动化程序也需要以相同权限启动，否则截图或输入可能无法生效。

## 3. 确认应用配置

项目标识已初始化为 `ok-script-app-pro`，三种运行目标（Windows、Android 模拟器、浏览器）的结构均保留。开发扩展能力时可以：

1. 按[应用配置](configuration.md)确认应用名称、运行目标、图标和更新仓库。
2. 按[任务开发](tasks.md)新增并注册扩展任务。
3. 启动 Debug 模式：

```powershell
python main_debug.py
```

4. 运行测试：

```powershell
python -m unittest tests.TestMain
```

5. 验证完成后，按[打包与发布](release.md)配置工作流并推送 tag。
