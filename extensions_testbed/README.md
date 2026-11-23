# 测试靶场程序（支持后台点击）

> **本目录是被测目标程序**，不是测试用例——文件命名不含 `test_` 前缀，
> 也不受 `extensions_tests/` 的测试收集影响。
>
> 本靶场派生自 `AutoOperation` 仓库的 `tests/testbed/`，**唯一的行为增强是
> 支持后台点击**（原版只能前台点击，见第 1.2 节）。其余画面、布局、日志格式
> 与原版一致，因此原版的配置与截图样本仍然适用。

## 1. 这是什么

一个**内容已知、可从零启动、可自证**的目标应用，供自动化流程验证编排、
画面标识、OCR 识别与模板匹配。它提供「机器可读的自证能力」——使「截图拍到了
什么」「点击有没有到达」变成可读数的事实，而非靠推断。

### 1.1 与上游靶场的差异

| 维度 | 上游靶场 | 本靶场 |
|---|---|---|
| 前台点击（Pynput / PyDirect） | 有效 | 有效 |
| 后台点击（PostMessage 投递） | **无效** | **有效** |
| 窗口标题 | `AutoOperation 测试靶场` | `ok-script-app-pro 测试靶场` |
| 单实例互斥量 | `AutoOperationTestbed` | `OkScriptAppProTestbed`（可与上游同时运行） |
| 包名 | `testbed` | `extensions_testbed` |

### 1.2 为什么需要「支持后台点击」

上游靶场用的是 Tkinter，而 **Tk 只处理进入系统输入队列的真实输入**：
`PostMessage` 投递到窗口的 `WM_LBUTTONDOWN` 会进窗口过程，但**不会被翻译成
`<Button-1>` 事件**。于是后台点击在它上面完全失效——不是注入端有问题，而是
目标程序不认这条路径。原靶场因此把输入模式固定为 `foreground`，后台点击根本
无法验证。

本靶场在窗口过程里把「Tk 丢弃的点击」补回来，使前台与后台两种输入**行为等价**，
后台点击这才具备可验证性。

### 1.3 兜接机制怎么保证「不重复触发」

最危险的失效是**点一次触发两次**：真实输入既进窗口过程、又被 Tk 转成事件，
若两处都处理，画面会跳两格。区分依据只能是「Tk 有没有处理它」：

| 输入方式 | 进窗口过程 | 产生 Tk 事件 | 应兜接 |
|---|---|---|---|
| `PostMessage` 投递 | 是 | **否** | 是 |
| pynput 真实输入 | 是 | **是** | 否 |

`GetMessageExtraInfo()` 对两者都是 0（本机同进程实测），**不能**用作判据。

因此采用**认领制**（`click_claim.ClickClaimer`）：

1. 窗口过程收到 `WM_LBUTTONDOWN` → 记一条「待定」记录（只写数据，不碰界面）；
2. Tk 的 `<Button-1>` 回调触发时 → 认领对应记录（证明这条点击已被 Tk 处理）；
3. 下一帧核对：仍未被认领的，才是 Tk 丢弃的投递点击 → 走与 Tk 相同的入口补一次。

两条硬约束（都是实测得到的，违反任一条会出问题）：

- **窗口过程里不能碰 Tk**。它可能在任意线程被回调，而 Tk 不是线程安全的；
  实测在回调里调 `root.after_idle` 会让解释器在 `PyEval_RestoreThread` 处崩溃。
- **兜接必须同进程**。`SetWindowSubclass` 不能跨进程（外部进程调用返回 0），
  所以兜接逻辑只能长在靶场自己身上。

### 1.4 画面图（8 个画面，5 层 + 1 个覆盖层）

```
entry ─ start_btn ──► chapter_select ─ 双击图标 ─► chapter_detail ─► battle ─► settlement
  ▲  ├ settings_btn ► settings_menu ─ back ──┐        │ back             │ back      │
  │  ├ help_btn ───► help_page ──── back ────┤        └──────────────────┘           │
  │  └ exit_btn ───► （关闭靶场）             │                                       │
  └──────────────────────────────────────────┴───────────────────────────────────────┘
                            settlement.confirm_btn ─► chapter_select（★ 环路）

popup_notice：覆盖层，F9 弹出 / Esc 收回；不属于上面的导航图
```


**★ 环路**：`entry→chapter_select→chapter_detail→battle→settlement→chapter_select`
——供 M2 验证防打转护栏。

**关于 `popup_notice`**：它是**覆盖层**（`F9` 弹出），当前画面 id 不变，而是其内容被
盖住使判别集失效。适配层**刻意不为它提供画面定义**——它必须表现为「未识别画面」，
才能作为 M2 兜底恢复的素材。

**各画面在适配层的定义**：`apps/testbed/screens/zh/<screen_id>.yaml`，共 7 份
（不含 `popup_notice`，理由同上）。

## 2. 怎么跑

### 双击运行（手动测试最省事）

双击**项目根**的 `run.cmd`，在菜单里选 `3`（源码启动）或 `4`（单文件 EXE）：

```
1. 主应用          main.py
2. 调试模式        main_debug.py
3. 测试靶场        extensions_testbed
4. 测试靶场 EXE    dist\extensions_testbed.exe
0. 退出
```

也可跳过菜单直接启动：`run.cmd 3`（源码）、`run.cmd 4`（EXE）。

选 `4` 时如果 EXE 不存在，会先调用 `extensions_testbed/build_exe.py` 打包，
完成后再启动。打包产物在 `dist/extensions_testbed.exe`，中间文件在
`build/pyinstaller/`，两者都被根 `.gitignore` 忽略，不入库。

也可以直接运行 `run_testbed.py`（靶场专用的 Python 启动器）：

```bash
python run_testbed.py
```

两个入口都用仓库内 `.venv` 的 `pythonw.exe`（无控制台），找不到时明确报错
而不是静默回退到裸解释器——裸解释器常缺 Tk，排查方向会被带偏。

### 打包为单文件 EXE

`run.cmd` 的 `4` **每次启动前都会校验 EXE 是否与源码一致**（内容指纹，不看
修改时间）：缺失或源码有改动时自动重新打包，再启动；一致时直接启动。也可手动
执行：

```bash
.venv/Scripts/python.exe extensions_testbed/build_exe.py          # 打包
.venv/Scripts/python.exe extensions_testbed/build_exe.py --check  # 只校验
```

`--check` 的退出码：`0` 一致、`1` 过期、`2` 产物缺失；`run.cmd` 依赖这三个值的
大小顺序判断下一步。

- 产物：`dist/extensions_testbed.exe`（约 11MB，Tkinter 与标准库都在里面）。
- 构建指纹：`dist/extensions_testbed.build-stamp.json`（记录每个源文件的 sha256；
  与 EXE 同目录，EXE 被拷走时一起带走）。
- 中间文件：`build/pyinstaller/`。
- 三者都被根 `.gitignore` 的 `dist/`、`build/` 覆盖，**不提交**。
- 单文件 EXE 首次启动会先解压到临时目录，比源码启动慢一两秒。
- 日志落在 **EXE 同目录**的 `_runtime/events.jsonl`：onefile 解压出的临时目录
  会随进程删除，日志写进去等于丢失。
- **为什么按内容而不是 mtime**：`git checkout` / `merge` 会把所有文件 mtime 刷成
  当前时间，按时间比会把没改过的源码判成「更新」；指纹只认内容真的变了。
- 打包时若靶场正在运行，EXE 被占用无法覆盖——先关闭再走菜单 `4`。

### 命令行运行（开发调试）

```bash
# 从项目根目录，要看控制台输出时用 python.exe
.venv/Scripts/python.exe extensions_testbed/main.py
```

可选参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--log PATH` | `extensions_testbed/_runtime/events.jsonl` | 事件日志路径 |
| `--size WxH` | `1280x720` | 客户区尺寸 |
| `--lang zh\|en` | `zh` | 界面语言（`F10` 可运行时切换） |
| `--screen ID` | `entry` | 启动时进入的画面（见下） |

> **`--screen` 的用途**：靶场默认停在 `entry`，而 `make_templates.py` 需要「处于目标画面」
> 的截图才能裁出该画面的模板图。故裁图时按画面逐个启动：
> `.venv/Scripts/python.exe extensions_testbed/main.py --screen battle` 然后运行裁图工具。

### 与本项目的场景管理配合使用

靶场只负责**当被测目标**，绑定与操作由应用侧完成：

1. 启动靶场（`python run_testbed.py`）；
2. 应用内选设备：窗口标题为 `ok-script-app-pro 测试靶场`；
3. 交互方式按需选：**前台验证**用 Pynput / PyDirect；**后台验证**用 PostMessage。

后台点击时窗口**不需要**在前台，这是本靶场相对上游靶场的唯一行为增强。


### 裁模板图（开发工具）

画面元素的模板图必须**从实际截图裁出**，且坐标要与 `screens.py` 一致。手工框选既费
力又易脱节，故提供工具：

```bash
uv run python extensions_testbed/make_templates.py --screen entry
```

它会绑定**正在运行的**靶场窗口、抓一帧，再按 `screens.SCREENS[<画面>].elements`
的基准矩形裁出每个元素的 PNG，写入
`apps/testbed/assets/templates/zh/<画面>/`。

| 参数 | 说明 |
|---|---|
| `--screen <id>` | 必填；画面 id（须在 `screens.SCREENS` 中） |
| `--app` / `--lang` | 适配层 id 与语言目录，默认 `testbed` / `zh` |
| `--dry-run` | 只打印将写入的路径，不落盘 |

**前提**：靶场已在运行（本工具不负责启动它）。
**注意**：工具按程序侧的元素表产出模板，故**新增画面元素后要重跑**；反之若产出多于
配置引用的模板（如配置未写 `exit_btn`），那些就是孤儿资产，应补齐配置或删除。

### 单实例约束

程序用命名互斥量（`Global\OkScriptAppProTestbed`）保证**只有一个实例**。若已有实例
在运行，第二个实例会拒绝启动并以退出码 `2` 结束。

> 互斥量名与上游靶场**不同**（上游是 `Global\AutoOperationTestbed`），
> 因此两个靶场可以同时运行、互不干扰。

## 3. 窗口标题常量（**两处必须一致**）

程序内的标题常量定义在 `extensions_testbed/app.py` 的 `TITLE`：

```python
TITLE = "ok-script-app-pro 测试靶场"
```

应用侧绑定设备时按标题选择窗口，`title_regex` 必须匹配它：

```yaml
title_regex: "^ok-script-app-pro 测试靶场"
```

**两处互相指向**——改其一必须同步改另一处，否则表现为「未找到目标窗口」。
（`TkTopLevel` 类名由 Tk 决定，不需手写。）

## 4. 日志格式

JSON Lines，**每写一行即 flush**（可边跑边读）。字段命名沿用 M0 受控窗口基准，
唯一必要的改名是 `source: "unity"` → `"app"`（靶场无游戏引擎，对应概念是 Tk 的
事件绑定回调是否被触发；**语义等价而非同一 API，不可直接数值比较**）。

四类事件：

| `source` | 含义 |
|---|---|
| `ready` | 场景就绪，**含 `dynamic_regions` 动态区域清单**（坐标为实际值） |
| `wndproc` | 原始 Win32 消息（含**客户区坐标**）——能力 A |
| `app` | 应用层感知（Tk 回调）——能力 B |
| `heartbeat` | 每秒一条，含 `frame` / `fps` / `foreground` |
| `note` | 挂钩安装、快捷键、生命周期等辅助记录 |

**日志位置会显示在界面的自述区**（第二行）。

## 5. 窗口绑定为什么这样配（跳板）

靶场由 **Python 解释器**承载，而 venv 的 `python.exe` 是「**跳板**」：`Popen` 返回的
stub 进程与真正持有窗口的子进程不是同一个，且子进程映像 = **基础解释器**路径。故
`exe_path` 无法唯一区分（写 venv 路径永不匹配、写基础解释器路径匹配所有 Python 程序）。

解法是「弱 `exe_path` + 类名 + 标题」三者 AND，唯一性由后两者承担：

- **启动路径与绑定条件是两个独立字段**：`launch.exe_path` 写可移植的 venv 相对路径
  即可，不影响 `candidates.exe_path` 的匹配语义。
- 详细论证与实测证据见 `08-decisions.md` **D-67**；内核**不为 Python 特判**。

## 6. 消息挂钩挂在哪些窗口上（**易错点**）

Tk 在 Windows 上有三层窗口，且**鼠标与键盘消息投递给不同的子窗口**：

| 层 | 类名 | 收到的消息 |
|---|---|---|
| 顶层 | `TkTopLevel` | 仅 `WM_ACTIVATE` / `WM_MOUSEACTIVATE` 等窗口级消息 |
| 客户区容器 | `TkChild`（`root.winfo_id()`） | **键盘**消息（`WM_KEYDOWN`） |
| 画布控件 | `TkChild`（`canvas.winfo_id()`） | **鼠标**消息（`WM_LBUTTONDOWN` 等） |

因此程序**同时挂钩后两者**（见 `app.py` 的 `_hook_hwnds`）。若只挂其一，
表现为「原始消息计数只增一半」，会被误判为「消息未到达」——**那是挂钩位置选错，
不是目标行为**。

## 7. 快捷键

| 键 | 功能 | 默认 |
|---|---|---|
| `F1` | 开关动画区 | **关** |
| `F3` | 切换状态横幅底色（应用计数区） | — |
| `F4` | 色卡显隐 | 显示 |
| `F5` | 清空所有计数与文本 | — |
| `F6` | 自述区显隐 | 显示 |
| `F7` | 切到下一画面（有弹窗时不生效） | — |
| `F9` | 弹出未识别弹窗（有弹窗时不重复弹出） | — |
| `F10` | 切换语言 `zh` / `en` | `zh` |
| 其他可打印键 | 计入按键/字符计数并进入文本区 | — |
| **`Esc`** | **有弹窗时关闭弹窗；无弹窗时不做任何事**（避免测试中误触退出） | — |

快捷键**同时也是输入测试对象**，故功能键也照常计入 A / B 两类计数。

### 7.1 弹窗是**覆盖层**，不改变当前画面

`F9` 弹出的 `popup_notice` 叠加在当前画面**之上**，**当前画面 id 不变**（自述区仍显示
原先的画面名）。它盖住画面标题区与主体区，使该画面的判别集全部失效——这正是它作为
「未识别画面」素材的用途（M2 的兜底恢复验收）。

`Esc` 或有弹窗时点其「关闭」按钮即可收回；弹窗显示期间 `F7` 不生效（模态语义）。

### 7.2 画面按钮会**真的切换画面**

各画面的按钮带着真实动作（定义在 `screens.py` 的 `Element.action`），点击后靶场真的
跳到目标画面。这与适配层 `screens/zh/*.yaml` 的 `goto` 边是**同一条事实的两侧**：

- 配置侧（`goto`）是**给工具读的**路由声明；
- 程序侧（`Element.action`）是**被测目标的真实行为**。

两者不一致会让导航测试失真。`screens.actions()` 会汇总出映射，便于逐条核对。

**`chapter_select` 的关卡图标是双击进入**（单击刻意不动作）——若单击也切画面，
双击会被第一次单击提前吃掉，双击交互便无法成立。

### 7.3 灵敏度滑块**拖动时实时跟手**

`settings_menu.sensitivity_slider` 在按住左键拖动时按「按下时的数值 + 相对位移」
实时更新数值与手柄位置，松手不再跳一次；换算基于**固定锚点**，不会随移动事件
次数累加。数值没变时不重绘（普通鼠标移动仍然零开销），抬起时用同一锚点重算
最终值，保证画面与日志一致。

对自动化来说这意味着：一次 `drag` 过程中就能读到数值变化，不必等松手；
`slider_drag` 日志仍只在抬起时写一条，位移与最终值可直接对应。

### 7.4 关卡列表：固定顺序 + 竖向滚动

`chapter_select` 的关卡列表共 **20 个**，按 **1~20 从左到右、从上到下**排列
（3 列 7 行，末行 2 个）。位置**不再随机洗牌**：同一画面的历史截图可复现，
样本采集与回归对比不必先对齐位置；外观（色相、装饰环起始角）与尺寸抖动仍是
「每次进入随机」，模板匹配依旧无法预先适配。

网格可视 **5 行**，**平滑竖向滚动**（按像素连续滚动，不再是整行翻页）：滚轮、右侧
滚动条拖拽（按住滑块拖动；点在轨道直接跳转）、以及**在列表区域内按住拖动**三条
入口共用同一套像素偏移。列表画在视口子画布里，半行会被裁在视口内、不会溢出到标题
区。滚轮日志新增 `scrolled`（是否真的滚动了）与 `scroll_row`（当前行号，0 为顶部），
拖动抬起额外写一条 `scrollbar_drag` 或 `grid_drag`（含最终行号）——供自动化侧确认
「列表确实滚了」而不只是收到输入事件。

## 8. 界面布局与动态区域清单

`layout.py` 的 `REGIONS` 是**布局的唯一真值**：界面绘制由它驱动，`ready` 事件的
`dynamic_regions` 也由它生成（`as_dynamic_regions()`）——不是「清单尽量跟布局一致」，
而是「**布局由清单生成**」。

**9 个区域恰好铺满客户区**（面积和 = 客户区面积、无重叠无空隙），启动时自检
（`layout.check_tiled`）。缩放以共享分割点为单位，任意尺寸下仍严格铺满。

### 8.1 区域内部：标题带与内容区

区域内部的顶部 `HEADER_H`（20px）是**标题带**，其余是**内容区**：

```python
layout.rect_of("raw_counters")      # 整个区域
layout.content_rect("raw_counters") # 内容区（已扣掉标题带）
```

**内容一律画在 `content_rect()` 内**——画在区域矩形里会侵占标题带，与标题叠字。
`app.py` 的 `_content()` 即该函数的封装。

**画面标题区（`screen_header`）与画面主体区（`screen_body`）不设标题带**：它们承载的是
被识别的画面内容（模板匹配 / OCR 的 ROI），画区域标题会污染判据，使识别不稳。

### 8.2 启动自检（改布局或画面元素时的第一道防线）

| 自检 | 保证 | 触发时 |
|---|---|---|
| `layout.check_tiled()` | 9 区域恰好铺满客户区 | 启动即报错 |
| `layout.check_capacity()` | 各区域高度足以容纳「标题带 + 内容」 | 启动即报错 |
| `screens.check_elements_inside()` | 画面元素落在所属区域内、**两两不重叠**、**不压到平坦探测矩形**、**`battle` 动画区域在 `panel_appearance` 之内且不压探测矩形**、**弹窗覆盖矩形盖住画面标题区与主体区** | 启动即报错 |
| `l10n.check_keys()` + `screens.check_label_keys()` | 区域标题与画面元素/画面标题的文案键在中英文表中都存在 | 启动即报错 |

这些自检的价值是**把「叠字 / 内容被裁 / 元素越界 / 元素重叠 / 判据被污染 / 界面显示键名」
从依靠肉眼变为启动即报错**——T0-2 曾因缺少它们而出现 5 处文字重叠、2 处末行被裁、
7 个区域标题显示为 `header.xxx` 字面量，以及 `exit_btn` 越出主体区压到色卡上
（后者靠使用者截图才发现）。T0-4 又把 `exit_btn` 那类问题的两个新变体纳入校验：
**两个按钮完全重叠**、**`battle` 的进度条压住平坦探测矩形**（后者会让该画面的
`color` 判据读到进度条而非底色，属静默失败）。

**调整 `REGIONS` 的高度、增删画面元素或移动动画区域后，务必确认这些自检通过。**

### 8.3 画面元素的位置约定

画面元素（按钮等）的坐标**一律由所属区域算出，不写死绝对坐标**
（见 `screens._button_column` / `_body_probe`）。原因：区域高度会随其余区域的
容量需要而调整，写死坐标一旦超出容器下沿就会压进相邻区域。
`check_elements_inside()` 在启动时兜底校验。

## 9. 运行产物

`extensions_testbed/_runtime/`（日志、截图、pid）已被 `.gitignore` **整目录忽略**，
不进入版本库。可安全删除。
