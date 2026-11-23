# scene-manager 测试策略与验收记录

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | scene-manager |
| 创建日期 | 2026-11-11 |
| 更新日期 | 2026-11-23 |
| 生成器 | devflow-guard/new |

## 测试策略

本能力的核心风险不在界面，而在**数据换算与判定逻辑**。因此测试重心放在六个纯逻辑模块上，
它们都不导入 `ok` 与 PySide6，可在无 GUI、无设备的环境完整运行。

| 被测行为 | 为什么必须覆盖 | 对应用例 |
|---|---|---|
| 场景 JSON 往返一致（含嵌套的特征列表与区域列表） | 场景库是唯一持久化载体，字段漏序列化不会报错，只会表现为「保存后重开某项没了」 | `TestSceneManagerModel.test_roundtrip_full_scene` |
| 删场景目录后加载不抛异常 | 用户会手工删目录；抛异常会让整库不可用 | `test_load_skips_missing_dir` |
| `preview.png` 缺失时返回占位标记 | 预览不参与识别，缺它不该阻断 | `test_missing_preview_returns_placeholder` |
| 草稿区与正式区互不可见 | 草稿是未确认的探索产物，泄漏进识别会让导航走到未验证场景 | `test_drafts_not_loaded_as_scenes` |
| 分辨率归一化的像素矩形满足比例关系 | 换分辨率后整个库能否继续用，全看这一处 | `TestSceneManagerNormalize.test_scale_rect_1920_to_3840` |
| 基准坐标 → 点击坐标的往返误差 ≤1px | 累积误差会让点击落到按钮边缘外 | `test_click_coord_roundtrip_within_1px` |
| 特征相对区域换算与帧尺寸无关 | 相对比例是跨分辨率的唯一依据 | `test_feature_region_is_resolution_independent` |
| 必需特征全中才命中、缺一不中 | 这是识别的唯一硬判据 | `TestSceneManagerRecognize.test_requires_all_mandatory_hits` |
| 相似场景不互相误识别 | 误识别会让导航从错误的起点寻路，比「识别不到」更糟 | `test_similar_scenes_not_confused` |
| 特征与遮罩矩形的相交检测 | 相交会裁出纯色模板，静默失效；必须能提前发现 | `test_feature_mask_overlap_detected` |
| 全不匹配时返回疑似候选与分值 | 用于区分「不在任何场景」与「在过渡动画中」 | `test_suspicious_candidates_when_no_match` |
| 三点链路 A→B→C 有路径、反向不可达 | 寻路的正确性基线 | `TestSceneManagerGraph.test_three_node_path` |
| `explored` 边不参与寻路 | 未确认的探索边若参与寻路，一次误判会让后续每次都点错 | `test_explored_edges_excluded_from_routing` |
| 空图、单节点图、起止相同不崩 | 新库、单场景库是真实初始状态 | `test_empty_and_trivial_graphs` |
| 导航走两步到达且结果含路径与每步耗时 | 接口契约的核心 | `TestSceneManagerNavigate.test_two_step_navigation_result` |
| 识别连续失败达上限返回失败而非抛异常 | 任务流程要靠返回值分支，异常会整条中断 | `test_returns_failure_not_exception_after_retries` |
| 白名单外区域一次都不点 | 误点可能花资源、进战斗，不可逆 | `TestSceneManagerExplore.test_whitelist_respected` |
| 三点上限任一触发即停 | 状态空间可能无限，无上限则永不结束 | `test_stops_on_any_limit` |
| 差异占比 >30% 不给候选框 | 动态背景的差分是噪声，给错误候选框比不给更糟 | `test_no_candidate_box_on_noisy_diff` |
| 连续两次无跳转才判无效区域 | 一次可能是冷却，一次即判会永久误杀 | `test_region_invalid_after_two_no_jump` |
| **点进新场景后能回退到父场景继续探其余区域** | 不会回退则多级图上 BFS 只探一条直线，需求「自动构建流转图」不成立 | `test_returns_to_parent_to_explore_remaining_areas`、`test_waits_for_returned_scene_before_clicking` |
| 回退优先用场景配置的返回区域 | 配了却不用，会让用户以为配置无效 | `test_uses_scene_return_area_when_configured` |
| 回退失败时不得在错误画面上点击 | 在别的画面上点本场景的区域是**真实误点**，可能花资源或进战斗 | `test_does_not_click_when_rollback_fails` |
| 回退过程受总时长上限约束 | 返回区域可能互相指向形成环形回退，无上限会一直点下去 | `test_rollback_respects_max_seconds` |
| `no_start_scene` 触发重启并重跑 | 这是「未完成」的主判据，错误地不重启等于功能不存在 | `TestExploreRestart.test_no_start_restarts_and_retries` |
| 正常完成、达到上限、用户取消不重启 | 把正常截止误判成故障会白杀目标，是破坏性副作用 | `test_completed_does_not_restart`、`test_limit_reasons_do_not_restart` |
| 开关关闭时行为与改动前一致 | 默认关闭是安全前提，关闭后不能有任何额外调用 | `test_restart_disabled_keeps_old_behavior`、`test_exception_raises_when_disabled` |
| 取帧/点击异常按未完成处理 | 目标崩溃最常表现为异常，而不是返回 `no_start_scene` | `test_exception_restarts_when_enabled` |
| 自动启动只在第一轮调用 | 重试时重复拉起会与重启流程叠加，造成两次启动 | `test_auto_launch_called_once` |
| 重启调用顺序：停窗口 → 兜底杀进程 → 启动 → 刷新设备 → 等取帧 | 顺序错了会启动到旧进程或等不到新窗口 | `TestTargetRestart.test_restart_target_uses_framework_calls` |
| ADB 明确不支持 | 误杀模拟器会影响多开与串口 | `TestTargetRestart.test_target_status_rejects_adb` |
| 四个配置字段随库往返、越界值夹取 | 配置写坏会让重启次数失控或等待为 0 | `TestLibraryMeta` 的 restart 两项 |
| 探索页控件初值与保存 | 控件不落盘等于用户改了没生效 | `test_explore_page_restart_settings_roundtrip` |

**刻意不测的行为**：

| 行为 | 不测的原因 |
|---|---|
| 真实游戏下的端到端跳转 | 依赖真实设备与具体游戏，CI 无此条件。改用假 task 桩验证逻辑，真机验证在 `testing.md` 的验收记录里人工登记 |
| QGraphicsView 的渲染像素 | 依赖图形环境与平台字体，且渲染外观不是正确性问题 |
| 页面外观与控件位置 | 属审美判断；但**滚动边界**是行为契约，另有用例守（外层无滚动范围、仅卡片列表可滚、固定部分不随列表滚动） |
| `acquire_exclusive` 与任务队列的真实交互 | 需完整 OK 运行时；该行为由框架提供，本能力只调用。已列入设计文档「未解问题」，实施时人工验证 |
| 探索在真实游戏中的边采集质量 | 取决于具体游戏的区域设计，无法用固定用例断言 |
| 探索回退在真实游戏上的成功率 | 取决于每个场景是否配了可靠的返回区域；桩世界能证明「逻辑会回退」，不能证明「这个游戏的返回按钮确实有效」 |
| 真实 Windows 目标被杀掉并重启 | 依赖真实窗口与游戏进程；单测用 Mock 验证调用顺序，真机验收见已知盲区 |
| ADB 模拟器重启 | 本能力明确不支持 |
| 目标重启后经过登录 / 公告画面 | 属于目标自身的流程，无法在单测中稳定复现 |

## 验收记录

| 日期 | 测试根 | 用例数 | 结论 | 环境 |
|---|---|---|---|---|
| 2026-11-12 | `extensions_tests/`（自有代码，场景管理 9 个文件） | 310 | 通过 | Windows 10 / Python 3.12 (.venv) / ok-script 3.0.0b3 / PySide6 6.12.0 / networkx 3.7 |
| 2026-11-12 | `extensions_tests/TestExtensionCatalogs.py`（catalog 源串语言与完整性） | 5 | 通过 | 同上 |
| 2026-11-12 | `.codebuddy.project/tools/i18n_gate_per_capability.py`（翻译门禁） | 2 个能力包 | 通过 | 同上 |
| 2026-11-11 | `.codebuddy.project/tools/verify_scene_manager.py`（装配验证） | 7 项 | 通过 | 同上，off-screen Qt |
| 2026-11-18 | `extensions_tests/`（Graph / Tasks / Explore 三个文件，含探索边落库修复） | 93 | 通过 | Windows 10 / Python 3.12 (.venv) |
| 2026-11-18 | `extensions_tests/`（场景管理 25 个文件 + catalog 检查，含拆页、DFS 与轮询计时） | 806 | 通过 | 同上 |
| 2026-11-19 | `extensions_tests/`（探索目标重启：重试判定、调用顺序、meta 往返与页面控件） | 858 | 通过 | Windows 10 / Python 3.12 / ok-script 3.0.0b3 |
| 2026-11-23 | `extensions_tests/TestSceneManagerEditor.py`（含页签滚动边界 4 项）+ `TestSceneManagerToasts.py` | 187 + 77 | 通过 | Windows 10 / Python 3.12 (.venv) / PySide6 6.12.0（off-screen Qt） |

**装配验证的 7 项**（脚本输出原文摘要）：

```text
[1] 装配注册：任务 2 / 页签 1
[2] SceneGotoTask 实例化 OK，name=Scene Goto，visible=False
[2] SceneExploreTask 实例化 OK，name=Scene Explore，visible=False
[3] 页签构造 OK，子页签 ['config', 'flow', 'drafts']
[3] 普通模式不含测试页：True
[4] 新建场景 scene-0001
[4] 配置页下拉项数 1
[5] 流转页节点数 1
[5] 草稿页表格行数 0
[6] debug 模式子页签 ['config', 'flow', 'drafts', 'debug']
[6] 空库识别正确抛 RecognizeError
全部通过
```

**用例分布**（`extensions_tests/`，场景管理 310 个 + catalog 检查 5 个）：

| 测试文件 | 用例数 | 覆盖 |
|---|---|---|
| `TestSceneManagerModel.py` | 43 | 数据模型、JSON 往返、场景库读写、草稿隔离、导入导出、**区域不带动作字段**、**编辑区→产物区导出（含缺掩码跳过、残留清理、不写原始帧）**、**缺 shape 字段报错**、掩码落盘 |
| `TestSceneManagerShapes.py` | 30 | **四种形状的几何与校验、圆半径按帧宽折算、掩码覆盖率（圆 π/4、扇形按张角）、命中测试、序列化只写生效字段、掩码提升匹配精度、搜索区扩边容忍位移** |
| `TestSceneManagerNormalize.py` | 29 | 宽高比判定、坐标换算、截断口径、相交检测、帧稳定判据 |
| `TestSceneManagerRecognize.py` | 31 | 必需全中、可选打分、短路、缓存优先、疑似候选、遮罩相交、零方差模板拒绝 |
| `TestSceneManagerGraph.py` | 28 | 建图、边来源过滤、寻路、回退、布局稳定性 |
| `TestSceneManagerNavigate.py` | 23 | 重试、多步、失败返回、回退、中止、辅助函数 |
| `TestSceneManagerExplore.py` | 34 | 探测白名单、三重上限、中止、跳转判定、候选框退化、稳定等待、**多级图回退** |
| `TestSceneManagerEditor.py` | 162 | 画布坐标换算与手柄、双击（命中/空白/预览/画新框模式）、弹窗属性对话框双入口、右侧列表两行完整显示（有/无主题）、拖动写回形状、**改框后保留缩放与位置／换底图时重置**、**配置页卡片网格（生成、只显示名称、信号载荷、按钮随选中态显隐、搜索过滤、删除只删编辑区、命名递增不复用、截图新建不弹窗、空态）**、**画布对齐标注编辑器（悬停反馈、缩放下限夹适配、未选中可拖手柄、手柄优先、无平移留白）** |
| `TestSceneManagerTasks.py` | 32 | 装配注册、幂等、任务契约、**`activate_area` 默认单击与 `area_center` 只算坐标**、遮罩矩形与遮罩能力逐值一致、**取帧返回原始像素（遮罩只在落盘时加）、apply_global_mask 不改原帧、设备缺失报错** |
| `TestExtensionCatalogs.py` | 5 | 仓库内 catalog：`msgid` 全英文、无空 `msgstr`、`en_US` 译文即源串、同文件内不重复 |

> **必须写明依据的是哪个测试根。** 框架基线与自有代码两类测试的结论不可互换：
> 自有代码全绿不能证明框架行为完好，框架基线全绿也不能证明自有代码正确。
>
> 本能力的用例全部落 `extensions_tests/`（自有代码）；`tests/`（上游基线）只在
> 同步上游后用于确认框架行为未被破坏，与本次实现无关。

**用例数必须填**：测试因缺少依赖而整类跳过时，框架往往仍返回成功——全部跳过与全部通过，退出码可能相同。

## 未做真机验证的阻断原因

本次交付**没有**完成真机端到端验证，原因不是省略，而是当前环境不具备条件：

```text
# 检查设备可用性（.venv 解释器，off-screen Qt）
$ .venv/Scripts/python.exe -c "... OK(cfg); print(ok.device_manager.capture_method)"
device_manager: DeviceManager
capture_method: None
```

`capture_method` 为 `None` 表示没有匹配到任何游戏窗口。本项目是**模板项目**，
`src/config.py` 的 `windows.exe` 被注释掉（上游默认形态），因此没有可截图的窗口。
要完成真机验证需要：

1. 在 `src/config.py` 的 `windows` 段填入目标游戏的 `exe`（或接模拟器配 `adb`）；
2. 用 `main_debug.py` 启动，进入「场景管理 → 测试」子页签；
3. 依次跑三段（实时识别 / 单步试走 / 完整跳转），把结论填进下面的表格。

已做的前置改进：测试子页签在取不到帧时会明确提示「请确认已连接设备，且
`src/config.py` 的 windows/adb 配置能匹配到游戏窗口」，而不是笼统报「取不到画面」
——否则使用者会以为是识别功能本身坏了。

## 真机验收记录（待填）

| 日期 | 游戏 | 分辨率 | 场景数 | 实时识别 | 单步试走 | 完整跳转 | 结论 |
|---|---|---|---|---|---|---|---|
| —— | —— | —— | —— | 待测 | 待测 | 待测 | 待测 |

## 已知盲区

| 盲区 | 影响面 | 缓解手段 |
|---|---|---|
| 无真机端到端验证 | 识别阈值、点击坐标、稳定帧判据、探索回退在真实游戏上的表现未经校验，可能整库在实机上命中率低 | 测试子页签提供「实时识别结果 + 各场景得分」；**阻断原因见下** |
| 探索质量无自动化断言 | 探索出的边是否正确只能人工看草稿列表判断 | 草稿必须人工确认才进正式图；探索报告列出每步的来源与耗时供核对 |
| 多分辨率兼容仅在两个尺寸上验证 | 极端宽高比（如 21:9）或带黑边的窗口未覆盖 | 归一化用相对比例 + 宽高比校验（容差 1%，同框架 `check_frame_and_resolution`），不匹配时明确拒绝而非猜测 |
| 遮罩相交检测依赖遮罩配置可读 | 若用户改了遮罩锚点/尺寸而未重新保存场景，相交判断用的是最新遮罩配置，可能与此前裁剪模板时不同 | 相交检测在每次保存特征时实时计算，不只存结果 |
| 场景数量到百级后的性能与仓库体积未验证 | 识别耗时随场景数线性增长（实测 9ms/场景），仓库因 PNG 体积增长 | 有缓存 + 短路；体积问题记在设计文档「未解问题」，场景数上百后再评估压缩或外置 |
| 未在真实窗口上验证「杀进程 → 重启 → 恢复取帧」全链路 | 目标启动慢于 `restart_wait` 时会报「未恢复取帧」，但不会继续无限等 | 默认 8 秒可调；日志记录具体原因 |
| 目标重启后停在登录 / 公告画面 | 轮询一直识别不到场景，重试次数耗尽后保留 `no_start_scene` | 探索页横幅与日志会显示重启次数；需要用户处理登录 |
| `stop_hwnd()` 在窗口未绑定时无目标可杀 | 已用 `get_exe_path()` + `kill_exe()` 兜底，但兜底按 exe 路径杀全部同名进程 | 仅对 Windows 目标生效；日志记录兜底调用 |
