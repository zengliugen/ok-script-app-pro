# 同步方向不变量

本文件展开 `scripts/sync_guard.py` 判定的四项不变量：判据、为什么它成立、违反后会怎样。

## 零、upstream 必须隔离 tag

**判据**（三条同时满足）：

| 配置 | 期望值 |
|---|---|
| `remote.upstream.tagOpt` | `--no-tags` |
| `remote.upstream.fetch` | 含负向规则 `^refs/tags/*` |
| `push.followTags` | `false` |

**为什么**：本仓库 fork 自上游，上游用 `v0.0.1` ~ `v1.1.3` 这套号段，与本项目自有 tag
重名。上游 tag 进入本地后有两个后果，且都不是「报个错就算了」：

1. **发布号被带偏**。`next_tag.py` 读本地 tag 判断「最新稳定版」，上游 `v1.1.3` 会被
   当成最新稳定版，预发布号随之跳到 `v1.1.4-alpha.1` 而非 `v0.0.2-alpha.1`。
2. **fetch 直接失败**。同名 tag 指向不同提交时，Git 报
   `! [rejected] v0.0.1 -> v0.0.1 (would clobber existing tag)`。

**为什么 `tagOpt` 一条不够**：Git 的优先级是**命令行 > 配置**。`tagOpt=--no-tags`
只是默认值，命令里出现 `--tags` 就被覆盖。只有负向 refspec `^refs/tags/*` 位于 refspec 层，
优先级更高，显式 `--tags` 也绕不过。

实测（2026-11-05，上游 46 个 tag）：

| 命令 | 仅 `tagOpt` | 加负向 refspec |
|---|---|---|
| `git fetch upstream` | 46 | **0** |
| `git fetch upstream --tags` | 46 | **0** |
| `git fetch --tags upstream` | 46 | **0** |
| `git fetch --all` | 46 | **0** |
| `git remote update` | 46 | **0** |

**最常见的污染源是 Git GUI**：Fork / SourceTree 等的「Fetch all tags」开关直接翻译成
命令行 `--tags`，定期自动执行（Fork 实测每 11 分钟一次）。Git 配置拦不住它，
必须在 GUI 里另行关闭。

**违反后**：不阻断同步本身（分支照样能拉），但会污染版本号与 git 操作。修复命令见
`SKILL.md` 的「前置：upstream 必须隔离 tag」节。本项只在本地 `.git/config` 生效，
**新机器或重新 clone 后必须重配**。

## 一、master 必须是上游镜像

**判据**：`master` 是 `upstream/master` 的祖先，或两者指向同一提交。

**等价表述**：`git rev-list --count upstream/master..master` 必须为 `0`。

**为什么**：master 的语义是「上游某个版本的干净副本」。一旦它含项目提交，
就看不出哪些提交来自上游、哪些是自己的；下次上游更新也无法快进，只能生成 merge 提交，
两种来源永久混在一起。

**违反后**：快进 `git merge --ff-only upstream/master` 会失败。此时必须先摘掉 master 上的
项目提交（`git branch -f master <upstream sha>`），把内容转移到 dev——这正是本次事故的修复方式。

**注意**：`git merge dev` 在 master 上是**合法命令且不报错**，Git 没有任何机制阻止它。
所以这项检查只能靠脚本主动执行，不能指望 Git 兜底。

## 二、dev 必须包含 master

**判据**：`master` 是 `dev` 的祖先。

**等价表述**：`git rev-list --count dev..master` 必须为 `0`。

**为什么**：同步的终点是 dev。master 前移只完成了「取到上游」，必须再合并进 dev，
项目才真正用上新版本。只前移 master 而不合进 dev，等于同步只做了一半，而下一次同步
会因为基线错位而更难判断。

**违反后**：dev 缺少上游的新提交，可能表现为「上游已修的 bug 仍在」或「依赖版本对不上」。
处置是 `git checkout dev && git merge master`。

## 三、同步基准必须指向当前上游

**判据**：`divergence.yaml` 的 `synced_upstream_commit` 等于 `upstream/master` 的完整 sha。

**为什么**：这个基准是 `upstream-compat-guard` 唯一的比对锚点（离线可用、
结果可复现）。锚点不更新，下游校验会拿旧版本比对——本该报「未登记」的改动被当成
「与基线一致」而漏报，登记清单的可信度随即失效。

**违反后**：差异盘点结果整体失真。处置是把它改成本次合并的上游 sha，并把 `updated_at`
改为同步日期。

## 检查时机

| 时机 | 命令 | 该通过的不变量 |
|---|---|---|
| 同步开始前 | `check --stage before` | 零、一、二（tag 隔离、master 纯净、dev 未落后） |
| 同步完成后、提交前 | `check --stage after` | 零、一、二、三 |

同步前查「二」看似多余（dev 本来就该包含 master），实际是为了拦住「上次同步只做了一半"
的状态——那种状态下继续同步会把问题叠加。

## 与 divergence 登记校验的分工

两者都读 `divergence.yaml`，但查的是不同的事：

| 校验 | 查什么 | 查不出什么 |
|---|---|---|
| `sync_guard.py` | 基准**指向**是否正确 | 条目内容是否与真实差异一致 |
| `upstream_compat_guard.py` | 条目与真实差异是否一致 | 分支关系是否搞反 |

**两项都必须跑**：基准对了但条目漏登记，或条目齐全但分支搞反，都是不完整的状态。
