# 同步方向不变量

本文件展开 `scripts/sync_guard.py` 判定的三项不变量：判据、为什么它成立、违反后会怎样。

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

**为什么**：这个基准是 `project-upstream-compat-guard` 唯一的比对锚点（离线可用、
结果可复现）。锚点不更新，下游校验会拿旧版本比对——本该报「未登记」的改动被当成
「与基线一致」而漏报，登记清单的可信度随即失效。

**违反后**：差异盘点结果整体失真。处置是把它改成本次合并的上游 sha，并把 `updated_at`
改为同步日期。

## 检查时机

| 时机 | 命令 | 该通过的不变量 |
|---|---|---|
| 同步开始前 | `check --stage before` | 一、二（master 纯净且 dev 未落后） |
| 同步完成后、提交前 | `check --stage after` | 一、二、三 |

同步前查「二」看似多余（dev 本来就该包含 master），实际是为了拦住「上次同步只做了一半"
的状态——那种状态下继续同步会把问题叠加。

## 与 divergence 登记校验的分工

两者都读 `divergence.yaml`，但查的是不同的事：

| 校验 | 查什么 | 查不出什么 |
|---|---|---|
| `sync_guard.py` | 基准**指向**是否正确 | 条目内容是否与真实差异一致 |
| `upstream_compat_guard.py` | 条目与真实差异是否一致 | 分支关系是否搞反 |

**两项都必须跑**：基准对了但条目漏登记，或条目齐全但分支搞反，都是不完整的状态。
