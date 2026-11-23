# -*- coding: utf-8 -*-
"""核对 requirements.txt 与 pyproject.toml 的直接依赖是否对齐。

**为什么需要这个脚本**：`requirements.txt` 是 piptools 从 `pyproject.toml` 编出的
锁定文件，正常流程必须重新编译、不得手改。但编译需要联网解析完整依赖树，在受限
网络下会长时间无输出而超时（实测 5 分钟仍未开始输出）。

此时唯一可行的兜底是「按 piptools 的输出格式手工补一条」。手工补就有补错的风险：
漏了某个直接依赖、版本与实际安装不一致、或格式与生成器不同导致下次编译大改。
本脚本把这三件事变成可机械判定——它不能替代编译，但能证明「当前锁定文件至少与
声明一致且可用」。

用法：

```bash
.venv/Scripts/python.exe .codebuddy.project/tools/check_requirements_sync.py
```

退出码：0 一致；1 存在不一致；3 环境不可用（缺 tomllib / 文件缺失）。
"""

import importlib.metadata as md
import os
import re
import sys
import tomllib

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PYPROJECT = os.path.join(BASE, "pyproject.toml")
REQUIREMENTS = os.path.join(BASE, "requirements.txt")

# 锁定文件里的一行依赖：`name==version`，可带 extras。
_LINE_PATTERN = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;]+)")


def required_names():
    """收集 pyproject 里声明的直接依赖（含 qt extra）。

    Returns：
        `[(name, spec)]` 列表，name 已小写。
    """
    with open(PYPROJECT, "rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project", {})
    specs = list(project.get("dependencies", []))
    specs.extend(project.get("optional-dependencies", {}).get("qt", []))
    found = []
    for spec in specs:
        matched = re.match(r"^([A-Za-z0-9_.\-]+)", spec)
        if matched:
            found.append((matched.group(1).lower(), spec))
    return found


def locked_versions():
    """解析锁定文件里的固定版本。

    Returns：
        `{name: version}` 字典，name 已小写。
    """
    versions = {}
    with open(REQUIREMENTS, encoding="utf-8") as handle:
        for line in handle:
            matched = _LINE_PATTERN.match(line.strip())
            if matched:
                versions[matched.group(1).lower()] = matched.group(2)
    return versions


def main():
    """执行核对。

    Returns：
        0 一致；1 不一致；3 环境不可用。
    """
    for path in (PYPROJECT, REQUIREMENTS):
        if not os.path.isfile(path):
            print(f"环境不可用：缺少 {path}", file=sys.stderr)
            return 3

    required = required_names()
    locked = locked_versions()
    problems = []

    for name, spec in required:
        if name not in locked:
            problems.append(f"锁定文件缺少直接依赖 {name}（pyproject 声明 {spec!r}）")
            continue
        try:
            installed = md.version(name)
        except md.PackageNotFoundError:
            problems.append(f"{name} 已锁定为 {locked[name]}，但当前环境未安装")
            continue
        if installed != locked[name]:
            problems.append(
                f"{name} 版本不一致：锁定 {locked[name]}，实装 {installed}"
            )
        else:
            print(f"[OK] {name}=={installed}")

    # 版本格式：piptools 一律输出 `==`，出现其它比较符说明被手工改过。
    for line in open(REQUIREMENTS, encoding="utf-8"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "==" in stripped or stripped.startswith(("    #", "-")):
            continue
        if re.match(r"^[A-Za-z0-9_.\-]+(?:\[[^\]]*\])?(?:[<>~!]=|[<>])", stripped):
            problems.append(f"锁定文件出现非 `==` 约束：{stripped!r}")

    if problems:
        print("", file=sys.stderr)
        for item in problems:
            print(f"[不一致] {item}", file=sys.stderr)
        return 1

    print(f"\n一致：{len(required)} 个直接依赖均已锁定且与实装版本相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
