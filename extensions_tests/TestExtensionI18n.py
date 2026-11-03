# -*- coding: utf-8 -*-
"""扩展翻译域测试：验证 ok_ext 域的挂链、退化与断言。

被测对象是 extensions/capabilities/extension_i18n/translator.py。用例只用替身
（伪 App、临时目录、临时 .mo），不启动真实 GUI——挂链的正确性由链结构与命中行为
决定，与界面无关。

为什么必须测「空 msgstr 遮蔽上游」：gettext 的 gettext 是 _catalog.get(key) 命中即
返回，空串也算命中，因此扩展 catalog 里的空条目会静默吞掉上游译文。这条语义不写
测试固定下来，下次有人往 catalog 里塞空 msgstr 时不会有任何信号。
"""

import gettext
import struct
import tempfile
import unittest
from pathlib import Path

try:
    from extensions.capabilities.extension_i18n import translator

    IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # 缺 ok / PySide6 等运行依赖
    translator = None
    IMPORT_ERROR = str(exc)


class _FakeLocale:
    """只提供 name() 的语言替身。"""

    def __init__(self, name):
        """记录语言名。

        Args：
            name：语言名，如 zh_CN。
        """
        self._name = name

    def name(self):
        """返回语言名。

        Returns：
            语言名字符串。
        """
        return self._name


class _FakeApp:
    """只带 po_translation 与 locale 的 App 替身。"""

    def __init__(self, locale_name="zh_CN"):
        """初始化替身。

        Args：
            locale_name：语言名。
        """
        self.locale = _FakeLocale(locale_name)
        self.po_translation = None


def _fake_upstream(catalog):
    """用真实的 GNUTranslations 构造上游翻译对象。

    刻意不用 NullTranslations：它的 gettext 直接返回 message、不查 _catalog，
    拿它当上游替身会让「上游译文命中」这条断言永远失败，从而掩盖真正的问题。

    Args：
        catalog：msgid 到 msgstr 的字典。

    Returns：
        GNUTranslations 实例。
    """
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "ok.mo"
        _write_mo(path, catalog)
        with open(path, "rb") as handle:
            return gettext.GNUTranslations(handle)


def _write_mo(path, catalog):
    """写一个最小可用的 .mo，供测试构造 catalog。

    与产出侧实现刻意分开：这里直接按标准库的读取格式拼字节，若产出侧写错了格式，
    测试才能发现，而不是两边同错。

    Args：
        path：输出路径。
        catalog：msgid 到 msgstr 的字典。
    """
    keys = [""] + sorted(catalog)
    values = ["Content-Type: text/plain; charset=UTF-8\n"] + [catalog[k] for k in sorted(catalog)]
    key_offsets, value_offsets = [], []
    key_blob = b""
    value_blob = b""
    for key in keys:
        data = key.encode("utf-8")
        key_offsets.append((len(data), len(key_blob)))
        key_blob += data
    for value in values:
        data = value.encode("utf-8")
        value_offsets.append((len(data), len(value_blob)))
        value_blob += data
    count = len(keys)
    key_table = 28
    value_table = key_table + 8 * count
    key_data = value_table + 8 * count
    value_data = key_data + len(key_blob)
    body = struct.pack("<7I", 0x950412DE, 0, count, key_table, value_table, 0, 0)
    for length, offset in key_offsets:
        body += struct.pack("<2I", length, key_data + offset)
    for length, offset in value_offsets:
        body += struct.pack("<2I", length, value_data + offset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body + key_blob + value_blob + b"\x00")


@unittest.skipIf(translator is None, f"缺少运行依赖，跳过扩展测试：{IMPORT_ERROR}")
class TestExtensionTranslator(unittest.TestCase):
    """验证挂链、退化与断言。"""

    def setUp(self):
        """准备临时能力包根目录。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        """清理临时目录。"""
        self._tmp.cleanup()

    def _make_capability(self, name, locale_catalogs):
        """在临时根下建一个能力包及其 catalog。

        Args：
            name：能力包目录名。
            locale_catalogs：{locale: {msgid: msgstr}}。

        Returns：
            能力包目录。
        """
        package = self.root / name
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        for locale, catalog in locale_catalogs.items():
            _write_mo(package / "i18n" / locale / "LC_MESSAGES" / "ok_ext.mo", catalog)
        return package

    def test_no_catalog_keeps_upstream_state(self):
        """没有任何 catalog 时不接管，po_translation 保持 None。"""
        app = _FakeApp()
        self.assertFalse(translator.install(app, capabilities_root=self.root))
        self.assertIsNone(app.po_translation)

    def test_catalog_missing_for_locale_keeps_upstream_state(self):
        """Catalog 存在但不含当前语言时不接管。"""
        self._make_capability("demo", {"ja_JP": {"Hello": "こんにちは"}})
        app = _FakeApp("zh_CN")
        self.assertFalse(translator.install(app, capabilities_root=self.root))
        self.assertIsNone(app.po_translation)

    def test_extension_entry_is_visible(self):
        """扩展条目生效。"""
        self._make_capability("demo", {"zh_CN": {"Auto Battle": "自动战斗"}})
        app = _FakeApp("zh_CN")
        self.assertTrue(translator.install(app, capabilities_root=self.root))
        self.assertEqual("自动战斗", app.po_translation.gettext("Auto Battle"))

    def test_missing_key_falls_back_to_source(self):
        """扩展没覆盖的串回落成源串，而不是抛错。"""
        self._make_capability("demo", {"zh_CN": {"Auto Battle": "自动战斗"}})
        app = _FakeApp("zh_CN")
        translator.install(app, capabilities_root=self.root)
        self.assertEqual("Unknown Key", app.po_translation.gettext("Unknown Key"))

    def test_upstream_is_chained_as_fallback(self):
        """上游译文在链尾兜底：扩展不覆盖时仍能命中上游。"""
        self._make_capability("demo", {"zh_CN": {"Auto Battle": "自动战斗"}})
        upstream = _fake_upstream({"Upstream Only": "上游译文"})
        translation, took_over = translator.build_translation(
            "zh_CN", translator.find_catalog_dirs(self.root), upstream=upstream
        )
        self.assertTrue(took_over)
        self.assertEqual("自动战斗", translation.gettext("Auto Battle"))
        self.assertEqual("上游译文", translation.gettext("Upstream Only"))

    def test_extension_overrides_upstream(self):
        """扩展在链顶：同名条目覆盖上游译文。"""
        self._make_capability("demo", {"zh_CN": {"Shared": "扩展译文"}})
        upstream = _fake_upstream({"Shared": "上游译文"})
        translation, _took_over = translator.build_translation(
            "zh_CN", translator.find_catalog_dirs(self.root), upstream=upstream
        )
        self.assertEqual("扩展译文", translation.gettext("Shared"))

    def test_missing_po_translation_attribute_raises(self):
        """App 上缺 po_translation 时崩溃并指名文件，不静默降级。"""

        class _NoAttrApp:
            """没有 po_translation 的替身。"""

            locale = _FakeLocale("zh_CN")

        with self.assertRaises(RuntimeError) as ctx:
            translator.install(_NoAttrApp(), capabilities_root=self.root)
        self.assertIn("po_translation", str(ctx.exception))
        self.assertIn("translator.py", str(ctx.exception))

    def test_catalog_dirs_sorted_and_scoped(self):
        """只收集能力包内的 i18n，且按目录名排序。"""
        self._make_capability("beta", {"zh_CN": {"B": "乙"}})
        self._make_capability("alpha", {"zh_CN": {"A": "甲"}})
        (self.root / "i18n").mkdir(parents=True, exist_ok=True)
        names = [path.parent.name for path in translator.find_catalog_dirs(self.root)]
        self.assertEqual(["alpha", "beta"], names)


@unittest.skipIf(translator is None, f"缺少运行依赖，跳过扩展测试：{IMPORT_ERROR}")
class TestExtensionTranslatorChainOrder(unittest.TestCase):
    """多能力包时的链顺序行为。"""

    def setUp(self):
        """准备临时目录。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        """清理临时目录。"""
        self._tmp.cleanup()

    def test_later_package_wins_on_duplicate_msgid(self):
        """同一 msgid 出现在两个包时，字典序靠后的包在链顶。

        这是已知的静默行为：不报错、由目录名决定胜负。用测试固定它，
        避免日后有人以为「先声明的优先」。跨包重复由规范禁止。
        """
        for name, value in (("aaa", "甲"), ("zzz", "乙")):
            package = self.root / name / "i18n" / "zh_CN" / "LC_MESSAGES"
            package.mkdir(parents=True)
            _write_mo(package / "ok_ext.mo", {"Dup": value})
        translation, _took_over = translator.build_translation("zh_CN", translator.find_catalog_dirs(self.root))
        self.assertEqual("乙", translation.gettext("Dup"))


if __name__ == "__main__":
    unittest.main()
