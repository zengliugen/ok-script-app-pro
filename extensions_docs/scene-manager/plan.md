# scene-manager 实施计划

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | scene-manager |
| 创建日期 | 2026-11-11 |
| 更新日期 | 2026-11-23 |
| 生成器 | devflow-guard/new |

## 目标

完成后系统具备：一套与业务解耦的场景库（`assets/scenes/`，一场景一目录）、四子页签界面
（配置 / 流转 / 草稿 / 测试，测试页仅 debug）、`goto_scene(name, timeout)` 稳定接口
（纯逻辑类 + 薄壳 Task）、BFS 自动探索器（白名单点击 + 稳定帧判达 + 草稿待确认）、
场景库导入导出。对应设计文档：`scene-manager/design.md`。

## 分批任务

按「先能存 → 再能认 → 再能走 → 再能探 → 最后做界面」推进。每批可独立验证，
不依赖后续批次。

| 批次 | 内容 | 涉及文件 | 验收标准 |
|---|---|---|---|
| 一 | 数据模型与场景库读写：场景/特征/区域/边数据类、JSON 序列化、一场景一目录、基准分辨率、`preview.png` 与 `templates/` 落盘、`preview.png` 缺失降级 | `model.py`、`store.py`、`extensions_tests/TestSceneManagerModel.py` | 存 → 读 → 再存，JSON 逐字段相等；删场景目录后再次加载不报错；`preview.png` 缺失时返回占位标记而非异常 |
| 二 | 分辨率归一化与坐标换算：帧缩放到基准分辨率、特征相对区域换算、基准坐标 → 点击坐标 | `normalize.py`、`extensions_tests/TestSceneManagerNormalize.py` | 1920×1080 与 3840×2160 下，同一相对区域的像素矩形满足 2 倍关系；坐标往返换算误差 ≤1px |
| 三 | 场景识别：必需全中 + 可选打分、特征短路顺序、当前场景缓存、疑似候选、特征与遮罩矩形求交检测 | `recognize.py`、`extensions_tests/TestSceneManagerRecognize.py` | 构造的两个场景互不误识别；仅改名/改色的帧判为不命中；相交检测对已知矩形对给出正确布尔与交集 |
| 四 | 流转图与寻路：networkx 建图、边来源过滤、最短路、布局坐标、不可达判定 | `graph.py`、`extensions_tests/TestSceneManagerGraph.py` | 三点链路 A→B→C 有路径、C→A 若无反向边则不可达；`explored` 边不参与寻路；空图与单节点图不崩 |
| 五 | 导航器：逐层跳转、结果对象、重试/回退/报错、独占执行器 | `navigate.py`、`extensions_tests/TestSceneManagerNavigate.py` | 用假 task 桩模拟点击与识别，A→C 走两步且结果对象含路径与每步耗时；识别连续失败达重试上限后返回失败而非抛异常 |
| 六 | 探索器：BFS、白名单过滤、稳定帧判达、两点无跳转判无效、差分提候选框、草稿产出 | `explore.py`、`extensions_tests/TestSceneManagerExplore.py` | 白名单外区域一次都不点；三点上限任一触发即停；差异占比 >30% 时不给候选框 |
| 七 | 任务薄壳与装配：`SceneGotoTask` / `SceneExploreTask`、`register(config)` 登记页签与任务、`visible=False` | `tasks.py`、`__init__.py`、`registry.py`（追加一行）、`pyproject.toml`（加 networkx） | 启动后任务列表不出现两个内部任务；`get_task_by_class` 能取到；`run_task_by_class` 能同步调用成功 |
| 八 | 配置子页签：实时截图 / 导入图片、框选区域、特征与区域表格、必需/可选标记、`>3 个必需特征` 提示、保存 | `tab.py`、`pages/config_page.py` | 新建一个场景后目录与三份文件齐全；框选写入相对比例；第 4 个必需特征出现提示但可保存 |
| 九 | 流转子页签：QGraphicsView 节点画布、拖动、点选详情、路径高亮、边表格、试走一步 | `pages/flow_page.py`、`flow_canvas.py` | 三场景两边的库能画出三节点两边；点节点显示其区域列表；选中起止点高亮最短路径 |
| 十 | 草稿子页签：草稿列表、逐条/批量确认、批量丢弃、确认后并入正式库 | `pages/drafts_page.py` | 确认一条草稿后它从草稿区消失且出现在正式库；丢弃不留残文件 |
| 十一 | 测试子页签（仅 debug）：实时识别结果与各场景得分、单步走一步、完整跳转 | `pages/debug_page.py` | `main_debug.py` 启动可见、`main.py` 启动不可见；三段各自输出可读结果 |
| 十二 | 导入导出与脱敏体检：单场景 / 整库 zip、导出前检查（基准图是否遮罩、模板与遮罩相交、OCR 长数字串） | `store.py`、`pages/flow_page.py` | 导出的 zip 能导入回来且数据一致；含相交特征的库导出时列出警告清单 |
| 十三 | 本地化：`i18n/zh_CN` 与 `en_US` 的 `ok_ext` catalog，覆盖页签名/按钮/表头/提示 | `i18n/**/*.po`、`i18n/**/*.mo` | `task_i18n_helper.py gate` 通过；`.po` 与 `.mo` 同步、无空 `msgstr` |

**批次依赖**：一 → 二 → 三 → 四 → 五 → 六 严格串行（后批用前批的产物）；
七依赖六；八依赖一至三；九依赖四；十依赖六与一；十一依赖三、五；十二依赖一；十三最后补。

## 文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `extensions/capabilities/scene_manager/__init__.py` | 新增 | 装配契约：登记页签与两个任务 |
| `extensions/capabilities/scene_manager/model.py` | 新增 | 数据类 + JSON 序列化，不导入 ok / PySide6 |
| `extensions/capabilities/scene_manager/store.py` | 新增 | 场景库读写、草稿区、导入导出 |
| `extensions/capabilities/scene_manager/normalize.py` | 新增 | 归一化与坐标换算，不导入 ok / PySide6 |
| `extensions/capabilities/scene_manager/recognize.py` | 新增 | 识别与打分、缓存、相交检测 |
| `extensions/capabilities/scene_manager/graph.py` | 新增 | networkx 建图与寻路 |
| `extensions/capabilities/scene_manager/navigate.py` | 新增 | `SceneNavigator` |
| `extensions/capabilities/scene_manager/explore.py` | 新增 | BFS 探索器 |
| `extensions/capabilities/scene_manager/tasks.py` | 新增 | 两个薄壳 Task |
| `extensions/capabilities/scene_manager/tab.py` | 新增 | 主页签 + 四子页签容器 |
| `extensions/capabilities/scene_manager/pages/config_page.py` | 新增 | 配置子页签 |
| `extensions/capabilities/scene_manager/pages/flow_page.py` | 新增 | 流转子页签 |
| `extensions/capabilities/scene_manager/pages/drafts_page.py` | 新增 | 草稿子页签 |
| `extensions/capabilities/scene_manager/pages/debug_page.py` | 新增 | 测试子页签（仅 debug） |
| `extensions/capabilities/scene_manager/flow_canvas.py` | 新增 | QGraphicsView 节点画布 |
| `extensions/capabilities/scene_manager/i18n/zh_CN/LC_MESSAGES/ok_ext.{po,mo}` | 新增 | 中文 catalog |
| `extensions/capabilities/scene_manager/i18n/en_US/LC_MESSAGES/ok_ext.{po,mo}` | 新增 | 英文 catalog |
| `extensions/capabilities/registry.py` | 修改 | `CAPABILITIES` 追加一行 |
| `pyproject.toml` | 修改 | `[project].dependencies` 加 `networkx` |
| `requirements.txt` | 修改 | 用 piptools 重新编译（不手改） |
| `extensions_tests/TestSceneManagerModel.py` 等 6 个 | 新增 | 按批次一至六 |
| `assets/scenes/` | 新增目录 | 空库启动；含 `.gitkeep` 与 `README.md` 说明格式 |
| `extensions_docs/README.md` | 修改 | 总览表已由 `new` 登记，状态随实施推进更新 |
| `.codebuddy.project/configs/upstream/divergence.yaml` | 修改 | `pyproject.toml` 条目的 reason 补充「新增 networkx 基础依赖」；新增 `requirements.txt` 条目 |
| `extensions/capabilities/scene_manager/shapes.py` | 新增 | 四种形状（矩形/圆/扇形/多边形）的几何、掩码生成与命中测试 |
| `extensions/capabilities/scene_manager/pages/scene_card.py` | 新增 | `SceneImageCard` 继承上游 `ImageCard`，改名与信号载荷 |
| `.gitignore` | 修改 | 加 `ok_scenes/`（编辑区，含未遮罩原始帧） |
| `extensions_tests/TestSceneManagerShapes.py` | 新增 | 形状几何、掩码覆盖率、掩码提升匹配精度 |

## 验收标准

- **功能**：
  1. 空库启动不报错，四个子页签可切换；测试子页签仅在 `main_debug.py` 下可见。
  2. 新建场景 → 截图/导入 → 框选特征与区域 → 保存 → 重启后数据仍在。
  3. 三个场景构成 A→B→C 链路时，`goto_scene('C')` 从 A 出发能走两步到达，返回的结果对象含
     实际路径与每步耗时。
  4. 同一库在另一分辨率下（如 1920×1080 采集、2560×1440 运行）仍能正确识别与点击。
  5. 探索在三点上限任一触发时停止，产出草稿场景与草稿边，不写入正式路由图。
  6. 导出的 zip 可导入回来，数据逐字段一致。
- **测试**：`extensions_tests/` 六个测试文件全通过，用例数写明在 `testing.md`；
  `run_tests.ps1` 两个根都跑（`tests/` 用于确认上游行为未被破坏）。
- **落位**：未改动框架拥有的文件。`pyproject.toml` / `requirements.txt` 属已登记的上游差异，
  本次只更新其内容并同步登记条目，不新增差异面。
- **体检**：`extension_capability_guard.py audit` 无「错误」档缺口；`devflow_guard.py audit`
  无错误。

### 批次十四、十五补充验收

| 项 | 判据 |
|---|---|
| 特征形状 | 四种形状的掩码覆盖率符合理论值：圆 π/4、扇形按张角、矩形 100% |
| 圆形不变形 | 1920×1080 下相对半径 0.1 的圆，包围盒必须是正方形（按帧宽折算） |
| 位移容忍 | 画面平移 1–8px 仍能命中（搜索区向外扩 8px） |
| 两区隔离 | 导出产物区不含原始帧；缺掩码的场景被跳过并给出原因 |
| 原始帧不落盘 | `ok_scenes/` 与 `assets/scenes/` 下都不存在 `raw.png` |
| 遮罩口径 | 取帧返回原始像素（遮罩只在落盘与展示时施加） |
| 卡片外观 | 与模板页签一致：缩略图 + 名称，无副文本行，选中态高亮 |
| 卡片交互 | 单击选中、双击开弹窗、选中后出现「删除」「编辑」 |
| 新建流程 | 截图/导入后自动建卡片并命名为「场景 N」，不弹窗 |

### 补充批次：探索未完成时重启目标（原 scene-target-restart，2026-11-19）

**目标**：探索任务在 Windows 目标异常中断时，能按用户配置自动重启目标并重跑一轮；
探索前也可选择自动启动目标。原独立立项的文档已并入本能力，代码与测试都在
`scene_manager` 内。

| 批次 | 内容 | 涉及文件 | 验收标准 |
|---|---|---|---|
| 一 | 目标探测 / 启动 / 重启：封装框架 `start_device`、`stop_hwnd`、`execute`、`do_refresh`，并在重启后轮询取帧与识别 | `runtime.py` | 单元测试用假设备记录调用顺序；Windows 目标缺失时返回可读原因 |
| 二 | 配置字段：`auto_launch`、`restart_on_incomplete`、`max_restarts`、`restart_wait` 写入 `LibraryMeta`，带版本迁移与范围校验 | `model.py`、`store.py` | 旧 meta 文件可读，字段往返一致，非法值回落默认 |
| 三 | 探索任务重试循环：`no_start_scene` 与异常触发；`completed/max/cancelled` 不触发；次数耗尽保留原停止原因 | `tasks.py` | 假探索器按脚本返回不同停止原因，断言重启次数与最终结果 |
| 四 | 探索页开关与数值控件，保存到 meta；翻译条目与 `.mo` | `explore_page.py`、i18n | 控件初值正确、修改即持久化；翻译门禁通过 |
| 五 | 验收：测试、文档、体检、提交 | `extensions_tests/`、`extensions_docs/scene-manager/` | 全量测试、ruff、翻译门禁、扩展体检通过 |

**原计划的验收标准**：`no_start_scene` 与取帧/点击异常在开关开启时触发一次重启并重跑；
`completed`、`max_scenes`、`max_seconds`、`max_clicks_per_scene`、`cancelled` 不重启；
开关关闭时行为与改动前完全一致。新增用例覆盖重试判定、次数上限、异常路径、meta 往返
与页面控件；场景管理全量测试通过。未改动框架文件，不改上游 `i18n/`；代码仍在
`scene_manager` 内。

## 实施进度

开工后逐批更新，**只记结论不记过程**（过程进偏差记录）。

| 批次 | 状态 | 完成日期 | 备注 |
|---|---|---|---|
| 一 | 已完成 | 2026-11-11 | — |
| 二 | 已完成 | 2026-11-11 | — |
| 三 | 已完成 | 2026-11-11 | — |
| 四 | 已完成 | 2026-11-11 | — |
| 五 | 已完成 | 2026-11-11 | — |
| 六 | 已完成 | 2026-11-11 | — |
| 七 | 已完成 | 2026-11-11 | 依赖锁定文件未能正式编译，见偏差 #3 与遗留问题 |
| 八 | 已完成 | 2026-11-11 | — |
| 九 | 已完成 | 2026-11-11 | — |
| 十 | 已完成 | 2026-11-11 | — |
| 十一 | 已完成 | 2026-11-11 | — |
| 十二 | 已完成 | 2026-11-11 | — |
| 十三 | 已完成 | 2026-11-11 | H1 翻译门禁命令改为按包校验，见偏差 #10 |
| 十四 | 待开始 | — | 特征改为形状 + 掩码；场景库拆编辑区与产物区 |
| 十五 | 待开始 | — | 配置页改卡片网格，卡片继承上游 ImageCard |
| 十六 | 已完成 | 2026-11-19 | 探索未完成时重启目标（原 scene-target-restart） |

## 计划外事项

实施中出现但未纳入原计划的内容，确认是否单独立项。

| 事项 | 是否立项 | 说明 |
|---|---|---|
| `.codebuddy.project/tools/verify_scene_manager.py` | 不立项 | 开发期装配验证脚本。构造真实页面与运行时对象，跑一次约 3 分钟，不适合进 CI，但每次改装配代码都该跑 |
| `.codebuddy.project/tools/gen_scene_manager_po.py` | 不立项 | 从源码抓界面文案生成 po。手写 76 条必然漏，而漏一条的表现是「某处显示英文原文」 |
| `.codebuddy.project/tools/i18n_gate_per_capability.py` | 不立项 | 把 H1 翻译门禁改为按能力包校验，属既有门禁的修正（见偏差 #10） |
| `extensions/tests/TestSceneManagerTasks.py` 中的遮罩矩形重实现 | 不立项 | 因「能力之间不得互相 import」而必须本地实现同一套几何（见偏差 #9），并用逐值比对用例防漂移 |
