# 项目侧配置目录（.codebuddy.project）

本目录存放**与具体项目绑定**的全部内容，与 `.codebuddy` 引擎目录分离：引擎可整体迁移
（如以 git submodule 在多项目共享），本目录则随当前项目独立维护。

## 目录结构

```
.codebuddy.project/
├── configs/            # 配置与规则项目事实正文（入库）：settings.json、skills/<技能>/、rules/<规则>/ 等
├── caches/             # 运行时产物（不入库）：各技能 check_logs/、.cache/、.vcs-lock/ 等
├── store/              # 持久化项目结果（入库）：需长期保留的运行结果
├── rules/              # 规则目录（入库）：<pack>/ 可选包链接，project/ 项目专有规则
├── skills/             # 技能目录（入库）：<pack>/ 可选包链接，project/ 项目专有技能
├── agents/             # 代理目录（入库）：<pack>/ 可选包链接，project/ 项目专有代理
├── envs/               # 工具环境实例（不入库，仅 README.md 入库）：各语言包的可重建环境
├── README.md
├── .gitignore          # 忽略 caches/、envs/* 与系统临时文件
└── cleanup-caches.cmd  # 缓存清理入口（薄入口，调用引擎侧清理工具）
```

四类内容的边界约定：

| 子目录 | 语义 | 是否入库 | 可否删除 |
|--------|------|---------|---------|
| `configs/` | 与项目绑定的配置与规则项目事实正文 | 是 | 删除后技能按「配置缺失即报错」 |
| `caches/` | 运行时生成、**不持久化**的临时结果 | 否（整体忽略） | 随时可删，可重新生成 |
| `store/` | 运行时生成、需**持久化保留**的项目结果 | 是 | 删除即丢失，需谨慎 |
| `envs/` | 各语言包的工具环境实例（可重建） | 否（仅 `README.md` 入库） | 随时可删，由对应技能的 `ensure` 重建 |

`rules/`、`skills/`、`agents/` 三个组件根**全部入库**，不与上述四类混用：其中 `<pack>/` 是可选包
桥接链接（不入库，由 `pack_install.py` 派生），`project/` 是项目专有内容（必须入库）。

**约定**：后续所有运行时生成的、不持久化的临时结果一律放 `caches/`，不得散落到其他目录。

## 文件从何而来

- 本目录的全部配置由 `core-init` 技能依据 `.codebuddy/project.index.json`
  （引擎侧的配置注册表）生成占位骨架。
- **占位值（`<...>`）须按本项目实际情况填写**；填写完成前，对应技能会按
  「配置缺失即报错」的约定明确报错并提示缺失项（不静默使用默认值）。
- 已存在的文件不会被初始化脚本覆盖（幂等保护），可安全重复运行。

## 与引擎的对应关系

`.codebuddy/skills/<pack>/<技能>/config.json` 的项目侧对应位置为
`.codebuddy.project/configs/skills/<pack>/<技能>/config.json`（相对路径镜像，含包层）；
规则项目事实正文同理位于 `.codebuddy.project/configs/rules/...`。定位逻辑由各技能脚本经
`.codebuddy/project.index.json` 解析。

## 运行时产物目录（caches/）

脚本执行过程中生成的产物（各技能 `check_logs/`、`.cache/`、`.vcs-lock/` 等）统一落在
`caches/` 下（产物清单见索引 `artifacts.dirs`），由本目录 `.gitignore` 整体忽略、不入库。
清理用本目录的 `cleanup-caches.cmd`（薄入口，调用引擎侧 `.codebuddy/scripts/cleanup_artifacts.py`）。

## 项目专有组件（rules / skills / agents）

三个组件根均为「可选包链接 + `project/` 真实目录」两层结构，`project/` 下按**族**分组一层：

```
rules/project/<族>/                 # 族名取技能名共同前缀，同时就是规范目录名
├── RULE.mdc                        # 规范索引（alwaysApply: false）
├── <主题>.mdc                      # 规范分项（alwaysApply: false）
└── <技能名>.entry/<技能名>.entry.mdc  # 入口规则（alwaysApply: true）

skills/project/<族>/<技能名>/SKILL.md
agents/project/<族>/<代理名>.md
```

- 组件名一律以 `project-` 开头，族内每个技能与入口规则名都必须以 `<族名>-` 开头
  （由 `check_manifest.py` 校验项 14 机械核对）。
- 规范分项**平铺在族目录内**，不再各占一层子目录——规范没有附件要挂，为单个 `.mdc` 建目录只会
  得到空壳。规则目录的层级因此比技能目录浅一层，这属预期形态（见 `core-rule-governance` §2.4）。
- 只有入口、无规范的族仍在族目录下建 `.entry/`，不退化为 `rules/project/<名>.entry/`。
- 三类组件均经 `.codebuddy/<类型>/project` 软链接被加载（详见 `skills/README.md`）。
- 与 `.codebuddy` 引擎解耦：引擎可 submodule 化、多项目共享，项目专有内容只随本仓库版本控制。

## 维护约定

- `configs/` 与 `store/` **入库**（随项目版本控制），`caches/` 与 `envs/*` **不入库**；不含敏感信息
  （凭据等机器级配置保留在 `.codebuddy` 根的 `models.json` / `settings.local.json`，已被忽略）。
- 修改引擎侧索引（新增配置项或产物目录）后，可重新运行 `core-init` 补齐缺失内容。
