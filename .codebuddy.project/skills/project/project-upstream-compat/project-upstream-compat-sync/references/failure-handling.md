# 失败处置

各步骤失败的处置路径。原则：**先保住判断依据，再动历史**——拿不准时先建备份分支。

## 第 1 步失败：同步前校验不通过

### master 已被污染（方向搞反过）

症状：`sync_guard.py check --stage before` 报「master 不是 upstream/master 的祖先」。

修复（不丢任何工作）：

```bash
# 0. 先备份，确认修复前状态可回退
git branch backup/master-before-fix master

# 1. 把当前 dev 缺失的内容补进去：若污染内容已在 dev，可跳过
#    典型做法是让 dev 快进到被污染的 master（dev 是它的祖先时可行）
git checkout dev && git merge --ff-only master

# 2. master 摘回纯上游
git branch -f master <upstream/master 的 sha>

# 3. 推送（master 回退需 force，dev 是正常推送）
git push origin dev
git push --force-with-lease=master:<被污染时推送的 sha> origin master
```

**为什么这样修**：被污染的 master 内容如果正是 dev 应有的结果（上游 + 全部项目改动），
让 dev 快进过去即可完整保留，无需重做合并。

### dev 未包含 master

症状：报「dev 未包含 master」。

这通常是上次同步只做了一半。直接 `git checkout dev && git merge master` 即可，
冲突处置同第 4 步。

### 差异登记校验不通过

报「未登记」先补登记（`upstream_compat_guard.py --add <路径>`），报「已失效」先删条目。
**未登记项不补齐不要开始同步**——否则同步后无法区分「新冲突」与「既有未登记改动」。

## 第 2 步失败：快进被拒

症状：`git merge --ff-only upstream/master` 报 `Not possible to fast-forward`。

含义就是 master 有本地提交。回到「master 已被污染」的修复路径。**不要**改用
`git merge upstream/master`（去掉 `--ff-only`）绕过——那会生成一个把上游和项目提交
混在一起的 merge，方向问题被永久埋进历史。

## 第 3 步失败：合并冲突

按 SKILL.md 第 4 步处置。补充要点：

- 冲突文件多时先看 `git diff --stat <上游 sha> HEAD` 判断改动面，再逐文件处理
- 合并进行中想退出：`git merge --abort`；已暂存部分冲突时 `git checkout -m -- <文件>`
  可恢复冲突标记重新解
- **禁止**为了「快速通过」用 `-X ours` 或整文件保留本地版——会静默丢掉上游改进

## 第 5 步：基准更新后校验仍失败

若 `check --stage after` 只报基准问题，检查 `synced_upstream_commit` 的 sha 是否完整
（40 位）。截断的 sha 无法与 `rev-parse` 结果匹配。

## 第 6 步失败：测试不通过

按测试根分流判断，**结论不可互换**：

| 失败的是 | 含义 | 处置 |
|---|---|---|
| `tests/` | 上游行为被本地改动破坏 | 必修。检查冲突处置是否保留了上游语义 |
| `extensions_tests/` | 项目自有代码需适配 | 必适配。与上游兼容性无关，但同样要修 |

**上游大版本升级时**，`tests/` 报 `ModuleNotFoundError` 通常是运行环境没更新，
而不是代码问题。先确认 venv 里的框架版本与 `requirements.txt` 一致：

```bash
python -m pip show ok-script | grep Version
grep '^ok-script' requirements.txt
```

两者不一致就先重装依赖再重跑测试，**不要**据此改代码。

## 第 7 步：提交后发现信息写错

提交信息的事实错误（方向写反、虚构上游改动、漏掉关键变更）必须修正，不能将就：

```bash
git commit --amend -F <修正后的消息文件>
git push --force-with-lease=dev:<原提交 sha> origin dev
```

`--amend` 只换消息、树哈希不变，内容零风险；`--force-with-lease` 带上期望的原 sha，
远程若已被他人更新则推送失败而不是覆盖。

**写正文前先核对**：

```bash
git diff --name-status <旧上游 sha> <新上游 sha>   # 上游到底改了什么
git diff --name-status <dev 上一提交> HEAD          # 本提交相对 dev 改了什么
```

## 分支状态自检（任何时候可用）

```bash
git log --oneline --graph --all -10      # 看拓扑，确认 master 是 dev 的祖先
git rev-list --count upstream/master..master   # master 应恒为 0
git branch -vv                            # 确认跟踪关系与领先/落后数
```
