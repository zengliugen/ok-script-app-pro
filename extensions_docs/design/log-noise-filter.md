# log-noise-filter 设计

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 生成器 | project-extension-capability-guard/new |
| 关联能力 | log-noise-filter |
| 创建日期 | 2026-10-26 |
| 更新日期 | 2026-10-26 |

## 背景

上游 `ok.util.window.get_exe_by_hwnd` 会对每个可见顶层窗口调用 `process.cmdline()`。
遇到以其他账户运行的进程（典型是本机 `GameViewerService`，LocalSystem 身份），
`OpenProcess` 返回拒绝访问，`psutil` 抛 `AccessDenied`。上游已经 catch 该异常
并返回空 cmdline，功能不受影响，但日志仍按 ERROR 打印完整 traceback。

## 要解决的问题

- 每次调用产生约 40 行 traceback；调用方是 `HwndWindow` 的 0.2 秒轮询线程，
  于是同一异常每分钟重复数百次。
- 实测本次会话 `logs/ok-script.log` 已达 750 KB，其中同类异常 573 次；
  历史日志中该进程的异常累计上千条。
- ERROR 级别会同时进入 UI 日志面板，真正的错误被淹没；`traceback.format_exc()`
  在 5 Hz 频率下也是真实开销。

## 候选方案

| 方案 | 做法 | 代价 | 前提 |
|---|---|---|---|
| A | 以管理员身份运行应用，或用管理员权限停掉 `GameViewerService` | 每次启动都要提权；远程控制服务是用户主动安装的，不应被本应用强制关停 | 用户愿意改变运行环境 |
| B | 在 logging 层按消息特征折叠重复噪音 | 需要新增一个过滤器；错误信息只保留摘要，排查时需看文件名与进程名 | 噪音消息文本稳定；ok logger 实例可被取到 |
| C | patch `ok.util.window.get_exe_by_hwnd`，把 `logger.error` 改成 warning 并去掉 traceback | 直接绑定上游内部函数，升级即失效；且属于「可覆写即可 patch」的禁区判定 | 上游函数签名不变 |
| D | 向上游提 issue，等待库侧修复 | 无法解决当前版本的问题；节奏不受控 | 上游接受该改动 |
| 不做 | 忍受刷屏 | 日志与 UI 面板持续被污染 | — |

## 实测证据

1. 目标进程身份与权限差异：

```powershell
Get-CimInstance Win32_Service | Where-Object { $_.PathName -like '*GameViewer*' }
# Name: GameViewerService  State: Running  PathName: "D:\Program Files\GameViewer\GameViewerService.exe" --service

([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrator')
# False
```

2. 该进程存在一个 2560x1440 的可见顶层窗口，因此进入 `EnumWindows` 的遍历范围：

```text
(125770816, 7780, 'GameViewerPrivateWindowClass', 'GameViewerPrivateWindowName', (0, 0, 2560, 1440))
```

3. 日志中的指纹与频次：

```bash
grep -o "psutil.AccessDenied: (pid=[0-9]*, name='[^']*')" logs/ok-script.log | sort | uniq -c
#   573 psutil.AccessDenied: (pid=7780, name='GameViewerServer.exe')
```

4. 过滤器在真实启动链上生效（装好后连续打三条噪音 + 一条非噪音）：

```text
WARNING MainThread 已折叠重复噪音日志，特征「get_exe_by_hwnd process.cmdline() error」：capture:...
ERROR   MainThread capture:get_exe_by_hwnd process.exe() error NoneType: None
```

## 结论

选方案 B。理由：

- 方案 A 改动运行环境且不可由应用保证；方案 C 触碰 `methods.mdc` 中「可覆写即可 patch」的禁区
  （本次并不需要替换框架行为，只需收敛日志），且 patch 目标随升级失效；
  方案 D 无法解决当前版本。
- 方案 B 只挂 logging Filter，不改任何框架符号，也不改变业务返回值——上游的
  `get_exe_by_hwnd` 仍照常吞异常并返回空值，功能语义完全不变。
- 收敛策略保留可观测性：首次命中仍报告（降级为 WARNING 并去掉 traceback），
  冷却期满后带上期间折叠条数再报告，不会让问题彻底隐身。

## 未解问题

- 噪音特征以消息子串匹配，若上游改写该日志文案，过滤器会静默失效（表现为噪音回归，
  不会误删其他日志）。这是刻意选择：宁可漏过滤，不可错杀。
- 冷却窗口 300 秒为经验值：过短则日志仍偏多，过长则相隔较久的真实变化被折叠。
  该值在 `COOLDOWN_SECONDS` 集中声明，可按需调整。
