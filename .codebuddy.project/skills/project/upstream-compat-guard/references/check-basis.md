# 校验基准与配置定位

## 为什么基准是本地锚点

`divergence.yaml` 的 `synced_upstream_commit` 是唯一基准。脚本拿它与 `HEAD` 做
`git diff --name-status <anchor>..HEAD`，得出「本项目相对上次同步点改了哪些上游文件」。

不取 `upstream/master` 之类远端分支的原因：

1. **离线可用**。没有 `git fetch` 也能跑，提交前检查不必依赖网络。
2. **结果可复现**。同一次提交、同一份清单，任何时候跑出的结果一致。
3. **判定不漂移**。若拿远端分支当基准，某次 `git fetch` 拉到新提交后，
   「本地有没有未登记改动」这个判定的含义会静默改变，历史结论随之失效。

代价是：**基准落后于远端时，本技能看不出问题**。这是有意为之——
「上游自锚点以来有哪些新提交」属于同步范畴，由规则 `upstream-sync.mdc` 处理，
不属于「本地改动是否登记完整」这件事。

## 锚点有效性

脚本在比对前先执行 `git cat-file -e <anchor>^{commit}`。锚点不存在（例如被 rebase 掉了、
写错了）时以退出码 2 失败，而不是把全部条目误判成「已失效」——后者会诱导人批量删条目，
是比报错严重得多的故障。

## 配置定位

两个清单在项目侧固定位置，脚本按 `Path(__file__)` 上溯定位，**不经** `.codebuddy/project.index.json`：

| 路径 | 用途 | 谁在读 |
|---|---|---|
| `.codebuddy.project/configs/upstream/divergence.yaml` | 上游差异登记清单（锚点 + 条目） | `upstream_compat_guard.py` 校验 |

**为什么不登记进索引**：索引随引擎共享、对所有消费项目一致，只承载引擎技能所需的配置。
本技能与两个清单都是项目专有内容，登进索引会让其他项目被 `codebuddy-init` 生成用不上的骨架。

判据是「读这份配置的代码住在哪个仓库」：代码在项目侧 → 不登记索引，按相对路径定位。

## 字段定义的权威来源

本文件只解释「基准为什么这样选」。条目字段（`path` / `kind` / `reason` / `method`）
`.codebuddy.project/rules/project/upstream-compat/divergence-registry.mdc` 为唯一来源。
两处不一致时以规则为准，并修正技能文档——不要反向改规则去迁就文档。
