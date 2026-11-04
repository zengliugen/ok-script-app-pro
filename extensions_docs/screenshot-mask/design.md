# screenshot-mask 设计

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | screenshot-mask |
| 创建日期 | 2026-11-04 |
| 更新日期 | 2026-11-04 |
| 生成器 | devflow-guard/new |

## 背景

> 需求已确认，探索记录见 explore/screenshot-mask.md（含已排除方向与重新可行的前提）。

截图时要用黑色矩形盖住画面里的游戏 UID。上游把位置与尺寸写死在 `src/config.py` 的
`make_bottom_right_black`（右下角，宽 13% × 高 2.5%，无法关闭）。换游戏或换分辨率后，
UID 不在那块区域，遮罩就挡了空气，用户也没有任何调整入口。本能力提供可配置的遮罩：
开关、8 个锚点、宽高、偏移，并配一个可视化页签让用户直接在画面上拖。

## 要解决的问题

- **现象**：遮罩位置与尺寸写死，且无法关闭。用户只能改源码里的两个魔数。
- **不做的后果**：每个用户要么改源码（升级上游时被覆盖，改动反复丢失），要么关掉遮罩泄漏 UID。

## 候选方案

| 方案 | 做法 | 代价 | 前提 |
|---|---|---|---|
| A：`screenshot_processor` 覆写 + 自定义 `CustomTab` | 在能力包 `register(config)` 里把 `config['screenshot_processor']` 换成自己的处理器，同时把页签类登记进 `custom_tabs` | 无 | 两个 config 键在上游保持存在 |
| B：子类化 `OK` 覆写 `do_init` | 换成自定义 OK，自己接管整个装配 | 要改入口脚本（上游文件），且要复制上游装配逻辑 | 需要改动 `main.py` 等上游入口 |
| C：patch 截图层 | 直接改 `ok.core.screenshot` 的处理器调用点 | 依赖内部符号，升级即失效 | 无 config 通道时 |
| 不做 | 保持写死的右下角 13% × 2.5% | 用户无法适配自己的游戏 | 无 |

**否决理由与重新可行的前提**：

- B 被否决：路径 A 已能拿到全部所需能力（处理器注入 + 页签注册），没有理由上升到改入口脚本。
  若上游移除了 `custom_tabs` 或 `screenshot_processor`，B 才需要重新评估。
- C 被否决：`screenshot_processor` 是正式扩展点，patch 一个本可通过 config 替换的组件属规则明令禁止。
  仅当该扩展点被移除，且 B 也不可行时才考虑。

## 实现手段

- 选定哪一级：**路径 1（继承 + config 注册）**——`screenshot_processor` 是 config 回调注入，
  `custom_tabs` 是 config 类注册。两者都属「扩展点内的对象替换」，零改动。
- 若上升过级别：未上升。因此不写 `divergence.yaml` 条目。

## 模块划分

```text
extensions/capabilities/screenshot_mask/
├── __init__.py      装配契约：register(config) 注入处理器 + 登记页签
├── geometry.py      纯几何：锚点表、resolve_rect、apply_mask、values_from_rect
├── processor.py     screenshot_processor 工厂 + 旧配置键迁移
├── tab.py           CustomTab 页签：工具条 + PreviewCanvas
└── i18n/            页签名、按钮、配置键与说明的译文
```

**为什么拆 `geometry.py`**：几何换算是纯函数（只依赖 numpy），与 Qt 无关，可以在无 GUI 环境
完整测试；页签里的拖拽只是「把鼠标坐标换算成矩形再交给几何层」。混在一个文件里，
最需要精确验证的部分反而必须依赖 Qt 才能跑。

## 几何语义（关键判据）

坐标一律为帧像素，矩形用 `(x1, y1, x2, y2)`（右下开区间，与 numpy 切片一致）。

**锚点只决定遮罩贴哪条边/哪个角**：

| 锚点 | 水平 | 垂直 |
|---|---|---|
| 左上角 / 左边中点 / 左下角 | 贴左边（`x1 = 0`） | 见下行 |
| 右上角 / 右边中点 / 右下角 | 贴右边（`x2 = width`） | 见下行 |
| 上边中点 / 左上角 / 右上角 | — | 贴上边（`y1 = 0`） |
| 下边中点 / 左下角 / 右下角 | — | 贴下边（`y2 = height`） |
| 上/下边中点 | 水平居中 | — |
| 左/右边中点 | — | 垂直居中 |

**偏移方向**：正值 = 背离所贴边缘向画面内部移动。
贴左边时 `x1 += offset_x`；贴右边时 `x2 -= offset_x`；贴上边时 `y1 += offset_y`；
贴下边时 `y2 -= offset_y`。居中方向（上/下边中点的水平轴，左/右边中点的垂直轴）偏移同样
沿「从中心向对侧」算——取「贴左边」语义，即 `x1 = 居中起点 + offset_x`。

**单位**：固定百分比。宽高与偏移都按帧宽/帧高的比例解释，`13.0` 表示 13%。
换算在 `resolve_rect` 内一次完成，配置里只存一套值。

**换算必须用截断，不能用四舍五入**：上游写的是 `int(0.13 * width)`。1920 宽下
`round` 得 250、`int` 得 249，遮罩左边差 1 像素——边缘可见细缝，且只在部分分辨率出现。
反方向（像素 → 百分比）取「能被截断还原」的区间中点，保证「拖到哪、存到哪」逐像素成立。

**越界**：结果按 `[0, width] × [0, height]` 裁剪；裁剪后任一边长为 0 时返回 `None`
（完全在画面外不绘制，不退化成一条线）。

## 预览与缩放

预览用**未经遮罩处理的原始帧**——预览若也走一遍遮罩，那块区域本来就是黑的，调整时看不出
边界落在哪。

缩放与平移的边界规则：

| 情形 | 规则 |
|---|---|
| 任意画面尺寸 | 可拖范围 = ``[视口尺寸 - 画面尺寸 - 60, 60]``，即**任一侧空白都不超过 60px** |
| 画面宽度 < 视口 - 120 | 判据无解（两侧空白必然超标），固定居中，两侧对称 |
| 适应窗口的比例 | 按完整控件尺寸算，画面贴住边缘（留白 0） |

**为什么只有一条判据**：曾按「画面是否比视口大」分两支——大画面限「视口内间隔」、
小画面限「可见部分」。实测 800px 视口下，800px 的画面能拖出 740px 空白，1280px 的画面
却只能拖 60px，画面越大反而越不能动。两套语义把「空白」和「可见」混为一谈，可拖幅度
相差一个数量级。统一为「单侧空白 ≤60」后，任何缩放下的手感一致。

区间倒置时（画面太窄，两侧空白无论怎么摆都超标）固定居中——此时追求的是左右对称，
避免「这次能拖一点、下次完全拖不动」的漂移。

**为什么适应窗口不留白**：它的语义是「尽量看清整幅画面」，留白纯属白缩一圈。贴边带来的
两个顾虑都有其他机制兜着——贴边手柄只在拖拽/缩放时才可能被裁，而那时留白是 60px；
适应窗口本身就是整幅画面在视口里，不需要靠留白判断边界。

**另一条轴的间隔不是留白**：1280×720 的画面放进 800×480 的控件，宽度撑满后上下各留 15px，
那是宽高比不同导致的黑边，任何等比缩放都会有。

**为什么平移留白是 60px 且不随状态变化**：画面边缘压在控件边界上时，看起来像被裁了一块，
分不清「画面到此为止」还是「还有内容在视野外」；贴边的遮罩手柄也会跟着切进边界、抓不到。
60px 足够让底色成为清晰参照。留白是**平移边界的属性**、不是交互阶段的属性——曾在拖拽期用
60、松手后收回 12，结果松手时必须重算偏移，画面当场弹回，拖到 60 的结果作废。

**松手不重算**：拖到哪儿就停在哪儿。`zoom_fit` / `zoom_reset` 只重新取景，也不改留白——
否则用户按一下按钮后就再也拖不到 60px。

**「适应窗口」是一次性计算，没有状态**：`zoom_fit` 把当前控件尺寸对应的比例与居中偏移
写入 `_scale` / `_offset`，算完就忘。之后控件缩放、用户拖动都不会因为「还在适应模式」而重算；
想重新取景再点一次即可。

这比「保留 is_fit 布尔量 + 按下时解除」更简单也更正确：后者会带来两个真实故障——
按下的瞬间比例基准从「现场推算值」突变成「字段默认值」（画面跳变一次），以及标签只能显示
「Fit」而无法显示已经算出的具体比例。

**比例标签永远显示百分比**：刚点完「适应窗口」时画面已按当前控件算出确定比例（如 31%），
显示「Fit」等于把已知数字藏起来，用户还会以为拖拽时比例会跟着变。

光标同样按「是否载入了画面」判断，而不是按某个模式标志：只要画面存在就显示抓手，
因为画面比视口小时也能拖动挪开。

**任何缩放比例下都能拖动**：画面比视口大时是「平移看别处」，比视口小时是「挪开露出底色」，
两者都允许。光标只要载入画面就显示抓手。

唯一的限制是**任一侧空白不超过 60px**，画面太窄导致该判据无解时固定居中。

## 拖拽精度

位移一律按「当前位置 − 按下点」计算，基准在按下时固定一次，**不做增量累加**；矩形内部
保存浮点，只在 `mask_rect()` 对外暴露时取整。

**为什么不能增量累加**：屏幕位移 ÷ 比例往往带小数，逐步取整会把零头一笔笔丢掉。

| 缩放 | 1px 折算帧位移 | 增量累加的表现 |
|---|---|---|
| 200% | 0.5 | 每步取整为 0，**完全拖不动** |
| 25% | 4.0 | 矩形宽度非整数时每步丢零头，拖 100px 只走 95px |

**手柄命中的内部优先规则**：缩到 10% 时遮罩只有 30×10 像素，四个手柄加 4px 容差几乎盖满
整块区域。判据是「距四条边都超过一个手柄边长」时才算内部、走整体平移——不能用「在矩形内
即不算手柄」，因为手柄中心正落在边界上，那样会把手柄整条废掉。

## 按钮可用性

三个按钮都按「点了会不会有变化」决定是否可用，无事可做时灰化，避免用户点了没反应却以为失灵：

| 按钮 | 可用条件 |
|---|---|
| 保存配置 | 存在未保存的临时调整 |
| 重置为配置值 | 存在未保存的临时调整（没有可丢弃的东西时灰化） |
| 重置为默认值 | 已保存的几何与默认值不同 |
| 1:1 | 当前比例不等于 1.0 |

## 实测证据

```text
# 环境：Windows 10 / Python 3.12 (.venv) / ok-script 3.0.0b3

# 1. 确认 screenshot_processor 的调用点与签名（处理器接收帧副本，返回值被采用）
$ grep -n "screenshot_processor" .venv/Lib/site-packages/ok/core/screenshot.py
208:            processor = og.config.get('screenshot_processor')
210:            frame = processor(frame.copy())

# 2. 确认模板采集也走同一处理器（说明覆写对「模板采集」同样生效）
$ grep -n "screenshot_processor" .venv/Lib/site-packages/ok/ui/qt/tasks/TemplateTab.py
176:    if processor := og.config.get('screenshot_processor'):
177:        frame = processor(frame.copy())

# 3. 确认 custom_tabs 的装配与无参构造（页签类必须可无参实例化）
$ sed -n '125,133p' .venv/Lib/site-packages/ok/ui/qt/MainWindow.py
tab_obj = init_class_by_name(tab[0], tab[1])
tab_obj.executor = executor
if tab_obj.add_after_default_tabs: ...

# 4. 确认页签分区：自定义页签一律进 SECTION_GAME，模板页签进 SECTION_DEVELOP
$ grep -n "add_page(tab_obj\|add_page(self.template_tab" .venv/Lib/site-packages/ok/ui/qt/MainWindow.py
135:            self.add_page(tab_obj, ..., SECTION_GAME)
151:            self.add_page(tab_obj, ..., SECTION_GAME)
177:            self.add_page(self.template_tab, "image", self.tr('Templates'), SECTION_DEVELOP)

# 5. 确认 GlobalConfig 的可见配置枚举（设置页每项都渲染，无折叠概念）
$ grep -n "def get_all_visible_configs" -A 8 .venv/Lib/site-packages/ok/util/GlobalConfig.py
# 6. 确认配置文件是按 ConfigOption.name 命名的 json，旧文件名 Screenshot Mask.json 可继续复用
$ sed -n '38p' .venv/Lib/site-packages/ok/util/config.py
self.config_file = get_relative_path(folder, f"{name}.json")
```

## 结论

采用方案 A。处理器走 `screenshot_processor` 覆写，页签走 `custom_tabs` 注册，几何换算独立成
纯函数模块。页签保持在 Game 分区末位（见探索记录的决定 4）。

## 未解问题

- **设置卡片的展开/收起**：需求引用 `ConfigCard.py` / `GlobalConfigCard.py` 的折叠与重置按钮，
  但这两个文件在当前上游不存在。现行 `ConfigSection` 的重置按钮常驻，不需要折叠来保住它。
  实施按现状：配置项照常出现在设置页，不实现折叠。这是与需求文档 R3.2.1 的已知差异，
  写进实施偏差记录。
- **界面文案清单**（本地化范围，全部落 `ok_ext` 域，语言 `zh_CN` + `en_US`）：

  | 类别 | 串 |
  |---|---|
  | 页签名 | `Screenshot Mask` |
  | 配置项名 | `Screenshot Mask` |
  | 配置项说明 | `Draw a black rectangle over part of every screenshot.` |
  | 配置键 | `Enable Mask` |
  | 配置键说明 | `Draw the mask on screenshots.` |
  | 按钮 | `Capture Frame`、`Save Config`、`Reset To Saved`、`Reset To Default`、`Zoom In`、`Zoom Out`、`1:1`、`Fit Window` |
  | 开关文字 | `Enable Mask` |
  | 状态提示 | `Unsaved changes`、`No frame captured` |
  | 比例文案 | `Fit` |

  配置键本身必须是 ASCII 英文（体检硬规则），译文只影响界面标签。
