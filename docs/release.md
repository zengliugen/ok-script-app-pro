# 打包与发布

## 发布相关文件

- `.github/workflows/build.yml`：监听 `v*` tag，运行测试、同步更新文件、打包并创建 GitHub Release。
- `pyappify.yml`：定义应用名称、入口、图标、Python 版本和更新仓库。
- `pyproject.toml`：定义 Qt 和文档依赖 profile；`web` 为桌面应用的兼容别名。
- `requirements.txt`：编译的安装锁定文件；`requirements-web.txt` 引用同一锁定文件以兼容旧命令。
- `deploy.txt`：定义同步到独立更新仓库的文件。
- `.github/workflows/mirrorchyan_*.yml`：可选的 Mirror酱上传和更新日志工作流。

## 构建工作流现状

`.github/workflows/build.yml` 已按本项目调整：

- Git 身份改为 `zengliugen`。
- 保留 `Synchronize repositories` 步骤：按 `deploy.txt` 白名单把运行文件同步到独立更新库
  `ok-script-app-pro-update`，并在更新库创建同名 tag。需要仓库 secret `OK_GH`
  （对更新库有 `contents: write` 权限的 PAT）；未配置或权限不足时该步骤失败，构建会红。
- `pyappify-action` 固定 `version: v1.2.3`，避免上游更新导致构建结果漂移；同时开启 `add_version_name: true`，安装包文件名带发布版本号。
- 声明 `permissions: contents: write`，否则默认只读的 `GITHUB_TOKEN` 无法创建 Release（报 403 `Resource not accessible by integration`）。
- 产物共 4 个：两个 profile 安装包（`China`/`Global`，名称带版本号如 `ok-script-app-pro-win32-China-setup-1.1.3.exe`）、`ok-script-app-pro-win32-online-setup-<版本>.exe`（在线安装包）与 `ok-script-app-pro-win32.zip`（免安装压缩包）。ok-script 3 移除了 Web 前端，`pyappify.yml` 不再有 `Web` profile。
- 更新日志使用本次 tag 的简要说明，不依赖同步步骤的输出。

构建约需 20 分钟，主要耗时在 PyAppify 的 Tauri/Rust 全量编译（约 18 分钟），GitHub Actions 未对该过程做缓存。如需加速，可以在 `pyappify-action` 步骤加 `use_release` 输入复用已构建的 launcher，但前提是本项目未改动图标与 `pyappify.yml`。

## 发布包的过滤机制

本仓库已启用发布过滤：`build.yml` 的 `Synchronize repositories` 按 `deploy.txt` 白名单把运行文件同步到独立更新库并创建同名 tag，`pyappify.yml` 的两个 profile 均指向该更新库。过滤之所以必须存在，是因为一个容易被忽略的事实——

### PyAppify 不读 `.gitignore`

PyAppify 用 libgit2 克隆指定 tag 的**完整 tree** 再打包，`.gitignore` 只影响 `git add`，不影响已提交内容的检出。其 Rust 源码中没有任何忽略规则处理逻辑，全链路唯一的排除项是 `__pycache__`。

因此**凡是入库的内容都会进安装包**，无论 `.gitignore` 怎么写。本仓库的口径是：`ok_templates/`、`ok_scenes/`、`ok_tasks/` 入库供协作，但不在 `deploy.txt` 中，所以不进安装包；`assets/scenes/` 是运行期产物区，随 `assets` 一起分发。下游应用沿用同一口径即可。

### 过滤靠白名单，不靠忽略规则

`deploy.txt` 与 `.update_repo_gitignore` 是配套机制，本仓库已接上 `partial-sync-repo`：

```
源码仓库 --(deploy.txt 白名单)--> 更新仓库 --(git_url)--> PyAppify 打包
```

| 环节 | 作用 |
|---|---|
| `deploy.txt` | **正向列举**要同步的条目；未列出的目录天然不进更新库 |
| `partial-sync-repo` | 按上面的清单把文件复制到更新库并打 tag |
| `.update_repo_gitignore` | 同步后写入更新库根目录，兜住构建产物与日志 |
| `pyappify.yml` 的 `git_url` | 必须指向**更新库**，不能是源码仓库 |

四者缺任一项，过滤都不成立。白名单之所以可靠，是因为它是**列举式**的：新增目录默认被排除，漏写只会导致「少带」，不会导致「多带」。

### 下游应用要做什么

本模板已经把过滤链路接好，下游应用要做的是**换成自己的归属**，不要沿用本项目的更新库：

1. 确认 `ok_templates/`、`ok_scenes/`、`ok_tasks/` 已入库（本模板已如此）；
2. 新建自己的更新库（如 `xxx-update`），把 `build.yml` 的 `repos` 与 secret 名换成自己的（`OK_GH` 存自己的 PAT）；
3. 把 `pyappify.yml` 各 profile 的 `git_url` 改成同一个更新库；
4. 确认 `deploy.txt` **不写** `ok_templates`、`ok_scenes`、`ok_tasks`（同时确认 `tests`、`docs`、`.github`、`.codebuddy*` 也不在清单内），并包含全部运行时代码——本项目新增了 `extensions`，删除该行会打出启动即 `ImportError` 的包；
5. 保留 `pyappify.yml` 的 `keep_releases: 30`，避免更新库历史无限膨胀。

**不要把截图放进 Git LFS。** PyAppify 走 libgit2，不执行 LFS smudge filter：CI 侧 `actions/checkout` 带 `lfs: true` 能取到真图，但用户侧克隆拿到的是约 130 字节的指针文本，`cv2.imread` 读不出来。故障只发生在终端用户机器上，构建期不报错。

### 体积参考

| 内容 | 体积 | 说明 |
|---|---|---|
| 源码仓库 `.git` | 约 60 MB | 含上游历史；用户不再克隆它 |
| 白名单运行文件 | 约 2.2 MB / 104 个文件 | 当前 `deploy.txt` 清单（含 `assets/scenes`） |
| 内联的 ok-script + pyappify | 约 8 MB | CI 的 `inline_ok_requirements` 运行时生成 |
| 更新库单次快照 | 约 10 MB | 白名单 + 内联内容 |

`tests/`、`docs/`、`extensions_docs/`、`.codebuddy` 子模块与源码历史都不会进入安装包。

## Mirror酱

两个 MirrorChyan workflow 已保留，默认关闭：

- `.github/workflows/mirrorchyan_uploading.yml`
- `.github/workflows/mirrorchyan_release_note.yml`

启用方式：

1. 在仓库 **Settings → Secrets and variables → Actions** 中新建 Variables `MIRRORCHYAN_ENABLED`，值为 `true`。
2. 新建 Secrets `MirrorChyanUploadToken`。
3. 向 MirrorChyan 申请资源并替换 workflow 中的 `mirrorchyan_rid`。

PyAppify 会在安装包名称中加入发布版本（`add_version_name: true`），例如
`ok-script-app-pro-win32-China-setup-1.1.3.exe`。Mirror酱使用
`ok-script-app-pro-win32-China-setup-*.exe` 匹配所选版本的安装包。

未启用时，两个 workflow 的 job 条件不满足，会被直接跳过，不会因缺少 secrets 而报错。`build.yml` 中的 `Trigger MirrorChyanUploading` 步骤同样受 `vars.MIRRORCHYAN_ENABLED` 控制。

## 推送版本 tag

提交并推送初始化结果，再创建符合 `v*` 规则的 tag：

```bash
git add .
git commit -m "Initialize project"
git push origin HEAD
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions 会运行测试、打包 EXE，并创建对应的 GitHub Release。发布前再次搜索 `.github/workflows`，确认没有遗留的模板仓库地址、项目名或未配置的 Secrets。

修改依赖后，使用项目虚拟环境重新编译锁定文件：

```powershell
python -m piptools compile --extra qt --strip-extras --no-header --output-file requirements.txt pyproject.toml
python -m piptools compile --extra docs --strip-extras --no-header --output-file requirements-docs.txt pyproject.toml
```

Qt 锁定文件使用 `--no-deps` 安装。ok-script 3 使用 `ok-pyside-minimal`，
无需手动删除 PySide6 依赖。`requirements-web.txt` 保持引用 `requirements.txt`。
