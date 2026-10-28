# -*- coding: utf-8 -*-
"""扩展能力守卫：约束扩展能力的开发流程。

两个模式：
    new <能力名>  —— 生成 design/plan/testing 三份骨架，并在能力总览表追加一行
    audit         —— 体检文档契约与代码落位，按三档输出缺口

设计前提（决定了实现方式）：
    1. 不改动任何既有文档内容，只做检查与追加；判断与措辞由人负责。
    2. 项目根由 __file__ 上溯定位，不读配置文件——这三个路径是本项目结构的一部分，
       改名意味着整套体系重构，不是可配置项。
    3. 分级用退出码承载，便于接入 CI：0 通过 / 1 警告 / 2 错误。

用法：
    python .codebuddy.project/skills/project/project-extension-capability-guard/scripts/extension_capability_guard.py audit
    python ... new config-overrides
"""

import re
import sys
from datetime import date
from pathlib import Path

# 项目根：scripts/ -> project-extension-capability-guard/ -> project/ -> skills/ -> .codebuddy.project/ -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[5]

DOCS_DIR = PROJECT_ROOT / "extensions_docs"
README_PATH = DOCS_DIR / "README.md"
EXTENSIONS_DIR = PROJECT_ROOT / "extensions"

# 能力总览表的固定契约（七列）。表头不符即报错，不静默降级。
TABLE_HEADER = ["能力", "状态", "设计", "实施计划", "实施偏差", "测试", "决策记录"]

# 状态枚举。新增取值时必须同步 extensions_docs/README.md 与规则文件。
STATUS_VALUES = {"草稿", "评审中", "已定", "已实施", "已废弃"}

# 单元格合法占位值：— 表示该阶段未产出，待补 表示应产出但暂缺。
# 「待补」是契约内的正式标记，不视为错误。
PLACEHOLDER_DASH = "—"
PLACEHOLDER_TODO = "待补"

# 需要文件头的文档类型：能力文档才有，目录说明与模板没有。
CAPABILITY_DOC_PATTERNS = (
    "design/*.md",
    "plans/*.md",
    "testing/*.md",
    "decisions/[0-9][0-9][0-9][0-9]-*.md",
)

REQUIRED_FIELDS = ("状态", "关联能力", "创建日期", "更新日期")

# 生成器字段：证明该文档由技能生成骨架。字段名固定，取值必须落在合法集合内。
# 注意：该字段只能证明「文件头里声明了来源」，不能证明内容经过推演——
# 内容是否合规仍需评审。此局限在规则文件与 README 中一并说明。
GENERATOR_FIELD = "生成器"
GENERATOR_VALUES = {"project-extension-capability-guard/new"}

# 文件头字段表的完整契约：四个业务字段 + 生成器，共五行。
HEADER_FIELDS = REQUIRED_FIELDS + (GENERATOR_FIELD,)

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


def die(message, code=2):
    """打印错误并退出。

    参数：
        message: 错误说明
        code: 退出码
    """
    print("[错误] " + message, file=sys.stderr)
    sys.exit(code)


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


def parse_head_fields(text, names=None):
    """解析文档开头的字段表。

    字段表形如：
        | 字段 | 值 |
        |---|---|
        | 状态 | 草稿 |

    参数：
        text: 文档全文
        names: 要解析的字段名序列；缺省为四个业务字段

    返回：
        {字段名: 值}；未找到的字段不出现在结果中
    """
    found = {}
    for name in (names if names is not None else REQUIRED_FIELDS):
        pattern = re.compile(r"^\|\s*" + name + r"\s*\|\s*(.*?)\s*\|\s*$", re.MULTILINE)
        m = pattern.search(text)
        if m:
            found[name] = m.group(1)
    return found


def iter_capability_docs():
    """遍历所有需要文件头的能力文档。

    返回：
        文档路径列表（已排序）
    """
    docs = []
    for pattern in CAPABILITY_DOC_PATTERNS:
        docs.extend(sorted(DOCS_DIR.glob(pattern)))
    # 目录 README 是说明文档，不要求文件头
    return [p for p in docs if p.name != "README.md"]


def parse_capability_table(text):
    """解析能力总览表。

    参数：
        text: README 全文

    返回：
        (rows, error)。rows 为 [{列名: 单元格}] 列表；error 为表头契约不符时的说明。
    """
    lines = text.splitlines()
    header_index = None
    for i, line in enumerate(lines):
        cells = split_row(line)
        if cells == TABLE_HEADER:
            header_index = i
            break
    if header_index is None:
        return [], "未找到能力总览表，或表头不符合七列契约：" + " / ".join(TABLE_HEADER)

    rows = []
    for line in lines[header_index + 1:]:
        stripped = line.strip()
        if not stripped:
            # 空行表示表格结束
            break
        if not stripped.startswith("|"):
            break
        cells = split_row(line)
        if not cells:
            continue
        if set(cells) <= {"---", "---:", ":---", ":---:"}:
            continue
        if len(cells) != len(TABLE_HEADER):
            continue
        rows.append(dict(zip(TABLE_HEADER, cells)))
    return rows, None


def split_row(line):
    """按竖线切分表格行，剥离反引号与首尾空白。

    参数：
        line: 一行 Markdown 表格文本

    返回：
        单元格列表；不是表格行时返回空列表
    """
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    parts = stripped.split("|")
    # 首尾为空的片段（表格行以竖线开头结尾）
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    cells = []
    for part in parts:
        cell = part.strip()
        if cell.startswith("`") and cell.endswith("`") and len(cell) >= 2:
            cell = cell[1:-1]
        cells.append(cell)
    return cells


def resolve_doc_ref(value):
    """把总览表单元格解析为路径列表。

    单元格支持四种写法：
        1. 占位值 — / 待补，表示该阶段未产出或暂缺，不算缺口
        2. 带扩展名的完整路径，如 design/foo.md、extensions_tests/TestX.py
        3. 目录前缀型引用，如 `decisions/0001` 起，表示从该 ADR 起都相关
        4. 以 / 结尾的目录引用

    参数：
        value: 单元格原始文本

    返回：
        (refs, is_error)。refs 为引用片段列表（可能是文件也可能是目录）；
        is_error 表示该值非法（既非占位也解析不出引用）
    """
    if value in (PLACEHOLDER_DASH, PLACEHOLDER_TODO, ""):
        return [], False
    candidates = re.findall(r"[A-Za-z0-9_./-]+", value)
    # 过滤掉纯中文与纯数字片段，只保留看起来像路径的（含 / 或带扩展名）
    refs = [c for c in candidates if "/" in c or re.search(r"\.(?:md|py)$", c)]
    if refs:
        return refs, False
    return [], True


def expand_ref(ref):
    """把一个引用片段展开为实际存在的文件列表。

    目录前缀型引用（如 decisions/0001）按前缀匹配 decisions/ 下同名前缀的文件。

    参数：
        ref: 引用片段，如 design/foo.md 或 decisions/0001

    返回：
        实际文件路径列表；不存在时返回空列表
    """
    # 三种基准前缀：以项目根为基准的完整路径、扩展文档内的相对路径
    root_prefixes = ("extensions_docs/", "extensions_tests/", "extensions/", "tests/")
    if ref.startswith(root_prefixes):
        base = PROJECT_ROOT / ref
    else:
        base = DOCS_DIR / ref
    if base.exists():
        return [base]
    # 目录前缀型：按文件名前缀在父目录下查找
    if base.parent.is_dir():
        prefix = base.name
        return sorted(p for p in base.parent.glob(prefix + "*") if p.is_file())
    return []


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------


def check_head_fields():
    """检查 1：能力文档的文件头四字段齐全，且状态值合法。

    返回：
        缺口列表
    """
    findings = []
    for path in iter_capability_docs():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        text = read_text(path)
        if text is None:
            findings.append(Finding(LEVEL_ERROR, "字段齐全", rel + " 无法读取"))
            continue
        # 只在文件头表格区域内解析：对全文做正则搜索时，正文任意位置的
        # 一行「| 状态 | 草稿 |」都能让「文件头缺字段」这条检查失效。
        header = extract_header_table(text)
        if not header:
            findings.append(Finding(LEVEL_ERROR, "字段齐全", rel + " 缺少文件头字段表"))
            continue
        fields = parse_head_fields(header)
        missing = [n for n in REQUIRED_FIELDS if n not in fields]
        if missing:
            findings.append(Finding(LEVEL_ERROR, "字段齐全", rel + " 缺少字段：" + "、".join(missing)))
            continue
        status = fields["状态"]
        if status not in STATUS_VALUES:
            findings.append(Finding(
                LEVEL_ERROR, "字段齐全",
                rel + " 的状态值非法：" + status + "（合法值：" + "、".join(sorted(STATUS_VALUES)) + "）",
            ))
    return findings


def check_table_contract():
    """检查 2：能力总览表存在且表头符合七列契约。

    返回：
        (rows, 缺口列表)
    """
    text = read_text(README_PATH)
    if text is None:
        return [], [Finding(LEVEL_ERROR, "表头契约", "未找到 extensions_docs/README.md")]
    rows, error = parse_capability_table(text)
    if error:
        return [], [Finding(LEVEL_ERROR, "表头契约", error)]
    return rows, []


def check_table_vs_disk(rows):
    """检查 3：总览表与磁盘双向对账。

    正向：表里引用的文件必须存在。
    反向：磁盘上独立存在的能力文档必须在表中有行。

    参数：
        rows: 总览表行

    返回：
        缺口列表
    """
    findings = []
    registered = set()

    for row in rows:
        name = row["能力"]
        for column in ("设计", "实施计划", "实施偏差", "测试", "决策记录"):
            refs, is_error = resolve_doc_ref(row[column])
            if is_error:
                findings.append(Finding(
                    LEVEL_ERROR, "表↔磁盘",
                    name + " 的「" + column + "」列取值非法：" + row[column],
                ))
                continue
            for ref in refs:
                expanded = expand_ref(ref)
                if not expanded:
                    findings.append(Finding(
                        LEVEL_ERROR, "表↔磁盘",
                        name + " 的「" + column + "」列指向不存在的文件：" + ref,
                    ))
                    continue
                for full in expanded:
                    registered.add(full.resolve())

    # 反向：磁盘上的能力文档是否都已登记。
    # decisions/ 下的 ADR 不参与反向检查——它是全局决策，不隶属于某个能力，
    # 只有被表里显式引用时才需要存在（正向已校验）。
    for path in iter_capability_docs():
        if path.name == "README.md":
            continue
        if path.parent.name == "decisions":
            continue
        if path.resolve() in registered:
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        findings.append(Finding(LEVEL_WARN, "表↔磁盘", rel + " 存在但未在能力总览表登记"))
    return findings


def check_adr_numbering():
    """检查 4：ADR 编号连续、无重复、文件名合规。

    返回：
        缺口列表
    """
    findings = []
    adr_dir = DOCS_DIR / "decisions"
    if not adr_dir.is_dir():
        return [Finding(LEVEL_ERROR, "ADR 编号", "未找到 extensions_docs/decisions/")]

    seen = {}
    for path in sorted(adr_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        m = re.match(r"^(\d{4})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$", path.name)
        if not m:
            findings.append(Finding(
                LEVEL_ERROR, "ADR 编号",
                "文件名不符合 NNNN-<slug>.md 契约：" + path.name,
            ))
            continue
        number = m.group(1)
        if number in seen:
            findings.append(Finding(
                LEVEL_ERROR, "ADR 编号",
                "编号重复：" + number + "（" + seen[number] + " 与 " + path.name + "）",
            ))
        seen[number] = path.name

    if not seen:
        return findings
    numbers = sorted(int(n) for n in seen)
    for expected, actual in enumerate(numbers, start=1):
        if expected != actual:
            findings.append(Finding(
                LEVEL_WARN, "ADR 编号",
                "编号不连续：期望 " + str(expected).zfill(4) + "，实际为 " + str(actual).zfill(4),
            ))
            break
    return findings


def _doc_path_exists(value):
    """判断总览表单元格指向的文档是否存在。

    与 `check_table_vs_disk` 用同一套解析（`resolve_doc_ref` + `expand_ref`）：
    单元格可能带 `extensions_docs/` 前缀、或用「、」分隔多个引用、或写目录前缀。
    只看是否能解析出至少一个存在的文件。

    参数：
        value: 单元格原文

    返回：
        存在返回 True
    """
    refs, is_error = resolve_doc_ref(value)
    if is_error or not refs:
        return False
    return any(expand_ref(ref) for ref in refs)

def check_plan_deviation_pair(rows):
    """检查 5：plan 与 deviation 的配对完整性。

    以总览表该行的实际路径为准，而不是按 <能力>-plan.md 猜名：能力归档后路径会变成
    archive/<能力名>/...，按名字猜会对着已归档能力稳定误报「缺少计划」。

    规则：状态为「已定 / 已实施」时，该行的计划列应指向存在的文件；
    偏差列若为占位符或指向的文件不存在，则读计划正文：正文在独立一行声明「无偏差：」
    时不报缺口，否则告警。限定行首匹配而非全文子串，避免正文引用旧结论或抄写提示文案被误判。

    参数：
        rows: 总览表行（每项含 能力 / 状态 / 单元格）

    返回：
        缺口列表
    """
    findings = []
    if not rows:
        return findings
    settled = {"已定", "已实施"}

    def column_rel(row, predicate):
        """按表头语义取出该行匹配列的单元格值。

        rows 的每一项是 {列名: 值} 字典（由 parse_capability_table 产出），
        因此按列名取值，不按列序号索引。
        """
        for column in TABLE_HEADER[2:]:
            if not predicate(column):
                continue
            cell = row.get(column, "")
            if cell in (PLACEHOLDER_DASH, PLACEHOLDER_TODO, ""):
                return ""
            return cell
        return ""

    for row in rows:
        if row.get("状态") not in settled:
            continue
        capability = row.get("能力", "")
        plan_rel = column_rel(row, lambda c: "计划" in c and "偏差" not in c)
        deviation_rel = column_rel(row, lambda c: "偏差" in c)
        if not plan_rel or not _doc_path_exists(plan_rel):
            findings.append(Finding(
                LEVEL_WARN, "plan/偏差配对",
                capability + " 的状态为「" + row["状态"] + "」但缺少实施计划",
            ))
            continue
        if not deviation_rel or not _doc_path_exists(deviation_rel):
            # 逃生口：计划正文显式声明「无偏差」时不算缺口。
            # 不提供该判定会让「确实没偏差」的能力被迫造一份空偏差文档，
            # 而空文档比没有更糟——它看起来像记录，实际什么都没说。
            plan_files = [p for ref in resolve_doc_ref(plan_rel)[0] for p in expand_ref(ref)]
            plan_text = read_text(plan_files[0]) if plan_files else ""
            # 只认独立一行的声明：「无偏差：…」或「无偏差。」，避免正文提及该词即算已声明
            if re.search(r"(?m)^\s*无偏差\s*[:：。]", plan_text):
                continue
            findings.append(Finding(
                LEVEL_WARN, "plan/偏差配对",
                capability + " 的状态为「" + row["状态"] + "」但缺少实施偏差记录"
                "（若确无偏差，在计划正文写明「无偏差」）",
            ))
    return findings

def extract_header_table(text):
    """提取文件头字段表区域。

    定位首个 `| 字段 | 值 |` 表头行，收集其后连续的表格行，遇非表格行即结束。
    只在这段区域内查找字段，避免正文里偶然出现的同名行被当作字段。

    参数：
        text: 文档全文

    返回：
        表头区域文本；未找到字段表时返回空串
    """
    lines = text.splitlines()
    start = None
    in_fence = False
    for index, line in enumerate(lines):
        # 跳过代码围栏内的内容：正文里的模板示例不应被当作真表头
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if re.match(r"^\|\s*字段\s*\|\s*值\s*\|\s*$", line):
            start = index
            break
    if start is None:
        return ""
    collected = []
    for line in lines[start:]:
        if not line.strip().startswith("|"):
            break
        collected.append(line)
    return "\n".join(collected)


def check_generator_mark():
    """检查 6：能力文档的生成器字段是否存在且取值合法。

    与旧实现的三点差异：
    - 字段解析后比对确切取值，不做子串匹配（避免空值、任意文本蒙混通过）；
    - 不再只查草稿态，所有能力文档一律适用（填完内容改状态后不会失去检查）；
    - 分级为错误，与「新增能力必须经技能生成骨架」的强制力对齐。

    局限：只能证明文件头声明了来源，不能证明内容经过推演。

    返回：
        缺口列表
    """
    findings = []
    for path in iter_capability_docs():
        text = read_text(path)
        if text is None:
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        header = extract_header_table(text)
        if not header:
            continue  # 缺表由「字段齐全」检查报，避免同一根因报两条
        fields = parse_head_fields(header, HEADER_FIELDS)
        value = fields.get(GENERATOR_FIELD)
        if value is None:
            findings.append(Finding(
                LEVEL_ERROR, "生成标记",
                rel + " 缺少「" + GENERATOR_FIELD + "」字段（应由 new 生成，不得手工省略）",
            ))
        elif value not in GENERATOR_VALUES:
            findings.append(Finding(
                LEVEL_ERROR, "生成标记",
                rel + " 的「" + GENERATOR_FIELD + "」取值为「" + value + "」，不在合法集合内："
                + "、".join(sorted(GENERATOR_VALUES)),
            ))
    return findings


def check_patch_assertion():
    """检查 7：每个 patch 模块必须提供 apply()，且函数体内有反抛断言。

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

        has_raise = False
        for node in ast.walk(apply_fn):
            if isinstance(node, ast.Raise):
                has_raise = True
                break
        if not has_raise:
            findings.append(Finding(
                LEVEL_ERROR, "patch 断言",
                rel + " 的 apply() 内无 raise 断言，目标消失时会静默失效",
            ))
    return findings


def check_sys_path_pollution():
    """检查 8：extensions/ 内不得把 extensions 加入 sys.path。

    返回：
        缺口列表
    """
    findings = []
    if not EXTENSIONS_DIR.is_dir():
        return findings
    pattern = re.compile(r"sys\.path\.(insert|append)\s*\(")
    for path in sorted(EXTENSIONS_DIR.rglob("*.py")):
        text = read_text(path)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line) and "extensions" in line:
                rel = path.relative_to(PROJECT_ROOT).as_posix()
                findings.append(Finding(
                    LEVEL_ERROR, "sys.path 污染",
                    rel + ":" + str(i) + " 把 extensions 加入 sys.path，会与上游同名包合并",
                ))
    return findings


def check_package_structure():
    """检查 9：extensions/ 下每层目录必须有 __init__.py。

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
        if not (path / "__init__.py").exists():
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            findings.append(Finding(
                LEVEL_ERROR, "包结构", rel + " 缺少 __init__.py，会退化为命名空间包",
            ))
    return findings


def check_code_changed_hint():
    """检查 10：extensions/ 有代码变更但总览表未变时给出提示（弱信号）。

    这是提示而非错误——能力登记表变化与代码变更不同步是常见且合理的。

    返回：
        缺口列表
    """
    import subprocess

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
        findings.append(Finding(
            LEVEL_INFO, "登记同步",
            "extensions/ 有未提交改动但能力总览表未变——若是新增能力，需登记",
        ))
    return findings


# ---------------------------------------------------------------------------
# audit 模式
# ---------------------------------------------------------------------------


def run_audit():
    """执行全部检查并输出报告。

    返回：
        退出码（0 通过 / 1 警告 / 2 错误）
    """
    print("扩展能力文档与代码体检")
    print("项目根：" + str(PROJECT_ROOT))
    print()

    findings = []
    rows, table_findings = check_table_contract()
    findings.extend(table_findings)
    findings.extend(check_head_fields())
    if not table_findings:
        findings.extend(check_table_vs_disk(rows))
    findings.extend(check_adr_numbering())
    findings.extend(check_plan_deviation_pair(rows))
    findings.extend(check_generator_mark())
    findings.extend(check_patch_assertion())
    findings.extend(check_sys_path_pollution())
    findings.extend(check_package_structure())
    findings.extend(check_code_changed_hint())

    errors = [f for f in findings if f.level == LEVEL_ERROR]
    warns = [f for f in findings if f.level == LEVEL_WARN]
    infos = [f for f in findings if f.level == LEVEL_INFO]

    for group, label in ((errors, "错误"), (warns, "警告"), (infos, "提示")):
        if not group:
            continue
        print("—— " + label + "（" + str(len(group)) + "）——")
        for f in group:
            print("  " + str(f))
        print("")

    print("结论：错误 %d，警告 %d，提示 %d" % (len(errors), len(warns), len(infos)))
    if errors:
        print("存在错误，需修复后重跑。")
        return 2
    if warns:
        print("无错误，但存在需人工判断的警告。")
        return 1
    print("通过。")
    return 0


# ---------------------------------------------------------------------------
# new 模式
# ---------------------------------------------------------------------------

CAPABILITY_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# 骨架来源模板 -> 目标路径模板
SCAFFOLD_SPECS = (
    ("design.md", "design/{name}.md"),
    ("plan.md", "plans/{name}-plan.md"),
    ("testing.md", "testing/{name}-testing.md"),
)


def run_new(name):
    """生成能力文档骨架并登记总览表。

    三道运行时保护：能力名格式、目标文件已存在、表头契约。任一不满足即中止且不写任何文件。

    参数：
        name: 能力名（英文 kebab-case）

    返回：
        退出码
    """
    if not CAPABILITY_NAME_RE.match(name):
        die(
            "能力名必须是英文 kebab-case（小写字母、数字、短横线），如 config-overrides。"
            "收到：" + name,
        )

    readme_text = read_text(README_PATH)
    if readme_text is None:
        die("未找到 extensions_docs/README.md")
    rows, error = parse_capability_table(readme_text)
    if error:
        die("总览表不可解析，已中止（不写任何文件）：" + error)

    for row in rows:
        if row["能力"] == name:
            die("能力已存在于总览表：" + name + "。若需重建请手工处理。")

    targets = []
    for template_name, target_pattern in SCAFFOLD_SPECS:
        template = DOCS_DIR / "templates" / template_name
        if not template.exists():
            die("缺少模板：" + str(template.relative_to(PROJECT_ROOT).as_posix()))
        target = DOCS_DIR / target_pattern.format(name=name)
        targets.append((template, target))

    conflicts = [str(t) for _, t in targets if t.exists()]
    if conflicts:
        die("以下文件已存在，已中止（不覆盖）：\n  " + "\n  ".join(conflicts))

    today = date.today().isoformat()
    created = []
    for template, target in targets:
        text = read_text(template)
        if text is None:
            die("模板读取失败：" + template.relative_to(PROJECT_ROOT).as_posix())
        text = text.replace("<能力名>", name)
        text = text.replace("YYYY-MM-DD", today)
        text = text.replace("{{generator}}", sorted(GENERATOR_VALUES)[0])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        created.append(target.relative_to(PROJECT_ROOT).as_posix())

    # 路径列加反引号，与总览表现有行风格一致（split_row 解析时会剥离，不影响判定）
    new_row = (
        "| " + name + " | 草稿 | `design/" + name + ".md` | `plans/" + name + "-plan.md` | "
        + PLACEHOLDER_DASH + " | `testing/" + name + "-testing.md` | " + PLACEHOLDER_DASH + " |"
    )
    updated = insert_table_row(readme_text, new_row)
    if updated is None:
        for target in created:
            (PROJECT_ROOT / target).unlink(missing_ok=True)
        die("未能在总览表定位插入点，已回滚生成的文件")
    README_PATH.write_text(updated, encoding="utf-8")

    print("已生成：")
    for item in created:
        print("  " + item)
    print("已在能力总览表登记：" + name)
    print("")
    print("下一步：填写 design 的方案与取舍，方案定稿后补 plan，开发完成后补 testing。")
    return 0


def insert_table_row(text, new_row):
    """在能力总览表最后一个数据行之后插入新行。

    参数：
        text: README 全文
        new_row: 待插入的表格行

    返回：
        新全文；无法定位插入点时返回 None
    """
    lines = text.splitlines()
    header_index = None
    for i, line in enumerate(lines):
        if split_row(line) == TABLE_HEADER:
            header_index = i
            break
    if header_index is None:
        return None

    last_row = header_index + 1
    for i in range(header_index + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            break
        if not stripped.startswith("|"):
            break
        last_row = i

    lines.insert(last_row + 1, new_row)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _render_template(template_name, values):
    """读取模板并替换占位符，返回正文。

    参数：
        template_name: templates/ 下的模板文件名
        values: 占位符键到替换值的映射（键形如 "<能力名>"）

    返回：
        替换后的正文
    """
    template = DOCS_DIR / "templates" / template_name
    if not template.exists():
        die("缺少模板：" + template.relative_to(PROJECT_ROOT).as_posix())
    text = read_text(template)
    if text is None:
        die("模板读取失败：" + template.relative_to(PROJECT_ROOT).as_posix())
    text = text.replace("{{generator}}", sorted(GENERATOR_VALUES)[0])
    text = text.replace("YYYY-MM-DD", date.today().isoformat())
    for key, value in values.items():
        text = text.replace(key, value)
    if "{{" in text:
        die("模板存在未替换的占位符：" + template_name)
    return text


def run_deviation(name):
    """为已登记能力补一份实施偏差记录。

    偏差文档不在 new 的三件套里（多数能力无偏差），因此单独提供入口：
    没有它时，「确实发生了偏差」的能力只能手工复制模板，而那会被 audit 判为未走技能。

    参数：
        name: 能力名，须已在总览表登记

    返回：
        退出码
    """
    if not CAPABILITY_NAME_RE.fullmatch(name):
        die("能力名必须是英文 kebab-case，如 config-overrides。收到：" + name)

    readme_text = read_text(README_PATH)
    if readme_text is None:
        die("未找到 extensions_docs/README.md")
    rows, error = parse_capability_table(readme_text)
    if error:
        die("总览表不可解析，已中止：" + error)
    if not any(row["能力"] == name for row in rows):
        die("能力未登记，请先执行 new " + name + " 或手工登记总览表")

    target = DOCS_DIR / "plans" / (name + "-deviation.md")
    if target.exists():
        die("偏差记录已存在，不覆盖：" + target.relative_to(PROJECT_ROOT).as_posix())

    text = _render_template("deviation.md", {"<能力名>": name})
    target.write_text(text, encoding="utf-8")
    print("已生成：" + target.relative_to(PROJECT_ROOT).as_posix())
    print("下一步：填写每条偏差的「计划 / 实际 / 原因 / 影响」，并把总览表的偏差列指向它。")
    return 0


def run_adr(slug):
    """新建一份 ADR（不可变决策快照）。

    ADR 序号按现有文件最大序号加一，只增不复用。

    参数：
        slug: 决策标题的英文 kebab-case 片段

    返回：
        退出码
    """
    if not CAPABILITY_NAME_RE.fullmatch(slug):
        die("决策标识必须是英文 kebab-case，如 choose-overrides-location。收到：" + slug)

    decisions_dir = DOCS_DIR / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(p.name for p in decisions_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))
    next_number = int(existing[-1][:4]) + 1 if existing else 1
    target = decisions_dir / ("%04d-%s.md" % (next_number, slug))
    if target.exists():
        die("目标已存在，不覆盖：" + target.relative_to(PROJECT_ROOT).as_posix())

    text = _render_template("adr.md", {"<决策标题>": slug, "<slug>": slug, "<能力名>": "全局"})
    text = text.replace("# ADR", "# ADR-%04d" % next_number, 1)
    target.write_text(text, encoding="utf-8")
    print("已生成：" + target.relative_to(PROJECT_ROOT).as_posix())
    print("下一步：填写背景、候选、结论与影响面。ADR 一经定稿不可改写——被推翻时新建一份并指向被取代者。")
    return 0


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

    command = argv[0]
    if command == "audit":
        return run_audit()
    if command == "new":
        if len(argv) < 2:
            die("new 需要能力名，如：new config-overrides")
        return run_new(argv[1])
    if command == "deviation":
        if len(argv) < 2:
            die("deviation 需要能力名，如：deviation config-overrides")
        return run_deviation(argv[1])
    if command == "adr":
        if len(argv) < 2:
            die("adr 需要决策标识，如：adr choose-overrides-location")
        return run_adr(argv[1])

    print("未知命令：" + command, file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


USAGE = """扩展能力守卫

用法：
  extension_capability_guard.py audit              体检文档契约与代码落位
  extension_capability_guard.py new <能力名>      生成能力文档骨架并登记总览表
  extension_capability_guard.py deviation <能力名> 为已登记能力补实施偏差记录
  extension_capability_guard.py adr <决策标识>     新建 ADR（序号自动递增）

退出码：0 通过 / 1 警告 / 2 错误
"""


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
