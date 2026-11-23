# -*- coding: utf-8 -*-
"""扩展能力 catalog 的源串语言与覆盖率检查。

被测对象是所有 `extensions/capabilities/<能力>/i18n/*/LC_MESSAGES/ok_ext.po`。

**为什么单独一份**：`TestExtensionI18n` 测的是 `ok_ext` 域的挂链行为（用替身与临时
`.mo`），与仓库里实际 catalog 的内容无关；而这里要固定的是「仓库里的译文文件长什么样」。

两个必须固化的约束，违反时都不会报错、只在界面上表现为「某处显示不该显示的语言」：

1. **`msgid` 一律英文**。中文当源串会让缺失译文时回退成中文（gettext 回退到源语言），
   而 `en_US` 用户看到中文是无法自解释的。
2. **没有空 `msgstr`**。gettext 命中空串即返回空串，`translated or key` 会回落源串，
   并且在链上遮蔽上游同名条目的译文。
"""

import re
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
CAPABILITIES = BASE / "extensions" / "capabilities"

# `msgid "..."` 与 `msgstr "..."` 的单行形态。多行串以 `"` 开头且本行只有引号，
# 匹配不到这里——本项目的 catalog 都是单行条目，出现多行时下面会以「解析不了」报错。
MSGID = re.compile(r'^msgid "(.*)"$', re.M)
MSGSTR = re.compile(r'^msgstr "(.*)"$', re.M)
CJK = re.compile(r"[\u4e00-\u9fff]")


def catalog_files():
    """列出全部能力的 po 文件。

    Returns：
        `Path` 列表，按路径排序。
    """
    return sorted(CAPABILITIES.glob("*/i18n/*/LC_MESSAGES/ok_ext.po"))


def parse_pairs(path):
    """解析一个 po，返回 `[(msgid, msgstr)]`。

    Args：
        path：po 文件路径。

    Returns：
        条目列表；空 msgid（头部）被跳过。
    """
    text = path.read_text(encoding="utf-8")
    ids = MSGID.findall(text)
    strs = MSGSTR.findall(text)
    pairs = []
    for index, msgid in enumerate(ids):
        if not msgid:
            continue
        msgstr = strs[index] if index < len(strs) else ""
        pairs.append((msgid, msgstr))
    return pairs


class TestExtensionCatalogs(unittest.TestCase):
    """仓库内实际 catalog 的源串语言与完整性。"""

    def test_catalogs_exist(self):
        """至少应有两个能力包的 catalog。

        数量断言能拦住「glob 写错导致一条都没扫到、于是全部通过」这种假绿。
        """
        files = catalog_files()
        self.assertGreaterEqual(len(files), 2, f"只找到 {len(files)} 个 catalog：{files}")

    def test_msgid_is_english(self):
        """`msgid` 一律是英文源串，不含中日韩字符。

        这条是 i18n 技能 `references/catalog-rules.md` §五的固化。检测到中文 msgid 时
        报出的不只是「风格不符」——它意味着缺译时用户会看到中文而不是英文原文。
        """
        offenders = []
        for path in catalog_files():
            for msgid, _msgstr in parse_pairs(path):
                if CJK.search(msgid):
                    offenders.append(f"{path.relative_to(BASE)}: {msgid!r}")
        self.assertEqual(offenders, [], "以下 msgid 不是英文源串：\n" + "\n".join(offenders))

    def test_no_empty_msgstr(self):
        """不得留空 `msgstr`。

        空串在 gettext 里等于「命中、译文是空」，会盖掉上游同名条目，界面上什么都不显示。
        确实没有译文的条目应当**不写**，而不是写一条空的。
        """
        offenders = []
        for path in catalog_files():
            for msgid, msgstr in parse_pairs(path):
                if not msgstr.strip():
                    offenders.append(f"{path.relative_to(BASE)}: {msgid!r}")
        self.assertEqual(offenders, [], "以下条目 msgstr 为空：\n" + "\n".join(offenders))

    def test_english_catalog_is_identity(self):
        """`en_US` 的译文必须等于 msgid。

        英文是源语言，译文与源串不同只说明有人把英文当成了「待翻译语言」，
        改一处漏一处时最难发现。
        """
        offenders = []
        for path in catalog_files():
            if "en_US" not in path.parts:
                continue
            for msgid, msgstr in parse_pairs(path):
                if msgid != msgstr:
                    offenders.append(f"{path.relative_to(BASE)}: {msgid!r} -> {msgstr!r}")
        self.assertEqual(offenders, [], "en_US 的 msgstr 与 msgid 不一致：\n" + "\n".join(offenders))

    def test_msgid_unique_per_catalog(self):
        """同一 catalog 内不得出现重复 msgid。

        重复时 gettext 取哪一条取决于文件顺序，属不可预测行为。
        """
        offenders = []
        for path in catalog_files():
            seen = set()
            for msgid, _msgstr in parse_pairs(path):
                if msgid in seen:
                    offenders.append(f"{path.relative_to(BASE)}: {msgid!r}")
                seen.add(msgid)
        self.assertEqual(offenders, [], "以下 msgid 重复：\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
