# 项目侧技能目录（.codebuddy.project/skills）

本目录存放**本项目独有的技能与 pack 链接**，随本项目仓库版本控制；`.codebuddy` 引擎（可 submodule 化、
多项目共享）不含本项目内容。

## 两类内容共存

```
.codebuddy.project/skills/
├── <pack>             # 引擎可选包的组件链接（由 pack_install.py 创建，勿手工增删）
└── project/           # ★项目专有技能（真实目录，随项目入库）
```

**`project` 与其他包同级并列**，区别仅在于它是真实目录（其他包是指向 `lib/` 的链接）。
规则与代理目录（`.codebuddy.project/rules/`、`.codebuddy.project/agents/`）结构完全一致。

## 如何被 CodeBuddy 加载

`.codebuddy/skills/` 是 CodeBuddy 的**内置技能扫描目录**。每个包经一个**目录软链接**挂载到该扫描目录下：

```
.codebuddy/skills/<pack>     ──软链接──▶  ../../.codebuddy.project/skills/<pack>
.codebuddy/skills/project    ──软链接──▶  ../../.codebuddy.project/skills/project
```

即本目录下的技能自动出现在 `.codebuddy/skills/<pack>/`，被 CodeBuddy 扫描并加载
（**实测：技能目录支持符号链接**）。新增/修改技能后新开会话生效。

## 创建项目专有技能

1. 建技能目录：`.codebuddy.project/skills/project/<技能名>/`
2. 写 `SKILL.md`（frontmatter 含 `name`、`description`）。
3. **无需其他操作**——`.codebuddy/skills/project` 链接已存在，CodeBuddy 自动扫描；新开会话即可见。

## 跨机器注意事项

- **git 配置**：克隆者需 `git config core.symlinks true`（Windows 默认 `false`）。若为 `false`，
  克隆出来是一个内容为路径字符串的普通文件；此时运行 `.codebuddy/setup.cmd --force` 重建链接
  （安装器会按实际文件类型移除该占位物后重建，不会因「不是目录」而失败）。
- **创建链接的权限**：Windows 需开启"开发者模式"或以管理员身份运行；`mklink /D` 失败时，
  初始化脚本会告警并给出手工命令。
- **平台**：macOS / Linux 原生支持符号链接，无额外要求。
- **链接目标用相对路径**（`../../.codebuddy.project/skills/<pack>`），保证跨机器有效。

## 与引擎的关系

本目录**不进入** `.codebuddy` 引擎库。可选包组件真实内容在引擎 `lib/<pack>/` 下，本目录只存
**指向它的链接**；`project/` 下是项目真实文件，与引擎完全解耦。
