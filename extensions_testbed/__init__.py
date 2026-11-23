"""ok-script-app-pro 测试靶场（支持后台点击）。

该目录是**被测目标程序**，不是测试用例——文件命名刻意不含 `test_` 前缀，
也不会被 `run_tests.ps1` 收集（它只扫 `tests/` 与 `extensions_tests/`）。

派生自 `AutoOperation` 仓库的 `tests/testbed/`，行为增强只有一处：
**支持 PostMessage 后台点击**（上游用的是 Tkinter，Tk 会丢弃投递的鼠标消息）。
机制与论证见 `extensions_testbed/click_claim.py` 与同目录 `README.md` §1.2。
"""
