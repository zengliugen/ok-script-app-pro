# -*- coding: utf-8 -*-
"""OCR 裁剪外扩：修复「特征框精确包裹文字却识别不到」的缺陷。

用户实测：`ok_scenes/scene-0002` 的「靶场入口」场景识别不到，但从画面上看特征框
明明完整包住了文字。

**排查澄清了两件事**（都推翻了最初的猜测）：

1. **不是坐标偏移，也不是框太小**。用像素阈值直接量文字墨迹的真实范围是
   `(251,82)-(406,120)`，特征框是 `(248,79)-(410,125)`——四边各留 3~5px 余量，
   完整包裹。
2. **不是「检测器在小图上失效」**。真正的成因有两层，都靠外扩几个像素一并解决。

成因一：DB 检测器把文字区域 `unclip`（向外扩张）后作为识别输入，扩张后的框一旦
超出裁剪边界就会被裁掉。实测同一段文字、四边留空不同：

| 文字上方留空 | 结果 |
|---|---|
| 0px | `'五'`、`'分'`（碎片） |
| 2px | 空结果 |
| **3px（特征框原样）** | **`'TT'`** |
| 4px | `'靶场入口'` 0.998 |
| 8px | `'靶场入口'` 0.999 |

成因二：检测器把输入高度吸附到 32 的倍数
（`resize_h = max(round(resize_h / 32) * 32, 32)`），高度 44–47 会被压到 32，笔画
糊在一起。外扩后高度越过 48，这一层也避开。

因此 `match_text` 在裁剪时向外扩 `TEXT_PAD = 8`，与 `match_template` 的搜索区
外扩一致。原先 `OcrAdapter` 里的「放大 2 倍」是基于错误归因的补丁，已移除。
"""

import unittest

from extensions.capabilities.scene_manager import recognize

try:
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值依赖
    np = None
    NUMPY_ERROR = str(exc)


class _Recorder:
    """记录收到的裁剪图尺寸。"""

    def __init__(self, texts=None):
        """绑定要返回的文本。

        Args：
            texts：返回的文本列表。
        """
        self.texts = texts or []
        self.shapes = []

    def __call__(self, frame, rect=None):
        """记录尺寸并返回固定文本。

        Args：
            frame：收到的图。
            rect：区域矩形。

        Returns：
            文本列表。
        """
        self.shapes.append((frame.shape[:2], rect))
        return list(self.texts)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestTextPad(unittest.TestCase):
    """裁剪外扩。"""

    def _frame(self, width=200, height=100):
        """造一张图。"""
        return np.zeros((height, width, 3), dtype=np.uint8)

    def test_pad_is_eight(self):
        """外扩 8px，与模板匹配的搜索区外扩一致。"""
        self.assertEqual(recognize.TEXT_PAD, 8)

    def test_crop_expanded_by_pad(self):
        """裁剪区域四边各外扩 pad 像素，并把尺寸补齐到 32 的倍数。"""
        recorder = _Recorder(["靶场入口"])
        frame = self._frame()
        recognize.match_text(frame, "靶场入口", (50, 40, 120, 70), 0.8, ocr=recorder)

        (height, width), rect = recorder.shapes[0][0], recorder.shapes[0][1]
        self.assertEqual(rect, (42, 32, 128, 78), "预期四边各扩 8")
        # 外扩后 86x46，再按 ceil 补齐到 32 的倍数 → 96x64
        self.assertEqual(width, 96, "宽度应补齐到 32 的倍数")
        self.assertEqual(height, 64, "高度应补齐到 32 的倍数")
        self.assertEqual(width % 32, 0)
        self.assertEqual(height % 32, 0)

    def test_sent_size_is_multiple_of_32(self):
        """送 OCR 的图宽高都是 32 的倍数——检测器吸附因此变成恒等。"""
        frame = self._frame(200, 100)
        for rect in ((50, 40, 60, 50), (10, 10, 130, 90), (5, 5, 40, 30)):
            recorder = _Recorder(["x"])
            recognize.match_text(frame, "x", rect, 0.5, ocr=recorder)
            (height, width), _r = recorder.shapes[0]
            self.assertEqual(width % recognize.OCR_SIDE_MULTIPLE, 0, f"{rect} 宽度未补齐")
            self.assertEqual(height % recognize.OCR_SIDE_MULTIPLE, 0, f"{rect} 高度未补齐")

    def test_pad_to_multiple_never_shrinks(self):
        """补齐只会放大，绝不缩小——这是「永不压缩」的结构保证。"""
        for h in (1, 17, 31, 32, 33, 46, 47, 48, 65, 100, 127):
            image = np.zeros((h, 50, 3), dtype=np.uint8)
            padded = recognize._pad_to_multiple(image, recognize.OCR_SIDE_MULTIPLE)
            self.assertGreaterEqual(padded.shape[0], h, f"高 {h} 被缩小了")
            self.assertEqual(padded.shape[0] % recognize.OCR_SIDE_MULTIPLE, 0)

    def test_pad_to_multiple_keeps_original_pixels(self):
        """补齐只追加像素，不改动原有内容（用复制边缘而非缩放）。"""
        image = np.full((46, 40, 3), 7, dtype=np.uint8)
        image[:5, :5] = 200
        padded = recognize._pad_to_multiple(image, recognize.OCR_SIDE_MULTIPLE)
        self.assertEqual(padded.shape[0], 64)
        self.assertEqual(padded.shape[1], 64)
        self.assertTrue(np.array_equal(padded[:46, :40], image), "原有像素必须原样保留")

    def test_expansion_clipped_to_frame(self):
        """贴近画面边缘时外扩被裁到边界内，不越界。"""
        recorder = _Recorder(["x"])
        frame = self._frame(200, 100)
        recognize.match_text(frame, "x", (2, 3, 15, 20), 0.5, ocr=recorder)
        _shape, rect = recorder.shapes[0]
        self.assertEqual(rect[0], 0, "左边不能为负")
        self.assertEqual(rect[1], 0, "上边不能为负")

        recorder2 = _Recorder(["x"])
        recognize.match_text(frame, "x", (185, 85, 198, 96), 0.5, ocr=recorder2)
        _shape2, rect2 = recorder2.shapes[0]
        self.assertEqual(rect2[2], 200, "右边不能超过帧宽")
        self.assertEqual(rect2[3], 100, "下边不能超过帧高")

    def test_hit_uses_similarity_threshold(self):
        """命中判定仍按相似度阈值，不受外扩影响。"""
        frame = self._frame()
        hit, confidence = recognize.match_text(frame, "靶场入口", (50, 40, 120, 70), 0.8, ocr=_Recorder(["靶场入口"]))
        self.assertTrue(hit)
        self.assertAlmostEqual(confidence, 1.0)

        hit2, _conf2 = recognize.match_text(frame, "靶场入口", (50, 40, 120, 70), 0.8, ocr=_Recorder(["完全无关"]))
        self.assertFalse(hit2)

    def test_no_ocr_returns_miss(self):
        """没有 OCR 引擎时按未命中处理，不静默判命中。"""
        hit, confidence = recognize.match_text(self._frame(), "靶场入口", (50, 40, 120, 70), 0.8, ocr=None)
        self.assertFalse(hit)
        self.assertEqual(confidence, 0.0)

    def test_empty_frame_returns_miss(self):
        """空帧返回未命中。"""
        hit, _conf = recognize.match_text(None, "靶场入口", (50, 40, 120, 70), 0.8, ocr=_Recorder(["x"]))
        self.assertFalse(hit)


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestAdapterPassesThrough(unittest.TestCase):
    """`OcrAdapter` 不再改图——外扩在识别层做。"""

    def test_adapter_does_not_resize(self):
        """适配器原样送检，图尺寸不变。"""
        from extensions.capabilities.scene_manager import runtime

        received = []

        class _Task:
            def ocr(self, frame=None):
                received.append(frame.shape[:2])

                class _Box:
                    def __init__(self, name):
                        self.name = name

                return [_Box("靶场入口")]

        adapter = runtime.OcrAdapter(_Task())
        frame = np.zeros((46, 162, 3), dtype=np.uint8)
        self.assertEqual(adapter(frame, (0, 0, 162, 46)), ["靶场入口"])
        self.assertEqual(received[0], (46, 162), "不应被放大")

    def test_adapter_skips_empty(self):
        """空帧不触发 OCR。"""
        from extensions.capabilities.scene_manager import runtime

        calls = []

        class _Task:
            def ocr(self, frame=None):
                calls.append(1)
                return []

        adapter = runtime.OcrAdapter(_Task())
        self.assertEqual(adapter(None), [])
        self.assertEqual(adapter(np.zeros((0, 0, 3), dtype=np.uint8)), [])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
