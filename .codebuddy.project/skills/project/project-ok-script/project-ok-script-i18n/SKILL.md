---
name: project-ok-script-i18n
description: 维护 ok-script 项目的 gettext 翻译目录：上游 `i18n/<locale>/LC_MESSAGES/ok.po` 与扩展能力 `<能力包>/i18n/.../ok_ext.po`。做四件事——扫描界面可见文案（任务名、配置项说明、选项、按钮、Tab 名）、补齐各语言 msgid 并翻译、把 .po 编译成 .mo、校验 .po 与 .mo 是否同步以及是否漏译。用户说"加翻译"、"同步 i18n"、"任务名没翻译"、"配置项说明要翻译"、"编译 po 文件"、"ok.po 里缺哪些串"、"翻译目录怎么加语言"、"怎么新增一种语言"、"扩展能力要翻译"、"漏译"、"漏翻"、"ok_ext.po 和 ok_ext.mo 对不上"、"改了翻译界面没变"、"翻译文件要放哪个目录"时使用。不负责写任务类本身（用 project-ok-script-tasks）、不负责生成自动化动作代码（用 project-ok-script-codegen）、不判扩展代码落位（用 project-extension-capability-guard）。
---

# ok-script 翻译目录维护

## 定位

ok-script 用 gettext 做界面翻译：目录在 `i18n/<locale>/LC_MESSAGES/ok.po`，
运行时读同名 `.mo`。**只改 `.po` 不编译 `.mo`，界面不会变**——这是本技能存在的主要理由。

本技能管**两类目录**：

| 对象 | 目录 | 域名 | 归属 |
|---|---|---|---|
| 上游框架文案 | `i18n/<locale>/LC_MESSAGES/ok.po` | `ok` | **上游文件**，改动须登记 `divergence.yaml` |
| 扩展能力文案 | `extensions/capabilities/<能力>/i18n/<locale>/LC_MESSAGES/ok_ext.po` | `ok_ext` | 项目自有，随能力包入库 |

扩展能力的译文走 `ok_ext` 域，由 `extension-i18n` 能力在启动时挂成
「扩展域 → 上游域」的 fallback 链（`extensions/capabilities/extension_i18n/translator.py`）。
**扩展文案一律不改上游 `i18n/` 目录**，否则每次合并上游都冲突。

本技能与任务开发的关系：任务文案由 `project-ok-script-tasks` 写出来，本技能负责让这些文案
在各语言目录里齐备并生效。

## 核心契约

| 维度 | 定义 |
|------|------|
| 拥有的任务 | 收集界面可见文案 → 比对各语言目录 → 补条目 → 翻译 → 编译 `.mo` → 校验 |
| 输出 | 修改后的 `ok.po` 与重新编译的 `ok.mo`，附本次新增/变更条目清单 |
| 排除 | 不改任务类的文案与配置键；不翻译纯日志串（除用户明确要求）；不新增语言目录除非用户要求 |
| 前提 | 目标目录存在；**不需要 `polib`**——解析与编译均用标准库 |

## 工作流

1. **收集文案**。读目标任务文件，取这些界面可见内容：
   - `self.name`、`self.description`；
   - `self.default_config` 的键与字符串值；
   - `self.config_description` 的字符串值；
   - `self.config_type` 里下拉、多选、列表、按钮的选项字符串；
   - OCR 文本列表——仅当它是用户可见或需要本地化时。
2. **枚举语言**。列 `i18n/*/LC_MESSAGES/ok.po`，**不要写死语言清单**。
3. **比对**。逐语言检查每个源串是否已存在。
4. **补条目**。缺失的 `msgid` 补进每个语言目录，保留已有 `msgstr`，除非用户要求改写译文。
5. **翻译**。按项目已有的语言逐个补齐缺失译文。
6. **编译**。把每个改动过的 `ok.po` 编译成 `ok.mo`。
7. **校验**。检查语法与重复 `msgid`。

## 辅助脚本

`H=.codebuddy.project/skills/project/project-ok-script/project-ok-script-i18n/scripts/task_i18n_helper.py`

```powershell
# 扫描文件或目录，列出界面可见字符串
.\.venv\Scripts\python.exe $H scan --path src\tasks
.\.venv\Scripts\python.exe $H scan --path extensions\capabilities

# 检查重复 msgid
.\.venv\Scripts\python.exe $H check --i18n i18n

# 编译所有 .po 为 .mo（改完 .po 必须跑）
.\.venv\Scripts\python.exe $H compile --i18n i18n

# 校验 .po 与 .mo 是否同步（改了 .po 忘编译会被发现）
.\.venv\Scripts\python.exe $H check-mo --i18n i18n

# 校验界面上文案是否都有译文
.\.venv\Scripts\python.exe $H check-missing --i18n i18n --path src\tasks

# 门禁：上面两项合并（供提交钩子与交付前检查调用）
.\.venv\Scripts\python.exe $H gate --i18n i18n --path src\tasks
```

**扩展能力的目录不同，参数要换**：

```powershell
# 扩展每个能力包自带 i18n/，根目录传 extensions/capabilities（脚本会递归找 .po）
.\.venv\Scripts\python.exe $H gate --i18n extensions\capabilities --path extensions\capabilities
```

退出码：`0` 通过；`1` 发现缺失或不同步；`3` 环境不可用（含不支持的 `.po` 构造）。

扫描器只是辅助，**不能替代读代码**。它能找到常见的字面量字符串，但会漏掉经常量、
导入、f-string、推导式或辅助函数拼出来的值——「扫描通过」不等于「没有漏译」。

## 目录规则

- `msgid` 必须与代码里的源串**完全一致**，多一个空格都匹配不上。
- 目录本来没排序时，新条目追加到末尾即可，不要重排整个文件。
- 纯日志串不加，除非用户明确要求。
- 重点覆盖界面可见的任务元数据、配置标签、配置选项与帮助文本。
- `msgstr` 留空只在该语言确实要回退到源语言时可接受。
- 尽量保留译者注释、flags、旧 `msgid` 与既有条目顺序。
- **改完 `.po` 必须编译 `.mo`**。

## 翻译口径

- 界面文案优先简洁，不做逐词直译。
- 占位符、代码要求的标点、热键名保持原样。
- 配置**键**持久化在 JSON 里，保持稳定；翻译目录里的条目只用于显示，
  不要改 Python 里的键名，除非项目本来就存本地化键。
- 简体（`zh_CN`）与繁体（`zh_TW`）同时存在时分开处理，不要混用字形。
- 选项列表里的每个选项串都要翻译。

## 与任务开发、扩展开发的衔接

**新增或修改任务时**：

1. 先用 `project-ok-script-tasks` 实现任务，并指明哪些串是界面可见的。
2. 再用本技能同步这些串的 gettext 目录。
3. 编译后，在改动摘要里同时列出 `.po` 与 `.mo`。

**新增或修改扩展能力时**：

1. 先判断该能力是否引入了**用户可见文案**——任务名、Tab 名、配置键、配置说明、
   下拉与多选选项、按钮文字、弹窗提示，任一命中即需要本地化。
2. 在能力包内建 `i18n/<locale>/LC_MESSAGES/ok_ext.po`（`.po` 与 `.mo` **都入库**）。
3. 跑 `gate --i18n extensions\capabilities --path extensions\capabilities`，
   缺条目就补、补完用 `compile` 重编。
4. **不要改上游 `i18n/` 目录**。

## 扩展 catalog 的三条硬约束

| 约束 | 违反后果 |
|---|---|
| **不得留空 `msgstr`** | gettext 命中空串即返回空串，`translated or key` 会回落源串——这条会在链上传导，遮蔽上游同名条目的译文 |
| **同一 `msgid` 不得出现在两个能力包里** | 加载按能力包名字典序，后者覆盖前者，胜负由目录名决定且不报错 |
| **`.po` 与 `.mo` 必须同时提交** | 只提交 `.po` 时运行时读到的仍是旧 `.mo`，翻译改了界面不变 |

## 参考文献

| 文件 | 内容 |
|------|------|
| `references/catalog-rules.md` | `.po`/`.mo` 的加载机制、msgid 精确匹配的成因、编译产物的重要性、常见失效现象 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |

## 已知局限

- 扫描器基于 AST 静态分析，**只覆盖** `self.name` / `self.description` /
  `self.default_config` / `self.config_description` / `self.config_type` 与
  `CustomTab.name` 这类已知属性。动态拼接、`f-string`、经辅助函数或配置文件生成的文案
  它看不到，`QLabel` 文本与弹窗文案也不在网内。因此 **gate 通过不等于没有漏译**，仍需人工过一遍。
- `gate` 只校验「代码里的字面量都在 catalog 里」，**不校验译文质量**，
  也不校验 catalog 里是否存在已无人使用的孤儿条目。
- 不支持的 `.po` 构造（`msgctxt`、`msgid_plural`、`fuzzy`）会以退出码 3 中止。
  本项目两份 catalog 均未使用这些构造；若日后需要复数形式，须先扩展解析器。
