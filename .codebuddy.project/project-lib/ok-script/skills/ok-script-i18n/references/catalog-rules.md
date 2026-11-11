# 翻译目录规则与失效成因

本文件解释 i18n 规则背后的机制。改翻译目录前先读这一份，能避开「改了却看不到效果」这类问题。

## 一、运行时到底读什么

源码依据：`ok/core/translation.py`

```python
def get_translations(language):
    fallback_list = get_language_fallbacks(language)
    gettext.textdomain('ok')
    dir_i18n = get_path_relative_to_exe('i18n')
    return gettext.translation('ok', dir_i18n, languages=fallback_list)
```

三件事由此确定：

1. **读的是 `.mo`，不是 `.po`**。`gettext.translation` 只认编译产物。
2. **目录结构固定**：`i18n/<locale>/LC_MESSAGES/ok.mo`，`ok` 这个域名不可改。
3. **语言回退由 `get_language_fallbacks` 决定**：
   - `zh_*` 简体系（`zh_CN`、`zh_SG` 等）→ `['zh_CN', 'zh']`；
   - `zh_HK`/`zh_TW`/`zh_MO` 繁体系 → `['zh_TW', 'zh']`；
   - 其他语言 → `[target]`，目标名与主语言码不同时再加主语言码（如 `en_US` → `['en_US', 'en']`）。

推论：**只建 `zh_CN` 时，繁体用户会回退到 `zh` 目录**，若 `i18n/zh/` 不存在则直接回退到源串。
新增语言前先看现有目录结构，不要假设只靠 `zh_CN` 就能覆盖全部中文用户。

## 二、msgid 必须与源串逐字符一致

`gettext` 用 `msgid` 做精确字符串查找。差一个空格、一个全角半角标点、一个换行，
就查不到，界面会显示源串本身——**不报错**。

实际踩过的形态：

| 形态 | 结果 |
|------|------|
| 代码 `"一键日常 "`（尾部空格），`msgid "一键日常"` | 查不到 |
| 代码用 f-string `f"第{n}关"` | 无法与固定 msgid 对上，需改用带占位符的条目 |
| 代码从常量或配置文件拼出来 | 同上，静态扫描也看不到 |
| 代码 `"确定"`，目录里写 `"确认"` | 查不到 |

因此：

- 加条目时**从代码复制原文**，不要手打。
- 动态文案要么改成带占位符的形式（`"第 %(n)s 关"`），要么放弃本地化。
- 扫描器（`task_i18n_helper.py scan`）只覆盖字面量常量，它列出的清单是**下限**不是全集。

## 三、为什么必须编译

`.po` 是给人和工具看的源文件，`.mo` 是二进制查表文件，两者不会自动同步。
ok-script 的调试界面里有一个「转换翻译」按钮（`convert_to_mo_files`），
但那是给人工用的，脚本与技能必须自己编译，否则：

- 新加的条目在 `.po` 里看得见；
- 界面照旧显示英文／源串；
- 没有任何报错，排查方向会被引向「代码没调 `tr()`」这类错误假设。

**规则：改完 `.po` 必须编译 `.mo`，两者一起进改动摘要。**

## 四、polib 依赖

`polib` 用来读写 `.po`，ok-script 自身在 `update_po_file` 与 `convert_to_mo_files` 里
也用它。但它**不是本项目的运行依赖**（`pyproject.toml` 里没有），
所以可能出现在 `.venv` 里装不上的情况。

本技能的处理：缺 `polib` 时 `compile` 与 `check` 以**退出码 3** 报错，并打印安装命令；
`scan` 不依赖 `polib`，仍可运行。**不做静默跳过**——跳过的后果是「翻译改了不生效」，
比报错难查得多。

安装（用项目解释器，不要装到全局）：

```powershell
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple/ polib
```

## 五、目录编辑规矩

| 规矩 | 原因 |
|------|------|
| 新条目追加到末尾，不重排全文 | 重排会让 diff 变成整文件改写，无法评审 |
| 保留译者注释、flags、旧 msgid | `polib` 回写时会保留，人工编辑容易误删 |
| `msgstr` 留空仅当该语言确实回退到源语言 | 空串在 `gettext` 里等于「无译文」，界面上会显示 `msgid` |
| 配置键保持英文 | 键会进持久化 JSON，改键会让用户旧配置失效 |
| 日志串默认不翻译 | 日志不面向终端用户，翻了反而增加维护面 |

## 六、失效现象对照表

| 现象 | 最可能的原因 |
|------|-------------|
| 界面全是英文，其他任务都正常 | 该语言 `.mo` 没编译，或编译了旧的 `.po` |
| 只有新加的条目没翻译 | 条目只加进了部分语言目录 |
| 所有条目都不生效 | `i18n/` 目录不在可执行文件同级，或目录名不是 `ok.mo` |
| 某一条死活不生效 | `msgid` 与源串有不可见差异（空格、全角半角） |
| 繁体用户看到简体 | 只有 `zh_CN`，回退到了 `zh` 或源串 |
| `check` 报重复 msgid | 同一串被追加过两次，`gettext` 取值不确定 |
