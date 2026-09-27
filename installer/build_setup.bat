@echo off
rem ============================================================
rem  zhuzhu Copilot 安装包构建（兼容入口）
rem
rem  完整流程已重构为 PowerShell 脚本 build_release.ps1
rem  （单一入口：版本号同步 / 体检 / 运行时与依赖组件 / 工作流种子 /
rem    打包 / 签名 / Inno Setup 编译 / 产物自检 / 日志留档）。
rem  本批处理仅作薄壳，避免与 PS 脚本出现两套流程。
rem
rem  用法：
rem    build_setup.bat                 完整打包 + 签名
rem    build_setup.bat -Version 5.1.2  同时升级版本号
rem    build_setup.bat -NoSign         不签名
rem    build_setup.bat -DryRun         只体检
rem  输出：
rem    dist\zhuzhu Copilot Setup.exe
rem ============================================================
setlocal
cd /d "%~dp0.."

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 powershell.exe
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\build_release.ps1" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo BUILD FAILED.  see build\build_*.log
)
exit /b %RC%
