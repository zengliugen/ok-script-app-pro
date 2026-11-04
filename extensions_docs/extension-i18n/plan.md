# extension-i18n 实施计划

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | extension-i18n |
| 创建日期 | 2026-11-03 |
| 更新日期 | 2026-11-03 |
| 生成器 | devflow-guard/new |

## 目标

扩展能力可以在不改上游 `i18n/` 的前提下提供界面文案译文，且漏译与 `.po`/`.mo` 不同步
会被机械检查发现。对应设计文档：`extension-i18n/design.md`。

## 分批任务

| 批次 | 内容 | 涉及文件 | 验收标准 |
|---|---|---|---|
| 一 | i18n 辅助脚本零依赖化：po 解析、mo 写出、scan 支持目录、新增 check-mo / check-missing / gate、加 --domain | `project-ok-script-i18n/scripts/task_i18n_helper.py` | 无 polib 也能 compile / check / gate；伪造缺译样例时 gate 退出 1 |
| 二 | 新增 `extension_i18n` 能力：translator 挂链 + 契约 + 注册 + `on_app_ready` 派发 | `extensions/capabilities/extension_i18n/`、`registry.py`、`app_globals.py` | 启动后 `app.po_translation` 为链式对象；无 catalog 时保持原状 |
| 三 | 体检适配：包结构排除 i18n 子树；新增 check_i18n_catalogs | `extension_capability_guard.py`、其 `SKILL.md` | i18n 子树不再报缺 `__init__.py`；po/mo 不一致报错误 |
| 四 | 规范层本地化条款 | 入口规则、`RULE.mdc`、`layout.mdc`、`docs.mdc` | 条款可判、与脚本判据一致 |
| 五 | 接入 H1 门禁与发布清单 | `vcs-git-commit/project-config.md`、`release-checklist.md` | gate 失败阻断提交；发布清单含该项 |
| 六 | 测试 | `extensions_tests/TestExtensionI18n.py` | 覆盖挂链顺序、空 catalog 退化、断言崩溃、空 msgstr 遮蔽 |

## 文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `.codebuddy.project/skills/project/project-ok-script/project-ok-script-i18n/scripts/task_i18n_helper.py` | 重写 | 零依赖 po/mo，新增三个子命令 |
| `.codebuddy.project/skills/project/project-ok-script/project-ok-script-i18n/SKILL.md` | 修改 | 作用域扩到 `extensions/`、新子命令、已知局限 |
| `.codebuddy.project/skills/project/project-ok-script/project-ok-script-i18n/evals/trigger_cases.json` | 修改 | 补扩展场景用例 |
| `extensions/capabilities/extension_i18n/__init__.py` | 新增 | `register` 空实现 + `on_app_ready` |
| `extensions/capabilities/extension_i18n/translator.py` | 新增 | 挂链与断言 |
| `extensions/capabilities/registry.py` | 修改 | 清单追加一行 |
| `extensions/app_globals.py` | 修改 | `__init__` 派发 `on_app_ready` |
| `extensions_tests/TestExtensionI18n.py` | 新增 | 用例 |
| `.codebuddy.project/skills/project/project-extension-capability/.../extension_capability_guard.py` | 修改 | 包结构排除 + 第 9 项 |
| `.../project-extension-capability-guard/SKILL.md` | 修改 | 检查表加一行 |
| `.codebuddy.project/rules/project/project-extension-capability/RULE.mdc` | 修改 | 底线第 6 条 |
| `.../layout.mdc` | 修改 | 翻译资源落位节 |
| `.../docs.mdc` | 修改 | 设计文档须列文案清单 |
| `.../project-extension-capability-guard.entry.mdc` | 修改 | 条款 6 |
| `.codebuddy.project/configs/skills/vcs/vcs-git-commit/project-config.md` | 修改 | H1 加 gate 步骤 |
| `.codebuddy.project/skills/project/project-ok-script/project-ok-script-release/references/release-checklist.md` | 修改 | 加 i18n 项 |

## 验收标准

- 功能：扩展新增界面文案后，按 `capabilities/<能力>/i18n/<locale>/LC_MESSAGES/ok_ext.po`
  补条目并编译，界面显示译文；不补时 gate 报缺失。
- 测试：`run_tests.ps1` 两测试根全绿，`extensions_tests` 用例数 > 0。
- 落位：未改动框架拥有的文件。`i18n/`、`src/`、`main*.py` 均不动。
- 体检：`extension_capability_guard.py audit` 与 `devflow_guard.py audit` 均 0 错误。

## 实施进度

开工后逐批更新，**只记结论不记过程**（过程进偏差记录）。

| 批次 | 状态 | 完成日期 | 备注 |
|---|---|---|---|
| 一 | 已完成 | 2026-11-03 | 脚本零依赖化；顺带修正按钮文案收集（`buttons[].text` 与元数据键混淆） |
| 二 | 已完成 | 2026-11-03 | 新增 `extension_i18n` 能力 + `on_app_ready` 派发 |
| 三 | 已完成 | 2026-11-03 | 包结构排除 i18n 子树；新增「本地化」检查项 |
| 四 | 已完成 | 2026-11-03 | 入口条款 6、RULE 底线 6、layout 翻译资源节、docs 文案清单 |
| 五 | 已完成 | 2026-11-03 | H1 第 2 步 + 发布清单 |
| 六 | 已完成 | 2026-11-03 | 9 个用例，两测试根全绿 |

## 计划外事项

实施中出现但未纳入原计划的内容，确认是否单独立项。
