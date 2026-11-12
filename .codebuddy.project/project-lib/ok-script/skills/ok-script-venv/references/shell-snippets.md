# Shell 片段与常见陷阱（ok-script-venv）

## 一、解释器解析

### PowerShell（通用写法）

```powershell
$Python = if (Test-Path ".\.venv\Scripts\python.exe") {
  ".\.venv\Scripts\python.exe"
} elseif (Test-Path ".\.venv\bin\python") {
  ".\.venv\bin\python"
} else {
  "python"
}
```

### PowerShell（单条命令）

```powershell
.\.venv\Scripts\python.exe -m unittest tests.TestMain
```

### POSIX

```bash
if [ -x "./.venv/bin/python" ]; then
  PYTHON="./.venv/bin/python"
elif [ -x "./.venv/Scripts/python.exe" ]; then
  PYTHON="./.venv/Scripts/python.exe"
else
  PYTHON="python"
fi
```

## 二、常见工具的完整写法

| 场景 | PowerShell | POSIX |
|---|---|---|
| 跑单个测试文件 | `.\.venv\Scripts\python.exe -m unittest tests.TestMain` | `$PYTHON -m unittest tests.TestMain` |
| 跑测试脚本 | `.\.venv\Scripts\python.exe -u run_test.py tests\TestMain.py` | `$PYTHON -u run_test.py tests/TestMain.py` |
| 装依赖 | `.\.venv\Scripts\python.exe -m pip install --no-deps -r requirements.txt` | `$PYTHON -m pip install --no-deps -r requirements.txt` |
| 静态检查 | `.\.venv\Scripts\python.exe -m ruff check src` | `$PYTHON -m ruff check src` |
| 构建文档 | `.\.venv\Scripts\python.exe -m mkdocs build --strict` | `$PYTHON -m mkdocs build --strict` |
| 编译检查 | `.\.venv\Scripts\python.exe -m compileall src` | `$PYTHON -m compileall src` |

**统一用 `-m <模块>` 而不是直接调可执行文件**（如 `ruff`、`mkdocs`）。
理由：`.venv\Scripts\` 下的 `.exe` 入口依赖 `PATH` 里有对应目录，而 `-m` 由解释器自己解析模块，
不依赖 `PATH`，在任意工作目录下都一致。

## 三、路径基准陷阱

**相对路径的基准是当前工作目录，不是脚本所在目录。**

```powershell
# 从仓库根执行：正确
.\.venv\Scripts\python.exe -m unittest tests.TestMain

# 从 src 目录执行：找不到 .venv
cd src
..\.venv\Scripts\python.exe -m unittest tests.TestMain   # 路径要相应调整
```

在 PowerShell 脚本里最稳妥的写法是用脚本自身位置推导：

```powershell
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
```

本项目的 `run_tests.ps1` 就用了 `$PSScriptRoot`，因此它可以从任意目录被调用。

## 四、CI 与本地差异

| 环境 | 有没有 `.venv` | 正确做法 |
|---|---|---|
| 本地开发 | 有（手工创建） | 直接调用 `.venv` 里的解释器 |
| GitHub Actions | **没有**（用 `actions/setup-python` 装到 PATH） | 回退到 `python`，并**显式说明**这次回退 |

这就是回退分支必须存在的原因。反过来，本地脚本不要硬编码 `python`，
否则会用到没装 PySide6 的系统解释器，测试以 `ImportError` 失败——
这个失败看起来像「代码坏了」，实际是「用错解释器」。

## 五、`activate` 为什么不够

| 方式 | 生效范围 | 失效场景 |
|---|---|---|
| `activate` 后跑 `python` | 仅当前交互式 shell | 子进程、IDE 运行配置、CI 步骤、`pwsh -File script.ps1` |
| 直接调用解释器路径 | 任何上下文 | 只受工作目录影响 |

`activate` 改的是当前 shell 的 `PATH` 环境变量，进程一旦脱离这个 shell（或 shell 被
`-File` / `-Command` 方式调用），修改就不会传递。

## 六、何时该换用 python-env

出现下列任一情况时，本技能的两级判断不够用，应改用 `python-env`：

| 情况 | 为什么 |
|---|---|
| 要写进 CI 配置或自动化脚本 | 需要绝对可靠，不能因环境变化静默用错 |
| 跑测试或覆盖率 | 用错解释器会导致用例全部跳过，而 `unittest` 仍返回 0 |
| 检查报「环境不可用」（退出码 3） | 需要先 `python-env ensure` 建环境 |
| 需要机器可读的解释器路径 | `python-env python` 直接输出绝对路径 |

`python-env` 的探测会**每一级都校验目标解释器能 `import ok`**，这是本技能刻意不做的
——它只为日常手敲命令服务，做那么重的校验会明显拖慢。
