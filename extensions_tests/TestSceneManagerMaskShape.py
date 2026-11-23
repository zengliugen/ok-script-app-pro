# -*- coding: utf-8 -*-
"""掩码形状：几何、编辑与序列化。

## 这个模块解决什么

掩码形状描述「模板内部哪些像素参与匹配」，坐标相对**特征包围盒**（不是整帧）。
与 `shapes.Shape`（相对整帧、描述特征定位框）是两套坐标系，刻意分开——
混成一个会让「我调了掩码，怎么搜索区域也变了」变成无法解释的现象。

## `none` 与 `rect` 的区别

`none` 是「没有形状这个概念」（只靠颜色筛选），`rect` 是「形状恰好是矩形」
（有可拖顶点，能把矩形拖小）。合并会让「我要排除边缘一圈」无处表达。
"""

import math
import unittest

from extensions.capabilities.scene_manager import mask_shape

try:
    import numpy as np

    NUMPY_ERROR = None
except ModuleNotFoundError as exc:  # 缺数值依赖
    np = None
    NUMPY_ERROR = str(exc)


class TestConstruct(unittest.TestCase):
    """构造与校验。"""

    def test_none_has_no_points(self):
        """`none` 不需要点。"""
        shape = mask_shape.MaskShape.none()
        self.assertEqual(shape.kind, mask_shape.MASK_NONE)
        self.assertEqual(shape.points, [])

    def test_rect_needs_two_points(self):
        """矩形必须两个对角点，且右下大于左上。"""
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.1, 0.1)])
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.5, 0.5), (0.2, 0.2)])

    def test_polygon_needs_three_points(self):
        """多边形至少三个点。"""
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape(mask_shape.MASK_POLYGON, [(0.1, 0.1), (0.5, 0.5)])

    def test_circle_needs_positive_radius(self):
        """圆半径必须为正。"""
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape(mask_shape.MASK_CIRCLE, [(0.5, 0.5)], radius=0)

    def test_sector_sweep_range(self):
        """扇角必须落在 (0, 360]。"""
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape(mask_shape.MASK_SECTOR, [(0.5, 0.5)], sweep=0, radius=0.4)
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape(mask_shape.MASK_SECTOR, [(0.5, 0.5)], sweep=361, radius=0.4)

    def test_coordinates_may_exceed_box(self):
        """坐标可以超出 0–1：形状允许伸出包围盒。

        **这是真实缺陷修复后的行为**。早期实现把坐标夹在 0–1 并在越界时抛异常，
        而异常发生在 `mouseMoveEvent` 里——界面表现是「拖到一半就不动了，
        没有任何提示」。用户把圆形拖到只覆盖模板一角、或让矩形的角伸到模板外
        一部分，都是合法诉求。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_POLYGON, [(1.2, 0.1), (0.5, 0.5), (0.1, 0.9)])
        self.assertEqual(shape.points[0], (1.2, 0.1))

    def test_absurd_coordinates_rejected(self):
        """荒谬坐标仍要报错，避免换算写错时静默存进 JSON。

        允许一定程度越界是为了「形状伸出包围盒」，不是为了容纳天文数字。
        """
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape(mask_shape.MASK_POLYGON, [(99.0, 0.1), (0.5, 0.5), (0.1, 0.9)])

    def test_unknown_kind_rejected(self):
        """未知种类报错。"""
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape("triangle", [(0.1, 0.1), (0.5, 0.5), (0.1, 0.9)])


@unittest.skipIf(np is None, f"缺少数值依赖：{NUMPY_ERROR}")
class TestMask(unittest.TestCase):
    """掩码生成。"""

    def test_none_returns_none(self):
        """`none` 返回 None 而不是全 255。

        调用方据此可以跳过掩码传参（`cv2.matchTemplate` 不带 mask 更快），
        而语义完全等价。返回全 255 会让「无形状」在代码里看起来像
        「形状是整块矩形」，两种状态又混在一起。
        """
        self.assertIsNone(mask_shape.MaskShape.none().mask(50, 50))

    def test_rect_covers_whole_box(self):
        """全矩形覆盖 100%。

        这条曾经是 bug：矩形只存两个对角点，直接喂给 `cv2.fillPoly` 会退化成
        画一条线，覆盖率从 100% 掉到 1%——而界面上看不出异常，只表现为
        「这个特征从来匹配不到」。
        """
        mask = mask_shape.MaskShape.full_rect().mask(100, 80)
        self.assertEqual(mask.shape, (80, 100))
        self.assertAlmostEqual(float((mask > 0).mean()), 1.0, places=3)

    def test_circle_coverage_near_expected(self):
        """正方形里的圆覆盖率接近 π/4 ≈ 78.5%。"""
        mask = mask_shape.MaskShape.centered_circle(radius=0.5).mask(200, 200)
        coverage = float((mask > 0).mean())
        self.assertAlmostEqual(coverage, 0.785, delta=0.02)

    def test_circle_is_round_not_ellipse_in_wide_box(self):
        """宽扁包围盒里的圆仍是正圆（纵向半径与横向同像素数）。

        若纵向也按高度比例算，宽扁盒里的圆会被压成椭圆。这里用「上下贴边」
        的半径让圆正好内切：覆盖面积 = πr² 被盒高裁掉上下部分后的结果，
        与「按椭圆算」的结果明显不同——椭圆会把纵向半径缩小，覆盖率随之变小，
        图形也从圆变成扁椭圆。
        """
        width, height = 400, 100
        radius = 0.125  # × 400 = 50px，正好等于盒高的一半
        mask = mask_shape.MaskShape.centered_circle(radius=radius).mask(width, height)
        radius_px = radius * width
        expected = 3.14159 * radius_px**2 / (width * height)
        coverage = float((mask > 0).mean())
        self.assertAlmostEqual(coverage, expected, delta=0.02)
        # 圆应当上下贴边、左右留白：若被压成椭圆，左右会多出空白
        column = mask[:, width // 2]
        self.assertGreater(int((column > 0).sum()), height - 3, "圆在纵向应几乎占满盒高")

    def test_sector_quarter_circle(self):
        """四分之一扇形覆盖率约 π/4 ÷ 4 ≈ 19.6%（半径占满时）。"""
        shape = mask_shape.MaskShape.centered_sector(angle=0, sweep=90, radius=0.5)
        mask = shape.mask(200, 200)
        coverage = float((mask > 0).mean())
        self.assertAlmostEqual(coverage, 0.196, delta=0.02)

    def test_polygon_diamond_half(self):
        """菱形的面积是外接矩形的一半。"""
        mask = mask_shape.MaskShape.diamond().mask(100, 100)
        self.assertAlmostEqual(float((mask > 0).mean()), 0.5, delta=0.02)


class TestEditing(unittest.TestCase):
    """控制点拖动与顶点增删。"""

    def test_circle_handles_are_center_and_radius(self):
        """圆的控制点是圆心与右边界点。"""
        shape = mask_shape.MaskShape.centered_circle(radius=0.3)
        handles = shape.handles()
        self.assertEqual(len(handles), 2)
        self.assertEqual(handles[0], (0.5, 0.5))

    def test_move_center_keeps_radius(self):
        """拖圆心只改位置，不改半径。"""
        shape = mask_shape.MaskShape.centered_circle(radius=0.3)
        moved = shape.moved(0, 0.3, 0.4)
        self.assertEqual(moved.points[0], (0.3, 0.4))
        self.assertAlmostEqual(moved.radius, 0.3)

    def test_move_radius_handle_changes_radius(self):
        """拖半径点改半径。"""
        shape = mask_shape.MaskShape.centered_circle(radius=0.3)
        moved = shape.moved(1, 0.9, 0.5)
        self.assertAlmostEqual(moved.radius, 0.4, places=4)

    def test_move_rect_normalizes_order(self):
        """矩形被拖成反向时自动规整，不报错。

        用户拖动时不会记得「右下必须大于左上」，中途必然出现反向坐标。
        这时报错会打断拖动（每拖一像素弹一次），体验很差。
        """
        shape = mask_shape.MaskShape.full_rect()
        moved = shape.moved(0, 0.9, 0.9)
        x1, y1 = moved.points[0]
        x2, y2 = moved.points[1]
        self.assertLess(x1, x2)
        self.assertLess(y1, y2)

    def test_move_out_of_range_not_clamped(self):
        """拖出包围盒的坐标保留原值，不夹到 0–1。

        夹住会让形状永远拖不出包围盒的边界，而「只覆盖模板一角」是合法诉求。
        """
        shape = mask_shape.MaskShape.full_rect()
        moved = shape.moved(0, -0.5, -0.5)
        self.assertEqual(moved.points[0], (-0.5, -0.5))

    def test_insert_vertex_at_edge(self):
        """在指定边插入顶点，位置落在该边上。"""
        shape = mask_shape.MaskShape.diamond()
        inserted = shape.inserted(0, 0.75, 0.25)
        self.assertEqual(len(inserted.points), 5)
        self.assertIn((0.75, 0.25), inserted.points)

    def test_insert_only_for_polygon(self):
        """只有多边形支持插点。"""
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape.full_rect().inserted(0, 0.5, 0.5)

    def test_remove_vertex(self):
        """删除顶点。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_POLYGON, [(0, 0), (1, 0), (1, 1), (0, 1)])
        removed = shape.removed(0)
        self.assertEqual(len(removed.points), 3)
        self.assertNotIn((0.0, 0.0), removed.points)

    def test_cannot_remove_below_three(self):
        """删到不足 3 个点必须报错。

        两个点构不成多边形，掩码面积为零，该特征永远匹配不到——
        静默允许会让用户配出一个「看着正常但永不命中」的特征。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_POLYGON, [(0, 0), (1, 0), (1, 1)])
        with self.assertRaises(mask_shape.MaskShapeError):
            shape.removed(0)

    def test_editing_returns_new_instance(self):
        """编辑返回新实例，不改原对象。

        不可变值让「取消/撤销」有实现基础，也避免「改到一半失败」
        留下半个坏形状。
        """
        shape = mask_shape.MaskShape.full_rect()
        shape.moved(0, 0.2, 0.2)
        self.assertEqual(shape.points[0], (0.0, 0.0))


class TestNearestEdge(unittest.TestCase):
    """找最近的边（加点用）。"""

    def test_finds_edge_and_projection(self):
        """返回边序号与投影点。"""
        shape = mask_shape.MaskShape.diamond()
        result = shape.nearest_edge(0.75, 0.25, 0.05)
        self.assertIsNotNone(result)
        edge, point = result
        self.assertEqual(edge, 0)
        self.assertAlmostEqual(point[0], 0.75, places=3)
        self.assertAlmostEqual(point[1], 0.25, places=3)

    def test_returns_none_far_from_edges(self):
        """离所有边都远时返回 None（避免在空白处右键也弹加点菜单）。"""
        shape = mask_shape.MaskShape.full_rect()
        # 矩形中心到四条边的距离都是 0.5，远大于容差
        self.assertIsNone(shape.nearest_edge(0.5, 0.5, 0.05))

    def test_non_polygon_has_no_edges(self):
        """非多边形没有边概念。"""
        self.assertIsNone(mask_shape.MaskShape.centered_circle().nearest_edge(0.5, 0.5, 0.5))


class TestHandleAt(unittest.TestCase):
    """按坐标命中控制点。"""

    def test_hits_within_tolerance(self):
        """容差内命中。"""
        shape = mask_shape.MaskShape.full_rect()
        self.assertEqual(shape.handle_at(0.01, 0.01, 0.05), 0)

    def test_misses_outside_tolerance(self):
        """容差外不命中。"""
        shape = mask_shape.MaskShape.full_rect()
        self.assertIsNone(shape.handle_at(0.5, 0.5, 0.05))


class TestWithKind(unittest.TestCase):
    """切换形状类型。"""

    def test_switch_to_circle_gives_centered_shape(self):
        """切到圆时自动给一个居中的可编辑形状。

        留空会让用户面对一块什么都没有的图，得先猜「怎么开始画」。
        自动给一个居中的形状，最坏情况是他不满意再拖。
        """
        shape = mask_shape.MaskShape.from_kind(mask_shape.MASK_CIRCLE)
        self.assertEqual(shape.kind, mask_shape.MASK_CIRCLE)
        self.assertEqual(shape.points[0], (0.5, 0.5))
        self.assertGreater(shape.radius, 0)

    def test_switch_to_none_clears(self):
        """切到「无形状」清空几何。"""
        shape = mask_shape.MaskShape.full_rect().with_kind(mask_shape.MASK_NONE)
        self.assertEqual(shape.kind, mask_shape.MASK_NONE)
        self.assertEqual(shape.points, [])

    def test_switch_keeps_approximate_position(self):
        """切换时保留大致位置：新形状以原形状的包围盒中心为心。

        完全不保留会让用户每换一次形状都要重新找位置。
        """
        rect = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.6, 0.6)])
        circle = rect.with_kind(mask_shape.MASK_CIRCLE)
        self.assertAlmostEqual(circle.points[0][0], 0.4, places=4)
        self.assertAlmostEqual(circle.points[0][1], 0.4, places=4)

    def test_polygon_default_is_inset_not_full(self):
        """多边形的初始形状是「内缩 20% 的矩形」，既不铺满也不缩成一点。

        两种极端都不能要：

        - **铺满**（`(0,0)~(1,1)`）覆盖率 100%，与 `rect` 完全等价，用户
          看到的是「切过来什么都没变」，找不到可以拖的顶点；
        - **缩成一点**（早期从圆切过来时只有 1.4% 宽）几乎看不见形状，
          用户以为「多边形没生效」，也就无从拖拽。

        内缩 20% 两头都避开。
        """
        shape = mask_shape.MaskShape.from_kind(mask_shape.MASK_POLYGON)
        self.assertEqual(len(shape.points), 4)
        self.assertNotIn((0.0, 0.0), shape.points, "不应铺满整盒")
        xs = [point[0] for point in shape.points]
        ys = [point[1] for point in shape.points]
        self.assertAlmostEqual(max(xs) - min(xs), 0.8, places=3)
        self.assertAlmostEqual(max(ys) - min(ys), 0.8, places=3)

    def test_circle_radius_respects_short_side(self):
        """宽扁包围盒上，初始圆半径受短边约束（占短边 70%）。

        模板常是宽扁条（本仓库的色卡 430×47）。若按宽度一半取半径，圆会远超
        盒高——覆盖率接近 100%，等于没筛。

        Note:
            取 70% 而不是 100%（贴边）：贴边的圆在宽扁模板上只有 23px 高，
            用户既看不清圆弧、也几乎点不中 8px 宽的「圆弧带」，交互形同虚设。
        """
        aspect = 47 / 430
        shape = mask_shape.MaskShape.from_kind(mask_shape.MASK_CIRCLE, aspect)
        radius_px = shape.radius * 430
        self.assertAlmostEqual(radius_px, 47 * 0.7, delta=1.0)

    def test_same_kind_returns_self(self):
        """切到同一种类原样返回（不重置用户调好的形状）。"""
        shape = mask_shape.MaskShape.centered_circle(radius=0.2)
        self.assertIs(shape.with_kind(mask_shape.MASK_CIRCLE), shape)

    def test_repeated_switch_does_not_shrink(self):
        """连续切换形状类型不得累积收缩。

        **这是一个真实缺陷的回归测试**。圆的 `bounds()` 是按半径算的小盒，
        早期实现拿它当「半高」再按短边约束算新半径——把一个半径 0.055 的圆
        切到扇形会得到 0.006，连切几次形状就缩成一个点，而用户完全看不出
        是哪一步导致的。
        """
        aspect = 47 / 430
        shape = mask_shape.MaskShape.none()
        radii = []
        for kind in (
            mask_shape.MASK_RECT,
            mask_shape.MASK_CIRCLE,
            mask_shape.MASK_SECTOR,
            mask_shape.MASK_CIRCLE,
        ):
            shape = shape.with_kind(kind, aspect)
            if shape.kind in (mask_shape.MASK_CIRCLE, mask_shape.MASK_SECTOR):
                radii.append(shape.radius)
        self.assertEqual(len(set(radii)), 1, f"来回切换后半径发生了变化：{radii}")

    def test_polygon_after_circle_is_not_degenerate(self):
        """从圆切到多边形后，多边形要足够大、看得见。

        Note:
            早期实现用「圆的半径撑起的方盒」当多边形：宽扁模板上那个盒只有 11%
            宽（色卡 430×47 时覆盖率 1.4%），用户切过去几乎看不到形状，
            以为「多边形没生效」。

            现在改用「内缩 20% 的包围盒」，与模板比例无关，任何模板上都是
            80% 大小。
        """
        aspect = 47 / 430
        circle = mask_shape.MaskShape.from_kind(mask_shape.MASK_CIRCLE, aspect)
        polygon = circle.with_kind(mask_shape.MASK_POLYGON, aspect)
        xs = [point[0] for point in polygon.points]
        ys = [point[1] for point in polygon.points]
        self.assertAlmostEqual(max(xs) - min(xs), 0.8, places=3)
        self.assertAlmostEqual(max(ys) - min(ys), 0.8, places=3)


class TestGrabDispatch(unittest.TestCase):
    """按下位置的语义分派（移动 / 缩放 / 旋转 / 张角）。

    这套判定是「市面通用图形编辑器」的手感来源：内部拖动移动、边角拖动缩放、
    中心拖动旋转。判定顺序固定为「中心 → 边/弧 → 内部 → 空白」，因为三者会
    重叠，先判中心才能让「想转一下」稳定命中——否则形状越小越转不动。
    """

    def test_rect_inside_moves(self):
        """矩形内部（远离中心与边）是移动。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)])
        self.assertEqual(shape.grab_at(0.35, 0.3, 0.02), (mask_shape.GRAB_MOVE, None))

    def test_rect_center_rotates(self):
        """矩形中心附近是旋转。

        中心点既在内部也在（对小形状来说）边上，先判中心才能稳定命中旋转。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)])
        self.assertEqual(shape.grab_at(0.5, 0.5, 0.02), (mask_shape.GRAB_ROTATE, None))

    def test_rect_edges_resize(self):
        """矩形四条边与四个角都是缩放，且各自返回对应标识。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)])
        cases = {
            (0.8, 0.5): mask_shape.RECT_RIGHT,
            (0.2, 0.5): mask_shape.RECT_LEFT,
            (0.5, 0.2): mask_shape.RECT_TOP,
            (0.5, 0.8): mask_shape.RECT_BOTTOM,
            (0.2, 0.2): mask_shape.RECT_TL,
            (0.8, 0.2): mask_shape.RECT_TR,
            (0.8, 0.8): mask_shape.RECT_BR,
            (0.2, 0.8): mask_shape.RECT_BL,
        }
        for (x, y), expected in cases.items():
            mode, detail = shape.grab_at(x, y, 0.02)
            self.assertEqual(mode, mask_shape.GRAB_RESIZE, f"({x},{y}) 应当是缩放")
            self.assertEqual(detail, expected, f"({x},{y}) 的边标识不对")

    def test_rect_corner_wins_over_edge(self):
        """角优先于边：角同时贴着两条边，先判边就永远拖不到角。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)])
        mode, detail = shape.grab_at(0.8, 0.2, 0.02)
        self.assertEqual((mode, detail), (mask_shape.GRAB_RESIZE, mask_shape.RECT_TR))

    def test_outside_shape_is_none(self):
        """形状外返回 GRAB_NONE，让画布去平移视图。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)])
        self.assertEqual(shape.grab_at(0.05, 0.05, 0.02), (mask_shape.GRAB_NONE, None))

    def test_circle_center_moves_not_rotates(self):
        """圆的圆心是移动，不是旋转。

        **这是一个真实缺陷的修复**。早期给圆也留了中心旋转区，在圆心附近拖动
        会被判成「旋转」——而圆旋转后画面毫无变化，用户看到的就是「圆拖不动」。
        圆没有可观察的旋转，不该有旋转中心区。
        """
        shape = mask_shape.MaskShape.centered_circle(radius=0.3)
        self.assertEqual(shape.grab_at(0.5, 0.5, 0.02), (mask_shape.GRAB_MOVE, None))
        self.assertEqual(shape.grab_at(0.52, 0.51, 0.02), (mask_shape.GRAB_MOVE, None))

    def test_circle_arc_band_does_not_eat_interior(self):
        """圆弧带不能吃掉大半个内部，否则圆拖不动。

        **这是「圆无法移动」的直接成因**：早期用 `abs(d - r) <= max(tol_x, tol_y)`，
        而宽扁模板上 `tol_y` 是 `tol_x` 的 9 倍（0.028 vs 0.003），半径只有
        0.055 时判定区间覆盖 `[0.026, 0.083]`——内部一半以上都成了「改半径」。
        现在容差统一按宽度换算且只判外侧，内部只剩圆弧带以外那圈。
        """
        radius = 0.0765
        shape = mask_shape.MaskShape(mask_shape.MASK_CIRCLE, [(0.5, 0.5)], radius=radius)
        aspect = 47 / 430
        tol = 0.009
        # 半径 30% / 70% 处必须是移动
        for ratio in (0.3, 0.5, 0.7):
            self.assertEqual(
                shape.grab_at(0.5 + radius * ratio, 0.5, tol, aspect),
                (mask_shape.GRAB_MOVE, None),
                f"半径 {ratio:.0%} 处应可拖动移动",
            )
        # 圆弧上才是改半径
        self.assertEqual(
            shape.grab_at(0.5 + radius, 0.5, tol, aspect),
            (mask_shape.GRAB_RESIZE, "radius"),
        )

    def test_sector_both_edges_reachable(self):
        """扇形的起始边与终止边都能命中（不能只剩一边）。

        **这是「张角只有单边能调」的修复验证**。早期角度容差用未归一化的距离
        换算，得到 70°——整个内部都被判成「拖起始边」，终止边永远点不中。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_SECTOR, [(0.5, 0.5)], angle=0, sweep=90, radius=0.0765)
        aspect = 47 / 430
        tol = 0.009
        for name, deg, expect in (("起始边", 0, "start"), ("终止边", 90, "end")):
            rad = math.radians(deg)
            x = 0.5 + shape.radius * 0.7 * math.cos(rad)
            y = 0.5 + (shape.radius * 0.7 * math.sin(rad)) / aspect
            self.assertEqual(
                shape.grab_at(x, y, tol, aspect),
                (mask_shape.GRAB_SWEEP, expect),
                f"{name}必须可命中",
            )

    def test_sector_interior_moves(self):
        """扇形内部（远离边与弧）是移动。

        早期因为角度容差过大，整个扇形内部都被判成「拖边」，用户无法移动扇形。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_SECTOR, [(0.5, 0.5)], angle=0, sweep=90, radius=0.0765)
        aspect = 47 / 430
        tol = 0.009
        # 45° 方向、半径 40% 处
        rad = math.radians(45)
        x = 0.5 + shape.radius * 0.4 * math.cos(rad)
        y = 0.5 + (shape.radius * 0.4 * math.sin(rad)) / aspect
        self.assertEqual(shape.grab_at(x, y, tol, aspect), (mask_shape.GRAB_MOVE, None))

    def test_sector_edge_only_near_outer_radial_band(self):
        """半径边的判定只在外侧径向范围内生效，靠近圆心一律是移动。

        **这是「扇形无法移动」的第二道防线**。角度容差已经压了上限，但靠近
        圆心的点方位角本身不稳定（一像素抖动跳几十度），按小半径换算出的角度
        容差仍然很大。若不限制径向范围，圆心附近的内部会被判成「拖边」，
        用户想在中间拖动移动却总在改张角。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_SECTOR, [(0.5, 0.5)], angle=0, sweep=90, radius=0.0765)
        aspect = 47 / 430
        tol = 0.009
        # 紧贴起始边方向（0°）、但只在半径 10% 处
        x = 0.5 + shape.radius * 0.1
        self.assertEqual(
            shape.grab_at(x, 0.5, tol, aspect),
            (mask_shape.GRAB_MOVE, None),
            "圆心附近的点不该被判成拖边",
        )
        # 同一方向、半径 70% 处才应该是拖边
        x_far = 0.5 + shape.radius * 0.7
        self.assertEqual(
            shape.grab_at(x_far, 0.5, tol, aspect),
            (mask_shape.GRAB_SWEEP, "start"),
        )

    def test_sector_center_rotates_edges_sweep_arc_resizes(self):
        """扇形：圆心是移动（不是旋转）、两条半径边改张角、圆弧改半径。

        Note:
            **扇形没有中心旋转区**。早期给扇形也留了中心区，在圆心拖动会转
            形状——而形状在圆心附近转几度视觉上几乎没变化，用户以为「拖不动」。
            改角度应该走两条半径边（明确的视觉对象）或角度输入框。

            半径边与圆弧在端点相交，判定顺序必须是**先边后弧**。先判弧会把
            「拖起始边」误判成「改半径」——而用户看到的是边在动、弧却没变，
            很难意识到是判定顺序反了。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_SECTOR, [(0.5, 0.5)], angle=0, sweep=90, radius=0.4)
        self.assertEqual(shape.grab_at(0.5, 0.5, 0.02), (mask_shape.GRAB_MOVE, None))
        self.assertEqual(shape.grab_at(0.85, 0.5, 0.02), (mask_shape.GRAB_SWEEP, "start"))
        self.assertEqual(shape.grab_at(0.5, 0.85, 0.02), (mask_shape.GRAB_SWEEP, "end"))
        self.assertEqual(shape.grab_at(0.78, 0.78, 0.02), (mask_shape.GRAB_RESIZE, "radius"))

    def test_polygon_vertex_resizes(self):
        """多边形顶点是缩放（拖该顶点），内部是移动。"""
        shape = mask_shape.MaskShape.diamond()
        mode, detail = shape.grab_at(0.5, 0.0, 0.02)
        self.assertEqual((mode, detail), (mask_shape.GRAB_RESIZE, 0))
        self.assertEqual(shape.grab_at(0.5, 0.5, 0.02), (mask_shape.GRAB_MOVE, None))

    def test_none_grabs_nothing(self):
        """`none` 不参与任何拖拽（它没有几何可言）。"""
        self.assertEqual(
            mask_shape.MaskShape.none().grab_at(0.5, 0.5, 0.02),
            (mask_shape.GRAB_NONE, None),
        )


class TestRotation(unittest.TestCase):
    """矩形旋转。"""

    def test_rotated_corners_are_rotated(self):
        """旋转 90° 后四角互换（正方形包围盒下）。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)], angle=90)
        corners = [(round(x, 3), round(y, 3)) for x, y in shape.corners(1.0)]
        self.assertEqual(corners, [(0.8, 0.2), (0.8, 0.8), (0.2, 0.8), (0.2, 0.2)])

    def test_angle_zero_keeps_axis_aligned(self):
        """角度为 0 时不旋转，四角就是轴对齐的四个角。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)])
        self.assertEqual(shape.corners(1.0), [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)])

    def test_rotation_uses_aspect_to_avoid_skew(self):
        """宽扁包围盒里旋转不能把矩形拉成平行四边形。

        比例空间不是等比的（色卡是 430×47）。若直接用比例旋转，y 方向的 0.1
        与 x 方向的 0.1 在像素上差 9 倍，转 45° 后四条边不再等长——界面上
        看起来像「形状自己歪了」，而用户只是转了一下。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)], angle=45)
        corners = shape.corners(47 / 430)
        # 换算到像素尺度后，对边应当等长
        lengths = []
        for index in range(4):
            ax, ay = corners[index]
            bx, by = corners[(index + 1) % 4]
            dx = (bx - ax) * 430
            dy = (by - ay) * 47
            lengths.append(math.hypot(dx, dy))
        self.assertAlmostEqual(lengths[0], lengths[2], delta=0.5)
        self.assertAlmostEqual(lengths[1], lengths[3], delta=0.5)

    def test_rotated_rect_hit_test_follows_rotation(self):
        """旋转后命中判定跟着转：转 90° 后原本的「上边」变成左边。

        不换算旋转会让「鼠标明明在斜边上却拖不动」——而用户看不出这是判定
        没跟着转，只会觉得「这个形状坏了」。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)], angle=90)
        mode, detail = shape.grab_at(0.8, 0.5, 0.02)
        self.assertEqual(mode, mask_shape.GRAB_RESIZE)

    def test_dragging_corner_of_rotated_rect_follows_mouse(self):
        """旋转后拖角，角要落到鼠标位置（不能被换算两次）。

        旋转换算只在 `resized` 里做一次。若 `grab_at` 也转一次，两次旋转互相
        抵消一部分，表现为「拖角时矩形不跟鼠标走」——而界面上完全看不出是
        换算重复，只会觉得「这个形状拖不动」。

        Note:
            必须走**真实流程**（`grab_at` 拿 detail → `resized`），不能手写
            角标识：转 90° 后视觉上的右上角，在未旋转坐标系里对应的是左上角。
            手写 RECT_TR 会去改另一条边，测出的失败与被测假设无关。
        """
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)], angle=90)
        # 视觉右上角落在 (0.8, 0.2)（未旋转左上角转 90° 的结果）
        mode, detail = shape.grab_at(0.8, 0.2, 0.02)
        self.assertEqual(mode, mask_shape.GRAB_RESIZE, "视觉上的角必须能命中")
        resized = shape.resized(detail, 0.9, 0.15, aspect=1.0)
        corners = resized.corners(1.0)
        nearest = min(corners, key=lambda point: math.hypot(point[0] - 0.9, point[1] - 0.15))
        self.assertAlmostEqual(nearest[0], 0.9, delta=0.02, msg=f"角没跟到鼠标：{corners}")
        self.assertAlmostEqual(nearest[1], 0.15, delta=0.02, msg=f"角没跟到鼠标：{corners}")


class TestOutOfBoxEditing(unittest.TestCase):
    """越界编辑：形状可以伸出包围盒。"""

    def test_resize_beyond_right(self):
        """右边可以拖到包围盒外。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)])
        resized = shape.resized(mask_shape.RECT_RIGHT, 2.5, 0.5)
        self.assertEqual(resized.points[1][0], 2.5)

    def test_resize_beyond_left(self):
        """左边可以拖到负坐标。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)])
        resized = shape.resized(mask_shape.RECT_LEFT, -0.5, 0.5)
        self.assertEqual(resized.points[0][0], -0.5)

    def test_translate_out_of_box(self):
        """整体平移到包围盒外。"""
        shape = mask_shape.MaskShape(mask_shape.MASK_RECT, [(0.2, 0.2), (0.8, 0.8)])
        moved = shape.translated(0.5, 0.5)
        self.assertEqual(moved.points[0], (0.7, 0.7))

    def test_circle_center_outside_box(self):
        """圆心可以移到包围盒外（只留一部分圆弧落在模板内）。"""
        shape = mask_shape.MaskShape.centered_circle(radius=0.3)
        moved = shape.moved(0, -0.2, 0.5)
        self.assertEqual(moved.points[0], (-0.2, 0.5))

    def test_mask_still_renders_for_outside_shape(self):
        """形状部分在盒外时，掩码只保留盒内那部分，不报错。

        这是越界坐标的实际用途：只保留落在模板内的像素。
        """
        if np is None:
            self.skipTest(f"缺少数值依赖：{NUMPY_ERROR}")
        shape = mask_shape.MaskShape(mask_shape.MASK_CIRCLE, [(0.0, 0.5)], radius=0.5)
        mask = shape.mask(100, 100)
        coverage = float((mask > 0).mean())
        self.assertGreater(coverage, 0)
        self.assertLess(coverage, 0.5, "圆心在左边线上，只有右半圆落在盒内")


class TestVertexCount(unittest.TestCase):
    """多边形顶点数调整。"""

    def test_rebuild_keeps_center(self):
        """改顶点数时保留中心，位置不跳。

        Note:
            尺寸**会**变小一点：N 边形内切于原包围盒的椭圆，边中点贴着边界、
            顶点往里收。这是几何必然（外接会让形状变大），中心的稳定才是用户
            真正在意的——位置跳了会让人以为形状被重置了。
        """
        shape = mask_shape.MaskShape.inset_polygon()
        before = shape.center()
        after = shape.with_vertex_count(7).center()
        self.assertAlmostEqual(before[0], after[0], places=3)
        self.assertAlmostEqual(before[1], after[1], places=3)

    def test_four_vertices_is_rectangle(self):
        """4 边形是轴对齐矩形，不是旋转 45° 的正方形。

        正四边形的外接圆顶点落在 45° 方向上，算出来是旋转的正方形——
        与用户「四边形 = 矩形」的直觉不符。
        """
        shape = mask_shape.MaskShape.diamond().with_vertex_count(4)
        xs = sorted({round(point[0], 3) for point in shape.points})
        ys = sorted({round(point[1], 3) for point in shape.points})
        self.assertEqual(len(xs), 2, "4 边形应当只有两个不同 x（轴对齐）")
        self.assertEqual(len(ys), 2)

    def test_same_count_returns_self(self):
        """已经是规整 N 边形且点数不变时原样返回，不重建。

        否则用户微调过的顶点会在每次触发参数同步时被抹掉。
        """
        shape = mask_shape.MaskShape.inset_polygon()
        self.assertIs(shape.with_vertex_count(4), shape)

    def test_diamond_four_rebuilds_to_rectangle(self):
        """菱形改成 4 边形时会重建成矩形。

        **「点数相同就原样返回」是不够的**：菱形的点数也是 4，但它不是用户
        调「顶点数」时想要的规整形状——直接返回会让「改成 4 边形」变成
        「什么都没变」，用户以为控件失效。
        """
        shape = mask_shape.MaskShape.diamond()
        rebuilt = shape.with_vertex_count(4)
        self.assertIsNot(rebuilt, shape)
        xs = {round(point[0], 3) for point in rebuilt.points}
        ys = {round(point[1], 3) for point in rebuilt.points}
        self.assertEqual(len(xs), 2, "重建后应是轴对齐矩形")
        self.assertEqual(len(ys), 2, "重建后应是轴对齐矩形")

    def test_rejects_below_three(self):
        """少于 3 个顶点必须报错。"""
        with self.assertRaises(mask_shape.MaskShapeError):
            mask_shape.MaskShape.diamond().with_vertex_count(2)

    def test_non_polygon_returns_self(self):
        """非多边形原样返回。"""
        circle = mask_shape.MaskShape.centered_circle(radius=0.3)
        self.assertIs(circle.with_vertex_count(6), circle)


class TestSerialization(unittest.TestCase):
    """序列化往返。"""

    def test_none_roundtrip(self):
        """`none` 只写 kind。"""
        data = mask_shape.MaskShape.none().to_dict()
        self.assertEqual(data, {"kind": "none"})
        restored = mask_shape.MaskShape.from_dict(data)
        self.assertEqual(restored.kind, mask_shape.MASK_NONE)

    def test_circle_roundtrip(self):
        """圆保留圆心与半径。"""
        shape = mask_shape.MaskShape.centered_circle(radius=0.33)
        restored = mask_shape.MaskShape.from_dict(shape.to_dict())
        self.assertEqual(restored, shape)

    def test_sector_roundtrip(self):
        """扇形保留角度与扇角。"""
        shape = mask_shape.MaskShape.centered_sector(angle=45, sweep=120, radius=0.4)
        restored = mask_shape.MaskShape.from_dict(shape.to_dict())
        self.assertEqual(restored, shape)
        self.assertEqual(restored.angle, 45.0)
        self.assertEqual(restored.sweep, 120.0)

    def test_polygon_roundtrip(self):
        """多边形保留全部顶点。"""
        shape = mask_shape.MaskShape.diamond()
        restored = mask_shape.MaskShape.from_dict(shape.to_dict())
        self.assertEqual(restored.points, shape.points)

    def test_missing_data_falls_back_to_none(self):
        """缺失字段按「无形状」处理，不报错。

        升级前保存的场景没有 `mask_shape` 字段，它们的正确语义就是「没有掩码
        形状」。报错会让旧库全部打不开；兜底成矩形会改变它们原有的识别行为，
        而那种改变不报错、只表现为识别率变化。
        """
        self.assertEqual(mask_shape.MaskShape.from_dict(None).kind, mask_shape.MASK_NONE)
        self.assertEqual(mask_shape.MaskShape.from_dict({}).kind, mask_shape.MASK_NONE)


if __name__ == "__main__":
    unittest.main()
