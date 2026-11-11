# 项目侧技能目录（.codebuddy.project/skills）

本目录**只剩中转链接**，随本项目仓库版本控制；`.codebuddy` 引擎（可 submodule 化、
多项目共享）不含本项目内容。

## 结构

```
.codebuddy.project/skills/
└── <pack>             # 每一项都是链接（由 pack_install.py 创建，勿手工增删，不入库）
```

每个链接指向对应来源的技能目录：

```
.codebuddy.project/skills/<pack>   ──链接──▶  ../../../project-lib/<pack>/skills   （项目侧包）
.codebuddy.project/skills/<pack>   ──链接──▶  ../../../.codebuddy/lib/<pack>/skills （引擎可选包）
```

真实技能内容随来源入库：项目侧包在 `.codebuddy.project/project-lib/<包>/skills/`，
引擎包在 `.codebuddy/lib/<pack>/skills/`。本层链接被 `.gitignore` 整目录忽略。

规则与代理目录（`.codebuddy.project/rules/`、`.codebuddy.project/agents/`）结构完全一致。

## 如何被 CodeBuddy 加载

`.codebuddy/skills/` 是 CodeBuddy 的**内置技能扫描目录**。每个包经**两层链接**挂载：

```
.codebuddy/skills/<pack>          ──软链接──▶  ../../.codebuddy.project/skills/<pack>
.codebuddy.project/skills/<pack>  ──软链接──▶  ../../../project-lib/<pack>/skills
```

即技能自动出现在 `.codebuddy/skills/<pack>/`，被 CodeBuddy 扫描并加载
（**实测：技能目录支持符号链接**）。新增/修改技能后新开会话生效。

## 创建项目侧技能

1. 建技能目录：`.codebuddy.project/project-lib/<包名>/skills/<技能名>/`。
   技能名须以 `<包名>-` 开头（或与包名相同），见 `core-rule-governance` §2.4。
2. 写 `SKILL.md`（frontmatter 的 `name` 必须与技能目录名完全一致）与
   `manifest.json`（`name` 同步）。
3. 在 `.codebuddy.project/project-lib/packs.json` 该包的 `comps.skills` 中登记技能名。
4. 安装并生效：

   ```
   python .codebuddy/scripts/pack_install.py --source project install <包名>
   ```

5. 新开会话即可见。

## 跨机器注意事项

- **git 配置**：克隆者需 `git config core.symlinks true`（Windows 默认 `false`）。若为 `false`，
  克隆出来是一个内容为路径字符串的普通文件；此时运行 `.codebuddy/setup.cmd --force` 重建链接
  （安装器会按实际文件类型移除该占位物后重建，不会因「不是目录」而失败）。
- **创建链接的权限**：Windows 需开启"开发者模式"或以管理员身份运行；`mklink /D` 失败时，
  初始化脚本会告警并给出手工命令。
- **平台**：macOS / Linux 原生支持符号链接，无额外要求。
- **链接目标用相对路径**，保证跨机器有效。

## 与引擎的关系

本目录**不进入** `.codebuddy` 引擎库。引擎可选包内容在 `lib/<pack>/`、项目侧包内容在
`.codebuddy.project/project-lib/<包>/`，本目录只存指向它们的链接。
