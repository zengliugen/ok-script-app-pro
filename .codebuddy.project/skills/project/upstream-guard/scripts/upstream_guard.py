# -*- coding: utf-8 -*-
"""上游差异守护：盘点本项目相对上游的改动、校验登记完整性、生成登记条目。

用法（在项目根目录执行）：
    python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py
    python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py --changed
    python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py --add src/config.py

配置路径（项目侧固定位置，不经索引）：
    .codebuddy.project/configs/upstream/divergence.yaml     —— 上游差异登记清单
    .codebuddy.project/configs/upstream/capabilities.yaml   —— 已实现能力清单
    文件缺失或 YAML 损坏时直接报错退出（退出码 3），不回退内置默认值。

归属判定（本技能最容易出错的地方，详见 references/ownership-rules.md）：
    只有「锚点中存在、且不是 submodule」的路径才算上游来源文件，才可能需要登记。
    .codebuddy/（引擎）等 submodule 内的改动归各自的仓库，既不登记也不告警。
    .codebuddy.project/ 是本项目自有目录，同样不登记。

退出码：
    0 = 校验通过
    1 = 存在未登记或已失效的改动
    2 = git 操作失败（上游锚点不存在等）
    3 = 配置缺失或损坏
    4 = 参数指向的路径不属于上游来源（--add 拒绝生成条目）
"""

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

# 项目根：本脚本位于 <项目根>/.codebuddy.project/skills/project/upstream-guard/scripts/
PROJECT_ROOT = Path(__file__).resolve().parents[5]

# 配置路径：项目专有技能的配置属项目侧内容，直接定位，不经 .codebuddy/project.index.json。
# 索引是引擎与项目侧之间的注册表、随引擎共享并对所有消费项目一致；
# 把项目专有技能的配置登进去，会让其他项目被 codebuddy-init 生成无用骨架。
DIVERGENCE_PATH = PROJECT_ROOT / ".codebuddy.project" / "configs" / "upstream" / "divergence.yaml"
CAPABILITIES_PATH = PROJECT_ROOT / ".codebuddy.project" / "configs" / "upstream" / "capabilities.yaml"

# 上游专属的**代码**目录：新增项目代码落在这些目录会告警（应写 extensions/）。
# 注意 docs/ 与 tests/ 不在此列——两处均允许新增文件：
#   docs/  —— 新增文档页不构成冲突源（nav 条目改动才需登记）
#   tests/ —— 新增 Test*.py 是扩展测试的正常落点，且需被 run_tests.ps1 捡到
UPSTREAM_ONLY_DIRS = (
    "src/tasks/",
    "src/ui/",
    "src/globals.py",
)

# 本项目自有目录：上游没有这些路径，改动与新增都与上游无关，一律不登记。
# extensions/ 与 extensions_tests/ 是项目唯一自有代码区（上游不存在），
# 故其下任何改动都不构成上游冲突，也不该出现在 divergence.yaml 里。
PROJECT_OWNED_DIRS = (
    ".codebuddy.project/",
    "extensions/",
    "extensions_tests/",
)


def fail(message, code=3):
    """统一错误出口：明确报错并退出，不静默降级。"""
    print(f"[错误] {message}", file=sys.stderr)
    sys.exit(code)


def load_divergence(path):
    """读取差异清单；缺失即报错。"""
    if not path.is_file():
        fail(f"差异清单不存在：{path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        fail(f"差异清单 YAML 损坏：{exc}")

    anchor = data.get("synced_upstream_commit")
    if not anchor:
        fail("差异清单缺少 synced_upstream_commit（校验基准）")
    entries = data.get("entries") or []
    if not isinstance(entries, list):
        fail("差异清单 entries 必须是列表")
    for entry in entries:
        if not entry.get("path") or not entry.get("kind") or not entry.get("reason"):
            fail(f"差异清单条目缺少必填字段（path/kind/reason）：{entry}")
    return anchor, entries


def git(*args):
    """执行 git 命令并返回标准输出；失败时报错退出。"""
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None, result.stderr.strip()
    return result.stdout, None


def verify_anchor(anchor):
    """确认锚点在仓库中存在，否则基准无效。"""
    out, err = git("cat-file", "-e", f"{anchor}^{{commit}}")
    if err:
        fail(f"上游锚点不可解析：{anchor}（{err}）")


def submodule_paths():
    """读取 .gitmodules 声明的 submodule 路径（引擎仓库等独立仓库）。

    .codebuddy 以 submodule 形式检出时是**另一个 Git 仓库**，不是本项目的一部分，
    更不是上游文件。它的改动走引擎仓库提交，不登记进 divergence.yaml。
    用 .gitmodules 而非硬编码 .codebuddy：submodule 路径可由项目自行配置。
    """
    out, _ = git("config", "--file", ".gitmodules", "--get-regexp", "^submodule[.].*[.]path$")
    if not out:
        return ()
    paths = []
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip():
            paths.append(parts[1].strip().replace("\\", "/").rstrip("/") + "/")
    return tuple(paths)


def is_upstream_file(path, anchor):
    """判断路径是否属于上游来源：在锚点中存在且不是 submodule。

    submodule 在 git diff 中同样显示为 M，但它是独立仓库（如 .codebuddy 引擎），
    不属于上游文件，登记它没有意义。
    """
    out, _ = git("ls-tree", anchor, "--", path)
    if not out or not out.strip():
        return False
    # ls-tree 输出格式：<mode> <type> <sha>\t<path>，type 为 commit 即 submodule
    fields = out.split()
    return len(fields) >= 2 and fields[1] != "commit"


def classify(path, subs, anchor):
    """判定路径归属，返回 (归属类别, 是否算上游来源)。

    类别取值与处置：

    +------------+--------------------------------------------------+------------------+
    | 归属       | 含义                                             | 登记 / 告警      |
    +============+==================================================+==================+
    | submodule  | 落在 .gitmodules 声明的 submodule 内（如引擎）   | 都不做           |
    | project    | 本项目自有目录（.codebuddy.project/）            | 都不做           |
    | upstream   | 锚点中存在且不是 submodule，真正的上游来源文件   | 需要登记         |
    | unknown    | 锚点中不存在（本项目新增的顶层路径）             | 都不做           |
    +------------+--------------------------------------------------+------------------+

    先把 submodule 与项目自有目录判掉，再问锚点。顺序不能反：若某项目历史上
    把引擎当普通目录提交过，锚点里就会有该路径的 tree，只靠锚点判定会把整棵
    引擎树误认成上游文件——这正是本函数存在的理由。
    """
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]

    for sub in subs:
        if normalized == sub.rstrip("/") or normalized.startswith(sub):
            return "submodule", False
    for owned in PROJECT_OWNED_DIRS:
        if normalized == owned.rstrip("/") or normalized.startswith(owned):
            return "project", False
    if is_upstream_file(path, anchor):
        return "upstream", True
    return "unknown", False


def collect_diff(anchor, changed_only, subs):
    """返回 {路径: 状态} 的上游文件改动表。

    changed_only=True 时只看工作区与暂存区（尚未提交的改动），
    False 时看锚点到 HEAD 的全部差异。

    只有 classify 判为 upstream 的路径才进结果；submodule 与项目自有目录
    在这一步就被滤掉，因此引擎仓库的改动不会流到后面的登记判定里。
    """
    if changed_only:
        out, err = git("diff", "--name-status", "HEAD")
    else:
        out, err = git("diff", "--name-status", f"{anchor}..HEAD")
    if err:
        fail(f"git diff 执行失败：{err}", code=2)

    result = {}
    skipped = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        path = path.replace("\\", "/")
        raw_status = "M" if status.startswith("R") else status[0]
        owner, is_upstream = classify(path, subs, anchor)

        if raw_status == "A":
            # 新增文件在上游不存在，不构成冲突源；仅当落进上游专属代码目录才告警。
            if is_upstream or owner in ("submodule", "project"):
                continue
            if any(path.startswith(d) for d in UPSTREAM_ONLY_DIRS):
                result[path] = "A"
            continue

        if not is_upstream:
            # 记录被排除的非上游改动，用于消除「我改了引擎，为什么这里没提示」的困惑。
            if owner in ("submodule", "project"):
                skipped.append((path, owner))
            continue
        result[path] = raw_status

    if changed_only:
        # git diff HEAD 不含未跟踪文件，需单独取，否则新建文件落在上游目录时漏检
        untracked, err = git("ls-files", "--others", "--exclude-standard")
        if err:
            fail(f"git ls-files 执行失败：{err}", code=2)
        for line in untracked.splitlines():
            path = line.strip().replace("\\", "/")
            if not path:
                continue
            owner, _ = classify(path, subs, anchor)
            if owner in ("submodule", "project"):
                continue
            if any(path.startswith(d) for d in UPSTREAM_ONLY_DIRS):
                result[path] = "A"
    return result, skipped


def report_submodules(subs, changed_only):
    """显式报告被排除的 submodule，并说明它们该在哪里提交。

    这一步的存在理由是防止误判：改引擎后跑本脚本什么都没提示，
    容易被理解成「引擎属于上游、不需要处理」。实际是「不归本技能管」。
    """
    if not subs:
        return
    lines = []
    for sub in subs:
        dirty, _ = git("status", "--porcelain", "--", sub.rstrip("/"))
        if dirty and dirty.strip():
            lines.append(f"  - {sub.rstrip('/')}（有未提交改动，应在该仓库内提交）")
    if not lines:
        return
    print("以下目录是独立仓库（submodule），其改动不属于上游差异，不在本技能登记范围内：")
    for line in lines:
        print(line)
    print("  归属判定与提交方式见 rules/tool/codebuddy-engine-vcs/。")


def report(anchor, entries, diff, changed_only):
    """比对实际差异与登记条目，返回是否存在问题。

    changed_only=True 时不做「已失效」判定——工作区只包含部分改动，
    拿它去判定条目失效会误报。失效只在全量模式下有意义。
    """
    registered = {e["path"]: e for e in entries}
    problems = []

    print(f"校验基准：{anchor}")
    print(f"检查范围：{'工作区未提交改动' if changed_only else '锚点到 HEAD 全部差异'}\n")

    for path, status in sorted(diff.items()):
        if status == "A":
            problems.append(
                f"[告警] 新增文件落在上游专属目录，应按扩展优先原则移到 extensions/：{path}"
            )
            continue
        if path not in registered:
            problems.append(f"[未登记] {path}（{status}）—— 请补登记到差异清单")
        elif registered[path]["kind"] != status:
            problems.append(
                f"[不一致] {path} 实际状态 {status}，登记为 {registered[path]['kind']}"
            )

    if not changed_only:
        for path in registered:
            if path not in diff:
                problems.append(f"[已失效] {path} 已无差异，请删除该条目")

    if not problems:
        scope = f"{len(diff)} 个文件变更，{len(entries)} 条登记"
        print(f"通过：{scope}，全部一致。")
        return False

    for item in problems:
        print(item)
    print(f"\n共 {len(problems)} 项问题。")
    return True


def add_entry(divergence_path, target, subs, anchor):
    """生成登记条目骨架，由人工补 reason 后写回。

    先判定归属再决定是否生成：目标不属上游来源时拒绝，并说明它该去哪。
    不拒绝就会产出「已登记」的假象——条目写进 divergence.yaml 后，
    校验脚本会认为这个文件是上游差异，而它其实属于引擎仓库或项目自有目录。
    """
    path = target.replace("\\", "/")
    owner, is_upstream = classify(path, subs, anchor)

    if not is_upstream:
        hints = {
            "submodule": f"该路径位于独立仓库（submodule）内，改动应提交到该仓库，不登记上游差异",
            "project": "该路径是本项目自有目录（.codebuddy.project/），上游不存在，无需登记",
            "unknown": "该路径在上游锚点中不存在（本项目新增），新增不构成冲突源，无需登记",
        }
        print(f"[拒绝] {path} 不属于上游来源文件。")
        print(f"原因：{hints[owner]}。")
        if owner == "submodule":
            for sub in subs:
                if path == sub.rstrip("/") or path.startswith(sub):
                    print(f"请在 {sub.rstrip('/')} 内提交；归属判定见 rules/tool/codebuddy-engine-vcs/。")
                    break
        return 4

    out, err = git("diff", "--name-status", "HEAD", "--", path)
    if err:
        fail(f"git 命令失败：{err}", code=2)

    status = "M"
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0][0]
            break

    skeleton = f"""  - path: {path}
    kind: {status}
    reason: <填写为什么必须改这个上游文件>
    method: <patch-point | necessary | patch | doc>"""
    print(f"请将以下条目追加到 {divergence_path} 的 entries 列表：\n")
    print(skeleton)
    print("\n说明：reason 写动机（为什么不能不改），不要写「调整了一下」这类空话。")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="上游差异守护：盘点改动、校验登记、生成条目"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--changed", action="store_true", help="只检查当前工作区变更")
    group.add_argument("--add", metavar="路径", help="为指定路径生成登记条目骨架")
    args = parser.parse_args()

    anchor, entries = load_divergence(DIVERGENCE_PATH)
    verify_anchor(anchor)
    subs = submodule_paths()

    if args.add:
        return add_entry(DIVERGENCE_PATH, args.add, subs, anchor)

    report_submodules(subs, args.changed)
    diff, skipped = collect_diff(anchor, args.changed, subs)
    has_problem = report(anchor, entries, diff, args.changed)
    if skipped and not args.changed:
        print(f"\n已排除 {len(skipped)} 项非上游改动（submodule / 项目自有目录），不计入登记要求。")
    return 1 if has_problem else 0


if __name__ == "__main__":
    sys.exit(main())
