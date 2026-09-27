@echo off
rem ============================================================
rem  WinAppMigrator - Inno Setup Default.isl repair script
rem  Replaces the broken Default.isl with the official full
rem  Chinese Simplified translation shipped with this project.
rem  Run: right-click this file -> Run as administrator
rem ============================================================
setlocal

set "ISDIR=C:\Program Files\Inno Setup 7"
set "SRC=%~dp0ChineseSimplified.isl"
set "DST=%ISDIR%\Default.isl"

if not exist "%ISDIR%\Default.isl" (
    echo [ERROR] Default.isl not found at %ISDIR%
    echo         Check your Inno Setup installation path.
    pause
    exit /b 1
)

if not exist "%SRC%" (
    echo [ERROR] ChineseSimplified.isl not found at %SRC%
    echo         Keep this script inside the installer folder.
    pause
    exit /b 1
)

rem Backup the original file
copy /y "%DST%" "%DST%.bak" >nul 2>&1
if exist "%DST%.bak" (
    echo [OK] Original Default.isl backed up to Default.isl.bak
) else (
    echo [WARN] Backup failed (may lack permission), continuing...
)

rem Overwrite with the official full Chinese file
copy /y "%SRC%" "%DST%" >nul
if errorlevel 1 (
    echo [ERROR] Overwrite failed. Make sure you ran this script
    echo         as administrator.
    pause
    exit /b 1
)

echo.
echo [DONE] Default.isl replaced with the official Chinese Simplified file.
echo        It contains all keys required by Inno Setup 7.
echo        You can now recompile the installer.
echo.
pause
