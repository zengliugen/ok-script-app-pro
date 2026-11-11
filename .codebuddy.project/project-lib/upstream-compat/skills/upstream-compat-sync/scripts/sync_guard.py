# -*- coding: utf-8 -*-
"""上游同步守卫：校验「master 是上游镜像、dev 承接项目改动」这一方向不变量。

为什么需要这个脚本
    ``git merge dev`` 与 ``git merge upstream/master`` 都是合法命令，一旦方向搞反，
    Git 不会报错，只会安静地把项目提交灌进本应纯净的 master。事后靠人看日志才发现，
    而此时可能已经推送出去。本脚本把方向约定变成可机械判定的断言。

判据（与 references/sync-invariants.md 一一对应）
    - master 必须是 upstream/master 的祖先或同一提交（即 master 不含任何项目提交）
    - dev 必须包含 master（同步完成后）
    - 同步完成后 divergence.yaml 的 synced_upstream_commit 必须等于 upstream/master

用法
    python sync_guard.py check --stage before    # 同步前：确认 master 纯净
    python sync_guard.py check --stage after     # 同步后：确认 dev 已含上游、基准已更新
    python sync_guard.py check --json            # 机器可读输出

退出码
    0 通过 / 1 违反不变量 / 3 环境或配置不可用
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# 配置缺失即报错，不内置默认值（与项目侧「配置是唯一事实来源」的约定一致）
DIVERGENCE_REL = Path(".codebuddy.project") / "configs" / "upstream" / "divergence.yaml"

UPSTREAM_REF = "upstream/master"
MIRROR_BRANCH = "master"
WORK_BRANCH = "dev"


def resolve_project_root(start):
    """自 start 向上查找含 `.codebuddy` 的目录，返回项目根。

    params:
        start: 定位起点（通常为脚本自身路径）。

    Returns:
        项目根的 Path。

    Raises:
        RuntimeError: 上溯到盘根仍未找到 `.codebuddy`，说明脚本被移出项目结构。

    """
    current = Path(start).resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / ".codebuddy").is_dir():
            return candidate
    raise RuntimeError("未能定位项目根：脚本不在含 .codebuddy 的目录树下（%s）" % start)


PROJECT_ROOT = resolve_project_root(__file__)


def run_git(args):
    """执行 git 命令并返回 (退出码, stdout, stderr)。

    params:
        args: 传给 git 的参数列表，如 ``["rev-parse", "master"]``。

    Returns:
        三元组 (退出码, 标准输出, 标准错误)，输出已去除首尾空白。

    """
    proc = subprocess.run(
        ["git"] + args,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def rev_parse(ref):
    """取某个 ref 的完整提交 sha，取不到时返回 None。

    params:
        ref: 分支名或 git 引用。

    Returns:
        40 位提交 sha；ref 不存在时返回 None。

    """
    code, out, _ = run_git(["rev-parse", "--verify", ref])
    return out if code == 0 else None


def is_ancestor(ancestor, descendant):
    """判断 ancestor 是否为 descendant 的祖先（含同一提交）。

    params:
        ancestor: 祖先候选的 ref 或 sha。
        descendant: 后代候选的 ref 或 sha。

    Returns:
        True 表示 ancestor 是 descendant 的祖先或两者相同。

    """
    code, _, _ = run_git(["merge-base", "--is-ancestor", ancestor, descendant])
    return code == 0


def read_synced_commit():
    """从 divergence.yaml 读取同步基准，不依赖 PyYAML。

    params:
        无。

    Returns:
        基准 commit 字符串。

    Raises:
        RuntimeError: 配置文件缺失或没有 synced_upstream_commit 字段。

    """
    path = PROJECT_ROOT / DIVERGENCE_REL
    if not path.is_file():
        raise RuntimeError("配置缺失：%s 不存在" % path)
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("synced_upstream_commit:"):
            value = stripped.split(":", 1)[1].strip()
            if not value:
                raise RuntimeError("配置错误：%s 的 synced_upstream_commit 为空" % path)
            return value
    raise RuntimeError("配置错误：%s 缺少 synced_upstream_commit 字段" % path)


def check_upstream_tag_isolation(problems):
    """校验 upstream 远端已隔离 tag，防止上游 tag 污染本地版本号。

    背景：本仓库 fork 自上游，上游用 ``v0.0.1`` ~ ``v1.1.3`` 这套号段，
    与本项目自有 tag 重名。上游 tag 一旦进入本地，``next_tag.py`` 会把上游的
    ``v1.1.3`` 认成「最新稳定版」，发布号随之跳到 ``v1.1.4-*`` 而非 ``v0.0.2-*``。
    更糟的是同名 tag 会让 ``git fetch`` 报 ``would clobber existing tag``。

    三重隔离缺一不可，逐项检查：

    1. ``remote.upstream.tagOpt=--no-tags``——关掉默认的 tag 自动跟随；
    2. ``remote.upstream.fetch`` 含 ``^refs/tags/*`` 负向 refspec——**唯一能挡住显式
       ``--tags`` 的手段**，因为命令行 ``--tags`` 的优先级高于 tagOpt 配置；
    3. ``push.followTags=false``——禁止普通 push 顺带推 annotated tag。

    只报问题不自动修复：这套配置只在本地生效，修不修、怎么修由人决定。

    params:
        problems: 收集问题描述的列表，就地追加。

    Returns:
        无。问题写入 problems。

    """
    _, tagopt, _ = run_git(["config", "--get", "remote.upstream.tagOpt"])
    if tagopt != "--no-tags":
        problems.append(
            "upstream 未设 remote.upstream.tagOpt=--no-tags（当前：%s）。"
            "上游 tag 会污染本地版本号，执行：git config remote.upstream.tagOpt --no-tags" % (tagopt or "未设置")
        )

    _, fetch_specs, _ = run_git(["config", "--get-all", "remote.upstream.fetch"])
    specs = [line.strip() for line in fetch_specs.splitlines() if line.strip()]
    if "^refs/tags/*" not in specs:
        problems.append(
            "upstream 的 fetch refspec 缺少负向规则 ^refs/tags/*（当前：%s）。"
            "缺它时显式 git fetch upstream --tags 仍会拉入上游 tag——tagOpt 挡不住命令行参数。\n"
            "  git config --unset-all remote.upstream.fetch\n"
            "  git config --add remote.upstream.fetch '+refs/heads/*:refs/remotes/upstream/*'\n"
            "  git config --add remote.upstream.fetch '^refs/tags/*'" % (" | ".join(specs) or "未设置")
        )

    _, follow, _ = run_git(["config", "--get", "push.followTags"])
    if follow not in ("false", ""):
        problems.append(
            "push.followTags 不为 false（当前：%s），push 时会顺带推 annotated tag，"
            "可能把上游 tag 或半成品 tag 一次推上 origin。执行：git config push.followTags false" % follow
        )


def check_master_is_mirror(problems):
    """校验 master 不含任何项目提交。

    判据：master 必须是 upstream/master 的祖先或同一提交。反向判断（upstream 是 master
    的祖先且两者不等）说明 master 上多了提交。

    params:
        problems: 收集问题描述的列表，就地追加。

    Returns:
        无。问题写入 problems。

    """
    upstream_sha = rev_parse(UPSTREAM_REF)
    master_sha = rev_parse(MIRROR_BRANCH)
    if not upstream_sha:
        problems.append("取不到 %s，先执行 git fetch upstream" % UPSTREAM_REF)
        return
    if not master_sha:
        problems.append("取不到 %s 分支" % MIRROR_BRANCH)
        return

    if is_ancestor(MIRROR_BRANCH, UPSTREAM_REF):
        return
    # master 不是上游祖先 = master 上有上游没有的提交
    _, ahead_out, _ = run_git(["rev-list", "--count", "%s..%s" % (UPSTREAM_REF, MIRROR_BRANCH)])
    detail = ""
    if ahead_out.isdigit() and int(ahead_out) > 0:
        detail = "（master 比上游多 %s 个提交）" % ahead_out
    problems.append(
        "%s 不是 %s 的祖先：master 混入了项目提交%s。"
        "上游同步的方向是 master 前移后同步到 dev，禁止把 dev 合并进 master" % (MIRROR_BRANCH, UPSTREAM_REF, detail)
    )


def check_work_branch_contains_master(problems):
    """校验 dev 已包含 master。

    params:
        problems: 收集问题描述的列表，就地追加。

    Returns:
        无。问题写入 problems。

    """
    if not rev_parse(MIRROR_BRANCH):
        problems.append("取不到 %s 分支" % MIRROR_BRANCH)
        return
    if not rev_parse(WORK_BRANCH):
        problems.append("取不到 %s 分支" % WORK_BRANCH)
        return
    if not is_ancestor(MIRROR_BRANCH, WORK_BRANCH):
        _, behind_out, _ = run_git(["rev-list", "--count", "%s..%s" % (WORK_BRANCH, MIRROR_BRANCH)])
        detail = ""
        if behind_out.isdigit() and int(behind_out) > 0:
            detail = "（dev 落后 master %s 个提交）" % behind_out
        problems.append(
            "%s 未包含 %s%s。同步未完成：需要在 dev 上执行 git merge master" % (WORK_BRANCH, MIRROR_BRANCH, detail)
        )


def check_divergence_baseline(problems):
    """校验 divergence.yaml 的同步基准已指向 upstream/master。

    params:
        problems: 收集问题描述的列表，就地追加。

    Returns:
        无。问题写入 problems。

    """
    upstream_sha = rev_parse(UPSTREAM_REF)
    if not upstream_sha:
        problems.append("取不到 %s，无法校验同步基准" % UPSTREAM_REF)
        return
    try:
        synced = read_synced_commit()
    except RuntimeError as exc:
        problems.append(str(exc))
        return

    if not synced.startswith(upstream_sha) and not upstream_sha.startswith(synced):
        problems.append(
            "divergence.yaml 的 synced_upstream_commit（%s…）未指向 %s（%s…）。"
            "同步完成后必须更新该基准，否则下游差异校验会以旧锚点比对" % (synced[:12], UPSTREAM_REF, upstream_sha[:12])
        )


def check_stage_before(problems):
    """同步前的检查项：tag 隔离配置、master 必须纯净、dev 必须未落后于 master。"""
    check_upstream_tag_isolation(problems)
    check_master_is_mirror(problems)
    check_work_branch_contains_master(problems)


def check_stage_after(problems):
    """同步后的检查项：tag 隔离配置、dev 必须含上游、基准必须已更新、master 仍须纯净。"""
    check_upstream_tag_isolation(problems)
    check_master_is_mirror(problems)
    check_work_branch_contains_master(problems)
    check_divergence_baseline(problems)


def main(argv=None):
    """命令行入口。

    params:
        argv: 参数列表；None 表示取 sys.argv[1:]。

    Returns:
        进程退出码。

    """
    parser = argparse.ArgumentParser(description="校验上游同步的方向不变量（master 为上游镜像、dev 承接项目改动）")
    parser.add_argument("command", choices=["check"], help="check：执行校验")
    parser.add_argument(
        "--stage",
        choices=["before", "after"],
        default="after",
        help="before：同步前（只查 master 纯净）；after：同步后（另查 dev 与基准）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    problems = []
    try:
        if args.stage == "before":
            check_stage_before(problems)
        else:
            check_stage_after(problems)
    except RuntimeError as exc:
        payload = {"ok": False, "stage": args.stage, "problems": [str(exc)], "error": "environment"}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("[不可用] %s" % exc)
        return 3

    if args.json:
        print(
            json.dumps(
                {"ok": not problems, "stage": args.stage, "problems": problems},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if problems:
            print("[违反] 同步方向不变量未满足（阶段：%s）" % args.stage)
            for item in problems:
                print("  - %s" % item)
        else:
            print("[通过] 同步方向不变量满足（阶段：%s）" % args.stage)

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
