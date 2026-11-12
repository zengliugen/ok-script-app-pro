# ok-script 应用初始化（ok-script-init-app）

## 解决什么问题

从 ok-script-app 模板克隆下来的仓库，到处是模板自己的标识：窗口标题叫 `ok-script-app`，
关于页链到模板作者的仓库，安装包叫 `ok-script-app-win32-*.exe`，`src/config.py` 里的运行目标
是示例值。这些**都不会报错**——界面照常打开，只是用户会看到别人的名字和链接。

本技能把这些一次性改造固化成流程：先收集需求，再按固定顺序落地，避免漏改。

## 怎么用

直接把游戏信息告诉它即可：

```text
$ok-script-init-app 初始化这个项目，游戏叫 XXX，Windows 原生，exe 是 Game.exe，窗口类名 GameWindow
```

信息不全时会先问一轮，可选项可以回答「不知道」，此时保留模板默认值、运行期再探测，不会编造。

## 边界

- **只管应用业务层**：应用名、运行目标、图标、更新仓库、打包、首个任务、文档站。
- 引擎侧 `.codebuddy.project/` 配置骨架 → `core-init`。
- 任务类骨架与配置声明 → `ok-script-tasks`。
- `run(self)` 动作序列 → `ok-script-codegen`。
- 翻译目录 → `ok-script-i18n`。
- 发布 → `ok-script-deploy`。

## 与 core-init 的分工

两者都叫「初始化」，对象完全不同：

| 技能 | 改什么 | 产出 |
|---|---|---|
| `core-init` | 工作流引擎那一层 | `.codebuddy.project/` 下的 `configs/`、`caches/`、`store/`、`.gitignore`、桥接链接 |
| 本技能 | 应用业务那一层 | `src/config.py`、`icons/`、`pyappify.yml`、workflows、`src/tasks/`、`docs/` |

一个项目通常两者都要做，顺序上先 `core-init` 接上引擎，再用本技能填业务配置。

## 文档索引

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 需求收集清单、七步落地顺序、模板资源处理、验证要求 |
| `references/checklist.md` | 逐项核对清单：漏改后果、两种更新仓库策略的取舍、ok_templates 入库校验命令 |
| `evals/trigger_cases.json` | 触发用例 |
| `evals/semantic_config.json` | 语义概念与权重 |
| `reports/output-risk-profile.md` | 风险矩阵与已知局限 |
| `reports/skill-ir.json` | 平台中立的技能契约 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |
