# ok-script-app-pro

[English](README_en.md) | 中文

ok-script-app-pro 是基于 [ok-script-app](https://github.com/ok-oldking/ok-script-app) 模板的能力扩展项目，底层依赖 [ok-script](https://github.com/ok-oldking/ok-script)。它在保留上游模板能力（Windows 原生游戏、Android 模拟器、浏览器自动化）的基础上，作为后续扩展开发的载体。

仓库保持模板结构，项目扩展写在 `extensions/` 下（上游不存在的目录）；任务示例、OCR、模板匹配、配置控件、测试、i18n、EXE 打包与更新发布配置可直接复用。

## 文档

完整文档已整理为 MkDocs 网站源文件：

- [文档首页](docs/index.md)
- [快速开始](docs/getting-started.md)
- [应用与运行目标配置](docs/configuration.md)
- [任务开发](docs/tasks.md)
- [扩展开发](docs/extension-guide.md)
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

本项目对上游做了哪些修改、每处为什么改、怎样防止修改面扩大，见
**[本仓库对上游的修改说明](UPSTREAM_CHANGES.md)**（共 22 个文件被修改，0 个被删除）。

### 首次检出

克隆后**必须先运行一次** `.codebuddy/setup.cmd`（macOS / Linux 为 `./.codebuddy/setup.sh`）。
它生成 `.codebuddy.project/` 配置骨架、建立 `settings.json` 与可选包的软链接，并配置引擎的
提交前校验钩子。这三类产物都不随仓库分发（在 `.git/` 或磁盘上），跳过会表现为配置读不到、
可选包技能不加载、引擎提交时没有一致性校验。

### 扩展约定

新增能力一律写在 `extensions/` 下，**不修改上游文件**。`src/config.py` 相对上游只保留一行
接线（`from extensions import bootstrap; bootstrap.apply(config)`），其余 config 改动走
`extensions/overrides.py`。扩展测试放 `extensions_tests/`。

**代码按能力分包**：一个能力一个包，落点是 `extensions/capabilities/<能力名>/`，包内自包含
该能力的实现与注册契约 `register(config)`；新增能力只需在 `extensions/capabilities/registry.py`
的清单里追加一行。目录名、清单条目与 `extensions_docs/` 能力总览表的「能力」列三者必须一致。
不按 `core/`、`tasks/`、`ui/` 这类技术层次分目录——那样改一个能力要翻几个目录，
删一个能力必然漏文件。

扩展能力的设计、实施计划、测试策略与决策记录见 **[extensions_docs/](extensions_docs/README.md)**；
该目录只放文档，不参与运行时与打包。新建能力用 `project-extension-capability-guard` 技能生成骨架并体检。

两套规范分工：`rules/project/project-extension-capability/` 管**不动上游该怎么做**，
`rules/project/project-upstream-compat/` 管**上游文件能不能改、改了怎么登记**。

这样上游更新时冲突面接近零；确实无法避免的上游改动，必须登记到
`.codebuddy.project/configs/upstream/divergence.yaml`，由 `upstream-guard` 技能校验。

详见[扩展开发指南](docs/extension-guide.md)。

## 致谢

- [ok-script-app](https://github.com/ok-oldking/ok-script-app)
- [ok-script](https://github.com/ok-oldking/ok-script)
- [OnnxOCR](https://github.com/ok-oldking/OnnxOCR)
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
