# ok-script 翻译维护（project-ok-script-i18n）

## 解决什么问题

ok-script 运行时读 `.mo`，不读 `.po`。改完 `.po` 不编译，界面照旧显示源串，
**不会报任何错**——排查方向很容易被引向「代码没调 `tr()`」这类错误假设。

第二个坑是 `msgid` 必须与代码里的源串逐字符一致：差一个空格、一个全角半角标点，
该条目就静默失效。

本技能把「收集文案 → 补条目 → 翻译 → 编译 → 校验」走成一条完整链路，
并用脚本兜住重复 `msgid` 与缺失依赖。

## 怎么用

```powershell
# 看某个任务有哪些界面可见文案
.\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-i18n\scripts\task_i18n_helper.py scan --task src\tasks\MyOneTimeTask.py

# 检查各语言目录的重复 msgid
.\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-i18n\scripts\task_i18n_helper.py check --i18n i18n

# 编译全部 .po 为 .mo
.\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-i18n\scripts\task_i18n_helper.py compile --i18n i18n
```

也可以直接描述需求：「给新任务加翻译」「翻译改了但界面没变」「怎么新增一种语言」。

## 边界

- **只动 `i18n/`**：不改任务类的文案与配置键，不新增语言目录除非用户要求。
- 纯日志串默认不翻译。
- 任务文案本身由 `project-ok-script-tasks` 写出来。
- 缺 `polib` 时 `compile` / `check` 以退出码 3 报错并给安装命令，**不静默跳过**。

## 文档索引

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 触发描述、工作流、目录规则、翻译口径、与任务开发的衔接 |
| `references/catalog-rules.md` | 运行时读什么、msgid 精确匹配成因、编译的必要性、polib 依赖、失效对照表 |
| `scripts/task_i18n_helper.py` | `scan` / `check` / `compile` 三个子命令 |
| `evals/trigger_cases.json` | 触发用例：12 正向 / 8 反向 / 6 近邻 |
| `evals/semantic_config.json` | 语义概念与权重 |
| `reports/output-risk-profile.md` | 风险矩阵与已知局限 |
| `reports/skill-ir.json` | 平台中立的技能契约 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |
