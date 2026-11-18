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
"%ROOT%runtime\python\python.exe" -I -X utf8 "%ROOT%scripts\install_voice_transcription.py"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
