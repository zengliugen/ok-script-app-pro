# update-check-guard 设计

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 生成器 | devflow-guard/new |
| 关联能力 | update-check-guard |
| 创建日期 | 2026-10-26 |
| 更新日期 | 2026-10-26 |

## 背景

上游 `MainWindow.showEvent` 在首次显示后 30 秒自动调用 `pyappify.get_version_list()`，
About 页的「App update」卡片随即进入不可用的错误态。该调用要求能定位 PyAppify 启动器，
而源码方式运行（PyCharm 直接跑 `main_debug.py`）天然不具备这套部署结构。

## 要解决的问题

- 每次启动固定产生一次 `FileNotFoundError`，日志里约 30 行栈。
- About 页更新卡片长期显示红色「Failed to check for updates: ...」，用户无法区分
  「源码运行不支持」与「网络/服务异常」。
- 卡片本身在源码模式下**没有任何可用功能**：没有启动器就无法检查版本，也无法升级。

## 候选方案

| 方案 | 做法 | 代价 | 前提 |
|---|---|---|---|
| A | 设 `PYAPPIFY_EXECUTABLE` 指向真实启动器 | 需要用户手工装好 PyAppify 并配环境变量，与「源码调试」场景矛盾 | 本机存在启动器 |
| B | 设 `PYAPPIFY_PYTHON_TEST` 走 mock 版本列表 | 会伪造一份假版本数据，更新按钮点了会真去调启动器；且在非调试场景也生效 | 仅测试用，不适合日常调试 |
| C | 用 `on_show_main_window` 钩子在启动器缺失时摘掉卡片 | 依赖 About 页的控件层级（UpdateCard → frame → Card） | 上游保留该层级；`my_app` 可替换 |
| D | patch `pyappify.get_version_list` 让它返回空列表 | 改的是第三方库行为，升级即失效；且空列表仍会渲染卡片 | 函数签名不变 |
| 不做 | 忍受报错 | 每次启动一条 ERROR + 卡片永久红色 | — |

## 实测证据

1. `find_pyappify_executable()` 在本项目返回 None（与报错来源同函数）：

```bash
.venv/Scripts/python.exe -c "import pyappify; print(pyappify.find_pyappify_executable())"
# None
```

2. `on_show_main_window` 在 `MainWindow.show()` 之前被调用，因此摘卡片能阻止
   `showEvent` 里的 `_schedule_update_check()` 排队：

```python
# ok/__init__.py:433-438
if og.my_app:
    if hasattr(og.my_app, 'on_show_main_window'):
        og.my_app.on_show_main_window(self.main_window)
self.main_window.show()
```

3. `_schedule_update_check` 对 `about_tab.update_card is None` 直接返回，置空即等同于关闭：

```python
# ok/ui/qt/MainWindow.py:265-267
def _schedule_update_check(self):
    if self.about_tab.update_card is None:
        return
```

4. 实际启动验证（2026-10-26 21:47:39—22:12:09，约 24 分钟）：

```text
21:47:39,895 WARNING update_check_guard:未找到 PyAppify 启动器，已隐藏「App update」卡片
21:47:40,043 INFO    ok:app.exec()
```

整个过程 `PyAppify executable was not found` 出现 0 次，`schedule pyappify update check`
出现 0 次，日志总量 96 KB。

## 结论

选方案 C。理据：

- A 与「源码调试」的场景互斥，且要用户改环境；B 会伪造数据、影响非调试场景；
  D 属 patch 第三方库，触碰「可覆写即可 patch」的禁区。
- C 走路径 1（`my_app` 注册）＋路径 3（`on_show_main_window` 钩子），零侵入：
  它不修改上游文件，不改 pyappify 行为，只是在启动器确实不存在时**不展示一个用不了的控件**。
- 判据与故障来源共用 `find_pyappify_executable()`，因此不会出现「判断说能用、
  实际用不了」。启动器存在但检查失败（网络、鉴权）仍由上游照常报错并显示——这不是掩盖失败。

## 未解问题

- 控件层级（`UpdateCard.parent().parent()` 是带标题的 Card）属上游实现细节，
  上游重构 Card 装配方式会导致守卫在启动期崩溃。选择崩溃而非静默跳过，是为了让
  上游变更立刻可见；崩溃信息里指名了要改的文件。
- 摘除后 About 页少一张卡片，源码模式用户若想查更新需自行用发行版。
