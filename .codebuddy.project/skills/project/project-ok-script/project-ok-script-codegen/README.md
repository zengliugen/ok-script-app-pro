# ok-script 代码生成（project-ok-script-codegen）

## 解决什么问题

写 `run(self)` 时最容易踩的坑不是语法，而是**帧的语义**：`self.frame` 是一帧快照，
点击后不重新取帧就会一直检测同一张旧图，表现为「明明在界面上却点不到」。
这类问题不报错、不抛异常，只让任务看起来「偶尔不灵」。

本技能把帧刷新规则、`wait_` 与 `find_` 的取舍、相对坐标与模板占位的写法固化成规则，
并强制逐行中文注释。

## 怎么用

```text
$project-ok-script-codegen 识别到「确认」按钮就点它，点完等 0.5 秒再继续
$project-ok-script-codegen 这张截图里的开始按钮，帮我写点击步骤
```

只输出 `run(self)` 方法体。任务类骨架还没建时，先用 `project-ok-script-tasks`。

## 边界

- **只生成 `run(self)` 内的动作代码**，不生成类结构、元数据、注册行。
- 不编造 API，不编造模板名；模板未给时用 `*_template` 占位并注明需手动标注。
- 不假装能验证真实匹配效果——阈值与区域需实际运行调。
- 生成结果的语义审查走 Python 审查链路，不在本技能内。

## 文档索引

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 触发描述、生成规则、逐行注释要求、帧刷新与 OCR/模板规则、自检清单 |
| `references/frame-and-wait.md` | 帧生命周期、清帧动作对照表、循环正确写法、失效现象对照 |
| `evals/trigger_cases.json` | 触发用例：12 正向 / 9 反向 / 6 近邻 |
| `evals/semantic_config.json` | 语义概念与权重 |
| `reports/output-risk-profile.md` | 风险矩阵与已知局限 |
| `reports/skill-ir.json` | 平台中立的技能契约 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |
