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
- 移除了 CNB 与独立更新仓库的 `Sync Repositories` 步骤，构建不再需要 `CNB_TOKEN`、`OK_GH` 等模板 secrets。
- `pyappify-action` 固定 `version: v1.2.3`，避免上游更新导致构建结果漂移；同时开启 `add_version_name: true`，安装包文件名带发布版本号。
- 声明 `permissions: contents: write`，否则默认只读的 `GITHUB_TOKEN` 无法创建 Release（报 403 `Resource not accessible by integration`）。
- 产物共 4 个：两个 profile 安装包（`China`/`Global`，名称带版本号如 `ok-script-app-pro-win32-China-setup-1.1.3.exe`）、`ok-script-app-pro-win32-online-setup-<版本>.exe`（在线安装包）与 `ok-script-app-pro-win32.zip`（免安装压缩包）。ok-script 3 移除了 Web 前端，`pyappify.yml` 不再有 `Web` profile。
- 更新日志使用本次 tag 的简要说明，不再依赖同步步骤的输出。

构建约需 20 分钟，主要耗时在 PyAppify 的 Tauri/Rust 全量编译（约 18 分钟），GitHub Actions 未对该过程做缓存。如需加速，可以在 `pyappify-action` 步骤加 `use_release` 输入复用已构建的 launcher，但前提是本项目未改动图标与 `pyappify.yml`。

## 发布包的过滤机制（下游应用必读）

当前模板仓库**未启用**任何发布过滤：`build.yml` 没有 `Synchronize repositories` 步骤，`pyappify.yml` 的三个 profile 也直接指向源码仓库。这带来一个必须理解的事实——

### PyAppify 不读 `.gitignore`

PyAppify 用 libgit2 克隆指定 tag 的**完整 tree** 再打包，`.gitignore` 只影响 `git add`，不影响已提交内容的检出。其 Rust 源码中没有任何忽略规则处理逻辑，全链路唯一的排除项是 `__pycache__`。

因此**凡是入库的内容都会进安装包**，无论 `.gitignore` 怎么写。当前仓库这一侧还好（`ok_templates/` 已忽略），但下游应用一旦按[快速开始](getting-started.md)把 `ok_templates/` 入库，源图就会直接进入安装包。

### 过滤靠白名单，不靠忽略规则

`deploy.txt` 与 `.update_repo_gitignore` 是配套机制，但**只有接上 `partial-sync-repo` 才生效**：

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

### 下游应用的落地清单

成熟应用（如 ok-wuthering-waves）的做法可直接参照：

1. `.gitignore` 去掉 `ok_templates/`，把它入库；
2. `deploy.txt` 中**不写** `ok_templates`（同时确认 `tests`、`docs`、`.github` 也不在清单内）；
3. 新建独立更新仓库（如 `xxx-update`），配好 `CNB_TOKEN` / `OK_GH` 等 secrets；
4. `build.yml` 恢复 `Synchronize repositories` 步骤，`repos` 填更新库地址、`sync_list: 'deploy.txt'`、`gitignore_file: '.update_repo_gitignore'`；
5. `pyappify.yml` 各 profile 的 `git_url` 改为更新库地址；
6. `pyappify.yml` 加 `keep_releases: 30` 之类的上限，避免更新库历史无限膨胀。

**不要把截图放进 Git LFS。** PyAppify 走 libgit2，不执行 LFS smudge filter：CI 侧 `actions/checkout` 带 `lfs: true` 能取到真图，但用户侧克隆拿到的是约 130 字节的指针文本，`cv2.imread` 读不出来。故障只发生在终端用户机器上，构建期不报错。

### 体积参考

| 仓库 | 体积 | 说明 |
|---|---|---|
| 源码仓库 | 约 1.2 GB | 含 `ok_templates`（约 900 MB）、`tests`、`docs`、git 历史 |
| 更新仓库 | 约 145 MB | 白名单过滤后，仅运行所需内容 |

相差 8 倍以上，差距主要来自源图与测试素材。

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
