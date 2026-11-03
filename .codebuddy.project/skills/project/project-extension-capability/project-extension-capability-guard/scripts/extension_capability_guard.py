# -*- coding: utf-8 -*-
"""扩展能力守卫：体检扩展代码落位，并把文档契约体检委托给 devflow-guard。

职责边界（决定了本文件为什么这么短）：
    文档契约（文件头字段、生成器标记、总览表、ADR 编号、计划与偏差配对、归档一致性、
    需求探索记录）一律由引擎侧 devflow-guard 体检，本脚本不重复实现——两处各写一份
    判定逻辑，改一处漏一处就会出现「一边报错一边放行」。

    本脚本只管 devflow-guard 查不了的那一类：需要读懂扩展代码结构才能判断的落位约束。
    它读的是 extensions/ 下的 Python 源码，与文档体系无关。

用法：
    python .codebuddy.project/skills/project/project-extension-capability/project-extension-capability-guard/scripts/extension_capability_guard.py audit

退出码：
    0 通过 / 1 警告 / 2 错误 / 3 配置或环境不可用
"""

import subprocess
import sys
from pathlib import Path

def resolve_project_root(start):
    """自 start 向上查找含 `.codebuddy` 的目录，返回项目根。

    需求探索：技能所在的族目录层级会随项目侧收纳结构调整（把技能收进族目录后深度增加一层），
    固定用 `parents[N]` 定位会在每次收纳调整后静默指错，故改为按标记目录上溯。

    参数：
        start：定位起点（通常为脚本自身路径）。

    返回：
        项目根的 Path。

    异常：
        RuntimeError：上溯到盘根仍未找到 `.codebuddy`，说明脚本被移出项目结构。
    """
    current = Path(start).resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / ".codebuddy").is_dir():
            return candidate
    raise RuntimeError("未能定位项目根：脚本不在含 .codebuddy 的目录树下（%s）" % start)

# 项目根经标记目录上溯定位，不依赖脚本所在目录的层数（族目录收纳后层数会变）
PROJECT_ROOT = resolve_project_root(__file__)

EXTENSIONS_DIR = PROJECT_ROOT / "extensions"

# 文档契约体检的委托目标（引擎侧，随 devflow 包安装）
DEVFLOW_SCRIPT = (
    PROJECT_ROOT
    / ".codebuddy"
    / "lib"
    / "devflow"
    / "skills"
    / "devflow-guard"
    / "scripts"
    / "devflow_guard.py"
)

# 分级
LEVEL_ERROR = "错误"
LEVEL_WARN = "警告"
LEVEL_INFO = "提示"


class Finding:
    """一条缺口记录。"""

    def __init__(self, level, check, message):
        """初始化缺口。

        参数：
            level: 分级（错误 / 警告 / 提示）
            check: 检查项名称
            message: 面向人的说明
        """
        self.level = level
        self.check = check
        self.message = message

    def __str__(self):
        """渲染为单行输出。"""
        return "[%s] %s：%s" % (self.level, self.check, self.message)


def read_text(path):
    """读 UTF-8 文本，失败时返回 None 而非抛错。

    参数：
        path: 文件路径

    返回：
        文本内容；文件不存在或解码失败时为 None
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# 委托：文档契约体检
# ---------------------------------------------------------------------------


def run_delegated_doc_audit():
    """调用引擎侧 devflow-guard 体检文档契约，并原样转发其输出。

    不吞输出、不改退出码语义：文档契约的判定只有一处事实来源，
    本脚本只负责把它串进同一次体检里，避免用户记两个命令。

    返回：
        退出码；委托目标不可用时返回 3
    """
    print("—— 文档契约（委托 devflow-guard）——")
    if not DEVFLOW_SCRIPT.is_file():
        print(
            "  [错误] 委托目标不存在："
            + DEVFLOW_SCRIPT.relative_to(PROJECT_ROOT).as_posix()
            + "\n         文档契约未体检。未安装 devflow 包时此项必然出现。"
        )
        print("")
        return 3

    try:
        result = subprocess.run(
            [sys.executable, str(DEVFLOW_SCRIPT), "audit"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print("  [错误] 委托执行失败：" + str(error))
        print("")
        return 3

    for line in (result.stdout or "").splitlines():
        print("  " + line)
    for line in (result.stderr or "").splitlines():
        print("  " + line)
    print("")
    return result.returncode


# ---------------------------------------------------------------------------
# 代码落位检查（本脚本的独有职责）
# ---------------------------------------------------------------------------


def check_patch_assertion():
    """检查 1：每个 patch 模块必须提供 apply()，且函数体内有反抛断言。

    用 ast 解析而非文本匹配——文本匹配会把注释里的 raise 也算进去。

    返回：
        缺口列表
    """
    import ast

    findings = []
    patches_dir = EXTENSIONS_DIR / "patches"
    if not patches_dir.is_dir():
        return findings

    for path in sorted(patches_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        text = read_text(path)
        if text is None:
            findings.append(Finding(LEVEL_ERROR, "patch 断言", rel + " 无法读取"))
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            findings.append(Finding(LEVEL_ERROR, "patch 断言", rel + " 语法错误：" + str(exc)))
            continue

        apply_fn = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "apply":
                apply_fn = node
                break
        if apply_fn is None:
            findings.append(Finding(LEVEL_ERROR, "patch 断言", rel + " 未定义 apply() 函数"))
            continue

        has_raise = any(isinstance(node, ast.Raise) for node in ast.walk(apply_fn))
        if not has_raise:
            findings.append(
                Finding(
                    LEVEL_ERROR,
                    "patch 断言",
                    rel + " 的 apply() 内无 raise 断言，目标消失时会静默失效",
                )
            )
    return findings


def check_sys_path_pollution():
    """检查 2：extensions/ 内不得把 extensions 加入 sys.path。

    返回：
        缺口列表
    """
    import re

    findings = []
    if not EXTENSIONS_DIR.is_dir():
        return findings
    pattern = re.compile(r"sys\.path\.(insert|append)\s*\(")
    for path in sorted(EXTENSIONS_DIR.rglob("*.py")):
        text = read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line) and "extensions" in line:
                rel = path.relative_to(PROJECT_ROOT).as_posix()
                findings.append(
                    Finding(
                        LEVEL_ERROR,
                        "sys.path 污染",
                        rel + ":" + str(lineno) + " 把 extensions 加入 sys.path，会与上游同名包合并",
                    )
                )
    return findings


def check_package_structure():
    """检查 3：extensions/ 下每层目录必须有 __init__.py。

    返回：
        缺口列表
    """
    findings = []
    if not EXTENSIONS_DIR.is_dir():
        return findings
    for path in sorted(EXTENSIONS_DIR.rglob("*")):
        if not path.is_dir():
            continue
        if "__pycache__" in path.parts:
            continue
        if _is_catalog_dir(path):
            continue
        if not (path / "__init__.py").exists():
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            findings.append(
                Finding(LEVEL_ERROR, "包结构", rel + " 缺少 __init__.py，会退化为命名空间包")
            )
    return findings


def _is_catalog_dir(path):
    """判断目录是否属于能力包内的翻译资源目录。

    命中条件：路径中同时含 `capabilities` 与 `i18n` 段，且 `i18n` 位于 `capabilities`
    之后（即能力包内部）。`extensions/i18n/` 这种顶层目录不豁免——顶层白名单会单独报错。

    参数：
        path：待判定目录

    返回：
        布尔值
    """
    parts = path.parts
    if "i18n" not in parts or "capabilities" not in parts:
        return False
    return parts.index("i18n") > parts.index("capabilities")


def check_code_changed_hint():
    """检查 4：extensions/ 有代码变更但总览表未变时给出提示（弱信号）。

    这是提示而非错误——能力登记表变化与代码变更不同步是常见且合理的。

    返回：
        缺口列表
    """
    findings = []
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return findings
    if result.returncode != 0:
        return findings

    lines = result.stdout.splitlines()
    code_changed = any("extensions/" in ln and "extensions_docs" not in ln for ln in lines)
    docs_changed = any("extensions_docs/README.md" in ln for ln in lines)
    if code_changed and not docs_changed:
        findings.append(
            Finding(
                LEVEL_INFO,
                "登记同步",
                "extensions/ 有未提交改动但能力总览表未变——若是新增能力，需登记",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 目录结构检查：一个能力一个包
#
# 这一组回答的是「这个文件属于哪个能力、这个能力还在不在」。按技术层次
# （core/ tasks/ ui/）拆目录时这些问题没有答案：改一个能力要翻三个目录，
# 删一个能力会漏掉散落在别处的文件。判据因此落在「能力包 ↔ 清单 ↔ 总览表」
# 这条链上，三处任一处对不上都报错。
# ---------------------------------------------------------------------------

# extensions/ 顶层允许出现的条目。新增条目必须在规则 layout.mdc 里同步说明，
# 否则「临时放一个文件在顶层」会逐次累积成第二个大杂烩目录。
ALLOWED_TOP_LEVEL = {
    "__init__.py",
    "bootstrap.py",
    "overrides.py",
    "app_globals.py",
    "capabilities",
    "patches",
}

PATCHES_DIR_NAME = "patches"
CAPABILITIES_DIR_NAME = "capabilities"
REGISTRY_MODULE_NAME = "registry.py"


def read_capability_list():
    """解析 CAPABILITIES 清单，返回 (完整模块路径列表, 错误说明列表)。

    用 ast 读字面量而不是 import：体检不能因为某个能力包 import 失败就整段
    不可用——那种情况下更需要看清楚清单本身写了什么。

    返回：
        (清单, 错误说明)。清单解析失败时返回空列表与若干说明。
    """
    import ast

    registry_path = EXTENSIONS_DIR / CAPABILITIES_DIR_NAME / REGISTRY_MODULE_NAME
    rel = registry_path.relative_to(PROJECT_ROOT).as_posix()
    if not registry_path.is_file():
        return [], [
            rel + " 不存在：能力清单是结构体检的入口，缺失时无法判断能力包归属"
        ]
    text = read_text(registry_path)
    if text is None:
        return [], [rel + " 无法读取"]

    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [], [rel + " 语法错误：" + str(exc)]

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "CAPABILITIES" not in targets:
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            return [], [
                rel + " 的 CAPABILITIES 不是字面量元组/列表，体检无法静态读取"
            ]
        if not isinstance(value, (tuple, list)):
            return [], [rel + " 的 CAPABILITIES 不是序列"]
        entries = [item for item in value if isinstance(item, str)]
        if len(entries) != len(value):
            return [], [rel + " 的 CAPABILITIES 含非字符串条目"]
        return entries, []

    return [], [rel + " 未定义 CAPABILITIES 清单"]


def _capability_package_dirs():
    """返回 extensions/capabilities 下的能力包目录名集合（不含 __pycache__）。"""
    root = EXTENSIONS_DIR / CAPABILITIES_DIR_NAME
    if not root.is_dir():
        return set()
    return {
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }


def check_capability_layout():
    """检查 5：extensions/ 按能力分包，清单与实际目录双向一致。

    四项判据：

    1. 顶层只允许装配基础设施与 capabilities/、patches/；
    2. 能力清单条目必须指向真实存在且提供 `register` 的包；
    3. capabilities/ 下的目录必须在清单里登记（否则永不生效）；
    4. 每个能力包必须提供 `register(config)`（缺契约会静默不生效）。

    返回：
        缺口列表
    """
    import ast

    findings = []
    if not EXTENSIONS_DIR.is_dir():
        return findings

    # 判据 1：顶层白名单。扩展代码一旦散落到顶层，就重新变成按层次堆放。
    for entry in sorted(EXTENSIONS_DIR.iterdir()):
        name = entry.name
        if name == "__pycache__":
            continue
        if name in ALLOWED_TOP_LEVEL:
            continue
        rel = entry.relative_to(PROJECT_ROOT).as_posix()
        findings.append(
            Finding(
                LEVEL_ERROR,
                "能力分包",
                rel + " 不在顶层白名单内：能力代码应放 extensions/capabilities/<能力名>/，"
                "装配基础设施只允许 bootstrap.py / overrides.py / app_globals.py",
            )
        )

    entries, errors = read_capability_list()
    for message in errors:
        findings.append(Finding(LEVEL_ERROR, "能力分包", message))
    if errors:
        return findings

    registry_rel = (
        EXTENSIONS_DIR / CAPABILITIES_DIR_NAME / REGISTRY_MODULE_NAME
    ).relative_to(PROJECT_ROOT).as_posix()
    registered_dirs = set()

    for module_path in entries:
        prefix = "extensions." + CAPABILITIES_DIR_NAME + "."
        if not module_path.startswith(prefix):
            findings.append(
                Finding(
                    LEVEL_ERROR,
                    "能力分包",
                    registry_rel + " 的条目「" + module_path + "」不在 extensions.capabilities 下",
                )
            )
            continue
        package_name = module_path[len(prefix) :]
        registered_dirs.add(package_name)
        package_dir = EXTENSIONS_DIR / CAPABILITIES_DIR_NAME / package_name
        package_rel = package_dir.relative_to(PROJECT_ROOT).as_posix()

        # 判据 2：清单条目指向真实存在的包。
        if not package_dir.is_dir():
            findings.append(
                Finding(
                    LEVEL_ERROR,
                    "能力分包",
                    registry_rel + " 登记的能力包不存在：" + package_rel,
                )
            )
            continue
        init_path = package_dir / "__init__.py"
        init_text = read_text(init_path) if init_path.is_file() else None
        if init_text is None:
            findings.append(
                Finding(
                    LEVEL_ERROR,
                    "能力分包",
                    package_rel + " 缺少可读的 __init__.py，能力契约无处声明",
                )
            )
            continue

        # 判据 4：能力包必须显式提供 register(config)。
        has_register = False
        try:
            tree = ast.parse(init_text)
        except SyntaxError as exc:
            findings.append(
                Finding(LEVEL_ERROR, "能力分包", package_rel + "/__init__.py 语法错误：" + str(exc))
            )
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "register":
                has_register = True
                break
        if not has_register:
            findings.append(
                Finding(
                    LEVEL_ERROR,
                    "能力分包",
                    package_rel + "/__init__.py 未定义 register(config)："
                    "缺此契约的能力不会被告装配，等于静默不生效",
                )
            )

    # 判据 3：目录必须登记，否则永不被装载。
    for package_name in sorted(_capability_package_dirs() - registered_dirs):
        findings.append(
            Finding(
                LEVEL_ERROR,
                "能力分包",
                "extensions/"
                + CAPABILITIES_DIR_NAME
                + "/"
                + package_name
                + " 未在 "
                + registry_rel
                + " 的 CAPABILITIES 中登记，永远不会被装载",
            )
        )
    return findings


def check_capability_naming():
    """检查 6：能力包名、清单条目与文档总览表的能力名三者一致。

    能力名是唯一的「能力标识」，它同时出现在包目录（snake_case）、清单条目
    （模块路径）与总览表（kebab-case）。三者对不上时，文档说的能力和代码里的
    包就是两回事，而这种不一致不会报错，只会让登记失去意义。

    文档根与总览表位置从 devflow 配置读，不内置路径——配置不可用时本项跳过，
    文档契约段已由委托体检覆盖。

    返回：
        缺口列表
    """
    findings = []
    doc_root, overview_file = _read_devflow_overview_location()
    if doc_root is None:
        return findings

    registered = set(_read_overview_capabilities(doc_root / overview_file))
    if not registered:
        return findings

    entries, errors = read_capability_list()
    if errors:
        return findings

    prefix = "extensions." + CAPABILITIES_DIR_NAME + "."
    for module_path in entries:
        if not module_path.startswith(prefix):
            continue
        package_name = module_path[len(prefix) :]
        capability_name = package_name.replace("_", "-")
        if capability_name in registered:
            continue
        # 尚未登记文档的能力：报提示而非错误。新建能力时先写代码再补文档是
        # 常见顺序，此时报错误会迫使人绕过体检。
        findings.append(
            Finding(
                LEVEL_INFO,
                "能力命名",
                "能力包 " + package_name + " 对应能力名「" + capability_name
                + "」未在文档总览表登记——新增能力需登记，纯内部重构可忽略",
            )
        )
    return findings


def _read_devflow_overview_location():
    """从项目索引读 devflow 的文档根与总览表文件名。

    索引缺失、条目缺失或文件不可读时返回 (None, None)——此时文档契约段
    已经报了环境问题，本项不重复报。

    返回：
        (文档根 Path, 总览表相对文件名)；不可用时为 (None, None)。
    """
    import json

    index_path = PROJECT_ROOT / ".codebuddy" / "project.index.json"
    text = read_text(index_path)
    if text is None:
        return None, None
    try:
        index = json.loads(text)
    except json.JSONDecodeError:
        return None, None

    entry = (index.get("files") or {}).get("devflow.docs.config")
    if not isinstance(entry, dict):
        return None, None
    config_rel = entry.get("path")
    if not isinstance(config_rel, str):
        return None, None
    config_text = read_text(PROJECT_ROOT / config_rel)
    if config_text is None:
        return None, None
    try:
        config = json.loads(config_text)
    except json.JSONDecodeError:
        return None, None

    docs_root = config.get("docs_root")
    overview_file = (config.get("overview") or {}).get("file")
    if not isinstance(docs_root, str) or not isinstance(overview_file, str):
        return None, None
    return PROJECT_ROOT / docs_root, overview_file


def _read_overview_capabilities(overview_path):
    """读取文档总览表第一列的能力名集合。

    只取第一列，不解析其余列——本项只关心能力名集合。表头与分隔行用
    「首格是能力名、次格是状态值」判不出来，因此跳过前两行。

    返回：
        能力名集合；文件不可读时返回空集合。
    """
    text = read_text(overview_path)
    if text is None:
        return set()

    names = set()
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        if cells[0] == "能力":
            in_table = True
            continue
        if not in_table:
            continue
        if set(cells[0]) <= {"-", ":", " "}:
            continue
        if cells[0]:
            names.add(cells[0])
    return names


def check_capability_isolation():
    """检查 7：能力之间不得互相 import。

    能力包自包含是「按能力分包」的前提。一旦 A 能力 import 了 B 能力的实现，
    B 就不再能被单独删除或迁移，而这条依赖在目录结构上完全看不见。

    只查顶层 `extensions.capabilities.<其他能力>` 的直接引用（含 from ... import
    与 import ...）；对模块内部同名函数的引用不在本项范围。

    返回：
        缺口列表
    """
    root = EXTENSIONS_DIR / CAPABILITIES_DIR_NAME
    if not root.is_dir():
        return []

    findings = []
    prefix = "extensions." + CAPABILITIES_DIR_NAME + "."
    for package_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if package_dir.name == "__pycache__":
            continue
        own_module = prefix + package_dir.name
        for path in sorted(package_dir.rglob("*.py")):
            text = read_text(path)
            if text is None:
                continue
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            for lineno, line in enumerate(text.splitlines(), start=1):
                target = _capability_import_target(line, prefix)
                if target is None or target == own_module:
                    continue
                findings.append(
                    Finding(
                        LEVEL_ERROR,
                        "能力隔离",
                        rel + ":" + str(lineno) + " 引用了其他能力 " + target
                        + "——能力之间不得互相 import，共享代码应上升为装配基础设施",
                    )
                )
    return findings


def _capability_import_target(line, prefix):
    """从一行源码里取出它引用的能力模块路径，非能力引用返回 None。

    参数：
        line: 源码行
        prefix: `extensions.capabilities.` 前缀

    返回：
        被引用的能力模块路径（截断到能力包一级）；非能力引用时为 None。
    """
    import re

    pattern = re.compile(
        r"^\s*(?:from|import)\s+(" + re.escape(prefix) + r"[a-z0-9_]+)"
    )
    match = pattern.match(line)
    if not match:
        return None
    return match.group(1)


# ---------------------------------------------------------------------------
# 配置键命名
# ---------------------------------------------------------------------------

I18N_DIR_NAME = "i18n"

# 界面可见文案的判定属性，与 i18n 技能的扫描口径保持一致。
UI_STRING_ATTRS = ("name", "description")
UI_DICT_ATTRS = ("default_config", "config_description", "config_type")


CONFIG_KEY_ATTRS = ("default_config", "config_description", "config_type")
def _parse_catalog(po_path):
    """解析 po 文件，返回 msgid 到 msgstr 的映射。

    遇到本检查不支持的构造（msgctxt / msgid_plural / fuzzy）时返回 None，
    交由调用方按「无法检查」处理，不猜。

    参数：
        po_path：po 路径

    返回：
        字典；解析失败或含不支持构造时为 None
    """
    import re

    text = read_text(po_path)
    if text is None:
        return None
    catalog = {}
    msgid = None
    msgstr = ""
    state = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if msgid:
                catalog[msgid] = msgstr
            msgid = None
            msgstr = ""
            state = None
            continue
        if stripped.startswith("#"):
            if "fuzzy" in stripped:
                return None
            continue
        if stripped.startswith(("msgctxt", "msgid_plural", "msgstr[")):
            return None
        match = re.match(r'^(msgid|msgstr)\s+"(.*)"\s*$', stripped)
        if match:
            value = match.group(2).replace("\\n", "\n").replace('\\"', '"')
            if match.group(1) == "msgid":
                if msgid:
                    catalog[msgid] = msgstr
                msgid = value
                msgstr = ""
                state = "id"
            else:
                msgstr = value
                state = "str"
            continue
        continuation = re.match(r'^"(.*)"\s*$', stripped)
        if continuation:
            value = continuation.group(1).replace("\\n", "\n").replace('\\"', '"')
            if state == "id":
                msgid = (msgid or "") + value
            elif state == "str":
                msgstr += value
            continue
        return None
    if msgid:
        catalog[msgid] = msgstr
    return catalog


def _read_mo_ids(mo_path):
    """读取 mo 文件的 msgid 到 msgstr 映射。

    参数：
        mo_path：mo 路径

    返回：
        字典；文件损坏时为 None
    """
    import struct

    try:
        data = mo_path.read_bytes()
    except OSError:
        return None
    if len(data) < 28:
        return None
    if struct.unpack("<I", data[:4])[0] != 0x950412DE:
        return None
    unpacked = struct.unpack("<6I", data[4:28])
    count, key_table, value_table = unpacked[1], unpacked[2], unpacked[3]
    catalog = {}
    try:
        for index in range(count):
            key_len, key_off = struct.unpack(
                "<2I", data[key_table + index * 8 : key_table + index * 8 + 8]
            )
            value_len, value_off = struct.unpack(
                "<2I", data[value_table + index * 8 : value_table + index * 8 + 8]
            )
            key = data[key_off : key_off + key_len].decode("utf-8")
            value = data[value_off : value_off + value_len].decode("utf-8")
            if key:
                catalog[key] = value
    except (struct.error, UnicodeDecodeError):
        return None
    return catalog



class _ConfigKeyVisitor:
    """收集 `self.<配置属性>` 字典里的字符串键，并按属性名分组。

    只认字面量键：常量拼接、变量、推导式拼出来的键无法静态确定，记进
    `dynamic`，由报告注明「有键没能静态检查」，而不是当作合规放过。
    """

    def __init__(self):
        """初始化分组结果。"""
        self.keys = {attr: [] for attr in CONFIG_KEY_ATTRS}
        self.dynamic = []

    def visit(self, tree):
        """遍历整棵 ast 树。

        参数：
            tree：ast 模块节点
        """
        import ast

        ast.NodeVisitor.__init__(self)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                self._collect_targets(node.targets, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self._collect_targets([node.target], node.value)
            elif isinstance(node, ast.Call):
                self._collect_update(node)

    def _collect_targets(self, targets, value):
        """处理 `self.<attr> = {...}` 与 `self.<attr>["键"] = ...` 两种赋值。"""
        import ast

        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in self.keys:
                self._add_dict_keys(target.attr, value)
            elif isinstance(target, ast.Subscript):
                holder = target.value
                if isinstance(holder, ast.Attribute) and holder.attr in self.keys:
                    key = target.slice
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        self.keys[holder.attr].append(key.value)
                    else:
                        self.dynamic.append(holder.attr)

    def _collect_update(self, node):
        """处理 `self.<attr>.update({...})`。"""
        import ast

        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "update":
            return
        holder = func.value
        if not isinstance(holder, ast.Attribute) or holder.attr not in self.keys:
            return
        for arg in node.args:
            self._add_dict_keys(holder.attr, arg)

    def _add_dict_keys(self, attr, node):
        """从字典字面量里取出字符串键。

        参数：
            attr：配置属性名
            node：被赋值或传入的表达式
        """
        import ast

        if not isinstance(node, ast.Dict):
            self.dynamic.append(attr)
            return
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.keys[attr].append(key.value)
            else:
                self.dynamic.append(attr)


def check_config_key_naming():
    """检查 8：扩展内的配置键是否符合键名约定。

    三条判据：

    1. 键必须是可打印 ASCII 且首尾无空白——中文键会原样写进持久化 JSON，
       日后改键让用户旧配置全部失效；首尾空白会让翻译目录的 msgid 永远匹配不上；
    2. 下划线开头的键只承载「隐藏」语义（框架据此过滤界面），因此不得出现在
       `config_description` 与 `config_type` 里——这两处的内容要渲染到界面；
    3. `config_description` 与 `config_type` 的键必须在 `default_config` 里存在，
       否则该配置项不会渲染，属静默不生效。

    第 3 条报警告而非错误：键可能由基类提供，静态分析只看得到本文件。
    命名风格（Title Case 名词短语）不在此校验——风格无法机械判定，
    约定见 `project-ok-script-tasks` 技能。

    返回：
        缺口列表
    """
    import ast

    findings = []
    root = EXTENSIONS_DIR / CAPABILITIES_DIR_NAME
    if not root.is_dir():
        return findings

    for path in sorted(root.rglob("*.py")):
        text = read_text(path)
        if text is None:
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            findings.append(
                Finding(LEVEL_ERROR, "配置键", rel + " 语法错误，无法检查配置键：" + str(exc))
            )
            continue

        visitor = _ConfigKeyVisitor()
        visitor.visit(tree)

        defaults = set(visitor.keys["default_config"])

        # 判据 1：可打印 ASCII，首尾无空白。
        for attr in CONFIG_KEY_ATTRS:
            for key in visitor.keys[attr]:
                if key != key.strip():
                    findings.append(
                        Finding(
                            LEVEL_ERROR,
                            "配置键",
                            rel + " 的配置键「" + key + "」首尾带空白——"
                            "翻译目录的 msgid 要求逐字符一致，带空白永远匹配不上",
                        )
                    )
                elif not key.isascii() or not key.isprintable():
                    findings.append(
                        Finding(
                            LEVEL_ERROR,
                            "配置键",
                            rel + " 的配置键「" + key + "」含非 ASCII 或不可打印字符——"
                            "键会原样写入持久化 JSON，非英文键日后无法安全改名",
                        )
                    )

        # 判据 2：下划线开头的键只用于隐藏，不得出现在要渲染的两处。
        for attr in ("config_description", "config_type"):
            for key in visitor.keys[attr]:
                if key.startswith("_"):
                    findings.append(
                        Finding(
                            LEVEL_ERROR,
                            "配置键",
                            rel + " 在 " + attr + " 里登记了下划线开头的键「" + key + "」——"
                            "下划线在框架里表示「隐藏」，两处登记会得到永不显示的配置项",
                        )
                    )

        # 判据 3：三处对齐。
        for attr in ("config_description", "config_type"):
            for key in visitor.keys[attr]:
                if key.startswith("_") or key in defaults:
                    continue
                findings.append(
                    Finding(
                        LEVEL_WARN,
                        "配置键",
                        rel + " 的 " + attr + " 登记了「" + key + "」，"
                        "但本文件的 default_config 里没有该键——若键由基类提供可忽略，"
                        "否则该配置项不会渲染",
                    )
                )

        if visitor.dynamic:
            findings.append(
                Finding(
                    LEVEL_INFO,
                    "配置键",
                    rel + " 的 "
                    + "/".join(sorted(set(visitor.dynamic)))
                    + " 含非字面量键，未能静态检查，需人工核对",
                )
            )

    return findings



def _ui_strings_in_capability(package_dir):
    """列出能力包里界面可见的字符串字面量。

    口径与 i18n 技能一致：只认 `self.<属性>` 上的字面量键与值。动态拼接看不见，
    因此返回的是下限，只用于「有文案却没建 catalog」的提示，不作阻断依据。

    参数：
        package_dir：能力包目录

    返回：
        字符串集合
    """
    import ast

    collected = set()
    for path in sorted(package_dir.rglob("*.py")):
        if I18N_DIR_NAME in path.relative_to(package_dir).parts:
            continue
        text = read_text(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if not isinstance(target, ast.Attribute):
                        continue
                    if target.attr in UI_STRING_ATTRS:
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            value = node.value.value.strip()
                            if value:
                                collected.add(value)
                    elif target.attr in UI_DICT_ATTRS:
                        collected.update(_dict_keys_and_values(node.value))
            elif isinstance(node, ast.Call):
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "update"):
                    continue
                holder = func.value
                if isinstance(holder, ast.Attribute) and holder.attr in UI_DICT_ATTRS:
                    for arg in node.args:
                        collected.update(_dict_keys_and_values(arg))
    return collected


def _dict_keys_and_values(node):
    """从字典字面量里取出键与值中的字符串。

    参数：
        node：表达式节点

    返回：
        字符串集合
    """
    import ast

    found = set()
    if not isinstance(node, ast.Dict):
        return found
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.strip():
            found.add(key.value.strip())
        if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.strip():
            found.add(value.value.strip())
        elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            for item in value.elts:
                if (
                    isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                    and item.value.strip()
                ):
                    found.add(item.value.strip())
    return found


def check_i18n_catalogs():
    """检查 9：能力包内的翻译目录是否完整且同步。

    四项判据：

    1. i18n/<locale>/LC_MESSAGES/ok_ext.po 必须有同名 .mo（错误）——
       运行时只读 .mo，缺了等于没翻译；
    2. .po 与 .mo 的 msgid 集合与译文必须一致（错误）——「改了 po 忘编译」；
    3. catalog 里不得有译文为空的条目（错误）——空串会遮蔽上游同名条目的译文；
    4. 能力包有界面可见文案却没有任何 catalog（警告）——大概率漏做本地化。

    第 4 条只报警告：文案可能由基类提供或来自配置，脚本看不到全貌。

    返回：
        缺口列表
    """
    findings = []
    root = EXTENSIONS_DIR / CAPABILITIES_DIR_NAME
    if not root.is_dir():
        return findings

    for package_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if package_dir.name == "__pycache__":
            continue
        package_rel = package_dir.relative_to(PROJECT_ROOT).as_posix()
        catalog_root = package_dir / I18N_DIR_NAME

        if not catalog_root.is_dir():
            ui_strings = _ui_strings_in_capability(package_dir)
            if ui_strings:
                sample = "、".join(sorted(ui_strings)[:3])
                findings.append(
                    Finding(
                        LEVEL_WARN,
                        "本地化",
                        package_rel + " 含界面可见文案（如 " + sample + "），但没有 "
                        + I18N_DIR_NAME + "/ 目录——扩展文案应落在此处走 ok_ext 域，不改上游 i18n/",
                    )
                )
            continue

        for po_path in sorted(catalog_root.rglob("*.po")):
            rel = po_path.relative_to(PROJECT_ROOT).as_posix()
            po_catalog = _parse_catalog(po_path)
            if po_catalog is None:
                findings.append(
                    Finding(
                        LEVEL_WARN,
                        "本地化",
                        rel + " 含本检查不支持的 po 构造（msgctxt / msgid_plural / fuzzy），"
                        "已跳过校验，需人工核对",
                    )
                )
                continue

            for key in sorted(key for key, value in po_catalog.items() if not value):
                findings.append(
                    Finding(
                        LEVEL_ERROR,
                        "本地化",
                        rel + " 的条目「" + key + "」译文为空——"
                        "空 msgstr 会在链上命中并遮蔽上游同名条目的译文",
                    )
                )

            mo_path = po_path.with_suffix(".mo")
            mo_rel = mo_path.relative_to(PROJECT_ROOT).as_posix()
            if not mo_path.is_file():
                findings.append(
                    Finding(
                        LEVEL_ERROR,
                        "本地化",
                        rel + " 缺少编译产物 " + mo_rel + "——运行时只读 .mo，缺了等于没翻译",
                    )
                )
                continue

            mo_catalog = _read_mo_ids(mo_path)
            if mo_catalog is None:
                findings.append(
                    Finding(LEVEL_ERROR, "本地化", mo_rel + " 无法解析，需重新编译")
                )
                continue

            only_po = sorted(set(po_catalog) - set(mo_catalog))
            only_mo = sorted(set(mo_catalog) - set(po_catalog))
            differ = sorted(
                key for key in set(po_catalog) & set(mo_catalog) if po_catalog[key] != mo_catalog[key]
            )
            for key in only_po:
                findings.append(
                    Finding(
                        LEVEL_ERROR,
                        "本地化",
                        rel + " 的条目「" + key + "」未出现在 .mo 中——改了 po 未重新编译",
                    )
                )
            for key in only_mo:
                findings.append(
                    Finding(
                        LEVEL_ERROR,
                        "本地化",
                        mo_rel + " 的条目「" + key + "」在 po 中已不存在——需重新编译",
                    )
                )
            for key in differ:
                findings.append(
                    Finding(
                        LEVEL_ERROR,
                        "本地化",
                        rel + " 的条目「" + key + "」译文与 .mo 不一致——需重新编译",
                    )
                )
    return findings

# ---------------------------------------------------------------------------
# audit 模式
# ---------------------------------------------------------------------------


def merge_exit_codes(doc_code, code_code):
    """合并文档契约与代码落位的退出码。

    规则按严重度取高值，但「环境不可用」（3）优先于一切——它表示检查没真正跑完，
    此时报出的 0 或 1 都不代表代码合规。

    参数：
        doc_code: 委托体检的退出码
        code_code: 代码落位体检的退出码

    返回：
        合并后的退出码
    """
    if 3 in (doc_code, code_code):
        return 3
    return max(doc_code, code_code)


def run_audit():
    """执行全部检查并输出合并报告。

    返回：
        退出码（0 通过 / 1 警告 / 2 错误 / 3 配置或环境不可用）
    """
    print("扩展能力体检（文档契约 + 代码落位）")
    print("项目根：" + str(PROJECT_ROOT))
    print("")

    doc_code = run_delegated_doc_audit()

    findings = []
    findings.extend(check_patch_assertion())
    findings.extend(check_sys_path_pollution())
    findings.extend(check_package_structure())
    findings.extend(check_capability_layout())
    findings.extend(check_capability_naming())
    findings.extend(check_capability_isolation())
    findings.extend(check_config_key_naming())
    findings.extend(check_i18n_catalogs())
    findings.extend(check_code_changed_hint())

    errors = [item for item in findings if item.level == LEVEL_ERROR]
    warns = [item for item in findings if item.level == LEVEL_WARN]
    infos = [item for item in findings if item.level == LEVEL_INFO]

    print("—— 代码落位（本技能）——")
    if not findings:
        print("  无缺口。")
    for group, label in ((errors, "错误"), (warns, "警告"), (infos, "提示")):
        for item in group:
            print("  " + str(item))
    print("")
    print(
        "代码落位结论：错误 %d，警告 %d，提示 %d"
        % (len(errors), len(warns), len(infos))
    )

    if errors:
        code_code = 2
    elif warns:
        code_code = 1
    else:
        code_code = 0

    final = merge_exit_codes(doc_code, code_code)
    print("")
    if final == 3:
        print("结论：有检查未能执行，先修复环境（多为 devflow 包未安装或配置不可用）。")
    elif final == 2:
        print("结论：存在错误，需修复后重跑。")
    elif final == 1:
        print("结论：无错误，但存在需人工判断的警告。")
    else:
        print("结论：通过。")
    return final


def main(argv):
    """命令行入口。

    参数：
        argv: 不含程序名的参数列表

    返回：
        退出码
    """
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0

    if argv[0] == "audit":
        return run_audit()

    print("未知命令：" + argv[0], file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


USAGE = """扩展能力守卫

用法：
  extension_capability_guard.py audit   体检代码落位，并委托 devflow-guard 体检文档契约

文档流程（explore / new / adr / status / archive）与文档契约体检由引擎侧
devflow-guard 提供，本技能不重复实现。

退出码：0 通过 / 1 警告 / 2 错误 / 3 配置或环境不可用
"""


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
