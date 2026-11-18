@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
for %%I in ("%~dp0..") do set "ROOT=%%~fI\"
if not exist "%ROOT%runtime\python\python.exe" (
    echo 请先运行根目录里的安装脚本。
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%local-voice-model\scripts\start-server.ps1"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
