# screenshot-mask 测试策略与验收记录

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | screenshot-mask |
| 创建日期 | 2026-11-04 |
| 更新日期 | 2026-11-04 |
| 生成器 | devflow-guard/new |

## 测试策略

| 被测行为 | 为什么必须覆盖 | 对应用例 |
|---|---|---|
| 默认几何与上游写死行为一致 | 升级后截图结果必须不变，否则用户的旧截图模板会失配 | `TestResolveRect.test_default_matches_upstream_behavior` |
| 默认几何在任意分辨率下逐像素等于上游 | 上游用 `int()` 截断；用 `round()` 会在 1920 宽下差 1 像素，边缘出现可见细缝 | `TestResolveRect.test_default_matches_upstream_on_any_resolution` |
| 8 锚点各自贴到正确边缘 | 锚点是用户唯一的位置语义，错一个方向就整块遮罩跑偏 | `TestResolveRect.test_all_anchors_place_mask_on_expected_side` |
| 偏移方向为「背离所贴边缘」 | 方向反了会把遮罩推出画面，而界面看不出原因 | `TestResolveRect.test_offset_moves_inward_from_anchored_edge`、`test_offset_from_top_left_moves_into_frame` |
| 越界裁剪与完全在画面外 | 裁剪算错会产生负宽高，numpy 切片静默返回空数组 | `test_negative_offset_clipped_to_frame`、`test_mask_outside_frame_returns_none`、`test_zero_frame_returns_none` |
| 矩形 → 配置值 → 矩形 逐像素还原 | 「拖到哪存到哪」的唯一机械判据；百分比四舍五入是主要风险 | `TestValuesFromRect.test_round_trip_all_anchors`、`test_round_trip_across_frame_sizes`、`test_dragged_rect_overrides_config` |
| 反解出的锚点与矩形实际位置一致 | 反解可被多个锚点满足，选错会得到「贴着对侧边界」的语义 | `test_anchor_changes_when_rect_moves_to_opposite_corner` |
| 只涂遮罩区域，灰度帧同样支持 | 涂错会连带毁掉需要识别的内容 | `TestApplyMask` 全组 |
| `apply_mask` 不判开关 | 两处判开关会让「关掉还遮罩」无从定位 | `TestApplyMask.test_disabled_flag_not_handled_here` |
| 开关关闭不涂黑、异常原帧返回、空帧透传 | 遮罩失败不得中断截图与模板采集 | `TestProcessor` 全组 |
| 每次调用重新读配置 | 否则「保存了但没生效」只能靠重启绕过 | `test_reads_config_every_call` |
| 旧中文键迁移且数值不丢 | 用户已调好的值丢失后只能重调 | `test_migrate_legacy_config`、`test_migrate_is_noop_on_new_config` |
| 配置项默认值与隐藏键 | 四个数值若在设置页可见，用户会面对「改数字看不到效果」的编辑方式 | `TestConfigOption` 全组 |
| 页签继承 `CustomTab` 且属性齐备 | 属性缺失只在真实启动时暴露 | `TestTabContract` 全组 |
| 8 手柄各只动自己那条边 | 首版曾一律只动右/下边，表现为「只能往右下变大」 | `TestPreviewHandles.test_each_handle_moves_its_own_edges` |
| 最小尺寸、越界、平移保尺寸 | 拖过对边会产生负宽高，越界会让遮罩跑出画面 | `test_minimum_size_enforced_on_shrink`、`test_resize_cannot_leave_frame`、`test_move_keeps_size_and_stays_inside` |
| 手柄画在矩形外侧且命中优先角 | 遮罩常很薄，居中手柄会叠成一团 | `test_hit_handle_prefers_corners`、`test_preview_keeps_padding_around_frame` |
| 缩放比例、锚点保持、坐标互逆 | 「放大后拖遮罩跑偏」的根因是比例改了但换算用旧值 | `TestCanvasZoom` 全组 |
| 逐像素拖拽的位移与鼠标一致（各缩放级别） | 增量累加会丢小数：200% 时完全拖不动、25% 时拖 100px 只走 95px | `test_small_step_drag_keeps_up_with_mouse`、`test_handle_resize_tracks_mouse_at_any_scale` |
| 遮罩内部深处走整体平移、贴边仍可抓手柄 | 缩小后手柄几乎盖满整块区域，不区分内部就无法平移 | `test_rect_interior_is_not_a_handle`、`test_handle_still_hit_at_rect_edge` |
| 平移极限时画面边缘不贴控件边界 | 贴边会让画面像被裁掉一块，贴边手柄也会切进边界抓不到 | `test_frame_edge_never_touches_canvas_edge`、`test_offset_clamped_when_frame_larger_than_canvas` |
| 平移留白 60px、适应窗口贴边（留白 0） | 平移要留大间隔才看得清边界；适应窗口要尽量看清整幅画面 | `test_padding_takes_effect_on_render`、`test_fit_aligns_to_canvas_edge`、`test_fit_scale_uses_full_canvas` |
| 松手后画面不弹回 | 留白若随状态收回，松手必然重算偏移，拖到极限的结果当场作废 | `test_release_keeps_pan_position` |
| 按钮重新取景不改平移留白 | 否则按过「适应窗口」就再也拖不到 60px | `test_buttons_reset_view_without_touching_padding` |
| 适应窗口后仍可拖拽 | 若把适应建模成状态，按钮一次点击就可能锁死后续平移 | `test_fit_does_not_lock_panning`、`test_one_to_one_can_pan` |
| 按下画面不改比例、不移动画面 | 早期版本按下时切模式再取比例，基准突变导致画面跳一次 | `test_press_does_not_change_scale`、`test_press_does_not_move_frame` |
| 适应窗口只算具体值、无持续状态 | 「Fit 显示 + 拖动时重算」既藏起已知比例，又让拖拽基准不稳 | `test_zoom_fit_sets_concrete_scale`、`test_zoom_fit_has_no_lasting_state` |
| 比例标签永远显示百分比 | 显示「Fit」会让用户以为拖拽时比例还会变 | `test_zoom_label_always_shows_percentage` |
| 光标只要有画面就显示抓手 | 画面小时也能挪开露底色，不该显示箭头让人以为拖不动 | `test_cursor_shows_grab_whenever_frame_loaded` |
| 单侧空白上限恒为 60px，与画面大小无关 | 按画面大小分两套语义会让可拖幅度相差一个数量级，同一画面换个缩放就「拖不动了」 | `test_single_side_blank_is_capped_for_any_frame_size`、`test_small_frame_can_be_dragged_aside`、`test_small_frame_drag_reveals_background` |
| 画面太窄时居中且两侧对称 | 两侧空白必然超标时无解，居中避免「这次能拖一点、下次拖不动」的漂移 | `test_tiny_frame_is_centered` |
| 拖动不写配置、保存才落盘、重置丢弃 | 编辑即落盘会让重置按钮失去意义 | `TestTabEditing.test_drag_does_not_write_config`、`test_save_writes_config`、`test_reset_discards_pending` |
| 三个按钮按「点了有无变化」灰化 | 可点却无反应会被当成失灵；按钮状态必须与实际可操作性一致 | `test_reset_to_saved_disabled_without_pending`、`test_reset_to_saved_disabled_after_save`、`test_reset_to_saved_disabled_after_drag_back_to_saved`、`test_reset_to_default_disabled_when_already_default` |
| 开关即时落盘且关闭时隐藏编辑框 | 开关不参与分离机制，关掉后不应留一个不可编辑的框 | `test_switch_writes_immediately`、`test_disabling_hides_edit_box`、`test_reenabling_restores_edit_box` |
| 「重置为默认值」回到上游几何且不动开关 | 这是「回到出厂设置」，与「撤销本次编辑」是两件事；顺手打开已关掉的遮罩会让人以为开关失灵 | `test_reset_to_default_restores_upstream_geometry`、`test_reset_to_default_keeps_enabled_switch`、`test_reset_to_default_disabled_when_already_default` |

**刻意不测的行为**：

| 行为 | 不测的原因 |
|---|---|
| 真实设备截图（`capture_frame`） | 依赖真实游戏窗口与采集后端，CI 与无头环境都拿不到；处理器入参已用构造帧覆盖 |
| 真实主窗口装配（`MainWindow` 里插入页签） | 需要完整 GUI 启动与会话；改测「按 `init_class_by_name` 实例化」这一条框架实际走的路径 |
| 译文是否被 gettext 正确加载 | 属 `extension-i18n` 能力的职责，本能力只保证 `.po`/`.mo` 同步，由 `task_i18n_helper.py gate` 校验 |
| 遮罩颜色与透明度 | 需求明确不做（只支持纯黑） |

## 验收记录

| 日期 | 测试根 | 用例数 | 结论 | 环境 |
|---|---|---|---|---|
| 2026-11-04 | `extensions_tests`（本能力） | 88 | 通过 | Windows 10 / Python 3.12 / ok-script 3.0.0b3 |
| 2026-11-04 | `extensions_tests`（全量，6 个文件） | 142 | 通过 | 同上 |
| 2026-11-04 | `tests`（上游基线） | 10 | 通过 | 同上 |
| 2026-11-04 | 体检 `extension_capability_guard.py audit` | — | 通过（错误 0 / 警告 0 / 提示 0） | 同上 |
| 2026-11-04 | 翻译门禁 `task_i18n_helper.py gate` | 13 条 × 2 语言 | 通过 | 同上 |

> **必须写明依据的是哪个测试根。** 框架基线与自有代码两类测试的结论不可互换：
> 自有代码全绿不能证明框架行为完好，框架基线全绿也不能证明自有代码正确。

**用例数必须填**：测试因缺少依赖而整类跳过时，框架往往仍返回成功——全部跳过与全部通过，退出码可能相同。

## 已知盲区

| 盲区 | 影响面 | 缓解手段 |
|---|---|---|
| 未在真实游戏中验证遮罩位置 | 采集后端可能对帧做过缩放，配置的百分比在极端分辨率下可能看起来偏 | 几何层按帧实际尺寸换算，与采集分辨率无关；用户可在页签里直接看到效果并调整 |
| 旧配置若存的是**像素**单位值，迁移后数值被直接当百分比 | 遮罩会大得铺满画面 | `migrate_legacy_config` 检测到 `数值单位` 非「百分比」时写警告日志，提示用户在页签里重调；当前仓库的配置是百分比，未受影响 |
| 页签位置为 Game 分区末位，非字面「模板之下」 | 与需求 A8 的字面表述不同 | 见探索记录决定 4：当前框架把模板页签放在 Develop 分区，跨分区不存在上下相邻 |
| 设置卡片不折叠 | 与需求 R3.2.1 的字面表述不同 | 见偏差记录 #3：折叠机制在当前框架不存在，重置按钮常驻，需求意图已满足 |
