# 触发评测：怎么跑、用例为什么这样设计

## 跑法

改 `SKILL.md` 的 `description` 后必须重跑，否则描述漂移不会被发现：

```bash
python .codebuddy/skills/yao-meta-skill/yao-meta-skill/scripts/trigger_eval.py \
  --description-file .codebuddy.project/skills/project/upstream-compat-guard/SKILL.md \
  --cases .codebuddy.project/skills/project/upstream-compat-guard/evals/trigger_cases.json \
  --semantic-config .codebuddy.project/skills/project/upstream-compat-guard/evals/semantic_config.json
```

退出码 0 表示零误判；非 0 时输出的 `misfires` 会给出每条失败用例命中了哪些概念、
漏了哪些概念，按那个改 `semantic_config.json` 的短语表或权重，**不要**为了过测去删用例。

## 当前基线

| 项 | 值 |
|---|---|
| 阈值 | 0.30 |
| 正向（should_trigger） | 15 条，全过 |
| 反向（should_not_trigger） | 6 条，全过 |
| 近邻（near_neighbor） | 9 条，全过 |
| Precision / Recall | 1.0 / 1.0 |

## 用例为什么这样分类

评测把请求分三档，三档的失败含义完全不同：

- **正向**：应当触发。漏了就是召回不足，用户得自己想起来用这个技能。
- **反向**：明显无关（写 CSV、翻译 README、总结纪要）。触发了就是纯噪音。
- **近邻**：**最容易错的一档**——字面很像、职责不同。这里放的是本项目真实存在的兄弟请求：

| 近邻请求 | 该由谁接 | 为什么不能由本技能接 |
|---|---|---|
| `git merge upstream/master` | `upstream-sync.mdc` 流程 | 本技能明确不做同步 |
| `git fetch upstream` | 同上 | 同上 |
| `run_tests.ps1` 验行为 | 测试流程 | 本技能只保证「登记完整」 |
| 「reason 你帮我编一句」 | 人写 | 自动推断的原因是负资产 |
| `解释 divergence.yaml 的 kind 取值` | 规则文档 | 是查知识，不是跑校验 |
| `评估 bootstrap.py 的 patch 合不合适` | 扩展评审 | 需要读 diff 判断，非机械比对 |
| 「帮我判断一下归属」 | 歧义请求 | **未提及差异 / 登记**，无法判定用户要什么 |

最后一条值得单独说：它字面上像本技能的职责（归属判定确实是本技能的核心），但一句话里
没有任何「差异」「登记」「divergence」信号。这类请求**宁可漏触发**——模型在上下文里
看到完整对话时自然会判断，而语义评测里把它算正向会逼着描述堆砌关键词，反而伤害其它用例。
它的正确定位是**近邻（不应触发）**，用来守住「不因沾边就触发」这条线。

## 归属相关用例

新增的 4 条正向专门覆盖这次的误判场景：

- 「我改了 `.codebuddy` 里的引擎技能，这算不算上游差异要登记？」
- 「引擎仓库的改动需要在 divergence.yaml 里补条目吗？」
- 「`.codebuddy.project` 下的改动要不要登记到上游差异清单？」
- 「刚才改的是引擎还是上游？」

它们同时含 `upstream_diff`（上游差异）与 `ownership_check`（算不算上游 / 归属）两类概念，
既能过阈值，也能在描述淡化归属说明时立刻掉下来——这是一条**回归保护**：
若有人把 SKILL.md 里「先确认归属」那节删掉，这 4 条会先失败。

## 与 `yao.py validate` 的关系

`validate` 会跑 `validate_skill` / `lint_skill` / `governance_check` / `resource_boundary_check`
四项，**不含**触发评测。两者都要跑：前者管结构合规，后者管路由质量。

注意顺序：**先改 SKILL.md，再跑 `export_skill_ir.py`，最后跑 `validate`**。
反过来会报 `description-mismatch`——Skill IR 里存的是上一次导出时的描述快照。
