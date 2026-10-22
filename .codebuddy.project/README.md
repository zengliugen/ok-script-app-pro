# 项目侧配置目录（.codebuddy.project）

本目录存放**与具体项目绑定**的全部内容，与 `.codebuddy` 引擎目录分离：引擎可整体迁移
（如以 git submodule 在多项目共享），本目录则随当前项目独立维护。

## 目录结构

```
.codebuddy.project/
├── configs/            # 配置（入库）：settings.json、skills/<技能>/、rules/<规则>/ 等
├── caches/             # 运行时产物（不入库）：各技能 check_logs/、.cache/、.vcs-lock/ 等
├── store/              # 持久化项目结果（入库）：需长期保留的运行结果
├── skills/             # 技能目录（入库）：<pack>/ 可选包链接，project/ 项目专有技能
├── README.md
├── .gitignore          # 忽略 caches/ 与系统临时文件
└── cleanup-caches.cmd  # 缓存清理入口（薄入口，调用引擎侧清理工具）
```

三类内容的边界约定：

| 子目录 | 语义 | 是否入库 | 可否删除 |
|--------|------|---------|---------|
| `configs/` | 与项目绑定的配置 | 是 | 删除后技能按「配置缺失即报错」 |
| `caches/` | 运行时生成、**不持久化**的临时结果 | 否（整体忽略） | 随时可删，可重新生成 |
| `store/` | 运行时生成、需**持久化保留**的项目结果 | 是 | 删除即丢失，需谨慎 |

**约定**：后续所有运行时生成的、不持久化的临时结果一律放 `caches/`，不得散落到其他目录。

## 文件从何而来

- 本目录的全部配置由 `codebuddy-init` 技能依据 `.codebuddy/project.index.json`
  （引擎侧的配置注册表）生成占位骨架。
- **占位值（`<...>`）须按本项目实际情况填写**；填写完成前，对应技能会按
  「配置缺失即报错」的约定明确报错并提示缺失项（不静默使用默认值）。
- 已存在的文件不会被初始化脚本覆盖（幂等保护），可安全重复运行。

## 与引擎的对应关系

`.codebuddy/skills/<技能>/a.json` 的项目侧对应位置为
`.codebuddy.project/configs/skills/<pack>/<技能>/a.json`（相对路径镜像，含包层）；规则项目事实正文同理位于
`.codebuddy.project/configs/rules/...`。定位逻辑由各技能脚本经 `.codebuddy/project.index.json` 解析。

## 运行时产物目录（caches/）

脚本执行过程中生成的产物（各技能 `check_logs/`、`.cache/`、`.vcs-lock/` 等）统一落在
`caches/` 下（产物清单见索引 `artifacts.dirs`），由本目录 `.gitignore` 整体忽略、不入库。
清理用本目录的 `cleanup-caches.cmd`（薄入口，调用引擎侧 `.codebuddy/scripts/cleanup_artifacts.py`）。

## 项目专有技能

项目专有技能放在本目录的 `skills/project/<技能名>/SKILL.md`（随项目入库），
经软链接 `.codebuddy/skills/project` 被 CodeBuddy 自动加载（详见 `skills/README.md`）。
这样与 `.codebuddy` 引擎解耦：引擎可 submodule 化、多项目共享，项目专有内容只随本仓库版本控制、不进入引擎。

## 维护约定

- `configs/` 与 `store/` **入库**（随项目版本控制），`caches/` **不入库**；不含敏感信息
  （凭据等机器级配置保留在 `.codebuddy` 根的 `models.json` / `settings.local.json`，已被忽略）。
- 修改引擎侧索引（新增配置项或产物目录）后，可重新运行 `codebuddy-init` 补齐缺失内容。
