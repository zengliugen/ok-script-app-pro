# extension-i18n 测试策略与验收记录

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | extension-i18n |
| 创建日期 | 2026-11-03 |
| 更新日期 | 2026-11-03 |
| 生成器 | devflow-guard/new |

## 测试策略

| 被测行为 | 为什么必须覆盖 | 对应用例 |
|---|---|---|
| 无 catalog 时不接管 `po_translation` | 接管是「有译文才生效」的开关；误接管会让上游译文被空链替换 | `TestExtensionTranslator.test_no_catalog_keeps_upstream_state` |
| catalog 不含当前语言时不接管 | catalog 可能是其他语言建的；按语言未命中就该退回上游 | `test_catalog_missing_for_locale_keeps_upstream_state` |
| 扩展条目生效 | 能力存在的主要目的 | `test_extension_entry_is_visible` |
| 未覆盖的串回落源串 | 链必须能兜底，不能抛错 | `test_missing_key_falls_back_to_source` |
| 上游在链尾兜底 | 扩展只补自己那部分，其余仍走上游 | `test_upstream_is_chained_as_fallback` |
| 扩展可覆盖上游同名条目 | 这是选 B 路径（自有域）而非语言码目录的关键理由 | `test_extension_overrides_upstream` |
| 缺 `po_translation` 时崩溃并指名文件 | 侵入性改动必须可失效；静默降级会让译文永久不生效 | `test_missing_po_translation_attribute_raises` |
| 只收集能力包内的 `i18n`，按名字排序 | 顺序决定同 msgid 的胜负，行为必须被固定 | `test_catalog_dirs_sorted_and_scoped`、`test_later_package_wins_on_duplicate_msgid` |

**刻意不测的行为**：

| 行为 | 不测的原因 |
|---|---|
| 真实 GUI 下的界面文字渲染 | 依赖图形环境与 Qt 事件循环；挂链正确性与界面无关，界面呈现由上游保证 |
| `App` / `HeadlessApp` 的真实构造 | 需完整运行依赖与设备；替身已覆盖「`po_translation` 存在性」这一唯一契约 |
| 真实 locale 切换 | 切换要重启应用（框架 `restart=True`）；语言名注入已覆盖 |
| `task_i18n_helper.py` 的脚本自身逻辑 | 它属项目侧工具（`.codebuddy.project/`），不在 `python-lint` 与 `extensions_tests/` 的覆盖范围；已用命令行实测覆盖各分支 |

## 验收记录

| 日期 | 测试根 | 用例数 | 结论 | 环境 |
|---|---|---|---|---|
| 2026-11-03 | `extensions_tests/`（项目扩展） | 9（`TestExtensionI18n`） | 通过 | Windows 10 + Python 3.12 + ok-script 2.0.0b7 |
| 2026-11-03 | `extensions_tests/`（全部） | 38（9 + 11 + 11 + 7） | 通过 | 同上 |
| 2026-11-03 | `tests/`（上游基线） | 见 `TestMain` 输出 | 通过 | 同上 |

> **必须写明依据的是哪个测试根。** 框架基线与自有代码两类测试的结论不可互换：
> 自有代码全绿不能证明框架行为完好，框架基线全绿也不能证明自有代码正确。

**用例数必须填**：测试因缺少依赖而整类跳过时，框架往往仍返回成功——全部跳过与全部通过，退出码可能相同。

本次两个测试根均无跳过：`extensions_tests` 的 4 个文件都输出了实际 `Ran N tests` 且 `OK`。

## 补充验收（非用例）

| 检查 | 命令 | 结果 |
|---|---|---|
| 扩展体检（9 项） | `extension_capability_guard.py audit` | 错误 0、警告 0、提示 0 |
| 文档契约 | 同上（委托段） | 错误 0、警告 0、提示 0 |
| 上游翻译门禁 | `task_i18n_helper.py gate --i18n i18n --path src/tasks` | 通过（两语言各 45 条已覆盖） |
| 扩展翻译门禁 | `gate --i18n extensions/capabilities --path extensions/capabilities` | 通过（当前无 catalog，按正常跳过） |
| 触发评测 | `trigger_eval.py` | Precision 0.85 / Recall 1.0（基线 0.80 / 1.00，同为 3 条既有误报） |
| 引擎登记 | `check_manifest.py` + `pack_install.py status` | 均一致 |

## 已知盲区

| 盲区 | 影响面 | 缓解手段 |
|---|---|---|
| 扫描器只认字面量 | 动态拼接、`f-string`、辅助函数生成的文案不会被 `gate` 发现 | 规范要求人工过一遍；`gate` 输出「已覆盖 N 条」而不是「无问题」 |
| 不校验译文质量 | `msgstr` 写错、错译、语序错都判为通过 | 评审；工具只保证「有条目且已编译」 |
| 不检查孤儿条目 | catalog 里已无人使用的条目不会被报 | 人工清理；当前上游 catalog 有 27 条这类条目（OCR 相关，属历史遗留） |
| 跨能力包重复 msgid 只固化行为不报错 | 后者覆盖前者，胜负由目录名决定 | `layout.mdc` 明确禁止；测试 `test_later_package_wins_on_duplicate_msgid` 固定了实际行为 |
| 不支持复数与上下文 | 一旦需要 `msgid_plural` 或 `msgctxt`，脚本以退出码 3 中止 | 明确报错而非静默误判；届时需扩展解析器 |
