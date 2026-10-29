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
        if not (path / "__init__.py").exists():
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            findings.append(
                Finding(LEVEL_ERROR, "包结构", rel + " 缺少 __init__.py，会退化为命名空间包")
            )
    return findings


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
