# -*- coding: utf-8 -*-
"""上游差异守护：盘点本项目相对上游的改动、校验登记完整性、生成登记条目。

用法（在项目根目录执行）：
    python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py
    python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py --changed
    python .codebuddy.project/skills/project/upstream-guard/scripts/upstream_guard.py --add src/config.py

配置来源：
    .codebuddy/project.index.json 的 files["upstream.divergence"] / files["upstream.capabilities"]
    路径缺失或索引损坏时直接报错退出（退出码 3），不回退内置默认值。

退出码：
    0 = 校验通过
    1 = 存在未登记或已失效的改动
    2 = git 操作失败（上游锚点不存在等）
    3 = 配置缺失或损坏
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[5]
INDEX_PATH = PROJECT_ROOT / ".codebuddy" / "project.index.json"

# 上游专属的**代码**目录：新增项目代码落在这些目录会告警（应写 src/extensions/）。
# 注意 docs/ 与 tests/ 不在此列——两处均允许新增文件：
#   docs/  —— 新增文档页不构成冲突源（nav 条目改动才需登记）
#   tests/ —— 新增 Test*.py 是扩展测试的正常落点，且需被 run_tests.ps1 捡到
UPSTREAM_ONLY_DIRS = (
    "src/tasks/",
    "src/ui/",
    "src/globals.py",
)


def fail(message, code=3):
    """统一错误出口：明确报错并退出，不静默降级。"""
    print(f"[错误] {message}", file=sys.stderr)
    sys.exit(code)


def load_index():
    """读取配置索引，返回差异清单与能力清单的绝对路径。"""
    if not INDEX_PATH.is_file():
        fail(f"配置索引不存在：{INDEX_PATH}")
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"配置索引 JSON 损坏：{exc}")

    files = data.get("files")
    if not isinstance(files, dict):
        fail("配置索引缺少 files 段")

    paths = {}
    for logical_name in ("upstream.divergence", "upstream.capabilities"):
        entry = files.get(logical_name)
        if not isinstance(entry, dict) or not entry.get("path"):
            fail(f"配置索引缺少逻辑名 {logical_name} 的 path 登记")
        paths[logical_name] = PROJECT_ROOT / entry["path"]
    return paths


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


def collect_diff(anchor, changed_only):
    """返回 {路径: 状态} 的上游文件改动表。

    changed_only=True 时只看工作区与暂存区（尚未提交的改动），
    False 时看锚点到 HEAD 的全部差异。
    """
    if changed_only:
        out, err = git("diff", "--name-status", "HEAD")
    else:
        out, err = git("diff", "--name-status", f"{anchor}..HEAD")
    if err:
        fail(f"git diff 执行失败：{err}", code=2)

    result = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        path = path.replace("\\", "/")
        raw_status = "M" if status.startswith("R") else status[0]
        # 新增文件在上游不存在，不构成冲突源，但仍需识别是否落入上游专属目录
        if raw_status == "A":
            if any(path.startswith(d) for d in UPSTREAM_ONLY_DIRS):
                result[path] = "A"
            continue
        # 修改与删除只保留真正属于上游的文件
        if is_upstream_file(path, anchor):
            result[path] = raw_status

    if changed_only:
        # git diff HEAD 不含未跟踪文件，需单独取，否则新建文件落在上游目录时漏检
        untracked, err = git("ls-files", "--others", "--exclude-standard")
        if err:
            fail(f"git ls-files 执行失败：{err}", code=2)
        for line in untracked.splitlines():
            path = line.strip().replace("\\", "/")
            if path and any(path.startswith(d) for d in UPSTREAM_ONLY_DIRS):
                result[path] = "A"
    return result


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
            # 新增文件不构成冲突源；但落在上游专属目录时告警
            problems.append(
                f"[告警] 新增文件落在上游专属目录，应按扩展优先原则移到 src/extensions/：{path}"
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


def add_entry(divergence_path, target):
    """生成登记条目骨架，由人工补 reason 后写回。"""
    path = target.replace("\\", "/")
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

    paths = load_index()
    anchor, entries = load_divergence(paths["upstream.divergence"])
    verify_anchor(anchor)

    if args.add:
        return add_entry(paths["upstream.divergence"], args.add)

    diff = collect_diff(anchor, args.changed)
    return 1 if report(anchor, entries, diff, args.changed) else 0


if __name__ == "__main__":
    sys.exit(main())
