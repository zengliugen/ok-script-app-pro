# ok-script 技能族（ok-script）

本目录收纳与本项目领域绑定的六个技能。它们原本都在仓库的 `.agents/skills/` 下
（那套约定用 `agents/openai.yaml` 做平台适配，不参与本项目的引擎校验），
现已全部迁到项目侧并按引擎规范改造；`.agents/` 保持与上游一致，只作为版本锚点。

## 技能清单

| 技能 | 负责什么 | 来源（`.agents/skills/`） |
|---|---|---|
| `ok-script-init-app` | 模板仓库初始化：应用标识、运行目标、图标、更新仓库、首个任务、文档站 | `initialize-ok-script-app` |
| `ok-script-venv` | 本地解释器约定：跑 Python 命令该用哪个解释器、怎么写 | `use-local-venv` |
| `ok-script-tasks` | 任务类骨架：类型选择、元数据、配置声明、注册 | `ok-script-tasks` |
| `ok-script-codegen` | `run(self)` 方法体：OCR、模板匹配、坐标、等待、帧刷新 | `ok-script-codegen` |
| `ok-script-i18n` | gettext 目录：收集文案、补条目、翻译、编译 `.mo` | `ok-script-i18n` |
| `ok-script-deploy` | 发布编排：版本号、tag、推送授权与核验 | `deploy` |

六者的边界按**产出物**划分，不按主题划分：应用配置 / 解释器命令 / 骨架 / 方法体 / 翻译文件 / 发布产物。
遇到跨两处的问题时，按「最终要产出哪个文件」决定走哪个技能。

## 强制入口规则

四个产出型技能各有一条常驻入口规则（`rules/<技能名>.entry/<技能名>.entry.mdc`，
`alwaysApply: true`），只写「什么时候必须走该技能」与不可妥协的底线，执行细则仍由技能按需加载。

| 入口规则 | 拦的是什么 |
|---|---|
| `ok-script-tasks.entry` | 凭记忆照抄任务模板：基类、注册位置、配置键风格每个项目不同，产物不报错但可能永远不生效 |
| `ok-script-codegen.entry` | 编造框架 API、自建轮询循环替代 `wait_` 系列 |
| `ok-script-i18n.entry` | **把中文当 `msgid` 源串**——源串语言一旦落地，纠正要连源码、所有译文与 `.mo` 一起改 |
| `ok-script-deploy.entry` | 人工推算版本号（本地混着上游 tag）、绕开 `vcs-router` 自己 `git commit` |

`ok-script-init-app` 与 `ok-script-venv` **不建入口规则**：前者是一次性问题（仓库初始化完就不再触发），
后者的错误后果是「命令用错解释器」，报错立刻可见、容易纠正，不需要在动手前拦。

**为什么其余四个必须有入口**：它们原先只有 `SKILL.md`，只能靠技能描述里的关键词被动触发。
「写界面文案」这类动作在描述里未必命中，而它的错误后果（`msgid` 用了中文）**事后不能低成本
纠正**。入口规则常驻，才能在动手之前把人导到技能上。

扩展能力开发流程也据此加了一条导流（`extension-capability-guard` 入口规则的条款 7）：
新增界面文案走 `ok-script-i18n`、建任务类走 `ok-script-tasks`、写动作序列走
`ok-script-codegen`，并明确**禁止「先写完再补译文」**。

`msgid` 必须是英文源串这条，除了规则与技能，还有两道机械兜底：
`task_i18n_helper.py check`（`gate` 也调用它）检查全部 `msgid` 是否含中日韩字符，
`extensions_tests/TestExtensionCatalogs.py` 对仓库内 catalog 做同样的断言。

## 与引擎侧同名关注点的分工

迁入的 `ok-script-init-app` 与 `ok-script-venv` 各自有一个引擎侧的邻居，看似重叠，
实际层次不同，**不合并**：

| 关注点 | 引擎侧（机制） | 本包（约定/业务） | 判据 |
|---|---|---|---|
| 初始化 | `core-init`：依据索引生成 `.codebuddy.project/` 骨架（`configs/`、`caches/`、`store/`、桥接链接） | `ok-script-init-app`：改写应用业务层（`src/config.py`、`icons/`、`pyappify.yml`、workflows、`src/tasks/`、`docs/`） | 改的是引擎配置还是应用配置 |
| 解释器 | `python-env`：四级探测（配置 → `VIRTUAL_ENV` → 项目根候选目录 → 启动器），每级校验 `import ok`；管理工具环境 | `ok-script-venv`：两级判断（本地 `.venv` → 全局 `python`），只管命令怎么写 | 手敲命令 → 本包；写进脚本/CI、跑测试与覆盖率 → 引擎侧 |

把「初始化」与「解释器」各写两处看起来像重复，但合并会带来确定的损失：
`core-init` 不知道 ok-script 应用的 `gui_title`、`pyappify.yml` 长什么样；
`python-env` 的四级探测带 `import ok` 校验，用于日常命令会明显拖慢。
两组职责的输入、输出与失败后果都不同，因此分置。

## 如何跟进来源更新

`.agents/skills/` **保持与上游一致**，不承载本地改造，只作为版本锚点。跟进方式：

1. 重算来源 `SKILL.md` 的 SHA-256 前缀：

   ```bash
   git hash-object .agents/skills/<来源技能>/SKILL.md        # 或
   python -c "import hashlib;print(hashlib.sha256(open('.agents/skills/<来源技能>/SKILL.md','rb').read()).hexdigest()[:12])"
   ```

2. 与该技能 `CHANGELOG.md` 的「来源版本」表比对。
3. 不一致即来源已变，**逐节比对后并入本地**，再更新该表。
4. **不要用来源文件直接覆盖本地**——中文正文、`references/` 拆分、退出码约定、
   与引擎侧邻居的分工说明都是本地改造，覆盖会丢失，且不会报错。

`ok-script-deploy` 是唯一做了**实质性改造**的技能：来源里的 `git commit` 步骤
与本项目的提交强制链路冲突，已改为交 `vcs-router`。来源更新时只比对**版本号规则**，
提交流程的差异属本地必要改造，不要随来源回退。

`ok-script-init-app` 与上下文的另一处差异是**新增了模板资源章节**：来源从不提 `ok_templates`
的版本控制问题，而下游应用必须让它入库才能协作，入库后又必须配发布过滤。这一节属本地补足，
来源更新时不要按来源删掉。

## 加载与校验

- 加载：`.codebuddy/skills/project` 是指向本目录的链接，CodeBuddy 扫描到后自动加载，
  新开会话生效。
- 校验：`python .codebuddy/scripts/check_manifest.py` 的校验项 10 会检查项目侧组件
  的命名前缀、入口目录形态与 `SKILL.md` 的 `name` 一致性。
- 六个技能均**不建常驻入口规则之外的注入**：唤起靠 `SKILL.md` 的 `description` 与
  `agents/interface.yaml` 的 `activation.triggers`。

