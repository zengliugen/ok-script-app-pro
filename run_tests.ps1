# 逐个执行测试文件，任一失败即中止。
#
# 与上游的差异：上游只收集 tests\*.py 这一层。本项目有两类测试根，职责不同：
#   tests/             —— 上游基线测试（随上游更新跟进，不得修改）
#   extensions_tests/  —— 项目扩展测试（被测对象是 extensions/ 下的运行时代码）
#
# 两层会一起跑，但结论不可混用：
#   tests/ 通过            => 上游既有行为没被本地改动破坏（同步后只能用这条判断）
#   extensions_tests/ 通过 => 扩展自身按预期工作，**不能**证明上游行为完好
#
# 必须显式传文件路径给 unittest：unittest discover 只递归含 __init__.py 的包，
# 靠 discover 会静默漏测（且仍报成功）。测试文件本身不带 __init__ 依赖，
# 逐个传路径最直接，也不受包结构变化影响。
#
# 解释器优先级：项目 venv → 裸 python。裸 python 常解析到系统解释器，它没有
# PySide6 等运行依赖，测试会以 ImportError 失败——那种失败看起来像「代码坏了」，
# 实际是「用错解释器」。因此优先用 venv，找不到时才回退并在启动时明说。
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = "python"
    Write-Host "提示：未找到项目 venv（$venvPython），回退到裸 python。缺运行依赖时测试会整类跳过。"
}

$testRoots = @(
    @{ Path = ".\tests";            Label = "上游基线" },
    @{ Path = ".\extensions_tests"; Label = "项目扩展" }
)

$testFiles = foreach ($root in $testRoots) {
    if (Test-Path $root.Path) {
        Get-ChildItem -Path $root.Path -Recurse -Filter "*.py" |
            Where-Object { $_.Name -like "Test*.py" } |
            ForEach-Object { [PSCustomObject]@{ File = $_; Label = $root.Label } }
    }
}

if (-not $testFiles) {
    Write-Error "未找到任何测试文件（已检查：$(($testRoots | ForEach-Object { $_.Path }) -join ', ')）"
    exit 1
}

foreach ($item in $testFiles) {
    Write-Host "Running [$($item.Label)] $($item.File.FullName)"
    try {
        & $python -m unittest $item.File.FullName

        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed in $($item.File.FullName)"
        }
    } catch {
        Write-Error $_
        exit 1
    }
}
