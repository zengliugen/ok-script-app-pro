# ok-script 任务开发（project-ok-script-tasks）

## 解决什么问题

ok-script 的任务类有一组固定的约定：元数据要在 `super().__init__` 之后设置、
配置键决定持久化字段、`config_type` 的键必须与 `default_config` 对齐、
`TriggerTask` 要显式声明 `_enabled` 与 `trigger_interval`。漏任一条都不报错，
只表现为「配置不生效」「控件不显示」「CPU 被轮询吃满」。

本技能把这些约定固化成流程，让任务类一次写对。

## 怎么用

直接说要做的事即可，例如「帮我新建一个一键日常任务」「这个功能做成后台检查」。
技能会先读项目现有基类与注册风格，再给代码。

```text
$project-ok-script-tasks 新建一个后台触发任务，每 2 秒检查一次弹窗并关闭
```

## 边界

- **只做任务骨架**：类结构、元数据、配置、注册。
- `run(self)` 里的 OCR／模板匹配／点击动作 → `project-ok-script-codegen`。
- 翻译目录 → `project-ok-script-i18n`。
- 发布 → `project-ok-script-release`。
- 不判断需求价值，只按已有设计落地。

## 文档索引

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 触发描述、工作流、必备规则、双语约定 |
| `references/task-api.md` | 生命周期、执行器语义、配置模型、注册方式、方法清单 |
| `references/templates.md` | 六类模板与提交前检查清单 |
| `evals/trigger_cases.json` | 触发用例：12 正向 / 9 反向 / 6 近邻 |
| `evals/semantic_config.json` | 语义概念与权重 |
| `reports/output-risk-profile.md` | 风险矩阵与已知局限 |
| `reports/skill-ir.json` | 平台中立的技能契约 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |
