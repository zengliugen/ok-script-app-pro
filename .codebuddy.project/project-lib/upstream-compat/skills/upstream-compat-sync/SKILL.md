---
name: upstream-compat-sync
description: 执行上游 ok-script-app 的同步：master 前移到 upstream/master，再把 master 合并进 dev，解决冲突、更新 divergence 基准、验证并提交。用户说"同步上游"、"更新上游"、"跟进上游版本"、"把上游合进来"、"merge upstream"、"上游有新版本了"时使用。也用于同步后校验方向是否正确（master 是上游镜像、dev 承接项目改动）。
---

# 上游同步

本项目 fork 自 `ok-oldking/ok-script-app`。本技能执行一次完整的上游同步，并把「谁往谁合」
这条方向约定变成可机械检查的断言——它是最容易搞反、且 Git 不会报错的一步。

## 前置：upstream 必须隔离 tag（新环境一次性配置）

本仓库 fork 自上游，上游用 `v0.0.1` ~ `v1.1.3` 这套号段，**与本项目自有 tag 重名**。
若上游 tag 进入本地，两个后果同时发生：

| 后果 | 表现 |
|---|---|
| 发布号被带偏 | `next_tag.py` 把上游 `v1.1.3` 认成最新稳定版，算出 `v1.1.4-*` 而非 `v0.0.2-*` |
| fetch 直接失败 | 同名 tag 指向不同提交，报 `would clobber existing tag` |

**新机器、重新 clone 时必须先跑这段配置**（它只写本地 `.git/config`，不进版本控制）：

```bash
git config remote.upstream.tagOpt --no-tags      # 关掉默认的 tag 自动跟随
git config push.followTags false                 # 禁止普通 push 顺带推 annotated tag

# 收紧 refspec：只拉分支，显式拒绝 tag
git config --unset-all remote.upstream.fetch
git config --add remote.upstream.fetch '+refs/heads/*:refs/remotes/upstream/*'
git config --add remote.upstream.fetch '^refs/tags/*'
```

### 三条缺一不可，尤其第二条

| 配置 | 作用 | 缺了会怎样 |
|---|---|---|
| `tagOpt=--no-tags` | 默认不跟随 tag | 普通 `git fetch upstream` 就会拉 tag |
| `^refs/tags/*` 负向 refspec | **唯一能挡住显式 `--tags`** | `git fetch upstream --tags` 照拉不误 |
| `push.followTags=false` | push 不顺带推 tag | 可能把上游 tag 或半成品 tag 一次推上 origin |

**为什么 `tagOpt` 不够**：Git 的优先级是**命令行 > 配置**。`tagOpt` 只是默认值，
一旦命令里出现 `--tags` 就被覆盖。负向 refspec 属于 refspec 层，优先级更高，`--tags` 也绕不过。

这不是推测——实测（2026-11-05）结果：

| 命令 | 仅 `tagOpt` | 加负向 refspec 后 |
|---|---|---|
| `git fetch upstream` | 拉回 46 个 | **0 个** |
| `git fetch upstream --tags` | 拉回 46 个 | **0 个** |
| `git fetch --tags upstream` | 拉回 46 个 | **0 个** |
| `git fetch --all` | 拉回 46 个 | **0 个** |
| `git remote update` | 拉回 46 个 | **0 个** |

**最常见的污染源是 Git GUI**（Fork / SourceTree 等）的「Fetch all tags」开关——它直接
翻译成命令行 `--tags`，每 11 分钟执行一次，Git 配置层面的 `tagOpt` 完全拦不住。
除本节的 Git 配置外，**还要在该 GUI 里关掉 fetch tags**（Fork：`FetchAllTags=false`、
`Push_PushAllTags=false`）。

第 1 步的 `sync_guard.py` 会**逐项检查这三条配置**，缺失即报问题并给出修复命令，
因此配漏不会静默失效。

## 方向约定（不可违反）

```
upstream/master  ──前移──▶  master  ──合并──▶  dev
                              （纯镜像）      （唯一开发分支）
```

| 分支 | 角色 | 允许的操作 |
|---|---|---|
| `master` | **上游镜像**，不含任何项目提交 | 只允许快进到 `upstream/master`；**禁止**任何本地提交 |
| `dev` | 唯一开发分支（`origin/HEAD` 指向它） | 承接全部项目改动；从 master 合并上游 |

**为什么方向不能反**：`git merge dev` 在 master 上完全合法、Git 不报任何错，但会让 master
失去「纯上游」语义——下次同步时无法再用快进，且无法判断哪些提交属于上游。
本项目已经出过一次这个事故（把 dev 合进 master 并推送），所以这条不是风格建议，是硬约束。

## 执行流程

全程在项目根执行。每步的失败处置见 `references/failure-handling.md`。

### 第 1 步：同步前校验

```bash
GUARD=.codebuddy.project/project-lib/upstream-compat/skills/upstream-compat-sync/scripts/sync_guard.py
python $GUARD check --stage before
```

退出码非 0 必须停下，**不要继续**。它会拦下三类前提缺失：upstream 未隔离 tag
（见上节）、master 已被污染（方向搞反过）、dev 尚未包含 master 的最新状态。
同时跑一次差异登记校验，确认没有未登记的上游改动：

```bash
python .codebuddy.project/project-lib/upstream-compat/skills/upstream-compat-guard/scripts/upstream_compat_guard.py
```

### 第 2 步：master 前移

```bash
git fetch upstream --prune
git checkout master
git merge --ff-only upstream/master
```

`--ff-only` 是关键：master 上若有任何本地提交，快进会失败并直接暴露污染，而不是生成一个
把两边混在一起的 merge 提交。

**不要**在这里加 `--tags`：上节已配置负向 refspec 把它挡住，即使加了也拉不到 upstream tag；
但显式写上会让人误以为同步需要 tag，且在没有配置隔离的环境里直接造成污染。

### 第 3 步：把 master 合并进 dev

```bash
git checkout dev
git merge master
```

出现冲突时按第 4 步处置。**不要**在 master 上做这一步。

### 第 4 步：处置冲突

冲突文件先在 `divergence.yaml` 中查有无登记条目：

| 情况 | 处置 |
|---|---|
| 有登记条目 | 预期内冲突。保留本地语义的同时吸收上游新内容，两边都要留 |
| 无登记条目 | 先停下查清本地为何改了它——是漏登记（补登记），还是上游动了我们依赖的结构（评估影响） |

逐个文件按语义合并，**禁止**用 `-X ours` / `-X theirs` 全局策略一次性吞掉一边。
每个冲突的处置理由写进提交正文。

### 第 5 步：更新同步基准

同步完成后，`divergence.yaml` 的基准必须指向本次的上游提交：

```yaml
synced_upstream_commit: <本次上游 master 的完整 sha>
updated_at: <同步日期>
```

同时删除已失效的条目（上游已自行改掉、或本地已迁移回原样的）。

### 第 6 步：验证

```bash
python $GUARD check --stage after          # 方向、dev 包含关系、基准三项
python .codebuddy.project/.../upstream_compat_guard.py   # 登记与差异一致
powershell -File run_tests.ps1             # 两个测试根
```

测试结论**不可混用**：`tests/` 通过才说明上游行为未被破坏；`extensions_tests/` 通过
只说明项目自有代码正常，与上游无关。

上游大版本升级（如 ok-script 2.x → 3.x）还需重建运行环境：

```bash
python -m pip install --index-url https://pypi.org/simple/ --no-deps --upgrade -r requirements.txt
```

预发布版本（`3.0.0b3` 这类）在国内镜像上可能返回 403，改用官方源。

### 第 7 步：提交

按 `vcs-router` → `vcs-git-commit` 流程提交到 **dev**。提交正文必须包含：上游带来的变更、
每个冲突的处置理由、项目侧随之更新的配置、验证结果。

**提交信息必须如实反映改动**——尤其不能漏掉依赖大版本升级这类最重要的信息，
也不能写「合并进 master」这种与事实相反的方向描述。

## 摘要与正文的常见错误

本次事故中实际出现过、需要引以为戒的问题：

| 错误 | 正确做法 |
|---|---|
| 写「合并进 master」「改动进入 master」 | 同步只前移 dev，master 保持纯上游 |
| 只写「同步上游至 v1.1.3」，漏掉 ok-script 3.0 升级 | 摘要点明最关键的变更，大版本升级必须写出 |
| 凭印象写上游改了什么（如虚构「校验 app.json 版本」） | 先 `git diff <旧> <新>` 核对再写 |
| 只写上游文件数，不提项目侧配置改动 | 分两块写清：上游文件 + 项目侧同步更新的配置 |

## 不适用范围（do not use / out of scope）

| 请求 | 该去哪 |
|---|---|
| 只盘点差异、校验登记完整度 | `upstream-compat-guard`（不执行同步） |
| 登记某个上游改动 | `upstream-compat-guard --add` |
| 引擎仓库 `.codebuddy/` 的更新 | `tool-engine-sync`（那是引擎子模块，不走 upstream） |
| 提交或推送 | `vcs-router` 路由 |
| 判断某个上游改动该不该改 | 人读 diff 判断，本技能不替用户决策 |

## 相关规则

`rules/project/upstream-compat/RULE.mdc` 是核心底线与路由；子规则
`upstream-sync.mdc`（merge 策略与冲突处置顺序）、`divergence-registry.mdc`（清单字段定义）
按需加载。本技能是 `upstream-sync.mdc` 的可执行化版本，两者冲突时以规则为准并回头修技能。

## 脚本与参考

| 文件 | 用途 |
|---|---|
| `scripts/sync_guard.py` | 方向不变量检查，`--stage before/after`，`--json` 机器可读 |
| `references/sync-invariants.md` | 三项不变量的判据、成立理由与违反后果 |
| `references/failure-handling.md` | 各步骤失败的处置路径（含方向已搞反后的修复） |
| `evals/trigger_cases.json` | 触发评测用例：12 条正向 / 6 条反向 / 8 条近邻 |
| `evals/semantic_config.json` | 语义评分配置，与用例配套 |

改 `description` 后必须重跑评测：

```bash
python .codebuddy/lib/yao-meta-skill/skills/yao-meta-skill/scripts/trigger_eval.py \
  --description-file .codebuddy.project/project-lib/upstream-compat/skills/upstream-compat-sync/SKILL.md \
  --cases .codebuddy.project/project-lib/upstream-compat/skills/upstream-compat-sync/evals/trigger_cases.json \
  --semantic-config .codebuddy.project/project-lib/upstream-compat/skills/upstream-compat-sync/evals/semantic_config.json \
  --threshold 0.3
```
