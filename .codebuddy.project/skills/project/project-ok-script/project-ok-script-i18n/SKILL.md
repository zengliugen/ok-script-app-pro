---
name: project-ok-script-i18n
description: 维护 ok-script 项目的 gettext 翻译目录（`i18n/<locale>/LC_MESSAGES/ok.po` 与编译产物 `ok.mo`）：扫描任务类的界面可见文案、补齐各语言 msgid、翻译缺失条目、编译 .po 为 .mo、检查重复 msgid。用户说"加翻译"、"同步 i18n"、"任务名没翻译"、"配置项说明要翻译"、"编译 po 文件"、"ok.po 里缺哪些串"、"翻译目录怎么加语言"时使用。不负责写任务类本身（用 project-ok-script-tasks）、不负责生成自动化动作代码（用 project-ok-script-codegen）。
---

# ok-script 翻译目录维护

## 定位

ok-script 用 gettext 做界面翻译：目录在 `i18n/<locale>/LC_MESSAGES/ok.po`，
运行时读同名 `ok.mo`。**只改 `.po` 不编译 `.mo`，界面不会变**——这是本技能存在的主要理由。

本技能与任务开发的关系：任务文案由 `project-ok-script-tasks` 写出来，本技能负责让这些文案
在各语言目录里齐备并生效。

## 核心契约

| 维度 | 定义 |
|------|------|
| 拥有的任务 | 收集界面可见文案 → 比对各语言目录 → 补条目 → 翻译 → 编译 `.mo` → 校验 |
| 输出 | 修改后的 `ok.po` 与重新编译的 `ok.mo`，附本次新增/变更条目清单 |
| 排除 | 不改任务类的文案与配置键；不翻译纯日志串（除用户明确要求）；不新增语言目录除非用户要求 |
| 前提 | 目标项目存在 `i18n/` 目录；编译需要 `polib`（ok-script 自身也用它） |

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

```powershell
# 扫描任务文件，列出界面可见字符串
.\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-i18n\scripts\task_i18n_helper.py scan --task src\tasks\MyOneTimeTask.py

# 检查各语言目录的重复 msgid
.\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-i18n\scripts\task_i18n_helper.py check --i18n i18n

# 编译所有 .po 为 .mo
.\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-i18n\scripts\task_i18n_helper.py compile --i18n i18n
```

扫描器只是辅助，**不能替代读任务文件**。它能找到常见的字面量字符串，但会漏掉经常量、
导入、f-string、推导式或辅助函数拼出来的值。

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

## 与任务开发的衔接

新增或修改任务时：

1. 先用 `project-ok-script-tasks` 实现任务，并指明哪些串是界面可见的。
2. 再用本技能同步这些串的 gettext 目录。
3. 编译后，在改动摘要里同时列出 `.po` 与 `.mo`。

## 参考文献

| 文件 | 内容 |
|------|------|
| `references/catalog-rules.md` | `.po`/`.mo` 的加载机制、msgid 精确匹配的成因、`polib` 依赖与缺失时的处理、常见失效现象 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |

## 已知局限

- 扫描器基于 AST 静态分析，动态拼接出来的文案它看不到，需人工补。
- 本项目 `.venv` 未安装 `polib` 时 `compile` 会以退出码 3 报错并给出安装提示，不会静默跳过。
  这是有意设计：翻译改了却不生效，比直接报错更难发现。
