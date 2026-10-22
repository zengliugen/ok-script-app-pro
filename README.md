# ok-script-app-pro

[English](README_en.md) | 中文

ok-script-app-pro 是基于 [ok-script-app](https://github.com/ok-oldking/ok-script-app) 模板的能力扩展项目，底层依赖 [ok-script](https://github.com/ok-oldking/ok-script)。它在保留上游模板能力（Windows 原生游戏、Android 模拟器、浏览器自动化）的基础上，作为后续扩展开发的载体。

仓库当前保持模板结构，扩展能力将在 `src/` 下逐步叠加，任务示例、OCR、模板匹配、配置控件、测试、i18n、EXE 打包与更新发布配置可直接复用。

## 文档

完整文档已整理为 MkDocs 网站源文件：

- [文档首页](docs/index.md)
- [快速开始](docs/getting-started.md)
- [应用与运行目标配置](docs/configuration.md)
- [任务开发](docs/tasks.md)
- [打包与发布](docs/release.md)
- [构建文档网站](docs/documentation.md)
- [English documentation](docs/en/index.md)

## 快速预览

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
$PypiIndex = "https://pypi.org/simple/"
python -m pip install --index-url $PypiIndex --upgrade pip
python -m pip install --index-url $PypiIndex --no-deps --upgrade -r requirements.txt
python main_debug.py
```

直接依赖统一维护在 `pyproject.toml`。`requirements.txt` 是 Qt profile 的锁定
文件，`requirements-web.txt` 是 Web profile 的锁定文件；请勿直接编辑这些
生成文件。Web profile 请将上面的依赖和启动命令分别替换为
`requirements-web.txt` 和 `python web_main_debug.py`。官方 PyPI 的 pip 索引地址
必须包含 `/simple/`；`https://pypi.org/` 不是有效的包索引地址。

## 构建文档网站

```powershell
python -m pip install --index-url https://pypi.org/simple/ -r requirements-docs.txt
python -m mkdocs serve
```

访问 `http://127.0.0.1:8000/` 预览。生成静态 HTML：

```powershell
python -m mkdocs build --strict
```

输出位于 `site/`。`.github/workflows/docs.yml` 可将网站自动发布到 GitHub Pages，具体设置见[文档网站说明](docs/documentation.md)。

## 与上游的关系

- 上游模板：[ok-oldking/ok-script-app](https://github.com/ok-oldking/ok-script-app)
- 本项目：扩展能力在模板之上开发，通过 GitHub `upstream` 远端同步上游更新
- 发布与更新：安装包与更新源均指向本项目仓库

## 致谢

- [ok-script-app](https://github.com/ok-oldking/ok-script-app)
- [ok-script](https://github.com/ok-oldking/ok-script)
- [OnnxOCR](https://github.com/ok-oldking/OnnxOCR)
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
