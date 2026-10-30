---
name: project-ok-script-release
description: 编排 ok-script 项目的版本发布：确认待发布改动、跑发布前验证、计算下一个版本 tag、交提交与推送给版本控制链路、创建带注释的 tag 并核验推送结果。用户说"发布版本"、"deploy"、"打个 tag"、"发 beta"、"发 alpha"、"出个新版本"、"release 一下"、"下一个版本号是多少"时使用。本技能只做编排，不自己执行 git commit 与 git push（那两条必须走 vcs-router 链路），也不负责改代码或写测试。
---

# ok-script 版本发布编排

## 定位与边界（先读这一节）

发布是一次**跨多个动作的编排**，不是一个新的提交通道。仓库里已有一条强制链路管提交与推送：

| 动作 | 由谁执行 | 原因 |
|---|---|---|
| 确认改动范围、跑验证 | 本技能 | 发布前的独有检查 |
| 计算下一个版本号 | 本技能 | 版本规则只在这里维护 |
| `git commit` | **`vcs-router` 链路** | 所有提交必须经该链路（含摘要格式、安全检查、互斥锁） |
| 创建带注释 tag | 本技能 | `vcs-router` 明确不支持标签操作，标签不在其职责内 |
| `git push`（提交与 tag） | 本技能，**须用户显式授权** | `vcs-router` 的路由范围是 commit / merge，推送与标签不在其内；不推 tag 就不构成发布 |

**本技能不执行也不替代 `git commit`**。它负责「版本号」与「发布顺序」，
提交交给已有链路——提交摘要格式、敏感信息检查、暂存区校验、VCS 互斥锁全部在链路里，
另写一份必然分叉，而分叉不会报错。

**推送为什么要自己做**：`vcs-router` 的路由表只有 commit 与 merge，标签操作明确排除在外，
推送也不在其内。发布必须把 tag 送上去才算完成，所以本技能承担这一步，
但**发起权仍受项目配置约束**（`push_initiation` 默认 `user_only`）——没有用户显式指令就不推。

## 版本规则

| 命令 | 产出 | 示例 |
|---|---|---|
| `发布` 或 `发布正式版` | 稳定版 tag | `v3.3.54` |
| `发布 beta` | beta 预发布 tag | `v3.3.55-beta.1` |
| `发布 alpha` | alpha 预发布 tag | `v3.3.55-alpha.1` |

稳定版在最新稳定 `vMAJOR.MINOR.PATCH` 上把 patch 加一。alpha 与 beta **各自独立**递进：

- 同一条未发布的预发布线，继续把后缀加一：`v3.3.55-beta.1` → `v3.3.55-beta.2`；
- 最新稳定版之后还没有该渠道的预发布，就从下一个 patch 的 `.1` 开始：
  稳定版是 `v3.3.54` 时产出 `v3.3.55-beta.1`；
- **基线版本已经发过正式版的预发布视为已关闭**：不再给它加后缀，改为从下一个 patch 起新的预发布。

以上规则由 `scripts/next_tag.py` 实现，不靠人工推算。

## 工作流

1. **看清工作区**。用 `git status --short --branch` 与相关 diff 确认要发布的内容。
   **不要把用户无关的改动带进发布提交**；提交内容有歧义时先确认范围。
2. **确认可以发**。跑本项目已有的验证（`run_tests.ps1`），或至少跑与该改动相关的聚焦测试。
   验证失败就停，除非用户明确接受带失败发布。
3. **算版本号**（在提交前算，避免与既有 tag 撞名）：

   ```powershell
   .\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-release\scripts\next_tag.py release --remote origin
   .\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-release\scripts\next_tag.py beta --remote origin
   .\.venv\Scripts\python.exe .codebuddy.project\skills\project\project-ok-script\project-ok-script-release\scripts\next_tag.py alpha --remote origin
   ```

   带 `--remote origin` 是为了同时考虑远端已有 tag，避免覆盖或重复。
   远端查询失败时**停下来**，不要拿本地旧 tag 猜一个版本号。
4. **交给提交链路**。按 `vcs-router` 的流程完成本次提交（它会做变更分析、摘要格式校验、
   安全检查与用户授权）。**不要**在这里手写 `git commit`。
5. **打 tag**。提交成功后，在新提交上创建带注释的 tag，风格与本仓库既有 tag 一致：

   ```powershell
   git tag -a "<算出的 tag>" -m "<算出的 tag>"
   git show --no-patch --decorate HEAD
   ```

6. **推送**。按项目配置的推送发起权处理（`push_initiation` 默认 `user_only`）：
   **必须等用户显式下达推送指令**，没有指令就在打完 tag 后停住并说明「仅本地完成」。
   获得授权后，把提交与 tag 一起推送：

   ```powershell
   git push origin HEAD "<算出的 tag>"
   ```

7. **核验并汇报**。确认推送真的成功（远端能看到该 tag），汇报：提交摘要、tag、远端、
   推送是否成功。本地有 tag 不等于发布成功——CI 发布与推送是两件事，分开说明。

## 硬约束

- **绝不复写、移动或删除已有 tag**。发布只做新增。
- **选提交摘要语言时不看合并提交**（项目摘要格式由 `git_commit.config` 决定，
  本技能不另立规则）。
- **不要只看本地 tag 就宣布发布成功**。推送结果与 CI 发布分别汇报。
- **用户只要本地操作时不推送**：明确说了「只本地」就在打完 tag 后停。
- **稳定版、beta、alpha 三条号段各自独立**，唯一的交叉规则是最新稳定版会关闭
  版本号小于等于它的预发布基线。
- **不要为了凑版本号改 `src/config.py` 的 `version`**。它固定为 `dev`，由打包流程自动改写。

## 不适用范围

| 请求 | 该去哪 |
|---|---|
| 只想提交代码，不发布 | `vcs-router` |
| 只想合并分支 | `vcs-router`（merge 当前为占位实现） |
| 改代码、补测试 | 对应开发流程 |
| 改 CI 工作流或打包配置 | 直接改 `.github/workflows/` 与 `pyappify.yml`，属上游差异需登记 |

## 参考文献

| 文件 | 内容 |
|------|------|
| `references/release-checklist.md` | 发布前检查清单、tag 与 CI 的触发关系、失败处置 |
| `scripts/next_tag.py` | 版本号计算（稳定 / beta / alpha，支持本地与远端 tag 合并判断） |
| `security/permission_policy.json` | 本技能允许的 git 子命令白名单 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |
