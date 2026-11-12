---
name: ok-script-venv
description: 在 ok-script 项目里跑 Python 命令时优先使用仓库本地虚拟环境。用户说"用哪个 python"、"跑测试用哪个解释器"、"要不要先 activate"、"venv 怎么调"、"PowerShell 里怎么跑 python"、"命令在 CI 上能跑本地不行"时使用。给出解释器解析写法（PowerShell 与 POSIX 两种 shell）与回退顺序。不适用于：创建或修复工具环境（用 python-env 的 ensure）、探测应用解释器并校验能 import ok（用 python-env 的 python 子命令）、判定测试是否真的跑过（用 python-test-coverage）。
---

# ok-script 项目里的本地虚拟环境

## 定位

本技能管的是**约定**：在 ok-script 项目里敲 Python 命令时，用哪个解释器、怎么写才不踩坑。

它不管**机制**：环境怎么创建、依赖怎么锁定、解释器怎么探测与校验，全在 `python-env` 技能里。两者的分工见下文「与 python-env 的分工」。

| 维度 | 定义 |
|------|------|
| 拥有的任务 | 给出解释器解析写法、说明回退顺序、指出常见误用 |
| 输出 | 可直接粘贴的 PowerShell / POSIX 命令片段，或对被问命令的解释器改写 |
| 排除 | 不创建虚拟环境（`python-env ensure`）；不做四级探测与 `import ok` 校验（`python-env` 的 `python` 子命令）；不跑检查（`python-lint` / `python-test-coverage`） |
| 前提 | 仓库根可能存在 `.venv`，也可能没有（CI 环境通常没有） |

## 核心规则

1. **跑 Python 之前，先看仓库根有没有 `.venv`。**
2. **优先直接调用解释器的绝对/相对路径**，而不是依赖 `activate`。
   理由：`activate` 改的是当前 shell 的 `PATH`，脚本、子进程、IDE 任务、CI 步骤都不会继承；
   而直接调用路径在任何上下文里结果一致。
3. **只有仓库里确实没有 `.venv` 时，才回退到全局 `python`。**
   回退必须**显式说明**——静默回退到系统解释器，往往因为缺依赖而以 `ImportError` 失败，
   看起来像「代码坏了」，实际是「用错了环境」。

## PowerShell

把解释器解析一次，之后用调用运算符 `&` 执行：

```powershell
$Python = if (Test-Path ".\.venv\Scripts\python.exe") {
  ".\.venv\Scripts\python.exe"
} elseif (Test-Path ".\.venv\bin\python") {
  ".\.venv\bin\python"
} else {
  "python"
}

& $Python -m unittest tests.TestMain
& $Python -m ruff check src
```

单条命令的场景，直接写路径通常就够：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.TestMain
```

**注意** `Scripts\python.exe`（Windows）与 `bin/python`（POSIX）的差异：
同一个 `.venv` 在不同平台上的布局不同，跨平台的脚本要两个都判断。

## POSIX Shell

```bash
if [ -x "./.venv/bin/python" ]; then
  PYTHON="./.venv/bin/python"
elif [ -x "./.venv/Scripts/python.exe" ]; then
  PYTHON="./.venv/Scripts/python.exe"
else
  PYTHON="python"
fi

$PYTHON -m unittest tests.TestMain
```

## 为什么不建议 activate

| 方式 | 生效范围 | 典型失效场景 |
|---|---|---|
| `activate` 后跑 `python` | 仅当前 shell 会话 | 子进程、IDE 运行配置、CI 步骤、`pwsh -File script.ps1` 都拿不到 |
| 直接调用解释器路径 | 任何上下文一致 | 路径写错（相对路径的基准是**当前工作目录**，不是脚本所在目录） |

第二种的唯一注意事项是路径基准：脚本里用相对路径时，先确认当前工作目录是不是仓库根。

## 与 python-env 的分工

`python-env` 与本技能都涉及「用哪个解释器」，但层次不同：

| 维度 | `python-env`（机制层） | `ok-script-venv`（本技能，约定层） |
|---|---|---|
| 解决什么 | 环境从哪来、是否就绪、解释器是否**真的可用** | 日常敲命令时该写哪个路径 |
| 探测方式 | 四级顺序（配置 → `VIRTUAL_ENV` → 项目根候选目录 → 启动器），**每级校验能 `import ok`** | 只看仓库根有没有 `.venv`，两级（本地 → 全局） |
| 校验 | 有，`import ok` 失败即换下一级 | 无，仅判断路径存在 |
| 产出 | 创建/更新工具环境、输出解释器绝对路径 | 一段可粘贴的命令 |
| 何时用 | 环境缺失、检查报退出码 3、需要机器可读的解释器路径 | 手边敲命令、写脚本、给用户示例命令 |

**为什么不合并**：探测逻辑一旦写两处必然分叉。`python-env` 的四级探测带 `import ok` 校验，
是因为它服务于「测试必须在应用环境里跑，否则全员跳过仍报成功」这种静默失败；
日常敲命令不需要这么重。但**当需要绝对可靠时**（跑覆盖率、写自动化脚本），应改用
`python-env` 的 `python` 子命令取解释器路径，而不是自己按本技能的两级判断。

**判据**：一次性的手敲命令 → 本技能；要写进脚本或 CI、或涉及测试与覆盖率 → `python-env`。

## 参考文献

| 文件 | 内容 |
|------|------|
| `references/shell-snippets.md` | 各 shell 与常见工具（unittest、ruff、mkdocs、pip）的完整命令写法、路径基准陷阱、CI 与本地差异 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |

## 不适用范围

| 请求 | 该去哪 |
|------|--------|
| 创建 / 重建工具环境（ruff + coverage） | `python-env` 的 `ensure` |
| 需要机器可读的应用解释器绝对路径 | `python-env` 的 `python` 子命令 |
| 判断 Python 环境是否就绪 | `python-env` 的 `check` |
| 跑 ruff 静态检查 | `python-lint` |
| 统计测试覆盖率 | `python-test-coverage` |

## 已知局限

- 只判断解释器**是否存在**，不判断它装了哪些包。用错环境（存在但缺依赖）时本技能不会发现，症状是 `ImportError`——此时改用 `python-env` 的 `python` 子命令做 `import ok` 校验。
- 相对路径的基准是当前工作目录。脚本被从别的目录调用时，`.\\.venv\\Scripts\\python.exe` 会指向不存在的位置，需改用脚本自身位置推导的绝对路径。
- 不涉及 conda、poetry、pdm 等其他环境管理器；本项目统一用 `.venv` 布局。
