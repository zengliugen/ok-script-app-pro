# 代码落位检查项与判据

> 本文件承接 `SKILL.md` 的检查细则，避免初始加载超预算。执行 `audit` 前按需读取。

## `audit` 的两段输出

1. **委托段**：调 `devflow_guard.py audit`，原样转发其输出与退出码。委托目标不存在
   （未安装 devflow 包）时该段报错误，整体退出码 3。
2. **代码落位段**：本技能独有的九项检查，读 `extensions/` 下的 Python 源码与翻译资源。

## 九项检查

| 检查项 | 判据 | 为什么脚本能判 |
|---|---|---|
| patch 断言 | `patches/*.py` 的 `apply()` 内必须有 `raise` | ast 解析函数体，不靠文本匹配（注释里的 raise 不算） |
| sys.path 污染 | `extensions/` 内不得 `sys.path.insert/append` 自己 | 文本匹配 + 同行含 extensions |
| 包结构 | `extensions/` 每层目录必须有 `__init__.py` | 目录遍历 |
| **能力分包** | 顶层条目在白名单内；`capabilities/` 下的目录与 `registry.py` 的 `CAPABILITIES` **双向一致**；每个能力包有 `register` 函数 | ast 读清单字面量（不 import，避免被 import 失败连带失效）+ 目录遍历 |
| **能力命名** | 能力包名按 `-`↔`_` 换算后能对上文档总览表「能力」列 | 从 `project.index.json` 找 devflow 配置，读文档根的 `README.md` 首列 |
| **能力隔离** | 能力包内不得 `from/import extensions.capabilities.<其他能力>` | 按行正则，截断到能力包一级 |
| **配置键** | 键须为可打印 ASCII 且首尾无空白；`_` 开头的键不得出现在 `config_description`/`config_type`；两处的键须在 `default_config` 里存在 | ast 读三处字典的字面量键，非字面量键报「提示」而不放过 |
| **本地化** | 能力包的 `i18n/<locale>/LC_MESSAGES/ok_ext.po` 必须有同名 `.mo`；两者条目与译文须一致；不得有空 `msgstr`；有界面文案却没建 catalog 报警告 | 标准库解析 po/mo（不依赖 polib，构建链也不编译 `.po`），按目录遍历 |
| 登记同步提示 | `extensions/` 有改动但总览表未变 | `git status` 比对，仅为提示 |

## 能力分包的六项判据

前四项报错误，末两项报错误或提示：

1. `extensions/` 顶层只允许 `__init__.py`、`bootstrap.py`、`overrides.py`、`app_globals.py`、
   `capabilities/`、`patches/`；
2. 清单条目必须在 `extensions.capabilities.` 下且目录真实存在；
3. `capabilities/` 下的目录必须在清单里登记，否则**永远不会被装载**；
4. 每个能力包的 `__init__.py` 必须定义 `register` 函数（**缺契约静默不生效**）；
5. 能力包名换算成 kebab-case 后未在总览表登记 → **提示**（新建能力先写代码后补文档是正常顺序）；
6. 能力之间互相 import → 错误。

## 脚本判断不了什么

**不查「用哪条扩展路径」「能力归属是否合理」**——那要读懂框架能力，脚本判断不了，走评审
（判据见规则 `extension-capability/methods.mdc`）。
