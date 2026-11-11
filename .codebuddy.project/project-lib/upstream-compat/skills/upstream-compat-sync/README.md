# 上游同步

执行 `ok-oldking/ok-script-app` 的同步：`master` 前移 → 合并进 `dev` → 解冲突 → 更新基准 →
验证 → 提交。附带一个确定性检查脚本，把「谁往谁合」这条最容易搞反、且 Git 不会报错的方向
约定变成可机械拦截的断言。

## 解决什么问题

本项目 fork 自上游，约定 `master` 保持为上游镜像、开发始终在 `dev` 进行、有更新时从 `master`
同步到 `dev`。但 `git merge dev` 在 `master` 上完全合法、不报任何错，一旦方向搞反，
`master` 就永久失去「纯上游」语义，事后只能靠人翻日志才发现。本技能把这条约定固化成
`sync_guard.py` 的四项不变量检查，在动手前和提交前各拦一次。

第四项是 **upstream tag 隔离**：上游与本项目共用 `v*` 号段（上游 `v0.0.1` ~ `v1.1.3`），
上游 tag 一旦进入本地，发布号会被带偏、同名 tag 还会让 fetch 报 `would clobber`。
该项目在本地 `.git/config` 中，新机器或重新 clone 后必须重配，因此纳入机械检查。

## 怎么用

```bash
S=.codebuddy.project/project-lib/upstream-compat/skills/upstream-compat-sync/scripts/sync_guard.py

python $S check --stage before    # 同步前：tag 隔离、master 是否纯净、dev 是否落后
python $S check --stage after     # 同步后：再加基准是否已更新
python $S check --json            # 机器可读输出
```

**新环境先配 tag 隔离**（详见 `SKILL.md` 的「前置：upstream 必须隔离 tag」）：

```bash
git config remote.upstream.tagOpt --no-tags
git config push.followTags false
git config --unset-all remote.upstream.fetch
git config --add remote.upstream.fetch '+refs/heads/*:refs/remotes/upstream/*'
git config --add remote.upstream.fetch '^refs/tags/*'
```

退出码：`0` 通过 / `1` 违反不变量 / `3` 环境或配置不可用。

完整执行流程见 `SKILL.md`；四项不变量的判据与理由见 `references/sync-invariants.md`；
各步骤失败（含方向已搞反后的修复）见 `references/failure-handling.md`。

## 与相邻技能的分工

| 技能 | 管什么 |
|---|---|
| **本技能** | **执行**同步：改分支、解冲突、更新基准 |
| `upstream-compat-guard` | **盘点**差异、校验登记完整性，不改分支 |

两者都读 `divergence.yaml` 但查的是不同的事：本技能查「基准指向对不对」，
另一个查「条目与真实差异一致不一致」。**两项都要跑**。

## 触发评测

`evals/` 下 12 条正向 / 6 条反向 / 8 条近邻，阈值 0.30，当前 FP=0 / FN=0。
改 `description` 后必须重跑：

```bash
python .codebuddy/lib/yao-meta-skill/skills/yao-meta-skill/scripts/trigger_eval.py \
  --description-file <本技能>/SKILL.md \
  --cases <本技能>/evals/trigger_cases.json \
  --semantic-config <本技能>/evals/semantic_config.json --threshold 0.3
```

## 来历

本技能固化的流程来自一次真实的上游同步（ok-script 2.0.0b7 → 3.0.0b3）。那次执行中出现过
方向搞反、提交消息写错等问题，`SKILL.md` 的「摘要与正文的常见错误」与
`reports/output-risk-profile.md` 的风险矩阵都直接来自这些实际教训，不是推测。
