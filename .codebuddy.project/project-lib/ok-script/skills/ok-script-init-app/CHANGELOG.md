# ok-script-init-app 变更记录

> **本技能由仓库内 `.agents/skills/initialize-ok-script-app` 迁移而来，归本项目维护。**
> 来源内容为英文，本次迁移做了中文化与引擎规范对齐改造；上游更新需对照来源目录手工同步，
> 具体来源版本见下表。

## 记录规范

- 一个变更一节，**按时间倒序**，标题为 `## YYYY-MM-DD 主题`。
- 每节写明**为什么改**，而不只是改了什么——只记措辞调整的不写。
- 行为变更（判定逻辑、产出物、触发条件）必须记录；纯文档润色不单独成节。

## 来源版本

| 项 | 值 |
|---|---|
| 来源路径 | `.agents/skills/initialize-ok-script-app` |
| 来源 `SKILL.md` SHA-256 前缀 | `1a4992d5ee9e` |
| 迁移日期 | 2026-11-12 |

## 2026-11-12 从 .agents 迁移并中文化

**为什么迁**：`.agents/skills/` 那套约定用 `agents/openai.yaml` 做平台适配，不参与本项目的引擎校验，
四个兄弟技能（tasks / codegen / i18n / release）早已迁到项目侧并按引擎规范改造，只剩本技能还在原处，
形成两套并行的技能组织方式。

**本地改造**：

1. 正文中文化，并按引擎技能规范补齐核心契约、参考文献、不适用范围、已知局限四节。
2. **新增「模板资源」一节**。来源只说「模板图与 COCO 标注放 `ok_templates`，与导出的裁剪图分开」，
   从不提版本控制问题。但上游模板的 `.gitignore` 忽略该目录，而下游应用需要协作者共同维护标注，
   两者冲突。本节写明：必须入库、目录级忽略会使深层反选失效（实测）、入库后必须配
   `partial-sync-repo` + `deploy.txt` 白名单、不得用 Git LFS（libgit2 不执行 smudge filter）。
3. **明确与 `core-init` 的分工**。来源不涉及引擎，但本项目里两者的描述词都与「初始化」相关，
   不写清会在路由时混淆：`core-init` 生成 `.codebuddy.project` 骨架，本技能只改应用业务层。

**未采纳来源的部分**：无。来源的七步流程、需求收集清单与验证要求全部保留。

## 与来源的同步策略

1. 重算来源 `SKILL.md` 的 SHA-256 前缀：

   ```bash
   python -c "import hashlib;print(hashlib.sha256(open('.agents/skills/initialize-ok-script-app/SKILL.md','rb').read()).hexdigest()[:12])"
   ```

2. 与上表「来源版本」比对。不一致即来源已变，**逐节比对后并入本地**，再更新该表。
3. **不要用来源文件直接覆盖本地**——中文正文、新增的模板资源章节、与 `core-init` 的分工说明
   都是本地改造，覆盖会丢失且不会报错。
