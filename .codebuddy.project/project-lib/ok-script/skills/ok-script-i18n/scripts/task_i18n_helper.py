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


# 界面控件的构造：第一个字符串字面量默认是显示文案。
#
# 只收**明确的界面控件**，不做「所有字符串字面量都算文案」的粗暴匹配——那会把
# 日志、字典键、路径、正则全部算进来，报出的清单没人看，等于没有检查。
UI_WIDGET_CALLS = {
    "Button",
    "Label",
    "ComboBox",
    "CheckBox",
    "RadioButton",
    "LineEdit",
    "MessageBox",
    "InputDialog",
}

# 显示文案在第几个位置参数上。多数控件是第 0 个；`add_tab(key, text)` 是第 1 个
# （第 0 个是页签 key，不是文字）。
UI_WIDGET_TEXT_INDEX = {
    "add_tab": 1,
    # 项目常把页签封装成 `_add_page(key, text, widget)` 这类私有方法。文案在第 2 个
    # 位置参数上，只认 `add_tab` 会把整批页签名漏掉——实测「Scene Config / Flow /
    # Drafts / Debug」四个页签全部显示英文，而检查报「全部通过」。
    "_add_page": 1,
    "add_page": 1,
    "_add_tab": 1,
}

# Qt 标准对话框按钮：文字由 Qt 内置翻译提供，但**只在 Qt 自己的翻译文件加载时才
# 是本地化文本**。本项目用的是 minimal 版 PySide6，Qt 的 zh_CN 翻译未随包分发，
# 实测 `QDialogButtonBox` 的 OK / Cancel 直接显示英文。
#
# 因此凡是构造标准按钮的地方，都要在收集结果里出现一条提示串，让开发者知道「这里
# 需要显式 setText」。同时 check-hardcoded 也要能识别出来。
QT_STANDARD_BUTTONS = {
    "QDialogButtonBox",
    "QMessageBox",
}

# 这些方法名本身就表明参数是给人看的文字。
TEXT_METHODS = {
    "setText",
    "setPlaceholderText",
    "setToolTip",
    "setWindowTitle",
    "setTitle",
}

# 这些控件/方法的**第 0 个**参数不是文案，分别是图标名、样式角色、键名等。
# 用白名单排除，避免把 `Button("trash")` 的图标名报成未翻译文案。
NON_TEXT_FIRST_ARG = {
    "addItem": 1,      # addItem(data, text)：第 1 个才是显示文本
}


def _is_tr_call(node):
    """判断调用是否为翻译函数。

    三种写法都要认：裸 `tr(...)`、`self.tr(...)`（CustomTab 提供的实例方法）、
    `app.tr(...)` / `og.app.tr(...)`（全局）。只认裸 `tr` 会把 `self.tr("Scene
    Manager")` 报成硬编码——那是误报，而误报多了整个检查就没人看。

    Args:
        node：ast 节点。

    Returns：
        布尔值。
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "tr"
    if isinstance(func, ast.Attribute):
        return func.attr == "tr"
    return False


class UiStringVisitor(ast.NodeVisitor):
    """收集界面文案：`tr(...)` 实参与界面控件构造里的字面量。

    与 `TaskStringVisitor` 的分工：那个只管任务类元数据（`self.name` /
    `config_description` 等），这个管页面代码。两者结果并集才是「界面会显示的
    全部文案」。

    只取字面量常量——f-string 与变量拼出来的文案无法静态确定，交由人工判断。
    """

    def __init__(self):
        """初始化结果容器。"""
        self.strings = []

    def visit_Call(self, node):
        """处理函数调用：`tr(...)` 与界面控件构造。

        Args:
            node：ast 调用节点。
        """
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr

        if _is_tr_call(node):
            for arg in node.args[:1]:
                self._add(arg)
        elif name in UI_WIDGET_TEXT_INDEX:
            index = UI_WIDGET_TEXT_INDEX[name]
            if len(node.args) > index:
                self._add(node.args[index])
        elif name in TEXT_METHODS:
            for arg in node.args[:1]:
                self._add(arg)
        elif name in NON_TEXT_FIRST_ARG:
            index = NON_TEXT_FIRST_ARG[name]
            if len(node.args) > index:
                self._add(node.args[index])
        elif name in UI_WIDGET_CALLS:
            for arg in node.args[:1]:
                self._add(arg)
            for keyword in node.keywords:
                if keyword.arg in {"text", "title", "placeholder", "label"}:
                    self._add(keyword.value)
        self.generic_visit(node)

    def _add(self, node):
        """把非空字符串常量收进结果。

        Args:
            node：表达式节点。
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if value:
                self.strings.append(value)


def _iter_python_files(targets):
    """展开目标为 .py 文件列表。

    Args:
        targets：文件或目录路径列表。

    Returns：
        路径字符串列表。

    Raises:
        SystemExit：路径不存在时退出码 3。
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
    return files


def _visit_file(path, visitor_class):
    """用指定访问器解析一个文件并返回收集到的字符串。

    Args:
        path：文件路径。
        visitor_class：`ast.NodeVisitor` 子类。

    Returns：
        字符串列表。

    Raises:
        SystemExit：解析失败时退出码 3。
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
    except (OSError, UnicodeDecodeError) as error:
        fail("解析失败：%s（%s）" % (path, error))
    visitor = visitor_class()
    visitor.visit(tree)
    return visitor.strings


def scan_ui_strings(targets, skip_status=False):
    """只扫描界面文案（tr 实参 + 控件字面量）。

    Args：
        targets：文件或目录路径列表。
        skip_status：为 True 时跳过 `setText` / `setToolTip` 里的状态提示。这些是
            f-string 拼出来的动态文案，没有固定 msgid，按项目规范不进 catalog。

    Returns：
        去重保序的字符串列表。
    """
    collected = []
    for path in _iter_python_files(targets):
        if skip_status:
            collected.extend(_ui_strings_excluding_status(path))
        else:
            collected.extend(_visit_file(path, UiStringVisitor))
    return list(dict.fromkeys(collected))


def _ui_strings_excluding_status(path):
    """收集一个文件里的界面文案，跳过状态提示。

    状态提示（`setText` / `setToolTip`）的文案是动态拼的，没有固定 msgid 与之一一
    对应，按项目规范不进 catalog。不跳过会让门禁报出一堆「缺译」而实际是设计如此。

    Args：
        path：文件路径。

    Returns：
        字符串列表。
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
    except (OSError, UnicodeDecodeError):
        fail("解析失败：%s" % path)
    collected = []
    for node in ast.walk(tree):
        if _is_status_call(node):
            continue
        for literal in _display_literals(node):
            text = literal.value.strip()
            if text:
                collected.append(text)
    return collected


# 判断「这个字符串是否是界面显示文案」的判定点。返回 True 表示该字面量会被
# 显示给用户，必须走 tr()。
def _ui_text_nodes(targets):
    """产出所有「界面显示文案」的字面量节点及其所属文件。

    与 `UiStringVisitor` 的区别：这里**不排除 tr() 包裹的**，而是连同父节点一起
    返回，由调用方判断是否已包 tr。

    Args:
        targets：文件或目录路径列表。

    Yields：
        `(path, node)` 二元组。
    """
    for path in _iter_python_files(targets):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            for candidate in _display_literals(node):
                yield path, candidate


def _display_literals(node):
    """从一个调用节点里挑出「会显示给用户」的字面量。

    Args:
        node：ast 节点。

    Returns：
        `ast.Constant` 列表。
    """
    if not isinstance(node, ast.Call):
        return []
    func = node.func
    name = None
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name is None:
        return []

    picked = []
    if _is_tr_call(node):
        picked.extend(node.args[:1])
    elif name in UI_WIDGET_TEXT_INDEX:
        index = UI_WIDGET_TEXT_INDEX[name]
        if len(node.args) > index:
            picked.append(node.args[index])
    elif name in TEXT_METHODS:
        picked.extend(node.args[:1])
    elif name in NON_TEXT_FIRST_ARG:
        index = NON_TEXT_FIRST_ARG[name]
        if len(node.args) > index:
            picked.append(node.args[index])
    elif name in UI_WIDGET_CALLS:
        picked.extend(node.args[:1])
        for keyword in node.keywords:
            if keyword.arg in {"text", "title", "placeholder", "label"}:
                picked.append(keyword.value)
    return [item for item in picked if isinstance(item, ast.Constant) and isinstance(item.value, str)]


# 状态提示类方法：文案会显示给用户，但每条都是动态拼出来的（「已创建「场景 3」」），
# 没有固定 msgid 与之对应。按项目规范这类不进 catalog。
# 这些方法的文案会在**方法内部**再包一层 tr，源码里看到的是未包装的字面量，但那
# 不是硬编码。不加豁免会误报——误报多了整个检查就没人看。
DELEGATED_TR_METHODS = {"_add_page", "add_page", "_add_tab"}

STATUS_TEXT_METHODS = {"setText", "setToolTip"} | DELEGATED_TR_METHODS


# Qt 标准按钮的中文文案。用显式 setText 之后这些串会出现在源码里，收集它们是为了
# 让「标准按钮被覆盖了」这件事可见；同时也作为 check-qt-buttons 的期望值。
_QT_BUTTON_TEXT = {
    "QDialogButtonBox.StandardButton.Ok": "OK",
    "QDialogButtonBox.StandardButton.Cancel": "Cancel",
    "QDialogButtonBox.StandardButton.Yes": "Yes",
    "QDialogButtonBox.StandardButton.No": "No",
    "QDialogButtonBox.StandardButton.Save": "Save",
    "QDialogButtonBox.StandardButton.Close": "Close",
}


# 可以「间接翻译」的方法：它们的参数在方法内部会再包一层 tr，属于合法写法，
# 不计入 check-dynamic-tr 的违规。
_TR_DELEGATING_METHODS = {"_add_page", "add_page", "_add_tab"}


def scan_dynamic_tr(targets):
    """找出 `tr(非字面量)` 的调用点。

    **为什么需要这条检查**：catalog 生成与门禁都靠**静态**抓 `tr("字面量")`。一旦
    写成 `tr(text)`（参数是变量），那一处文案就永远不会进 catalog，界面上一直显示
    源串，而门禁报「全部通过」。

    实测踩过一次：`_add_page(key, "Scene Config", ...)` 内部 `self.tr(text)` 翻译的是
    变量，四个页签名（场景配置 / 界面流转 / 草稿确认 / 调试）全部漏在 catalog 外，
    界面上一直是英文。

    判据：`tr(...)` 的实参不是字符串常量。两类已知的合法写法不报：

    - 「间接翻译」包装方法（`_add_page` 等）的变量入参——它们在方法内部翻译；
    - 循环变量（`for key, text in MODE_LABELS: ... tr(text)`）——文案来自本文件的
      常量表，生成脚本有专门规则收。

    Args：
        targets：文件或目录路径列表。

    Returns：
        `(path, lineno, 代码片段)` 列表，按文件与行号排序。
    """
    found = []
    for path in _iter_python_files(targets):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                source_text = handle.read()
                tree = ast.parse(source_text, filename=path)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        lines = source_text.splitlines()
        tr_nodes = [node for node in ast.walk(tree) if _is_tr_call(node) and node.args]
        # 出现在 `_add_page(KEY, text, ...)` 实参位置上的变量名是合法的：那些方法的
        # 参数本来就要在方法内部被翻译。把它们对应的 `tr(text)` 跳过。
        #
        # 注意判据必须是「这个 Name 出现在间接方法的实参里」，而不是「凡是 Name 都
        # 跳过」——后者会把真正的 `self.tr(text)` 漏掉，而那种写法正是本条检查要
        # 抓的目标。
        delegated_names = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in _TR_DELEGATING_METHODS:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    delegated_names.add(arg.id)
        # 循环变量名：`for key, text in MODE_LABELS: ... tr(text)`。这类写法里的文案
        # 来自本文件的常量表，生成脚本有专门规则收（如 MODE_LABEL_TABLE），不算漏译。
        loop_names = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            for target in ast.walk(node.target):
                if isinstance(target, ast.Name):
                    loop_names.add(target.id)
        for node in tr_nodes:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                continue
            if isinstance(first, ast.Name) and first.id in delegated_names | loop_names:
                continue
            snippet = lines[node.lineno - 1].strip() if node.lineno - 1 < len(lines) else ""
            found.append((path, node.lineno, snippet))
    found.sort(key=lambda item: (item[0], item[1]))
    return found

def scan_qt_standard_buttons(targets):
    """找出构造了 Qt 标准按钮但没有显式设置文字的对话框。

    **为什么需要这条检查**：`QDialogButtonBox` 的 OK / Cancel 文字由 Qt 内置翻译
    提供，而本项目用的是 minimal 版 PySide6，Qt 的 zh_CN 翻译文件不随包分发。
    实测 `QDialogButtonBox(QDialogButtonBox.Ok | Cancel)` 在中文界面下直接显示
    英文，而扫描脚本对这类「文字不在源码里」的情况完全看不见。

    Args:
        targets：文件或目录路径列表。

    Returns：
        `(path, lineno)` 列表，按文件与行号排序。
    """
    found = []
    for path in _iter_python_files(targets):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        # 记录该文件是否对按钮调过 button(...).setText(...)
        overridden = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "setText":
                source_text = ast.unparse(node.func)
                if ".button(" in source_text:
                    overridden = True
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in QT_STANDARD_BUTTONS and not overridden:
                found.append((path, node.lineno))
    found.sort()
    return found

def _is_status_call(node):
    """判断调用是否为状态提示（`setText` / `setToolTip`）。

    Args：
        node：ast 节点。

    Returns：
        布尔值。
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in STATUS_TEXT_METHODS


def scan_hardcoded_ui_text(targets, strict=False):
    """找出**没有包 `tr()` 的界面文案**（不限中英文）。

    这是审计而不是翻译收集。判定口径：该字面量会被控件显示给用户，但没有被
    `tr(...)` 包住。中英文一视同仁——`Button("Screenshot")` 与 `Button("截图")`
    同样不合规：

    - 中文：切到英文界面时露出中文，而中文环境下一路正常，问题只在交付后暴露；
    - 英文：看似「反正英文也是英文」，但它绕过了 catalog——以后要改文案、要加
      第三种语言、要做术语统一时，这一处永远是漏网的。

    Args：
        targets：文件或目录路径列表。
        strict：为 True 时连 `setText` / `setToolTip` 这类状态提示也报。默认不报——
            状态提示是 f-string 拼出来的动态文案，没有固定 msgid，按项目规范本来
            就不进 catalog；把它们算作违规会让清单里九成是误报。

    Returns：
        `(path, lineno, text)` 列表，按文件与行号排序。
    """
    tr_positions = set()
    all_calls = []
    for path in _iter_python_files(targets):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if _is_tr_call(node):
                for arg in node.args:
                    tr_positions.add((path, arg.lineno, arg.col_offset))
            all_calls.append((path, node))

    found = []
    for path, node in all_calls:
        if not strict and _is_status_call(node):
            continue
        for literal in _display_literals(node):
            if (path, literal.lineno, literal.col_offset) in tr_positions:
                continue
            text = literal.value.strip()
            if not text:
                continue
            found.append((path, literal.lineno, text))
    found.sort(key=lambda item: (item[0], item[1]))
    return found

def scan_paths(targets):
    """扫描文件或目录，返回界面可见字符串（去重保序）。

    同时跑两类访问器：`TaskStringVisitor`（任务元数据）与 `UiStringVisitor`
    （页面文案与控件字面量）。只跑前者时页面代码一条都扫不到——实测 103 条的
    catalog 只覆盖到 14 条，剩下 89 条永远不会被发现缺译。

    Args：
        targets：文件或目录路径列表；目录会递归收集 .py。

    Returns：
        字符串列表。

    Raises：
        SystemExit：目标不存在或解析失败时，退出码 3。
    """
    collected = []
    for path in _iter_python_files(targets):
        collected.extend(_visit_file(path, TaskStringVisitor))
        collected.extend(_ui_strings_excluding_status(path))
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

    scan_ui = subparsers.add_parser("scan-ui", help="只扫描界面文案（tr 实参与控件字面量）")
    scan_ui.add_argument("--path", nargs="+", required=True, help="文件或目录，可多个")

    hardcoded = subparsers.add_parser(
        "check-hardcoded",
        help="找出没包 tr() 的界面文案（中英文一律检查）",
    )
    hardcoded.add_argument("--path", nargs="+", required=True, help="文件或目录，可多个")
    hardcoded.add_argument(
        "--strict",
        action="store_true",
        help="连状态栏提示（setText/setToolTip）一起报；默认只查静态界面文案",
    )

    dynamic = subparsers.add_parser(
        "check-dynamic-tr",
        help="找出 tr(非字面量) 的调用点（其中的文案永远进不了 catalog）",
    )
    dynamic.add_argument("--path", nargs="+", required=True, help="文件或目录，可多个")

    qt_buttons = subparsers.add_parser(
        "check-qt-buttons",
        help="找出构造了 Qt 标准按钮但未显式设文字的对话框（minimal 版 PySide6 不带 Qt 翻译）",
    )
    qt_buttons.add_argument("--path", nargs="+", required=True, help="文件或目录，可多个")

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

    if args.command == "scan-ui":
        for value in scan_ui_strings(args.path):
            print(value)
        return

    if args.command == "check-dynamic-tr":
        entries = scan_dynamic_tr(args.path)
        if not entries:
            print("没有 tr(非字面量) 的调用")
            return
        print("以下 tr() 的实参不是字符串常量，其中的文案进不了 catalog：", file=sys.stderr)
        for path, lineno, snippet in entries:
            print("  %s:%d  %s" % (path, lineno, snippet), file=sys.stderr)
        raise SystemExit(1)

    if args.command == "check-qt-buttons":
        entries = scan_qt_standard_buttons(args.path)
        if not entries:
            print("没有未设文字的 Qt 标准按钮")
            return
        print("以下位置用了 Qt 标准按钮但未显式设文字，共 %d 处：" % len(entries), file=sys.stderr)
        print("（minimal 版 PySide6 不带 Qt 翻译，这些按钮会显示英文）", file=sys.stderr)
        for path, lineno in entries:
            print("  %s:%d" % (path, lineno), file=sys.stderr)
        raise SystemExit(1)

    if args.command == "check-hardcoded":
        entries = scan_hardcoded_ui_text(args.path, strict=args.strict)
        if not entries:
            print("界面文案全部经过 tr()，没有硬编码")
            return
        print("以下界面文案没有包 tr()，共 %d 处：" % len(entries), file=sys.stderr)
        current = None
        for path, lineno, text in entries:
            if path != current:
                current = path
                print("  %s" % path, file=sys.stderr)
            print("    第 %d 行：%s" % (lineno, text), file=sys.stderr)
        raise SystemExit(1)

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
