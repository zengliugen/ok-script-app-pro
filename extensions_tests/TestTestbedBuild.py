# -*- coding: utf-8 -*-
"""测试靶场 EXE 的「是否过期」校验。

`run.cmd` 的入口 4 每次启动前调用 `build_exe.py --check`：缺失或源码改动时先
重新打包。判据是**内容指纹**而不是修改时间——`git checkout` / `merge` 会把所有
文件 mtime 刷成当前时间，按时间比会把没改过的源码判成更新。

退出码语义（`run.cmd` 依赖其大小顺序）：

| 码 | 含义 | run.cmd 动作 |
|---|---|---|
| 0 | 一致 | 直接启动 |
| 1 | 过期 | 重新打包后启动 |
| 2 | 产物缺失 | 打包后启动 |
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from extensions_testbed import build_exe


class TestFingerprint(unittest.TestCase):
    """内容指纹：同一份源码稳定，内容变了才变。"""

    def setUp(self):
        """造一个只含两个源文件的临时包目录。"""
        self.temp_dir = tempfile.mkdtemp(prefix="testbed-build-")
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.package = Path(self.temp_dir) / "pkg"
        self.package.mkdir()
        (self.package / "a.py").write_text("A = 1\n", encoding="utf-8")
        (self.package / "b.py").write_text("B = 1\n", encoding="utf-8")

    def test_fingerprint_is_stable(self):
        """内容不变时两次计算得到同一指纹。"""
        first = build_exe.fingerprint(build_exe.source_hashes(self.package))
        second = build_exe.fingerprint(build_exe.source_hashes(self.package))
        self.assertEqual(first, second)

    def test_fingerprint_changes_with_content(self):
        """改一个文件的内容，总指纹变化。"""
        before = build_exe.fingerprint(build_exe.source_hashes(self.package))
        (self.package / "a.py").write_text("A = 2\n", encoding="utf-8")
        after = build_exe.fingerprint(build_exe.source_hashes(self.package))
        self.assertNotEqual(before, after)

    def test_changed_files_marks_modified_and_added(self):
        """变化清单标出修改与新增，供提示里点名。"""
        previous = build_exe.source_hashes(self.package)
        (self.package / "a.py").write_text("A = 2\n", encoding="utf-8")
        (self.package / "c.py").write_text("C = 1\n", encoding="utf-8")
        current = build_exe.source_hashes(self.package)
        self.assertEqual(build_exe.changed_files(previous, current), ["*a.py", "+c.py"])


class TestCheck(unittest.TestCase):
    """`check()` 的三种退出码与提示。"""

    def setUp(self):
        """造临时包目录、产物目录与路径参数。"""
        self.temp_dir = tempfile.mkdtemp(prefix="testbed-check-")
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.package = Path(self.temp_dir) / "pkg"
        self.package.mkdir()
        (self.package / "main.py").write_text("print('hi')\n", encoding="utf-8")
        self.dist = Path(self.temp_dir) / "dist"
        self.dist.mkdir()
        self.exe = self.dist / "testbed.exe"
        self.stamp = self.dist / "testbed.stamp.json"

    def _check(self):
        """用临时路径调用 `check()`。"""
        return build_exe.check(exe_path=self.exe, stamp_path=self.stamp, package_dir=self.package)

    def _write_exe(self):
        """造一个占位产物文件。"""
        self.exe.write_bytes(b"MZ")

    def test_missing_exe(self):
        """没有产物时返回 2（缺失）。"""
        code, message = self._check()
        self.assertEqual(code, build_exe.CHECK_MISSING)
        self.assertIn("不存在", message)

    def test_missing_stamp_is_stale(self):
        """有产物但没有指纹：按过期处理，不静默当成最新。"""
        self._write_exe()
        code, _message = self._check()
        self.assertEqual(code, build_exe.CHECK_STALE)

    def test_matching_stamp_is_ok(self):
        """指纹与源码一致时返回 0。"""
        self._write_exe()
        build_exe.write_stamp(package_dir=self.package, stamp_path=self.stamp, built_at="2026-11-23 10:00:00")
        code, message = self._check()
        self.assertEqual(code, build_exe.CHECK_OK)
        self.assertIn("一致", message)

    def test_changed_source_is_stale_and_names_file(self):
        """源码改动后返回 1，提示里点名改动文件。"""
        self._write_exe()
        build_exe.write_stamp(package_dir=self.package, stamp_path=self.stamp)
        (self.package / "main.py").write_text("print('changed')\n", encoding="utf-8")
        code, message = self._check()
        self.assertEqual(code, build_exe.CHECK_STALE)
        self.assertIn("*main.py", message)

    def test_corrupt_stamp_is_stale(self):
        """指纹文件损坏时按过期处理（不能读成「最新」）。"""
        self._write_exe()
        self.stamp.write_text("{ 坏掉", encoding="utf-8")
        code, _message = self._check()
        self.assertEqual(code, build_exe.CHECK_STALE)


if __name__ == "__main__":
    unittest.main()
