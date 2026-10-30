# -*- coding: utf-8 -*-
"""ok-script 翻译目录辅助脚本：扫描任务文案、校验重复 msgid、编译 .mo。

三个子命令各管一件事，互不牵连：

- ``scan`` 用 AST 静态分析任务文件，列出界面可见字符串；
- ``check`` 逐语言检查 ``ok.po`` 是否含重复 ``msgid``；
- ``compile`` 把 ``.po`` 编译成运行时真正读取的 ``.mo``。

依赖 ``polib``（ok-script 自身也用它读写 ``.po``）。它不属于项目运行依赖，
缺失时统一以退出码 3 报错并给出安装命令，不静默跳过——翻译改了却没生效，
比直接报错更难发现。

退出码：
    0 成功 / 无问题
    1 检查发现问题（仅 ``check`` 会返回）
    2 参数错误
    3 环境不可用（缺 ``polib``、目录不存在、解析失败）
"""

import argparse
import ast
import os
import sys
from collections import Counter

# 任务类里承载界面可见文案的属性名。
TASK_DICT_ATTRS = {"default_config", "config_description", "config_type"}
TASK_STRING_ATTRS = {"name", "description"}

# config_type 中的元数据键，它们本身是控件配置而不是要翻译的文案。
CONFIG_TYPE_META = {
    "type",
    "options",
    "buttons",
    "drop_down",
    "multi_selection",
    "global",
    "text_edit",
    "button",
}


def init_stdout():
    """Windows 下统一以 UTF-8 输出，避免中文乱码。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def require_polib():
    """导入 polib；缺失时按环境不可用处理。

    Returns：
        polib 模块。

    Raises：
        SystemExit：退出码 3，并给出可操作的安装命令。
    """
    try:
        import polib  # noqa: PLC0415 —— 延迟导入，避免无 polib 时连 scan 都跑不了
    except ImportError:
        print(
            "环境不可用：未安装 polib，无法读写 .po 文件。\n"
            "请用项目解释器安装：.venv\\Scripts\\python.exe -m pip install polib",
            file=sys.stderr,
        )
        raise SystemExit(3)
    return polib


class TaskStringVisitor(ast.NodeVisitor):
    """收集任务类里界面可见的字符串字面量。

    覆盖 ``self.name`` / ``self.description`` 的赋值，以及
    ``self.default_config`` / ``self.config_description`` / ``self.config_type``
    的赋值与 ``update(...)`` 调用。只取字面量常量，常量拼接与 f-string 不算——
    那类值无法静态确定，交给人工补。
    """

    def __init__(self):
        """初始化空的结果列表。"""
        self.strings = []

    def visit_Assign(self, node):
        """处理普通赋值语句。"""
        for target in node.targets:
            self._collect_assignment(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        """处理带类型注解的赋值语句。"""
        self._collect_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node):
        """处理 ``self.<配置字典>.update(...)`` 形式的追加。"""
        attr = node.func
        if isinstance(attr, ast.Attribute) and attr.attr == "update":
            if self._is_self_attr_in(attr.value, {"default_config", "config_description"}):
                for arg in node.args:
                    self._collect_dict_strings(arg)
            elif self._is_self_attr_in(attr.value, {"config_type"}):
                for arg in node.args:
                    self._collect_config_type_strings(arg)
        self.generic_visit(node)

    def _collect_assignment(self, target, value):
        """按被赋值属性分派到对应的收集逻辑。"""
        if self._is_self_attr_in(target, TASK_STRING_ATTRS):
            self._add_string(value)
        elif self._is_self_attr_in(target, {"default_config", "config_description"}):
            self._collect_dict_strings(value)
        elif self._is_self_attr_in(target, {"config_type"}):
            self._collect_config_type_strings(value)

    def _is_self_attr_in(self, node, attrs):
        """判断节点是否为 ``self.<attrs 之一>``。"""
        return (
            isinstance(node, ast.Attribute)
            and node.attr in attrs
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )

    def _add_string(self, node):
        """把字符串常量去空白后收进结果；非字符串常量忽略。"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if value:
                self.strings.append(value)

    def _collect_dict_strings(self, node):
        """收集字典字面量的键与值中的字符串。"""
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values):
            self._add_string(key)
            self._collect_value_strings(value)

    def _collect_value_strings(self, node):
        """递归收集值里的字符串，列表与嵌套字典都展开。"""
        self._add_string(node)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self._collect_value_strings(item)
        elif isinstance(node, ast.Dict):
            self._collect_dict_strings(node)

    def _collect_config_type_strings(self, node):
        """收集 config_type 字典里的键与选项文案。"""
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values):
            self._add_string(key)
            self._collect_config_type_value(value)

    def _collect_config_type_value(self, node):
        """收集 config_type 的值：只翻译 options / buttons 里的选项串。"""
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in {"options", "buttons"}:
                    self._collect_value_strings(value)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self._collect_config_type_value(item)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in CONFIG_TYPE_META:
            self._add_string(node)


def scan_task(path):
    """扫描单个 Python 文件，按首次出现顺序去重打印界面可见字符串。

    参数：
        path：任务文件路径。
    """
    if not os.path.isfile(path):
        print("文件不存在：%s" % path, file=sys.stderr)
        raise SystemExit(3)
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError) as error:
        print("解析失败：%s（%s）" % (path, error), file=sys.stderr)
        raise SystemExit(3)
    visitor = TaskStringVisitor()
    visitor.visit(tree)
    for value in dict.fromkeys(visitor.strings):
        print(value)


def iter_po_paths(i18n_dir):
    """递归产出 ``ok.po`` 路径（只认这个固定文件名）。"""
    for root, _, files in os.walk(i18n_dir):
        if "ok.po" in files:
            yield os.path.join(root, "ok.po")


def compile_i18n(i18n_dir):
    """把目录下每个 ``ok.po`` 编译为同名 ``ok.mo``。

    参数：
        i18n_dir：翻译根目录，如 ``i18n``。
    """
    require_polib()
    count = 0
    for po_path in iter_po_paths(i18n_dir):
        mo_path = os.path.splitext(po_path)[0] + ".mo"
        try:
            import polib  # noqa: PLC0415 —— 与 require_polib 成对，保证此处必定可用

            po = polib.pofile(str(po_path))
            po.save_as_mofile(mo_path)
        except (OSError, ValueError) as error:
            print("编译失败：%s（%s）" % (po_path, error), file=sys.stderr)
            raise SystemExit(3)
        print("已编译 %s -> %s" % (po_path, mo_path))
        count += 1
    if count == 0:
        print("未找到任何 ok.po：%s" % i18n_dir, file=sys.stderr)
        raise SystemExit(3)


def check_i18n(i18n_dir):
    """逐语言检查重复 msgid；有重复时退出码 1。

    参数：
        i18n_dir：翻译根目录，如 ``i18n``。
    """
    require_polib()
    import polib  # noqa: PLC0415 —— 与 require_polib 成对，保证此处必定可用

    failed = False
    for po_path in iter_po_paths(i18n_dir):
        try:
            po = polib.pofile(str(po_path))
        except (OSError, ValueError) as error:
            print("读取失败：%s（%s）" % (po_path, error), file=sys.stderr)
            raise SystemExit(3)
        ids = [entry.msgid for entry in po if entry.msgid]
        duplicates = sorted(msgid for msgid, count in Counter(ids).items() if count > 1)
        if duplicates:
            failed = True
            print("重复 msgid：%s" % po_path)
            for msgid in duplicates:
                print("  %s" % msgid)
        else:
            print("正常 %s" % po_path)
    if failed:
        raise SystemExit(1)


def main():
    """解析参数并派发子命令。"""
    init_stdout()
    parser = argparse.ArgumentParser(description="ok-script 翻译目录辅助脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="扫描任务文件中的界面可见字符串")
    scan.add_argument("--task", required=True, help="任务文件路径")

    compile_cmd = subparsers.add_parser("compile", help="编译 .po 为 .mo")
    compile_cmd.add_argument("--i18n", default="i18n", help="翻译根目录，默认 i18n")

    check_cmd = subparsers.add_parser("check", help="检查重复 msgid")
    check_cmd.add_argument("--i18n", default="i18n", help="翻译根目录，默认 i18n")

    args = parser.parse_args()

    if args.command == "scan":
        scan_task(args.task)
    elif args.command == "compile":
        if not os.path.isdir(args.i18n):
            print("目录不存在：%s" % args.i18n, file=sys.stderr)
            raise SystemExit(3)
        compile_i18n(args.i18n)
    elif args.command == "check":
        if not os.path.isdir(args.i18n):
            print("目录不存在：%s" % args.i18n, file=sys.stderr)
            raise SystemExit(3)
        check_i18n(args.i18n)


if __name__ == "__main__":
    main()
