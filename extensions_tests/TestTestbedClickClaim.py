# -*- coding: utf-8 -*-
"""后台点击兜接的判定逻辑：认领、去重、过期。

新靶场要支持后台点击，做法是在窗口过程里补处理「Tk 丢弃的投递点击」。
最危险的失效是**点一次触发两次**：真实输入既进窗口过程又被 Tk 转成事件，
若两处都处理，画面会跳两格。因此判定逻辑必须严格：

| 输入方式 | 进窗口过程 | 产生 Tk 事件 | 应兜接 |
|---|---|---|---|
| `PostMessage` 投递 | 是 | 否 | 是 |
| 真实输入 | 是 | 是 | 否 |

区分依据只能是「Tk 有没有处理它」——`GetMessageExtraInfo` 对两者都是 0
（本机同进程实测），不能用作判据。
"""

import time
import unittest

from extensions_testbed import click_claim

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202


def _point(x: int, y: int) -> int:
    """把客户区坐标打包成 lParam。

    Args:
        x: 客户区 x。
        y: 客户区 y。

    Returns:
        打包后的 lParam。
    """
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


class TestNoteMessage(unittest.TestCase):
    """消息记录。"""

    def test_records_down_message(self):
        """按下消息被记成待定。"""
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(100, 200))
        self.assertTrue(claimer.has_pending())

    def test_ignores_non_click_messages(self):
        """非点击消息一律忽略（鼠标移动每秒数百次，记下来会撑爆列表）。"""
        claimer = click_claim.ClickClaimer()
        for msg in (0x0200, 0x0100, 0x0006):
            claimer.note_message(msg, _point(1, 1))
        self.assertFalse(claimer.has_pending())

    def test_decodes_negative_coordinates(self):
        """负坐标按补码解释，不读成 6 万。

        多显示器下客户区坐标可能为负；不扩展会得到 65535 量级的错误值，
        兜接点击就会飞到屏幕另一端。
        """
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(-10, -20))
        pending = claimer.take_unclaimed()
        self.assertEqual(pending, [(-10, -20)])

    def test_duplicate_messages_across_windows_merge(self):
        """同一坐标同一类型的重复消息只记一条。

        顶层与画布两个窗口都会被挂钩，一次点击会到达两次；不去重会让
        一次点击兜接出两次动作。
        """
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(50, 60))
        claimer.note_message(WM_LBUTTONDOWN, _point(50, 60))
        self.assertEqual(len(claimer.take_unclaimed()), 1)


class TestClaim(unittest.TestCase):
    """Tk 认领：真实输入不该被兜接。"""

    def test_claim_marks_message_handled(self):
        """认领后不再进入兜接列表。"""
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(300, 400))
        self.assertTrue(claimer.claim(300, 400))
        self.assertEqual(claimer.take_unclaimed(), [])
        self.assertEqual(claimer.claimed, 1)

    def test_claim_without_pending_returns_false(self):
        """没有待定记录时认领失败（不凭空造记录）。"""
        claimer = click_claim.ClickClaimer()
        self.assertFalse(claimer.claim(10, 10))

    def test_claim_tolerates_small_offset(self):
        """认领允许小幅坐标偏差。

        Tk 报告的是控件相对坐标，`lParam` 是窗口客户区坐标，多级子窗口下
        可能差几个像素；要求精确相等会让真实输入被误判成投递点击。
        """
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(300, 400))
        self.assertTrue(claimer.claim(305, 396))

    def test_claim_rejects_far_apart_position(self):
        """坐标差太多不认领（那是另一次点击）。"""
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(300, 400))
        self.assertFalse(claimer.claim(500, 600))

    def test_claim_only_consumes_down(self):
        """认领只针对按下，抬起不会被当成一次新点击。"""
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONUP, _point(300, 400))
        self.assertFalse(claimer.claim(300, 400))

    def test_claim_is_not_reused_for_second_click(self):
        """一条待定记录只能被认领一次。

        否则连点两次时第二次真实点击会「借用」第一次的记录而不被兜接，
        表现为后台点击时灵时不灵。
        """
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(100, 100))
        self.assertTrue(claimer.claim(100, 100))
        self.assertFalse(claimer.claim(100, 100))


class TestTakeUnclaimed(unittest.TestCase):
    """兜接列表的产出。"""

    def test_returns_unclaimed_down(self):
        """未认领的按下被返回。"""
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(11, 22))
        self.assertEqual(claimer.take_unclaimed(), [(11, 22)])

    def test_excludes_up_events(self):
        """抬起不返回——一次点击只兜接一次，否则动作执行两遍。"""
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(11, 22))
        claimer.note_message(WM_LBUTTONUP, _point(11, 22))
        self.assertEqual(claimer.take_unclaimed(), [(11, 22)])

    def test_clears_after_take(self):
        """取出后清空，避免同一记录被重复兜接。"""
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(11, 22))
        claimer.take_unclaimed()
        self.assertEqual(claimer.take_unclaimed(), [])

    def test_expired_records_are_dropped(self):
        """超期记录被丢弃。

        界面忙了很久时，旧的待定记录不该在之后某帧突然变成一次点击——
        那会在用户没有点击的时刻凭空触发动作。
        """
        claimer = click_claim.ClickClaimer(ttl=0.01)
        claimer.note_message(WM_LBUTTONDOWN, _point(11, 22))
        time.sleep(0.05)
        self.assertEqual(claimer.take_unclaimed(), [])

    def test_counts_synthesized(self):
        """每次兜接都计入统计（供日志与自检）。"""
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(1, 2))
        claimer.note_message(WM_LBUTTONDOWN, _point(3, 4))
        claimer.take_unclaimed()
        self.assertEqual(claimer.synthesized, 2)


class TestSnapshot(unittest.TestCase):
    """状态快照。"""

    def test_snapshot_shape(self):
        """快照含三个计数字段。"""
        snap = click_claim.ClickClaimer().snapshot()
        self.assertEqual(set(snap), {"pending", "claimed", "synthesized"})

    def test_snapshot_reflects_counts(self):
        """快照反映实际计数。"""
        claimer = click_claim.ClickClaimer()
        claimer.note_message(WM_LBUTTONDOWN, _point(1, 1))
        self.assertEqual(claimer.snapshot()["pending"], 1)


class TestHookWindows(unittest.TestCase):
    """挂钩窗口必须包含顶层窗口。

    **这是后台点击失效的直接原因**（用户实测「PostMessage 无法触发后台点击」）：
    自动化工具的 `PostMessage` 后端默认把点击投给**顶层窗口**——它先在
    `hwnd_window.hwnds` 里找坐标命中的子窗口，为空时退回 `top_hwnd`，
    而 `top_hwnd` 就是 `TkTopLevel`。若只挂钩两个 `TkChild`，投到顶层的
    点击谁也看不见：靶场的原始消息计数完全不动，表现为「发出去了但没反应」，
    极难归因到「挂钩窗口选错」。
    """

    def _hook_hwnds_of(self, top: int, container: int, canvas: int):
        """在不建真窗口的前提下取 `_hook_hwnds` 的返回值。

        Args:
            top: 顶层窗口句柄。
            container: 客户区容器句柄。
            canvas: 画布句柄。

        Returns:
            挂钩列表。
        """
        from extensions_testbed import app as app_module

        instance = object.__new__(app_module.TestbedApp)
        instance._top_hwnd = lambda: top
        instance.root = type("Root", (object,), {"winfo_id": lambda self: container})()
        instance.canvas = type("Canvas", (object,), {"winfo_id": lambda self: canvas})()
        return instance._hook_hwnds()

    def test_includes_top_level_window(self):
        """顶层窗口必须在挂钩列表里。

        缺了它，`PostMessage` 投给顶层的点击会全部丢失。
        """
        hwnds = self._hook_hwnds_of(10, 20, 30)
        self.assertIn(10, hwnds, "顶层窗口必须挂钩，否则投给顶层的后台点击收不到")

    def test_includes_both_child_windows(self):
        """两个子窗口仍要挂钩（容器收键盘、画布收鼠标）。"""
        hwnds = self._hook_hwnds_of(10, 20, 30)
        self.assertIn(20, hwnds)
        self.assertIn(30, hwnds)

    def test_no_duplicate_handles(self):
        """句柄不重复。

        重复挂钩会让同一条消息被计数两次，干扰「注入点 vs 实收」的核对。
        """
        hwnds = self._hook_hwnds_of(10, 10, 30)
        self.assertEqual(len(hwnds), len(set(hwnds)), f"存在重复句柄：{hwnds}")


if __name__ == "__main__":
    unittest.main()
