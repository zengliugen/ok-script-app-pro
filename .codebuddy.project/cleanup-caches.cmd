@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

rem ============================================================ 
rem  清理项目侧运行时缓存目录（caches/ 下的 check_logs / .cache / .vcs-lock 等）。 
rem 
rem  本脚本为薄入口：实际清理逻辑由引擎侧通用工具 
rem  （.codebuddy/scripts/cleanup_artifacts.py）承担，待清理目录清单 
rem  由 .codebuddy/project.index.json 的 artifacts.dirs 驱动， 
rem  不硬编码任何技能名或路径。 
rem 
rem  用法： 
rem    cleanup-caches.cmd            交互确认后清理 
rem    cleanup-caches.cmd all        跳过确认直接清理 
rem ============================================================ 

rem 定位引擎侧清理工具（相对本脚本：<项目根>/.codebuddy.project/ -> <项目根>/.codebuddy/scripts/） 
set "BASE=%~dp0"
rem 去除脚本路径末尾的反斜杠。 
if "%BASE:~-1%"=="\" set "BASE=%BASE:~0,-1%"
set "CLEANUP_TOOL=%BASE%\..\.codebuddy\scripts\cleanup_artifacts.py"

if not exist "%CLEANUP_TOOL%" (
    echo [错误] 未找到引擎侧清理工具：%CLEANUP_TOOL% 
    echo        请确认 .codebuddy 引擎包完整。 
    pause
    exit /b 1
)

rem 查找可用的 Python（python 优先，回退 py）。 
set "PYEXE="
where python >nul 2>&1
if not errorlevel 1 set "PYEXE=python"
if not defined PYEXE (
    where py >nul 2>&1
    if not errorlevel 1 set "PYEXE=py"
)
if not defined PYEXE (
    echo [错误] 未找到 Python，请先安装 Python 3 并加入 PATH。 
    pause
    exit /b 1
)

if /i "%~1"=="all" (
    "%PYEXE%" "%CLEANUP_TOOL%" all
) else (
    "%PYEXE%" "%CLEANUP_TOOL%"
)
set "RC=%ERRORLEVEL%"

echo.
echo 提示：清理完成，按任意键关闭窗口。 
pause
exit /b %RC%
