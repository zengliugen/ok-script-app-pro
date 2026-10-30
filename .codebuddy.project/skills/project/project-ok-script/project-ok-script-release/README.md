# ok-script 版本发布（project-ok-script-release）

## 解决什么问题

发布要一次做对四件事：**版本号算对、提交走对链路、tag 打对、推送前拿到授权**。
任一件做错都不会中途报错，而是事后才发现——版本号撞了、tag 指向另一份代码、
或者根本没推上去导致 CI 从未启动。

本技能只做「版本号」与「顺序」两件事，提交交给已有的 `vcs-router` 链路，
避免出现第二套提交流程。

## 怎么用

```text
$project-ok-script-release 发布一个新版本
$project-ok-script-release deploy beta
$project-ok-script-release 下一个版本号是多少
```

算版本号也可以单独跑：

```powershell
.\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-release\scripts\next_tag.py beta --remote origin
```

## 边界

| 动作 | 谁做 |
|---|---|
| 确认范围、跑验证、算版本号、打 tag、核验 | 本技能 |
| `git commit` | `vcs-router` 链路 |
| `git push` | 本技能，**须用户显式授权**（`push_initiation` 默认 `user_only`） |
| 改代码 / 补测试 | 不走本技能 |

不删 tag、不移动 tag、不强推。版本号规则里最容易被忽略的一条：
**稳定版发布后，同基线的预发布线即关闭**，新预发布从下一个 patch 起算。

## 文档索引

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 定位与边界、版本规则、工作流、硬约束、不适用范围 |
| `references/release-checklist.md` | 发布前检查清单、tag 与 CI 的触发关系、三条号段关系、失败处置 |
| `scripts/next_tag.py` | 版本号计算（`release` / `beta` / `alpha`，支持 `--remote` 纳入远端 tag） |
| `security/permission_policy.json` | 允许与禁止的 git 子命令清单 |
| `evals/trigger_cases.json` | 触发用例：12 正向 / 8 反向 / 6 近邻 |
| `evals/semantic_config.json` | 语义概念与权重 |
| `reports/output-risk-profile.md` | 风险矩阵与已知局限 |
| `reports/skill-ir.json` | 平台中立的技能契约 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |
