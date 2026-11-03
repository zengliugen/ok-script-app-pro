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
