# ok-script 技能族（ok-script）

本目录收纳与本项目领域绑定的四个技能。它们原本散在仓库的 `.agents/skills/` 下
（那套约定用 `agents/openai.yaml` 做平台适配，不参与本项目的引擎校验），
本次迁到项目侧并按引擎规范改造。

## 技能清单

| 技能 | 负责什么 | 来源（`.agents/skills/`） |
|---|---|---|
| `ok-script-tasks` | 任务类骨架：类型选择、元数据、配置声明、注册 | `ok-script-tasks` |
| `ok-script-codegen` | `run(self)` 方法体：OCR、模板匹配、坐标、等待、帧刷新 | `ok-script-codegen` |
| `ok-script-i18n` | gettext 目录：收集文案、补条目、翻译、编译 `.mo` | `ok-script-i18n` |
| `ok-script-release` | 发布编排：版本号、tag、推送授权与核验 | `deploy` |

四者的边界按**产出物**划分，不按主题划分：骨架 / 方法体 / 翻译文件 / 发布产物。
遇到跨两处的问题时，按「最终要产出哪个文件」决定走哪个技能。

## 强制入口规则

四个技能各有一条常驻入口规则（`rules/<技能名>.entry/<技能名>.entry.mdc`，
`alwaysApply: true`），只写「什么时候必须走该技能」与不可妥协的底线，执行细则仍由技能按需加载。

| 入口规则 | 拦的是什么 |
|---|---|
| `ok-script-tasks.entry` | 凭记忆照抄任务模板：基类、注册位置、配置键风格每个项目不同，产物不报错但可能永远不生效 |
| `ok-script-codegen.entry` | 编造框架 API、自建轮询循环替代 `wait_` 系列 |
| `ok-script-i18n.entry` | **把中文当 `msgid` 源串**——源串语言一旦落地，纠正要连源码、所有译文与 `.mo` 一起改 |
| `ok-script-release.entry` | 人工推算版本号（本地混着上游 tag）、绕开 `vcs-router` 自己 `git commit` |

**为什么必须有入口**：这四个技能原先只有 `SKILL.md`，只能靠技能描述里的关键词被动触发。
「写界面文案」这类动作在描述里未必命中，而它的错误后果（`msgid` 用了中文）**事后不能低成本
纠正**。入口规则常驻，才能在动手之前把人导到技能上。

扩展能力开发流程也据此加了一条导流（`extension-capability-guard` 入口规则的条款 7）：
新增界面文案走 `ok-script-i18n`、建任务类走 `ok-script-tasks`、写动作序列走
`ok-script-codegen`，并明确**禁止「先写完再补译文」**。

`msgid` 必须是英文源串这条，除了规则与技能，还有两道机械兜底：
`task_i18n_helper.py check`（`gate` 也调用它）检查全部 `msgid` 是否含中日韩字符，
`extensions_tests/TestExtensionCatalogs.py` 对仓库内 catalog 做同样的断言。

## 未迁移的技能及理由

`.agents/skills/` 下另有两个技能**没有**迁入，理由记录在此以免日后重复讨论：

| 原技能 | 处置 | 理由 |
|---|---|---|
| `use-local-venv` | 不迁 | `python-env` 技能已负责解释器四级探测（配置 → `VIRTUAL_ENV` → 项目根候选目录 → 启动器），且每级都校验目标解释器能 `import ok`。再保留一份 `.venv` 选择逻辑会形成两套口径，出现「这个技能说用 xxx，那个说用 yyy」时无从判断 |
| `initialize-ok-script-app` | 不迁 | 它解决的是「从模板新建一个仓库」的一次性问题，本项目已完成初始化；且项目侧配置骨架的生成归 `core-init` 技能，两者职责重叠 |

`sources` 目录（如仍有残留）不参与加载，仅作历史参考。

## 如何跟进来源更新

来源目录 `.agents/skills/` **保留未删**，作为版本锚点。跟进方式：

1. 重算来源 `SKILL.md` 的 SHA-256 前缀：

   ```bash
   git hash-object .agents/skills/<来源技能>/SKILL.md        # 或
   python -c "import hashlib;print(hashlib.sha256(open('.agents/skills/<来源技能>/SKILL.md','rb').read()).hexdigest()[:12])"
   ```

2. 与该技能 `CHANGELOG.md` 的「来源版本」表比对。
3. 不一致即来源已变，**逐节比对后并入本地**，再更新该表。
4. **不要用来源文件直接覆盖本地**——中文正文、`references/` 拆分、退出码约定都是本地改造，
   覆盖会丢失，且不会报错。

`ok-script-release` 是唯一做了**实质性改造**的技能：来源里的 `git commit` 步骤
与本项目的提交强制链路冲突，已改为交 `vcs-router`。来源更新时只比对**版本号规则**，
提交流程的差异属本地必要改造，不要随来源回退。

## 加载与校验

- 加载：`.codebuddy/skills/project` 是指向本目录的链接，CodeBuddy 扫描到后自动加载，
  新开会话生效。
- 校验：`python .codebuddy/scripts/check_manifest.py` 的校验项 10 会检查项目侧组件
  的 `project-` 前缀、入口目录形态与 `SKILL.md` 的 `name` 一致性。
- 四个技能均**不建常驻入口规则**：它们的唤起靠 `SKILL.md` 的 `description` 与
  `agents/interface.yaml` 的 `activation.triggers`，不常驻注入。
