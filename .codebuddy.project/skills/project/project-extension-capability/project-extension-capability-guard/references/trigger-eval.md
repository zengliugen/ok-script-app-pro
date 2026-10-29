# 触发评测：怎么跑、用例为什么这样设计

## 跑法

改 `SKILL.md` 的 `description` 或 `evals/trigger_cases.json` 后必须重跑，否则描述漂移不会被发现：

```bash
BASE=.codebuddy.project/skills/project/project-extension-capability/project-extension-capability-guard
TOOL=.codebuddy/skills/yao-meta-skill/yao-meta-skill/scripts/trigger_eval.py

python $TOOL \
  --description-file $BASE/SKILL.md \
  --cases $BASE/evals/trigger_cases.json \
  --semantic-config $BASE/evals/semantic_config.json
```

退出码 0 表示零误判；非 0 时输出的 `misfires` 会给出每条失败用例命中了哪些概念、
漏了哪些概念，按那个改 `semantic_config.json` 的短语表或权重，**不要**为了过测去删用例。

## 当前基线

| 项 | 值 |
|---|---|
| 阈值 | 0.30 |
| 正向（should_trigger） | 16 条 |
| 反向（should_not_trigger） | 10 条 |
| 近邻（near_neighbor） | 8 条 |
| Precision / Recall | 1.0 / 1.0 |

## 用例为什么这样分类

评测把请求分三档，三档的失败含义完全不同：

- **正向**：应当触发。漏了就是召回不足，用户得自己想起来用这个技能。
- **反向**：明显无关（爬虫、bug 修复、格式化、解释概念）。触发了就是纯噪音。
- **近邻**：**最容易错的一档**——字面很像、职责不同。这里放的是本项目真实存在的兄弟请求：

| 近邻请求 | 该由谁接 | 为什么不能由本技能接 |
|---|---|---|
| `extensions/` 代码放哪个子目录 | `layout.mdc` | 本技能不判落位，只检查落位结果 |
| 选继承还是 patch | `methods.mdc` | 需要读框架能力，脚本判断不了 |
| 同步上游之后要更新什么 | `project-upstream-compat` | 本技能不管上游 |
| 上游改动登记到哪里 | `project-upstream-compat-guard` 技能 | 同上 |
| 文档写完了要归档吗 | `docs.mdc` | 是查流程知识，不是跑体检 |
| ADR 可以改吗 | `docs.mdc` | 同上 |
| 能力总览表怎么加一行 | `docs.mdc` | 是问格式，不是要求执行 `new` |

这些请求都含「扩展能力」相关字面，但**诉求是查知识或改代码**，不是「生成骨架」或「体检」。
把它们算正向会逼着描述堆砌关键词，反而伤害其它用例。

## 口语化正向用例的必要性

正向里刻意保留了不带「扩展能力」字样的口语表述：

- 「我加了个新任务，需要写文档吗」
- 「看看有哪些能力没走文档流程」
- 「总览表和磁盘上的文档对得上吗」
- 「这个能力在总览表登记了吗」

真实用户在推进过程中不会说全称。这类用例守住「宁可召回、不丢触发」这条线，
也是 `semantic_config.json` 里 `scaffold` / `audit_check` 两侧都必须有对应短语的原因。

## 与 `yao.py validate` 的关系

`validate` 会跑 `validate_skill` / `lint_skill` / `governance_check` / `resource_boundary_check`
四项，**不含**触发评测。两者都要跑：前者管结构合规，后者管路由质量。

注意顺序：**先改 SKILL.md，再跑 `export_skill_ir.py`，最后跑 `validate`**。
反过来会报 `description-mismatch`——Skill IR 里存的是上一次导出时的描述快照。
