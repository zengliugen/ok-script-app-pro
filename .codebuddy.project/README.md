# 项目侧配置目录（.codebuddy.project）

本目录存放**与具体项目绑定**的全部内容，与 `.codebuddy` 引擎目录分离：引擎可整体迁移
（如以 git submodule 在多项目共享），本目录则随当前项目独立维护。

## 目录结构

```
.codebuddy.project/
├── configs/            # 配置与规则项目事实正文（入库）：settings.json、packs.json、project-packs.json 等
├── caches/             # 运行时产物（不入库）：各技能 check_logs/、.cache/、.vcs-lock/ 等
├── store/              # 持久化项目结果（入库）：需长期保留的运行结果
├── project-lib/        # ★项目侧包仓库（入库）：<包>/ 下 rules|skills|agents 与 packs.json
├── rules/              # 中转链接层（不入库）：<pack>/ 链接，指向 project-lib 或引擎 lib
├── skills/             # 同上：<pack>/ 链接（不入库），另有 README.md 说明文档入库
├── agents/             # 同上：<pack>/ 链接（不入库）
├── envs/               # 工具环境实例（不入库，仅 README.md 入库）：各语言包的可重建环境
├── README.md
├── .gitignore          # 忽略 caches/、envs/*、三个组件根（整层链接）与系统临时文件
└── cleanup-caches.cmd  # 缓存清理入口（薄入口，调用引擎侧清理工具）
```

五类内容的边界约定：

| 子目录 | 语义 | 是否入库 | 可否删除 |
|--------|------|---------|---------|
| `configs/` | 与项目绑定的配置与规则项目事实正文 | 是 | 删除后技能按「配置缺失即报错」 |
| `caches/` | 运行时生成、**不持久化**的临时结果 | 否（整体忽略） | 随时可删，可重新生成 |
| `store/` | 运行时生成、需**持久化保留**的项目结果 | 是 | 删除即丢失，需谨慎 |
| `envs/` | 各语言包的工具环境实例（可重建） | 否（仅 `README.md` 入库） | 随时可删，由对应技能的 `ensure` 重建 |
| `project-lib/` | **项目侧包仓库**：包内容与注册表 | 是 | 删除即丢失，包需重新安装 |

`rules/`、`skills/`、`agents/` 三个组件根**整层只是链接**（指向 `project-lib/<包>/<类型>` 或引
擎 `lib/<pack>/<类型>`），由 `pack_install.py` 派生，**整层不入库**。项目真实内容只在 `project-lib/`。

**约定**：后续所有运行时生成的、不持久化的临时结果一律放 `caches/`，不得散落到其他目录。

## 新机器安装步骤

仓库里**不含**三类产物：符号链接（Windows 克隆时被落成普通文本文件）、`envs/` 下的工具环境、
以及 `.git` 里的钩子配置。因此新机器检出后必须按下列顺序执行一次。

### 前置条件

| 项 | 要求 | 不满足时 |
|---|---|---|
| Python 3 | 已安装且在 PATH（`python` 或 `py` 可用） | `setup` 直接报错退出 3 |
| git 符号链接 | `git config core.symlinks true`（Windows 默认 `false`） | 链接落成普通文件，需 `setup --force` 重建 |
| 链接权限 | Windows 需开启「开发者模式」或以管理员运行 | `mklink /D` 失败，`setup` 告警并给出手工命令 |
| uv | 重建 Python 工具环境时需要（第四步） | 该步失败，不影响前三步 |

### 步骤 1：检出引擎子模块

```bash
git clone <本项目仓库地址> ok-script-app-pro
cd ok-script-app-pro
git submodule update --init          # 引擎是 submodule，不初始化则 .codebuddy/ 为空
```

### 步骤 2：生成项目侧骨架并装引擎可选包

```bash
.codebuddy/setup.cmd                 # Windows（双击亦可）
./.codebuddy/setup.sh                # macOS / Linux
```

它做四件事：按 `project.index.json` 生成/补齐 `.codebuddy.project/` 骨架与产物空目录；建立
`settings.json` 与 `.codebuddy/<类型>/<pack>` 两层的可选包链接；按 `configs/packs.json` 的
`installed` 物化组件；把 `core.hooksPath` 指向入库的 `hooks/`，使提交前校验生效。

> **这一步不安装项目侧包**。`setup` 只调 `pack_install.py verify --repair`（默认
> `--source engine`），项目侧包属另一张注册表，需在步骤 3 单独安装。

### 步骤 3：安装项目侧包

```bash
python .codebuddy/scripts/pack_install.py --source project install extension-capability upstream-compat ok-script
```

三个包均 `default: false`，**不装则项目侧技能与规则全部不生效**——不报错，只是加载不到。
`extension-capability` 与 `upstream-compat` 的 `requires` 指向引擎包（`devflow` / `vcs`），
安装时会自动带上；若对应引擎包尚未装，该步会一并装进引擎侧安装状态。

只想装其中一部分时按需挑选，例如：

```bash
python .codebuddy/scripts/pack_install.py --source project install upstream-compat   # 只装上游差异相关能力
```

### 步骤 4：重建语言工具环境（按需）

`envs/` 不入库，需要跑提交前检查或覆盖率统计时重建。本项目的 Python 工具环境：

```bash
python .codebuddy/lib/python/skills/python-env/scripts/env.py ensure
```

它是独立虚拟环境，只装 `ruff` 与 `coverage`，与项目自身运行环境物理隔离；删除后数秒可重建。
无此环境时提交仍能完成，但 H1 的 Python 静态检查会报「环境不可用」（退出码 3）。

> 其他语言包同理——各自的 `envs/<包名>/` 由该包的技能重新生成；未安装该包时本步无需执行。
> 上表命令依赖 `python` 可选包已安装（步骤 2 中 `--pack` 指定，或后续 `pack_install.py install python`）。

### 步骤 5：验证安装

```bash
python .codebuddy/scripts/check_manifest.py                      # 期望：[一致] 包 N 个，退出码 0
python .codebuddy/scripts/pack_install.py status                 # 期望：一致（引擎可选包）
python .codebuddy/scripts/pack_install.py --source project status  # 期望：一致（项目侧包）
```

三条都应返回退出码 0。第二条报「不一致」通常是步骤 2 未成功（链接缺失）；第三条报
「不一致」则是步骤 3 未执行或包名写错。

**最后新开一次会话**：技能与规则在会话启动时扫描，当前会话看不到新装组件。

### 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `.codebuddy/` 下为空 | 未初始化 submodule | `git submodule update --init` |
| 项目侧技能列表里看不到任何 `*-guard` 技能 | 步骤 3 未执行 | 重跑步骤 3 后新开会话 |
| `setup` 报 `mklink` 失败 | 无符号链接权限 | 开启开发者模式，或按告警给出的手工命令执行 |
| 链接存在但读不了内容 | `core.symlinks=false` 克隆 | `git config core.symlinks true` 后 `setup --force` |
| 提交前 Python 检查报退出码 3 | `envs/python` 未重建 | 执行步骤 4 |
| `check_manifest.py` 报项 17「已声明但未安装」 | 注册表声明了包但未 install | 重跑步骤 3，或删掉该包声明 |

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

## 项目侧包（project-lib）

项目自有内容按**包**组织，与引擎可选包完全同构。包仓库在 `project-lib/`：

```
project-lib/
├── packs.json                       # 包注册表：包名、requires、default、comps
└── <包名>/
    ├── rules/<规则名>/RULE.mdc      # 规范规则（alwaysApply: false）
    ├── rules/<规则名>.entry/<规则名>.entry.mdc  # 入口规则（alwaysApply: true）
    ├── skills/<技能名>/SKILL.md
    └── agents/<代理名>.md
```

`skills/`、`rules/`、`agents/` 三个组件根只是指向 `project-lib/<包名>/<类型>` 的**中转链接**，
由 `pack_install.py --source project` 物化，整层不入库。

- 组件名遵循与引擎侧同一条约定：以 `<包名>-` 开头，或与包名相同（`check_manifest.py` 项 8）。
  **不再有 `project-` 前缀要求**——项目侧身份由 `location: project-lib` 与所在注册表表达。
- 规范分项**平铺在 `rules/<规则名>/` 内**，不为单个 `.mdc` 建目录。
- 三类组件均经两层链接被加载：`.codebuddy/<类型>/<包名>` → `.codebuddy.project/<类型>/<包名>`
  → `project-lib/<包名>/<类型>`。
- **依赖单向**：项目包 `requires` 可指向引擎包（安装时自动带上），引擎注册表不得出现项目包名。
- 与 `.codebuddy` 引擎解耦：引擎可 submodule 化、多项目共享，项目侧包内容只随本仓库版本控制。

安装与查看：

```bash
python .codebuddy/scripts/pack_install.py --source project list
python .codebuddy/scripts/pack_install.py --source project install <包名>
python .codebuddy/scripts/pack_install.py --source project status
```

## 维护约定

- `configs/`、`store/` 与 `project-lib/` **入库**（随项目版本控制），`caches/` 与 `envs/*` **不入库**；不含敏感信息
  （凭据等机器级配置保留在 `.codebuddy` 根的 `models.json` / `settings.local.json`，已被忽略）。
- 修改引擎侧索引（新增配置项或产物目录）后，可重新运行 `core-init` 补齐缺失内容。
