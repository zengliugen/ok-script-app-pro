# 打包与发布

## 发布相关文件

- `.github/workflows/build.yml`：监听 `v*` tag，运行测试、同步更新文件、打包并创建 GitHub Release。
- `pyappify.yml`：定义应用名称、入口、图标、Python 版本和更新仓库。
- `pyproject.toml`：定义 Qt、Web 和文档依赖 profile。
- `requirements.txt`、`requirements-web.txt`：由 TOML profile 编译的安装锁定文件。
- `deploy.txt`：定义同步到独立更新仓库的文件。
- `.github/workflows/mirrorchyan_*.yml`：可选的 Mirror酱上传和更新日志工作流。

## 构建工作流现状

`.github/workflows/build.yml` 已按本项目调整：

- Git 身份改为 `zengliugen`。
- 移除了 CNB 与独立更新仓库的 `Sync Repositories` 步骤，构建不再需要 `CNB_TOKEN`、`OK_GH` 等模板 secrets。
- 安装包名称为 `ok-script-app-pro-win32-<Profile>-setup.exe`，Release 下载链接指向本项目。
- 更新日志使用本次 tag 的简要说明，不再依赖同步步骤的输出。

## Mirror酱

两个 MirrorChyan workflow 已保留，默认关闭：

- `.github/workflows/mirrorchyan_uploading.yml`
- `.github/workflows/mirrorchyan_release_note.yml`

启用方式：

1. 在仓库 **Settings → Secrets and variables → Actions** 中新建 Variables `MIRRORCHYAN_ENABLED`，值为 `true`。
2. 新建 Secrets `MirrorChyanUploadToken`。
3. 向 MirrorChyan 申请资源并替换 workflow 中的 `mirrorchyan_rid`。

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
python -m piptools compile --extra web --strip-extras --no-header --output-file requirements-web.txt pyproject.toml
python -m piptools compile --extra docs --strip-extras --no-header --output-file requirements-docs.txt pyproject.toml
```

Qt 锁定文件使用 `--no-deps` 安装。编译完成后，删除生成的 `pyside6` 和
`pyside6-addons` 条目，但保留 `pyside6-essentials`，避免 Fluent Widgets
重新引入未使用的完整 PySide6 组件。
