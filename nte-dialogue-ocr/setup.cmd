@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
for %%I in ("%~dp0..") do set "ROOT=%%~fI\"
call "%ROOT%scripts\ensure_embedded_python.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%nte-dialogue-ocr\setup.ps1"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
