---
name: ok-script-init-app
description: 把一个基于 ok-script-app 模板克隆出来的仓库初始化为具体游戏的自动化应用。用户说"初始化这个项目"、"按我的游戏配置模板"、"改应用名和图标"、"配置运行目标"、"接更新仓库"、"模板初始化"、"这个模板怎么改成我的游戏"时使用。收集项目信息后改写应用标识、运行目标、图标、更新仓库、首个任务、MkDocs 文档、MirrorChyan 集成与 tag 打包配置。不适用于：生成 .codebuddy.project 引擎侧配置骨架（用 core-init）、新建任务类骨架（用 ok-script-tasks）、写 run 方法体（用 ok-script-codegen）。
---

# ok-script 应用初始化

## 定位

本技能解决的是**一次性问题**：一个空模板仓库要怎么变成某个具体游戏的应用。

| 维度 | 定义 |
|------|------|
| 拥有的任务 | 收集需求 → 改写应用标识与运行目标 → 换图标 → 配更新仓库与打包 → 建首个任务 → 对齐文档站 |
| 输出 | 改写后的 `src/config.py`、`icons/`、`pyappify.yml`、`.github/workflows/`、`src/tasks/`、`docs/` 与 `README.md` |
| 排除 | 不生成引擎侧 `.codebuddy.project/` 配置骨架（交给 `core-init`）；不写 `run(self)` 动作序列（交给 `ok-script-codegen`）；不判断该不该做某个任务 |
| 前提 | 仓库是从 `ok-script-app` 模板克隆而来，尚保留模板标识 |

**与 `core-init` 的分工**：两者都叫「初始化」，但对象完全不同。`core-init` 依据 `.codebuddy/project.index.json` 生成 `.codebuddy.project/` 下的引擎配置骨架（`configs/`、`caches/`、`store/`、`.gitignore`、桥接链接），**只管工作流引擎那一层**；本技能改写的是**应用业务那一层**——游戏名、图标、运行目标、更新仓库、任务、文档。一个项目通常两者都要做，顺序上先 `core-init` 把引擎接上，再用本技能填业务配置。

## 工作流

### 第一步：先收需求，再动文件

动手前用一次简洁的提问收集下面这些信息，**不要边问边改**。允许用户对可选项回答「不知道」，此时保留模板安全默认值或说明可以在运行期再探测，**不要凭空编造**可执行名、包名、仓库地址或图标。

1. 游戏名，以及想要的应用名/窗口标题。
2. 运行目标：Windows 原生游戏、Android 模拟器/真机、浏览器，可多选，至少一个。
3. Windows：可执行文件名与窗口类名（已知时提供）。
4. Android：包名与模拟器类型（已知时提供）。
5. 浏览器：初始 URL、显示名、分辨率。
6. 源码仓库地址与更新策略：用独立更新仓库，还是早期测试阶段直接用源码仓库。
7. 替换用的 `icon.png` 与 `icon.ico`，或明确表示暂时保留占位图标。
8. 首个任务：类名、用户可见名称、一次性还是后台触发、一句话描述它做什么。
9. 是否接 MirrorChyan；接的话要资源 ID 与上传 token 的 secret 名。
10. 支持的宽高比与最低分辨率、项目/社区链接（已知时提供）。

### 第二步：先读再改

- 读 `README.md`、`src/config.py`、`pyappify.yml`、`mkdocs.yml`、`.github/workflows`、`src/tasks` 下的相关文件，以及 `docs/` 与 `docs/en/` 下的对应页面。
- 跑 `git status` 确认当前状态，**保留用户已有的无关改动**。
- 搜索仓库里残留的 `ok-script-app`、`ok-oldking`、模板仓库地址与模板专属发布集成。
- 项目里有 `.venv` 时优先用它执行 Python 命令。

### 第三步：按顺序落地

1. **改 `src/config.py`**：
   - 设置 `gui_title` 与 `links`。
   - `windows.exe` 与 `windows.hwnd_class` 只在用户给了值时才写。
   - `adb.packages` 同理。
   - `browser.url`、`browser.nick`、`browser.resolution` 同理。
   - `windows`、`adb`、`browser` **至少保留一个**，用户要支持的全部保留。
   - 需要时调整 `supported_resolution`。
   - 截图与交互的默认配置保持不动，除非用户主动要求或目标程序确实需要改。
2. **换图标**：用用户提供的素材替换 `icons/icon.png` 与 `icons/icon.ico`。文件名保持不变，确需改名时同步所有引用点。
3. **改 `pyappify.yml`**：
   - 设置应用名与 profile 名。
   - 各 profile 的 `git_url` 指向选定的源码仓库或独立更新仓库。
   - Python 版本保持 3.12，除非项目有明确且有据的理由要改。
4. **审 `.github/workflows/build.yml`**：
   - 替换项目专属的仓库地址、产物名、release 链接与 Git 身份。
   - 用独立更新仓库时，按用户选定的 secret 名配置同步目标，并**明确列出需要在仓库里创建的 secrets**。
   - 早期测试直接用源码仓库时，移除或禁用依赖模板 secrets 的更新库同步步骤。
   - 接 MirrorChyan 时，改 `mirrorchyan_uploading.yml` 与 `mirrorchyan_release_note.yml` 的 owner、仓库、资源 ID、安装包文件名与 secret 名，并保留 `build.yml` 里的触发步骤。
   - 不接 MirrorChyan 时，删掉这两个 workflow 并移除 `build.yml` 里的触发步骤。
   - 用户不用 CNB 时移除 CNB 集成。
5. **建首个任务**：在 `src/tasks` 下建任务类，把模块与类名成对注册进 `src/config.py` 的 `onetime_tasks` 或 `trigger_tasks`。
   - 任务结构走 `ok-script-tasks`。
   - 从行为描述或截图实现自动化动作时走 `ok-script-codegen`。
   - 用户可见文案要可翻译；需要译文时走 `ok-script-i18n`。
6. **模板资源**：按下面「模板资源」一节处理 `ok_templates/`。
7. **对齐文档站**：
   - 按初始化后的项目设置 `mkdocs.yml` 的 `site_name`、`site_description`、`repo_name`、`repo_url`、`edit_uri`。
   - 在 `docs/` 与 `docs/en/` 两侧同步更新模板标识、仓库、安装包名、运行目标、链接与安装说明。
   - 两种语言结构保持对齐；页面增删改动时同步更新 `mkdocs.yml` 的导航。
   - 用户需要 GitHub Pages 时保留 `.github/workflows/docs.yml`，否则说明可以删除。
8. **`README.md` 与 `README_en.md`** 保持为简洁的仓库首页，指向 MkDocs 正式文档。

## 模板资源

`ok_templates/` 是**模板编辑区**：放原始截图与手工标注 `coco_annotations.json`，不是切图产物。上游模板的 `.gitignore` 忽略了它，**下游真实应用必须改掉这一处**。

### 为什么必须入库

| 资产 | 可再生产性 | 入库必要性 |
|---|---|---|
| `ok_templates/coco_annotations.json`（类名 + bbox） | **不可再生**，人工标注 | 必需 |
| `ok_templates/*.png`（原始截图） | **不可再生**，需复现完全相同的画面 | 必需 |
| `assets/templates/*.png`（裁剪产物） | 可由源图重新生成 | 可选 |

`generate_templates` 在源图缺失时直接抛 `ValueError`。源图一旦不入库，新加入的协作者就无法重新导出任何特征，标注知识随之丢失；而裁剪产物丢掉只是麻烦，重新生成即可。

### 怎么入库

删掉 `.gitignore` 里的 `ok_templates/` **整行**。

**不要**改成 `ok_templates/*` 再加反选——目录级忽略会让 git 停止遍历该目录，深层反选实测无效：

```gitignore
ok_templates/
!ok_templates/coco_annotations.json    # 无效：文件仍被忽略
```

### 入库后必须配发布过滤

**PyAppify 不读 `.gitignore`**：它用 libgit2 克隆指定 tag 的完整 tree 再打包，`.gitignore` 只影响 `git add`，不影响已提交内容的检出。其 Rust 源码中没有任何忽略规则处理逻辑，全链路唯一排除项是 `__pycache__`。

所以入库即进包。下游应用发布前必须做两件事：

1. 新建独立更新仓库，在 `build.yml` 用 `ok-oldking/partial-sync-repo` 按 `deploy.txt` 白名单同步；
2. 把 `pyappify.yml` 各 profile 的 `git_url` 指向更新库。

白名单是**正向列举**：`deploy.txt` 里没有 `ok_templates` 就天然被挡在包外，同时用 `.update_repo_gitignore` 兜住构建产物与日志。四要素（`deploy.txt`、`partial-sync-repo`、`.update_repo_gitignore`、指向更新库的 `git_url`）缺一不可。

### 不要用 Git LFS

PyAppify 走 libgit2，不执行 LFS smudge filter。CI 侧 `actions/checkout` 带 `lfs: true` 能取到真图，但用户侧克隆拿到的是约 130 字节的指针文本文件，`cv2.imread` 读不出图像。故障只发生在终端用户机器上，构建期完全不报错。

## 验证

- 用 `python -m compileall` 校验改过的 Python 文件，或用本地 `.venv` 跑相关测试。
- 有 YAML 解析器时校验 YAML 语法。
- 文档改动后，在本地 `.venv` 装 `requirements-docs.txt` 并跑 `python -m mkdocs build --strict`。
- 只有在当前环境适合启动 GUI 时才跑 `python main_debug.py`。
- 再搜一遍残留的模板名与地址；区分「上游有意保留的文档/action 引用」与「本项目漏改的残留」。
- 展示最终 diff，并汇总仍待用户提供的信息。

用户没有明确要求发布时，**不要创建或推送 tag**。要发布时用语义化版本号如 `v0.1.0`；`.github/workflows/build.yml` 的触发条件是 `v*`。

## 参考文献

| 文件 | 内容 |
|------|------|
| `references/checklist.md` | 初始化逐项核对清单、常见漏改点、更新仓库与 MirrorChyan 的取舍 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |

## 不适用范围

| 请求 | 该去哪 |
|------|--------|
| 生成 `.codebuddy.project/` 引擎配置骨架 | `core-init` |
| 新建任务类骨架与配置声明 | `ok-script-tasks` |
| 写 `run(self)` 里的动作序列 | `ok-script-codegen` |
| 补翻译目录 | `ok-script-i18n` |
| 打 tag、发布版本 | `ok-script-deploy` |
| 提交代码 | `vcs-router` 技能路由 |

## 已知局限

- 本技能只改**配置文件层面的标识**，无法替用户验证目标游戏的可执行名、窗口类名、包名是否真的正确——这些必须在真机或模拟器上跑一次 `main_debug.py` 才能确认。
- 首个任务只建骨架，行为实现依赖 `ok-script-codegen` 对截图或描述的解读质量。
- 更新仓库与 MirrorChyan 的 secrets 必须由用户在仓库设置里手工创建，技能只能列出清单。
