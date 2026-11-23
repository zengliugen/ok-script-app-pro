"""新靶场的启动器：用项目虚拟环境启动，避免落到没有 Tk 的裸解释器上。

直接 `python extensions_testbed/main.py` 也能跑，但很容易用到系统解释器
（缺 Tk 或版本不对），排查方向会被带偏。本启动器固定用仓库内的 `.venv`，
找不到时明确报错而不是静默回退。

用法（在项目根目录）：``python run_testbed.py [--screen ID] [--lang en]``
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
ENTRY = PROJECT_ROOT / "extensions_testbed" / "main.py"


def main(argv: list[str] | None = None) -> int:
    """启动靶场。

    Args:
        argv: 追加给靶场的参数；None 表示取 `sys.argv[1:]`。

    Returns:
        进程退出码。找不到解释器或入口时返回 2。

    Note:
        用 `pythonw.exe` 而非 `python.exe`：靶场跑起来后控制台窗口没有用处，
        留着只会挡住别的窗口。需要看输出时改用 `python.exe` 手动启动。
    """
    args = sys.argv[1:] if argv is None else argv
    if not VENV_PYTHON.exists():
        print(f"未找到项目虚拟环境的解释器：{VENV_PYTHON}", file=sys.stderr)
        print("请先按 README 创建 .venv（缺少它时靶场无法启动）", file=sys.stderr)
        return 2
    if not ENTRY.exists():
        print(f"未找到靶场入口：{ENTRY}", file=sys.stderr)
        return 2
    return subprocess.call([str(VENV_PYTHON), str(ENTRY), *args], cwd=str(PROJECT_ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
