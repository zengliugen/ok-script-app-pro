# -*- coding: utf-8 -*-
"""截图遮罩测试。

被测对象是 `extensions/capabilities/screenshot_mask/` 下的几何、处理器与页签。分三层：

1. **几何层**：锚点、偏移、裁剪、百分比换算与矩形反解。纯 numpy，任何环境都跑。
2. **处理器层**：开关判定、异常兜底、配置缺失时的原帧返回。这层决定「遮罩失败会不会
   拖垮截图」，比几何是否正确更关乎稳定性。
3. **界面层**：页签契约（`MainWindow` 依赖的属性）、8 个手柄的拖拽语义、缩放换算、
   编辑与保存分离。需要 PySide6，缺依赖时整类跳过。

为什么几何层要单独成层：它会用「拖出来的矩形 → 配置值 → 还原矩形」逐像素比对，
这是本能力唯一能机械证伪的部分；混进 Qt 测试会让它在无 GUI 环境整块跳过。
"""

import unittest

import numpy as np

from extensions.capabilities.screenshot_mask import geometry

try:
    from extensions.capabilities.screenshot_mask import processor

    PROCESSOR_ERROR = None
except ModuleNotFoundError as exc:  # 缺 ok 运行依赖
    processor = None
    PROCESSOR_ERROR = str(exc)

try:
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from extensions.capabilities.screenshot_mask.tab import (
        DRAG_PADDING,
        HANDLES,
        MIN_RECT_SIZE,
        PREVIEW_PADDING,
        ZOOM_STEP,
        PreviewCanvas,
        ScreenshotMaskTab,
        _clamp_interval,
        _to_qrect,
    )

    QT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 PySide6
    QApplication = None
    QT_ERROR = str(exc)


def _frame(width, height, value=200):
    """构造单色 BGR 帧，便于判断哪块被涂黑。

    Args：
        width：帧宽。
        height：帧高。
        value：填充值。

    Returns：
        三通道 uint8 数组。
    """
    return np.full((height, width, 3), value, dtype=np.uint8)


def _ensure_application():
    """创建（或复用）唯一的 QApplication。

    必须在这里持有一个模块级引用：`QApplication` 被回收会让后续所有 Qt 控件构造失败，
    而失败信息是 `SystemError: returned NULL`，看起来像控件代码有问题。

    Returns：
        `QApplication` 实例；缺 PySide6 时返回 ``None``。
    """
    if QApplication is None:
        return None
    return QApplication.instance() or QApplication([])


_APP = _ensure_application()


def _values(**overrides):
    """以默认值为底，覆盖指定键。

    Args：
        **overrides：要覆盖的键值。

    Returns：
        新的配置字典。
    """
    values = dict(geometry.DEFAULT_VALUES)
    values.update(overrides)
    return values


class TestResolveRect(unittest.TestCase):
    """几何换算：锚点、偏移、裁剪。"""

    def test_default_matches_upstream_behavior(self):
        """默认配置应还原上游写死的右下角 13% × 2.5%。"""
        self.assertEqual((1114, 702, 1280, 720), geometry.resolve_rect(geometry.DEFAULT_VALUES, 1280, 720))

    def test_default_matches_upstream_on_any_resolution(self):
        """默认几何必须逐像素等于上游 `int(0.13*w)` / `int(0.025*h)`。

        上游用的是**截断**而不是四舍五入。1920 宽下 `round(0.13*1920)=250` 而
        `int(0.13*1920)=249`，遮罩左边会差 1 像素——边缘可见细缝，且只在部分分辨率下
        出现，最难排查。这条用例就是为固定「必须截断」写的。
        """
        for width, height in ((640, 480), (1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(size=(width, height)):
                expected = (
                    width - int(0.13 * width),
                    height - int(0.025 * height),
                    width,
                    height,
                )
                self.assertEqual(expected, geometry.resolve_rect(geometry.DEFAULT_VALUES, width, height))

    def test_all_anchors_place_mask_on_expected_side(self):
        """8 个锚点都应把遮罩贴到对应边缘或角落。"""
        width, height = 1280, 720
        mask_width = round(0.13 * width)
        mask_height = round(0.025 * height)
        expected = {
            geometry.ANCHOR_TOP_LEFT: (0, 0, mask_width, mask_height),
            geometry.ANCHOR_TOP: ((width - mask_width) // 2, 0, (width - mask_width) // 2 + mask_width, mask_height),
            geometry.ANCHOR_TOP_RIGHT: (width - mask_width, 0, width, mask_height),
            geometry.ANCHOR_RIGHT: (width - mask_width, (height - mask_height) // 2, width,
                                    (height - mask_height) // 2 + mask_height),
            geometry.ANCHOR_BOTTOM_RIGHT: (width - mask_width, height - mask_height, width, height),
            geometry.ANCHOR_BOTTOM: ((width - mask_width) // 2, height - mask_height,
                                     (width - mask_width) // 2 + mask_width, height),
            geometry.ANCHOR_BOTTOM_LEFT: (0, height - mask_height, mask_width, height),
            geometry.ANCHOR_LEFT: (0, (height - mask_height) // 2, mask_width,
                                   (height - mask_height) // 2 + mask_height),
        }
        for anchor, rect in expected.items():
            with self.subTest(anchor=anchor):
                values = _values(**{geometry.KEY_ANCHOR: anchor})
                self.assertEqual(rect, geometry.resolve_rect(values, width, height))

    def test_offset_moves_inward_from_anchored_edge(self):
        """正值偏移沿「背离所贴边缘」方向移动，且偏移也是画面比例。"""
        values = _values(
            **{
                geometry.KEY_ANCHOR: geometry.ANCHOR_BOTTOM_RIGHT,
                geometry.KEY_WIDTH: 13.0,
                geometry.KEY_HEIGHT: 2.5,
                geometry.KEY_OFFSET_X: 10.0,
                geometry.KEY_OFFSET_Y: 5.0,
            }
        )
        # 1280 的 10% = 128，720 的 5% = 36
        self.assertEqual((986, 666, 1152, 684), geometry.resolve_rect(values, 1280, 720))

    def test_offset_from_top_left_moves_into_frame(self):
        """左上角锚点的偏移把遮罩推向画面内部。"""
        values = _values(
            **{
                geometry.KEY_ANCHOR: geometry.ANCHOR_TOP_LEFT,
                geometry.KEY_OFFSET_X: 10.0,
                geometry.KEY_OFFSET_Y: 10.0,
            }
        )
        self.assertEqual((128, 72, 294, 90), geometry.resolve_rect(values, 1280, 720))

    def test_negative_offset_clipped_to_frame(self):
        """负偏移可把遮罩推出画面，超出部分按边界裁剪且不报错。"""
        values = _values(
            **{
                geometry.KEY_ANCHOR: geometry.ANCHOR_TOP_LEFT,
                geometry.KEY_OFFSET_X: -10.0,
            }
        )
        # 贴左边时 x1 = -128，裁剪到 0，宽度由 166 变成 38
        self.assertEqual((0, 0, 38, 18), geometry.resolve_rect(values, 1280, 720))

    def test_negative_offset_fully_outside_returns_none(self):
        """负偏移把整块遮罩推出画面时返回 None，不产生负宽高。"""
        values = _values(
            **{
                geometry.KEY_ANCHOR: geometry.ANCHOR_TOP_LEFT,
                geometry.KEY_OFFSET_X: -10.0,
                geometry.KEY_OFFSET_Y: -10.0,
            }
        )
        self.assertIsNone(geometry.resolve_rect(values, 1280, 720))

    def test_mask_outside_frame_returns_none(self):
        """完全落在画面外时返回 None，而不是退化成一条线。"""
        values = _values(
            **{
                geometry.KEY_ANCHOR: geometry.ANCHOR_TOP_LEFT,
                geometry.KEY_OFFSET_X: 100.0,
            }
        )
        self.assertIsNone(geometry.resolve_rect(values, 1280, 720))

    def test_zero_frame_returns_none(self):
        """帧尺寸非法时不产出矩形。"""
        self.assertIsNone(geometry.resolve_rect(geometry.DEFAULT_VALUES, 0, 720))
        self.assertIsNone(geometry.resolve_rect(geometry.DEFAULT_VALUES, 1280, 0))

    def test_unknown_anchor_returns_none(self):
        """锚点不在表里时返回 None，不猜一个位置。"""
        values = _values(**{geometry.KEY_ANCHOR: "nowhere"})
        self.assertIsNone(geometry.resolve_rect(values, 1280, 720))


class TestApplyMask(unittest.TestCase):
    """遮罩落地：只涂目标区域。"""

    def test_color_frame_blackens_only_target(self):
        """彩色帧只有遮罩区域变黑。"""
        frame = _frame(1280, 720)
        result = geometry.apply_mask(frame, geometry.DEFAULT_VALUES)
        self.assertIs(result, frame)
        self.assertEqual(0, result[710, 1200].max())
        self.assertEqual(200, result[100, 100].max())

    def test_grayscale_frame_supported(self):
        """灰度帧同样能遮罩，不依赖通道数。"""
        frame = np.full((720, 1280), 200, dtype=np.uint8)
        result = geometry.apply_mask(frame, geometry.DEFAULT_VALUES)
        self.assertEqual(0, result[710, 1200])
        self.assertEqual(200, result[100, 100])

    def test_disabled_flag_not_handled_here(self):
        """`apply_mask` 不判断开关：开关由处理器统一判，避免两处语义分叉。"""
        frame = _frame(1280, 720)
        values = _values(**{geometry.KEY_ENABLED: False})
        result = geometry.apply_mask(frame, values)
        self.assertEqual(0, result[710, 1200].max())

    def test_none_frame_passthrough(self):
        """空帧原样返回，不抛异常。"""
        self.assertIsNone(geometry.apply_mask(None, geometry.DEFAULT_VALUES))

    def test_mask_outside_frame_leaves_frame_untouched(self):
        """遮罩完全在画面外时帧不被修改。"""
        frame = _frame(1280, 720)
        values = _values(**{geometry.KEY_ANCHOR: geometry.ANCHOR_TOP_LEFT, geometry.KEY_OFFSET_X: 100.0})
        result = geometry.apply_mask(frame, values)
        self.assertEqual(200, result.min())


class TestValuesFromRect(unittest.TestCase):
    """拖拽回写：矩形 → 配置值。"""

    def test_round_trip_all_anchors(self):
        """每个锚点下，矩形 → 配置值 → 矩形 应逐像素稳定。"""
        width, height = 1280, 720
        mask_width, mask_height = 166, 18
        cases = {
            geometry.ANCHOR_TOP_LEFT: (0, 0, mask_width, mask_height),
            geometry.ANCHOR_TOP_RIGHT: (width - mask_width, 0, width, mask_height),
            geometry.ANCHOR_BOTTOM_RIGHT: (width - mask_width, height - mask_height, width, height),
            geometry.ANCHOR_BOTTOM_LEFT: (0, height - mask_height, mask_width, height),
        }
        for anchor, rect in cases.items():
            with self.subTest(anchor=anchor):
                values = geometry.values_from_rect(rect, width, height)
                self.assertEqual(rect, geometry.resolve_rect(values, width, height))

    def test_round_trip_across_frame_sizes(self):
        """不同分辨率下反解都能精确还原——百分比精度不够时会在这里暴露。"""
        rects = (
            (100, 100, 500, 300),
            (0, 0, 166, 18),
            (1920 - 250, 1080 - 27, 1920, 1080),
            (640 - 83, 0, 640, 16),
        )
        for width, height in ((640, 480), (1280, 720), (1920, 1080), (2560, 1440)):
            for rect in rects:
                if rect[2] > width or rect[3] > height:
                    continue
                with self.subTest(size=(width, height), rect=rect):
                    values = geometry.values_from_rect(rect, width, height)
                    self.assertEqual(rect, geometry.resolve_rect(values, width, height))

    def test_dragged_rect_overrides_config(self):
        """拖到哪存哪：回写值应精确复现拖拽后的矩形。"""
        width, height = 1280, 720
        values = geometry.values_from_rect((100, 200, 400, 240), width, height)
        self.assertEqual((100, 200, 400, 240), geometry.resolve_rect(values, width, height))

    def test_anchor_changes_when_rect_moves_to_opposite_corner(self):
        """从右下角拖到左上角后，锚点跟着改，否则偏移会变成大负值。"""
        values = geometry.values_from_rect((0, 0, 166, 18), 1280, 720)
        self.assertEqual(geometry.ANCHOR_TOP_LEFT, values[geometry.KEY_ANCHOR])

    def test_base_values_preserved(self):
        """反解只改锚点与四个数值键，其余键（如开关）保持传入的基准值。"""
        base = _values(**{geometry.KEY_ENABLED: False})
        values = geometry.values_from_rect((100, 100, 500, 300), 1280, 720, base)
        self.assertFalse(values[geometry.KEY_ENABLED])


@unittest.skipIf(processor is None, f"缺少运行依赖，跳过处理器测试：{PROCESSOR_ERROR}")
class TestProcessor(unittest.TestCase):
    """处理器：配置读取与异常兜底。"""

    def test_processor_returns_frame_when_config_missing(self):
        """配置项未注册时直接返回原帧，不影响截图主流程。"""

        def missing():
            raise RuntimeError("未注册")

        frame = _frame(1280, 720)
        result = processor.make_processor(missing)(frame)
        self.assertIs(result, frame)

    def test_processor_swallows_exceptions(self):
        """内部异常必须被吞掉：遮罩失败不应让截图整体失败。"""

        def boom():
            raise ValueError("bad config")

        frame = _frame(1280, 720)
        result = processor.make_processor(boom)(frame)
        self.assertIs(result, frame)
        self.assertEqual(200, result.min())

    def test_processor_passes_none_through(self):
        """空帧原样返回，不触发任何计算。"""
        self.assertIsNone(processor.make_processor(lambda: dict(geometry.DEFAULT_VALUES))(None))

    def test_disabled_writes_nothing(self):
        """开关关闭时处理器不涂黑任何像素。"""
        values = _values(**{geometry.KEY_ENABLED: False})
        frame = _frame(1280, 720)
        result = processor.make_processor(lambda: values)(frame)
        self.assertEqual(200, result.min())

    def test_enabled_blackens_bottom_right(self):
        """开关开启时按默认几何涂黑右下角。"""
        frame = _frame(1280, 720)
        result = processor.make_processor(lambda: dict(geometry.DEFAULT_VALUES))(frame)
        self.assertEqual(0, result[710, 1200].max())
        self.assertEqual(200, result[100, 100].max())

    def test_reads_config_every_call(self):
        """每次调用都重新读配置：页签里改了立刻生效，不必重启。"""
        values = _values(**{geometry.KEY_ENABLED: False})
        process = processor.make_processor(lambda: values)
        frame = _frame(1280, 720)
        self.assertEqual(200, process(frame) .min())
        values[geometry.KEY_ENABLED] = True
        self.assertEqual(0, process(_frame(1280, 720))[710, 1200].max())

    def test_migrate_legacy_config(self):
        """旧中文键被改写为英文键，锚点取值一并翻译。"""
        import json
        import os
        import tempfile

        folder = tempfile.mkdtemp()
        path = os.path.join(folder, processor.OPTION_NAME + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "启用遮罩": True,
                    "遮罩位置": "右下角",
                    "宽度": 40.21,
                    "高度": 24.26,
                    "水平内缩": 30.94,
                    "垂直内缩": 40.0,
                    "数值单位": "百分比",
                },
                handle,
                ensure_ascii=False,
            )

        changed = processor.migrate_legacy_config(folder)
        self.assertEqual(6, changed)

        with open(path, "r", encoding="utf-8") as handle:
            migrated = json.load(handle)
        self.assertEqual(40.21, migrated[geometry.KEY_WIDTH])
        self.assertEqual(30.94, migrated[geometry.KEY_OFFSET_X])
        self.assertEqual(geometry.ANCHOR_BOTTOM_RIGHT, migrated[geometry.KEY_ANCHOR])
        self.assertNotIn("启用遮罩", migrated)
        self.assertNotIn("数值单位", migrated)

    def test_migrate_is_noop_on_new_config(self):
        """已经是新键时不再改写文件。"""
        import json
        import os
        import tempfile

        folder = tempfile.mkdtemp()
        path = os.path.join(folder, processor.OPTION_NAME + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(dict(geometry.DEFAULT_VALUES), handle)

        self.assertEqual(0, processor.migrate_legacy_config(folder))


@unittest.skipIf(processor is None, f"缺少运行依赖，跳过配置项测试：{PROCESSOR_ERROR}")
class TestConfigOption(unittest.TestCase):
    """配置项契约：默认值与隐藏键。"""

    def test_defaults_preserve_upstream_behavior(self):
        """默认值与上游写死行为一致，升级后截图结果不变。"""
        option = processor.build_config_option()
        defaults = option.default_config
        self.assertTrue(defaults[geometry.KEY_ENABLED])
        self.assertEqual(geometry.ANCHOR_BOTTOM_RIGHT, defaults[geometry.KEY_ANCHOR])
        self.assertEqual(13.0, defaults[geometry.KEY_WIDTH])
        self.assertEqual(2.5, defaults[geometry.KEY_HEIGHT])
        self.assertEqual(0.0, defaults[geometry.KEY_OFFSET_X])
        self.assertEqual(0.0, defaults[geometry.KEY_OFFSET_Y])

    def test_numeric_keys_hidden_in_settings(self):
        """几何与锚点在设置页隐藏：脱离画面拖拽去编辑数字只会产生歧义。"""
        option = processor.build_config_option()
        for key in (geometry.KEY_ANCHOR, *geometry.NUMERIC_KEYS):
            with self.subTest(key=key):
                self.assertTrue(option.config_type.get(key, {}).get("hidden", False))

    def test_enabled_key_visible(self):
        """「启用遮罩」必须在设置页可见，否则用户找不到总开关。"""
        option = processor.build_config_option()
        self.assertNotIn("hidden", option.config_type.get(geometry.KEY_ENABLED, {}))
        self.assertIn(geometry.KEY_ENABLED, option.config_description)

    def test_keys_ascii(self):
        """配置键必须可打印 ASCII：它同时是持久化字段名与界面标签。"""
        for key in geometry.DEFAULT_VALUES:
            with self.subTest(key=key):
                self.assertTrue(key.isascii() and key.isprintable())


@unittest.skipIf(QApplication is None, f"缺少 Qt 运行依赖，跳过界面测试：{QT_ERROR}")
class TestTabContract(unittest.TestCase):
    """页签契约：`MainWindow` 注册 `custom_tabs` 时依赖的属性必须齐备。

    属性缺失不会在导入期暴露，只能在装配路径上踩到——静态检查与单元测试都过，
    真实启动才抛 AttributeError。
    """

    @classmethod
    def setUpClass(cls):
        """创建 QApplication：页签是 QWidget，无 app 时无法实例化。"""
        cls.app = QApplication.instance() or QApplication([])

    def test_extends_custom_tab(self):
        """必须继承 `CustomTab`——`position` / `add_after_default_tabs` 由它提供。"""
        from ok.ui.qt.widget.CustomTab import CustomTab

        self.assertTrue(issubclass(ScreenshotMaskTab, CustomTab))

    def test_required_attributes_present(self):
        """`MainWindow` 会读取的属性都在类上定义，而不是等到实例化才补。"""
        for attribute in ("icon", "name", "add_after_default_tabs"):
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(ScreenshotMaskTab, attribute))


class TestPreviewHandles(unittest.TestCase):
    """框选手柄：8 个方向都能缩放，预览四周留白。"""

    @classmethod
    def setUpClass(cls):
        """创建 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def _canvas(self, width=1280, height=720, rect=(100, 100, 500, 300)):
        """构造已载入帧、已知矩形的画布。"""
        canvas = PreviewCanvas()
        canvas.resize(800, 480)
        image = QImage(width, height, QImage.Format.Format_RGB888)
        image.fill(0x808080)
        canvas.set_frame(image)
        canvas.set_rect(rect)
        return canvas

    def test_eight_handles_defined(self):
        """8 个手柄齐备：4 角 + 4 边中点。"""
        self.assertEqual(8, len(HANDLES))
        self.assertEqual({"nw", "n", "ne", "e", "se", "s", "sw", "w"}, set(HANDLES))

    def test_each_handle_moves_its_own_edges(self):
        """每个手柄只移动自己所属的边，对边保持不动。"""
        canvas = self._canvas()
        rect = (100, 100, 500, 300)
        expected = {
            "nw": (110, 110, 500, 300),
            "n": (100, 110, 500, 300),
            "ne": (100, 110, 510, 300),
            "e": (100, 100, 510, 300),
            "se": (100, 100, 510, 310),
            "s": (100, 100, 500, 310),
            "sw": (110, 100, 500, 310),
            "w": (110, 100, 500, 300),
        }
        for handle, result in expected.items():
            with self.subTest(handle=handle):
                self.assertEqual(result, canvas._resized_rect(rect, handle, 10, 10))

    def test_negative_delta_shrinks_from_dragged_edge(self):
        """反向拖动沿被拖的边收缩，而不是整体移动。"""
        canvas = self._canvas()
        self.assertEqual((100, 100, 460, 300), canvas._resized_rect((100, 100, 500, 300), "e", -40, 0))
        self.assertEqual((140, 100, 500, 300), canvas._resized_rect((100, 100, 500, 300), "w", 40, 0))

    def test_minimum_size_enforced_on_shrink(self):
        """把边拖过对边时停在最小尺寸，不会翻转成负宽高。"""
        canvas = self._canvas()
        rect = (100, 100, 500, 300)
        result = canvas._resized_rect(rect, "w", 5000, 0)
        self.assertEqual(500 - MIN_RECT_SIZE, result[0])
        self.assertEqual(500, result[2])

    def test_resize_cannot_leave_frame(self):
        """向外拖动不会越出画面边界。"""
        canvas = self._canvas()
        self.assertEqual((0, 0, 500, 300), canvas._resized_rect((100, 100, 500, 300), "nw", -5000, -5000))
        self.assertEqual((100, 100, 1280, 720), canvas._resized_rect((100, 100, 500, 300), "se", 5000, 5000))

    def test_move_keeps_size_and_stays_inside(self):
        """平移保持尺寸，且整体不越界。"""
        canvas = self._canvas()
        self.assertEqual((150, 130, 550, 330), canvas._moved_rect((100, 100, 500, 300), 50, 30))
        self.assertEqual((880, 520, 1280, 720), canvas._moved_rect((100, 100, 500, 300), 5000, 5000))

    def test_fit_aligns_to_canvas_edge(self):
        """「适应窗口」的画面贴住控件边缘，不再留白。

        留白等于白缩一圈，而贴边手柄的遮挡由平移留白（DRAG_PADDING）在拖拽时接管。

        注意「贴住」指的是**至少一条轴**顶到边界；另一条轴上的间隔来自画面与控件
        的宽高比不同（1280×720 放进 800×480，宽度撑满后上下各留 15px），不是留白。
        """
        canvas = self._canvas()
        top_left = canvas._frame_to_view((0, 0))
        bottom_right = canvas._frame_to_view((1280, 720))
        touches_x = top_left.x() <= 1 and abs(bottom_right.x() - canvas.width()) <= 1
        touches_y = top_left.y() <= 1 and abs(bottom_right.y() - canvas.height()) <= 1
        self.assertTrue(touches_x or touches_y, "适应窗口后画面应至少一条轴贴住控件边缘")

    def test_frame_scale_uses_full_canvas(self):
        """缩放比例按完整控件尺寸计算（`PREVIEW_PADDING` 为 0）。"""
        canvas = self._canvas()
        expected = min(800 / 1280, 480 / 720)
        self.assertAlmostEqual(expected, canvas._fit_scale(), places=6)
        self.assertEqual(0, PREVIEW_PADDING)

    def test_rect_tuple_converts_with_correct_width(self):
        """`(x1,y1,x2,y2)` 必须换算成宽高，不能把结束坐标当宽高。"""
        rect = _to_qrect((1114, 702, 1280, 720))
        self.assertEqual(166, rect.width())
        self.assertEqual(18, rect.height())

    def test_hit_handle_prefers_corners(self):
        """矩形很小时角手柄与边中点重叠，命中判定必须优先返回角。

        取样点取手柄矩形的左上角外侧——手柄本身跨骑矩形边界，而矩形内部已被规定
        「不算命中手柄」（否则小矩形点中间会变成缩放，无法平移）。
        """
        canvas = self._canvas(rect=(100, 100, 110, 110))
        canvas.set_rect((100, 100, 110, 110))
        handle_rect = canvas._handle_rect("nw")
        outside = handle_rect.topLeft().toPoint() - QPoint(1, 1)
        self.assertEqual("nw", canvas._hit_handle(outside))

    def test_rect_interior_is_not_a_handle(self):
        """点遮罩内部永远是「整体平移」，不会被小矩形的手柄抢走。

        缩小到 10% 时遮罩只有 30×10 像素，四个手柄加容差几乎盖满整块区域；
        若内部仍判成手柄，用户点中间怎么拖都是缩放，平移彻底失效。
        """
        canvas = self._canvas()
        canvas.set_rect((600, 400, 900, 500))
        canvas._set_scale(0.1)
        center = canvas._frame_to_view((750, 450)).toPoint()

        # 内部深处：不算手柄，按下后进入整体平移
        self.assertTrue(canvas._in_rect_interior(center))
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        canvas.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(center),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.assertEqual("move", canvas._drag_mode)

    def test_handle_still_hit_at_rect_edge(self):
        """贴近边界时仍是手柄：内部判定不能把贴边手柄整条废掉。

        手柄中心正落在矩形边界上，若用「在矩形内即不算手柄」的判据，
        边中点与角手柄会全部失效。
        """
        canvas = self._canvas()
        canvas.set_rect((600, 400, 900, 500))
        canvas._set_scale(2.0)
        handle = canvas._handle_rect("e").center().toPoint()

        self.assertFalse(canvas._in_rect_interior(handle))
        self.assertEqual("e", canvas._hit_handle(handle))

    def test_clamp_interval_keeps_dragged_edge_tracking(self):
        """被拖的边越过对边时，由被拖的那一侧让位，边始终跟着鼠标。"""
        # 起点侧拖 -4：新起点 96
        self.assertEqual(96, _clamp_interval(100, 200, 1280, -4, True))
        # 终点侧拖 +4：新终点 204
        self.assertEqual(204, _clamp_interval(100, 200, 1280, 4, False))
        # 起点侧拖过终点：停在「终点 - 最小尺寸」
        self.assertEqual(200 - MIN_RECT_SIZE, _clamp_interval(100, 200, 1280, 5000, True))
        # 终点侧拖过起点：停在「起点 + 最小尺寸」
        self.assertEqual(100 + MIN_RECT_SIZE, _clamp_interval(100, 200, 1280, -5000, False))


class TestCanvasZoom(unittest.TestCase):
    """预览缩放：滚轮、按钮、平移边界。"""

    @classmethod
    def setUpClass(cls):
        """创建 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def _canvas(self, frame_width=2560, frame_height=1440, size=(800, 480)):
        """构造载入指定尺寸帧的画布。"""
        canvas = PreviewCanvas()
        canvas.resize(*size)
        image = QImage(frame_width, frame_height, QImage.Format.Format_RGB888)
        image.fill(0x333333)
        canvas.set_frame(image)
        return canvas

    def test_fit_scale_uses_full_canvas(self):
        """载入画面时按完整控件尺寸取景，画面贴边。"""
        canvas = self._canvas()
        expected = min(800 / 2560, 480 / 1440)
        self.assertAlmostEqual(expected, canvas.zoom_scale(), places=6)

    def test_zoom_in_and_out_are_inverse(self):
        """放大再缩小回到原比例。"""
        canvas = self._canvas()
        start = canvas.zoom_scale()
        canvas.zoom_in()
        self.assertAlmostEqual(start * ZOOM_STEP, canvas.zoom_scale(), places=6)
        canvas.zoom_out()
        self.assertAlmostEqual(start, canvas.zoom_scale(), places=6)

    def test_zoom_reset_returns_to_one_to_one(self):
        """1:1 按钮回到原尺寸比例 1.0。"""
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas.zoom_reset()
        self.assertAlmostEqual(1.0, canvas.zoom_scale(), places=6)

    def test_zoom_reset_centers_mask_into_view(self):
        """1:1 后遮罩必须可见：画面远大于视口时它很容易跑到视口外。"""
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas.zoom_reset()
        top_left = canvas._frame_to_view((2100, 1300))
        bottom_right = canvas._frame_to_view((2500, 1400))
        view_rect = _to_qrect((top_left.x(), top_left.y(), bottom_right.x(), bottom_right.y()))
        self.assertTrue(view_rect.intersects(canvas.rect()))

    def test_zoom_fit_sets_concrete_scale(self):
        """「适应窗口」把比例算成具体值，而不是留下一个「自动」状态。"""
        canvas = self._canvas()
        canvas.zoom_reset()
        self.assertAlmostEqual(1.0, canvas.zoom_scale(), places=6)

        canvas.zoom_fit()
        expected = min(canvas.width() / 2560, canvas.height() / 1440)
        self.assertAlmostEqual(expected, canvas.zoom_scale(), places=6)

    def test_zoom_fit_has_no_lasting_state(self):
        """适应窗口是一次性动作：之后控件尺寸变化，比例不跟着重算。"""
        canvas = self._canvas()
        canvas.zoom_fit()
        fitted = canvas.zoom_scale()

        canvas.resize(600, 360)
        self.assertAlmostEqual(fitted, canvas.zoom_scale(), places=6)

    def test_scale_is_clamped(self):
        """缩放比例被夹在上下限内，反复点击不会越界。"""
        canvas = self._canvas()
        for _ in range(60):
            canvas.zoom_in()
        self.assertLessEqual(canvas.zoom_scale(), 20.0)
        for _ in range(120):
            canvas.zoom_out()
        self.assertGreaterEqual(canvas.zoom_scale(), 0.05)

    def test_coordinates_stay_consistent_across_scales(self):
        """各缩放级别下，帧坐标换算成控件再反推回来必须一致。"""
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        for scale in (0.3, 1.0, 2.0):
            with self.subTest(scale=scale):
                canvas._set_scale(scale)
                point = canvas._frame_to_view((2200, 1350))
                back = canvas._view_to_frame(point)
                self.assertAlmostEqual(2200, back.x(), delta=0.5)
                self.assertAlmostEqual(1350, back.y(), delta=0.5)

    def test_drag_delta_scales_with_zoom(self):
        """放大后拖动：屏幕位移换算成帧位移时必须除以当前比例。"""
        canvas = self._canvas()
        canvas._set_scale(2.0)
        self.assertEqual(2.0, canvas.zoom_scale())
        self.assertAlmostEqual(100 / 2.0, QPoint(100, 50).x() / canvas.zoom_scale(), places=6)

    def test_small_step_drag_keeps_up_with_mouse(self):
        """逐像素拖动时，画面位移必须与鼠标位移一致——不分放大还是缩小。

        这是真实踩过的坑：早期用「上一次位置」做增量的基准，每一步都把取整后的矩形
        当新基准，亚像素位移被反复丢掉。200% 时鼠标每动 1px 只折算 0.5 帧像素，
        取整后恒为 0，表现为完全拖不动；25% 时则每步丢一点，拖 100px 只走 95px。
        """
        for scale in (2.0, 1.0, 0.5, 0.25):
            canvas = self._canvas()
            canvas.set_rect((600, 400, 900, 500))
            canvas._set_scale(scale)
            before = canvas.mask_rect()
            # 从遮罩中心按下，确保进入「整体平移」
            center = canvas._frame_to_view((750, 450))

            self._press_at(canvas, center)
            self.assertEqual("move", canvas._drag_mode, f"scale={scale}")
            # 逐步移动 1 个控件像素，模拟真实拖拽
            position = QPointF(center)
            for _ in range(30):
                position = QPointF(position.x() + 1, position.y())
                self._move(canvas, position)

            moved = (canvas.mask_rect()[0] - before[0]) * scale
            with self.subTest(scale=scale):
                self.assertAlmostEqual(30, moved, delta=1)

    def test_handle_resize_tracks_mouse_at_any_scale(self):
        """拖动右手柄时，宽度增量也必须与鼠标位移一致。"""
        for scale in (2.0, 1.0, 0.5, 0.25):
            canvas = self._canvas()
            canvas.set_rect((600, 400, 900, 500))
            canvas._set_scale(scale)
            before = canvas.mask_rect()
            handle = canvas._handle_rect("e").center()

            self._press_at(canvas, handle)
            self.assertEqual("resize", canvas._drag_mode, f"scale={scale}")
            self.assertEqual("e", canvas._drag_handle)

            position = QPointF(handle)
            for _ in range(30):
                position = QPointF(position.x() + 1, position.y())
                self._move(canvas, position)

            after = canvas.mask_rect()
            grown = ((after[2] - after[0]) - (before[2] - before[0])) * scale
            with self.subTest(scale=scale):
                self.assertAlmostEqual(30, grown, delta=1)

    def test_offset_clamped_when_frame_larger_than_canvas(self):
        """画面大于控件时，偏移被限制在「四周仍留黑色间隔」的范围内。

        不允许画面边缘正好压在控件边界上：贴边会让画面看起来像被裁掉一块，贴边的遮罩
        手柄也会跟着切进边界。因此可拖范围是 ``[控件宽 - 画面宽 - 留白, 留白]``。
        """
        canvas = self._canvas()
        canvas._set_scale(2.0)
        offset = canvas._clamp_offset(QPointF(99999, 99999), 2.0)
        self.assertLessEqual(offset.x(), DRAG_PADDING)
        self.assertLessEqual(offset.y(), DRAG_PADDING)
        self.assertGreaterEqual(offset.x(), canvas.width() - 2560 * 2.0 - DRAG_PADDING)
        self.assertGreaterEqual(offset.y(), canvas.height() - 1440 * 2.0 - DRAG_PADDING)

    def test_frame_edge_never_touches_canvas_edge(self):
        """拖到极限时，画面「被拖向的那一侧」边缘与控件边界之间仍留着间隔。

        画面比控件大时另一侧本来就在视野外，因此只校验贴在视口内的那条边。
        """
        canvas = self._canvas()
        canvas._set_scale(2.0)
        width = 2560 * 2.0
        height = 1440 * 2.0

        # 往右下拖到极限：画面左上角停在留白处，不能压到 (0, 0)
        far = canvas._clamp_offset(QPointF(99999, 99999), 2.0)
        self.assertAlmostEqual(DRAG_PADDING, far.x(), places=6)
        self.assertAlmostEqual(DRAG_PADDING, far.y(), places=6)

        # 往左上拖到极限：画面右下角停在「控件尺寸 - 留白」处
        near = canvas._clamp_offset(QPointF(-99999, -99999), 2.0)
        self.assertAlmostEqual(canvas.width() - DRAG_PADDING, near.x() + width, places=6)
        self.assertAlmostEqual(canvas.height() - DRAG_PADDING, near.y() + height, places=6)

    def test_release_keeps_pan_position(self):
        """松手后不重算偏移：拖到哪儿就停在哪儿，不能弹回去。

        这条是真实踩过的坑：曾在松手时把留白从 60 收回 12 再夹一次，
        结果画面当场弹回小留白边界，用户拖了半天等于白拖。
        """
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas._set_scale(2.0)

        dragged = self._drag_far_corner(canvas)
        self.assertAlmostEqual(DRAG_PADDING, dragged.x(), delta=1)
        self.assertAlmostEqual(DRAG_PADDING, dragged.y(), delta=1)
        # 松手后偏移保持不变
        self.assertAlmostEqual(dragged.x(), canvas._offset.x(), places=6)
        self.assertAlmostEqual(dragged.y(), canvas._offset.y(), places=6)

    def test_buttons_reset_view_without_touching_padding(self):
        """「适应窗口」/「1:1」重新取景，但平移留白始终保持 DRAG_PADDING。"""
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas._set_scale(2.0)
        self._drag_far_corner(canvas)

        # 1:1 后仍能往极限方向拖到 DRAG_PADDING，不会因为「按钮把留白改小」而受限
        canvas.zoom_reset()
        canvas._offset = canvas._clamp_offset(QPointF(99999, 99999), canvas.zoom_scale())
        self.assertAlmostEqual(DRAG_PADDING, canvas._offset.x(), places=6)

        canvas.zoom_fit()
        canvas.zoom_reset()
        canvas._offset = canvas._clamp_offset(QPointF(99999, 99999), canvas.zoom_scale())
        self.assertAlmostEqual(DRAG_PADDING, canvas._offset.x(), places=6)

    def test_fit_does_not_lock_panning(self):
        """「适应窗口」是一次性取景，不能让后续拖拽失效。

        这条是真实踩过的坑：曾用 `_fit` 布尔量表示「处于适应模式」，而平移只认
        `not _fit`，于是点完「适应窗口」就再也拖不动画面——按钮把后续操作锁死了。
        """
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas.zoom_fit()

        # 适应后放大，画面超出视口，必须能平移
        canvas.zoom_in()
        before = QPointF(canvas._offset)
        self._press_empty_area(canvas)
        self.assertEqual("pan", canvas._drag_mode)
        self._move(canvas, QPointF(canvas.width() / 2 + 120, canvas.height() / 2 + 80))
        self.assertNotEqual((before.x(), before.y()), (canvas._offset.x(), canvas._offset.y()))

    def test_press_does_not_change_scale(self):
        """按下画面不改变比例：适应窗口后直接拖，比例仍是适应算出的那个值。

        这是「拖动时跳变」的根因用例：早期版本按下时先切模式再取比例，
        基准从推算值换成字段默认值，第一下就跳。
        """
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas.zoom_fit()
        fitted = canvas.zoom_scale()
        expected = min(canvas.width() / 2560, canvas.height() / 1440)
        self.assertAlmostEqual(expected, fitted, places=6)

        self._press_empty_area(canvas)
        self.assertAlmostEqual(fitted, canvas.zoom_scale(), places=6)
        self.assertEqual("pan", canvas._drag_mode)

    def test_press_does_not_move_frame(self):
        """按下画面本身不移动画面：拖动产生的位移与鼠标位移一致。"""
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        # 放大到画面明显超出视口，并把画面摆到中间位置，避免一上来就贴着 60px 边界
        canvas.zoom_in()
        canvas.zoom_in()
        canvas._offset = canvas._clamp_offset(QPointF(-400, -300), canvas.zoom_scale())
        before = QPointF(canvas._offset)

        self._press_empty_area(canvas)
        self.assertAlmostEqual(before.x(), canvas._offset.x(), places=6)
        self.assertAlmostEqual(before.y(), canvas._offset.y(), places=6)

        # 从实际按下点往右拖 100 像素，画面应恰好移动 100 像素
        origin = canvas._drag_origin
        self._move(canvas, QPointF(origin.x() + 100, origin.y()))
        moved = canvas._offset.x() - before.x()
        self.assertAlmostEqual(100, moved, delta=2)

    def test_one_to_one_can_pan(self):
        """1:1 下画面远大于视口，必须能拖。"""
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas.zoom_reset()
        self.assertTrue(canvas._can_pan())
        self._press_empty_area(canvas)
        self.assertEqual("pan", canvas._drag_mode)

    def test_cursor_shows_grab_whenever_frame_loaded(self):
        """只要载入画面就显示抓手：画面小时也能挪开露出底色。"""
        canvas = self._canvas()
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas.zoom_fit()
        # 适应窗口下画面 800×450，比视口小，但仍可拖动挪开
        self.assertTrue(canvas._can_pan())
        canvas.zoom_in()
        self.assertTrue(canvas._can_pan())

    def test_fit_scale_never_exceeds_canvas(self):
        """适应窗口必须让整幅画面进视口，不能因为留白为 0 就溢出。"""
        canvas = self._canvas()
        canvas.zoom_fit()
        scale = canvas.zoom_scale()
        self.assertLessEqual(2560 * scale, canvas.width() + 1)
        self.assertLessEqual(1440 * scale, canvas.height() + 1)

    def test_padding_takes_effect_on_render(self):
        """渲染结果里能看到留白：画面边缘与控件边界之间有底色间隔。"""
        canvas = self._canvas()
        canvas.set_frame(self._white_frame(2560, 1440))
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas._set_scale(2.0)

        self._press_empty_area(canvas)
        origin = canvas._drag_origin
        self._move(canvas, QPointF(origin.x() + 5000, origin.y() + 5000))
        drag_margin = self._visible_left_margin(canvas)
        self._release(canvas)
        release_margin = self._visible_left_margin(canvas)

        self.assertAlmostEqual(DRAG_PADDING, drag_margin, delta=1)
        # 松手后位置不变，因此间隔也不变——这正是「不弹回去」的可视判据。
        self.assertAlmostEqual(DRAG_PADDING, release_margin, delta=1)

    def _white_frame(self, width, height):
        """构造纯白帧，便于从渲染结果里分辨画面与底色。"""
        from PySide6.QtGui import QColor

        image = QImage(width, height, QImage.Format.Format_RGB888)
        image.fill(QColor(255, 255, 255))
        return image

    def _press_at(self, canvas, point):
        """在指定控件坐标按下左键。

        Args：
            canvas：目标画布。
            point：``QPointF`` 控件坐标。
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        canvas.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(point),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    def _press_empty_area(self, canvas):
        """在画面内、遮罩外的位置按下左键，进入平移模式。

        取画面中心偏左上一点的点：遮罩默认在右下角，这个位置必然落在遮罩外；
        且画面可能已被拖到左上角之外，直接换算帧坐标 (10,10) 会得到控件外的负坐标。

        Args：
            canvas：目标画布。
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        center = canvas._frame_to_view((1280, 720))
        point = QPointF(center.x() - 100, center.y() - 100)
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.mousePressEvent(event)
        self._press_point = point

    def _move(self, canvas, position):
        """把鼠标移动到指定控件坐标（拖拽中）。

        Args：
            canvas：目标画布。
            position：``QPointF`` 控件坐标。
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            position,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.mouseMoveEvent(event)

    def _drag_far_corner(self, canvas):
        """从按下点往右下方向拖到极限，返回松手后的偏移。

        Args：
            canvas：目标画布。

        Returns：
            拖拽结束时的偏移 ``QPointF``。
        """
        self._press_empty_area(canvas)
        origin = canvas._drag_origin
        self._move(canvas, QPointF(origin.x() + 5000, origin.y() + 5000))
        self._release(canvas)
        return QPointF(canvas._offset)

    def _release(self, canvas):
        """在当前位置松开左键，结束拖拽。"""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            QPointF(canvas.width() / 2, canvas.height() / 2),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.mouseReleaseEvent(event)

    def _visible_left_margin(self, canvas):
        """从渲染结果里量出画面左侧的空白宽度（像素）。"""
        import numpy as np

        rendered = canvas.grab().toImage().convertToFormat(QImage.Format.Format_RGB888)
        width, height = rendered.width(), rendered.height()
        buffer = np.frombuffer(rendered.constBits(), np.uint8).reshape(
            (height, rendered.bytesPerLine())
        )[:, : width * 3].reshape(height, width, 3)
        white = buffer[:, :, 0] > 200
        return int(np.where(white.any(axis=0))[0].min())

    def test_single_side_blank_is_capped_for_any_frame_size(self):
        """不论画面比视口大还是小，单侧空白上限都是 DRAG_PADDING。

        这条是为消除「有时能拖很宽、有时只有 60」写的：早期按「画面是否比视口大」分成
        两套语义（大画面限空白、小画面限可见），同一张画面在不同缩放下可拖幅度相差
        一个数量级。
        """
        for frame_width, frame_height, scale in (
            (2560, 1440, 0.3125),  # 画面 ≈ 视口宽
            (2560, 1440, 1.0),     # 画面远大于视口
            (1280, 720, 1.0),      # 画面略大于视口
            (800, 480, 1.0),       # 画面与视口等宽
            (720, 480, 1.0),       # 画面略小于视口
        ):
            canvas = self._canvas(frame_width=frame_width, frame_height=frame_height)
            size = frame_width * scale
            far = canvas._clamp_offset(QPointF(99999, 99999), scale)
            near = canvas._clamp_offset(QPointF(-99999, -99999), scale)
            with self.subTest(size=(frame_width, frame_height), scale=scale):
                # 拖到最右下：左侧空白不超过 DRAG_PADDING
                self.assertLessEqual(far.x(), DRAG_PADDING + 1)
                # 拖到最左上：右侧空白不超过 DRAG_PADDING
                self.assertLessEqual(canvas.width() - (near.x() + size), DRAG_PADDING + 1)

    def test_tiny_frame_is_centered(self):
        """画面小到两侧空白必然超标时居中，两侧对称，不再可拖。

        判据是「两侧空白都 ≤DRAG_PADDING」，画面宽度小于 ``视口 - 2*padding`` 时无解，
        此时居中让两侧一致——否则会出现「这次能拖很宽、下次拖不动」的漂移感。
        """
        canvas = self._canvas(frame_width=400, frame_height=300)
        far = canvas._clamp_offset(QPointF(99999, 99999), 1.0)
        near = canvas._clamp_offset(QPointF(-99999, -99999), 1.0)
        self.assertEqual(far.x(), near.x())
        self.assertAlmostEqual((canvas.width() - 400) / 2, far.x(), delta=1)
        self.assertAlmostEqual((canvas.height() - 300) / 2, far.y(), delta=1)

    def test_small_frame_can_be_dragged_aside(self):
        """画面略小于视口时也能拖开露底色，且空白上限与大画面一致。"""
        canvas = self._canvas(frame_width=720, frame_height=480)

        far = canvas._clamp_offset(QPointF(99999, 99999), 1.0)
        # 拖到最右下：画面左侧留出的空白上限是 DRAG_PADDING
        self.assertAlmostEqual(DRAG_PADDING, far.x(), delta=1)
        self.assertAlmostEqual(DRAG_PADDING, far.y(), delta=1)

        near = canvas._clamp_offset(QPointF(-99999, -99999), 1.0)
        # 拖到最左上：右侧/下方留出的空白上限同样是 DRAG_PADDING
        self.assertAlmostEqual(DRAG_PADDING, canvas.width() - (near.x() + 720), delta=1)
        self.assertAlmostEqual(DRAG_PADDING, canvas.height() - (near.y() + 480), delta=1)

    def test_small_frame_drag_reveals_background(self):
        """适应窗口后拖动，画面拉开底色，但空白不会超过 DRAG_PADDING。"""
        canvas = self._canvas()
        canvas.set_frame(self._white_frame(2560, 1440))
        canvas.set_rect((2100, 1300, 2500, 1400))
        canvas.zoom_fit()
        self.assertAlmostEqual(0, self._visible_left_margin(canvas), delta=1)

        self._press_empty_area(canvas)
        origin = canvas._drag_origin
        # 拖很远，空白应被夹在 DRAG_PADDING
        self._move(canvas, QPointF(origin.x() + 5000, origin.y()))
        self.assertAlmostEqual(DRAG_PADDING, self._visible_left_margin(canvas), delta=1)

    def test_zoom_notification_reports_scale(self):
        """缩放回调只带具体比例，供按钮刷新。"""
        canvas = self._canvas()
        events = []
        canvas.zoom_changed.connect(lambda scale: events.append(round(scale, 4)))
        canvas.zoom_in()
        self.assertEqual(1, len(events))
        canvas.zoom_fit()
        self.assertEqual(2, len(events))
        expected = round(min(canvas.width() / 2560, canvas.height() / 1440), 4)
        self.assertAlmostEqual(expected, events[1], places=4)


@unittest.skipIf(QApplication is None, f"缺少 Qt 运行依赖，跳过页签联动测试：{QT_ERROR}")
class TestTabEditing(unittest.TestCase):
    """编辑与保存分离：拖拽只改临时值，保存才落盘。

    这组用例的存在理由：旧实现「编辑即生效」时，重置按钮永远无事可做、误拖无法撤销，
    而三个按钮看起来都正常。只有把「拖动后配置是否变化」写成断言，这类问题才会暴露。
    """

    @classmethod
    def setUpClass(cls):
        """创建 QApplication 与临时配置目录。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """为每个用例准备一份独立的配置对象与页签。"""
        import tempfile

        from ok import og
        from ok.util.config import Config

        if processor is None:
            self.skipTest("缺少 ok 运行依赖")

        option = processor.build_config_option()
        self._config = Config(option.name, option.default_config, folder=tempfile.mkdtemp())

        config = self._config

        class _FakeGlobalConfig:
            def get_config(self, name):
                if name != option.name:
                    raise RuntimeError("未注册")
                return config

        self._previous = getattr(og, "global_config", None)
        og.global_config = _FakeGlobalConfig()
        self.tab = ScreenshotMaskTab()
        image = QImage(1280, 720, QImage.Format.Format_RGB888)
        image.fill(0x808080)
        self.tab.canvas.set_frame(image)
        self.tab._reload_from_config()

    def tearDown(self):
        """还原全局配置并销毁页签。

        必须显式 close + deleteLater + 处理事件：上游 `enable_touch_scrolling` 给每个页签
        挂了全局事件过滤器，旧页签被 GC 回收后该过滤器仍可能被调用，表现为下一个用例
        在构造阶段炸出一串 `_TouchScrollClickGuard` 递归错误。
        """
        from ok import og

        self.tab.close()
        self.tab.deleteLater()
        QApplication.processEvents()
        if self._previous is not None:
            og.global_config = self._previous

    def test_drag_does_not_write_config(self):
        """拖动只改临时值：配置值保持不变，保存按钮变为可用。"""
        before = dict(self._config)
        self.tab._on_rect_changed((900, 600, 1100, 700))
        self.assertEqual(before, dict(self._config))
        self.assertTrue(self.tab.save_button.isEnabled())
        # 用 isHidden 而不是 isVisible：页签本身未 show，子控件的 isVisible 恒为 False。
        self.assertFalse(self.tab.pending_label.isHidden())

    def test_save_writes_config(self):
        """点「保存配置」后数值落盘，且能还原成同一矩形。"""
        self.tab._on_rect_changed((900, 600, 1100, 700))
        self.tab.save_config()
        self.assertEqual((900, 600, 1100, 700), geometry.resolve_rect(self._config, 1280, 720))
        self.assertFalse(self.tab.save_button.isEnabled())

    def test_reset_discards_pending(self):
        """「重置为配置值」丢弃临时调整，回到已保存值。"""
        saved = geometry.resolve_rect(self._config, 1280, 720)
        self.tab._on_rect_changed((900, 600, 1100, 700))
        self.tab.reset_to_saved()
        self.assertEqual(saved, self.tab.canvas.mask_rect())
        self.assertFalse(self.tab.save_button.isEnabled())

    def test_reset_to_default_restores_upstream_geometry(self):
        """「重置为默认值」把几何还原成上游的右下角 13% × 2.5%。"""
        self.tab._on_rect_changed((100, 100, 500, 300))
        self.tab.save_config()
        self.assertNotEqual(geometry.DEFAULT_VALUES[geometry.KEY_WIDTH], self._config[geometry.KEY_WIDTH])

        self.tab.reset_to_default()
        defaults = self.tab._config().default
        self.assertEqual(defaults[geometry.KEY_ANCHOR], self._config[geometry.KEY_ANCHOR])
        for key in geometry.NUMERIC_KEYS:
            self.assertEqual(defaults[key], self._config[key])
        self.assertEqual((1114, 702, 1280, 720), self.tab.canvas.mask_rect())

    def test_reset_to_default_keeps_enabled_switch(self):
        """「重置为默认值」不动开关：关掉遮罩后点它不该被偷偷打开。"""
        self.tab.enable_switch.setChecked(False)
        self.assertFalse(self._config[geometry.KEY_ENABLED])
        self.tab.reset_to_default()
        self.assertFalse(self._config[geometry.KEY_ENABLED])
        self.assertFalse(self.tab.enable_switch.isChecked())

    def test_reset_to_default_disabled_when_already_default(self):
        """已在默认值时按钮不可用；改过之后变为可用。"""
        self.tab.reset_to_default()
        self.assertFalse(self.tab.default_button.isEnabled())
        self.tab._on_rect_changed((100, 100, 500, 300))
        self.tab.save_config()
        self.assertTrue(self.tab.default_button.isEnabled())

    def test_reset_to_saved_disabled_without_pending(self):
        """没有临时调整时「重置为配置值」不可用——它没有可丢弃的东西。

        与「重置为默认值」一致：按钮在无事可做时灰化，避免用户点了没反应却以为失灵。
        """
        self.assertFalse(self.tab.reset_button.isEnabled())

        self.tab._on_rect_changed((900, 600, 1100, 700))
        self.assertTrue(self.tab.reset_button.isEnabled())

        self.tab.reset_to_saved()
        self.assertFalse(self.tab.reset_button.isEnabled())

    def test_reset_to_saved_disabled_after_save(self):
        """保存后界面等于已保存值，重置按钮重新灰化。"""
        self.tab._on_rect_changed((900, 600, 1100, 700))
        self.assertTrue(self.tab.reset_button.isEnabled())

        self.tab.save_config()
        self.assertFalse(self.tab.reset_button.isEnabled())

    def test_reset_to_saved_disabled_after_drag_back_to_saved(self):
        """拖回原位置后也算无临时调整，重置按钮灰化。"""
        saved = self.tab.canvas.mask_rect()
        self.tab._on_rect_changed((900, 600, 1100, 700))
        self.assertTrue(self.tab.reset_button.isEnabled())

        self.tab._on_rect_changed(saved)
        self.assertFalse(self.tab.reset_button.isEnabled())

    def test_disabling_hides_edit_box(self):
        """关闭开关后预览不画遮罩框，且已保存配置不变。"""
        before = dict(self._config)
        self.tab.enable_switch.setChecked(False)
        self.assertIsNone(self.tab.canvas.mask_rect())
        self.assertFalse(before[geometry.KEY_ENABLED] == self._config[geometry.KEY_ENABLED])
        self.assertEqual(before[geometry.KEY_WIDTH], self._config[geometry.KEY_WIDTH])

    def test_reenabling_restores_edit_box(self):
        """重新开启后按原配置恢复编辑框。"""
        self.tab.enable_switch.setChecked(False)
        self.tab.enable_switch.setChecked(True)
        self.assertEqual((1114, 702, 1280, 720), self.tab.canvas.mask_rect())

    def test_switch_writes_immediately(self):
        """开关不参与分离机制：它即时生效并落盘。"""
        self.tab.enable_switch.setChecked(False)
        self.assertFalse(self._config[geometry.KEY_ENABLED])

    def test_zoom_label_always_shows_percentage(self):
        """比例标签永远显示具体百分比，包括刚点完「适应窗口」。

        显示「Fit」等于把已算出的比例藏起来，用户还会以为拖拽时比例会变。
        """
        self.tab.canvas.resize(800, 480)
        self.tab.canvas.zoom_fit()
        expected = round(min(800 / 1280, 480 / 720) * 100)
        self.assertEqual(f"{expected}%", self.tab.zoom_label.text())
        self.assertNotEqual("Fit", self.tab.zoom_label.text())

        self.tab.canvas.zoom_reset()
        self.assertEqual("100%", self.tab.zoom_label.text())

    def test_reset_button_disabled_at_one_to_one(self):
        """已在 1:1 时「1:1」按钮不可用——重复点击无意义。"""
        self.tab.canvas.zoom_reset()
        self.assertFalse(self.tab.zoom_reset_button.isEnabled())
        self.tab.canvas.zoom_in()
        self.assertTrue(self.tab.zoom_reset_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
