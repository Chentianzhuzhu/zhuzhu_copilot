@echo off
rem ============================================================
rem  Install zhutianliang self-signed code-signing root cert
rem  Run as Administrator ONCE so the signed exes validate as
rem  "Valid signature" on this machine.
rem ============================================================
setlocal
set CER=%~dp0zhutianliang.cer
if not exist "%CER%" (
    echo [ERROR] %CER% not found.
    exit /b 1
)
certutil -addstore -f Root "%CER%"
if errorlevel 1 goto :err
certutil -addstore -f TrustedPublisher "%CER%"
if errorlevel 1 goto :err
echo.
echo [OK] Trust installed. Signed exes now validate on this machine.
exit /b 0
:err
echo [ERROR] install failed. Run this file as Administrator.
exit /b 1
