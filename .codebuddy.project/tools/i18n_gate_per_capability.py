# -*- coding: utf-8 -*-
"""按能力包逐个校验扩展翻译一致性。

**为什么需要这个包装**：上游门禁脚本 `task_i18n_helper.py gate` 的语义是
「扫描给定代码路径的全部界面文案，要求**每个** locale 的 catalog 都覆盖它们」，
而它把 `--i18n` 根目录下**所有** po 按 locale 名合并成一份 catalog
（`cmd_check_missing` 里的 `catalogs.setdefault(locale, set()).update(...)`）。

只有一个能力包时，这两种语义恰好等价；一旦有第二个能力包，就会变成
「A 包的 catalog 缺少 B 包的文案」——而本项目的能力包按**能力隔离**，各包自带
独立 catalog，本来就不该互相覆盖。

因此这里按 `extensions/capabilities/<能力>/` 逐个调用门禁，各自比对：
每个能力包只需覆盖**自己**的文案。任一包失败则整体退出 1。

用法：

```bash
python .codebuddy.project/tools/i18n_gate_per_capability.py
```
"""

import os
import subprocess
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CAPABILITIES_DIR = os.path.join(BASE, "extensions", "capabilities")
GATE_SCRIPT = os.path.join(
    BASE,
    ".codebuddy.project",
    "project-lib",
    "ok-script",
    "skills",
    "ok-script-i18n",
    "scripts",
    "task_i18n_helper.py",
)
PYTHON = os.path.join(BASE, ".codebuddy.project", "envs", "python", "Scripts", "python.exe")


def capability_packages():
    """列出带 i18n 目录的能力包。

    Returns：
        能力包名列表（按名称排序）。
    """
    if not os.path.isdir(CAPABILITIES_DIR):
        return []
    found = []
    for name in sorted(os.listdir(CAPABILITIES_DIR)):
        package = os.path.join(CAPABILITIES_DIR, name)
        if not os.path.isdir(package):
            continue
        if os.path.isdir(os.path.join(package, "i18n")):
            found.append(name)
    return found


def main():
    """逐个能力包跑门禁。

    Returns：
        全部通过时为 0，任一失败为 1；门禁环境不可用为 3。
    """
    packages = capability_packages()
    if not packages:
        print("没有任何带 i18n 目录的能力包，跳过")
        return 0

    interpreter = PYTHON if os.path.isfile(PYTHON) else sys.executable
    failed = []
    for name in packages:
        package = os.path.join(CAPABILITIES_DIR, name)
        command = [
            interpreter,
            GATE_SCRIPT,
            "gate",
            "--i18n",
            os.path.join(package, "i18n"),
            "--path",
            package,
        ]
        print(f"== 能力包 {name} ==")
        completed = subprocess.run(command, cwd=BASE, check=False)
        if completed.returncode == 3:
            print(f"能力包 {name} 门禁环境不可用（退出码 3）", file=sys.stderr)
            return 3
        if completed.returncode != 0:
            failed.append(name)
        print("")

    if failed:
        print(
            "翻译门禁未通过：" + "、".join(failed),
            file=sys.stderr,
        )
        return 1
    print(f"全部通过（{len(packages)} 个能力包）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
