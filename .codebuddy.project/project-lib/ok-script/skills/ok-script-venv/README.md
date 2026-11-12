# ok-script 本地虚拟环境（ok-script-venv）

## 解决什么问题

在 ok-script 项目里敲 Python 命令时，用错解释器**不会报错，只会表现为奇怪的失败**：
系统 `python` 没装 PySide6，测试以 `ImportError` 挂掉，看起来像「代码坏了」；
或者用 `activate` 激活后在 IDE 里跑，环境又没继承过去。

本技能把「用哪个解释器、怎么写命令」固化成约定，给出可直接粘贴的片段。

## 怎么用

问法很随意：

```text
$ok-script-venv 跑测试该用哪个 python
$ok-script-venv PowerShell 里怎么调 .venv
$ok-script-venv 为什么 CI 上能跑本地不行
```

## 三条规则

1. 跑 Python 前先看仓库根有没有 `.venv`。
2. 优先**直接调用解释器路径**，不依赖 `activate`——`activate` 只改当前 shell 的 `PATH`，子进程、IDE、CI 都拿不到。
3. 只有确实没有 `.venv` 时才回退全局 `python`，并且**显式说明**这次回退。

## 边界

- **只管约定**：给出该用哪个解释器的命令写法。
- 环境创建、依赖锁定、解释器四级探测与 `import ok` 校验 → `python-env`。
- 静态检查 → `python-lint`。
- 覆盖率 → `python-test-coverage`。

## 与 python-env 的分工

| 维度 | `python-env` | `ok-script-venv`（本技能） |
|---|---|---|
| 层次 | 机制层 | 约定层 |
| 探测 | 四级，每级校验 `import ok` | 两级，只看路径存在 |
| 适用 | 写进脚本/CI、跑测试与覆盖率、环境诊断 | 手敲命令、给示例命令 |

判据：一次性的手敲命令 → 本技能；要写进脚本或 CI、或涉及测试与覆盖率 → `python-env`。

## 文档索引

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 三条规则、PowerShell 与 POSIX 写法、为什么不建议 activate、与 python-env 的分工 |
| `references/shell-snippets.md` | 各工具完整命令、路径基准陷阱、CI 与本地差异、何时该换用 python-env |
| `evals/trigger_cases.json` | 触发用例 |
| `evals/semantic_config.json` | 语义概念与权重 |
| `reports/output-risk-profile.md` | 风险矩阵与已知局限 |
| `reports/skill-ir.json` | 平台中立的技能契约 |
| `CHANGELOG.md` | 来源版本与本地改造记录 |
