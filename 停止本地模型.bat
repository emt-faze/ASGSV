@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%local-voice-model\scripts\stop-server.ps1"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
