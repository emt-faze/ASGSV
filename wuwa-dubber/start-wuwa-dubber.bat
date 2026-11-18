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
"%ROOT%runtime\python\python.exe" -I -X utf8 -m wuwa_dubber
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
