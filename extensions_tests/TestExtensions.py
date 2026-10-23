# -*- coding: utf-8 -*-
"""扩展机制测试：验证 config 覆写已生效且未破坏上游结构。

被测对象是 extensions/ 下的运行时代码；上游基线测试在 tests/，两者互不混放。

环境要求：导入 src.config 需要 ok / PySide6 等运行依赖。缺依赖时整类跳过并说明原因，
而不是让测试报错——报错会掩盖真正的断言失败，也让无 GUI 的环境无法区分
「环境没装」与「代码坏了」。
"""

import unittest

try:
    from src.config import config
    from extensions import overrides

    IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 ok / PySide6 等运行依赖
    config = None
    overrides = None
    IMPORT_ERROR = str(exc)


@unittest.skipIf(overrides is None, f"缺少运行依赖，跳过扩展测试：{IMPORT_ERROR}")
class TestExtensionOverrides(unittest.TestCase):
    """验证 overrides 的覆写结果与缺键语义。"""

    def test_override_applied_to_links(self):
        """项目链接覆写已生效，且未被上游默认值覆盖。"""
        links = config["links"]["default"]
        self.assertEqual(
            "https://github.com/zengliugen/ok-script-app-pro", links["github"]
        )
        self.assertIn("ok-script-app-pro", links["share"])
        self.assertEqual(
            "https://github.com/zengliugen/ok-script-app-pro", links["faq"]
        )

    def test_override_applied_to_gui_title(self):
        """窗口标题使用项目名。"""
        self.assertEqual("ok-script-app-pro", config["gui_title"])

    def test_upstream_links_keys_preserved(self):
        """覆写只改值不改结构：上游有的链接键必须仍然存在。"""
        links = config["links"]["default"]
        for key in ("discord", "qq_group", "qq_channel"):
            self.assertIn(key, links)

    def test_override_keys_all_exist_in_config(self):
        """覆写表中的每个键路径在 config 中都能解析。"""
        missing = [path for path in overrides.OVERRIDES if not overrides._has_path(config, path)]
        self.assertEqual([], missing)

    def test_apply_raises_on_missing_key(self):
        """缺键时抛错并指名缺失路径，不静默降级。"""
        broken = {"links": {"default": {}}, "gui_title": "x"}
        with self.assertRaises(RuntimeError) as ctx:
            overrides.apply(broken)
        self.assertIn("links.default.github", str(ctx.exception))

    def test_apply_reports_all_missing_keys_at_once(self):
        """多个键缺失时一次列全，不在第一个缺失处中断。"""
        with self.assertRaises(RuntimeError) as ctx:
            overrides.apply({})
        message = str(ctx.exception)
        for path in overrides.OVERRIDES:
            self.assertIn(path, message)

    def test_apply_is_idempotent(self):
        """重复应用覆写不产生副作用。"""
        overrides.apply(config)
        first = config["gui_title"]
        overrides.apply(config)
        self.assertEqual(first, config["gui_title"])


if __name__ == "__main__":
    unittest.main()
