# 初始化核对清单（ok-script-init-app）

初始化一个 ok-script-app 模板仓库时，漏改项的后果分两类：**报错**（启动即崩，容易发现）与**静默**（界面照常打开，只是名字、链接、图标还是模板的）。本清单按「漏了什么 → 会怎样」组织，逐项勾选。

## 一、应用标识

| 检查点 | 文件 | 漏改后果 |
|---|---|---|
| `gui_title` 改成目标应用名 | `src/config.py` | 窗口标题仍是 `ok-script-app`，**不报错** |
| `links` 各项指向本项目仓库与社区 | `src/config.py` | 关于页里全是模板作者的链接，用户点过去会到上游仓库 |
| `pyappify.yml` 的 `name` | `pyappify.yml` | 安装包文件名、安装目录名仍是模板名 |
| 各 profile 的 `name` | `pyappify.yml` | 安装包文件名形如 `<模板名>-win32-China-setup-*.exe` |
| `mkdocs.yml` 的 `site_name` / `repo_url` / `edit_uri` | `mkdocs.yml` | 文档站点页头与「编辑此页」指向模板仓库 |
| `README.md` / `README_en.md` 的项目描述 | 根目录 | 仓库首页仍在介绍模板本身 |

**全局搜索残留**：改完后在仓库里搜 `ok-script-app`、`ok-oldking`，逐个确认是「有意保留的上游引用」还是「漏改」。

## 二、运行目标

至少保留 `windows`、`adb`、`browser` 中的**一个**，用户要支持的全部保留。

| 目标 | 必填项 | 只在有值时写 |
|---|---|---|
| Windows | `windows.exe`、`windows.hwnd_class` | 未知时留空，运行期再探测 |
| Android | `adb.packages`、模拟器类型 | 同上 |
| 浏览器 | `browser.url`、`browser.nick`、`browser.resolution` | 同上 |

**不要编造**可执行名、包名或 URL。猜错的值比留空更糟：留空会在运行时报「找不到目标」，猜错的值可能匹配到别的进程。

`supported_resolution` 只在用户明确给了宽高比与最低分辨率时才调整；否则保留模板默认。

## 三、图标

- 替换 `icons/icon.png` 与 `icons/icon.ico`。
- **文件名保持不变**，因为 `pyappify.yml` 的 `icon` 字段与 `src/config.py` 都可能引用。
- 确需改名时，同步 `pyappify.yml` 的 `icon`、`src/config.py` 里的图标路径，以及 `docs/` 中提到图标的说明。

## 四、更新仓库与打包

这是最容易做错的一环，因为**两种策略的配置互相排斥**。

### 策略 A：独立更新仓库（成熟应用的选择）

| 步骤 | 落点 |
|---|---|
| 新建更新仓库 | GitHub / CNB，需有 push 权限 |
| 配置 secrets | `CNB_TOKEN`、`OK_GH`（或用户自定义名） |
| `build.yml` 加同步步骤 | `ok-oldking/partial-sync-repo`，`sync_list: 'deploy.txt'`，`gitignore_file: '.update_repo_gitignore'` |
| `pyappify.yml` 各 profile 的 `git_url` | **指向更新库**，不是源码仓库 |
| `deploy.txt` | 正向列举要同步的条目；确认 `ok_templates`、`tests`、`docs`、`.github` **不在**清单里 |
| `keep_releases` | 建议设 `30` 之类的上限，避免更新库历史无限膨胀 |

### 策略 B：直接用源码仓库（早期测试阶段）

| 步骤 | 落点 |
|---|---|
| 移除同步步骤 | `build.yml` 里删掉依赖模板 secrets 的 `Synchronize repositories` |
| `pyappify.yml` 各 profile 的 `git_url` | 指向源码仓库 |
| 后果认知 | **仓库里所有跟踪内容都会进安装包**——开发文档、测试、引擎子模块、`.codebuddy.project` 全部包含 |

**两者不可混用**：留着同步步骤但 `git_url` 指向源码仓库，等于白做过滤；`git_url` 指向更新库却删了同步步骤，更新库永远不更新，打出的包是旧代码。

### MirrorChyan

| 情形 | 操作 |
|---|---|
| 接 | 改 `mirrorchyan_uploading.yml` 与 `mirrorchyan_release_note.yml` 的 owner、仓库、`mirrorchyan_rid`、安装包文件名与 secret 名；保留 `build.yml` 的触发步骤 |
| 不接 | 删掉这两个 workflow，并移除 `build.yml` 里的触发步骤 |

两者只做一半的后果：workflow 存在但 secret 缺失 → 步骤失败或静默跳过；`build.yml` 仍触发但 workflow 已删 → `gh workflow run` 报错。

## 五、模板资源（ok_templates）

| 步骤 | 命令/落点 | 校验 |
|---|---|---|
| 从 `.gitignore` 删掉 `ok_templates/` 整行 | `.gitignore` | `git check-ignore -v ok_templates/coco_annotations.json` 应无输出 |
| 确认文件进入索引 | `git add ok_templates` | `git ls-files ok_templates \| wc -l` 大于 0 |
| 确认不进发布包 | `deploy.txt` | `grep -c ok_templates deploy.txt` 应为 0 |
| 打测试 tag 后核对更新库 | 更新库顶层结构 | 不应出现 `ok_templates` 目录 |

**不要**用 `ok_templates/*` + `!ok_templates/xxx` 的写法。目录级忽略会让 git 不再遍历该目录，深层反选无效——这一点已实测确认。

**不要**用 Git LFS。PyAppify 走 libgit2，不执行 smudge filter，用户侧拿到的是指针文本文件。

## 六、首个任务

| 检查点 | 落点 | 漏改后果 |
|---|---|---|
| 任务类建在 `src/tasks/` 下 | 目录 | — |
| 模块与类名成对注册 | `src/config.py` 的 `onetime_tasks` / `trigger_tasks` | 类写好了但**永远不出现在任务列表里，不报错** |
| 元数据在 `super().__init__` 之后设置 | 任务类 | 父类初始化覆盖，界面显示类名 |
| `config_type` 的键都在 `default_config` 里 | 任务类 | 配置项不渲染，不报错 |
| 用户可见文案可翻译 | `i18n/` 或 `extensions/.../i18n/` | 界面混语言 |

任务结构的完整约束见 `ok-script-tasks`，动作序列见 `ok-script-codegen`，翻译见 `ok-script-i18n`。

## 七、文档站

- `mkdocs.yml` 的 `site_name`、`site_description`、`repo_name`、`repo_url`、`edit_uri`。
- `docs/` 与 `docs/en/` **两侧同步**：模板标识、仓库地址、安装包名、运行目标、链接、安装步骤。
- 页面增删时同步 `mkdocs.yml` 的 `nav`。
- 改完跑 `python -m mkdocs build --strict`：`--strict` 会把断链与未知锚点当错误，能拦住中英文结构漂移。

**只改一侧是常见漏改**：中文页改了项目名，英文页还写着模板名，两语言读者看到的事实不一致。

## 八、收尾

- `git status` 确认没有把无关改动带进本次初始化。
- 展示最终 diff。
- 列出仍待用户提供的信息（可执行名、窗口类名、图标、secrets 等）。
- 用户没明确要求时**不要**创建或推送 tag。
