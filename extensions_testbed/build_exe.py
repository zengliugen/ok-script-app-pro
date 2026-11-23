# -*- coding: utf-8 -*-
"""把测试靶场打包成单文件 EXE。

由 `run.cmd` 的「测试靶场（EXE）」入口调用：**每次启动前先校验 EXE 是否与源码
一致**（`--check`），过期或缺产物时重新打包，打包完成后启动。产物在
`dist/extensions_testbed.exe`（根 `.gitignore` 已忽略 `dist/`），中间文件在
`build/pyinstaller/`。

**为什么需要「过期校验」**：EXE 只在缺失时打包的话，改完源码再走菜单会启动
上一版程序——新代码不生效，而界面上没有任何提示，排查时极易误判成「修复没生
效」（已实际发生过：22:36 启动的进程跑的是 22:47 才提交的修复）。

**为什么用内容指纹而不是修改时间**：`git checkout` / `git merge` 会把文件
mtime 全部刷成当前时间，按时间比会把没改过的源码判成「更新」；反过来从备份
恢复旧文件时 mtime 变新但内容更旧。指纹按文件内容计算，只认「源码真的变了」。

**为什么用单文件**：靶场要能单独拷走运行，不依赖仓库与虚拟环境；Tkinter
与标准库都会被 PyInstaller 收进同一个 EXE。代价是首次启动要解压，约慢
一两秒。

**为什么把打包逻辑放 Python 而不是 CMD**：参数里有大量路径与引号，
CMD 的括号块与延迟展开容易把参数拆错；Python 只负责拼命令行，
run.cmd 只管「有没有、要不要打包、打包后启动」。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
ENTRY = PACKAGE_DIR / "main.py"
DIST_DIR = ROOT / "dist"
WORK_DIR = ROOT / "build" / "pyinstaller"
EXE_NAME = "extensions_testbed"
EXE_PATH = DIST_DIR / f"{EXE_NAME}.exe"
# 构建指纹放在产物旁边：EXE 被单独拷走时指纹随目录一起走，不会误判。
STAMP_PATH = DIST_DIR / f"{EXE_NAME}.build-stamp.json"

# `--check` 的退出码。`run.cmd` 依赖这三个值的**大小顺序**（`if errorlevel N`
# 是「≥ N」），不能随意调整。
CHECK_OK = 0
CHECK_STALE = 1
CHECK_MISSING = 2


def _ensure_pyinstaller():
    """确保当前解释器能使用 PyInstaller。

    Returns:
        True 表示可用；False 表示安装失败。
    """
    try:
        import PyInstaller  # noqa: F401 — 只检查可导入性
    except ModuleNotFoundError:
        print("未安装 PyInstaller，正在安装打包工具……")
        result = subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"])
        if result.returncode != 0:
            print("PyInstaller 安装失败。请手动执行：")
            print(f"  {sys.executable} -m pip install pyinstaller")
            return False
    return True


def source_hashes(package_dir=None):
    """计算靶场源码的内容指纹。

    Args:
        package_dir: 源码目录；省略时用本文件所在目录。

    Returns:
        `{相对路径: sha256}` 字典，按路径排序。只收 `.py` 文件：靶场没有图片、
        配置等外部资源，界面元素全在代码里定义。

    Note:
        路径用 POSIX 分隔符写进指纹：Windows 的反斜杠与 Linux 的斜杠不同，
        换平台（如 WSL 里校验）会把同一份源码算成不同指纹。
    """
    base = Path(package_dir) if package_dir is not None else PACKAGE_DIR
    hashes = {}
    for path in sorted(base.glob("*.py")):
        if path.name.startswith("."):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[path.name.replace("\\", "/")] = digest
    return hashes


def fingerprint(hashes):
    """把逐文件指纹合成一个总指纹。

    Args:
        hashes: `source_hashes` 的返回值。

    Returns:
        十六进制 sha256。
    """
    joined = "\n".join(f"{name}\0{digest}" for name, digest in sorted(hashes.items()))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def read_stamp(stamp_path=None):
    """读取构建指纹文件。

    Args:
        stamp_path: 指纹文件路径；省略时用默认位置。

    Returns:
        `{"fingerprint": str, "files": {名: sha256}, "built_at": str}`；
        文件缺失或损坏时返回 None（按「过期」处理，不静默当成最新）。
    """
    path = Path(stamp_path) if stamp_path is not None else STAMP_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    return data


def write_stamp(package_dir=None, stamp_path=None, built_at=""):
    """写入当前源码的构建指纹。

    Args:
        package_dir: 源码目录；省略时用本文件所在目录。
        stamp_path: 指纹文件路径；省略时用默认位置。
        built_at: 打包完成的时间文本，仅供人查看。

    Returns:
        写入的指纹字典。
    """
    hashes = source_hashes(package_dir)
    payload = {
        "fingerprint": fingerprint(hashes),
        "files": hashes,
        "built_at": built_at,
    }
    path = Path(stamp_path) if stamp_path is not None else STAMP_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def changed_files(previous, current):
    """列出两次指纹之间新增 / 修改 / 删除的源文件名。

    Args:
        previous: 旧指纹里的 `files` 字典。
        current: 新算出的 `{名: sha256}`。

    Returns:
        排序后的文件名字符串列表；名字带前缀标记（`+` 新增、`-` 删除、`*` 修改）。
    """
    names = []
    for name in sorted(set(previous) | set(current)):
        if name not in previous:
            names.append(f"+{name}")
        elif name not in current:
            names.append(f"-{name}")
        elif previous[name] != current[name]:
            names.append(f"*{name}")
    return names


def check(exe_path=None, stamp_path=None, package_dir=None):
    """校验 EXE 是否与当前源码一致。

    Args:
        exe_path: EXE 路径；省略时用默认产物路径。
        stamp_path: 指纹文件路径；省略时用默认位置。
        package_dir: 源码目录；省略时用本文件所在目录。

    Returns:
        `(退出码, 说明)` 二元组。退出码语义见 `CHECK_*` 常量。
    """
    exe = Path(exe_path) if exe_path is not None else EXE_PATH
    if not exe.exists():
        return CHECK_MISSING, f"EXE 不存在：{exe}"
    stamp = read_stamp(stamp_path)
    if stamp is None:
        return CHECK_STALE, "缺少构建指纹（EXE 由旧版打包或指纹被删），需要重新打包"
    current = source_hashes(package_dir)
    if stamp.get("fingerprint") != fingerprint(current):
        names = changed_files(stamp.get("files") or {}, current)
        detail = "、".join(names[:6]) + ("…" if len(names) > 6 else "")
        return CHECK_STALE, f"源码自上次打包后有改动（{detail}），需要重新打包"
    built_at = stamp.get("built_at") or "未知时间"
    return CHECK_OK, f"EXE 与源码一致（打包于 {built_at}）"


def _remove_stamp(stamp_path=None):
    """删除旧指纹。

    Args:
        stamp_path: 指纹文件路径；省略时用默认位置。

    Note:
        打包开始时先删：若打包中途失败，产物可能已被 PyInstaller 删掉或写坏，
        留着旧指纹会让下一次校验误判成「一致」。
    """
    path = Path(stamp_path) if stamp_path is not None else STAMP_PATH
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def build():
    """执行打包。

    Returns:
        进程退出码：0 成功，非 0 失败。
    """
    if not _ensure_pyinstaller():
        return 3
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    _remove_stamp()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        EXE_NAME,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(WORK_DIR),
        "--paths",
        str(ROOT),
        str(ENTRY),
    ]
    print("开始打包测试靶场：")
    print("  " + " ".join(command))
    result = subprocess.run(command)
    if result.returncode != 0:
        print("打包失败，请检查上面的 PyInstaller 输出。")
        print("提示：若测试靶场正在运行，请先关闭它再重试（EXE 被占用时无法覆盖）。")
        return result.returncode
    exe_path = DIST_DIR / f"{EXE_NAME}.exe"
    if not exe_path.exists():
        print(f"打包命令返回成功，但没有找到产物：{exe_path}")
        return 4
    from datetime import datetime

    stamp = write_stamp(built_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(f"打包完成：{exe_path}")
    print(f"源码指纹：{stamp['fingerprint'][:12]}（{len(stamp['files'])} 个文件）")
    return 0


def main(argv=None):
    """命令行入口。

    Args:
        argv: 参数列表；省略时取 `sys.argv[1:]`。

    Returns:
        进程退出码。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--check" in args:
        code, message = check()
        print(message)
        return code
    return build()


if __name__ == "__main__":
    sys.exit(main())
