#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ok-script 翻译目录辅助脚本：扫描文案、校验目录、编译 .mo。

六个子命令：

- scan           用 AST 静态分析目标文件或目录，列出界面可见字符串；
- check          逐语言检查 catalog 是否含重复 msgid；
- compile        把 .po 编译成运行时真正读取的 .mo；
- check-mo       校验同一目录下 .po 与 .mo 的条目是否一致；
- check-missing  扫描代码文案，要求每个语言的 catalog 都已覆盖；
- gate           check-mo 与 check-missing 的合并入口，供门禁调用。

不依赖 polib：.po 解析与 .mo 写出都用标准库完成。原因有二——polib 不在项目
运行依赖里（pyproject.toml 无此项），实测本机 .venv 也未安装；而构建链同样不含
编译步骤（pyappify.yml 与 workflows 均无 msgfmt/polib）。校验要在任何机器与 CI 上
稳定跑，就不能引入可选依赖。

遇到不支持的 .po 构造（msgctxt、msgid_plural、fuzzy）直接以退出码 3 报错并指名
文件与行号，不做降级解析——降级会让「漏译」与「解析失败」混为一谈。

退出码：
    0 通过 / 无问题
    1 检查发现问题（check / check-mo / check-missing / gate）
    2 参数错误
    3 环境不可用（目录不存在、.po 解析失败、不支持的构造）
"""

import argparse
import ast
import os
import re
import struct
import sys
from collections import Counter

# 任务类里承载界面可见文案的属性名。
TASK_STRING_ATTRS = {"name", "description"}

# config_type 中的元数据键，它们本身是控件配置而不是要翻译的文案。
CONFIG_TYPE_META = {
    "type",
    "options",
    "options_available",
    "buttons",
    "callback",
    "sub_configs",
    "allow_duplication",
    "drop_down",
    "multi_selection",
    "global",
    "text_edit",
    "line_edit",
    "file_selector",
    "folder_selector",
    "button",
    "switch",
    "int",
    "float",
    "bool",
    "str",
}

# .po 里出现即表明本脚本无法正确解析的构造。宁可报错，不猜。
UNSUPPORTED_PO_CONSTRUCTS = ("msgctxt", "msgid_plural", "msgstr[")

# msgid 里出现即判为「源串不是英文」。范围取中日韩统一表意文字（含扩展 A）与
# 兼容表意文字——界面文案可能遇到的都在这几段。
#
# 为什么这条要机械校验：中文当源串时，gettext 缺译会回退成中文（回退目标是源语言），
# 英文用户看到中文而没有任何报错；而源串语言一旦落地，纠正要连源码、所有译文与 .mo
# 一起改。检测点只有 `check` 与 `gate`，没有别的地方能发现它。
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# .mo 文件魔数（GNU gettext）。
MO_MAGIC = 0x950412DE


def init_stdout():
    """Windows 下统一以 UTF-8 输出，避免中文乱码。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def fail(message):
    """以退出码 3 结束，并打印可操作的原因。

    Args：
        message：面向人的说明。

    Raises：
        SystemExit：始终抛出，退出码 3。
    """
    print("环境不可用：%s" % message, file=sys.stderr)
    raise SystemExit(3)


def require_dir(path):
    """确认目录存在。

    Args：
        path：目录路径。

    Raises：
        SystemExit：目录不存在时退出码 3。
    """
    if not os.path.isdir(path):
        fail("目录不存在：%s" % path)


class PoEntry:
    """一条 .po 记录。"""

    def __init__(self, msgid, msgstr, lineno):
        """初始化条目。

        Args：
            msgid：源串，同时是查找键。
            msgstr：译文；空串表示无译文。
            lineno：该条目在文件中的起始行号，用于报错定位。
        """
        self.msgid = msgid
        self.msgstr = msgstr
        self.lineno = lineno


def _unescape(text):
    """还原 .po 字符串字面量里的转义序列。

    Args：
        text：引号内的原始文本。

    Returns：
        还原后的字符串。
    """
    return (
        text.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\r", "\r")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def parse_po(path):
    """解析 .po 文件，返回条目列表。

    Args：
        path：.po 文件路径。

    Returns：
        PoEntry 列表；文件头那条空 msgid 记录会被跳过。

    Raises：
        SystemExit：文件不可读、含不支持的构造、或语法非法时，退出码 3。
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError) as error:
        fail("读取失败：%s（%s）" % (path, error))

    entries = []
    state = {"msgid": None, "msgstr": None, "lineno": 0}

    def flush():
        """把已累积的条目收进结果列表。"""
        if state["msgid"]:
            entries.append(PoEntry(state["msgid"], state["msgstr"] or "", state["lineno"]))
        state["msgid"] = None
        state["msgstr"] = None

    mode = None
    for index, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            flush()
            mode = None
            continue
        if line.startswith("#"):
            # fuzzy 标记意味着该条目尚未定稿，编译后不该生效；本脚本不猜它的语义。
            if "fuzzy" in line:
                fail("不支持的 po 构造（fuzzy 标记）：%s:%d" % (path, index))
            continue
        for construct in UNSUPPORTED_PO_CONSTRUCTS:
            if line.startswith(construct):
                fail("不支持的 po 构造（%s）：%s:%d" % (construct, path, index))

        match = re.match(r'^(msgid|msgstr)\s+"(.*)"\s*$', line)
        if match:
            key, value = match.group(1), _unescape(match.group(2))
            if key == "msgid":
                if state["msgid"] is not None:
                    flush()
                state["msgid"] = value
                state["lineno"] = index
                mode = "id"
            else:
                state["msgstr"] = value
                mode = "str"
            continue

        continuation = re.match(r'^"(.*)"\s*$', line)
        if continuation:
            value = _unescape(continuation.group(1))
            if mode == "id":
                state["msgid"] = (state["msgid"] or "") + value
            elif mode == "str":
                state["msgstr"] = (state["msgstr"] or "") + value
            else:
                fail("无法解析的行（前无 msgid/msgstr）：%s:%d" % (path, index))
            continue

        fail("无法解析的行：%s:%d（%s）" % (path, index, line))

    flush()
    return entries


def collect_po_ids(entries):
    """把条目列表转成 msgid 到 msgstr 的字典。

    Args：
        entries：PoEntry 列表。

    Returns：
        字典；重复 msgid 取最后一次出现（重复本身由 check 单独报）。
    """
    return {entry.msgid: entry.msgstr for entry in entries}


def write_mo(path, catalog):
    """把 msgid 到 msgstr 的字典写成 .mo。

    文件布局按 GNU gettext 约定：头部 7 个 32 位字 + 原文偏移表 + 译文偏移表 +
    原文串 + 译文串。第 0 条固定为空 msgid 的元数据记录，内容带上 Content-Type，
    否则读取方会按 ascii 解码而报错。

    Args：
        path：输出路径。
        catalog：msgid 到 msgstr 的字典；空 msgstr 会被跳过（它等于无译文）。
    """
    effective = {key: value for key, value in catalog.items() if key and value}
    keys = [""] + sorted(effective)
    values = ["Content-Type: text/plain; charset=UTF-8\n"] + [
        effective[key] for key in sorted(effective)
    ]

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

    header = struct.pack("<7I", MO_MAGIC, 0, count, key_table, value_table, 0, 0)
    body = header
    for length, offset in key_offsets:
        body += struct.pack("<2I", length, key_data + offset)
    for length, offset in value_offsets:
        body += struct.pack("<2I", length, value_data + offset)
    # 末尾补一个 \x00：标准库 gettext 的解析用「偏移+长度 < 文件长度」判定边界，
    # 最后一条串正好顶到文件末尾会被判为 File is corrupt。polib 生成的 .mo 同样带这个尾字节。
    body += key_blob + value_blob + b"\x00"

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(body)


def read_mo(path):
    """读取 .mo，返回 msgid 到 msgstr 的字典。

    Args：
        path：.mo 文件路径。

    Returns：
        字典；文件头那条空 msgid 记录会被跳过。

    Raises：
        SystemExit：文件不可读或格式损坏时，退出码 3。
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        fail("读取失败：%s（%s）" % (path, error))

    if len(data) < 28:
        fail("文件过短，不是合法 .mo：%s" % path)
    magic = struct.unpack("<I", data[:4])[0]
    if magic == MO_MAGIC:
        endian = "<"
    elif struct.unpack(">I", data[:4])[0] == MO_MAGIC:
        endian = ">"
    else:
        fail("魔数不符，不是合法 .mo：%s" % path)

    unpacked = struct.unpack(endian + "6I", data[4:28])
    count, key_table, value_table = unpacked[1], unpacked[2], unpacked[3]
    catalog = {}
    for index in range(count):
        key_len, key_off = struct.unpack(
            endian + "2I", data[key_table + index * 8 : key_table + index * 8 + 8]
        )
        value_len, value_off = struct.unpack(
            endian + "2I", data[value_table + index * 8 : value_table + index * 8 + 8]
        )
        key = data[key_off : key_off + key_len].decode("utf-8")
        value = data[value_off : value_off + value_len].decode("utf-8")
        if key:
            catalog[key] = value
    return catalog

class TaskStringVisitor(ast.NodeVisitor):
    """收集任务类里界面可见的字符串字面量。

    覆盖 self.name / self.description 的赋值，以及 self.default_config、
    self.config_description、self.config_type 的赋值与 update(...) 调用。
    只取字面量常量——常量拼接、f-string、变量拼出来的文案无法静态确定，交给人工补。
    """

    def __init__(self):
        """初始化结果容器。"""
        self.strings = []

    def visit_Assign(self, node):
        """处理普通赋值语句。

        Args：
            node：ast 赋值节点。
        """
        for target in node.targets:
            self._collect_assignment(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        """处理带类型注解的赋值语句。

        Args：
            node：ast 注解赋值节点。
        """
        self._collect_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node):
        """处理 self.<配置字典>.update(...) 形式的追加。

        Args：
            node：ast 调用节点。
        """
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "update":
            if self._is_self_attr_in(func.value, {"default_config", "config_description"}):
                for arg in node.args:
                    self._collect_dict_strings(arg)
            elif self._is_self_attr_in(func.value, {"config_type"}):
                for arg in node.args:
                    self._collect_config_type_strings(arg)
        self.generic_visit(node)

    def _collect_assignment(self, target, value):
        """按被赋值属性分派到对应的收集逻辑。

        Args：
            target：赋值目标节点。
            value：赋值表达式节点。
        """
        if self._is_self_attr_in(target, TASK_STRING_ATTRS):
            self._add_string(value)
        elif self._is_self_attr_in(target, {"default_config", "config_description"}):
            self._collect_dict_strings(value)
        elif self._is_self_attr_in(target, {"config_type"}):
            self._collect_config_type_strings(value)

    def _is_self_attr_in(self, node, attrs):
        """判断节点是否为 self.<attrs 之一>。

        Args：
            node：待判定节点。
            attrs：属性名集合。

        Returns：
            布尔值。
        """
        return (
            isinstance(node, ast.Attribute)
            and node.attr in attrs
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )

    def _add_string(self, node):
        """把字符串常量收进结果；非字符串常量忽略。

        Args：
            node：表达式节点。
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if value:
                self.strings.append(value)

    def _collect_dict_strings(self, node):
        """收集字典字面量的键与值中的字符串。

        Args：
            node：表达式节点。
        """
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values):
            self._add_string(key)
            self._collect_value_strings(value)

    def _collect_value_strings(self, node):
        """递归收集值里的字符串，列表、元组与嵌套字典都展开。

        Args：
            node：表达式节点。
        """
        self._add_string(node)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self._collect_value_strings(item)
        elif isinstance(node, ast.Dict):
            self._collect_dict_strings(node)

    def _collect_config_type_strings(self, node):
        """收集 config_type 字典里的键与选项文案。

        Args：
            node：表达式节点。
        """
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values):
            self._add_string(key)
            self._collect_config_type_value(value)

    def _collect_config_type_value(self, node):
        """收集 config_type 的值：只取 options 与 buttons 里的选项串。

        Args：
            node：表达式节点。
        """
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if not isinstance(key, ast.Constant):
                    continue
                if key.value == "options" or key.value == "options_available":
                    self._collect_value_strings(value)
                elif key.value == "buttons":
                    self._collect_button_strings(value)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self._collect_config_type_value(item)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value not in CONFIG_TYPE_META
        ):
            self._add_string(node)

    def _collect_button_strings(self, node):
        """收集按钮列表里要显示的文字。

        按钮项形如 `{"text": "Show Config Values", "callback": ...}`：只有 `text`
        的值会显示在界面上，`callback` 是函数引用、键名 `text`/`callback` 是元数据。
        直接递归会把键名也当成待翻译串，因此在入口处就分派掉。

        Args：
            node：`buttons` 对应的表达式节点。
        """
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self._collect_button_strings(item)
            return
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "text":
                self._collect_value_strings(value)


def scan_paths(targets):
    """扫描文件或目录，返回界面可见字符串（去重保序）。

    Args：
        targets：文件或目录路径列表；目录会递归收集 .py。

    Returns：
        字符串列表。

    Raises：
        SystemExit：目标不存在或解析失败时，退出码 3。
    """
    files = []
    for target in targets:
        if os.path.isdir(target):
            for root, _dirs, names in os.walk(target):
                if "__pycache__" in root:
                    continue
                for name in sorted(names):
                    if name.endswith(".py"):
                        files.append(os.path.join(root, name))
        elif os.path.isfile(target):
            files.append(target)
        else:
            fail("路径不存在：%s" % target)

    collected = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
        except (OSError, UnicodeDecodeError, SyntaxError) as error:
            fail("解析失败：%s（%s）" % (path, error))
        visitor = TaskStringVisitor()
        visitor.visit(tree)
        collected.extend(visitor.strings)
    return list(dict.fromkeys(collected))

def iter_po_paths(i18n_dir):
    """递归产出 .po 路径。

    Args：
        i18n_dir：翻译根目录。

    Yields：
        每个 .po 文件的路径。
    """
    for root, _dirs, files in os.walk(i18n_dir):
        for name in sorted(files):
            if name.endswith(".po"):
                yield os.path.join(root, name)


def iter_catalog_dirs(i18n_dir):
    """产出 catalog 三元组：目录、.po 路径、同名 .mo 路径（不存在时为 None）。

    Args：
        i18n_dir：翻译根目录。

    Returns：
        三元组列表。
    """
    found = []
    for po_path in iter_po_paths(i18n_dir):
        mo_path = os.path.splitext(po_path)[0] + ".mo"
        found.append((os.path.dirname(po_path), po_path, mo_path if os.path.isfile(mo_path) else None))
    return found


def locale_of(po_path, i18n_dir):
    """从 .po 路径推语言名：取相对根目录的第一段。

    Args：
        po_path：.po 路径。
        i18n_dir：翻译根目录。

    Returns：
        语言名；路径不在根目录下时返回完整相对路径。
    """
    relative = os.path.relpath(po_path, i18n_dir)
    parts = relative.replace("\\", "/").split("/")
    return parts[0] if parts else relative


def cmd_compile(i18n_dir):
    """编译全部 .po 为 .mo。

    Args：
        i18n_dir：翻译根目录。
    """
    count = 0
    for po_path in iter_po_paths(i18n_dir):
        catalog = collect_po_ids(parse_po(po_path))
        for key in sorted(key for key, value in catalog.items() if not value):
            print(
                "警告：%s 的条目「%s」译文为空，编译后不会生效" % (po_path, key),
                file=sys.stderr,
            )
        mo_path = os.path.splitext(po_path)[0] + ".mo"
        write_mo(mo_path, catalog)
        print("已编译 %s -> %s（%d 条）" % (po_path, mo_path, len(catalog)))
        count += 1
    if count == 0:
        fail("未找到任何 .po：%s" % i18n_dir)


def cmd_check(i18n_dir):
    """逐语言检查重复 msgid 与非英文源串。

    Args：
        i18n_dir：翻译根目录。
    """
    failed = False
    for po_path in iter_po_paths(i18n_dir):
        entries = parse_po(po_path)
        ids = [entry.msgid for entry in entries]
        duplicates = sorted(msgid for msgid, count in Counter(ids).items() if count > 1)
        non_english = [msgid for msgid in dict.fromkeys(ids) if msgid and CJK_PATTERN.search(msgid)]
        if duplicates:
            failed = True
            print("重复 msgid：%s" % po_path)
            for msgid in duplicates:
                print("  %s" % msgid)
        if non_english:
            failed = True
            print("msgid 不是英文源串：%s" % po_path)
            for msgid in non_english:
                print("  %s" % msgid)
            print(
                "  修正方式：把源码里 tr() 的实参改成英文，再同步该条目的各语言译文。"
                "只改 .po 不生效——msgid 必须与源码源串逐字符一致。"
            )
        if not duplicates and not non_english:
            print("正常 %s" % po_path)
    if failed:
        raise SystemExit(1)


def cmd_check_mo(i18n_dir):
    """校验 .po 与 .mo 的条目一致。

    比对三件事：只出现在 .po 的条目、只出现在 .mo 的条目、同一条目的译文不同。
    三者任一非空即失败——这是「改了 .po 忘了编译」的唯一机械检测点。

    Args：
        i18n_dir：翻译根目录。
    """
    failed = False
    checked = 0
    for _directory, po_path, mo_path in iter_catalog_dirs(i18n_dir):
        if mo_path is None:
            failed = True
            missing_mo = os.path.splitext(po_path)[0] + ".mo"
            print("缺少编译产物：%s（应有 %s）" % (po_path, missing_mo), file=sys.stderr)
            continue
        po_catalog = {
            key: value for key, value in collect_po_ids(parse_po(po_path)).items() if key and value
        }
        mo_catalog = read_mo(mo_path)
        only_po = sorted(set(po_catalog) - set(mo_catalog))
        only_mo = sorted(set(mo_catalog) - set(po_catalog))
        differ = sorted(
            key for key in set(po_catalog) & set(mo_catalog) if po_catalog[key] != mo_catalog[key]
        )
        checked += 1
        if not (only_po or only_mo or differ):
            print("一致 %s" % mo_path)
            continue
        failed = True
        print("不同步：%s" % mo_path, file=sys.stderr)
        for key in only_po:
            print("  仅 .po 有：「%s」——改了 .po 未编译" % key, file=sys.stderr)
        for key in only_mo:
            print("  仅 .mo 有：「%s」——.po 已删除但 .mo 未重编" % key, file=sys.stderr)
        for key in differ:
            print("  译文不同：「%s」" % key, file=sys.stderr)
    # 没有任何 catalog 不算失败：扩展尚未需要本地化时这是合法状态，
    # 报错会让提交门禁误伤「这个能力本来就没有界面文案」的场景。
    if checked == 0 and not failed:
        print("未找到任何 .po：%s（无 catalog 属正常，跳过）" % i18n_dir)
    if failed:
        raise SystemExit(1)


def cmd_check_missing(i18n_dir, code_paths):
    """扫描代码文案，要求每个语言的 catalog 都已覆盖。

    Args：
        i18n_dir：翻译根目录。
        code_paths：待扫描的代码路径（文件或目录）。
    """
    strings = scan_paths(code_paths)
    if not strings:
        print("代码中没有界面可见文案，无需翻译")
        return

    catalogs = {}
    for po_path in iter_po_paths(i18n_dir):
        locale = locale_of(po_path, i18n_dir)
        catalogs.setdefault(locale, set()).update(
            key for key in collect_po_ids(parse_po(po_path)) if key
        )
    if not catalogs:
        fail(
            "代码里有界面文案，但 %s 下没有任何 .po——"
            "请在能力包内建 i18n/<locale>/LC_MESSAGES/ok_ext.po" % i18n_dir
        )

    failed = False
    for locale in sorted(catalogs):
        missing = [text for text in strings if text not in catalogs[locale]]
        if missing:
            failed = True
            print("语言 %s 缺 %d 条：" % (locale, len(missing)), file=sys.stderr)
            for text in missing:
                print("  「%s」" % text, file=sys.stderr)
        else:
            print("语言 %s 已覆盖 %d 条" % (locale, len(strings)))
    if failed:
        raise SystemExit(1)


def cmd_gate(i18n_dir, code_paths):
    """门禁入口：先校验 po 与 mo 一致，再校验 msgid 为英文源串，最后校验文案覆盖。

    Args：
        i18n_dir：翻译根目录。
        code_paths：待扫描的代码路径。
    """
    print("== 校验 .po 与 .mo 是否同步 ==")
    cmd_check_mo(i18n_dir)
    print("")
    print("== 校验 msgid 是否为英文源串 ==")
    cmd_check(i18n_dir)
    print("")
    print("== 校验界面文案是否都有译文 ==")
    cmd_check_missing(i18n_dir, code_paths)

def build_parser():
    """构造命令行解析器。

    Returns：
        argparse 解析器。
    """
    parser = argparse.ArgumentParser(description="ok-script 翻译目录辅助脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="扫描文件或目录中的界面可见字符串")
    scan.add_argument("--path", nargs="+", default=None, help="文件或目录，可多个")
    scan.add_argument("--task", default=None, help="单个任务文件（兼容旧用法）")

    for name, help_text in (
        ("compile", "编译 .po 为 .mo"),
        ("check", "检查重复 msgid"),
        ("check-mo", "校验 .po 与 .mo 是否一致"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--i18n", default="i18n", help="翻译根目录，默认 i18n")

    for name, help_text in (
        ("check-missing", "校验界面文案是否都有译文"),
        ("gate", "门禁：check-mo 与 check-missing"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--i18n", default="i18n", help="翻译根目录，默认 i18n")
        sub.add_argument("--path", nargs="+", required=True, help="待扫描的代码路径")

    return parser


def main(argv=None):
    """解析参数并派发子命令。

    Args：
        argv：不含程序名的参数列表；默认取 sys.argv[1:]。

    Raises：
        SystemExit：各子命令的退出码。
    """
    init_stdout()
    args = build_parser().parse_args(argv)

    if args.command == "scan":
        targets = args.path or ([args.task] if args.task else None)
        if not targets:
            fail("scan 需要 --path 或 --task")
        for value in scan_paths(targets):
            print(value)
        return

    require_dir(args.i18n)
    if args.command == "compile":
        cmd_compile(args.i18n)
    elif args.command == "check":
        cmd_check(args.i18n)
    elif args.command == "check-mo":
        cmd_check_mo(args.i18n)
    elif args.command == "check-missing":
        cmd_check_missing(args.i18n, args.path)
    elif args.command == "gate":
        cmd_gate(args.i18n, args.path)


if __name__ == "__main__":
    main()
