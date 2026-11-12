# 快速开始

## 1. 获取仓库

本项目从上游模板派生，已按扩展项目的需要完成初始化。直接 clone 本项目：

```bash
git clone https://github.com/zengliugen/ok-script-app-pro.git
cd ok-script-app-pro
```

仓库已配置两个远端：

```bash
git remote -v
# origin   https://github.com/zengliugen/ok-script-app-pro.git
# upstream https://github.com/ok-oldking/ok-script-app.git
```

项目在模板之上开发扩展能力。需要跟随上游更新时，从 `upstream` 拉取：

```bash
git fetch upstream
git merge upstream/master
```

合并后重新检查 `src/config.py`、`pyappify.yml` 与 `.github/workflows`，避免上游改动覆盖本项目的标识与发布配置。

## 2. 安装 Python 3.12 和项目依赖

安装 [Python 3.12.10](https://www.python.org/downloads/release/python-31210/)，然后在仓库目录中执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
$PypiIndex = "https://pypi.org/simple/"
python -m pip install --index-url $PypiIndex --upgrade pip
```

安装 Qt 桌面界面的依赖（ok-script `3.0.0b3`）：

```powershell
python -m pip install --index-url $PypiIndex --no-deps --upgrade -r requirements.txt
python main_debug.py
```

ok-script 3 已移除 Web 界面。原 Web 启动器、`web` extra 和
`requirements-web.txt` 保留为桌面应用的兼容入口。

锁定文件用于可重复安装，是日常开发的推荐方式。如果需要直接从
`pyproject.toml` 解析最新的兼容依赖，可以分别使用：

```powershell
python -m pip install --index-url $PypiIndex ".[qt]"
```

只需执行与目标对应的一条命令。官方 PyPI 的 pip 索引入口是
`https://pypi.org/simple/`，而不是网站首页 `https://pypi.org/`；使用后者会导致
`No matching distribution found`，即使该包实际存在。

通常不需要管理员权限。如果目标游戏以管理员权限运行，自动化程序也需要以相同权限启动，否则截图或输入可能无法生效。

## 3. 确认应用配置

项目标识已初始化为 `ok-script-app-pro`，三种运行目标（Windows、Android 模拟器、浏览器）的结构均保留。开发扩展能力时可以：

1. 按[应用配置](configuration.md)确认应用名称、运行目标、图标和更新仓库。
2. 按[任务开发](tasks.md)新增并注册扩展任务。
3. 启动 Debug 模式：

```powershell
python main_debug.py
```

4. 运行测试：

```powershell
python -m unittest tests.TestMain
```

5. 验证完成后，按[打包与发布](release.md)配置工作流并推送 tag。

## 4. 模板资源的处理（下游应用必读）

本节只对**基于本模板开发的下游真实应用**适用。当前模板仓库自身保持 `ok_templates/` 不入库，下游应用则必须把它纳入版本库。

### 两组目录，性质完全不同

| 目录 | 内容 | 可否再生 | 下游是否入库 |
|---|---|---|---|
| `ok_templates/` | 原始截图 + 手工标注 `coco_annotations.json` | **不可再生** | **必须入库** |
| `assets/templates/` | 由标注裁剪出的特征图（Split 模式产物） | 可由源图重新生成 | 建议入库 |

`ok_templates/` 是编辑区：在界面「模板」页签里从这张原始截图框选区域、命名特征、反复调整。`assets/templates/` 是产物区：点「保存」后生成的裁剪图，运行时由 `config['template_matching']['template_folder']` 加载。

### 为什么下游必须把 `ok_templates/` 入库

- **标注不可再生**：`coco_annotations.json` 里的每个类名和 bbox 都是人工框选的结果，丢失后只能重做。
- **截图不可再生**：`generate_templates` 需要读取原始截图才能裁剪，源图缺失时直接抛 `ValueError`（`ok/feature/FeatureSet.py`）。换一台机器或换一个协作者，没有源图就永远无法重新导出。
- **产物可以丢弃**：`assets/templates/` 里的 PNG 全部能由「源图 + 标注」重新生成，因此不入库只是麻烦，不会损失信息。

### 入库方法

删除 `.gitignore` 中的 `ok_templates/` 整行即可。

**不要**改成 `ok_templates/*` 再加 `!ok_templates/xxx` 反选——目录级忽略会让 git 停止遍历该目录，深层反选实测无效：

```gitignore
ok_templates/
!ok_templates/coco_annotations.json    # 无效：文件仍被忽略
```

### 入库后必须配置发布过滤

`ok_templates/` 入库是为了协作，不是为了分发。用户安装包里不需要原始截图，而且体积可观（成熟项目可达数百 MB）。

**关键事实：PyAppify 不读 `.gitignore`。** 它用 libgit2 克隆指定 tag 的完整 tree 再打包，`gitignore` 只影响 `git add`，不影响已提交内容的检出。其 Rust 源码中没有任何忽略规则处理逻辑，全链路唯一的排除项是 `__pycache__`。

所以下游应用发布前必须做两件事：

1. **建立独立更新仓库**，在 `build.yml` 用 `ok-oldking/partial-sync-repo` 按 `deploy.txt` 白名单同步；
2. **把 `pyappify.yml` 各 profile 的 `git_url` 指向更新库**，而不是源码仓库。

白名单是正向列举，`deploy.txt` 里没有 `ok_templates` 就天然被挡在包外。同时用 `.update_repo_gitignore` 兜住构建产物与日志。

**不要用 Git LFS 存放这些截图。** PyAppify 走 libgit2，不执行 LFS smudge filter：CI 侧 `lfs: true` 能取到真图，但用户侧克隆拿到的是约 130 字节的指针文本，`cv2.imread` 读不出来。这个故障只发生在终端用户机器上，构建期完全不报错。

详见[打包与发布](release.md)。
