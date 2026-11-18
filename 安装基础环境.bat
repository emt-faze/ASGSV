@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
call "%ROOT%scripts\ensure_embedded_python.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\install.ps1"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
