@echo off
chcp 65001 > nul
if not "%~1"=="__utf8__" (
    cmd /c "%~f0" __utf8__ %*
    exit /b %errorlevel%
)
shift

rem ============================================================
rem  启动菜单 
rem
rem  四个入口：主应用、调试模式、测试靶场源码、测试靶场 EXE。双击即可运行， 
rem  也可带参数跳过菜单：run.cmd 1 / 2 / 3 / 4
rem
rem  ── 为什么前几行必须保留「代码页重读」── 
rem  cmd 是边读边执行的：它按当时的活动代码页解析字节，并据此记录行内偏移。 
rem  若脚本含中文、而 chcp 65001 在文件中途才执行，则 chcp 之后的行会用新 
rem  代码页解码、却仍按旧偏移切分，导致字节错位——注释尾部会被当成命令执行， 
rem  报 'xxx' is not recognized。 
rem  因此本脚本用 chcp 65001 后重入 cmd /c "%~f0" __utf8__ 的方式，让新进程 
rem  在 65001 代码页下从头读取，使解码与偏移一致。 
rem
rem  ── 为什么重入后要 shift ── 
rem  重入时 __utf8__ 占了第一个参数位，用户参数被挤到 %2。shift 把它挪回 %1， 
rem  否则 __utf8__ 会被当成用户输入的序号。 
rem
rem  ── 为什么用 PowerShell 的 Start-Process 而不是 start ── 
rem  cmd 的 start 会让子进程继承本脚本的 stdout 句柄，调用方在程序退出前 
rem  一直等不到管道 EOF。Start-Process 创建的进程完全分离。 
rem
rem  ── 为什么不在括号块内用 goto ── 
rem  括号块内 goto 会提前退出块，丢弃未执行的括号逻辑。故所有跳转都在顶层， 
rem  括号块内只用 exit /b（它在块内是安全的）。 
rem ============================================================

setlocal EnableDelayedExpansion
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"

set "CHOICE=%~1"
if not "%CHOICE%"=="" goto dispatch

:menu
cls
echo ============================================================
echo   启动菜单 
echo ============================================================
echo   1. 主应用          main.py 
echo   2. 调试模式        main_debug.py 
echo   3. 测试靶场        extensions_testbed 
echo   4. 测试靶场 EXE    dist\extensions_testbed.exe 
echo   0. 退出 
echo ============================================================
set "CHOICE="
set /p "CHOICE=请输入序号后回车: "
if not defined CHOICE goto done

:dispatch
if "%CHOICE%"=="1" goto start_app
if "%CHOICE%"=="2" goto start_debug
if "%CHOICE%"=="3" goto start_testbed
if "%CHOICE%"=="4" goto start_testbed_exe
if "%CHOICE%"=="0" goto done
echo.
echo 无效的序号: %CHOICE% 
echo 请重新选择。 
echo.
pause
goto menu

rem ── 以下为各入口。先校验解释器与入口文件，缺失时明确报错 ────── 

:start_app
call :require "主应用" "%ROOT%\main.py"
if errorlevel 1 goto done
echo 正在启动主应用... 
call :launch "主应用" "%ROOT%\main.py"
goto done

:start_debug
call :require "调试模式" "%ROOT%\main_debug.py"
if errorlevel 1 goto done
echo 正在启动调试模式... 
call :launch "调试模式" "%ROOT%\main_debug.py"
goto done

:start_testbed
call :require "测试靶场" "%ROOT%\extensions_testbed\main.py"
if errorlevel 1 goto done
echo 正在启动测试靶场... 
call :launch "测试靶场" "%ROOT%\extensions_testbed\main.py"
goto done

:start_testbed_exe
call :testbed_exe_status
if errorlevel 2 goto testbed_exe_build
if errorlevel 1 goto testbed_exe_rebuild
goto testbed_exe_launch

:testbed_exe_rebuild
echo [提示] 靶场源码有改动，正在重新打包 EXE... 
call :build_testbed
if errorlevel 1 goto testbed_exe_failed
goto testbed_exe_launch

:testbed_exe_build
echo [提示] 未找到测试靶场 EXE，正在打包... 
call :build_testbed
if errorlevel 1 goto testbed_exe_failed
goto testbed_exe_launch

:testbed_exe_launch
if not exist "%ROOT%\dist\extensions_testbed.exe" goto testbed_exe_missing
echo 正在启动测试靶场 EXE... 
powershell -NoProfile -Command "Start-Process -FilePath '%ROOT%\dist\extensions_testbed.exe' -WorkingDirectory '%ROOT%'"
if errorlevel 1 goto testbed_exe_launch_failed
echo 已启动测试靶场 EXE。 
timeout /t 2 > nul 2>&1
goto done

:testbed_exe_failed
echo [错误] 测试靶场 EXE 打包失败，请检查上面的输出。 
pause
goto done

:testbed_exe_missing
echo [错误] 打包后仍未找到测试靶场 EXE: 
echo        %ROOT%\dist\extensions_testbed.exe 
pause
goto done

:testbed_exe_launch_failed
echo [错误] 测试靶场 EXE 启动失败。 
pause
goto done

rem ── 子过程 ────────────────────────────────────────────────── 
rem  :require 校验解释器与目标文件，缺失时打印中文提示并返回 1
rem  :launch  用完全分离的方式启动进程，避免占住调用方的管道 

:require
if not exist "%PYW%" (
    echo [错误] 未找到虚拟环境解释器: 
    echo        %PYW% 
    echo 请先按 README 创建 .venv 后重试。 
    pause
    exit /b 1
)
if not exist "%~2" (
    echo [错误] 未找到%~1的入口文件: 
    echo        %~2 
    pause
    exit /b 1
)
exit /b 0

:launch
powershell -NoProfile -Command "Start-Process -FilePath '%PYW%' -ArgumentList '%~2' -WorkingDirectory '%ROOT%'"
if errorlevel 1 (
    echo [错误]%~1启动失败，请检查上面的错误信息。 
    pause
    exit /b 1
)
echo 已启动%~1。 
timeout /t 2 > nul 2>&1
exit /b 0

:testbed_exe_status
if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境解释器: 
    echo        %ROOT%\.venv\Scripts\python.exe 
    echo 请先按 README 创建 .venv 后重试。 
    pause
    exit /b 2
)
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\extensions_testbed\build_exe.py" --check
exit /b %errorlevel%

:build_testbed
if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境解释器: 
    echo        %ROOT%\.venv\Scripts\python.exe 
    echo 请先按 README 创建 .venv 后重试。 
    pause
    exit /b 1
)
if not exist "%ROOT%\extensions_testbed\build_exe.py" (
    echo [错误] 未找到打包脚本: 
    echo        %ROOT%\extensions_testbed\build_exe.py 
    pause
    exit /b 1
)
echo 正在打包测试靶场，请稍候... 
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\extensions_testbed\build_exe.py"
if errorlevel 1 (
    echo [错误] 测试靶场打包失败。 
    pause
    exit /b 1
)
exit /b 0

:done
endlocal
exit /b 0
