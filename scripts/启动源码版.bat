@echo off
rem ============================================================
rem  启动「源码版」zhuzhu Copilot（供验证最新改动，不影响安装版）
rem
rem  为什么需要它：
rem    AI 助手执行的命令运行在受限沙箱里，从沙箱启动的应用其
rem    QtWebEngine 无法初始化（Chromium 会直接崩溃）—— 结果就是预览面板
rem    静默降级为纯文本，看起来"没有样式"。请用本文件在本机正常环境启动。
rem
rem  说明：安装版（Program Files）代码是打包时的快照，不含最新改动；
rem        源码版始终是最新的。
rem ============================================================
setlocal
set "REPO=%~dp0.."
set "PY=C:\Users\zhuzhu\AppData\Local\Programs\Python\Python313\pythonw.exe"

if not exist "%PY%" (
  echo [错误] 未找到 Python: %PY%
  echo 请修改本文件里的 PY 变量为你的 pythonw.exe 路径。
  pause
  exit /b 1
)

rem 清理可能干扰 WebEngine 的环境变量（这些会让 Chromium 崩溃/降级）
set "QTWEBENGINE_CHROMIUM_FLAGS="
set "QTWEBENGINE_DISABLE_SANDBOX="
set "QT_QPA_PLATFORM="

cd /d "%REPO%"
echo 正在启动源码版 zhuzhu Copilot ...
echo   工作目录: %REPO%
start "" "%PY%" "%REPO%\src\main.py"
echo 已启动。若预览面板仍未显示样式，请查看诊断日志：
echo   %TEMP%\zhuzhu_copilot_preview\diag.log
timeout /t 3 >nul
