# -*- coding: utf-8 -*-
"""扩展机制测试：验证 config 覆写已生效且未破坏上游结构.

被测对象是 extensions/ 下的运行时代码；上游基线测试在 tests/，两者互不混放。

环境要求：导入 src.config 需要 ok / PySide6 等运行依赖。缺依赖时整类跳过并说明原因，
而不是让测试报错——报错会掩盖真正的断言失败，也让无 GUI 的环境无法区分
「环境没装」与「代码坏了」。
"""

import unittest

try:
    from extensions import overrides
    from src.config import config

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
        self.assertEqual("https://github.com/zengliugen/ok-script-app-pro", links["github"])
        self.assertIn("ok-script-app-pro", links["share"])
        self.assertEqual("https://github.com/zengliugen/ok-script-app-pro", links["faq"])

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


class TestCapabilityRegistry(unittest.TestCase):
    """验证能力清单与能力包契约一致。

    这一组用例只依赖标准库与项目自身代码，不需要 ok / PySide6——结构错误
    必须在无运行依赖时也能被发现，否则最该拦住的问题只在特定机器上暴露。
    """

    def setUp(self):
        """导入清单，缺运行依赖时跳过整类。"""
        try:
            from extensions.capabilities import registry
        except ModuleNotFoundError as exc:  # 缺 ok / PySide6 等运行依赖
            self.skipTest(f"缺少运行依赖，跳过能力清单用例：{exc}")
        self.registry = registry

    def test_every_capability_provides_register(self):
        """清单内每个能力包都提供 register(config)，缺契约会让能力静默不生效。"""
        from importlib import import_module

        for module_path in self.registry.CAPABILITIES:
            with self.subTest(capability=module_path):
                module = import_module(module_path)
                self.assertTrue(
                    callable(getattr(module, "register", None)),
                    f"{module_path} 未提供可调用的 register(config)",
                )

    def test_capabilities_list_has_no_duplicates(self):
        """清单不得重复登记同一能力，否则 register 会被调用两次。"""
        names = list(self.registry.CAPABILITIES)
        self.assertEqual(len(names), len(set(names)), "CAPABILITIES 存在重复条目")

    def test_capability_names_are_snake_case_module_paths(self):
        """清单条目是 `extensions.capabilities.<能力包>` 形式，与目录一一对应。"""
        for module_path in self.registry.CAPABILITIES:
            with self.subTest(capability=module_path):
                self.assertTrue(
                    module_path.startswith("extensions.capabilities."),
                    f"{module_path} 不在能力包根下",
                )
                leaf = module_path.rsplit(".", 1)[-1]
                self.assertRegex(leaf, r"^[a-z0-9]+(_[a-z0-9]+)*$")

    def test_register_all_raises_on_missing_contract(self):
        """缺 register 的能力包必须报错并指名，不得静默跳过。"""
        original = self.registry.CAPABILITIES
        self.registry.CAPABILITIES = ("extensions.capabilities.registry",)
        try:
            with self.assertRaises(AttributeError) as ctx:
                self.registry.register_all({})
        finally:
            self.registry.CAPABILITIES = original
        self.assertIn("extensions.capabilities.registry", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
